from app.spl.base import BaseSPLExplainer
from app.context.generation_context import GenerationContext


class MockExplainer(BaseSPLExplainer):
    """Mock implementation of SPL Explainer using GenerationContext."""

    def explain(self, spl: str, context: GenerationContext) -> str:
        return f"This mock query explains the query: '{spl}'."
