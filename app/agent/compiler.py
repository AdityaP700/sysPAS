from typing import List
from app.domain.enums import StepType
from app.domain.models import Runbook, CompilationResult, AgentSkill
from app.agent.governance import GovernancePolicy, ExecutionMode
from app.planner.planner import ExecutionPlanner


class AgentSkillCompiler:
    """
    Compiles a structural CompilationResult and its source Runbook
    into a deployable AgentSkill representation by leveraging the ExecutionPlanner
    and inferring governance policies.
    """

    def compile_skill(
        self,
        runbook: Runbook,
        compilation_result: CompilationResult,
        compiler_version: str = "1.0.0"
    ) -> AgentSkill:
        """
        Builds the AgentSkill model:
        1. Delegates execution graph generation and validation to ExecutionPlanner.
        2. Infers overall GovernancePolicy from step actions.
        """
        planner = ExecutionPlanner()
        graph = planner.generate_graph(runbook, compilation_result.steps)
        governance = self._infer_governance_policy(runbook)

        return AgentSkill(
            name=f"{runbook.name} Skill",
            source_runbook=f"{runbook.name.lower().replace(' ', '_')}_sop.md",
            compiler_version=compiler_version,
            graph=graph,
            governance=governance,
            steps=compilation_result.steps
        )

    def _infer_governance_policy(self, runbook: Runbook) -> GovernancePolicy:
        """
        Scans all runbook steps to determine the execution mode and approval roles.
        Hierarchy of restrictiveness: MANUAL > HUMAN_IN_LOOP > AUTO
        """
        step_modes: List[ExecutionMode] = []

        for step in runbook.steps:
            step_desc_lower = step.description.lower()
            action_lower = step.action.lower() if step.action else ""

            # Check if explicit approval keywords exist
            is_explicit_approval = any(
                kw in step_desc_lower or kw in action_lower
                for kw in ["approve", "approval", "gate", "confirm", "signoff", "authorize", "authorization", "verify", "verification"]
            )

            if (step.step_type == StepType.MANUAL or "manual" in action_lower) and is_explicit_approval:
                step_modes.append(ExecutionMode.MANUAL)
            elif "block" in action_lower or "deny" in action_lower or "escalate" in action_lower:
                step_modes.append(ExecutionMode.HUMAN_IN_LOOP)
            elif "jira" in action_lower or "ticket" in action_lower or "notify" in action_lower or "email" in action_lower:
                step_modes.append(ExecutionMode.AUTO)
            elif step.step_type in (StepType.DETECTION, StepType.INVESTIGATION, StepType.CORRELATION):
                step_modes.append(ExecutionMode.AUTO)
            else:
                # Default safety gate for other steps
                step_modes.append(ExecutionMode.AUTO)

        # Determine overall mode based on hierarchy
        if ExecutionMode.MANUAL in step_modes:
            overall_mode = ExecutionMode.MANUAL
            approval_required = True
            approval_role = "operator"
        elif ExecutionMode.HUMAN_IN_LOOP in step_modes:
            overall_mode = ExecutionMode.HUMAN_IN_LOOP
            approval_required = True
            approval_role = "soc_analyst"
        else:
            overall_mode = ExecutionMode.AUTO
            approval_required = False
            approval_role = None

        return GovernancePolicy(
            approval_required=approval_required,
            approval_role=approval_role,
            audit_enabled=True,
            execution_mode=overall_mode
        )
