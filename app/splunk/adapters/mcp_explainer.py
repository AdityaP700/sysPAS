from app.spl.base import BaseSPLExplainer
from app.context.generation_context import GenerationContext
from app.splunk.adapters.spl_provider import generate_spl


class SplunkMCPExplainer(BaseSPLExplainer):
    """
    SPL Explainer — explanation is generated locally from the SPL structure.

    No LLM tokens are consumed here.
    The provider's generate_spl() call is always a cache hit at this point in
    the RunbookCompiler pipeline (generator runs before explainer).
    """

    def explain(self, spl: str, context: GenerationContext) -> str:
        """
        Returns a locally-built explanation from the cached SPLResult.
        Falls back gracefully to the passed-in `spl` if the cache has expired.
        """
        from app.config.settings import settings
        from app.splunk.adapters.client import SplunkMCPAdapterError
        if not settings.enable_mcp:
            raise SplunkMCPAdapterError("MCP adapter execution is disabled in settings.")

        try:
            result = generate_spl(context)
            return result.explanation
        except Exception as e:
            if isinstance(e, SplunkMCPAdapterError):
                raise
            raise SplunkMCPAdapterError(f"Splunk MCP Explainer failed: {str(e)}") from e


