"""Pure advisor derivations (PRD addendum 4): hand-computed unit tests, seeded property/invariant tests."""

from __future__ import annotations

import math
import random

import pytest

import build_fixtures as bf
from roi_mcp import derive
from roi_mcp.advisor import economics as eco
from roi_mcp.advisor.contract import LEVELS, confidence, lower, merge_confidence, quantity

RNG_SEEDS = range(25)


# ------------------------------------------------------------------ confidence (D-ADV-CONF-1)

def test_confidence_levels_and_factors():
    assert confidence("high") == {"level": "high", "factors": []}
    assert confidence("high", ["stale_data"])["level"] == "medium"
    assert confidence("high", ["stale_data", "price_fallback"])["level"] == "low"
    assert confidence("medium", ["stale_data", "price_fallback", "partial_inputs"]) == \
        {"level": "low", "factors": ["stale_data", "price_fallback", "partial_inputs"]}
    assert confidence("high", ["stale_data", "stale_data"])["level"] == "medium"     # distinct factors only
    with pytest.raises(AssertionError):
        confidence("high", ["made_up_factor"])
    assert merge_confidence([confidence("high"), confidence("medium", ["partial_inputs"])]) == \
        {"level": "low", "factors": ["partial_inputs"]}
    assert lower(confidence("high", ["stale_data"]), "stale_data")["level"] == "medium"   # same factor not counted twice


@pytest.mark.parametrize("seed", RNG_SEEDS)
def test_confidence_is_monotone_in_factors(seed):
    from roi_mcp.advisor.contract import FACTORS
    rng = random.Random(seed)
    fs = rng.sample(FACTORS, rng.randint(0, len(FACTORS)))
    for start in ("high", "medium"):
        prev = LEVELS.index(start)
        for i in range(len(fs) + 1):
            lvl = LEVELS.index(confidence(start, fs[:i])["level"])
            assert lvl <= prev
            prev = lvl


def test_quantity_never_emits_non_finite():
    assert quantity(math.nan, "money", "observed", source="x")["value"] is None
    assert quantity(math.inf, "money", "derived", method="m")["value"] is None
    assert quantity(True, "count", "observed", source="x")["value"] is None
    with pytest.raises(AssertionError):
        quantity(1, "furlongs", "observed", source="x")


# ------------------------------------------------------------------ D-ADV-PRICE-1 / INPUTVAL-1

def test_sale_price_basis_order():
    assert eco.sale_price([100, 300, 200], 50, 40)["value"] == 200
    assert eco.sale_price([100, 300, 200, 400], 50, 40)["value"] == 250            # even count: mean of middle two
    r = eco.sale_price([0, -5, None, "x"], 50, 40)
    assert r["value"] == 50 and r["basis"] == "market_final" and "price_fallback" in r["confidence"]["factors"]
    assert eco.sale_price([], None, 40)["basis"] == "market_price"
    r = eco.sale_price([], 0, None)
    assert r["value"] is None and r["confidence"] is None
    assert eco.sale_price([10], None, None)["confidence"] == {"level": "high", "factors": []}


def test_input_value():
    assert eco.input_value(96, 95)["value"] == 96
    assert eco.input_value(None, 95)["basis"] == "market_price"
    assert eco.input_value(None, None)["value"] is None


# ------------------------------------------------------------------ D-ADV-UPKEEPSHARE-1 / UNITCOST-1 / DISTCOST-1 / MARGIN-1

def test_upkeep_per_unit_hand_computed():
    # two Chemicals plants: 10000/month each, 3 units/30 d each -> 20000 / 6
    r = eco.upkeep_per_unit([{"building": "a", "upkeep": 10000, "output_per_30d": 3}, {"building": "b", "upkeep": 10000, "output_per_30d": 3}])
    assert r["value"] == pytest.approx(3333.3333)
    # multi-output share: 60 % of upkeep attributed to the product
    r = eco.upkeep_per_unit([{"building": "a", "upkeep": 1000, "output_per_30d": 6, "share": 0.6}])
    assert r["value"] == 100
    # a producer without upkeep is skipped and flagged partial
    r = eco.upkeep_per_unit([{"building": "a", "upkeep": 1000, "output_per_30d": 10}, {"building": "b", "upkeep": None, "output_per_30d": 5}])
    assert r["value"] == 100 and "partial_inputs" in r["confidence"]["factors"] and r["inputs"]["producers_skipped"] == ["b"]
    # zero output: not computable (no division by zero, no 0 substitute)
    assert eco.upkeep_per_unit([{"building": "a", "upkeep": 1000, "output_per_30d": 0}])["value"] is None
    assert eco.upkeep_per_unit([])["value"] is None


def test_unit_cost_and_margin_hand_computed():
    upk = {"value": 3333.3333, "confidence": confidence("medium")}
    gas = {"value": 96, "basis": "market_final", "confidence": confidence("medium")}
    cost = eco.unit_cost(upk, [{"product": "Gas", "per_unit": 1.5, "value": gas}])
    assert cost["value"] == pytest.approx(3477.3333) and cost["inputs_per_unit"] == 144
    dist = eco.distribution_cost([52.5, 52.5, 101.5625])
    assert dist["value"] == pytest.approx(68.8542, abs=1e-4)
    price = {"value": 610, "confidence": confidence("high")}
    m = eco.margin(price, cost, dist, 3, "produced_last_month")
    assert m["per_unit"] == pytest.approx(610 - 3477.3333 - 68.8542, abs=1e-3)
    assert m["per_30d"] == pytest.approx(m["per_unit"] * 3, abs=0.01)
    assert m["confidence"]["level"] == "medium"
    # missing input value -> unit cost null, never a partial sum
    cost2 = eco.unit_cost(upk, [{"product": "Gas", "per_unit": 1.5, "value": {"value": None}}])
    assert cost2["value"] is None and cost2["missing"] == ["Gas"] and cost2["inputs_per_unit"] is None
    assert eco.margin(price, cost2, dist, 3, "theoretical")["per_unit"] is None
    # no distribution route: margin excludes it and says so
    m2 = eco.margin(price, cost, eco.distribution_cost([]), 3, "theoretical")
    assert m2["distribution_included"] is False and m2["per_unit"] == pytest.approx(610 - 3477.3333, abs=1e-3)
    assert "approximate_rate" in m2["confidence"]["factors"]


@pytest.mark.parametrize("seed", RNG_SEEDS)
def test_unit_cost_is_linear_in_input_values(seed):
    rng = random.Random(seed)
    upk = {"value": rng.uniform(0, 5000), "confidence": confidence("medium")}
    ings = [{"product": f"p{i}", "per_unit": rng.uniform(0, 4), "value": {"value": rng.uniform(1, 2000), "confidence": confidence("medium")}}
            for i in range(rng.randint(0, 4))]
    base = eco.unit_cost(upk, ings)["value"]
    k = rng.uniform(1.1, 3)
    scaled = eco.unit_cost(upk, [{**i, "value": {**i["value"], "value": i["value"]["value"] * k}} for i in ings])["value"]
    assert scaled - upk["value"] == pytest.approx((base - upk["value"]) * k, rel=1e-3, abs=1e-2)


# ------------------------------------------------------------------ D-ADV-NEWRATE-1 / NEWUPKEEP-1 / CAPEX-1

def _static():
    return bf.static_data()


def _bt(name):
    return next(b for b in _static()["building_types"] if b["name"] == name)


def _recipe(name):
    return next(r for r in _static()["recipes"] if r["name"] == name)


def test_new_rate_static_recipe_and_peer():
    r = eco.new_building_rate(_recipe("Chemicals"), _bt("PetrochemicalFactory"))
    # 2 per 20 days at speed 1, efficiency multiplier eff_out[3] = 1.0 -> 3 per 30 d
    assert r["per_30d"] == {"Chemicals": 3} and r["basis"] == "static_recipe" and "approximate_rate" in r["confidence"]["factors"]
    g = eco.new_building_rate(_recipe("Gas"), _bt("GasWell"))
    assert g["per_30d"] == {"Gas": 18} and g["modules"] == 3       # 2 per 10 days x 3 modules
    p = eco.new_building_rate(_recipe("Chemicals"), _bt("PetrochemicalFactory"), [3.0, 2.0, 2.6])
    assert p["per_30d"] == {"Chemicals": 2.6} and p["basis"] == "observed_peer" and p["confidence"]["level"] == "high"
    bad = dict(_recipe("Chemicals"), game_days=0)
    assert eco.new_building_rate(bad, _bt("PetrochemicalFactory"))["per_30d"] == {"Chemicals": None}


def test_new_upkeep_and_capex():
    u = eco.new_building_upkeep(_bt("PaintFactory"), None, 0, 1.0)
    # 500000 x 0.025 x 1 x 1; the company modifier is not measured -> assumed 1, flagged (live validation LV-upkeep)
    assert u["value"] == 12500 and u["confidence"]["level"] == "low" and "mechanic_unverified" in u["confidence"]["factors"]
    assert u["inputs"]["company_upkeep_modifier_basis"] == "assumed_1"
    m = eco.new_building_upkeep(_bt("PaintFactory"), None, 0, 1.0, {"value": 1.2, "peers": 6})
    assert m["value"] == 15000 and m["confidence"]["level"] == "medium" and m["inputs"]["company_upkeep_modifier"] == 1.2
    assert m["inputs"]["company_upkeep_modifier_basis"] == "observed_same_type"
    u2 = eco.new_building_upkeep(_bt("GasWell"), _bt("GasPump"), 3, 1.5)
    # (120000 x 0.025 + 3 x 20000 x 0.025) x 1.5 x 1.0 = (3000 + 1500) x 1.5
    assert u2["value"] == 6750 and "mechanic_unverified" in u2["confidence"]["factors"]
    floor = eco.new_building_upkeep(dict(_bt("PaintFactory"), efficiency_upkeep=[0.0] * 7), None, 0, 1.0)
    assert floor["value"] == 12500 * 0.25                                       # min upkeep floor
    assert eco.new_building_upkeep(dict(_bt("PaintFactory"), base_cost=None), None, 0, 1)["value"] is None
    c = eco.capex(180000, 500000)
    assert c["value"] == 180000 and c["basis"] == "current_player_cost"
    c2 = eco.capex(None, 120000, 3, None, 20000)
    assert c2["value"] == 180000 and "price_fallback" in c2["confidence"]["factors"]
    assert eco.capex(None, None)["value"] is None


# ------------------------------------------------------------------ D-ADV-PLAN-1

def _chooser(products_recipes):
    def choose(p):
        return {"recipe": products_recipes.get(p), "chosen_by": "test"}
    return choose


PAINT_CHAIN = {"Paint": _recipe("Paints"), "Chemicals": _recipe("Chemicals"), "Dye": _recipe("Dye"),
               "Gas": _recipe("Gas"), "Flowers": _recipe("Flowers"), "Water": _recipe("Water")}


def test_plan_paint_oracle_without_spare():
    # PRD acceptance A2 oracle: 100 Paint needs 50 Chemicals, 100 Dye, 75 Gas (and 100 Flowers, 50 Water for Dye).
    plan = eco.plan_chain("Paint", 100, choose=_chooser(PAINT_CHAIN), spare={})
    new = {s["product"]: s["new_per_30d"] for s in plan["steps"]}
    assert new == {"Paint": 100, "Chemicals": 50, "Gas": 75, "Dye": 100, "Flowers": 100, "Water": 50}
    assert plan["complete"] and all(s["status"] == "ok" for s in plan["steps"])


def test_plan_uses_spare_first_and_shrinks_upstream():
    plan = eco.plan_chain("Paint", 100, choose=_chooser(PAINT_CHAIN), spare={"Chemicals": 20, "Gas": 1000})
    s = {x["product"]: x for x in plan["steps"]}
    assert s["Chemicals"]["from_existing_per_30d"] == 20 and s["Chemicals"]["new_per_30d"] == 30
    assert s["Gas"]["required_per_30d"] == 45 and s["Gas"]["new_per_30d"] == 0 and s["Gas"]["status"] == "uses_existing"


def test_plan_cycle_unplannable_truncated_and_bad_target():
    cyc = {"A": {"name": "ra", "results": [{"product": "A", "amount": 1}], "ingredients": [{"product": "B", "amount": 1}]},
           "B": {"name": "rb", "results": [{"product": "B", "amount": 1}], "ingredients": [{"product": "A", "amount": 1}]}}
    plan = eco.plan_chain("A", 10, choose=_chooser(cyc), spare={})
    assert plan["cycles"] and not plan["complete"] and any(s["status"] == "cycle" for s in plan["steps"])
    plan = eco.plan_chain("X", 10, choose=_chooser({}), spare={})
    assert plan["steps"][0]["status"] == "unplannable" and not plan["complete"]
    plan = eco.plan_chain("Paint", 10, choose=_chooser(PAINT_CHAIN), spare={}, max_depth=1)
    assert any(s["status"] == "truncated" for s in plan["steps"])
    for bad in (0, -1, math.nan, math.inf):
        with pytest.raises(ValueError):
            eco.plan_chain("Paint", bad, choose=_chooser(PAINT_CHAIN), spare={})


@pytest.mark.parametrize("seed", RNG_SEEDS)
def test_plan_invariants_match_d_req_1(seed):
    rng = random.Random(seed)
    target = rng.uniform(0.5, 5000)
    plan = eco.plan_chain("Paint", target, choose=_chooser(PAINT_CHAIN), spare={})
    req = derive.requirements("Paint", target, lambda p: (PAINT_CHAIN.get(p), "x"))
    for s in plan["steps"]:
        assert s["required_per_30d"] == pytest.approx(req["required_per_30d"][s["product"]], rel=1e-6)
        assert s["from_existing_per_30d"] + s["new_per_30d"] == pytest.approx(s["required_per_30d"], rel=1e-6, abs=1e-6)
    # linearity: doubling the target doubles every new amount when no spare exists
    plan2 = eco.plan_chain("Paint", target * 2, choose=_chooser(PAINT_CHAIN), spare={})
    for a, b in zip(plan["steps"], plan2["steps"]):
        assert b["new_per_30d"] == pytest.approx(2 * a["new_per_30d"], rel=1e-5, abs=1e-4)
    # spare never increases a new amount
    spare = {p: rng.uniform(0, 100) for p in PAINT_CHAIN}
    plan3 = eco.plan_chain("Paint", target, choose=_chooser(PAINT_CHAIN), spare=spare)
    for a, b in zip(plan["steps"], plan3["steps"]):
        assert b["new_per_30d"] <= a["new_per_30d"] + 1e-6


def test_ceil_count_tolerates_float_noise():
    assert eco.ceil_count(2.0000000001) == 2 and eco.ceil_count(2.01) == 3 and eco.ceil_count(0) == 0 and eco.ceil_count(-1) == 0


# ------------------------------------------------------------------ D-ADV-DISPATCH-1

def test_dispatch_replica_reproduces_every_fixture_route():
    for r in bf.player_routes():
        d = r["dispatch_amount_now"]
        rep = eco.dispatch_amount(d["inputs"], destination_stock=r["destination_stock"],
                                  destination_incoming=r["destination_incoming_reserved"])
        assert rep["value"] == d["value"], r["route_key"]


def test_dispatch_hand_computed_changes():
    inputs = {"vehicle_capacity": 10, "origin_stock": 12, "min_keep": 1, "free_space": 27, "max_send": 8,
              "destination_slots": 40, "wait_for_full_vehicle": False}
    assert eco.dispatch_amount(inputs)["value"] == 0                 # 8 - (40 - 27) < 0 -> room 0
    r = eco.dispatch_amount(inputs, max_send=20)
    assert r["value"] == 7 and r["limited_by"] == ["max_send"]      # room = 20 - 13
    assert eco.dispatch_amount(inputs, max_send=0)["value"] == 10    # unlimited: cap
    assert eco.dispatch_amount(inputs, max_send=0, min_keep=5)["value"] == 7     # available 12 - 5
    assert eco.dispatch_amount(inputs, max_send=0, keep_all=True)["value"] == 0
    assert eco.dispatch_amount({**inputs, "wait_for_full_vehicle": True}, max_send=0, min_keep=5) == \
        {**eco.dispatch_amount({**inputs, "wait_for_full_vehicle": True}, max_send=0, min_keep=5)}
    assert eco.dispatch_amount({**inputs, "wait_for_full_vehicle": True}, max_send=0, min_keep=5)["limited_by"] == ["wait_full"]
    r = eco.dispatch_amount({**inputs, "contract_room": 3}, max_send=0)
    assert r["value"] == 3 and "contract" in r["limited_by"]
    assert eco.dispatch_amount({**inputs, "free_space": None})["value"] is None
    assert eco.dispatch_amount({**inputs, "destination_slots": None}, max_send=20, destination_stock=9, destination_incoming=4)["value"] == 7


@pytest.mark.parametrize("seed", RNG_SEEDS)
def test_dispatch_properties(seed):
    rng = random.Random(seed)
    slots = rng.randint(1, 100)
    stored = rng.randint(0, slots)
    inputs = {"vehicle_capacity": rng.randint(1, 50), "origin_stock": rng.randint(0, 120), "min_keep": rng.randint(0, 99),
              "free_space": slots - stored, "max_send": rng.randint(0, 100), "destination_slots": slots,
              "wait_for_full_vehicle": rng.random() < 0.3}
    r = eco.dispatch_amount(inputs)
    v = r["value"]
    assert 0 <= v <= inputs["vehicle_capacity"]
    assert v <= max(inputs["origin_stock"] - inputs["min_keep"], 0)
    assert v <= inputs["free_space"]
    if inputs["wait_for_full_vehicle"]:
        assert v in (0, inputs["vehicle_capacity"])
    # raising Max Send (or making it unlimited) never lowers the amount
    hi = eco.dispatch_amount(inputs, max_send=inputs["max_send"] + rng.randint(1, 50))["value"]
    if inputs["max_send"]:
        assert hi >= v
    assert eco.dispatch_amount(inputs, max_send=0)["value"] >= v
    # raising Min Keep never raises it
    assert eco.dispatch_amount(inputs, min_keep=min(99, inputs["min_keep"] + rng.randint(0, 20)))["value"] <= v


# ------------------------------------------------------------------ D-ADV-EFF-1

def test_efficiency_change_hand_computed():
    eo = eu = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
    r = eco.efficiency_change({"Chemicals": 3}, 10000, eo, eu, 3, 5, 400000, 0.025, 0.25, 1.0)
    assert r["rates"] == {"Chemicals": 4.5} and r["upkeep"] == 15000 and r["rate_multiplier"] == 1.5
    low = eco.efficiency_change({"Chemicals": 3}, 10000, eo, eu, 3, 0, 400000, 0.025, 0.25, 1.0)
    assert low["upkeep"] == 2500 and low["rates"] == {"Chemicals": 0.75}         # floor 400000 x 0.025 x 0.25 = 2500
    zero_a = eco.efficiency_change({"X": 2}, 0, eo, [0.0] + eu[1:], 0, 3, 1000, 0.1, 0.25, 2.0)
    assert zero_a["upkeep"] == 200 and "mechanic_unverified" in zero_a["confidence"]["factors"]
    assert eco.efficiency_change({}, 1, eo, eu, 3, 9)["confidence"] is None


@pytest.mark.parametrize("seed", RNG_SEEDS)
def test_efficiency_round_trip(seed):
    rng = random.Random(seed)
    eo = eu = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
    a, b = rng.randrange(7), rng.randrange(7)
    rate, up = rng.uniform(0.1, 100), rng.uniform(1, 1e5)
    there = eco.efficiency_change({"p": rate}, up, eo, eu, a, b)
    back = eco.efficiency_change(there["rates"], there["upkeep"], eo, eu, b, a)
    assert back["rates"]["p"] == pytest.approx(rate, rel=1e-3)
    assert back["upkeep"] == pytest.approx(up, rel=1e-3)


# ------------------------------------------------------------------ D-ADV-ROUTE-RULES-1

def test_route_rules_on_fixture_routes():
    routes = {r["route_key"]: r for r in bf.player_routes()}
    keep_all = next(r for r in routes.values() if r["min_keep"]["keep_all"])
    kinds = {f["kind"] for f in eco.route_findings(keep_all, 96, 0.2, 1)}
    assert "keep_all" in kinds and "zero_dispatch_now" not in kinds              # keep_all explains the zero
    err = next(r for r in routes.values() if r["errors"])
    assert eco.route_findings(err, 1450, 0.2, 1)[0]["kind"] == "route_error"
    gw1 = routes[f"{bf.B_GW1}|Gas|{bf.B_PC1}|own|0"]
    f = {x["kind"]: x for x in eco.route_findings(gw1, 96, 0.2, 3)}
    # (250 + 22 x 10) x 1.25 / 10 = 58.75 per unit; 58.75 / 96 = 0.612 >= 2 x 0.2 -> high
    assert f["high_unit_cost"]["severity"] == "high"
    assert f["high_unit_cost"]["evidence"]["cost_per_unit_at_capacity"]["value"] == 58.75
    assert f["max_send_saturated"]["evidence"]["routes_sharing_cap"] == 3 and "shared_max_send" in f
    assert "high_unit_cost" not in {x["kind"] for x in eco.route_findings(gw1, 96, 0.9, 3)}
    assert "high_unit_cost" not in {x["kind"] for x in eco.route_findings(gw1, None, 0.2, 3)}   # no price: not evaluated
