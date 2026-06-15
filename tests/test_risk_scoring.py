import pytest
from app.governance.risk import calculate_risk_score


def test_calculate_risk_score_levels():
    # 1. LOW Risk: few steps, no sensitive keywords, MANUAL mode
    payload_low = {
        "steps": [
            {"action": "check_status", "description": "Check service health"}
        ],
        "governance": {
            "execution_mode": "MANUAL"
        }
    }
    low_res = calculate_risk_score(payload_low)
    assert low_res["level"] == "LOW"
    assert low_res["score"] < 30

    # 2. MEDIUM Risk: multiple steps, HIL mode
    payload_med = {
        "steps": [
            {"action": "check_status", "description": "Check service health"},
            {"action": "gather_logs", "description": "Collect logs"},
            {"action": "read_metrics", "description": "Verify load metrics"}
        ],
        "governance": {
            "execution_mode": "HUMAN_IN_LOOP"
        }
    }
    med_res = calculate_risk_score(payload_med)
    assert med_res["level"] == "MEDIUM"

    # 3. HIGH Risk: sensitive keyword (delete), AUTO mode
    payload_high = {
        "steps": [
            {"action": "delete_cache", "description": "Delete temporary cache"}
        ],
        "governance": {
            "execution_mode": "AUTO"
        }
    }
    high_res = calculate_risk_score(payload_high)
    assert high_res["level"] == "HIGH"

    # 4. CRITICAL Risk: many steps, sensitive keywords (shutdown), secret references, AUTO mode
    payload_critical = {
        "steps": [
            {"action": "shutdown_database", "description": "Shutdown production database", "secrets": ["db_passwd"]},
            {"action": "delete_logs", "description": "Remove temporary logs"},
            {"action": "revoke_keys", "description": "Revoke API access keys"}
        ],
        "governance": {
            "execution_mode": "AUTO"
        }
    }
    critical_res = calculate_risk_score(payload_critical)
    assert critical_res["level"] == "CRITICAL"
    assert critical_res["score"] >= 90
