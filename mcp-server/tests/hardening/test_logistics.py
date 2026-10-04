"""Hardening: logistics tools `list_routes`, `get_route`, `list_warehouse_requests`, `list_vehicles`.

Every response (ok and error) is validated with `check_response` (tool response schema + 30 KB cap +
meta keys). Expected behaviour is justified by PRD sections (7, 11.7, 12.3, 13, 14.4, 14.9, 15, 16),
docs/USAGE.md, docs/SNAPSHOT-FORMAT.md or gate E2 (docs/VALIDATION-REPORT.md session D). Where the PRD
is silent only invariants are asserted (valid envelope, schema-valid, no internal_error); those places are
marked "AMBIGUITY".

Defects are kept as strict xfails tagged `DEFECT LO-<n>`.
"""

from __future__ import annotations

import asyncio
import copy
import json
import math

import pytest

import build_fixtures as bf
from conftest import FakeClock, FakeProcs, Harness, game_proc  # noqa: F401  (harness types used by fixtures)
from fake_observer import FakeObserver
from roi_mcp import config as cfg
from roi_mcp.util import json_size
from test_tools_contract import ROUTE_1, VALID_CALLS, _observer_harness, check_response

LOGI = ("list_routes", "get_route", "list_warehouse_requests", "list_vehicles")
BASE_ARGS = {"list_routes": {}, "get_route": {"route": ROUTE_1}, "list_warehouse_requests": {}, "list_vehicles": {}}
WS = bf.WORLD_SESSION
WS2 = bf.WORLD_SESSION_2
COMPACT_ROUTE_KEYS = {"route_id", "origin", "destination", "product", "transport_mode", "max_send", "min_keep", "distance_tiles",
                      "dispatch_cost", "vehicle_capacity", "dispatch_amount_now", "in_flight", "paused", "dormant", "errors"}
AI_ROUTE_B = f"{bf.AI_PF}|Paint|{bf.S_HW2}|own|0"
AI_ROUTE_C = "FlowerFarm@250,60|Flowers|ChemicalPlant@255,70|own|0"
STATE_TP = "TradingPost@5,5"


# ============================================================================ helpers

def call(h, name, args=None) -> dict:
    r = h.call(name, args if args is not None else {})
    check_response(name, r)
    if not r["ok"]:
        assert r["error"]["code"] != "internal_error", r["error"]
    return r


def ok(h, name, args=None) -> dict:
    r = call(h, name, args)
    assert r["ok"], r.get("error")
    return r


def err(h, name, args=None) -> dict:
    r = call(h, name, args)
    assert r["ok"] is False, f"{name} {args} unexpectedly ok"
    return r["error"]


def wcodes(r) -> list[str]:
    return [w["code"] for w in r["meta"]["warnings"]]


def rid(key: str) -> str:
    return f"route:{key}"


def rkey(origin, product, dest, source="own", n=0) -> str:
    return f"{origin}|{product}|{dest}|{source}|{n}"


def ids(rows, k="route_id") -> list:
    return [r[k] for r in rows]


def publish(h, data: dict, sections: dict | None = None) -> int:
    """Publish a new, schema-valid state.json built from `data` (new seq + content hash) and a heartbeat
    whose family status matches it. Synthetic data only."""
    h._seq = getattr(h, "_seq", 500) + 1
    w = copy.deepcopy(h.world)
    doc = bf.envelope("state", data, seq=h._seq, now=h.clock.now(), static_ref=bf.static_ref_of(w["static"]),
                      sections=sections if sections is not None else bf.state_sections(data))
    w["state"] = doc
    w["heartbeat"] = bf.build_heartbeat(h.clock.now(), static_doc=w["static"], state_doc=doc, history_doc=w["history"])
    h.write_world(w, ("heartbeat", "state"))
    r = ok(h, "list_vehicles", {"aggregate": True}) if data.get("vehicles") is not None else None
    if r is not None:
        assert r["meta"]["snapshot"]["seq"] == h._seq, "new state.json was not picked up"
        assert "snapshot_invalid_using_previous" not in wcodes(r)
    return h._seq


def with_sections(data: dict, **overrides) -> dict:
    secs = bf.state_sections(data)
    for sec, (status, reason) in overrides.items():
        secs[sec] = bf.section_status(1512, 0, status, reason)
    return secs


def walk(h, name, args, key) -> tuple[list, list]:
    """Follow page.next_cursor to the end; returns (rows, responses)."""
    rows, resps = [], []
    a = dict(args)
    for _ in range(200):
        r = ok(h, name, a)
        resps.append(r)
        rows.extend(r["data"][key])
        nxt = r["page"]["next_cursor"]
        if nxt is None:
            return rows, resps
        a = {**args, "cursor": nxt}
    raise AssertionError("pagination did not terminate")


def strip_meta(r: dict) -> dict:
    return {"data": r["data"], "page": r["page"]}


def default_order_keys(routes: list[dict]) -> list[str]:
    return [rid(r["route_key"]) for r in sorted(routes, key=lambda r: (r["origin"], r["slot_index"], r["route_key"]))]


# ============================================================================ valid calls, defaults, envelope

@pytest.mark.parametrize("name", LOGI)
def test_valid_contract_call_is_schema_valid(h, name):
    r = ok(h, name, VALID_CALLS[name])
    assert r["meta"]["source"] == "live_snapshot" and r["meta"]["stale"] is False and r["meta"]["world_session"] == WS
    assert "routes_player" in r["meta"]["snapshot"]["sections_used"] or name in ("list_warehouse_requests", "list_vehicles")


def test_list_routes_omitted_optionals_defaults(h):
    r = ok(h, "list_routes")
    d = r["data"]
    routes = bf.player_routes()
    # PRD 13.2: limit default 25 -> all 13 routes; company defaults to the player (PRD 14.4)
    assert r["page"]["total"] == len(routes) == 13 and r["page"]["next_cursor"] is None and r["page"]["limit"] == 25
    assert d["company"] == f"company:{bf.PLAYER}"
    # include_dormant default true: the dormant AUTO_WH route is listed (PRD 14.4)
    assert any(x["dormant"] for x in d["routes"])
    # fields default compact: exactly the PRD 14.4 compact keys
    for row in d["routes"]:
        assert set(row) == COMPACT_ROUTE_KEYS
        assert isinstance(row["dispatch_amount_now"], (int, type(None)))
    # tool states the shared destination semantics (PRD 14.9)
    assert "SHARED" in d["semantics"]["max_send"] and "per route" in d["semantics"]["min_keep"]
    assert set(r["data"]["provenance"]["game_computed"]) >= {"distance_tiles", "dispatch_cost", "vehicle_capacity"}


def test_list_routes_fields_full_adds_section_12_3_fields(h):
    d = ok(h, "list_routes", {"fields": "full"})["data"]
    for row in d["routes"]:
        for k in ("source", "endpoint", "wait_for_full_vehicle", "path_status", "destination_stock",
                  "destination_incoming_reserved", "destination_free_space", "origin_stock", "straight_line_tiles",
                  "cost_per_unit_at_capacity", "unit_costs"):
            assert k in row, k
        assert isinstance(row["dispatch_amount_now"], dict) and "complete" in row["dispatch_amount_now"]
        assert row["straight_line_tiles"]["estimate"] is True and row["unit_costs"]["method"] == "D-ROUTE-1"


def test_list_routes_full_row_equals_get_route(h):
    full = {x["route_id"]: x for x in ok(h, "list_routes", {"fields": "full"})["data"]["routes"]}
    for route_id, row in full.items():
        g = ok(h, "get_route", {"route": route_id})["data"]
        for k, v in row.items():
            assert g[k] == v, (route_id, k)


def test_list_vehicles_omitted_optionals_defaults(h):
    r = ok(h, "list_vehicles")
    d = r["data"]
    # aggregate default true (PRD 14.4): groups + player fleets, no per-vehicle rows
    assert "groups" in d and "vehicles" not in d
    assert {g["owner"]["actor_id"] for g in d["groups"]} == {bf.PLAYER}
    assert len(d["groups"]) == 3 and r["page"]["total"] == 3
    assert d["fleets"] and d["fleets"][0]["building"] == f"building:{bf.B_TD}"
    assert d["total_active_all_owners"] == 6
    assert "session" in d["identity_note"]


def test_list_warehouse_requests_omitted_optionals_defaults(h):
    r = ok(h, "list_warehouse_requests")
    d = r["data"]
    assert r["page"]["total"] == 2
    # rows per 12.3.4; int.MaxValue -> "fill"
    paint = next(q for q in d["requests"] if q["product"] == "product:Paint")
    chem = next(q for q in d["requests"] if q["product"] == "product:Chemicals")
    assert paint["requested_amount"] == "fill" and paint["fill"] is True
    assert chem["requested_amount"] == 50 and chem["fill"] is False
    assert paint["request_id"] == f"request:{bf.B_WH}|Paint|0"
    assert paint["allowed_depots"] == ["TruckDepot"] and paint["endpoint"]["id"] == f"building:{bf.B_WH}"
    assert d["logistic_requests_enabled"] is True and "fill" in d["notes"]


def test_get_route_requires_route(h):
    assert err(h, "get_route", {})["code"] == "invalid_argument"


# ============================================================================ parameter validation

INVALID = [
    ("list_routes", {"bogus": 1}), ("list_routes", {"limit": "5"}), ("list_routes", {"limit": 0}), ("list_routes", {"limit": 51}),
    ("list_routes", {"limit": -1}), ("list_routes", {"limit": True}), ("list_routes", {"limit": 2.5}),
    ("list_routes", {"sort": "bogus"}), ("list_routes", {"sort": "DISTANCE"}), ("list_routes", {"sort": None}),
    ("list_routes", {"fields": "FULL"}), ("list_routes", {"fields": "all"}), ("list_routes", {"include_dormant": "no"}),
    ("list_routes", {"errors_only": 1}), ("list_routes", {"product": 5}), ("list_routes", {"origin": None}),
    ("list_routes", {"destination": ["a"]}), ("list_routes", {"company": 1}), ("list_routes", {"transport_mode": 3}),
    ("list_routes", {"cursor": 5}), ("list_routes", {"fresh": "yes"}), ("list_routes", {"allow_stale": "x"}),
    ("list_routes", {"paused": True}), ("list_routes", {"include_path": True}), ("list_routes", {"route": ROUTE_1}),
    ("list_routes", {"origin": "   "}),
    ("get_route", {"route": None}), ("get_route", {"route": 5}), ("get_route", {"route": [ROUTE_1]}),
    ("get_route", {"route": ROUTE_1, "include_path": "yes"}), ("get_route", {"route": ROUTE_1, "fields": "full"}),
    ("get_route", {"route": ROUTE_1, "limit": 5}), ("get_route", {"route": ROUTE_1, "cursor": "x"}),
    ("get_route", {"route": ROUTE_1, "bogus": 1}), ("get_route", {"route": ROUTE_1, "sort": "distance"}),
    ("list_warehouse_requests", {"sort": "priority"}), ("list_warehouse_requests", {"endpoint": 3}),
    ("list_warehouse_requests", {"bogus": 1}), ("list_warehouse_requests", {"limit": 51}),
    ("list_warehouse_requests", {"active_only": True}),
    ("list_vehicles", {"aggregate": "no"}), ("list_vehicles", {"aggregate": 0}), ("list_vehicles", {"vehicle": 5}),
    ("list_vehicles", {"sort": "x"}), ("list_vehicles", {"bogus": 1}), ("list_vehicles", {"limit": 0}),
]


@pytest.mark.parametrize("name,args", INVALID, ids=[f"{n}-{json.dumps(a)[:40]}" for n, a in INVALID])
def test_invalid_arguments(h, name, args):
    assert err(h, name, args)["code"] == "invalid_argument"


@pytest.mark.parametrize("name", LOGI)
def test_arguments_must_be_an_object(h, name):
    r = asyncio.run(h.app.call(name, [1, 2]))
    check_response(name, r)
    assert r["error"]["code"] == "invalid_argument"


@pytest.mark.parametrize("limit,n", [(1, 1), (13, 13), (50, 13)])
def test_limit_boundaries(h, limit, n):
    r = ok(h, "list_routes", {"limit": limit})
    assert len(r["data"]["routes"]) == n and r["page"]["total"] == 13
    assert (r["page"]["next_cursor"] is None) == (n == 13)


@pytest.mark.parametrize("value", ["", "   ", "	"])
def test_empty_string_filter_is_invalid(h, value):
    # PRD 13.2: an empty or whitespace-only text filter is invalid_argument; omitting it means no filter.
    r = call(h, "list_routes", {"origin": value})
    assert r["ok"] is False and r["error"]["code"] == "invalid_argument"


# ============================================================================ list_routes filters

@pytest.mark.parametrize("product", ["Gas", "product:Gas", "gaz", "GAZ", "Gaz"])
def test_product_filter_by_id_asset_display_and_english_name(h, product):
    rows = ok(h, "list_routes", {"product": product})["data"]["routes"]
    assert len(rows) == 4 and {x["product"] for x in rows} == {"product:Gas"}


@pytest.mark.parametrize("product", ["Unobtainium", "product:Nope", "building:" + bf.B_PC1])
def test_product_filter_not_found(h, product):
    assert err(h, "list_routes", {"product": product})["code"] == "not_found"


@pytest.mark.parametrize("origin,n", [(f"building:{bf.B_PF1}", 3), (bf.B_PF1, 3), (f"building:{bf.B_PC1}", 3),
                                      ("SIPHON 1", 1), ("siphon 1", 1), ("entrepot 1", 0), (f"building:{bf.B_WH}", 0)])
def test_origin_filter(h, origin, n):
    rows = ok(h, "list_routes", {"origin": origin})["data"]["routes"]
    assert len(rows) == n
    assert len({x["origin"]["id"] for x in rows}) <= 1


def test_origin_filter_ambiguous_display_name(h):
    # PRD 13.6: a name matching several entities returns ambiguous with candidates (max 10)
    e = err(h, "list_routes", {"origin": "PUITS DE GAZ 1"})
    assert e["code"] == "ambiguous" and 2 <= len(e["candidates"]) <= 10
    assert {c["id"] for c in e["candidates"]} >= {f"building:{bf.B_GW1}", "building:GasWell@160,118"}


@pytest.mark.parametrize("name,value", [("origin", "building:Nowhere@1,1"), ("origin", "Nope Factory"),
                                        ("destination", "building:Nowhere@1,1"), ("destination", "city:10")])
def test_building_filters_not_found(h, name, value):
    assert err(h, "list_routes", {name: value})["code"] == "not_found"


@pytest.mark.parametrize("args,n", [
    ({"destination": bf.B_PC1}, 3), ({"destination": f"building:{bf.S_HW1}"}, 1),
    ({"destination": bf.B_PC1, "product": "Gas"}, 3), ({"destination": bf.B_PC1, "product": "Paint"}, 0),
    ({"origin": bf.B_PC1, "destination": bf.B_PF1, "product": "Chemicals"}, 2),
    ({"origin": bf.B_PC1, "destination": bf.B_PF1, "product": "Gas"}, 0),
    ({"transport_mode": "Rail"}, 1), ({"transport_mode": "Road"}, 12), ({"transport_mode": "Air"}, 0),
    ({"include_dormant": False}, 12), ({"include_dormant": True}, 13),
    ({"errors_only": True}, 1), ({"errors_only": False}, 13),
    ({"product": "Gas", "include_dormant": False}, 3), ({"product": "Paint", "errors_only": True}, 1),
    ({"product": "Gas", "errors_only": True}, 0),
])
def test_filter_result_counts(h, args, n):
    r = ok(h, "list_routes", args)
    assert len(r["data"]["routes"]) == n and r["page"]["total"] == n
    if n == 0:
        assert r["data"]["routes"] == [] and r["page"]["next_cursor"] is None


def test_filter_semantics_hold_on_every_row(h):
    rows = ok(h, "list_routes", {"include_dormant": False, "fields": "full"})["data"]["routes"]
    assert not any(x["dormant"] for x in rows)
    err_rows = ok(h, "list_routes", {"errors_only": True})["data"]["routes"]
    assert err_rows and all(x["errors"] for x in err_rows)
    rail = ok(h, "list_routes", {"transport_mode": "Rail", "fields": "full"})["data"]["routes"]
    assert [x["source"] for x in rail] == ["TrainTerminal"]


def test_transport_mode_case(h):
    # AMBIGUITY: PRD 14.4 does not say whether transport_mode matching is case-sensitive.
    r = call(h, "list_routes", {"transport_mode": "rail"})
    assert r["ok"] and len(r["data"]["routes"]) in (0, 1)


@pytest.mark.parametrize("company", [None, "player", "company:1", "Acme Industries"])
def test_company_player_forms(h, company):
    args = {} if company is None else {"company": company}
    r = call(h, "list_routes", args)
    assert r["ok"] and r["page"]["total"] == 13 and r["data"]["company"] == "company:1"


def test_company_ai_without_routes_ai_is_section_unavailable(h):
    # PRD 12.2: routes_ai optional (off); PRD 13.3: section_unavailable names the section and reason
    e = err(h, "list_routes", {"company": "company:2"})
    assert e["code"] == "section_unavailable" and "routes_ai" in e["message"]
    assert e["details"]["section"] == "routes_ai"


@pytest.mark.parametrize("company", ["company:99", "Valmont", "city:10", "nobody"])
def test_company_not_found(h, company):
    assert err(h, "list_routes", {"company": company})["code"] == "not_found"


def _ai_routes_data():
    data = bf.state_data()
    data["routes_ai"] = [
        bf._route(bf.AI_PF, bf.S_HW2, "Paint", kind="shop", dest_owner=bf.CITY_BRI, dest_city=bf.CITY_BRI, distance=60),
        bf._route("FlowerFarm@250,60", "ChemicalPlant@255,70", "Flowers", dest_owner=bf.AI_C, distance=10),
    ]
    return data


def test_ai_routes_per_company_when_routes_ai_enabled(h):
    publish(h, _ai_routes_data())
    b = ok(h, "list_routes", {"company": "company:2"})["data"]
    c = ok(h, "list_routes", {"company": "company:3"})["data"]
    p = ok(h, "list_routes")["data"]
    assert ids(b["routes"]) == [rid(AI_ROUTE_B)] and b["company"] == "company:2"
    assert ids(c["routes"]) == [rid(AI_ROUTE_C)]
    assert rid(AI_ROUTE_B) not in ids(p["routes"]) and len(p["routes"]) == 13
    g = ok(h, "get_route", {"route": rid(AI_ROUTE_B)})["data"]
    assert g["origin"]["owner"]["actor_id"] == bf.AI_B


# Regression test for DEFECT LO-4 (fixed).
def test_ai_company_filter_does_not_leak_other_companies_routes(h):
    data = _ai_routes_data()
    data["buildings_ai"] = None
    publish(h, data, with_sections(data, buildings_ai=("failed", "exception")))
    r = call(h, "list_routes", {"company": "company:2"})
    # PRD 14.4: `company` selects that company's routes. AI_C's route must never be reported as AI_B's.
    if r["ok"]:
        assert rid(AI_ROUTE_C) not in ids(r["data"]["routes"])


# ============================================================================ sorting and determinism

def test_default_sort_is_origin_then_slot(h):
    rows = ok(h, "list_routes")["data"]["routes"]
    assert ids(rows) == default_order_keys(bf.player_routes())
    assert ids(ok(h, "list_routes", {"sort": "origin"})["data"]["routes"]) == ids(rows)


def _sorted_expected(routes, field):
    def k(r):
        v = r.get(field)
        return (v is None, v if v is not None else math.inf, rid(r["route_key"]))
    return [rid(r["route_key"]) for r in sorted(routes, key=k)]


@pytest.mark.parametrize("field", ["distance", "dispatch_cost"])
def test_numeric_sorts_ascending_nulls_last_ties_by_id(h, field):
    src = {"distance": "distance_tiles", "dispatch_cost": "dispatch_cost"}[field]
    rows = ok(h, "list_routes", {"sort": field})["data"]["routes"]
    assert ids(rows) == _sorted_expected(bf.player_routes(), src)
    vals = [x[src] for x in rows]
    nn = [v for v in vals if v is not None]
    assert nn == sorted(nn) and vals[len(nn):] == [None] * (len(vals) - len(nn))


def test_product_sort(h):
    rows = ok(h, "list_routes", {"sort": "product"})["data"]["routes"]
    assert [(x["product"], x["route_id"]) for x in rows] == sorted((x["product"], x["route_id"]) for x in rows)


# Regression test for DEFECT LO-1 (fixed).
def test_dispatch_cost_sort_with_zero_cost(h):
    data = bf.state_data()
    data["routes_player"][0]["dispatch_cost"] = 0.0  # cached path, zero cost (only 0 WITH NO PATH becomes null, PRD 12.3)
    publish(h, data)
    rows = ok(h, "list_routes", {"sort": "dispatch_cost"})["data"]["routes"]
    nn = [x["dispatch_cost"] for x in rows if x["dispatch_cost"] is not None]
    assert nn == sorted(nn) or nn == sorted(nn, reverse=True), nn


@pytest.mark.parametrize("name,args", [("list_routes", {"fields": "full", "limit": 50}),
                                       ("list_routes", {"sort": "distance"}),
                                       ("get_route", {"route": ROUTE_1}),
                                       ("list_warehouse_requests", {}),
                                       ("list_vehicles", {"aggregate": False}),
                                       ("list_vehicles", {})])
def test_identical_answers_across_calls_and_restart(h, name, args):
    a = strip_meta(ok(h, name, args))
    b = strip_meta(ok(h, name, args))
    h.restart()
    c = strip_meta(ok(h, name, args))
    assert a == b == c


# ============================================================================ get_route: id parsing

def test_get_route_echoes_id_and_full_row(h):
    d = ok(h, "get_route", {"route": ROUTE_1})["data"]
    assert d["route_id"] == ROUTE_1
    assert d["origin"]["id"] == f"building:{bf.B_GW1}" and d["destination"]["id"] == f"building:{bf.B_PC1}"
    assert d["source"] == "own" and d["product"] == "product:Gas"
    assert d["dispatch_amount_now"]["method"] == "replica:ManualDestinationManager.GetRequestedAmount"
    assert "inputs" in d["dispatch_amount_now"] and d["in_flight_requests"]["requests_started"] == 1


def test_duplicate_slots_have_distinct_occurrence_ids(h):
    k0 = rkey(bf.B_PC1, "Chemicals", bf.B_PF1, n=0)
    k1 = rkey(bf.B_PC1, "Chemicals", bf.B_PF1, n=1)
    d0 = ok(h, "get_route", {"route": rid(k0)})["data"]
    d1 = ok(h, "get_route", {"route": rid(k1)})["data"]
    assert d0["route_id"] != d1["route_id"] and d0["paused"] is False and d1["paused"] is True
    assert err(h, "get_route", {"route": rid(rkey(bf.B_PC1, "Chemicals", bf.B_PF1, n=2))})["code"] == "not_found"


def test_depot_source_route(h):
    d = ok(h, "get_route", {"route": rid(rkey(bf.B_PC1, "Chemicals", bf.B_WH, "TrainTerminal"))})["data"]
    assert d["source"] == "TrainTerminal" and d["transport_mode"] == "Rail" and d["dispatch_formula"] == "TrainTerminalDispatchCost"


@pytest.mark.parametrize("route", [
    rid(rkey(bf.B_GW1, "Water", bf.B_PC1)),            # wrong product
    rid(rkey(bf.B_GW1, "Gas", bf.B_PC1, "TrainTerminal")),  # wrong source
    rid(rkey(bf.B_GW1, "Gas", bf.B_PC2)),               # wrong destination
    rid(rkey(bf.B_PC1, "Gas", bf.B_GW1)),               # reversed
    rid(rkey(bf.B_GW1, "Gas", bf.B_PC1, n=1)),          # no duplicate
    f"route:{bf.B_GW1}|Gas|{bf.B_PC1}|own",              # missing <n>
])
def test_unknown_but_well_formed_route_ids_are_not_found(h, route):
    assert err(h, "get_route", {"route": route})["code"] == "not_found"


@pytest.mark.parametrize("route", ["", "Gas route", "route:", f"building:{bf.B_PC1}", f"ROUTE:{ROUTE_1[6:]}",
                                   f"product:Gas", "route", ":" + ROUTE_1, f"vehicle:{WS}:-1201"])
def test_malformed_route_ids_are_invalid_argument(h, route):
    assert err(h, "get_route", {"route": route})["code"] == "invalid_argument"


@pytest.mark.parametrize("route", ["route:garbage", "route:a|b|c|d|e|f", "route:|||| ", "route:https://example.invalid/x?y=1#frag",
                                   "route:%7Cown%7C0", "route:../../etc/passwd", "route:é漢字|Gas|x|own|0", "route:\u0000",
                                   "route:" + ROUTE_1, ROUTE_1 + " ", " " + ROUTE_1, ROUTE_1.replace("|", "%7C"),
                                   f"route:{bf.B_GW1}|Gas|{bf.B_PC1}|own|00", f"route:{bf.B_GW1}|Gas|{bf.B_PC1}|own|-0"])
def test_odd_route_ids_never_crash(h, route):
    # AMBIGUITY: PRD 13.3 offers not_found (id not resolvable) or invalid_argument (validation) for
    # malformed-but-prefixed ids and for surrounding whitespace; only invariants are asserted.
    r = call(h, "get_route", {"route": route})
    if r["ok"]:
        assert r["data"]["route_id"] == ROUTE_1
    else:
        assert r["error"]["code"] in ("not_found", "invalid_argument")


def _collision_data():
    """Two buildings on 'GasWell@20,30' after the observer's collision suffixing (PRD 7.2)."""
    k1, k2 = bf.B_GW1 + "#1a2b3c4d", bf.B_GW1 + "#9f8e7d6c"
    data = json.loads(json.dumps(bf.state_data()).replace(bf.B_GW1 + '"', k1 + '"').replace(bf.B_GW1 + "|", k1 + "|"))
    b2 = copy.deepcopy(next(b for b in data["buildings_player"] if b["key"] == k1))
    b2["key"], b2["display_name"] = k2, "PUITS DE GAZ 1B"
    data["buildings_player"].append(b2)
    data["routes_player"].append(bf._route(k2, bf.B_PC1, "Gas", max_send=8, distance=23, origin_stock=3, dest_stock=9,
                                           incoming=4))
    return data, k1, k2


def test_route_ids_with_collision_suffix(h):
    data, k1, k2 = _collision_data()
    publish(h, data)
    r1 = rid(rkey(k1, "Gas", bf.B_PC1))
    r2 = rid(rkey(k2, "Gas", bf.B_PC1))
    d1 = ok(h, "get_route", {"route": r1})["data"]
    d2 = ok(h, "get_route", {"route": r2})["data"]
    assert d1["route_id"] == r1 and d2["route_id"] == r2 and d1["origin"]["id"] == f"building:{k1}"
    assert ids(ok(h, "list_routes", {"origin": f"building:{k2}"})["data"]["routes"]) == [r2]
    assert ids(ok(h, "list_routes", {"origin": "PUITS DE GAZ 1B"})["data"]["routes"]) == [r2]
    assert err(h, "get_route", {"route": ROUTE_1})["code"] == "not_found"
    # vehicle job matching follows the suffixed key
    assert [v["id"] for v in d1["vehicles_assigned"]] == [f"vehicle:{WS}:-1201"] and d2["vehicles_assigned"] == []
    # shared Max Send stays consistent across all origins of the destination+product (E2)
    shared = ok(h, "list_routes", {"destination": bf.B_PC1, "product": "Gas"})["data"]["routes"]
    assert len(shared) == 4 and len({x["max_send"]["value"] for x in shared}) == 1


# ============================================================================ Max Send / Min Keep semantics

def test_max_send_shared_per_destination_and_product(h):
    # PRD 12.3.1 + E2: one cap per destination and product, identical for every origin.
    rows = ok(h, "list_routes", {"fields": "full", "limit": 50})["data"]["routes"]
    by = {}
    for x in rows:
        assert x["max_send"]["scope"] == "destination_product_shared"
        by.setdefault((x["destination"]["id"], x["product"]), set()).add((x["max_send"]["value"], x["max_send"]["mode"]))
    assert all(len(v) == 1 for v in by.values()), by
    gas = [x for x in rows if x["destination"]["id"] == f"building:{bf.B_PC1}" and x["product"] == "product:Gas"]
    assert len(gas) == 3 and {x["max_send"]["value"] for x in gas} == {8}
    # ...while Min Keep is per route (PRD 12.3.2)
    assert len({x["min_keep"]["value"] for x in gas}) > 1


def test_max_send_zero_is_unlimited_and_headroom_null(h):
    d = ok(h, "get_route", {"route": rid(rkey(bf.B_GW2, "Gas", bf.B_PC2))})["data"]
    assert d["max_send"]["value"] == 0 and d["max_send"]["unlimited"] is True and d["max_send"]["headroom_now"] is None


def test_headroom_now_matches_formula(h):
    # PRD 12.3.1: headroom_now = max(value - (stock + incoming_reserved), 0); null if unlimited
    rows = ok(h, "list_routes", {"fields": "full", "limit": 50})["data"]["routes"]
    checked = 0
    for x in rows:
        ms = x["max_send"]
        if ms["unlimited"]:
            assert ms["headroom_now"] is None
        elif ms["mode"] == "manual":
            assert ms["headroom_now"] == max(ms["value"] - (x["destination_stock"] + x["destination_incoming_reserved"]), 0)
            checked += 1
    assert checked >= 4
    assert ok(h, "get_route", {"route": ROUTE_1})["data"]["max_send"]["headroom_now"] == 0  # 8 - (9 + 4) -> 0


def test_auto_shop_demand_mode(h):
    d = ok(h, "get_route", {"route": rid(rkey(bf.B_PF1, "Paint", bf.S_HW1))})["data"]
    assert d["max_send"]["mode"] == "auto_shop_demand" and d["max_send"]["unlimited"] is False
    assert d["destination"]["kind"] == "shop" and d["destination"]["city"]["id"] == f"city:{bf.CITY_VAL}"


def test_keep_all_min_keep(h):
    # PRD 12.3.2: int.MaxValue = keep all, the route never dispatches
    key = rid(rkey(bf.B_GW2, "Gas", bf.B_PC2))
    d = ok(h, "get_route", {"route": key})["data"]
    assert d["min_keep"] == {"value": bf.INT_MAX, "keep_all": True, "ui_label_validated": False}
    assert d["dispatch_amount_now"]["value"] == 0
    assert d["unit_costs"]["cost_per_unit_at_current_amount"] is None  # D-ROUTE-1: null if amount is 0
    row = next(x for x in ok(h, "list_routes")["data"]["routes"] if x["route_id"] == key)
    assert row["dispatch_amount_now"] == 0 and row["min_keep"]["keep_all"] is True


def test_state_destination_reported_unlimited(h):
    data = bf.state_data()
    data["routes_player"].append(bf._route(bf.B_PF1, STATE_TP, "Paint", kind="state_trading", dest_owner=bf.STATE_ACTOR,
                                           max_send=0, distance=60, origin_stock=38, slot_index=3))
    publish(h, data)
    key = rid(rkey(bf.B_PF1, "Paint", STATE_TP))
    d = ok(h, "get_route", {"route": key})["data"]
    # E2: State destinations have no Max Send control and are reported as unlimited (value 0)
    assert d["max_send"]["value"] == 0 and d["max_send"]["unlimited"] is True and d["max_send"]["headroom_now"] is None
    assert d["destination"]["kind"] == "state_trading" and d["destination"]["owner"]["kind"] == "state"
    assert ids(ok(h, "list_routes", {"destination": STATE_TP})["data"]["routes"]) == [key]
    assert ids(ok(h, "list_routes", {"destination": "COMPTOIR D'ETAT"})["data"]["routes"]) == [key]


@pytest.mark.parametrize("ms_ok,mk_ok,warned", [(False, False, True), (True, False, True), (False, True, True), (True, True, False)])
def test_ui_label_unvalidated_warning(h, ms_ok, mk_ok, warned):
    # PRD 12.3.1 / 13.3 + E2: warning ui_label_unvalidated iff a value carries ui_label_validated: false
    data = bf.state_data()
    for r in data["routes_player"]:
        r["max_send"]["ui_label_validated"] = ms_ok
        r["min_keep"]["ui_label_validated"] = mk_ok
    publish(h, data)
    for name, args in (("list_routes", {}), ("get_route", {"route": ROUTE_1}), ("list_routes", {"fields": "full"})):
        r = ok(h, name, args)
        assert ("ui_label_unvalidated" in wcodes(r)) is warned, (name, ms_ok, mk_ok)


def test_incomplete_dispatch_replica_is_preserved(h):
    # PRD 12.3.3: unreviewed storage / world event -> complete: false with limited_by naming the reason
    data = bf.state_data()
    r0 = data["routes_player"][0]
    r0["max_send"]["storage_kind"] = "unknown"
    r0["dispatch_amount_now"].update({"value": 3, "complete": False, "limited_by": ["available", "world_event_unevaluated"]})
    r0["dispatch_amount_now"]["inputs"]["world_event_targets_destination"] = True
    publish(h, data)
    d = ok(h, "get_route", {"route": ROUTE_1})["data"]
    assert d["dispatch_amount_now"]["complete"] is False and "world_event_unevaluated" in d["dispatch_amount_now"]["limited_by"]
    full = next(x for x in ok(h, "list_routes", {"fields": "full"})["data"]["routes"] if x["route_id"] == ROUTE_1)
    assert full["dispatch_amount_now"]["complete"] is False
    compact = next(x for x in ok(h, "list_routes")["data"]["routes"] if x["route_id"] == ROUTE_1)
    assert compact["dispatch_amount_now"] == 3  # PRD 14.4 compact carries dispatch_amount_now.value only


def test_dispatch_amount_null_and_missing_storage_values(h):
    data = bf.state_data()
    r0 = data["routes_player"][0]
    r0["dispatch_amount_now"].update({"value": None, "complete": False, "limited_by": []})
    for k in ("destination_stock", "destination_incoming_reserved", "destination_free_space", "destination_slots"):
        r0[k] = None
    r0["max_send"]["headroom_now"] = None
    publish(h, data)
    d = ok(h, "get_route", {"route": ROUTE_1})["data"]
    assert d["dispatch_amount_now"]["value"] is None and d["destination_stock"] is None
    assert d["unit_costs"]["cost_per_unit_at_current_amount"] is None
    compact = next(x for x in ok(h, "list_routes")["data"]["routes"] if x["route_id"] == ROUTE_1)
    assert compact["dispatch_amount_now"] is None


# ============================================================================ distance, cost, path

def test_route_without_path(h):
    # PRD 12.3: game distance 0 -> null with path_status unavailable; dispatch cost null; errors no_path
    d = ok(h, "get_route", {"route": rid(rkey(bf.B_PF1, "Paint", bf.S_CS1))})["data"]
    assert d["path_status"] == "unavailable" and d["distance_tiles"] is None and d["dispatch_cost"] is None
    assert d["cost_per_unit_at_capacity"] is None and "no_path" in d["errors"]
    assert d["straight_line_tiles"]["euclidean"] is not None and d["straight_line_tiles"]["estimate"] is True


def test_cost_per_unit_and_straight_line(h):
    d = ok(h, "get_route", {"route": ROUTE_1})["data"]
    # D-ROUTE-1
    assert d["cost_per_unit_at_capacity"] == round(d["dispatch_cost"] / d["vehicle_capacity"], 4)
    f = ok(h, "get_route", {"route": rid(rkey(bf.B_FF, "Flowers", bf.B_CP))})["data"]
    assert f["dispatch_amount_now"]["value"] == 10
    assert f["unit_costs"]["cost_per_unit_at_current_amount"] == round(f["dispatch_cost"] / 10, 4)
    # D-DIST-1: GW1 (20,30) -> PC1 (40,32)
    assert d["straight_line_tiles"]["euclidean"] == pytest.approx(math.hypot(20, 2), abs=1e-3)
    assert d["straight_line_tiles"]["chebyshev"] == 20


def test_zero_vehicle_capacity_does_not_divide_by_zero(h):
    data = bf.state_data()
    data["routes_player"][0]["vehicle_capacity"] = 0
    publish(h, data)
    d = ok(h, "get_route", {"route": ROUTE_1})["data"]
    assert d["cost_per_unit_at_capacity"] is None


def test_include_path_when_route_paths_off_is_section_unavailable(h):
    # PRD 14.4: include_path only if route_paths captured; else section_unavailable
    e = err(h, "get_route", {"route": ROUTE_1, "include_path": True})
    assert e["code"] == "section_unavailable" and e["details"]["section"] == "route_paths"
    d = ok(h, "get_route", {"route": ROUTE_1, "include_path": False})["data"]
    assert "path" not in d


def test_include_path_when_route_paths_on(h):
    data = bf.state_data()
    k1 = rkey(bf.B_GW1, "Gas", bf.B_PC1)
    data["route_paths"] = [{"route_key": k1, "original_points": 40, "points": [[20, 30], [30, 30], [40, 32]]}]
    publish(h, data)
    d = ok(h, "get_route", {"route": rid(k1), "include_path": True})["data"]
    assert d["path"]["points"] == [[20, 30], [30, 30], [40, 32]]
    r = ok(h, "get_route", {"route": rid(rkey(bf.B_PF1, "Paint", bf.S_CS1)), "include_path": True})
    assert r["data"]["path"] is None and "path" in {u["field"] for u in r["data"]["unavailable"]}
    assert "route_paths" in r["meta"]["snapshot"]["sections_used"]


def test_include_path_route_paths_failed(h):
    data = bf.state_data()
    publish(h, data, with_sections(data, route_paths=("failed", "exception")))
    e = err(h, "get_route", {"route": ROUTE_1, "include_path": True})
    assert e["code"] == "section_unavailable" and "route_paths" in e["message"]


def test_endpoint_missing_route(h):
    data = bf.state_data()
    data["routes_player"][0]["endpoint"] = None
    data["routes_player"][0]["errors"] = ["destination_module_missing"]
    publish(h, data)
    d = ok(h, "get_route", {"route": ROUTE_1})["data"]
    assert d["endpoint"] is None and "destination_module_missing" in d["errors"]
    assert ROUTE_1 in ids(ok(h, "list_routes", {"errors_only": True})["data"]["routes"])


# ============================================================================ get_route: vehicles and in-flight

def test_get_route_vehicles_assigned(h):
    d = ok(h, "get_route", {"route": ROUTE_1})["data"]
    assert [v["id"] for v in d["vehicles_assigned"]] == [f"vehicle:{WS}:-1201"]
    assert d["vehicles_assigned"][0]["cargo"] == {"product": "product:Gas", "amount": 4}
    none = ok(h, "get_route", {"route": rid(rkey(bf.B_WS, "Water", bf.B_CP))})["data"]
    assert none["vehicles_assigned"] == []


def test_get_route_with_vehicles_section_failed(h):
    data = bf.state_data()
    data["vehicles"] = None
    publish(h, data, with_sections(data, vehicles=("failed", "exception")))
    r = ok(h, "get_route", {"route": ROUTE_1})
    assert r["data"]["vehicles_assigned"] == []
    assert "vehicles" in {u["field"] for u in r["data"]["unavailable"]}
    assert "vehicles" in r["meta"]["snapshot"]["sections_unavailable"] and "section_degraded" in wcodes(r)
    assert err(h, "list_vehicles")["code"] == "section_unavailable"
    assert err(h, "list_vehicles", {"aggregate": False})["code"] == "section_unavailable"


# ============================================================================ disabled / failed sections

def test_routes_player_disabled_reflection_missing(h):
    data = bf.state_data()
    data["routes_player"] = None
    secs = with_sections(data, routes_player=("disabled", "reflection_missing"))
    publish(h, data, secs)
    for name, args in (("list_routes", {}), ("get_route", {"route": ROUTE_1}), ("list_routes", {"product": "Gas"})):
        e = err(h, name, args)
        # PRD 13.3: section_unavailable names the section and reason
        assert e["code"] == "section_unavailable" and "routes_player" in e["message"] and "reflection_missing" in e["message"]
        assert e["details"]["section"] == "routes_player"
    # PRD 16: section failures affect the affected tools only
    ok(h, "list_vehicles")
    ok(h, "list_warehouse_requests")


@pytest.mark.parametrize("status", [("failed", "exception"), ("over_budget", "capture_ceiling_ms"), ("skipped", "size_cap")])
def test_requests_player_unavailable(h, status):
    data = bf.state_data()
    data["requests_player"] = None
    publish(h, data, with_sections(data, requests_player=status))
    e = err(h, "list_warehouse_requests")
    assert e["code"] == "section_unavailable" and "requests_player" in e["message"]
    ok(h, "list_routes")


# ============================================================================ pagination

def _many_routes_data(n=400):
    data = bf.state_data()
    base = data["routes_player"][0]
    for i in range(n):
        r = copy.deepcopy(base)
        r["occurrence"] = i + 1
        r["route_key"] = rkey(bf.B_GW1, "Gas", bf.B_PC1, n=i + 1)
        r["distance_tiles"] = (i * 7) % 50 + 1           # many ties
        r["dispatch_cost"] = round((250 + r["distance_tiles"] * 10) * bf.DISPATCH_DIFFICULTY, 2)
        r["slot_index"] = i % 3
        data["routes_player"].append(r)
    return data


def test_default_limit_25_and_page_walk(h):
    data = _many_routes_data(30)
    publish(h, data)
    r = ok(h, "list_routes")
    assert len(r["data"]["routes"]) == 25 and r["page"]["total"] == 43 and r["page"]["next_cursor"]
    rows, resps = walk(h, "list_routes", {}, "routes")
    assert ids(rows) == default_order_keys(data["routes_player"]) and len(set(ids(rows))) == 43
    assert resps[-1]["page"]["next_cursor"] is None and len(resps) == 2


@pytest.mark.parametrize("args", [{"limit": 50}, {"limit": 50, "fields": "full"}, {"limit": 7, "sort": "distance"},
                                  {"limit": 50, "sort": "dispatch_cost", "fields": "full"}, {"limit": 33, "sort": "product"},
                                  {"product": "Gas", "include_dormant": False, "limit": 50, "sort": "distance"}])
def test_large_route_set_pages_are_capped_complete_and_ordered(h, args):
    data = _many_routes_data(400)
    publish(h, data)
    rows, resps = walk(h, "list_routes", args, "routes")
    for r in resps:
        assert json_size(r) <= cfg.RESPONSE_SIZE_CAP_BYTES
    expected_rows = data["routes_player"]
    if args.get("product") == "Gas":
        expected_rows = [x for x in expected_rows if x["product"] == "Gas" and not x["dormant_auto_warehouse"]]
    sort = args.get("sort", "origin")
    if sort == "origin":
        expected = default_order_keys(expected_rows)
    elif sort == "product":
        expected = [rid(x["route_key"]) for x in sorted(expected_rows, key=lambda x: (f"product:{x['product']}", x["route_key"]))]
    else:
        expected = _sorted_expected(expected_rows, {"distance": "distance_tiles", "dispatch_cost": "dispatch_cost"}[sort])
    assert ids(rows) == expected
    assert resps[0]["page"]["total"] == len(expected)
    if args.get("fields") == "full":
        assert any("truncated" in wcodes(r) for r in resps)


def test_cursor_rules(h):
    publish(h, _many_routes_data(30))
    first = ok(h, "list_routes", {"limit": 5})
    cur = first["page"]["next_cursor"]
    assert cur
    # changed filters / sort -> invalid
    for args in ({"limit": 5, "product": "Gas", "cursor": cur}, {"limit": 5, "sort": "distance", "cursor": cur},
                 {"limit": 5, "errors_only": True, "cursor": cur}, {"limit": 5, "include_dormant": False, "cursor": cur}):
        assert err(h, "list_routes", args)["code"] == "invalid_argument"
    # a cursor of one tool is not valid for another
    assert err(h, "list_vehicles", {"cursor": cur})["code"] == "invalid_argument"
    assert err(h, "list_warehouse_requests", {"cursor": cur})["code"] == "invalid_argument"
    for bad in ("garbage", "djF8LTF8eA", "v1|5|x", "%%%", "A" * 300):
        assert err(h, "list_routes", {"limit": 5, "cursor": bad})["code"] == "invalid_argument"
    # same args -> continues; the cursor survives a server restart (PRD 13.1 identical answers)
    nxt = ok(h, "list_routes", {"limit": 5, "cursor": cur})
    h.restart()
    again = ok(h, "list_routes", {"limit": 5, "cursor": cur})
    assert strip_meta(nxt) == strip_meta(again)
    assert ids(nxt["data"]["routes"])[0] not in ids(first["data"]["routes"])


def test_empty_cursor_string(h):
    # PRD 13.2: a cursor is a page.next_cursor value; an empty string is invalid_argument.
    r = call(h, "list_routes", {"cursor": ""})
    assert r["ok"] is False and r["error"]["code"] == "invalid_argument"


def test_cursor_after_snapshot_shrinks(h):
    publish(h, _many_routes_data(60))
    p1 = ok(h, "list_routes", {"limit": 50})
    cur = p1["page"]["next_cursor"]
    publish(h, bf.state_data())   # now only 13 routes
    # AMBIGUITY: PRD 13.2 does not define cursors across snapshot changes; must not crash
    r = call(h, "list_routes", {"limit": 50, "cursor": cur})
    if r["ok"]:
        assert r["data"]["routes"] == [] and r["page"]["next_cursor"] is None


def test_large_vehicle_and_request_sets(h):
    data = bf.state_data()
    veh = data["vehicles"]
    template = veh["vehicles_player"][0]
    for i in range(300):
        v = copy.deepcopy(template)
        v["instance_id"] = -5000 - i
        v["trip_counter_id"] = 1000 + i
        veh["vehicles_player"].append(v)
    for i in range(120):
        veh["groups"].append({"owner_actor_id": bf.PLAYER, "transport_mode": f"Mode{i:03d}", "product": "Gas", "vehicles": 1,
                              "units_in_transit": i})
    reqs = data["requests_player"]
    template_q = reqs[1]
    for i in range(300):
        q = copy.deepcopy(template_q)
        q["request_key"] = f"{bf.B_WH}|Chemicals|{i + 1}"
        q["occurrence"] = i + 1
        q["priority"] = i % 5
        reqs.append(q)
    publish(h, data)

    vrows, vresps = walk(h, "list_vehicles", {"aggregate": False, "limit": 50}, "vehicles")
    assert len(vrows) == 304 and len({v["id"] for v in vrows}) == 304
    iids = [int(v["id"].rsplit(":", 1)[1]) for v in vrows]
    assert iids == sorted(iids)
    groups, _ = walk(h, "list_vehicles", {"limit": 50}, "groups")
    assert len(groups) == 123
    assert [(g["transport_mode"] or "", g["product"] or "") for g in groups] == sorted(
        (g["transport_mode"] or "", g["product"] or "") for g in groups)
    qrows, qresps = walk(h, "list_warehouse_requests", {"limit": 50}, "requests")
    assert len(qrows) == 302 and len({q["request_id"] for q in qrows}) == 302
    rows = [x for x in reqs]
    expected = [f"request:{q['request_key']}" for q in sorted(rows, key=lambda q: (q["endpoint"], q["priority"], q["request_key"]))]
    assert [q["request_id"] for q in qrows] == expected
    # get_route with hundreds of assigned vehicles stays under the cap and says so (PRD 14.9)
    g = ok(h, "get_route", {"route": ROUTE_1})
    assert json_size(g) <= cfg.RESPONSE_SIZE_CAP_BYTES and "truncated" in wcodes(g)
    assert 0 < len(g["data"]["vehicles_assigned"]) < 301


# ============================================================================ response size cap on the error path

# Regression test for DEFECT LO-3 (fixed).
def test_huge_route_id_error_stays_under_cap(h):
    r = h.call("get_route", {"route": "route:" + "A" * 40000})
    assert r["ok"] is False and r["error"]["code"] in ("not_found", "invalid_argument")
    assert json_size(r) <= cfg.RESPONSE_SIZE_CAP_BYTES  # PRD 14.9: every response <= ~30 KB


# Regression test for DEFECT LO-3 (fixed).
@pytest.mark.parametrize("name,args", [("list_routes", {"origin": "Z" * 40000}),
                                       ("list_vehicles", {"vehicle": f"vehicle:{'Q' * 40000}:1"})])
def test_huge_filter_value_error_stays_under_cap(h, name, args):
    r = h.call(name, args)
    assert r["ok"] is False
    assert json_size(r) <= cfg.RESPONSE_SIZE_CAP_BYTES


# ============================================================================ list_vehicles

def test_aggregate_groups_sorted_and_shaped(h):
    d = ok(h, "list_vehicles")["data"]
    assert [(g["transport_mode"], g["product"]) for g in d["groups"]] == [("Road", None), ("Road", "product:Gas"),
                                                                          ("Road", "product:Paint")]
    assert sum(g["vehicles"] for g in d["groups"]) == 4


def test_aggregate_ai_company(h):
    d = ok(h, "list_vehicles", {"company": "company:2"})["data"]
    assert len(d["groups"]) == 1 and d["groups"][0]["owner"]["actor_id"] == bf.AI_B and d["groups"][0]["vehicles"] == 2
    assert "fleets" not in d  # fleets are player-only (PRD 12.2)


@pytest.mark.parametrize("args,n", [({"product": "Gas"}, 1), ({"product": "gaz"}, 1), ({"transport_mode": "Rail"}, 0),
                                    ({"transport_mode": "Road"}, 3), ({"product": "Water"}, 0),
                                    ({"aggregate": True, "product": "Paint", "transport_mode": "Road"}, 1)])
def test_aggregate_filters(h, args, n):
    assert len(ok(h, "list_vehicles", args)["data"]["groups"]) == n


def test_aggregate_fleet_building_filter(h):
    d = ok(h, "list_vehicles", {"fleet_building": bf.B_TD})["data"]
    assert [f["building"] for f in d["fleets"]] == [f"building:{bf.B_TD}"]
    assert ok(h, "list_vehicles", {"fleet_building": bf.B_PC1})["data"]["fleets"] == []
    # AMBIGUITY: aggregate groups have no fleet dimension (PRD 12.2), so fleet_building does not filter them.


def test_per_vehicle_rows(h):
    d = ok(h, "list_vehicles", {"aggregate": False})["data"]
    assert [v["id"] for v in d["vehicles"]] == [f"vehicle:{WS}:{i}" for i in (-1204, -1203, -1202, -1201)]
    home = d["vehicles"][0]
    assert home["going_home"] is True and home["cargo"]["product"] is None and home["origin"] is None
    v = d["vehicles"][-1]
    assert v["origin"] == f"building:{bf.B_GW1}" and v["destination"] == f"building:{bf.B_PC1}" and v["prefab"] == "Vehicle"
    assert v["fleet_building"] == f"building:{bf.B_TD}" and "tile_x" in v["position"]
    assert "groups" not in d and "session" in d["identity_note"]


@pytest.mark.parametrize("args,expect", [({"product": "Paint"}, [-1203]), ({"fleet_building": bf.B_TD}, [-1204, -1203, -1202, -1201]),
                                         ({"fleet_building": bf.B_PC1}, []), ({"transport_mode": "Rail"}, []),
                                         ({"product": "Gas", "transport_mode": "Road"}, [-1202, -1201])])
def test_per_vehicle_filters(h, args, expect):
    d = ok(h, "list_vehicles", {"aggregate": False, **args})["data"]
    assert [v["id"] for v in d["vehicles"]] == [f"vehicle:{WS}:{i}" for i in expect]


def test_per_vehicle_rows_for_ai_are_unavailable(h):
    e = err(h, "list_vehicles", {"aggregate": False, "company": "company:2"})
    assert e["code"] == "section_unavailable"


def test_single_vehicle_same_session(h):
    d = ok(h, "list_vehicles", {"vehicle": f"vehicle:{WS}:-1202"})["data"]
    assert [v["id"] for v in d["vehicles"]] == [f"vehicle:{WS}:-1202"]
    # PRD 14.4: `vehicle` implies aggregate=false, also when aggregate=true is passed
    d2 = ok(h, "list_vehicles", {"vehicle": f"vehicle:{WS}:-1202", "aggregate": True})["data"]
    assert d2["vehicles"] == d["vehicles"] and "groups" not in d2


@pytest.mark.parametrize("aggregate", [None, True, False])
def test_vehicle_from_another_world_session_is_stale_reference(h, aggregate):
    # PRD 7.4 / 13.3: session-scoped ids from another world session -> stale_reference
    args = {"vehicle": f"vehicle:{WS2}:-1201"}
    if aggregate is not None:
        args["aggregate"] = aggregate
    assert err(h, "list_vehicles", args)["code"] == "stale_reference"


def test_vehicle_unknown_in_session_is_not_found(h):
    assert err(h, "list_vehicles", {"vehicle": f"vehicle:{WS}:99999"})["code"] == "not_found"


@pytest.mark.parametrize("vehicle", ["-1201", f"building:{bf.B_TD}", f"vehicle:{WS}:abc", f"vehicle:{WS}:-1201:9",
                                     f"VEHICLE:{WS}:-1201", "vehicle:abc"])
def test_malformed_vehicle_ids(h, vehicle):
    assert err(h, "list_vehicles", {"vehicle": vehicle})["code"] == "invalid_argument"


@pytest.mark.parametrize("vehicle", [f"vehicle:{WS}:", "vehicle::-1201", ""])
def test_degenerate_vehicle_ids(h, vehicle):
    # PRD 14.4: malformed (empty session or instance part, or blank) -> invalid_argument, checked before the session.
    assert err(h, "list_vehicles", {"vehicle": vehicle})["code"] == "invalid_argument"


def test_vehicle_ids_in_other_parameters(h):
    # PRD 7.4: an id from another world session is rejected with stale_reference wherever it is passed
    assert err(h, "list_routes", {"origin": f"vehicle:{WS2}:-1201"})["code"] == "stale_reference"
    assert err(h, "list_vehicles", {"fleet_building": f"vehicle:{WS2}:-1201"})["code"] == "stale_reference"
    assert err(h, "list_warehouse_requests", {"endpoint": f"vehicle:{WS2}:-1201"})["code"] == "stale_reference"
    assert err(h, "list_routes", {"origin": f"vehicle:{WS}:-1201"})["code"] == "invalid_argument"


def test_vehicle_ids_after_new_world_session(tmp_path, clock, procs):
    hh, obs = _observer_harness(tmp_path, clock, procs, serve=False)
    assert ok(hh, "list_vehicles", {"vehicle": f"vehicle:{WS}:-1201"})["data"]["vehicles"]
    obs.ready(world_session=WS2)  # quickload: new world_session, new snapshot
    assert err(hh, "list_vehicles", {"vehicle": f"vehicle:{WS}:-1201"})["code"] == "stale_reference"
    d = ok(hh, "list_vehicles", {"aggregate": False})["data"]
    assert all(v["id"].startswith(f"vehicle:{WS2}:") for v in d["vehicles"])
    g = ok(hh, "get_route", {"route": ROUTE_1})["data"]
    assert all(v["id"].startswith(f"vehicle:{WS2}:") for v in g["vehicles_assigned"])


def test_empty_fleet(h):
    data = bf.state_data()
    data["vehicles"] = {"groups": [], "fleets_player": [], "vehicles_player": [], "total_active": 0,
                        "identity": "session_pooled_object"}
    publish(h, data)
    a = ok(h, "list_vehicles")
    assert a["data"]["groups"] == [] and a["data"]["fleets"] == [] and a["page"]["total"] == 0
    p = ok(h, "list_vehicles", {"aggregate": False})
    assert p["data"]["vehicles"] == [] and p["page"]["next_cursor"] is None
    assert err(h, "list_vehicles", {"vehicle": f"vehicle:{WS}:-1201"})["code"] == "not_found"
    assert ok(h, "get_route", {"route": ROUTE_1})["data"]["vehicles_assigned"] == []


def test_vehicle_stale_session_with_allow_stale(h):
    # AMBIGUITY (recorded): with allow_stale the previous session's snapshot is served; PRD 7.4 does not
    # say whether its own vehicle ids are then accepted. Invariants only.
    obs = FakeObserver(h.exchange, h.clock, h.procs)
    obs.world_session = WS2
    obs.heartbeat("ready")
    for vid in (f"vehicle:{WS}:-1201", f"vehicle:{WS2}:-1201"):
        r = call(h, "list_vehicles", {"vehicle": vid, "allow_stale": True})
        if r["ok"]:
            assert r["meta"]["stale"] is True and r["meta"]["stale_reason"] == "world_session_changed"
        else:
            assert r["error"]["code"] in ("stale_reference", "not_found")


# ============================================================================ list_warehouse_requests

@pytest.mark.parametrize("args,n", [({"product": "Paint"}, 1), ({"product": "Peinture"}, 1), ({"endpoint": bf.B_WH}, 2),
                                    ({"endpoint": "ENTREPÔT 1"}, 2), ({"endpoint": bf.B_PC1}, 0),
                                    ({"endpoint": bf.B_WH, "product": "Chemicals"}, 1), ({"product": "Gas"}, 0),
                                    ({"company": "player"}, 2), ({"company": "company:1"}, 2)])
def test_request_filters(h, args, n):
    r = ok(h, "list_warehouse_requests", args)
    assert len(r["data"]["requests"]) == n and r["page"]["total"] == n


@pytest.mark.parametrize("args,code", [({"endpoint": "building:Nowhere@1,1"}, "not_found"), ({"product": "Nope"}, "not_found"),
                                       ({"company": "company:2"}, "section_unavailable"), ({"company": "company:99"}, "not_found")])
def test_request_filter_errors(h, args, code):
    assert err(h, "list_warehouse_requests", args)["code"] == code


def test_requests_order_and_edge_values(h):
    data = bf.state_data()
    reqs = data["requests_player"]
    zero = copy.deepcopy(reqs[1])
    zero.update({"request_key": f"{bf.B_WH}|Paint|1", "product": "Paint", "occurrence": 1, "requested_amount": 0,
                 "remaining": 0, "active": False, "priority": 1})
    maxv = copy.deepcopy(reqs[0])
    maxv.update({"request_key": f"{bf.B_PF1}|Chemicals|0", "endpoint": bf.B_PF1, "product": "Chemicals",
                 "requested_amount": bf.INT_MAX, "fill": True, "priority": 0})
    noprod = copy.deepcopy(reqs[1])
    noprod.update({"request_key": f"{bf.B_WH}|None|0", "product": None, "priority": 9})
    reqs.extend([zero, maxv, noprod])
    data["session"]["logistic_requests_enabled"] = False
    publish(h, data)
    d = ok(h, "list_warehouse_requests")["data"]
    rows = {q["request_id"]: q for q in d["requests"]}
    z = rows[f"request:{bf.B_WH}|Paint|1"]
    assert z["requested_amount"] == 0 and z["active"] is False   # zero is not "fill"; inactive rows are listed
    assert rows[f"request:{bf.B_PF1}|Chemicals|0"]["requested_amount"] == "fill"  # PRD 12.3.4 int.MaxValue -> fill
    assert rows[f"request:{bf.B_WH}|None|0"]["product"] is None
    assert d["logistic_requests_enabled"] is False
    # deterministic ordering (endpoint, priority, key); ties on priority broken by the key
    order = [q["request_id"] for q in d["requests"]]
    exp = [f"request:{q['request_key']}" for q in sorted(reqs, key=lambda q: (q["endpoint"], q["priority"], q["request_key"]))]
    assert order == exp
    h.restart()
    assert [q["request_id"] for q in ok(h, "list_warehouse_requests")["data"]["requests"]] == order


def test_requests_empty(h):
    data = bf.state_data()
    data["requests_player"] = []
    publish(h, data)
    r = ok(h, "list_warehouse_requests")
    assert r["data"]["requests"] == [] and r["page"]["total"] == 0 and r["page"]["next_cursor"] is None


# ============================================================================ fields on every list tool (PRD 13.2)

# Regression test for DEFECT LO-2 (fixed).
@pytest.mark.parametrize("name", ["list_vehicles", "list_warehouse_requests"])
@pytest.mark.parametrize("fields", ["compact", "full"])
def test_list_tools_accept_fields(h, name, fields):
    r = call(h, name, {"fields": fields})
    assert r["ok"], r.get("error")


# ============================================================================ lifecycle, stale, fresh

@pytest.mark.parametrize("name", LOGI)
def test_game_not_running_and_allow_stale(h, name):
    h.procs.procs = []
    assert err(h, name, BASE_ARGS[name])["code"] == "game_not_running"
    r = ok(h, name, {**BASE_ARGS[name], "allow_stale": True})
    assert r["meta"]["source"] == "stale_snapshot" and r["meta"]["stale"] is True
    assert r["meta"]["stale_reason"] == "game_not_running"


@pytest.mark.parametrize("name", LOGI)
@pytest.mark.parametrize("state,code", [("menu", "at_main_menu"), ("loading", "loading"), ("disabled", "observer_disabled"),
                                        ("faulted", "observer_faulted")])
def test_lifecycle_states(h, name, state, code):
    obs = FakeObserver(h.exchange, h.clock, h.procs)
    obs.heartbeat(state)
    assert err(h, name, BASE_ARGS[name])["code"] == code
    r = call(h, name, {**BASE_ARGS[name], "allow_stale": True})
    if r["ok"]:  # PRD 13.4 / 16: old snapshot only via allow_stale, flagged stale
        assert r["meta"]["stale"] is True and r["meta"]["source"] == "stale_snapshot"


@pytest.mark.parametrize("name", LOGI)
def test_stale_by_age_is_flagged(h, name):
    h.clock.advance(20)  # > max(15 s, 3 x 5 s) since capture (PRD 11.7)
    w = h.world
    h.write("heartbeat", bf.build_heartbeat(h.clock.now(), static_doc=w["static"], state_doc=w["state"], history_doc=w["history"]))
    r = ok(h, name, BASE_ARGS[name])
    assert r["meta"]["stale"] is True and r["meta"]["stale_reason"] == "age" and "stale" in wcodes(r)


@pytest.mark.parametrize("name", LOGI)
def test_world_session_changed(h, name):
    obs = FakeObserver(h.exchange, h.clock, h.procs)
    obs.world_session = WS2
    obs.heartbeat("ready")
    assert err(h, name, BASE_ARGS[name])["code"] == "snapshot_unavailable"  # PRD 16 quickload row
    r = ok(h, name, {**BASE_ARGS[name], "allow_stale": True})
    assert r["meta"]["stale"] is True and r["meta"]["stale_reason"] == "world_session_changed"


@pytest.mark.parametrize("name", LOGI)
def test_fresh_writes_exactly_state(tmp_path, clock, procs, name):
    hh, obs = _observer_harness(tmp_path, clock, procs)
    r = ok(hh, name, {**BASE_ARGS[name], "fresh": True})
    req = obs.read_refresh_request()
    assert {k for k, v in req["requests"].items() if v is not None} == {"state"}  # PRD 13.7
    assert r["meta"]["snapshot"]["seq"] == obs.state_seq and "refresh_timeout" not in wcodes(r)


@pytest.mark.parametrize("name", LOGI)
def test_fresh_timeout_and_not_live(tmp_path, clock, procs, name):
    hh, obs = _observer_harness(tmp_path, clock, procs, wait=0.5, serve=False)
    r = ok(hh, name, {**BASE_ARGS[name], "fresh": True})
    assert "refresh_timeout" in wcodes(r)
    obs.menu()
    (tmp_path / "x" / "refresh-request.json").unlink()
    assert err(hh, name, {**BASE_ARGS[name], "fresh": True})["code"] == "at_main_menu"
    assert obs.read_refresh_request() is None


@pytest.mark.parametrize("name", LOGI)
def test_no_files_at_all(empty_h, name):
    r = call(empty_h, name, BASE_ARGS[name])
    assert r["ok"] is False and r["meta"]["source"] == "none"
