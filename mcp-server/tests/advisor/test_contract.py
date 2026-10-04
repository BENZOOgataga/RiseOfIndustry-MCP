"""Advisor tool contract: registry, refresh scopes, input validation, response schemas, Quantity contract,
size cap, read-only statements (PRD addendum sections 2, 3, 5, 6)."""

from __future__ import annotations

import asyncio
import json
import math

import pytest

import build_fixtures as bf
from advisor.helpers import VALID_CALLS, WHAT_IF_CALLS, check_advisor_response
from roi_mcp.tools import ADVISOR_TOOL_NAMES, ALL_TOOL_NAMES, REFRESH_SCOPE, TOOL_NAMES

ADDENDUM_SCOPES = {"none": {"explain_mechanic", "how_to"},
                   "state+history": {"get_overview", "get_profitability", "what_if", "what_changed", "loan_calculator",
                                     "compare_options", "forecast"},
                   "state": {"diagnose_chain", "find_opportunities", "review_routes", "plan_chain",
                             "research_path", "suggest_research", "route_calculator",
                             "spatial_analysis", "get_chain_graph"}}


def test_registry_order_and_scopes(h):
    assert list(h.app.specs) == list(TOOL_NAMES) + list(ADVISOR_TOOL_NAMES) == list(ALL_TOOL_NAMES)
    assert len(ADVISOR_TOOL_NAMES) == 18 and len(ALL_TOOL_NAMES) == 47
    for scope, names in ADDENDUM_SCOPES.items():
        for n in names:
            assert REFRESH_SCOPE[n] == scope == h.app.specs[n].scope, n
    assert set().union(*ADDENDUM_SCOPES.values()) == set(ADVISOR_TOOL_NAMES)


def test_no_helper_exposed_and_no_write_semantics(h):
    # Helpers stay internal; compare_options is the one constrained comparison tool (phase-2 addendum 2.1).
    for n in ALL_TOOL_NAMES:
        assert not n.startswith(("set_", "build_", "buy_", "sell_", "apply_", "change_", "delete_", "rank_", "evaluate_")), n
    assert [n for n in ALL_TOOL_NAMES if n.startswith("compare_")] == ["compare_options"]
    for n in ADVISOR_TOOL_NAMES:
        d = h.app.specs[n].description
        assert "Read-only" in d and "meta.stale" in d and "user content" in d, n
    for n in ("what_if", "plan_chain"):
        assert "Nothing is changed in the game" in h.app.specs[n].description or "on copies" in h.app.specs[n].description


@pytest.mark.parametrize("name", ADVISOR_TOOL_NAMES)
def test_valid_call_matches_schema_and_contract(h, name):
    r = h.call(name, VALID_CALLS[name])
    assert r["ok"], r.get("error")
    check_advisor_response(name, r)
    props = h.app.specs[name].input_schema()["properties"]
    assert ("fresh" in props) == (REFRESH_SCOPE[name] != "none")
    assert h.app.specs[name].input_schema()["additionalProperties"] is False


@pytest.mark.parametrize("change", WHAT_IF_CALLS, ids=lambda c: c["type"] + ("+" + "/".join(sorted(set(c) - {"type"}))))
def test_what_if_every_change_type(h, change):
    r = h.call("what_if", {"change": change})
    assert r["ok"], r.get("error")
    check_advisor_response("what_if", r)
    assert r["data"]["result"]["applied"] is False
    assert "Nothing was changed" in r["data"]["result"]["note"]


def test_mcp_protocol_lists_advisor_tools_and_resources(h):
    from mcp import Client

    from roi_mcp.server import build_server

    async def go():
        async with Client(build_server(h.app)) as client:
            tools = (await client.list_tools()).tools
            res = await client.list_resources()
            mech = await client.read_resource("roi://knowledge/mechanics/max-send")
            call = await client.call_tool("get_overview", {})
            return tools, res, mech, call

    tools, res, mech, call = asyncio.run(go())
    names = [t.name for t in tools]
    assert names == list(ALL_TOOL_NAMES)
    for t in tools:
        assert t.annotations.read_only_hint is True and t.annotations.destructive_hint is False
    uris = {str(r.uri) for r in res.resources}
    assert {"roi://knowledge/mechanics", "roi://knowledge/glossary", "roi://knowledge/pitfalls", "roi://knowledge/how-to",
            "roi://knowledge/mechanics/max-send"} <= uris
    body = json.loads(mech.contents[0].text)
    assert body["mechanic"]["id"] == "max-send" and body["mechanic"]["verification"] == "CONFIRMED_IN_GAME"
    ov = json.loads(call.content[0].text)
    assert ov["ok"] and call.is_error is False
    check_advisor_response("get_overview", ov)


def test_unknown_resource_is_an_mcp_error(h):
    from mcp import Client
    from mcp.shared.exceptions import MCPError

    from roi_mcp.server import build_server

    async def go():
        async with Client(build_server(h.app)) as client:
            try:
                await client.read_resource("roi://knowledge/does-not-exist")
            except MCPError as exc:
                return exc
            return None

    exc = asyncio.run(go())
    assert isinstance(exc, MCPError) and "unknown resource" in exc.message


# ------------------------------------------------------------------ malformed arguments

MALFORMED = [
    ("get_overview", {"max_attention": 0}), ("get_overview", {"max_attention": 11}), ("get_overview", {"bogus": 1}),
    ("diagnose_chain", {}), ("diagnose_chain", {"product": "   "}), ("diagnose_chain", {"product": "Paint", "depth": 7}),
    ("diagnose_chain", {"product": 5}),
    ("get_profitability", {"input_cost_basis": "magic"}), ("get_profitability", {"limit": 26}), ("get_profitability", {"product": ""}),
    ("find_opportunities", {"types": []}), ("find_opportunities", {"types": ["free_money"]}), ("find_opportunities", {"max_buildings": 0}),
    ("find_opportunities", {"cursor": "not-a-cursor"}), ("find_opportunities", {"fields": "full"}),
    ("review_routes", {"min_severity": "critical"}), ("review_routes", {"cost_ratio_threshold": 0}), ("review_routes", {"kinds": ["x"]}),
    ("review_routes", {"origin": " "}),
    ("plan_chain", {"product": "Paint"}), ("plan_chain", {"product": "Paint", "target_per_month": 0}),
    ("plan_chain", {"product": "Paint", "target_per_month": -5}), ("plan_chain", {"product": "Paint", "target_per_month": 2e6}),
    ("plan_chain", {"product": "Paint", "target_per_month": 10, "max_depth": 13}),
    ("plan_chain", {"product": "Paint", "target_per_month": 10, "existing_capacity": "all"}),
    ("plan_chain", {"product": "Paint", "target_per_month": 10, "recipe_choice": {"Paint": " "}}),
    ("plan_chain", {"product": "Paint", "target_per_month": 10, "recipe_choice": {"Paint": "Chemicals"}}),
    ("what_if", {}), ("what_if", {"change": {}}), ("what_if", {"change": {"type": "demolish_everything"}}),
    ("what_if", {"change": {"type": "set_max_send", "route": "x"}}),
    ("what_if", {"change": {"type": "set_max_send", "route": "x", "value": 1, "recipe": "Paints"}}),
    ("what_if", {"change": {"type": "set_min_keep", "route": "x"}}),
    ("what_if", {"change": {"type": "set_min_keep", "route": "x", "value": 3, "keep_all": True}}),
    ("what_if", {"change": {"type": "set_max_send", "route": " ", "value": 3}}),
    ("what_if", {"change": {"type": "set_max_send", "route": "x", "value": -1}}),
    ("what_if", {"change": {"type": "add_buildings", "recipe": "Paints", "count": 51}}),
    ("what_if", {"change": {"type": "add_buildings", "recipe": "Paints", "building_type": "GasWell"}}),
    ("what_if", {"change": {"type": "set_efficiency", "building": f"building:{bf.B_PC1}", "index": 7}}),
    ("what_if", {"change": {"type": "change_recipe", "building": f"building:{bf.B_PC1}", "recipe": "Paints"}}),
    ("what_if", {"change": {"type": "remove_building", "building": "building:Headquarters@140,110"}}),
    ("what_if", {"change": {"type": "set_min_keep", "route": f"route:{bf.B_GW1}|Gas|{bf.B_PC1}|own|0", "value": 100}}),
    ("what_changed", {"horizon": "yearly"}), ("what_changed", {"since_seq": -1}), ("what_changed", {"limit": 0}),
    ("explain_mechanic", {}), ("explain_mechanic", {"topic": ""}), ("explain_mechanic", {"topic": "x", "fresh": True}),
    ("how_to", {"action": "  "}), ("how_to", {"action": "x", "allow_stale": True}),
]


@pytest.mark.parametrize("name,args", MALFORMED, ids=[f"{n}-{i}" for i, (n, _) in enumerate(MALFORMED)])
def test_malformed_arguments_are_invalid_argument(h, name, args):
    r = h.call(name, args)
    assert not r["ok"] and r["error"]["code"] == "invalid_argument", r
    check_advisor_response(name, r)


@pytest.mark.parametrize("name,args", [("plan_chain", {"product": "Paint", "target_per_month": math.nan}),
                                       ("plan_chain", {"product": "Paint", "target_per_month": math.inf}),
                                       ("review_routes", {"cost_ratio_threshold": math.nan})])
def test_non_finite_numbers_rejected(h, name, args):
    r = h.call(name, args)
    assert r["error"]["code"] == "invalid_argument"


@pytest.mark.parametrize("name,args,code", [
    ("diagnose_chain", {"product": "Unobtainium"}, "not_found"),
    ("plan_chain", {"product": "product:Nope", "target_per_month": 5}, "not_found"),
    ("what_if", {"change": {"type": "set_max_send", "route": "route:nope", "value": 3}}, "not_found"),
    ("what_if", {"change": {"type": "take_loan", "loan": "GoldenParachute"}}, "not_found"),
    ("explain_mechanic", {"topic": "zzzz qqqq xxxx"}, "not_found"),
    ("how_to", {"action": "zzzz qqqq xxxx"}, "not_found"),
    ("what_changed", {"since_seq": 999}, "not_found"),
])
def test_unresolvable_references(h, name, args, code):
    r = h.call(name, args)
    assert r["error"]["code"] == code, r
    check_advisor_response(name, r)


def test_not_found_knowledge_lists_candidates(h):
    r = h.call("explain_mechanic", {"topic": "zzzz qqqq xxxx"})
    assert r["error"]["candidates"] and all(c["kind"] == "mechanic" for c in r["error"]["candidates"])


def test_paging_review_routes(h):
    first = h.call("review_routes", {"limit": 5})
    assert first["ok"] and len(first["data"]["result"]["findings"]) == 5
    total = first["page"]["total"]
    seen = [f["route_id"] + f["kind"] for f in first["data"]["result"]["findings"]]
    cur = first["page"]["next_cursor"]
    while cur:
        nxt = h.call("review_routes", {"limit": 5, "cursor": cur})
        check_advisor_response("review_routes", nxt)
        seen += [f["route_id"] + f["kind"] for f in nxt["data"]["result"]["findings"]]
        cur = nxt["page"]["next_cursor"]
    assert len(seen) == total == len(set(seen))
    # a cursor belongs to its arguments
    other = h.call("review_routes", {"limit": 5, "product": "Gas", "cursor": first["page"]["next_cursor"]})
    assert other["error"]["code"] == "invalid_argument"


def test_fresh_writes_the_tool_scope_only(h):
    # get_profitability refreshes state and history (live validation V1.1); review_routes state only;
    # knowledge tools have no fresh parameter (scope none).
    from test_tools_contract import _observer_harness  # noqa: F401  (V1 helper exists; scopes are checked via the request file)
    h.app.config.refresh_wait_s = 0.0
    h.app.refresh.wait_s = 0.0
    h.call("review_routes", {"fresh": True})
    req = json.loads((h.exchange / "refresh-request.json").read_text(encoding="utf-8"))
    assert req["requests"]["state"] is not None and req["requests"]["history"] is None
    h.call("get_profitability", {"fresh": True})
    req = json.loads((h.exchange / "refresh-request.json").read_text(encoding="utf-8"))
    assert req["requests"]["history"] is not None
    h.call("review_routes", {"fresh": True})
    req = json.loads((h.exchange / "refresh-request.json").read_text(encoding="utf-8"))
    assert req["requests"]["state"] is not None


@pytest.mark.parametrize("name", sorted(ADDENDUM_SCOPES["state+history"]))
def test_fresh_refreshes_state_and_history(h, name, monkeypatch):
    """Live validation: a state-only refresh left history stale (confidence low) on tools that read both."""
    from advisor.helpers import VALID_CALLS
    seen = []

    async def record(ctx, scope):
        seen.append(scope)

    monkeypatch.setattr(h.app, "_do_refresh", record)
    h.call(name, {**VALID_CALLS[name], "fresh": True})
    assert seen == ["state", "history"]


# ---- state+history freshness contract (release-preparation review of bed7b1b) ----

COMBINED = sorted(ADDENDUM_SCOPES["state+history"])


def _wcodes(r):
    return [w["code"] for w in r["meta"]["warnings"]]


def test_only_the_seven_advisor_tools_use_the_combined_scope_and_v1_scopes_are_unchanged():
    combined = sorted(n for n, s in REFRESH_SCOPE.items() if s == "state+history")
    assert combined == COMBINED and set(combined) <= set(ADVISOR_TOOL_NAMES) and len(combined) == 7
    v1 = {n: REFRESH_SCOPE[n] for n in TOOL_NAMES}
    assert set(v1.values()) <= {"none", "state", "history"}
    assert [n for n, s in v1.items() if s == "none"] == ["get_game_status", "get_recipe"]
    assert [n for n, s in v1.items() if s == "history"] == ["get_finances"]


@pytest.mark.parametrize("name", COMBINED)
def test_combined_fresh_serves_both_families_end_to_end(tmp_path, clock, procs, name):
    from advisor.helpers import VALID_CALLS
    from test_tools_contract import _observer_harness
    hh, obs = _observer_harness(tmp_path, clock, procs)
    r = hh.call(name, {**VALID_CALLS[name], "fresh": True})
    assert r["ok"], r.get("error")
    req = obs.read_refresh_request()["requests"]
    assert req["state"] is not None and req["history"] is not None and req["static"] is None
    assert "refresh_timeout" not in _wcodes(r) and r["meta"]["stale"] is False
    used = {s["family"]: s["seq"] for s in r["meta"]["snapshots"]}
    assert used["state"] == obs.state_seq
    if "history" in used:                         # what_if (not take_loan) does not read history
        assert used["history"] == obs.history_doc["seq"]


def test_concurrent_combined_fresh_calls_coalesce_to_one_nonce_per_family(tmp_path, clock, procs):
    from test_tools_contract import _observer_harness
    hh, obs = _observer_harness(tmp_path, clock, procs, wait=3.0, serve=False)
    n = {"sleeps": 0}
    calls = 4

    def on_sleep(c):
        n["sleeps"] += 1
        if n["sleeps"] == calls:              # every call registered its state request
            obs.serve_refresh("state")
        if n["sleeps"] == 2 * calls:          # every call registered its history request
            obs.serve_refresh("history")
    clock.on_sleep = on_sleep

    async def go():
        return await asyncio.gather(*(hh.acall("get_overview", {"fresh": True}) for _ in range(calls)))

    results = asyncio.run(go())
    assert all(r["ok"] and "refresh_timeout" not in _wcodes(r) for r in results)
    assert hh.app.refresh.writes == 2          # one state nonce + one history nonce for all four calls
    assert len({tuple(sorted((s["family"], s["seq"]) for s in r["meta"]["snapshots"])) for r in results}) == 1


def test_combined_fresh_timeout_is_bounded_and_reported(tmp_path, clock, procs):
    """The observer serves state but never history: the call answers within 2 x refresh_wait_s with a
    refresh_timeout warning, and history is not presented as freshly refreshed."""
    from test_tools_contract import _observer_harness
    hh, obs = _observer_harness(tmp_path, clock, procs, wait=1.0, serve=False)
    clock.on_sleep = lambda c: obs.serve_refresh("state")
    before = clock.monotonic()
    r = hh.call("get_overview", {"fresh": True})
    assert r["ok"] and clock.monotonic() - before <= 2 * 1.0 + 1e-6
    assert "refresh_timeout" in _wcodes(r)
    hist = next(s for s in r["meta"]["snapshots"] if s["family"] == "history")
    assert hist["seq"] == 1                    # the unrefreshed snapshot, with its own age and stale flag
    assert any("history" in (w.get("detail") or "") for w in r["meta"]["warnings"] if w["code"] == "refresh_timeout")


def test_old_history_without_fresh_is_marked_stale(tmp_path, clock, procs):
    from test_tools_contract import _observer_harness
    hh, obs = _observer_harness(tmp_path, clock, procs, serve=False)
    clock.advance(120)
    obs.publish_state()
    obs.heartbeat("ready")                    # state is current again; history was captured 120 s ago
    r = hh.call("get_overview", {})
    hist = next(s for s in r["meta"]["snapshots"] if s["family"] == "history")
    assert hist["stale"] is True and hist["stale_reason"] == "age" and r["meta"]["stale"] is True
    assert "stale_data" in r["data"]["confidence"]["factors"]
