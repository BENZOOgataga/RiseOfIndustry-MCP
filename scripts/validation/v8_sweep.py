"""Gate V8 sweep (PRD 22): every MCP tool, repeatedly, with `fresh: true`, through the real stdio server.

Run from the repository root while the validation save is loaded and paused, between the V8-b and V8-c saves:

    uv run --directory mcp-server python ../scripts/validation/v8_sweep.py --minutes 5 --out ../.local/v8/sweep.json

What it does (read-only: every call is a read-only MCP tool; the server's only write is refresh-request.json):
  * discovers real ids from the live snapshot (player and AI buildings, a route, a vehicle, a shop and a product
    it accepts, a city, a region, a company, a recipe, a building type);
  * calls all 29 tools in rounds until the duration has elapsed (the last round is completed), with a few
    parameter variants per tool; every tool whose refresh scope is not `none` gets `fresh: true` on every call;
  * validates every response against schemas/tool-responses/<tool>.schema.json and the 30 KB size cap;
  * records per tool: calls, ok, error codes, warning codes, unavailable fields, sources, stale flags,
    refresh timeouts, latency; counts internal_error;
  * checks that meta.world_session and every meta.snapshots[].world_session equal the heartbeat's world
    session, that game_state stays `ready`, and that the paused game date does not change during the sweep.

The output names entities of the loaded save, so it must be written below .local/ (gitignored).
"""

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO / "schemas" / "tool-responses"
EXCHANGE = Path(os.environ.get("ROI_MCP_EXCHANGE_DIR") or Path(os.environ["LOCALAPPDATA"]) / "RoiMcp")
SIZE_CAP = 30000

ALL_TOOLS = [
    "get_game_status", "search", "list_companies", "get_company", "get_finances", "list_buildings",
    "get_building", "get_production_overview", "find_production_issues", "list_routes", "get_route",
    "list_warehouse_requests", "list_vehicles", "get_supply_chain", "list_products", "get_product",
    "list_recipes", "get_recipe", "list_building_types", "get_building_type", "list_cities", "get_city",
    "get_shop", "find_shops", "list_regions", "get_region", "get_market", "get_tech_tree", "get_research_state",
]
NO_FRESH = {"get_game_status", "get_recipe"}  # PRD 13.7 scope `none`
ALL_INCLUDES = ["production", "inventory", "outgoing_routes", "incoming_routes", "requests", "modules", "history", "vehicles"]


def _params():
    from mcp import StdioServerParameters
    return StdioServerParameters(command=sys.executable, args=["-m", "roi_mcp.server"], env=dict(os.environ))


def _get(body, *path):
    cur = body
    for p in path:
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and isinstance(p, int) and len(cur) > p:
            cur = cur[p]
        else:
            return None
    return cur


def heartbeat() -> dict:
    for _ in range(5):
        try:
            return json.loads((EXCHANGE / "heartbeat.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.2)
    return {}


class Sweep:
    def __init__(self, client):
        from jsonschema import Draft202012Validator
        self.client = client
        self.validators = {t: Draft202012Validator(json.loads((SCHEMA_DIR / f"{t}.schema.json").read_text(encoding="utf-8")))
                           for t in ALL_TOOLS}
        self.stats = {t: {"calls": 0, "ok": 0, "fresh_calls": 0, "errors": Counter(), "warnings": Counter(),
                          "unavailable_fields": Counter(), "sources": Counter(), "stale": 0, "schema_invalid": 0,
                          "over_size_cap": 0, "max_s": 0.0, "total_s": 0.0, "variants": set()} for t in ALL_TOOLS}
        self.problems: list[dict] = []
        self.world_sessions: Counter = Counter()
        self.game_states: Counter = Counter()
        self.game_dates: Counter = Counter()

    async def call(self, tool, args, variant):
        t0 = time.perf_counter()
        res = await self.client.call_tool(tool, args)
        dt = time.perf_counter() - t0
        text = res.content[0].text
        body = json.loads(text)
        s = self.stats[tool]
        s["calls"] += 1
        s["variants"].add(variant)
        s["fresh_calls"] += 1 if args.get("fresh") else 0
        s["max_s"] = max(s["max_s"], round(dt, 3))
        s["total_s"] += dt
        errs = list(self.validators[tool].iter_errors(body))
        if errs:
            s["schema_invalid"] += 1
            self.problems.append({"tool": tool, "variant": variant, "kind": "schema_invalid",
                                  "detail": f"{'/'.join(map(str, errs[0].absolute_path))}: {errs[0].message[:200]}"})
        if len(text.encode("utf-8")) > SIZE_CAP:
            s["over_size_cap"] += 1
            self.problems.append({"tool": tool, "variant": variant, "kind": "over_size_cap", "detail": len(text.encode("utf-8"))})
        meta = body.get("meta") or {}
        for w in meta.get("warnings") or []:
            s["warnings"][w.get("code") if isinstance(w, dict) else w] += 1
        s["sources"][meta.get("source")] += 1
        if meta.get("stale"):
            s["stale"] += 1
        self.game_states[meta.get("game_state")] += 1
        if body.get("ok"):
            s["ok"] += 1
            for u in _get(body, "data", "unavailable") or []:
                s["unavailable_fields"][u.get("field")] += 1
        else:
            code = _get(body, "error", "code") or "unknown"
            s["errors"][code] += 1
            if code == "internal_error":
                self.problems.append({"tool": tool, "variant": variant, "kind": "internal_error",
                                      "detail": _get(body, "error", "message")})
        ws = meta.get("world_session")
        self.world_sessions[ws] += 1
        for snap in meta.get("snapshots") or []:
            if snap.get("family") != "static" and snap.get("world_session") != ws:
                self.problems.append({"tool": tool, "variant": variant, "kind": "snapshot_session_mismatch",
                                      "detail": {"meta": ws, "snapshot": snap.get("world_session"), "family": snap.get("family")}})
            if snap.get("age_s") is not None and snap["age_s"] < 0:
                self.problems.append({"tool": tool, "variant": variant, "kind": "negative_age", "detail": snap})
            if snap.get("game_date"):
                self.game_dates[(snap.get("family"), snap.get("game_date"))] += 1
        return body


async def discover(sw: Sweep) -> dict:
    ids = {}
    c = await sw.call("list_companies", {}, "discover")
    comps = _get(c, "data", "companies") or []
    ids["player_company"] = next((x["id"] for x in comps if x.get("is_player")), None)
    ids["ai_company"] = next((x["id"] for x in comps if not x.get("is_player")), None)
    b = await sw.call("list_buildings", {"limit": 50, "sort": "produced_last_month"}, "discover")
    rows = _get(b, "data", "buildings") or []
    ids["player_building"] = rows[0]["id"] if rows else None
    ids["player_building_name"] = rows[0].get("display_name") if rows else None
    ab = await sw.call("list_buildings", {"owner": "ai", "limit": 5}, "discover")
    ids["ai_building"] = _get(ab, "data", "buildings", 0, "id")
    r = await sw.call("list_routes", {"limit": 50}, "discover")
    routes = _get(r, "data", "routes") or []
    ids["route"] = routes[0]["route_id"] if routes else None
    if routes:
        ids["route_origin"] = _get(routes[0], "origin", "id")
    v = await sw.call("list_vehicles", {"aggregate": False, "limit": 5}, "discover")
    ids["vehicle"] = _get(v, "data", "vehicles", 0, "id")
    ct = await sw.call("list_cities", {"sort": "population", "limit": 5}, "discover")
    ids["city"] = _get(ct, "data", "cities", 0, "id")
    gc = await sw.call("get_city", {"city": ids["city"]}, "discover") if ids["city"] else {}
    shop = _get(gc, "data", "shops", 0)
    ids["shop"] = (shop or {}).get("id")
    gs = await sw.call("get_shop", {"shop": ids["shop"]}, "discover") if ids["shop"] else {}
    ids["shop_product"] = _get(gs, "data", "products", 0, "product")
    g = await sw.call("list_regions", {"owner": "player", "limit": 5}, "discover")
    ids["region"] = _get(g, "data", "regions", 0, "id")
    if ids["region"] is None:
        g = await sw.call("list_regions", {"limit": 5}, "discover")
        ids["region"] = _get(g, "data", "regions", 0, "id")
    po = await sw.call("get_production_overview", {}, "discover")
    ids["product"] = _get(po, "data", "products", 0, "product") or ids["shop_product"]
    rc = await sw.call("list_recipes", {"product": ids["product"], "limit": 5}, "discover") if ids["product"] else {}
    ids["recipe"] = _get(rc, "data", "recipes", 0, "id")
    bt = await sw.call("list_building_types", {"limit": 5}, "discover")
    ids["building_type"] = _get(bt, "data", "building_types", 0, "id")
    tt = await sw.call("get_tech_tree", {"limit": 5}, "discover")
    ids["tech_tree"] = _get(tt, "data", "trees", 0, "id")
    return ids


def variants(ids: dict) -> dict:
    """tool -> list of (variant name, args). Real ids only; ids that were not found are dropped."""
    P, B, AB = ids.get("product"), ids.get("player_building"), ids.get("ai_building")
    v = {
        "get_game_status": [("default", {})],
        "search": [("building_name", {"query": ids.get("player_building_name") or "a"}),
                   ("product_kind", {"query": (P or "product:a").split(":", 1)[1], "kinds": ["product", "recipe"]}),
                   ("owner_all", {"query": "a", "owner": "all", "limit": 50})],
        "list_companies": [("default", {}), ("full", {"fields": "full"})],
        "get_company": [("player", {}), ("ai", {"company": ids.get("ai_company")})],
        "get_finances": [("6_months", {"months": 6}), ("overview_group", {"months": 3, "group_by": "overview_group"})],
        "list_buildings": [("default", {}), ("all_upkeep", {"owner": "all", "sort": "upkeep", "limit": 50}),
                           ("full_idle", {"fields": "full", "status": "idle"})],
        "get_building": [("default", {"building": B}), ("all_includes", {"building": B, "include": ALL_INCLUDES}),
                         ("ai", {"building": AB})],
        "get_production_overview": [("default", {}), ("product", {"product": P})],
        "find_production_issues": [("default", {}), ("kinds", {"kinds": ["route_error", "output_full", "missing_input"]})],
        "list_routes": [("default", {}), ("cost_full", {"sort": "dispatch_cost", "fields": "full"}),
                        ("errors_only", {"errors_only": True})],
        "get_route": [("default", {"route": ids.get("route")})],
        "list_warehouse_requests": [("default", {})],
        "list_vehicles": [("aggregate", {}), ("per_vehicle", {"aggregate": False}), ("one", {"vehicle": ids.get("vehicle")})],
        "get_supply_chain": [("product", {"product": P}), ("building_both", {"building": B, "direction": "both", "depth": 4}),
                             ("recipe_target", {"product": P, "mode": "recipe", "target_output_per_30d": 100})],
        "list_products": [("default", {}), ("unlocked_full", {"unlocked_only": True, "fields": "full"})],
        "get_product": [("default", {"product": P})],
        "list_recipes": [("product", {"product": P})],
        "get_recipe": [("default", {"recipe": ids.get("recipe")})],
        "list_building_types": [("default", {}), ("product", {"product": P})],
        "get_building_type": [("default", {"building_type": ids.get("building_type")})],
        "list_cities": [("population", {"sort": "population"})],
        "get_city": [("default", {"city": ids.get("city")})],
        "get_shop": [("default", {"shop": ids.get("shop")})],
        "find_shops": [("from_player", {"product": ids.get("shop_product"), "from_building": ids.get("route_origin") or B,
                                        "sort": "distance"}),
                       ("from_ai", {"product": ids.get("shop_product"), "from_building": AB}),
                       ("no_origin", {"product": ids.get("shop_product")})],
        "list_regions": [("default", {}), ("player", {"owner": "player"})],
        "get_region": [("default", {"region": ids.get("region")})],
        "get_market": [("default", {}), ("contracts", {"include": ["contracts"]})],
        "get_tech_tree": [("available", {"state": "available"}), ("ai", {"company": ids.get("ai_company")})],
        "get_research_state": [("default", {})],
    }
    out = {}
    for tool, vs in v.items():
        keep = []
        for name, args in vs:
            if any(val is None for val in args.values()):
                continue
            keep.append((name, args))
        out[tool] = keep
    return out


async def monitor(samples: list, stop: asyncio.Event):
    """Samples the heartbeat every 2 s: the game must stay ready, paused, in one world session and on one game day."""
    while not stop.is_set():
        d = heartbeat().get("data") or {}
        samples.append({"t": round(time.time(), 1), "state": d.get("state"), "paused": d.get("paused"),
                        "world_session": d.get("world_session"), "game_day": d.get("game_day"),
                        "errors_last_hour": d.get("errors_last_hour"), "degraded": d.get("degraded")})
        try:
            await asyncio.wait_for(stop.wait(), 2.0)
        except asyncio.TimeoutError:
            pass


async def run(minutes: float, out: Path):
    from mcp import Client
    hb0 = heartbeat()
    t_start = time.time()
    samples: list = []
    stop = asyncio.Event()
    mon = asyncio.create_task(monitor(samples, stop))
    async with Client(_params(), read_timeout_seconds=120) as client:
        sw = Sweep(client)
        ids = await discover(sw)
        plan = variants(ids)
        missing = [t for t in ALL_TOOLS if not plan.get(t)]
        deadline = time.time() + minutes * 60
        rounds = 0
        while time.time() < deadline:
            for tool in ALL_TOOLS:
                for name, args in plan.get(tool) or []:
                    a = dict(args)
                    if tool not in NO_FRESH:
                        a["fresh"] = True
                    await sw.call(tool, a, name)
            rounds += 1
    stop.set()
    await mon
    hb1 = heartbeat()
    t_end = time.time()

    def hb_summary(hb):
        d = hb.get("data") or {}
        return {"state": d.get("state"), "world_session": d.get("world_session"), "paused": d.get("paused"),
                "speed_level": d.get("speed_level"), "time_scale": d.get("time_scale"),
                "game_date": d.get("game_date"), "game_day": d.get("game_day"), "compatibility": d.get("compatibility"),
                "observer_version": d.get("observer_version"), "degraded": d.get("degraded"),
                "disabled_sections": d.get("disabled_sections"), "errors_last_hour": d.get("errors_last_hour"),
                "publish_failures": d.get("publish_failures"), "refresh_seen": d.get("refresh_seen"),
                "refresh_served": d.get("refresh_served"), "families": d.get("families"),
                "written_utc": hb.get("written_utc")}

    tools = {}
    for t in ALL_TOOLS:
        s = sw.stats[t]
        tools[t] = {"calls": s["calls"], "ok": s["ok"], "fresh_calls": s["fresh_calls"], "errors": dict(s["errors"]),
                    "warnings": dict(s["warnings"]), "unavailable_fields": dict(s["unavailable_fields"]),
                    "sources": {str(k): v for k, v in s["sources"].items()}, "stale": s["stale"],
                    "schema_invalid": s["schema_invalid"], "over_size_cap": s["over_size_cap"],
                    "max_s": s["max_s"], "mean_s": round(s["total_s"] / s["calls"], 3) if s["calls"] else None,
                    "variants": sorted(s["variants"])}
    totals = {
        "calls": sum(x["calls"] for x in tools.values()),
        "fresh_calls": sum(x["fresh_calls"] for x in tools.values()),
        "ok": sum(x["ok"] for x in tools.values()),
        "schema_invalid": sum(x["schema_invalid"] for x in tools.values()),
        "over_size_cap": sum(x["over_size_cap"] for x in tools.values()),
        "internal_error": sum(x["errors"].get("internal_error", 0) for x in tools.values()),
        "refresh_timeout": sum(x["warnings"].get("refresh_timeout", 0) for x in tools.values()),
        "error_codes": dict(sum((Counter(x["errors"]) for x in tools.values()), Counter())),
        "warning_codes": dict(sum((Counter(x["warnings"]) for x in tools.values()), Counter())),
    }
    result = {
        "gate": "V8 sweep", "minutes_requested": minutes, "elapsed_s": round(t_end - t_start, 1), "rounds": rounds,
        "tools_exercised": sum(1 for x in tools.values() if x["calls"] > 0), "tools_without_variant": missing,
        "ids": ids, "heartbeat_before": hb_summary(hb0), "heartbeat_after": hb_summary(hb1),
        "world_sessions_in_meta": {str(k): v for k, v in sw.world_sessions.items()},
        "game_states_in_meta": {str(k): v for k, v in sw.game_states.items()},
        "snapshot_game_dates": {f"{k[0]}:{k[1]}": v for k, v in sw.game_dates.items()},
        "heartbeat_samples": {
            "count": len(samples),
            "states": dict(Counter(str(x["state"]) for x in samples)),
            "paused": dict(Counter(str(x["paused"]) for x in samples)),
            "world_sessions": dict(Counter(str(x["world_session"]) for x in samples)),
            "game_days": dict(Counter(str(x["game_day"]) for x in samples)),
            "errors_last_hour": sorted({x["errors_last_hour"] for x in samples if x["errors_last_hour"] is not None}),
            "degraded": dict(Counter(str(x["degraded"]) for x in samples)),
        },
        "totals": totals, "problems": sw.problems[:200], "problem_count": len(sw.problems), "tools": tools,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("elapsed_s", "rounds", "tools_exercised", "tools_without_variant",
                                             "world_sessions_in_meta", "game_states_in_meta", "totals", "problem_count")},
                     indent=2, default=str))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=5.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out).resolve()
    if REPO / ".local" not in out.parents:
        sys.exit("--out must be below .local/ (the output names entities of the loaded save)")
    asyncio.run(run(a.minutes, out))


if __name__ == "__main__":
    main()
