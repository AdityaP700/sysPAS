from typing import List, Optional
from pydantic import BaseModel, Field
from app.planner.conditions import BranchCondition


class ExecutionNode(BaseModel):
    """Represents an active step node inside the agent execution workflow."""
    node_id: str = Field(..., description="Unique node identifier in the execution graph")
    step_id: str = Field(..., description="Reference to the original runbook step_id")
    step_name: str = Field(..., description="Friendly name or description summary of the step")
    action_type: Optional[str] = Field(default=None, description="Inferred operational action type")
    compiled_spl: Optional[str] = Field(default=None, description="Compiled executable SPL query, if any")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Step confidence level")


class ExecutionEdge(BaseModel):
    """Represents control flow or routing path between two execution nodes."""
    source: str = Field(..., description="Source node ID")
    target: str = Field(..., description="Target node ID")
    condition: Optional[str] = Field(default=None, description="Branching trigger condition, if applicable")
    branch_condition: Optional[BranchCondition] = Field(default=None, description="Structured branching condition")


class ExecutionGraph(BaseModel):
    """Graph structure containing nodes, edges, and initial entry points for agent execution."""
    nodes: List[ExecutionNode] = Field(default_factory=list, description="All nodes representing steps")
    edges: List[ExecutionEdge] = Field(default_factory=list, description="All execution paths/edges linking steps")
    entry_node: Optional[str] = Field(default=None, description="Starting node ID of the workflow")
