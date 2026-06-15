from fastapi.testclient import TestClient
from app.web.main import app
from app.api.schemas import CompileRunbookResponse

client = TestClient(app)


def test_health_endpoint():
    """Verify that the health check endpoint returns 200 OK and correct JSON payload."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_compile_endpoint():
    """Verify compiling a valid runbook over the REST API."""
    runbook_md = (
        "# Authentication Spike Runbook\n"
        "## Steps\n"
        "1. Check auth logs for authentication failures [DETECTION] {data_source=auth_logs}\n"
        "2. Correlate threat intel list [CORRELATION]\n"
    )
    payload = {
        "content": runbook_md,
        "filename": "auth_spike.md"
    }
    response = client.post("/compile", json=payload)
    assert response.status_code == 200
    data = response.json()
    
    # Validate structure using the API response schema
    api_response = CompileRunbookResponse(**data)
    assert api_response.status == "SUCCESS"
    assert api_response.runbook_name == "Authentication Spike Runbook"
    assert api_response.bundle.manifest.skill_name == "Authentication Spike Runbook Skill"
    assert len(api_response.bundle.traces) == 2


def test_export_endpoint():
    """Verify exporting a skill bundle produces correct, sorted JSON representation."""
    runbook_md = (
        "# Escalation SOP\n"
        "## Steps\n"
        "1. Escalate to tier 2 security analyst [ESCALATION]\n"
    )
    payload = {
        "content": runbook_md,
        "filename": "escalation.md"
    }
    # 1. Compile to get a valid bundle
    compile_response = client.post("/compile", json=payload)
    assert compile_response.status_code == 200
    bundle_data = compile_response.json()["bundle"]

    # 2. Export the bundle
    export_response = client.post("/bundle/export", json=bundle_data)
    assert export_response.status_code == 200
    
    exported_data = export_response.json()
    assert "manifest" in exported_data
    assert "agent_skill" in exported_data
    assert "traces" in exported_data
    assert "diagnostics" in exported_data
