import re
from typing import List, Optional
from app.domain.enums import StepType, ActionType
from app.domain.models import Runbook, RunbookStep
from app.parser.normalizer import normalize_time_window, infer_step_type, infer_action_type
from app.core.exceptions import ParsingError


class MarkdownParser:
    """Parses markdown runbooks into a structured Runbook domain model."""

    @staticmethod
    def parse(content: str) -> Runbook:
        """
        Parses Markdown formatted text to extract title, description, and steps.
        
        Raises ParsingError if no title or valid steps are found.
        """
        if not content or not content.strip():
            raise ParsingError("Empty runbook content")

        lines = [line.strip() for line in content.splitlines()]

        # Extract title and description
        title = None
        description_lines = []
        step_lines = []
        title_idx = -1

        for i, line in enumerate(lines):
            if not line:
                continue
            if line.startswith("#"):
                title = line.lstrip("#").strip()
                title_idx = i
                break
            elif line.endswith(":"):
                title = line.rstrip(":").strip()
                title_idx = i
                break
            else:
                title = line.strip()
                title_idx = i
                break

        if not title:
            raise ParsingError("Could not parse runbook title")

        # Parse everything after the title
        for line in lines[title_idx + 1:]:
            if not line:
                continue

            # Identify if this line is a step
            match_ordered = re.match(r'^(\d+)[\.\)]\s+(.*)', line)
            match_unordered = re.match(r'^[\-\*]\s+(.*)', line)
            match_step_word = re.match(r'^step\s+(\d+)[:\.]?\s*(.*)', line, re.IGNORECASE)

            if match_ordered or match_unordered or match_step_word:
                step_lines.append(line)
            else:
                # If we haven't hit steps yet, collect as description
                if not step_lines:
                    description_lines.append(line)

        description = " ".join(description_lines).strip() if description_lines else None

        steps: List[RunbookStep] = []
        for idx, line in enumerate(step_lines):
            match_ordered = re.match(r'^(\d+)[\.\)]\s+(.*)', line)
            match_unordered = re.match(r'^[\-\*]\s+(.*)', line)
            match_step_word = re.match(r'^step\s+(\d+)[:\.]?\s*(.*)', line, re.IGNORECASE)

            if match_ordered:
                step_id = match_ordered.group(1)
                body = match_ordered.group(2)
            elif match_step_word:
                step_id = match_step_word.group(1)
                body = match_step_word.group(2)
            else:
                step_id = str(idx + 1)
                body = match_unordered.group(1) if match_unordered else line

            steps.append(MarkdownParser.parse_step(step_id, body))

        if not steps:
            raise ParsingError("No runbook steps found")

        return Runbook(
            name=title,
            description=description,
            steps=steps
        )

    @staticmethod
    def parse_step(step_id: str, body: str) -> RunbookStep:
        """
        Parses a single step string to extract data_source, condition, threshold,
        time_window, action details, join requirements, and confidence.
        """
        body_clean = body.strip()

        # 1. Infer Step Type
        step_type = infer_step_type(body_clean)

        # 2. Extract Data Source
        data_source = None
        ds_match = re.search(r'index\s*=\s*([a-zA-Z0-9_\-*]+)', body_clean, re.IGNORECASE)
        if ds_match:
            data_source = ds_match.group(1)
        else:
            # Look for patterns like "auth logs", "threat intel", "web logs", etc.
            # Avoid matching common action verbs as part of the logs data source
            ds_match = re.search(r'\b(?!check|search|query|find|get|look|filter|in|for|with|on|at|from\b)(\w+(?:\s+\w+)?)\s+logs', body_clean, re.IGNORECASE)
            if ds_match:
                data_source = ds_match.group(1).lower().replace(" ", "_") + "_logs"
            else:
                # Other common patterns
                for source in ["threat intel", "threat_intel", "sales metrics", "sales_metrics", "snowflake"]:
                    if source in body_clean.lower():
                        data_source = source.replace(" ", "_")
                        break

        # 3. Extract Condition
        condition = None
        # Check "if ..."
        if_match = re.search(r'\bif\s+([^,.]+)', body_clean, re.IGNORECASE)
        if if_match:
            condition = if_match.group(1).strip()
        else:
            # Check threshold conditions like "spikes > 100" or "failures > 100"
            cond_match = re.search(r'(\w+\s*(?:>|<|>=|<=|==|=)\s*\d+)', body_clean, re.IGNORECASE)
            if cond_match:
                condition = cond_match.group(1).strip()

        # 4. Extract Threshold
        threshold = None
        thresh_match = re.search(r'(?:>|<|>=|<=|==|=)\s*(\d+(?:\s*\w+)?)', body_clean)
        if thresh_match:
            threshold = thresh_match.group(1).strip()
        else:
            thresh_match = re.search(
                r'(?:spikes|failures|limit|threshold|above|below|exceeds?)\s*(?:of|than|over|above)?\s*(\d+)',
                body_clean, re.IGNORECASE
            )
            if thresh_match:
                threshold = thresh_match.group(1).strip()

        # 5. Extract Time Window
        time_window = None
        tw_match = re.search(
            r'\b(?:in|over|last|within|time window of)\s+(\d+\s*(?:min|minute|sec|second|hour|hr|day|d|m|s|h)s?)',
            body_clean, re.IGNORECASE
        )
        if tw_match:
            time_window = normalize_time_window(tw_match.group(1))

        # 6. Extract Action
        action = None
        action_match = re.search(r'\b(escalate\s+to\s+[^,.]+)', body_clean, re.IGNORECASE)
        if action_match:
            action = action_match.group(1).strip()
        else:
            action_match = re.search(r'\b(block\s+[^,.]+)', body_clean, re.IGNORECASE)
            if action_match:
                action = action_match.group(1).strip()
            else:
                action_match = re.search(r'\b(create\s+(?:[a-zA-Z0-9_\-\s]+)?ticket[^,.]+)', body_clean, re.IGNORECASE)
                if action_match:
                    action = action_match.group(1).strip()
                elif "if" in body_clean.lower() and "," in body_clean:
                    parts = body_clean.split(",", 1)
                    action = parts[1].strip()

        # 7. Join & Snowflake
        join_required = False
        if any(kw in body_clean.lower() for kw in ["correlate", "join", "lookup"]):
            join_required = True

        snowflake_table = None
        if "snowflake" in body_clean.lower():
            join_required = True
            sf_match = re.search(r'(\w+)\s+table', body_clean, re.IGNORECASE)
            if sf_match:
                snowflake_table = sf_match.group(1).strip()
            else:
                # look for snake_case words indicating table
                for word in body_clean.split():
                    if "_" in word and word.islower():
                        snowflake_table = word.strip(",.*")
                        break

        # 8. Human in the Loop Gate
        gate = None
        if any(kw in body_clean.lower() for kw in ["human", "verify", "approval", "gate", "escalate to"]):
            gate = "human_in_loop"

        # 9. Compute Confidence Score
        score = 0.7
        if data_source:
            score += 0.05
        if condition:
            score += 0.05
        if threshold:
            score += 0.05
        if time_window:
            score += 0.05
        if action:
            score += 0.05
        confidence = min(round(score, 2), 1.0)

        return RunbookStep(
            step_id=step_id,
            description=body_clean,
            step_type=step_type,
            action=action,
            data_source=data_source,
            condition=condition,
            threshold=threshold,
            time_window=time_window,
            join_required=join_required,
            snowflake_table=snowflake_table,
            gate=gate,
            confidence=confidence
        )
