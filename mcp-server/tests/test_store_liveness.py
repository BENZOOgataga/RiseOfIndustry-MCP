"""T-5: snapshot store (reload, last-good, session switch, window), liveness matrix, staleness,
name resolution and ambiguity."""

import copy
from datetime import timedelta

import pytest

import build_fixtures as bf
from conftest import FakeProcs, game_proc
from roi_mcp.config import ProcInfo, is_game_process_name
from roi_mcp.store import FamilyStatus, Snapshot, classify, currency
from roi_mcp.util import parse_utc


def hb_status(doc):
    st = FamilyStatus()
    if doc is not None:
        st.current = Snapshot("heartbeat", doc, doc["seq"], None, doc["world_session"], doc["pid"], doc["schema_version"],
                              None, (0, 0), 0.0)
    return st


NOW = bf.BASE_TIME
OLD_START = (NOW - timedelta(minutes=30)).timestamp()


def proc(pid=bf.PID, start=OLD_START):
    return [ProcInfo(pid, "Rise of Industry.exe", start)]


# ------------------------------------------------------------------ liveness matrix (PRD 11.7)

@pytest.mark.parametrize("procs,hb,expected", [
    ([], bf.build_heartbeat(NOW), "game_not_running"),
    ([], None, "game_not_running"),
    (proc(), None, "observer_not_detected"),
    (proc(start=(NOW - timedelta(seconds=20)).timestamp()), None, "starting"),
    (proc(pid=999), bf.build_heartbeat(NOW), "observer_not_detected"),
    (proc(start=(NOW - timedelta(seconds=10)).timestamp()), bf.build_heartbeat(NOW, written_age_s=30), "starting"),
    (proc(), bf.build_heartbeat(NOW, written_age_s=6), "observer_unresponsive"),
    (proc(), bf.build_heartbeat(NOW, state="menu"), "menu"),
    (proc(), bf.build_heartbeat(NOW, state="loading"), "loading"),
    (proc(), bf.build_heartbeat(NOW, state="disabled"), "disabled"),
    (proc(), bf.build_heartbeat(NOW, state="faulted"), "faulted"),
    (proc(), bf.build_heartbeat(NOW, state="starting"), "starting"),
    (proc(), bf.build_heartbeat(NOW), "ready"),
])
def test_liveness_matrix(procs, hb, expected):
    lv = classify(procs, hb_status(hb), NOW)
    assert lv.game_state == expected


def test_heartbeat_older_than_process_start_is_not_detected():
    start = (NOW - timedelta(minutes=5)).timestamp()
    hb = bf.build_heartbeat(NOW - timedelta(minutes=10))  # written before the process started
    lv = classify(proc(start=start), hb_status(hb), NOW)
    assert lv.game_state == "observer_not_detected"


def test_game_unresponsive_warning():
    lv = classify(proc(), hb_status(bf.build_heartbeat(NOW, tick_age_s=12)), NOW)
    assert lv.game_state == "ready"
    assert [w["code"] for w in lv.warnings] == ["game_unresponsive"]


def test_unsupported_build_overrides():
    hb = bf.build_heartbeat(NOW, state="unsupported_build", compatibility="unsupported_build")
    lv = classify(proc(), hb_status(hb), NOW)
    assert lv.unsupported_build and lv.error_code() == "unsupported_build"
    # a heartbeat from an older process does not block a newly started game
    lv2 = classify(proc(pid=777, start=(NOW - timedelta(seconds=5)).timestamp()), hb_status(hb), NOW)
    assert not lv2.unsupported_build and lv2.game_state == "starting"


def test_pending_compatibility_maps_lifecycle_states():
    hb = bf.build_heartbeat(NOW, state="menu", compatibility="pending")
    lv = classify(proc(), hb_status(hb), NOW)
    assert lv.game_state == "menu" and lv.error_code() == "at_main_menu" and not lv.unsupported_build


def test_process_name_matching():
    assert is_game_process_name("Rise of Industry.exe")
    assert is_game_process_name("rise of industry")
    assert not is_game_process_name("Rise of Industry 2.exe")
    assert not is_game_process_name("RiseOfIndustry.exe")


# ------------------------------------------------------------------ currency / staleness

def _snap(doc):
    return Snapshot(doc["schema"].split("/")[1], doc, doc["seq"], doc["content_hash"], doc["world_session"], doc["pid"],
                    doc["schema_version"], parse_utc(doc["captured"]["utc_end"]), (0, 0), 0.0)


def test_currency_rules():
    world = bf.build_world()
    lv = classify(proc(), hb_status(world["heartbeat"]), NOW)
    st = _snap(world["state"])
    c = currency(st, lv, NOW)
    assert c.current and c.age_s == pytest.approx(2.0)
    # age limit max(15, 3 * effective_interval_s=5) = 15 s
    later = NOW + timedelta(seconds=14)
    hb_later = copy.deepcopy(world["heartbeat"])
    hb_later["data"]["written_utc"] = bf.iso(later)
    hb_later["data"]["families"]["state"]["last_verified_utc"] = world["state"]["captured"]["utc_end"]
    lv2 = classify(proc(), hb_status(hb_later), later)
    assert currency(st, lv2, later).current is False and currency(st, lv2, later).stale_reason == "age"
    # last_verified_utc refreshes the age when content is unchanged
    hb_later["data"]["families"]["state"]["last_verified_utc"] = bf.iso(later - timedelta(seconds=1))
    lv3 = classify(proc(), hb_status(hb_later), later)
    assert currency(st, lv3, later).current is True
    # but not when the heartbeat refers to different content
    hb_later["data"]["families"]["state"]["content_hash"] = "other"
    lv4 = classify(proc(), hb_status(hb_later), later)
    assert currency(st, lv4, later).current is False
    # larger effective interval widens the limit
    hb_slow = copy.deepcopy(hb_later)
    hb_slow["data"]["families"]["state"]["content_hash"] = st.content_hash
    hb_slow["data"]["families"]["state"]["last_verified_utc"] = world["state"]["captured"]["utc_end"]
    hb_slow["data"]["effective_interval_s"] = 20.0
    assert currency(st, classify(proc(), hb_status(hb_slow), later), later).current is True


def test_currency_other_session_and_not_live():
    world = bf.build_world()
    hb = bf.build_heartbeat(NOW, world_session=bf.WORLD_SESSION_2)
    c = currency(_snap(world["state"]), classify(proc(), hb_status(hb), NOW), NOW)
    assert not c.current and c.stale_reason == "world_session_changed"
    c2 = currency(_snap(world["state"]), classify([], hb_status(world["heartbeat"]), NOW), NOW)
    assert c2.stale_reason == "game_not_running"
    c3 = currency(_snap(world["state"]), classify(proc(), hb_status(bf.build_heartbeat(NOW, state="menu")), NOW), NOW)
    assert c3.stale_reason == "not_in_game"


# ------------------------------------------------------------------ store behaviour

def test_reload_on_seq_change(h):
    assert h.call("list_routes")["meta"]["snapshot"]["seq"] == 10
    doc = copy.deepcopy(h.world["state"])
    doc["seq"] = 11
    h.write("state", doc)
    h.write_raw("state", (h.exchange / "state.json").read_text(encoding="utf-8"))
    assert h.call("list_routes")["meta"]["snapshot"]["seq"] == 11


def test_last_good_fallback_and_recovery(h):
    assert h.call("list_routes")["ok"]
    h.write_raw("state", '{"schema": "roi-mcp/state", "truncated...')
    r = h.call("list_routes")
    assert r["ok"] and "snapshot_invalid_using_previous" in [w["code"] for w in r["meta"]["warnings"]]
    assert r["meta"]["snapshot"]["seq"] == 10
    doc = copy.deepcopy(h.world["state"])
    doc["seq"] = 12
    h.write_raw("state", __import__("json").dumps(doc))
    r2 = h.call("list_routes")
    assert r2["meta"]["snapshot"]["seq"] == 12
    assert "snapshot_invalid_using_previous" not in [w["code"] for w in r2["meta"]["warnings"]]


def test_world_session_switch_clears_window(h):
    h.call("list_routes")
    assert h.app.window.world_session == bf.WORLD_SESSION and len(h.app.window.entries) == 1
    w2 = bf.build_world(world_session=bf.WORLD_SESSION_2)
    h.write_world(w2)
    h.call("list_routes")
    assert h.app.window.world_session == bf.WORLD_SESSION_2 and len(h.app.window.entries) == 1


def test_window_bounds(h):
    win = h.app.window
    for i in range(30):
        doc = bf.build_state(bf.BASE_TIME + timedelta(seconds=10 * i), seq=100 + i)
        win.add(_snap(doc))
    assert len(win.entries) == 20
    win.clear()
    for i in range(10):
        doc = bf.build_state(bf.BASE_TIME + timedelta(minutes=10 * i), seq=200 + i)
        win.add(_snap(doc))
    assert len(win.entries) == 4  # 30-minute bound: 0, 10, 20, 30 minutes before the newest


def test_inventory_window_delta_in_get_building(h):
    first = copy.deepcopy(h.world["state"])
    h.call("get_building", {"building": bf.B_PF1})
    data = bf.state_data()
    for b in data["buildings_player"]:
        if b["key"] == bf.B_PF1:
            for inv in b["inventory"]:
                if inv["product"] == "Dye":
                    inv["count"] = 9
    h.clock.advance(5)
    doc = bf.build_state(h.clock.now(), seq=first["seq"] + 1, data=data)
    h.write_raw("state", __import__("json").dumps(doc))
    hb = bf.build_heartbeat(h.clock.now(), static_doc=h.world["static"], state_doc=doc, history_doc=h.world["history"])
    h.write_raw("heartbeat", __import__("json").dumps(hb))
    r = h.call("get_building", {"building": bf.B_PF1})
    dye = next(i for i in r["data"]["inventory"] if i["product"] == "product:Dye")
    assert dye["window_delta"]["value"] == 3 and dye["window_delta"]["method"] == "D-INV-2"
    assert dye["accumulation"]["accumulating"] and "count_rose_across_window" in dye["accumulation"]["reasons"]


def test_static_mismatch_reload_then_warning(h):
    st = copy.deepcopy(h.world["state"])
    st["static_ref"] = {"seq": 99, "content_hash": "nope"}
    st["seq"] = 20
    h.write_raw("state", __import__("json").dumps(st))
    r = h.call("list_routes")
    assert r["ok"] and "static_mismatch" in [w["code"] for w in r["meta"]["warnings"]]
    # automatic recovery writes a static-scope nonce only
    req = __import__("json").loads((h.exchange / "refresh-request.json").read_text(encoding="utf-8"))
    assert req["requests"]["static"] is not None and req["requests"]["state"] is None and req["requests"]["history"] is None


def test_static_reloaded_when_static_ref_points_to_newer_static(h):
    h.call("list_products")
    new_static = bf.build_static(seq=2)
    new_static["data"]["products"][0]["display_name"] = "Gaz naturel"
    new_static["content_hash"] = bf.content_hash(new_static["data"])
    st = bf.build_state(static_doc=new_static, seq=30)
    # write state first: the static_ref check must force a static reload once
    h.write_raw("state", __import__("json").dumps(st))
    h.write_raw("static", __import__("json").dumps(new_static))
    r = h.call("list_routes")
    assert "static_mismatch" not in [w["code"] for w in r["meta"]["warnings"]]


# ------------------------------------------------------------------ name resolution

def test_name_resolution_exact_accent_case_insensitive(h):
    r = h.call("get_city", {"city": "SAINT-ELOI"})
    assert r["ok"] and r["data"]["identity"]["id"] == "city:12"
    assert h.call("get_building", {"building": "usine pétrochimique 2"})["data"]["identity"]["id"] == f"building:{bf.B_PC2}"
    assert h.call("get_building", {"building": "Usine Petrochimique 2"})["ok"]
    assert h.call("get_product", {"product": "peinture"})["data"]["definition"]["id"] == "product:Paint"
    assert h.call("get_product", {"product": "Paint"})["ok"]          # English name
    assert h.call("get_product", {"product": "product:Paint"})["ok"]  # id
    assert h.call("get_building", {"building": bf.B_PF1})["ok"]       # raw key


def test_ambiguity_and_not_found(h):
    r = h.call("get_building", {"building": "USINE DE PEINTURE 1"})
    assert r["error"]["code"] == "ambiguous"
    ids = {c["id"] for c in r["error"]["candidates"]}
    assert ids == {f"building:{bf.B_PF1}", f"building:{bf.AI_PF}"}
    assert all("owner" in c and "coordinates" in c for c in r["error"]["candidates"])
    r2 = h.call("get_shop", {"shop": "Quincaillerie"})
    assert r2["error"]["code"] == "ambiguous" and len(r2["error"]["candidates"]) == 2
    # no fuzzy matching outside search
    assert h.call("get_product", {"product": "Pain"})["error"]["code"] == "not_found"
    assert h.call("get_building", {"building": "building:Nope@1,1"})["error"]["code"] == "not_found"


def test_search_fuzzy_and_kinds(h):
    r = h.call("search", {"query": "peintur"})
    assert r["ok"] and r["data"]["results"][0]["match_kind"] in ("prefix", "exact")
    r2 = h.call("search", {"query": "quincaillerie", "kinds": ["shop"]})
    assert {x["id"] for x in r2["data"]["results"]} == {f"building:{bf.S_HW1}", f"building:{bf.S_HW2}"}
    r3 = h.call("search", {"query": "usine de peinture 1", "owner": "player"})
    assert [x["id"] for x in r3["data"]["results"]][0] == f"building:{bf.B_PF1}"
    assert all(x["owner"]["actor_id"] == 1 for x in r3["data"]["results"])
    r4 = h.call("search", {"query": "Valmnt"})
    assert any(x["id"] == "city:10" and x["match_kind"] == "fuzzy" for x in r4["data"]["results"])
