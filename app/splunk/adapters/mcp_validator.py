import json
from app.spl.base import BaseSPLValidator
from app.context.generation_context import GenerationContext
from app.splunk.adapters.client import call_mcp_tool, SplunkMCPAdapterError


class SplunkMCPValidator(BaseSPLValidator):
    """Splunk MCP Validator adapter executing syntactic and semantic validation over Model Context Protocol."""

    def validate(self, spl: str, context: GenerationContext) -> bool:
        """
        Validates an SPL query syntax and field references using the 'splunk_validate_spl' tool from the MCP server.
        """
        arguments = {
            "spl": spl,
            "data_source": context.data_source,
            "schema_fields": context.schema_fields,
            "constraints": context.constraints
        }
        try:
            response_text = call_mcp_tool("splunk_validate_spl", arguments)
            try:
                data = json.loads(response_text)
                # Check for either 'is_valid' or 'valid' boolean fields in JSON
                if "is_valid" in data:
                    return bool(data["is_valid"])
                if "valid" in data:
                    return bool(data["valid"])
                return False
            except json.JSONDecodeError:
                # Fallback to checking raw string value
                return response_text.strip().lower() in ("true", "1", "yes", "valid")
        except Exception as e:
            raise SplunkMCPAdapterError(f"Splunk MCP Validator failed: {str(e)}") from e
