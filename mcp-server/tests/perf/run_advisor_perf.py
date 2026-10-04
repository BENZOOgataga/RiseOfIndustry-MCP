"""Latency benchmark of the advisor tools on the large synthetic fixture (not collected by pytest).

    uv run --directory mcp-server python tests/perf/run_advisor_perf.py [iterations]

Builds the PERF-9 large fixture (~3x the sample save) in a temporary exchange directory, then calls every advisor
tool `iterations` times (default 30) after one warm-up call and prints median / p95 / max in milliseconds.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "fixtures"), str(HERE.parent.parent / "src"), str(HERE)]

import build_fixtures as bf  # noqa: E402
import build_large_fixture as blf  # noqa: E402
from conftest import FakeClock, FakeProcs, Harness  # noqa: E402
from advisor.helpers import VALID_CALLS, WHAT_IF_CALLS  # noqa: E402
from roi_mcp.tools import ADVISOR_TOOL_NAMES  # noqa: E402
from roi_mcp.util import json_size  # noqa: E402


def main(argv: list[str]) -> int:
    n = int(argv[0]) if argv else 30
    world = blf.build_large()
    st = world["state"]["data"]
    with tempfile.TemporaryDirectory() as tmp:
        ex = Path(tmp) / "RoiMcp"
        ex.mkdir()
        h = Harness(ex, FakeClock(), FakeProcs())
        h.write_world(world)
        paint = next(b["key"] for b in st["buildings_player"] if b.get("recipe") == "Paints")
        shops = [s["building"] for s in st["shops"] if any(p["product"] == "Paint" for p in s["products"])]
        keys = [f"building:{b['key']}" for b in st["buildings_player"]]
        calls = dict(VALID_CALLS)
        calls.update({"review_routes": {"limit": 50}, "find_opportunities": {"limit": 50, "include_unviable": True},
                      "get_profitability": {"limit": 25}, "plan_chain": {"product": "Paint", "target_per_month": 1e5},
                      "route_calculator": {"origin": f"building:{paint}", "destination": f"building:{shops[-1]}", "product": "Paint"},
                      "compare_options": {"kind": "shop_destinations", "origin": f"building:{paint}", "product": "Paint",
                                          "options": [f"building:{x}" for x in shops[:8]]},
                      "spatial_analysis": {"mode": "hub", "locations": keys[:30], "candidates": keys[:20]},
                      "get_chain_graph": {"product": "Paint", "depth": 8}, "suggest_research": {"include_reachable": True, "max_chain": 6}})
        counts = {"player_buildings": len(st["buildings_player"]), "routes": len(st["routes_player"]),
                  "ai_buildings": len(st["buildings_ai"]), "shops": len(st["shops"])}
        print("fixture:", json.dumps(counts))
        t0 = time.perf_counter()
        h.call("get_overview")
        print(f"first call (parse + validate the large state): {(time.perf_counter() - t0) * 1000:.0f} ms")
        rows = []
        for name in list(ADVISOR_TOOL_NAMES) + ["what_if:" + c["type"] for c in WHAT_IF_CALLS]:
            tool, args = (name, calls.get(name)) if not name.startswith("what_if:") else \
                ("what_if", {"change": next(c for c in WHAT_IF_CALLS if c["type"] == name.split(":")[1])})
            h.call(tool, args)
            ts = []
            for _ in range(n):
                t = time.perf_counter()
                r = h.call(tool, args)
                ts.append((time.perf_counter() - t) * 1000)
            ts.sort()
            rows.append((name, statistics.median(ts), ts[int(0.95 * (len(ts) - 1))], ts[-1], json_size(r), r["ok"]))
        print(f"{'tool':32s} {'median':>8s} {'p95':>8s} {'max':>8s} {'bytes':>7s}")
        for name, med, p95, mx, size, ok in rows:
            print(f"{name:32s} {med:8.1f} {p95:8.1f} {mx:8.1f} {size:7d} {'' if ok else 'ERROR'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
