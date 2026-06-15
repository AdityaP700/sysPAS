"""
ClaudeSPLService
================
Single responsibility: call Claude (via official Anthropic SDK) to produce a
raw SPL query, explanation, and optimization notes in a structured JSON format
from a GenerationContext, and cache the result.

Caching and rate-limiting behaviors are identical to gemini_spl_service.py.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

import anthropic

from app.config.settings import settings
from app.context.generation_context import GenerationContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ClaudeSPLResult:
    """Holds everything the service produces for a single runbook step."""
    spl: str
    explanation: str
    optimization_notes: str
    cached: bool = False
    created_at: float = field(default_factory=time.monotonic)


# ---------------------------------------------------------------------------
# Simple timestamp cache { hash_key -> (ClaudeSPLResult, created_at) }
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[ClaudeSPLResult, float]] = {}


def _cache_get(key: str, ttl: int) -> Optional[ClaudeSPLResult]:
    """Return cached result if it exists and has not expired."""
    if key not in _cache:
        return None
    result, ts = _cache[key]
    if time.monotonic() - ts > ttl:
        del _cache[key]
        return None
    result.cached = True
    return result


def _cache_set(key: str, result: ClaudeSPLResult) -> None:
    _cache[key] = (result, time.monotonic())


# ---------------------------------------------------------------------------
# Rate limiter
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
# Local SPL analysis (fallback logic if JSON parsing fails or has empty keys)
# ---------------------------------------------------------------------------

def _extract_indexes(spl: str) -> list[str]:
    return re.findall(r'index\s*=\s*(\S+)', spl, re.IGNORECASE)


def _extract_pipe_commands(spl: str) -> list[str]:
    commands = []
    for segment in spl.split('|')[1:]:
        segment = segment.strip()
        if segment:
            cmd = segment.split()[0].lower()
            commands.append(cmd)
    return commands


def build_local_explanation(spl: str, context: GenerationContext) -> str:
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


def parse_json_response(text: str) -> dict:
    """Strip markdown and extract the JSON block."""
    cleaned = re.sub(r'^```(?:json)?\n?', '', text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r'\n?```$', '', cleaned)
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start != -1 and end != -1:
        cleaned = cleaned[start:end+1]
    return json.loads(cleaned)


# ---------------------------------------------------------------------------
# Claude client (initialised dynamically on settings API key)
# ---------------------------------------------------------------------------

_client: Optional[anthropic.Anthropic] = None
_client_api_key: Optional[str] = None


def _ensure_claude_init() -> None:
    global _client, _client_api_key
    api_key = settings.claude_api_key
    if not api_key:
        raise RuntimeError(
            "RUNBOOKMIND_CLAUDE_API_KEY / ANTHROPIC_API_KEY is not set. "
            "Add it to your .env file or set RUNBOOKMIND_LLM_PROVIDER=gemini."
        )
    if _client is not None and _client_api_key == api_key:
        return
    _client = anthropic.Anthropic(api_key=api_key)
    _client_api_key = api_key





_SYSTEM_PROMPT = (
    "You are a Splunk SPL expert. Given a runbook step, generate a precise, executable SPL query.\n"
    "CRITICAL RULES:\n"
    "1. Prefer schema fields over semantic keywords when generating filters.\n"
    "2. If the 'status' field exists in the available fields list, you MUST use 'status=failed' instead of semantic keywords like 'failed', 'failure', or 'error' (either as bare keywords or as other field values/assignments).\n"
    "3. Prefer using the schema fields 'status', 'user', 'src_ip', and 'host' over generic or semantic terms.\n\n"
    "You MUST respond ONLY with a valid JSON object. Do not include any explanation outside the JSON object.\n"
    "The JSON object must have exactly the following keys:\n"
    "{\n"
    '  "spl": "<the generated SPL query>",\n'
    '  "explanation": "<a concise, one-sentence description of what the query does>",\n'
    '  "optimization_notes": "<any optimization or performance notes, or \'- No obvious anti-patterns detected.\'>"\n'
    "}"
)


def _call_claude(context: GenerationContext) -> tuple[str, str, str]:
    """Make the single live Claude API call and return (spl, explanation, optimization_notes)."""
    _ensure_claude_init()

    fields_str = ", ".join(context.schema_fields) if context.schema_fields else "not specified"
    user_prompt = (
        f"Step description: {context.step.description}\n"
        f"Target index: {context.data_source or 'main'}\n"
        f"Available fields: {fields_str}\n"
        f"Constraints: {json.dumps(context.constraints)}\n"
        f"Step type: {context.step.step_type.value}"
    )

    message = _client.messages.create(
        model=settings.claude_model,
        max_tokens=settings.claude_max_tokens,
        temperature=0.0,
        system=_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": user_prompt}
        ]
    )

    raw_text = message.content[0].text.strip()
    
    try:
        parsed = parse_json_response(raw_text)
        spl = parsed.get("spl", "").strip()
        explanation = parsed.get("explanation", "").strip()
        opt_notes = parsed.get("optimization_notes", "").strip()
        return spl, explanation, opt_notes
    except Exception as exc:
        logger.warning(
            "Failed to parse JSON response from Claude: %s. Raw output: %r. "
            "Falling back to local extraction/explanation.",
            exc,
            raw_text,
        )
        # Fallback to local parsing
        # Try to find anything looking like an SPL query
        spl = raw_text
        if "{" in raw_text:
            # Maybe partially valid json
            match_spl = re.search(r'"spl"\s*:\s*"([^"]+)"', raw_text)
            if match_spl:
                spl = match_spl.group(1)
        return spl, "", ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class ClaudeSPLService:
    """
    Singleton-style service for Claude SPL generation.
    """

    def generate(self, context: GenerationContext) -> ClaudeSPLResult:
        key = _make_cache_key(context)
        ttl = settings.claude_cache_ttl

        # 1. Cache lookup
        cached = _cache_get(key, ttl)
        if cached is not None:
            return cached

        # 2. Rate limit enforcement
        min_interval = 60.0 / max(settings.claude_rpm_cap, 1)
        _rate_wait(min_interval)

        # 3. Live call
        spl, explanation, optimization_notes = _call_claude(context)

        # 4. Apply fallbacks if empty
        if not explanation:
            explanation = build_local_explanation(spl, context)
        if not optimization_notes:
            optimization_notes = build_local_optimization_notes(spl)

        result = ClaudeSPLResult(
            spl=spl,
            explanation=explanation,
            optimization_notes=optimization_notes,
            cached=False,
        )

        # 5. Store in cache
        _cache_set(key, result)
        return result


# Singleton instance
claude_spl_service = ClaudeSPLService()
