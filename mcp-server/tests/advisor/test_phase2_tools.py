"""Phase-2 tools through the server: golden scenarios, arguments, missing/degraded data, localization, response modes,
graph and spatial edge cases (docs/v1.1/PHASE2-ADDENDUM.md)."""

from __future__ import annotations

import copy
import json

import pytest

import build_fixtures as bf
from advisor.helpers import VALID_CALLS, check_advisor_response
from advisor.test_window_knowledge import publish_state
from advisor.worlds import failed, load, world_with
from conftest import Harness
from roi_mcp.tools import ADVISOR_PHASE2_TOOL_NAMES, ADVISOR_TOOL_NAMES


def v(q):
    return None if q is None else q["value"]


def ok(h, name, args):
    r = h.call(name, args)
    assert r["ok"], r.get("error")
    check_advisor_response(name, r)
    return r


def err(h, name, args, code):
    r = h.call(name, args)
    assert not r["ok"] and r["error"]["code"] == code, r
    check_advisor_response(name, r)
    return r


# ------------------------------------------------------------------ research_path

def test_research_path_golden(h):
    res = ok(h, "research_path", {"target": "Polymers"})["data"]["result"]
    assert [n["tech"] for n in res["remaining"]] == ["tech:Plastics", "tech:Polymers"]
    assert res["already_unlocked"] == ["tech:Petrochemistry"]
    pl, po = res["remaining"]
    assert pl["basis"] == "game_computed" and pl["days"]["kind"] == "game_computed" and v(pl["days"]) == 1680
    assert po["basis"] == "formula_calibrated" and po["days"]["kind"] == "estimate" and v(po["days"]) == 3900
    assert v(res["totals"]["days"]) == 5580 and res["totals"]["complete"] is True
    assert res["alternatives"] == [] and "conjunctive" in res["alternatives_reason"]
    # queue ahead: the active node AdvancedPaints (324 days left) and queued Railways (540) come first
    assert res["queue_ahead"] == ["tech:AdvancedPaints", "tech:Railways"]
    assert v(res["estimated_completion_days"]) == pytest.approx(5580 + 324 + 540)


def test_research_path_active_and_unlocked_targets(h):
    act = ok(h, "research_path", {"target": "AdvancedPaints"})["data"]["result"]
    assert act["remaining"][0]["status"] == "active" and act["remaining"][0]["basis"] == "game_active_remaining"
    assert v(act["totals"]["days"]) == 324 and v(act["totals"]["cost"]) == 1080000
    done = ok(h, "research_path", {"target": "Paints"})["data"]["result"]
    assert done["remaining"] == [] and done["target"]["already_unlocked"] is True
    teaser = ok(h, "research_path", {"target": "FutureTech"})
    assert teaser["data"]["result"]["target"]["teaser"] is True
    assert any(d["input"] == "tech:FutureTech" for d in teaser["data"]["degraded_inputs"])
    err(h, "research_path", {"target": "tech:NoSuchTech"}, "not_found")


def test_research_path_zero_efficiency_never_invents_times(exchange, clock, procs):
    def mutate(d):
        d["research"]["player"]["efficiency"] = 0
        d["research"]["player"]["costs"] = []
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(mutate))
    res = ok(hh, "research_path", {"target": "Polymers"})["data"]
    assert v(res["result"]["totals"]["days"]) is None and res["result"]["totals"]["complete"] is False
    assert any(d["reason"] == "missing_value" for d in res["degraded_inputs"])


def test_research_path_without_research_section(exchange, clock, procs):
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(state_sections={"research": failed()}))
    err(hh, "research_path", {"target": "Polymers"}, "section_unavailable")
    err(hh, "suggest_research", {}, "section_unavailable")


# ------------------------------------------------------------------ suggest_research

def test_suggest_research_score_components_golden(h):
    rows = ok(h, "suggest_research", {"include_reachable": True})["data"]["result"]["suggestions"]
    top = rows[0]
    assert top["tech"] == "tech:AdvancedPaints" and top["rank"] == 1
    c = top["components"]
    # Paint unmet demand 42 / 30 d x median shop price 1480 = 62160; chain fit 1 (Chemicals and Dye produced);
    # research cost = remaining cost of the active node 1,080,000
    assert v(c["demand_value_per_30d"]) == 62160 and v(c["chain_fit"]) == 1 and v(c["research_cost"]) == 1080000
    assert v(top["score"]) == pytest.approx(62160 * 1.0 / 1080000 * 30, rel=1e-4)
    unscored = [r for r in rows if v(r["score"]) is None]
    assert unscored and all(r["unscored_reason"] for r in unscored)
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    avail = ok(h, "suggest_research", {})["data"]["result"]["suggestions"]
    assert all(r["available_now"] for r in avail)


# ------------------------------------------------------------------ loan_calculator

def test_loan_calculator_catalogue_loan_golden(h):
    r = ok(h, "loan_calculator", {"loan": "Bank Loan"})["data"]["result"]["loan"]
    assert v(r["payment"]) == 18000 and v(r["total_repayment"]) == 1080000 and v(r["financing_cost"]) == 80000
    cf = r["cash_flow"]
    assert v(cf["net"]) == 156000 and v(cf["net_after_payment"]) == 138000
    assert v(cf["payment_share_of_net"]) == pytest.approx(18000 / 156000, abs=1e-4)
    assert v(cf["months_covered_by_cash"]) == pytest.approx(2500000 / 18000, abs=1e-3)
    assert r["payment"]["kind"] == "derived"


def test_loan_calculator_explicit_terms_and_boundaries(h):
    r = ok(h, "loan_calculator", {"principal": 120000, "apr": 0, "duration_months": 12, "grace_months": 3})["data"]["result"]["loan"]
    assert v(r["payment"]) == 10000 and v(r["financing_cost"]) == 0 and r["first_payment_month_offset"] == 4
    one = ok(h, "loan_calculator", {"principal": 1, "apr": 10, "duration_months": 1})["data"]["result"]["loan"]
    assert v(one["payment"]) == 11 and one["schedule_rows_omitted"] == 0
    big = ok(h, "loan_calculator", {"principal": 1e12, "apr": 10, "duration_months": 1200, "detail": "full"})
    loan = big["data"]["result"]["loan"]
    assert len(loan["schedule"]) == 49 and loan["schedule_rows_omitted"] == 1151
    assert not any(w["code"] == "truncated" for w in big["meta"]["warnings"])
    starter = ok(h, "loan_calculator", {"loan": "StarterLoanNormal"})["data"]["result"]["loan"]
    assert starter["first_payment_month_offset"] == 25                          # 24 grace months
    ex = ok(h, "loan_calculator", {"existing": True})["data"]["result"]
    assert len(ex["existing_loans"]) == 2 and "loan" not in ex
    assert v(ex["existing_loans"][1]["payment"]) == 18000 and v(ex["existing_loans"][1]["remaining_to_pay"]) == 18000 * 50


@pytest.mark.parametrize("args", [{}, {"principal": 100}, {"principal": 100, "apr": 0.1},
                                  {"loan": "BankLoan", "principal": 100, "apr": 0.1, "duration_months": 5},
                                  {"principal": 0, "apr": 0.1, "duration_months": 5}, {"principal": -1, "apr": 0.1, "duration_months": 5},
                                  {"principal": 100, "apr": -0.1, "duration_months": 5}, {"principal": 100, "apr": 0.1, "duration_months": 0},
                                  {"principal": 100, "apr": 0.1, "duration_months": 5, "modifier": 0}, {"loan": " "}])
def test_loan_calculator_invalid(h, args):
    err(h, "loan_calculator", args, "invalid_argument")


def test_loan_calculator_without_history(h):
    (h.exchange / "history.json").unlink()
    r = ok(h, "loan_calculator", {"loan": "BankLoan"})["data"]
    assert v(r["result"]["loan"]["cash_flow"]["net"]) is None
    assert any(d["input"] == "history" for d in r["degraded_inputs"])
    err(h, "loan_calculator", {"loan": "MagicLoan"}, "not_found")


# ------------------------------------------------------------------ route_calculator

def test_route_calculator_hypothetical_golden(h):
    r = ok(h, "route_calculator", VALID_CALLS["route_calculator"])["data"]
    res = r["result"]
    # (55,40) -> (200,150): sqrt(145^2 + 110^2) = 182.003; (250 + 182.003 x 10) x 1.25 = 2587.54
    assert v(res["straight_line"]["euclidean"]) == pytest.approx(182.003, abs=1e-3)
    assert res["straight_line"]["is_path_distance"] is False and "not a road" in res["warning"]
    assert v(res["cost_per_trip"]["at_straight_line"]) == pytest.approx(2587.54, abs=0.01)
    assert res["existing_route"] is None
    assert v(res["vehicle_capacity"]) == 10 and v(res["cost_per_unit_at_capacity"]) == pytest.approx(258.754, abs=1e-3)
    t = res["throughput"]
    assert v(t["destination_demand_per_30d"]) == 12 and v(t["flow_cap_per_30d"]) == pytest.approx(1.7143, abs=1e-3)
    assert t["travel_time"] is None and any(d["input"] == "travel_time" for d in r["degraded_inputs"])
    assert "A-STRAIGHT-LINE" in {a["id"] for a in r["assumptions"]}


def test_route_calculator_existing_route_is_authoritative(h):
    res = ok(h, "route_calculator", {"origin": f"building:{bf.B_PF1}", "destination": f"building:{bf.S_HW1}", "product": "Paint"})["data"]["result"]
    ex = res["existing_route"]
    assert ex["authoritative"] is True and v(ex["distance_tiles"]) == 75 and v(ex["dispatch_cost"]) == 1250


def test_route_calculator_variants(h):
    tr = ok(h, "route_calculator", {**VALID_CALLS["route_calculator"], "source": "TrainTerminal", "vehicle_capacity": 40})["data"]
    assert tr["result"]["cost_per_trip"]["formula"] == "TrainTerminalDispatchCost"
    assert "mechanic_unverified" in tr["result"]["cost_per_trip"]["at_straight_line"]["confidence"]["factors"]
    assert tr["result"]["vehicle_capacity"]["kind"] == "parameter"
    err(h, "route_calculator", {"origin": f"building:{bf.B_PF1}", "destination": f"building:{bf.B_PF1}"}, "invalid_argument")
    err(h, "route_calculator", {"origin": f"building:{bf.B_PF1}"}, "invalid_argument")
    err(h, "route_calculator", {"origin": f"building:{bf.B_PF1}", "destination": "building:Nope@1,1"}, "not_found")
    err(h, "route_calculator", {**VALID_CALLS["route_calculator"], "vehicle_capacity": 0}, "invalid_argument")


def test_route_calculator_without_routes_section(exchange, clock, procs):
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(state_sections={"routes_player": failed()}))
    r = ok(hh, "route_calculator", VALID_CALLS["route_calculator"])["data"]
    assert r["result"]["path_tiles_range_estimate"] is None and v(r["result"]["vehicle_capacity"]) is None
    assert v(r["result"]["cost_per_unit_at_capacity"]) is None                       # no capacity: not invented
    assert v(r["result"]["cost_per_trip"]["at_straight_line"]) is not None


# ------------------------------------------------------------------ compare_options

def test_compare_recipes_golden(h):
    res = ok(h, "compare_options", VALID_CALLS["compare_options"])["data"]["result"]
    opts = {o["id"]: o for o in res["options"]}
    assert v(opts["recipe:Paints"]["values"]["output_per_30d_per_building"]) == pytest.approx(1.7143, abs=1e-3)
    assert v(opts["recipe:PaintsAdvanced"]["values"]["output_per_30d_per_building"]) == 3
    assert v(opts["recipe:PaintsAdvanced"]["values"]["input_cost_per_unit"]) == pytest.approx((2 * 612 + 688) / 3, abs=1e-3)
    assert opts["recipe:PaintsAdvanced"]["locked_by"] == ["tech:AdvancedPaints"]
    best = {d["metric"]: d["best_option"] for d in res["differences"]}
    assert best["unit_cost"] == "recipe:PaintsAdvanced"
    assert "game_days" not in best                                                   # better = none: not ranked


def test_compare_shops_and_sources(h):
    res = ok(h, "compare_options", {"kind": "shop_destinations", "origin": f"building:{bf.B_PF1}", "product": "Paint",
                                    "options": [f"building:{bf.S_HW1}", f"building:{bf.S_HW2}", f"building:{bf.S_GS}"]})["data"]["result"]
    o = {x["id"]: x for x in res["options"]}
    assert o[f"building:{bf.S_HW1}"]["values"]["cost_per_unit"]["kind"] == "derived"            # existing route
    assert v(o[f"building:{bf.S_HW1}"]["values"]["cost_per_unit"]) == 125
    assert o[f"building:{bf.S_HW2}"]["values"]["cost_per_unit"]["kind"] == "estimate"           # straight line
    assert res["context"]["is_path_distance"] is False
    src = ok(h, "compare_options", {"kind": "supply_sources", "destination": f"building:{bf.B_PC1}", "product": "Gas",
                                    "options": [f"building:{bf.B_GW1}", f"building:{bf.B_GW2}"]})["data"]["result"]
    assert {x["existing_route"] for x in src["options"]} == {f"route:{bf.B_GW1}|Gas|{bf.B_PC1}|own|0", f"route:{bf.B_GW2}|Gas|{bf.B_PC1}|own|0"}


def test_compare_building_types_research_and_scenarios(h):
    bt = ok(h, "compare_options", {"kind": "building_types", "options": ["PetrochemicalFactory", "ChemicalPlant"], "recipe": "Chemicals"})
    o = {x["id"]: x for x in bt["data"]["result"]["options"]}
    assert v(o["building_type:PetrochemicalFactory"]["values"]["output_per_30d"]) == 3
    assert "output_per_30d" in o["building_type:ChemicalPlant"]["unavailable"]                  # cannot run Chemicals
    rs = ok(h, "compare_options", {"kind": "research", "options": ["AdvancedPaints", "Railways"]})["data"]["result"]
    assert {d["metric"] for d in rs["differences"]} >= {"research_cost", "research_days"}
    sc = ok(h, "compare_options", {"kind": "scenarios", "product": "Paint", "scenarios": [
        {"type": "add_buildings", "recipe": "Paints"}, {"type": "set_efficiency", "building": f"building:{bf.B_PF1}", "index": 6}]})
    opts = sc["data"]["result"]["options"]
    assert v(opts[0]["values"]["capex"]) == 180000 and v(opts[1]["values"]["product_supply_delta_per_30d"]) == pytest.approx(1.7143, abs=1e-3)
    assert "capex" in opts[1]["unavailable"]                                                    # not fabricated for an efficiency change


@pytest.mark.parametrize("args", [
    {"kind": "recipes", "options": ["Chemicals", "Dye"]},                                       # no shared product
    {"kind": "recipes", "options": ["Paints"]}, {"kind": "recipes", "options": ["Paints", "Paints"]},
    {"kind": "recipes"}, {"kind": "shop_destinations", "options": [f"building:{bf.S_HW1}", f"building:{bf.S_HW2}"], "product": "Paint"},
    {"kind": "shop_destinations", "origin": f"building:{bf.B_PF1}", "product": "Paint", "options": [f"building:{bf.B_PC1}", f"building:{bf.S_HW1}"]},
    {"kind": "supply_sources", "destination": f"building:{bf.B_PC1}", "product": "Gas", "options": [f"building:{bf.B_PF1}", f"building:{bf.B_GW1}"]},
    {"kind": "scenarios", "scenarios": [{"type": "add_buildings", "recipe": "Paints"}]},
    {"kind": "scenarios", "scenarios": [{"type": "nope"}, {"type": "add_buildings", "recipe": "Paints"}]},
    {"kind": "everything", "options": ["a", "b"]}, {"kind": "recipes", "options": ["a"] * 9},
])
def test_compare_invalid(h, args):
    err(h, "compare_options", args, "invalid_argument")


def test_compare_unknown_option(h):
    err(h, "compare_options", {"kind": "recipes", "options": ["Paints", "recipe:Nope"]}, "not_found")


# ------------------------------------------------------------------ spatial_analysis

def test_spatial_matrix_golden_and_limits(h):
    res = ok(h, "spatial_analysis", VALID_CALLS["spatial_analysis"])["data"]["result"]
    assert res["is_path_distance"] is False and len(res["pairs"]) == 6                          # 4 locations -> 6 pairs
    first = res["pairs"][0]
    assert (first["from"], first["to"]) == (f"building:{bf.B_PF1}", "city:10")
    assert v(first["euclidean"]) == pytest.approx(((102 - 55) ** 2 + (101 - 40) ** 2) ** 0.5, abs=1e-3)
    full = ok(h, "spatial_analysis", {**VALID_CALLS["spatial_analysis"], "detail": "full"})["data"]["result"]
    assert "chebyshev" in full["pairs"][0]
    dup = ok(h, "spatial_analysis", {"mode": "matrix", "locations": [f"building:{bf.B_PF1}", f"building:{bf.B_PF1}", "city:10"]})
    assert dup["data"]["result"]["duplicates_removed"] == [f"building:{bf.B_PF1}"] and len(dup["data"]["result"]["pairs"]) == 1
    err(h, "spatial_analysis", {"mode": "matrix", "locations": [f"building:{b['key']}" for b in bf.player_buildings()][:13]}, "invalid_argument")
    err(h, "spatial_analysis", {"mode": "matrix", "locations": ["Valmont"]}, "ambiguous")              # city and region
    err(h, "spatial_analysis", {"mode": "matrix", "locations": ["building:Nope@1,1"]}, "not_found")
    err(h, "spatial_analysis", {"mode": "matrix"}, "invalid_argument")
    err(h, "spatial_analysis", {"mode": "chain"}, "invalid_argument")


def test_spatial_hub_deterministic(h):
    args = {"mode": "hub", "locations": [f"building:{bf.B_GW1}", f"building:{bf.B_PF1}", f"building:{bf.S_HW1}"], "weights": [1, 2, 3]}
    a = ok(h, "spatial_analysis", args)["data"]["result"]
    b = ok(h, "spatial_analysis", args)["data"]["result"]
    assert a["ranking"] == b["ranking"] and a["ranking"][0]["id"] == f"building:{bf.S_HW1}"
    sums = [v(r["weighted_distance_sum"]) for r in a["ranking"]]
    assert sums == sorted(sums)
    assert v(a["geometric_median"]["weighted_distance_sum"]) <= sums[0] + 1e-6
    err(h, "spatial_analysis", {**args, "weights": [1, 2]}, "invalid_argument")
    err(h, "spatial_analysis", {**args, "weights": [1, -2, 3]}, "invalid_argument")


def test_spatial_chain_mode(h):
    res = ok(h, "spatial_analysis", {"mode": "chain", "product": "Paint"})["data"]["result"]
    pf = res["producers"][0]
    assert pf["producer"]["id"] == f"building:{bf.B_PF1}"
    assert {leg["input"] for leg in pf["inbound"]} == {"product:Chemicals", "product:Dye"}
    assert pf["outbound"] and all(leg["euclidean"]["kind"] == "derived" for leg in pf["outbound"])


# ------------------------------------------------------------------ get_chain_graph

def test_chain_graph_basis_classes_and_determinism(h):
    a = ok(h, "get_chain_graph", VALID_CALLS["get_chain_graph"])["data"]["result"]
    b = ok(h, "get_chain_graph", VALID_CALLS["get_chain_graph"])["data"]["result"]
    assert a == b
    edges = a["edges"]
    assert {e["basis"] for e in edges} == {"observed", "catalogue", "hypothetical"}
    hyp = [e for e in edges if e["basis"] == "hypothetical"]
    assert all("PaintsAdvanced" in e["from"] + e["to"] for e in hyp)                 # the player does not run it
    runs = [e for e in edges if e["relation"] == "runs"]
    assert all(e["basis"] == "observed" for e in runs)
    ids = [n["id"] for n in a["nodes"]]
    assert len(ids) == len(set(ids)) and ids == sorted(ids, key=lambda i: (next(n["kind"] for n in a["nodes"] if n["id"] == i), i))
    assert a["mermaid"].startswith("graph LR") and "-.->" in a["mermaid"]
    plain = ok(h, "get_chain_graph", {"product": "Paint"})["data"]["result"]
    assert "mermaid" not in plain
    err(h, "get_chain_graph", {"product": "product:Nope"}, "not_found")
    err(h, "get_chain_graph", {"product": "Paint", "depth": 9}, "invalid_argument")


def test_chain_graph_cycle_and_disconnected(exchange, clock, procs):
    hh = Harness(exchange, clock, procs)
    w = world_with()
    # a cyclic catalogue: Chemicals also made from Paint
    w["static"]["data"]["recipes"].append({"name": "Recycle", "display_name": "Recyclage", "english_name": "Recycle",
                                           "ingredients": [{"product": "Paint", "amount": 1}], "results": [{"product": "Chemicals", "amount": 1}],
                                           "game_days": 10.0, "game_days_for_price": 10.0, "required_modules": [], "tier": 1,
                                           "building_types": ["ChemicalPlant"], "used_by_water_harvester": False})
    w["static"]["seq"] = 2
    w["static"]["content_hash"] = bf.content_hash(w["static"]["data"])
    w["state"]["static_ref"] = bf.static_ref_of(w["static"])
    w["history"]["static_ref"] = bf.static_ref_of(w["static"])
    w["heartbeat"] = bf.build_heartbeat(static_doc=w["static"], state_doc=w["state"], history_doc=w["history"])
    load(hh, w)
    res = ok(hh, "get_chain_graph", {"product": "Paint", "depth": 8})["data"]["result"]
    assert "recipe:Recycle" in {n["id"] for n in res["nodes"]}
    lonely = ok(hh, "get_chain_graph", {"product": "Water"})["data"]["result"]                 # raw product, no recipe inputs
    assert {n["kind"] for n in lonely["nodes"]} >= {"product", "recipe"}


# ------------------------------------------------------------------ forecast

def test_forecast_cash_golden(h):
    res = ok(h, "forecast", {"kind": "cash"})["data"]["result"]
    assert res["sample_months"] == ["Y4-12", "Y5-01", "Y5-02"] and res["projection"] == "exhaustion"
    assert v(res["months_to_zero_pessimistic"]) == pytest.approx(2500000 / 363000, abs=0.01)
    assert v(res["months_to_zero_at_mean"]) == pytest.approx(2500000 / (89000 / 3), abs=0.01)
    two = ok(h, "forecast", {"kind": "cash", "months": 2})["data"]["result"]
    assert two["sample_months"] == ["Y5-01", "Y5-02"]


def test_forecast_cash_without_history(exchange, clock, procs):
    hh = Harness(exchange, clock, procs)
    w = world_with()
    hh.write_world(w, ("heartbeat", "static", "state"))
    none = ok(hh, "forecast", {"kind": "cash"})["data"]["result"]
    assert none["available"] is False and "reason" in none


def test_forecast_cash_not_projected_when_nets_positive(exchange, clock, procs):
    def mutate(hd):
        for m in hd["ledger_player"]["months"]:
            m["expense_total"] = 0.0
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(mutate_history=mutate))
    res = ok(hh, "forecast", {"kind": "cash"})["data"]["result"]
    assert res["projection"] == "not_projected" and v(res["months_to_zero_pessimistic"]) is None


def test_forecast_stock_needs_two_game_days(h):
    args = {"kind": "stock", "building": f"building:{bf.B_PF1}", "product": "Paint"}
    first = ok(h, "forecast", args)["data"]["result"]
    assert first["available"] is False

    def grow(d):
        d["session"]["game_day"] = 1522
        for b in d["buildings_player"]:
            if b["key"] == bf.B_PF1:
                b["inventory"][2]["count"] = 39
    publish_state(h, grow, 11)
    res = ok(h, "forecast", args)["data"]["result"]
    assert res["available"] and res["trend"] == "filling"
    assert v(res["rate_per_game_day"]) == pytest.approx(0.1) and v(res["days_to_full"]) == pytest.approx(10)
    err(h, "forecast", {"kind": "stock", "building": f"building:{bf.B_PF1}"}, "invalid_argument")
    err(h, "forecast", {"kind": "stock", "building": f"building:{bf.B_PF1}", "product": "Gas"}, "not_found")
    err(h, "forecast", {"kind": "stock", "building": "building:Headquarters@140,110", "product": "Paint"}, "invalid_argument")
    err(h, "forecast", {"kind": "cash", "months": 1}, "invalid_argument")


# ------------------------------------------------------------------ localization

def test_language_fr_uses_catalogue_names_and_authored_texts(h):
    d = ok(h, "diagnose_chain", {"product": "Paint", "language": "fr"})["data"]
    assert d["language"] == "fr" and d["text_translation"]["untranslated_texts"] == 0
    p = d["result"]["product"]
    assert p["id"] == "product:Paint" and p["name"] == "Peinture" and p["name_source"] == "catalogue_fr"
    assert any(f["summary"].startswith("La production s'accumule") for f in d["result"]["findings"])
    both = ok(h, "diagnose_chain", {"product": "Paint", "language": "both"})["data"]["result"]
    assert both["product"]["name"] == "Paint" and both["product"]["name_fr"] == "Peinture"
    f0 = both["findings"][0]
    assert f0["summary"].startswith("Output piles up") and f0["summary_fr"].startswith("La production")
    en = ok(h, "diagnose_chain", {"product": "Paint"})["data"]["result"]
    assert en["product"]["name"] == "Paint" and "name_fr" not in en["product"]
    assert [f["kind"] for f in en["findings"]] == [f["kind"] for f in both["findings"]]           # language-independent


@pytest.mark.parametrize("name", ["get_overview", "review_routes", "find_opportunities", "what_if", "what_changed", "plan_chain"])
def test_every_advisor_sentence_has_a_french_version(h, name):
    d = ok(h, name, {**VALID_CALLS[name], "language": "fr"})["data"]
    assert d["text_translation"]["untranslated_texts"] == 0, name


def test_french_falls_back_when_catalogue_is_not_french(exchange, clock, procs):
    hh = Harness(exchange, clock, procs)
    w = world_with()
    w["static"]["data"]["language"] = "English"
    w["static"]["content_hash"] = bf.content_hash(w["static"]["data"])
    w["state"]["static_ref"] = bf.static_ref_of(w["static"])
    w["history"]["static_ref"] = bf.static_ref_of(w["static"])
    w["heartbeat"] = bf.build_heartbeat(static_doc=w["static"], state_doc=w["state"], history_doc=w["history"])
    load(hh, w)
    p = ok(hh, "diagnose_chain", {"product": "Paint", "language": "fr"})["data"]["result"]["product"]
    assert p["name"] == "Paint" and p["name_source"] == "fallback_en"


def test_knowledge_titles_in_french(h):
    m = ok(h, "explain_mechanic", {"topic": "min keep", "language": "fr"})["data"]["result"]["matches"][0]
    assert m["title"].startswith("Min Keep : stock minimal") and m.get("summary_language") == "en"
    m2 = ok(h, "explain_mechanic", {"topic": "Prêts"})["data"]["result"]["matches"][0]
    assert m2["id"] == "loans"                                                       # French title is searchable
    en = ok(h, "how_to", {"action": "set max send"})["data"]["result"]["matches"][0]
    assert "title_fr" not in en


# ------------------------------------------------------------------ response modes

@pytest.mark.parametrize("name", ADVISOR_TOOL_NAMES)
def test_summary_mode_keeps_result_confidence_and_warnings(h, name):
    std = ok(h, name, VALID_CALLS[name])["data"]
    s = ok(h, name, {**VALID_CALLS[name], "detail": "summary"})["data"]
    assert s["detail"] == "summary" and s["observed"] == {} and s["calculation_inputs"] == {}
    assert s["confidence"] == std["confidence"] and s["assumptions"] == std["assumptions"]
    assert s["degraded_inputs"] == std["degraded_inputs"] and s["unavailable"] == std["unavailable"]
    assert set(s["result"]) <= set(std["result"]) | {f"{k}_omitted" for k in std["result"]} | {"summary_skeleton"}

    def lists(o, path=""):
        if isinstance(o, dict):
            for k, x in o.items():
                yield from lists(x, f"{path}.{k}")
        elif isinstance(o, list):
            yield path, o
            for x in o:
                yield from lists(x, path)
    for path, lst in lists(s["result"]):
        if path in (".nodes", ".edges") and s["result"].get("summary_skeleton"):
            continue            # graph skeleton reduced coherently by the tool (no dangling edges)
        if lst and all(isinstance(i, dict) for i in lst):
            assert len(lst) <= 5, (name, path)
    if name == "get_chain_graph":
        ids = {n["id"] for n in s["result"]["nodes"]}
        assert all(e["from"] in ids and e["to"] in ids for e in s["result"]["edges"])
        assert all(n["kind"] != "building" for n in s["result"]["nodes"])


def test_summary_keeps_top_items_in_order(h):
    std = ok(h, "review_routes", {"limit": 50})["data"]["result"]["findings"]
    s = ok(h, "review_routes", {"limit": 50, "detail": "summary"})["data"]["result"]
    assert [f["route_id"] + f["kind"] for f in s["findings"]] == [f["route_id"] + f["kind"] for f in std[:5]]
    assert s["findings_omitted"] == len(std) - 5
    assert s["findings"][0]["evidence"] == std[0]["evidence"] and s["findings"][4]["evidence"] == {"omitted_in_summary": True}


# ------------------------------------------------------------------ degraded / stale for the new tools

@pytest.mark.parametrize("name", ADVISOR_PHASE2_TOOL_NAMES)
def test_phase2_tools_report_stale_basis(h, name):
    h.procs.procs = []
    assert h.call(name, VALID_CALLS[name])["error"]["code"] == "game_not_running"
    r = ok(h, name, {**VALID_CALLS[name], "allow_stale": True})
    assert "stale_data" in r["data"]["confidence"]["factors"] or name == "forecast"


def test_contradictory_route_copy_in_route_calculator_does_not_change_distance(exchange, clock, procs):
    def mutate(d):
        for r in d["routes_player"]:
            r["distance_tiles"] = (r["distance_tiles"] or 0) * 100 if r["distance_tiles"] else None
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(mutate))
    res = ok(hh, "route_calculator", VALID_CALLS["route_calculator"])["data"]["result"]
    assert v(res["straight_line"]["euclidean"]) == pytest.approx(182.003, abs=1e-3)              # geometry is never altered
    assert v(res["path_tiles_range_estimate"]["min"]) > v(res["straight_line"]["euclidean"])


# ------------------------------------------------------------------ generic argument sweeps (all advisor tools)

def _text_params():
    from roi_mcp.tools import build_specs
    out = []
    for spec in build_specs():
        if spec.name not in ADVISOR_TOOL_NAMES:
            continue
        for p, ps in spec.input_schema()["properties"].items():
            if ps.get("type") == "string" and "enum" not in ps:
                out.append((spec.name, p, False))
            elif ps.get("type") == "array" and ps.get("items", {}).get("type") == "string" and "enum" not in ps["items"]:
                out.append((spec.name, p, True))
    return out


@pytest.mark.parametrize("blank", ["", " ", "\t\n"])
@pytest.mark.parametrize("name,param,is_array", _text_params())
def test_blank_text_arguments_are_invalid(h, name, param, is_array, blank):
    r = h.call(name, {**VALID_CALLS[name], param: [blank] if is_array else blank})
    assert r["error"]["code"] == "invalid_argument", (name, param, r)


@pytest.mark.parametrize("name", ADVISOR_TOOL_NAMES)
def test_unknown_parameter_and_bad_modes(h, name):
    for bad in ({"bogus": 1}, {"detail": "tiny"}, {"language": "de"}, {"detail": None}):
        assert h.call(name, {**VALID_CALLS[name], **bad})["error"]["code"] == "invalid_argument", (name, bad)


@pytest.mark.parametrize("name", ADVISOR_TOOL_NAMES)
def test_huge_strings_never_crash(h, name):
    from roi_mcp import config as cfg
    from roi_mcp.util import json_size
    from roi_mcp.tools import build_specs
    spec = next(s for s in build_specs() if s.name == name)
    for p, ps in spec.input_schema()["properties"].items():
        if ps.get("type") == "string" and "enum" not in ps:
            r = h.call(name, {**VALID_CALLS[name], p: "Z" * 100_000})
            assert r["ok"] or r["error"]["code"] in ("invalid_argument", "not_found", "ambiguous"), (name, p, r["error"])
            assert json_size(r) <= cfg.RESPONSE_SIZE_CAP_BYTES
