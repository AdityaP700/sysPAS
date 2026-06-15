import json
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from app.storage.sqlite import SQLiteRepository
from app.audit.repository import SQLiteAuditRepository
from app.audit.models import AuditEventRecord
from app.agent.graph import ExecutionNode
from app.runtime.models import ActionExecutionRecord
from app.actions.models import ActionResult
from app.actions.email import EmailConnector
from app.actions.webhook import WebhookConnector
from app.actions.ticket import TicketConnector


class ConnectorRegistry:
    """Registry mapping specific action strings dynamically to action connectors."""

    def __init__(self):
        self._connectors = {}

    def register(self, action_name: str, connector_instance) -> None:
        self._connectors[action_name.upper()] = connector_instance

    def get(self, action_name: str) -> Optional[Any]:
        return self._connectors.get(action_name.upper())


class ActionExecutionEngine:
    """Runtime engine orchestrating execution, idempotency, and database persistence of Actions."""

    def __init__(self, repo: SQLiteRepository, audit_repo: Optional[SQLiteAuditRepository] = None):
        self.repo = repo
        self.audit_repo = audit_repo
        self.registry = ConnectorRegistry()
        self._register_default_connectors()

    def _register_default_connectors(self) -> None:
        """Bootstraps default action connector implementations."""
        email_conn = EmailConnector()
        webhook_conn = WebhookConnector()
        ticket_conn = TicketConnector()

        self.registry.register("SEND_EMAIL", email_conn)
        self.registry.register("EMAIL_NOTIFICATION", email_conn)
        
        self.registry.register("POST_WEBHOOK", webhook_conn)
        self.registry.register("BLOCK_IP", webhook_conn)
        
        self.registry.register("CREATE_TICKET", ticket_conn)
        self.registry.register("CREATE_JIRA", ticket_conn)
        self.registry.register("UPDATE_TICKET", ticket_conn)

    def _log_audit(
        self,
        tenant_id: str,
        action: str,
        resource_id: str,
        status: str,
        user_id: str,
        details: Optional[dict] = None,
    ) -> None:
        """Helper to create audit trace log entries during action execution."""
        if not self.audit_repo:
            return
        record = AuditEventRecord(
            audit_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            request_id=str(uuid.uuid4()),
            correlation_id=str(uuid.uuid4()),
            user_id=user_id,
            role="SYSTEM",
            action=action,
            resource_type="action_execution",
            resource_id=resource_id,
            status=status,
            details=details or {},
            tenant_id=tenant_id,
        )
        self.audit_repo.save_audit_event(tenant_id, record)

    def prepare_payload(self, action_type: str, node: ExecutionNode, context: Dict[str, Any]) -> Dict[str, Any]:
        """Maps flat context variables to structured parameter payloads expected by connectors."""
        payload = {}
        
        # Determine connector category
        if action_type in ("SEND_EMAIL", "EMAIL_NOTIFICATION"):
            payload["to"] = context.get("email_to") or context.get("to") or "admin@runbookmind.local"
            payload["subject"] = context.get("email_subject") or context.get("subject") or f"Runbook Alert: {node.step_name}"
            payload["body"] = context.get("email_body") or context.get("body") or f"Incident Execution details: Node={node.node_id}"
        elif action_type in ("POST_WEBHOOK", "BLOCK_IP"):
            payload["url"] = context.get("webhook_url") or context.get("url") or "http://localhost:8000/webhook"
            payload["data"] = context.get("webhook_payload") or context.get("data") or context
        elif action_type in ("CREATE_TICKET", "CREATE_JIRA", "UPDATE_TICKET"):
            payload["title"] = context.get("ticket_title") or f"Incident - {node.step_name}"
            payload["description"] = context.get("ticket_description") or f"Automatically created ticket. Context: {json.dumps(context)}"
            payload["priority"] = context.get("ticket_priority") or "High"
            
            # Sub-action mapping
            if action_type == "UPDATE_TICKET":
                payload["action"] = "UPDATE_TICKET"
                payload["ticket_id"] = context.get("ticket_id") or "INC-12345"
                payload["comment"] = context.get("ticket_comment") or "Automated comment update."
            else:
                payload["action"] = "CREATE_TICKET"
        # Copy key-value inputs ending in _secret from context if not already in payload
        for k, v in context.items():
            if k.endswith("_secret") and k not in payload:
                payload[k] = v
        return payload

    def _resolve_secrets_recursive(self, tenant_id: str, data: Any) -> Any:
        """Recursively traverses dictionaries/lists and resolves keys ending in _secret."""
        from typing import Any
        if isinstance(data, dict):
            new_dict = {}
            for k, v in list(data.items()):
                # Recursively resolve nested dictionaries and lists first
                resolved_val = self._resolve_secrets_recursive(tenant_id, v)
                
                if isinstance(k, str) and k.endswith("_secret"):
                    new_key = k[:-7] # Strip "_secret" suffix
                    secret_name = resolved_val
                    if secret_name:
                        from app.vault.service import VaultService
                        vault_service = VaultService(self.repo)
                        try:
                            decrypted = vault_service.resolve_secret(tenant_id, secret_name)
                            new_dict[new_key] = decrypted
                        except Exception as e:
                            # Log SECRET_RESOLUTION_FAILED audit event
                            if self.audit_repo:
                                from app.audit.models import AuditEventRecord
                                import uuid
                                from datetime import datetime, timezone
                                audit_record = AuditEventRecord(
                                    audit_id=str(uuid.uuid4()),
                                    timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                                    request_id=str(uuid.uuid4()),
                                    correlation_id=str(uuid.uuid4()),
                                    user_id="system",
                                    role="SYSTEM",
                                    action="SECRET_RESOLUTION_FAILED",
                                    resource_type="secret",
                                    resource_id=secret_name,
                                    status="ERROR",
                                    details={"error": str(e), "secret_name": secret_name},
                                    tenant_id=tenant_id
                                )
                                self.audit_repo.save_audit_event(tenant_id, audit_record)
                            raise ValueError(f"Validation failed: Secret resolution failed for '{secret_name}': {str(e)}") from e
                    else:
                        new_dict[new_key] = None
                else:
                    new_dict[k] = resolved_val
            return new_dict
        elif isinstance(data, list):
            return [self._resolve_secrets_recursive(tenant_id, item) for item in data]
        else:
            return data

    def execute(
        self,
        tenant_id: str,
        execution_id: str,
        node: ExecutionNode,
        context: Dict[str, Any]
    ) -> ActionResult:
        """Executes action connector under idempotency validation checks."""
        start_time = time.perf_counter()
        
        # 1. Resolve target action registry connector
        action_name = (node.action_type or "").upper()
        connector = self.registry.get(action_name)
        if not connector:
            err_msg = f"Action Routing Error: No connector found in registry matching '{action_name}'"
            self._log_audit(tenant_id, "ACTION_FAILED", execution_id, "ERROR", "system", {"error": err_msg})
            raise ValueError(err_msg)

        # 2. Check Action Idempotency
        idempotency_key = f"{execution_id}:{node.node_id}"
        existing_run = self.repo.get_successful_action_execution(tenant_id, idempotency_key)
        if existing_run:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            # Reuse output result payload
            return ActionResult(
                success=True,
                action_type=existing_run.action_type,
                external_id=existing_run.external_id,
                details={
                    **existing_run.payload,
                    "idempotent_cached": True,
                    "original_run_id": existing_run.action_execution_id
                },
                duration_ms=duration_ms
            )

        # 3. Construct payload parameters
        payload = self.prepare_payload(action_name, node, context)
        payload = self._resolve_secrets_recursive(tenant_id, payload)

        try:
            # 4. Invoke connector execute
            result = connector.execute(payload)
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            
            # Preserve details returned
            db_payload = {
                "request": payload,
                "response": result.details
            }

            # 5. Persist run to database
            action_run_id = f"actrun_{uuid.uuid4().hex[:12]}"
            db_record = ActionExecutionRecord(
                action_execution_id=action_run_id,
                tenant_id=tenant_id,
                execution_id=execution_id,
                node_id=node.node_id,
                action_type=result.action_type,
                external_id=result.external_id,
                success=result.success,
                duration_ms=duration_ms,
                payload=db_payload,
                idempotency_key=idempotency_key,
                created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            )
            self.repo.save_action_execution(tenant_id, db_record)

            # 6. Audit Logging and Telemetry triggers
            audit_action = "ACTION_EXECUTED"
            if result.success:
                if result.action_type == "SEND_EMAIL":
                    audit_action = "EMAIL_SENT"
                elif result.action_type == "POST_WEBHOOK":
                    audit_action = "WEBHOOK_SENT"
                elif result.action_type == "CREATE_TICKET":
                    audit_action = "TICKET_CREATED"
                elif result.action_type == "UPDATE_TICKET":
                    audit_action = "TICKET_UPDATED"
                
                status_val = "SUCCESS"
            else:
                audit_action = "ACTION_FAILED"
                status_val = "ERROR"

            self._log_audit(
                tenant_id=tenant_id,
                action=audit_action,
                resource_id=action_run_id,
                status=status_val,
                user_id="system",
                details={
                    "node_id": node.node_id,
                    "external_id": result.external_id,
                    "success": result.success
                }
            )

            # Record telemetry metrics
            from app.observability.metrics import metrics_collector
            metrics_collector.record_action_execution(tenant_id, result.action_type, result.success, duration_ms)

            return result

        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            
            # Persist failed run to database
            action_run_id = f"actrun_{uuid.uuid4().hex[:12]}"
            db_record = ActionExecutionRecord(
                action_execution_id=action_run_id,
                tenant_id=tenant_id,
                execution_id=execution_id,
                node_id=node.node_id,
                action_type=action_name,
                external_id=None,
                success=False,
                duration_ms=duration_ms,
                payload={"request": payload, "error": str(e)},
                idempotency_key=idempotency_key,
                created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            )
            self.repo.save_action_execution(tenant_id, db_record)

            # Write failed audit log
            self._log_audit(
                tenant_id=tenant_id,
                action="ACTION_FAILED",
                resource_id=action_run_id,
                status="ERROR",
                user_id="system",
                details={
                    "node_id": node.node_id,
                    "error": str(e)
                }
            )
            
            # Record failed metric
            from app.observability.metrics import metrics_collector
            metrics_collector.record_action_execution(tenant_id, action_name, False, duration_ms)
            
            raise e
