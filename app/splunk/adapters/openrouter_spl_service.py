"""
OpenRouterSPLService
====================
Identical contract to GeminiSPLService but backed by OpenRouter's
OpenAI-compatible endpoint (https://openrouter.ai/api/v1).

Resource-conservation design (hackathon / limited-credit context):
  - Shared timestamp-based in-memory cache (SHA-256 key, configurable TTL).
  - Token-bucket rate limiter: enforces a minimum inter-call gap so
    we never exceed RUNBOOKMIND_OPENROUTER_RPM_CAP requests per minute.
  - Single user-message to the chat endpoint (no extra system-prefill round-trips).
  - Reply is the raw SPL string only — no extra tokens for explanation.
    (Explanation and optimization notes are built locally, zero cost.)

Model can be swapped instantly via env without code changes:
  RUNBOOKMIND_OPENROUTER_MODEL=anthropic/claude-sonnet-4
  RUNBOOKMIND_OPENROUTER_MODEL=google/gemini-2.5-flash
  RUNBOOKMIND_OPENROUTER_MODEL=qwen/qwen3-32b
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.config.settings import settings
from app.context.generation_context import GenerationContext

# Re-use the local SPL analysis helpers from the Gemini module (zero cost)
from app.splunk.adapters.gemini_spl_service import (
    build_local_explanation,
    build_local_optimization_notes,
)


# ---------------------------------------------------------------------------
# Result dataclass (mirrors GeminiSPLResult exactly for drop-in compatibility)
# ---------------------------------------------------------------------------

@dataclass
class OpenRouterSPLResult:
    """Holds everything the service produces for a single runbook step."""
    spl: str
    explanation: str           # built locally — no extra API tokens
    optimization_notes: str    # built locally from SPL structure
    cached: bool = False
    model_used: str = ""       # which OpenRouter model fulfilled the request
    created_at: float = field(default_factory=time.monotonic)


# ---------------------------------------------------------------------------
# Shared timestamp cache  { hash_key -> (OpenRouterSPLResult, created_at) }
# ---------------------------------------------------------------------------

_or_cache: dict[str, tuple[OpenRouterSPLResult, float]] = {}


def _cache_get(key: str, ttl: int) -> Optional[OpenRouterSPLResult]:
    if key not in _or_cache:
        return None
    result, ts = _or_cache[key]
    if time.monotonic() - ts > ttl:
        del _or_cache[key]
        return None
    result.cached = True
    return result


def _cache_set(key: str, result: OpenRouterSPLResult) -> None:
    _or_cache[key] = (result, time.monotonic())


# ---------------------------------------------------------------------------
# Rate limiter (token bucket, simplified — identical to Gemini variant)
# ---------------------------------------------------------------------------

_or_rate_lock = threading.Lock()
_or_last_call_ts: float = 0.0


def _rate_wait(min_interval: float) -> None:
    """Block until at least `min_interval` seconds have passed since last call."""
    global _or_last_call_ts
    with _or_rate_lock:
        now = time.monotonic()
        wait = min_interval - (now - _or_last_call_ts)
        if wait > 0:
            time.sleep(wait)
        _or_last_call_ts = time.monotonic()


# ---------------------------------------------------------------------------
# Cache key (shared algorithm with GeminiSPLService — hits cross-provider)
# ---------------------------------------------------------------------------

def _make_cache_key(context: GenerationContext) -> str:
    raw = (
        f"{context.step.description}"
        f"|{context.data_source}"
        f"|{sorted(context.schema_fields)}"
        f"|{sorted(context.constraints.items())}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# System prompt (identical to Gemini for consistent output format)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a Splunk SPL expert. "
    "Given a runbook step, generate a precise, executable SPL query. "
    "Reply with ONLY the raw SPL query string — no markdown fences, "
    "no explanation, no extra text.\n"
    "CRITICAL RULES:\n"
    "1. Prefer schema fields over semantic keywords when generating filters.\n"
    "2. If the 'status' field exists in the available fields list, you MUST use 'status=failed' instead of semantic keywords like 'failed', 'failure', or 'error' (either as bare keywords or as other field values/assignments).\n"
    "3. Prefer using the schema fields 'status', 'user', 'src_ip', and 'host' over generic or semantic terms."
)

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


# ---------------------------------------------------------------------------
# Live API call
# ---------------------------------------------------------------------------

def _call_openrouter(context: GenerationContext) -> tuple[str, str]:
    """
    POST to OpenRouter chat completions endpoint.
    Returns (spl_string, model_actually_used).
    Raises RuntimeError on configuration issues, httpx.HTTPError on network/API errors.
    """
    api_key = settings.openrouter_api_key
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. "
            "Add it to your .env file or set RUNBOOKMIND_LLM_PROVIDER=gemini to use Gemini."
        )

    fields_str = ", ".join(context.schema_fields) if context.schema_fields else "not specified"
    user_prompt = (
        f"Step description: {context.step.description}\n"
        f"Target index: {context.data_source or 'main'}\n"
        f"Available fields: {fields_str}\n"
        f"Constraints: {json.dumps(context.constraints)}\n"
        f"Step type: {context.step.step_type.value}"
    )

    model = settings.openrouter_model
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        # Cap tokens to 512 — SPL queries rarely exceed this.
        # This is the primary resource-conservation lever.
        "max_tokens": settings.openrouter_max_tokens,
        "temperature": 0.1,   # low temperature → deterministic SPL, fewer re-tries
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # OpenRouter attribution headers (recommended by their docs)
        "HTTP-Referer": "https://github.com/RunbookMind",
        "X-Title": "RunbookMind",
    }

    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            f"{_OPENROUTER_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()

    data = response.json()
    raw_spl = data["choices"][0]["message"]["content"].strip()
    model_used = data.get("model", model)

    # Strip accidental markdown fences
    raw_spl = re.sub(r'^```[a-z]*\n?', '', raw_spl, flags=re.IGNORECASE)
    raw_spl = re.sub(r'\n?```$', '', raw_spl)
    return raw_spl.strip(), model_used


# ---------------------------------------------------------------------------
# Public service class
# ---------------------------------------------------------------------------

class OpenRouterSPLService:
    """
    Drop-in replacement for GeminiSPLService backed by OpenRouter.

    generate(context) → OpenRouterSPLResult
        - Cache hit  → returns immediately (0 API calls, 0 credits spent)
        - Cache miss → rate-limited OpenRouter call → cache write → return

    Resource guarantees:
        - max_tokens capped at RUNBOOKMIND_OPENROUTER_MAX_TOKENS (default 512)
        - RPM capped at RUNBOOKMIND_OPENROUTER_RPM_CAP (default 20)
        - Cache TTL at RUNBOOKMIND_OPENROUTER_CACHE_TTL seconds (default 3600)
    """

    def generate(self, context: GenerationContext) -> OpenRouterSPLResult:
        key = _make_cache_key(context)
        ttl = settings.openrouter_cache_ttl

        # 1. Cache hit — zero cost
        cached = _cache_get(key, ttl)
        if cached is not None:
            return cached

        # 2. Rate-limit enforcement (token bucket)
        min_interval = 60.0 / max(settings.openrouter_rpm_cap, 1)
        _rate_wait(min_interval)

        # 3. Live OpenRouter call (SPL only — 512 tokens max)
        spl, model_used = _call_openrouter(context)

        # 4. Build explanation and notes locally (zero API cost)
        explanation = build_local_explanation(spl, context)
        optimization_notes = build_local_optimization_notes(spl)

        result = OpenRouterSPLResult(
            spl=spl,
            explanation=explanation,
            optimization_notes=optimization_notes,
            cached=False,
            model_used=model_used,
        )

        # 5. Store in cache
        _cache_set(key, result)
        return result


# Module-level singleton
openrouter_spl_service = OpenRouterSPLService()
