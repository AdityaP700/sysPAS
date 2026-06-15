import time
import uuid
import traceback
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from app.storage.sqlite import SQLiteRepository
from app.storage.bundle_store import BundleStore
from app.audit.repository import SQLiteAuditRepository
from app.audit.models import AuditEventRecord
from app.runtime.models import ExecutionRecord, NodeExecutionRecord, ApprovalRecord, ExecutionStatus, ApprovalStatus, FailureCategory
from app.runtime.runner import BaseQueryRunner
from app.runtime.evaluator import BranchEvaluator
from app.runtime.governance import GovernanceRuntime
from app.agent.governance import GovernancePolicy
from app.domain.models import AgentSkill
from app.observability.logging import logger


class ExecutionEngine:
    """Core runtime engine responsible for executing compiled Agent Skills, evaluating routing branches, and enforcing approvals."""

    def __init__(
        self,
        repo: SQLiteRepository,
        bundle_store: BundleStore,
        audit_repo: Optional[SQLiteAuditRepository],
        query_runner: BaseQueryRunner,
        max_nodes_executed: int = 50,
    ):
        self.repo = repo
        self.bundle_store = bundle_store
        self.audit_repo = audit_repo
        self.query_runner = query_runner
        self.max_nodes_executed = max_nodes_executed
        from app.actions.engine import ActionExecutionEngine
        self.action_engine = ActionExecutionEngine(repo, audit_repo)

    def _log_audit(
        self,
        tenant_id: str,
        action: str,
        resource_id: Optional[str],
        status: str,
        user_id: str,
        details: Optional[dict] = None,
    ) -> None:
        """Helper to create audit trace log entries during execution."""
        if not self.audit_repo:
            return
        # Fetch correlation IDs from context if any, or generate new
        record = AuditEventRecord(
            audit_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            request_id=str(uuid.uuid4()),
            correlation_id=str(uuid.uuid4()),
            user_id=user_id,
            role="SYSTEM",
            action=action,
            resource_type="execution",
            resource_id=resource_id,
            status=status,
            details=details or {},
            tenant_id=tenant_id,
        )
        self.audit_repo.save_audit_event(tenant_id, record)

    def execute(
        self,
        tenant_id: str,
        bundle_id: str,
        version: int,
        triggered_by: str,
        initial_input: Dict[str, Any],
        execution_id: Optional[str] = None,
    ) -> ExecutionRecord:
        """Triggers a new compiled skill execution instance and begins execution traversal."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if not execution_id:
            execution_id = f"exec_{uuid.uuid4().hex[:12]}"

        # Load target compiled skill bundle
        record = self.bundle_store.get_bundle(bundle_id, version, tenant_id=tenant_id)
        if not record:
            raise ValueError(f"Bundle '{bundle_id}' version {version} not found in tenant '{tenant_id}'")

        from app.package.bundle import SkillBundle
        skill_bundle = SkillBundle(**record.payload)
        skill = skill_bundle.agent_skill

        entry_node = skill.graph.entry_node
        if not entry_node:
            raise ValueError("Execution graph lacks a valid entry_node")

        # Initialize context state with initial inputs
        context = dict(initial_input)

        exec_record = ExecutionRecord(
            execution_id=execution_id,
            tenant_id=tenant_id,
            bundle_id=bundle_id,
            bundle_version=version,
            status=ExecutionStatus.RUNNING,
            current_node_id=entry_node,
            started_at=now,
            triggered_by=triggered_by,
            context_payload=context,
        )

        self.repo.save_execution(tenant_id, exec_record)
        self._log_audit(
            tenant_id=tenant_id,
            action="START_EXECUTION",
            resource_id=execution_id,
            status="SUCCESS",
            user_id=triggered_by,
            details={"bundle_id": bundle_id, "version": version, "initial_input": initial_input},
        )

        return self._run_loop(exec_record, skill)

    def resume(
        self,
        execution_id: str,
        decider_id: str,
        decision: ApprovalStatus,
        tenant_id: str,
    ) -> ExecutionRecord:
        """Resumes a paused execution run upon receiving a human-in-the-loop approval decision."""
        exec_record = self.repo.get_execution(tenant_id, execution_id)
        if not exec_record:
            raise ValueError(f"Execution run '{execution_id}' not found in tenant '{tenant_id}'")

        if exec_record.status != ExecutionStatus.RUNNING:
            raise ValueError(f"Execution run '{execution_id}' is not in a resumeable state (current: {exec_record.status})")

        # Fetch active approval record
        approval = self.repo.get_approval_by_execution(tenant_id, execution_id)
        if not approval or approval.decision is not None:
            raise ValueError(f"No active pending approval found for execution '{execution_id}'")

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # Update approval record
        approval.decision = decision
        approval.decided_at = now
        approval.decided_by = decider_id
        self.repo.save_approval(tenant_id, approval)

        # Log approval audit decision
        audit_action = "APPROVAL_GRANTED" if decision == ApprovalStatus.APPROVED else "APPROVAL_REJECTED"
        self._log_audit(
            tenant_id=tenant_id,
            action=audit_action,
            resource_id=execution_id,
            status="SUCCESS",
            user_id=decider_id,
            details={"node_id": approval.node_id},
        )

        # Load skill bundle
        bundle_rec = self.bundle_store.get_bundle(exec_record.bundle_id, exec_record.bundle_version, tenant_id=tenant_id)
        from app.package.bundle import SkillBundle
        skill_bundle = SkillBundle(**bundle_rec.payload)
        skill = skill_bundle.agent_skill

        if decision == ApprovalStatus.APPROVED:
            # Continue execution, bypassing the gate check on this specific node since it is now approved
            return self._run_loop(exec_record, skill, bypass_gate_node=approval.node_id)
        else:
            # Terminate workflow on rejection
            exec_record.status = ExecutionStatus.FAILED
            exec_record.failure_category = FailureCategory.APPROVAL_REJECTED
            exec_record.completed_at = now
            self.repo.save_execution(tenant_id, exec_record)
            self._log_audit(
                tenant_id=tenant_id,
                action="EXECUTION_FAILED",
                resource_id=execution_id,
                status="SUCCESS",
                user_id=decider_id,
                details={"error": "Approval rejected by administrator", "node_id": approval.node_id, "failure_category": FailureCategory.APPROVAL_REJECTED},
            )
            return exec_record

    def cancel(self, execution_id: str, tenant_id: str, cancelled_by: str) -> ExecutionRecord:
        """Forces cancellation of an active running or paused execution."""
        exec_record = self.repo.get_execution(tenant_id, execution_id)
        if not exec_record:
            raise ValueError(f"Execution run '{execution_id}' not found in tenant '{tenant_id}'")

        if exec_record.status not in (ExecutionStatus.PENDING, ExecutionStatus.RUNNING):
            raise ValueError(f"Execution run '{execution_id}' cannot be cancelled (current status: {exec_record.status})")

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        exec_record.status = ExecutionStatus.CANCELLED
        exec_record.completed_at = now
        self.repo.save_execution(tenant_id, exec_record)

        self._log_audit(
            tenant_id=tenant_id,
            action="EXECUTION_CANCELLED",
            resource_id=execution_id,
            status="SUCCESS",
            user_id=cancelled_by,
        )

        return exec_record

    def _run_loop(
        self,
        record: ExecutionRecord,
        skill: AgentSkill,
        bypass_gate_node: Optional[str] = None,
    ) -> ExecutionRecord:
        """Internal execution loop traversing steps and branch routing conditions."""
        current_node_id = record.current_node_id
        tenant_id = record.tenant_id
        execution_id = record.execution_id

        # Find all nodes in the skill graph
        nodes_map = {n.node_id: n for n in skill.graph.nodes}
        visited_nodes: List[str] = []

        # Reconstruct execution depth from existing node executions to protect against restart loops
        existing_nodes = self.repo.get_node_executions(tenant_id, execution_id)
        executed_count = len(existing_nodes)

        try:
            while current_node_id is not None:
                # 1. Cycle & Loop Protection
                if executed_count >= self.max_nodes_executed:
                    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    record.status = ExecutionStatus.FAILED
                    record.failure_category = FailureCategory.RUNTIME_ERROR
                    record.completed_at = now
                    self.repo.save_execution(tenant_id, record)
                    self._log_audit(
                        tenant_id=tenant_id,
                        action="EXECUTION_FAILED",
                        resource_id=execution_id,
                        status="ERROR",
                        user_id=record.triggered_by,
                        details={"error": f"Max execution depth limit of {self.max_nodes_executed} nodes exceeded.", "failure_category": FailureCategory.RUNTIME_ERROR},
                    )
                    return record

                node = nodes_map.get(current_node_id)
                if not node:
                    raise ValueError(f"Graph traversal error: node '{current_node_id}' not found in execution graph")

                # Update the cursor in database
                record.current_node_id = current_node_id
                self.repo.save_execution(tenant_id, record)

                # Evaluate governance policies dynamically
                from app.governance.policy_engine import PolicyEngine
                from app.governance.models import PolicyType
                policy_engine = PolicyEngine(self.repo)

                bundle_rec = self.repo.get_bundle(tenant_id, record.bundle_id, record.bundle_version)
                bundle_env = bundle_rec.environment if bundle_rec else "DEV"

                policy_context = {
                    "resource_id": current_node_id,
                    "action": node.action_type or "",
                    "environment": bundle_env,
                }
                if node.action_type:
                    policy_context["connector_type"] = node.action_type

                decision = policy_engine.evaluate(tenant_id, PolicyType.EXECUTION, policy_context)
                if not decision.allowed:
                    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    record.status = ExecutionStatus.FAILED
                    record.failure_category = FailureCategory.POLICY_VIOLATION
                    record.completed_at = now
                    self.repo.save_execution(tenant_id, record)
                    self._log_audit(
                        tenant_id=tenant_id,
                        action="EXECUTION_FAILED",
                        resource_id=execution_id,
                        status="ERROR",
                        user_id=record.triggered_by,
                        details={
                            "error": f"Execution blocked by policy: {decision.violations[0] if decision.violations else 'Policy violation'}",
                            "failure_category": FailureCategory.POLICY_VIOLATION.value,
                            "matched_policy_id": decision.matched_policy_id,
                            "matched_policy_version": decision.matched_policy_version
                        },
                    )
                    return record

                # 2. Check Governance Policy Gate
                # Skip if we are resuming/bypassing gate on this specific node
                if current_node_id != bypass_gate_node:
                    gate_decision = GovernanceRuntime.evaluate_gate(skill.governance, node.action_type or "")
                    logger.info(
                        f"[Execution {execution_id}] Node: '{current_node_id}', "
                        f"Action Type: '{node.action_type}', "
                        f"Execution Mode: '{skill.governance.execution_mode}', "
                        f"Gate Decision: '{gate_decision}'"
                    )
                    if gate_decision == "PAUSE_APPROVAL":
                        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                        approval_rec = ApprovalRecord(
                            approval_id=f"appr_{uuid.uuid4().hex[:12]}",
                            execution_id=execution_id,
                            node_id=current_node_id,
                            requested_at=now,
                        )
                        self.repo.save_approval(tenant_id, approval_rec)
                        
                        # Create new collaboration approval request with escalation policy
                        try:
                            from app.collaboration.approval_service import ApprovalService
                            approval_svc = ApprovalService(self.repo)
                            escalation_policy = getattr(skill.governance, "escalation_policy", None) or {
                                "levels": [
                                    {"level": 1, "approver": "ops-team@runbookmind.local", "timeout_seconds": 60},
                                    {"level": 2, "approver": "manager@runbookmind.local", "timeout_seconds": 120}
                                ]
                            }
                            approval_svc.create_approval_request(
                                tenant_id=tenant_id,
                                execution_id=execution_id,
                                node_id=current_node_id,
                                escalation_policy=escalation_policy
                            )
                        except Exception as e:
                            logger.error(f"Failed to create collaboration approval request: {str(e)}", exc_info=True)

                        self._log_audit(
                            tenant_id=tenant_id,
                            action="APPROVAL_REQUESTED",
                            resource_id=execution_id,
                            status="SUCCESS",
                            user_id="system",
                            details={"node_id": current_node_id},
                        )
                        # Yield execution flow back (execution state remains RUNNING but cursor points to node)
                        return record

                    elif gate_decision == "STOP_MANUAL":
                        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                        record.status = ExecutionStatus.FAILED
                        record.failure_category = FailureCategory.VALIDATION_ERROR
                        record.completed_at = now
                        self.repo.save_execution(tenant_id, record)
                        self._log_audit(
                            tenant_id=tenant_id,
                            action="EXECUTION_FAILED",
                            resource_id=execution_id,
                            status="ERROR",
                            user_id="system",
                            details={"error": f"Execution halted: node '{current_node_id}' requires manual operator execution mode.", "failure_category": FailureCategory.VALIDATION_ERROR},
                        )
                        return record

                # 3. Execute Node
                now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                node_run = NodeExecutionRecord(
                    node_execution_id=f"nrun_{uuid.uuid4().hex[:12]}",
                    execution_id=execution_id,
                    node_id=current_node_id,
                    status=ExecutionStatus.RUNNING,
                    started_at=now,
                    input_data=record.context_payload,
                )
                self.repo.save_node_execution(tenant_id, node_run)
                self._log_audit(
                    tenant_id=tenant_id,
                    action="NODE_EXECUTED",
                    resource_id=execution_id,
                    status="RUNNING",
                    user_id="system",
                    details={"node_id": current_node_id},
                )

                # Differentiate between Query Node vs Action Node
                action_key = (node.action_type or "").upper()
                is_action = action_key in ("SEND_EMAIL", "EMAIL_NOTIFICATION", "POST_WEBHOOK", "BLOCK_IP", "CREATE_TICKET", "CREATE_JIRA", "UPDATE_TICKET")

                if is_action:
                    try:
                        # Invoke Action Connector Framework
                        result = self.action_engine.execute(tenant_id, execution_id, node, record.context_payload)

                        output_vars = {
                            "success": result.success,
                            "action_type": result.action_type,
                            "external_id": result.external_id,
                            "details": result.details,
                            "duration_ms": result.duration_ms
                        }

                        # Store variables
                        record.context_payload[current_node_id] = output_vars
                        record.context_payload[f"{current_node_id}.success"] = result.success
                        record.context_payload[f"{current_node_id}.external_id"] = result.external_id
                        for k, v in output_vars.items():
                            record.context_payload[k] = v

                        # Automatically create correlation mapping in incident_links if external_id was generated
                        if result.success and result.external_id:
                            action_upper = (result.action_type or "").upper()
                            external_system = None
                            if "JIRA" in action_upper:
                                external_system = "JIRA"
                            elif "SERVICENOW" in action_upper or "SNOW" in action_upper:
                                external_system = "SERVICENOW"
                            elif "PAGERDUTY" in action_upper or "PD" in action_upper:
                                external_system = "PAGERDUTY"
                            elif "TICKET" in action_upper:
                                external_system = "TICKET_SYSTEM"

                            if external_system:
                                try:
                                    from app.collaboration.models import IncidentLinkRecord
                                    link_record = IncidentLinkRecord(
                                        link_id=f"lnk_{uuid.uuid4().hex[:12]}",
                                        tenant_id=tenant_id,
                                        execution_id=execution_id,
                                        connector_id=node.node_id,
                                        external_system=external_system,
                                        external_id=result.external_id,
                                        status="ACTIVE",
                                        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                                    )
                                    self.repo.save_incident_link(tenant_id, link_record)
                                except Exception as e:
                                    logger.error(f"Failed to save incident correlation link: {str(e)}", exc_info=True)

                        # Update node run record
                        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                        node_run.status = ExecutionStatus.COMPLETED if result.success else ExecutionStatus.FAILED
                        node_run.completed_at = now
                        node_run.output_data = output_vars
                        self.repo.save_node_execution(tenant_id, node_run)

                        if not result.success:
                            # Halt traversal on action execution failure
                            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                            record.status = ExecutionStatus.FAILED
                            record.failure_category = FailureCategory.ACTION_ERROR
                            record.completed_at = now
                            self.repo.save_execution(tenant_id, record)
                            self._log_audit(
                                tenant_id=tenant_id,
                                action="EXECUTION_FAILED",
                                resource_id=execution_id,
                                status="ERROR",
                                user_id="system",
                                details={"error": f"Action execution failed on node '{current_node_id}'", "failure_category": FailureCategory.ACTION_ERROR}
                            )
                            return record

                    except Exception as e:
                        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                        node_run.status = ExecutionStatus.FAILED
                        node_run.completed_at = now
                        node_run.output_data = {"error": str(e)}
                        self.repo.save_node_execution(tenant_id, node_run)

                        record.status = ExecutionStatus.FAILED
                        is_validation = "validation" in str(e).lower() or "secret resolution failed" in str(e).lower()
                        failure_cat = FailureCategory.VALIDATION_ERROR if is_validation else FailureCategory.ACTION_ERROR
                        record.failure_category = failure_cat
                        record.completed_at = now
                        self.repo.save_execution(tenant_id, record)
                        self._log_audit(
                            tenant_id=tenant_id,
                            action="EXECUTION_FAILED",
                            resource_id=execution_id,
                            status="ERROR",
                            user_id="system",
                            details={"error": f"Action validation or dispatch raised error on node '{current_node_id}': {str(e)}", "failure_category": failure_cat}
                        )
                        return record

                else:
                    # Execute as a Query Node
                    from app.runtime.runner import SplunkQueryRunner
                    from app.runtime.result_mapper import ResultMapper
                    from app.runtime.query_results import QueryResult, QueryExecutionError

                    try:
                        if isinstance(self.query_runner, SplunkQueryRunner):
                            self.query_runner.repo = self.repo
                            self.query_runner.audit_repo = self.audit_repo
                            query_result = self.query_runner.run_query_detailed(node.compiled_spl or "", record.context_payload, tenant_id=tenant_id)
                        else:
                            sim_vars = self.query_runner.run_query(node.compiled_spl or "", record.context_payload)
                            query_result = QueryResult(
                                success=True,
                                row_count=len(sim_vars) if sim_vars else 0,
                                rows=[sim_vars] if sim_vars else [],
                                metadata={},
                                duration_ms=1.0
                            )

                        # Capture and persist initial query outputs in the execution context
                        rows = getattr(query_result, "rows", []) or []
                        metadata = getattr(query_result, "metadata", {}) or {}
                        events = metadata.get("events", [])
                        stats = metadata.get("stats", {})
                        
                        record.context_payload["query_results"] = rows
                        record.context_payload["query_events"] = events
                        record.context_payload["query_stats"] = stats

                        # Automated Investigation loop (MAX_AGENT_STEPS = 3)
                        investigation_history = []
                        investigation_history.append({
                            "spl": node.compiled_spl or "",
                            "result_count": len(rows),
                            "sample_results": rows[:10],
                            "reasoning": "Initial query generated by the runbook."
                        })

                        # Resolve schema fields
                        schema_fields = None
                        try:
                            data_source = None
                            for step in skill.steps:
                                if step.step_id == node.step_id:
                                    data_source = step.data_source
                                    break
                            if data_source:
                                if settings.enable_mcp:
                                    from app.schema.discovery import SchemaDiscoveryEngine
                                    schema_provider = SchemaDiscoveryEngine()
                                else:
                                    from app.schema.provider import MockSchemaProvider
                                    schema_provider = MockSchemaProvider()
                                schema_fields = schema_provider.get_fields(data_source)
                            
                            if not schema_fields:
                                if data_source == "main" or not data_source:
                                    schema_fields = ["host", "user", "process", "parent_process", "action", "severity", "status", "src_ip"]
                        except Exception as e:
                            logger.warning(f"Could not resolve schema fields: {e}")
                            if not schema_fields:
                                schema_fields = ["host", "user", "process", "parent_process", "action", "severity", "status", "src_ip"]

                        from app.agent.investigation_agent import InvestigationAgent
                        from app.agent.summary import SummaryGenerator

                        agent = InvestigationAgent()
                        summary_gen = SummaryGenerator()

                        current_spl = node.compiled_spl or ""
                        current_rows = rows
                        current_result = query_result

                        MAX_AGENT_STEPS = 5
                        step_count = 0

                        while step_count < MAX_AGENT_STEPS:
                            step_res = agent.analyze_and_next_step(
                                current_query=current_spl,
                                current_results=current_rows,
                                history=investigation_history,
                                task_description=node.step_name or skill.source_runbook,
                                schema_fields=schema_fields
                            )

                            if step_res.investigation_complete or not step_res.next_query:
                                break

                            next_spl = step_res.next_query
                            step_count += 1

                            try:
                                if isinstance(self.query_runner, SplunkQueryRunner):
                                    next_res = self.query_runner.run_query_detailed(next_spl, record.context_payload, tenant_id=tenant_id)
                                else:
                                    sim_vars = self.query_runner.run_query(next_spl, record.context_payload)
                                    next_res = QueryResult(
                                        success=True,
                                        row_count=len(sim_vars) if sim_vars else 0,
                                        rows=[sim_vars] if sim_vars else [],
                                        metadata={},
                                        duration_ms=1.0
                                    )
                                next_rows = getattr(next_res, "rows", []) or []
                                current_result = next_res
                            except Exception as q_err:
                                logger.error(f"Failed to execute investigative query '{next_spl}': {q_err}")
                                next_rows = []
                                next_res = QueryResult(
                                    success=False,
                                    row_count=0,
                                    rows=[],
                                    metadata={"error": str(q_err)},
                                    duration_ms=0.0
                                )

                            investigation_history.append({
                                "spl": next_spl,
                                "result_count": len(next_rows),
                                "sample_results": next_rows[:10],
                                "reasoning": step_res.reasoning
                            })

                            current_spl = next_spl
                            current_rows = next_rows

                        # Finalise query_result to be the last executed search in the loop
                        query_result = current_result

                        # Store investigation details in execution context
                        record.context_payload["investigation_history"] = investigation_history
                        
                        # Phase 2A/F: Threat classification and Evidence Collection
                        threat_classification_dict = None
                        try:
                            from app.agent.threat_classifier import ThreatClassifier
                            threat_classifier = ThreatClassifier()
                            threat_res = threat_classifier.classify_threat(current_rows, investigation_history)
                            threat_classification_dict = threat_res.model_dump()
                            record.context_payload["threat_classification"] = threat_classification_dict
                        except Exception as tc_err:
                            logger.error(f"Failed to classify threat: {tc_err}")
                            
                        # Store evidence collection explicitly
                        record.context_payload["evidence"] = [
                            {
                                "query": h.get("spl"),
                                "results": h.get("sample_results")
                            }
                            for h in investigation_history
                        ]

                        try:
                            exec_report = summary_gen.generate_report(
                                task_description=node.step_name or skill.source_runbook,
                                history=investigation_history,
                                threat_classification=threat_classification_dict
                            )
                            record.context_payload["executive_report"] = exec_report
                        except Exception as s_err:
                            logger.error(f"Failed to generate executive report: {s_err}")
                            record.context_payload["executive_report"] = f"# Investigation Error\n\nFailed to generate report: {s_err}"

                        # Flatten and map results with limits using final query_result
                        mapper = ResultMapper()
                        output_vars = mapper.map_results(tenant_id, execution_id, query_result, record.context_payload, self.audit_repo)

                        # Store outputs in context
                        record.context_payload[current_node_id] = output_vars
                        for k, v in output_vars.items():
                            record.context_payload[k] = v

                        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                        node_run.status = ExecutionStatus.COMPLETED
                        node_run.completed_at = now
                        node_run.output_data = output_vars
                        self.repo.save_node_execution(tenant_id, node_run)

                        self._log_audit(
                            tenant_id=tenant_id,
                            action="NODE_EXECUTED",
                            resource_id=execution_id,
                            status="SUCCESS",
                            user_id="system",
                            details={"node_id": current_node_id, "output_vars": output_vars},
                        )

                        # Record query success metrics
                        from app.observability.metrics import metrics_collector
                        metrics_collector.record_query_execution(tenant_id, query_result.success, query_result.duration_ms)

                    except QueryExecutionError as qee:
                        is_timeout = "timeout" in str(qee).lower()
                        is_validation = "validation" in str(qee).lower() or "secret resolution failed" in str(qee).lower()
                        if is_timeout:
                            failure_cat = FailureCategory.TIMEOUT
                        elif is_validation:
                            failure_cat = FailureCategory.VALIDATION_ERROR
                        else:
                            failure_cat = FailureCategory.QUERY_ERROR

                        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                        node_run.status = ExecutionStatus.FAILED
                        node_run.completed_at = now
                        node_run.output_data = {"error": str(qee)}
                        self.repo.save_node_execution(tenant_id, node_run)

                        record.status = ExecutionStatus.FAILED
                        record.failure_category = failure_cat
                        record.completed_at = now
                        self.repo.save_execution(tenant_id, record)

                        # Write failed logs
                        self._log_audit(
                            tenant_id=tenant_id,
                            action="QUERY_FAILED",
                            resource_id=execution_id,
                            status="ERROR",
                            user_id="system",
                            details={"error": str(qee), "node_id": current_node_id}
                        )
                        self._log_audit(
                            tenant_id=tenant_id,
                            action="EXECUTION_FAILED",
                            resource_id=execution_id,
                            status="ERROR",
                            user_id="system",
                            details={"error": str(qee), "failure_category": failure_cat}
                        )

                        # Record query failure metrics
                        from app.observability.metrics import metrics_collector
                        metrics_collector.record_query_execution(tenant_id, False, 0.0)
                        return record

                executed_count += 1
                bypass_gate_node = None # Clear bypass flag for subsequent nodes

                # 4. Traversal Branch Routing
                # Find outgoing edges from current node
                outgoing_edges = [edge for edge in skill.graph.edges if edge.source == current_node_id]

                if not outgoing_edges:
                    # Traversed to a leaf node
                    current_node_id = None
                    break

                next_node_id = None
                # Check for edges with conditions first, evaluate them
                for edge in outgoing_edges:
                    if edge.branch_condition:
                        if BranchEvaluator.evaluate_condition(edge.branch_condition, record.context_payload):
                            next_node_id = edge.target
                            break

                # If no conditional edges matched, look for a default edge (an edge with no condition)
                if not next_node_id:
                    for edge in outgoing_edges:
                        if not edge.branch_condition and not edge.condition:
                            next_node_id = edge.target
                            break

                if not next_node_id:
                    # No routing edge matches
                    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    record.status = ExecutionStatus.FAILED
                    record.failure_category = FailureCategory.VALIDATION_ERROR
                    record.completed_at = now
                    self.repo.save_execution(tenant_id, record)
                    self._log_audit(
                        tenant_id=tenant_id,
                        action="EXECUTION_FAILED",
                        resource_id=execution_id,
                        status="ERROR",
                        user_id="system",
                        details={"error": f"Workflow failed: no conditional routing edges matched node '{current_node_id}' output context state.", "failure_category": FailureCategory.VALIDATION_ERROR},
                    )
                    return record

                current_node_id = next_node_id

            # Execution traversal completed successfully
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            record.status = ExecutionStatus.COMPLETED
            record.completed_at = now
            record.current_node_id = None
            self.repo.save_execution(tenant_id, record)
            self._log_audit(
                tenant_id=tenant_id,
                action="EXECUTION_COMPLETED",
                resource_id=execution_id,
                status="SUCCESS",
                user_id="system",
            )
            return record

        except Exception as e:
            # Trace exception and fail workflow
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            record.status = ExecutionStatus.FAILED
            record.failure_category = FailureCategory.RUNTIME_ERROR
            record.completed_at = now
            self.repo.save_execution(tenant_id, record)
            self._log_audit(
                tenant_id=tenant_id,
                action="EXECUTION_FAILED",
                resource_id=execution_id,
                status="ERROR",
                user_id="system",
                details={"error": str(e), "traceback": traceback.format_exc(), "failure_category": FailureCategory.RUNTIME_ERROR},
            )
            return record
