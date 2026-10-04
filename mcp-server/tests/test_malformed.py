"""T-8: truncated JSON, wrong schema, empty files, old world_session, dead PID, old heartbeat,
missing static, static_ref mismatch. The server never crashes and always answers with the envelope."""

import copy
import json
from datetime import timedelta

import pytest

import build_fixtures as bf
from test_tools_contract import PRD_TOOLS, VALID_CALLS, check_response

GARBAGE = {
    "truncated": lambda doc: json.dumps(doc)[: len(json.dumps(doc)) // 2],
    "empty": lambda doc: "",
    "whitespace": lambda doc: "   \n",
    "not_object": lambda doc: "[1, 2, 3]",
    "null": lambda doc: "null",
    "binary": lambda doc: "\x00\x01\x02garbage",
    "wrong_schema_name": lambda doc: json.dumps({**doc, "schema": "roi-mcp/other"}),
    "missing_data": lambda doc: json.dumps({k: v for k, v in doc.items() if k != "data"}),
    "bad_version": lambda doc: json.dumps({**doc, "schema_version": "one"}),
}


@pytest.mark.parametrize("kind", sorted(GARBAGE))
@pytest.mark.parametrize("family", ["state", "history", "static"])
def test_garbage_without_previous_snapshot(empty_h, world, kind, family):
    h = empty_h
    h.write_world(world)
    h.write_raw(family, GARBAGE[kind](world[family]))
    for name in PRD_TOOLS:
        r = h.call(name, VALID_CALLS[name])
        check_response(name, r)
        if not r["ok"]:
            assert r["error"]["code"] in ("snapshot_unavailable", "section_unavailable", "not_found", "invalid_argument"), (name, r["error"])


@pytest.mark.parametrize("kind", sorted(GARBAGE))
def test_garbage_heartbeat_never_crashes(h, kind):
    h.write_raw("heartbeat", GARBAGE[kind](h.world["heartbeat"]))
    h.restart()
    for name in PRD_TOOLS:
        r = h.call(name, VALID_CALLS[name])
        check_response(name, r)
    st = h.call("get_game_status")
    assert st["ok"] and st["data"]["game"]["state"] in ("observer_not_detected", "starting")


@pytest.mark.parametrize("kind", ["truncated", "empty", "wrong_schema_name"])
def test_garbage_with_previous_snapshot_uses_last_good(h, kind):
    assert h.call("list_routes")["ok"]
    h.write_raw("state", GARBAGE[kind](h.world["state"]))
    r = h.call("list_routes")
    assert r["ok"]
    w = [x for x in r["meta"]["warnings"] if x["code"] == "snapshot_invalid_using_previous"]
    assert w and "state.json" in w[0]["detail"]


def test_old_world_session_snapshot(h):
    hb = bf.build_heartbeat(h.clock.now(), world_session=bf.WORLD_SESSION_2, static_doc=h.world["static"])
    h.write_raw("heartbeat", json.dumps(hb))
    r = h.call("get_building", {"building": bf.B_PC1})
    assert r["error"]["code"] == "snapshot_unavailable"
    r2 = h.call("get_building", {"building": bf.B_PC1, "allow_stale": True})
    assert r2["ok"] and r2["meta"]["stale"] and r2["meta"]["stale_reason"] == "world_session_changed"
    assert r2["meta"]["source"] == "stale_snapshot"


def test_dead_pid(h):
    h.procs.procs = [__import__("conftest").game_proc(pid=31337, start=h.clock.now() - timedelta(minutes=10))]
    r = h.call("list_routes")
    assert r["error"]["code"] == "observer_not_detected"
    h.procs.procs = []
    assert h.call("list_routes")["error"]["code"] == "game_not_running"


def test_old_heartbeat(h):
    h.clock.advance(6)
    r = h.call("list_routes")
    assert r["error"]["code"] == "observer_unresponsive"
    r2 = h.call("list_routes", {"allow_stale": True})
    assert r2["ok"] and r2["meta"]["stale_reason"] == "observer_unresponsive"


def test_missing_static(empty_h, world):
    h = empty_h
    h.write_world(world, ("heartbeat", "state", "history"))
    r = h.call("list_products")
    assert r["error"]["code"] == "snapshot_unavailable"
    assert h.call("get_recipe", {"recipe": "Chemicals"})["error"]["code"] == "snapshot_unavailable"
    # runtime tools still answer; definition-dependent parts are listed as unavailable
    r2 = h.call("get_building", {"building": bf.B_PC1})
    assert r2["ok"] and any(u["field"] == "definition" for u in r2["data"]["unavailable"])
    r3 = h.call("list_routes")
    assert r3["ok"] and "static_mismatch" in [w["code"] for w in r3["meta"]["warnings"]]


def test_static_ref_mismatch(h):
    st = copy.deepcopy(h.world["history"])
    st["static_ref"] = {"seq": 7, "content_hash": "x"}
    st["seq"] = 9
    h.write_raw("history", json.dumps(st))
    r = h.call("get_finances")
    assert r["ok"] and "static_mismatch" in [w["code"] for w in r["meta"]["warnings"]]


def test_section_failed_only_affects_its_tools(h):
    st = copy.deepcopy(h.world["state"])
    st["data"]["routes_player"] = None
    st["sections"]["routes_player"] = bf.section_status(1512, 0, "failed", "exception NullReference")
    st["seq"] = 40
    h.write_raw("state", json.dumps(st))
    r = h.call("list_routes")
    assert r["error"]["code"] == "section_unavailable" and "routes_player" in r["error"]["message"]
    assert h.call("list_cities")["ok"]
    b = h.call("get_building", {"building": bf.B_PC1})
    assert b["ok"]


def test_huge_and_odd_arguments(h):
    assert h.call("search", {"query": "x" * 5000})["error"]["code"] == "invalid_argument"
    assert h.call("get_building", {"building": "\x00‮"})["error"]["code"] in ("not_found", "invalid_argument")
    assert h.call("list_routes", None)["ok"]
    assert asyncio_call(h, "list_routes", "notadict")["error"]["code"] == "invalid_argument"
    assert h.call("no_such_tool")["error"]["code"] == "invalid_argument"


def asyncio_call(h, name, args):
    import asyncio
    return asyncio.run(h.app.call(name, args))
