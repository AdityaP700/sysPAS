import pytest
from app.domain.enums import StepType, CompilationStatus
from app.domain.models import Runbook, RunbookStep, CompiledStep
from app.planner.planner import ExecutionPlanner
from app.agent.graph import ExecutionGraph, ExecutionEdge, ExecutionNode
from app.core.exceptions import ValidationError


def test_planner_condition_parsing():
    planner = ExecutionPlanner()
    
    # Standard operators
    cond1 = planner.parse_condition("status == error")
    assert cond1.expression == "status == error"
    assert cond1.operator == "=="
    assert cond1.value == "error"
    
    cond2 = planner.parse_condition("failure_count > 100")
    assert cond2.operator == ">"
    assert cond2.value == "100"
    
    # Prefix cleanup
    cond3 = planner.parse_condition("If source_ip != internal")
    assert cond3.expression == "source_ip != internal"
    assert cond3.operator == "!="
    assert cond3.value == "internal"
    
    # Unstructured
    cond4 = planner.parse_condition("internal range IP")
    assert cond4.expression == "internal range IP"
    assert cond4.operator is None
    assert cond4.value is None


def test_planner_graph_generation():
    planner = ExecutionPlanner()
    
    # Construct a branching and merging runbook
    step1 = RunbookStep(step_id="1", description="Check auth logs", step_type=StepType.DETECTION)
    step2 = RunbookStep(step_id="2", description="If internal, escalate", condition="source_ip == internal", step_type=StepType.ESCALATION)
    step3 = RunbookStep(step_id="3", description="If external, block", condition="source_ip == external", step_type=StepType.ACTION)
    step4 = RunbookStep(step_id="4", description="Notify lead", step_type=StepType.ACTION)
    runbook = Runbook(name="Branching SOP", steps=[step1, step2, step3, step4])
    
    c_steps = [
        CompiledStep(step_id="1", description="Check auth logs", compiled_spl="index=auth", status=CompilationStatus.SUCCESS),
        CompiledStep(step_id="2", description="If internal, escalate", compiled_spl="escalate", status=CompilationStatus.SUCCESS),
        CompiledStep(step_id="3", description="If external, block", compiled_spl="block", status=CompilationStatus.SUCCESS),
        CompiledStep(step_id="4", description="Notify lead", compiled_spl="notify", status=CompilationStatus.SUCCESS),
    ]
    
    graph = planner.generate_graph(runbook, c_steps)
    
    # Assert nodes
    assert len(graph.nodes) == 4
    assert graph.entry_node == "node_1"
    
    # Assert edges: 1->2 (cond), 1->3 (cond), 2->4 (merge), 3->4 (merge)
    assert len(graph.edges) == 4
    
    edges_map = {(e.source, e.target): e for e in graph.edges}
    
    assert ("node_1", "node_2") in edges_map
    assert edges_map[("node_1", "node_2")].branch_condition.operator == "=="
    
    assert ("node_1", "node_3") in edges_map
    assert edges_map[("node_1", "node_3")].branch_condition.operator == "=="
    
    # Verify merges
    assert ("node_2", "node_4") in edges_map
    assert ("node_3", "node_4") in edges_map


def test_graph_validation_orphans():
    planner = ExecutionPlanner()
    
    node1 = ExecutionNode(node_id="node_1", step_id="1", step_name="Step 1", confidence=1.0)
    node2 = ExecutionNode(node_id="node_2", step_id="2", step_name="Step 2", confidence=1.0)
    node3 = ExecutionNode(node_id="node_3", step_id="3", step_name="Step 3", confidence=1.0)
    
    # node_3 is disconnected/orphan
    edge = ExecutionEdge(source="node_1", target="node_2")
    graph = ExecutionGraph(nodes=[node1, node2, node3], edges=[edge], entry_node="node_1")
    
    with pytest.raises(ValidationError) as excinfo:
        planner.validate_graph(graph)
    assert "orphan node detected" in str(excinfo.value)


def test_graph_validation_reachability():
    planner = ExecutionPlanner()
    
    node1 = ExecutionNode(node_id="node_1", step_id="1", step_name="Step 1", confidence=1.0)
    node2 = ExecutionNode(node_id="node_2", step_id="2", step_name="Step 2", confidence=1.0)
    node3 = ExecutionNode(node_id="node_3", step_id="3", step_name="Step 3", confidence=1.0)
    
    # Node 3 is reachable from Node 2, but Node 2 is not reachable from Entry Node 1
    # Actually, we have a cycle or independent component: Node 2 -> Node 3 and Node 3 -> Node 2, but entry Node 1 is not connected to them
    # Incoming count for Node 2 is 1 (from Node 3), so it's not an orphan by count, but it's unreachable from entry Node 1.
    edge1 = ExecutionEdge(source="node_2", target="node_3")
    edge2 = ExecutionEdge(source="node_3", target="node_2")
    graph = ExecutionGraph(nodes=[node1, node2, node3], edges=[edge1, edge2], entry_node="node_1")
    
    with pytest.raises(ValidationError) as excinfo:
        planner.validate_graph(graph)
    assert "unreachable nodes detected" in str(excinfo.value)
