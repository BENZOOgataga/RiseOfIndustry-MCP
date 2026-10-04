"""Research graph traversal and costing (phase-2 addendum D-RES-PATH-1, D-RES-SCORE-1). Pure functions.

Formula texts are computed with the server's safe arithmetic parser (roi_mcp.formula), never with Python code
execution.
"""

from __future__ import annotations

from typing import Any, Mapping

from .. import formula as formula_mod
from .contract import confidence, num
from .economics import median

ALTERNATIVES_REASON = ("prerequisites are conjunctive (requiredUnlocks); the game queues every missing prerequisite "
                       "(GetUnlockChain), so the research graph offers no alternative path")


def is_unlocked(name: str, unlocks: Mapping[str, dict], unlocked: set) -> bool:
    return name in unlocked or bool((unlocks.get(name) or {}).get("unlocked_by_default"))


def chain(target: str, unlocks: Mapping[str, dict], unlocked: set) -> dict:
    """Missing prerequisites (transitively) in deterministic topological order, then the target.

    Depth-first over `required` in catalogue order; a node appears after all of its prerequisites."""
    order: list[str] = []
    already: list[str] = []
    cycles: list[list[str]] = []
    unknown: list[str] = []
    state: dict[str, int] = {}          # 1 visiting, 2 done

    def visit(n: str, stack: tuple) -> None:
        if state.get(n) == 2:
            return
        if state.get(n) == 1:
            cycles.append(list(stack[stack.index(n):]) + [n])
            return
        u = unlocks.get(n)
        if u is None:
            unknown.append(n)
            state[n] = 2
            return
        if is_unlocked(n, unlocks, unlocked):
            already.append(n)
            state[n] = 2
            return
        state[n] = 1
        for r in u.get("required") or []:
            visit(r, stack + (n,))
        state[n] = 2
        order.append(n)

    visit(target, ())
    return {"remaining": order, "already_unlocked": sorted(set(already)), "cycles": cycles, "unknown": sorted(set(unknown))}


def formula_value(text: str | None, tier: Any, efficiency: Any) -> float | None:
    """Catalogue formula text at (tier, efficiency) with the safe parser; None when absent or not computable."""
    if not text:
        return None
    try:
        v = formula_mod.evaluate(text, {"tier": float(tier), "efficiency": float(efficiency)})
    except (formula_mod.FormulaError, TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def node_costs(names: list[str], unlocks: Mapping[str, dict], formulas: Mapping[str, str], observed: Mapping[str, dict],
               efficiency: Any, active: str | None = None, active_remaining_days: Any = None,
               active_remaining_cost: Any = None) -> dict:
    """Per-node daily cost and days: game values first, else calibrated formula estimates, else null."""
    eff = num(efficiency)
    samples_days, samples_cost = [], []
    for n, c in sorted(observed.items()):
        u = unlocks.get(n) or {}
        fd = formula_value(formulas.get(u.get("research_time_formula")), u.get("tier"), eff) if eff else None
        fc = formula_value(formulas.get(u.get("research_cost_formula")), u.get("tier"), eff) if eff is not None else None
        od, oc = num(c.get("days")), num(c.get("daily_cost"))
        if fd and od:
            samples_days.append(od / fd)
        if fc and oc:
            samples_cost.append(oc / fc)
    k_days = median(samples_days) if samples_days else None
    k_cost = median(samples_cost) if samples_cost else None
    rows = []
    for n in names:
        u = unlocks.get(n) or {}
        row: dict[str, Any] = {"unlock": n, "tier": u.get("tier")}
        if n == active and num(active_remaining_days) is not None:
            rd, rc = num(active_remaining_days), num(active_remaining_cost)
            row.update({"basis": "game_active_remaining", "days": rd, "cost": rc,
                        "daily_cost": (rc / rd) if rc is not None and rd else None, "confidence": confidence("high")})
        elif n in observed:
            c = observed[n]
            d, dc = num(c.get("days")), num(c.get("daily_cost"))
            row.update({"basis": "game_computed", "days": d, "daily_cost": dc,
                        "cost": dc * d if dc is not None and d is not None else None, "confidence": confidence("high")})
        elif not eff:
            row.update({"basis": None, "days": None, "daily_cost": None, "cost": None, "confidence": None,
                        "reason": "research efficiency is 0 or unknown: the time formula divides by efficiency"})
        else:
            fd = formula_value(formulas.get(u.get("research_time_formula")), u.get("tier"), eff)
            fc = formula_value(formulas.get(u.get("research_cost_formula")), u.get("tier"), eff)
            if fd is None or fc is None:
                row.update({"basis": None, "days": None, "daily_cost": None, "cost": None, "confidence": None,
                            "reason": "cost/time formula text missing or not computable from tier and efficiency"})
            else:
                d = fd * (k_days or 1.0)
                dc = fc * (k_cost or 1.0)
                factors = ["approximate_rate"] + ([] if (k_days and k_cost) else ["mechanic_unverified"])  # formula text is game data
                row.update({"basis": "formula_calibrated" if (k_days and k_cost) else "formula_uncalibrated",
                            "days": d, "daily_cost": dc, "cost": d * dc, "confidence": confidence("high", factors)})
        rows.append(row)
    known = [r for r in rows if r.get("cost") is not None and r.get("days") is not None]
    complete = len(known) == len(rows)
    return {"method": "D-RES-PATH-1", "nodes": rows,
            "total_days": sum(r["days"] for r in rows) if complete else None,
            "total_cost": sum(r["cost"] for r in rows) if complete else None,
            "known_partial": {"days": sum(r["days"] for r in known), "cost": sum(r["cost"] for r in known), "nodes": len(known)},
            "calibration": {"days_factor": k_days, "cost_factor": k_cost, "samples": len(samples_days)},
            "complete": complete}


def products_unlocked(node: str, unlocks: Mapping[str, dict], recipes: Mapping[str, dict],
                      building_types: Mapping[str, dict]) -> dict:
    """Recipes and products a node (with its included unlocks) makes available."""
    names = [node] + list((unlocks.get(node) or {}).get("included") or [])
    rec: set[str] = set()
    for n in names:
        u = unlocks.get(n) or {}
        rec.update(u.get("recipes") or [])
        if u.get("kind") != "building_price":
            for b in u.get("buildings") or []:
                rec.update((building_types.get(b) or {}).get("recipes") or [])
    prods = sorted({r["product"] for name in rec for r in (recipes.get(name) or {}).get("results") or []})
    return {"recipes": sorted(rec), "products": prods}
