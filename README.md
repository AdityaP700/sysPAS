# RunbookMind

RunbookMind is an autonomous agent compiler that translates standard operational procedures (SOPs) and runbooks into structured, executable agent workflows.

This repository contains the Phase 1 implementation focusing on the **Foundation and Domain Layer**.

---

## Architecture Overview

RunbookMind decomposes unstructured operational procedures into a structured domain model, allowing Splunk/Cisco agent runtimes to interpret, compile, and execute steps autonomously with appropriate governance gates.

```
                  ┌───────────────────────┐
                  │ Runbook (MD / Text)   │
                  └───────────┬───────────┘
                              │
                              ▼
                  ┌───────────────────────┐
                  │        Parsers        │
                  │   (Markdown / Text)   │
                  └───────────┬───────────┘
                              │
                              ▼
                  ┌───────────────────────┐
                  │    Domain Models      │
                  │ (Runbook/RunbookStep) │
                  └───────────┬───────────┘
                              │
                              ▼
                  ┌───────────────────────┐
                  │   Runbook Validator   │
                  │  (Semantic Checks)    │
                  └───────────────────────┘
```

---

## Directory Structure

```
runbookmind/
│
├── app/
│   ├── core/
│   │   ├── __init__.py
│   │   └── exceptions.py          # Custom domain exceptions
│   │
│   ├── domain/
│   │   ├── __init__.py
│   │   ├── enums.py               # Operational classification enums
│   │   └── models.py              # Pydantic v2 models
│   │
│   ├── parser/
│   │   ├── __init__.py
│   │   ├── normalizer.py          # Value and temporal string normalizer
│   │   ├── markdown_parser.py     # Heading and list parser
│   │   └── text_parser.py         # Plain text line parser
│   │
│   └── validation/
│       ├── __init__.py
│       └── runbook_validator.py   # Semantic and structural check engine
│
├── tests/
│   ├── test_models.py             # Serialization & validation tests
│   ├── test_parser.py             # Parser extraction and normalization tests
│   └── test_validator.py          # Validation engine success/failure tests
│
├── pyproject.toml                 # Packaging configuration
└── README.md                      # Documentation
```

---

## Domain Layer Specification

### Core Enums
- `StepType`: `DETECTION`, `CORRELATION`, `ESCALATION`, `ACTION`, `MANUAL`, `INVESTIGATION`.
- `ActionType`: `HUMAN_ESCALATION`, `BLOCK_IP`, `CREATE_JIRA`, `EMAIL_NOTIFICATION`, `MANUAL`.
- `ApprovalLevel`: `NONE`, `MEMBER`, `LEAD`, `ADMIN`.
- `CompilationStatus`: `PENDING`, `SUCCESS`, `FAILED`, `PARTIAL`.

### Core Models
- `Runbook`: Holds the title, description, and list of `RunbookStep` definitions.
- `RunbookStep`: Represents a parsed operation containing extracted filters, condition thresholds, data source scope, action mappings, and confidence scores.
- `CompiledStep`: Represents steps that have been compiled into execution-ready queries.
- `CompilationResult`: Summarizes compilation status for a runbook.
- `AgentSkill`: Target JSON object for Cisco AI Toolkit Agent Builder deployment.

---

## Quick Start & Usage

### Installation

To install dependencies locally:

```bash
pip install -e .[dev]
```

### Decomposing and Validating a Runbook

You can parse a Markdown runbook and validate it programmatically using the snippet below:

```python
from app.parser.markdown_parser import MarkdownParser
from app.validation.runbook_validator import RunbookValidator

# 1. Define runbook content
runbook_md = """# Failed Login Investigation
Investigates brute-force authentication attempts.

1. Check auth logs for spikes > 100 failures in 5 min
2. If external, block IP and create JIRA ticket
"""

# 2. Parse into domain model
runbook = MarkdownParser.parse(runbook_md)
print(f"Parsed Runbook: {runbook.name}")
print(f"Steps Count: {len(runbook.steps)}")

# 3. Validate structures
validation_result = RunbookValidator.validate(runbook)
if validation_result.is_valid:
    print("Runbook is valid!")
    print(runbook.model_dump_json(indent=2))
else:
    print("Validation failed:")
    for error in validation_result.errors:
        print(f" - {error}")
```

### Running Tests

To run the unit tests suite:

```bash
pytest tests/
```
4b01c492-62a6-493d-9dd1-67cbac37ff80