"""Missing data, degraded sections, stale snapshots, contradictory data, lifecycle states and pinning
(PRD addendum 3.3-3.6)."""

from __future__ import annotations

import copy
import json

import pytest

import build_fixtures as bf
from advisor.helpers import VALID_CALLS, check_advisor_response
from advisor.worlds import failed, load, world_with
from conftest import Harness, game_proc
from roi_mcp.tools import ADVISOR_TOOL_NAMES

RUNTIME = [n for n in ADVISOR_TOOL_NAMES if n not in ("explain_mechanic", "how_to")]


def degraded_inputs(r):
    return {d["input"]: d for d in r["data"]["degraded_inputs"]}


def unavailable(r):
    return {u["field"] for u in r["data"]["unavailable"]}


# ------------------------------------------------------------------ lifecycle

@pytest.mark.parametrize("name", RUNTIME)
def test_game_not_running_gives_lifecycle_error(h, name):
    h.procs.procs = []
    r = h.call(name, VALID_CALLS[name])
    assert not r["ok"] and r["error"]["code"] == "game_not_running"
    check_advisor_response(name, r)


@pytest.mark.parametrize("name", RUNTIME)
def test_allow_stale_returns_flagged_data_with_stale_factor(h, name):
    h.procs.procs = []
    r = h.call(name, {**VALID_CALLS[name], "allow_stale": True})
    assert r["ok"], r.get("error")
    check_advisor_response(name, r)
    assert r["meta"]["source"] == "stale_snapshot" and r["meta"]["stale"] is True
    assert "stale_data" in r["data"]["confidence"]["factors"]
    assert r["data"]["confidence"]["level"] != "high"
    assert any(d["reason"] == "stale" for d in r["data"]["degraded_inputs"])


@pytest.mark.parametrize("name", ["explain_mechanic", "how_to"])
def test_knowledge_tools_answer_without_game(h, name):
    h.procs.procs = []
    r = h.call(name, VALID_CALLS[name])
    assert r["ok"] and r["data"]["applies_to"]["build_match"] in (None, True)
    check_advisor_response(name, r)


@pytest.mark.parametrize("name", ["explain_mechanic", "how_to"])
def test_knowledge_tools_on_unsupported_build_say_so(exchange, clock, procs, name):
    hh = Harness(exchange, clock, procs)
    w = bf.build_world()
    w["heartbeat"] = bf.build_heartbeat(state="unsupported_build", compatibility="unsupported_build")
    hh.write_world(w)
    r = hh.call(name, VALID_CALLS[name])
    assert r["ok"] and r["data"]["applies_to"]["build_match"] is False
    assert r["meta"]["source"] in ("none",)                       # no static catalogue read on an unsupported build
    assert hh.call("get_overview")["error"]["code"] == "unsupported_build"


def test_stale_by_age_while_live(exchange, clock, procs):
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(state_kw={"capture_age_s": 120.0}))
    r = hh.call("get_overview")
    assert r["ok"] and r["meta"]["stale_reason"] == "age"
    assert "stale_data" in r["data"]["confidence"]["factors"]
    est = r["data"]["result"]["production"]["deficits"][0]["balance_per_30d"]
    assert "stale_data" in est["confidence"]["factors"]


# ------------------------------------------------------------------ degraded sections

def test_overview_degrades_without_history(h):
    (h.exchange / "history.json").unlink()
    r = h.call("get_overview")
    assert r["ok"] and r["data"]["result"]["finances"]["available"] is False
    assert degraded_inputs(r)["history"]["reason"] == "family_unavailable"
    assert "live:history" in unavailable(r)
    check_advisor_response("get_overview", r)


def test_history_of_another_world_session_is_never_combined(exchange, clock, procs):
    hh = Harness(exchange, clock, procs)
    w = world_with()
    w["history"] = bf.build_history(world_session=bf.WORLD_SESSION_2)
    load(hh, w)
    r = hh.call("get_overview")
    # V1 currency rules already refuse history of another world session; the advisor reports it as missing
    assert r["ok"] and r["data"]["result"]["finances"]["available"] is False
    assert degraded_inputs(r)["history"]["reason"] == "family_unavailable"
    assert any("another world session" in u["reason"] for u in r["data"]["unavailable"] if u["field"] == "live:history")
    assert [s["family"] for s in r["meta"]["snapshots"]] in (["state", "static"], ["state"])
    r = hh.call("get_profitability", {"product": "Paint"})
    assert r["data"]["result"]["products"][0]["game_stats"] is None


def test_basis_drops_history_when_state_and_history_sessions_differ(exchange, clock, procs):
    # allow_stale: the state snapshot is from the previous session, history from the current one -> never combined
    hh = Harness(exchange, clock, procs)
    w = world_with()
    w["history"] = bf.build_history(world_session=bf.WORLD_SESSION_2)
    w["heartbeat"] = bf.build_heartbeat(world_session=bf.WORLD_SESSION_2, static_doc=w["static"], history_doc=w["history"])
    load(hh, w)
    r = hh.call("get_overview", {"allow_stale": True})
    assert r["ok"] and degraded_inputs(r)["history"]["reason"] == "history_world_session_mismatch"
    assert r["data"]["result"]["finances"]["available"] is False
    assert "history" not in [s["family"] for s in r["meta"]["snapshots"]]
    assert r["meta"]["stale_reason"] == "world_session_changed"


@pytest.mark.parametrize("sec", ["shops", "routes_player", "market", "research", "buildings_player", "cities"])
def test_overview_survives_each_failed_optional_section(exchange, clock, procs, sec):
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(state_sections={sec: failed()}))
    r = hh.call("get_overview")
    assert r["ok"], r.get("error")
    check_advisor_response("get_overview", r)
    assert f"state.{sec}" in degraded_inputs(r) or sec in ("market",)
    if sec == "buildings_player":
        assert r["data"]["result"]["buildings"] == {"available": False}          # never zero counts
        assert r["data"]["result"]["production"] == {"available": False}
    if sec == "routes_player":
        assert r["data"]["result"]["logistics"] == {"available": False}


@pytest.mark.parametrize("name,sec", [("review_routes", "routes_player"), ("find_opportunities", "shops"),
                                      ("diagnose_chain", "buildings_player"), ("plan_chain", "buildings_player"),
                                      ("get_profitability", "buildings_player"), ("get_overview", "companies")])
def test_required_section_missing_is_section_unavailable(exchange, clock, procs, name, sec):
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(state_sections={sec: failed()}))
    r = hh.call(name, VALID_CALLS[name])
    assert r["error"]["code"] == "section_unavailable", r
    check_advisor_response(name, r)


def test_profitability_without_market_nulls_costs_not_zero(exchange, clock, procs):
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(state_sections={"market": failed()}))
    r = hh.call("get_profitability", {"product": "Chemicals"})
    e = r["data"]["result"]["products"][0]["estimate"]
    assert e["unit_cost"]["total"]["value"] is None and e["margin_per_unit"]["value"] is None
    assert e["sale_price"]["value"] == 610                    # shops still give the sale price
    assert "state.market" in degraded_inputs(r)
    check_advisor_response("get_profitability", r)


def test_review_routes_without_prices_skips_cost_rule(exchange, clock, procs):
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(state_sections={"market": failed(), "shops": failed()}))
    r = hh.call("review_routes", {"limit": 50})
    assert r["ok"] and "high_unit_cost" not in r["data"]["result"]["summary"]["findings_by_kind"]
    assert any(d["input"].startswith("sale_price:") for d in r["data"]["degraded_inputs"])


def test_missing_cycle_time_degrades_rates(exchange, clock, procs):
    def mutate(d):
        for b in d["buildings_player"]:
            if b["key"] == bf.B_PF1:
                b["cycle_days_effective"] = None
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(mutate))
    r = hh.call("get_profitability", {"product": "Paint"})
    e = r["data"]["result"]["products"][0]["estimate"]
    assert e["unit_cost"]["upkeep_per_unit"]["value"] is None and e["margin_per_30d"]["value"] is None
    plan = hh.call("plan_chain", {"product": "Paint", "target_per_month": 10, "existing_capacity": "none"})
    step = next(s for s in plan["data"]["result"]["steps"] if s["product"]["id"] == "product:Paint")
    assert step["rate_per_building"]["basis"] == "static_recipe"                 # no observed peer rate -> static recipe
    assert "approximate_rate" in step["rate_per_building"]["confidence"]["factors"]


def test_inconsistent_snapshot_and_static_mismatch_lower_confidence(exchange, clock, procs):
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(state_kw={"consistent": False}))
    r = hh.call("diagnose_chain", {"product": "Paint"})
    assert "inconsistent_snapshot" in r["data"]["confidence"]["factors"]
    assert r["data"]["basis"]["consistency"]["state_consistent"] is False
    w = world_with()
    w["state"]["static_ref"] = {"seq": 999, "content_hash": "nope"}
    w["state"]["seq"] = 11
    load(hh, w)
    r = hh.call("get_overview")
    assert "static_mismatch" in r["data"]["confidence"]["factors"]
    assert r["data"]["basis"]["consistency"]["static_ref_matches"] is False


# ------------------------------------------------------------------ contradictions

def test_route_stock_contradiction_reported(exchange, clock, procs):
    def mutate(d):
        for r in d["routes_player"]:
            if r["origin"] == bf.B_GW1:
                r["origin_stock"] = 99
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(mutate))
    r = hh.call("review_routes", {"limit": 50})
    c = [x for x in r["data"]["contradictions"] if x["kind"] == "route_origin_stock_mismatch"]
    assert c and c[0]["values"] == {"route_copy": 99, "building_inventory": 12}
    f = [x for x in r["data"]["result"]["findings"] if x["route_id"].startswith(f"route:{bf.B_GW1}") and x["kind"] == "zero_dispatch_now"]
    assert f and "contradictory_inputs" in f[0]["confidence"]["factors"]
    assert hh.call("get_overview")["data"]["contradictions"]


def test_ledger_balance_contradiction_same_game_day(exchange, clock, procs):
    def mutate(h):
        h["ledger_player"]["balance_now"] = 1234.0
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(mutate_history=mutate))
    r = hh.call("get_overview")
    assert any(c["kind"] == "ledger_balance_mismatch" for c in r["data"]["contradictions"])
    assert r["data"]["result"]["cash"]["value"] == 2500000                          # state cash reported


def test_no_false_contradictions_on_consistent_fixture(h):
    for name in ("get_overview", "review_routes", "diagnose_chain"):
        assert h.call(name, VALID_CALLS[name])["data"]["contradictions"] == []


def test_contradictions_are_capped(exchange, clock, procs):
    def mutate(d):
        for r in d["routes_player"]:
            r["origin_stock"] = (r["origin_stock"] or 0) + 1000
            r["destination_stock"] = (r["destination_stock"] or 0) + 1000
    hh = Harness(exchange, clock, procs)
    load(hh, world_with(mutate))
    r = hh.call("get_overview")
    assert len(r["data"]["contradictions"]) == 20 and r["data"]["contradictions_total"] > 20


# ------------------------------------------------------------------ pinning

def test_snapshot_is_pinned_within_one_call(h):
    from roi_mcp.app import CallContext
    spec = h.app.specs["get_overview"]
    ctx = CallContext(h.app, spec, {}, h.app.liveness())
    first = ctx.snapshot("state")
    doc = copy.deepcopy(h.world["state"])
    doc["seq"] = 77
    doc["data"]["companies"][0]["cash"]["value"] = 1.0
    h.write_raw("state", json.dumps(doc))
    assert ctx.snapshot("state") is first and ctx.snapshot("state", sections=("companies",)) is first
    # a new call sees the new file
    assert h.call("get_overview")["data"]["result"]["cash"]["value"] == 1.0
