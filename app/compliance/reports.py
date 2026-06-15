import json
import uuid
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, List
from app.governance.models import ComplianceSnapshotRecord


def generate_compliance_report(tenant_id: str, repo: Any, report_type: str = "FULL") -> ComplianceSnapshotRecord:
    """
    Collects policy events and deployment histories, serializes them to a JSON structure,
    computes a SHA-256 integrity checksum, and stores the snapshot.
    """
    now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # 1. Fetch data from repository
    policy_events = repo.list_policy_events(tenant_id)
    deployments = repo.list_deployments(tenant_id)

    # 2. Structure the report payload
    report_data = {
        "generated_at": now_str,
        "tenant_id": tenant_id,
        "report_type": report_type,
        "policy_events": [
            {
                "event_id": e.event_id,
                "policy_id": e.policy_id,
                "resource_type": e.resource_type,
                "resource_id": e.resource_id,
                "decision": e.decision,
                "timestamp": e.timestamp
            } for e in policy_events
        ],
        "deployments": [
            {
                "deployment_id": d.deployment_id,
                "bundle_id": d.bundle_id,
                "version": d.version,
                "environment": d.environment,
                "status": d.status,
                "created_at": d.created_at
            } for d in deployments
        ]
    }

    # 3. Calculate cryptographic SHA-256 hash of serialization payload
    serialized = json.dumps(report_data, sort_keys=True)
    report_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    # 4. Save to repository
    snapshot = ComplianceSnapshotRecord(
        snapshot_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        report_type=report_type,
        report_data=report_data,
        snapshot_hash=report_hash,
        created_at=now_str
    )
    repo.save_compliance_snapshot(tenant_id, snapshot)

    return snapshot


def export_report_to_csv(report_data: Dict[str, Any]) -> str:
    """
    Converts compliance report data to an auditable CSV formatted string.
    """
    lines = []
    lines.append("COMPLIANCE REPORT SUMMARY")
    lines.append(f"Generated At,{report_data.get('generated_at', '')}")
    lines.append(f"Tenant ID,{report_data.get('tenant_id', '')}")
    lines.append(f"Report Type,{report_data.get('report_type', '')}")
    lines.append("")

    lines.append("POLICY EVENTS")
    lines.append("Event ID,Policy ID,Resource Type,Resource ID,Decision,Timestamp")
    for event in report_data.get("policy_events", []):
        lines.append(
            f"{event['event_id']},{event.get('policy_id') or 'N/A'},{event['resource_type']},{event['resource_id']},{event['decision']},{event['timestamp']}"
        )
    lines.append("")

    lines.append("ENVIRONMENT PROMOTIONS")
    lines.append("Deployment ID,Bundle ID,Version,Environment,Status,Timestamp")
    for dep in report_data.get("deployments", []):
        lines.append(
            f"{dep['deployment_id']},{dep['bundle_id']},{dep['version']},{dep['environment']},{dep['status']},{dep['created_at']}"
        )

    return "\n".join(lines)
