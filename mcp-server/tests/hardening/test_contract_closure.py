"""Contract-closure tests: the decisions that closed the last PRD ambiguities after the 29-tool hardening.

D1  list_vehicles `vehicle` lookup (PRD 14.4, A15)
D2  find_shops existing_route_status (PRD 14.7, D-ROUTE-2)
D3  meta.source precedence and meta.snapshots (PRD 13.3, 13.4)
D4  paging / sort / fields exactly where PRD 14 lists them (PRD 13.2); registry check in test_infrastructure
D5  ids are case-sensitive (PRD 7.1, 13.6)
D6  empty or whitespace-only text arguments are invalid_argument (PRD 13.2)
D7  search without usable live state (PRD 13.4, 14.1)

Every response is validated with check_response (tool response schema + 30 KB cap).
"""

from __future__ import annotations

import copy
import json

import pytest
from jsonschema import Draft202012Validator

import build_fixtures as bf
from test_tools_contract import PRD_TOOLS, TOOL_SCHEMA_DIR, VALID_CALLS, check_response


def call(h, name, args=None):
    resp = h.call(name, args or {})
    check_response(name, resp)
    return resp


def ok(h, name, args=None):
    resp = call(h, name, args)
    assert resp["ok"], resp.get("error")
    return resp


def err(h, name, args, code):
    resp = call(h, name, args)
    assert resp["ok"] is False, resp.get("data")
    assert resp["error"]["code"] == code, resp["error"]
    return resp


def unavailable(resp) -> dict:
    return {u["field"]: u["reason"] for u in resp["data"].get("unavailable") or []}


def warning_codes(resp) -> list:
    return [w["code"] for w in resp["meta"]["warnings"]]


def families(resp) -> list:
    return [s["family"] for s in resp["meta"]["snapshots"]]


def republish_state(h, w, failed=()):
    st = w["state"]
    st["seq"] += 1
    for s in failed:
        st["data"][s] = None
    st["content_hash"] = bf.content_hash(st["data"])
    st["sections"] = bf.state_sections(st["data"])
    for s in failed:
        st["sections"][s] = bf.section_status(1512, 0, "failed", "exception")
    w["heartbeat"] = bf.build_heartbeat(h.clock.now(), static_doc=w["static"], state_doc=st, history_doc=w["history"])
    h.write_world(w, ("state", "heartbeat"))


# ============================================================================ D1 list_vehicles.vehicle

VID = f"vehicle:{bf.WORLD_SESSION}:-1203"


def test_vehicle_lookup_returns_that_vehicle_only(h):
    r = ok(h, "list_vehicles", {"vehicle": VID})
    rows = r["data"]["vehicles"]
    assert [v["id"] for v in rows] == [VID]
    assert "groups" not in r["data"]  # implies aggregate=false
    assert ok(h, "list_vehicles", {"vehicle": VID, "aggregate": True})["data"]["vehicles"] == rows


def test_vehicle_lookup_other_world_session_is_stale_reference(h):
    """A15: a vehicle id kept across a quickload is rejected, never matched to the pooled object with the same instance id."""
    err(h, "list_vehicles", {"vehicle": "vehicle:00000000-0000-4000-8000-00000000dead:-1203"}, "stale_reference")


@pytest.mark.parametrize("value", ["vehicle", "vehicle:-1203", f"vehicle:{bf.WORLD_SESSION}:abc",
                                   f"VEHICLE:{bf.WORLD_SESSION}:-1203", f"Vehicle:{bf.WORLD_SESSION}:-1203",
                                   f"building:{bf.B_PF1}", f"vehicle:{bf.WORLD_SESSION}:-1203:1"])
def test_vehicle_lookup_malformed_is_invalid_argument(h, value):
    err(h, "list_vehicles", {"vehicle": value}, "invalid_argument")


def test_vehicle_lookup_inactive_is_not_found(h):
    err(h, "list_vehicles", {"vehicle": f"vehicle:{bf.WORLD_SESSION}:-9999"}, "not_found")


def test_vehicle_lookup_is_player_only(h):
    err(h, "list_vehicles", {"vehicle": VID, "company": f"company:{bf.AI_B}"}, "section_unavailable")


def test_vehicle_lookup_other_filters_still_apply(h):
    assert ok(h, "list_vehicles", {"vehicle": VID, "product": "Gas"})["data"]["vehicles"] == []
    assert len(ok(h, "list_vehicles", {"vehicle": VID, "product": "Paint"})["data"]["vehicles"]) == 1


# ============================================================================ D2 find_shops existing_route_status

def _rows(r):
    return {row["shop"]: row for row in r["data"]["shops"]}


def test_route_status_present_and_absent_from_player_building(h):
    r = ok(h, "find_shops", {"product": "Paint", "from_building": bf.B_PF1})
    rows = _rows(r)
    for key in (bf.S_HW1, bf.S_CS1):
        row = rows[f"building:{key}"]
        assert row["existing_route_status"] == "present"
        assert row["existing_route"]["authoritative"] is True and row["straight_line_cost_estimate"] is None
    for key in (bf.S_HW2, bf.S_GS):
        row = rows[f"building:{key}"]
        assert row["existing_route_status"] == "absent" and row["existing_route"] is None
        est = row["straight_line_cost_estimate"]
        assert est["route_exists"] is False and est["authoritative"] is False
    assert "existing_route" not in unavailable(r)


def test_route_status_null_without_from_building(h):
    for row in ok(h, "find_shops", {"product": "Paint"})["data"]["shops"]:
        assert row["existing_route_status"] is None and row["existing_route"] is None
        assert row["straight_line_cost_estimate"] is None


@pytest.mark.parametrize("origin", [bf.AI_PF, "TradingPost@5,5"])
def test_route_status_unavailable_from_ai_or_state_origin_without_routes_ai(h, origin):
    """routes_ai is off by default: existence of a route from an AI/State building is unknown, never 'absent'."""
    r = ok(h, "find_shops", {"product": "Paint", "from_building": origin})
    for row in r["data"]["shops"]:
        assert row["existing_route_status"] == "unavailable" and row["existing_route"] is None
        est = row["straight_line_cost_estimate"]
        assert est["route_exists"] is None and est["authoritative"] is False and est["value"] is not None
    assert "routes_ai" in unavailable(r)["existing_route"]


def test_route_status_unavailable_when_routes_player_failed(h):
    w = copy.deepcopy(h.world)
    republish_state(h, w, failed=("routes_player",))
    r = ok(h, "find_shops", {"product": "Paint", "from_building": bf.B_PF1})
    for row in r["data"]["shops"]:
        assert row["existing_route_status"] == "unavailable" and row["existing_route"] is None
        assert row["straight_line_cost_estimate"]["route_exists"] is None
    assert "routes_player" in unavailable(r)["existing_route"]
    assert "routes_player" in r["meta"]["snapshot"]["sections_unavailable"]


def test_route_status_known_from_ai_origin_when_routes_ai_exported(h):
    w = copy.deepcopy(h.world)
    data = w["state"]["data"]
    template = next(x for x in data["routes_player"] if x["origin"] == bf.B_PF1 and x["destination"] == bf.S_HW1)
    ai_route = copy.deepcopy(template)
    ai_route.update({"origin": bf.AI_PF, "destination": bf.S_HW2, "endpoint": bf.S_HW2,
                     "route_key": f"{bf.AI_PF}|Paint|{bf.S_HW2}|own|0"})
    data["routes_ai"] = [ai_route]
    republish_state(h, w)
    r = ok(h, "find_shops", {"product": "Paint", "from_building": bf.AI_PF})
    rows = _rows(r)
    assert rows[f"building:{bf.S_HW2}"]["existing_route_status"] == "present"
    assert rows[f"building:{bf.S_HW2}"]["straight_line_cost_estimate"] is None
    for key in (bf.S_HW1, bf.S_CS1, bf.S_GS):
        assert rows[f"building:{key}"]["existing_route_status"] == "absent"
        assert rows[f"building:{key}"]["straight_line_cost_estimate"]["route_exists"] is False
    assert "existing_route" not in unavailable(r)


@pytest.mark.parametrize("status,route,estimate_exists,valid", [
    ("present", {"route_id": "route:x", "distance_tiles": 1, "dispatch_cost": 1.0}, "none", True),
    ("present", {"route_id": "route:x", "distance_tiles": 1, "dispatch_cost": 1.0}, False, False),
    ("absent", None, False, True),
    ("absent", None, None, False),
    ("absent", {"route_id": "route:x", "distance_tiles": 1, "dispatch_cost": 1.0}, "none", False),
    ("unavailable", None, None, True),
    ("unavailable", None, "none", True),
    ("unavailable", None, False, False),
    (None, None, "none", True),
    (None, None, False, False),
])
def test_route_status_is_machine_checked_by_the_response_schema(h, status, route, estimate_exists, valid):
    resp = ok(h, "find_shops", {"product": "Paint", "from_building": bf.B_PF1})
    row = copy.deepcopy(resp["data"]["shops"][0])
    row["existing_route_status"] = status
    row["existing_route"] = route
    row["straight_line_cost_estimate"] = None if estimate_exists == "none" else {
        "value": 1.0, "distance_kind": "straight_line", "route_exists": estimate_exists, "authoritative": False,
        "formula": "ManualDestinationDispatchCost", "method": "D-ROUTE-2"}
    resp["data"]["shops"] = [row]
    schema = json.loads((TOOL_SCHEMA_DIR / "find_shops.schema.json").read_text(encoding="utf-8"))
    assert Draft202012Validator(schema).is_valid(resp) is valid


# ============================================================================ D3 meta.source

def test_source_static_catalog_for_pure_catalogue(h):
    r = ok(h, "get_recipe", {"recipe": "Chemicals"})
    assert r["meta"]["source"] == "static_catalog" and families(r) == ["static"]


def test_source_live_snapshot_for_mixed_answer_lists_every_family(h):
    r = ok(h, "get_product", {"product": "Paint"})
    assert r["meta"]["source"] == "live_snapshot" and families(r) == ["state", "static"]
    assert r["meta"]["snapshot"] == r["meta"]["snapshots"][0]


def test_source_live_snapshot_for_history(h):
    r = ok(h, "get_finances", {})
    assert r["meta"]["source"] == "live_snapshot" and "history" in families(r)


def test_source_static_catalog_when_not_live(h):
    h.procs.procs = []
    r = ok(h, "get_product", {"product": "Paint"})
    assert r["meta"]["source"] == "static_catalog" and families(r) == ["static"]
    assert "catalog_from_previous_session" in warning_codes(r)
    assert unavailable(r)["live:state"].startswith("game_not_running")


def test_source_stale_snapshot_when_stale_by_age(h):
    h.clock.advance(60)
    w = h.world
    h.write("heartbeat", bf.build_heartbeat(h.clock.now(), static_doc=w["static"], state_doc=w["state"],
                                            history_doc=w["history"]))
    r = ok(h, "get_product", {"product": "Paint"})
    assert r["meta"]["source"] == "stale_snapshot" and r["meta"]["stale_reason"] == "age"
    assert families(r) == ["state", "static"]


def test_source_none_for_status(h):
    assert ok(h, "get_game_status")["meta"]["source"] == "none"


def test_source_none_on_error_before_data(h):
    r = call(h, "get_building", {"building": "  "})
    assert r["ok"] is False and r["meta"]["source"] == "none" and r["meta"]["snapshots"] == []


# ============================================================================ D4 fields without effect

@pytest.mark.parametrize("name,args,key", [("list_warehouse_requests", {}, "requests"),
                                           ("list_vehicles", {"aggregate": False}, "vehicles"),
                                           ("list_vehicles", {}, "groups")])
def test_fields_compact_and_full_return_the_same_rows(h, name, args, key):
    compact = ok(h, name, {**args, "fields": "compact"})["data"]
    full = ok(h, name, {**args, "fields": "full"})["data"]
    assert compact[key] and compact == full == ok(h, name, args)["data"]


# ============================================================================ D5 ids are case-sensitive

@pytest.mark.parametrize("name,param,value", [
    ("get_building", "building", f"building:{bf.B_PF1}"),
    ("get_building", "building", bf.B_PF1),
    ("get_product", "product", "product:Paint"),
    ("get_city", "city", f"city:{bf.CITY_VAL}"),
    ("get_company", "company", f"company:{bf.AI_B}"),
    ("get_route", "route", f"route:{bf.B_PF1}|Paint|{bf.S_HW1}|own|0"),
])
def test_exact_ids_resolve(h, name, param, value):
    ok(h, name, {param: value})


@pytest.mark.parametrize("name,param,value", [
    ("get_building", "building", f"BUILDING:{bf.B_PF1}"),
    ("get_building", "building", f"Building:{bf.B_PF1}"),
    ("get_building", "building", f"building:{bf.B_PF1.lower()}"),
    ("get_building", "building", f"building:{bf.B_PF1.upper()}"),
    ("get_building", "building", bf.B_PF1.lower()),
    ("get_product", "product", "PRODUCT:Paint"),
    ("get_city", "city", f"CITY:{bf.CITY_VAL}"),
    ("get_company", "company", f"COMPANY:{bf.AI_B}"),
    ("get_route", "route", f"route:{bf.B_PF1.lower()}|Paint|{bf.S_HW1}|own|0"),
])
def test_re_cased_ids_are_not_found(h, name, param, value):
    r = err(h, name, {param: value}, "not_found")
    assert "case-sensitive" in (r["error"]["message"] + (r["error"]["hint"] or ""))


def test_re_cased_route_prefix_is_not_a_route_id(h):
    """get_route accepts route ids only (no names): a re-cased prefix is not a route id."""
    err(h, "get_route", {"route": f"ROUTE:{bf.B_PF1}|Paint|{bf.S_HW1}|own|0"}, "invalid_argument")


@pytest.mark.parametrize("value", ["Paint", "paint", "PAINT", "  Paint  "])
def test_names_stay_case_insensitive(h, value):
    """Asset names (and display/English names) keep the PRD 13.6 name rules; `product:Paint` is the id."""
    assert ok(h, "get_product", {"product": value})["data"]["definition"]["id"] == "product:Paint"


def test_search_still_matches_ids_as_text(h):
    r = ok(h, "search", {"query": f"BUILDING:{bf.B_PF1}"})
    assert r["data"]["results"][0]["id"] == f"building:{bf.B_PF1}"


# ============================================================================ D6 blank text arguments

def _text_params():
    from roi_mcp.tools import build_specs
    out = []
    for spec in build_specs():
        if spec.name not in VALID_CALLS:
            continue   # V1 tools only; advisor blank-argument tests live in tests/advisor
        for p, ps in spec.input_schema()["properties"].items():
            if ps.get("type") == "string" and "enum" not in ps:
                out.append((spec.name, p, False))
            elif ps.get("type") == "array" and ps["items"].get("type") == "string" and "enum" not in ps["items"]:
                out.append((spec.name, p, True))
    return out


TEXT_PARAMS = _text_params()


def test_text_parameter_inventory():
    names = {(n, p) for n, p, _ in TEXT_PARAMS}
    assert ("search", "query") in names and ("list_vehicles", "vehicle") in names and ("get_market", "products") in names
    assert all((n, "cursor") in names for n in ("search", "find_shops", "get_tech_tree", "list_companies"))


@pytest.mark.parametrize("blank", ["", " ", "\t", "\n  \r"])
@pytest.mark.parametrize("name,param,is_array", TEXT_PARAMS)
def test_blank_text_arguments_are_invalid(h, name, param, is_array, blank):
    args = {**VALID_CALLS[name], param: [blank] if is_array else blank}
    r = err(h, name, args, "invalid_argument")
    assert "empty or whitespace-only" in r["error"]["message"]
    assert not (h.exchange / "refresh-request.json").exists()


@pytest.mark.parametrize("name", PRD_TOOLS)
def test_omitted_optional_parameters_mean_no_filter(h, name):
    ok(h, name, VALID_CALLS[name])


# ============================================================================ D7 search without usable live state

def _invalid_state(h):
    doc = copy.deepcopy(h.world["state"])
    doc["seq"] += 1
    del doc["data"]["session"]             # schema-invalid (required section key missing)
    doc["content_hash"] = bf.content_hash(doc["data"])
    return doc


def _assert_static_only(r, reason_prefix):
    assert r["meta"]["source"] == "static_catalog" and families(r) == ["static"]
    un = unavailable(r)
    assert un["live:state"].startswith(reason_prefix), un
    assert "live_entities" in un
    kinds = {x["kind"] for x in r["data"]["results"]}
    assert kinds and kinds <= {"product", "recipe", "building_type", "tech"}
    for x in r["data"]["results"]:
        assert x["owner"] is None and x["city"] is None


def test_search_with_schema_invalid_state_and_no_previous_snapshot(h):
    h.write_raw("state", json.dumps(_invalid_state(h)))
    h.restart()                            # no valid state snapshot was ever loaded by this server
    r = ok(h, "search", {"query": "paint"})
    _assert_static_only(r, "snapshot_unavailable")
    assert "invalid" in unavailable(r)["live:state"]


def test_search_with_major_mismatched_state(h):
    doc = copy.deepcopy(h.world["state"])
    doc["schema_version"] = "2.0.0"
    h.write_raw("state", json.dumps(doc))
    h.restart()
    r = ok(h, "search", {"query": "paint"})
    _assert_static_only(r, "schema_mismatch")


def test_search_with_schema_invalid_state_and_owner_filter(h):
    h.write_raw("state", json.dumps(_invalid_state(h)))
    h.restart()
    r = ok(h, "search", {"query": "paint", "owner": "player"})
    assert r["data"]["results"] == [] and "owner_filter" in unavailable(r)
    assert "live:state" in unavailable(r)


def test_search_with_schema_invalid_state_uses_the_previous_valid_snapshot(h):
    assert ok(h, "search", {"query": "Valmont"})["meta"]["source"] == "live_snapshot"
    h.write_raw("state", json.dumps(_invalid_state(h)))
    r = ok(h, "search", {"query": "Valmont"})
    assert r["meta"]["source"] == "live_snapshot" and r["meta"]["snapshot"]["seq"] == h.world["state"]["seq"]
    assert "snapshot_invalid_using_previous" in warning_codes(r)
    assert f"city:{bf.CITY_VAL}" in [x["id"] for x in r["data"]["results"]]
