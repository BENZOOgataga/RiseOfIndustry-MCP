"""Adversarial edge cases for the advisor tools (release-candidate step 8): empty worlds, missing families, invalid files,
zero/negative values, missing names, unknown ids, determinism. Insufficient evidence must give null/unavailable,
never a plausible default."""

from __future__ import annotations

import copy
import json

import pytest

import build_fixtures as bf
from advisor.helpers import VALID_CALLS, check_advisor_response
from advisor.worlds import load, world_with
from conftest import Harness
from roi_mcp.tools import ADVISOR_TOOL_NAMES

RUNTIME = [n for n in ADVISOR_TOOL_NAMES if n not in ("explain_mechanic", "how_to")]


def v(q):
    return None if q is None else q["value"]


def empty_company(d):
    d["buildings_player"] = []
    d["routes_player"] = []
    d["requests_player"] = []
    d["vehicles"]["vehicles_player"] = []
    d["vehicles"]["fleets_player"] = []
    d["vehicles"]["groups"] = []


EMPTY_CALLS = dict(VALID_CALLS)
EMPTY_CALLS.update({"route_calculator": {"origin": f"building:{bf.AI_PF}", "destination": f"building:{bf.S_HW2}", "product": "Paint"},
                    "spatial_analysis": {"mode": "matrix", "locations": ["city:10", "city:11"]},
                    "what_if": {"change": {"type": "add_buildings", "recipe": "Paints"}},
                    "compare_options": {"kind": "recipes", "options": ["Paints", "PaintsAdvanced"]}})


@pytest.mark.parametrize("name", ADVISOR_TOOL_NAMES)
def test_empty_company_never_crashes_and_never_invents(exchange, clock, procs, name):
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(empty_company))
    r = hh.call(name, EMPTY_CALLS[name])
    check_advisor_response(name, r)
    assert r["ok"] or r["error"]["code"] in ("not_found", "invalid_argument", "section_unavailable"), r["error"]
    if name == "diagnose_chain":
        assert r["data"]["result"]["findings"][0]["kind"] == "no_producer"
    if name == "review_routes":
        assert r["data"]["result"]["summary"]["routes_reviewed"] == 0 and r["data"]["result"]["findings"] == []
    if name == "get_profitability":
        assert r["data"]["result"]["products"] == []
    if name == "plan_chain":
        steps = r["data"]["result"]["steps"]
        assert all(v(s["from_existing_per_30d"]) == 0 for s in steps)
        paint = next(s for s in steps if s["product"]["id"] == "product:Paint")
        assert paint["rate_per_building"]["basis"] == "static_recipe"           # no peer: static recipe, flagged
    if name == "get_overview":
        assert r["data"]["result"]["buildings"]["count"] == 0                  # observed empty list, not unknown


def test_missing_static_family(exchange, clock, procs):
    hh = Harness(exchange, clock, procs)
    w = world_with()
    hh.write_world(w, ("heartbeat", "state", "history"))
    for name in ("diagnose_chain", "plan_chain", "find_opportunities", "get_profitability", "research_path", "compare_options",
                 "get_chain_graph", "route_calculator"):
        r = hh.call(name, VALID_CALLS[name])
        assert r["error"]["code"] == "snapshot_unavailable", (name, r)
        check_advisor_response(name, r)
    ov = hh.call("get_overview")
    assert ov["ok"] and "definition" in {u["field"] for u in ov["data"]["unavailable"]}
    assert hh.call("explain_mechanic", {"topic": "upkeep"})["ok"]


def test_schema_invalid_state(exchange, clock, procs):
    hh = Harness(exchange, clock, procs)
    w = world_with()
    hh.write_world(w)
    hh.write_raw("state", json.dumps({**w["state"], "data": {"session": "broken"}}))
    for name in RUNTIME:
        r = hh.call(name, VALID_CALLS[name])
        assert r["error"]["code"] == "snapshot_unavailable", (name, r["error"])
    hh2 = Harness(exchange, clock, procs)
    hh2.write_world(w)
    assert hh2.call("get_overview")["ok"]
    hh2.write_raw("state", "{not json")
    r = hh2.call("get_overview")
    assert r["ok"] and "snapshot_invalid_using_previous" in [x["code"] for x in r["meta"]["warnings"]]


@pytest.mark.parametrize("name,args,code", [
    ("what_if", {"change": {"type": "set_max_send", "route": "Route:x|y", "value": 1}}, "not_found"),
    ("what_if", {"change": {"type": "set_max_send", "route": f"route:{bf.B_GW1.lower()}|Gas|{bf.B_PC1}|own|0", "value": 1}}, "not_found"),
    ("what_if", {"change": {"type": "remove_building", "building": f"BUILDING:{bf.B_PC1}"}}, "not_found"),
    ("what_if", {"change": {"type": "remove_building", "building": "vehicle:other-session:5"}}, "stale_reference"),
    ("research_path", {"target": "Tech:Polymers"}, "not_found"),
    ("forecast", {"kind": "stock", "building": f"building:{bf.B_PF1}", "product": "product:Unobtainium"}, "not_found"),
    ("spatial_analysis", {"mode": "matrix", "locations": ["region:not-a-guid"]}, "not_found"),
    ("compare_options", {"kind": "research", "options": ["tech:Polymers", "tech:Nope"]}, "not_found"),
    ("get_chain_graph", {"product": "Peinture "}, "ok"),                        # names are trimmed and accent-folded
])
def test_malformed_and_wrong_ids(h, name, args, code):
    r = h.call(name, args)
    if code == "ok":
        assert r["ok"], r
    else:
        assert r["error"]["code"] == code, r


# ------------------------------------------------------------------ zero / negative / extreme values

def test_zero_and_negative_cash(exchange, clock, procs):
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(lambda d: d["companies"][0]["cash"].__setitem__("value", 0.0)))
    f = hh.call("forecast", {"kind": "cash"})["data"]["result"]
    assert f["already_negative"] is True and v(f["months_to_zero_pessimistic"]) is None
    lc = hh.call("loan_calculator", {"loan": "BankLoan"})["data"]["result"]["loan"]["cash_flow"]
    assert v(lc["months_covered_by_cash"]) == 0
    load(hh, world_with(lambda d: d["companies"][0]["cash"].__setitem__("value", -5000.0)))
    att = hh.call("get_overview")["data"]["result"]["attention"]
    assert att[0]["kind"] == "negative_cash" and att[0]["severity"] == "high"


def test_zero_production_and_zero_demand(exchange, clock, procs):
    def mutate(d):
        for b in d["buildings_player"]:
            if b.get("production"):
                b["production"]["produced_last_month"] = 0
        for s in d["shops"]:
            for p in s["products"]:
                p["demand_for_player"] = 0
                p["demand_raw"] = 0
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(mutate))
    prof = hh.call("get_profitability", {"product": "Chemicals"})["data"]["result"]["products"][0]["estimate"]
    assert v(prof["volume_per_30d"]) == 0 and v(prof["margin_per_30d"]) == 0
    assert prof["sale_price"]["basis"] == "market_final"                         # no shop with demand: market fallback, flagged
    assert "price_fallback" in prof["sale_price"]["confidence"]["factors"]
    opp = hh.call("find_opportunities", {"types": ["route_surplus", "expand_production", "new_product"]})["data"]["result"]
    assert opp["opportunities"] == []
    sug = hh.call("suggest_research", {})["data"]["result"]["suggestions"]
    top = next(s for s in sug if s["tech"] == "tech:AdvancedPaints")
    assert v(top["components"]["demand_value_per_30d"]) == 0 and v(top["score"]) == 0


def test_zero_capacity_and_missing_inputs_give_null(exchange, clock, procs):
    def mutate(d):
        for r in d["routes_player"]:
            r["vehicle_capacity"] = 0
            r["dispatch_amount_now"]["inputs"]["vehicle_capacity"] = 0
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(mutate))
    rr = hh.call("review_routes", {"limit": 50})["data"]["result"]
    assert "high_unit_cost" not in rr["summary"]["findings_by_kind"]             # capacity 0: cost per unit not computed
    rc = hh.call("route_calculator", VALID_CALLS["route_calculator"])["data"]["result"]
    assert v(rc["cost_per_unit_at_capacity"]) is None and v(rc["vehicle_capacity"]) is None


def test_dispatch_replica_never_treats_unknown_as_zero():
    from roi_mcp.advisor import economics as eco
    inputs = {"vehicle_capacity": 10, "origin_stock": 12, "min_keep": None, "free_space": 27, "max_send": 8, "destination_slots": 40}
    assert eco.dispatch_amount(inputs)["value"] is None and "min_keep" in eco.dispatch_amount(inputs)["reason"]
    assert eco.dispatch_amount({**inputs, "min_keep": 0, "max_send": None})["value"] is None
    assert eco.dispatch_amount({**inputs, "min_keep": 0}, max_send=0)["value"] == 10            # 0 really means unlimited


def test_unlimited_max_send_round_trip(h):
    r = h.call("what_if", {"change": {"type": "set_max_send", "route": f"route:{bf.B_GW1}|Gas|{bf.B_PC1}|own|0", "value": 0}})
    res = r["data"]["result"]
    assert res["scenario"]["max_send_unlimited"] is True
    rows = {x["route_id"]: x for x in res["deltas"]["routes"]}
    assert v(rows[f"route:{bf.B_GW1}|Gas|{bf.B_PC1}|own|0"]["scenario"]) == 10      # capacity-bound once unlimited


def test_extreme_values_stay_finite(exchange, clock, procs):
    def mutate(d):
        d["companies"][0]["cash"]["value"] = 1e15
        for s in d["shops"]:
            for p in s["products"]:
                p["price_for_player"] = 1e9
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(mutate))
    for name in ("get_overview", "find_opportunities", "get_profitability", "suggest_research", "forecast"):
        r = hh.call(name, VALID_CALLS[name])
        assert r["ok"] and "NaN" not in json.dumps(r) and "Infinity" not in json.dumps(r)
        check_advisor_response(name, r)
    p = hh.call("plan_chain", {"product": "Paint", "target_per_month": 1e6, "existing_capacity": "none"})["data"]["result"]
    assert p["totals"]["buildings"] > 100000 and v(p["totals"]["capex"]) > 0


# ------------------------------------------------------------------ names and determinism

def test_missing_english_and_display_names(exchange, clock, procs):
    hh = Harness(exchange, clock, procs)
    w = world_with()
    for p in w["static"]["data"]["products"]:
        if p["name"] == "Paint":
            p["english_name"] = None
            p["display_name"] = None
    w["static"]["content_hash"] = bf.content_hash(w["static"]["data"])
    w["state"]["static_ref"] = bf.static_ref_of(w["static"])
    w["history"]["static_ref"] = bf.static_ref_of(w["static"])
    w["heartbeat"] = bf.build_heartbeat(static_doc=w["static"], state_doc=w["state"], history_doc=w["history"])
    load(hh, w)
    for lang, name, src in (("en", "Paint", "asset"), ("fr", "Paint", "fallback_en")):
        p = hh.call("diagnose_chain", {"product": "Paint", "language": lang})["data"]["result"]["product"]
        assert p["id"] == "product:Paint" and p["name"] == name and p["name_source"] == src
    both = hh.call("diagnose_chain", {"product": "Paint", "language": "both"})["data"]["result"]["product"]
    assert both["name_fr"] is None and both["name_fr_source"] == "unavailable"


@pytest.mark.parametrize("name", ADVISOR_TOOL_NAMES)
def test_repeated_calls_are_deterministic(h, name):
    a = h.call(name, VALID_CALLS[name])
    b = h.call(name, VALID_CALLS[name])
    assert a["data"] == b["data"], name


def test_ordering_is_stable_under_input_permutation(h):
    locs = [f"building:{bf.B_GW1}", f"building:{bf.B_PF1}", f"building:{bf.S_HW1}"]
    a = h.call("spatial_analysis", {"mode": "hub", "locations": locs})["data"]["result"]["ranking"]
    b = h.call("spatial_analysis", {"mode": "hub", "locations": list(reversed(locs))})["data"]["result"]["ranking"]
    assert [x["id"] for x in a] == [x["id"] for x in b]
    c1 = h.call("compare_options", {"kind": "recipes", "options": ["Paints", "PaintsAdvanced"]})["data"]["result"]["differences"]
    c2 = h.call("compare_options", {"kind": "recipes", "options": ["PaintsAdvanced", "Paints"]})["data"]["result"]["differences"]
    assert [(d["metric"], d["best_option"]) for d in c1] == [(d["metric"], d["best_option"]) for d in c2]


def test_disconnected_graph_product(exchange, clock, procs):
    def mutate(d):
        d["routes_player"] = []
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(mutate))
    g = hh.call("get_chain_graph", {"product": "Paint"})["data"]["result"]
    assert g["counts"]["observed_edges"] > 0 and not any(e["relation"] == "ships" for e in g["edges"])
    d = hh.call("diagnose_chain", {"product": "Paint"})["data"]["result"]
    assert any(f["kind"] == "no_inbound_source" for f in d["findings"])


def test_world_with_only_ai_routes_hidden(exchange, clock, procs):
    # advisor tools analyse the player only: AI routes in the optional section never appear
    def mutate(d):
        d["routes_ai"] = [copy.deepcopy(d["routes_player"][0]) | {"route_key": "AI|x|y|own|0", "origin": bf.AI_PF}]
    hh = Harness(exchange, clock, procs)
    w = world_with(mutate)
    w["state"]["sections"]["routes_ai"] = bf.section_status(1512, 1)
    load(hh, w)
    rr = hh.call("review_routes", {"limit": 50})["data"]["result"]
    assert rr["summary"]["routes_reviewed"] == 13


def test_shared_max_send_room_is_not_additive(h):
    """Live validation (V1.1 LV-dispatch): two wells share one Max Send room on the same destination; each route alone
    could dispatch, but together they cannot exceed the room left."""
    from roi_mcp.advisor import tools_scenario as ts
    route = {"dispatch_amount_now": {"inputs": {"destination_slots": 100, "free_space": 92}}}
    conf = {"level": "high", "factors": []}
    rows = [{"scenario": {"value": 1, "confidence": conf}}, {"scenario": {"value": 1, "confidence": conf}}]
    s = ts._shared_room(route, rows, 9)
    assert v(s["max_send_room_scenario"]) == 1 and v(s["sum_of_route_amounts"]) == 2 and v(s["combined_max_next_dispatches"]) == 1
    assert v(ts._shared_room(route, rows, 10)["combined_max_next_dispatches"]) == 2
    unlimited = ts._shared_room(route, rows, 0)
    assert unlimited["max_send_room_scenario"] is None and v(unlimited["combined_max_next_dispatches"]) == 2
    rows[1]["scenario"]["value"] = None
    assert v(ts._shared_room(route, rows, 9)["combined_max_next_dispatches"]) is None   # unknown stays unknown
    assert v(ts._shared_room({"dispatch_amount_now": {"inputs": {}}}, rows[:1], 9)["combined_max_next_dispatches"]) is None


# Live validation (V1.1 LV-modifiers): the validation world showed company modifiers that are not exported
# (oil buildings x0.75 output, x1.2 upkeep) and module upkeep percentages missing from the catalogue.

def _petro(d, upkeeps, efficiency=None):
    pcs = [b for b in d["buildings_player"] if b["prefab"] == "PetrochemicalFactory"]
    for b, u in zip(pcs, upkeeps):
        b["upkeep"]["monthly_full"] = u
        b["efficiency"] = dict(efficiency or {"kind": "building", "index": 3, "output_multiplier": 0.75, "upkeep_multiplier": 1.0})


def _add_chemicals(h):
    r = h.call("what_if", {"change": {"type": "add_buildings", "recipe": "Chemicals", "count": 1}})
    return r["data"]["result"]["deltas"]["upkeep_per_30d"], r["data"]


def test_measured_company_upkeep_modifier_is_used(exchange, clock, procs):
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(lambda d: _petro(d, [12000.0, 12000.0])))
    upk, data = _add_chemicals(hh)
    assert v(upk) == 12000 and "mechanic_unverified" not in upk["confidence"]["factors"]   # 10000 x measured 1.2
    assert "A-ACTOR-MODIFIER-1" in [a["id"] for a in data["assumptions"]]


def test_disagreeing_company_modifiers_are_not_used(exchange, clock, procs):
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(lambda d: _petro(d, [12000.0, 10000.0])))
    upk, _ = _add_chemicals(hh)
    assert v(upk) == 10000 and "mechanic_unverified" in upk["confidence"]["factors"] and upk["confidence"]["level"] == "low"


def test_type_modifiers_divide_out_the_building_efficiency(exchange, clock, procs):
    eff4 = {"kind": "building", "index": 4, "output_multiplier": None, "upkeep_multiplier": None}
    static = bf.static_data()
    bt = next(b for b in static["building_types"] if b["name"] == "PetrochemicalFactory")
    o4, u4 = bt["efficiency_output"][4], bt["efficiency_upkeep"][4]
    eff4.update(output_multiplier=0.75 * o4, upkeep_multiplier=u4)
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(lambda d: _petro(d, [12000.0 * u4, 12000.0 * u4], eff4)))
    upk, _ = _add_chemicals(hh)
    # observed 12000 x u4 at index 4 -> modifier 1.2 once u4 is divided out; a new building starts at the initial index
    assert v(upk) == 12000 and "mechanic_unverified" not in upk["confidence"]["factors"]


def test_static_rate_uses_measured_output_modifier():
    from roi_mcp.advisor import economics as eco
    static = bf.static_data()
    bt = next(b for b in static["building_types"] if b["name"] == "PetrochemicalFactory")
    rc = next(r for r in static["recipes"] if r["name"] == "Chemicals")
    r = eco.new_building_rate(rc, bt, (), {"value": 0.75, "peers": 2})
    assert r["per_30d"] == {"Chemicals": pytest.approx(2.25)} and r["inputs"]["company_output_modifier_basis"] == "observed_same_type"
    assumed = eco.new_building_rate(rc, bt)
    assert assumed["per_30d"] == {"Chemicals": 3} and "mechanic_unverified" in assumed["confidence"]["factors"]


def test_missing_module_upkeep_pct_uses_owner_pct_as_assumption():
    from roi_mcp.advisor import economics as eco
    static = bf.static_data()
    owner = next(b for b in static["building_types"] if b["name"] == "GasWell")
    module = dict(next(b for b in static["building_types"] if b["name"] == "GasPump"), upkeep_cost_percentage=None)
    u = eco.new_building_upkeep(owner, module, 3, 1.0, {"value": 1.0, "peers": 2})
    pct = owner["upkeep_cost_percentage"]
    assert u["value"] == pytest.approx((owner["base_cost"] + 3 * module["base_cost"]) * pct)
    assert u["assumptions"] == ["A-MODULE-UPKEEP-PCT"]
    assert eco.new_building_upkeep(owner, dict(module, base_cost=None), 3, 1.0)["value"] is None    # unknown stays unknown


def test_prebuilt_gatherer_kind_other_uses_module_rate(exchange, clock, procs):
    """Live validation: map-prebuilt gatherers arrive with kind "other" (tag PrebuiltGatherer); their rate must come
    from their modules (D-RATE-2), not the factory formula."""
    def mutate(d):
        b = next(x for x in d["buildings_player"] if x["key"] == "GasWell@24,36")
        b["kind"], b["tags"] = "other", ["PrebuiltGatherer"]
    base = Harness(exchange, clock, procs)
    load(base, world_with())
    want = base.call("get_building", {"building": "building:GasWell@24,36"})["data"]["production"]["theoretical_output_per_30d"]
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(mutate))
    got = hh.call("get_building", {"building": "building:GasWell@24,36"})["data"]["production"]["theoretical_output_per_30d"]
    assert got["method"] == "D-RATE-2" and got["outputs"] == want["outputs"]
    # a module-less "other" building (e.g. pollution management) keeps the factory path
    from roi_mcp.tools.common import theoretical_rate
    from roi_mcp.index import StaticIndex
    six = StaticIndex(bf.static_data())
    assert theoretical_rate(six, {"kind": "other", "recipe": None, "modules": None})["method"] == "D-RATE-1"
    # only the PrebuiltGatherer tag triggers the gatherer path; another "other" building with modules is unchanged
    gw = next(b for b in bf.state_data()["buildings_player"] if b["key"] == "GasWell@24,36")
    untagged = {**gw, "kind": "other", "tags": ["PollutionManagement"]}
    assert theoretical_rate(six, untagged)["method"] == "D-RATE-1"
    assert theoretical_rate(six, {**untagged, "tags": ["PrebuiltGatherer"]})["method"] == "D-RATE-2"


def test_set_efficiency_reports_the_effective_multiplier_with_company_modifier(exchange, clock, procs):
    """Live validation (USINE PETROCHIMIQUE 2, index 3 -> 4): the game showed 0.75 -> 0.9375, not the array's 1 -> 1.25."""
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(lambda d: _petro(d, [10000.0, 10000.0])))
    static = bf.static_data()
    eo = next(b for b in static["building_types"] if b["name"] == "PetrochemicalFactory")["efficiency_output"]
    r = hh.call("what_if", {"change": {"type": "set_efficiency", "building": "building:PetrochemicalFactory@40,32", "index": 4}})
    res = r["data"]["result"]
    assert v(res["baseline"]["effective_output_multiplier"]) == 0.75
    assert v(res["scenario"]["effective_output_multiplier"]) == pytest.approx(0.75 * eo[4] / eo[3])
    assert v(res["scenario"]["output_multiplier"]) == eo[4]          # the array value is still reported, labelled definition


def test_state_trading_routes_are_not_detour_samples(exchange, clock, procs):
    """Live validation: routes to state trading measure their path to a trade point (ratios 0.24-0.40 against the
    destination coordinates); they must not widen the detour range."""
    args = {"origin": f"building:{bf.B_PF1}", "destination": f"building:{bf.S_HW2}", "product": "Paint"}
    base = Harness(exchange, clock, procs)
    load(base, world_with())
    want = base.call("route_calculator", args)["data"]["result"]["path_tiles_range_estimate"]

    def mutate(d):
        for i, o in enumerate((bf.B_PF1, bf.B_GW1, bf.B_PC1)):
            r = bf._route(o, "TradingPost@5,5", "Paint", kind="state_trading", dest_owner=bf.STATE_ACTOR, distance=2, slot_index=5 + i)
            d["routes_player"].append(r)
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(mutate))
    got = hh.call("route_calculator", args)["data"]["result"]["path_tiles_range_estimate"]
    assert got == want


def test_opportunity_caveats_flag_unbuilt_chains_and_long_payback(h):
    """Live validation: the top opportunity needed three unproduced inputs and paid back in 31 months; the ranking
    stays by monthly margin but such rows must say so."""
    r = h.call("find_opportunities", {"include_locked": True, "include_unviable": True, "limit": 50})
    rows = r["data"]["result"]["opportunities"]
    assert rows and all(isinstance(o["caveats"], list) for o in rows)
    for o in rows:
        ev = o.get("evidence") or {}
        unbuilt = bool(ev.get("inputs_not_produced")) and not ev.get("inputs_produced_by_player")
        assert ("needs_new_supply_chain" in o["caveats"]) == unbuilt, o["product"]
        pb = v(o.get("payback_months"))
        assert ("long_payback" in o["caveats"]) == (pb is not None and pb > 24), o["product"]


def _add(h, recipe):
    res = h.call("what_if", {"change": {"type": "add_buildings", "recipe": recipe, "count": 1}})["data"]["result"]
    return res["scenario"]["rate_per_building"], res["deltas"]


def test_peer_rates_are_normalised_to_the_initial_efficiency(exchange, clock, procs):
    """Live validation: a peer running at 125 % must not make a new building (initial index) look 25 % faster."""
    base = Harness(exchange, clock, procs)
    load(base, world_with())
    want, _ = _add(base, "Chemicals")
    eo = next(b for b in bf.static_data()["building_types"] if b["name"] == "PetrochemicalFactory")["efficiency_output"]

    def faster(d):
        for b in d["buildings_player"]:
            if b["prefab"] == "PetrochemicalFactory" and b.get("recipe") == "Chemicals":
                b["efficiency"] = {"kind": "building", "index": 4, "output_multiplier": eo[4], "upkeep_multiplier": eo[4]}
                b["cycle_days_effective"] = b["cycle_days_effective"] * eo[3] / eo[4]
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(faster))
    got, _ = _add(hh, "Chemicals")
    assert want["basis"] == got["basis"] == "observed_peer"
    assert v(got) == pytest.approx(v(want))


def test_new_hubs_use_the_players_observed_module_limit(exchange, clock, procs):
    """Live validation: the catalogue said 3 modules, every hub reported modules.max = 5 after research."""
    base = Harness(exchange, clock, procs)
    load(base, world_with())
    r3, d3 = _add(base, "Gas")

    def five(d):
        for b in d["buildings_player"]:
            if b["prefab"] == "GasWell" and b.get("modules"):
                b["modules"]["max"] = 5
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(five))
    r5, d5 = _add(hh, "Gas")
    static = bf.static_data()
    well = next(b for b in static["building_types"] if b["name"] == "GasWell")
    pump = next(b for b in static["building_types"] if b["name"] == well["module_prefab"])
    assert well["max_module_count"] != 5
    assert v(r5) == pytest.approx(v(r3) * 5 / well["max_module_count"])
    assert v(d5["capex"]) - v(d3["capex"]) == pytest.approx((5 - well["max_module_count"]) * pump["base_cost"])


def test_overview_explains_production_deficits(h):
    """Live validation: deficits include world-wide shop demand and were read as chain shortages."""
    en = h.call("get_overview", {"language": "en"})["data"]["result"]["production"]
    assert "every live shop in the world" in en["note"] and "not an input shortage" in en["note"]
    fr = h.call("get_overview", {"language": "fr"})["data"]["result"]["production"]
    assert "tous les magasins actifs du monde" in json.dumps(fr, ensure_ascii=False)


def test_zero_dispatch_held_by_max_send_is_not_called_a_fault():
    """Release review: a route held by Max Send while deliveries are on the way is normal; the wording says so."""
    from roi_mcp.advisor import economics as eco
    base = {"dispatch_amount_now": {"value": 0, "limited_by": ["max_send"], "complete": True}, "vehicle_capacity": 1,
            "min_keep": {"value": 0}, "max_send": {"value": 10, "headroom_now": 0}}
    z = next(f for f in eco.route_findings(base, None, 0.2, 2) if f["kind"] == "zero_dispatch_now")
    assert "normal while vehicles are in transit" in z["suggestion"] and "problem only if it persists" in z["suggestion"]
    other = {**base, "dispatch_amount_now": {"value": 0, "limited_by": ["free_space"], "complete": True}}
    z2 = next(f for f in eco.route_findings(other, None, 0.2, 2) if f["kind"] == "zero_dispatch_now")
    assert "in transit" not in z2["suggestion"]
