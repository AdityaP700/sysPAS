from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

import anthropic

from app.config.settings import settings
from app.agent.mitre_mapper import map_threat_to_mitre

logger = logging.getLogger(__name__)


class ThreatClassificationResult(BaseModel):
    threat_type: str = Field(..., description="The classified threat type (e.g. Brute Force, PowerShell Encoded, Mimikatz, etc.)")
    severity: str = Field(..., description="HIGH, MEDIUM, or LOW")
    confidence: float = Field(..., description="Classification confidence score between 0.0 and 1.0")
    mitre: List[str] = Field(default_factory=list, description="MITRE ATT&CK technique IDs mapped to this threat")
    risk_score: int = Field(..., description="Risk score between 0 and 100")


class ThreatClassifier:
    """
    Threat classification component that analyzes query results and investigation history
    to categorize the incident, assign severity/confidence, and map to MITRE ATT&CK.
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
            return False

        try:
            self._client = anthropic.Anthropic(api_key=api_key)
            return True
        except Exception as e:
            logger.error(f"Failed to initialise Anthropic client: {e}")
            return False

    def classify_threat(
        self,
        query_results: List[Dict[str, Any]],
        investigation_history: List[Dict[str, Any]]
    ) -> ThreatClassificationResult:
        """
        Classify the security threat based on query results and investigation history.
        """
        if not self._ensure_init():
            return self._classify_fallback(query_results, investigation_history)

        # Prepare context for Claude
        history_summary = []
        for i, h in enumerate(investigation_history):
            history_summary.append(
                f"Step {i+1}:\n"
                f"- SPL Query: {h.get('spl')}\n"
                f"- Result Count: {h.get('result_count')}\n"
                f"- Agent Reasoning: {h.get('reasoning')}\n"
                f"- Sample Results (up to 5): {json.dumps(h.get('sample_results', [])[:5])}\n"
            )
        history_str = "\n".join(history_summary) if history_summary else "No history."

        system_prompt = (
            "You are an expert Threat Intelligence Analyst.\n"
            "Analyze the query results and the investigation history to classify the threat.\n"
            "You MUST respond ONLY with a valid JSON object. Do not include any explanation outside the JSON object.\n"
            "The JSON object must have exactly the following structure:\n"
            "{\n"
            '  "threat_type": "<e.g., Brute Force, PowerShell Encoded, Mimikatz, Persistence Registry, etc.>",\n'
            '  "severity": "<HIGH, MEDIUM, or LOW>",\n'
            '  "confidence": <float between 0.0 and 1.0>,\n'
            '  "risk_score": <integer between 0 and 100>\n'
            "}"
        )

        user_prompt = (
            f"Current Query Results (up to 10): {json.dumps(query_results[:10])}\n\n"
            f"Investigation History:\n{history_str}\n"
        )

        try:
            message = self._client.messages.create(
                model=settings.claude_model,
                max_tokens=settings.claude_max_tokens,
                temperature=0.0,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_prompt}
                ]
            )
            raw_text = message.content[0].text.strip()

            cleaned = re.sub(r'^```(?:json)?\n?', '', raw_text.strip(), flags=re.IGNORECASE)
            cleaned = re.sub(r'\n?```$', '', cleaned)
            start = cleaned.find('{')
            end = cleaned.rfind('}')
            if start != -1 and end != -1:
                cleaned = cleaned[start:end+1]

            data = json.loads(cleaned)
            threat_type = data.get("threat_type", "Unknown")
            severity = data.get("severity", "MEDIUM").upper()
            confidence = float(data.get("confidence", 0.8))
            
            # Map threat type to MITRE ATT&CK techniques
            mitre_ids = map_threat_to_mitre(threat_type)

            # Extract risk_score or compute dynamically
            risk_score = data.get("risk_score")
            if risk_score is None:
                base_risk = 85 if severity == "HIGH" else (50 if severity == "MEDIUM" else 20)
                risk_score = int(base_risk + (confidence * 15))
            risk_score = min(max(int(risk_score), 0), 100)

            return ThreatClassificationResult(
                threat_type=threat_type,
                severity=severity,
                confidence=confidence,
                mitre=mitre_ids,
                risk_score=risk_score
            )

        except Exception as e:
            logger.error(f"Failed to classify threat via Claude: {e}")
            return self._classify_fallback(query_results, investigation_history)

    def _classify_fallback(
        self,
        query_results: List[Dict[str, Any]],
        investigation_history: List[Dict[str, Any]]
    ) -> ThreatClassificationResult:
        """Rule-based threat classifier fallback."""
        # Join query text and sample results content to scan for signatures
        combined_text = ""
        for h in investigation_history:
            combined_text += f" {h.get('spl', '')} {h.get('reasoning', '')}"
            for row in h.get("sample_results", []):
                combined_text += f" {json.dumps(row)}"
        for row in query_results:
            combined_text += f" {json.dumps(row)}"
            
        combined_text_lower = combined_text.lower()

        # Heuristic rules
        if "mimikatz" in combined_text_lower or "lsass" in combined_text_lower:
            threat_type = "Credential Dumping"
            severity = "CRITICAL"
            confidence = 0.99
            risk_score = 99
        elif "-enc" in combined_text_lower or "encodedcommand" in combined_text_lower or "obfuscated" in combined_text_lower or "-executionpolicy bypass" in combined_text_lower:
            threat_type = "Obfuscated PowerShell"
            severity = "HIGH"
            confidence = 0.95
            risk_score = 95
        elif "currentversion\\run" in combined_text_lower or "reg add" in combined_text_lower or "persistence" in combined_text_lower:
            threat_type = "Persistence"
            severity = "CRITICAL"
            confidence = 0.98
            risk_score = 98
        elif "net user" in combined_text_lower or "user add" in combined_text_lower or "/add" in combined_text_lower or "account_creation" in combined_text_lower:
            threat_type = "Account Creation"
            severity = "CRITICAL"
            confidence = 0.97
            risk_score = 97
        elif "brute" in combined_text_lower or "failed" in combined_text_lower or "failure" in combined_text_lower:
            threat_type = "Brute Force"
            severity = "HIGH"
            confidence = 0.94
            risk_score = 99
        else:
            threat_type = "Suspicious Activity"
            severity = "MEDIUM"
            confidence = 0.75
            risk_score = 60

        mitre_ids = map_threat_to_mitre(threat_type)

        return ThreatClassificationResult(
            threat_type=threat_type,
            severity=severity,
            confidence=confidence,
            mitre=mitre_ids,
            risk_score=risk_score
        )
