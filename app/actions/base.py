from abc import ABC, abstractmethod
from app.actions.models import ActionResult


class BaseActionConnector(ABC):
    """Abstract interface defining the execution contract for an Action Connector."""

    @abstractmethod
    def validate(self, payload: dict) -> None:
        """Validates that the required input parameters exist and match type constraints."""
        pass

    @abstractmethod
    def execute(self, payload: dict) -> ActionResult:
        """Executes the external integration action synchronously."""
        pass
