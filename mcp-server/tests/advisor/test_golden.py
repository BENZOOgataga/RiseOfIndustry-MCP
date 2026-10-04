"""Golden economic scenarios: tool outputs on synthetic worlds checked against hand-computed values."""

from __future__ import annotations

import pytest

import build_fixtures as bf
from advisor.helpers import ROUTE_GW1, check_advisor_response
from advisor.worlds import load, set_paint_price, world_with


def v(q):
    return None if q is None else q["value"]


def by_product(rows, key="product"):
    return {r[key]["id"]: r for r in rows}


# ------------------------------------------------------------------ get_profitability (sample world)

def test_profitability_chemicals_hand_computed(h):
    r = h.call("get_profitability", {"product": "Chemicals"})
    check_advisor_response("get_profitability", r)
    e = r["data"]["result"]["products"][0]["estimate"]
    # price: median shop price with demand for the player = 610 (one shop)
    assert v(e["sale_price"]) == 610 and e["sale_price"]["basis"] == "shop_median"
    # upkeep: PC1 + PC2 active upkeep 10000 each over 3 + 3 units/30 d
    assert v(e["unit_cost"]["upkeep_per_unit"]) == pytest.approx(3333.3333)
    # inputs: 3 Gas per 2 Chemicals at the market final price 96 -> 144
    assert v(e["unit_cost"]["inputs_per_unit"]) == 144
    # distribution: mean of 525/10, 525/10, 4062.5/40 (rail) = 68.8542
    assert v(e["distribution_cost_per_unit"]) == pytest.approx(68.8542, abs=1e-3)
    assert v(e["margin_per_unit"]) == pytest.approx(610 - 3333.3333 - 144 - 68.8542, abs=1e-2)
    assert v(e["volume_per_30d"]) == 3 and e["volume_per_30d"]["kind"] == "observed"
    assert v(e["margin_per_30d"]) == pytest.approx(3 * (610 - 3333.3333 - 144 - 68.8542), abs=0.05)
    assert {a["id"] for a in r["data"]["assumptions"]} >= {"A-INPUT-MARKET", "A-CONTINUOUS", "A-FULL-VEHICLES"}


def test_profitability_game_stats_contradiction_reported(h):
    r = h.call("get_profitability", {"product": "Paint"})
    p = r["data"]["result"]["products"][0]
    assert v(p["game_stats"]["profit"]) == 4300 and v(p["game_stats"]["profit_per_unit_sold"]) == 430
    c = r["data"]["contradictions"]
    assert c and c[0]["kind"] == "game_stats_vs_estimate" and c[0]["subject"] == "product:Paint"
    assert "contradictory_inputs" in p["estimate"]["margin_per_unit"]["confidence"]["factors"]


def test_profitability_own_cost_basis_values_inputs_at_own_cost(h):
    mk = h.call("get_profitability", {"product": "Paint"})["data"]["result"]["products"][0]["estimate"]
    own = h.call("get_profitability", {"product": "Paint", "input_cost_basis": "own_cost"})
    oe = own["data"]["result"]["products"][0]["estimate"]
    check_advisor_response("get_profitability", own)
    assert v(oe["unit_cost"]["inputs_per_unit"]) != v(mk["unit_cost"]["inputs_per_unit"])
    assert "A-INPUT-OWN" in {a["id"] for a in own["data"]["assumptions"]}


# ------------------------------------------------------------------ find_opportunities (profitable paint world)

def test_opportunities_golden_paint_at_20000(h):
    load(h, world_with(set_paint_price(20000.0)))
    r = h.call("find_opportunities", {})
    check_advisor_response("find_opportunities", r)
    opps = r["data"]["result"]["opportunities"]
    paint = [o for o in opps if o["product"]["id"] == "product:Paint"]
    kinds = {o["type"]: o for o in paint}
    assert set(kinds) >= {"route_surplus", "expand_production"}
    # unmet per 30 d: HW1 10-3=7 (30 d), CS1 8 (30 d), HW2 6 per 15 d = 12, GS 5 per 10 d = 15 -> 42
    assert v(kinds["route_surplus"]["evidence"]["unmet_demand_per_30d"]) == 42
    # route_surplus: volume = spare supply 2 x 30 / 35 = 1.714286
    rs = kinds["route_surplus"]
    assert v(rs["volume_per_30d"]) == pytest.approx(1.7143, abs=1e-3)
    margin = 20000 - 12500 / 1.714286 - (0.5 * 612 + 1 * 688) - 81.25
    assert v(rs["margin_per_unit"]) == pytest.approx(margin, abs=0.05)
    # expand: want 42 - 1.714 = 40.29 -> 24 buildings needed, capped at max_buildings 3 -> volume 5.142857
    ex = kinds["expand_production"]
    assert ex["buildings_needed"] == 3 and v(ex["volume_per_30d"]) == pytest.approx(5.142857, abs=1e-3)
    new_margin = 20000 - (12500 / 1.714286 + 994)
    assert v(ex["margin_per_unit"]) == pytest.approx(new_margin, abs=0.05)
    assert v(ex["capex"]) == 540000                                     # 3 x player price 180000
    assert v(ex["payback_months"]) == pytest.approx(540000 / (new_margin * 5.142857), rel=1e-3)
    assert ex["building_type"] == "building_type:PaintFactory" and ex["viable"] is True
    ranks = [o["rank"] for o in opps]
    assert ranks == sorted(ranks) and ranks[0] == 1
    ex5 = h.call("find_opportunities", {"max_buildings": 5, "types": ["expand_production"]})["data"]["result"]["opportunities"]
    assert ex5[0]["buildings_needed"] == 5


def test_unviable_candidates_hidden_by_default(h):
    r = h.call("find_opportunities", {})
    assert all(o["viable"] is not False for o in r["data"]["result"]["opportunities"])
    assert any(u["field"] == "opportunities[viable=false]" for u in r["data"]["unavailable"])
    r2 = h.call("find_opportunities", {"include_unviable": True})
    assert any(o["viable"] is False for o in r2["data"]["result"]["opportunities"])


# ------------------------------------------------------------------ plan_chain

def test_plan_chain_paint_100_greenfield(h):
    r = h.call("plan_chain", {"product": "Paint", "target_per_month": 100, "existing_capacity": "none"})
    check_advisor_response("plan_chain", r)
    steps = {s["product"]["id"]: s for s in r["data"]["result"]["steps"]}
    new = {k: v(s["new_per_30d"]) for k, s in steps.items()}
    assert new == {"product:Paint": 100, "product:Chemicals": 50, "product:Gas": 75, "product:Dye": 100,
                   "product:Flowers": 100, "product:Water": 50}
    # Paint: observed peer rate 2 x 30 / 35 = 1.714286 -> ceil(58.33) = 59 factories, player price 180000, upkeep 12500
    p = steps["product:Paint"]
    assert p["buildings"] == 59 and p["rate_per_building"]["basis"] == "observed_peer"
    assert v(p["capex"]) == 59 * 180000 and v(p["upkeep_per_30d"]) == 59 * 12500
    # Gas: peer GW1 10.8 with 2 modules -> 16.2 at 3 modules; 75 / 16.2 -> 5 wells
    assert steps["product:Gas"]["buildings"] == 5 and v(steps["product:Gas"]["rate_per_building"]) == pytest.approx(16.2)
    tot = r["data"]["result"]["totals"]
    assert tot["complete"] is True and tot["buildings"] == sum(s["buildings"] or 0 for s in steps.values())
    assert set(tot["raw_inputs_per_30d"]) == {"product:Gas", "product:Flowers", "product:Water"}


def test_plan_chain_spare_reduces_requirements(h):
    spare = h.call("plan_chain", {"product": "Paint", "target_per_month": 100})["data"]["result"]
    none = h.call("plan_chain", {"product": "Paint", "target_per_month": 100, "existing_capacity": "none"})["data"]["result"]
    assert spare["totals"]["buildings"] < none["totals"]["buildings"]
    s = {x["product"]["id"]: x for x in spare["steps"]}
    assert v(s["product:Paint"]["from_existing_per_30d"]) == pytest.approx(1.7143, abs=1e-3)


def test_plan_chain_recipe_choice_and_locked(h):
    r = h.call("plan_chain", {"product": "Paint", "target_per_month": 30, "recipe_choice": {"Paint": "PaintsAdvanced"},
                              "existing_capacity": "none"})
    steps = {s["product"]["id"]: s for s in r["data"]["result"]["steps"]}
    assert steps["product:Paint"]["recipe"] == "recipe:PaintsAdvanced"
    assert steps["product:Paint"]["recipe_chosen_by"] == "recipe_choice"
    assert v(steps["product:Chemicals"]["new_per_30d"]) == 20 and v(steps["product:Dye"]["new_per_30d"]) == 10   # 30 x 2/3, 30 x 1/3


# ------------------------------------------------------------------ what_if

def test_what_if_set_max_send_shared_cap_golden(h):
    r = h.call("what_if", {"change": {"type": "set_max_send", "route": ROUTE_GW1, "value": 20}})
    rows = {x["route_id"]: x for x in r["data"]["result"]["deltas"]["routes"]}
    assert len(rows) == 3                                                       # every origin sharing the cap
    g1 = rows[ROUTE_GW1]
    assert v(g1["dispatch_amount_now"]) == 0 and v(g1["replica_now"]) == 0 and v(g1["scenario"]) == 7
    assert any(s["kind"] == "shared_cap" for s in r["data"]["result"]["side_effects"])


def test_what_if_min_keep_and_keep_all(h):
    r = h.call("what_if", {"change": {"type": "set_min_keep", "route": f"route:{bf.B_GW2}|Gas|{bf.B_PC2}|own|0", "value": 0}})
    row = r["data"]["result"]["deltas"]["routes"][0]
    # GW2 -> PC2: currently keep_all; with Min Keep 0: min(cap 10, stock 30, free 39) = 10
    assert v(row["replica_now"]) == 0 and v(row["scenario"]) == 10
    r = h.call("what_if", {"change": {"type": "set_min_keep", "route": ROUTE_GW1, "keep_all": True}})
    assert v(r["data"]["result"]["deltas"]["routes"][0]["scenario"]) == 0


def test_what_if_efficiency_golden(h):
    r = h.call("what_if", {"change": {"type": "set_efficiency", "building": f"building:{bf.B_PC1}", "index": 5}})
    check_advisor_response("what_if", r)
    res = r["data"]["result"]
    assert v(res["baseline"]["output_multiplier"]) == 1.0 and v(res["scenario"]["output_multiplier"]) == 1.5
    assert v(res["scenario"]["upkeep_per_30d"]) == 15000 and v(res["deltas"]["upkeep_per_30d"]) == 5000
    prods = {p["product"]["id"]: p for p in res["deltas"]["products"]}
    # PC1 makes 3 Chemicals / 30 d -> 4.5; total supply 6 -> 7.5; Gas need 4.5 -> 6.75 at PC1, 9 -> 11.25 total
    assert v(prods["product:Chemicals"]["supply_after"]) == pytest.approx(7.5)
    assert v(prods["product:Gas"]["internal_need_after"]) == pytest.approx(11.25)


def test_what_if_add_and_remove_buildings(h):
    r = h.call("what_if", {"change": {"type": "add_buildings", "recipe": "Paints", "count": 2}})
    res = r["data"]["result"]
    assert v(res["deltas"]["capex"]) == 360000 and v(res["deltas"]["upkeep_per_30d"]) == 25000
    prods = {p["product"]["id"]: p for p in res["deltas"]["products"]}
    assert v(prods["product:Paint"]["supply_after"]) == pytest.approx(1.714286 * 3, abs=1e-3)
    r = h.call("what_if", {"change": {"type": "remove_building", "building": f"building:{bf.B_PC1}"}})
    res = r["data"]["result"]
    assert v(res["deltas"]["refund"]) == 300000                                    # 0.75 x 400000 paid
    assert v(res["deltas"]["upkeep_per_30d"]) == -10000
    assert any(s["kind"] == "routes_removed" for s in res["side_effects"])


def test_what_if_take_loan_golden(h):
    r = h.call("what_if", {"change": {"type": "take_loan", "loan": "Bank Loan"}})
    res = r["data"]["result"]
    # 1,000,000 x 1.08 / 60 = 18000 per month; last complete month net Y5-02 = 156000
    assert v(res["deltas"]["monthly_payment"]) == 18000
    assert v(res["deltas"]["net_after_payment"]) == 156000 - 18000


def test_what_if_change_recipe(h):
    r = h.call("what_if", {"change": {"type": "change_recipe", "building": f"building:{bf.B_PF1}", "recipe": "PaintsAdvanced"}})
    prods = {p["product"]["id"]: p for p in r["data"]["result"]["deltas"]["products"]}
    # observed cycle 35 scaled by 30/35 -> 30 days: Paint 3 per 30 d (was 1.714), Chemicals need 2 (was 0.857)
    assert v(prods["product:Paint"]["supply_after"]) == pytest.approx(1.714286 - 1.714286 + 3, abs=1e-3)
    assert any(s["kind"] == "storage_cleared" for s in r["data"]["result"]["side_effects"])


# ------------------------------------------------------------------ diagnose_chain / review_routes / overview

def test_diagnose_paint_findings(h):
    r = h.call("diagnose_chain", {"product": "Paint"})
    res = r["data"]["result"]
    kinds = [(f["kind"], f["building"]) for f in res["findings"]]
    assert ("outbound_constrained", None) in kinds
    assert ("inbound_route_blocked", f"building:{bf.B_PC1}") in kinds              # all Gas routes zero (Max Send) or dormant
    assert ("inbound_route_blocked", f"building:{bf.B_PC2}") in kinds              # keep-all
    assert ("producer_missing_input", f"building:{bf.B_PC2}") in kinds
    assert res["healthy"] is False
    sev = [f["severity"] for f in res["findings"]]
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    assert sev == sorted(sev, key=order.get)
    levels = {l["product"]["id"]: l for l in res["levels"]}
    assert set(levels) == {"product:Paint", "product:Chemicals", "product:Dye", "product:Gas", "product:Flowers", "product:Water"}
    shallow = h.call("diagnose_chain", {"product": "Paint", "depth": 1})["data"]["result"]
    assert {l["product"]["id"] for l in shallow["levels"]} == {"product:Paint", "product:Chemicals", "product:Dye"}


def test_diagnose_product_without_producer(h):
    load(h, world_with(lambda d: d.__setitem__("buildings_player", [b for b in d["buildings_player"] if b["key"] != bf.B_CP])))
    r = h.call("diagnose_chain", {"product": "Dye"})
    f = r["data"]["result"]["findings"]
    assert f[0]["kind"] == "no_producer" and f[0]["severity"] == "high"


def test_review_routes_golden_summary(h):
    r = h.call("review_routes", {"limit": 50})
    s = r["data"]["result"]["summary"]
    assert s["routes_reviewed"] == 13
    assert s["findings_by_kind"]["route_error"] == 1 and s["findings_by_kind"]["keep_all"] == 1
    assert s["findings_by_kind"]["max_send_saturated"] == 4 and s["findings_by_kind"]["duplicate_route"] == 1
    hi = h.call("review_routes", {"min_severity": "high", "limit": 50})["data"]["result"]["findings"]
    assert all(f["severity"] == "high" for f in hi)
    gas = h.call("review_routes", {"product": "Gas", "kinds": ["keep_all"]})["data"]["result"]["findings"]
    assert [f["route_id"] for f in gas] == [f"route:{bf.B_GW2}|Gas|{bf.B_PC2}|own|0"]


def test_overview_golden(h):
    r = h.call("get_overview", {})
    res = r["data"]["result"]
    assert v(res["cash"]) == 2500000
    assert v(res["loans"]["monthly_payment_total"]) == pytest.approx(7500000 / 120 + 1080000 / 60)
    assert v(res["finances"]["last_complete_month"]["net"]) == 156000
    assert v(res["finances"]["net_change_vs_previous"]) == 156000 - (-363000)
    assert res["buildings"]["count"] == 13 and res["logistics"]["with_errors"] == 1
    assert res["attention"][0]["severity"] == "high"
    assert len(h.call("get_overview", {"max_attention": 1})["data"]["result"]["attention"]) == 1
