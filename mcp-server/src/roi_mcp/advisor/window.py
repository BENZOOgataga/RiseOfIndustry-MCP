"""Compact per-snapshot digests for what_changed (D-ADV-CHG-1, architecture section 5).

`digest` is called by the V1 StateWindow when a state snapshot is loaded for a call; it keeps only small values
(no names, no geometry), so 20 digests stay a few hundred kilobytes even on large saves. `diff` compares two digests.
Pure functions over plain data: no imports from the server framework.
"""

from __future__ import annotations

from typing import Any

INT32_MAX = 2147483647


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    return float(v) if isinstance(v, (int, float)) else None


def digest(data: dict) -> dict:
    session = data.get("session") or {}
    player = session.get("player_actor_id")
    companies = data.get("companies") or []
    pc = next((c for c in companies if c.get("actor_id") == player), None) or next((c for c in companies if c.get("is_player")), None) or {}
    cash = (pc.get("cash") or {}).get("value")
    loans = pc.get("loans")
    buildings = {}
    for b in data.get("buildings_player") or []:
        flags = b.get("flags") or {}
        buildings[b.get("key")] = {
            "prefab": b.get("prefab"), "recipe": b.get("recipe"), "user_enabled": flags.get("user_enabled"),
            "is_working": flags.get("is_working"), "requirements_met": flags.get("requirements_met"),
            "efficiency_index": (b.get("efficiency") or {}).get("index"),
            "inventory": {i.get("product"): i.get("count") for i in b.get("inventory") or []},
        }
    routes = {}
    for r in data.get("routes_player") or []:
        routes[r.get("route_key")] = {
            "max_send": (r.get("max_send") or {}).get("value"), "min_keep": (r.get("min_keep") or {}).get("value"),
            "paused": r.get("paused"), "errors": sorted(r.get("errors") or []),
            "dispatch_amount_now": (r.get("dispatch_amount_now") or {}).get("value"),
        }
    research = (data.get("research") or {}).get("player") or {}
    market = {p.get("product"): {"price": p.get("price"), "trend": p.get("trend")} for p in (data.get("market") or {}).get("prices") or []}
    return {
        "game_date": session.get("game_date"), "game_day": session.get("game_day"),
        "cash": cash, "loans_total": sum(_num(l.get("principal")) or 0.0 for l in loans) if isinstance(loans, list) else None,
        "buildings": buildings if data.get("buildings_player") is not None else None,
        "routes": routes if data.get("routes_player") is not None else None,
        "research": {"active": research.get("active"), "unlocked": sorted(research.get("unlocked") or [])} if research else None,
        "market": market if data.get("market") is not None else None,
    }


def _numeric(kind: str, subject: str, field: str, a: Any, b: Any) -> dict | None:
    x, y = _num(a), _num(b)
    if a == b or (x is not None and y is not None and x == y):
        return None
    row = {"kind": kind, "subject": subject, "field": field, "before": a, "after": b}
    if x is not None and y is not None:
        row["delta"] = round(y - x, 4)
        row["relative"] = round((y - x) / abs(x), 4) if x else None
    return row


def diff(old: dict, new: dict) -> list[dict]:
    """Changes from `old` to `new` digests, entity changes first, then numeric changes by |relative delta|."""
    entity: list[dict] = []
    numeric: list[dict] = []

    def add(row: dict | None, bucket: list) -> None:
        if row is not None:
            bucket.append(row)

    add(_numeric("company", "company:player", "cash", old.get("cash"), new.get("cash")), numeric)
    add(_numeric("company", "company:player", "loans_total", old.get("loans_total"), new.get("loans_total")), numeric)
    ob, nb = old.get("buildings"), new.get("buildings")
    if ob is not None and nb is not None:
        for k in sorted(set(nb) - set(ob)):
            entity.append({"kind": "building_added", "subject": f"building:{k}", "field": None, "before": None,
                           "after": {"recipe": nb[k].get("recipe")}})
        for k in sorted(set(ob) - set(nb)):
            entity.append({"kind": "building_removed", "subject": f"building:{k}", "field": None,
                           "before": {"recipe": ob[k].get("recipe")}, "after": None})
        for k in sorted(set(ob) & set(nb)):
            a, b = ob[k], nb[k]
            for f in ("recipe", "user_enabled", "is_working", "requirements_met", "efficiency_index"):
                if a.get(f) != b.get(f):
                    entity.append({"kind": "building_changed", "subject": f"building:{k}", "field": f, "before": a.get(f), "after": b.get(f)})
            for p in sorted(set(a.get("inventory") or {}) | set(b.get("inventory") or {}), key=str):
                add(_numeric("inventory", f"building:{k}", f"inventory.{p}", (a.get("inventory") or {}).get(p),
                             (b.get("inventory") or {}).get(p)), numeric)
    orr, nr = old.get("routes"), new.get("routes")
    if orr is not None and nr is not None:
        for k in sorted(set(nr) - set(orr)):
            entity.append({"kind": "route_added", "subject": f"route:{k}", "field": None, "before": None, "after": nr[k]})
        for k in sorted(set(orr) - set(nr)):
            entity.append({"kind": "route_removed", "subject": f"route:{k}", "field": None, "before": orr[k], "after": None})
        for k in sorted(set(orr) & set(nr)):
            a, b = orr[k], nr[k]
            for f in ("max_send", "min_keep", "paused", "errors"):
                if a.get(f) != b.get(f):
                    entity.append({"kind": "route_changed", "subject": f"route:{k}", "field": f, "before": a.get(f), "after": b.get(f)})
            add(_numeric("route", f"route:{k}", "dispatch_amount_now", a.get("dispatch_amount_now"), b.get("dispatch_amount_now")), numeric)
    ores, nres = old.get("research"), new.get("research")
    if ores is not None and nres is not None:
        if ores.get("active") != nres.get("active"):
            entity.append({"kind": "research_changed", "subject": "research:player", "field": "active",
                           "before": ores.get("active"), "after": nres.get("active")})
        for u in sorted(set(nres.get("unlocked") or []) - set(ores.get("unlocked") or [])):
            entity.append({"kind": "research_unlocked", "subject": f"tech:{u}", "field": None, "before": False, "after": True})
    om, nm = old.get("market"), new.get("market")
    if om is not None and nm is not None:
        for p in sorted(set(om) & set(nm), key=str):
            add(_numeric("market", f"product:{p}", "price", om[p].get("price"), nm[p].get("price")), numeric)
            if om[p].get("trend") != nm[p].get("trend"):
                entity.append({"kind": "market_trend_changed", "subject": f"product:{p}", "field": "trend",
                               "before": om[p].get("trend"), "after": nm[p].get("trend")})
    numeric.sort(key=lambda r: (-(abs(r.get("relative")) if r.get("relative") is not None else float("inf")), r["subject"], r["field"]))
    return entity + numeric
