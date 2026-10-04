"""Pinned, consistent snapshot basis for advisor answers (PRD addendum 3.3) and contradiction detection (3.6).

Internal infrastructure: nothing here is a public tool. `pin_basis` loads each family once through the V1
CallContext (which pins it for the rest of the call), drops history of another world session, and turns snapshot
problems into confidence factors and degraded-input rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..app import CallContext
from ..store import Snapshot
from .contract import num


@dataclass
class Basis:
    ctx: CallContext
    state: Snapshot | None
    history: Snapshot | None
    static: Snapshot | None
    factors: list = field(default_factory=list)
    degraded: list = field(default_factory=list)
    consistency: dict = field(default_factory=dict)

    def _row(self, family: str, snap: Snapshot) -> dict:
        used = self.ctx.used.get(family)
        cur = used.cur if used else None
        age = round(cur.age_s, 1) if cur and cur.age_s is not None else (
            round((self.ctx.now - snap.captured_end).total_seconds(), 1) if snap.captured_end else None)
        return {"family": family, "seq": snap.seq, "content_hash": snap.content_hash, "world_session": snap.world_session,
                "game_date": snap.captured.get("game_date"), "captured_utc": snap.captured.get("utc_end"), "age_s": age,
                "stale": bool(used.stale) if used and family != "static" else False,
                "stale_reason": used.stale_reason if used and family != "static" else None}

    def to_dict(self) -> dict:
        rows = [self._row(f, s) for f, s in (("state", self.state), ("history", self.history), ("static", self.static)) if s is not None]
        by = {r["family"]: r for r in rows}
        return {"snapshots": rows, "consistency": dict(self.consistency),
                "freshness": {"state_age_s": (by.get("state") or {}).get("age_s"),
                              "history_age_s": (by.get("history") or {}).get("age_s"),
                              "state_game_date": (by.get("state") or {}).get("game_date"),
                              "history_game_date": (by.get("history") or {}).get("game_date")}}


def pin_basis(ctx: CallContext, *, history: str = "none", static: str = "optional", state_sections: tuple = (),
              state_optional: tuple = (), history_optional_sections: tuple = ()) -> Basis:
    """history / static: "none" | "optional" | "required". State is always required."""
    st = ctx.static(required=(static == "required")) if static != "none" else None
    state = ctx.snapshot("state", sections=state_sections, optional_sections=state_optional)
    hist = None
    if history != "none":
        hist = ctx.snapshot("history", required=(history == "required"))
    b = Basis(ctx=ctx, state=state, history=hist, static=st)
    su = ctx.used.get("state")
    if su is not None and su.stale:
        b.factors.append("stale_data")
        b.degraded.append({"input": "state", "reason": "stale", "effect": f"confidence_lowered ({su.stale_reason})"})
    if hist is not None:
        if hist.world_session != state.world_session or hist.pid != state.pid:
            # Never combine two worlds: the history part is dropped (addendum 3.3 rule 2).
            ctx.used.pop("history", None)
            ctx.add_unavailable("live:history", "history_world_session_mismatch: history.json belongs to another world session")
            b.degraded.append({"input": "history", "reason": "history_world_session_mismatch", "effect": "omitted"})
            b.history = hist = None
        else:
            hu = ctx.used.get("history")
            if hu is not None and hu.stale and "stale_data" not in b.factors:
                b.factors.append("stale_data")
            if hu is not None and hu.stale:
                b.degraded.append({"input": "history", "reason": "stale", "effect": f"confidence_lowered ({hu.stale_reason})"})
            for sec in history_optional_sections:
                ctx.section("history", sec)
    elif history != "none":
        b.degraded.append({"input": "history", "reason": "family_unavailable", "effect": "omitted"})
    static_ok = ctx.app.store.static_ref_matches(state) if st is not None else None
    if static_ok is False:
        b.factors.append("static_mismatch")
        b.degraded.append({"input": "static", "reason": "static_mismatch", "effect": "confidence_lowered"})
    consistent = state.captured.get("consistent")
    if consistent is False:
        b.factors.append("inconsistent_snapshot")
        b.degraded.append({"input": "state", "reason": "inconsistent_snapshot", "effect": "confidence_lowered"})
    b.consistency = {"same_world_session": hist is None or hist.world_session == state.world_session,
                     "history_same_session": (hist.world_session == state.world_session) if hist is not None else None,
                     "static_ref_matches": static_ok, "state_consistent": consistent}
    return b


# ------------------------------------------------------------------ contradictions (D-ADV-CONTRA-1)

def route_stock_contradictions(ix: Any, routes: list[dict]) -> list[dict]:
    """A route's copied origin/destination stock that disagrees with the building's own inventory count."""
    out = []
    for r in routes:
        prod = r.get("product")
        for side, key, value in (("origin", r.get("origin"), r.get("origin_stock")),
                                 ("destination", r.get("destination"), r.get("destination_stock"))):
            v = num(value)
            if v is None:
                continue
            b = ix.buildings_full.get(key)
            count = None
            if b is not None and "inventory" in b:
                inv = next((i for i in b.get("inventory") or [] if i.get("product") == prod), None)
                count = num(inv.get("count")) if inv else None
            elif key in ix.shops:
                sp = next((p for p in ix.shops[key].get("products") or [] if p.get("product") == prod), None)
                count = num(sp.get("stock")) if sp else None
            if count is not None and count != v:
                out.append({"kind": f"route_{side}_stock_mismatch", "subject": f"route:{r['route_key']}",
                            "values": {"route_copy": v, "building_inventory": count},
                            "effect": "calculations use the building's own inventory count"})
    return out


def ledger_balance_contradiction(state: Snapshot, history: Snapshot | None, cash: Any) -> dict | None:
    if history is None:
        return None
    ledger = history.data.get("ledger_player") or {}
    bal = num(ledger.get("balance_now"))
    c = num(cash)
    same_day = state.captured.get("game_day_end") is not None and \
        state.captured.get("game_day_end") == history.captured.get("game_day_end")
    if bal is None or c is None or not same_day or abs(bal - c) < 0.5:
        return None
    return {"kind": "ledger_balance_mismatch", "subject": "company:player",
            "values": {"state_cash": c, "history_balance_now": bal, "game_day": state.captured.get("game_day_end")},
            "effect": "state cash is reported; ledger figures are used only for monthly totals"}
