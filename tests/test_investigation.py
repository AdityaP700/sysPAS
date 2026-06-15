import json
from unittest import mock
import pytest
from fastapi.testclient import TestClient

from app.agent.investigation_agent import InvestigationAgent, InvestigationStepResult
from app.agent.summary import SummaryGenerator
from app.runtime.models import ExecutionRecord, ExecutionStatus
from app.runtime.query_results import QueryResult
from app.web.dependencies import get_sqlite_repository, get_execution_engine
from app.web.main import app

client = TestClient(app)


def test_investigation_agent_fallback_no_key():
    """Verify fallback behavior when Claude API key is missing."""
    with mock.patch("app.config.settings.settings.claude_api_key", None):
        agent = InvestigationAgent()
        res = agent.analyze_and_next_step(
            current_query="index=main status=failed",
            current_results=[{"user": "admin"}],
            history=[],
            task_description="Investigate failures"
        )
        assert res.investigation_complete is True
        assert res.next_query is None
        assert "API key is missing" in res.reasoning


def test_investigation_agent_success():
    """Verify InvestigationAgent parses Claude output correctly and invokes Claude API."""
    mock_client = mock.MagicMock()
    mock_message = mock.MagicMock()
    mock_message.content = [
        mock.MagicMock(text=json.dumps({
            "investigation_complete": False,
            "next_query": "index=main status=failed user=admin",
            "reasoning": "Looking for admin failures specifically."
        }))
    ]
    mock_client.messages.create.return_value = mock_message

    agent = InvestigationAgent()
    agent._client = mock_client

    res = agent.analyze_and_next_step(
        current_query="index=main status=failed",
        current_results=[{"user": "admin"}],
        history=[],
        task_description="Investigate failures",
        schema_fields=["status", "user", "src_ip", "host"]
    )

    assert res.investigation_complete is False
    assert res.next_query == "index=main status=failed user=admin"
    assert res.reasoning == "Looking for admin failures specifically."
    mock_client.messages.create.assert_called_once()


def test_investigation_agent_re_prompt_on_schema_violation():
    """Verify that the agent automatically re-prompts Claude if the generated SPL violates schema/rules."""
    mock_client = mock.MagicMock()
    
    # First response contains invalid query violating rule (uses semantic failure keyword)
    mock_msg_invalid = mock.MagicMock()
    mock_msg_invalid.content = [
        mock.MagicMock(text=json.dumps({
            "investigation_complete": False,
            "next_query": "index=main failed OR error",  # semantic keywords
            "reasoning": "Analyzing raw errors"
        }))
    ]
    
    # Second response (after re-prompt) returns valid SPL using schema preference
    mock_msg_valid = mock.MagicMock()
    mock_msg_valid.content = [
        mock.MagicMock(text=json.dumps({
            "investigation_complete": False,
            "next_query": "index=main status=failed",
            "reasoning": "Corrected query to use schema fields."
        }))
    ]

    mock_client.messages.create.side_effect = [mock_msg_invalid, mock_msg_valid]

    agent = InvestigationAgent()
    agent._client = mock_client

    res = agent.analyze_and_next_step(
        current_query="index=main",
        current_results=[],
        history=[],
        task_description="Find failures",
        schema_fields=["status", "user", "src_ip", "host"]
    )

    # Should have triggered second call
    assert mock_client.messages.create.call_count == 2
    assert res.next_query == "index=main status=failed"
    assert res.reasoning == "Corrected query to use schema fields."


def test_summary_generator_report_parsing():
    """Verify that SummaryGenerator constructs a structured report with classifications & recommendations."""
    mock_client = mock.MagicMock()
    mock_message = mock.MagicMock()
    mock_message.content = [
        mock.MagicMock(text=json.dumps({
            "incident_type": "Brute Force Attack",
            "severity": "High",
            "confidence": 0.92,
            "affected_hosts": ["10.0.0.12"],
            "affected_users": ["admin", "svc_backup"],
            "root_cause": "Multiple failed logins followed by success.",
            "recommended_actions": {
                "containment": ["Block IP 10.0.0.12"],
                "eradication": ["Remove malicious task scheduler job"],
                "recovery": ["Reset passwords"],
                "prevention": ["Enable MFA"]
            },
            "executive_summary": "Brute force attack compromise threat successfully resolved."
        }))
    ]
    mock_client.messages.create.return_value = mock_message

    generator = SummaryGenerator()
    generator._client = mock_client

    report = generator.generate_report(
        task_description="Investigate suspicious logons",
        history=[]
    )

    assert "Brute Force Attack" in report
    assert "High" in report
    assert "92%" in report
    assert "10.0.0.12" in report
    assert "svc_backup" in report
    assert "Block IP 10.0.0.12" in report
    assert "Enable MFA" in report


def test_summary_generator_fallback():
    """Verify SummaryGenerator fallback report when Claude is unconfigured."""
    with mock.patch("app.config.settings.settings.claude_api_key", None), \
         mock.patch.dict("os.environ", {"RUNBOOKMIND_CLAUDE_API_KEY": "", "ANTHROPIC_API_KEY": ""}):
        generator = SummaryGenerator()
        report = generator.generate_report(
            task_description="Suspicious login check",
            history=[{
                "spl": "index=main status=failed",
                "result_count": 2,
                "sample_results": [{"user": "svc_test", "host": "10.0.0.22"}],
                "reasoning": "Check failure logons."
            }]
        )

        assert "Executive Incident Report" in report
        assert "Suspicious Activity" in report
        assert "svc_test" in report
        assert "10.0.0.22" in report
        assert "Isolate affected hosts" in report


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.anyio
async def test_end_to_end_investigation_loop_in_engine():
    """Verify engine correctly runs the iterative loop, stores context, and exposes results in the API."""
    from app.runtime.engine import ExecutionEngine
    from app.agent.graph import ExecutionGraph, ExecutionNode
    from app.agent.governance import GovernancePolicy, ExecutionMode
    from app.domain.models import AgentSkill, CompiledStep
    import uuid

    # 1. Setup mock repo, engine, and mock query runner
    mock_repo = mock.MagicMock()
    mock_audit_repo = mock.MagicMock()
    
    from app.runtime.runner import SplunkQueryRunner
    class TestQueryRunner(SplunkQueryRunner):
        def __init__(self):
            pass
        def run_query_detailed(self, query, context, tenant_id=None):
            return QueryResult(
                success=True,
                row_count=1,
                rows=[{"status": "failed", "user": "admin", "src_ip": "192.168.1.10"}],
                metadata={"events": [{"raw": "failed log"}], "stats": {"errors": 1}},
                duration_ms=10.0
            )

    mock_query_runner = TestQueryRunner()

    engine = ExecutionEngine(
        repo=mock_repo,
        bundle_store=mock.MagicMock(),
        audit_repo=mock_audit_repo,
        query_runner=mock_query_runner
    )

    # Stub the InvestigationAgent & SummaryGenerator to avoid real API requests
    with mock.patch("app.agent.investigation_agent.InvestigationAgent.analyze_and_next_step") as mock_step, \
         mock.patch("app.agent.summary.SummaryGenerator.generate_report") as mock_report:
        
        # Stop investigation immediately on first iteration
        mock_step.return_value = InvestigationStepResult(
            investigation_complete=True,
            next_query=None,
            reasoning="Incident diagnosed on initial analysis."
        )
        mock_report.return_value = "# Executive Report\nAll is well."

        # Setup runtime execution data structures
        execution_id = "exec_test_loop"
        tenant_id = "test_tenant"

        execution_graph = ExecutionGraph(
            nodes=[ExecutionNode(
                node_id="node_1",
                step_id="step_1",
                step_name="Investigate Splunk",
                compiled_spl="index=main status=failed"
            )],
            edges=[],
            entry_node="node_1"
        )
        gov_policy = GovernancePolicy(approval_required=False, execution_mode=ExecutionMode.AUTO)
        skill = AgentSkill(
            name="CheckLogs",
            source_runbook="Runbook source",
            graph=execution_graph,
            governance=gov_policy,
            steps=[CompiledStep(
                step_id="step_1",
                description="Investigate Splunk",
                compiled_spl="index=main status=failed"
            )]
        )

        # Stub repository calls for policy engine and executions
        mock_repo.get_node_executions.return_value = []
        mock_repo.get_system_flag.return_value = None
        mock_repo.list_policies.return_value = []

        record = ExecutionRecord(
            execution_id=execution_id,
            tenant_id=tenant_id,
            bundle_id="bundle_1",
            bundle_version=1,
            status=ExecutionStatus.RUNNING,
            current_node_id="node_1",
            started_at="2026-06-15T12:00:00Z",
            triggered_by="test_user",
            context_payload={}
        )

        # 2. Run engine execution for the node
        engine._run_loop(record, skill)

        # Verify that outputs were stored in context_payload
        assert "query_results" in record.context_payload
        assert record.context_payload["query_results"] == [{"status": "failed", "user": "admin", "src_ip": "192.168.1.10"}]
        assert "investigation_history" in record.context_payload
        assert "executive_report" in record.context_payload
        assert record.context_payload["executive_report"] == "# Executive Report\nAll is well."

        # 3. Test web routes fetch payload correctly
        mock_repo.get_execution.return_value = record
        mock_repo.get_node_executions.return_value = []
        mock_repo.get_action_executions.return_value = []

        # Override dependency to use our stubbed repository
        app.dependency_overrides[get_sqlite_repository] = lambda: mock_repo
        app.dependency_overrides[get_execution_engine] = lambda: engine

        # Call the GET /executions/{execution_id}/results endpoint
        response = client.get(
            f"/executions/{execution_id}/results",
            headers={"Authorization": "Bearer test_token"}  # Auth dummy
        )
        assert response.status_code == 200
        res_data = response.json()
        assert res_data["results"] == [{"status": "failed", "user": "admin", "src_ip": "192.168.1.10"}]
        assert res_data["executive_report"] == "# Executive Report\nAll is well."
        assert len(res_data["investigation_history"]) == 1

        # Call the GET /executions/{execution_id}/investigation endpoint
        response_investigation = client.get(
            f"/executions/{execution_id}/investigation",
            headers={"Authorization": "Bearer test_token"}
        )
        assert response_investigation.status_code == 200
        inv_data = response_investigation.json()
        assert inv_data["executive_report"] == "# Executive Report\nAll is well."
        assert "query_results" in inv_data
        assert "query_events" in inv_data
        assert "query_stats" in inv_data

        app.dependency_overrides.clear()


def test_resolve_index():
    """Verify natural language data sources map to Splunk indexes and sourcetypes."""
    from app.agent.index_resolver import resolve_index
    
    assert resolve_index("auth_logs") == "index=main sourcetype=security_logs"
    assert resolve_index("Windows Login Events") == "index=main sourcetype=security_logs"
    assert resolve_index("endpoint") == "index=main sourcetype=endpoint_logs"
    assert resolve_index("network_traffic") == "index=main sourcetype=network_traffic"
    assert resolve_index("Unknown Data") == "index=main"
    assert resolve_index("") == "index=main"
    assert resolve_index(None) == "index=main"


def test_mitre_mapper():
    """Verify MITRE ATT&CK mapping logic."""
    from app.agent.mitre_mapper import map_threat_to_mitre
    
    assert map_threat_to_mitre("Brute Force Attack") == ["T1110"]
    assert map_threat_to_mitre("PowerShell Encoded Command") == ["T1059.001"]
    assert map_threat_to_mitre("mimikatz execution detected") == ["T1003"]
    assert map_threat_to_mitre("persistence_registry modification") == ["T1547.001"]
    assert map_threat_to_mitre("Unknown anomaly") == []


def test_threat_classifier_fallback():
    """Verify that ThreatClassifier falls back to rule-based classification when Claude is unconfigured."""
    from app.agent.threat_classifier import ThreatClassifier
    
    classifier = ThreatClassifier()
    # Mock settings to ensure no client is initialized
    with mock.patch("app.config.settings.settings.claude_api_key", None), \
         mock.patch.dict("os.environ", {"RUNBOOKMIND_CLAUDE_API_KEY": "", "ANTHROPIC_API_KEY": ""}):
        
        # Test brute force detection from history query
        res = classifier.classify_threat(
            query_results=[],
            investigation_history=[{
                "spl": "index=main status=failed",
                "result_count": 83,
                "sample_results": [{"user": "admin"}],
                "reasoning": "Looking for login failures"
            }]
        )
        assert res.threat_type == "Brute Force"
        assert res.severity == "HIGH"
        assert res.confidence == 0.94
        assert res.mitre == ["T1110"]
        assert res.risk_score == 99

        # Test powershell encoded detection
        res_pw = classifier.classify_threat(
            query_results=[],
            investigation_history=[{
                "spl": "index=main powershell -encodedcommand abc",
                "result_count": 1,
                "sample_results": [],
                "reasoning": "Checking script executions"
            }]
        )
        assert res_pw.threat_type == "Obfuscated PowerShell"
        assert res_pw.severity == "HIGH"
        assert res_pw.confidence == 0.95
        assert res_pw.mitre == ["T1059.001"]
        assert res_pw.risk_score == 95


@pytest.mark.anyio
async def test_end_to_end_threat_intelligence_layer():
    """Verify that engine executes ThreatClassifier, generates reports with MITRE / evidence, and exposes via API."""
    from app.runtime.engine import ExecutionEngine
    from app.agent.graph import ExecutionGraph, ExecutionNode
    from app.agent.governance import GovernancePolicy, ExecutionMode
    from app.domain.models import AgentSkill, CompiledStep

    mock_repo = mock.MagicMock()
    mock_audit_repo = mock.MagicMock()
    
    from app.runtime.runner import SplunkQueryRunner
    class TestQueryRunner(SplunkQueryRunner):
        def __init__(self):
            pass
        def run_query_detailed(self, query, context, tenant_id=None):
            return QueryResult(
                success=True,
                row_count=1,
                rows=[{"status": "failed", "user": "admin", "src_ip": "10.0.0.12"}],
                metadata={"events": [], "stats": {}},
                duration_ms=10.0
            )

    mock_query_runner = TestQueryRunner()

    engine = ExecutionEngine(
        repo=mock_repo,
        bundle_store=mock.MagicMock(),
        audit_repo=mock_audit_repo,
        query_runner=mock_query_runner
    )

    # Patch out real Claude/Anthropic APIs
    with mock.patch("app.config.settings.settings.claude_api_key", None), \
         mock.patch.dict("os.environ", {"RUNBOOKMIND_CLAUDE_API_KEY": "", "ANTHROPIC_API_KEY": ""}), \
         mock.patch("app.agent.investigation_agent.InvestigationAgent.analyze_and_next_step") as mock_step:
        
        mock_step.return_value = InvestigationStepResult(
            investigation_complete=True,
            next_query=None,
            reasoning="Diagnosed brute force attempt."
        )

        execution_id = "exec_threat_intel_test"
        tenant_id = "test_tenant"

        execution_graph = ExecutionGraph(
            nodes=[ExecutionNode(
                node_id="node_1",
                step_id="step_1",
                step_name="Investigate logons",
                compiled_spl="index=main status=failed"
            )],
            edges=[],
            entry_node="node_1"
        )
        gov_policy = GovernancePolicy(approval_required=False, execution_mode=ExecutionMode.AUTO)
        skill = AgentSkill(
            name="ThreatIntelSkill",
            source_runbook="Brute force runbook",
            graph=execution_graph,
            governance=gov_policy,
            steps=[CompiledStep(
                step_id="step_1",
                description="Investigate logons",
                compiled_spl="index=main status=failed"
            )]
        )

        mock_repo.get_node_executions.return_value = []
        mock_repo.get_system_flag.return_value = None
        mock_repo.list_policies.return_value = []

        record = ExecutionRecord(
            execution_id=execution_id,
            tenant_id=tenant_id,
            bundle_id="bundle_1",
            bundle_version=1,
            status=ExecutionStatus.RUNNING,
            current_node_id="node_1",
            started_at="2026-06-15T12:00:00Z",
            triggered_by="test_user",
            context_payload={}
        )

        # Execute node
        engine._run_loop(record, skill)

        # Asserts on Context Payload
        assert "threat_classification" in record.context_payload
        assert record.context_payload["threat_classification"]["threat_type"] == "Brute Force"
        assert record.context_payload["threat_classification"]["severity"] == "HIGH"
        assert record.context_payload["threat_classification"]["mitre"] == ["T1110"]
        assert record.context_payload["threat_classification"]["risk_score"] == 99
        
        assert "evidence" in record.context_payload
        assert len(record.context_payload["evidence"]) == 1
        assert record.context_payload["evidence"][0]["query"] == "index=main status=failed"

        # Assert report format contains MITRE and evidence
        report = record.context_payload["executive_report"]
        assert "**MITRE ATT&CK**: T1110" in report
        assert "## Investigation Evidence" in report
        assert "Evidence 1" in report
        assert "index=main status=failed" in report

        # Test route integrations
        mock_repo.get_execution.return_value = record
        mock_repo.get_node_executions.return_value = []
        mock_repo.get_action_executions.return_value = []

        app.dependency_overrides[get_sqlite_repository] = lambda: mock_repo
        app.dependency_overrides[get_execution_engine] = lambda: engine

        # Results Endpoint
        response = client.get(
            f"/executions/{execution_id}/results",
            headers={"Authorization": "Bearer test_token"}
        )
        assert response.status_code == 200
        res_data = response.json()
        assert "threat_classification" in res_data
        assert res_data["threat_classification"]["threat_type"] == "Brute Force"
        assert res_data["threat_classification"]["risk_score"] == 99
        assert "evidence" in res_data
        assert len(res_data["evidence"]) == 1

        # Investigation Endpoint
        response_inv = client.get(
            f"/executions/{execution_id}/investigation",
            headers={"Authorization": "Bearer test_token"}
        )
        assert response_inv.status_code == 200
        inv_data = response_inv.json()
        assert "threat_classification" in inv_data
        assert inv_data["threat_classification"]["threat_type"] == "Brute Force"
        assert "evidence" in inv_data

        app.dependency_overrides.clear()

