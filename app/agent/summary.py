from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

import anthropic

from app.config.settings import settings

logger = logging.getLogger(__name__)


class SummaryGenerator:
    """
    Synthesizes the investigation steps, query history, and results into a
    structured threat classification and professional Executive Incident Report.
    """

    def __init__(self) -> None:
        self._client: Optional[anthropic.Anthropic] = None

    def _ensure_init(self) -> bool:
        """Initialise the Anthropic client using the configured key. Returns True if successful."""
        if self._client is not None:
            return True

        api_key = settings.claude_api_key
        if not api_key:
            import os
            api_key = os.environ.get("RUNBOOKMIND_CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")

        if not api_key:
            logger.warning("Claude API key not found. SummaryGenerator will operate in mock/fallback mode.")
            return False

        try:
            self._client = anthropic.Anthropic(api_key=api_key)
            return True
        except Exception as e:
            logger.error(f"Failed to initialise Anthropic client: {e}")
            return False

    def generate_report(
        self,
        task_description: str,
        history: List[Dict[str, Any]],
        threat_classification: Optional[dict] = None
    ) -> str:
        """
        Analyze the full investigation history and generate a detailed markdown incident report
        with threat classification and recommended actions.
        """
        if not self._ensure_init():
            return self._generate_fallback_report(task_description, history, threat_classification)

        # Format history summary for the prompt
        history_summary = []
        for i, h in enumerate(history):
            history_summary.append(
                f"Step {i+1}:\n"
                f"- SPL Query: {h.get('spl')}\n"
                f"- Result Count: {h.get('result_count')}\n"
                f"- Agent Reasoning: {h.get('reasoning')}\n"
                f"- Sample Results (up to 5): {json.dumps(h.get('sample_results', [])[:5])}\n"
            )
        history_str = "\n".join(history_summary) if history_summary else "No history recorded."

        system_prompt = (
            "You are a Senior Threat Classifier and Incident Reporter.\n"
            "Analyze the target task, the query execution history, and the results to classify the incident and provide recommended actions.\n\n"
            "You MUST respond ONLY with a valid JSON object. Do not include any explanation outside the JSON object.\n"
            "The JSON object must have exactly the following structure:\n"
            "{\n"
            '  "incident_type": "<e.g., Brute Force, Credential Attack, Exfiltration, Malware, etc.>",\n'
            '  "severity": "<Critical, High, Medium, or Low>",\n'
            '  "confidence": <float between 0.0 and 1.0, representing classification confidence>,\n'
            '  "affected_hosts": ["<list of IP addresses or hostnames identified as affected/target>"],\n'
            '  "affected_users": ["<list of usernames identified as affected/target>"],\n'
            '  "root_cause": "<detailed root cause explanation>"\n'
            '  "recommended_actions": {\n'
            '    "containment": ["<list of containment action items>"],\n'
            '    "eradication": ["<list of eradication action items>"],\n'
            '    "recovery": ["<list of recovery action items>"],\n'
            '    "prevention": ["<list of prevention/remediation action items>"]\n'
            '  },\n'
            '  "executive_summary": "<concise executive summary paragraph of the incident and investigation findings>"\n'
            "}"
        )

        user_prompt = (
            f"Target Investigation Task: {task_description}\n\n"
            f"Initial Threat Classification: {json.dumps(threat_classification) if threat_classification else 'None'}\n\n"
            f"Investigation History & Query Results:\n{history_str}\n"
        )

        try:
            message = self._client.messages.create(
                model=settings.claude_model,
                max_tokens=settings.claude_max_tokens * 2,
                temperature=0.0,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_prompt}
                ]
            )
            raw_text = message.content[0].text.strip()

            # Extract and parse JSON
            cleaned = re.sub(r'^```(?:json)?\n?', '', raw_text.strip(), flags=re.IGNORECASE)
            cleaned = re.sub(r'\n?```$', '', cleaned)
            start = cleaned.find('{')
            end = cleaned.rfind('}')
            if start != -1 and end != -1:
                cleaned = cleaned[start:end+1]
            
            data = json.loads(cleaned)
            return self._build_markdown_report(data, history)

        except Exception as e:
            logger.error(f"Failed to generate structured summary report via Claude: {e}")
            return self._generate_fallback_report(task_description, history, threat_classification)

    def _build_markdown_report(self, data: Dict[str, Any], history: List[Dict[str, Any]]) -> str:
        """Helper to format parsed JSON threat intelligence into a premium markdown report."""
        confidence_pct = int(data.get("confidence", 1.0) * 100)
        
        # Determine MITRE mapping
        mitre_list = data.get("mitre", [])
        if not mitre_list:
            from app.agent.mitre_mapper import map_threat_to_mitre
            mitre_list = map_threat_to_mitre(data.get("incident_type", ""))
            
        mitre_str = ", ".join(mitre_list) if mitre_list else "None mapped"
        
        report_lines = [
            f"# Executive Incident Report",
            f"",
            f"## Critical Incident Details",
            f"- **Incident Type**: {data.get('incident_type', 'Unknown')}",
            f"- **Severity**: {data.get('severity', 'Medium')}",
            f"- **Confidence**: {confidence_pct}%",
            f"- **MITRE ATT&CK**: {mitre_str}",
            f"",
            f"## Impacted Assets",
        ]

        # Affected Users
        users = data.get("affected_users", [])
        report_lines.append("### Affected Users")
        if users:
            for user in users:
                report_lines.append(f"- {user}")
        else:
            report_lines.append("- None identified")
        report_lines.append("")

        # Affected Hosts
        hosts = data.get("affected_hosts", [])
        report_lines.append("### Affected Hosts")
        if hosts:
            for host in hosts:
                report_lines.append(f"- {host}")
        else:
            report_lines.append("- None identified")
        report_lines.append("")

        # Root Cause
        report_lines.extend([
            f"## Root Cause Analysis",
            f"{data.get('root_cause', 'Root cause could not be determined conclusively.')}",
            f"",
            f"## Recommended Actions",
        ])

        rec = data.get("recommended_actions", {})
        
        # Containment
        report_lines.append("### Containment")
        containment = rec.get("containment", [])
        if containment:
            for item in containment:
                report_lines.append(f"- {item}")
        else:
            report_lines.append("- No immediate containment actions recommended.")
        report_lines.append("")

        # Eradication
        report_lines.append("### Eradication")
        eradication = rec.get("eradication", [])
        if eradication:
            for item in eradication:
                report_lines.append(f"- {item}")
        else:
            report_lines.append("- No eradication actions recommended.")
        report_lines.append("")

        # Recovery
        report_lines.append("### Recovery")
        recovery = rec.get("recovery", [])
        if recovery:
            for item in recovery:
                report_lines.append(f"- {item}")
        else:
            report_lines.append("- No recovery actions recommended.")
        report_lines.append("")

        # Prevention
        report_lines.append("### Prevention")
        prevention = rec.get("prevention", [])
        if prevention:
            for item in prevention:
                report_lines.append(f"- {item}")
        else:
            report_lines.append("- No prevention actions recommended.")
        report_lines.append("")

        # Executive Summary
        report_lines.extend([
            f"## Executive Summary",
            f"{data.get('executive_summary', 'No summary provided.')}",
            f""
        ])

        # Investigation Evidence
        report_lines.append("## Investigation Evidence")
        if history:
            for idx, item in enumerate(history):
                report_lines.extend([
                    f"### Evidence {idx + 1}",
                    f"- **SPL Query**: `{item.get('spl')}`",
                    f"- **Result Count**: {item.get('result_count', 0)}",
                    f"- **Agent Reasoning**: {item.get('reasoning', '')}",
                    f"- **Sample Results**: {json.dumps(item.get('sample_results', [])[:3])}",
                    f""
                ])
        else:
            report_lines.append("No evidence collected.")

        return "\n".join(report_lines)

    def _generate_fallback_report(
        self,
        task_description: str,
        history: List[Dict[str, Any]],
        threat_classification: Optional[dict] = None
    ) -> str:
        """Deterministic fallback report generation if Claude is unavailable."""
        # Simple extraction logic from history for users/hosts if possible
        users = set()
        hosts = set()
        
        for h in history:
            for row in h.get("sample_results", []):
                if isinstance(row, dict):
                    if "user" in row:
                        users.add(str(row["user"]))
                    if "src_ip" in row:
                        hosts.add(str(row["src_ip"]))
                    if "host" in row:
                        hosts.add(str(row["host"]))

        incident_type = "Suspicious Activity"
        severity = "High" if len(history) > 1 else "Medium"
        confidence = 0.85
        mitre = []
        if threat_classification:
            incident_type = threat_classification.get("threat_type", incident_type)
            severity = threat_classification.get("severity", severity)
            confidence = threat_classification.get("confidence", confidence)
            mitre = threat_classification.get("mitre", mitre)

        fallback_data = {
            "incident_type": incident_type,
            "severity": severity,
            "confidence": confidence,
            "mitre": mitre,
            "affected_hosts": list(hosts) if hosts else ["unknown_host"],
            "affected_users": list(users) if users else ["unknown_user"],
            "root_cause": f"Iterative Splunk queries were conducted to investigate: '{task_description}'. Root cause needs manual review of the timeline.",
            "recommended_actions": {
                "containment": ["Isolate affected hosts from the network if active breach suspected."],
                "eradication": ["Inspect security audit logs and terminate unauthorized sessions."],
                "recovery": ["Reset credentials for affected users."],
                "prevention": ["Review authentication policies and configure alert rules."]
            },
            "executive_summary": f"Automated root-cause analysis ran {len(history)} queries to investigate '{task_description}'. Please see timeline below.",
        }
        return self._build_markdown_report(fallback_data, history)
