import pytest
import os
import tempfile
from app.storage.sqlite import SQLiteRepository
from app.storage.models import BundleRecord
from app.governance.models import PolicyRecord, PolicyType
from app.governance.policy_engine import PolicyEngine
from app.deployment.service import DeploymentService


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    repo = SQLiteRepository(path)
    
    yield repo
    
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def test_tenant_policy_isolation(temp_db):
    repo = temp_db
    engine = PolicyEngine(repo)

    # 1. Define a deny policy in tenant-1 blocking PAGERDUTY
    p_deny = PolicyRecord(
        policy_id="p-t1",
        tenant_id="tenant-1",
        name="Block PagerDuty",
        policy_type=PolicyType.EXECUTION,
        enabled=True,
        priority=100,
        version=1,
        is_current=True,
        policy_definition=[
            {
                "if": {"connector_type": "PAGERDUTY"},
                "then": {"allowed": False, "message": "PagerDuty blocked in tenant-1"}
            }
        ],
        created_at="2026-06-13T12:00:00Z",
        updated_at="2026-06-13T12:00:00Z"
    )
    repo.save_policy("tenant-1", p_deny)

    # 2. Evaluate in tenant-1 context -> Deny
    decision_t1 = engine.evaluate("tenant-1", PolicyType.EXECUTION, {"connector_type": "PAGERDUTY"})
    assert decision_t1.allowed is False

    # 3. Evaluate in tenant-2 context -> Allow (no policy active in tenant-2)
    decision_t2 = engine.evaluate("tenant-2", PolicyType.EXECUTION, {"connector_type": "PAGERDUTY"})
    assert decision_t2.allowed is True
