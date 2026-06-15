import re
from typing import List, Dict
from app.grounding.models import SchemaGroundingResult
from app.schema.intelligence import FieldIntelligenceEngine


class SchemaGroundingEngine:
    """
    Extracts, validates, and matches natural language field names against actual schema definitions
    using the FieldIntelligenceEngine synonym resolver and popularity mappings.
    """

    def __init__(self):
        self.intel_engine = FieldIntelligenceEngine()
        # Default alias mappings for token extraction
        self._alias_map: Dict[str, str] = {
            "source_ip": "src_ip",
            "ip": "src_ip",
            "source ip": "src_ip",
            "username": "user",
            "user": "user",
            "action": "action",
            "status": "status",
            "threat_score": "threat_score",
            "store_id": "store_id",
            "revenue_drop_pct": "revenue_drop_pct",
            "date": "date"
        }

    def extract_requested_fields(self, description: str) -> List[str]:
        """
        Scans a text description for keywords matching known fields or aliases.
        """
        desc_lower = description.lower()
        requested = []

        # Find multi-word aliases first to prevent partial matching (e.g. "source ip")
        sorted_aliases = sorted(self._alias_map.keys(), key=len, reverse=True)
        
        for alias in sorted_aliases:
            if alias in desc_lower:
                # Avoid duplicates
                canonical = self._alias_map[alias]
                if canonical not in requested:
                    # Capture the original matched name
                    requested.append(alias)
                # Remove matched alias from temporary string to avoid double matching
                desc_lower = desc_lower.replace(alias, "")

        return requested

    def ground(self, description: str, schema_fields: List[str]) -> SchemaGroundingResult:
        """
        Maps extracted field tokens to schema fields using field intelligence,
        identifying missing ones and computing a weighted confidence score.
        """
        requested = self.extract_requested_fields(description)
        resolved_fields: List[str] = []
        missing_fields: List[str] = []
        warnings: List[str] = []
        confidence_sum = 0.0

        if not requested:
            # If no fields are identified, confidence is 1.0 (trivial success)
            return SchemaGroundingResult(
                requested_fields=[],
                resolved_fields=[],
                missing_fields=[],
                confidence=1.0,
                warnings=[]
            )

        for req in requested:
            resolved_field, weight = self.intel_engine.resolve_field(req, schema_fields)
            confidence_sum += weight

            if resolved_field:
                if resolved_field not in resolved_fields:
                    resolved_fields.append(resolved_field)
            else:
                if req not in missing_fields:
                    missing_fields.append(req)
                    warnings.append(f"Field '{req}' not found in schema.")

        # Calculate grounding confidence as the average weight of requested tokens
        average_confidence = confidence_sum / len(requested)

        return SchemaGroundingResult(
            requested_fields=requested,
            resolved_fields=resolved_fields,
            missing_fields=missing_fields,
            confidence=round(average_confidence, 2),
            warnings=warnings
        )
