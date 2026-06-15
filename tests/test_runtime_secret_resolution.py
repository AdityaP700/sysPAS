import pytest
import os
import tempfile
from unittest.mock import AsyncMock, patch, MagicMock
from app.storage.sqlite import SQLiteRepository
from app.runtime.runner import SplunkQueryRunner
from app.runtime.query_results import QueryExecutionError
from app.vault.service import VaultService
from app.vault.models import SecretType
from app.config.settings import settings


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def test_runtime_splunk_secret_resolution(temp_db):
    old_key = settings.vault_master_key
    settings.vault_master_key = "a" * 32
    
    try:
        repo = SQLiteRepository(temp_db)
        vault_service = VaultService(repo)
        
        tenant_id = "tenant-splunk"
        vault_service.create_secret(tenant_id, "splunk-api-token", SecretType.TOKEN, "splunk-decrypted-value")
        
        runner = SplunkQueryRunner(repo=repo)
        
        # We pass splunk_secret in kwargs pointing to splunk-api-token secret
        mock_response = '{"results": [{"count": 42}]}'
        with patch("app.runtime.runner.call_mcp_tool_async", new_callable=AsyncMock, return_value=mock_response) as mock_mcp:
            res = runner.run_query_detailed(
                "index=main", 
                context={}, 
                tenant_id=tenant_id, 
                splunk_secret="splunk-api-token"
            )
            assert res.success is True
            assert res.rows[0]["count"] == 42
            
            # Verify that the tool was called with splunk_token and token replaced by decrypted value,
            # and that splunk_secret and tenant_id were not passed to MCP tool
            mock_mcp.assert_called_once()
            called_args = mock_mcp.call_args[0][1]
            assert called_args["splunk_token"] == "splunk-decrypted-value"
            assert called_args["token"] == "splunk-decrypted-value"
            assert "splunk_secret" not in called_args
            assert "tenant_id" not in called_args
    finally:
        settings.vault_master_key = old_key


def test_runtime_splunk_secret_resolution_failure(temp_db):
    old_key = settings.vault_master_key
    settings.vault_master_key = "a" * 32
    
    try:
        repo = SQLiteRepository(temp_db)
        runner = SplunkQueryRunner(repo=repo)
        
        # Attempting to resolve non-existent secret
        with pytest.raises(QueryExecutionError) as exc:
            runner.run_query_detailed(
                "index=main", 
                context={}, 
                tenant_id="tenant-splunk", 
                splunk_secret="non-existent"
            )
        assert "Validation failed" in str(exc.value)
    finally:
        settings.vault_master_key = old_key
