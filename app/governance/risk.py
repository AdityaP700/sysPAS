from typing import Dict, Any

def calculate_risk_score(bundle_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculates a risk score from 0 to 100 and maps it to a level:
    LOW, MEDIUM, HIGH, CRITICAL.
    """
    score = 10  # Base score
    
    # 1. Evaluate steps count
    steps = bundle_payload.get("steps", [])
    if not steps:
        # Fallback to runbook subfield or payload steps
        runbook = bundle_payload.get("runbook", {})
        steps = runbook.get("steps", [])
        
    score += min(len(steps) * 5, 20)
    
    # 2. Check for sensitive/destructive actions
    has_sensitive = False
    has_secret_refs = False
    for step in steps:
        action = str(step.get("action", "")).lower()
        desc = str(step.get("description", "")).lower()
        
        # Check sensitive keywords
        sensitive_keywords = ["block", "delete", "revoke", "shutdown", "stop", "disable", "kill", "terminate"]
        if any(kw in action or kw in desc for kw in sensitive_keywords):
            has_sensitive = True
            
        # Check if credential/secret references are used
        if "secret" in action or "secret" in desc or step.get("secrets") or step.get("credential_vault_ref"):
            has_secret_refs = True

    if has_sensitive:
        score += 30
    if has_secret_refs:
        score += 15

    # 3. Check execution mode (AUTO is higher risk than HIL / MANUAL)
    gov = bundle_payload.get("governance", {})
    if not gov:
        runbook = bundle_payload.get("runbook", {})
        gov = runbook.get("governance", {})
        
    exec_mode = gov.get("execution_mode", "MANUAL")
    if exec_mode == "AUTO":
        score += 25
    elif exec_mode == "HUMAN_IN_LOOP":
        score += 10

    # Cap score at 100, min at 0
    score = max(0, min(score, 100))

    # Determine risk level
    if score < 30:
        level = "LOW"
    elif score < 60:
        level = "MEDIUM"
    elif score < 90:
        level = "HIGH"
    else:
        level = "CRITICAL"

    return {
        "score": score,
        "level": level
    }
