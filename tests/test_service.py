from app.service.runbook_service import RunbookService
from app.api.schemas import CompileRunbookResponse


def test_service_compilation_success():
    service = RunbookService()
    
    runbook_md = """# Critical Outage SOP
Description of critical outage checking index auth.

1. Check auth logs for spikes > 100 failures in 5 min
2. If external, block IP and create JIRA ticket
"""
    
    response = service.compile_runbook(runbook_md, "critical_outage.md")
    
    # Assert return types and schemas
    assert isinstance(response, CompileRunbookResponse)
    assert response.status == "SUCCESS"
    assert response.runbook_name == "Critical Outage SOP"
    
    # Assert bundle contents
    bundle = response.bundle
    assert bundle.manifest.skill_name == "Critical Outage SOP Skill"
    assert bundle.manifest.overall_confidence > 0.0
    assert len(bundle.agent_skill.graph.nodes) == 2
    assert len(bundle.traces) == 2
    
    # Confirm no errors in successfully compiled runbook
    assert len(response.errors) == 0


def test_service_compilation_failure_invalid_runbook():
    service = RunbookService()
    
    # Runbook failing validation (contains step with duplicate ID or empty step)
    # Or parsing failure due to empty content
    response = service.compile_runbook("", "empty.md")
    
    assert response.status == "FAILED"
    assert len(response.errors) > 0
    assert "Parsing failed" in response.errors[0]
