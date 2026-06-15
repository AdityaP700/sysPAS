import pytest
from unittest.mock import MagicMock
from app.actions.engine import ActionExecutionEngine, ConnectorRegistry
from app.actions.base import BaseActionConnector
from app.actions.models import ActionResult
from app.agent.graph import ExecutionNode
from app.runtime.models import ActionExecutionRecord


class DummyConnector(BaseActionConnector):
    def validate(self, payload: dict) -> None:
        pass

    def execute(self, payload):
        return ActionResult(success=True, action_type="DUMMY", external_id="ext-dummy-123", details={"status": "sent"}, duration_ms=1.0)


def test_connector_registry():
    registry = ConnectorRegistry()
    connector = DummyConnector()
    registry.register("DUMMY_ACTION", connector)
    assert registry.get("DUMMY_ACTION") == connector
    assert registry.get("dummy_action") == connector
    assert registry.get("NON_EXISTENT") is None


def test_action_engine_idempotency():
    repo = MagicMock()
    # Mock finding a cached successful run
    cached_record = ActionExecutionRecord(
        action_execution_id="actrun_123",
        tenant_id="tenant-1",
        execution_id="exec-1",
        node_id="node-action",
        action_type="DUMMY",
        external_id="ext-dummy-123",
        success=True,
        duration_ms=5.0,
        payload={"response": {"status": "sent"}},
        idempotency_key="exec-1:node-action",
        created_at="2026-06-13T00:00:00Z"
    )
    repo.get_successful_action_execution.return_value = cached_record

    engine = ActionExecutionEngine(repo=repo)
    engine.registry.register("DUMMY", DummyConnector())

    node = ExecutionNode(node_id="node-action", step_id="1", step_name="Action Node", action_type="DUMMY")
    res = engine.execute(tenant_id="tenant-1", execution_id="exec-1", node=node, context={})

    # Should reuse cached result
    assert res.success is True
    assert res.external_id == "ext-dummy-123"
    assert res.details.get("idempotent_cached") is True
    # Ensure connector execute was never called because it returned cached run
    repo.save_action_execution.assert_not_called()


def test_action_engine_execution_and_persistence():
    repo = MagicMock()
    repo.get_successful_action_execution.return_value = None

    engine = ActionExecutionEngine(repo=repo)
    connector = DummyConnector()
    engine.registry.register("DUMMY", connector)

    node = ExecutionNode(node_id="node-action", step_id="1", step_name="Action Node", action_type="DUMMY")
    res = engine.execute(tenant_id="tenant-1", execution_id="exec-1", node=node, context={})

    assert res.success is True
    assert res.external_id == "ext-dummy-123"
    repo.save_action_execution.assert_called_once()
    # Verify save parameters
    args, kwargs = repo.save_action_execution.call_args
    record = args[1]
    assert record.execution_id == "exec-1"
    assert record.node_id == "node-action"
    assert record.success is True
