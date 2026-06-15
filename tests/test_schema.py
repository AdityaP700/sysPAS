from app.schema.provider import MockSchemaProvider
from app.schema.base import SchemaContext, SchemaDiscoveryResult


def test_schema_provider_fields():
    provider = MockSchemaProvider()
    
    assert provider.get_fields("auth_logs") == ["src_ip", "user", "action", "status"]
    assert provider.get_fields("unknown") == []


def test_schema_provider_indexes():
    provider = MockSchemaProvider()
    indexes = provider.get_indexes()
    
    assert "auth_logs" in indexes
    assert "threat_intel" in indexes
    assert "sales_metrics" in indexes
    assert "main" in indexes
    assert len(indexes) == 4


def test_schema_provider_validate_field():
    provider = MockSchemaProvider()
    
    assert provider.validate_field("auth_logs", "src_ip") is True
    assert provider.validate_field("auth_logs", "invalid_field") is False
    assert provider.validate_field("threat_intel", "threat_score") is True


def test_schema_provider_summary():
    provider = MockSchemaProvider()
    summary = provider.get_schema_summary("auth_logs")
    
    assert isinstance(summary, SchemaContext)
    assert summary.data_source == "auth_logs"
    assert summary.fields == ["src_ip", "user", "action", "status"]


def test_schema_provider_discovery():
    provider = MockSchemaProvider()
    
    # Discovery of a known index
    discovery_known = provider.discover_schema("auth_logs")
    assert isinstance(discovery_known, SchemaDiscoveryResult)
    assert discovery_known.data_source == "auth_logs"
    assert discovery_known.is_successful is True
    assert discovery_known.fields == ["src_ip", "user", "action", "status"]
    
    # Discovery of an unknown index
    discovery_unknown = provider.discover_schema("unknown_index")
    assert discovery_unknown.data_source == "unknown_index"
    assert discovery_unknown.is_successful is False
    assert discovery_unknown.fields == []
