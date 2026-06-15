from app.spl.base import BaseSPLOptimizer
from app.context.generation_context import GenerationContext


class MockOptimizer(BaseSPLOptimizer):
    """Mock implementation of SPL Optimizer using GenerationContext."""

    def optimize(self, spl: str, context: GenerationContext) -> str:
        # Standard mock optimization: append time window filter if not present
        if spl == "index=auth":
            return "index=auth earliest=-15m"
        return f"{spl} earliest=-15m"
