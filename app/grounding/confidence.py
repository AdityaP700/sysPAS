class ConfidenceCalculator:
    """Calculates overall compilation confidence metrics by combining phase-specific scores."""

    @staticmethod
    def calculate_overall(parser_conf: float, grounding_conf: float, generator_conf: float) -> float:
        """
        Combines parser (decomposition), grounding (schema mapping), and generator (SPL generation)
        confidence scores via multiplication.
        """
        # Ensure bounds are honored
        p = max(0.0, min(1.0, parser_conf))
        gr = max(0.0, min(1.0, grounding_conf))
        gen = max(0.0, min(1.0, generator_conf))
        
        overall = p * gr * gen
        return round(overall, 2)
