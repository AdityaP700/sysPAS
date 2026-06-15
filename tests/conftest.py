import pytest
from app.config.settings import settings

# Globally disable real MCP server calls for all unit and integration tests
settings.enable_mcp = False
