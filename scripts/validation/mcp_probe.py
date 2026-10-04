"""Validation helper: drives the real roi-mcp server over MCP stdio (the same path an MCP client uses).

Run from the repository root:

    uv run --directory mcp-server python ../scripts/validation/mcp_probe.py call get_game_status
    uv run --directory mcp-server python ../scripts/validation/mcp_probe.py call list_routes '{"product": "Gas"}'
    uv run --directory mcp-server python ../scripts/validation/mcp_probe.py sweep --minutes 5 --out ../.local/validation/v8-sweep.json
    uv run --directory mcp-server python ../scripts/validation/mcp_probe.py refresh-latency --n 20

Read-only: every call goes through the server's read-only tools. The only file the server writes in the
exchange directory is refresh-request.json (for fresh: true).
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time

ALL_TOOLS = [
    "get_game_status", "search", "list_companies", "get_company", "get_finances", "list_buildings",
    "get_building", "get_production_overview", "find_production_issues", "list_routes", "get_route",
    "list_warehouse_requests", "list_vehicles", "get_supply_chain", "list_products", "get_product",
    "list_recipes", "get_recipe", "list_building_types", "get_building_type", "list_cities", "get_city",
    "get_shop", "find_shops", "list_regions", "get_region", "get_market", "get_tech_tree", "get_research_state",
]
NO_FRESH = {"get_game_status", "get_recipe"}


def _params():
    from mcp import StdioServerParameters
    return StdioServerParameters(command=sys.executable, args=["-m", "roi_mcp.server"], env=dict(os.environ))


async def _call(client, name, args):
    t0 = time.perf_counter()
    res = await client.call_tool(name, args)
    dt = time.perf_counter() - t0
    body = json.loads(res.content[0].text)
    return body, dt


def _first(body, *path):
    cur = body
    for p in path:
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and isinstance(p, int) and len(cur) > p:
            cur = cur[p]
        else:
            return None
    return cur


async def cmd_call(name, args):
    from mcp import Client
    async with Client(_params(), read_timeout_seconds=120) as client:
        body, dt = await _call(client, name, args)
        print(json.dumps(body, indent=2, ensure_ascii=False))
        print("elapsed_s=%.3f" % dt, file=sys.stderr)


async def _discover(client):
    """Picks real ids from the live snapshot for the detail tools."""
    ids = {}
    b, _ = await _call(client, "list_buildings", {"limit": 5})
    ids["building"] = _first(b, "data", "buildings", 0, "id") or _first(b, "data", "rows", 0, "id")
    r, _ = await _call(client, "list_routes", {"limit": 5})
    ids["route"] = _first(r, "data", "routes", 0, "route_id")
    c, _ = await _call(client, "list_cities", {"limit": 5})
    ids["city"] = _first(c, "data", "cities", 0, "id") or _first(c, "data", "rows", 0, "id")
    g, _ = await _call(client, "list_regions", {"limit": 5})
    ids["region"] = _first(g, "data", "regions", 0, "id") or _first(g, "data", "rows", 0, "id")
    p, _ = await _call(client, "list_products", {"limit": 5})
    ids["product"] = _first(p, "data", "products", 0, "id") or _first(p, "data", "rows", 0, "id")
    t, _ = await _call(client, "list_building_types", {"limit": 5})
    ids["building_type"] = _first(t, "data", "building_types", 0, "id") or _first(t, "data", "rows", 0, "id")
    rc, _ = await _call(client, "list_recipes", {"limit": 5})
    ids["recipe"] = _first(rc, "data", "recipes", 0, "id") or _first(rc, "data", "rows", 0, "id")
    sh, _ = await _call(client, "get_city", {"city": ids["city"]}) if ids.get("city") else ({}, 0)
    ids["shop"] = _first(sh, "data", "shops", 0, "id")
    return ids


def _args_for(tool, ids, fresh):
    a = {}
    if tool == "search":
        a = {"query": "a"}
    elif tool == "get_building":
        a = {"building": ids.get("building")}
    elif tool == "get_route":
        a = {"route": ids.get("route")}
    elif tool == "get_supply_chain":
        a = {"product": ids.get("product")}
    elif tool == "get_product":
        a = {"product": ids.get("product")}
    elif tool == "get_recipe":
        a = {"recipe": ids.get("recipe")}
    elif tool == "get_building_type":
        a = {"building_type": ids.get("building_type")}
    elif tool == "get_city":
        a = {"city": ids.get("city")}
    elif tool == "get_shop":
        a = {"shop": ids.get("shop")}
    elif tool == "find_shops":
        a = {"product": ids.get("product")}
    elif tool == "get_region":
        a = {"region": ids.get("region")}
    if fresh and tool not in NO_FRESH:
        a["fresh"] = True
    return {k: v for k, v in a.items() if v is not None}


async def cmd_sweep(minutes, out, fresh):
    from mcp import Client
    stats = {t: {"calls": 0, "ok": 0, "errors": {}, "max_s": 0.0} for t in ALL_TOOLS}
    async with Client(_params(), read_timeout_seconds=120) as client:
        ids = await _discover(client)
        deadline = time.time() + minutes * 60
        rounds = 0
        while time.time() < deadline:
            rounds += 1
            for tool in ALL_TOOLS:
                body, dt = await _call(client, tool, _args_for(tool, ids, fresh))
                s = stats[tool]
                s["calls"] += 1
                s["max_s"] = max(s["max_s"], round(dt, 3))
                if body.get("ok"):
                    s["ok"] += 1
                else:
                    code = _first(body, "error", "code") or "unknown"
                    s["errors"][code] = s["errors"].get(code, 0) + 1
    result = {"minutes": minutes, "fresh": fresh, "rounds": rounds, "ids": ids, "tools": stats}
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        open(out, "w", encoding="utf-8").write(text)
    print(text)


async def cmd_refresh_latency(n, tool):
    from mcp import Client
    lat, timeouts = [], 0
    async with Client(_params(), read_timeout_seconds=120) as client:
        for _ in range(n):
            body, dt = await _call(client, tool, {"fresh": True, "limit": 1})
            warns = [w.get("code") if isinstance(w, dict) else w for w in (_first(body, "meta", "warnings") or [])]
            if "refresh_timeout" in warns:
                timeouts += 1
            lat.append(dt)
            await asyncio.sleep(1.5)
    print(json.dumps({
        "tool": tool, "n": n, "refresh_timeouts": timeouts,
        "latency_s": {"min": round(min(lat), 3), "median": round(statistics.median(lat), 3), "max": round(max(lat), 3)},
    }, indent=2))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("call")
    c.add_argument("tool")
    c.add_argument("args", nargs="?", default="{}")
    s = sub.add_parser("sweep")
    s.add_argument("--minutes", type=float, default=5)
    s.add_argument("--out")
    s.add_argument("--no-fresh", action="store_true")
    r = sub.add_parser("refresh-latency")
    r.add_argument("--n", type=int, default=20)
    r.add_argument("--tool", default="list_routes")
    a = ap.parse_args()
    if a.cmd == "call":
        asyncio.run(cmd_call(a.tool, json.loads(a.args)))
    elif a.cmd == "sweep":
        asyncio.run(cmd_sweep(a.minutes, a.out, not a.no_fresh))
    else:
        asyncio.run(cmd_refresh_latency(a.n, a.tool))


if __name__ == "__main__":
    main()
