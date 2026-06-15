import re
from typing import Dict, Any
from app.planner.conditions import BranchCondition


class BranchEvaluator:
    """Evaluates conditional routing edge expressions against variables in the execution context."""

    @staticmethod
    def evaluate_condition(condition: BranchCondition, context_state: Dict[str, Any]) -> bool:
        expr = condition.expression.strip()
        op = condition.operator
        val = condition.value

        # If operator or value are missing, try to parse them from expression
        if not op or not val:
            match = re.search(r'(==|!=|>=|<=|>|<)', expr)
            if match:
                op = match.group(1)
                parts = expr.split(op, 1)
                var_name = parts[0].strip()
                val = parts[1].strip()
            else:
                # Check if expression exists as a key
                var_val = context_state.get(expr)
                if var_val is None:
                    # check nested
                    for k, v in context_state.items():
                        if isinstance(v, dict) and expr in v:
                            var_val = v[expr]
                            break
                return bool(var_val)
        else:
            parts = expr.split(op, 1)
            var_name = parts[0].strip()

        val_str = val.strip().strip("'\"")

        # Resolve variable value
        var_val = context_state.get(var_name)
        if var_val is None:
            # Check nested dictionary nodes
            for k, v in context_state.items():
                if isinstance(v, dict) and var_name in v:
                    var_val = v[var_name]
                    break

        if var_val is None:
            return False

        # Cast to match variable type
        try:
            if isinstance(var_val, bool):
                compare_val = val_str.lower() in ("true", "1")
            elif isinstance(var_val, int):
                compare_val = int(val_str)
            elif isinstance(var_val, float):
                compare_val = float(val_str)
            else:
                compare_val = val_str
        except (ValueError, TypeError):
            compare_val = val_str

        # Comparisons
        try:
            if op == "==":
                return var_val == compare_val
            elif op == "!=":
                return var_val != compare_val
            elif op == ">":
                return var_val > compare_val
            elif op == "<":
                return var_val < compare_val
            elif op == ">=":
                return var_val >= compare_val
            elif op == "<=":
                return var_val <= compare_val
        except TypeError:
            return False

        return False
