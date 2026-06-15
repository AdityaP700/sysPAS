from unittest.mock import patch, MagicMock
from app.schema.discovery import SchemaDiscoveryEngine
from app.schema.cache import SchemaCache
from app.config.settings import settings


def test_discovery_cache_hit():
    """Verify that a cache hit prevents calls to the MCP tool."""
    cache = SchemaCache()
    cache.set("auth_logs", ["src_ip", "user"], ttl=60)
    
    engine = SchemaDiscoveryEngine(cache=cache)
    
    with patch("app.schema.discovery.call_mcp_tool") as mock_mcp:
        fields = engine.get_fields("auth_logs")
        assert fields == ["src_ip", "user"]
        mock_mcp.assert_not_called()


def test_discovery_cache_miss_mcp_success():
    """Verify that a cache miss triggers an MCP call and populates the cache."""
    cache = SchemaCache()
    engine = SchemaDiscoveryEngine(cache=cache)
    
    mock_response = '{"fields": ["src_ip", "user", "action"]}'
    
    with patch("app.schema.discovery.call_mcp_tool", return_value=mock_response) as mock_mcp:
        fields = engine.get_fields("auth_logs")
        
        assert fields == ["src_ip", "user", "action"]
        mock_mcp.assert_called_once_with(settings.mcp_tool_get_fields, {"index": "auth_logs"})
        
        # Verify it was cached
        assert cache.get("auth_logs") == ["src_ip", "user", "action"]


def test_discovery_indexes_mcp_success():
    """Verify index discovery calls splunk_get_indexes tool."""
    engine = SchemaDiscoveryEngine()
    mock_response = '{"indexes": ["auth_logs", "threat_intel"]}'
    
    with patch("app.schema.discovery.call_mcp_tool", return_value=mock_response) as mock_mcp:
        indexes = engine.get_indexes()
        assert indexes == ["auth_logs", "threat_intel"]
        mock_mcp.assert_called_once_with(settings.mcp_tool_get_indexes, {})


def test_discovery_mcp_failure_handled():
    """Verify that MCP connection failures are handled gracefully without crashing."""
    engine = SchemaDiscoveryEngine()
    
    with patch("app.schema.discovery.call_mcp_tool", side_effect=Exception("Connection lost")):
        fields = engine.get_fields("auth_logs")
        assert fields == []  # Graceful fallback to empty list


def test_discover_schema_force_refresh():
    """Verify discover_schema invalidates cache and triggers dynamic scan."""
    cache = SchemaCache()
    cache.set("auth_logs", ["cached_field"], ttl=60)
    
    engine = SchemaDiscoveryEngine(cache=cache)
    mock_response = '{"fields": ["fresh_field"]}'
    
    with patch("app.schema.discovery.call_mcp_tool", return_value=mock_response):
        res = engine.discover_schema("auth_logs")
        assert res.is_successful is True
        assert res.fields == ["fresh_field"]
        assert cache.get("auth_logs") == ["fresh_field"]


def test_discovery_with_custom_tool_names():
    """Verify that SchemaDiscoveryEngine uses custom tool names if overridden in settings."""
    engine = SchemaDiscoveryEngine()
    
    with patch("app.config.settings.settings.mcp_tool_get_fields", "custom_fields_tool"), \
         patch("app.config.settings.settings.mcp_tool_get_indexes", "custom_indexes_tool"), \
         patch("app.schema.discovery.call_mcp_tool") as mock_mcp:
         
         mock_mcp.return_value = '{"fields": ["custom_f"]}'
         fields = engine.get_fields("auth_logs")
         assert fields == ["custom_f"]
         mock_mcp.assert_called_once_with("custom_fields_tool", {"index": "auth_logs"})
         
         mock_mcp.reset_mock()
         mock_mcp.return_value = '{"indexes": ["custom_idx"]}'
         indexes = engine.get_indexes()
         assert indexes == ["custom_idx"]
         mock_mcp.assert_called_once_with("custom_indexes_tool", {})
