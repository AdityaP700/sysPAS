from typing import Set, Optional
from app.spl.base import BaseSPLValidator
from app.context.generation_context import GenerationContext


class MockValidator(BaseSPLValidator):
    """Mock implementation of SPL Validator using GenerationContext."""

    def __init__(self, invalid_queries: Optional[Set[str]] = None):
        self.invalid_queries = invalid_queries if invalid_queries is not None else set()

    def validate(self, spl: str, context: GenerationContext) -> bool:
        if spl in self.invalid_queries or "invalid" in spl.lower():
            return False
        return True
