import asyncio
from typing import Any, Dict
import anyio
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from app.config.settings import settings


class SplunkMCPAdapterError(Exception):
    """Base exception for all Splunk MCP Adapter operations."""
    pass


class MCPConnectionError(SplunkMCPAdapterError):
    """Raised when establishing connection to MCP server fails."""
    pass


class MCPToolExecutionError(SplunkMCPAdapterError):
    """Raised when an MCP tool execution fails or returns an error response."""
    pass


def run_async(async_fn, *args, **kwargs) -> Any:
    """Helper to run an async function synchronously, handling existing event loops."""
    try:
        # Check if an event loop is already running
        asyncio.get_running_loop()
        from anyio.from_thread import run as from_thread_run
        return from_thread_run(async_fn, *args, **kwargs)
    except RuntimeError:
        return anyio.run(async_fn, *args, **kwargs)


async def call_mcp_tool_async(tool_name: str, arguments: Dict[str, Any]) -> str:
    """Connects to the configured MCP server, executes a tool, and returns the response text."""
    if not settings.enable_mcp:
        raise SplunkMCPAdapterError("MCP adapter execution is disabled in settings.")

    try:
        if settings.mcp_transport == "stdio":
            server_params = StdioServerParameters(
                command=settings.mcp_command,
                args=settings.mcp_args
            )
            # Establish stdio communication channel
            async with stdio_client(server_params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
                    
                    if result.isError:
                        err_msg = result.content[0].text if result.content else "Unknown error"
                        raise MCPToolExecutionError(f"MCP tool execution failed: {err_msg}")
                    
                    if not result.content:
                        raise MCPToolExecutionError("MCP tool execution returned empty content.")
                    
                    return result.content[0].text

        elif settings.mcp_transport == "sse":
            # Inject bearer token for authenticated Splunk MCP endpoints
            sse_headers = {}
            if settings.mcp_token:
                sse_headers["Authorization"] = f"Bearer {settings.mcp_token}"
            # Use a custom httpx client that skips SSL verification for self-signed Splunk certs
            def _ssl_factory(**kwargs):
                kwargs.setdefault("verify", False)
                return httpx.AsyncClient(**kwargs)
            # Establish SSE communication channel
            async with sse_client(
                settings.mcp_sse_url,
                headers=sse_headers,
                httpx_client_factory=_ssl_factory,
            ) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
                    
                    if result.isError:
                        err_msg = result.content[0].text if result.content else "Unknown error"
                        raise MCPToolExecutionError(f"MCP tool execution failed: {err_msg}")
                    
                    if not result.content:
                        raise MCPToolExecutionError("MCP tool execution returned empty content.")
                    
                    return result.content[0].text
        elif settings.mcp_transport == "streamable_http":
            # Splunk Enterprise MCP endpoint uses Streamable HTTP (POST-based JSON-RPC)
            # Auth header uses 'Splunk <token>' format, not 'Bearer'
            sh_headers = {}
            if settings.mcp_token:
                sh_headers["Authorization"] = f"Splunk {settings.mcp_token}"
            def _ssl_factory_sh(**kwargs):
                kwargs.setdefault("verify", False)
                return httpx.AsyncClient(**kwargs)
            async with streamablehttp_client(
                settings.mcp_sse_url,
                headers=sh_headers,
                httpx_client_factory=_ssl_factory_sh,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)

                    if result.isError:
                        err_msg = result.content[0].text if result.content else "Unknown error"
                        raise MCPToolExecutionError(f"MCP tool execution failed: {err_msg}")

                    if not result.content:
                        raise MCPToolExecutionError("MCP tool execution returned empty content.")

                    return result.content[0].text
        else:
            raise SplunkMCPAdapterError(f"Unsupported MCP transport: {settings.mcp_transport}")
            
    except Exception as e:
        if isinstance(e, SplunkMCPAdapterError):
            raise e
        raise MCPConnectionError(f"Failed to communicate with MCP server: {str(e)}") from e


def call_mcp_tool(tool_name: str, arguments: Dict[str, Any]) -> str:
    """Synchronous entry point to execute an MCP tool."""
    return run_async(call_mcp_tool_async, tool_name, arguments)
