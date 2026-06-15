import pytest
from app.planner.conditions import BranchCondition
from app.runtime.evaluator import BranchEvaluator


def test_branch_evaluation_operators():
    # 1. Greater than
    cond1 = BranchCondition(expression="failures > 100", operator=">", value="100")
    assert BranchEvaluator.evaluate_condition(cond1, {"failures": 120}) is True
    assert BranchEvaluator.evaluate_condition(cond1, {"failures": 80}) is False
    assert BranchEvaluator.evaluate_condition(cond1, {"failures": 100}) is False

    # 2. Equal to (string)
    cond2 = BranchCondition(expression="source_ip == internal", operator="==", value="internal")
    assert BranchEvaluator.evaluate_condition(cond2, {"source_ip": "internal"}) is True
    assert BranchEvaluator.evaluate_condition(cond2, {"source_ip": "external"}) is False

    # 3. Nested context lookup
    assert BranchEvaluator.evaluate_condition(cond1, {"node_1": {"failures": 120}}) is True

    # 4. Less than or equal to
    cond3 = BranchCondition(expression="failures <= 100", operator="<=", value="100")
    assert BranchEvaluator.evaluate_condition(cond3, {"failures": 100}) is True
    assert BranchEvaluator.evaluate_condition(cond3, {"failures": 50}) is True
    assert BranchEvaluator.evaluate_condition(cond3, {"failures": 120}) is False

    # 5. Missing operator / expression parsing
    cond4 = BranchCondition(expression="failures > 50")
    assert BranchEvaluator.evaluate_condition(cond4, {"failures": 80}) is True
    assert BranchEvaluator.evaluate_condition(cond4, {"failures": 30}) is False
