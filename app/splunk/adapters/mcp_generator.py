from app.spl.base import BaseSPLGenerator
from app.context.generation_context import GenerationContext
from app.splunk.adapters.spl_provider import generate_spl


class SplunkMCPGenerator(BaseSPLGenerator):
    """
    SPL Generator — provider-agnostic facade over the LLM routing layer.

    Active provider is controlled by RUNBOOKMIND_LLM_PROVIDER in .env:
      openrouter  → OpenRouter first, Gemini fallback  (default)
      gemini      → Gemini only

    The RunbookCompiler interface (generate → str) is preserved exactly.
    """

    def __init__(self):
        self.last_intent = "LLM_GENERATED"
        self.last_generator_confidence = 1.0
        self.last_provider = ""

    def generate(self, context: GenerationContext) -> str:
        """
        Routes through spl_provider.generate_spl() (live or cache) and
        returns the raw SPL string.
        Populates last_intent / last_generator_confidence for compiler trace.
        """
        from app.config.settings import settings
        from app.splunk.adapters.client import SplunkMCPAdapterError
        if not settings.enable_mcp:
            raise SplunkMCPAdapterError("MCP adapter execution is disabled in settings.")

        try:
            result = generate_spl(context)

            self.last_intent = f"{result.provider.upper()}_GENERATED"
            self.last_provider = result.provider
            # Cache hits get a fractionally higher confidence
            self.last_generator_confidence = 0.97 if result.cached else 0.95

            return result.spl
        except Exception as e:
            if isinstance(e, SplunkMCPAdapterError):
                raise
            raise SplunkMCPAdapterError(f"Splunk MCP Generator failed: {str(e)}") from e


