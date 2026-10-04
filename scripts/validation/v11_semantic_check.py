"""V1.1 semantic check against a snapshot exchange directory (read-only).

Calls the 18 advisor tools through the server's own App and compares the advisor's models with values the game
itself exports in the same snapshots. It only READS the exchange directory (it never writes refresh requests:
`fresh` is never passed). Intended for the supervised V1.1 live validation (docs/v1.1/LIVE-VALIDATION-PLAN.md),
after the user has loaded a validation save.

    uv run --directory mcp-server python ../scripts/validation/v11_semantic_check.py [--exchange-dir DIR] [--out report.json]

Exit code 0 = every hard check passed (model comparisons are reported as measurements, not pass/fail).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "mcp-server" / "src"))

from roi_mcp import config as cfg  # noqa: E402
from roi_mcp.advisor import economics as eco  # noqa: E402
from roi_mcp.advisor.research import formula_value  # noqa: E402
from roi_mcp.app import App  # noqa: E402
from roi_mcp.config import ServerConfig  # noqa: E402
from roi_mcp.tools import ADVISOR_TOOL_NAMES  # noqa: E402
from roi_mcp.util import json_size, normalize_name  # noqa: E402


def stats(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "min": round(min(xs), 4), "median": round(statistics.median(xs), 4), "max": round(max(xs), 4)}


def call(app, name, args):
    t = time.perf_counter()
    r = asyncio.run(app.call(name, args))
    return r, (time.perf_counter() - t) * 1000


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exchange-dir", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--allow-stale", action="store_true", help="read the last snapshots when the game is not live")
    a = ap.parse_args(argv)
    conf = ServerConfig(file_logging=False) if a.exchange_dir is None else ServerConfig(exchange_dir=Path(a.exchange_dir), file_logging=False)
    report = run(App(conf), a.allow_stale)
    text = json.dumps(report, indent=1, ensure_ascii=False)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    if "error" in report:
        return 2
    return 0 if report["passed"] else 1


def run(app: App, allow_stale: bool = False) -> dict:
    stale = {"allow_stale": True} if allow_stale else {}
    report = {"hard_checks": {}, "measurements": {}, "tools": {}}
    hard = report["hard_checks"]

    # 1. every advisor tool answers within the cap
    ov, _ = call(app, "get_overview", stale)
    if not ov["ok"]:
        return {"error": ov["error"], "passed": False}
    state = app.store.families["state"].current.data
    static = app.store.families["static"].current.data if app.store.families["static"].current else {}
    routes = state.get("routes_player") or []
    blds = state.get("buildings_player") or []
    some_route = next((r for r in routes if not r.get("dormant_auto_warehouse")), None)
    prod = next((b.get("recipe") for b in blds if b.get("recipe")), None)
    product = next((res["product"] for rc in static.get("recipes") or [] if rc["name"] == prod for res in rc["results"]), None)
    shop = next((s["building"] for s in state.get("shops") or [] if product in {p["product"] for p in s["products"]}), None)
    producer = next((b["key"] for b in blds if b.get("recipe") == prod), None)
    locked = sorted(u["name"] for u in static.get("tech_unlocks") or []
                    if u["name"] not in set(((state.get("research") or {}).get("player") or {}).get("unlocked") or []) and not u.get("teaser"))
    calls = {"get_overview": {}, "diagnose_chain": {"product": product}, "get_profitability": {}, "find_opportunities": {},
             "review_routes": {"limit": 50}, "plan_chain": {"product": product, "target_per_month": 100},
             "what_if": {"change": {"type": "set_min_keep", "route": "route:" + some_route["route_key"], "value": 1}} if some_route else None,
             "what_changed": {}, "explain_mechanic": {"topic": "max send"}, "how_to": {"action": "change max send"},
             "research_path": {"target": locked[-1]} if locked else None, "suggest_research": {"include_reachable": True},
             "loan_calculator": {"existing": True}, "spatial_analysis": {"mode": "chain", "product": product},
             "route_calculator": {"origin": f"building:{producer}", "destination": f"building:{shop}", "product": product} if producer and shop else None,
             "compare_options": None, "get_chain_graph": {"product": product, "format": "mermaid"}, "forecast": {"kind": "cash"}}
    tool_ok = True
    for name in ADVISOR_TOOL_NAMES:
        args = calls.get(name)
        if args is None:
            report["tools"][name] = {"skipped": "no suitable entity in this save"}
            continue
        for detail in ("summary", "standard", "full"):
            r, ms = call(app, name, {**args, **stale, "detail": detail, "language": "both"})
            ok = r["ok"] and json_size(r) <= cfg.RESPONSE_SIZE_CAP_BYTES
            tool_ok = tool_ok and (ok or r.get("error", {}).get("code") in ("not_found",))
            report["tools"][f"{name}:{detail}"] = {"ok": r["ok"], "bytes": json_size(r), "ms": round(ms, 1),
                                                   "error": (r.get("error") or {}).get("code"),
                                                   "confidence": (r.get("data") or {}).get("confidence")}
    hard["all_tools_answer_under_cap"] = tool_ok

    # 2. server dispatch replica == observer replica on every route (no change applied)
    mism = [r["route_key"] for r in routes
            if eco.dispatch_amount((r.get("dispatch_amount_now") or {}).get("inputs") or {},
                                   destination_stock=r.get("destination_stock"),
                                   destination_incoming=r.get("destination_incoming_reserved"))["value"]
            != (r.get("dispatch_amount_now") or {}).get("value")]
    hard["dispatch_replica_matches_observer"] = not mism
    report["measurements"]["dispatch_replica_mismatches"] = mism[:20]

    # 3. cycle-time model (D-ADV-NEWRATE-1 static basis) vs observed effective cycle, buildings at the initial index
    bt = {b["name"]: b for b in static.get("building_types") or []}
    rc = {r["name"]: r for r in static.get("recipes") or []}
    ratios, upkeep_ratios = [], []
    for b in blds:
        t, r = bt.get(b.get("prefab")), rc.get(b.get("recipe"))
        idx = (b.get("efficiency") or {}).get("index")
        if t and r and b.get("cycle_days_effective") and idx == t.get("initial_efficiency_index"):
            eff = (t.get("efficiency_output") or [None] * 8)[idx]
            pred = r["game_days"] / ((t.get("production_speed") or 1.0) * (eff or 1.0))
            ratios.append(b["cycle_days_effective"] / pred)
        if t and idx == t.get("initial_efficiency_index") and not (b.get("modules") or {}).get("count") and (b.get("upkeep") or {}).get("monthly_full"):
            pred = eco.new_building_upkeep(t, None, 0, ((state.get("session") or {}).get("difficulty") or {}).get("upkeep"))["value"]
            if pred:
                upkeep_ratios.append(b["upkeep"]["monthly_full"] / pred)
    report["measurements"]["cycle_observed_over_static_model"] = stats(ratios)
    report["measurements"]["upkeep_observed_over_model"] = stats(upkeep_ratios)

    # 4. profitability estimate vs the game's own product statistics
    prof, _ = call(app, "get_profitability", {**stale, "limit": 25})
    pairs = []
    for p in (prof.get("data") or {}).get("result", {}).get("products", []):
        gs = p.get("game_stats")
        if gs:
            pairs.append({"product": p["product"]["id"], "game_profit_per_unit": gs["profit_per_unit_sold"]["value"],
                          "advisor_margin_per_unit": p["estimate"]["margin_per_unit"]["value"]})
    report["measurements"]["profitability_vs_game_stats"] = pairs

    # 5. research formula leave-one-out vs the game's costed nodes
    pr = (state.get("research") or {}).get("player") or {}
    forms = {f["name"]: f.get("text") for f in static.get("formulas") or []}
    un = {u["name"]: u for u in static.get("tech_unlocks") or []}
    res = []
    for c in pr.get("costs") or []:
        u = un.get(c["unlock"]) or {}
        fd = formula_value(forms.get(u.get("research_time_formula")), u.get("tier"), pr.get("efficiency"))
        if fd and c.get("days"):
            res.append(c["days"] / fd)
    report["measurements"]["research_days_game_over_formula"] = stats(res)

    # 6. straight-line honesty and detour factors
    hard["route_calculator_never_claims_path_distance"] = True
    if calls.get("route_calculator"):
        rcalc, _ = call(app, "route_calculator", {**calls["route_calculator"], **stale})
        hard["route_calculator_never_claims_path_distance"] = rcalc["ok"] and rcalc["data"]["result"]["straight_line"]["is_path_distance"] is False
    coords = {b["key"]: (b.get("x"), b.get("y")) for b in blds}
    det = []
    for r in routes:
        o, d = coords.get(r["origin"]), coords.get(r["destination"])
        if o and d and None not in o + d and r.get("distance_tiles"):
            e = ((o[0] - d[0]) ** 2 + (o[1] - d[1]) ** 2) ** 0.5
            if e >= 5:
                det.append(r["distance_tiles"] / e)
    report["measurements"]["detour_factor_path_over_straight_line"] = stats(det)
    hard["route_path_range_not_below_straight_line"] = True
    if calls.get("route_calculator"):
        rr = rcalc["data"]["result"] if rcalc.get("ok") else {}
        rng, eu = rr.get("path_tiles_range_estimate"), ((rr.get("straight_line") or {}).get("euclidean") or {}).get("value")
        if rng and eu:
            hard["route_path_range_not_below_straight_line"] = rng["min"]["value"] >= 0.95 * eu
            report["measurements"]["route_path_range_min_over_straight_line"] = round(rng["min"]["value"] / eu, 4)

    # 6b. new-building economics (what_if add_buildings) vs the player's own buildings at the type's initial
    # efficiency index with a full module set (live validation V1.1: company modifiers, module upkeep, module limit)
    truth = {}
    for b in blds:
        t, r0 = bt.get(b.get("prefab")), rc.get(b.get("recipe"))
        m = b.get("modules") or {}
        if not t or not r0 or b.get("is_module") or not b.get("cycle_days_effective"):
            continue
        if (b.get("efficiency") or {}).get("index") != t.get("initial_efficiency_index") or (m and m.get("count") != m.get("max")):
            continue
        if any(abs((x.get("speed_replica") or 1.0) - 1.0) > 1e-6 for x in m.get("items") or []):
            continue
        rate = r0["results"][0]["amount"] * 30 / b["cycle_days_effective"] * (m.get("count") or 1)
        truth.setdefault((b["prefab"], b["recipe"]), (rate, (b.get("upkeep") or {}).get("monthly_full")))
    mism_nb = []
    for (prefab, recipe), (rate, upk) in sorted(truth.items()):
        w, _ = call(app, "what_if", {**stale, "change": {"type": "add_buildings", "recipe": recipe, "count": 1}})
        if not w["ok"] or w["data"]["result"]["scenario"]["building_type"] != "building_type:" + prefab:
            continue
        pr = w["data"]["result"]["scenario"]["rate_per_building"]["value"]
        pu = w["data"]["result"]["deltas"]["upkeep_per_30d"]["value"]
        if pr is None or abs(pr - rate) > 0.01 * rate or (upk and (pu is None or abs(pu - upk) > 0.01 * upk)):
            mism_nb.append({"building_type": prefab, "recipe": recipe, "rate": [rate, pr], "upkeep": [upk, pu]})
    hard["new_building_economics_match_player_buildings"] = not mism_nb
    report["measurements"]["new_building_comparisons"] = {"n": len(truth), "mismatches": mism_nb[:20]}

    # 7. names: catalogue language and the curated glossary
    names = [(p.get("display_name"), p.get("english_name")) for p in static.get("products") or []]
    report["measurements"]["catalogue_language"] = static.get("language")
    report["measurements"]["products_with_distinct_display_name"] = sum(1 for d, e in names if d and e and d != e)
    petro = (bt.get("PetrochemicalFactory") or {}).get("display_name")
    report["measurements"]["glossary_petrochemical_fr"] = {"catalogue": petro, "glossary": "Usine pétrochimique",
                                                           "match": bool(petro) and normalize_name(petro) == normalize_name("Usine pétrochimique")}
    report["passed"] = all(hard.values())
    return report


if __name__ == "__main__":
    sys.exit(main())
