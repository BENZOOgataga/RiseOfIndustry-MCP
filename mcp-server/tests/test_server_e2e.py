"""End-to-end: the real `roi_mcp.server` entry point over MCP stdio (subprocess, temporary exchange
directory), plus the background prefetcher."""

import asyncio
import json
import os
import sys

import pytest

import build_fixtures as bf
from conftest import SCHEMA_DIR
from test_tools_contract import PRD_TOOLS


def test_stdio_server_lists_tools_and_answers(tmp_path):
    from mcp import Client, StdioServerParameters

    exchange = tmp_path / "RoiMcp"
    exchange.mkdir()
    bf.write_world(exchange, bf.build_world())
    env = {k: v for k, v in os.environ.items() if k.upper() in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA",
                                                                  "APPDATA", "COMSPEC", "PATHEXT", "WINDIR", "HOME")}
    env.update({"ROI_MCP_EXCHANGE_DIR": str(exchange), "ROI_MCP_SCHEMA_DIR": str(SCHEMA_DIR), "PYTHONIOENCODING": "utf-8"})
    params = StdioServerParameters(command=sys.executable, args=["-m", "roi_mcp.server"], env=env)

    async def go():
        async with Client(params, read_timeout_seconds=60) as client:
            tools = (await client.list_tools()).tools
            status = await client.call_tool("get_game_status", {})
            recipe = await client.call_tool("get_recipe", {"recipe": "Chemicals"})
            return tools, status, recipe

    tools, status, recipe = asyncio.run(go())
    from roi_mcp.tools import ADVISOR_TOOL_NAMES
    assert sorted(t.name for t in tools) == sorted(PRD_TOOLS + list(ADVISOR_TOOL_NAMES))
    body = json.loads(status.content[0].text)
    assert body["ok"] is True and body["data"]["server"]["exchange_dir"] == str(exchange)
    # The real process list is read (read-only); the synthetic heartbeat's PID belongs to no game process,
    # so whatever the machine runs, the fixture data is never classified as live.
    assert body["meta"]["game_state"] != "ready"
    rb = json.loads(recipe.content[0].text)
    assert rb["ok"] and rb["data"]["per_30d"]["outputs"][0]["per_30d"] == 3
    # server log written into the (temporary) exchange directory, nothing else besides the fixtures
    names = {p.name for p in exchange.iterdir()}
    assert names <= {"heartbeat.json", "static.json", "state.json", "history.json", "server.log"}, names


def test_prefetch_prepares_and_call_swaps_in(h):
    assert h.call("list_routes")["meta"]["snapshot"]["seq"] == 10
    doc = bf.build_state(seq=11)
    h.write_raw("state", json.dumps(doc))
    prepared = h.app.store.prefetch_once()
    assert "state" in prepared                        # (history is prepared too: not loaded by list_routes yet)
    assert h.app.store.prefetch_once() == []          # same files are not prepared twice
    r = h.call("list_routes")
    assert r["meta"]["snapshot"]["seq"] == 11
    assert "state" not in h.app.store._prepared       # consumed
    h.write_raw("state", "{broken")
    assert h.app.store.prefetch_once() == ["state"]
    r2 = h.call("list_routes")
    assert r2["ok"] and r2["meta"]["snapshot"]["seq"] == 11
    assert "snapshot_invalid_using_previous" in [w["code"] for w in r2["meta"]["warnings"]]


def test_prefetcher_thread_starts_and_stops(h):
    t = h.app.start_prefetcher(interval_s=0.01)
    assert t.is_alive()
    h.app.stop_prefetcher()
    t.join(timeout=5)
    assert not t.is_alive()
