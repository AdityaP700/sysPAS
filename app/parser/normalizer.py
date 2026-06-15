import re
from typing import Optional
from app.domain.enums import StepType, ActionType


def normalize_time_window(time_str: Optional[str]) -> Optional[str]:
    """
    Normalizes time duration strings to a standard format (e.g. '5 min' -> '5m').
    Returns None if input is empty or None.
    """
    if not time_str:
        return None
    
    time_str = time_str.lower().strip()
    
    # Check for direct matches like '5m', '1h', '30s'
    if re.match(r'^\d+[smhd]$', time_str):
        return time_str

    # Regex for standard units
    match = re.search(r'(\d+)\s*(minute|min|second|sec|hour|hr|day|d|m|s|h)s?', time_str)
    if match:
        val, unit = match.groups()
        if unit.startswith('min') or unit == 'm':
            return f"{val}m"
        elif unit.startswith('sec') or unit == 's':
            return f"{val}s"
        elif unit.startswith('hour') or unit.startswith('hr') or unit == 'h':
            return f"{val}h"
        elif unit.startswith('day') or unit == 'd':
            return f"{val}d"
            
    return time_str


def infer_step_type(description: str) -> StepType:
    """
    Infers StepType from description text.
    """
    desc_lower = description.lower()
    
    if any(kw in desc_lower for kw in ["escalate", "tier 2", "tier 3", "pagerduty", "opsgenie", "on-call"]):
        return StepType.ESCALATION
    if any(kw in desc_lower for kw in ["block", "disable", "remediate", "kill", "jira", "ticket", "delete", "create ticket"]):
        return StepType.ACTION
    if any(kw in desc_lower for kw in ["correlate", "join", "lookup", "combine", "cross-reference"]):
        return StepType.CORRELATION
    if any(kw in desc_lower for kw in ["check", "search", "find", "query", "detect", "alert", "monitor"]):
        return StepType.DETECTION
    if any(kw in desc_lower for kw in ["manual", "human", "gate", "approval"]):
        return StepType.MANUAL
        
    return StepType.INVESTIGATION


def infer_action_type(action_desc: Optional[str]) -> Optional[ActionType]:
    """
    Infers ActionType from an action description string.
    """
    if not action_desc:
        return None
        
    action_lower = action_desc.lower()
    
    if any(kw in action_lower for kw in ["escalate", "tier", "pagerduty", "notify team"]):
        return ActionType.HUMAN_ESCALATION
    if any(kw in action_lower for kw in ["block", "blacklist", "disable", "ban", "deny"]):
        return ActionType.BLOCK_IP
    if any(kw in action_lower for kw in ["jira", "ticket", "servicenow", "snow", "incident"]):
        return ActionType.CREATE_JIRA
    if any(kw in action_lower for kw in ["email", "slack", "notify", "message", "alert"]):
        return ActionType.EMAIL_NOTIFICATION
    if any(kw in action_lower for kw in ["manual", "human"]):
        return ActionType.MANUAL
        
    return None
