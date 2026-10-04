# MCP client configuration

The server `roi-mcp` speaks MCP over **stdio** only: your MCP client starts it as a child process. It opens no
network port. It can be started before or after the game; it never needs a restart when the game starts,
stops or loads another save.

Command (replace `<REPO>` with the absolute path of your clone, using forward slashes or escaped
backslashes in JSON):

```text
uv --directory <REPO>/mcp-server run roi-mcp
```

The first start creates a project-local virtual environment in `<REPO>/mcp-server/.venv` and installs the
dependencies (official `mcp` SDK, `jsonschema`, `psutil`).

## Claude Desktop

Edit `claude_desktop_config.json` (Claude Desktop → Settings → Developer → Edit Config) and add:

```json
{
  "mcpServers": {
    "rise-of-industry": {
      "command": "uv",
      "args": ["--directory", "<REPO>/mcp-server", "run", "roi-mcp"]
    }
  }
}
```

Restart Claude Desktop. The tools appear under the `rise-of-industry` server.

## Claude Code

```bash
claude mcp add rise-of-industry -- uv --directory <REPO>/mcp-server run roi-mcp
```

or add the same `mcpServers` entry to a project `.mcp.json`.

Use `-s user` to make it available in all your projects:

```bash
claude mcp add -s user rise-of-industry -- uv --directory <REPO>/mcp-server run roi-mcp
```

## Codex

Codex versions with `codex mcp` support local stdio servers:

```bash
codex mcp add rise-of-industry -- uv --directory <REPO>/mcp-server run roi-mcp
codex mcp get rise-of-industry
```

This writes `[mcp_servers.rise-of-industry]` to `~/.codex/config.toml`. Start a new Codex session (restart the
Codex app if it is open) to load it.

## Other clients (ChatGPT desktop, IDE assistants, ...)

Any client that can launch a local stdio MCP server works. Configure:

- command: `uv`
- arguments: `--directory`, `<REPO>/mcp-server`, `run`, `roi-mcp`
- transport: stdio

Clients that only support remote (HTTP/SSE) MCP servers are not supported by V1: the server deliberately has
no network listener.

## Environment variables

| Variable | Used by | Meaning |
|---|---|---|
| `ROI_MCP_EXCHANGE_DIR` | observer and server | Override the exchange directory (default `%LOCALAPPDATA%\RoiMcp`). Both sides must use the same value; the observer reads it from the game process environment |
| `ROI_GAME_DIR` | build, gate, install scripts | Game install folder if not at the default Steam location |

## First check

Ask your assistant: *"Call get_game_status."* With the game in a loaded save you should see
`game.state: "ready"` and `game.compatibility: "verified"`. See [USAGE.md](USAGE.md) for example questions
and [TROUBLESHOOTING.md](TROUBLESHOOTING.md) if something is off.
