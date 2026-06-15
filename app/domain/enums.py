from enum import Enum


class StepType(str, Enum):
    """Types of steps that can exist within a runbook."""
    DETECTION = "DETECTION"
    CORRELATION = "CORRELATION"
    ESCALATION = "ESCALATION"
    ACTION = "ACTION"
    MANUAL = "MANUAL"
    INVESTIGATION = "INVESTIGATION"


class ActionType(str, Enum):
    """Types of actions that can be executed autonomously or manually."""
    HUMAN_ESCALATION = "HUMAN_ESCALATION"
    BLOCK_IP = "BLOCK_IP"
    CREATE_JIRA = "CREATE_JIRA"
    EMAIL_NOTIFICATION = "EMAIL_NOTIFICATION"
    MANUAL = "MANUAL"


class ApprovalLevel(str, Enum):
    """Governance approval levels required for automated action execution."""
    NONE = "NONE"
    MEMBER = "MEMBER"
    LEAD = "LEAD"
    ADMIN = "ADMIN"


class CompilationStatus(str, Enum):
    """The status of the compilation process for runbook steps."""
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
