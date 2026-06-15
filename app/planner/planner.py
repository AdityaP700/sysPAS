import re
from typing import List, Dict, Set, Optional
from app.domain.enums import StepType
from app.domain.models import Runbook, CompiledStep
from app.agent.graph import ExecutionNode, ExecutionEdge, ExecutionGraph
from app.planner.conditions import BranchCondition
from app.parser.normalizer import infer_action_type
from app.core.exceptions import ValidationError


class ExecutionPlanner:
    """
    Analyzes Runbook structures and compiled query metadata to generate 
    a conditional ExecutionGraph, enforcing control routing guards and connectivity validations.
    """

    def parse_condition(self, condition_str: Optional[str]) -> Optional[BranchCondition]:
        """
        Parses condition strings into structured BranchCondition objects.
        Detects standard operators (==, !=, >, <, >=, <=, in).
        """
        if not condition_str:
            return None

        condition_clean = condition_str.strip()
        
        # Remove common prefix keywords like "if ", "when ", "unless " (case insensitive)
        condition_clean = re.sub(r'^(if|when|unless|else)\s+', '', condition_clean, flags=re.IGNORECASE)

        # Match patterns like: source_ip == internal, count > 100
        pattern = r'^(\b\w+\b)\s*(==|!=|>=|<=|>|<|in|not\s+in)\s*(\b\w+\b)$'
        match = re.match(pattern, condition_clean, re.IGNORECASE)
        
        if match:
            expr, op, val = match.groups()
            return BranchCondition(
                expression=condition_clean,
                operator=op.strip(),
                value=val.strip(),
                confidence=0.95
            )
            
        return BranchCondition(
            expression=condition_clean,
            confidence=0.75
        )

    def generate_graph(self, runbook: Runbook, compiled_steps: List[CompiledStep]) -> ExecutionGraph:
        """
        Processes compiled steps, creates graph nodes, generates branching/merging edges,
        and runs graph topology validation before returning.
        """
        if not compiled_steps:
            raise ValidationError("Cannot plan execution for an empty compilation result.")

        step_metadata = {s.step_id: s for s in runbook.steps}
        nodes: List[ExecutionNode] = []

        for c_step in compiled_steps:
            step_id = c_step.step_id
            orig_step = step_metadata.get(step_id)

            # Resolve action type
            action_type_val = None
            if orig_step and orig_step.action:
                act_type = infer_action_type(orig_step.action)
                if act_type:
                    action_type_val = act_type.value
            elif orig_step and orig_step.step_type == StepType.MANUAL:
                action_type_val = "MANUAL"

            nodes.append(ExecutionNode(
                node_id=f"node_{step_id}",
                step_id=step_id,
                step_name=c_step.description,
                action_type=action_type_val,
                compiled_spl=c_step.compiled_spl,
                confidence=c_step.confidence
            ))

        # Graph routing algorithm
        edges: List[ExecutionEdge] = []
        last_non_conditional_node = nodes[0]
        leaf_conditional_nodes: List[ExecutionNode] = []

        for i in range(1, len(nodes)):
            this_node = nodes[i]
            orig_step = step_metadata.get(this_node.step_id)
            prev_node = nodes[i - 1]

            # Determine if this step is conditional
            is_cond = False
            condition_text = None
            if orig_step:
                desc_lower = orig_step.description.lower()
                is_cond = (orig_step.condition is not None) or any(
                    desc_lower.startswith(prefix) for prefix in ["if ", "when ", "unless ", "else "]
                )
                condition_text = orig_step.condition or orig_step.description

            if is_cond:
                # Branch from the last decision/non-conditional checkpoint
                parsed_cond = self.parse_condition(condition_text)
                edges.append(ExecutionEdge(
                    source=last_non_conditional_node.node_id,
                    target=this_node.node_id,
                    condition=condition_text,
                    branch_condition=parsed_cond
                ))
                leaf_conditional_nodes.append(this_node)
            else:
                # Merges active leaf conditional branches back into the linear path
                if leaf_conditional_nodes:
                    for leaf in leaf_conditional_nodes:
                        edges.append(ExecutionEdge(
                            source=leaf.node_id,
                            target=this_node.node_id
                        ))
                    leaf_conditional_nodes.clear()
                else:
                    # Default linear connection
                    edges.append(ExecutionEdge(
                        source=prev_node.node_id,
                        target=this_node.node_id
                    ))
                last_non_conditional_node = this_node

        entry_node = nodes[0].node_id if nodes else None
        graph = ExecutionGraph(
            nodes=nodes,
            edges=edges,
            entry_node=entry_node
        )

        # Validate graph connectivity prior to export
        self.validate_graph(graph)

        return graph

    def validate_graph(self, graph: ExecutionGraph) -> bool:
        """
        Validates the ExecutionGraph topological connectivity rules:
        1. Entry node is valid and exists
        2. No orphan nodes (all non-entry nodes must have at least one incoming edge)
        3. All nodes are reachable from the entry node (DFS)
        """
        node_ids = {n.node_id for n in graph.nodes}

        # 1. Valid entry node check
        if not graph.entry_node:
            raise ValidationError("Graph validation failed: missing entry node.")
        if graph.entry_node not in node_ids:
            raise ValidationError(f"Graph validation failed: entry node '{graph.entry_node}' does not exist.")

        if len(graph.nodes) == 1:
            return True

        # Map edges
        adj_list: Dict[str, List[str]] = {n_id: [] for n_id in node_ids}
        incoming_counts: Dict[str, int] = {n_id: 0 for n_id in node_ids}

        for edge in graph.edges:
            if edge.source not in node_ids:
                raise ValidationError(f"Graph validation failed: edge source '{edge.source}' does not exist.")
            if edge.target not in node_ids:
                raise ValidationError(f"Graph validation failed: edge target '{edge.target}' does not exist.")
            adj_list[edge.source].append(edge.target)
            incoming_counts[edge.target] += 1

        # 2. No orphan nodes check
        for node_id in node_ids:
            if node_id == graph.entry_node:
                continue
            if incoming_counts[node_id] == 0:
                raise ValidationError(
                    f"Graph validation failed: orphan node detected. "
                    f"Node '{node_id}' is unreachable (no incoming edges)."
                )

        # 3. Reachability check from entry node (DFS)
        visited: Set[str] = set()

        def dfs(curr: str):
            visited.add(curr)
            for neighbor in adj_list[curr]:
                if neighbor not in visited:
                    dfs(neighbor)

        dfs(graph.entry_node)

        unreachable = node_ids - visited
        if unreachable:
            raise ValidationError(
                f"Graph validation failed: unreachable nodes detected from entry. "
                f"Nodes: {unreachable}"
            )

        return True
