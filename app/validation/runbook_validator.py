from typing import Set
from app.domain.models import Runbook, ValidationResult
from app.domain.enums import StepType, ActionType
from app.parser.normalizer import infer_action_type


class RunbookValidator:
    """Validates Runbook domain models for structural and semantic correctness."""

    @staticmethod
    def validate(runbook: Runbook) -> ValidationResult:
        """
        Validates the runbook against various validation rules:
        - No empty steps
        - Unique step IDs
        - Valid confidence values (between 0.0 and 1.0)
        - Valid action definitions
        """
        errors = []

        # 1. Check if runbook has no steps
        if not runbook.steps:
            errors.append("Runbook must contain at least one step.")
        
        seen_ids: Set[str] = set()

        for idx, step in enumerate(runbook.steps):
            step_ref = step.step_id or f"index {idx}"

            # 2. Check for empty step description
            if not step.description or not step.description.strip():
                errors.append(f"Step '{step_ref}' has an empty description.")

            # 3. Check for unique step IDs
            if step.step_id in seen_ids:
                errors.append(f"Duplicate step ID found: '{step.step_id}'.")
            seen_ids.add(step.step_id)

            # 4. Validate confidence range
            if not (0.0 <= step.confidence <= 1.0):
                errors.append(
                    f"Step '{step_ref}' has an invalid confidence value of {step.confidence}. "
                    f"Confidence must be between 0.0 and 1.0."
                )

            # 5. Validate action definitions
            if step.step_type == StepType.ACTION:
                if not step.action or not step.action.strip():
                    errors.append(f"Step '{step_ref}' is marked as ACTION but lacks an action definition.")
            
            # If an action is specified, verify it can be parsed to a valid ActionType
            if step.action:
                action_type = infer_action_type(step.action)
                if not action_type:
                    errors.append(f"Step '{step_ref}' has an unresolvable action definition: '{step.action}'.")

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors
        )
