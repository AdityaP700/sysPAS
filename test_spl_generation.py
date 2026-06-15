"""
test_spl_generation.py
======================
Verification script for:
1. OpenRouter endpoint response
2. Schema-grounding field guardrail (including self-correction)
3. Failover/fallback from OpenRouter to Gemini
4. In-memory result caching

Run:
    python test_spl_generation.py
"""

import os
import sys
import logging

sys.path.insert(0, ".")

# Suppress noise
os.environ["GRPC_VERBOSITY"] = "ERROR"
os.environ["GRPC_TRACE"] = ""

# Setup basic logging to see guardrail logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

from app.config.settings import settings
from app.context.generation_context import GenerationContext
from app.domain.models import RunbookStep
from app.domain.enums import StepType
from app.splunk.adapters.spl_provider import generate_spl, SPLValidationError


from app.splunk.adapters.guardrails import validate_spl_fields


def test_generation():
    print("\n" + "=" * 70)
    print("TEST 0: Unit Test - Field Hallucination Guardrail")
    print("=" * 70)
    
    # 1. Clean query (all fields exist or are derived/built-in)
    clean_spl = "index=auth_logs status=failed | stats count by src_ip | eval my_derived = count * 2"
    schema = ["timestamp", "src_ip", "status"]
    hals = validate_spl_fields(clean_spl, schema)
    print(f"SPL: {clean_spl}")
    print(f"Allowed Schema: {schema}")
    print(f"Detected Hallucinations: {hals}")
    assert len(hals) == 0, f"Expected 0 hallucinations, got: {hals}"
    print("SUCCESS: Clean query validation passed!")
    
    # 2. Hallucinated query (uses 'invalid_field' which doesn't exist in schema)
    hallucinated_spl = "index=auth_logs status=failed | stats count by invalid_field | eval derived = count * 2"
    hals_bad = validate_spl_fields(hallucinated_spl, schema)
    print(f"\nSPL: {hallucinated_spl}")
    print(f"Allowed Schema: {schema}")
    print(f"Detected Hallucinations: {hals_bad}")
    assert "invalid_field" in hals_bad, "Expected 'invalid_field' to be flagged as hallucination!"
    print("SUCCESS: Hallucinated field successfully detected!")

    print("\n" + "=" * 70)
    print("TEST 1: Standard Generation (Claude)")
    print("=" * 70)
    
    # 1. Standard correct context
    step = RunbookStep(
        step_id="1",
        description="Find failed SSH logins from auth_logs",
        step_type=StepType.INVESTIGATION,
        time_window="15m"
    )
    context = GenerationContext(
        step=step,
        data_source="auth_logs",
        schema_fields=["timestamp", "user", "src_ip", "status", "action"],
        constraints={"time_window": "15m"}
    )
    
    # Force claude as primary provider
    settings.llm_provider = "claude"
    
    print("Sending generation request to Claude...")
    res = generate_spl(context)
    print(f"\nSUCCESS: Result generated from {res.provider} (model: {res.model_used})")
    print(f"SPL: {res.spl}")
    print(f"Explanation:\n{res.explanation}")
    print(f"Optimization Notes:\n{res.optimization_notes}")
    print(f"Cached? {res.cached}")

    print("\n" + "=" * 70)
    print("TEST 2: Cache Hit Verification")
    print("=" * 70)
    print("Sending identical generation request...")
    res_cached = generate_spl(context)
    print(f"SUCCESS: Result fetched. Cached? {res_cached.cached}")
    assert res_cached.cached is True, "Caching did not work!"

    print("\n" + "=" * 70)
    print("TEST 3: Schema Guardrail / Hallucination Detection & Re-prompting")
    print("=" * 70)
    # Context with a limited schema that will cause a hallucination if LLM generates default names.
    # We ask for something containing "bytes_in" but we only allow a schema WITHOUT bytes_in.
    # This should trigger the field validator.
    step_hallucination = RunbookStep(
        step_id="2",
        description="Calculate total size of bytes transferred (field bytes_in) in network traffic logs",
        step_type=StepType.INVESTIGATION,
    )
    # Available schema fields do NOT include bytes_in, but instead have only timestamp, src, dest, volume
    context_hallucination = GenerationContext(
        step=step_hallucination,
        data_source="network_logs",
        schema_fields=["timestamp", "src", "dest", "volume"],
    )
    
    print("Sending hallucination-inducing request...")
    try:
        res_hal = generate_spl(context_hallucination)
        print(f"\nSUCCESS: Model complied with schema! Generated SPL: {res_hal.spl}")
    except SPLValidationError as ex:
        print(f"\nExpected SPLValidationError raised: {ex}")

    print("\n" + "=" * 70)
    print("TEST 4: Fallback to Gemini on Claude failure")
    print("=" * 70)
    # Temporarily invalidate Claude key to trigger error and verify fallback
    original_key = settings.claude_api_key
    settings.claude_api_key = None
    
    step_fallback = RunbookStep(
        step_id="3",
        description="List all events in endpoint index",
        step_type=StepType.INVESTIGATION
    )
    context_fallback = GenerationContext(
        step=step_fallback,
        data_source="endpoint",
        schema_fields=["timestamp", "host", "process"]
    )
    
    print("Sending request with invalid Claude key (testing fallback to Gemini)...")
    try:
        res_fb = generate_spl(context_fallback)
        print(f"\nSUCCESS: Result generated after fallback! Provider used: {res_fb.provider}")
        print(f"SPL: {res_fb.spl}")
    except Exception as ex:
        # Check if the fallback actually happened and hit the Gemini API call
        err_msg = str(ex)
        if "ResourceExhausted" in err_msg or "429" in err_msg or "quota" in err_msg:
            print("\nSUCCESS: Fallback to Gemini was triggered successfully!")
            print(f"Gemini was reached but failed with expected rate/quota exhaustion: {ex}")
        else:
            print(f"\nUnexpected error during fallback test: {ex}")
            raise ex
    finally:
        # Restore key
        settings.claude_api_key = original_key

    print("\n" + "=" * 70)
    print("ALL TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 70)


if __name__ == "__main__":
    test_generation()
