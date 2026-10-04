"""Hardening tests for `get_game_status` and `search` (PRD 11.6, 11.7, 13, 14.1, 14.9, 16).

Every response (ok and error) is validated with `check_response`. Expectations are justified by the PRD,
the response schemas or docs/*.md; where the PRD is silent only envelope invariants are asserted.
"""

from __future__ import annotations

import asyncio
import copy
import os
import unicodedata
from datetime import timedelta

import pytest

import build_fixtures as bf
from conftest import FakeClock, FakeProcs, Harness, game_proc  # noqa: F401
from fake_observer import FakeObserver
from test_tools_contract import VALID_CALLS, _observer_harness, check_response

# PRD 13.3 error table (internal_error is not part of the PRD vocabulary).
PRD_ERROR_CODES = {
    "game_not_running", "observer_not_detected", "observer_unresponsive", "at_main_menu", "loading",
    "observer_disabled", "observer_faulted", "unsupported_build", "snapshot_unavailable", "section_unavailable",
    "schema_mismatch", "not_found", "ambiguous", "stale_reference", "invalid_argument",
}
# PRD 13.3: lifecycle classification (11.7) -> error code.
LIFECYCLE_ERROR = {
    "game_not_running": "game_not_running", "observer_not_detected": "observer_not_detected",
    "starting": "observer_not_detected", "observer_unresponsive": "observer_unresponsive", "menu": "at_main_menu",
    "loading": "loading", "disabled": "observer_disabled", "faulted": "observer_faulted",
    "unsupported_build": "unsupported_build",
}
STALE_REASONS = {"age", "world_session_changed", "not_in_game", "game_not_running", "observer_unresponsive"}
LIVE_KINDS = {"building", "shop", "company", "city", "region"}
STATIC_KINDS = {"product", "recipe", "building_type", "tech", "tech_tree", "bill_category"}
SEARCH_KINDS = ["building", "building_type", "product", "recipe", "city", "region", "company", "tech", "shop"]

WS = bf.WORLD_SESSION


# ============================================================================ helpers

_MTIME = [0]


def _bump(path) -> None:
    """Give a rewritten file a strictly newer mtime so the store always sees a new signature."""
    st = path.stat()
    new = max(st.st_mtime_ns, _MTIME[0]) + 10_000_000
    _MTIME[0] = new
    os.utime(path, ns=(new, new))


def put(h: Harness, family: str, doc) -> None:
    h.write(family, doc)
    _bump(h.exchange / f"{family}.json")


def put_raw(h: Harness, family: str, text: str) -> None:
    h.write_raw(family, text)
    _bump(h.exchange / f"{family}.json")


def hb(h: Harness, w: dict, **kw) -> dict:
    """Write a heartbeat stamped at the fake clock's current time."""
    doc = bf.build_heartbeat(h.clock.now(), static_doc=w["static"], state_doc=w["state"], history_doc=w["history"], **kw)
    put(h, "heartbeat", doc)
    return doc


def put_state(h: Harness, w: dict) -> None:
    """Publish a mutated state as a new snapshot (new seq + content hash, same static_ref)."""
    w["state"]["seq"] += 1
    w["state"]["content_hash"] = bf.content_hash(w["state"]["data"])
    put(h, "state", w["state"])


def call(h: Harness, name: str, args=None) -> dict:
    resp = h.call(name, args)
    check_response(name, resp)
    _envelope_invariants(resp)
    return resp


def raw_call(h: Harness, name: str, args) -> dict:
    """Call without the harness' `args or {}` coercion (non-dict arguments)."""
    resp = asyncio.run(h.app.call(name, args))
    check_response(name, resp)
    _envelope_invariants(resp)
    return resp


def _envelope_invariants(resp: dict) -> None:
    meta = resp["meta"]
    if meta["stale"]:
        assert meta["stale_reason"] in STALE_REASONS, meta
    else:
        assert meta["stale_reason"] is None, meta
    if not resp["ok"]:
        e = resp["error"]
        assert e["code"] in PRD_ERROR_CODES, e          # PRD 13.3 table; internal_error is a server bug
        assert isinstance(e["message"], str) and e["message"].strip(), e
        assert "hint" in e, e


def ok(resp: dict) -> dict:
    assert resp["ok"] is True, resp.get("error")
    return resp["data"]


def err(resp: dict, code=None) -> dict:
    assert resp["ok"] is False, "expected an error response"
    e = resp["error"]
    if code is not None:
        codes = {code} if isinstance(code, str) else set(code)
        assert e["code"] in codes, e
    return e


def results(resp: dict) -> list[dict]:
    return ok(resp)["results"]


def ids(resp: dict) -> list[str]:
    return [r["id"] for r in results(resp)]


def by_id(resp: dict) -> dict[str, dict]:
    return {r["id"]: r for r in results(resp)}


def warning_codes(resp: dict) -> list[str]:
    return [w["code"] for w in resp["meta"]["warnings"]]


def norm(text) -> str:
    s = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).casefold().strip()


def ai_row(key: str, display: str, owner: int = bf.AI_B) -> dict:
    prefab, rest = key.split("@", 1)
    x, y = (int(v) for v in rest.split(","))
    return {"key": key, "prefab": prefab, "display_name": display, "owner_actor_id": owner, "x": x, "y": y,
            "region_id": bf.R_BRI, "city_id": bf.CITY_BRI, "kind": "factory", "tags": ["factory"],
            "flags": {"user_enabled": True, "requirements_met": True, "is_working": True}, "recipe": "Paints",
            "produced_last_month": 1, "cycle_days_effective": 35.0, "module_count": None, "is_module": False}


def enter(h: Harness, w: dict, state: str) -> str:
    """Put the harness into a lifecycle state (PRD 8.2 / 11.7). Returns the expected meta.game_state."""
    now = h.clock.now()
    if state == "ready":
        hb(h, w)
    elif state in ("menu", "loading", "disabled", "faulted"):
        hb(h, w, state=state)
    elif state == "starting_hb":
        hb(h, w, state="starting")
        return "starting"
    elif state == "unsupported_build":
        detected = dict(bf.GAME)
        detected["build"] = "0600a"
        detected["assembly_sha256"] = "0" * 64
        hb(h, w, state="unsupported_build", compatibility="unsupported_build", detected_game=detected)
    elif state == "game_not_running":
        h.procs.procs = []
    elif state == "observer_unresponsive":
        h.clock.advance(6)  # heartbeat (written 0.5 s before) is now 6.5 s old: > 5 s
    elif state == "observer_not_detected":
        h.procs.procs = [game_proc(pid=555, start=now - timedelta(minutes=5))]
    elif state == "starting":
        h.procs.procs = [game_proc(pid=555, start=now - timedelta(seconds=10))]
    else:  # pragma: no cover
        raise AssertionError(state)
    return state


ALL_STATES = ["ready", "menu", "loading", "disabled", "faulted", "starting_hb", "unsupported_build", "game_not_running",
              "observer_unresponsive", "observer_not_detected", "starting"]
NOT_LIVE = [s for s in ALL_STATES if s not in ("ready", "unsupported_build")]


# ============================================================================ get_game_status: contract

def test_status_live_fields_reflect_heartbeat(h):
    r = call(h, "get_game_status")
    d = ok(r)
    g, o, s = d["game"], d["observer"], d["server"]
    hbd = h.world["heartbeat"]["data"]
    # PRD 14.1 game block
    assert g["running"] is True and g["pid"] == bf.PID and g["state"] == "ready"
    assert g["paused"] is False and g["speed_level"] == 1 and g["time_scale"] == 1.0
    assert g["game_date"] == "Y5-03-12" and g["world_session"] == WS and g["language"] == "French"
    assert g["compatibility"] == "verified" and g["detected_game"] == g["expected_game"] == bf.GAME
    assert g["module"] is not None
    # PRD 14.1 observer block
    assert o["version"] == "1.0.0" and o["effective_interval_s"] == 5.0 and o["degraded"] is False
    assert o["disabled_sections"] == [] and o["errors_last_hour"] == 0
    assert o["reflection_self_check"]["total"] == 24 and o["reflection_self_check"]["resolved"] == 24
    assert set(o["last_captures"]) == {"static", "state", "history"}
    for fam in ("static", "state", "history"):
        lc = o["last_captures"][fam]
        assert lc["seq"] == hbd["families"][fam]["seq"]
        assert lc["size_bytes"] == hbd["families"][fam]["size_bytes"]          # "size"
        assert lc["last_published_utc"] == hbd["families"][fam]["last_published_utc"]  # "time"
    # PRD 14.1 server block
    assert s["version"] == r["meta"]["server_version"] and isinstance(s["schema_versions"], dict)
    assert d["limitations"] and all(isinstance(x, str) and x for x in d["limitations"])
    # meta (PRD 13.3)
    m = r["meta"]
    assert m["game_state"] == g["state"] == "ready" and m["world_session"] == WS and m["compatibility"] == "verified"
    assert m["stale"] is False and m["paused"] is False
    assert r["page"] == {"next_cursor": None, "total": None}


@pytest.mark.parametrize("state", ALL_STATES)
def test_status_always_answers_in_every_lifecycle_state(h, state):
    """PRD 14.1: errors none, always answers, including when the game is not running."""
    expected = enter(h, h.world, state)
    r = call(h, "get_game_status")
    d = ok(r)
    assert d["game"]["state"] == r["meta"]["game_state"] == expected
    assert d["game"]["running"] is (state != "game_not_running")
    if state == "game_not_running":
        assert d["game"]["pid"] is None
    if state == "unsupported_build":
        assert d["game"]["compatibility"] == "unsupported_build" == r["meta"]["compatibility"]
        assert d["game"]["detected_game"] != d["game"]["expected_game"]          # PRD 3.2 / TROUBLESHOOTING
    assert d["limitations"] and all(isinstance(x, str) for x in d["limitations"])
    assert not (h.exchange / "refresh-request.json").exists()  # scope none: never writes a request


def test_status_limitations_are_a_static_list(h):
    first = ok(call(h, "get_game_status"))["limitations"]
    h.procs.procs = []
    assert ok(call(h, "get_game_status"))["limitations"] == first


def test_status_without_any_file(empty_h):
    r = call(empty_h, "get_game_status")
    d = ok(r)
    assert d["observer"] is None
    assert d["game"]["running"] is True and d["game"]["state"] == "observer_not_detected"  # game started 30 min ago
    assert r["meta"]["source"] in ("none", "live_snapshot", "static_catalog", "stale_snapshot")


def test_status_without_game_and_without_files(empty_h):
    empty_h.procs.procs = []
    d = ok(call(empty_h, "get_game_status"))
    assert d["game"]["running"] is False and d["game"]["state"] == "game_not_running"


def test_status_with_missing_exchange_dir(tmp_path, clock):
    hh = Harness(tmp_path / "does-not-exist", clock, FakeProcs([]))
    d = ok(call(hh, "get_game_status"))
    assert d["game"]["state"] == "game_not_running" and d["observer"] is None


@pytest.mark.parametrize("text", ["{not json", "", "[]", "null", '{"schema": "roi-mcp/heartbeat"}', "﻿{}"])
def test_status_with_malformed_heartbeat_first_load(h, text):
    put_raw(h, "heartbeat", text)
    d = ok(call(h, "get_game_status"))
    assert d["observer"] is None                                    # no valid heartbeat
    assert d["game"]["state"] == "observer_not_detected"           # PRD 11.7: process, no heartbeat, after grace


def test_status_with_malformed_heartbeat_after_valid_one(h):
    ok(call(h, "get_game_status"))
    put_raw(h, "heartbeat", "{truncated")
    ok(call(h, "get_game_status"))


@pytest.mark.parametrize("family", ["static", "state", "history"])
@pytest.mark.parametrize("text", ["{not json", "", '{"schema": "roi-mcp/x"}'])
def test_status_with_malformed_snapshot(h, family, text):
    put_raw(h, family, text)
    d = ok(call(h, "get_game_status"))
    assert d["game"]["state"] == "ready"


@pytest.mark.parametrize("family", ["static", "state", "history"])
def test_status_with_missing_snapshot(h, family):
    (h.exchange / f"{family}.json").unlink()
    ok(call(h, "get_game_status"))


def test_status_with_heartbeat_schema_major_mismatch(h):
    hb(h, h.world, schema_version="2.0.0")
    ok(call(h, "get_game_status"))


@pytest.mark.parametrize("family", ["static", "state", "history"])
def test_status_with_snapshot_schema_major_mismatch(h, family):
    w = copy.deepcopy(h.world)
    w[family]["schema_version"] = "2.0.0"
    put(h, family, w[family])
    ok(call(h, "get_game_status"))


@pytest.mark.parametrize("field", ["written_utc", "main_thread_last_tick_utc"])
@pytest.mark.parametrize("value", ["0001-01-01T00:00:00.0000000+01:00", "9999-12-31T23:59:59.9999999+00:00"])
# Regression test for DEFECT SS-1 (fixed).
def test_status_survives_extreme_heartbeat_timestamps(h, field, value):
    """PRD 14.1: get_game_status has no errors and always answers. The heartbeat below is schema-valid
    (timestamps are plain strings) and contains .NET DateTime.MinValue / MaxValue in round-trip format."""
    w = copy.deepcopy(h.world)
    w["heartbeat"]["data"][field] = value
    put(h, "heartbeat", w["heartbeat"])
    r = call(h, "get_game_status")
    ok(r)


# Regression test for DEFECT SS-1 (fixed).
def test_status_survives_extreme_snapshot_timestamp(h):
    w = copy.deepcopy(h.world)
    w["history"]["captured"]["utc_end"] = "9999-12-31T23:59:59.9999999-01:00"
    put(h, "history", w["history"])
    ok(call(h, "get_game_status"))


# Regression test for DEFECT SS-1 (fixed).
def test_search_survives_extreme_heartbeat_timestamp(h):
    w = copy.deepcopy(h.world)
    w["heartbeat"]["data"]["written_utc"] = "9999-12-31T23:59:59.9999999+00:00"
    put(h, "heartbeat", w["heartbeat"])
    call(h, "search", {"query": "gaz"})        # asserts the error code (if any) is in the PRD 13.3 table


def test_status_paused_flag(h):
    hb(h, h.world, paused=True)
    r = call(h, "get_game_status")
    assert ok(r)["game"]["paused"] is True and r["meta"]["paused"] is True   # PRD 8.2 / 16 paused row


def test_status_game_unresponsive_is_a_warning(h):
    """PRD 11.7: fresh heartbeat but main thread tick older than 5 s -> warning, still live."""
    hb(h, h.world, tick_age_s=10)
    r = call(h, "get_game_status")
    ok(r)
    assert r["meta"]["game_state"] == "ready" and "game_unresponsive" in warning_codes(r)


@pytest.mark.parametrize("age,expected", [(5.0, "ready"), (5.5, "observer_unresponsive"), (60.0, "observer_unresponsive")])
def test_status_heartbeat_age_boundary(h, age, expected):
    """PRD 11.7: heartbeat older than 5 s -> observer_unresponsive."""
    hb(h, h.world, written_age_s=age)
    assert ok(call(h, "get_game_status"))["game"]["state"] == expected


@pytest.mark.parametrize("since_start,expected", [(10, "starting"), (59, "starting"), (61, "observer_not_detected"),
                                                  (3600, "observer_not_detected")])
def test_status_grace_period_boundary(h, since_start, expected):
    """PRD 11.7 / 16: no matching heartbeat; 60 s grace from process start reported as starting."""
    h.procs.procs = [game_proc(pid=555, start=h.clock.now() - timedelta(seconds=since_start))]
    d = ok(call(h, "get_game_status"))
    assert d["game"]["state"] == expected and d["game"]["running"] is True


def test_status_heartbeat_older_than_process_start(h):
    """PRD 11.7 row 2: same pid but heartbeat written before the process started -> observer_not_detected."""
    h.procs.procs = [game_proc(pid=bf.PID, start=h.clock.now() - timedelta(seconds=120))]
    hb(h, h.world, written_age_s=200)
    assert ok(call(h, "get_game_status"))["game"]["state"] == "observer_not_detected"


def test_status_reports_disabled_sections(h):
    """PRD 14.1 observer.disabled_sections (TROUBLESHOOTING: section_unavailable -> see this field)."""
    sec = {"section": "routes_player", "error_signature": "NullReferenceException@X", "reason": "failed_repeatedly"}
    hb(h, h.world, disabled_sections=[sec], degraded=True)
    d = ok(call(h, "get_game_status"))
    assert d["observer"]["disabled_sections"] == [sec] and d["observer"]["degraded"] is True


# ---------------------------------------------------------------- get_game_status: parameters

@pytest.mark.parametrize("args", [{"fresh": True}, {"fresh": False}, {"limit": 5}, {"cursor": "x"}, {"query": "gaz"},
                                  {"set_max_send": 5}, {"": 1}])
def test_status_rejects_parameters(h, args):
    """PRD 14.1: no params; PRD 13.2: `fresh` only on tools whose scope is not none."""
    err(call(h, "get_game_status", args), "invalid_argument")
    assert not (h.exchange / "refresh-request.json").exists()


def test_status_allow_stale_is_ambiguous_but_enveloped(h):
    # PRD-ambiguity: 13.2 lists allow_stale with fresh for non-`none` scopes only; no expectation beyond the envelope.
    call(h, "get_game_status", {"allow_stale": True})


@pytest.mark.parametrize("args", [[1], "x", 5, True])
def test_status_non_object_arguments(h, args):
    err(raw_call(h, "get_game_status", args), "invalid_argument")


def test_status_none_arguments(h):
    ok(raw_call(h, "get_game_status", None))


def test_status_is_identical_across_restart_and_repeats(h):
    """PRD 13.1: restarting the server with the same files yields identical answers."""
    a = call(h, "get_game_status")
    b = call(h, "get_game_status")
    h.restart()
    c = call(h, "get_game_status")
    assert a == b == c


def test_status_never_writes_in_the_exchange_dir(h):
    before = sorted(p.name for p in h.exchange.iterdir())
    for state in ("ready", "menu", "game_not_running"):
        enter(h, h.world, state)
        call(h, "get_game_status")
    assert sorted(p.name for p in h.exchange.iterdir()) == before


def test_status_with_fake_observer_lifecycle(tmp_path, clock, procs):
    """Drive a full menu -> loading -> ready -> quickload -> unsupported sequence with the fake observer."""
    d = tmp_path / "obs"
    d.mkdir()
    hh = Harness(d, clock, procs)
    obs = FakeObserver(d, clock, procs)
    obs.start_process(clock.now() - timedelta(minutes=5))
    seen = []
    for step in (obs.menu, obs.loading, obs.ready, obs.loading, lambda: obs.ready(bf.WORLD_SESSION_2), obs.menu,
                 obs.unsupported_build):
        step()
        r = call(hh, "get_game_status")
        seen.append(ok(r)["game"]["state"])
        assert r["meta"]["game_state"] == seen[-1]
    assert seen == ["menu", "loading", "ready", "loading", "ready", "menu", "unsupported_build"]
    obs.kill_process()
    assert ok(call(hh, "get_game_status"))["game"]["running"] is False


# ============================================================================ search: contract and matching

def test_search_valid_call_meta(h):
    r = call(h, "search", VALID_CALLS["search"])
    ok(r)
    m = r["meta"]
    assert m["game_state"] == "ready" and m["source"] == "live_snapshot" and m["stale"] is False
    assert m["world_session"] == WS and m["snapshot"]["family"] == "state" and m["snapshot"]["seq"] == 10
    assert r["page"]["total"] == len(results(r)) and r["page"]["next_cursor"] is None


@pytest.mark.parametrize("query,expected_id", [
    ("gaz", "product:Gas"),                                     # French display name
    ("GAZ", "product:Gas"),                                     # case
    ("Gas", "product:Gas"),                                     # English / asset name
    ("product:Gas", "product:Gas"),                             # id
    ("Produits chimiques", "product:Chemicals"),
    ("PRODUITS CHIMIQUES", "product:Chemicals"),
    ("Peintures avancées", "recipe:PaintsAdvanced"),            # accent in the query, none in the data
    ("Advanced paints", "recipe:PaintsAdvanced"),               # English
    ("PaintsAdvanced", "recipe:PaintsAdvanced"),                # asset
    ("usine pétrochimique", "building_type:PetrochemicalFactory"),
    ("Petrochemical Plant", "building_type:PetrochemicalFactory"),
    ("PetrochemicalFactory", "building_type:PetrochemicalFactory"),
    ("usine petrochimique 1", f"building:{bf.B_PC1}"),          # accent in the data, none in the query
    ("USINE PÉTROCHIMIQUE 1", f"building:{bf.B_PC1}"),
    ("Usine Pétrochimique 1", f"building:{bf.B_PC1}"),    # decomposed (NFD) accent in the query
    ("entrepot 1", f"building:{bf.B_WH}"),
    ("epicerie generale", f"building:{bf.S_GS}"),               # shop
    (f"building:{bf.B_PF1}", f"building:{bf.B_PF1}"),
    ("Saint-Eloi", f"city:{bf.CITY_SEL}"),
    ("saint-éloi", f"region:{bf.R_SEL}"),
    ("Valmont", f"city:{bf.CITY_VAL}"),
    ("Greyhollow", f"region:{bf.R_GRE}"),
    ("acme industries", f"company:{bf.PLAYER}"),
    ("Borealis Corp", f"company:{bf.AI_B}"),
    ("Pétrochimie", "tech:Petrochemistry"),
    ("Petrochemistry", "tech:Petrochemistry"),
])
def test_search_exact_match_case_and_accent_insensitive(h, query, expected_id):
    """PRD 13.6: ids, French display names (case/accent-insensitive), English names and asset names are indexed."""
    r = call(h, "search", {"query": query})
    found = by_id(r)
    assert expected_id in found, (query, list(found))
    assert found[expected_id]["match_kind"] == "exact"


def test_search_exact_results_really_match(h):
    """match_kind exact means the query equals one indexed name (PRD 13.6) of that entity."""
    for q in ("gaz", "usine de peinture 1", "quincaillerie", "Valmont"):
        for row in results(call(h, "search", {"query": q, "limit": 50})):
            if row["match_kind"] != "exact":
                continue
            names = {norm(row["display_name"]), norm(row["english_name"]), norm(row["id"]), norm(row["id"].split(":", 1)[1])}
            if row["kind"] == "shop":
                names.add(norm(row["id"].split(":", 1)[1].split("@")[0]))  # asset (prefab) name of the shop
            assert norm(q) in names, (q, row)


def test_search_multiple_exact_matches_are_all_listed(h):
    """Search returns results[] (PRD 14.1); same-named entities are listed, the caller disambiguates by id."""
    found = by_id(call(h, "search", {"query": "usine de peinture 1"}))
    assert found[f"building:{bf.B_PF1}"]["match_kind"] == "exact"
    assert found[f"building:{bf.AI_PF}"]["match_kind"] == "exact"


def test_search_prefix_match(h):
    found = by_id(call(h, "search", {"query": "Chemi"}))
    assert found["product:Chemicals"]["match_kind"] == "prefix"


@pytest.mark.parametrize("query,expected_id", [("Peintrue", "product:Paint"), ("Valmnot", f"city:{bf.CITY_VAL}"),
                                               ("Borealis Crop", f"company:{bf.AI_B}")])
def test_search_fuzzy_match(h, query, expected_id):
    """PRD 13.6: fuzzy matching is used in search (only)."""
    found = by_id(call(h, "search", {"query": query}))
    assert expected_id in found, list(found)
    assert found[expected_id]["match_kind"] == "fuzzy"


def test_search_result_fields_for_live_entities(h):
    """PRD 14.1 result fields: id, kind, display_name, english_name, owner, city, coordinates, match_kind, score."""
    found = by_id(call(h, "search", {"query": "usine de peinture 1"}))
    b = found[f"building:{bf.B_PF1}"]
    assert b["kind"] == "building" and b["display_name"] == "USINE DE PEINTURE 1"
    assert b["owner"]["actor_id"] == bf.PLAYER and b["owner"]["kind"] == "company"
    assert b["city"]["id"] == f"city:{bf.CITY_VAL}" and b["coordinates"] == {"x": 55, "y": 40}
    ai = found[f"building:{bf.AI_PF}"]
    assert ai["owner"]["actor_id"] == bf.AI_B and ai["coordinates"] == {"x": 150, "y": 120}
    shop = by_id(call(h, "search", {"query": "quincaillerie"}))[f"building:{bf.S_HW1}"]
    assert shop["kind"] == "shop" and shop["owner"]["kind"] == "city" and shop["owner"]["actor_id"] == bf.CITY_VAL
    assert shop["city"]["id"] == f"city:{bf.CITY_VAL}" and shop["coordinates"] == {"x": 100, "y": 100}


def test_search_result_fields_for_static_entities(h):
    p = by_id(call(h, "search", {"query": "gaz"}))["product:Gas"]
    assert p["kind"] == "product" and p["display_name"] == "Gaz" and p["english_name"] == "Gas"
    assert p["owner"] is None and p["coordinates"] is None
    assert isinstance(p["score"], (int, float))


@pytest.mark.parametrize("query", ["zzzzqqqqxxxx", "☃☃☃", "0000-not-a-thing-0000"])
def test_search_no_match(h, query):
    r = call(h, "search", {"query": query})
    assert results(r) == [] and r["page"] == {**r["page"], "total": 0, "next_cursor": None}


@pytest.mark.parametrize("query", ["   ", "\t", "\n\n", " ", "́", "​"])
def test_search_whitespace_or_invisible_query(h, query):
    # PRD 13.2: a whitespace-only text argument (NBSP included) is invalid_argument. Invisible characters that
    # are not whitespace (combining accent, zero-width space) are a valid query that matches nothing.
    r = call(h, "search", {"query": query})
    if query.strip():
        assert results(r) == [] and r["page"]["total"] == 0
    else:
        err(r, "invalid_argument")


def test_search_surrounding_whitespace(h):
    r = call(h, "search", {"query": "  gaz  "})
    if r["ok"]:
        assert "product:Gas" in ids(r)


@pytest.mark.parametrize("length", [1, 199, 200, 201, 1000, 20000])
def test_search_long_queries(h, length):
    # PRD is silent on a max query length; a rejection must be invalid_argument, never a crash.
    r = call(h, "search", {"query": ("Peinture " * (length // 9 + 1))[:length]})
    if not r["ok"]:
        err(r, "invalid_argument")


@pytest.mark.parametrize("query", ["\U0001F3ED usine", "‮erutniep", "a\x00b", "<script>alert(1)</script>", "%s%n",
                                   "'; DROP TABLE x; --", "ＧＡＺ", "STRASSE", "straße", "Œufs", "퟿"])
def test_search_odd_unicode_queries(h, query):
    r = call(h, "search", {"query": query})
    if not r["ok"]:
        err(r, "invalid_argument")


def test_search_ligature_and_eszett_case_insensitive(h):
    """Case-insensitivity (PRD 13.6) for Œ/œ and ß: same name in two cases is an exact match."""
    w = copy.deepcopy(h.world)
    w["state"]["data"]["buildings_ai"].append(ai_row("PaintFactory@151,121", "ŒUFS GROẞE STRAẞE"))
    put_state(h, w)
    for q in ("œufs große straße", "ŒUFS GROẞE STRAẞE", "Œufs Große Straße"):
        assert by_id(call(h, "search", {"query": q})).get("building:PaintFactory@151,121", {}).get("match_kind") == "exact", q


# ---------------------------------------------------------------- search: parameters

@pytest.mark.parametrize("args", [
    {}, {"query": ""}, {"query": None}, {"query": 5}, {"query": ["gaz"]}, {"query": True}, {"query": {"q": "gaz"}},
    {"query": "gaz", "kinds": "product"}, {"query": "gaz", "kinds": ["vehicle"]}, {"query": "gaz", "kinds": ["Product"]},
    {"query": "gaz", "kinds": [None]}, {"query": "gaz", "kinds": [1]}, {"query": "gaz", "kinds": {"product": True}},
    {"query": "gaz", "owner": 1}, {"query": "gaz", "owner": ["player"]}, {"query": "gaz", "owner": None},
    {"query": "gaz", "limit": 51}, {"query": "gaz", "limit": "10"}, {"query": "gaz", "limit": 2.5},
    {"query": "gaz", "limit": True}, {"query": "gaz", "limit": None}, {"query": "gaz", "limit": 10 ** 12},
    {"query": "gaz", "cursor": 5}, {"query": "gaz", "fresh": "yes"}, {"query": "gaz", "fresh": 1},
    {"query": "gaz", "allow_stale": "true"}, {"query": "gaz", "allow_stale": 0},
    {"query": "gaz", "set_max_send": 5}, {"query": "gaz", "kind": "product"}, {"query": "gaz", "fuzzy": False},
    {"query": "gaz", "sort": "score"}, {"query": "gaz", "Query": "gaz"},
])
def test_search_parameter_validation(h, args):
    """PRD 13.3 invalid_argument for parameter validation (types, enums, limit max 50, unknown params)."""
    err(call(h, "search", args), "invalid_argument")
    assert not (h.exchange / "refresh-request.json").exists()


@pytest.mark.parametrize("args", [{"query": "gaz", "limit": 0}, {"query": "gaz", "limit": -1},
                                  {"query": "gaz", "fields": "full"}, {"query": "gaz", "fields": "compact"},
                                  {"query": "gaz", "sort": "score"}])
def test_search_rejects_parameters_outside_its_contract(h, args):
    # PRD 13.2: limit is 1-50; `fields` and `sort` are not search parameters.
    err(call(h, "search", args), "invalid_argument")


@pytest.mark.parametrize("args", [{"query": "gaz", "kinds": []}, {"query": "gaz", "kinds": ["product"] * 10},
                                  {"query": "gaz", "owner": "all"}])
def test_search_parameters_the_prd_does_not_settle(h, args):
    # PRD-ambiguity: empty kinds, duplicate kinds, owner "all".
    r = call(h, "search", args)
    if not r["ok"]:
        err(r, "invalid_argument")


@pytest.mark.parametrize("args", [[], [1], "gaz", 3])
def test_search_non_object_arguments(h, args):
    err(raw_call(h, "search", args), "invalid_argument")


@pytest.mark.parametrize("kind,query", [("building", "usine"), ("building_type", "usine"), ("product", "gaz"),
                                        ("recipe", "peintures"), ("city", "valmont"), ("region", "greyhollow"),
                                        ("company", "borealis"), ("tech", "petrochimie"), ("shop", "quincaillerie")])
def test_search_kinds_filter_each_kind(h, kind, query):
    """PRD 14.1 kinds[] filter: only entities of the requested kind come back (a shop is a building)."""
    allowed = {"building": {"building", "shop"}, "tech": {"tech", "tech_tree"}}.get(kind, {kind})
    r = call(h, "search", {"query": query, "kinds": [kind], "limit": 50})
    kinds = {row["kind"] for row in results(r)}
    assert kinds and kinds <= allowed, kinds


def test_search_kinds_filter_broad_query(h):
    for kind in SEARCH_KINDS:
        allowed = {"building": {"building", "shop"}, "tech": {"tech", "tech_tree"}}.get(kind, {kind})
        cursor, seen = None, []
        while True:
            args = {"query": "e", "kinds": [kind], "limit": 50}
            if cursor:
                args["cursor"] = cursor
            r = call(h, "search", args)
            seen.extend(row["kind"] for row in results(r))
            cursor = r["page"]["next_cursor"]
            if not cursor:
                break
        assert set(seen) <= allowed, (kind, set(seen))


def test_search_kinds_combination(h):
    r = call(h, "search", {"query": "gaz", "kinds": ["product", "recipe"]})
    assert set(ids(r)) == {"product:Gas", "recipe:Gas"}
    r = call(h, "search", {"query": "gaz", "kinds": ["city", "region"]})
    assert {row["kind"] for row in results(r)} <= {"city", "region"}


def test_search_kinds_filter_does_not_change_matching(h):
    """A kinds filter only removes rows: the remaining rows are the same as in the unfiltered answer."""
    full = by_id(call(h, "search", {"query": "peinture", "limit": 50}))
    for kind in SEARCH_KINDS:
        for row in results(call(h, "search", {"query": "peinture", "kinds": [kind], "limit": 50})):
            assert full[row["id"]]["match_kind"] == row["match_kind"] and full[row["id"]]["score"] == row["score"]


@pytest.mark.parametrize("owner,actors", [("player", {bf.PLAYER}), ("Player", {bf.PLAYER}), ("PLAYER ", {bf.PLAYER}),
                                          ("ai", {bf.AI_B, bf.AI_C}), ("Borealis Corp", {bf.AI_B}),
                                          ("borealis corp", {bf.AI_B}), (f"company:{bf.AI_C}", {bf.AI_C}),
                                          ("Cobalt Works", {bf.AI_C}), ("Acme Industries", {bf.PLAYER})])
def test_search_owner_filter(h, owner, actors):
    """PRD 14.1 owner filter (player / ai / company id or name)."""
    for q in ("usine", "siege", "puits de gaz 1", "acme", "borealis", "cobalt"):
        for row in results(call(h, "search", {"query": q, "owner": owner, "limit": 50})):
            if row["kind"] == "company":
                assert int(row["id"].split(":")[1]) in actors, (owner, row)
            else:
                assert row["owner"] is not None and row["owner"]["actor_id"] in actors, (owner, row)
    assert results(call(h, "search", {"query": "usine", "owner": owner, "limit": 50}))


def test_search_owner_filter_keeps_the_owner_entities(h):
    player = set(ids(call(h, "search", {"query": "usine", "owner": "player", "limit": 50})))
    everyone = set(ids(call(h, "search", {"query": "usine", "limit": 50})))
    assert f"building:{bf.B_PF1}" in player and f"building:{bf.AI_PF}" not in player and player < everyone


def test_search_owner_and_kinds_combined(h):
    r = call(h, "search", {"query": "quincaillerie", "owner": "player", "kinds": ["shop"]})
    assert results(r) == []                      # shops belong to cities
    r = call(h, "search", {"query": "usine", "owner": "ai", "kinds": ["building"], "limit": 50})
    assert results(r) and all(row["owner"]["actor_id"] in (bf.AI_B, bf.AI_C) for row in results(r))


@pytest.mark.parametrize("owner", ["Nobody Ltd", "company:99", f"city:{bf.CITY_VAL}", "building:Nope@1,1", "Valmont"])
def test_search_owner_not_found(h, owner):
    """PRD 13.3 not_found: id or name not resolvable (a city is not a company)."""
    e = err(call(h, "search", {"query": "usine", "owner": owner}), ("not_found", "invalid_argument"))
    assert e["code"] == "not_found" or owner.startswith("city:")


@pytest.mark.parametrize("owner", ["", "   "])
def test_search_owner_blank(h, owner):
    # PRD 13.2: a blank owner is invalid_argument, never "no filter".
    err(call(h, "search", {"query": "usine", "owner": owner}), "invalid_argument")


def test_search_owner_ambiguous_name(h):
    """PRD 13.6 / 13.3: a name matching several entities returns `ambiguous` with candidates (max 10)."""
    w = copy.deepcopy(h.world)
    for c in w["state"]["data"]["companies"]:
        if c["actor_id"] == bf.AI_C:
            c["name"] = "Borealis Corp"
    put_state(h, w)
    e = err(call(h, "search", {"query": "usine", "owner": "BOREALIS CORP"}), "ambiguous")
    assert {c["id"] for c in e["candidates"]} == {f"company:{bf.AI_B}", f"company:{bf.AI_C}"}
    assert len(e["candidates"]) <= 10
    # the id still resolves
    rows = results(call(h, "search", {"query": "usine", "owner": f"company:{bf.AI_C}"}))
    assert rows and all(r["owner"]["actor_id"] == bf.AI_C for r in rows)


# ---------------------------------------------------------------- search: pagination (PRD 13.2: default 25, max 50)

def _walk(h, args: dict, limit: int) -> tuple[list[str], int, int]:
    out, cursor, pages, total = [], None, 0, None
    while True:
        a = dict(args, limit=limit)
        if cursor:
            a["cursor"] = cursor
        r = call(h, "search", a)
        rows = ids(r)
        if total is None:
            total = r["page"]["total"]
        assert r["page"]["total"] == total
        out.extend(rows)
        pages += 1
        cursor = r["page"]["next_cursor"]
        if not cursor:
            break
        assert rows, "a page with a next cursor must not be empty"
        assert pages < 200
    return out, total, pages


def test_search_default_limit_is_25(h):
    r = call(h, "search", {"query": "a"})
    assert r["page"]["total"] > 25
    assert len(results(r)) == 25 and r["page"]["next_cursor"]


def test_search_limit_bounds(h):
    assert len(results(call(h, "search", {"query": "a", "limit": 1}))) == 1
    r = call(h, "search", {"query": "a", "limit": 50})
    assert len(results(r)) == min(50, r["page"]["total"])


def test_search_pagination_round_trip(h):
    pages10, total, n10 = _walk(h, {"query": "a"}, 10)
    pages50, total50, n50 = _walk(h, {"query": "a"}, 50)
    pages7, _, _ = _walk(h, {"query": "a"}, 7)
    assert total == total50 == len(pages10) and n10 == -(-total // 10) and n50 == -(-total // 50)
    assert pages10 == pages50 == pages7
    assert len(set(pages10)) == len(pages10)                    # no duplicates
    assert pages10 == ids(call(h, "search", {"query": "a", "limit": 50})) + \
        ids(call(h, "search", {"query": "a", "limit": 50,
                               "cursor": call(h, "search", {"query": "a", "limit": 50})["page"]["next_cursor"]}))


def test_search_single_page_has_no_cursor(h):
    r = call(h, "search", {"query": "gaz", "limit": 50})
    assert r["page"]["next_cursor"] is None and r["page"]["total"] == len(results(r))


@pytest.mark.parametrize("other", [{"query": "e"}, {"query": "a", "kinds": ["product"]}, {"query": "a", "owner": "player"},
                                   {"query": "A"}, {"query": "a "}])
def test_search_cursor_from_different_arguments_rejected(h, other):
    cur = call(h, "search", {"query": "a", "limit": 10})["page"]["next_cursor"]
    assert cur
    err(call(h, "search", {**other, "limit": 10, "cursor": cur}), "invalid_argument")


def test_search_cursor_from_another_tool_rejected(h):
    cur = call(h, "list_routes", {"limit": 5})["page"]["next_cursor"]
    assert cur
    err(call(h, "search", {"query": "a", "cursor": cur}), "invalid_argument")
    scur = call(h, "search", {"query": "a", "limit": 10})["page"]["next_cursor"]
    r = h.call("list_routes", {"limit": 5, "cursor": scur})
    check_response("list_routes", r)
    assert r["error"]["code"] == "invalid_argument"


@pytest.mark.parametrize("cursor", ["garbage", "djF8MTB8", "====", "éé", "v1|10|abc", "x" * 5000,
                                    "djF8LTF8MDAwMDAwMDAwMDAw"])
def test_search_invalid_cursor(h, cursor):
    err(call(h, "search", {"query": "a", "cursor": cursor}), "invalid_argument")


def test_search_cursor_with_another_limit(h):
    cur = call(h, "search", {"query": "a", "limit": 10})["page"]["next_cursor"]
    r = call(h, "search", {"query": "a", "limit": 20, "cursor": cur})
    if r["ok"]:
        assert ids(r) == _walk(h, {"query": "a"}, 10)[0][10:30]


def test_search_cursor_survives_server_restart(h):
    """PRD 13.1: identical answers from the same files after a restart; an opaque cursor keeps working."""
    first = call(h, "search", {"query": "a", "limit": 10})
    second = call(h, "search", {"query": "a", "limit": 10, "cursor": first["page"]["next_cursor"]})
    h.restart()
    again = call(h, "search", {"query": "a", "limit": 10, "cursor": first["page"]["next_cursor"]})
    assert again == second


def test_search_is_deterministic_across_calls_and_restarts(h):
    queries = [{"query": "a", "limit": 50}, {"query": "usine de peinture 1"}, {"query": "e", "kinds": ["building"], "limit": 50},
               {"query": "quincaillerie"}, {"query": "Peintrue"}]
    first = [call(h, "search", q) for q in queries]
    assert [call(h, "search", q) for q in queries] == first
    h.restart()
    assert [call(h, "search", q) for q in queries] == first


def test_search_size_cap_truncates_and_paginates(h):
    """PRD 14.9: responses stay <= ~30 KB; larger results paginate/truncate with next_cursor + `truncated`."""
    w = copy.deepcopy(h.world)
    for i in range(60):
        w["state"]["data"]["buildings_ai"].append(ai_row(f"PaintFactory@{400 + i},{400 + i}", f"ZQX {i:02d} " + "É" * 1500))
    put_state(h, w)
    seen, cursor, truncated = [], None, False
    for _ in range(100):
        args = {"query": "zqx", "limit": 50}
        if cursor:
            args["cursor"] = cursor
        r = call(h, "search", args)                 # check_response asserts the 30 KB cap
        rows = ids(r)
        if r["page"]["next_cursor"] and len(rows) < 50 and len(seen) + len(rows) < r["page"]["total"]:
            truncated = truncated or "truncated" in warning_codes(r)
        seen.extend(rows)
        cursor = r["page"]["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 60
    assert truncated


# ---------------------------------------------------------------- search: data changes

def test_search_sees_renamed_entity_after_new_snapshot(h):
    """PRD 13.1: the server reloads state when its seq/hash changes; renamed entities become resolvable."""
    assert "building:" + bf.B_PF2 not in ids(call(h, "search", {"query": "Atelier Arc-en-ciel"}))
    w = copy.deepcopy(h.world)
    for b in w["state"]["data"]["buildings_player"]:
        if b["key"] == bf.B_PF2:
            b["display_name"] = "ATELIER ARC-EN-CIEL"
    put_state(h, w)
    found = by_id(call(h, "search", {"query": "atelier arc-en-ciel"}))
    assert found[f"building:{bf.B_PF2}"]["match_kind"] == "exact"
    assert f"building:{bf.B_PF2}" not in by_id(call(h, "search", {"query": "usine de peinture 2"})) or \
        by_id(call(h, "search", {"query": "usine de peinture 2"}))[f"building:{bf.B_PF2}"]["match_kind"] != "exact"


@pytest.mark.parametrize("display", ["", "   "])
def test_search_with_blank_display_names(h, display):
    """A building with a blank name stays resolvable by its id (PRD 13.6 indexes ids)."""
    w = copy.deepcopy(h.world)
    w["state"]["data"]["buildings_ai"].append(ai_row("PaintFactory@151,121", display))
    put_state(h, w)
    r = call(h, "search", {"query": "building:PaintFactory@151,121"})
    assert by_id(r)["building:PaintFactory@151,121"]["match_kind"] == "exact"
    call(h, "search", {"query": "a", "limit": 50})


def test_search_english_names_unavailable_falls_back_to_asset_names(h):
    """docs/KNOWN-LIMITATIONS.md U-EN: english_name null + warning english_name_unavailable; search falls back
    to asset names."""
    w = copy.deepcopy(h.world)
    w["static"]["data"]["english_names_available"] = False
    for coll in ("products", "recipes", "building_types", "tech_unlocks", "tech_trees", "bill_categories"):
        for item in w["static"]["data"][coll]:
            item["english_name"] = None
    put(h, "static", w["static"])
    r = call(h, "search", {"query": "Paint"})
    found = by_id(r)
    assert found["product:Paint"]["match_kind"] == "exact" and found["product:Paint"]["english_name"] is None
    assert "english_name_unavailable" in warning_codes(r)


# ============================================================================ search: freshness and lifecycle

@pytest.mark.parametrize("extra,stale", [(13.0, False), (13.5, True), (60.0, True)])
def test_search_stale_by_age_boundary(h, extra, stale):
    """PRD 11.7: current iff age <= max(15 s, 3 x effective_interval_s); 13.4: live but stale by age -> data with
    stale: true, stale_reason: age."""
    h.clock.advance(extra)                     # state captured 2 s before the base time
    hb(h, h.world)
    r = call(h, "search", {"query": "Valmont"})
    assert f"city:{bf.CITY_VAL}" in ids(r)
    assert r["meta"]["game_state"] == "ready" and r["meta"]["stale"] is stale
    if stale:
        assert r["meta"]["stale_reason"] == "age" and r["meta"]["source"] != "live_snapshot"
    else:
        assert r["meta"]["source"] == "live_snapshot"


def test_search_stale_limit_scales_with_effective_interval(h):
    h.clock.advance(20)                        # age 22 s <= 3 x 10 s
    hb(h, h.world, effective_interval_s=10.0)
    r = call(h, "search", {"query": "Valmont"})
    assert r["meta"]["stale"] is False and f"city:{bf.CITY_VAL}" in ids(r)


def test_search_age_measured_from_last_verified(h):
    """PRD 11.7: age from max(captured.utc_end, last_verified_utc)."""
    h.clock.advance(60)
    w = h.world
    doc = bf.build_heartbeat(h.clock.now(), static_doc=w["static"], state_doc=w["state"], history_doc=w["history"])
    doc["data"]["families"]["state"]["last_verified_utc"] = bf.iso(h.clock.now() - timedelta(seconds=1))
    put(h, "heartbeat", doc)
    r = call(h, "search", {"query": "Valmont"})
    assert r["meta"]["stale"] is False and r["meta"]["source"] == "live_snapshot"


def test_search_stale_age_with_allow_stale(h):
    h.clock.advance(30)
    hb(h, h.world)
    r = call(h, "search", {"query": "Valmont", "allow_stale": True})
    assert r["meta"]["stale"] is True and r["meta"]["stale_reason"] == "age" and f"city:{bf.CITY_VAL}" in ids(r)


@pytest.mark.parametrize("state", NOT_LIVE)
def test_search_not_live_without_allow_stale_never_silently_stale(h, state):
    """PRD 13.4: never silently stale. Not live -> lifecycle error, or (search also indexes static names, 13.7)
    an answer that does not present snapshot data as live."""
    expected = enter(h, h.world, state)
    r = call(h, "search", {"query": "Valmont"})
    assert r["meta"]["game_state"] == expected
    if r["ok"]:
        assert r["meta"]["source"] != "live_snapshot"
        if any(row["kind"] in LIVE_KINDS for row in results(r)):
            assert r["meta"]["stale"] is True
        assert "product:Gas" in ids(call(h, "search", {"query": "gaz"}))   # static names are always indexed
    else:
        err(r, (LIFECYCLE_ERROR[expected], "snapshot_unavailable"))


@pytest.mark.parametrize("state", NOT_LIVE)
def test_search_not_live_with_allow_stale_returns_flagged_snapshot(h, state):
    """PRD 13.4: with allow_stale the last snapshot comes back with source stale_snapshot, stale true and the
    reason; PRD 16 names not_in_game for menu/loading; 11.7 vocabulary for the others."""
    expected = enter(h, h.world, state)
    r = call(h, "search", {"query": "Valmont", "allow_stale": True})
    assert f"city:{bf.CITY_VAL}" in ids(r)
    m = r["meta"]
    assert m["game_state"] == expected and m["source"] == "stale_snapshot" and m["stale"] is True
    reason = {"menu": "not_in_game", "loading": "not_in_game", "game_not_running": "game_not_running",
              "observer_unresponsive": "observer_unresponsive"}.get(state)
    if reason:
        assert m["stale_reason"] == reason
    assert "stale" in warning_codes(r)


@pytest.mark.parametrize("allow_stale", [False, True])
def test_search_unsupported_build(h, allow_stale):
    """PRD 16: unsupported build -> unsupported_build for runtime and static tools alike."""
    enter(h, h.world, "unsupported_build")
    r = call(h, "search", {"query": "gaz", "allow_stale": allow_stale})
    err(r, "unsupported_build")
    assert r["meta"]["compatibility"] == "unsupported_build"


def test_search_paused(h):
    hb(h, h.world, paused=True)
    r = call(h, "search", {"query": "Valmont"})
    assert r["meta"]["paused"] is True and r["meta"]["stale"] is False and f"city:{bf.CITY_VAL}" in ids(r)


def test_search_game_unresponsive_warning(h):
    hb(h, h.world, tick_age_s=10)
    r = call(h, "search", {"query": "Valmont"})
    assert "game_unresponsive" in warning_codes(r) and f"city:{bf.CITY_VAL}" in ids(r)


def test_search_quickload_world_session_changed(h):
    """PRD 16 quickload: the old-session snapshot is never served as current; with allow_stale it is flagged
    world_session_changed."""
    hb(h, h.world, world_session=bf.WORLD_SESSION_2)
    r = call(h, "search", {"query": "Valmont"})
    if r["ok"]:
        assert r["meta"]["source"] != "live_snapshot"
        assert not any(row["kind"] in LIVE_KINDS for row in results(r)) or r["meta"]["stale"] is True
    else:
        err(r, "snapshot_unavailable")
    r = call(h, "search", {"query": "Valmont", "allow_stale": True})
    assert f"city:{bf.CITY_VAL}" in ids(r)
    assert r["meta"]["stale"] is True and r["meta"]["stale_reason"] == "world_session_changed"
    assert r["meta"]["source"] == "stale_snapshot"


def test_search_snapshot_from_another_pid_is_not_current(h):
    """PRD 11.7: current requires snapshot.pid == heartbeat.pid."""
    w = copy.deepcopy(h.world)
    w["state"]["pid"] = 999
    put_state(h, w)
    r = call(h, "search", {"query": "Valmont"})
    assert r["meta"]["source"] != "live_snapshot"
    if r["ok"] and any(row["kind"] in LIVE_KINDS for row in results(r)):
        assert r["meta"]["stale"] is True
    r = call(h, "search", {"query": "Valmont", "allow_stale": True})
    assert r["meta"]["stale"] is True


# ---------------------------------------------------------------- search: missing / invalid files

def test_search_without_state_snapshot(h):
    (h.exchange / "state.json").unlink()
    r = call(h, "search", {"query": "gaz"})
    if r["ok"]:
        assert "product:Gas" in ids(r)                             # PRD 13.7: static names always indexed
        assert not any(row["kind"] in LIVE_KINDS for row in results(r))
        assert ok(r)["unavailable"]
    else:
        err(r, "snapshot_unavailable")


def test_search_without_static_catalogue(h):
    (h.exchange / "static.json").unlink()
    r = call(h, "search", {"query": "Valmont"})
    if r["ok"]:
        assert f"city:{bf.CITY_VAL}" in ids(r)
        assert not any(row["kind"] in STATIC_KINDS for row in results(call(h, "search", {"query": "gaz"})))
    else:
        err(r, "snapshot_unavailable")


def test_search_without_any_snapshot(h):
    for f in ("static", "state", "history"):
        (h.exchange / f"{f}.json").unlink()
    r = call(h, "search", {"query": "gaz"})
    if r["ok"]:
        assert results(r) == []
    else:
        err(r, "snapshot_unavailable")


def test_search_in_empty_exchange_dir(empty_h):
    r = call(empty_h, "search", {"query": "gaz"})
    if r["ok"]:
        assert results(r) == []
    else:
        err(r, ("observer_not_detected", "snapshot_unavailable"))


@pytest.mark.parametrize("family", ["state", "static"])
@pytest.mark.parametrize("text", ["{not json", "", "[]", '{"schema": "roi-mcp/state"}'])
def test_search_with_invalid_file_first_load(h, family, text):
    put_raw(h, family, text)
    r = call(h, "search", {"query": "gaz"})
    if not r["ok"]:
        err(r, "snapshot_unavailable")


@pytest.mark.parametrize("family,query,expected", [("state", "Valmont", f"city:{bf.CITY_VAL}"),
                                                   ("static", "gaz", "product:Gas")])
def test_search_keeps_last_good_snapshot_on_corruption(h, family, query, expected):
    """PRD 16: corrupted/incomplete file -> last good of the same session + warning."""
    assert expected in ids(call(h, "search", {"query": query}))
    put_raw(h, family, '{"schema": "roi-mcp/' + family + '", "truncated')
    r = call(h, "search", {"query": query})
    assert expected in ids(r)
    assert "snapshot_invalid_using_previous" in warning_codes(r)


def test_search_static_schema_major_mismatch(h):
    """PRD 16: schema mismatch -> schema_mismatch with both versions (static tools: same)."""
    w = copy.deepcopy(h.world)
    w["static"]["schema_version"] = "2.0.0"
    put(h, "static", w["static"])
    e = err(call(h, "search", {"query": "gaz"}), "schema_mismatch")
    text = e["message"] + str(e.get("details"))
    assert "2.0.0" in text and "1.0.0" in text


def test_search_state_schema_major_mismatch(h):
    # PRD 13.4 / 14.1: the mismatched state.json is never read; search answers from the static catalogue
    # (source static_catalog) and names the mismatch in `unavailable` (live:state).
    w = copy.deepcopy(h.world)
    w["state"]["schema_version"] = "2.0.0"
    put(h, "state", w["state"])
    r = call(h, "search", {"query": "Valmont"})
    assert not any(row["kind"] in LIVE_KINDS for row in results(r))
    reasons = {u["field"]: u["reason"] for u in ok(r)["unavailable"]}
    assert reasons["live:state"].startswith("schema_mismatch") and "2.0.0" in reasons["live:state"]
    assert r["meta"]["source"] == "static_catalog"


def test_search_static_ref_mismatch_warns(h):
    """PRD 11.4: a state whose static_ref does not match static.json (after one reload) -> warning static_mismatch."""
    w = copy.deepcopy(h.world)
    w["state"]["static_ref"] = {"seq": 99, "content_hash": "f" * 64}
    put_state(h, w)
    r = call(h, "search", {"query": "Valmont"})
    assert f"city:{bf.CITY_VAL}" in ids(r) and "static_mismatch" in warning_codes(r)
    req = h.exchange / "refresh-request.json"
    if req.exists():  # PRD 13.7: only the server's static recovery may use the `static` scope here
        import json
        reqs = json.loads(req.read_text(encoding="utf-8"))["requests"]
        assert {k for k, v in reqs.items() if v is not None} <= {"static"}


def test_search_heartbeat_schema_major_mismatch(h):
    hb(h, h.world, schema_version="2.0.0")
    err(call(h, "search", {"query": "gaz"}), "schema_mismatch")


# ---------------------------------------------------------------- search: fresh (PRD 13.2 / 13.7)

def test_search_fresh_refreshes_exactly_state(tmp_path, clock, procs):
    hh, obs = _observer_harness(tmp_path, clock, procs)
    r = call(hh, "search", {"query": "gaz", "fresh": True})
    ok(r)
    req = obs.read_refresh_request()
    assert req is not None and {k for k, v in req["requests"].items() if v is not None} == {"state"}
    assert "refresh_timeout" not in warning_codes(r)
    assert r["meta"]["snapshot"]["family"] == "state" and r["meta"]["snapshot"]["seq"] == obs.state_seq


def test_search_without_fresh_writes_no_request(tmp_path, clock, procs):
    hh, obs = _observer_harness(tmp_path, clock, procs)
    for args in ({"query": "gaz"}, {"query": "gaz", "fresh": False}, {"query": "gaz", "allow_stale": True}):
        ok(call(hh, "search", args))
    assert obs.read_refresh_request() is None


def test_search_fresh_timeout(tmp_path, clock, procs):
    hh, obs = _observer_harness(tmp_path, clock, procs, wait=1.0, serve=False)
    t0 = clock.monotonic()
    r = call(hh, "search", {"query": "gaz", "fresh": True})
    waited = clock.monotonic() - t0
    assert 1.0 <= waited < 1.5                     # PRD 13.2: waits up to refresh_wait_s, then answers
    assert "product:Gas" in ids(r) and "refresh_timeout" in warning_codes(r)
    assert {k for k, v in obs.read_refresh_request()["requests"].items() if v is not None} == {"state"}


def test_search_fresh_makes_new_entities_resolvable(tmp_path, clock, procs):
    """PRD 13.7: search refreshes `state` so newly built or renamed entities become resolvable."""
    hh, obs = _observer_harness(tmp_path, clock, procs, serve=False)
    original = obs.publish_state

    def publish_with_new_building(data=None, capture_age_s=0.5):
        d = bf.state_data()
        d["buildings_ai"].append(ai_row("PaintFactory@151,121", "NOUVELLE USINE 7"))
        return original(data=d, capture_age_s=capture_age_s)

    before = by_id(call(hh, "search", {"query": "nouvelle usine 7"}))
    assert "building:PaintFactory@151,121" not in before
    obs.publish_state = publish_with_new_building
    clock.on_sleep = lambda c: obs.serve_refresh("state")
    after = by_id(call(hh, "search", {"query": "nouvelle usine 7", "fresh": True}))
    assert after["building:PaintFactory@151,121"]["match_kind"] == "exact"


def test_search_fresh_with_allow_stale_when_live(tmp_path, clock, procs):
    hh, obs = _observer_harness(tmp_path, clock, procs)
    r = call(hh, "search", {"query": "Valmont", "fresh": True, "allow_stale": True})
    assert f"city:{bf.CITY_VAL}" in ids(r) and r["meta"]["stale"] is False


@pytest.mark.parametrize("step", ["menu", "loading", "kill"])
def test_search_fresh_when_not_live(tmp_path, clock, procs, step):
    """PRD 13.7: outside ready, fresh returns the lifecycle error or times out; tools never refresh families not
    listed for them."""
    hh, obs = _observer_harness(tmp_path, clock, procs)
    {"menu": obs.menu, "loading": obs.loading, "kill": obs.kill_process}[step]()
    r = call(hh, "search", {"query": "Valmont", "fresh": True})
    if r["ok"]:
        assert r["meta"]["source"] != "live_snapshot"
    req = obs.read_refresh_request()
    if req is not None:
        assert {k for k, v in req["requests"].items() if v is not None} <= {"state"}


def test_search_fresh_unsupported_build(tmp_path, clock, procs):
    hh, obs = _observer_harness(tmp_path, clock, procs)
    obs.unsupported_build()
    err(call(hh, "search", {"query": "gaz", "fresh": True}), "unsupported_build")
    req = obs.read_refresh_request()
    assert req is None or {k for k, v in req["requests"].items() if v is not None} <= {"state"}
