# Implementation MCP Audit

## Current Architecture
The RunbookMind application is transitioning from a simulated (mocked) backend to executing complex tasks over the Model Context Protocol (MCP). The workflow orchestration revolves around `RunbookCompiler` logic, converting higher-level natural language steps into targeted runbooks.
Currently, compiling runbooks (Generation, Optimization, Explanation, and Validation of SPL), Schema Discovery, and Data queries abstract through adapter layers. However, the centralized dependency container and orchestrator components bypass these adapters in favor of hard-coded simulated endpoints (`MockGenerator`, `MockQueryRunner`, etc.). 

Dependency Injection relies heavily on FastAPI application startup definitions (`app/web/dependencies.py`), which constructs Singleton instances. 

## MCP Call Flow
The communication pattern for standard RunbookMind queries executing real SPL traces back dynamically:
1. **API/System**: A request hits a fastAPI route or Job Worker which executes `Engine.run()` or `RunbookService.compile()`.
2. **Service Layer**: The orchestration component invokes methods on adapters (e.g., `schema_provider.get_fields()`, `generator.generate()`).
3. **Adapter Implementation**: The mapped adapter (like `SplunkMCPGenerator` or `SplunkQueryRunner`) parses required variables, transforms it, and maps it to a designated MCP tool identifier (e.g., `splunk_generate_spl`).
4. **Client Transport (`app/splunk/adapters/client.py`)**: The `call_mcp_tool` (sync) or `call_mcp_tool_async` connects over a subprocess (`stdio_client`) or HTTP (`sse_client`), negotiates handshakes with `ClientSession`, dispatches the tool call, decodes the response, and closes.
5. **Adapter Return**: Decoded string or JSON payload resolves up the tree back to orchestrating layer.

## Mocked Components
The following implementations explicitly override actual API or logic execution paths with mock values:
1. **Dependencies (`app/web/dependencies.py`)**: Sets `_query_runner_instance = MockQueryRunner()`.
2. **Service Orchestrator (`app/service/runbook_service.py`)**: The `__init__` constructor manually hardcodes:
   - `MockGenerator()`
   - `MockOptimizer()`
   - `MockExplainer()`
   - `MockValidator()`
   - `MockSchemaProvider()`
3. **Action Connectors (`app/actions/email.py`, `app/actions/ticket.py`)**: Contain placeholders that merely log or sleep and return simulated successful responses in code.
4. **Test & Utility code**: Pervasive use of `Mock...` classes across unit testing wrappers.

## Risk Assessment
Major risks are uncovered within the current Splunk MCP implementations, specifically within `app/splunk/adapters/client.py`:
- **Not Production-Ready (Blocking)**: The `call_mcp_tool_async` opens a brand-new connection tunnel (spawning `settings.mcp_command` as a new child subprocess for `stdio`) *every time* a tool is called. This represents significant operational overhead. The lack of a long-lived persistence session pool or multi-threaded task management will throttle application endpoints rapidly.
- Partial Error Handling: Mappers surrounding JSON parsing (`app/splunk/adapters/mcp_generator.py` and others) have broad `except json.JSONDecodeError` fallbacks returning untyped strings which obscure generation faults.
- Native Timeout Absence: Synchronous MCP tools don't inherently implement safety boundaries unless dynamically halted (like via `anyio` in `SplunkQueryRunner`). The generator, optimizer, and synchronous methods could block indefinitely.

`settings.py` establishes default transport over `"stdio"` which invokes `python -m splunk_mcp_server`. 

## Required Code Changes
To decouple simulated components and fully realize MCP Splunk capabilities:
1. Refactor `app/splunk/adapters/client.py` to maintain a persistent `ClientSession` over the lifecycle of the FastAPI worker, avoiding process respawns per query.
2. Parameterize `RunbookService` to accept SPL handlers externally or substitute `Mock...` classes with their equivalent `SplunkMCP...` counterparts (`SplunkMCPGenerator`, `SplunkMCPOptimizer`, `SplunkMCPExplainer`, `SplunkMCPValidator`, `SchemaDiscoveryEngine`).
3. Replace the `MockQueryRunner` assignment in `app/web/dependencies.py` with `SplunkQueryRunner`.
4. Replace mocked implementations of Action Connectors with actual target integrations.
5. Add configuration handlers for MCP lifecycle startup/shutdown events hooked via `main.py`.

## Exact Files to Edit
1. `app/web/dependencies.py`
2. `app/service/runbook_service.py`
3. `app/splunk/adapters/client.py`
4. `app/actions/email.py`
5. `app/actions/ticket.py`
6. `app/web/main.py` (To manage `ClientSession` lifecycle pooling for `client.py`)
