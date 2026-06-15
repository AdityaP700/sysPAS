from app.grounding.resolver import SchemaGroundingEngine


def test_grounding_extraction():
    engine = SchemaGroundingEngine()
    
    # Verify extraction of matches
    requested = engine.extract_requested_fields("Check source ip and username in logs")
    assert "source ip" in requested
    assert "username" in requested
    assert len(requested) == 2


def test_grounding_alias_resolution_success():
    engine = SchemaGroundingEngine()
    schema = ["src_ip", "user", "action", "status"]
    
    # "source ip" resolves to "src_ip" which is in schema.
    # "username" resolves to "user" which is in schema.
    result = engine.ground("Check source ip and username", schema)
    
    assert result.confidence == 0.9
    assert "src_ip" in result.resolved_fields
    assert "user" in result.resolved_fields
    assert len(result.missing_fields) == 0
    assert len(result.warnings) == 0


def test_grounding_missing_fields():
    engine = SchemaGroundingEngine()
    schema = ["user", "status"]  # missing src_ip
    
    result = engine.ground("Check source ip and username", schema)
    
    # source ip -> src_ip (missing)
    # username -> user (resolved)
    assert result.confidence == 0.7
    assert "user" in result.resolved_fields
    assert "src_ip" not in result.resolved_fields
    assert "source ip" in result.missing_fields
    assert len(result.warnings) == 1
    assert "not found in schema" in result.warnings[0]
