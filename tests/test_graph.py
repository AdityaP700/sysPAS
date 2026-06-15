from app.agent.graph import ExecutionNode, ExecutionEdge, ExecutionGraph


def test_execution_graph_structure():
    node1 = ExecutionNode(
        node_id="node_1",
        step_id="1",
        step_name="Step 1",
        action_type="DETECTION",
        compiled_spl="index=auth",
        confidence=0.9
    )
    node2 = ExecutionNode(
        node_id="node_2",
        step_id="2",
        step_name="Step 2",
        action_type="ACTION",
        compiled_spl="index=auth | block",
        confidence=0.95
    )
    
    edge = ExecutionEdge(
        source="node_1",
        target="node_2",
        condition="failures > 100"
    )
    
    graph = ExecutionGraph(
        nodes=[node1, node2],
        edges=[edge],
        entry_node="node_1"
    )
    
    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1
    assert graph.entry_node == "node_1"
    assert graph.edges[0].source == "node_1"
    assert graph.edges[0].target == "node_2"
    assert graph.edges[0].condition == "failures > 100"
