"""T-9: a scripted fake observer drives the server through every row of PRD section 16."""

import json
from datetime import timedelta

import pytest

import build_fixtures as bf
from conftest import FakeProcs, Harness, game_proc
from fake_observer import FakeObserver
from test_tools_contract import PRD_TOOLS, VALID_CALLS, check_response

RUNTIME_PROBE = ("get_building", {"building": f"building:{bf.B_PC1}"})
STATIC_PROBE = ("list_products", {})


@pytest.fixture
def env(tmp_path, clock):
    d = tmp_path / "RoiMcp"
    d.mkdir()
    procs = FakeProcs([])
    hh = Harness(d, clock, procs)
    obs = FakeObserver(d, clock, procs)
    return hh, obs


def warn_codes(r):
    return [w["code"] for w in r["meta"]["warnings"]]


def live_world(hh, obs):
    obs.start_process(hh.clock.now() - timedelta(minutes=5))
    obs.ready()
    r = hh.call(*RUNTIME_PROBE)
    assert r["ok"] and r["meta"]["game_state"] == "ready"
    return r


def test_row_game_not_running(env):
    hh, obs = env
    # no files at all
    assert hh.call(*RUNTIME_PROBE)["error"]["code"] == "game_not_running"
    assert hh.call(*STATIC_PROBE)["error"]["code"] == "snapshot_unavailable"
    st = hh.call("get_game_status")
    assert st["ok"] and st["data"]["game"]["running"] is False
    # files from a previous run remain
    live_world(hh, obs)
    obs.kill_process()
    r = hh.call(*RUNTIME_PROBE)
    assert r["error"]["code"] == "game_not_running"
    r2 = hh.call(*RUNTIME_PROBE[:1], {**RUNTIME_PROBE[1], "allow_stale": True})
    assert r2["ok"] and r2["meta"]["source"] == "stale_snapshot" and r2["meta"]["stale_reason"] == "game_not_running"
    s = hh.call(*STATIC_PROBE)
    assert s["ok"] and s["meta"]["source"] == "static_catalog" and "catalog_from_previous_session" in warn_codes(s)
    assert any(u["field"].startswith("base_price") for u in s["data"]["unavailable"])


def test_row_main_menu_and_loading(env):
    hh, obs = env
    live_world(hh, obs)
    obs.menu()
    assert hh.call(*RUNTIME_PROBE)["error"]["code"] == "at_main_menu"
    r = hh.call(*RUNTIME_PROBE[:1], {**RUNTIME_PROBE[1], "allow_stale": True})
    assert r["ok"] and r["meta"]["stale_reason"] == "not_in_game"
    s = hh.call(*STATIC_PROBE)
    assert s["ok"] and "catalog_from_previous_session" in warn_codes(s)
    obs.loading()
    assert hh.call(*RUNTIME_PROBE)["error"]["code"] == "loading"
    assert "catalog_from_previous_session" in warn_codes(hh.call(*STATIC_PROBE))


def test_row_save_active_and_paused(env):
    hh, obs = env
    r = live_world(hh, obs)
    assert r["meta"]["source"] == "live_snapshot" and r["meta"]["stale"] is False and r["meta"]["paused"] is False
    s = hh.call(*STATIC_PROBE)
    assert s["ok"] and "catalog_from_previous_session" not in warn_codes(s) and s["data"]["products"][0]["current_price"] is not None
    obs.heartbeat("ready", paused=True)
    r = hh.call(*RUNTIME_PROBE)
    assert r["ok"] and r["meta"]["paused"] is True
    # refresh requests are served while paused (A16): Min Keep changed by the user while paused
    obs.min_keep_override = 3
    hh.clock.on_sleep = lambda c: obs.serve_refresh("state")
    route = f"route:{bf.B_GW1}|Gas|{bf.B_PC1}|own|0"
    r = hh.call("get_route", {"route": route, "fresh": True})
    assert r["ok"] and r["data"]["min_keep"]["value"] == 3 and "refresh_timeout" not in warn_codes(r)


def test_row_return_to_menu_then_new_save(env):
    hh, obs = env
    live_world(hh, obs)
    obs.menu()
    assert hh.call(*RUNTIME_PROBE)["error"]["code"] == "at_main_menu"
    obs.loading()
    assert hh.call(*RUNTIME_PROBE)["error"]["code"] == "loading"
    obs.ready(world_session=bf.WORLD_SESSION_2)
    r = hh.call(*RUNTIME_PROBE)
    assert r["ok"] and r["meta"]["world_session"] == bf.WORLD_SESSION_2


def test_row_quickload(env):
    hh, obs = env
    live_world(hh, obs)
    veh = hh.call("list_vehicles", {"aggregate": False})["data"]["vehicles"][0]["id"]
    assert hh.call("list_vehicles", {"vehicle": veh})["ok"]
    obs.loading()
    assert hh.call(*RUNTIME_PROBE)["error"]["code"] == "loading"
    # ready in a new session, before the first capture of that session
    obs.ready(world_session=bf.WORLD_SESSION_2, publish=False)
    r = hh.call(*RUNTIME_PROBE)
    assert r["error"]["code"] == "snapshot_unavailable"
    r2 = hh.call(*RUNTIME_PROBE[:1], {**RUNTIME_PROBE[1], "allow_stale": True})
    assert r2["ok"] and r2["meta"]["stale_reason"] == "world_session_changed"
    # static of the previous session is still served for catalogue tools, with a warning
    assert "catalog_from_previous_session" in warn_codes(hh.call(*STATIC_PROBE))
    obs.publish_static()
    obs.publish_state()
    obs.publish_history()
    obs.heartbeat("ready")
    r3 = hh.call(*RUNTIME_PROBE)
    assert r3["ok"] and r3["meta"]["snapshot"]["world_session"] == bf.WORLD_SESSION_2
    assert "catalog_from_previous_session" not in warn_codes(hh.call(*STATIC_PROBE))
    # vehicle ids of the old session are rejected
    r4 = hh.call("list_vehicles", {"vehicle": veh})
    assert r4["error"]["code"] == "stale_reference"
    assert hh.call("get_building", {"building": veh})["error"]["code"] == "stale_reference"
    # no response mixes sessions
    for name in PRD_TOOLS:
        resp = hh.call(name, VALID_CALLS[name])
        for s in resp["meta"].get("snapshots") or []:
            if s["family"] != "static":
                assert s["world_session"] == bf.WORLD_SESSION_2


def test_row_game_closing_or_hang(env):
    hh, obs = env
    live_world(hh, obs)
    hh.clock.advance(8)  # heartbeat stops (hung process)
    assert hh.call(*RUNTIME_PROBE)["error"]["code"] == "observer_unresponsive"
    assert "catalog_from_previous_session" in warn_codes(hh.call(*STATIC_PROBE))
    obs.kill_process()  # process gone
    assert hh.call(*RUNTIME_PROBE)["error"]["code"] == "game_not_running"


def test_row_observer_starting_grace(env):
    hh, obs = env
    obs.start_process(hh.clock.now())
    r = hh.call(*RUNTIME_PROBE)
    assert r["error"]["code"] == "observer_not_detected" and r["meta"]["game_state"] == "starting"
    hh.clock.advance(61)
    r = hh.call(*RUNTIME_PROBE)
    assert r["error"]["code"] == "observer_not_detected" and r["meta"]["game_state"] == "observer_not_detected"
    obs.heartbeat("starting")
    assert hh.call(*RUNTIME_PROBE)["meta"]["game_state"] == "starting"
    obs.menu()
    assert hh.call(*RUNTIME_PROBE)["error"]["code"] == "at_main_menu"


def test_row_observer_faulted_and_section_failures(env):
    hh, obs = env
    live_world(hh, obs)
    data = bf.state_data()
    data["shops"] = None
    obs.publish_state(data=data)
    doc = obs.state_doc
    doc["sections"]["shops"] = bf.section_status(1512, 0, "disabled", "failed_repeatedly")
    doc["content_hash"] = bf.content_hash(doc["data"])
    obs._write("state", doc)
    obs.heartbeat("ready", disabled_sections=[{"section": "shops", "error_signature": "NullReference@Shop", "reason": "failed_repeatedly"}])
    assert hh.call("get_shop", {"shop": bf.S_HW1})["error"]["code"] == "section_unavailable"
    assert hh.call("find_shops", {"product": "Paint"})["error"]["code"] == "section_unavailable"
    assert hh.call(*RUNTIME_PROBE)["ok"]
    city = hh.call("get_city", {"city": "Valmont"})
    assert city["ok"] and "shops" in city["meta"]["snapshot"]["sections_unavailable"]
    st = hh.call("get_game_status")
    assert st["data"]["observer"]["disabled_sections"][0]["section"] == "shops"
    obs.heartbeat("faulted")
    assert hh.call(*RUNTIME_PROBE)["error"]["code"] == "observer_faulted"


def test_row_mcp_started_before_game(env):
    """A18: game_not_running -> observer_not_detected (grace) -> at_main_menu -> live, without restart."""
    hh, obs = env
    assert hh.call(*RUNTIME_PROBE)["error"]["code"] == "game_not_running"
    obs.start_process(hh.clock.now())
    hh.clock.advance(5)
    r = hh.call(*RUNTIME_PROBE)
    assert r["error"]["code"] == "observer_not_detected" and r["meta"]["game_state"] == "starting"
    obs.menu()
    assert hh.call(*RUNTIME_PROBE)["error"]["code"] == "at_main_menu"
    obs.loading()
    obs.ready()
    assert hh.call(*RUNTIME_PROBE)["ok"]


def test_row_mcp_restart_identical_answers(env):
    hh, obs = env
    live_world(hh, obs)
    before = {n: hh.call(n, VALID_CALLS[n]) for n in PRD_TOOLS}
    hh.restart()
    after = {n: hh.call(n, VALID_CALLS[n]) for n in PRD_TOOLS}
    for n in PRD_TOOLS:
        assert before[n]["ok"] and after[n]["ok"], n
        assert before[n]["data"] == after[n]["data"], n
        b, a = dict(before[n]["meta"]), dict(after[n]["meta"])
        assert b == a, n


def test_row_stale_by_age(env):
    hh, obs = env
    live_world(hh, obs)
    hh.clock.advance(20)
    obs.heartbeat("ready")  # heartbeat fresh, but no new capture and last_verified unchanged
    r = hh.call(*RUNTIME_PROBE)
    assert r["ok"] and r["meta"]["stale"] is True and r["meta"]["stale_reason"] == "age" and "stale" in warn_codes(r)


def test_row_corrupted_file(env):
    hh, obs = env
    live_world(hh, obs)
    hh.write_raw("state", "{garbage")
    r = hh.call(*RUNTIME_PROBE)
    assert r["ok"] and "snapshot_invalid_using_previous" in warn_codes(r)
    obs.publish_state()  # recovery at the next capture (A19)
    obs.heartbeat("ready")
    r2 = hh.call(*RUNTIME_PROBE)
    assert r2["ok"] and "snapshot_invalid_using_previous" not in warn_codes(r2)
    assert r2["meta"]["snapshot"]["seq"] == obs.state_seq


def test_row_schema_mismatch(env):
    hh, obs = env
    live_world(hh, obs)
    doc = json.loads((hh.exchange / "history.json").read_text(encoding="utf-8"))
    doc["schema_version"] = "3.1.0"
    hh.write_raw("history", json.dumps(doc))
    r = hh.call("get_finances")
    assert r["error"]["code"] == "schema_mismatch" and r["error"]["details"]["file_version"] == "3.1.0"
    assert r["error"]["details"]["server_version"] == "1.0.0"
    assert hh.call(*RUNTIME_PROBE)["ok"]


def test_row_unsupported_build(env):
    """A20 (server part): every runtime and static tool returns unsupported_build; get_game_status shows detected vs expected."""
    hh, obs = env
    obs.start_process(hh.clock.now() - timedelta(minutes=2))
    obs.unsupported_build()
    for name in PRD_TOOLS:
        r = hh.call(name, VALID_CALLS[name])
        check_response(name, r)
        if name == "get_game_status":
            g = r["data"]["game"]
            assert r["ok"] and g["compatibility"] == "unsupported_build"
            assert g["detected_game"]["build"] == "0600a" and g["expected_game"]["build"] == "0507b"
        else:
            assert r["error"]["code"] == "unsupported_build", name
    # also when a catalogue from an earlier verified run exists
    obs.world_session = bf.WORLD_SESSION
    obs.publish_static()
    obs.unsupported_build()
    assert hh.call(*STATIC_PROBE)["error"]["code"] == "unsupported_build"
    # fresh never writes a refresh request in this state
    hh.call("list_routes", {"fresh": True})
    assert obs.read_refresh_request() is None


def test_pending_compatibility_heartbeat(env):
    hh, obs = env
    obs.start_process(hh.clock.now() - timedelta(minutes=1))
    obs.heartbeat("menu", compatibility="pending", detected_game=None)
    r = hh.call(*RUNTIME_PROBE)
    assert r["error"]["code"] == "at_main_menu"
    st = hh.call("get_game_status")
    assert st["ok"] and st["data"]["game"]["compatibility"] == "pending"
