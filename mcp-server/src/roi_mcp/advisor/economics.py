"""Pure advisor derivations (PRD addendum section 4). Plain values in, plain dicts out; no snapshot access.

Every function returns its derivation id in `method`, its inputs, and a confidence record (contract.confidence)
WITHOUT snapshot-level factors; tools apply those through Advice.track. A value that cannot be computed is None,
never 0 or a default.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Iterable, Mapping

from .contract import clean, confidence, derived, lower, merge_confidence, num, parameter

DAYS_PER_MONTH = 30.0
EPS = 1e-9


def median(values: Iterable[float]) -> float | None:
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return None
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def ceil_count(x: float) -> int:
    """Buildings needed for a fractional requirement (tolerant to float noise: 2.0000000001 -> 2)."""
    return int(math.ceil(x - EPS)) if x > EPS else 0


# --------------------------------------------------------------------------- D-ADV-PRICE-1

def sale_price(shop_prices: Iterable[Any], market_final: Any, market_price: Any) -> dict:
    """Median shop price for the player (shops with demand), else market final price, else market price."""
    prices = sorted(p for p in (num(x) for x in shop_prices) if p is not None and p > 0)
    out = {"method": "D-ADV-PRICE-1", "inputs": {"shop_prices_count": len(prices), "market_final_price": clean(num(market_final)),
                                                  "market_price": clean(num(market_price))}}
    if prices:
        return {**out, "value": clean(median(prices)), "basis": "shop_median", "confidence": confidence("high")}
    mf, mp = num(market_final), num(market_price)
    if mf is not None and mf > 0:
        return {**out, "value": clean(mf), "basis": "market_final", "confidence": confidence("high", ["price_fallback"])}
    if mp is not None and mp > 0:
        return {**out, "value": clean(mp), "basis": "market_price", "confidence": confidence("high", ["price_fallback"])}
    return {**out, "value": None, "basis": None, "confidence": None}


# --------------------------------------------------------------------------- D-ADV-INPUTVAL-1

def input_value(market_final: Any, market_price: Any) -> dict:
    """Opportunity value of one unit of an input: market final price for the player, else market price."""
    mf, mp = num(market_final), num(market_price)
    if mf is not None and mf > 0:
        return {"method": "D-ADV-INPUTVAL-1", "value": clean(mf), "basis": "market_final", "confidence": confidence("medium")}
    if mp is not None and mp > 0:
        return {"method": "D-ADV-INPUTVAL-1", "value": clean(mp), "basis": "market_price",
                "confidence": confidence("medium", ["price_fallback"])}
    return {"method": "D-ADV-INPUTVAL-1", "value": None, "basis": None, "confidence": None}


# --------------------------------------------------------------------------- D-ADV-UPKEEPSHARE-1

def upkeep_per_unit(producers: Iterable[Mapping]) -> dict:
    """sum(upkeep_b * share_b) / sum(output_b) over producers with both values known.

    producers: {building, upkeep, output_per_30d, share (P's share of the recipe output, default 1),
                approximate (rate fallback), upkeep_fallback (monthly_full used)}"""
    tot_u = 0.0
    tot_o = 0.0
    used = 0
    skipped = []
    factors: list[str] = []
    for p in producers:
        u, o = num(p.get("upkeep")), num(p.get("output_per_30d"))
        if u is None or o is None:
            skipped.append(p.get("building"))
            continue
        share = num(p.get("share"))
        share = 1.0 if share is None else share
        tot_u += u * share
        tot_o += o
        used += 1
        if p.get("approximate"):
            factors.append("approximate_rate")
    if skipped and used:
        factors.append("partial_inputs")
    out = {"method": "D-ADV-UPKEEPSHARE-1",
           "inputs": {"producers_used": used, "producers_skipped": skipped, "upkeep_total_per_30d": clean(tot_u, 2),
                      "output_total_per_30d": clean(tot_o)}}
    if used == 0 or tot_o <= EPS:
        return {**out, "value": None, "confidence": None, "reason": "no producer with known upkeep and rate"}
    return {**out, "value": (tot_u / tot_o), "confidence": confidence("medium", factors)}


# --------------------------------------------------------------------------- D-ADV-UNITCOST-1

def unit_cost(upkeep_pu: Mapping, ingredients: Iterable[Mapping]) -> dict:
    """upkeep per unit + sum(amount_i / result_amount * value_i).

    ingredients: {product, per_unit (amount_i / result_amount_P), value (dict with value/confidence/basis)}"""
    rows = []
    missing = []
    confs = [upkeep_pu.get("confidence")]
    total_inputs = 0.0
    for ing in ingredients:
        v = ing.get("value") or {}
        val = num(v.get("value"))
        per = num(ing.get("per_unit"))
        rows.append({"product": ing.get("product"), "per_unit": clean(per), "unit_value": clean(val),
                     "basis": v.get("basis"), "cost_per_unit": clean(per * val) if per is not None and val is not None else None})
        if per is None or val is None:
            missing.append(ing.get("product"))
            continue
        total_inputs += per * val
        confs.append(v.get("confidence"))
    up = num(upkeep_pu.get("value"))
    if up is None:
        missing.insert(0, "upkeep_per_unit")
    out = {"method": "D-ADV-UNITCOST-1", "upkeep_per_unit": (up), "inputs_per_unit": (total_inputs) if not missing else None,
           "inputs": rows}
    if missing:
        return {**out, "value": None, "confidence": None, "missing": missing}
    conf = merge_confidence(c for c in confs if c)
    if conf["level"] == "high":
        conf = {"level": "medium", "factors": conf["factors"]}
    return {**out, "value": (up + total_inputs), "confidence": conf}


# --------------------------------------------------------------------------- D-ADV-DISTCOST-1

def distribution_cost(per_unit_costs: Iterable[Any]) -> dict:
    vals = [v for v in (num(x) for x in per_unit_costs) if v is not None]
    if not vals:
        return {"method": "D-ADV-DISTCOST-1", "value": None, "confidence": None, "routes": 0,
                "reason": "no configured outbound route with a known dispatch cost and capacity"}
    return {"method": "D-ADV-DISTCOST-1", "value": (sum(vals) / len(vals)), "routes": len(vals),
            "confidence": confidence("medium")}


# --------------------------------------------------------------------------- D-ADV-MARGIN-1

def margin(price: Mapping, cost: Mapping, dist: Mapping | None, volume_per_30d: Any, volume_basis: str | None) -> dict:
    p, c = num(price.get("value")), num(cost.get("value"))
    d = num((dist or {}).get("value"))
    out = {"method": "D-ADV-MARGIN-1", "distribution_included": d is not None, "volume_basis": volume_basis,
           "volume_per_30d": clean(num(volume_per_30d))}
    if p is None or c is None:
        return {**out, "per_unit": None, "per_30d": None, "confidence": None,
                "missing": [n for n, v in (("sale_price", p), ("unit_cost", c)) if v is None]}
    per_unit = p - c - (d or 0.0)
    confs = [price.get("confidence"), cost.get("confidence")] + ([dist.get("confidence")] if d is not None else [])
    conf = merge_confidence(x for x in confs if x)
    vol = num(volume_per_30d)
    if volume_basis == "theoretical":
        conf = lower(conf, "approximate_rate")
    if conf["level"] == "high":
        conf = {"level": "medium", "factors": conf["factors"]}
    return {**out, "per_unit": (per_unit), "per_30d": (per_unit * vol) if vol is not None else None,
            "confidence": conf}


# --------------------------------------------------------------------------- D-ADV-NEWRATE-1

def is_module_owner(btype: Mapping | None) -> bool:
    return bool(btype) and bool(btype.get("module_prefab")) and (num(btype.get("max_module_count")) or 0) > 0


def new_building_rate(recipe: Mapping, btype: Mapping | None, peer_rates: Iterable[Any] = (),
                      type_modifier: Mapping | None = None) -> dict:
    """Output per 30 days of ONE new building running `recipe` on `btype`, per result product.

    peer_rates are already normalized to the type's initial efficiency (they include the company modifier).
    type_modifier: {"value", "peers"} from D-ADV-TYPEMOD-1 (the player's buildings of this type), or None when the
    player owns none of the type or they disagree: then the company modifier is assumed 1 and flagged."""
    peers = [v for v in (num(x) for x in peer_rates) if v is not None and v > 0]
    results = recipe.get("results") or []
    base = {"method": "D-ADV-NEWRATE-1", "recipe": recipe.get("name"), "building_type": (btype or {}).get("name")}
    if peers:
        # peer rates are for the recipe's first result; other results scale by their amount ratio
        first = num(results[0].get("amount")) if results else None
        med = median(peers)
        per = {}
        for r in results:
            amt = num(r.get("amount"))
            per[r.get("product")] = (med * amt / first) if first and amt is not None else None
        return {**base, "per_30d": per, "basis": "observed_peer", "peers": len(peers), "cycle_days": None, "modules": None,
                "confidence": confidence("high")}
    days = num(recipe.get("game_days"))
    if days is None or days <= 0:
        return {**base, "per_30d": {r.get("product"): None for r in results}, "basis": None, "confidence": None,
                "reason": "recipe game_days unknown"}
    factors = ["approximate_rate"]
    speed = num((btype or {}).get("production_speed"))
    if speed is None or speed <= 0:
        speed = 1.0
        factors.append("partial_inputs")
    eff = 1.0
    arr = (btype or {}).get("efficiency_output") or []
    idx = (btype or {}).get("initial_efficiency_index")
    if isinstance(idx, int) and 0 <= idx < len(arr) and num(arr[idx]):
        eff = num(arr[idx])
    elif btype is not None:
        factors.append("partial_inputs")
    mod = num((type_modifier or {}).get("value"))
    mod_assumed = mod is None or mod <= 0
    if mod_assumed:
        mod = 1.0
        factors.append("mechanic_unverified")     # A-ACTOR-MODIFIER-1: live data shows types with modifier != 1
    cycle = days / (speed * eff * mod)
    modules = 1
    if is_module_owner(btype):
        modules = int(num(btype.get("max_module_count")) or 1)
        if not num(btype.get("max_module_count")):
            factors.append("partial_inputs")
    per = {r.get("product"): ((num(r.get("amount")) or 0.0) * DAYS_PER_MONTH / cycle * modules) for r in results}
    return {**base, "per_30d": per, "basis": "static_recipe", "cycle_days": clean(cycle), "modules": modules,
            "inputs": {"game_days": clean(days), "production_speed": clean(speed), "efficiency_multiplier": clean(eff),
                       "company_output_modifier": clean(mod),
                       "company_output_modifier_basis": "assumed_1" if mod_assumed else "observed_same_type"},
            "confidence": confidence("medium", factors)}


# --------------------------------------------------------------------------- D-ADV-NEWUPKEEP-1 / D-ADV-CAPEX-1

def upkeep_base(btype: Mapping, module_type: Mapping | None, modules: int) -> dict:
    """Monthly upkeep at efficiency 1, difficulty 1 and company modifier 1: base_cost x pct + modules x module cost x
    module pct. The catalogue does not export module upkeep percentages; the owner's percentage is used then
    (A-MODULE-UPKEEP-PCT, matched live on every module-owner type of the validation world)."""
    cost = num(btype.get("base_cost"))
    pct = num(btype.get("upkeep_cost_percentage"))
    if cost is None or pct is None:
        return {"value": None, "reason": "base_cost or upkeep_cost_percentage unknown", "assumptions": [], "factors": []}
    assumptions: list[str] = []
    factors: list[str] = []
    mod_part = 0.0
    if modules and module_type is not None:
        mc, mp = num(module_type.get("base_cost")), num(module_type.get("upkeep_cost_percentage"))
        if mp is None:
            mp = pct
            assumptions.append("A-MODULE-UPKEEP-PCT")
        if mc is None:
            return {"value": None, "reason": "module base_cost unknown", "assumptions": assumptions, "factors": ["partial_inputs"]}
        mod_part = modules * mc * mp
    elif modules:
        return {"value": None, "reason": "module type unknown", "assumptions": assumptions, "factors": ["partial_inputs"]}
    return {"value": cost * pct + mod_part, "building_part": cost * pct, "module_part": mod_part,
            "assumptions": assumptions, "factors": factors}


def new_building_upkeep(btype: Mapping, module_type: Mapping | None, modules: int, difficulty_upkeep: Any,
                        type_modifier: Mapping | None = None) -> dict:
    """D-ADV-NEWUPKEEP-1: max(upkeep_base x difficulty x company modifier x efficiency_upkeep[initial],
    base_cost x pct x min_upkeep). type_modifier as in new_building_rate (D-ADV-TYPEMOD-1)."""
    out = {"method": "D-ADV-NEWUPKEEP-1", "building_type": btype.get("name")}
    ub = upkeep_base(btype, module_type, modules)
    if ub["value"] is None:
        return {**out, "value": None, "confidence": None, "reason": ub["reason"], "assumptions": ub["assumptions"]}
    factors = list(ub["factors"])
    min_up = num(btype.get("min_upkeep"))
    arr = btype.get("efficiency_upkeep") or []
    idx = btype.get("initial_efficiency_index")
    eff = num(arr[idx]) if isinstance(idx, int) and 0 <= idx < len(arr) else None
    if eff is None:
        eff = 1.0
        factors.append("partial_inputs")
    diff = num(difficulty_upkeep)
    if diff is None:
        diff = 1.0
        factors.append("partial_inputs")
    mod = num((type_modifier or {}).get("value"))
    mod_assumed = mod is None or mod <= 0
    if mod_assumed:
        mod = 1.0
        factors.append("mechanic_unverified")     # A-ACTOR-MODIFIER-1: live data shows types with modifier != 1
    raw = ub["value"] * diff * mod * eff
    if min_up is None:
        factors.append("partial_inputs")      # floor unknown: no minimum applied, flagged
    floor = ub["building_part"] * (min_up if min_up is not None else 0.0)
    value = max(raw, floor)
    return {**out, "value": (value), "assumptions": ub["assumptions"],
            "inputs": {"base_cost": clean(num(btype.get("base_cost"))), "upkeep_cost_percentage": num(btype.get("upkeep_cost_percentage")),
                       "module_upkeep": clean(ub["module_part"], 2), "modules": modules, "difficulty_upkeep": clean(diff),
                       "efficiency_upkeep_multiplier": clean(eff), "company_upkeep_modifier": clean(mod),
                       "company_upkeep_modifier_basis": "assumed_1" if mod_assumed else "observed_same_type",
                       "min_upkeep": min_up, "floor": clean(floor, 2)},
            "confidence": confidence("medium", factors)}


def capex(player_cost: Any, base_cost: Any, modules: int = 0, module_player_cost: Any = None, module_base_cost: Any = None) -> dict:
    factors: list[str] = []
    c = num(player_cost)
    basis = "current_player_cost"
    if c is None:
        c = num(base_cost)
        basis = "base_cost"
        if c is not None:
            factors.append("price_fallback")
    if c is None:
        return {"method": "D-ADV-CAPEX-1", "value": None, "confidence": None, "reason": "build cost unknown"}
    mod_total = 0.0
    if modules:
        mc = num(module_player_cost)
        if mc is None:
            mc = num(module_base_cost)
            if mc is not None:
                factors.append("price_fallback")
        if mc is None:
            factors.append("partial_inputs")
        else:
            mod_total = modules * mc
    return {"method": "D-ADV-CAPEX-1", "value": (c + mod_total), "basis": basis,
            "inputs": {"building_cost": clean(c, 2), "modules": modules, "modules_cost": clean(mod_total, 2)},
            "confidence": confidence("high", factors)}


# --------------------------------------------------------------------------- D-ADV-PLAN-1

def plan_chain(product: str, target_per_30d: float, *, choose: Callable[[str], dict], spare: Mapping[str, float],
               max_depth: int = 8) -> dict:
    """Allocate a target through the recipe chain, using spare existing supply first (addendum 4.1 D-ADV-PLAN-1).

    choose(product) -> {"recipe": dict|None, "chosen_by": str, ...}. Returns per-product amounts; the caller turns
    new amounts into buildings (they depend on building types and live data)."""
    if not isinstance(target_per_30d, (int, float)) or not math.isfinite(target_per_30d) or target_per_30d <= 0:
        raise ValueError("target must be a finite number > 0")
    spare_left = {k: max(float(v), 0.0) for k, v in spare.items() if num(v) is not None}
    required: dict[str, float] = {}
    from_existing: dict[str, float] = {}
    new: dict[str, float] = {}
    depth_of: dict[str, int] = {}
    status: dict[str, str] = {}
    choices: dict[str, dict] = {}
    cycles: list[list[str]] = []
    order: list[str] = []

    def visit(p: str, amount: float, depth: int, stack: tuple) -> None:
        if p not in required:
            order.append(p)
            required[p] = 0.0
            from_existing[p] = 0.0
            new[p] = 0.0
            depth_of[p] = depth
        depth_of[p] = min(depth_of[p], depth)
        required[p] += amount
        use = min(spare_left.get(p, 0.0), amount)
        if use > 0:
            spare_left[p] = spare_left.get(p, 0.0) - use
            from_existing[p] += use
        rest = amount - use
        if rest <= EPS:
            status.setdefault(p, "uses_existing")
            return
        new[p] += rest
        if p in stack:
            cycles.append(list(stack[stack.index(p):]) + [p])
            status[p] = "cycle"
            return
        if depth >= max_depth:
            status[p] = "truncated"
            return
        ch = choices.get(p)
        if ch is None:
            ch = choices[p] = choose(p) or {"recipe": None, "chosen_by": "none"}
        recipe = ch.get("recipe")
        if recipe is None:
            status[p] = "unplannable"
            return
        res_amt = 0.0
        for r in recipe.get("results") or []:
            if r.get("product") == p:
                res_amt = num(r.get("amount")) or 0.0
        if res_amt <= 0:
            status[p] = "unplannable"
            return
        if status.get(p) not in ("cycle", "truncated"):
            status[p] = "ok"
        for ing in recipe.get("ingredients") or []:
            visit(ing.get("product"), rest * (num(ing.get("amount")) or 0.0) / res_amt, depth + 1, stack + (p,))

    visit(product, float(target_per_30d), 0, ())
    steps = []
    for p in order:
        steps.append({"product": p, "depth": depth_of[p], "required_per_30d": (required[p]),
                      "from_existing_per_30d": (from_existing[p]), "new_per_30d": (new[p]),
                      "status": status.get(p, "uses_existing" if new[p] <= EPS else "ok"),
                      "choice": choices.get(p)})
    return {"method": "D-ADV-PLAN-1", "target": {"product": product, "per_30d": clean(float(target_per_30d))},
            "steps": steps, "cycles": cycles,
            "complete": all(s["status"] in ("ok", "uses_existing") for s in steps)}


# --------------------------------------------------------------------------- D-ADV-DISPATCH-1

def dispatch_amount(inputs: Mapping, *, destination_stock: Any = None, destination_incoming: Any = None,
                    max_send: Any = "keep", min_keep: Any = "keep", keep_all: bool | None = None) -> dict:
    """Replica of the game's per-dispatch amount (PRD 12.3.3) from a route's exported inputs, optionally with
    max_send / min_keep replaced. `keep_all` True means Min Keep = infinity."""
    cap = num(inputs.get("vehicle_capacity"))
    stock = num(inputs.get("origin_stock"))
    free = num(inputs.get("free_space"))
    slots = num(inputs.get("destination_slots"))
    mk = inputs.get("min_keep") if min_keep == "keep" else min_keep
    ms = inputs.get("max_send") if max_send == "keep" else max_send
    wait = bool(inputs.get("wait_for_full_vehicle"))
    contract_room = num(inputs.get("contract_room"))
    mk_val = num(mk)
    is_keep_all = bool(keep_all) or (mk_val is not None and mk_val >= 2147483647)
    used = {"vehicle_capacity": clean(cap), "origin_stock": clean(stock), "min_keep": "keep_all" if is_keep_all else clean(mk_val),
            "free_space": clean(free), "max_send": clean(num(ms)), "destination_slots": clean(slots),
            "contract_room": clean(contract_room), "wait_for_full_vehicle": wait}
    out = {"method": "D-ADV-DISPATCH-1", "inputs": used}
    if cap is None or stock is None or free is None:
        return {**out, "value": None, "limited_by": [], "reason": "vehicle_capacity, origin_stock or free_space unknown"}
    if mk_val is None and not is_keep_all:
        return {**out, "value": None, "limited_by": [], "reason": "min_keep unknown"}
    if num(ms) is None:
        return {**out, "value": None, "limited_by": [], "reason": "max_send unknown (0 means unlimited; a missing value is not 0)"}
    available = 0.0 if is_keep_all else max(stock - (mk_val or 0.0), 0.0)
    eff_free = free
    max_send_room = None
    msv = num(ms) or 0.0
    if msv > 0:
        if slots is not None:
            in_dest = slots - free                       # stored + incoming put reservations (game: slots - FreeSpace)
        else:
            a, b = num(destination_stock), num(destination_incoming)
            in_dest = None if a is None else a + (b or 0.0)
        if in_dest is None:
            return {**out, "value": None, "limited_by": [], "reason": "destination stock unknown"}
        max_send_room = max(msv - in_dest, 0.0)
        eff_free = min(eff_free, max_send_room)
    if contract_room is not None:
        eff_free = min(eff_free, contract_room)
        cap = min(cap, eff_free)
    amount = min(cap, available, eff_free)
    limits = []
    if amount == available:
        limits.append("available")
    if amount == cap:
        limits.append("cap")
    if amount == eff_free:
        if max_send_room is not None and eff_free == max_send_room and max_send_room <= free:
            limits.append("max_send")
        elif contract_room is not None and eff_free == contract_room:
            limits.append("contract")
        else:
            limits.append("free_space")
    if wait and amount < cap:
        amount = 0.0
        limits = ["wait_full"]
    out["inputs"]["max_send_room"] = clean(max_send_room)
    return {**out, "value": clean(amount), "limited_by": limits, "confidence": confidence("high")}


# --------------------------------------------------------------------------- D-ADV-EFF-1

def efficiency_change(rates: Mapping[str, Any], upkeep_now: Any, eff_out: list, eff_upk: list, a: int, b: int,
                      base_cost: Any = None, pct: Any = None, min_upkeep: Any = None, difficulty_upkeep: Any = None) -> dict:
    """Move a building from efficiency index a to b."""
    out = {"method": "D-ADV-EFF-1", "from_index": a, "to_index": b}
    if not (0 <= a < len(eff_out) and 0 <= b < len(eff_out)):
        return {**out, "rates": None, "upkeep": None, "confidence": None, "reason": "efficiency index outside the type's arrays"}
    oa, ob = num(eff_out[a]), num(eff_out[b])
    factors: list[str] = []
    new_rates = {}
    for p, v in rates.items():
        rv = num(v)
        new_rates[p] = (rv * ob / oa) if rv is not None and oa and ob is not None else None
    if not oa or ob is None:
        factors.append("partial_inputs")
    up = num(upkeep_now)
    ua = num(eff_upk[a]) if a < len(eff_upk) else None
    ub = num(eff_upk[b]) if b < len(eff_upk) else None
    c, p_, mu, d = num(base_cost), num(pct), num(min_upkeep), num(difficulty_upkeep)
    floor = c * p_ * mu if None not in (c, p_, mu) else None
    new_up = None
    if up is not None and ua and ub is not None:
        new_up = up * ub / ua
    elif ub is not None and c is not None and p_ is not None:
        new_up = c * p_ * (d if d is not None else 1.0) * ub
        factors.append("mechanic_unverified")
    if new_up is not None and floor is not None:
        new_up = max(new_up, floor)
    if new_up is None:
        factors.append("partial_inputs")
    return {**out, "rate_multiplier": (ob / oa) if oa and ob is not None else None,
            "upkeep_multiplier": (ub / ua) if ua and ub is not None else None,
            "rates": new_rates, "upkeep": (new_up), "upkeep_floor": (floor),
            "confidence": confidence("high", factors)}


# --------------------------------------------------------------------------- D-ADV-ROUTE-RULES-1

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
ROUTE_FINDING_KINDS = ("route_error", "keep_all", "destination_rejects_product", "dead_city_destination", "paused",
                       "dormant_auto_wh", "zero_dispatch_now", "max_send_saturated", "high_unit_cost",
                       "underfilled_dispatch", "shared_max_send", "duplicate_route")


def route_findings(r: Mapping, sale_price: Any, threshold: float, sharing: int) -> list[dict]:
    """Findings for one raw route row (state.routes_player) per addendum 4.1 D-ADV-ROUTE-RULES-1."""
    out: list[dict] = []

    def add(kind: str, severity: str, evidence: dict, suggestion: str) -> None:
        out.append({"kind": kind, "severity": severity, "evidence": evidence, "suggestion": suggestion})

    errors = list(r.get("errors") or [])
    if errors or r.get("has_error"):
        add("route_error", "high", {"errors": errors, "validation_error": r.get("validation_error"), "path_status": r.get("path_status")},
            "Check the route in the game: the destination may be unreachable (no path) or invalid.")
    mk = r.get("min_keep") or {}
    if mk.get("keep_all"):
        add("keep_all", "high", {"min_keep": mk}, "Min Keep is set to keep everything: this route never dispatches. Lower Min Keep if it should ship.")
    if r.get("destination_accepts_product") is False:
        add("destination_rejects_product", "high", {"destination_accepts_product": False},
            "The destination does not accept this product: deliveries cannot be stored there.")
    if r.get("destination_dead_city"):
        add("dead_city_destination", "high", {"destination_dead_city": True}, "The destination's city is dead: shop demand is 0.")
    if r.get("paused"):
        add("paused", "medium", {"paused": True}, "The route is paused in the game.")
    if r.get("dormant_auto_warehouse"):
        add("dormant_auto_wh", "low", {"dormant_auto_warehouse": True},
            "AUTO_WH is set on the origin: its manual routes are dormant while the building pushes to its warehouse.")
    dan = r.get("dispatch_amount_now") or {}
    val = num(dan.get("value"))
    cap = num(r.get("vehicle_capacity"))
    already = {f["kind"] for f in out}
    if val == 0 and not ({"keep_all", "paused", "route_error", "dormant_auto_wh"} & already):
        limited = list(dan.get("limited_by") or [])
        sev = "low" if set(limited) <= {"available", "wait_full"} and limited else "medium"
        text = ("The next dispatch would request 0 units now; see limited_by (origin stock vs Min Keep, destination space, Max Send).")
        if "max_send" in limited:
            # live validation: routes held by Max Send while trucks were on the way were normal, not faulty
            text = ("The next dispatch would request 0 units now because the Max Send room is used by stock at the destination "
                    "and deliveries already on the way. This is normal while vehicles are in transit; it is a problem only "
                    "if it persists.")
        add("zero_dispatch_now", sev, {"dispatch_amount_now": clean(val), "limited_by": limited, "complete": dan.get("complete")}, text)
    ms = r.get("max_send") or {}
    if not ms.get("unlimited") and ms.get("headroom_now") == 0:
        add("max_send_saturated", "low", {"max_send": ms.get("value"), "headroom_now": 0, "routes_sharing_cap": sharing},
            "The destination has reached the Max Send cap (stock + incoming). The cap is shared by every origin shipping this product there.")
    dc = num(r.get("dispatch_cost"))
    price = num(sale_price)
    if dc is not None and cap and price:
        ratio = (dc / cap) / price
        if ratio >= threshold:
            add("high_unit_cost", "high" if ratio >= 2 * threshold else "medium",
                {"cost_per_unit_at_capacity": derived(dc / cap, "money/unit", "D-ROUTE-1"),
                 "sale_price": {"value": clean(price), "unit": "money/unit", "kind": "estimate", "method": "D-ADV-PRICE-1"},
                 "ratio": derived(ratio, "ratio", "cost_per_unit_at_capacity / sale_price"),
                 "threshold": parameter(threshold, "ratio"), "distance_tiles": r.get("distance_tiles"), "dispatch_cost": dc},
                "Transport costs a large share of the product's value on this route; a closer destination or a larger vehicle may be cheaper.")
    if val is not None and cap and 0 < val < 0.5 * cap and not r.get("wait_for_full_vehicle"):
        add("underfilled_dispatch", "low", {"dispatch_amount_now": clean(val), "vehicle_capacity": clean(cap),
                                            "cost_per_unit_now": derived(dc / val if dc is not None else None, "money/unit", "D-ROUTE-1")},
            "The next dispatch would leave less than half the vehicle used; each dispatch costs the same.")
    if sharing >= 2:
        add("shared_max_send", "info", {"routes_sharing_cap": sharing, "max_send": ms.get("value")},
            "Several origins ship this product to the same destination; changing Max Send on one changes it for all.")
    if (r.get("occurrence") or 0) > 0:
        add("duplicate_route", "low", {"occurrence": r.get("occurrence")},
            "Another slot of this origin ships the same product to the same destination.")
    return out
