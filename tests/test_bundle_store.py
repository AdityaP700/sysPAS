import os
import tempfile
import pytest
from app.package.bundle import SkillBundle
from app.package.manifest import AgentSkillManifest
from app.domain.models import AgentSkill
from app.agent.graph import ExecutionGraph
from app.agent.governance import GovernancePolicy, ExecutionMode
from app.storage.sqlite import SQLiteRepository
from app.storage.bundle_store import BundleStore


@pytest.fixture
def temp_db_file():
    """Create a temporary database file and clean it up after the test completes."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


@pytest.fixture
def mock_skill_bundle():
    manifest = AgentSkillManifest(
        skill_name="Brute Force SOP Skill",
        compiler_version="1.0.0",
        created_at="2026-06-12T00:00:00Z",
        overall_confidence=0.9,
    )
    skill = AgentSkill(
        name="Brute Force SOP",
        source_runbook="brute_force.md",
        graph=ExecutionGraph(nodes=[], edges=[]),
        governance=GovernancePolicy(
            approval_required=True,
            execution_mode=ExecutionMode.MANUAL
        )
    )
    return SkillBundle(
        manifest=manifest,
        agent_skill=skill,
        diagnostics={"errors": [], "warnings": []},
        traces=[]
    )


def test_bundle_store_uuid_routing_and_versioning(temp_db_file, mock_skill_bundle):
    """Test saving same name resolves to same UUID, auto-increments version, and retrieves versions."""
    repo = SQLiteRepository(temp_db_file)
    store = BundleStore(repo)

    # 1. Save first version
    rec_v1 = store.save_bundle("Brute Force SOP", mock_skill_bundle, "SUCCESS")
    assert rec_v1.version == 1
    assert rec_v1.payload["manifest"]["version"] == "1"
    uuid_1 = rec_v1.bundle_id

    # 2. Save second version of same bundle
    mock_skill_bundle.manifest.created_at = "2026-06-12T00:10:00Z"
    rec_v2 = store.save_bundle("Brute Force SOP", mock_skill_bundle, "SUCCESS")
    assert rec_v2.version == 2
    assert rec_v2.payload["manifest"]["version"] == "2"
    assert rec_v2.bundle_id == uuid_1  # same name maps to same UUID

    # 3. Save a different bundle name
    other_bundle = mock_skill_bundle.model_copy(deep=True)
    other_bundle.manifest.skill_name = "Escalation SOP Skill"
    rec_other = store.save_bundle("Escalation SOP", other_bundle, "PARTIAL")
    assert rec_other.version == 1
    assert rec_other.bundle_id != uuid_1  # different name maps to different UUID

    # 4. Retrieve list of latest unique bundles
    latest_bundles = store.list_bundles()
    assert len(latest_bundles) == 2
    
    # Sort by name to check contents reliably
    latest_bundles.sort(key=lambda x: x.bundle_name)
    assert latest_bundles[0].bundle_name == "Brute Force SOP"
    assert latest_bundles[0].version == 2
    assert latest_bundles[1].bundle_name == "Escalation SOP"
    assert latest_bundles[1].version == 1

    # 5. Retrieve versions history
    versions = store.get_versions(uuid_1)
    assert len(versions) == 2
    assert versions[0].version == 1
    assert versions[1].version == 2
