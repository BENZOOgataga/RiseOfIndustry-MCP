"""Deterministic calculators (phase-2 addendum D-LOAN-SCHED-1, D-SPATIAL-1, D-FORECAST-1). Pure functions."""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

from .contract import confidence, num
from .economics import median

# ---------------------------------------------------------------------- D-LOAN-SCHED-1


def loan_schedule(principal: float, apr: float, duration: int, grace: int = 0, modifier: float = 1.0,
                  max_rows: int = 12) -> dict:
    """Flat-interest loan (CONFIRMED_SOURCE): constant payment = principal x (1 + apr) / duration x modifier,
    paid at the start of each month after the grace months."""
    if not (principal > 0 and duration >= 1 and apr >= 0 and grace >= 0 and modifier > 0):
        raise ValueError("principal > 0, duration >= 1, apr >= 0, grace >= 0, modifier > 0 required")
    payment = principal * (1.0 + apr) / duration * modifier
    total = payment * duration
    rows = []
    idx = list(range(1, duration + 1))
    shown = idx if duration <= max_rows + 1 else idx[:max_rows] + [duration]
    for i in shown:
        rows.append({"payment_number": i, "month_offset": grace + i, "payment": payment,
                     "remaining_balance_after": payment * (duration - i),
                     "early_repay_after": (duration - i) / duration * principal})
    return {"method": "D-LOAN-SCHED-1", "payment": payment, "total_repayment": total, "financing_cost": total - principal,
            "first_payment_month_offset": grace + 1, "last_payment_month_offset": grace + duration,
            "schedule": rows, "rows_omitted": duration - len(rows), "confidence": confidence("high")}


# ---------------------------------------------------------------------- D-SPATIAL-1

def euclid(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def chebyshev(a: Sequence[float], b: Sequence[float]) -> float:
    return max(abs(b[0] - a[0]), abs(b[1] - a[1]))


def weighted_sum(c: Sequence[float], points: Sequence[tuple[Sequence[float], float]]) -> float:
    return sum(w * euclid(c, p) for p, w in points)


def geometric_median(points: Sequence[tuple[Sequence[float], float]], max_iter: int = 500, tol: float = 1e-4) -> dict:
    """Weighted geometric median (Weiszfeld). Returns the point and iterations; deterministic."""
    pts = [(tuple(map(float, p)), float(w)) for p, w in points if w > 0]
    if not pts:
        return {"point": None, "iterations": 0, "converged": False}
    tw = sum(w for _, w in pts)
    x = (sum(p[0] * w for p, w in pts) / tw, sum(p[1] * w for p, w in pts) / tw)
    for it in range(1, max_iter + 1):
        num_x = num_y = den = 0.0
        for p, w in pts:
            d = euclid(x, p)
            if d < 1e-12:
                return {"point": p, "iterations": it, "converged": True}
            num_x += w * p[0] / d
            num_y += w * p[1] / d
            den += w / d
        nx = (num_x / den, num_y / den)
        if euclid(nx, x) < tol:
            return _best_with_vertices(nx, pts, it, True)
        x = nx
    return _best_with_vertices(x, pts, max_iter, False)


def _best_with_vertices(x, pts, it: int, converged: bool) -> dict:
    """Weiszfeld converges slowly when the optimum is a data point: keep the best of the iterate and the points."""
    best, best_s = x, weighted_sum(x, pts)
    for p, _ in pts:
        s = weighted_sum(p, pts)
        if s < best_s - 1e-12:
            best, best_s = p, s
    return {"point": best, "iterations": it, "converged": converged or best is not x}


def detour_range(samples: Iterable[tuple[Any, Any]], min_euclid: float = 5.0, min_samples: int = 3) -> dict:
    """Observed path/straight-line ratios over existing routes: (distance_tiles, euclidean) pairs."""
    ratios = sorted(num(d) / num(e) for d, e in samples
                    if num(d) is not None and num(e) is not None and num(e) >= min_euclid and num(d) > 0)
    if len(ratios) < min_samples:
        return {"available": False, "samples": len(ratios), "reason": f"fewer than {min_samples} routes with a cached path"}
    return {"available": True, "samples": len(ratios), "min": ratios[0], "median": median(ratios), "max": ratios[-1]}


# ---------------------------------------------------------------------- D-FORECAST-1

def cash_forecast(cash: Any, nets: Sequence[Any]) -> dict:
    vals = [num(n) for n in nets]
    c = num(cash)
    if c is None or len(vals) < 2 or any(v is None for v in vals):
        return {"available": False, "reason": "needs current cash and at least 2 complete months of ledger net"}
    mean = sum(vals) / len(vals)
    out = {"available": True, "method": "D-FORECAST-1", "sample_months": len(vals), "mean_net": mean, "min_net": min(vals),
           "max_net": max(vals)}
    if c <= 0:
        return {**out, "already_negative": True, "projection": "cash is already zero or negative"}
    if min(vals) >= 0:
        return {**out, "already_negative": False, "projection": "not_projected", "months_to_zero_range": None,
                "reason": "every sampled month had a non-negative net"}
    pessimistic = c / -min(vals)
    optimistic = c / -mean if mean < 0 else None
    conf = confidence("medium", [] if len(vals) >= 4 else ["partial_inputs"])
    return {**out, "already_negative": False, "projection": "exhaustion",
            "months_to_zero_range": [pessimistic, optimistic], "confidence": conf}


def stock_forecast(samples: Sequence[tuple[Any, Any]], slots: Any) -> dict:
    """samples: (game_day, count) in capture order; distinct game days required."""
    pts = []
    seen = set()
    for d, c in samples:
        dd, cc = num(d), num(c)
        if dd is None or cc is None or dd in seen:
            continue
        seen.add(dd)
        pts.append((dd, cc))
    pts.sort()
    if len(pts) < 2:
        return {"available": False, "samples": len(pts),
                "reason": "needs at least 2 snapshots on different game days in this server's in-memory window"}
    overall = (pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0])
    pair = [(b[1] - a[1]) / (b[0] - a[0]) for a, b in zip(pts, pts[1:])]
    lo, hi = min(pair + [overall]), max(pair + [overall])
    count = pts[-1][1]
    s = num(slots)
    out = {"available": True, "method": "D-FORECAST-1", "samples": len(pts), "from_game_day": pts[0][0],
           "to_game_day": pts[-1][0], "rate_per_day": overall, "rate_range_per_day": [lo, hi], "count_now": count,
           "confidence": confidence("medium", [] if len(pts) >= 4 else ["partial_inputs"])}
    if overall > 0:
        if s is None:
            return {**out, "trend": "filling", "days_to_full": None, "reason": "storage slots unknown"}
        room = max(s - count, 0.0)
        return {**out, "trend": "filling", "days_to_full": room / overall,
                "days_to_full_range": [room / hi if hi > 0 else None, room / lo if lo > 0 else None]}
    if overall < 0:
        return {**out, "trend": "depleting", "days_to_empty": count / -overall,
                "days_to_empty_range": [count / -lo if lo < 0 else None, count / -hi if hi < 0 else None]}
    return {**out, "trend": "stable"}
