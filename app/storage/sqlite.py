import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import List, Optional, Any, Dict
from app.storage.models import BundleRecord, CompilationRecord, TraceRecord
from app.storage.repository import StorageRepository
from app.auth.models import APIKeyRecord, UserRole, TenantRole, TenantRecord, MembershipRecord, GlobalRole
from app.auth.repository import APIKeyRepository
from app.audit.models import AuditEventRecord
from app.runtime.models import ExecutionRecord, NodeExecutionRecord, ApprovalRecord, ExecutionStatus, ApprovalStatus, FailureCategory, ActionExecutionRecord


class SQLiteRepository(StorageRepository, APIKeyRepository):
    """Thread-safe SQLite implementation of Storage, APIKey, Tenant, and Membership repositories."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.lock = threading.RLock()
        self._create_tables()
        self._migrate_schema()
        self._create_indexes()
        self._bootstrap_system_tenant()

    def _create_tables(self) -> None:
        """Create database tables and composite indexes automatically if they do not exist."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                # Enable foreign keys
                cursor.execute("PRAGMA foreign_keys = ON;")

                # 1. Tenants table (with soft delete and UNIQUE slug)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tenants (
                        tenant_id TEXT PRIMARY KEY,
                        name TEXT,
                        slug TEXT UNIQUE,
                        created_at TEXT,
                        enabled INTEGER,
                        deleted_at TEXT
                    )
                    """
                )

                # 2. Tenant Memberships table (with UNIQUE tenant_id + api_key_id constraint)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tenant_memberships (
                        membership_id TEXT PRIMARY KEY,
                        tenant_id TEXT,
                        api_key_id TEXT,
                        role TEXT,
                        UNIQUE(tenant_id, api_key_id),
                        FOREIGN KEY(tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
                    )
                    """
                )

                # 3. Bundles table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS bundles (
                        bundle_id TEXT,
                        bundle_name TEXT,
                        version INTEGER,
                        created_at TEXT,
                        status TEXT,
                        payload TEXT,
                        tenant_id TEXT NOT NULL DEFAULT 'system',
                        created_by TEXT,
                        PRIMARY KEY (bundle_id, version)
                    )
                    """
                )

                # 4. Compilations table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS compilations (
                        compilation_id TEXT PRIMARY KEY,
                        bundle_id TEXT,
                        timestamp TEXT,
                        duration_ms REAL,
                        confidence REAL,
                        status TEXT,
                        tenant_id TEXT NOT NULL DEFAULT 'system'
                    )
                    """
                )

                # 5. Traces table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS traces (
                        trace_id TEXT PRIMARY KEY,
                        compilation_id TEXT,
                        step_id TEXT,
                        request_id TEXT,
                        correlation_id TEXT,
                        payload TEXT,
                        tenant_id TEXT NOT NULL DEFAULT 'system'
                    )
                    """
                )

                # 6. API Keys table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS api_keys (
                        key_id TEXT PRIMARY KEY,
                        name TEXT,
                        key_hash TEXT UNIQUE,
                        key_prefix TEXT,
                        role TEXT,
                        tenant_id TEXT NOT NULL DEFAULT 'system',
                        global_role TEXT,
                        tenant_role TEXT,
                        created_at TEXT,
                        enabled INTEGER
                    )
                    """
                )

                # 7. Audit Logs table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audit_logs (
                        audit_id TEXT PRIMARY KEY,
                        timestamp TEXT,
                        request_id TEXT,
                        correlation_id TEXT,
                        user_id TEXT,
                        role TEXT,
                        action TEXT,
                        resource_type TEXT,
                        resource_id TEXT,
                        status TEXT,
                        details TEXT,
                        tenant_id TEXT NOT NULL DEFAULT 'system'
                    )
                    """
                )

                # 8. Executions table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS executions (
                        execution_id TEXT PRIMARY KEY,
                        tenant_id TEXT,
                        bundle_id TEXT,
                        bundle_version INTEGER,
                        status TEXT,
                        current_node_id TEXT,
                        started_at TEXT,
                        completed_at TEXT,
                        triggered_by TEXT,
                        context_payload TEXT
                    )
                    """
                )

                # Check node_executions table compatibility (recreate if missing node_execution_id primary key)
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='node_executions';")
                if cursor.fetchone():
                    cursor.execute("PRAGMA table_info(node_executions);")
                    cols = [row[1] for row in cursor.fetchall()]
                    if "node_execution_id" not in cols:
                        cursor.execute("DROP TABLE node_executions;")

                # 9. Node Executions table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS node_executions (
                        node_execution_id TEXT PRIMARY KEY,
                        execution_id TEXT,
                        node_id TEXT,
                        status TEXT,
                        started_at TEXT,
                        completed_at TEXT,
                        input_data TEXT,
                        output_data TEXT
                    )
                    """
                )

                # 10. Approvals table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS approvals (
                        approval_id TEXT PRIMARY KEY,
                        execution_id TEXT,
                        node_id TEXT,
                        requested_at TEXT,
                        decided_at TEXT,
                        decision TEXT,
                        decided_by TEXT
                    )
                    """
                )

                # 11. Jobs table (durable background queue)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS jobs (
                        job_id TEXT PRIMARY KEY,
                        tenant_id TEXT,
                        execution_id TEXT,
                        bundle_id TEXT,
                        bundle_version INTEGER,
                        status TEXT,
                        attempt_count INTEGER,
                        max_attempts INTEGER,
                        created_at TEXT,
                        started_at TEXT,
                        completed_at TEXT,
                        last_error TEXT,
                        payload TEXT,
                        run_at TEXT,
                        created_by TEXT,
                        worker_id TEXT,
                        priority INTEGER DEFAULT 100,
                        schedule_fire_id TEXT UNIQUE
                    )
                    """
                )

                # 12. Schedules table (durable cron schedules)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schedules (
                        schedule_id TEXT PRIMARY KEY,
                        tenant_id TEXT,
                        bundle_id TEXT,
                        bundle_version INTEGER,
                        cron_expression TEXT,
                        enabled INTEGER,
                        next_run_at TEXT,
                        created_by TEXT,
                        created_at TEXT,
                        last_triggered_at TEXT
                    )
                    """
                )

                # 13. Action Executions table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS action_executions (
                        action_execution_id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL DEFAULT 'system',
                        execution_id TEXT,
                        node_id TEXT,
                        action_type TEXT,
                        external_id TEXT,
                        success INTEGER,
                        duration_ms REAL,
                        payload TEXT,
                        idempotency_key TEXT,
                        created_at TEXT,
                        FOREIGN KEY(execution_id) REFERENCES executions(execution_id) ON DELETE CASCADE
                    )
                    """
                )

                # 14. Secrets table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS secrets (
                        secret_id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        secret_type TEXT,
                        encrypted_value TEXT NOT NULL,
                        version INTEGER,
                        enabled INTEGER,
                        is_current INTEGER,
                        created_at TEXT,
                        updated_at TEXT
                    )
                    """
                )

                # 15. Connectors table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS connectors (
                        connector_id TEXT,
                        tenant_id TEXT NOT NULL,
                        connector_type TEXT NOT NULL,
                        name TEXT NOT NULL,
                        description TEXT,
                        enabled INTEGER DEFAULT 1,
                        configuration TEXT NOT NULL,
                        connector_version INTEGER NOT NULL DEFAULT 1,
                        schema_version INTEGER NOT NULL DEFAULT 1,
                        health_status TEXT DEFAULT 'UNKNOWN',
                        last_health_check TEXT,
                        last_success_at TEXT,
                        consecutive_failures INTEGER DEFAULT 0,
                        last_validation_at TEXT,
                        validation_error TEXT,
                        rate_limit_per_minute INTEGER DEFAULT 100,
                        circuit_state TEXT DEFAULT 'CLOSED',
                        circuit_failure_count INTEGER DEFAULT 0,
                        circuit_opened_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (connector_id, connector_version)
                    )
                    """
                )

                # 16. Approval requests table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS approval_requests (
                        approval_id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL,
                        execution_id TEXT NOT NULL,
                        node_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        escalation_level INTEGER DEFAULT 1,
                        escalated_to TEXT,
                        escalation_policy TEXT,
                        approval_token TEXT,
                        created_at TEXT NOT NULL,
                        decided_at TEXT,
                        decision TEXT,
                        decided_by TEXT,
                        expires_at TEXT NOT NULL
                    )
                    """
                )

                # 17. Approval callbacks table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS approval_callbacks (
                        callback_id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL,
                        approval_id TEXT NOT NULL,
                        source TEXT NOT NULL,
                        payload_hash TEXT,
                        nonce TEXT UNIQUE,
                        timestamp TEXT NOT NULL,
                        received_at TEXT NOT NULL,
                        processed INTEGER DEFAULT 0
                    )
                    """
                )

                # 18. Approval reminders table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS approval_reminders (
                        reminder_id TEXT PRIMARY KEY,
                        approval_id TEXT,
                        reminder_number INTEGER,
                        sent_at TEXT
                    )
                    """
                )

                # 19. Incident links table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS incident_links (
                        link_id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL,
                        execution_id TEXT NOT NULL,
                        connector_id TEXT NOT NULL,
                        external_system TEXT NOT NULL,
                        external_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )

                # 20. Notification templates table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS notification_templates (
                        template_id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        channel TEXT NOT NULL,
                        subject_template TEXT,
                        body_template TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )

                # --- Phase 22 Governance & Promotions Database Migrations ---

                # Column alterations with try-except OperationalError checks
                try:
                    cursor.execute("ALTER TABLE bundles ADD COLUMN environment TEXT DEFAULT 'DEV'")
                except sqlite3.OperationalError:
                    pass
                try:
                    cursor.execute("ALTER TABLE bundles ADD COLUMN promotion_status TEXT DEFAULT 'DRAFT'")
                except sqlite3.OperationalError:
                    pass
                try:
                    cursor.execute("ALTER TABLE secrets ADD COLUMN environment TEXT DEFAULT 'DEV'")
                except sqlite3.OperationalError:
                    pass
                try:
                    cursor.execute("ALTER TABLE connectors ADD COLUMN environment TEXT DEFAULT 'DEV'")
                except sqlite3.OperationalError:
                    pass

                # Create Policies table (versioned)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS policies (
                        policy_id TEXT,
                        tenant_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        policy_type TEXT NOT NULL,
                        enabled INTEGER DEFAULT 1,
                        priority INTEGER DEFAULT 100,
                        version INTEGER NOT NULL DEFAULT 1,
                        is_current INTEGER DEFAULT 1,
                        policy_definition TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (policy_id, version)
                    )
                    """
                )

                # Create Deployments table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS deployments (
                        deployment_id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL,
                        bundle_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        environment TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )

                # Create Deployment Snapshots table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS deployment_snapshots (
                        snapshot_id TEXT PRIMARY KEY,
                        deployment_id TEXT NOT NULL,
                        tenant_id TEXT NOT NULL,
                        bundle_payload TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )

                # Create Deployment Approvals table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS deployment_approvals (
                        approval_id TEXT PRIMARY KEY,
                        deployment_id TEXT NOT NULL,
                        tenant_id TEXT NOT NULL,
                        approved_by TEXT,
                        approved_at TEXT,
                        decision TEXT NOT NULL,
                        comments TEXT
                    )
                    """
                )

                # Create Compliance Snapshots table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS compliance_snapshots (
                        snapshot_id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL,
                        report_type TEXT NOT NULL,
                        report_data TEXT NOT NULL,
                        snapshot_hash TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )

                # Create Policy Events table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS policy_events (
                        event_id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL,
                        policy_id TEXT,
                        resource_type TEXT NOT NULL,
                        resource_id TEXT NOT NULL,
                        decision TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    )
                    """
                )

                # Create System Flags table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS system_flags (
                        flag_name TEXT PRIMARY KEY,
                        flag_value TEXT NOT NULL,
                        updated_by TEXT,
                        updated_at TEXT NOT NULL
                    )
                    """
                )

                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def _create_indexes(self) -> None:
        """Create composite tenant-aware indexes once columns are guaranteed to exist."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_bundles_tenant_bundle ON bundles(tenant_id, bundle_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_compilations_tenant_compilation ON compilations(tenant_id, compilation_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_traces_tenant_compilation ON traces(tenant_id, compilation_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_tenant_key ON api_keys(tenant_id, key_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_logs(tenant_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_executions_tenant_id ON executions(tenant_id, execution_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_node_executions_id ON node_executions(execution_id, node_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_approvals_exec ON approvals(execution_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_tenant ON jobs(tenant_id, job_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_priority_created ON jobs(priority, created_at) WHERE status = 'QUEUED' OR status = 'RETRYING';")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_schedules_next_run ON schedules(next_run_at) WHERE enabled = 1;")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_schedules_tenant ON schedules(tenant_id, schedule_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_action_execution ON action_executions(execution_id, node_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_action_execution_tenant ON action_executions(tenant_id, action_execution_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_action_idempotency ON action_executions(tenant_id, idempotency_key);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_secret_tenant ON secrets(tenant_id, secret_id);")
                cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_secret_name ON secrets(tenant_id, name, version);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_connector_tenant ON connectors(tenant_id, connector_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_approval_tenant ON approval_requests(tenant_id, approval_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_callback_nonce ON approval_callbacks(nonce);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_incident_exec ON incident_links(tenant_id, execution_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_policy_tenant ON policies(tenant_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_deployments_tenant ON deployments(tenant_id, deployment_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_policy_events_tenant ON policy_events(tenant_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_compliance_tenant ON compliance_snapshots(tenant_id);")
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def _migrate_schema(self) -> None:
        """Inspect existing tables and migrate columns automatically for multi-tenancy."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                tables_to_migrate = ["bundles", "compilations", "traces", "api_keys", "audit_logs"]
                for table in tables_to_migrate:
                    cursor.execute(f"PRAGMA table_info({table});")
                    columns = [row[1] for row in cursor.fetchall()]
                    if columns and "tenant_id" not in columns:
                        cursor.execute(f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'system';")
                
                # Check created_by column on bundles table
                cursor.execute("PRAGMA table_info(bundles);")
                bundle_cols = [row[1] for row in cursor.fetchall()]
                if bundle_cols and "created_by" not in bundle_cols:
                    if "owner_id" in bundle_cols:
                        cursor.execute("ALTER TABLE bundles ADD COLUMN created_by TEXT DEFAULT 'system';")
                        cursor.execute("UPDATE bundles SET created_by = owner_id;")
                    else:
                        cursor.execute("ALTER TABLE bundles ADD COLUMN created_by TEXT DEFAULT 'system';")

                # Check global_role and tenant_role columns on api_keys table
                cursor.execute("PRAGMA table_info(api_keys);")
                key_cols = [row[1] for row in cursor.fetchall()]
                if key_cols:
                    if "global_role" not in key_cols:
                        cursor.execute("ALTER TABLE api_keys ADD COLUMN global_role TEXT;")
                    if "tenant_role" not in key_cols:
                        cursor.execute("ALTER TABLE api_keys ADD COLUMN tenant_role TEXT;")

                # Check failure_category column on executions table
                cursor.execute("PRAGMA table_info(executions);")
                exec_cols = [row[1] for row in cursor.fetchall()]
                if exec_cols and "failure_category" not in exec_cols:
                    cursor.execute("ALTER TABLE executions ADD COLUMN failure_category TEXT;")

                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def _bootstrap_system_tenant(self) -> None:
        """Register the default system tenant in tenants if missing."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT tenant_id FROM tenants WHERE tenant_id = 'system';")
                if not cursor.fetchone():
                    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    cursor.execute(
                        """
                        INSERT INTO tenants (tenant_id, name, slug, created_at, enabled, deleted_at)
                        VALUES ('system', 'System Tenant', 'system', ?, 1, NULL)
                        """,
                        (now,),
                    )
                    conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    # --- Tenant Management Operations ---

    def save_tenant(self, record: TenantRecord) -> None:
        """Persist or overwrite a tenant record."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO tenants (tenant_id, name, slug, created_at, enabled, deleted_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id) DO UPDATE SET
                        name = excluded.name,
                        slug = excluded.slug,
                        enabled = excluded.enabled,
                        deleted_at = excluded.deleted_at
                    """,
                    (
                        record.tenant_id,
                        record.name,
                        record.slug,
                        record.created_at,
                        1 if record.enabled else 0,
                        record.deleted_at,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_tenant(self, tenant_id: str) -> Optional[TenantRecord]:
        """Retrieve an active, non-deleted tenant record."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT tenant_id, name, slug, created_at, enabled, deleted_at
                    FROM tenants
                    WHERE tenant_id = ? AND enabled = 1 AND deleted_at IS NULL
                    """,
                    (tenant_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return TenantRecord(
                    tenant_id=row[0],
                    name=row[1],
                    slug=row[2],
                    created_at=row[3],
                    enabled=bool(row[4]),
                    deleted_at=row[5],
                )
            finally:
                conn.close()

    def list_tenants(self) -> List[TenantRecord]:
        """List active, non-deleted tenants."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT tenant_id, name, slug, created_at, enabled, deleted_at
                    FROM tenants
                    WHERE enabled = 1 AND deleted_at IS NULL
                    ORDER BY created_at DESC
                    """
                )
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    results.append(
                        TenantRecord(
                            tenant_id=row[0],
                            name=row[1],
                            slug=row[2],
                            created_at=row[3],
                            enabled=bool(row[4]),
                            deleted_at=row[5],
                        )
                    )
                return results
            finally:
                conn.close()

    def delete_tenant(self, tenant_id: str) -> bool:
        """Soft delete a tenant by deactivating enabled state and setting deleted_at timestamp."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                cursor.execute(
                    "UPDATE tenants SET enabled = 0, deleted_at = ? WHERE tenant_id = ? AND enabled = 1",
                    (now, tenant_id),
                )
                affected = cursor.rowcount > 0
                conn.commit()
                return affected
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    # --- Membership Management Operations ---

    def save_membership(self, record: MembershipRecord) -> None:
        """Persist or overwrite a membership mapping association."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO tenant_memberships (membership_id, tenant_id, api_key_id, role)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(membership_id) DO UPDATE SET
                        tenant_id = excluded.tenant_id,
                        api_key_id = excluded.api_key_id,
                        role = excluded.role
                    """,
                    (
                        record.membership_id,
                        record.tenant_id,
                        record.api_key_id,
                        record.role.value,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_memberships(self, tenant_id: str) -> List[MembershipRecord]:
        """List all active memberships in a tenant."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT membership_id, tenant_id, api_key_id, role
                    FROM tenant_memberships
                    WHERE tenant_id = ?
                    """,
                    (tenant_id,),
                )
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    results.append(
                        MembershipRecord(
                            membership_id=row[0],
                            tenant_id=row[1],
                            api_key_id=row[2],
                            role=TenantRole(row[3]),
                        )
                    )
                return results
            finally:
                conn.close()

    def delete_membership(self, tenant_id: str, membership_id: str) -> bool:
        """Remove a membership mapping from the tenant."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM tenant_memberships WHERE tenant_id = ? AND membership_id = ?",
                    (tenant_id, membership_id),
                )
                deleted = cursor.rowcount > 0
                conn.commit()
                return deleted
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    # --- Agent Execution Runtime Operations ---

    def save_execution(self, tenant_id: str, record: ExecutionRecord) -> None:
        """Persist or update an execution run record."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO executions (execution_id, tenant_id, bundle_id, bundle_version, status, current_node_id, started_at, completed_at, triggered_by, context_payload, failure_category)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(execution_id) DO UPDATE SET
                        status = excluded.status,
                        current_node_id = excluded.current_node_id,
                        completed_at = excluded.completed_at,
                        context_payload = excluded.context_payload,
                        failure_category = excluded.failure_category
                    """,
                    (
                        record.execution_id,
                        tenant_id,
                        record.bundle_id,
                        record.bundle_version,
                        record.status.value,
                        record.current_node_id,
                        record.started_at,
                        record.completed_at,
                        record.triggered_by,
                        json.dumps(record.context_payload),
                        record.failure_category.value if record.failure_category else None,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_execution(self, tenant_id: str, execution_id: str) -> Optional[ExecutionRecord]:
        """Retrieve a specific workflow execution record."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT execution_id, tenant_id, bundle_id, bundle_version, status, current_node_id, started_at, completed_at, triggered_by, context_payload, failure_category
                    FROM executions
                    WHERE tenant_id = ? AND execution_id = ?
                    """,
                    (tenant_id, execution_id),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return ExecutionRecord(
                    execution_id=row[0],
                    tenant_id=row[1],
                    bundle_id=row[2],
                    bundle_version=row[3],
                    status=ExecutionStatus(row[4]),
                    current_node_id=row[5],
                    started_at=row[6],
                    completed_at=row[7],
                    triggered_by=row[8],
                    context_payload=json.loads(row[9]) if row[9] else {},
                    failure_category=FailureCategory(row[10]) if row[10] else None,
                )
            finally:
                conn.close()

    def list_executions(self, tenant_id: str) -> List[ExecutionRecord]:
        """List all workflow executions in a tenant."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT execution_id, tenant_id, bundle_id, bundle_version, status, current_node_id, started_at, completed_at, triggered_by, context_payload, failure_category
                    FROM executions
                    WHERE tenant_id = ?
                    ORDER BY started_at DESC
                    """,
                    (tenant_id,),
                )
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    results.append(
                        ExecutionRecord(
                            execution_id=row[0],
                            tenant_id=row[1],
                            bundle_id=row[2],
                            bundle_version=row[3],
                            status=ExecutionStatus(row[4]),
                            current_node_id=row[5],
                            started_at=row[6],
                            completed_at=row[7],
                            triggered_by=row[8],
                            context_payload=json.loads(row[9]) if row[9] else {},
                            failure_category=FailureCategory(row[10]) if row[10] else None,
                        )
                    )
                return results
            finally:
                conn.close()

    def save_node_execution(self, tenant_id: str, record: NodeExecutionRecord) -> None:
        """Persist or update a node execution record."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO node_executions (node_execution_id, execution_id, node_id, status, started_at, completed_at, input_data, output_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(node_execution_id) DO UPDATE SET
                        status = excluded.status,
                        completed_at = excluded.completed_at,
                        input_data = excluded.input_data,
                        output_data = excluded.output_data
                    """,
                    (
                        record.node_execution_id,
                        record.execution_id,
                        record.node_id,
                        record.status.value,
                        record.started_at,
                        record.completed_at,
                        json.dumps(record.input_data),
                        json.dumps(record.output_data),
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_node_executions(self, tenant_id: str, execution_id: str) -> List[NodeExecutionRecord]:
        """List all step node runs associated with an execution ID."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT node_execution_id, execution_id, node_id, status, started_at, completed_at, input_data, output_data
                    FROM node_executions
                    WHERE execution_id = ?
                    ORDER BY started_at ASC
                    """,
                    (execution_id,),
                )
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    results.append(
                        NodeExecutionRecord(
                            node_execution_id=row[0],
                            execution_id=row[1],
                            node_id=row[2],
                            status=ExecutionStatus(row[3]),
                            started_at=row[4],
                            completed_at=row[5],
                            input_data=json.loads(row[6]) if row[6] else {},
                            output_data=json.loads(row[7]) if row[7] else {},
                        )
                    )
                return results
            finally:
                conn.close()

    def save_action_execution(self, tenant_id: str, record: ActionExecutionRecord) -> None:
        """Persist or update an action execution record."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO action_executions (action_execution_id, tenant_id, execution_id, node_id, action_type, external_id, success, duration_ms, payload, idempotency_key, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(action_execution_id) DO UPDATE SET
                        external_id = excluded.external_id,
                        success = excluded.success,
                        duration_ms = excluded.duration_ms,
                        payload = excluded.payload,
                        idempotency_key = excluded.idempotency_key
                    """,
                    (
                        record.action_execution_id,
                        tenant_id,
                        record.execution_id,
                        record.node_id,
                        record.action_type,
                        record.external_id,
                        1 if record.success else 0,
                        record.duration_ms,
                        json.dumps(record.payload),
                        record.idempotency_key,
                        record.created_at,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_action_executions(self, tenant_id: str, execution_id: str) -> List[ActionExecutionRecord]:
        """List all action executions associated with an execution ID within a tenant scope."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT action_execution_id, tenant_id, execution_id, node_id, action_type, external_id, success, duration_ms, payload, idempotency_key, created_at
                    FROM action_executions
                    WHERE tenant_id = ? AND execution_id = ?
                    ORDER BY created_at ASC
                    """,
                    (tenant_id, execution_id),
                )
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    results.append(
                        ActionExecutionRecord(
                            action_execution_id=row[0],
                            tenant_id=row[1],
                            execution_id=row[2],
                            node_id=row[3],
                            action_type=row[4],
                            external_id=row[5],
                            success=bool(row[6]),
                            duration_ms=row[7],
                            payload=json.loads(row[8]) if row[8] else {},
                            idempotency_key=row[9],
                            created_at=row[10],
                        )
                    )
                return results
            finally:
                conn.close()

    def get_successful_action_execution(self, tenant_id: str, idempotency_key: str) -> Optional[ActionExecutionRecord]:
        """Retrieve a successful action execution record matching the idempotency key."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT action_execution_id, tenant_id, execution_id, node_id, action_type, external_id, success, duration_ms, payload, idempotency_key, created_at
                    FROM action_executions
                    WHERE tenant_id = ? AND idempotency_key = ? AND success = 1
                    LIMIT 1
                    """,
                    (tenant_id, idempotency_key),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return ActionExecutionRecord(
                    action_execution_id=row[0],
                    tenant_id=row[1],
                    execution_id=row[2],
                    node_id=row[3],
                    action_type=row[4],
                    external_id=row[5],
                    success=bool(row[6]),
                    duration_ms=row[7],
                    payload=json.loads(row[8]) if row[8] else {},
                    idempotency_key=row[9],
                    created_at=row[10],
                )
            finally:
                conn.close()

    def save_secret(self, tenant_id: str, record: Any) -> None:
        """Persist a new secret or a new version of a secret under a tenant scope."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                # If this is being saved as the current version, mark all other versions of the same secret name as non-current.
                if record.is_current:
                    cursor.execute(
                        "UPDATE secrets SET is_current = 0, updated_at = ? WHERE tenant_id = ? AND name = ?",
                        (record.updated_at, tenant_id, record.name)
                    )
                cursor.execute(
                    """
                    INSERT INTO secrets (secret_id, tenant_id, name, secret_type, encrypted_value, version, enabled, is_current, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(secret_id) DO UPDATE SET
                        encrypted_value = excluded.encrypted_value,
                        version = excluded.version,
                        enabled = excluded.enabled,
                        is_current = excluded.is_current,
                        updated_at = excluded.updated_at
                    """,
                    (
                        record.secret_id,
                        tenant_id,
                        record.name,
                        record.secret_type.value if hasattr(record.secret_type, 'value') else record.secret_type,
                        record.encrypted_value,
                        record.version,
                        1 if record.enabled else 0,
                        1 if record.is_current else 0,
                        record.created_at,
                        record.updated_at,
                    )
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_secret(self, tenant_id: str, secret_id: str) -> Optional[Any]:
        """Retrieve a specific secret metadata record by ID."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT secret_id, tenant_id, name, secret_type, encrypted_value, version, enabled, is_current, created_at, updated_at
                    FROM secrets
                    WHERE tenant_id = ? AND secret_id = ?
                    """,
                    (tenant_id, secret_id)
                )
                row = cursor.fetchone()
                if not row:
                    return None
                from app.vault.models import SecretRecord, SecretType
                return SecretRecord(
                    secret_id=row[0],
                    tenant_id=row[1],
                    name=row[2],
                    secret_type=SecretType(row[3]),
                    encrypted_value=row[4],
                    version=row[5],
                    enabled=bool(row[6]),
                    is_current=bool(row[7]),
                    created_at=row[8],
                    updated_at=row[9],
                )
            finally:
                conn.close()

    def get_secret_by_name(self, tenant_id: str, name: str, version: Optional[int] = None) -> Optional[Any]:
        """Retrieve a secret version by its name. If version is None, returns the current active version."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                if version is None:
                    cursor.execute(
                        """
                        SELECT secret_id, tenant_id, name, secret_type, encrypted_value, version, enabled, is_current, created_at, updated_at
                        FROM secrets
                        WHERE tenant_id = ? AND name = ? AND is_current = 1
                        LIMIT 1
                        """,
                        (tenant_id, name)
                    )
                else:
                    cursor.execute(
                        """
                        SELECT secret_id, tenant_id, name, secret_type, encrypted_value, version, enabled, is_current, created_at, updated_at
                        FROM secrets
                        WHERE tenant_id = ? AND name = ? AND version = ?
                        """,
                        (tenant_id, name, version)
                    )
                row = cursor.fetchone()
                if not row:
                    return None
                from app.vault.models import SecretRecord, SecretType
                return SecretRecord(
                    secret_id=row[0],
                    tenant_id=row[1],
                    name=row[2],
                    secret_type=SecretType(row[3]),
                    encrypted_value=row[4],
                    version=row[5],
                    enabled=bool(row[6]),
                    is_current=bool(row[7]),
                    created_at=row[8],
                    updated_at=row[9],
                )
            finally:
                conn.close()

    def list_secrets(self, tenant_id: str) -> List[Any]:
        """Retrieve all secret records under a tenant scope."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT secret_id, tenant_id, name, secret_type, encrypted_value, version, enabled, is_current, created_at, updated_at
                    FROM secrets
                    WHERE tenant_id = ?
                    ORDER BY name ASC, version DESC
                    """,
                    (tenant_id,)
                )
                rows = cursor.fetchall()
                results = []
                from app.vault.models import SecretRecord, SecretType
                for row in rows:
                    results.append(
                        SecretRecord(
                            secret_id=row[0],
                            tenant_id=row[1],
                            name=row[2],
                            secret_type=SecretType(row[3]),
                            encrypted_value=row[4],
                            version=row[5],
                            enabled=bool(row[6]),
                            is_current=bool(row[7]),
                            created_at=row[8],
                            updated_at=row[9],
                        )
                    )
                return results
            finally:
                conn.close()

    def disable_secret(self, tenant_id: str, secret_id: str) -> bool:
        """Disable all versions of the secret associated with the same name as the given secret_id."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM secrets WHERE tenant_id = ? AND secret_id = ?", (tenant_id, secret_id))
                row = cursor.fetchone()
                if not row:
                    return False
                name = row[0]
                now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                cursor.execute(
                    "UPDATE secrets SET enabled = 0, is_current = 0, updated_at = ? WHERE tenant_id = ? AND name = ?",
                    (now_str, tenant_id, name)
                )
                disabled = cursor.rowcount > 0
                conn.commit()
                return disabled
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def save_approval(self, tenant_id: str, record: ApprovalRecord) -> None:
        """Persist or update an authorization gate request/decision."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO approvals (approval_id, execution_id, node_id, requested_at, decided_at, decision, decided_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(approval_id) DO UPDATE SET
                        decided_at = excluded.decided_at,
                        decision = excluded.decision,
                        decided_by = excluded.decided_by
                    """,
                    (
                        record.approval_id,
                        record.execution_id,
                        record.node_id,
                        record.requested_at,
                        record.decided_at,
                        record.decision.value if record.decision else None,
                        record.decided_by,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_approval_by_execution(self, tenant_id: str, execution_id: str) -> Optional[ApprovalRecord]:
        """Retrieve the approval associated with a running/paused execution (most recent node-scope approval)."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT approval_id, execution_id, node_id, requested_at, decided_at, decision, decided_by
                    FROM approvals
                    WHERE execution_id = ?
                    ORDER BY requested_at DESC
                    LIMIT 1
                    """,
                    (execution_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return ApprovalRecord(
                    approval_id=row[0],
                    execution_id=row[1],
                    node_id=row[2],
                    requested_at=row[3],
                    decided_at=row[4],
                    decision=ApprovalStatus(row[5]) if row[5] else None,
                    decided_by=row[6],
                )
            finally:
                conn.close()

    def list_pending_approvals(self, tenant_id: str) -> List[ApprovalRecord]:
        """List active pending approvals (where decision is not made yet)."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT a.approval_id, a.execution_id, a.node_id, a.requested_at, a.decided_at, a.decision, a.decided_by
                    FROM approvals a
                    JOIN executions e ON a.execution_id = e.execution_id
                    WHERE e.tenant_id = ? AND a.decision IS NULL
                    ORDER BY a.requested_at DESC
                    """,
                    (tenant_id,),
                )
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    results.append(
                        ApprovalRecord(
                            approval_id=row[0],
                            execution_id=row[1],
                            node_id=row[2],
                            requested_at=row[3],
                            decided_at=row[4],
                            decision=ApprovalStatus(row[5]) if row[5] else None,
                            decided_by=row[6],
                        )
                    )
                return results
            finally:
                conn.close()

    # --- Tenant-Scoped StorageRepository Implementations ---

    def save_bundle(self, tenant_id_or_record, record: Optional[BundleRecord] = None) -> None:
        """Persist a new version of a skill bundle under a tenant scope."""
        if isinstance(tenant_id_or_record, BundleRecord):
            record = tenant_id_or_record
            tenant_id = record.tenant_id or "system"
        else:
            tenant_id = tenant_id_or_record
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO bundles (bundle_id, bundle_name, version, created_at, status, payload, tenant_id, created_by, environment, promotion_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.bundle_id,
                        record.bundle_name,
                        record.version,
                        record.created_at,
                        record.status,
                        json.dumps(record.payload),
                        tenant_id,
                        record.created_by,
                        record.environment,
                        record.promotion_status,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_bundle(self, tenant_id_or_bundle_id: str, bundle_id: Optional[str] = None, version: Optional[int] = None) -> Optional[BundleRecord]:
        """Retrieve a specific bundle under a tenant scope (latest if version is None)."""
        if bundle_id is None:
            bundle_id = tenant_id_or_bundle_id
            tenant_id = "system"
        else:
            tenant_id = tenant_id_or_bundle_id
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                if version is None:
                    cursor.execute(
                        """
                        SELECT bundle_id, bundle_name, version, created_at, status, payload, tenant_id, created_by, environment, promotion_status
                        FROM bundles
                        WHERE tenant_id = ? AND bundle_id = ?
                        ORDER BY version DESC LIMIT 1
                        """,
                        (tenant_id, bundle_id),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT bundle_id, bundle_name, version, created_at, status, payload, tenant_id, created_by, environment, promotion_status
                        FROM bundles
                        WHERE tenant_id = ? AND bundle_id = ? AND version = ?
                        """,
                        (tenant_id, bundle_id, version),
                    )
                row = cursor.fetchone()
                if not row:
                    return None
                return BundleRecord(
                    bundle_id=row[0],
                    bundle_name=row[1],
                    version=row[2],
                    created_at=row[3],
                    status=row[4],
                    payload=json.loads(row[5]),
                    tenant_id=row[6],
                    created_by=row[7] if row[7] else "system",
                    environment=row[8] if row[8] else "DEV",
                    promotion_status=row[9] if row[9] else "DRAFT",
                )
            finally:
                conn.close()

    def list_bundles(self, tenant_id: str = "system") -> List[BundleRecord]:
        """Retrieve the latest version of all unique bundles stored under a tenant scope."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT b1.bundle_id, b1.bundle_name, b1.version, b1.created_at, b1.status, b1.payload, b1.tenant_id, b1.created_by, b1.environment, b1.promotion_status
                    FROM bundles b1
                    INNER JOIN (
                        SELECT bundle_id, MAX(version) as max_ver
                        FROM bundles
                        WHERE tenant_id = ?
                        GROUP BY bundle_id
                    ) b2 ON b1.bundle_id = b2.bundle_id AND b1.version = b2.max_ver
                    WHERE b1.tenant_id = ?
                    ORDER BY b1.created_at DESC
                    """,
                    (tenant_id, tenant_id),
                )
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    results.append(
                        BundleRecord(
                            bundle_id=row[0],
                            bundle_name=row[1],
                            version=row[2],
                            created_at=row[3],
                            status=row[4],
                            payload=json.loads(row[5]),
                            tenant_id=row[6],
                            created_by=row[7] if row[7] else "system",
                            environment=row[8] if row[8] else "DEV",
                            promotion_status=row[9] if row[9] else "DRAFT",
                        )
                    )
                return results
            finally:
                conn.close()

    def delete_bundle(self, tenant_id_or_bundle_id: str, bundle_id: Optional[str] = None) -> bool:
        """Delete all versions of a specific bundle under a tenant scope."""
        if bundle_id is None:
            bundle_id = tenant_id_or_bundle_id
            tenant_id = "system"
        else:
            tenant_id = tenant_id_or_bundle_id
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM bundles WHERE tenant_id = ? AND bundle_id = ?", (tenant_id, bundle_id))
                deleted = cursor.rowcount > 0
                conn.commit()
                return deleted
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def save_compilation(self, tenant_id_or_record, record: Optional[CompilationRecord] = None) -> None:
        """Persist a compilation execution history record under a tenant scope."""
        if isinstance(tenant_id_or_record, CompilationRecord):
            record = tenant_id_or_record
            tenant_id = record.tenant_id or "system"
        else:
            tenant_id = tenant_id_or_record
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO compilations (compilation_id, bundle_id, timestamp, duration_ms, confidence, status, tenant_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.compilation_id,
                        record.bundle_id,
                        record.timestamp,
                        record.duration_ms,
                        record.confidence,
                        record.status,
                        tenant_id,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_compilation(self, tenant_id_or_compilation_id: str, compilation_id: Optional[str] = None) -> Optional[CompilationRecord]:
        """Retrieve a single compilation record under a tenant scope."""
        if compilation_id is None:
            compilation_id = tenant_id_or_compilation_id
            tenant_id = "system"
        else:
            tenant_id = tenant_id_or_compilation_id
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT compilation_id, bundle_id, timestamp, duration_ms, confidence, status, tenant_id
                    FROM compilations
                    WHERE tenant_id = ? AND compilation_id = ?
                    """,
                    (tenant_id, compilation_id),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return CompilationRecord(
                    compilation_id=row[0],
                    bundle_id=row[1],
                    timestamp=row[2],
                    duration_ms=row[3],
                    confidence=row[4],
                    status=row[5],
                    tenant_id=row[6],
                )
            finally:
                conn.close()

    def list_compilations(self, tenant_id_or_bundle_id: str, bundle_id: Optional[str] = None) -> List[CompilationRecord]:
        """List historical compilation records associated with a bundle ID under a tenant scope."""
        if bundle_id is None:
            bundle_id = tenant_id_or_bundle_id
            tenant_id = "system"
        else:
            tenant_id = tenant_id_or_bundle_id
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT compilation_id, bundle_id, timestamp, duration_ms, confidence, status, tenant_id
                    FROM compilations
                    WHERE tenant_id = ? AND bundle_id = ?
                    ORDER BY timestamp DESC
                    """,
                    (tenant_id, bundle_id),
                )
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    results.append(
                        CompilationRecord(
                            compilation_id=row[0],
                            bundle_id=row[1],
                            timestamp=row[2],
                            duration_ms=row[3],
                            confidence=row[4],
                            status=row[5],
                            tenant_id=row[6],
                        )
                    )
                return results
            finally:
                conn.close()

    def save_trace(self, tenant_id_or_record, record: Optional[TraceRecord] = None) -> None:
        """Persist a compilation trace step under a tenant scope."""
        if isinstance(tenant_id_or_record, TraceRecord):
            record = tenant_id_or_record
            tenant_id = record.tenant_id or "system"
        else:
            tenant_id = tenant_id_or_record
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO traces (trace_id, compilation_id, step_id, request_id, correlation_id, payload, tenant_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.trace_id,
                        record.compilation_id,
                        record.step_id,
                        record.request_id,
                        record.correlation_id,
                        json.dumps(record.payload),
                        tenant_id,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_traces_by_compilation(self, tenant_id_or_compilation_id: str, compilation_id: Optional[str] = None) -> List[TraceRecord]:
        """Retrieve all step traces compiled during a single execution run under a tenant scope."""
        if compilation_id is None:
            compilation_id = tenant_id_or_compilation_id
            tenant_id = "system"
        else:
            tenant_id = tenant_id_or_compilation_id
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT trace_id, compilation_id, step_id, request_id, correlation_id, payload, tenant_id
                    FROM traces
                    WHERE tenant_id = ? AND compilation_id = ?
                    ORDER BY rowid ASC
                    """,
                    (tenant_id, compilation_id),
                )
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    results.append(
                        TraceRecord(
                            trace_id=row[0],
                            compilation_id=row[1],
                            step_id=row[2],
                            request_id=row[3],
                            correlation_id=row[4],
                            payload=json.loads(row[5]),
                            tenant_id=row[6],
                        )
                    )
                return results
            finally:
                conn.close()

    def get_versions(self, tenant_id_or_bundle_id: str, bundle_id: Optional[str] = None) -> List[BundleRecord]:
        """Retrieve all version records stored for a specific bundle ID under a tenant scope."""
        if bundle_id is None:
            bundle_id = tenant_id_or_bundle_id
            tenant_id = "system"
        else:
            tenant_id = tenant_id_or_bundle_id
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT bundle_id, bundle_name, version, created_at, status, payload, tenant_id, created_by, environment, promotion_status
                    FROM bundles
                    WHERE tenant_id = ? AND bundle_id = ?
                    ORDER BY version ASC
                    """,
                    (tenant_id, bundle_id),
                )
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    results.append(
                        BundleRecord(
                            bundle_id=row[0],
                            bundle_name=row[1],
                            version=row[2],
                            created_at=row[3],
                            status=row[4],
                            payload=json.loads(row[5]),
                            tenant_id=row[6],
                            created_by=row[7] if row[7] else "system",
                            environment=row[8] if row[8] else "DEV",
                            promotion_status=row[9] if row[9] else "DRAFT",
                        )
                    )
                return results
            finally:
                conn.close()

    # --- Tenant-Scoped APIKeyRepository Implementations ---

    def save_api_key(self, tenant_id_or_record, record: Optional[APIKeyRecord] = None) -> None:
        """Persist a new API key record under a tenant scope."""
        if isinstance(tenant_id_or_record, APIKeyRecord):
            record = tenant_id_or_record
            tenant_id = record.tenant_id or "system"
        else:
            tenant_id = tenant_id_or_record
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO api_keys (key_id, name, key_hash, key_prefix, role, tenant_id, global_role, tenant_role, created_at, enabled)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.key_id,
                        record.name,
                        record.key_hash,
                        record.key_prefix,
                        record.tenant_role.value if record.tenant_role else "",
                        tenant_id,
                        record.global_role.value if record.global_role else None,
                        record.tenant_role.value if record.tenant_role else None,
                        record.created_at,
                        1 if record.enabled else 0,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_api_key_by_hash(self, key_hash: str) -> Optional[APIKeyRecord]:
        """Look up an API key by its SHA-256 hash representation globally (used for request login check)."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT key_id, name, key_hash, key_prefix, tenant_id, global_role, tenant_role, created_at, enabled
                    FROM api_keys
                    WHERE key_hash = ?
                    """,
                    (key_hash,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return APIKeyRecord(
                    key_id=row[0],
                    name=row[1],
                    key_hash=row[2],
                    key_prefix=row[3],
                    tenant_id=row[4],
                    global_role=GlobalRole(row[5]) if row[5] else None,
                    tenant_role=TenantRole(row[6]) if row[6] else None,
                    created_at=row[7],
                    enabled=bool(row[8]),
                )
            finally:
                conn.close()

    def get_api_key_by_id(self, tenant_id_or_key_id: str, key_id: Optional[str] = None) -> Optional[APIKeyRecord]:
        """Look up an API key under a tenant scope."""
        if key_id is None:
            key_id = tenant_id_or_key_id
            tenant_id = "system"
        else:
            tenant_id = tenant_id_or_key_id
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT key_id, name, key_hash, key_prefix, tenant_id, global_role, tenant_role, created_at, enabled
                    FROM api_keys
                    WHERE tenant_id = ? AND key_id = ?
                    """,
                    (tenant_id, key_id),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return APIKeyRecord(
                    key_id=row[0],
                    name=row[1],
                    key_hash=row[2],
                    key_prefix=row[3],
                    tenant_id=row[4],
                    global_role=GlobalRole(row[5]) if row[5] else None,
                    tenant_role=TenantRole(row[6]) if row[6] else None,
                    created_at=row[7],
                    enabled=bool(row[8]),
                )
            finally:
                conn.close()

    def list_api_keys(self, tenant_id: str = "system") -> List[APIKeyRecord]:
        """List metadata summaries of all keys in a tenant scope."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT key_id, name, key_hash, key_prefix, tenant_id, global_role, tenant_role, created_at, enabled
                    FROM api_keys
                    WHERE tenant_id = ?
                    ORDER BY created_at DESC
                    """,
                    (tenant_id,),
                )
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    results.append(
                        APIKeyRecord(
                            key_id=row[0],
                            name=row[1],
                            key_hash=row[2],
                            key_prefix=row[3],
                            tenant_id=row[4],
                            global_role=GlobalRole(row[5]) if row[5] else None,
                            tenant_role=TenantRole(row[6]) if row[6] else None,
                            created_at=row[7],
                            enabled=bool(row[8]),
                        )
                    )
                return results
            finally:
                conn.close()

    def revoke_api_key(self, tenant_id_or_key_id: str, key_id: Optional[str] = None) -> bool:
        """Disable/revoke an API key by setting its enabled state to false under a tenant scope."""
        if key_id is None:
            key_id = tenant_id_or_key_id
            tenant_id = "system"
        else:
            tenant_id = tenant_id_or_key_id
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("UPDATE api_keys SET enabled = 0 WHERE tenant_id = ? AND key_id = ?", (tenant_id, key_id))
                revoked = cursor.rowcount > 0
                conn.commit()
                return revoked
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    # --- Tenant-Scoped AuditRepository Implementations ---

    def save_audit_event(self, tenant_id_or_record, record: Optional[AuditEventRecord] = None) -> None:
        """Persist a structured audit log event record under a tenant scope."""
        if isinstance(tenant_id_or_record, AuditEventRecord):
            record = tenant_id_or_record
            tenant_id = record.tenant_id or "system"
        else:
            tenant_id = tenant_id_or_record
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO audit_logs (
                        audit_id, timestamp, request_id, correlation_id, user_id, role,
                        action, resource_type, resource_id, status, details, tenant_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.audit_id,
                        record.timestamp,
                        record.request_id,
                        record.correlation_id,
                        record.user_id,
                        record.role,
                        record.action,
                        record.resource_type,
                        record.resource_id,
                        record.status,
                        json.dumps(record.details),
                        tenant_id,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def list_audit_events(self, tenant_id: str = "system", limit: int = 100, offset: int = 0) -> List[AuditEventRecord]:
        """Retrieve audit log history under a tenant scope using limit/offset pagination."""
        if isinstance(tenant_id, int):
            offset = limit
            limit = tenant_id
            tenant_id = "system"
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT audit_id, timestamp, request_id, correlation_id, user_id, role,
                           action, resource_type, resource_id, status, details, tenant_id
                    FROM audit_logs
                    WHERE tenant_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ? OFFSET ?
                    """,
                    (tenant_id, limit, offset),
                )
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    results.append(
                        AuditEventRecord(
                            audit_id=row[0],
                            timestamp=row[1],
                            request_id=row[2],
                            correlation_id=row[3],
                            user_id=row[4],
                            role=row[5],
                            action=row[6],
                            resource_type=row[7],
                            resource_id=row[8],
                            status=row[9],
                            details=json.loads(row[10]),
                            tenant_id=row[11],
                        )
                    )
                return results
            finally:
                conn.close()

    # --- Vault / Secrets Repository Operations ---

    def save_secret(self, tenant_id: str, record: 'SecretRecord') -> None:
        """Persist a new secret version. If is_current is True, mark previous versions as not current."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                if record.is_current:
                    # Mark all other versions of the secret with the same name in the same tenant as not current
                    cursor.execute(
                        "UPDATE secrets SET is_current = 0 WHERE tenant_id = ? AND name = ?",
                        (tenant_id, record.name)
                    )
                
                cursor.execute(
                    """
                    INSERT INTO secrets (secret_id, tenant_id, name, secret_type, encrypted_value, version, enabled, is_current, created_at, updated_at, environment)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(secret_id) DO UPDATE SET
                        enabled = excluded.enabled,
                        is_current = excluded.is_current,
                        encrypted_value = excluded.encrypted_value,
                        updated_at = excluded.updated_at,
                        environment = excluded.environment
                    """,
                    (
                        record.secret_id,
                        tenant_id,
                        record.name,
                        record.secret_type.value,
                        record.encrypted_value,
                        record.version,
                        1 if record.enabled else 0,
                        1 if record.is_current else 0,
                        record.created_at,
                        record.updated_at,
                        record.environment,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_secret(self, tenant_id: str, secret_id: str) -> Optional['SecretRecord']:
        """Retrieve a specific secret record by ID."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT secret_id, tenant_id, name, secret_type, encrypted_value, version, enabled, is_current, created_at, updated_at, environment
                    FROM secrets
                    WHERE tenant_id = ? AND secret_id = ?
                    """,
                    (tenant_id, secret_id),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                from app.vault.models import SecretRecord, SecretType
                return SecretRecord(
                    secret_id=row[0],
                    tenant_id=row[1],
                    name=row[2],
                    secret_type=SecretType(row[3]),
                    encrypted_value=row[4],
                    version=row[5],
                    enabled=bool(row[6]),
                    is_current=bool(row[7]),
                    created_at=row[8],
                    updated_at=row[9],
                    environment=row[10] if row[10] else "DEV",
                )
            finally:
                conn.close()

    def get_secret_by_name(self, tenant_id: str, name: str, version: Optional[int] = None) -> Optional['SecretRecord']:
        """
        Retrieve a secret by name.
        If version is None, retrieve the current active version (is_current = 1).
        If version is specified, retrieve that specific version.
        """
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                if version is None:
                    cursor.execute(
                        """
                        SELECT secret_id, tenant_id, name, secret_type, encrypted_value, version, enabled, is_current, created_at, updated_at, environment
                        FROM secrets
                        WHERE tenant_id = ? AND name = ? AND is_current = 1
                        """,
                        (tenant_id, name),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT secret_id, tenant_id, name, secret_type, encrypted_value, version, enabled, is_current, created_at, updated_at, environment
                        FROM secrets
                        WHERE tenant_id = ? AND name = ? AND version = ?
                        """,
                        (tenant_id, name, version),
                    )
                row = cursor.fetchone()
                if not row:
                    return None
                from app.vault.models import SecretRecord, SecretType
                return SecretRecord(
                    secret_id=row[0],
                    tenant_id=row[1],
                    name=row[2],
                    secret_type=SecretType(row[3]),
                    encrypted_value=row[4],
                    version=row[5],
                    enabled=bool(row[6]),
                    is_current=bool(row[7]),
                    created_at=row[8],
                    updated_at=row[9],
                    environment=row[10] if row[10] else "DEV",
                )
            finally:
                conn.close()

    def list_secrets(self, tenant_id: str) -> List['SecretRecord']:
        """List all secret records for a tenant."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT secret_id, tenant_id, name, secret_type, encrypted_value, version, enabled, is_current, created_at, updated_at, environment
                    FROM secrets
                    WHERE tenant_id = ?
                    ORDER BY name ASC, version DESC
                    """,
                    (tenant_id,),
                )
                rows = cursor.fetchall()
                results = []
                from app.vault.models import SecretRecord, SecretType
                for row in rows:
                    results.append(
                        SecretRecord(
                            secret_id=row[0],
                            tenant_id=row[1],
                            name=row[2],
                            secret_type=SecretType(row[3]),
                            encrypted_value=row[4],
                            version=row[5],
                            enabled=bool(row[6]),
                            is_current=bool(row[7]),
                            created_at=row[8],
                            updated_at=row[9],
                            environment=row[10] if row[10] else "DEV",
                        )
                    )
                return results
            finally:
                conn.close()

    def disable_secret(self, tenant_id: str, secret_id: str) -> bool:
        """Disable a secret record by setting enabled = 0."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                cursor.execute(
                    "UPDATE secrets SET enabled = 0, updated_at = ? WHERE tenant_id = ? AND secret_id = ? AND enabled = 1",
                    (now, tenant_id, secret_id),
                )
                affected = cursor.rowcount > 0
                conn.commit()
                return affected
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    # --- Phase 21: Connector Marketplace & Collaboration Repository Operations ---

    def save_connector(self, tenant_id: str, record: 'ConnectorRecord') -> None:
        """Persist a version of a connector configuration."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO connectors (
                        connector_id, tenant_id, connector_type, name, description, enabled,
                        configuration, connector_version, schema_version, health_status,
                        last_health_check, last_success_at, consecutive_failures,
                        last_validation_at, validation_error, rate_limit_per_minute,
                        circuit_state, circuit_failure_count, circuit_opened_at, created_at, updated_at, environment
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(connector_id, connector_version) DO UPDATE SET
                        name = excluded.name,
                        description = excluded.description,
                        enabled = excluded.enabled,
                        configuration = excluded.configuration,
                        schema_version = excluded.schema_version,
                        health_status = excluded.health_status,
                        last_health_check = excluded.last_health_check,
                        last_success_at = excluded.last_success_at,
                        consecutive_failures = excluded.consecutive_failures,
                        last_validation_at = excluded.last_validation_at,
                        validation_error = excluded.validation_error,
                        rate_limit_per_minute = excluded.rate_limit_per_minute,
                        circuit_state = excluded.circuit_state,
                        circuit_failure_count = excluded.circuit_failure_count,
                        circuit_opened_at = excluded.circuit_opened_at,
                        updated_at = excluded.updated_at,
                        environment = excluded.environment
                    """,
                    (
                        record.connector_id,
                        tenant_id,
                        record.connector_type.value,
                        record.name,
                        record.description,
                        1 if record.enabled else 0,
                        json.dumps(record.configuration),
                        record.connector_version,
                        record.schema_version,
                        record.health_status,
                        record.last_health_check,
                        record.last_success_at,
                        record.consecutive_failures,
                        record.last_validation_at,
                        record.validation_error,
                        record.rate_limit_per_minute,
                        record.circuit_state,
                        record.circuit_failure_count,
                        record.circuit_opened_at,
                        record.created_at,
                        record.updated_at,
                        record.environment,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_connector(self, tenant_id: str, connector_id: str, version: Optional[int] = None) -> Optional['ConnectorRecord']:
        """Retrieve a specific version of a connector config, or the latest version if version is None."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                if version is None:
                    cursor.execute(
                        """
                        SELECT connector_id, tenant_id, connector_type, name, description, enabled,
                               configuration, connector_version, schema_version, health_status,
                               last_health_check, last_success_at, consecutive_failures,
                               last_validation_at, validation_error, rate_limit_per_minute,
                               circuit_state, circuit_failure_count, circuit_opened_at, created_at, updated_at, environment
                        FROM connectors
                        WHERE tenant_id = ? AND connector_id = ?
                        ORDER BY connector_version DESC LIMIT 1
                        """,
                        (tenant_id, connector_id),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT connector_id, tenant_id, connector_type, name, description, enabled,
                               configuration, connector_version, schema_version, health_status,
                               last_health_check, last_success_at, consecutive_failures,
                               last_validation_at, validation_error, rate_limit_per_minute,
                               circuit_state, circuit_failure_count, circuit_opened_at, created_at, updated_at, environment
                        FROM connectors
                        WHERE tenant_id = ? AND connector_id = ? AND connector_version = ?
                        """,
                        (tenant_id, connector_id, version),
                    )
                row = cursor.fetchone()
                if not row:
                    return None
                from app.connectors.models import ConnectorRecord, ConnectorType
                return ConnectorRecord(
                    connector_id=row[0],
                    tenant_id=row[1],
                    connector_type=ConnectorType(row[2]),
                    name=row[3],
                    description=row[4],
                    enabled=bool(row[5]),
                    configuration=json.loads(row[6]) if row[6] else {},
                    connector_version=row[7],
                    schema_version=row[8],
                    health_status=row[9],
                    last_health_check=row[10],
                    last_success_at=row[11],
                    consecutive_failures=row[12],
                    last_validation_at=row[13],
                    validation_error=row[14],
                    rate_limit_per_minute=row[15],
                    circuit_state=row[16],
                    circuit_failure_count=row[17],
                    circuit_opened_at=row[18],
                    created_at=row[19],
                    updated_at=row[20],
                    environment=row[21] if row[21] else "DEV",
                )
            finally:
                conn.close()

    def list_connectors(self, tenant_id: str) -> List['ConnectorRecord']:
        """List latest version of all unique connectors inside a tenant."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT connector_id, tenant_id, connector_type, name, description, enabled,
                           configuration, connector_version, schema_version, health_status,
                           last_health_check, last_success_at, consecutive_failures,
                           last_validation_at, validation_error, rate_limit_per_minute,
                           circuit_state, circuit_failure_count, circuit_opened_at, created_at, updated_at, environment
                    FROM connectors
                    WHERE tenant_id = ?
                    GROUP BY connector_id
                    ORDER BY name ASC, connector_version DESC
                    """,
                    (tenant_id,),
                )
                rows = cursor.fetchall()
                results = []
                from app.connectors.models import ConnectorRecord, ConnectorType
                for row in rows:
                    results.append(
                        ConnectorRecord(
                            connector_id=row[0],
                            tenant_id=row[1],
                            connector_type=ConnectorType(row[2]),
                            name=row[3],
                            description=row[4],
                            enabled=bool(row[5]),
                            configuration=json.loads(row[6]) if row[6] else {},
                            connector_version=row[7],
                            schema_version=row[8],
                            health_status=row[9],
                            last_health_check=row[10],
                            last_success_at=row[11],
                            consecutive_failures=row[12],
                            last_validation_at=row[13],
                            validation_error=row[14],
                            rate_limit_per_minute=row[15],
                            circuit_state=row[16],
                            circuit_failure_count=row[17],
                            circuit_opened_at=row[18],
                            created_at=row[19],
                            updated_at=row[20],
                            environment=row[21] if row[21] else "DEV",
                        )
                    )
                return results
            finally:
                conn.close()

    def delete_connector(self, tenant_id: str, connector_id: str) -> bool:
        """Hard delete all versions of a connector configuration."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM connectors WHERE tenant_id = ? AND connector_id = ?", (tenant_id, connector_id))
                affected = cursor.rowcount > 0
                conn.commit()
                return affected
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    # --- Approval Requests Repository ---

    def save_approval_request(self, tenant_id: str, record: 'ApprovalRequestRecord') -> None:
        """Persist or update an approval request."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO approval_requests (
                        approval_id, tenant_id, execution_id, node_id, status,
                        escalation_level, escalated_to, escalation_policy, approval_token,
                        created_at, decided_at, decision, decided_by, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(approval_id) DO UPDATE SET
                        status = excluded.status,
                        escalation_level = excluded.escalation_level,
                        escalated_to = excluded.escalated_to,
                        decided_at = excluded.decided_at,
                        decision = excluded.decision,
                        decided_by = excluded.decided_by
                    """,
                    (
                        record.approval_id,
                        tenant_id,
                        record.execution_id,
                        record.node_id,
                        record.status.value,
                        record.escalation_level,
                        record.escalated_to,
                        json.dumps(record.escalation_policy),
                        record.approval_token,
                        record.created_at,
                        record.decided_at,
                        record.decision.value if record.decision else None,
                        record.decided_by,
                        record.expires_at,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_approval_request(self, tenant_id: str, approval_id: str) -> Optional['ApprovalRequestRecord']:
        """Retrieve an approval request record."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT approval_id, tenant_id, execution_id, node_id, status,
                           escalation_level, escalated_to, escalation_policy, approval_token,
                           created_at, decided_at, decision, decided_by, expires_at
                    FROM approval_requests
                    WHERE tenant_id = ? AND approval_id = ?
                    """,
                    (tenant_id, approval_id),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                from app.collaboration.models import ApprovalRequestRecord, ApprovalState
                return ApprovalRequestRecord(
                    approval_id=row[0],
                    tenant_id=row[1],
                    execution_id=row[2],
                    node_id=row[3],
                    status=ApprovalState(row[4]),
                    escalation_level=row[5],
                    escalated_to=row[6],
                    escalation_policy=json.loads(row[7]) if row[7] else {},
                    approval_token=row[8],
                    created_at=row[9],
                    decided_at=row[10],
                    decision=ApprovalState(row[11]) if row[11] else None,
                    decided_by=row[12],
                    expires_at=row[13],
                )
            finally:
                conn.close()

    def list_approval_requests(self, tenant_id: str) -> List['ApprovalRequestRecord']:
        """List all approvals for a tenant."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT approval_id, tenant_id, execution_id, node_id, status,
                           escalation_level, escalated_to, escalation_policy, approval_token,
                           created_at, decided_at, decision, decided_by, expires_at
                    FROM approval_requests
                    WHERE tenant_id = ?
                    ORDER BY created_at DESC
                    """,
                    (tenant_id,),
                )
                rows = cursor.fetchall()
                results = []
                from app.collaboration.models import ApprovalRequestRecord, ApprovalState
                for row in rows:
                    results.append(
                        ApprovalRequestRecord(
                            approval_id=row[0],
                            tenant_id=row[1],
                            execution_id=row[2],
                            node_id=row[3],
                            status=ApprovalState(row[4]),
                            escalation_level=row[5],
                            escalated_to=row[6],
                            escalation_policy=json.loads(row[7]) if row[7] else {},
                            approval_token=row[8],
                            created_at=row[9],
                            decided_at=row[10],
                            decision=ApprovalState(row[11]) if row[11] else None,
                            decided_by=row[12],
                            expires_at=row[13],
                        )
                    )
                return results
            finally:
                conn.close()

    # --- Approval Callbacks (Replay Protection) ---

    def save_approval_callback(self, tenant_id: str, record: 'ApprovalCallbackRecord') -> None:
        """Save callback request."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO approval_callbacks (
                        callback_id, tenant_id, approval_id, source, payload_hash, nonce, timestamp, received_at, processed
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.callback_id,
                        tenant_id,
                        record.approval_id,
                        record.source,
                        record.payload_hash,
                        record.nonce,
                        record.timestamp,
                        record.received_at,
                        1 if record.processed else 0,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_approval_callback(self, tenant_id: str, callback_id: str) -> Optional['ApprovalCallbackRecord']:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT callback_id, tenant_id, approval_id, source, payload_hash, nonce, timestamp, received_at, processed
                    FROM approval_callbacks
                    WHERE tenant_id = ? AND callback_id = ?
                    """,
                    (tenant_id, callback_id),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                from app.collaboration.models import ApprovalCallbackRecord
                return ApprovalCallbackRecord(
                    callback_id=row[0],
                    tenant_id=row[1],
                    approval_id=row[2],
                    source=row[3],
                    payload_hash=row[4],
                    nonce=row[5],
                    timestamp=row[6],
                    received_at=row[7],
                    processed=bool(row[8]),
                )
            finally:
                conn.close()

    def is_callback_nonce_processed(self, nonce: str) -> bool:
        """Check if nonce has already been logged."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM approval_callbacks WHERE nonce = ?", (nonce,))
                return cursor.fetchone() is not None
            finally:
                conn.close()

    # --- Approval Reminders ---

    def save_approval_reminder(self, record: 'ApprovalReminderRecord') -> None:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO approval_reminders (reminder_id, approval_id, reminder_number, sent_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (record.reminder_id, record.approval_id, record.reminder_number, record.sent_at),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_approval_reminders(self, approval_id: str) -> List['ApprovalReminderRecord']:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT reminder_id, approval_id, reminder_number, sent_at FROM approval_reminders WHERE approval_id = ?",
                    (approval_id,),
                )
                rows = cursor.fetchall()
                results = []
                from app.collaboration.models import ApprovalReminderRecord
                for row in rows:
                    results.append(
                        ApprovalReminderRecord(
                            reminder_id=row[0],
                            approval_id=row[1],
                            reminder_number=row[2],
                            sent_at=row[3],
                        )
                    )
                return results
            finally:
                conn.close()

    # --- Incident Links ---

    def save_incident_link(self, tenant_id: str, record: 'IncidentLinkRecord') -> None:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO incident_links (link_id, tenant_id, execution_id, connector_id, external_system, external_id, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.link_id,
                        tenant_id,
                        record.execution_id,
                        record.connector_id,
                        record.external_system,
                        record.external_id,
                        record.status,
                        record.created_at,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def list_incident_links(self, tenant_id: str, execution_id: str) -> List['IncidentLinkRecord']:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT link_id, tenant_id, execution_id, connector_id, external_system, external_id, status, created_at
                    FROM incident_links
                    WHERE tenant_id = ? AND execution_id = ?
                    """,
                    (tenant_id, execution_id),
                )
                rows = cursor.fetchall()
                results = []
                from app.collaboration.models import IncidentLinkRecord
                for row in rows:
                    results.append(
                        IncidentLinkRecord(
                            link_id=row[0],
                            tenant_id=row[1],
                            execution_id=row[2],
                            connector_id=row[3],
                            external_system=row[4],
                            external_id=row[5],
                            status=row[6],
                            created_at=row[7],
                        )
                    )
                return results
            finally:
                conn.close()

    # --- Notification Templates ---

    def save_notification_template(self, tenant_id: str, record: 'NotificationTemplateRecord') -> None:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO notification_templates (template_id, tenant_id, event_type, channel, subject_template, body_template, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(template_id) DO UPDATE SET
                        subject_template = excluded.subject_template,
                        body_template = excluded.body_template,
                        updated_at = excluded.updated_at
                    """,
                    (
                        record.template_id,
                        tenant_id,
                        record.event_type,
                        record.channel,
                        record.subject_template,
                        record.body_template,
                        record.created_at,
                        record.updated_at,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_notification_template(self, tenant_id: str, event_type: str, channel: str) -> Optional['NotificationTemplateRecord']:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT template_id, tenant_id, event_type, channel, subject_template, body_template, created_at, updated_at
                    FROM notification_templates
                    WHERE tenant_id = ? AND event_type = ? AND channel = ?
                    """,
                    (tenant_id, event_type, channel),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                from app.collaboration.models import NotificationTemplateRecord
                return NotificationTemplateRecord(
                    template_id=row[0],
                    tenant_id=row[1],
                    event_type=row[2],
                    channel=row[3],
                    subject_template=row[4],
                    body_template=row[5],
                    created_at=row[6],
                    updated_at=row[7],
                )
            finally:
                conn.close()

    # --- Governance & Change Management Operations ---

    def save_policy(self, tenant_id: str, record: Any) -> None:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                if record.is_current:
                    cursor.execute(
                        "UPDATE policies SET is_current = 0 WHERE tenant_id = ? AND name = ?",
                        (tenant_id, record.name)
                    )
                cursor.execute(
                    """
                    INSERT INTO policies (policy_id, tenant_id, name, policy_type, enabled, priority, version, is_current, policy_definition, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(policy_id, version) DO UPDATE SET
                        enabled = excluded.enabled,
                        priority = excluded.priority,
                        is_current = excluded.is_current,
                        policy_definition = excluded.policy_definition,
                        updated_at = excluded.updated_at
                    """,
                    (
                        record.policy_id,
                        tenant_id,
                        record.name,
                        record.policy_type.value if hasattr(record.policy_type, "value") else record.policy_type,
                        1 if record.enabled else 0,
                        record.priority,
                        record.version,
                        1 if record.is_current else 0,
                        json.dumps(record.policy_definition),
                        record.created_at,
                        record.updated_at,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_policy(self, tenant_id: str, policy_id: str, version: Optional[int] = None) -> Optional[Any]:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                if version is None:
                    cursor.execute(
                        """
                        SELECT policy_id, tenant_id, name, policy_type, enabled, priority, version, is_current, policy_definition, created_at, updated_at
                        FROM policies
                        WHERE tenant_id = ? AND policy_id = ? AND is_current = 1
                        """,
                        (tenant_id, policy_id),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT policy_id, tenant_id, name, policy_type, enabled, priority, version, is_current, policy_definition, created_at, updated_at
                        FROM policies
                        WHERE tenant_id = ? AND policy_id = ? AND version = ?
                        """,
                        (tenant_id, policy_id, version),
                    )
                row = cursor.fetchone()
                if not row:
                    return None
                from app.governance.models import PolicyRecord, PolicyType
                return PolicyRecord(
                    policy_id=row[0],
                    tenant_id=row[1],
                    name=row[2],
                    policy_type=PolicyType(row[3]),
                    enabled=bool(row[4]),
                    priority=row[5],
                    version=row[6],
                    is_current=bool(row[7]),
                    policy_definition=json.loads(row[8]) if row[8] else [],
                    created_at=row[9],
                    updated_at=row[10],
                )
            finally:
                conn.close()

    def list_policies(self, tenant_id: str) -> List[Any]:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT policy_id, tenant_id, name, policy_type, enabled, priority, version, is_current, policy_definition, created_at, updated_at
                    FROM policies
                    WHERE tenant_id = ? AND is_current = 1
                    ORDER BY priority DESC, name ASC
                    """,
                    (tenant_id,),
                )
                rows = cursor.fetchall()
                results = []
                from app.governance.models import PolicyRecord, PolicyType
                for row in rows:
                    results.append(
                        PolicyRecord(
                            policy_id=row[0],
                            tenant_id=row[1],
                            name=row[2],
                            policy_type=PolicyType(row[3]),
                            enabled=bool(row[4]),
                            priority=row[5],
                            version=row[6],
                            is_current=bool(row[7]),
                            policy_definition=json.loads(row[8]) if row[8] else [],
                            created_at=row[9],
                            updated_at=row[10],
                        )
                    )
                return results
            finally:
                conn.close()

    def delete_policy(self, tenant_id: str, policy_id: str) -> bool:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM policies WHERE tenant_id = ? AND policy_id = ?", (tenant_id, policy_id))
                affected = cursor.rowcount > 0
                conn.commit()
                return affected
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def save_deployment(self, tenant_id: str, record: Any) -> None:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO deployments (deployment_id, tenant_id, bundle_id, version, environment, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.deployment_id,
                        tenant_id,
                        record.bundle_id,
                        record.version,
                        record.environment,
                        record.status,
                        record.created_at,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_deployment(self, tenant_id: str, deployment_id: str) -> Optional[Any]:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT deployment_id, tenant_id, bundle_id, version, environment, status, created_at
                    FROM deployments
                    WHERE tenant_id = ? AND deployment_id = ?
                    """,
                    (tenant_id, deployment_id),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                # Create deployment record
                from app.governance.models import DeploymentRecord
                return DeploymentRecord(
                    deployment_id=row[0],
                    tenant_id=row[1],
                    bundle_id=row[2],
                    version=row[3],
                    environment=row[4],
                    status=row[5],
                    created_at=row[6],
                )
            finally:
                conn.close()

    def list_deployments(self, tenant_id: str) -> List[Any]:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT deployment_id, tenant_id, bundle_id, version, environment, status, created_at
                    FROM deployments
                    WHERE tenant_id = ?
                    ORDER BY created_at DESC
                    """,
                    (tenant_id,),
                )
                rows = cursor.fetchall()
                results = []
                from app.governance.models import DeploymentRecord
                for row in rows:
                    results.append(
                        DeploymentRecord(
                            deployment_id=row[0],
                            tenant_id=row[1],
                            bundle_id=row[2],
                            version=row[3],
                            environment=row[4],
                            status=row[5],
                            created_at=row[6],
                        )
                    )
                return results
            finally:
                conn.close()

    def save_deployment_snapshot(self, tenant_id: str, record: Any) -> None:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO deployment_snapshots (snapshot_id, deployment_id, tenant_id, bundle_payload, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        record.snapshot_id,
                        record.deployment_id,
                        tenant_id,
                        json.dumps(record.bundle_payload),
                        record.created_at,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_deployment_snapshot(self, tenant_id: str, snapshot_id: str) -> Optional[Any]:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT snapshot_id, deployment_id, tenant_id, bundle_payload, created_at
                    FROM deployment_snapshots
                    WHERE tenant_id = ? AND snapshot_id = ?
                    """,
                    (tenant_id, snapshot_id),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                from app.governance.models import DeploymentSnapshotRecord
                return DeploymentSnapshotRecord(
                    snapshot_id=row[0],
                    deployment_id=row[1],
                    tenant_id=row[2],
                    bundle_payload=json.loads(row[3]) if row[3] else {},
                    created_at=row[4],
                )
            finally:
                conn.close()

    def get_deployment_snapshot_by_deployment(self, tenant_id: str, deployment_id: str) -> Optional[Any]:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT snapshot_id, deployment_id, tenant_id, bundle_payload, created_at
                    FROM deployment_snapshots
                    WHERE tenant_id = ? AND deployment_id = ?
                    LIMIT 1
                    """,
                    (tenant_id, deployment_id),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                from app.governance.models import DeploymentSnapshotRecord
                return DeploymentSnapshotRecord(
                    snapshot_id=row[0],
                    deployment_id=row[1],
                    tenant_id=row[2],
                    bundle_payload=json.loads(row[3]) if row[3] else {},
                    created_at=row[4],
                )
            finally:
                conn.close()

    def save_deployment_approval(self, tenant_id: str, record: Any) -> None:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO deployment_approvals (approval_id, deployment_id, tenant_id, approved_by, approved_at, decision, comments)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.approval_id,
                        record.deployment_id,
                        tenant_id,
                        record.approved_by,
                        record.approved_at,
                        record.decision,
                        record.comments,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_deployment_approvals(self, tenant_id: str, deployment_id: str) -> List[Any]:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT approval_id, deployment_id, tenant_id, approved_by, approved_at, decision, comments
                    FROM deployment_approvals
                    WHERE tenant_id = ? AND deployment_id = ?
                    """,
                    (tenant_id, deployment_id),
                )
                rows = cursor.fetchall()
                results = []
                from app.governance.models import DeploymentApprovalRecord
                for row in rows:
                    results.append(
                        DeploymentApprovalRecord(
                            approval_id=row[0],
                            deployment_id=row[1],
                            tenant_id=row[2],
                            approved_by=row[3],
                            approved_at=row[4],
                            decision=row[5],
                            comments=row[6],
                        )
                    )
                return results
            finally:
                conn.close()

    def save_compliance_snapshot(self, tenant_id: str, record: Any) -> None:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO compliance_snapshots (snapshot_id, tenant_id, report_type, report_data, snapshot_hash, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.snapshot_id,
                        tenant_id,
                        record.report_type,
                        json.dumps(record.report_data),
                        record.snapshot_hash,
                        record.created_at,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_compliance_snapshot(self, tenant_id: str, snapshot_id: str) -> Optional[Any]:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT snapshot_id, tenant_id, report_type, report_data, snapshot_hash, created_at
                    FROM compliance_snapshots
                    WHERE tenant_id = ? AND snapshot_id = ?
                    """,
                    (tenant_id, snapshot_id),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                from app.governance.models import ComplianceSnapshotRecord
                return ComplianceSnapshotRecord(
                    snapshot_id=row[0],
                    tenant_id=row[1],
                    report_type=row[2],
                    report_data=json.loads(row[3]) if row[3] else {},
                    snapshot_hash=row[4],
                    created_at=row[5],
                )
            finally:
                conn.close()

    def list_compliance_snapshots(self, tenant_id: str) -> List[Any]:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT snapshot_id, tenant_id, report_type, report_data, snapshot_hash, created_at
                    FROM compliance_snapshots
                    WHERE tenant_id = ?
                    ORDER BY created_at DESC
                    """,
                    (tenant_id,),
                )
                rows = cursor.fetchall()
                results = []
                from app.governance.models import ComplianceSnapshotRecord
                for row in rows:
                    results.append(
                        ComplianceSnapshotRecord(
                            snapshot_id=row[0],
                            tenant_id=row[1],
                            report_type=row[2],
                            report_data=json.loads(row[3]) if row[3] else {},
                            snapshot_hash=row[4],
                            created_at=row[5],
                        )
                    )
                return results
            finally:
                conn.close()

    def save_policy_event(self, tenant_id: str, record: Any) -> None:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO policy_events (event_id, tenant_id, policy_id, resource_type, resource_id, decision, timestamp, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.event_id,
                        tenant_id,
                        record.policy_id,
                        record.resource_type,
                        record.resource_id,
                        record.decision,
                        record.timestamp,
                        record.expires_at,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def list_policy_events(self, tenant_id: str) -> List[Any]:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT event_id, tenant_id, policy_id, resource_type, resource_id, decision, timestamp, expires_at
                    FROM policy_events
                    WHERE tenant_id = ?
                    ORDER BY timestamp DESC
                    """,
                    (tenant_id,),
                )
                rows = cursor.fetchall()
                results = []
                from app.governance.models import PolicyEventRecord
                for row in rows:
                    results.append(
                        PolicyEventRecord(
                            event_id=row[0],
                            tenant_id=row[1],
                            policy_id=row[2],
                            resource_type=row[3],
                            resource_id=row[4],
                            decision=row[5],
                            timestamp=row[6],
                            expires_at=row[7],
                        )
                    )
                return results
            finally:
                conn.close()

    def save_system_flag(self, flag_name: str, flag_value: str, updated_by: Optional[str] = None) -> None:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO system_flags (flag_name, flag_value, updated_by, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (flag_name, flag_value, updated_by, now),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def get_system_flag(self, flag_name: str) -> Optional[str]:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT flag_value FROM system_flags WHERE flag_name = ?", (flag_name,))
                row = cursor.fetchone()
                return row[0] if row else None
            finally:
                conn.close()
