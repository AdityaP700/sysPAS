from app.grounding.confidence import ConfidenceCalculator


def test_confidence_multiplication():
    # 0.9 * 0.8 * 1.0 = 0.72
    val = ConfidenceCalculator.calculate_overall(0.9, 0.8, 1.0)
    assert val == 0.72
    
    # 0.5 * 0.5 * 0.5 = 0.125 -> 0.12 (banker's rounding: round half to even)
    val2 = ConfidenceCalculator.calculate_overall(0.5, 0.5, 0.5)
    assert val2 == 0.12


def test_confidence_boundaries():
    # Bounds safety checks
    val = ConfidenceCalculator.calculate_overall(-0.5, 1.5, 0.8)
    # Parser: 0.0, Grounding: 1.0, Generator: 0.8 -> 0.0
    assert val == 0.0
    
    val2 = ConfidenceCalculator.calculate_overall(1.0, 1.0, 1.0)
    assert val2 == 1.0
