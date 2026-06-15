from abc import ABC, abstractmethod
from app.context.generation_context import GenerationContext


class BaseSPLGenerator(ABC):
    """Abstract Base Class for generating raw SPL queries from runbook contexts."""
    
    @abstractmethod
    def generate(self, context: GenerationContext) -> str:
        """
        Generates a raw SPL query based on the provided GenerationContext.
        """
        pass


class BaseSPLOptimizer(ABC):
    """Abstract Base Class for optimizing SPL queries."""
    
    @abstractmethod
    def optimize(self, spl: str, context: GenerationContext) -> str:
        """
        Takes a raw SPL query and returns an optimized version under contextual constraints.
        """
        pass


class BaseSPLExplainer(ABC):
    """Abstract Base Class for explaining SPL queries."""
    
    @abstractmethod
    def explain(self, spl: str, context: GenerationContext) -> str:
        """
        Generates a human-readable explanation of an SPL query.
        """
        pass


class BaseSPLValidator(ABC):
    """Abstract Base Class for validating SPL queries."""
    
    @abstractmethod
    def validate(self, spl: str, context: GenerationContext) -> bool:
        """
        Validates the SPL query syntax or field schemas under context constraints.
        Returns True if valid, False otherwise.
        """
        pass
