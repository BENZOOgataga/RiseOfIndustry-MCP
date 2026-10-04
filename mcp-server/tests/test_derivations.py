"""T-6: every derivation id with hand-computed fixtures, including the Paint chain oracle."""

import pytest

from roi_mcp import derive
from roi_mcp.util import INT32_MAX

CHEMICALS = {"name": "Chemicals", "ingredients": [{"product": "Gas", "amount": 3}], "results": [{"product": "Chemicals", "amount": 2}],
             "game_days": 20.0}
PAINTS = {"name": "Paints", "ingredients": [{"product": "Chemicals", "amount": 1}, {"product": "Dye", "amount": 2}],
          "results": [{"product": "Paint", "amount": 2}], "game_days": 35.0}
DYE = {"name": "Dye", "ingredients": [{"product": "Flowers", "amount": 2}, {"product": "Water", "amount": 1}],
       "results": [{"product": "Dye", "amount": 2}], "game_days": 20.0}
GAS = {"name": "Gas", "ingredients": [], "results": [{"product": "Gas", "amount": 2}], "game_days": 10.0}
RECIPES = {"Paint": PAINTS, "Chemicals": CHEMICALS, "Dye": DYE, "Gas": GAS}


def chooser(p):
    r = RECIPES.get(p)
    return (r, "test") if r else (None, "none")


# D-RATE-1
def test_rate_factory_chemicals():
    r = derive.rate_factory(CHEMICALS, 20.0)
    assert r["method"] == "D-RATE-1"
    assert r["outputs"] == [{"product": "Chemicals", "amount_per_cycle": 2, "per_30d": 3}]   # 2 * 30 / 20
    assert r["inputs"] == [{"product": "Gas", "amount_per_cycle": 3, "per_30d": 4.5}]        # 3 * 30 / 20


def test_rate_factory_paints_and_unknown_cycle():
    r = derive.rate_factory(PAINTS, 35.0)
    assert r["outputs"][0]["per_30d"] == pytest.approx(60 / 35)
    assert derive.rate_factory(PAINTS, None)["available"] is False
    assert derive.rate_factory(None, 10)["available"] is False


# D-RATE-2
def test_rate_gatherer_with_replica_speeds():
    mods = [{"key": "a", "speed_replica": 1.0}, {"key": "b", "speed_replica": 0.8}]
    r = derive.rate_gatherer(GAS, 10.0, mods, "gatherer")
    assert r["method"] == "D-RATE-2"
    assert r["outputs"][0]["per_30d"] == pytest.approx(2 * 30 / 10 * 1.8)  # 10.8
    assert r["approximate"] is False


def test_harvester_speed_replica_formula():
    # 3 nodes with amount, 1 depleted (modifier 0.5), maxResources 4, efficiency 1.2, min 0.25 -> (3 + 0.5)/4*1.2 = 1.05 -> clamp 1
    m = {"efficiency": 1.2, "max_resources": 4, "nodes": 4, "nodes_depleted": 1, "min_guaranteed_speed": 0.25}
    assert derive.harvester_speed(m, depleted_modifier=0.5) == (1.0, True)
    m2 = {"efficiency": 0.5, "max_resources": 4, "nodes": 2, "nodes_depleted": 0, "min_guaranteed_speed": 0.1}
    assert derive.harvester_speed(m2) == (pytest.approx(0.25), True)      # 2/4*0.5
    m3 = {"efficiency": 0.1, "max_resources": 4, "nodes": 1, "nodes_depleted": 0, "min_guaranteed_speed": 0.25}
    assert derive.harvester_speed(m3)[0] == 0.25                          # clamped to the guaranteed minimum
    # depleted nodes without an exported depleted modifier cannot be computed exactly
    assert derive.harvester_speed({**m, "speed_replica": None}) == (None, False)


def test_rate_gatherer_fallback_is_approximate():
    r = derive.rate_gatherer(GAS, 10.0, None, "gatherer", module_count=3)
    assert r["approximate"] is True
    assert r["outputs"][0]["per_30d"] == 18   # 3 * 2 * 30 / 10
    farm = derive.rate_gatherer({"name": "F", "ingredients": [], "results": [{"product": "Flowers", "amount": 3}]}, 30.0,
                                [{"key": "f1", "efficiency": 1.0}, {"key": "f2", "efficiency": 0.5}], "farm")
    assert farm["outputs"][0]["per_30d"] == 4.5   # 3 * 30 / 30 * 1.5


# D-RATE-3
def test_uptime_ratio():
    assert derive.uptime_ratio(850, 1000)["value"] == 0.85
    assert derive.uptime_ratio(0, 0)["value"] is None
    assert derive.uptime_ratio(5, 10)["method"] == "D-RATE-3"


# D-REQ-1 (Paint chain oracle, PRD 14.5 / A2)
def test_requirements_paint_oracle():
    r = derive.requirements("Paint", 100, chooser)
    assert r["method"] == "D-REQ-1"
    req = r["required_per_30d"]
    assert req["Paint"] == 100
    assert req["Chemicals"] == 50
    assert req["Dye"] == 100
    assert req["Gas"] == 75
    assert req["Flowers"] == 100 and req["Water"] == 50
    assert r["raw_inputs_total"] == {"Gas": 75, "Flowers": 100, "Water": 50}


def test_requirements_per_unit_ratio():
    # 1 Paint needs 0.5 Chemicals needs 0.75 Gas
    r = derive.requirements("Paint", 1, chooser)
    assert r["required_per_30d"]["Chemicals"] == 0.5
    assert r["required_per_30d"]["Gas"] == 0.75


def test_requirements_cycle_and_depth():
    loop = {"A": {"name": "A", "ingredients": [{"product": "B", "amount": 1}], "results": [{"product": "A", "amount": 1}]},
            "B": {"name": "B", "ingredients": [{"product": "A", "amount": 1}], "results": [{"product": "B", "amount": 1}]}}
    r = derive.requirements("A", 1, lambda p: (loop.get(p), "t"))
    assert r["cycles_detected"]
    r2 = derive.requirements("Paint", 100, chooser, max_depth=1)
    assert "Gas" not in r2["required_per_30d"] and r2["truncated_at_depth"]


# D-INV-1 / D-INV-2
def test_fill_ratio_and_accumulation():
    a = derive.accumulation("output", 38, 40)
    assert a["fill_ratio"] == 0.95 and a["accumulating"] and a["method"] == "D-INV-1"
    assert not derive.accumulation("input", 38, 40)["accumulating"]
    w = derive.accumulation("input", 12, 40, [("Y5-03-10", 5), ("Y5-03-11", 8), ("Y5-03-12", 12)])
    assert w["accumulating"] and w["reasons"] == ["count_rose_across_window"]
    assert w["window"]["from"] == {"game_date": "Y5-03-10", "count": 5}
    assert derive.fill_ratio(1, 0) is None


def test_inventory_delta_same_session_only():
    d = derive.inventory_delta(5, "Y5-03-10", 12, "Y5-03-12", "s1", "s1")
    assert d["value"] == 7 and d["method"] == "D-INV-2"
    assert derive.inventory_delta(5, "a", 12, "b", "s1", "s2")["value"] is None


# D-SHOP-1
def test_unmet_demand():
    u = derive.unmet_demand(10, 3, 15)
    assert u["per_interval"] == 7 and u["per_30d"] == 14 and u["method"] == "D-SHOP-1"
    assert derive.unmet_demand(2, 5, 30)["per_interval"] == 0
    assert derive.unmet_demand(4, None, None)["per_30d"] is None


# D-SUPDEM-1
def test_supply_demand_ratio():
    assert derive.supply_demand_ratio(30, 10, 5)["value"] == 2
    assert derive.supply_demand_ratio(30, 0, 0)["value"] is None


# D-ROUTE-1
def test_route_unit_costs():
    c = derive.route_unit_costs(587.5, 10, 5)
    assert c["cost_per_unit_at_capacity"] == 58.75 and c["cost_per_unit_at_current_amount"] == 117.5
    assert derive.route_unit_costs(587.5, 10, 0)["cost_per_unit_at_current_amount"] is None


# D-DIST-1 / D-ROUTE-2
def test_distances():
    d = derive.distances(0, 0, 3, 4)
    assert d["euclidean"] == 5 and d["chebyshev"] == 4 and d["method"] == "D-DIST-1"


def test_straight_line_cost_estimate():
    e = derive.straight_line_cost_estimate("(250 + distance * 10) * difficulty * actor", 5.0, 1.25)
    assert e["value"] == 375.0                      # (250 + 50) * 1.25
    assert e["authoritative"] is False and e["route_exists"] is False and e["distance_kind"] == "straight_line"
    assert e["formula"] == "ManualDestinationDispatchCost" and e["method"] == "D-ROUTE-2"
    assert derive.straight_line_cost_estimate(None, 5.0, 1.0)["value"] is None
    assert derive.straight_line_cost_estimate("(250 + nope) * 1", 5.0, 1.0)["value"] is None


# D-VAL-1
def test_company_value():
    v = derive.company_value([{"region_id": "r1", "permit_cost": 1000000, "paid_to_build_in_region": 500000},
                              {"region_id": "r2", "permit_cost": 200000, "paid_to_build_in_region": 300000}], 2)
    # (1.5M + 0.5M) * max(1, 1 + 0.2) = 2.4M ; bundle price = round(0.1 * 2.4M)
    assert v["value"] == 2400000 and v["bundle_price"] == 240000 and v["method"] == "D-VAL-1"
    big = derive.company_value([{"region_id": "r", "permit_cost": 2e10, "paid_to_build_in_region": 0}], 0)
    assert big["bundle_price"] == 999000000
    assert derive.company_value(None, 0)["value"] is None


# D-PERMIT-1
def test_permit_cost():
    assert derive.permit_cost(1600, 1000, 1.0, False)["value"] == 1200000   # x 0.75 without a city
    assert derive.permit_cost(1500, 1000, 1.1, True)["value"] == 1650000
    assert derive.permit_cost(None, 1000, 1, True)["value"] is None


# D-GROW-1
def test_growth_state_order():
    g = lambda **kw: derive.growth_state({"waiting_for_sponsor": False, "prospering": False, "growing": False, **kw}, False)
    assert g(waiting_for_sponsor=True)["value"] == "WaitingForSponsor"
    assert derive.growth_state({"waiting_for_sponsor": False, "prospering": True, "growing": True}, True)["value"] == "Bloated"
    assert g(prospering=True, growing=True)["value"] == "Prospering"
    assert g(growing=True)["value"] == "Growing"
    assert g()["value"] == "Stagnating"
    # thresholds instead of flags
    t = derive.growth_state({"waiting_for_sponsor": False, "prospering": None, "growing": None, "consumed_products": 150,
                             "growth_threshold": 100, "prosperity_threshold": 300}, False)
    assert t["value"] == "Growing" and t["method"] == "D-GROW-1"
    # observer-provided state wins
    assert derive.growth_state({"state": "Prospering"}, False)["source"] == "observer"
    assert derive.growth_state(None, False)["value"] is None


# D-STATUS-1
def _b(**kw):
    base = {"flags": {"user_enabled": True, "requirements_met": True, "is_working": True}, "recipe": "Chemicals",
            "inventory": [{"product": "Gas", "role": "input", "count": 9, "slots": 40},
                          {"product": "Chemicals", "role": "output", "count": 10, "slots": 40}],
            "modules": None, "notifications": [], "is_polluted": False}
    base.update(kw)
    return base


def test_building_status_order():
    st = derive.building_status(_b(), CHEMICALS, True)
    assert st["derived_status"] == "working" and st["method"] == "D-STATUS-1"
    st = derive.building_status(_b(flags={"user_enabled": False, "requirements_met": False, "is_working": False}), CHEMICALS, True)
    assert st["derived_status"] == "disabled"
    assert [e["reason"] for e in st["evidence"]][:2] == ["disabled", "blocked"]
    assert derive.building_status(_b(recipe=None), None, True)["derived_status"] == "no_recipe"
    assert derive.building_status(_b(modules={"count": 0, "max": 3, "items": []}), CHEMICALS, True)["derived_status"] == "no_modules"
    idle_starved = _b(flags={"user_enabled": True, "requirements_met": True, "is_working": False},
                      inventory=[{"product": "Gas", "role": "input", "count": 1, "slots": 40}])
    st = derive.building_status(idle_starved, CHEMICALS, True)
    assert st["derived_status"] == "missing_input" and st["evidence"][0]["count"] == 1 and st["status_class"] == "idle"
    full = _b(inventory=[{"product": "Gas", "role": "input", "count": 9, "slots": 40},
                         {"product": "Chemicals", "role": "output", "count": 39, "slots": 40}])
    assert derive.building_status(full, CHEMICALS, True)["derived_status"] == "output_full"
    assert derive.building_status(_b(is_polluted=True), CHEMICALS, True)["derived_status"] == "polluted"
    dep = _b(modules={"count": 2, "max": 3, "items": [{"deposit_remaining": 0}, {"nodes": 4, "nodes_depleted": 4}]})
    assert derive.building_status(dep, CHEMICALS, True)["derived_status"] == "deposit_depleted"
    # live validation V1.1: modules on no deposit (fields, water, sand: nodes 0, deposit_remaining 0) are not depleted
    fields = _b(modules={"count": 2, "max": 5, "items": [{"nodes": 0, "nodes_depleted": 0, "deposit_remaining": 0, "resource": None}] * 2})
    assert derive.building_status(fields, CHEMICALS, True)["derived_status"] != "deposit_depleted"
    mixed = _b(modules={"count": 2, "max": 5, "items": [{"nodes": 0, "nodes_depleted": 0, "deposit_remaining": 0},
                                                         {"nodes": 1, "nodes_depleted": 1, "deposit_remaining": 0}]})
    assert derive.building_status(mixed, CHEMICALS, True)["derived_status"] != "deposit_depleted"
    idle = _b(flags={"user_enabled": True, "requirements_met": True, "is_working": False})
    assert derive.building_status(idle, CHEMICALS, True)["derived_status"] == "idle"
    blocked = derive.building_status(_b(flags={"user_enabled": True, "requirements_met": False, "is_working": False},
                                        notifications=["NoRoad"]), CHEMICALS, True)
    assert blocked["derived_status"] == "blocked" and blocked["status_class"] == "blocked"


# D-LOAN-1
def test_loan_monthly_payment():
    assert derive.loan_monthly_payment(1000000, 0.08, 60)["value"] == 18000.0   # 1.08M / 60
    assert derive.loan_monthly_payment(7500000, 0.0, 120)["value"] == 62500.0
    assert derive.loan_monthly_payment(1000, 0.1, 0)["value"] is None


# D-FIN-1
def test_month_over_month():
    months = [
        {"month": "Y5-01", "net": 100, "by_category": [{"category": "A", "income": 200, "expense": 50, "net": 150},
                                                        {"category": "B", "income": 0, "expense": 50, "net": -50}]},
        {"month": "Y5-02", "net": 160, "by_category": [{"category": "A", "income": 260, "expense": 50, "net": 210},
                                                        {"category": "B", "income": 0, "expense": 50, "net": -50}]},
    ]
    mom = derive.month_over_month(months)
    assert len(mom) == 1 and mom[0]["net_change"] == 60 and mom[0]["method"] == "D-FIN-1"
    a = next(r for r in mom[0]["by_category"] if r["category"] == "A")
    assert a["net_delta"] == 60 and a["share_of_total_change"] == 1
    assert derive.month_over_month(months[:1]) == []


def test_keep_all():
    assert derive.keep_all(INT32_MAX) and not derive.keep_all(99)
