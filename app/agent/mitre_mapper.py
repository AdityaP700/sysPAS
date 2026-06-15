from typing import List

MITRE_MAP = {
    "brute_force": "T1110",
    "powershell_encoded": "T1059.001",
    "obfuscated_powershell": "T1059.001",
    "mimikatz": "T1003",
    "persistence_registry": "T1547.001",
    "registry_run_key": "T1547.001",
    "account_creation": "T1136",
    "net_user_add": "T1136"
}

def map_threat_to_mitre(threat_type: str) -> List[str]:
    """
    Map a given threat type or description to matching MITRE ATT&CK technique IDs.
    Does case-insensitive matching and partial keyword searches.
    """
    if not threat_type:
        return []
        
    threat_lower = threat_type.lower()
    mitre_ids = []
    
    # Direct lookup or key substring matching
    for key, value in MITRE_MAP.items():
        # Replace underscore with space for friendly matches
        key_space = key.replace("_", " ")
        if key in threat_lower or key_space in threat_lower:
            mitre_ids.append(value)
            
    # Fallback to general patterns if no match is found
    if not mitre_ids:
        if "brute" in threat_lower or "login" in threat_lower:
            mitre_ids.append("T1110")
        elif "powershell" in threat_lower or "script" in threat_lower or "execution" in threat_lower:
            mitre_ids.append("T1059.001")
        elif "dump" in threat_lower or "credential" in threat_lower or "lsass" in threat_lower:
            mitre_ids.append("T1003")
        elif "registry" in threat_lower or "autorun" in threat_lower or "startup" in threat_lower or "persistence" in threat_lower:
            mitre_ids.append("T1547.001")
        elif "account" in threat_lower or "user add" in threat_lower:
            mitre_ids.append("T1136")
            
    return mitre_ids
