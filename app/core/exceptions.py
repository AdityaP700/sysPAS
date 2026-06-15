"""
Custom exception definitions for RunbookMind.
"""

class RunbookMindError(Exception):
    """Base exception for all RunbookMind errors."""
    pass


class ParsingError(RunbookMindError):
    """Exception raised when parsing of a runbook fails."""
    pass


class ValidationError(RunbookMindError):
    """Exception raised when validation of a runbook fails."""
    pass


class ModelError(RunbookMindError):
    """Exception raised when model instantiation or operation fails."""
    pass
