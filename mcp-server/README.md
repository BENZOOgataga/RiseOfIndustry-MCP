# roi-mcp (MCP server)

Read-only MCP server for the original **Rise of Industry** (Steam App 671440). It reads the snapshot files the
in-game observer writes to the exchange directory and exposes the 29 tools of `PRD.md` §14 over MCP **stdio**
(no network listener). No tool can change the game.

## Run

```text
uv --directory <repo>/mcp-server run roi-mcp
```

MCP client configuration (Claude Desktop / Claude Code style):

```json
{"mcpServers": {"rise-of-industry": {"command": "uv", "args": ["--directory", "C:\\path\\to\\ROI-MCP\\mcp-server", "run", "roi-mcp"]}}}
```

The server starts without the game; tools answer with lifecycle errors (`game_not_running`, `at_main_menu`, ...)
until the observer publishes data. `get_game_status` always answers.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `ROI_MCP_EXCHANGE_DIR` | `%LOCALAPPDATA%\RoiMcp` | Exchange directory shared with the observer |
| `ROI_MCP_REFRESH_WAIT_S` | `3` (max `10`) | How long a `fresh: true` call waits for the observer |
| `ROI_MCP_SCHEMA_DIR` | `<repo>/schemas` | Snapshot JSON Schemas used to validate the files |
| `ROI_MCP_LOG_LEVEL` | `INFO` | Log level (stderr and `server.log`) |

Schema location: `uv --directory ... run` installs this project in editable mode, so the schemas are resolved
relative to the source tree (`mcp-server/src/roi_mcp` -> `<repo>/schemas`). A packager may instead copy them to
`roi_mcp/_schemas/`, which is used when present; `ROI_MCP_SCHEMA_DIR` overrides both.

Files written by the server: only `refresh-request.json` (atomic temp file + replace, for `fresh: true`) and its
log `server.log` (rotating 5 MB x 3, only if the exchange directory exists; the server never creates it).
stdout carries MCP protocol messages only.

## Test

```text
uv run --directory mcp-server pytest
```

Tests use temporary exchange directories and synthetic fixtures only (`tests/fixtures/`). Regenerate:

```text
uv run --directory mcp-server python tests/fixtures/build_fixtures.py          # committed sample fixtures
uv run --directory mcp-server python -m roi_mcp.response_schemas              # schemas/tool-responses/*.schema.json
uv run --directory mcp-server python tests/perf/run_perf9.py                  # PERF-9 on a large fixture in .local/
```

## Layout

| Module | Role |
|---|---|
| `server.py` | MCP stdio entry point, logging, background snapshot prefetcher |
| `app.py` | Call framework: liveness gating, stale handling, refresh, envelope/meta, pagination, 30 KB cap |
| `store.py` | Snapshot store (validation, retry, last-good, schema/static checks), liveness, staleness, state window |
| `refresh.py` | The only writer: `refresh-request.json`, per-scope nonces with coalescing |
| `index.py` | Indexes, `<kind>:<key>` ids, name resolution (exact; fuzzy only in `search`) |
| `derive.py`, `formula.py` | PRD §15 derivations (D-*) and the safe formula evaluator (no eval) |
| `tools/` | The 29 tools |
| `response_schemas.py` | Source of `schemas/tool-responses/*.schema.json` |
