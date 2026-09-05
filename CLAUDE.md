# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Local MCP server (`edi-grpc-mcp`) that exposes 64 tools wrapping EDA/electromagnetic-simulation toolchains so AI clients (Claude Code, OpenClaw) can drive engineering projects via natural language. Windows-only, Python ≥3.10, FastMCP, `uv`-managed. Version in `servers/__init__.py:__version__` (must stay in sync with `pyproject.toml`).

Three simulation backends are wrapped in parallel, each with its own integration style:

- **EDI gRPC** (`servers/eda/`) — primary backend. `ExternalCall` stub on `127.0.0.1:50055`; async `FetchEvent` (subscribe) → `PerformAction` (submit) model. Most tools.
- **ANSYS HFSS** (`servers/ansys/`) — COM attachment to `ansysedt.exe` via `pywin32`, multi-ProgID fallback.
- **CST** (`servers/cst/`) — Python API session, one-shot solve + S-parameter/farfield export.

## Commands

```powershell
# Install / sync
uv sync

# Run the server (streamable-http on 127.0.0.1:50026 by default)
uv run python start_servers.py
uv run python start_servers.py --transport stdio    # Claude Code stdio mode
uv run python start_servers.py --port 9000

# Tests (346 items)
uv run pytest -q                                     # full suite
uv run pytest tests/test_grpc_client.py -v           # single file
uv run pytest tests/test_grpc_client.py::test_name -v # single test

# Package / publish
uv build && uv publish                               # PyPI wheel
powershell -File scripts/build.ps1                   # PyInstaller -> dist/edi-mcp/ (auto smoke test)

# Health checks (server running)
curl http://127.0.0.1:50026/health
curl http://127.0.0.1:50026/ready
```

There is no linter/formatter configured. Tests are the verification surface.

## Architecture

**Single global FastMCP instance.** `servers/__init__.py` creates `mcp = FastMCP(...)`. Tools are registered by `@mcp.tool()` decorators that run at import time. `servers/registry_server.py` is the import hub — importing each `servers/<module>/<file>` triggers registration, and it also wires custom HTTP routes. There is no central tool registry to update when adding a tool; the count (64) is dynamic and asserted by `tests/test_tool_registry.py` (`required` list — update it when adding/removing tools). `start_servers.py` is the CLI entrypoint, not where tools live.

**Configuration is centralized.** `servers/settings.py` exposes a frozen `Settings` dataclass singleton via `get_settings()` (lru_cache). All env-var reads live there (`_read_str/_read_bool/_read_int`); modules must never call `os.getenv` directly. `.env` is loaded at import. Path fields store raw env values; auto-detection logic lives in each module's `config.py`, not in settings.

**gRPC communication model** (`servers/eda/grpc_client.py`). Every EDA tool funnels through `call_grpc(task_type, payload, timeout_seconds)`. Key invariants, easy to break when editing:

- A global `_EDA_LOCK` (RLock) serializes *all* EDA gRPC operations — EDI accepts only one op at a time. `timeout_seconds` is total (queue + execute), and the lock acquire itself is time-bounded.
- Flow: `FetchEvent` subscribe first → then `PerformAction` submit, same `client_uuid` for both.
- Triple echo validation (client_uuid / task_id / event_type) guards against protocol mismatch.
- `ads_output` is collected incrementally across stream events (appended verbatim, never stripped); the terminal event's `ads_output` replaces the accumulated chunks when non-empty.
- Every result uses `_terminal_result(...)` which carries `outcome_known` (received EDI terminal event?) vs `task_success` (only meaningful when `outcome_known=True`). Timeouts/disconnects set `outcome_known=False, task_success=None` — do not conflate MCP-side failure with EDI business failure. gRPC status → MCP status mapping: `DEADLINE_EXCEEDED→TIMEOUT`, `RESOURCE_EXHAUSTED→PAYLOAD_TOO_LARGE`, accepted-then-drop→`STREAM_DISCONNECTED`, never-accepted→`GRPC_UNAVAILABLE`.

**Async task queue** (`servers/task_runner.py`). Generic single-worker `TaskRunner` (QUEUED → RUNNING → SUCCEEDED/FAILED, TTL cleanup, RUNNING-timeout backstop). CST uses it; EDA simulation (`servers/eda/simulation.py`) and HFSS still have their own older implementations, with migration toward this class ongoing. `run_sync()` submits and blocks — used where a serialized op needs a synchronous result.

**Tool return conventions.** gRPC tools return the `_terminal_result` dict (see above). Artifact-producing tools (`turbocharts_convert`, `capture_schematic`, `compare_simulation_results`, `generate_simulation_report`) return `{"success", "artifacts": [{type, path, name, generated_by}], "message"}`. Helpers `validate_file()`, `error_response()` (plus `submitted_response()` / `queue_full_response()` response builders), `build_file_link()` live in `servers/utils.py`. The EDA project-file parser (`ProjectReader` + `parse_sexp`/`parse_components`/`parse_paramsinfo`) lives in `servers/eda/project_reader.py`; signal-chain tracing (`get_signal_chain`) lives in `servers/eda/signal_chain.py`.

**Optional / conditional modules.** `servers/chat/` (LLM multi-round tool loop; `service.py` auto-builds tool schemas from MCP metadata), `servers/multimodal_vision/` (vision analysis), `servers/report/` (report rendering via external HTTP service) are all opt-in. `copy_image_to_workspace` is currently hidden (disabled in `workspace_copy.py`, no workspace detection).

**HTTP surface.** `streamable-http` (default, `stateless_http=True`) or `stdio`. Custom routes (`/health /ready /mcp /ui /chat /tools/list /upload /metrics /images/{token} /documents/{token}`) are registered in `registry_server.py`. When `MCP_API_KEY` is set, a `_TokenAuthMiddleware` in `start_servers.py` requires `?token=` on `/mcp /ui /chat /tools/list /upload` (health/ready/metrics stay open).