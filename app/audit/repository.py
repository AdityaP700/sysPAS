import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from typing import List, Optional
from app.audit.models import AuditEventRecord


class AuditRepository(ABC):
    """Abstract interface defining required storage CRUD operations for audit logs."""

    @abstractmethod
    def save_audit_event(self, tenant_id: str, record: AuditEventRecord) -> None:
        """Persist a new audit log event."""
        pass

    @abstractmethod
    def list_audit_events(self, tenant_id: str = "system", limit: int = 100, offset: int = 0) -> List[AuditEventRecord]:
        """Retrieve historical audit events using pagination (limit and offset)."""
        pass


class SQLiteAuditRepository(AuditRepository):
    """Thread-safe SQLite implementation of the AuditRepository interface."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.lock = threading.RLock()
        self._create_table()

    def _create_table(self) -> None:
        """Create audit logs table if it does not exist."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
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
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def save_audit_event(self, tenant_id_or_record, record: Optional[AuditEventRecord] = None) -> None:
        """Persist a structured audit log event record using a parameterized query."""
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
        """Retrieve audit log history using limit/offset pagination, sorted by timestamp DESC."""
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
