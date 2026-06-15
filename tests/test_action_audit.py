import pytest
import os
import tempfile
from unittest.mock import MagicMock
from app.storage.sqlite import SQLiteRepository
from app.audit.repository import SQLiteAuditRepository
from app.actions.engine import ActionExecutionEngine
from app.actions.base import BaseActionConnector
from app.actions.models import ActionResult
from app.agent.graph import ExecutionNode


class AuditDummyConnector(BaseActionConnector):
    def __init__(self, succeed: bool):
        self.succeed = succeed

    def validate(self, payload: dict) -> None:
        pass

    def execute(self, payload):
        if self.succeed:
            return ActionResult(success=True, action_type="SEND_EMAIL", external_id="msg-111", details={"status": "sent"}, duration_ms=1.0)
        else:
            raise ValueError("SMTP Server Down")


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_action_audit_success(temp_db):
    repo = SQLiteRepository(temp_db)
    audit_repo = SQLiteAuditRepository(temp_db)
    
    engine = ActionExecutionEngine(repo=repo, audit_repo=audit_repo)
    engine.registry.register("SEND_EMAIL", AuditDummyConnector(succeed=True))
    
    node = ExecutionNode(node_id="node-act", step_id="1", step_name="Email node", action_type="SEND_EMAIL")
    engine.execute(tenant_id="tenant-audit", execution_id="exec-100", node=node, context={"email_to": "a@b.com"})
    
    events = audit_repo.list_audit_events("tenant-audit")
    assert len(events) == 1
    event = events[0]
    assert event.action == "EMAIL_SENT"
    assert event.resource_type == "action_execution"
    assert event.status == "SUCCESS"
    assert event.details["node_id"] == "node-act"
    assert event.details["success"] is True


def test_action_audit_failure(temp_db):
    repo = SQLiteRepository(temp_db)
    audit_repo = SQLiteAuditRepository(temp_db)
    
    engine = ActionExecutionEngine(repo=repo, audit_repo=audit_repo)
    engine.registry.register("SEND_EMAIL", AuditDummyConnector(succeed=False))
    
    node = ExecutionNode(node_id="node-act", step_id="1", step_name="Email node", action_type="SEND_EMAIL")
    
    with pytest.raises(ValueError):
        engine.execute(tenant_id="tenant-audit", execution_id="exec-101", node=node, context={"email_to": "a@b.com"})
        
    events = audit_repo.list_audit_events("tenant-audit")
    assert len(events) == 1
    event = events[0]
    assert event.action == "ACTION_FAILED"
    assert event.status == "ERROR"
    assert "SMTP Server Down" in event.details["error"]
