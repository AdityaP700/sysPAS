from enum import Enum


class Intent(str, Enum):
    """Supported query intentions for template selection."""
    FAILED_LOGIN = "FAILED_LOGIN"
    BRUTE_FORCE = "BRUTE_FORCE"
    SUSPICIOUS_IP = "SUSPICIOUS_IP"
    THREAT_LOOKUP = "THREAT_LOOKUP"
    ESCALATION = "ESCALATION"
    POWERSHELL_ACTIVITY = "POWERSHELL_ACTIVITY"
    CREDENTIAL_DUMPING = "CREDENTIAL_DUMPING"
    PERSISTENCE = "PERSISTENCE"
    REGISTRY_MODIFICATION = "REGISTRY_MODIFICATION"
    ACCOUNT_CREATION = "ACCOUNT_CREATION"
    GENERIC = "GENERIC"


class IntentMapper:
    """Maps operational step descriptions to security intents."""

    @staticmethod
    def map_description_to_intent(description: str) -> Intent:
        desc_lower = description.lower()

        if any(kw in desc_lower for kw in ["powershell activity", "powershell abuse", "powershell"]):
            return Intent.POWERSHELL_ACTIVITY
        if any(kw in desc_lower for kw in ["credential dumping", "credential theft", "mimikatz", "lsass"]):
            return Intent.CREDENTIAL_DUMPING
        if any(kw in desc_lower for kw in ["persistence mechanism", "persistence"]):
            return Intent.PERSISTENCE
        if any(kw in desc_lower for kw in ["registry modification", "registry"]):
            return Intent.REGISTRY_MODIFICATION
        if any(kw in desc_lower for kw in ["account creation", "net user", "create account"]):
            return Intent.ACCOUNT_CREATION
        if any(kw in desc_lower for kw in ["brute force", "multiple authentication failures", "failed login spikes", "login spikes", "spikes >"]):
            return Intent.BRUTE_FORCE
        if any(kw in desc_lower for kw in ["failed login", "login failure", "authentication failure", "authentication failures", "auth check", "failed logins", "auth logs"]):
            return Intent.FAILED_LOGIN
        if any(kw in desc_lower for kw in ["correlate", "threat intel", "lookup threat", "threat lookup"]):
            return Intent.THREAT_LOOKUP
        if any(kw in desc_lower for kw in ["suspicious ip", "block ip", "block"]):
            return Intent.SUSPICIOUS_IP
        if any(kw in desc_lower for kw in ["escalate", "escalation", "tier 2", "tier 3"]):
            return Intent.ESCALATION

        return Intent.GENERIC
