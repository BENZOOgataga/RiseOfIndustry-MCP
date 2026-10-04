"""Hardening: the SHARED server machinery used by all 29 tools (not individual tool semantics).

Covers: tool registry vs PRD 13.2/13.7/14, generic argument validation, envelope/meta invariants, error
codes, liveness classification and allow_stale (PRD 11.7, 13.3, 13.4, 16), snapshot store (PRD 11.4),
refresh manager (PRD 11.6, 13.2, 13.2a), cursor engine and response size cap (PRD 13.2, 14.9), start-up
and recovery (PRD 13.1, 16), concurrency, and per-call logging (PRD 19).

Every response is validated with `check_response` (tool response schema + 30 KB cap + meta keys) and the
generic meta invariants of `check` below. Deterministic: FakeClock, fake process list, temp exchange dirs.
Defects found are kept as `xfail(strict=True)` tests whose reason starts with "DEFECT IN-<n>".
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import os
from datetime import timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import build_fixtures as bf
from conftest import SCHEMA_DIR, FakeClock, FakeProcs, Harness, game_proc
from fake_observer import FakeObserver
from roi_mcp import config as cfg
from roi_mcp import refresh as refresh_mod
from roi_mcp.errors import ERROR_CODES, WARNING_CODES
from roi_mcp.refresh import RefreshManager, RefreshRequestWriter
from roi_mcp.schemas import load_json_lenient
from roi_mcp.tools import REFRESH_SCOPE, TOOL_NAMES
from roi_mcp.util import encode_cursor, json_size
from test_tools_contract import PRD_TOOLS, ROUTE_1, VALID_CALLS, _observer_harness, check_response

# --------------------------------------------------------------------------------------------- helpers

STALE_REASONS = {"age", "world_session_changed", "not_in_game", "game_not_running", "observer_unresponsive"}  # PRD 11.7
LIFECYCLE_CODES = {"game_not_running", "observer_not_detected", "observer_unresponsive", "at_main_menu", "loading",
                   "observer_disabled", "observer_faulted", "unsupported_build"}


def wcodes(r: dict) -> list[str]:
    return [w["code"] for w in r["meta"]["warnings"]]


def check(name: str, r: dict) -> dict:
    """check_response + generic envelope/meta invariants (PRD 13.3, 13.4, 11.7)."""
    check_response(name, r)
    m = r["meta"]
    assert set(wcodes(r)) <= set(WARNING_CODES), wcodes(r)
    if not r["ok"]:
        # PRD 13.3 error form {ok:false, error{code,message,hint,...}, meta}
        assert set(r) == {"ok", "error", "meta"}, set(r)
        assert r["error"]["code"] in ERROR_CODES and r["error"]["code"] != "internal_error", r["error"]
        assert isinstance(r["error"]["message"], str) and r["error"]["message"]
        if r["error"]["code"] in LIFECYCLE_CODES:
            assert m["source"] == "none"
        return r
    assert set(r) == {"ok", "meta", "data", "page"}, set(r)
    if m["stale"]:
        assert m["stale_reason"] in STALE_REASONS, m["stale_reason"]
    else:
        assert m["stale_reason"] is None
        assert m["source"] != "stale_snapshot"
    if m["source"] == "stale_snapshot":  # PRD 13.4: stale data is labelled stale
        assert m["stale"] is True
    if m["source"] == "live_snapshot":   # never silently stale (PRD 13.4): live data is current, same session
        assert m["game_state"] == "ready" and m["stale"] is False
        for s in m.get("snapshots") or []:
            if s["family"] != "static":
                assert s["world_session"] == m["world_session"], (name, s)
    return r


def call(h, name, args=None) -> dict:
    return check(name, h.call(name, args or {}))


def specs(h):
    # The 29 V1 tools; the V1.1 advisor tools have their own suite (tests/advisor).
    return {n: s for n, s in h.app.specs.items() if n in TOOL_NAMES}


def list_tools(h) -> list[str]:
    return [n for n, s in specs(h).items() if "cursor" in s.input_schema()["properties"]]


def live_env(tmp_path, clock, wait=3.0, name="RoiMcp"):
    d = tmp_path / name
    d.mkdir()
    procs = FakeProcs([])
    hh = Harness(d, clock, procs, refresh_wait_s=wait)
    obs = FakeObserver(d, clock, procs)
    obs.start_process(clock.now() - timedelta(minutes=5))
    obs.ready()
    return hh, obs


NOT_LIVE = {  # state -> (expected error code PRD 13.3/16, expected stale_reason or None when PRD is silent)
    "game_not_running": ("game_not_running", "game_not_running"),
    "observer_unresponsive": ("observer_unresponsive", "observer_unresponsive"),
    "observer_not_detected": ("observer_not_detected", None),
    "starting": ("observer_not_detected", None),
    "menu": ("at_main_menu", "not_in_game"),
    "loading": ("loading", "not_in_game"),
    "disabled": ("observer_disabled", None),
    "faulted": ("observer_faulted", None),
}


def enter(hh, obs, state):
    if state == "game_not_running":
        obs.kill_process()
    elif state == "observer_unresponsive":
        hh.clock.advance(10)
    elif state == "observer_not_detected":
        hh.procs.procs = [game_proc(pid=555, start=hh.clock.now() - timedelta(minutes=5))]
    elif state == "starting":
        hh.procs.procs = [game_proc(pid=555, start=hh.clock.now() - timedelta(seconds=10))]
    else:
        obs.heartbeat(state)


def read_request(d: Path) -> dict | None:
    p = d / "refresh-request.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ============================================================================ (a) registry vs PRD

# PRD 14.1-14.8 parameters ("paging" = limit + cursor); fresh/allow_stale are the only 13.2 parameters added to every
# tool with a refresh scope. Paging, sort and fields are listed per tool (PRD 13.2: only where 14 lists them).
PRD_PARAMS = {
    "get_game_status": set(),
    "search": {"query", "kinds", "owner", "limit", "cursor"},
    "list_companies": {"limit", "cursor", "fields"},
    "get_company": {"company"},
    "get_finances": {"company", "months", "categories", "group_by"},
    "list_buildings": {"owner", "kind", "building_type", "product", "recipe", "city", "region", "status", "sort", "limit",
                       "cursor", "fields"},
    "get_building": {"building", "include"},
    "get_production_overview": {"company", "product"},
    "find_production_issues": {"company", "product", "kinds", "limit", "cursor"},
    "list_routes": {"origin", "destination", "product", "company", "transport_mode", "include_dormant", "errors_only", "sort",
                    "limit", "cursor", "fields"},
    "get_route": {"route", "include_path"},
    "list_warehouse_requests": {"endpoint", "product", "company", "limit", "cursor", "fields"},
    "list_vehicles": {"company", "transport_mode", "product", "fleet_building", "aggregate", "vehicle", "limit", "cursor", "fields"},
    "get_supply_chain": {"product", "building", "direction", "depth", "company", "mode", "target_output_per_30d", "recipe_choice"},
    "list_products": {"category", "tag", "query", "unlocked_only", "limit", "cursor", "fields"},
    "get_product": {"product"},
    "list_recipes": {"product", "building_type", "available_to_player", "limit", "cursor", "fields"},
    "get_recipe": {"recipe", "product"},
    "list_building_types": {"tag", "product", "unlocked_only", "limit", "cursor", "fields"},
    "get_building_type": {"building_type"},
    "list_cities": {"limit", "cursor", "sort", "fields"},
    "get_city": {"city"},
    "get_shop": {"shop"},
    "find_shops": {"product", "from_building", "city", "region", "limit", "cursor", "sort"},
    "list_regions": {"owner", "resource", "limit", "cursor", "fields"},
    "get_region": {"region"},
    "get_market": {"products", "include"},
    "get_tech_tree": {"tree", "state", "company", "limit", "cursor"},
    "get_research_state": {"company"},
}
PRD_REQUIRED = {"search": {"query"}, "get_building": {"building"}, "get_route": {"route"}, "get_product": {"product"},
                "get_building_type": {"building_type"}, "get_city": {"city"}, "get_shop": {"shop"}, "find_shops": {"product"},
                "get_region": {"region"}}
COMMON_13_2 = {"fresh", "allow_stale"}
PAGED_TOOLS = {"list_companies", "list_buildings", "list_routes", "list_warehouse_requests", "list_vehicles", "list_products",
               "list_recipes", "list_building_types", "list_cities", "list_regions", "search", "find_production_issues",
               "find_shops", "get_tech_tree"}
SORTED_TOOLS = {"list_buildings", "list_routes", "list_cities", "find_shops"}

# PRD 13.7 table, verbatim
PRD_13_7 = {
    "none": {"get_game_status", "get_recipe"},
    "state": {"search", "list_companies", "get_company", "list_buildings", "get_building", "get_production_overview",
              "find_production_issues", "list_routes", "get_route", "list_warehouse_requests", "list_vehicles", "get_supply_chain",
              "list_products", "get_product", "list_recipes", "list_building_types", "get_building_type", "list_cities", "get_city",
              "get_shop", "find_shops", "list_regions", "get_region", "get_market", "get_tech_tree", "get_research_state"},
    "history": {"get_finances"},
}

PRD_ENUMS = {  # (tool, param) -> PRD enum (14.x)
    ("search", "kinds"): {"building", "building_type", "product", "recipe", "city", "region", "company", "tech", "shop"},
    ("list_buildings", "kind"): {"factory", "gatherer", "farm", "harvester", "field", "warehouse", "depot", "shop", "hq", "other"},
    ("list_buildings", "status"): {"working", "idle", "disabled", "blocked"},
    ("list_buildings", "sort"): {"name", "type", "stock_ratio", "produced_last_month", "upkeep"},
    ("get_building", "include"): {"production", "inventory", "outgoing_routes", "incoming_routes", "requests", "modules", "history",
                                  "vehicles"},
    ("find_production_issues", "kinds"): {"disabled", "no_recipe", "missing_input", "output_full", "no_modules", "deposit_depleted",
                                          "polluted", "requirements_unmet", "route_error", "route_dormant_auto_wh", "route_keep_all",
                                          "inventory_accumulating"},
    ("list_routes", "sort"): {"distance", "dispatch_cost", "product", "origin"},
    ("get_supply_chain", "direction"): {"upstream", "downstream", "both"},
    ("get_supply_chain", "mode"): {"actual", "recipe"},
    ("get_finances", "group_by"): {"category", "overview_group"},
    ("list_cities", "sort"): {"population", "tier"},
    ("find_shops", "sort"): {"demand", "price", "distance", "unmet_demand"},
    ("get_market", "include"): {"state", "contracts", "auctions"},
    ("get_tech_tree", "state"): {"unlocked", "available", "queued", "researching", "locked", "teaser"},
}
PRD_DEFAULTS = {("get_supply_chain", "depth"): 6, ("get_supply_chain", "direction"): "upstream",
                ("get_supply_chain", "mode"): "actual", ("get_finances", "months"): 6, ("get_finances", "group_by"): "category",
                ("list_routes", "include_dormant"): True, ("list_vehicles", "aggregate"): True}


def _enum_of(ps: dict):
    if "enum" in ps:
        return set(ps["enum"])
    if ps.get("type") == "array" and "enum" in ps.get("items", {}):
        return set(ps["items"]["enum"])
    return None


def test_registry_is_exactly_29_prd_tools_with_prd_13_7_scopes(h):
    assert len(specs(h)) == 29 and set(specs(h)) == set(PRD_TOOLS) == set(TOOL_NAMES)
    for scope, names in PRD_13_7.items():
        for n in names:
            assert REFRESH_SCOPE[n] == scope == specs(h)[n].scope, n
    assert set().union(*PRD_13_7.values()) == set(PRD_TOOLS)


@pytest.mark.parametrize("name", PRD_TOOLS)
def test_tool_parameters_cover_prd_and_common_parameters(h, name):
    schema = specs(h)[name].input_schema()
    props = schema["properties"]
    missing = PRD_PARAMS[name] - set(props)
    assert not missing, f"{name}: PRD 14 parameters missing: {missing}"
    assert set(schema["required"]) == PRD_REQUIRED.get(name, set()), name
    assert schema.get("additionalProperties") is False
    # PRD 13.2: fresh / allow_stale exactly on tools whose scope is not `none`
    has_scope = REFRESH_SCOPE[name] != "none"
    assert ("fresh" in props) == has_scope and ("allow_stale" in props) == has_scope, name
    if has_scope:
        assert props["fresh"] == {**props["fresh"], "type": "boolean", "default": False}
        assert props["allow_stale"] == {**props["allow_stale"], "type": "boolean", "default": False}
    # PRD 13.2 list parameters: limit default 25 / max 50, cursor opaque string, fields compact|full default compact
    if "limit" in props:
        assert props["limit"]["type"] == "integer" and props["limit"]["default"] == 25
        assert props["limit"]["maximum"] == 50 and props["limit"]["minimum"] == 1
    if "cursor" in props:
        assert props["cursor"]["type"] == "string"
    if "fields" in props:
        assert set(props["fields"]["enum"]) == {"compact", "full"} and props["fields"]["default"] == "compact"
    if name.startswith("list_"):
        assert {"limit", "cursor"} <= set(props), name
    # PRD 13.2: paging, sort and fields exactly where 14 lists them
    assert ({"limit", "cursor"} <= set(props)) == (name in PAGED_TOOLS), name
    assert ("limit" in props or "cursor" in props) == (name in PAGED_TOOLS), name
    assert ("sort" in props) == (name in SORTED_TOOLS), name
    assert ("fields" in props) == name.startswith("list_"), name
    for (tool, p), enum in PRD_ENUMS.items():
        if tool == name:
            assert _enum_of(props[p]) == enum, (name, p, _enum_of(props[p]))
    for (tool, p), default in PRD_DEFAULTS.items():
        if tool == name:
            assert props[p].get("default") == default, (name, p)
    if name == "get_supply_chain":
        assert props["depth"]["maximum"] == 12


def test_no_parameters_beyond_prd_14_and_13_2(h):
    # Former DEFECT IN-4, closed by the PRD: list_vehicles.vehicle is a PRD 14.4 parameter.
    extra = {n: set(s.input_schema()["properties"]) - PRD_PARAMS[n] - COMMON_13_2 for n, s in specs(h).items()}
    assert {n: e for n, e in extra.items() if e} == {}


# ============================================================================ (b) argument validation (generic)

def _bad_values(ps: dict) -> list:
    t = ps.get("type")
    if t == "boolean":
        return ["true", "false", "yes", 1, 0, None, [], {}, ""]
    if t == "integer":
        out = ["5", 5.5, None, True, [], {}, "", 10 ** 30, -(10 ** 30)]
        if "minimum" in ps:
            out.append(ps["minimum"] - 1)
        if "maximum" in ps:
            out.append(ps["maximum"] + 1)
        return out
    if t == "number":
        return ["5", None, True, [], {}, "", 0, -1, 1e12]
    if t == "string":
        out = [5, 1.5, True, None, [], {}]
        if "enum" in ps:
            out += ["bogus", "", ps["enum"][0].upper() + "X"]
        return out
    if t == "array":
        out = ["x", None, {}, 5, True, [None], [5]]
        if "enum" in ps.get("items", {}):
            out.append(["bogus"])
        if ps.get("uniqueItems") and "enum" in ps.get("items", {}):
            out.append([ps["items"]["enum"][0]] * 2)
        return out
    if t == "object":
        return ["x", None, [], 5, True, {"k": 5}]
    return []


@pytest.mark.parametrize("name", PRD_TOOLS)
def test_wrong_json_types_and_ranges_are_invalid_argument(h, name):
    props = specs(h)[name].input_schema()["properties"]
    for p, ps in props.items():
        for bad in _bad_values(ps):
            r = call(h, name, {**VALID_CALLS[name], p: bad})
            assert r["ok"] is False and r["error"]["code"] == "invalid_argument", (name, p, bad, r.get("error"))
            assert r["error"]["hint"] is None or isinstance(r["error"]["hint"], str)


@pytest.mark.parametrize("name", PRD_TOOLS)
def test_unknown_parameters_and_non_object_arguments(h, name):
    for extra in ({"Fresh": True}, {"": 1}, {"limit ": 5}, {"__proto__": {}}, {"x" * 300: 1}):
        r = call(h, name, {**VALID_CALLS[name], **extra})
        assert r["error"]["code"] == "invalid_argument", (name, extra)
    for args in ([], "x", 5, True):
        r = check(name, asyncio.run(h.app.call(name, args)))
        assert r["error"]["code"] == "invalid_argument"
    # None is "no arguments" (MCP allows omitting arguments)
    r = check(name, asyncio.run(h.app.call(name, None)))
    assert r["ok"] or r["error"]["code"] == "invalid_argument"


@pytest.mark.parametrize("name", PRD_TOOLS)
def test_integral_floats_follow_json_schema_integer_semantics(h, name):
    """JSON Schema (2020-12) treats 5.0 as an integer; such values must behave like the integer, never crash."""
    props = specs(h)[name].input_schema()["properties"]
    for p, ps in props.items():
        if ps.get("type") != "integer":
            continue
        val = ps.get("default", ps.get("minimum", 1))
        r_int = call(h, name, {**VALID_CALLS[name], p: int(val)})
        r_flt = call(h, name, {**VALID_CALLS[name], p: float(val)})
        assert r_flt["ok"] == r_int["ok"], (name, p)
        if r_int["ok"]:
            assert r_flt["data"] == r_int["data"], (name, p)


@pytest.mark.parametrize("name", PRD_TOOLS)
def test_odd_but_well_typed_strings_never_crash(h, name):
    """Empty, whitespace, control/RTL characters in free-text parameters: a defined code, schema-valid envelope.
    PRD 13.2: an empty or whitespace-only value is invalid_argument."""
    props = specs(h)[name].input_schema()["properties"]
    allowed = {"invalid_argument", "not_found", "ambiguous", "section_unavailable", "stale_reference"}
    for p, ps in props.items():
        if ps.get("type") != "string" or "enum" in ps or p == "cursor":
            continue
        for val in ("", " ", "\t\n", "\x00", "‮gaz", "%s%n{0}", "../../heartbeat.json", "building:", "route:|||"):
            r = call(h, name, {**VALID_CALLS[name], p: val})
            if not val.strip():
                assert r["ok"] is False and r["error"]["code"] == "invalid_argument", (name, p, repr(val))
            else:
                assert r["ok"] or r["error"]["code"] in allowed, (name, p, val, r["error"])


# (tool, free-text parameter) pairs whose not_found message echoes the argument (one per affected tool)
HUGE_ARG_CASES = [
    ("get_company", "company"), ("get_finances", "company"), ("list_buildings", "owner"), ("get_building", "building"),
    ("get_production_overview", "product"), ("find_production_issues", "product"), ("list_routes", "origin"),
    ("list_warehouse_requests", "endpoint"), ("list_vehicles", "fleet_building"), ("get_supply_chain", "product"),
    ("get_product", "product"), ("list_recipes", "product"), ("get_recipe", "recipe"), ("list_building_types", "product"),
    ("get_building_type", "building_type"), ("get_city", "city"), ("get_shop", "shop"), ("find_shops", "product"),
    ("list_regions", "owner"), ("get_region", "region"), ("get_tech_tree", "company"), ("get_research_state", "company"),
]


# Regression test for DEFECT IN-1 (fixed).
@pytest.mark.parametrize("name,param", HUGE_ARG_CASES)
def test_error_responses_respect_size_cap_for_huge_string_arguments(h, name, param):
    args = {**VALID_CALLS[name], param: "Z" * 100_000}
    if name == "get_recipe":
        args.pop("product", None)
    r = h.call(name, args)
    assert json_size(r) <= cfg.RESPONSE_SIZE_CAP_BYTES, (name, param, json_size(r), r.get("error", {}).get("code"))


def test_huge_string_arguments_never_crash(h):
    for name, spec in specs(h).items():
        for p, ps in spec.input_schema()["properties"].items():
            if ps.get("type") == "string" and "enum" not in ps:
                r = h.call(name, {**VALID_CALLS[name], p: "Z" * 100_000})
                assert r["ok"] or r["error"]["code"] in ("invalid_argument", "not_found", "ambiguous"), (name, p, r["error"])


# Regression test for DEFECT IN-1 (fixed).
def test_unknown_tool_name_error_respects_size_cap(h):
    r = asyncio.run(h.app.call("x" * 100_000, {}))
    assert r["error"]["code"] == "invalid_argument"
    assert json_size(r) <= cfg.RESPONSE_SIZE_CAP_BYTES


def test_validation_error_message_is_bounded(h):
    """The validation layer itself truncates its message (app.validate_args) even for huge invalid values."""
    r = call(h, "search", {"query": "q" * 100_000})
    assert r["error"]["code"] == "invalid_argument" and len(r["error"]["message"]) <= 300


# ============================================================================ (c) cursor engine

def _rows(r: dict, h, name) -> list:
    key = h.app.specs[name]  # the page key is the only top-level list that paginate() filled
    lists = [k for k, v in r["data"].items() if isinstance(v, list) and k != "unavailable"]
    for k in ("routes", "buildings", "issues", "products", "recipes", "building_types", "companies", "requests", "groups",
              "vehicles", "nodes", "results", "cities", "shops", "regions"):
        if k in lists:
            return r["data"][k]
    raise AssertionError((name, lists, key))


def _paged_args(name):
    args = dict(VALID_CALLS[name])
    args.pop("limit", None)
    return args


@pytest.mark.parametrize("name", PRD_TOOLS)
def test_full_iteration_limit_1_matches_single_page(h, name):
    if name not in list_tools(h):
        pytest.skip("not a paginated tool")
    base = _paged_args(name)
    full = call(h, name, {**base, "limit": 50})
    assert full["ok"], full.get("error")
    total = full["page"]["total"]
    assert isinstance(total, int) and total == len(_rows(full, h, name)) or total > 50
    seen, cursor, pages = [], None, 0
    while True:
        args = {**base, "limit": 1}
        if cursor:
            args["cursor"] = cursor
        r = call(h, name, args)
        assert r["ok"], r.get("error")
        assert r["page"]["total"] == total  # total consistent on every page
        rows = _rows(r, h, name)
        assert len(rows) <= 1
        seen.extend(json.dumps(x, sort_keys=True) for x in rows)
        cursor = r["page"]["next_cursor"]
        pages += 1
        if cursor is None:
            break
        assert pages <= total + 1, "pagination does not terminate"
    assert pages == max(total, 1)
    if total <= 50:
        assert seen == [json.dumps(x, sort_keys=True) for x in _rows(full, h, name)]
    assert len(seen) == len(set(seen)) or total != len(seen)


def _first_cursor(h, name):
    r = call(h, name, {**_paged_args(name), "limit": 1})
    return r["page"]["next_cursor"]


def _garbage_cursors(token_cursor: str | None):
    b = lambda s: base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")
    out = ["garbage", "!!!!", " ", "====", b("hello"), b("v1|abc|tok"), b("v1|-5|tok"), b("v2|0|tok"), b("v1|0|wrongtoken"),
           b("v1|1"), "A" * 5000, "€", b("v1|1|") + "%%"]
    if token_cursor:
        raw = base64.urlsafe_b64decode(token_cursor + "=" * (-len(token_cursor) % 4)).decode()
        _v, _o, tok = raw.split("|", 2)
        out += [b(f"v1|-1|{tok}"), b(f"v1|1.5|{tok}"), b(f"v2|1|{tok}"), b(f"v1| 1|{tok}x")]
    return out


@pytest.mark.parametrize("name", PRD_TOOLS)
def test_tampered_and_garbage_cursors_rejected(h, name):
    if name not in list_tools(h):
        pytest.skip("not a paginated tool")
    good = _first_cursor(h, name)
    for c in _garbage_cursors(good):
        r = call(h, name, {**_paged_args(name), "limit": 1, "cursor": c})
        assert r["ok"] is False and r["error"]["code"] == "invalid_argument", (name, c[:40], r.get("page"))


def test_forged_cursor_with_valid_token_and_huge_offset_returns_empty_last_page(h):
    good = _first_cursor(h, "list_routes")
    raw = base64.urlsafe_b64decode(good + "=" * (-len(good) % 4)).decode()
    tok = raw.split("|", 2)[2]
    r = call(h, "list_routes", {**_paged_args("list_routes"), "limit": 1, "cursor": encode_cursor(10 ** 15, tok)})
    assert r["ok"] and r["data"]["routes"] == [] and r["page"]["next_cursor"] is None
    assert r["page"]["total"] == call(h, "list_routes", _paged_args("list_routes"))["page"]["total"]


def test_cursor_reuse_across_tools_is_rejected(h):
    cursors = {n: _first_cursor(h, n) for n in list_tools(h)}
    cursors = {n: c for n, c in cursors.items() if c}
    assert len(cursors) >= 5, cursors.keys()
    for src, c in cursors.items():
        for dst in cursors:
            if dst == src:
                continue
            r = call(h, dst, {**_paged_args(dst), "limit": 1, "cursor": c})
            assert r["error"]["code"] == "invalid_argument", (src, dst)


def test_cursor_reuse_with_different_filters_or_sort_is_rejected_but_limit_and_fields_may_change(h):
    c = call(h, "list_routes", {"limit": 2, "sort": "distance"})["page"]["next_cursor"]
    assert call(h, "list_routes", {"limit": 2, "sort": "product", "cursor": c})["error"]["code"] == "invalid_argument"
    assert call(h, "list_routes", {"limit": 2, "cursor": c})["error"]["code"] == "invalid_argument"
    assert call(h, "list_routes", {"limit": 2, "sort": "distance", "errors_only": True, "cursor": c})["error"]["code"] == \
        "invalid_argument"
    # fresh / allow_stale / limit / fields are not filters (app._cursor_token) and do not invalidate the cursor
    r = call(h, "list_routes", {"limit": 5, "sort": "distance", "fields": "full", "allow_stale": True, "cursor": c})
    assert r["ok"] and r["page"]["offset"] == 2
    # cursor on a non-paginated tool is an unknown parameter
    assert call(h, "get_route", {"route": ROUTE_1, "cursor": c})["error"]["code"] == "invalid_argument"


def test_cursor_after_world_session_change_never_mixes_sessions(h):
    """PRD-ambiguity: PRD 13.2 only says the cursor is opaque; nothing says a cursor must be rejected after a
    quickload. Invariant asserted: the page served is entirely from the new session (PRD 16 quickload row)."""
    c = call(h, "list_routes", {"limit": 3})["page"]["next_cursor"]
    h.write_world(bf.build_world(world_session=bf.WORLD_SESSION_2))
    r = call(h, "list_routes", {"limit": 3, "cursor": c})
    if r["ok"]:
        assert r["meta"]["world_session"] == bf.WORLD_SESSION_2
        assert all(s["world_session"] == bf.WORLD_SESSION_2 for s in r["meta"]["snapshots"] if s["family"] != "static")
    else:
        assert r["error"]["code"] in ("invalid_argument", "stale_reference")


def test_cursor_after_new_capture_keeps_total_of_the_new_capture(h):
    c = call(h, "list_routes", {"limit": 5})["page"]["next_cursor"]
    w = copy.deepcopy(h.world)
    w["state"]["data"]["routes_player"] = w["state"]["data"]["routes_player"][:4]
    w["state"]["seq"] = 11
    w["state"]["content_hash"] = bf.content_hash(w["state"]["data"])
    h.write_world(w, ("state",))
    r = call(h, "list_routes", {"limit": 5, "cursor": c})
    assert r["ok"] and r["data"]["routes"] == [] and r["page"]["total"] == 4 and r["page"]["next_cursor"] is None


def test_cursor_tokens_are_independent_of_argument_order(h):
    a = call(h, "list_routes", {"product": "Gas", "sort": "distance", "limit": 1})["page"]["next_cursor"]
    r = call(h, "list_routes", {"limit": 1, "cursor": a, "sort": "distance", "product": "Gas"})
    assert r["ok"] and r["page"]["offset"] == 1


# ============================================================================ (d) size cap (generic)

IN5 = ()  # regression test for DEFECT IN-5 (fixed)


@pytest.mark.parametrize("name", [pytest.param(n, marks=IN5) if n in ("search", "list_buildings") else n for n in PRD_TOOLS])
def test_size_cap_truncation_preserves_pagination(h, name, monkeypatch):
    """With a cap that fits ~2 rows, the truncation path must keep the page sequence lossless and duplicate-free,
    flag `truncated` and provide next_cursor (PRD 14.9)."""
    if name not in list_tools(h):
        pytest.skip("not a paginated tool")
    base = _paged_args(name)
    full = call(h, name, {**base, "limit": 50})
    rows = _rows(full, h, name)
    if len(rows) < 3 or full["page"]["total"] > 50:
        pytest.skip("needs 3..50 rows")
    empty = copy.deepcopy(full)
    for k, v in empty["data"].items():
        if v is rows:
            empty["data"][k] = []
    key = next(k for k, v in full["data"].items() if v is rows)
    empty["data"][key] = []
    biggest = max(json_size(x) for x in rows)
    cap = json_size(empty) + 2 * biggest + 600
    if json_size(full) <= cap:
        pytest.skip("rows too small to force truncation")
    monkeypatch.setattr(cfg, "RESPONSE_SIZE_CAP_BYTES", cap)
    seen, cursor, pages, truncated_seen = [], None, 0, False
    while True:
        args = {**base, "limit": 50}
        if cursor:
            args["cursor"] = cursor
        r = call(h, name, args)  # check_response asserts size <= patched cap
        got = _rows(r, h, name)
        assert got, "an empty page before the end"
        remaining = full["page"]["total"] - len(seen)
        if len(got) < min(50, remaining):
            assert "truncated" in wcodes(r) and r["page"]["next_cursor"], (name, len(got), remaining)
            truncated_seen = True
        seen.extend(json.dumps(x, sort_keys=True) for x in got)
        assert r["page"]["total"] == full["page"]["total"]
        cursor = r["page"]["next_cursor"]
        pages += 1
        if not cursor:
            break
        assert pages < 100
    assert truncated_seen
    assert seen == [json.dumps(x, sort_keys=True) for x in rows]


@pytest.fixture
def shared_constants():
    """Module-level lists that responses return by reference; restored in place because IN-6 mutates them."""
    from roi_mcp.tools import common, status
    saved_rp, saved_lim = copy.deepcopy(common.ROUTE_PROVENANCE), list(status.LIMITATIONS)
    yield
    common.ROUTE_PROVENANCE.clear()
    common.ROUTE_PROVENANCE.update(copy.deepcopy(saved_rp))
    status.LIMITATIONS[:] = saved_lim


def _huge_name_world(h):
    w = copy.deepcopy(h.world)
    for b in w["state"]["data"]["buildings_player"]:
        if b["key"] == bf.B_GW1:
            b["display_name"] = "N" * 40_000
    w["state"]["seq"] = 55
    w["state"]["content_hash"] = bf.content_hash(w["state"]["data"])
    h.write_world(w, ("state",))


# Regression test for DEFECT IN-3 (fixed).
@pytest.mark.parametrize("name,args", [("get_route", {"route": ROUTE_1}), ("get_building", {"building": bf.B_GW1})])
def test_size_cap_holds_with_a_huge_game_string(h, name, args, shared_constants):
    _huge_name_world(h)
    r = h.call(name, args)
    assert r["ok"]
    assert json_size(r) <= cfg.RESPONSE_SIZE_CAP_BYTES, json_size(r)


def test_size_cap_with_huge_game_string_is_at_least_flagged(h, shared_constants):
    """Invariant that holds today: whenever the cap logic shortens anything, `truncated` is reported."""
    _huge_name_world(h)
    for name, args in (("get_route", {"route": ROUTE_1}), ("get_building", {"building": bf.B_GW1}), ("list_routes", {})):
        r = h.call(name, args)
        assert r["ok"]
        if json_size(r) > cfg.RESPONSE_SIZE_CAP_BYTES or len(r["data"].get("routes", [None] * 13)) < 13:
            assert "truncated" in wcodes(r), name


# Regression test for DEFECT IN-6 (fixed).
def test_an_oversized_response_does_not_change_later_answers(h, shared_constants):
    normal = copy.deepcopy(call(h, "get_route", {"route": ROUTE_1}))   # the response aliases module state
    _huge_name_world(h)
    assert "truncated" in wcodes(h.call("get_route", {"route": ROUTE_1}))
    h.write_world(h.world, ("state",))          # back to the original files
    h.restart()                                 # PRD 13.1: same files -> identical answers
    again = call(h, "get_route", {"route": ROUTE_1})
    assert again["data"]["provenance"] == normal["data"]["provenance"]


# Regression test for DEFECT IN-5 (fixed).
def test_pagination_under_real_cap_never_skips_rows(h):
    w = copy.deepcopy(h.world)
    routes = w["state"]["data"]["routes_player"]
    base = routes[0]
    for i in range(80):
        r = copy.deepcopy(base)
        r["occurrence"] = i + 1
        r["route_key"] = f"{base['origin']}|Gas|{base['destination']}|own|{i + 1}"
        routes.append(r)
    for b in w["state"]["data"]["buildings_player"]:
        if b["key"] == bf.B_GW1:
            b["display_name"] = "Puits " + "x" * 96     # a 102-character player-chosen name
    w["state"]["seq"] = 196
    w["state"]["content_hash"] = bf.content_hash(w["state"]["data"])
    h.write_world(w, ("state",))
    seen, cursor = 0, None
    while True:
        args = {"limit": 50, "fields": "full"}
        if cursor:
            args["cursor"] = cursor
        r = call(h, "list_routes", args)
        assert r["page"]["offset"] == seen, f"rows {seen}..{r['page']['offset'] - 1} were never returned"
        seen += len(r["data"]["routes"])
        cursor = r["page"]["next_cursor"]
        if not cursor:
            break
    assert seen == r["page"]["total"] == 93


# ============================================================================ (e) snapshot store

def test_bom_prefixed_file_is_read_or_falls_back_never_silently(h):
    assert call(h, "list_routes")["meta"]["snapshot"]["seq"] == 10
    doc = copy.deepcopy(h.world["state"])
    doc["seq"] = 11
    p = h.exchange / "state.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps(doc).encode("utf-8"))
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))
    r = call(h, "list_routes")
    assert r["ok"]
    seq = r["meta"]["snapshot"]["seq"]
    assert seq == 11 or (seq == 10 and "snapshot_invalid_using_previous" in wcodes(r))


def test_utf16_file_falls_back_to_previous_with_warning(h):
    assert call(h, "list_routes")["ok"]
    doc = copy.deepcopy(h.world["state"])
    doc["seq"] = 11
    p = h.exchange / "state.json"
    p.write_bytes(json.dumps(doc).encode("utf-16"))
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))
    r = call(h, "list_routes")
    assert r["ok"] and r["meta"]["snapshot"]["seq"] == 10 and "snapshot_invalid_using_previous" in wcodes(r)


def test_transient_invalid_read_is_retried_once(h, monkeypatch):
    """PRD 11.4: on failure retry once. Simulates a reader that hits the file mid-replacement (stat then a torn read):
    the retry reads the complete new file and no fallback warning is raised."""
    assert call(h, "list_routes")["meta"]["snapshot"]["seq"] == 10
    doc = copy.deepcopy(h.world["state"])
    doc["seq"] = 11
    h.write_raw("state", json.dumps(doc))
    store = h.app.store
    real = store._read_once
    calls = {"n": 0}

    def torn(family, path):
        if family == "state":
            calls["n"] += 1
            if calls["n"] == 1:
                return "invalid", "json_error: torn read"
        return real(family, path)

    monkeypatch.setattr(store, "_read_once", torn)
    r = call(h, "list_routes")
    assert calls["n"] == 2
    assert r["ok"] and r["meta"]["snapshot"]["seq"] == 11 and "snapshot_invalid_using_previous" not in wcodes(r)


def test_file_replaced_between_reads_reloads_newest(h):
    """Sequential replacements with equal size: every change of (mtime,size) is picked up; same seq/hash is not reloaded."""
    call(h, "list_routes")
    for seq in (11, 12, 13):
        doc = copy.deepcopy(h.world["state"])
        doc["seq"] = seq
        h.write_raw("state", json.dumps(doc))
        assert call(h, "list_routes")["meta"]["snapshot"]["seq"] == seq
    n = h.app.store.families["state"].reload_count
    h.write_raw("state", (h.exchange / "state.json").read_text(encoding="utf-8"))  # same content, new mtime
    assert call(h, "list_routes")["meta"]["snapshot"]["seq"] == 13
    assert h.app.store.families["state"].reload_count == n


@pytest.mark.parametrize("family,probe", [("static", ("list_products", {})), ("history", ("get_finances", {})),
                                          ("state", ("get_building", {"building": bf.B_PC1}))])
@pytest.mark.parametrize("garbage", ["{trunc", "", "[]", '{"schema": "roi-mcp/x"}'])
def test_previous_snapshot_fallback_for_every_family(h, family, probe, garbage):
    assert call(h, *probe)["ok"]
    h.write_raw(family, garbage)
    r = call(h, *probe)
    assert r["ok"] and "snapshot_invalid_using_previous" in wcodes(r), (family, garbage)
    w = next(x for x in r["meta"]["warnings"] if x["code"] == "snapshot_invalid_using_previous")
    assert f"{family}.json" in w["detail"]


def test_previous_snapshot_of_another_session_is_never_served_as_current(h):
    """PRD 11.4: keep the last good snapshot *of the same world_session*."""
    assert call(h, "list_routes")["ok"]
    hb = bf.build_heartbeat(h.clock.now(), world_session=bf.WORLD_SESSION_2, static_doc=h.world["static"])
    h.write_raw("heartbeat", json.dumps(hb))
    h.write_raw("state", "{broken")
    for name in PRD_TOOLS:
        if specs(h)[name].kind != "runtime" or REFRESH_SCOPE[name] != "state":
            continue
        r = call(h, name, VALID_CALLS[name])
        assert not r["ok"] and r["error"]["code"] == "snapshot_unavailable", name
        r2 = call(h, name, {**VALID_CALLS[name], "allow_stale": True})
        if r2["ok"]:
            assert r2["meta"]["stale"] and r2["meta"]["stale_reason"] == "world_session_changed", name


@pytest.mark.parametrize("family,tools", [("state", None), ("history", ("get_finances",))])
def test_snapshot_with_other_pid_same_session_is_never_current(h, family, tools):
    """PRD 11.7: current requires snapshot.pid == heartbeat.pid (PID of another process, same world_session)."""
    doc = copy.deepcopy(h.world[family])
    doc["pid"] = 999
    doc["seq"] = doc["seq"] + 50
    h.write_raw(family, json.dumps(doc))
    names = tools or [n for n in PRD_TOOLS if specs(h)[n].kind == "runtime" and REFRESH_SCOPE[n] == "state"]
    for name in names:
        r = call(h, name, VALID_CALLS[name])
        if r["ok"]:
            assert r["meta"]["source"] != "live_snapshot", name
            for s in r["meta"]["snapshots"]:
                assert s["family"] != family or s["stale"], name
        else:
            assert r["error"]["code"] == "snapshot_unavailable", (name, r["error"])
        r2 = call(h, name, {**VALID_CALLS[name], "allow_stale": True})
        assert r2["ok"] and r2["meta"]["stale"] is True, name


def test_heartbeat_present_state_missing(empty_h, world):
    h = empty_h
    h.write_world(world, ("heartbeat", "static", "history"))
    for name in PRD_TOOLS:
        r = call(h, name, VALID_CALLS[name])
        kind = specs(h)[name].kind
        if name == "get_game_status":
            assert r["ok"]
        elif kind == "runtime" and REFRESH_SCOPE[name] == "state":
            assert r["error"]["code"] == "snapshot_unavailable", name   # PRD 13.3: ready, no snapshot of the family yet
        elif name == "get_finances":
            assert r["ok"], r.get("error")                              # its family (history) exists
        else:   # static / static-plus-live: definition from static, live part unavailable (PRD 13.7 rules)
            assert r["ok"], (name, r.get("error"))
            assert r["meta"]["source"] == "static_catalog", name


def test_very_large_state_file(h):
    w = copy.deepcopy(h.world)
    routes = w["state"]["data"]["routes_player"]
    base = routes[0]
    for i in range(3000):
        r = copy.deepcopy(base)
        r["occurrence"] = i + 1
        r["route_key"] = f"{base['origin']}|Gas|{base['destination']}|own|{i + 1}"
        routes.append(r)
    w["state"]["seq"] = 90
    w["state"]["content_hash"] = bf.content_hash(w["state"]["data"])
    h.write_world(w, ("state",))
    assert (h.exchange / "state.json").stat().st_size > 2_000_000
    r = call(h, "list_routes", {"limit": 50, "fields": "full"})
    assert r["ok"] and r["page"]["total"] == 3013 and r["page"]["next_cursor"]
    assert call(h, "get_route", {"route": ROUTE_1})["ok"]


@pytest.mark.parametrize("version", ["2.0.0", "0.9.0", "10.0.0"])
def test_static_schema_major_mismatch(h, version):
    doc = copy.deepcopy(h.world["static"])
    doc["schema_version"] = version
    h.write_raw("static", json.dumps(doc))
    for name in PRD_TOOLS:
        r = call(h, name, VALID_CALLS[name])
        if name == "get_game_status":
            assert r["ok"]
        elif specs(h)[name].kind in ("static", "static_live"):
            assert r["error"]["code"] == "schema_mismatch", name     # PRD 11.4 / 16 "Schema mismatch | same"
        if not r["ok"] and r["error"]["code"] == "schema_mismatch":
            assert version in r["error"]["message"]


def test_heartbeat_invalid_after_good_degrades_to_unresponsive(h):
    assert call(h, "list_routes")["ok"]
    h.write_raw("heartbeat", "{broken")
    r = call(h, "list_routes")
    assert r["ok"] or r["error"]["code"] in LIFECYCLE_CODES
    h.clock.advance(10)
    assert call(h, "list_routes")["error"]["code"] == "observer_unresponsive"


# ============================================================================ (f) liveness and allow_stale

@pytest.mark.parametrize("state", sorted(NOT_LIVE))
def test_lifecycle_matrix_for_every_tool(tmp_path, clock, state):
    hh, obs = live_env(tmp_path, clock)
    enter(hh, obs, state)
    code, reason = NOT_LIVE[state]
    for name in PRD_TOOLS:
        kind = specs(hh)[name].kind
        r = call(hh, name, VALID_CALLS[name])
        if kind == "status":
            assert r["ok"], "get_game_status always answers (PRD 14.1)"
            continue
        if kind == "runtime":
            assert r["error"]["code"] == code, (state, name, r.get("error"))
            r2 = call(hh, name, {**VALID_CALLS[name], "allow_stale": True})
            assert r2["ok"], (state, name, r2.get("error"))
            m = r2["meta"]
            assert m["source"] == "stale_snapshot" and m["stale"] is True, (state, name)
            if reason is not None:
                assert m["stale_reason"] == reason, (state, name, m["stale_reason"])
        else:  # static catalogue tools (PRD 13.4): from static.json with catalog_from_previous_session
            assert r["ok"], (state, name, r.get("error"))
            assert "catalog_from_previous_session" in wcodes(r), (state, name)
            assert r["meta"]["source"] in ("static_catalog", "stale_snapshot")


def test_unsupported_build_ignores_allow_stale(tmp_path, clock):
    """PRD 16 unsupported_build row: runtime and static tools return unsupported_build (no allow_stale escape)."""
    hh, obs = live_env(tmp_path, clock)
    obs.unsupported_build()
    for name in PRD_TOOLS:
        if name == "get_game_status":
            continue
        args = {**VALID_CALLS[name]}
        if REFRESH_SCOPE[name] != "none":
            args["allow_stale"] = True
        assert call(hh, name, args)["error"]["code"] == "unsupported_build", name


@pytest.mark.parametrize("since_start,expected", [(59.0, "starting"), (61.0, "observer_not_detected")])
def test_grace_period_boundary(tmp_path, clock, since_start, expected):
    d = tmp_path / "g"
    d.mkdir()
    hh = Harness(d, clock, FakeProcs([game_proc(start=clock.now() - timedelta(seconds=since_start))]))
    r = call(hh, "list_routes")
    assert r["error"]["code"] == "observer_not_detected" and r["meta"]["game_state"] == expected
    assert call(hh, "get_game_status")["ok"]


@pytest.mark.parametrize("age,ok", [(4.4, True), (5.6, False)])
def test_heartbeat_staleness_boundary(tmp_path, clock, age, ok):
    hh, obs = live_env(tmp_path, clock)
    clock.advance(age)  # heartbeat written 0.5 s before the observer's clock reading
    r = call(hh, "list_routes")
    assert r["ok"] is ok
    if not ok:
        assert r["error"]["code"] == "observer_unresponsive" and r["meta"]["game_state"] == "observer_unresponsive"


def test_main_thread_stale_is_a_warning_on_live_answers(tmp_path, clock):
    hh, obs = live_env(tmp_path, clock)
    obs.heartbeat("ready", tick_age_s=12)
    for name in PRD_TOOLS:
        r = call(hh, name, VALID_CALLS[name])
        assert r["ok"] and r["meta"]["game_state"] == "ready", name
        assert "game_unresponsive" in wcodes(r), name


def test_pid_reuse_by_a_newer_process_is_not_live(tmp_path, clock):
    """Same PID, but the process started after the heartbeat was written (PID reuse): PRD 11.7 row 2."""
    hh, obs = live_env(tmp_path, clock)
    clock.advance(3)
    hh.procs.procs = [game_proc(pid=bf.PID, start=clock.now())]  # restarted with the same pid; old heartbeat remains
    r = call(hh, "get_building", {"building": bf.B_PC1})
    assert r["error"]["code"] == "observer_not_detected" and r["meta"]["game_state"] == "starting"
    r2 = call(hh, "get_building", {"building": bf.B_PC1, "allow_stale": True})
    assert r2["ok"] and r2["meta"]["stale"] and r2["meta"]["source"] == "stale_snapshot"
    clock.advance(61)
    assert call(hh, "list_routes")["meta"]["game_state"] == "observer_not_detected"


def test_unknown_heartbeat_state_is_not_live(tmp_path, clock):
    hh, obs = live_env(tmp_path, clock)
    hb = json.loads((hh.exchange / "heartbeat.json").read_text(encoding="utf-8"))
    hb["data"]["state"] = "exploded"
    hh.write_raw("heartbeat", json.dumps(hb))
    r = call(hh, "list_routes")
    assert not r["ok"] or r["meta"]["source"] != "live_snapshot"


def test_multiple_game_processes_pick_the_heartbeat_pid(tmp_path, clock):
    hh, obs = live_env(tmp_path, clock)
    hh.procs.procs = [game_proc(pid=777, start=clock.now() - timedelta(seconds=1)), game_proc(start=clock.now() - timedelta(minutes=5))]
    r = call(hh, "list_routes")
    assert r["ok"] and r["meta"]["game_state"] == "ready"


# ============================================================================ (g) refresh manager

def test_nonces_strictly_increase_with_frozen_and_backward_clock(tmp_path):
    d = tmp_path / "n"
    d.mkdir()
    clock = FakeClock()
    rm = RefreshManager(RefreshRequestWriter(d, clock), clock, wait_s=3.0)

    async def go():
        out = []
        for scope in ("state", "state", "history", "state", "static"):
            req = await rm.request(scope, None, None)
            req.served = True
            out.append(req.nonce)
        clock.set(clock.now() - timedelta(hours=1))   # wall clock steps back (NTP)
        req = await rm.request("state", None, None)
        out.append(req.nonce)
        return out

    nonces = asyncio.run(go())
    assert all(b > a for a, b in zip(nonces, nonces[1:])), nonces
    doc = read_request(d)
    assert doc["requests"]["state"] == nonces[-1] and doc["requests"]["history"] == nonces[2] and doc["requests"]["static"] == nonces[4]


# Regression test for DEFECT IN-2 (fixed).
def test_request_served_before_the_waiter_polls_is_not_reused(tmp_path, clock):
    hh, obs = _observer_harness(tmp_path, clock, procs=FakeProcs(), wait=3.0, serve=False)
    flag = {"served": False}

    def on_sleep(c):
        if not flag["served"]:
            obs.serve_refresh("state")          # observer serves nonce N right after call A started waiting
            flag["served"] = True
    clock.on_sleep = on_sleep

    async def late():
        while not flag["served"]:
            await asyncio.sleep(0)
        clock.advance(0.1)                       # call C arrives after N was served, before A polls again
        started = clock.now()
        return started, await hh.acall("list_routes", {"fresh": True})

    async def go():
        a = asyncio.create_task(hh.acall("list_routes", {"fresh": True}))
        c = asyncio.create_task(late())
        return await a, await c

    ra, (c_started, rc) = asyncio.run(go())
    check("list_routes", ra)
    check("list_routes", rc)
    captured = bf.iso(c_started)
    # PRD 13.2/13.2a: only an outstanding request may be reused; C must get a capture made after it was called
    assert hh.app.refresh.writes == 2
    assert rc["meta"]["snapshot"]["captured_utc"] >= captured or "refresh_timeout" in wcodes(rc)


def test_outstanding_unserved_request_expires_after_refresh_wait(tmp_path, clock):
    hh, obs = _observer_harness(tmp_path, clock, procs=FakeProcs(), wait=1.0, serve=False)
    r1 = check("list_routes", hh.call("list_routes", {"fresh": True}))
    assert "refresh_timeout" in wcodes(r1) and hh.app.refresh.writes == 1
    n1 = read_request(hh.exchange)["requests"]["state"]
    r2 = check("get_building", hh.call("get_building", {"building": bf.B_PC1, "fresh": True}))
    assert "refresh_timeout" in wcodes(r2)
    assert hh.app.refresh.writes == 2 and read_request(hh.exchange)["requests"]["state"] > n1


def test_concurrent_fresh_calls_of_different_tools_share_one_nonce_per_scope(tmp_path, clock):
    hh, obs = _observer_harness(tmp_path, clock, procs=FakeProcs(), wait=2.0, serve=False)
    n = {"sleeps": 0}
    state_tools = ["list_routes", "get_building", "search", "list_products", "get_market"]

    def on_sleep(c):
        n["sleeps"] += 1
        if n["sleeps"] == len(state_tools) + 1:
            obs.serve_refresh("state")
            obs.serve_refresh("history")
    clock.on_sleep = on_sleep

    async def go():
        calls = [hh.acall(t, {**VALID_CALLS[t], "fresh": True}) for t in state_tools]
        calls.append(hh.acall("get_finances", {"fresh": True}))
        return await asyncio.gather(*calls)

    results = asyncio.run(go())
    for t, r in zip(state_tools + ["get_finances"], results):
        check(t, r)
        assert r["ok"] and "refresh_timeout" not in wcodes(r), (t, wcodes(r))
    assert hh.app.refresh.writes == 2  # one state nonce + one history nonce
    req = read_request(hh.exchange)["requests"]
    assert req["state"] is not None and req["history"] is not None and req["static"] is None
    seqs = {r["meta"]["snapshot"]["seq"] for t, r in zip(state_tools, results) if r["meta"]["snapshot"]["family"] == "state"}
    assert seqs == {obs.state_seq}


def test_scopes_are_waited_on_separately(tmp_path, clock):
    hh, obs = _observer_harness(tmp_path, clock, procs=FakeProcs(), wait=1.0, serve=False)
    clock.on_sleep = lambda c: obs.serve_refresh("history")   # the observer serves only the history scope

    async def go():
        return await asyncio.gather(hh.acall("list_routes", {"fresh": True}), hh.acall("get_finances", {"fresh": True}))

    routes, fin = asyncio.run(go())
    check("list_routes", routes)
    check("get_finances", fin)
    assert "refresh_timeout" in wcodes(routes) and "refresh_timeout" not in wcodes(fin)
    req = read_request(hh.exchange)["requests"]
    assert req["state"] is not None and req["history"] is not None   # PRD 11.6 / 13.2a: other scope preserved


def test_refresh_request_file_contract_after_many_writes(tmp_path, clock):
    hh, obs = _observer_harness(tmp_path, clock, procs=FakeProcs(), wait=0.5, serve=False)
    schema = Draft202012Validator(load_json_lenient(SCHEMA_DIR / "refresh-request.schema.json"))
    for name in ("list_routes", "get_finances", "list_cities"):
        check(name, hh.call(name, {"fresh": True}))
        clock.advance(1)
        doc = read_request(hh.exchange)
        assert list(schema.iter_errors(doc)) == []
        assert (hh.exchange / "refresh-request.json").stat().st_size < 1024   # observer ignores > 1 KB (PRD 11.6)
    hh.app.request_static_recovery()
    doc = read_request(hh.exchange)
    assert all(isinstance(doc["requests"][s], int) for s in ("state", "history", "static"))
    leftovers = [p.name for p in hh.exchange.iterdir() if ".tmp" in p.name]
    assert leftovers == []


def test_refresh_write_failure_is_atomic_and_answered(tmp_path, clock, monkeypatch):
    hh, obs = _observer_harness(tmp_path, clock, procs=FakeProcs(), wait=0.5, serve=False)
    check("get_finances", hh.call("get_finances", {"fresh": True}))
    before = (hh.exchange / "refresh-request.json").read_bytes()
    real_replace = os.replace

    def failing_replace(src, dst, *a, **kw):
        if str(dst).endswith("refresh-request.json"):
            raise PermissionError("sharing violation")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(refresh_mod.os, "replace", failing_replace)
    clock.advance(1)
    r = check("list_routes", hh.call("list_routes", {"fresh": True}))
    assert r["ok"] and "refresh_timeout" in wcodes(r)
    assert (hh.exchange / "refresh-request.json").read_bytes() == before        # never a partial file
    assert [p.name for p in hh.exchange.iterdir() if ".tmp" in p.name] == []   # temp file cleaned up


def test_served_with_unchanged_content_via_last_verified(tmp_path, clock):
    """PRD 11.3: unchanged content skips the write and only updates last_verified_utc; PRD 13.2 accepts
    last_verified_utc advancing as the served capture."""
    hh, obs = _observer_harness(tmp_path, clock, procs=FakeProcs(), wait=3.0, serve=False)
    clock.advance(20)   # the snapshot is now stale by age

    def on_sleep(c):
        req = obs.read_refresh_request()
        if req and req["requests"]["state"] and obs.refresh_served["state"] != req["requests"]["state"]:
            obs.refresh_served["state"] = req["requests"]["state"]
            hb = bf.build_heartbeat(c.now(), static_doc=obs.static_doc, state_doc=obs.state_doc, history_doc=obs.history_doc,
                                    refresh_served=dict(obs.refresh_served), seq=obs.hb_seq + 1)
            hb["data"]["families"]["state"]["last_verified_utc"] = bf.iso(c.now())
            obs.hb_seq += 1
            obs._write("heartbeat", hb)
    clock.on_sleep = on_sleep
    r = check("list_routes", hh.call("list_routes", {"fresh": True}))
    assert "refresh_timeout" not in wcodes(r) and r["meta"]["stale"] is False


def test_served_nonce_without_any_new_capture_times_out(tmp_path, clock):
    """PRD 13.2: waits for the served nonce AND the family's seq or last_verified_utc to advance."""
    hh, obs = _observer_harness(tmp_path, clock, procs=FakeProcs(), wait=1.0, serve=False)

    def on_sleep(c):
        req = obs.read_refresh_request()
        if req and req["requests"]["state"]:
            obs.refresh_served["state"] = req["requests"]["state"]
            obs.heartbeat("ready")   # claims served, but no new capture and last_verified unchanged
    clock.on_sleep = on_sleep
    r = check("list_routes", hh.call("list_routes", {"fresh": True}))
    assert r["ok"] and "refresh_timeout" in wcodes(r)


@pytest.mark.parametrize("state", sorted(NOT_LIVE))
def test_fresh_while_not_live_writes_nothing(tmp_path, clock, state):
    hh, obs = live_env(tmp_path, clock)
    enter(hh, obs, state)
    for name in ("list_routes", "get_finances", "list_products", "get_supply_chain"):
        r = call(hh, name, {**VALID_CALLS[name], "fresh": True})
        if specs(hh)[name].kind == "runtime":
            assert r["error"]["code"] == NOT_LIVE[state][0], (name, r.get("error"))
            r2 = call(hh, name, {**VALID_CALLS[name], "fresh": True, "allow_stale": True})
            assert r2["ok"] and r2["meta"]["stale"] is True
    assert read_request(hh.exchange) is None


def test_world_session_change_while_waiting_and_served_on_ready_entry(tmp_path, clock):
    """PRD 11.6: ready-entry captures satisfy all nonces seen before; the answer is wholly the new session."""
    hh, obs = _observer_harness(tmp_path, clock, procs=FakeProcs(), wait=3.0, serve=False)

    def on_sleep(c):
        req = obs.read_refresh_request()
        if req and obs.world_session == bf.WORLD_SESSION:
            obs.loading()
            obs.ready(world_session=bf.WORLD_SESSION_2, publish=True)
            obs.refresh_served["state"] = req["requests"]["state"]
            obs.heartbeat("ready")
    clock.on_sleep = on_sleep
    r = check("get_building", hh.call("get_building", {"building": bf.B_PC1, "fresh": True}))
    assert r["ok"] and r["meta"]["world_session"] == bf.WORLD_SESSION_2
    assert all(s["world_session"] == bf.WORLD_SESSION_2 for s in r["meta"]["snapshots"] if s["family"] != "static")


def test_world_session_change_while_waiting_without_capture(tmp_path, clock):
    """PRD 16 quickload row: the old-session snapshot is never served as current."""
    hh, obs = _observer_harness(tmp_path, clock, procs=FakeProcs(), wait=1.0, serve=False)

    def on_sleep(c):
        if obs.world_session == bf.WORLD_SESSION:
            obs.loading()
            obs.ready(world_session=bf.WORLD_SESSION_2, publish=False)
    clock.on_sleep = on_sleep
    r = check("get_building", hh.call("get_building", {"building": bf.B_PC1, "fresh": True}))
    assert not r["ok"] and r["error"]["code"] == "snapshot_unavailable"
    assert "refresh_timeout" in wcodes(r)


def test_game_leaves_ready_while_waiting(tmp_path, clock):
    hh, obs = _observer_harness(tmp_path, clock, procs=FakeProcs(), wait=1.0, serve=False)
    clock.on_sleep = lambda c: obs.loading() if obs.world_session else None
    r = check("list_routes", hh.call("list_routes", {"fresh": True}))
    assert not r["ok"] and r["error"]["code"] == "loading"


# ============================================================================ (h) start-up and recovery

def test_startup_without_exchange_dir_then_recovery_without_restart(tmp_path, clock):
    d = tmp_path / "absent" / "RoiMcp"
    procs = FakeProcs([])
    hh = Harness(d, clock, procs)
    for name in PRD_TOOLS:
        args = {**VALID_CALLS[name]}
        if REFRESH_SCOPE[name] != "none":
            args["fresh"] = True
        r = call(hh, name, args)
        if name == "get_game_status":
            assert r["ok"] and r["data"]["game"]["running"] is False
        elif r["ok"]:
            # PRD 13.4 / 14.1: `search` answers from what exists (nothing here) and lists what is unavailable
            assert name == "search" and r["data"]["results"] == [] and r["meta"]["source"] == "none", name
        else:
            assert r["error"]["code"] in ("game_not_running", "snapshot_unavailable"), (name, r["error"])
    assert not d.exists(), "the server never creates the exchange directory"
    procs.procs = [game_proc(start=clock.now() - timedelta(minutes=5))]
    d.mkdir(parents=True)
    obs = FakeObserver(d, clock, procs)
    obs.ready()
    for name in PRD_TOOLS:
        r = call(hh, name, VALID_CALLS[name])
        assert r["ok"], (name, r.get("error"))


def test_files_appearing_one_by_one(tmp_path, clock):
    d = tmp_path / "RoiMcp"
    d.mkdir()
    procs = FakeProcs([game_proc(start=clock.now() - timedelta(minutes=5))])
    hh = Harness(d, clock, procs)
    w = bf.build_world(clock.now())
    assert call(hh, "list_routes")["error"]["code"] == "observer_not_detected"
    hh.write_world(w, ("heartbeat",))
    assert call(hh, "list_routes")["error"]["code"] == "snapshot_unavailable"
    assert call(hh, "list_products")["error"]["code"] == "snapshot_unavailable"
    hh.write_world(w, ("static",))
    assert call(hh, "list_products")["ok"]
    assert call(hh, "list_routes")["error"]["code"] == "snapshot_unavailable"
    hh.write_world(w, ("state",))
    assert call(hh, "list_routes")["ok"]
    assert call(hh, "get_finances")["error"]["code"] == "snapshot_unavailable"
    hh.write_world(w, ("history",))
    assert call(hh, "get_finances")["ok"]


def _all_answers(hh, extra=None):
    out = {}
    for name in PRD_TOOLS:
        args = {**VALID_CALLS[name], **(extra if REFRESH_SCOPE[name] != "none" and extra else {})}
        out[name] = check(name, hh.call(name, args))
    return out


@pytest.mark.parametrize("situation", ["live", "stale_age", "menu_allow_stale", "not_running_allow_stale", "quickload_pending",
                                       "static_mismatch", "observer_unresponsive"])
def test_restart_determinism(tmp_path, clock, situation):
    """PRD 13.1: restarting the server while the game is open yields identical answers from the same files."""
    hh, obs = live_env(tmp_path, clock)
    extra = None
    if situation == "stale_age":
        clock.advance(20)
        obs.heartbeat("ready")
    elif situation == "menu_allow_stale":
        obs.menu()
        extra = {"allow_stale": True}
    elif situation == "not_running_allow_stale":
        obs.kill_process()
        extra = {"allow_stale": True}
    elif situation == "quickload_pending":
        obs.loading()
        obs.ready(world_session=bf.WORLD_SESSION_2, publish=False)
    elif situation == "static_mismatch":
        st = copy.deepcopy(obs.state_doc)
        st["static_ref"] = {"seq": 99, "content_hash": "nope"}
        st["seq"] += 1
        obs._write("state", st)
    elif situation == "observer_unresponsive":
        clock.advance(8)
        extra = {"allow_stale": True}
    before = _all_answers(hh, extra)
    hh.restart()
    after = _all_answers(hh, extra)
    for name in PRD_TOOLS:
        assert before[name] == after[name], (situation, name)


# ============================================================================ (i) concurrency

def test_many_simultaneous_calls_equal_sequential_answers(h):
    sequential = {n: h.call(n, VALID_CALLS[n]) for n in PRD_TOOLS}

    async def go():
        return await asyncio.gather(*(h.acall(n, VALID_CALLS[n]) for n in PRD_TOOLS * 3))

    results = asyncio.run(go())
    for n, r in zip(PRD_TOOLS * 3, results):
        check(n, r)
        assert r == sequential[n], n


def test_simultaneous_fresh_and_plain_calls_with_serving_observer(tmp_path, clock):
    hh, obs = _observer_harness(tmp_path, clock, procs=FakeProcs(), wait=3.0, serve=True)
    calls = []
    for n in PRD_TOOLS:
        calls.append((n, dict(VALID_CALLS[n])))
        if REFRESH_SCOPE[n] != "none":
            calls.append((n, {**VALID_CALLS[n], "fresh": True}))
            calls.append((n, {**VALID_CALLS[n], "allow_stale": True, "fresh": True}))

    async def go():
        return await asyncio.gather(*(hh.acall(n, a) for n, a in calls))

    results = asyncio.run(go())
    for (n, a), r in zip(calls, results):
        check(n, r)
        assert r["ok"], (n, a, r.get("error"))
        assert r["meta"]["world_session"] == bf.WORLD_SESSION
    assert [p.name for p in hh.exchange.iterdir() if ".tmp" in p.name] == []


def test_concurrent_calls_across_a_session_switch_never_mix(h):
    async def go():
        first = [h.acall(n, VALID_CALLS[n]) for n in PRD_TOOLS]
        r1 = await asyncio.gather(*first)
        h.write_world(bf.build_world(world_session=bf.WORLD_SESSION_2))
        r2 = await asyncio.gather(*(h.acall(n, VALID_CALLS[n]) for n in PRD_TOOLS))
        return r1, r2

    r1, r2 = asyncio.run(go())
    for n, a, b in zip(PRD_TOOLS, r1, r2):
        check(n, a)
        check(n, b)
        for s in b["meta"]["snapshots"]:
            if s["family"] != "static":
                assert s["world_session"] == bf.WORLD_SESSION_2, n


# ============================================================================ logging (PRD 19)

def test_call_log_has_digest_not_payload(h):
    logger = logging.getLogger("roi_mcp.tools")
    records = []

    class Cap(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = Cap(level=logging.INFO)
    old = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        secret = "SENTINEL-PAYLOAD-Zq9"
        call(h, "search", {"query": secret})
        call(h, "list_routes", {"limit": 99})
        call(h, "get_route", {"route": ROUTE_1})
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)
    assert len(records) == 3
    assert all(secret not in m for m in records)
    assert records[0].startswith("tool=search args=") and "result=ok" in records[0] and "dur_ms=" in records[0]
    assert "result=invalid_argument" in records[1]
    assert "seq=state:10" in records[2]
