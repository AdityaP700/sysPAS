"""
GeminiSPLService
================
Single responsibility: call Gemini Flash to produce a raw SPL query from a
GenerationContext and cache the result by a SHA-256 hash of the step inputs.

Design choices (hackathon-optimised):
  - Gemini returns ONLY the SPL string. No explanation, no optimization prose.
    (Explanation is generated locally from the SPL structure — zero extra tokens.)
  - Cache: plain dict[str, CacheEntry] with created_at timestamp. No LRU,
    no threading.Lock on the dict itself (single-process API server; GIL is enough).
  - Rate limiter: a threading.Lock + monotonic timestamp enforcing a minimum
    interval between live Gemini calls (default 7.5 s → ≤ 8 RPM).
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import google.generativeai as genai

from app.config.settings import settings
from app.context.generation_context import GenerationContext


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class GeminiSPLResult:
    """Holds everything the service produces for a single runbook step."""
    spl: str
    explanation: str          # built locally — no extra Gemini tokens
    optimization_notes: str   # built locally from SPL structure
    cached: bool = False
    created_at: float = field(default_factory=time.monotonic)


# ---------------------------------------------------------------------------
# Simple timestamp cache  { hash_key -> (GeminiSPLResult, created_at) }
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[GeminiSPLResult, float]] = {}


def _cache_get(key: str, ttl: int) -> Optional[GeminiSPLResult]:
    """Return cached result if it exists and has not expired."""
    if key not in _cache:
        return None
    result, ts = _cache[key]
    if time.monotonic() - ts > ttl:
        del _cache[key]
        return None
    result.cached = True
    return result


def _cache_set(key: str, result: GeminiSPLResult) -> None:
    _cache[key] = (result, time.monotonic())


# ---------------------------------------------------------------------------
# Rate limiter (token bucket, simplified)
# ---------------------------------------------------------------------------

_rate_lock = threading.Lock()
_last_call_ts: float = 0.0


def _rate_wait(min_interval: float) -> None:
    """Block until at least `min_interval` seconds have passed since the last call."""
    global _last_call_ts
    with _rate_lock:
        now = time.monotonic()
        wait = min_interval - (now - _last_call_ts)
        if wait > 0:
            time.sleep(wait)
        _last_call_ts = time.monotonic()


# ---------------------------------------------------------------------------
# Local SPL analysis helpers (no API tokens consumed)
# ---------------------------------------------------------------------------

def _extract_indexes(spl: str) -> list[str]:
    return re.findall(r'index\s*=\s*(\S+)', spl, re.IGNORECASE)


def _extract_pipe_commands(spl: str) -> list[str]:
    """Return the command names from each pipe stage."""
    commands = []
    for segment in spl.split('|')[1:]:
        segment = segment.strip()
        if segment:
            cmd = segment.split()[0].lower()
            commands.append(cmd)
    return commands


def build_local_explanation(spl: str, context: GenerationContext) -> str:
    """
    Produce a human-readable explanation from the SPL structure alone.
    Fast, deterministic, zero API cost.
    """
    indexes = _extract_indexes(spl)
    commands = _extract_pipe_commands(spl)
    time_window = context.constraints.get("time_window", "unspecified")
    index_str = ", ".join(f"'{i}'" for i in indexes) if indexes else f"'{context.data_source or 'default'}'"
    ops_str = " -> ".join(commands) if commands else "filter events"

    return (
        f"Searches {index_str} over a {time_window} window.\n"
        f"Pipeline: {ops_str}.\n"
        f"Purpose: {context.step.description}"
    )


def build_local_optimization_notes(spl: str) -> str:
    """Flag common SPL anti-patterns without calling an LLM."""
    notes: list[str] = []
    spl_lower = spl.lower()

    if "earliest=" not in spl_lower and "latest=" not in spl_lower:
        notes.append("- No time bounds set -- add earliest/latest to limit scan cost.")
    if "| head " not in spl_lower and "| tail " not in spl_lower:
        notes.append("- No result cap -- consider `| head 1000` to prevent large result sets.")
    if "wildcard" in spl_lower or spl_lower.count("*") > 2:
        notes.append("- Heavy wildcard usage detected -- scope with explicit field filters where possible.")
    if "|stats" in spl_lower and "by " not in spl_lower:
        notes.append("- Stats command without `by` clause may aggregate all events into one row.")

    return "\n".join(notes) if notes else "- No obvious anti-patterns detected."


# ---------------------------------------------------------------------------
# Cache key
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
# Gemini client (initialised once)
# ---------------------------------------------------------------------------

_gemini_ready = False


def _ensure_gemini_init() -> None:
    global _gemini_ready
    if _gemini_ready:
        return
    api_key = settings.gemini_api_key
    if not api_key:
        raise RuntimeError(
            "RUNBOOKMIND_GEMINI_API_KEY is not set. "
            "Add it to your .env file."
        )
    genai.configure(api_key=api_key)
    _gemini_ready = True


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


def _call_gemini(context: GenerationContext) -> str:
    """Make the single live Gemini API call and return the raw SPL string."""
    _ensure_gemini_init()

    fields_str = ", ".join(context.schema_fields) if context.schema_fields else "not specified"
    user_prompt = (
        f"Step description: {context.step.description}\n"
        f"Target index: {context.data_source or 'main'}\n"
        f"Available fields: {fields_str}\n"
        f"Constraints: {json.dumps(context.constraints)}\n"
        f"Step type: {context.step.step_type.value}"
    )

    model = genai.GenerativeModel(
        model_name=settings.gemini_model,
        system_instruction=_SYSTEM_PROMPT,
    )
    response = model.generate_content(user_prompt)
    spl = response.text.strip()

    # Strip accidental markdown fences if the model ignores instructions
    spl = re.sub(r'^```[a-z]*\n?', '', spl, flags=re.IGNORECASE)
    spl = re.sub(r'\n?```$', '', spl)
    return spl.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class GeminiSPLService:
    """
    Singleton-style service (instantiate once per process; share across adapters).

    generate(context) → GeminiSPLResult
        - Cache hit  → returns immediately (0 API calls)
        - Cache miss → rate-limited Gemini call → cache write → return
    """

    def generate(self, context: GenerationContext) -> GeminiSPLResult:
        key = _make_cache_key(context)
        ttl = settings.gemini_cache_ttl

        # 1. Cache lookup
        cached = _cache_get(key, ttl)
        if cached is not None:
            return cached

        # 2. Rate-limit enforcement
        min_interval = 60.0 / max(settings.gemini_rpm_cap, 1)
        _rate_wait(min_interval)

        # 3. Live Gemini call (SPL only)
        spl = _call_gemini(context)

        # 4. Build explanation and notes locally
        explanation = build_local_explanation(spl, context)
        optimization_notes = build_local_optimization_notes(spl)

        result = GeminiSPLResult(
            spl=spl,
            explanation=explanation,
            optimization_notes=optimization_notes,
            cached=False,
        )

        # 5. Store in cache
        _cache_set(key, result)
        return result


# Module-level singleton shared by all three adapter shims
gemini_spl_service = GeminiSPLService()
