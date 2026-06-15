from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

import anthropic

from app.config.settings import settings
from app.splunk.adapters.guardrails import validate_schema_preferences, validate_spl_fields

logger = logging.getLogger(__name__)


class InvestigationStepResult(BaseModel):
    investigation_complete: bool = Field(
        ...,
        description="True if no more queries are needed and we have identified root cause or done enough investigation"
    )
    next_query: Optional[str] = Field(
        default=None,
        description="The next Splunk SPL query to run. Optional if investigation_complete is True"
    )
    reasoning: str = Field(
        ...,
        description="Reasoning for either stopping or generating the next query"
    )


class InvestigationAgent:
    """
    Closed-loop investigation agent that uses the Claude provider to analyze Splunk query results,
    perform iterative root-cause analysis, and determine subsequent investigative actions.
    """

    def __init__(self) -> None:
        self._client: Optional[anthropic.Anthropic] = None

    def _ensure_init(self) -> bool:
        """Initialise the Anthropic client using the configured key. Returns True if successful."""
        if self._client is not None:
            return True

        api_key = settings.claude_api_key
        if not api_key:
            # Check environment variables directly just in case settings did not populate it
            import os
            api_key = os.environ.get("RUNBOOKMIND_CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")

        if not api_key:
            logger.warning("Claude API key not found. InvestigationAgent will operate in pass-through mock/fallback mode.")
            return False

        try:
            self._client = anthropic.Anthropic(api_key=api_key)
            return True
        except Exception as e:
            logger.error(f"Failed to initialise Anthropic client: {e}")
            return False

    def analyze_and_next_step(
        self,
        current_query: str,
        current_results: List[Dict[str, Any]],
        history: List[Dict[str, Any]],
        task_description: str,
        schema_fields: Optional[List[str]] = None
    ) -> InvestigationStepResult:
        """
        Analyze current results and past query history against the target task.
        Returns the next investigation step.
        """
        if not self._ensure_init():
            return self._fallback_pivot(
                current_query=current_query,
                current_results=current_results,
                history=history,
                task_description=task_description,
                schema_fields=schema_fields
            )

        # Prepare execution history for prompt
        history_summary = []
        for i, h in enumerate(history):
            history_summary.append(
                f"Step {i+1}:\n"
                f"- SPL Query: {h.get('spl')}\n"
                f"- Result Count: {h.get('result_count')}\n"
                f"- Agent Reasoning: {h.get('reasoning')}\n"
                f"- Sample Results: {json.dumps(h.get('sample_results', [])[:5])}\n"
            )
        history_str = "\n".join(history_summary) if history_summary else "No previous history."

        fields_str = ", ".join(schema_fields) if schema_fields else "status, user, src_ip, host"

        system_prompt = (
            "You are an expert Splunk security and operations investigation agent.\n"
            "Your job is to perform root-cause analysis by iteratively exploring Splunk query results.\n"
            "Review the current query, its results, the execution history of past queries, and the target investigation task.\n"
            "Decide if you have found the root cause or if further investigation is needed.\n\n"
            "DETAILED PIVOTING GUIDELINES:\n"
            "Do not stop after a single broad query. Follow a systematic threat hunting/pivoting path:\n"
            "1. Find/filter for the suspicious process or artifact (e.g. process=\"mimikatz.exe\").\n"
            "2. Pivot to find affected hosts (e.g. process=\"mimikatz.exe\" | stats count by host).\n"
            "3. Pivot to find affected users (e.g. process=\"mimikatz.exe\" | stats count by user).\n"
            "4. Pivot to inspect execution timeline or parent processes (e.g. process=\"mimikatz.exe\" | stats count by parent_process).\n"
            "5. Mark investigation complete once all these pivots are done.\n\n"
            "CRITICAL SPL GENERATION RULES:\n"
            "1. Prefer schema fields over semantic keywords when generating filters.\n"
            "2. If the 'status' field exists in the available fields list, you MUST use 'status=failed' instead of semantic keywords like 'failed', 'failure', or 'error' (either as bare keywords or as other field values/assignments).\n"
            "3. Prefer using the schema fields 'status', 'user', 'src_ip', and 'host' over generic or semantic terms.\n\n"
            "You MUST respond ONLY with a valid JSON object. Do not include any explanation outside the JSON object.\n"
            "The JSON object must have exactly the following keys:\n"
            "{\n"
            '  "investigation_complete": <true or false>,\n'
            '  "next_query": "<the next SPL query to run, or null/empty if complete>",\n'
            '  "reasoning": "<explanation of findings or what you are looking for next>"\n'
            "}"
        )

        user_prompt = (
            f"Target Investigation Task: {task_description}\n\n"
            f"Available Fields in Schema: {fields_str}\n\n"
            f"Current Query: {current_query}\n"
            f"Current Result Count: {len(current_results)}\n"
            f"Current Sample Results (up to 10): {json.dumps(current_results[:10])}\n\n"
            f"Past Query Execution History:\n{history_str}\n"
        )

        try:
            # 1. First attempt to call Claude
            result_json = self._call_claude(system_prompt, user_prompt)
            investigation_complete = result_json.get("investigation_complete", False)
            next_query = result_json.get("next_query")
            reasoning = result_json.get("reasoning", "")

            # 2. Schema guardrail validation & self-corrective re-prompting
            if not investigation_complete and next_query and schema_fields:
                hallucinations = validate_spl_fields(next_query, schema_fields)
                violations = validate_schema_preferences(next_query, schema_fields)
                
                if hallucinations or violations:
                    feedback_parts = []
                    if hallucinations:
                        feedback_parts.append(
                            f"CRITICAL: The previously generated SPL query referenced invalid fields not present in the allowed schema: {list(hallucinations)}. "
                            f"Do NOT use those fields. Generate the SPL using ONLY these allowed fields: {schema_fields}."
                        )
                    if violations:
                        feedback_parts.append(
                            "CRITICAL: Prefer schema fields over semantic keywords. If the 'status' field exists in the available fields list, "
                            "you MUST use 'status=failed' instead of semantic keywords like 'failed', 'failure', or 'error' (either as bare keywords "
                            "or as other field values/assignments)."
                        )

                    logger.warning(
                        "InvestigationAgent SPL guardrail validation failed. Attempting self-corrective LLM re-prompt..."
                    )
                    user_prompt_retry = (
                        f"{user_prompt}\n\n"
                        f"Your previous next_query '{next_query}' was invalid.\n"
                        f"Feedback: {' '.join(feedback_parts)}\n"
                        f"Please generate a corrected next_query respecting the schema rules."
                    )

                    result_json_retry = self._call_claude(system_prompt, user_prompt_retry)
                    investigation_complete = result_json_retry.get("investigation_complete", False)
                    next_query = result_json_retry.get("next_query")
                    reasoning = result_json_retry.get("reasoning", "")

            return InvestigationStepResult(
                investigation_complete=investigation_complete,
                next_query=next_query,
                reasoning=reasoning
            )

        except Exception as e:
            logger.error(f"Error during InvestigationAgent step: {e}")
            return self._fallback_pivot(
                current_query=current_query,
                current_results=current_results,
                history=history,
                task_description=task_description,
                schema_fields=schema_fields
            )

    def _fallback_pivot(
        self,
        current_query: str,
        current_results: List[Dict[str, Any]],
        history: List[Dict[str, Any]],
        task_description: str,
        schema_fields: Optional[List[str]] = None
    ) -> InvestigationStepResult:
        """Rule-based deterministic pivot planner for autonomous flow when Claude is unconfigured."""
        task_lower = task_description.lower()
        query_lower = current_query.lower()

        # Check if the task description has any threat indicators for pivoting
        threat_indicators = [
            "brute force", "credential", "dump", "mimikatz", "powershell",
            "enc", "account", "net user", "persistence", "registry"
        ]
        if not any(ti in task_lower for ti in threat_indicators) and not any(ti in query_lower for ti in threat_indicators):
            return InvestigationStepResult(
                investigation_complete=True,
                next_query=None,
                reasoning="Claude API key is missing. Investigation cannot proceed."
            )

        # Extract whatever index/sourcetype is used
        index = "main"
        if "index=" in query_lower:
            match = re.search(r'index=([^\s|]+)', current_query)
            if match:
                index = match.group(1)

        # Detect the process or primary filter target
        suspicious_proc = None
        all_process_names = ["mimikatz.exe", "mimikatz", "powershell.exe", "powershell", "net.exe", "net", "reg.exe", "cmd.exe"]
        for proc in all_process_names:
            if proc in query_lower:
                suspicious_proc = proc
                break

        if not suspicious_proc:
            for row in current_results:
                proc_val = row.get("process") or row.get("process_name") or row.get("parent_process")
                if proc_val:
                    for p in all_process_names:
                        if p in str(proc_val).lower():
                            suspicious_proc = p
                            break
                if suspicious_proc:
                    break

        if not suspicious_proc:
            if "credential" in task_lower or "dump" in task_lower or "mimikatz" in task_lower:
                suspicious_proc = "mimikatz.exe"
            elif "powershell" in task_lower or "enc" in task_lower:
                suspicious_proc = "powershell.exe"
            elif "account" in task_lower or "net user" in task_lower:
                suspicious_proc = "net.exe"
            elif "persistence" in task_lower or "registry" in task_lower:
                suspicious_proc = "reg.exe"
            else:
                suspicious_proc = "mimikatz.exe"

        # Determine the next pivot step based on the current query pattern
        if "|" not in current_query or "stats" not in query_lower:
            next_spl = f"index={index} process=\"{suspicious_proc}\" | stats count by host"
            return InvestigationStepResult(
                investigation_complete=False,
                next_query=next_spl,
                reasoning=f"Found suspicious process '{suspicious_proc}' in initial logs. Pivoting to identify affected hosts."
            )
        elif "by host" in query_lower:
            next_spl = f"index={index} process=\"{suspicious_proc}\" | stats count by user"
            return InvestigationStepResult(
                investigation_complete=False,
                next_query=next_spl,
                reasoning=f"Identified affected hosts for '{suspicious_proc}'. Pivoting to identify exposed user accounts."
            )
        elif "by user" in query_lower:
            next_spl = f"index={index} process=\"{suspicious_proc}\" | stats count by parent_process"
            return InvestigationStepResult(
                investigation_complete=False,
                next_query=next_spl,
                reasoning=f"Identified exposed user accounts. Pivoting to analyze parent process tree."
            )
        else:
            return InvestigationStepResult(
                investigation_complete=True,
                next_query=None,
                reasoning=f"Autonomous investigation complete. Traced process '{suspicious_proc}' across hosts, users, and parent processes."
            )

    def _call_claude(self, system_prompt: str, user_prompt: str) -> dict:
        """Helper to invoke Claude API and parse JSON response."""
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

        # Extract and parse JSON
        cleaned = re.sub(r'^```(?:json)?\n?', '', raw_text.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r'\n?```$', '', cleaned)
        start = cleaned.find('{')
        end = cleaned.rfind('}')
        if start != -1 and end != -1:
            cleaned = cleaned[start:end+1]
        
        return json.loads(cleaned)
