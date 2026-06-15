import pytest
from app.domain.enums import StepType, ActionType
from app.parser.markdown_parser import MarkdownParser
from app.parser.text_parser import TextParser
from app.parser.normalizer import normalize_time_window
from app.core.exceptions import ParsingError


def test_normalize_time_window():
    assert normalize_time_window("5 min") == "5m"
    assert normalize_time_window("15 minutes") == "15m"
    assert normalize_time_window("1 hour") == "1h"
    assert normalize_time_window("30s") == "30s"
    assert normalize_time_window("2 hrs") == "2h"
    assert normalize_time_window("1 day") == "1d"
    assert normalize_time_window(None) is None
    assert normalize_time_window("") is None


def test_markdown_parser_success():
    md_content = """# Failed Login Investigation

This runbook investigates spikes in failed login attempts.

1. Check auth logs for spikes > 100 failures in 5 min
2. Identify source IPs, correlate with threat intel
3. If source IP is internal, escalate to Tier 2
4. If external, block IP and create JIRA ticket
"""

    runbook = MarkdownParser.parse(md_content)
    
    assert runbook.name == "Failed Login Investigation"
    assert runbook.description == "This runbook investigates spikes in failed login attempts."
    assert len(runbook.steps) == 4
    
    # Check Step 1
    assert runbook.steps[0].step_id == "1"
    assert runbook.steps[0].data_source == "auth_logs"
    assert "spikes > 100" in runbook.steps[0].condition
    assert runbook.steps[0].threshold == "100 failures"
    assert runbook.steps[0].time_window == "5m"
    assert runbook.steps[0].step_type == StepType.DETECTION
    
    # Check Step 2
    assert runbook.steps[1].step_id == "2"
    assert runbook.steps[1].data_source == "threat_intel"
    assert runbook.steps[1].join_required is True
    assert runbook.steps[1].step_type == StepType.CORRELATION
    
    # Check Step 3
    assert runbook.steps[2].step_id == "3"
    assert "internal" in runbook.steps[2].condition
    assert "escalate" in runbook.steps[2].action
    assert runbook.steps[2].gate == "human_in_loop"
    assert runbook.steps[2].step_type == StepType.ESCALATION
    
    # Check Step 4
    assert runbook.steps[3].step_id == "4"
    assert "external" in runbook.steps[3].condition
    assert "block" in runbook.steps[3].action
    assert runbook.steps[3].step_type == StepType.ACTION


def test_text_parser_success():
    text_content = """Failed Login Investigation SOP
This description details the operational process.

Step 1: Check auth logs for spikes > 100 failures in 5 min
Step 2: Identify source IPs, correlate with threat intel
Step 3: If source IP is internal, escalate to Tier 2
Step 4: If external, block IP and create JIRA ticket
"""

    runbook = TextParser.parse(text_content)
    
    assert runbook.name == "Failed Login Investigation SOP"
    assert runbook.description == "This description details the operational process."
    assert len(runbook.steps) == 4
    assert runbook.steps[0].step_id == "1"
    assert runbook.steps[0].data_source == "auth_logs"
    assert runbook.steps[1].step_id == "2"
    assert runbook.steps[2].step_id == "3"
    assert runbook.steps[3].step_id == "4"


def test_parser_empty_content():
    with pytest.raises(ParsingError):
        MarkdownParser.parse("")

    with pytest.raises(ParsingError):
        TextParser.parse("   \n   ")
