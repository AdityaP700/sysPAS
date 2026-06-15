import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.config.settings import settings
from app.context.generation_context import GenerationContext
from app.domain.models import RunbookStep
from app.domain.enums import StepType
from app.splunk.adapters.client import (
    call_mcp_tool,
    call_mcp_tool_async,
    SplunkMCPAdapterError,
    MCPConnectionError,
    MCPToolExecutionError
)
from app.splunk.adapters.mcp_generator import SplunkMCPGenerator
from app.splunk.adapters.mcp_optimizer import SplunkMCPOptimizer
from app.splunk.adapters.mcp_validator import SplunkMCPValidator
from app.splunk.adapters.mcp_explainer import SplunkMCPExplainer
import mcp.types


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture(autouse=True)
def enable_mcp_for_mcp_tests(monkeypatch):
    monkeypatch.setattr(settings, "enable_mcp", True)


def test_mcp_settings():
    """Verify default configurations are parsed correctly."""
    # Since settings are loaded from .env which might be customized,
    # we verify that keys are present and typed correctly.
    assert isinstance(settings.mcp_transport, str)
    assert isinstance(settings.mcp_timeout, float)
    assert isinstance(settings.enable_mcp, bool)


def test_mcp_generator_structured_success():
    """Verify that SplunkMCPGenerator successfully handles response from generate_spl."""
    step = RunbookStep(
        step_id="1",
        description="Check auth logs",
        step_type=StepType.DETECTION,
        confidence=0.9
    )
    context = GenerationContext(step=step, schema_fields=[])
    
    from app.splunk.adapters.spl_provider import SPLResult
    mock_res = SPLResult(
        spl="index=auth_logs status=failure",
        explanation="Check auth logs",
        optimization_notes="",
        cached=False,
        provider="openrouter",
        model_used="claude-3-5"
    )
    
    with patch("app.splunk.adapters.mcp_generator.generate_spl", return_value=mock_res) as mock_call:
        generator = SplunkMCPGenerator()
        spl = generator.generate(context)
        
        assert spl == "index=auth_logs status=failure"
        assert generator.last_intent == "OPENROUTER_GENERATED"
        assert generator.last_generator_confidence == 0.95
        mock_call.assert_called_once_with(context)


def test_mcp_generator_raw_text_success():
    """Verify that SplunkMCPGenerator handles cached result from generate_spl."""
    step = RunbookStep(
        step_id="1",
        description="Check auth logs",
        step_type=StepType.DETECTION,
        confidence=0.9
    )
    context = GenerationContext(step=step, schema_fields=[])
    
    from app.splunk.adapters.spl_provider import SPLResult
    mock_res = SPLResult(
        spl="index=auth_logs status=failure | stats count",
        explanation="Check auth logs",
        optimization_notes="",
        cached=True,
        provider="gemini",
        model_used="gemini-2.5-flash"
    )
    
    with patch("app.splunk.adapters.mcp_generator.generate_spl", return_value=mock_res) as mock_call:
        generator = SplunkMCPGenerator()
        spl = generator.generate(context)
        
        assert spl == "index=auth_logs status=failure | stats count"
        assert generator.last_intent == "GEMINI_GENERATED"
        assert generator.last_generator_confidence == 0.97
        mock_call.assert_called_once_with(context)


def test_mcp_generator_failure():
    """Verify that generator adapter-level error is raised when generation fails."""
    step = RunbookStep(
        step_id="1",
        description="Check auth logs",
        step_type=StepType.DETECTION,
        confidence=0.9
    )
    context = GenerationContext(step=step, schema_fields=[])
    
    with patch("app.splunk.adapters.mcp_generator.generate_spl", side_effect=ValueError("connection lost")):
        generator = SplunkMCPGenerator()
        with pytest.raises(SplunkMCPAdapterError) as exc_info:
            generator.generate(context)
        assert "Splunk MCP Generator failed" in str(exc_info.value)


def test_mcp_optimizer_success():
    """Verify SplunkMCPOptimizer processes raw SPL successfully."""
    step = RunbookStep(
        step_id="1",
        description="Check auth logs",
        step_type=StepType.DETECTION,
        confidence=0.9
    )
    context = GenerationContext(step=step, schema_fields=[])
    
    from app.splunk.adapters.spl_provider import SPLResult
    mock_res = SPLResult(
        spl="index=auth earliest=-5m",
        explanation="Check auth logs",
        optimization_notes="",
        cached=False,
        provider="openrouter",
        model_used="claude-3-5"
    )
    
    with patch("app.splunk.adapters.mcp_optimizer.generate_spl", return_value=mock_res) as mock_call:
        optimizer = SplunkMCPOptimizer()
        res = optimizer.optimize("index=auth", context)
        assert res == "index=auth earliest=-5m"
        mock_call.assert_called_once_with(context)


def test_mcp_validator_success_json():
    """Verify SplunkMCPValidator handles JSON validator response."""
    step = RunbookStep(
        step_id="1",
        description="Check auth logs",
        step_type=StepType.DETECTION,
        confidence=0.9
    )
    context = GenerationContext(step=step, schema_fields=[])
    
    mock_response = '{"is_valid": true}'
    
    with patch("app.splunk.adapters.mcp_validator.call_mcp_tool", return_value=mock_response):
        validator = SplunkMCPValidator()
        assert validator.validate("index=auth", context) is True


def test_mcp_validator_success_raw_string():
    """Verify SplunkMCPValidator handles raw string validation response."""
    step = RunbookStep(
        step_id="1",
        description="Check auth logs",
        step_type=StepType.DETECTION,
        confidence=0.9
    )
    context = GenerationContext(step=step, schema_fields=[])
    
    with patch("app.splunk.adapters.mcp_validator.call_mcp_tool", return_value="valid"):
        validator = SplunkMCPValidator()
        assert validator.validate("index=auth", context) is True


def test_mcp_explainer_success():
    """Verify SplunkMCPExplainer works successfully."""
    step = RunbookStep(
        step_id="1",
        description="Check auth logs",
        step_type=StepType.DETECTION,
        confidence=0.9
    )
    context = GenerationContext(step=step, schema_fields=[])
    
    from app.splunk.adapters.spl_provider import SPLResult
    mock_res = SPLResult(
        spl="index=auth",
        explanation="Explanation text from tool.",
        optimization_notes="",
        cached=False,
        provider="openrouter",
        model_used="claude-3-5"
    )
    
    with patch("app.splunk.adapters.mcp_explainer.generate_spl", return_value=mock_res) as mock_call:
        explainer = SplunkMCPExplainer()
        res = explainer.explain("index=auth", context)
        assert res == "Explanation text from tool."
        mock_call.assert_called_once_with(context)


@pytest.mark.anyio
async def test_client_call_mcp_tool_stdio_success():
    """Verify that call_mcp_tool_async connects via stdio and retrieves data successfully."""
    mock_result = MagicMock()
    mock_result.isError = False
    mock_result.content = [mcp.types.TextContent(type="text", text="raw generated spl query")]

    mock_session = AsyncMock()
    mock_session.call_tool.return_value = mock_result

    # Custom context manager mocks for stdio_client and ClientSession
    class MockStdioContext:
        async def __aenter__(self):
            return (MagicMock(), MagicMock())
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    class MockSessionContext:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("app.splunk.adapters.client.stdio_client", return_value=MockStdioContext()), \
         patch("app.splunk.adapters.client.ClientSession", return_value=MockSessionContext()), \
         patch("app.config.settings.settings.mcp_transport", "stdio"):
        
        res = await call_mcp_tool_async("splunk_generate_spl", {"query": "test"})
        assert res == "raw generated spl query"
        mock_session.initialize.assert_awaited_once()
        mock_session.call_tool.assert_awaited_once_with("splunk_generate_spl", {"query": "test"})


@pytest.mark.anyio
async def test_client_call_mcp_tool_is_error():
    """Verify that MCPToolExecutionError is raised if isError is set to True."""
    mock_result = MagicMock()
    mock_result.isError = True
    mock_result.content = [mcp.types.TextContent(type="text", text="Syntax error in query")]

    mock_session = AsyncMock()
    mock_session.call_tool.return_value = mock_result

    class MockStdioContext:
        async def __aenter__(self):
            return (MagicMock(), MagicMock())
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    class MockSessionContext:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("app.splunk.adapters.client.stdio_client", return_value=MockStdioContext()), \
         patch("app.splunk.adapters.client.ClientSession", return_value=MockSessionContext()), \
         patch("app.config.settings.settings.mcp_transport", "stdio"):
        
        with pytest.raises(MCPToolExecutionError) as exc_info:
            await call_mcp_tool_async("splunk_generate_spl", {"query": "test"})
        assert "Syntax error in query" in str(exc_info.value)

