from app.agent.governance import GovernancePolicy, ExecutionMode


class GovernanceRuntime:
    """Enforces policy controls and authorization gates before executing node instructions."""

    @staticmethod
    def evaluate_gate(policy: GovernancePolicy, action_type: str) -> str:
        """
        Determines the execution path for a step based on the skill's GovernancePolicy.
        Returns one of: 'CONTINUE', 'PAUSE_APPROVAL', 'STOP_MANUAL'.
        """
        if policy.execution_mode == ExecutionMode.AUTO:
            return "CONTINUE"

        if policy.execution_mode == ExecutionMode.HUMAN_IN_LOOP:
            return "PAUSE_APPROVAL"

        if policy.execution_mode == ExecutionMode.MANUAL:
            return "STOP_MANUAL"

        return "CONTINUE"
