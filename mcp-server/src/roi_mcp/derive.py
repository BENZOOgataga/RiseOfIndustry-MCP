"""Deterministic derivations of PRD section 15. Every result carries its derivation id in `method`.

The functions are pure: they take plain values / snapshot dicts and return plain dicts, so each
can be unit tested with hand-computed fixtures (T-6).
"""

from __future__ import annotations

import math
from typing import Any, Callable, Iterable, Mapping

from . import formula as formula_mod
from .util import INT32_MAX

DAYS_PER_PERIOD = 30.0


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
            return None
        return float(v)
    return None


def _clean(x: float | None, digits: int = 6) -> float | int | None:
    if x is None:
        return None
    r = round(x, digits)
    if r == int(r) and abs(r) < 1e15:
        return int(r)
    return r


# --------------------------------------------------------------------------- D-RATE-1

def rate_factory(recipe: Mapping | None, cycle_days_effective: Any, speed: float = 1.0) -> dict:
    """D-RATE-1: per-30-day output per result and input need per ingredient.

    `amount * 30 / cycle_days_effective` (game days; cycle_days_effective = GetFinalProductionTime()).
    """
    cycle = _num(cycle_days_effective)
    out: dict[str, Any] = {"method": "D-RATE-1", "cycle_days_effective": _clean(cycle), "outputs": [], "inputs": []}
    if recipe is None or cycle is None or cycle <= 0:
        out["available"] = False
        out["reason"] = "recipe_or_cycle_unknown"
        return out
    out["available"] = True
    for res in recipe.get("results") or []:
        out["outputs"].append({"product": res.get("product"), "amount_per_cycle": res.get("amount"),
                               "per_30d": _clean(float(res.get("amount") or 0) * DAYS_PER_PERIOD / cycle * speed)})
    for ing in recipe.get("ingredients") or []:
        out["inputs"].append({"product": ing.get("product"), "amount_per_cycle": ing.get("amount"),
                              "per_30d": _clean(float(ing.get("amount") or 0) * DAYS_PER_PERIOD / cycle * speed)})
    return out


# --------------------------------------------------------------------------- D-RATE-2

def harvester_speed(module: Mapping, depleted_modifier: float | None = None) -> tuple[float | None, bool]:
    """Replica of the harvester production speed:
    clamp(sum(node has amount ? 1 : depletedModifier) / maxResources * _efficiency, minGuaranteed, 1).

    Returns (speed, exact). Prefers the observer's own replica (`speed_replica`). When some nodes are
    depleted and the depleted modifier is not exported the speed cannot be computed exactly.
    """
    replica = _num(module.get("speed_replica"))
    if replica is not None:
        return replica, True
    eff = _num(module.get("efficiency"))
    max_res = _num(module.get("max_resources"))
    nodes = _num(module.get("nodes"))
    depleted = _num(module.get("nodes_depleted")) or 0.0
    min_g = _num(module.get("min_guaranteed_speed"))
    if eff is None or not max_res or nodes is None:
        return None, False
    if depleted > 0 and depleted_modifier is None:
        return None, False
    total = (nodes - depleted) * 1.0 + depleted * (depleted_modifier or 0.0)
    speed = total / max_res * eff
    lo = min_g if min_g is not None else 0.0
    return max(lo, min(speed, 1.0)), True


def rate_gatherer(recipe: Mapping | None, cycle_days_effective: Any, modules: Iterable[Mapping] | None,
                  hub_kind: str, module_count: int | None = None,
                  depleted_modifier: float | None = None) -> dict:
    """D-RATE-2: gatherer / farm theoretical output per 30 days.

    Sum over modules of amount * 30 / cycle * module_speed; module_speed is the harvester replica, or
    the field `_efficiency` for farms. Falls back to moduleCount * amount * 30 / cycle (approximate).
    """
    cycle = _num(cycle_days_effective)
    out: dict[str, Any] = {"method": "D-RATE-2", "cycle_days_effective": _clean(cycle), "outputs": [],
                           "approximate": False, "module_speeds": []}
    if recipe is None or cycle is None or cycle <= 0:
        out["available"] = False
        out["reason"] = "recipe_or_cycle_unknown"
        return out
    mods = list(modules or [])
    speeds: list[float] = []
    approximate = False
    if mods:
        for m in mods:
            if m.get("user_enabled") is False:
                speeds.append(0.0)
                out["module_speeds"].append({"module": m.get("key"), "speed": 0.0, "note": "module_disabled"})
                continue
            if hub_kind == "farm":
                sp = _num(m.get("efficiency"))
                exact = sp is not None
            else:
                sp, exact = harvester_speed(m, depleted_modifier)
            if sp is None or not exact:
                approximate = True
                sp = 1.0
            speeds.append(sp)
            out["module_speeds"].append({"module": m.get("key"), "speed": _clean(sp), "exact": bool(exact)})
    else:
        n = module_count or 0
        speeds = [1.0] * n
        approximate = True
    total_speed = sum(speeds)
    out["approximate"] = approximate
    out["available"] = True
    for res in recipe.get("results") or []:
        out["outputs"].append({"product": res.get("product"), "amount_per_cycle": res.get("amount"),
                               "per_30d": _clean(float(res.get("amount") or 0) * DAYS_PER_PERIOD / cycle * total_speed)})
    out["inputs"] = []
    for ing in recipe.get("ingredients") or []:
        out["inputs"].append({"product": ing.get("product"), "amount_per_cycle": ing.get("amount"),
                              "per_30d": _clean(float(ing.get("amount") or 0) * DAYS_PER_PERIOD / cycle * total_speed)})
    return out


# --------------------------------------------------------------------------- D-RATE-3

def uptime_ratio(frames_spent_producing: Any, production_frames: Any) -> dict:
    """D-RATE-3: framesSpentProducing / productionFrames."""
    a = _num(frames_spent_producing)
    b = _num(production_frames)
    value = None if a is None or not b else _clean(a / b)
    return {"value": value, "method": "D-RATE-3",
            "inputs": {"frames_spent_producing": frames_spent_producing, "production_frames": production_frames}}


# --------------------------------------------------------------------------- D-REQ-1

RecipeChooser = Callable[[str], tuple[Mapping | None, str]]


def requirements(product: str, target_per_30d: float, choose_recipe: RecipeChooser, max_depth: int = 12) -> dict:
    """D-REQ-1: theoretical upstream requirement for a target output of `product`.

    runs = T / result_amount(r, P); every ingredient i needs runs * amount(r, i); recurse. Products
    without a producing recipe, or whose recipe has no ingredients (gathered/farmed), are raw inputs.
    `choose_recipe(product)` returns (recipe dict or None, how_chosen).
    """
    required: dict[str, float] = {}
    raw: dict[str, float] = {}
    recipes_used: dict[str, dict] = {}
    cycles: list[list[str]] = []
    truncated: list[str] = []

    def visit(prod: str, amount: float, stack: list[str]) -> None:
        required[prod] = required.get(prod, 0.0) + amount
        if prod in stack:
            cycles.append(stack[stack.index(prod):] + [prod])
            return
        recipe, how = choose_recipe(prod)
        if recipe is None:
            raw[prod] = raw.get(prod, 0.0) + amount
            return
        result_amount = 0.0
        for res in recipe.get("results") or []:
            if res.get("product") == prod:
                result_amount = float(res.get("amount") or 0)
        ingredients = recipe.get("ingredients") or []
        if result_amount <= 0 or not ingredients:
            # Raw input (gathered/farmed or no usable recipe), also at the depth limit.
            recipes_used.setdefault(prod, {"recipe": recipe.get("name"), "chosen_by": how})
            raw[prod] = raw.get(prod, 0.0) + amount
            return
        if len(stack) >= max_depth:
            # Its inputs lie beyond the depth limit: reported, not silently dropped.
            truncated.append(prod)
            return
        recipes_used.setdefault(prod, {"recipe": recipe.get("name"), "chosen_by": how})
        runs = amount / result_amount
        for ing in ingredients:
            visit(ing.get("product"), runs * float(ing.get("amount") or 0), stack + [prod])

    visit(product, float(target_per_30d), [])
    return {
        "method": "D-REQ-1",
        "target": {"product": product, "per_30d": _clean(float(target_per_30d))},
        "required_per_30d": {k: _clean(v) for k, v in required.items()},
        "raw_inputs_total": {k: _clean(v) for k, v in raw.items()},
        "recipes_used": recipes_used,
        "cycles_detected": cycles,
        "truncated_at_depth": sorted(set(truncated)),
    }


# --------------------------------------------------------------------------- D-INV-1 / D-INV-2

def fill_ratio(count: Any, slots: Any) -> float | None:
    c = _num(count)
    s = _num(slots)
    if c is None or not s:
        return None
    return _clean(c / s)


def accumulation(role: str | None, count: Any, slots: Any, window_counts: list[tuple[str, int]] | None = None) -> dict:
    """D-INV-1: fill ratio and accumulation flag (output >= 90 % full, or count rose across the
    in-memory snapshot window; the window evidence is labelled with snapshot game dates)."""
    ratio = fill_ratio(count, slots)
    reasons = []
    if role == "output" and ratio is not None and ratio >= 0.9:
        reasons.append("output_fill_ratio_ge_0_9")
    window = None
    if window_counts and len(window_counts) >= 2:
        first_date, first = window_counts[0]
        last_date, last = window_counts[-1]
        window = {"from": {"game_date": first_date, "count": first}, "to": {"game_date": last_date, "count": last},
                  "snapshots": len(window_counts)}
        if last is not None and first is not None and last > first:
            reasons.append("count_rose_across_window")
    return {"method": "D-INV-1", "fill_ratio": ratio, "accumulating": bool(reasons), "reasons": reasons, "window": window}


def inventory_delta(count_t1: Any, date_t1: str | None, count_t2: Any, date_t2: str | None,
                    session_t1: str | None, session_t2: str | None) -> dict:
    """D-INV-2: count_t2 - count_t1 between two snapshots of the same world session only."""
    if session_t1 is None or session_t1 != session_t2:
        return {"method": "D-INV-2", "value": None, "reason": "different_world_session"}
    a, b = _num(count_t1), _num(count_t2)
    return {"method": "D-INV-2", "value": None if a is None or b is None else _clean(b - a),
            "from": {"game_date": date_t1, "count": count_t1}, "to": {"game_date": date_t2, "count": count_t2}}


# --------------------------------------------------------------------------- D-SHOP-1

def unmet_demand(demand_for_player: Any, player_delivered_stock: Any, consumption_interval_days: Any) -> dict:
    """D-SHOP-1: max(demand_for_player - player_delivered_stock, 0) per consumption interval;
    per 30 days = x 30 / consumption_interval_days."""
    d = _num(demand_for_player)
    s = _num(player_delivered_stock)
    interval = _num(consumption_interval_days)
    if d is None:
        return {"method": "D-SHOP-1", "per_interval": None, "per_30d": None, "unit": "units per consumption interval"}
    per_interval = max(d - (s or 0.0), 0.0)
    per_30 = None if not interval else per_interval * DAYS_PER_PERIOD / interval
    return {"method": "D-SHOP-1", "per_interval": _clean(per_interval), "per_30d": _clean(per_30),
            "consumption_interval_days": consumption_interval_days, "unit": "units per consumption interval",
            "assumes_player_delivered_stock_zero": s is None}


def per_interval_to_30d(value: Any, interval_days: Any) -> float | None:
    v = _num(value)
    i = _num(interval_days)
    if v is None or not i:
        return None
    return _clean(v * DAYS_PER_PERIOD / i)


# --------------------------------------------------------------------------- D-SUPDEM-1

def supply_demand_ratio(supply_per_30d: Any, internal_need_per_30d: Any, shop_demand_per_30d: Any) -> dict:
    s = _num(supply_per_30d) or 0.0
    denom = (_num(internal_need_per_30d) or 0.0) + (_num(shop_demand_per_30d) or 0.0)
    return {"method": "D-SUPDEM-1", "value": None if denom <= 0 else _clean(s / denom),
            "inputs": {"supply_per_30d": _clean(s), "internal_need_per_30d": internal_need_per_30d,
                       "shop_demand_per_30d": shop_demand_per_30d}}


# --------------------------------------------------------------------------- D-ROUTE-1 / D-ROUTE-2 / D-DIST-1

def route_unit_costs(dispatch_cost: Any, vehicle_capacity: Any, dispatch_amount: Any) -> dict:
    c = _num(dispatch_cost)
    cap = _num(vehicle_capacity)
    amt = _num(dispatch_amount)
    return {
        "method": "D-ROUTE-1",
        "cost_per_unit_at_capacity": None if c is None or not cap else _clean(c / cap, 4),
        "cost_per_unit_at_current_amount": None if c is None or not amt else _clean(c / amt, 4),
        "inputs": {"dispatch_cost": dispatch_cost, "vehicle_capacity": vehicle_capacity, "dispatch_amount_now": dispatch_amount},
    }


def distances(x0: Any, y0: Any, x1: Any, y1: Any) -> dict:
    """D-DIST-1: Euclidean and Chebyshev tile distance (estimates, not path lengths)."""
    if None in (x0, y0, x1, y1):
        return {"method": "D-DIST-1", "euclidean": None, "chebyshev": None, "estimate": True}
    dx, dy = float(x1) - float(x0), float(y1) - float(y0)
    return {"method": "D-DIST-1", "euclidean": _clean(math.hypot(dx, dy), 3),
            "chebyshev": _clean(max(abs(dx), abs(dy))), "estimate": True}


def straight_line_cost_estimate(formula_text: str | None, euclidean_tiles: Any, difficulty_dispatch: Any,
                                actor_modifier: float = 1.0, formula_name: str = "ManualDestinationDispatchCost",
                                route_exists: bool | None = False) -> dict:
    """D-ROUTE-2: evaluate the ManualDestinationDispatchCost formula text with the straight-line
    (Euclidean) distance, difficulty.dispatch and actor = 1. Never for a destination WITH a known route:
    route_exists is False when no route exists, None when whether one exists is unknown."""
    base = {"distance_kind": "straight_line", "route_exists": route_exists, "authoritative": False,
            "formula": formula_name, "method": "D-ROUTE-2"}
    dist = _num(euclidean_tiles)
    diff = _num(difficulty_dispatch)
    if not formula_text or dist is None or diff is None:
        return {**base, "value": None, "reason": "formula_or_inputs_unavailable",
                "inputs": {"distance": euclidean_tiles, "difficulty": difficulty_dispatch, "actor": actor_modifier}}
    try:
        value = formula_mod.evaluate(formula_text, {"distance": dist, "difficulty": diff, "actor": actor_modifier})
    except formula_mod.FormulaError as exc:
        return {**base, "value": None, "reason": f"formula_error: {exc}",
                "inputs": {"distance": dist, "difficulty": diff, "actor": actor_modifier}}
    return {**base, "value": _clean(value, 2), "formula_text": formula_text,
            "inputs": {"distance": _clean(dist, 3), "difficulty": diff, "actor": actor_modifier}}


# --------------------------------------------------------------------------- D-VAL-1

BUNDLE_PRICE_CAP = 999_000_000


def company_value(value_inputs: Iterable[Mapping] | None, bundles_owned_by_competitors: Any) -> dict:
    """D-VAL-1: sum over fully-permitted regions of (permit cost + paid_to_build in region) x
    max(1, 1 + 0.1 x bundles owned by competitors); bundle price = min(round(0.1 x value), 999000000)."""
    if value_inputs is None:
        return {"method": "D-VAL-1", "value": None, "reason": "value_inputs_unavailable"}
    regions_value = 0.0
    rows = []
    for row in value_inputs:
        pc = _num(row.get("permit_cost")) or 0.0
        pb = _num(row.get("paid_to_build_in_region")) or 0.0
        regions_value += pc + pb
        rows.append({"region_id": row.get("region_id"), "permit_cost": row.get("permit_cost"),
                     "paid_to_build_in_region": row.get("paid_to_build_in_region")})
    comp = _num(bundles_owned_by_competitors) or 0.0
    multiplier = max(1.0, 1.0 + 0.1 * comp)
    value = regions_value * multiplier
    # PRD-ambiguity: PRD D-VAL-1 prices a bundle from the company value; research notes price the
    # sell bundle from RegionsValue. The PRD formula is used and regions_value is returned as input.
    bundle_price = min(round(0.1 * value), BUNDLE_PRICE_CAP)
    return {"method": "D-VAL-1", "value": _clean(value, 2), "bundle_price": bundle_price,
            "inputs": {"regions_value": _clean(regions_value, 2), "bundles_owned_by_competitors": bundles_owned_by_competitors,
                       "multiplier": _clean(multiplier), "regions": rows}}


# --------------------------------------------------------------------------- D-PERMIT-1

def permit_cost(tiles: Any, cost_per_tile_top_level: Any, cost_modifier: Any, has_city: bool) -> dict:
    """D-PERMIT-1: round(tiles x costPerTile(top-level parent) x costModifier x (city ? 1 : 0.75))."""
    t, c, m = _num(tiles), _num(cost_per_tile_top_level), _num(cost_modifier)
    if t is None or c is None or m is None:
        return {"method": "D-PERMIT-1", "value": None, "reason": "inputs_unavailable"}
    value = round(t * c * m * (1.0 if has_city else 0.75))
    return {"method": "D-PERMIT-1", "value": int(value),
            "inputs": {"tiles": tiles, "cost_per_tile": cost_per_tile_top_level, "cost_modifier": cost_modifier,
                       "city_factor": 1.0 if has_city else 0.75}}


# --------------------------------------------------------------------------- D-GROW-1

GROWTH_STATES = ("WaitingForSponsor", "Bloated", "Prospering", "Growing", "Stagnating")


def growth_state(growth: Mapping | None, population_limit_reached: Any) -> dict:
    """D-GROW-1: WaitingForSponsor -> Bloated -> Prospering -> Growing -> Stagnating (UI order).

    Uses the observer's `growth.state` when present, else derives it from the exported inputs."""
    if growth is None:
        return {"method": "D-GROW-1", "value": None, "reason": "growth_inputs_unavailable"}
    observed = growth.get("state")
    if observed in GROWTH_STATES:
        return {"method": "D-GROW-1", "value": observed, "source": "observer"}
    consumed = _num(growth.get("consumed_products"))
    gth = _num(growth.get("growth_threshold"))
    pth = _num(growth.get("prosperity_threshold"))
    waiting = growth.get("waiting_for_sponsor")
    prospering = growth.get("prospering")
    if prospering is None and consumed is not None and pth is not None:
        prospering = consumed >= pth
    growing = growth.get("growing")
    if growing is None and consumed is not None and gth is not None:
        growing = consumed >= gth
    inputs = {"waiting_for_sponsor": waiting, "population_limit_reached": population_limit_reached,
              "prospering": prospering, "growing": growing, "consumed_products": growth.get("consumed_products"),
              "growth_threshold": growth.get("growth_threshold"), "prosperity_threshold": growth.get("prosperity_threshold")}
    if waiting is None or population_limit_reached is None or prospering is None or growing is None:
        # Evaluate as far as the known inputs allow, in UI order.
        for name, flag in (("WaitingForSponsor", waiting), ("Bloated", population_limit_reached),
                           ("Prospering", prospering), ("Growing", growing)):
            if flag is True:
                return {"method": "D-GROW-1", "value": name, "source": "derived", "inputs": inputs}
            if flag is None:
                return {"method": "D-GROW-1", "value": None, "reason": "growth_inputs_incomplete", "inputs": inputs}
        return {"method": "D-GROW-1", "value": "Stagnating", "source": "derived", "inputs": inputs}
    if waiting:
        state = "WaitingForSponsor"
    elif population_limit_reached:
        state = "Bloated"
    elif prospering:
        state = "Prospering"
    elif growing:
        state = "Growing"
    else:
        state = "Stagnating"
    return {"method": "D-GROW-1", "value": state, "source": "derived", "inputs": inputs}


# --------------------------------------------------------------------------- D-LOAN-1

def loan_monthly_payment(principal: Any, apr: Any, duration_months: Any, modifier: float = 1.0) -> dict:
    """D-LOAN-1: amount x (1 + apr) / duration x modifier (modifier 1 unless exported)."""
    p, a, d = _num(principal), _num(apr), _num(duration_months)
    if p is None or a is None or not d:
        return {"method": "D-LOAN-1", "value": None, "reason": "inputs_unavailable_or_zero_duration"}
    return {"method": "D-LOAN-1", "value": _clean(p * (1.0 + a) / d * modifier, 2),
            "inputs": {"principal": principal, "apr": apr, "duration_months": duration_months, "modifier": modifier}}


# --------------------------------------------------------------------------- D-FIN-1

def month_over_month(months: list[Mapping]) -> list[dict]:
    """D-FIN-1: per-category delta between consecutive months (chronological order) and each
    category's share of the total net change."""
    out = []
    for prev, cur in zip(months, months[1:]):
        prev_cats = {c["category"]: c for c in prev.get("by_category", [])}
        cur_cats = {c["category"]: c for c in cur.get("by_category", [])}
        total_change = (cur.get("net") or 0) - (prev.get("net") or 0)
        rows = []
        for cat in sorted(set(prev_cats) | set(cur_cats)):
            p = prev_cats.get(cat, {})
            c = cur_cats.get(cat, {})
            d_income = (c.get("income") or 0) - (p.get("income") or 0)
            d_expense = (c.get("expense") or 0) - (p.get("expense") or 0)
            d_net = (c.get("net") or 0) - (p.get("net") or 0)
            share = None if not total_change else _clean(d_net / total_change, 4)
            rows.append({"category": cat, "income_delta": _clean(d_income, 2), "expense_delta": _clean(d_expense, 2),
                         "net_delta": _clean(d_net, 2), "share_of_total_change": share})
        rows.sort(key=lambda r: -abs(r["net_delta"] or 0))
        out.append({"from": prev.get("month"), "to": cur.get("month"), "net_change": _clean(total_change, 2),
                    "by_category": rows, "method": "D-FIN-1",
                    "to_is_month_to_date": bool(cur.get("current_month_to_date"))})
    return out


# --------------------------------------------------------------------------- D-STATUS-1

# PRD-ambiguity: list_buildings filters on status working|idle|disabled|blocked while D-STATUS-1 has
# finer reasons; every non-working reason other than disabled/blocked is classed as "idle"
# (derived_status keeps the exact reason).
STATUS_CLASS = {"disabled": "disabled", "blocked": "blocked", "working": "working"}


def building_status(b: Mapping, recipe: Mapping | None, is_recipe_user: bool) -> dict:
    """D-STATUS-1 (PRD 15.6). All applicable reasons are reported as evidence; the first one in the
    normative order sets derived_status."""
    flags = b.get("flags") or {}
    evidence: list[dict] = []
    inventory = {i.get("product"): i for i in (b.get("inventory") or [])}
    if flags.get("user_enabled") is False:
        evidence.append({"reason": "disabled", "user_enabled": False})
    if is_recipe_user and not b.get("recipe"):
        evidence.append({"reason": "no_recipe"})
    modules = b.get("modules")
    if modules is not None and (modules.get("max") or 0) > 0 and (modules.get("count") or 0) == 0:
        evidence.append({"reason": "no_modules", "module_count": modules.get("count"), "max": modules.get("max")})
    if flags.get("requirements_met") is False:
        evidence.append({"reason": "blocked", "requirements_met": False, "notifications": list(b.get("notifications") or [])})
    working = bool(flags.get("is_working"))
    if recipe is not None and "inventory" in b:
        if not working:
            for ing in recipe.get("ingredients") or []:
                inv = inventory.get(ing.get("product"))
                count = inv.get("count") if inv else 0
                if count is not None and count < (ing.get("amount") or 0):
                    evidence.append({"reason": "missing_input", "product": ing.get("product"), "count": count,
                                     "needed_per_cycle": ing.get("amount")})
        for res in recipe.get("results") or []:
            inv = inventory.get(res.get("product"))
            if inv is None:
                continue
            slots, count = inv.get("slots"), inv.get("count")
            if slots is not None and count is not None and slots - count < (res.get("amount") or 0):
                evidence.append({"reason": "output_full", "product": res.get("product"), "count": count, "slots": slots,
                                 "result_per_cycle": res.get("amount"), "approximation": "ignores reservations"})
    if b.get("is_polluted"):
        evidence.append({"reason": "polluted", "pollution_at_tile": b.get("pollution_at_tile")})
    if modules is not None and modules.get("items"):
        items = modules["items"]
        known = [m for m in items if m.get("deposit_remaining") is not None or m.get("nodes") is not None]
        # nodes == 0: the module sits on no deposit (crop fields, water, sand), so it cannot be depleted; its
        # deposit_remaining is 0 by construction (live validation V1.1: 10 working farms/gatherers were flagged).
        if known and len(known) == len(items) and all(
            (m.get("nodes") and m.get("nodes_depleted") == m.get("nodes"))
            or (m.get("nodes") != 0 and m.get("deposit_remaining") == 0)
            for m in known
        ):
            evidence.append({"reason": "deposit_depleted", "modules": len(items)})
    if working:
        evidence.append({"reason": "working", "is_working": True})
    else:
        evidence.append({"reason": "idle", "is_working": False})
    derived = evidence[0]["reason"]
    return {"method": "D-STATUS-1", "derived_status": derived,
            "status_class": STATUS_CLASS.get(derived, "idle"), "evidence": evidence}


# --------------------------------------------------------------------------- misc

def keep_all(min_keep_value: Any) -> bool:
    return isinstance(min_keep_value, int) and min_keep_value >= INT32_MAX
