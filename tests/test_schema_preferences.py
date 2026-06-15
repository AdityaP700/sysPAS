import pytest
from app.splunk.adapters.guardrails import validate_schema_preferences

def test_validate_schema_preferences_no_status_field():
    # If "status" is not in schema_fields, should not enforce preferences
    spl = "index=auth failed OR failure OR error"
    schema = ["src_ip", "user"]
    violations = validate_schema_preferences(spl, schema)
    assert len(violations) == 0

def test_validate_schema_preferences_correct_usage():
    # If status is in schema and status=failed is used properly without semantic keywords
    spl = "index=auth status=failed | stats count by user"
    schema = ["src_ip", "user", "status"]
    violations = validate_schema_preferences(spl, schema)
    assert len(violations) == 0

def test_validate_schema_preferences_incorrect_status_value():
    # If status is in schema but uses status=failure
    spl = "index=auth status=failure | stats count by user"
    schema = ["src_ip", "user", "status"]
    violations = validate_schema_preferences(spl, schema)
    assert len(violations) > 0
    assert any("failure" in v for v in violations)

def test_validate_schema_preferences_semantic_keywords():
    # If status is in schema and semantic keywords failed/failure/error are searched
    spl = "index=auth failed OR error | stats count by user"
    schema = ["src_ip", "user", "status"]
    violations = validate_schema_preferences(spl, schema)
    assert len(violations) > 0
    assert any("failed" in v for v in violations)
    assert any("error" in v for v in violations)

def test_validate_schema_preferences_derived_and_ok():
    # Ensure failure_count (with underscore) or failed_login as field names don't violate when status=failed is present
    spl = "index=auth status=failed | stats count as failure_count by user"
    schema = ["src_ip", "user", "status"]
    violations = validate_schema_preferences(spl, schema)
    assert len(violations) == 0
