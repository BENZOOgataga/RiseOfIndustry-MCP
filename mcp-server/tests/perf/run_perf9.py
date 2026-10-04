"""PERF-9 measurement: tool latency (excluding the fresh wait) p95 <= 500 ms and server memory
<= 500 MB on the large synthetic fixture.

    uv run --directory mcp-server python tests/perf/run_perf9.py

Writes the large fixture to <repo>/.local/large-fixture/exchange (gitignored) and drives the server
core with a fake clock and a fake process list (the real game is never touched).
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "fixtures"))
sys.path.insert(0, str(HERE.parents[1] / "src"))

import psutil  # noqa: E402

import build_fixtures as bf  # noqa: E402
from build_large_fixture import DEFAULT_OUT, build_large  # noqa: E402
from conftest import SCHEMA_DIR, FakeClock, FakeProcs  # noqa: E402
from roi_mcp.app import App  # noqa: E402
from roi_mcp.config import ServerConfig  # noqa: E402
from roi_mcp.util import json_size  # noqa: E402

ITERATIONS = 10


def calls(world: dict) -> list[tuple[str, dict]]:
    d = world["state"]["data"]
    b0 = d["buildings_player"][4]["key"]
    pf = next(b["key"] for b in d["buildings_player"] if b["prefab"] == "PaintFactory" and b.get("recipe"))
    r0 = d["routes_player"][0]["route_key"]
    shop = d["shops"][0]["building"]
    return [
        ("get_game_status", {}), ("search", {"query": "usine"}), ("search", {"query": "peinture", "kinds": ["product"]}),
        ("list_companies", {}), ("get_company", {}), ("get_finances", {"months": 12}),
        ("list_buildings", {"limit": 50}), ("list_buildings", {"sort": "stock_ratio", "owner": "all", "limit": 50, "fields": "full"}),
        ("get_building", {"building": f"building:{b0}"}), ("get_building", {"building": f"building:{pf}", "include": ["history"]}),
        ("get_production_overview", {}), ("find_production_issues", {"limit": 50}),
        ("list_routes", {"limit": 50}), ("list_routes", {"limit": 50, "fields": "full", "sort": "dispatch_cost"}),
        ("get_route", {"route": f"route:{r0}"}), ("list_warehouse_requests", {"limit": 50}),
        ("list_vehicles", {}), ("list_vehicles", {"aggregate": False, "limit": 50}),
        ("get_supply_chain", {"product": "Paint", "target_output_per_30d": 100}),
        ("get_supply_chain", {"building": f"building:{pf}", "direction": "both"}),
        ("get_supply_chain", {"product": "Paint", "mode": "recipe"}),
        ("list_products", {}), ("get_product", {"product": "Paint"}), ("list_recipes", {}), ("get_recipe", {"recipe": "Paints"}),
        ("list_building_types", {}), ("get_building_type", {"building_type": "PaintFactory"}),
        ("list_cities", {"limit": 50}), ("get_city", {"city": "Ville 03"}), ("get_shop", {"shop": f"building:{shop}"}),
        ("find_shops", {"product": "Paint", "from_building": f"building:{pf}", "limit": 50}),
        ("list_regions", {"limit": 50}), ("get_region", {"region": "Campagne 03"}),
        ("get_market", {}), ("get_tech_tree", {"limit": 50}), ("get_research_state", {}),
    ]


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1)))))
    return s[k]


def main() -> int:
    out = DEFAULT_OUT / "exchange"
    world = build_large()
    bf.write_world(out, world)
    sizes = {f: (out / f"{f}.json").stat().st_size for f in world}
    clock = FakeClock()
    app = App(ServerConfig(exchange_dir=out, schema_dir=SCHEMA_DIR, clock=clock, process_lister=FakeProcs(),
                           file_logging=False, heartbeat_cache_s=1.0))
    proc = psutil.Process()
    rss0 = proc.memory_info().rss
    t0 = time.perf_counter()
    first = asyncio.run(app.call("list_routes", {}))
    cold_ms = (time.perf_counter() - t0) * 1000
    assert first["ok"], first
    lat: dict[str, list[float]] = {}
    sizes_resp: dict[str, int] = {}
    errors = []
    for it in range(ITERATIONS):
        for name, args in calls(world):
            t = time.perf_counter()
            r = asyncio.run(app.call(name, args))
            ms = (time.perf_counter() - t) * 1000
            if not r["ok"]:
                errors.append((name, r["error"]["code"], r["error"]["message"][:100]))
            lat.setdefault(f"{name} {json.dumps(args, sort_keys=True)[:60]}", []).append(ms)
            sizes_resp[name] = max(sizes_resp.get(name, 0), json_size(r))
    # live cadence: the observer publishes a new state between calls (every ~5 s in game); the prefetcher
    # validates it off the call path, so the next call only swaps it in.
    app.start_prefetcher(interval_s=0.25)
    cadence_ms = []
    state_doc = world["state"]
    for i in range(5):
        state_doc = dict(state_doc)
        state_doc["seq"] = state_doc["seq"] + 1
        state_doc["content_hash"] = f"{i:064x}"
        bf.write_json(out / "state.json", state_doc)
        deadline = time.time() + 10
        while time.time() < deadline and "state" not in app.store._prepared:
            time.sleep(0.05)
        t = time.perf_counter()
        r = asyncio.run(app.call("list_routes", {"limit": 50}))
        cadence_ms.append((time.perf_counter() - t) * 1000)
        assert r["ok"] and r["meta"]["snapshot"]["seq"] == state_doc["seq"], r["meta"]
    app.stop_prefetcher()
    all_ms = [v for vs in lat.values() for v in vs]
    rss = proc.memory_info().rss
    d = world["state"]["data"]
    print(f"fixture: player_buildings={len(d['buildings_player'])} routes={len(d['routes_player'])} "
          f"player_vehicles={len(d['vehicles']['vehicles_player'])} ai_buildings={len(d['buildings_ai'])} file_sizes={sizes}")
    print(f"cold first call (load + validate + index all families): {cold_ms:.0f} ms")
    print(f"warm calls: n={len(all_ms)} p50={statistics.median(all_ms):.1f} ms p95={pct(all_ms, 95):.1f} ms max={max(all_ms):.1f} ms")
    worst = sorted(((pct(v, 95), k) for k, v in lat.items()), reverse=True)[:8]
    for p95, k in worst:
        print(f"  p95 {p95:7.1f} ms  {k}")
    print(f"first call after a new 4 MB state.json (prefetcher on): max={max(cadence_ms):.1f} ms "
          f"values={[round(x, 1) for x in cadence_ms]}")
    print(f"max response size: {max(sizes_resp.values())} bytes ({max(sizes_resp, key=sizes_resp.get)})")
    print(f"memory: rss={rss / 1e6:.0f} MB (after load; {rss0 / 1e6:.0f} MB before first call)")
    if errors:
        print("errors:", errors[:10])
    ok = pct(all_ms, 95) <= 500 and max(cadence_ms) <= 500 and rss <= 500e6 and not errors
    print("PERF-9", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
