import uuid
import json
from datetime import datetime, timezone
from typing import Optional, List, Any, Dict
from app.storage.models import BundleRecord
from app.governance.models import DeploymentRecord, DeploymentSnapshotRecord, DeploymentApprovalRecord, PolicyType


class DeploymentService:
    """Orchestrates environment promotions, rollbacks, and approvals with snapshot integrity."""

    def __init__(self, repo: Any, policy_engine: Any):
        self.repo = repo
        self.policy_engine = policy_engine

    def validate_promotion(self, tenant_id: str, bundle_id: str, target_environment: str) -> Dict[str, Any]:
        """
        Runs a dry-run policy evaluation check for promoting a bundle.
        Returns evaluation decision dict.
        """
        bundle = self.repo.get_bundle(tenant_id, bundle_id)
        if not bundle:
            return {"allowed": False, "violations": [f"Bundle '{bundle_id}' not found."]}

        context = {
            "resource_id": bundle_id,
            "bundle_id": bundle_id,
            "version": bundle.version,
            "environment": target_environment,
            "action": "PROMOTION"
        }

        decision = self.policy_engine.evaluate(tenant_id, PolicyType.DEPLOYMENT, context)
        return {
            "allowed": decision.allowed,
            "violations": decision.violations,
            "warnings": decision.warnings,
            "matched_policy_id": decision.matched_policy_id,
            "matched_policy_version": decision.matched_policy_version
        }

    def promote_bundle(
        self, tenant_id: str, bundle_id: str, target_environment: str, approver: Optional[str] = None, comments: Optional[str] = None
    ) -> BundleRecord:
        """
        Promotes a bundle to a target environment.
        Ensures deployment policy evaluations are satisfied.
        Requires approval for PRODUCTION target environment.
        """
        bundle = self.repo.get_bundle(tenant_id, bundle_id)
        if not bundle:
            raise ValueError(f"Bundle '{bundle_id}' not found.")

        # 1. Evaluate deployment policies
        validation = self.validate_promotion(tenant_id, bundle_id, target_environment)
        if not validation["allowed"]:
            raise ValueError(f"Promotion blocked by policy: {validation['violations']}")

        deployment_id = str(uuid.uuid4())
        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # 2. Handle restricted production environment approvals
        status = "SUCCESS"
        if target_environment == "PRODUCTION":
            if approver:
                # Direct approval provided
                approval = DeploymentApprovalRecord(
                    approval_id=str(uuid.uuid4()),
                    deployment_id=deployment_id,
                    tenant_id=tenant_id,
                    approved_by=approver,
                    approved_at=now_str,
                    decision="APPROVED",
                    comments=comments or "Approved during promotion call"
                )
                self.repo.save_deployment_approval(tenant_id, approval)
            else:
                # No approver provided. Check if there is already an approval
                existing = self.repo.get_deployment_approvals(tenant_id, deployment_id)
                approved = [a for a in existing if a.decision == "APPROVED"]
                if not approved:
                    # Create pending deployment and approval request, then pause
                    dep_record = DeploymentRecord(
                        deployment_id=deployment_id,
                        tenant_id=tenant_id,
                        bundle_id=bundle_id,
                        version=bundle.version,
                        environment=target_environment,
                        status="PENDING",
                        created_at=now_str
                    )
                    self.repo.save_deployment(tenant_id, dep_record)
                    
                    pending_app = DeploymentApprovalRecord(
                        approval_id=str(uuid.uuid4()),
                        deployment_id=deployment_id,
                        tenant_id=tenant_id,
                        approved_by=None,
                        approved_at=None,
                        decision="PENDING",
                        comments="Awaiting operator authorization"
                    )
                    self.repo.save_deployment_approval(tenant_id, pending_app)
                    raise ValueError("Promotion to PRODUCTION requires explicit approval.")

        # 3. Save snapshot before updating
        snapshot = DeploymentSnapshotRecord(
            snapshot_id=str(uuid.uuid4()),
            deployment_id=deployment_id,
            tenant_id=tenant_id,
            bundle_payload=bundle.payload,
            created_at=now_str
        )
        self.repo.save_deployment_snapshot(tenant_id, snapshot)

        # 4. Save Deployment run record
        dep_record = DeploymentRecord(
            deployment_id=deployment_id,
            tenant_id=tenant_id,
            bundle_id=bundle_id,
            version=bundle.version,
            environment=target_environment,
            status=status,
            created_at=now_str
        )
        self.repo.save_deployment(tenant_id, dep_record)

        # 5. Create new bundle version with updated environment
        new_version = bundle.version + 1
        promoted_bundle = BundleRecord(
            bundle_id=bundle.bundle_id,
            bundle_name=bundle.bundle_name,
            version=new_version,
            created_at=now_str,
            status="COMPILED",
            payload=bundle.payload,
            tenant_id=tenant_id,
            created_by=approver or bundle.created_by,
            environment=target_environment,
            promotion_status="APPROVED" if target_environment == "PRODUCTION" else "PROMOTED"
        )
        self.repo.save_bundle(tenant_id, promoted_bundle)

        # Log audit event
        from app.audit.models import AuditEventRecord
        audit_rec = AuditEventRecord(
            audit_id=str(uuid.uuid4()),
            timestamp=now_str,
            user_id=approver or "system",
            role="admin" if approver else "system",
            action="BUNDLE_PROMOTE",
            resource_type="BUNDLE",
            resource_id=bundle_id,
            status="SUCCESS",
            details={
                "target_environment": target_environment,
                "version": new_version,
                "deployment_id": deployment_id
            },
            tenant_id=tenant_id
        )
        self.repo.save_audit_event(tenant_id, audit_rec)

        return promoted_bundle

    def rollback_bundle(self, tenant_id: str, bundle_id: str, target_version: int, actor: str) -> BundleRecord:
        """
        Rolls back a bundle to a prior version.
        Uses stored deployment snapshots if available, or falls back to direct version retrieval.
        """
        # Find snapshot with the target version payload
        snapshots = self.repo.get_versions(tenant_id, bundle_id)
        target_bundle = None
        for b in snapshots:
            if b.version == target_version:
                target_bundle = b
                break

        if not target_bundle:
            raise ValueError(f"Target version {target_version} not found for bundle '{bundle_id}'")

        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        latest = self.repo.get_bundle(tenant_id, bundle_id)
        new_version = (latest.version if latest else target_version) + 1

        rolled_bundle = BundleRecord(
            bundle_id=bundle_id,
            bundle_name=target_bundle.bundle_name,
            version=new_version,
            created_at=now_str,
            status="COMPILED",
            payload=target_bundle.payload,
            tenant_id=tenant_id,
            created_by=actor,
            environment=target_bundle.environment,
            promotion_status="DRAFT"  # Reset status on rollback
        )
        self.repo.save_bundle(tenant_id, rolled_bundle)

        # Log audit event
        from app.audit.models import AuditEventRecord
        audit_rec = AuditEventRecord(
            audit_id=str(uuid.uuid4()),
            timestamp=now_str,
            user_id=actor,
            role="admin",
            action="BUNDLE_ROLLBACK",
            resource_type="BUNDLE",
            resource_id=bundle_id,
            status="SUCCESS",
            details={
                "target_version": target_version,
                "new_version": new_version
            },
            tenant_id=tenant_id
        )
        self.repo.save_audit_event(tenant_id, audit_rec)

        return rolled_bundle
