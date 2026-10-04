"""Synthetic world variants for advisor scenario tests (built from the V1 fixture builder; nothing is read from a
live exchange directory)."""

from __future__ import annotations

import copy

import build_fixtures as bf


def world_with(mutate_state=None, mutate_history=None, *, state_kw=None, history_kw=None, now=bf.BASE_TIME, hb_kw=None,
               state_sections=None, history_sections=None) -> dict:
    static_doc = bf.build_static(now)
    sdata = bf.state_data()
    if mutate_state:
        mutate_state(sdata)
    state_doc = bf.build_state(now, static_doc=static_doc, data=sdata, **(state_kw or {}))
    if state_sections:
        for k, v in state_sections.items():
            state_doc["sections"][k] = v
            if v.get("status") != "ok":
                state_doc["data"][k] = None
    hdata = bf.history_data()
    if mutate_history:
        mutate_history(hdata)
    history_doc = bf.build_history(now, static_doc=static_doc, data=hdata, **(history_kw or {}))
    if history_sections:
        for k, v in history_sections.items():
            history_doc["sections"][k] = v
            if v.get("status") != "ok":
                history_doc["data"][k] = None
    hb = bf.build_heartbeat(now, static_doc=static_doc, state_doc=state_doc, history_doc=history_doc, **(hb_kw or {}))
    return {"heartbeat": hb, "static": static_doc, "state": state_doc, "history": history_doc}


def load(h, world: dict) -> None:
    h.world = copy.deepcopy(world)
    h.write_world(world)


def failed(reason="test failure"):
    return bf.section_status(1512, 0, "failed", reason)


def set_paint_price(price: float):
    def mutate(d):
        for s in d["shops"]:
            for p in s["products"]:
                if p["product"] == "Paint":
                    p["price_for_player"] = price
    return mutate
