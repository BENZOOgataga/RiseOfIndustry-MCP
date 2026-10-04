"""Phase-2 pure calculators: research graph, loan schedule, spatial maths, forecasts (unit, boundary, property)."""

from __future__ import annotations

import math
import random

import pytest

import build_fixtures as bf
from roi_mcp.advisor import calculators as calc
from roi_mcp.advisor import research as rs

UNLOCKS = {u["name"]: u for u in bf.static_data()["tech_unlocks"]}
FORMULAS = {f["name"]: f["text"] for f in bf.static_data()["formulas"]}


# ------------------------------------------------------------------ research graph

def test_chain_order_and_already_unlocked():
    ch = rs.chain("Polymers", UNLOCKS, {"BasicGas", "Petrochemistry"})
    assert ch["remaining"] == ["Plastics", "Polymers"] and ch["already_unlocked"] == ["Petrochemistry"]
    ch = rs.chain("AdvancedPaints", UNLOCKS, set())
    # prerequisites before dependents; catalogue order of `required` (Petrochemistry, Dyes) is kept
    assert ch["remaining"] == ["Petrochemistry", "Dyes", "Paints", "AdvancedPaints"]
    assert rs.chain("BasicGas", UNLOCKS, set())["remaining"] == []                  # unlocked by default


def test_chain_cycle_unknown_and_duplicates():
    u = {"A": {"name": "A", "required": ["B", "C"]}, "B": {"name": "B", "required": ["A"]},
         "C": {"name": "C", "required": ["Z", "D"]}, "D": {"name": "D", "required": []}}
    ch = rs.chain("A", u, set())
    assert ch["cycles"] == [["A", "B", "A"]] and ch["unknown"] == ["Z"]
    assert ch["remaining"].count("D") == 1 and ch["remaining"][-1] == "A"


@pytest.mark.parametrize("seed", range(20))
def test_chain_is_topological_on_random_dags(seed):
    rng = random.Random(seed)
    n = rng.randint(2, 25)
    u = {f"t{i}": {"name": f"t{i}", "required": sorted({f"t{j}" for j in rng.sample(range(i), min(i, rng.randint(0, 3)))})}
         for i in range(n)}
    unlocked = {f"t{i}" for i in range(n) if rng.random() < 0.3}
    ch = rs.chain(f"t{n - 1}", u, unlocked)
    pos = {x: i for i, x in enumerate(ch["remaining"])}
    for x in ch["remaining"]:
        for r in u[x]["required"]:
            assert r in unlocked or pos[r] < pos[x]
    assert not (set(ch["remaining"]) & unlocked) and len(pos) == len(ch["remaining"])
    assert rs.chain(f"t{n - 1}", u, unlocked) == ch                                  # deterministic


def test_node_costs_observed_calibrated_and_missing():
    observed = {"Plastics": {"unlock": "Plastics", "daily_cost": 3333.33, "days": 1680.0}}
    r = rs.node_costs(["Plastics", "Polymers"], UNLOCKS, FORMULAS, observed, 1.0)
    p, q = r["nodes"]
    assert p["basis"] == "game_computed" and p["days"] == 1680 and p["cost"] == pytest.approx(3333.33 * 1680)
    # Polymers tier 4: (60 + 64 x 60) / 1 = 3900 days, calibration factor 1680/1680 = 1
    assert q["basis"] == "formula_calibrated" and q["days"] == pytest.approx(3900) and q["confidence"]["level"] == "medium"
    assert r["complete"] and r["total_days"] == pytest.approx(5580)
    unc = rs.node_costs(["Polymers"], UNLOCKS, FORMULAS, {}, 2.5)
    assert unc["nodes"][0]["basis"] == "formula_uncalibrated" and unc["nodes"][0]["days"] == pytest.approx(3900 / 2.5)
    assert unc["nodes"][0]["confidence"]["level"] == "low"
    zero = rs.node_costs(["Polymers"], UNLOCKS, FORMULAS, {}, 0)
    assert zero["nodes"][0]["days"] is None and zero["total_days"] is None and not zero["complete"]
    nof = rs.node_costs(["Polymers"], UNLOCKS, {}, {}, 1.0)
    assert nof["nodes"][0]["cost"] is None and "formula" in nof["nodes"][0]["reason"]
    act = rs.node_costs(["AdvancedPaints"], UNLOCKS, FORMULAS, {}, 1.0, "AdvancedPaints", 324.0, 1080000.0)
    assert act["nodes"][0]["basis"] == "game_active_remaining" and act["total_cost"] == 1080000


def test_formula_value_is_safe():
    assert rs.formula_value("(60 + (tier ^ 3) * 60) / efficiency", 2, 1) == 540
    assert rs.formula_value("__import__('os')", 1, 1) is None
    assert rs.formula_value("1 / efficiency", 1, 0) is None
    assert rs.formula_value("unknown_var * 2", 1, 1) is None


# ------------------------------------------------------------------ loans (D-LOAN-SCHED-1)

def test_loan_schedule_hand_computed():
    s = calc.loan_schedule(1_000_000, 0.08, 60)
    assert s["payment"] == pytest.approx(18000) and s["total_repayment"] == pytest.approx(1_080_000)
    assert s["financing_cost"] == pytest.approx(80000) and s["first_payment_month_offset"] == 1
    assert len(s["schedule"]) == 13 and s["rows_omitted"] == 47 and s["schedule"][-1]["payment_number"] == 60
    assert s["schedule"][0]["remaining_balance_after"] == pytest.approx(18000 * 59)
    assert s["schedule"][-1]["remaining_balance_after"] == 0 and s["schedule"][-1]["early_repay_after"] == 0
    g = calc.loan_schedule(7_500_000, 0.0, 120, grace=24)
    assert g["financing_cost"] == 0 and g["first_payment_month_offset"] == 25 and g["last_payment_month_offset"] == 144
    one = calc.loan_schedule(100, 0.5, 1)
    assert one["payment"] == 150 and one["rows_omitted"] == 0
    big = calc.loan_schedule(1e12, 10, 1200, max_rows=120)
    assert math.isfinite(big["payment"]) and len(big["schedule"]) == 121
    m = calc.loan_schedule(1000, 0.1, 10, modifier=1.5)
    assert m["payment"] == pytest.approx(165)


@pytest.mark.parametrize("args", [(0, 0.1, 10), (-5, 0.1, 10), (100, -0.1, 10), (100, 0.1, 0), (100, 0.1, 10, -1), (100, 0.1, 10, 0, 0)])
def test_loan_schedule_rejects_invalid(args):
    with pytest.raises(ValueError):
        calc.loan_schedule(*args)


@pytest.mark.parametrize("seed", range(20))
def test_loan_schedule_invariants(seed):
    rng = random.Random(seed)
    p, apr, d, g = rng.uniform(1, 1e8), rng.uniform(0, 1), rng.randint(1, 600), rng.randint(0, 48)
    s = calc.loan_schedule(p, apr, d, g)
    assert s["total_repayment"] == pytest.approx(p * (1 + apr))
    assert s["financing_cost"] >= -1e-6
    assert s["last_payment_month_offset"] - s["first_payment_month_offset"] + 1 == d
    bal = [r["remaining_balance_after"] for r in s["schedule"]]
    assert bal == sorted(bal, reverse=True)


# ------------------------------------------------------------------ spatial (D-SPATIAL-1)

def test_distances_and_median():
    assert calc.euclid((0, 0), (3, 4)) == 5 and calc.chebyshev((0, 0), (3, 4)) == 4
    assert calc.geometric_median([((5, 5), 1.0)])["point"] == (5.0, 5.0)
    m = calc.geometric_median([((0, 0), 1), ((10, 0), 1), ((5, 10), 1)])
    assert m["converged"] and 4.9 < m["point"][0] < 5.1
    heavy = calc.geometric_median([((0, 0), 100), ((10, 0), 1)])
    assert calc.euclid(heavy["point"], (0, 0)) < 1e-3                # dominated by the heavy point
    assert calc.geometric_median([((1, 1), 0)])["point"] is None
    assert calc.weighted_sum((0, 0), [((3, 4), 2), ((0, 1), 1)]) == 11


def test_detour_range():
    r = calc.detour_range([(22, 20), (30, 20), (40, 20), (10, 3)])
    assert r["available"] and r["samples"] == 3 and r["min"] == 1.1 and r["max"] == 2.0  # euclid 3 < 5 ignored
    assert not calc.detour_range([(22, 20), (None, 20)])["available"]
    assert not calc.detour_range([(0, 20), (10, 20)])["available"]


# ------------------------------------------------------------------ forecasts (D-FORECAST-1)

def test_cash_forecast():
    f = calc.cash_forecast(2_500_000, [118000, -363000, 156000])
    assert f["projection"] == "exhaustion"
    assert f["months_to_zero_range"][0] == pytest.approx(2_500_000 / 363000)
    assert f["months_to_zero_range"][1] == pytest.approx(2_500_000 / (89000 / 3))
    assert calc.cash_forecast(1000, [5, 10])["projection"] == "not_projected"
    assert calc.cash_forecast(-1, [5, -10])["already_negative"] is True
    assert calc.cash_forecast(1000, [-5])["available"] is False
    assert calc.cash_forecast(None, [-5, -6])["available"] is False
    assert calc.cash_forecast(1000, [-5, None])["available"] is False
    pos_mean = calc.cash_forecast(1000, [-10, 100])
    assert pos_mean["months_to_zero_range"] == [100.0, None]          # mean >= 0: no optimistic bound


def test_stock_forecast():
    f = calc.stock_forecast([(10, 5), (12, 9), (14, 13)], 40)
    assert f["trend"] == "filling" and f["rate_per_day"] == 2 and f["days_to_full"] == pytest.approx(27 / 2)
    d = calc.stock_forecast([(10, 20), (20, 10)], 40)
    assert d["trend"] == "depleting" and d["days_to_empty"] == pytest.approx(10)
    assert calc.stock_forecast([(10, 5), (10, 6)], 40)["available"] is False      # same game day twice
    assert calc.stock_forecast([(10, 5), (11, 5)], 40)["trend"] == "stable"
    assert calc.stock_forecast([(10, 5), (11, 6)], None)["days_to_full"] is None
    assert calc.stock_forecast([], 40)["available"] is False
