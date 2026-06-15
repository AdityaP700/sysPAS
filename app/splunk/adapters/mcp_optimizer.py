from app.spl.base import BaseSPLOptimizer
from app.context.generation_context import GenerationContext
from app.splunk.adapters.spl_provider import generate_spl


class SplunkMCPOptimizer(BaseSPLOptimizer):
    """
    SPL Optimizer — returns the provider-generated SPL (already optimised at
    generation time) together with locally-computed optimization notes.

    Replaces the old SAIA `saia_optimize_spl` MCP tool call.
    The spl_provider.generate_spl() call is always a cache hit at this stage.
    """

    def optimize(self, spl: str, context: GenerationContext) -> str:
        """
        Returns the cached SPL from the active provider.
        `spl` (the raw SPL passed by the compiler) is identical to result.spl
        because the generator already produced the optimised form.
        """
        from app.config.settings import settings
        from app.splunk.adapters.client import SplunkMCPAdapterError
        if not settings.enable_mcp:
            raise SplunkMCPAdapterError("MCP adapter execution is disabled in settings.")

        try:
            result = generate_spl(context)
            return result.spl
        except Exception as e:
            if isinstance(e, SplunkMCPAdapterError):
                raise
            raise SplunkMCPAdapterError(f"Splunk MCP Optimizer failed: {str(e)}") from e


