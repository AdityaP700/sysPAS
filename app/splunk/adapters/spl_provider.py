"""
SPL Provider Factory
====================
Centralised LLM provider selection for SPL generation.

Control via .env:
    RUNBOOKMIND_LLM_PROVIDER=openrouter   # (default) OpenRouter first, Gemini fallback
    RUNBOOKMIND_LLM_PROVIDER=gemini       # Gemini only — no OpenRouter calls

Fallback order (when provider=openrouter):
    1. OpenRouter  → on any exception or validation failure →
    2. Gemini      → raises if also fails

Both services share the same cache key algorithm, so a cache hit in either
service answers requests immediately without touching the other.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Callable
import time

from app.config.settings import settings
from app.context.generation_context import GenerationContext
from app.splunk.adapters.guardrails import validate_spl_fields, validate_schema_preferences
from app.agent.index_resolver import resolve_index

logger = logging.getLogger(__name__)


class SPLValidationError(ValueError):
    """Raised when generated SPL fails schema-grounding / field guardrails."""
    pass


# ---------------------------------------------------------------------------
# Unified result type that both backing services can fill
# ---------------------------------------------------------------------------

@dataclass
class SPLResult:
    """Normalised result returned to adapters regardless of which LLM produced it."""
    spl: str
    explanation: str
    optimization_notes: str
    cached: bool = False
    provider: str = ""          # "openrouter" | "gemini" | "gemini_fallback"
    model_used: str = ""
    created_at: float = field(default_factory=time.monotonic)


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def _get_provider_name() -> str:
    """Read the env-configured provider; normalise to lowercase."""
    return getattr(settings, "llm_provider", "claude").lower()


def generate_spl(context: GenerationContext) -> SPLResult:
    """
    Generate an SPL query using the configured LLM provider with automatic
    fallback to the secondary provider on any transient error or validation failure.

    All resource guards (rate limiting, token caps, caching) are enforced
    inside each backing service — this function is purely routing logic.
    """
    provider = _get_provider_name()

    # Apply demo index/schema resolution mapping
    if context.data_source:
        context.data_source = resolve_index(context.data_source)

    if provider == "gemini":
        return _generate_with_guardrail(_generate_via_gemini_raw, context, label="gemini")

    if provider == "openrouter":
        try:
            return _generate_with_guardrail(_generate_via_openrouter_raw, context, label="openrouter")
        except Exception as exc:
            logger.warning(
                "OpenRouter SPL generation/validation failed (%s: %s). "
                "Falling back to Gemini.",
                type(exc).__name__,
                exc,
            )
            return _generate_with_guardrail(_generate_via_gemini_raw, context, label="gemini_fallback")

    # Default / claude: claude → gemini fallback
    try:
        return _generate_with_guardrail(_generate_via_claude_raw, context, label="claude")
    except Exception as exc:
        logger.warning(
            "Claude SPL generation/validation failed (%s: %s). "
            "Falling back to Gemini.",
            type(exc).__name__,
            exc,
        )
        return _generate_with_guardrail(_generate_via_gemini_raw, context, label="gemini_fallback")


# ---------------------------------------------------------------------------
# Guardrail Orchestrator
# ---------------------------------------------------------------------------

def _generate_with_guardrail(
    generator_fn: Callable[[GenerationContext], SPLResult],
    context: GenerationContext,
    label: str
) -> SPLResult:
    """
    Executes the generator function and validates the output SPL fields.
    If hallucinations are detected:
      1. Logs warning and makes one self-corrective re-prompt attempt.
      2. If it still fails, raises SPLValidationError to trigger fallback/failure.
    """
    # 1. First attempt
    result = generator_fn(context)
    result.provider = label

    # 2. Check for schema/field hallucinations and preference violations (only if allowed fields are specified)
    if not context.schema_fields:
        return result

    hallucinations = validate_spl_fields(result.spl, context.schema_fields)
    violations = validate_schema_preferences(result.spl, context.schema_fields)
    if not hallucinations and not violations:
        return result

    feedback_parts = []
    if hallucinations:
        logger.warning(
            "SPL guardrail validation failed. Hallucinated fields detected: %s. "
            "Query: %s",
            hallucinations,
            result.spl
        )
        feedback_parts.append(
            f"CRITICAL: The previously generated SPL query referenced invalid fields not present in the allowed schema: {list(hallucinations)}. "
            f"Do NOT use those fields. Generate the SPL using ONLY these allowed fields: {context.schema_fields}."
        )

    if violations:
        logger.warning(
            "SPL guardrail validation failed. Schema preference violations detected: %s. "
            "Query: %s",
            violations,
            result.spl
        )
        feedback_parts.append(
            "CRITICAL: Prefer schema fields over semantic keywords. If the 'status' field exists in the available fields list, "
            "you MUST use 'status=failed' instead of semantic keywords like 'failed', 'failure', or 'error' (either as bare keywords "
            "or as other field values/assignments)."
        )

    # 3. Attempt self-correction by re-prompting once
    context_copy = context.model_copy(deep=True)
    if context_copy.constraints is None:
        context_copy.constraints = {}

    context_copy.constraints["validation_feedback"] = " ".join(feedback_parts)

    logger.info("Attempting self-corrective LLM re-prompt...")
    try:
        result_retry = generator_fn(context_copy)
        result_retry.provider = label
        
        # Check retried output
        hallucinations_retry = validate_spl_fields(result_retry.spl, context.schema_fields)
        violations_retry = validate_schema_preferences(result_retry.spl, context.schema_fields)
        if not hallucinations_retry and not violations_retry:
            logger.info("Self-corrective re-prompt succeeded! Generated SPL is now schema-grounded.")
            return result_retry
            
        # If it still fails, raise validation error
        err_msg = ""
        if hallucinations_retry:
            err_msg += f"Generated SPL uses fields not present in the schema: {hallucinations_retry}. "
        if violations_retry:
            err_msg += f"Generated SPL violates schema preference rules: {violations_retry}. "
        raise SPLValidationError(
            f"{err_msg}Allowed: {context.schema_fields}. Query: {result_retry.spl}"
        )
    except Exception as exc:
        if isinstance(exc, SPLValidationError):
            raise
        raise SPLValidationError(
            f"Exception occurred during self-corrective re-prompt: {str(exc)}"
        ) from exc


# ---------------------------------------------------------------------------
# Internal helpers — lazy-import to avoid circular imports at module load
# ---------------------------------------------------------------------------

def _generate_via_openrouter_raw(context: GenerationContext) -> SPLResult:
    from app.splunk.adapters.openrouter_spl_service import openrouter_spl_service

    r = openrouter_spl_service.generate(context)
    return SPLResult(
        spl=r.spl,
        explanation=r.explanation,
        optimization_notes=r.optimization_notes,
        cached=r.cached,
        provider="openrouter",
        model_used=r.model_used,
        created_at=r.created_at,
    )


def _generate_via_gemini_raw(context: GenerationContext) -> SPLResult:
    from app.splunk.adapters.gemini_spl_service import gemini_spl_service

    r = gemini_spl_service.generate(context)
    return SPLResult(
        spl=r.spl,
        explanation=r.explanation,
        optimization_notes=r.optimization_notes,
        cached=r.cached,
        provider="gemini",
        model_used=getattr(settings, "gemini_model", "gemini-2.5-flash"),
        created_at=r.created_at,
    )


def _generate_via_claude_raw(context: GenerationContext) -> SPLResult:
    from app.splunk.adapters.claude_spl_service import claude_spl_service

    r = claude_spl_service.generate(context)
    return SPLResult(
        spl=r.spl,
        explanation=r.explanation,
        optimization_notes=r.optimization_notes,
        cached=r.cached,
        provider="claude",
        model_used=getattr(settings, "claude_model", "claude-3-5-haiku-latest"),
        created_at=r.created_at,
    )
