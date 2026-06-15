import re
from typing import List
from app.domain.models import Runbook, RunbookStep
from app.parser.markdown_parser import MarkdownParser
from app.core.exceptions import ParsingError


class TextParser:
    """Parses plain text runbooks into a structured Runbook domain model."""

    @staticmethod
    def parse(content: str) -> Runbook:
        """
        Parses unstructured/plain text to extract title, description, and steps.
        
        Raises ParsingError if no title or valid steps are found.
        """
        if not content or not content.strip():
            raise ParsingError("Empty runbook content")

        lines = [line.strip() for line in content.splitlines() if line.strip()]

        if not lines:
            raise ParsingError("No content to parse")

        # The first line is treated as the Title
        title = lines[0]
        if title.endswith(":"):
            title = title[:-1].strip()

        description_lines = []
        step_lines = []

        # Identify lists and steps in the remaining lines
        for line in lines[1:]:
            # Match numbered lines, bulleted lines, or step-prefixed lines
            match_ordered = re.match(r'^(\d+)[\.\)]\s+(.*)', line)
            match_unordered = re.match(r'^[\-\*•]\s+(.*)', line)
            match_step_word = re.match(r'^step\s+(\d+)[:\.]?\s*(.*)', line, re.IGNORECASE)

            if match_ordered or match_unordered or match_step_word:
                step_lines.append(line)
            else:
                # Accumulate as description if steps haven't started yet
                if not step_lines:
                    description_lines.append(line)

        description = " ".join(description_lines).strip() if description_lines else None

        steps: List[RunbookStep] = []
        for idx, line in enumerate(step_lines):
            match_ordered = re.match(r'^(\d+)[\.\)]\s+(.*)', line)
            match_unordered = re.match(r'^[\-\*•]\s+(.*)', line)
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

            # Reuse the robust parse_step method from MarkdownParser
            steps.append(MarkdownParser.parse_step(step_id, body))

        if not steps:
            # If no structured steps found, try to treat every line after title as a step
            if len(lines) > 1:
                for idx, line in enumerate(lines[1:]):
                    steps.append(MarkdownParser.parse_step(str(idx + 1), line))
            else:
                raise ParsingError("No runbook steps found")

        return Runbook(
            name=title,
            description=description,
            steps=steps
        )
