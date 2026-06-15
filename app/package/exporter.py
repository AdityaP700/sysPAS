import json
from typing import Dict, Any
from app.package.bundle import SkillBundle


class SkillExporter:
    """Handles exporting SkillBundle configurations into sorted dictionaries and JSON string formats."""

    @staticmethod
    def export_dict(bundle: SkillBundle) -> Dict[str, Any]:
        """
        Exports the bundle as a standard Python dictionary.
        """
        return bundle.model_dump()

    @staticmethod
    def export_json(bundle: SkillBundle) -> str:
        """
        Exports the bundle as a sorted, human-readable JSON string.
        Ensures strict key sorting to guarantee deterministic output hashes.
        """
        data = bundle.model_dump()
        return json.dumps(data, sort_keys=True, indent=2)
