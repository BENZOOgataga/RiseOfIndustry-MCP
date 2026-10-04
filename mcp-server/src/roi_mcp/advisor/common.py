"""Helpers shared by the advisor tool handlers."""

from __future__ import annotations

from typing import Any

from ..app import COMMON_SUFFIX, CallContext
from ..errors import ToolError
from ..index import StateIndex, make_id
from ..tools.common import provenance
from ..util import json_size
from .contract import Advice, clean, estimate, num, observed

PAGE_BUDGET = 15_000   # bytes of rows per page (room for envelope, language fields, advisor parts)

ADVISOR_NOTE = (" Advisory (V1.1): analyses the PLAYER company only. Every number is a Quantity {value, unit, kind} where kind "
                "says whether it is observed, a definition, game-computed, an estimate or a parameter; estimates rely on "
                "data.assumptions and carry a confidence. Check data.confidence, data.degraded_inputs and data.contradictions. "
                "Recommendations are player actions; the MCP never performs them.")
DESCRIPTION_SUFFIX = ADVISOR_NOTE + COMMON_SUFFIX

SEVERITIES = ("high", "medium", "low", "info")
SEVERITY_ORDER = {s: i for i, s in enumerate(SEVERITIES)}


def require_player(ix: StateIndex) -> int:
    if ix.player_actor_id is None:
        raise ToolError("section_unavailable", "The player company is not known (companies/session section missing).",
                        details={"section": "companies", "reason": "player_unknown"})
    return ix.player_actor_id


def bref(ix: StateIndex, key: str | None) -> dict | None:
    if key is None:
        return None
    b = ix.building(key) or {}
    return {"id": make_id("building", key), "name": b.get("display_name")}


def pref(ix: StateIndex, product: str | None) -> dict | None:
    if product is None:
        return None
    p = ix.static.products.get(product) or {}
    return {"id": make_id("product", product), "display_name": p.get("display_name"), "english_name": p.get("english_name")}


def est_from(d: dict, unit: str, adv: Advice, field: str, key: str = "value", digits: int = 4) -> dict:
    """Quantity(kind=estimate) from an economics result dict (value + method + confidence), applying basis factors.
    A value that could not be computed keeps value null with confidence low / partial_inputs."""
    conf = adv.track(d.get("confidence")) if d.get("confidence") else None
    method = d.get("method") or "unknown"
    adv.method(field, method)
    return estimate(d.get(key), unit, method, conf or {"level": "low", "factors": ["partial_inputs"]}, digits)


def assume_from(adv: Advice, *results: dict | None) -> None:
    """Register the assumptions an economics result reports (new-building economics, upkeep)."""
    for r in results:
        if not r:
            continue
        for part in (r, r.get("upkeep") or {}, r.get("rate") or {}):
            for a in part.get("assumptions") or []:
                adv.assume(a)


def section_or_degrade(ctx: CallContext, adv: Advice, family: str, sec: str, effect: str = "omitted") -> Any:
    data = ctx.section(family, sec)
    if data is None:
        adv.degrade(f"{family}.{sec}", "section_unavailable", effect)
    return data


def advisor_provenance(observed_fields: list, definition_fields: list, derived: list) -> dict:
    return provenance(observed=observed_fields, definition=definition_fields, derived=derived)


def obs_num(value: Any, unit: str, source: str) -> dict:
    return observed(value if num(value) is not None else None, unit, source)


def money(value: Any) -> Any:
    return clean(num(value), 2)


def bounded_page(ctx: CallContext, key: str, rows: list, budget: int = PAGE_BUDGET) -> tuple[dict, bool]:
    """ctx.paginate, then shorten the page (never the data set) until its rows fit the budget, moving
    page.next_cursor accordingly, so the generic size cap never has to cut rows without a cursor."""
    data = ctx.paginate(key, rows)
    page = data[key]
    n = len(page)
    while n > 1 and json_size(page[:n]) > budget:
        n = max(1, int(n * 0.8))
    if n < len(page):
        data[key] = page[:n]
        ctx.set_next_cursor(ctx.page_offset + n)
        return data, True
    return data, False


def finding(rank_key: tuple, **row: Any) -> dict:
    row["_rank"] = rank_key
    return row


def rank_findings(rows: list[dict]) -> list[dict]:
    rows.sort(key=lambda r: r.pop("_rank") if "_rank" in r else ())
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows
