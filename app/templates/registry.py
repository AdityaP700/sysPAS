from typing import Dict


class SPLTemplateRegistry:
    """Stores and retrieves parameterized SPL query templates for security intents."""

    TEMPLATES: Dict[str, str] = {
        "FAILED_LOGIN": "index={index} {status_filter} | stats count by {group_clause}",
        "BRUTE_FORCE": "index={index} {status_filter} | bucket _time span={time_window} | stats count as failure_count by {group_clause} | where failure_count > {threshold}",
        "SUSPICIOUS_IP": "index={index} {ip_field} IN ({suspicious_ips})",
        "THREAT_LOOKUP": "index={index} | lookup threat_intel_lookup {ip_field} OUTPUT threat_score, category",
        "ESCALATION": "| makeresults | eval message=\"Human escalation triggered for step: {step_id}\"",
        "POWERSHELL_ACTIVITY": "index={index} (process=\"powershell.exe\" OR process=\"pwsh.exe\") (action=\"encoded\" OR \"-enc\" OR \"-executionpolicy bypass\" OR \"bypass\") | stats count by {group_clause}",
        "CREDENTIAL_DUMPING": "index={index} (process=\"mimikatz.exe\" OR \"mimikatz\" OR \"lsass\") | stats count by {group_clause}, process",
        "PERSISTENCE": "index={index} (\"currentversion\\\\run\" OR \"reg add\" OR \"persistence\") | stats count by {group_clause}, process",
        "REGISTRY_MODIFICATION": "index={index} (\"reg add\" OR \"currentversion\\\\run\") | stats count by {group_clause}, process",
        "ACCOUNT_CREATION": "index={index} (\"net user\" OR \"user add\" OR \"/add\") | stats count by {group_clause}",
        "GENERIC": "index={index} | head 100"
    }

    @classmethod
    def get_template(cls, intent_name: str) -> str:
        """
        Returns the raw SPL template string associated with the intent.
        Falls back to GENERIC template if intent is not found.
        """
        return cls.TEMPLATES.get(intent_name, cls.TEMPLATES["GENERIC"])
