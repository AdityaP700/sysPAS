import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from app.governance.models import PolicyRecord, PolicyDecision, PolicyType, PolicyEventRecord


class PolicyEngine:
    """Centralized policy engine evaluating compliance and governance constraints."""

    def __init__(self, repo: Any):
        self.repo = repo

    def evaluate(self, tenant_id: str, policy_type: PolicyType, context: Dict[str, Any]) -> PolicyDecision:
        """
        Evaluates active tenant policies for the given type against a context.
        Also checks global system flags (e.g. WORKFLOW_EXECUTION_DISABLED).
        """
        # 1. Check global system flags for execution policies
        if policy_type == PolicyType.EXECUTION:
            flag_val = self.repo.get_system_flag("WORKFLOW_EXECUTION_DISABLED")
            if flag_val == "true":
                decision = PolicyDecision(
                    allowed=False,
                    violations=["Global kill switch active: WORKFLOW_EXECUTION_DISABLED is set to true."],
                    warnings=[]
                )
                self._audit_policy_event(tenant_id, None, "SYSTEM", "WORKFLOW_EXECUTION_DISABLED", "DENY")
                return decision

        # 2. Retrieve all active policies for the tenant
        policies = self.repo.list_policies(tenant_id)
        # Filter by type
        type_policies = [p for p in policies if p.policy_type == policy_type and p.enabled]

        matched_rules = []

        # 3. Match rules across all active policies
        for policy in type_policies:
            for rule in policy.policy_definition:
                if self._rule_matches(rule.get("if", {}), context):
                    matched_rules.append({
                        "policy_id": policy.policy_id,
                        "policy_name": policy.name,
                        "version": policy.version,
                        "priority": policy.priority,
                        "rule": rule
                    })

        # 4. Apply priority & conflict resolution
        if not matched_rules:
            # Default allow if no rules matched
            return PolicyDecision(allowed=True, violations=[], warnings=[])

        # Sort matched rules by priority DESC
        matched_rules.sort(key=lambda x: x["priority"], reverse=True)
        highest_priority = matched_rules[0]["priority"]

        # Filter rules with the highest priority
        top_rules = [r for r in matched_rules if r["priority"] == highest_priority]

        # Check if any top rule denies
        deny_rule = None
        allow_rules = []
        for r in top_rules:
            then_block = r["rule"].get("then", {})
            if not then_block.get("allowed", True):
                deny_rule = r
                break
            else:
                allow_rules.append(r)

        if deny_rule:
            then_block = deny_rule["rule"].get("then", {})
            message = then_block.get("message", f"Denied by policy '{deny_rule['policy_name']}'")
            decision = PolicyDecision(
                allowed=False,
                matched_policy_id=deny_rule["policy_id"],
                matched_policy_version=deny_rule["version"],
                matched_rule=deny_rule["rule"],
                violations=[message],
                warnings=[]
            )
            # Log audit event
            self._audit_policy_event(
                tenant_id,
                deny_rule["policy_id"],
                "POLICY",
                context.get("resource_id", "unknown"),
                "DENY",
                expires_in_days=30
            )
            return decision

        # If we got here, all highest priority matched rules allow
        first_allow = allow_rules[0]
        decision = PolicyDecision(
            allowed=True,
            matched_policy_id=first_allow["policy_id"],
            matched_policy_version=first_allow["version"],
            matched_rule=first_allow["rule"],
            violations=[],
            warnings=[]
        )
        self._audit_policy_event(
            tenant_id,
            first_allow["policy_id"],
            "POLICY",
            context.get("resource_id", "unknown"),
            "ALLOW",
            expires_in_days=30
        )
        return decision

    def simulate(self, tenant_id: str, context: Dict[str, Any], policy_definition: List[Dict[str, Any]]) -> PolicyDecision:
        """
        Simulates evaluation of rules (policy_definition) against a context.
        Does not persist anything.
        """
        matched_rules = []
        for rule in policy_definition:
            if self._rule_matches(rule.get("if", {}), context):
                matched_rules.append(rule)

        if not matched_rules:
            return PolicyDecision(allowed=True, violations=[], warnings=[])

        # If any matches say allowed=False, deny
        violations = []
        deny_rule = None
        for rule in matched_rules:
            then_block = rule.get("then", {})
            if not then_block.get("allowed", True):
                violations.append(then_block.get("message", "Denied by simulated rule"))
                deny_rule = rule

        if violations:
            return PolicyDecision(
                allowed=False,
                matched_policy_id="simulation",
                matched_policy_version=1,
                matched_rule=deny_rule,
                violations=violations,
                warnings=[]
            )

        return PolicyDecision(
            allowed=True,
            matched_policy_id="simulation",
            matched_policy_version=1,
            matched_rule=matched_rules[0],
            violations=[],
            warnings=[]
        )

    def rollback_policy(self, tenant_id: str, policy_id: str, target_version: int) -> PolicyRecord:
        """
        Rolls back a policy to a target version by updating the active version.
        Marks all other versions as not current, and target as current.
        """
        policy = self.repo.get_policy(tenant_id, policy_id, target_version)
        if not policy:
            raise ValueError(f"Policy version {target_version} not found for ID '{policy_id}'")

        # Prepare rolled-back record marked as current
        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        rolled_back = PolicyRecord(
            policy_id=policy.policy_id,
            tenant_id=policy.tenant_id,
            name=policy.name,
            policy_type=policy.policy_type,
            enabled=policy.enabled,
            priority=policy.priority,
            version=policy.version,
            is_current=True,
            policy_definition=policy.policy_definition,
            created_at=policy.created_at,
            updated_at=now_str
        )
        self.repo.save_policy(tenant_id, rolled_back)
        
        # Save audit event
        from app.audit.models import AuditEventRecord
        from app.audit.repository import AuditRepository
        audit_rec = AuditEventRecord(
            audit_id=str(uuid.uuid4()),
            timestamp=now_str,
            user_id="system",
            role="admin",
            action="POLICY_ROLLBACK",
            resource_type="POLICY",
            resource_id=policy_id,
            status="SUCCESS",
            details={
                "target_version": target_version,
                "policy_name": policy.name
            },
            tenant_id=tenant_id
        )
        self.repo.save_audit_event(tenant_id, audit_rec)
        return rolled_back

    def _rule_matches(self, rule_if: Dict[str, Any], context: Dict[str, Any]) -> bool:
        """Helper to evaluate if context matches rule criteria."""
        if not rule_if:
            return True  # Empty if means unconditional match

        for key, value in rule_if.items():
            if key not in context:
                return False

            ctx_value = context[key]

            if isinstance(value, list):
                if ctx_value not in value:
                    return False
            else:
                if ctx_value != value:
                    return False

        return True

    def _audit_policy_event(
        self, tenant_id: str, policy_id: Optional[str], resource_type: str, resource_id: str, decision: str, expires_in_days: int = 30
    ) -> None:
        """Helper to log policy evaluation events with retention timestamps."""
        now = datetime.now(timezone.utc)
        now_str = now.isoformat().replace("+00:00", "Z")
        import datetime as dt
        expires_at = (now + dt.timedelta(days=expires_in_days)).isoformat().replace("+00:00", "Z")

        event = PolicyEventRecord(
            event_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            policy_id=policy_id,
            resource_type=resource_type,
            resource_id=resource_id,
            decision=decision,
            timestamp=now_str,
            expires_at=expires_at
        )
        self.repo.save_policy_event(tenant_id, event)
