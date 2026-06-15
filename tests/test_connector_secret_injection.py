import pytest
import os
import tempfile
from unittest.mock import MagicMock
from app.storage.sqlite import SQLiteRepository
from app.actions.engine import ActionExecutionEngine
from app.agent.graph import ExecutionNode
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


def test_connector_secret_injection_and_removal(temp_db):
    old_key = settings.vault_master_key
    settings.vault_master_key = "a" * 32
    
    try:
        repo = SQLiteRepository(temp_db)
        vault_service = VaultService(repo)
        
        tenant_id = "tenant-inject"
        
        # 1. Create a secret
        vault_service.create_secret(tenant_id, "my-smtp-password", SecretType.PASSWORD, "smtp-safe-pass")
        
        # 2. Setup ActionExecutionEngine with mock connector returning valid ActionResult
        from app.actions.models import ActionResult
        engine = ActionExecutionEngine(repo)
        mock_connector = MagicMock()
        mock_connector.execute.return_value = ActionResult(
            success=True,
            action_type="SEND_EMAIL",
            external_id="msg-123",
            details={},
            duration_ms=1.2
        )
        engine.registry.register("SEND_EMAIL", mock_connector)
        
        # 3. Create a node with input keys ending in _secret
        node = ExecutionNode(
            node_id="node-1",
            step_id="node-1",
            step_name="Send Alert",
            action_type="SEND_EMAIL",
            compiled_spl=None
        )
        
        # In context we define smtp_password_secret pointing to vault secret name
        context = {
            "email_to": "admin@local.host",
            "smtp_password_secret": "my-smtp-password"
        }
        
        # Execute the action
        engine.execute(tenant_id, "exec-1", node, context)
        
        # 4. Verify mock_connector was called with resolved payload
        # smtp_password_secret should be resolved to smtp_password,
        # and the original smtp_password_secret key must be completely removed!
        mock_connector.execute.assert_called_once()
        called_payload = mock_connector.execute.call_args[0][0]
        
        assert called_payload["smtp_password"] == "smtp-safe-pass"
        assert "smtp_password_secret" not in called_payload
    finally:
        settings.vault_master_key = old_key


def test_connector_secret_injection_failure_halting(temp_db):
    old_key = settings.vault_master_key
    settings.vault_master_key = "a" * 32
    
    try:
        repo = SQLiteRepository(temp_db)
        from app.actions.models import ActionResult
        engine = ActionExecutionEngine(repo)
        mock_connector = MagicMock()
        mock_connector.execute.return_value = ActionResult(
            success=True,
            action_type="SEND_EMAIL",
            external_id="msg-123",
            details={},
            duration_ms=1.2
        )
        engine.registry.register("SEND_EMAIL", mock_connector)
        
        node = ExecutionNode(
            node_id="node-1",
            step_id="node-1",
            step_name="Send Alert",
            action_type="SEND_EMAIL",
            compiled_spl=None
        )
        
        # Attempting to resolve non-existent secret
        context = {
            "email_to": "admin@local.host",
            "smtp_password_secret": "non-existent-secret-name"
        }
        
        with pytest.raises(ValueError) as exc:
            engine.execute("tenant-inject", "exec-2", node, context)
            
        assert "Validation failed" in str(exc.value)
        # Ensure connector was never executed
        mock_connector.execute.assert_not_called()
    finally:
        settings.vault_master_key = old_key
