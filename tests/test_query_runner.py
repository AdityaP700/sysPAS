import pytest
from unittest.mock import AsyncMock, patch
from app.runtime.runner import SplunkQueryRunner
from app.runtime.query_results import QueryExecutionError
from app.config.settings import settings


def test_splunk_query_runner_saved_search_routing():
    runner = SplunkQueryRunner()
    
    # Valid saved search name format
    mock_response = '{"results": [{"count": 42}]}'
    with patch("app.runtime.runner.call_mcp_tool_async", new_callable=AsyncMock, return_value=mock_response) as mock_mcp:
        res = runner.run_query_detailed("My_Saved-Search", {})
        assert res.success is True
        assert res.row_count == 1
        assert res.rows[0]["count"] == 42
        mock_mcp.assert_called_once_with("splunk_run_saved_search", {"name": "My_Saved-Search"})


def test_splunk_query_runner_raw_query_routing():
    runner = SplunkQueryRunner()
    
    # Raw query string containing spaces/operators
    mock_response = '[{"host": "web-01"}]'
    with patch("app.runtime.runner.call_mcp_tool_async", new_callable=AsyncMock, return_value=mock_response) as mock_mcp:
        res = runner.run_query_detailed("index=main status=400", {})
        assert res.success is True
        assert res.row_count == 1
        assert res.rows[0]["host"] == "web-01"
        mock_mcp.assert_called_once_with("splunk_run_query", {"query": "index=main status=400"})


def test_splunk_query_runner_saved_search_invalid_name():
    runner = SplunkQueryRunner()
    
    # Saved search with invalid characters
    with pytest.raises(QueryExecutionError) as exc:
        runner.run_query_detailed("Invalid@Search!", {})
    assert "Invalid saved search name format" in str(exc.value)


def test_splunk_query_runner_timeout_handling():
    runner = SplunkQueryRunner()
    
    # Simulate a timeout
    async def slow_mcp(*args, **kwargs):
        import anyio
        await anyio.sleep(1.0)
        return "{}"
        
    with patch("app.runtime.runner.call_mcp_tool_async", side_effect=slow_mcp):
        # Temp override query timeout settings to a low value
        old_timeout = settings.query_timeout_seconds
        settings.query_timeout_seconds = 0.1
        try:
            with pytest.raises(QueryExecutionError) as exc:
                runner.run_query_detailed("index=main", {})
            assert "timed out" in str(exc.value)
        except Exception as e:
            # Under some runtimes we might raise generic errors, but it should be timeout related
            assert "timed out" in str(e).lower()
        finally:
            settings.query_timeout_seconds = old_timeout
