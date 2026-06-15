from app.templates.mappings import IntentMapper, Intent
from app.generation.engine import TemplateGenerationEngine
from app.context.generation_context import GenerationContext
from app.domain.models import RunbookStep
from app.domain.enums import StepType


def test_intent_mapping():
    assert IntentMapper.map_description_to_intent("Check authentication failures in logs") == Intent.FAILED_LOGIN
    assert IntentMapper.map_description_to_intent("Detect brute force logins spike") == Intent.BRUTE_FORCE
    assert IntentMapper.map_description_to_intent("Lookup threat intelligence categories") == Intent.THREAT_LOOKUP
    assert IntentMapper.map_description_to_intent("Block offending IP from network") == Intent.SUSPICIOUS_IP
    assert IntentMapper.map_description_to_intent("Escalate to Tier 2") == Intent.ESCALATION
    assert IntentMapper.map_description_to_intent("Perform generic task") == Intent.GENERIC


def test_generation_engine_brute_force():
    engine = TemplateGenerationEngine()
    step = RunbookStep(
        step_id="1",
        description="Detect brute force logins spike > 150 failures",
        step_type=StepType.DETECTION,
        threshold="150 failures",
        time_window="10 min"
    )
    context = GenerationContext(
        step=step,
        schema_fields=["src_ip", "user", "status"],
        data_source="auth_logs",
        constraints={"time_window": "10m"},
        metadata={"grounding": {"resolved_fields": ["src_ip", "user"], "confidence": 1.0}}
    )
    
    spl, intent, conf = engine.generate_spl(context)
    
    assert intent == "BRUTE_FORCE"
    assert "index=auth_logs" in spl
    assert "where failure_count > 150" in spl
    assert "span=-10m" in spl
    assert "by src_ip, user" in spl
    assert conf == 1.0


def test_generation_engine_grounding_fallback():
    engine = TemplateGenerationEngine()
    step = RunbookStep(
        step_id="1",
        description="Check auth logs",
        step_type=StepType.DETECTION
    )
    # Schema missing fields, forcing fallback to default src_ip and user
    context = GenerationContext(
        step=step,
        schema_fields=[],
        data_source="auth_logs",
        constraints={},
        metadata={"grounding": {"resolved_fields": [], "confidence": 0.0}}
    )
    
    spl, intent, conf = engine.generate_spl(context)
    
    assert intent == "FAILED_LOGIN"
    # Even though schema has no fields, it falls back to src_ip and user
    assert "by user, src_ip" in spl
    # Confidence is penalized because we fell back
    assert conf < 1.0
