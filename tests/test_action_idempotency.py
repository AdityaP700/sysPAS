import pytest
from unittest.mock import MagicMock
from app.actions.engine import ActionExecutionEngine
from app.actions.base import BaseActionConnector
from app.actions.models import ActionResult
from app.agent.graph import ExecutionNode
from app.runtime.models import ActionExecutionRecord


class MockEmailConnector(BaseActionConnector):
    def __init__(self):
        self.call_count = 0

    def validate(self, payload: dict) -> None:
        pass

    def execute(self, payload):
        self.call_count += 1
        return ActionResult(success=True, action_type="SEND_EMAIL", external_id=f"msg-{self.call_count}", details={"status": "sent"}, duration_ms=1.0)


def test_action_idempotency_caching():
    repo = MagicMock()
    connector = MockEmailConnector()
    
    # First execution -> no cache in repo
    repo.get_successful_action_execution.return_value = None
    
    engine = ActionExecutionEngine(repo=repo)
    engine.registry.register("SEND_EMAIL", connector)
    
    node = ExecutionNode(node_id="node-email", step_id="1", step_name="Email node", action_type="SEND_EMAIL")
    
    # 1. Run first time
    res1 = engine.execute(tenant_id="tenant-1", execution_id="exec-123", node=node, context={"email_to": "admin@c.com"})
    assert res1.success is True
    assert res1.external_id == "msg-1"
    assert connector.call_count == 1
    
    # 2. Mock finding a successful cached execution in the DB for the second call
    cached = ActionExecutionRecord(
        action_execution_id="actrun_999",
        tenant_id="tenant-1",
        execution_id="exec-123",
        node_id="node-email",
        action_type="SEND_EMAIL",
        external_id="msg-1",
        success=True,
        duration_ms=1.5,
        payload={"request": {}, "response": {"status": "sent"}},
        idempotency_key="exec-123:node-email",
        created_at="2026-06-13T12:00:00Z"
    )
    repo.get_successful_action_execution.return_value = cached
    
    # 3. Run second time with same engine and keys -> should hit cache and NOT call the connector
    res2 = engine.execute(tenant_id="tenant-1", execution_id="exec-123", node=node, context={"email_to": "admin@c.com"})
    assert res2.success is True
    assert res2.external_id == "msg-1"
    assert res2.details.get("idempotent_cached") is True
    assert connector.call_count == 1  # count remains 1, connector was NOT executed again
