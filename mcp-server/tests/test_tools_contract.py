"""T-7: MCP contract. Tool list == the 29 PRD tools; parameter validation; response schemas;
pagination; size cap; error codes; meta and provenance; refresh scopes; coalescing; no write semantics."""

import ast
import asyncio
import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import build_fixtures as bf
from conftest import TOOL_SCHEMA_DIR, Harness
from fake_observer import FakeObserver
from roi_mcp import config as cfg
from roi_mcp.errors import ERROR_CODES
from roi_mcp.tools import ADVISOR_TOOL_NAMES, REFRESH_SCOPE, TOOL_NAMES
from roi_mcp.util import json_size

PRD_TOOLS = [
    "get_game_status", "search", "list_companies", "get_company", "get_finances", "list_buildings", "get_building",
    "get_production_overview", "find_production_issues", "list_routes", "get_route", "list_warehouse_requests", "list_vehicles",
    "get_supply_chain", "list_products", "get_product", "list_recipes", "get_recipe", "list_building_types", "get_building_type",
    "list_cities", "get_city", "get_shop", "find_shops", "list_regions", "get_region", "get_market", "get_tech_tree",
    "get_research_state",
]

ROUTE_1 = f"route:{bf.B_GW1}|Gas|{bf.B_PC1}|own|0"

VALID_CALLS = {
    "get_game_status": {},
    "search": {"query": "gaz"},
    "list_companies": {},
    "get_company": {"company": "Borealis Corp"},
    "get_finances": {"months": 3, "group_by": "overview_group"},
    "list_buildings": {"sort": "stock_ratio"},
    "get_building": {"building": f"building:{bf.B_PC1}", "include": ["production", "inventory", "incoming_routes", "outgoing_routes", "history"]},
    "get_production_overview": {"product": "Gas"},
    "find_production_issues": {},
    "list_routes": {"product": "Gas", "fields": "full"},
    "get_route": {"route": ROUTE_1},
    "list_warehouse_requests": {},
    "list_vehicles": {"aggregate": False},
    "get_supply_chain": {"product": "Paint", "target_output_per_30d": 100},
    "list_products": {"fields": "full"},
    "get_product": {"product": "Paint"},
    "list_recipes": {"product": "Paint"},
    "get_recipe": {"recipe": "Chemicals"},
    "list_building_types": {"tag": "factory"},
    "get_building_type": {"building_type": "PaintFactory"},
    "list_cities": {"sort": "tier"},
    "get_city": {"city": "Valmont"},
    "get_shop": {"shop": f"building:{bf.S_HW1}"},
    "find_shops": {"product": "Paint", "from_building": f"building:{bf.B_PF1}"},
    "list_regions": {"owner": "player"},
    "get_region": {"region": "Greyhollow"},
    "get_market": {},
    "get_tech_tree": {"state": "available"},
    "get_research_state": {},
}

VALIDATORS = {n: Draft202012Validator(json.loads((TOOL_SCHEMA_DIR / f"{n}.schema.json").read_text(encoding="utf-8"))) for n in PRD_TOOLS}


def check_response(name: str, resp: dict) -> None:
    errs = list(VALIDATORS[name].iter_errors(resp))
    if errs:
        from jsonschema.exceptions import best_match
        e = best_match(errs)
        raise AssertionError(f"{name}: {'/'.join(map(str, e.absolute_path))}: {e.message[:300]}")
    assert json_size(resp) <= cfg.RESPONSE_SIZE_CAP_BYTES, f"{name}: {json_size(resp)} bytes"
    meta = resp["meta"]
    for k in ("server_version", "schema_versions", "game_state", "compatibility", "world_session", "source", "snapshot",
              "paused", "stale", "stale_reason", "warnings"):
        assert k in meta, (name, k)
    if resp["ok"]:
        prov = resp["data"]["provenance"]
        assert set(prov) == {"observed", "definition", "game_computed", "derived"}
        assert isinstance(resp["data"]["unavailable"], list)


# ------------------------------------------------------------------ tool list and descriptions

def test_registered_tools_equal_prd_list(h):
    # V1.1: the 29 PRD 14 tools first, in PRD order, then the ten advisor tools (docs/v1.1/PRD-ADDENDUM.md 2.1).
    assert list(TOOL_NAMES) == PRD_TOOLS
    assert list(h.app.specs) == PRD_TOOLS + list(ADVISOR_TOOL_NAMES)
    assert len(PRD_TOOLS) == 29


def test_mcp_list_tools_over_protocol(h):
    from mcp import Client

    from roi_mcp.server import build_server

    async def go():
        async with Client(build_server(h.app)) as client:
            tools = (await client.list_tools()).tools
            res = await client.call_tool("get_route", {"route": ROUTE_1})
            return tools, res

    tools, res = asyncio.run(go())
    assert sorted(t.name for t in tools) == sorted(PRD_TOOLS + list(ADVISOR_TOOL_NAMES))
    for t in tools:
        assert t.annotations.read_only_hint is True and t.annotations.destructive_hint is False
        assert "Read-only" in t.description and "meta.stale" in t.description and "user content" in t.description
        props = t.input_schema["properties"]
        assert ("fresh" in props) == (REFRESH_SCOPE[t.name] != "none"), t.name
        assert t.input_schema.get("additionalProperties") is False
    body = json.loads(res.content[0].text)
    assert body["ok"] and body["data"]["route_id"] == ROUTE_1 and res.is_error is False
    check_response("get_route", body)


def test_semantic_statements_in_descriptions(h):
    d = {n: s.description for n, s in h.app.specs.items()}
    for n in ("list_routes", "get_route", "get_building"):
        assert "SHARED" in d[n] and "destination" in d[n].lower() and "0 = unlimited" in d[n]
    assert "per route" in d["list_routes"]
    for n in ("list_companies", "get_company"):
        assert "infinite" in d[n]
    for n in ("get_shop", "find_shops", "get_city", "get_product"):
        assert "units per consumption interval" in d[n]
    assert "NON-AUTHORITATIVE" in d["find_shops"] and "WITHOUT an existing route" in d["find_shops"]
    assert "price history" in d["get_market"].lower()
    assert "session-scoped" in d["list_vehicles"]


def test_refresh_scopes_match_prd_13_7(h):
    v1 = {n: s for n, s in REFRESH_SCOPE.items() if n in TOOL_NAMES}   # advisor scopes: tests/advisor
    assert {n for n, s in v1.items() if s == "none"} == {"get_game_status", "get_recipe"}
    assert {n for n, s in v1.items() if s == "history"} == {"get_finances"}
    assert sum(1 for s in v1.values() if s == "state") == 26
    for n, spec in h.app.specs.items():
        assert spec.scope == REFRESH_SCOPE[n]


# ------------------------------------------------------------------ responses

@pytest.mark.parametrize("name", PRD_TOOLS)
def test_valid_call_matches_response_schema(h, name):
    resp = h.call(name, VALID_CALLS[name])
    assert resp["ok"], resp.get("error")
    check_response(name, resp)
    if name != "get_game_status" and name != "get_recipe":
        assert resp["meta"]["source"] in ("live_snapshot", "static_catalog")
    assert resp["meta"]["game_state"] == "ready" and resp["meta"]["stale"] is False


@pytest.mark.parametrize("name", PRD_TOOLS)
def test_unknown_parameter_rejected(h, name):
    resp = h.call(name, {**VALID_CALLS[name], "set_max_send": 5})
    assert resp["ok"] is False and resp["error"]["code"] == "invalid_argument"
    check_response(name, resp)


@pytest.mark.parametrize("name,args", [
    ("search", {}), ("get_building", {}), ("get_route", {}), ("get_shop", {}), ("find_shops", {}), ("get_city", {}),
    ("get_region", {}), ("get_product", {}), ("get_building_type", {}),
    ("list_routes", {"limit": 0}), ("list_routes", {"limit": 51}), ("list_routes", {"sort": "bogus"}),
    ("list_buildings", {"status": "broken"}), ("get_finances", {"months": "6"}), ("get_supply_chain", {"depth": 13}),
    ("get_supply_chain", {}), ("get_supply_chain", {"product": "Paint", "building": bf.B_PF1}), ("get_recipe", {}),
    ("list_vehicles", {"aggregate": "no"}), ("get_route", {"route": "Gas route"}), ("list_routes", {"cursor": "garbage"}),
    ("get_market", {"include": ["prices"]}), ("search", {"query": ""}),
])
def test_parameter_validation(h, name, args):
    resp = h.call(name, args)
    assert resp["error"]["code"] == "invalid_argument", resp
    check_response(name, resp)


def test_pagination_round_trip(h):
    full = h.call("list_routes", {"limit": 50})["data"]["routes"]
    assert len(full) == 13
    seen, cursor, pages = [], None, 0
    while True:
        args = {"limit": 5}
        if cursor:
            args["cursor"] = cursor
        r = h.call("list_routes", args)
        check_response("list_routes", r)
        seen.extend(x["route_id"] for x in r["data"]["routes"])
        assert r["page"]["total"] == 13
        cursor = r["page"]["next_cursor"]
        pages += 1
        if not cursor:
            break
    assert pages == 3 and seen == [x["route_id"] for x in full]
    first = h.call("list_routes", {"limit": 5})
    bad = h.call("list_routes", {"limit": 5, "product": "Gas", "cursor": first["page"]["next_cursor"]})
    assert bad["error"]["code"] == "invalid_argument"


def test_default_limit_is_25(h):
    w = copy.deepcopy(h.world)
    extra = []
    for i in range(30):
        r = copy.deepcopy(w["state"]["data"]["routes_player"][0])
        r["route_key"] = r["route_key"][:-1] + str(i + 1)
        r["occurrence"] = i + 1
        extra.append(r)
    w["state"]["data"]["routes_player"].extend(extra)
    w["state"]["seq"] = 50
    h.write_world(w, ("state",))
    r = h.call("list_routes")
    assert len(r["data"]["routes"]) == 25 and r["page"]["total"] == 43 and r["page"]["next_cursor"]


def _big_world(h, n_routes=600):
    w = copy.deepcopy(h.world)
    routes = w["state"]["data"]["routes_player"]
    base = routes[0]
    for i in range(n_routes):
        r = copy.deepcopy(base)
        r["occurrence"] = i + 1
        r["route_key"] = f"{base['origin']}|Gas|{base['destination']}|own|{i + 1}"
        routes.append(r)
    w["state"]["seq"] = 77
    h.write_world(w, ("state",))


def test_size_cap_truncates_with_warning(h):
    _big_world(h)
    r = h.call("list_routes", {"limit": 50, "fields": "full"})
    check_response("list_routes", r)
    assert "truncated" in [w["code"] for w in r["meta"]["warnings"]]
    n = len(r["data"]["routes"])
    assert 0 < n < 50 and r["page"]["next_cursor"]
    r2 = h.call("list_routes", {"limit": 50, "fields": "full", "cursor": r["page"]["next_cursor"]})
    assert r2["data"]["routes"][0]["route_id"] != r["data"]["routes"][-1]["route_id"]
    g = h.call("get_building", {"building": bf.B_GW1})
    check_response("get_building", g)
    assert "truncated" in [w["code"] for w in g["meta"]["warnings"]]


# ------------------------------------------------------------------ provenance and semantics

def test_route_semantics_in_outputs(h):
    r = h.call("get_route", {"route": ROUTE_1})["data"]
    assert r["max_send"]["scope"] == "destination_product_shared" and r["max_send"]["value"] == 8
    assert r["min_keep"] == {"value": 1, "keep_all": False, "ui_label_validated": False}
    shared = h.call("list_routes", {"destination": bf.B_PC1, "product": "Gas"})["data"]["routes"]
    assert {x["max_send"]["value"] for x in shared} == {8} and len(shared) == 3
    assert any(x["dormant"] for x in shared)
    keep_all = h.call("get_route", {"route": f"route:{bf.B_GW2}|Gas|{bf.B_PC2}|own|0"})["data"]
    assert keep_all["min_keep"]["keep_all"] is True and keep_all["max_send"]["unlimited"] is True
    auto = h.call("get_route", {"route": f"route:{bf.B_PF1}|Paint|{bf.S_HW1}|own|0"})["data"]
    assert auto["max_send"]["mode"] == "auto_shop_demand"
    resp = h.call("get_route", {"route": ROUTE_1})
    assert "ui_label_unvalidated" in [w["code"] for w in resp["meta"]["warnings"]]
    assert r["cost_per_unit_at_capacity"] == round(r["dispatch_cost"] / r["vehicle_capacity"], 4)
    assert r["straight_line_tiles"]["estimate"] is True
    assert r["vehicles_assigned"] and r["vehicles_assigned"][0]["id"].startswith(f"vehicle:{bf.WORLD_SESSION}:")


def test_find_shops_never_shows_both(h):
    rows = h.call("find_shops", {"product": "Paint", "from_building": bf.B_PF1})["data"]["shops"]
    for row in rows:
        assert (row["existing_route"] is None) != (row["straight_line_cost_estimate"] is None)
    by = {r["shop"]: r for r in rows}
    hw1 = by[f"building:{bf.S_HW1}"]
    assert hw1["existing_route"]["distance_tiles"] == 75 and hw1["existing_route"]["dispatch_cost"] == 1250.0
    est = by[f"building:{bf.S_HW2}"]["straight_line_cost_estimate"]
    assert est["authoritative"] is False and est["route_exists"] is False and est["value"] == pytest.approx(2587.53, abs=0.01)


def test_ai_cash_infinite_and_price_history(h):
    comps = {c["id"]: c for c in h.call("list_companies")["data"]["companies"]}
    assert comps["company:2"]["cash"] == {"infinite": True} and comps["company:1"]["cash"] == 2500000.0
    assert h.call("get_market")["data"]["price_history"] == {"available": False, "reason": "not_retained_by_game"}
    assert h.call("get_product", {"product": "Paint"})["data"]["price_history"]["available"] is False


def test_shop_demand_unit_note(h):
    s = h.call("get_shop", {"shop": bf.S_HW2})["data"]
    assert s["demand_unit_note"].startswith("Shop demand is in units per consumption interval")
    p = s["products"][0]
    assert p["unmet_demand"]["per_interval"] == 6 and p["unmet_demand"]["per_30d"] == 12  # interval 15 days


def test_actor_labels(h):
    rows = h.call("list_buildings", {"owner": "all", "limit": 50})["data"]["buildings"]
    kinds = {r["owner"]["kind"] for r in rows}
    assert {"company", "other"} <= kinds or {"company", "state"} <= kinds
    shop = h.call("get_shop", {"shop": bf.S_HW1})["data"]
    assert shop["identity"]["owner"] == {"id": "city:10", "actor_id": 10, "kind": "city", "name": "Valmont"}


# ------------------------------------------------------------------ error codes

def test_all_error_codes_reachable(tmp_path, clock, procs, world):
    codes = set()

    def harness(name):
        d = tmp_path / name
        d.mkdir()
        return Harness(d, clock, procs)

    hh = harness("a")
    hh.write_world(world)
    codes.add(hh.call("get_product", {"product": "Nothing"})["error"]["code"])                      # not_found
    codes.add(hh.call("get_building", {"building": "USINE DE PEINTURE 1"})["error"]["code"])        # ambiguous
    codes.add(hh.call("list_routes", {"limit": 99})["error"]["code"])                               # invalid_argument
    codes.add(hh.call("list_vehicles", {"vehicle": f"vehicle:{bf.WORLD_SESSION_2}:-1201"})["error"]["code"])  # stale_reference
    codes.add(hh.call("get_route", {"route": ROUTE_1, "include_path": True})["error"]["code"])     # section_unavailable
    procs.procs = []
    codes.add(hh.call("list_routes")["error"]["code"])                                             # game_not_running
    procs.procs = [__import__("conftest").game_proc()]
    obs = FakeObserver(hh.exchange, clock, procs)
    for state, expect in (("menu", "at_main_menu"), ("loading", "loading"), ("disabled", "observer_disabled"),
                          ("faulted", "observer_faulted")):
        obs.heartbeat(state)
        r = hh.call("list_routes")
        assert r["error"]["code"] == expect
        codes.add(expect)
    obs.unsupported_build()
    codes.add(hh.call("list_products")["error"]["code"])                                           # unsupported_build
    obs.world_session = bf.WORLD_SESSION
    obs.heartbeat("ready")
    clock.advance(10)
    codes.add(hh.call("list_routes")["error"]["code"])                                             # observer_unresponsive
    obs.heartbeat("ready", world_session=bf.WORLD_SESSION_2)
    codes.add(hh.call("list_routes")["error"]["code"])                                             # snapshot_unavailable
    procs.procs = [__import__("conftest").game_proc(pid=555, start=clock.now() - __import__("datetime").timedelta(minutes=5))]
    codes.add(hh.call("list_routes")["error"]["code"])                                             # observer_not_detected
    procs.procs = [__import__("conftest").game_proc(start=clock.now() - __import__("datetime").timedelta(minutes=5))]
    obs.heartbeat("ready", world_session=bf.WORLD_SESSION, schema_version="2.0.0")
    codes.add(hh.call("list_routes")["error"]["code"])                                             # schema_mismatch
    assert codes == set(ERROR_CODES) - {"internal_error"}


def test_internal_errors_return_envelope(h, monkeypatch):
    from roi_mcp.tools import logistics

    def boom(ctx):
        raise RuntimeError("bug")

    monkeypatch.setattr(h.app.specs["list_routes"], "handler", boom)
    r = h.call("list_routes")
    assert r["ok"] is False and r["error"]["code"] == "internal_error" and "meta" in r


# ------------------------------------------------------------------ refresh requests (PRD 13.2 / 13.2a / 13.7)

def _observer_harness(tmp_path, clock, procs, wait=3.0, serve=True):
    d = tmp_path / "x"
    d.mkdir()
    hh = Harness(d, clock, procs, refresh_wait_s=wait)
    obs = FakeObserver(d, clock, procs)
    obs.ready()
    if serve:
        def on_sleep(c):
            req = obs.read_refresh_request()
            if req:
                for scope in ("state", "history", "static"):
                    obs.serve_refresh(scope)
        clock.on_sleep = on_sleep
    return hh, obs


@pytest.mark.parametrize("name", [n for n in PRD_TOOLS if REFRESH_SCOPE[n] != "none"])
def test_fresh_writes_exactly_the_tool_scope(tmp_path, clock, procs, name):
    hh, obs = _observer_harness(tmp_path, clock, procs)
    args = dict(VALID_CALLS[name])
    if name == "get_supply_chain":
        args["mode"] = "actual"
    r = hh.call(name, {**args, "fresh": True})
    assert r["ok"], r.get("error")
    check_response(name, r)
    req = obs.read_refresh_request()
    scope = REFRESH_SCOPE[name]
    assert req is not None
    assert {k for k, v in req["requests"].items() if v is not None} == {scope}
    assert "refresh_timeout" not in [w["code"] for w in r["meta"]["warnings"]]
    fam = r["meta"]["snapshots"]
    used = {s["family"]: s["seq"] for s in fam}
    if scope == "state":
        assert used.get("state") == obs.state_seq
    else:
        assert used.get("history") == obs.history_doc["seq"]


@pytest.mark.parametrize("name", ["get_game_status", "get_recipe"])
def test_none_scope_tools_have_no_fresh_and_write_nothing(tmp_path, clock, procs, name):
    hh, obs = _observer_harness(tmp_path, clock, procs)
    assert hh.call(name, {**VALID_CALLS[name], "fresh": True})["error"]["code"] == "invalid_argument"
    assert hh.call(name, VALID_CALLS[name])["ok"]
    assert obs.read_refresh_request() is None


def test_recipe_mode_supply_chain_writes_nothing(tmp_path, clock, procs):
    hh, obs = _observer_harness(tmp_path, clock, procs)
    r = hh.call("get_supply_chain", {"product": "Paint", "mode": "recipe", "fresh": True, "target_output_per_30d": 100})
    assert r["ok"] and "fresh_not_applicable" in [w["code"] for w in r["meta"]["warnings"]]
    assert obs.read_refresh_request() is None
    req = r["data"]["requirements"]["products"]
    assert req["product:Chemicals"]["required_per_30d"] == 50 and req["product:Dye"]["required_per_30d"] == 100
    assert req["product:Gas"]["required_per_30d"] == 75
    check_response("get_supply_chain", r)


def test_refresh_timeout(tmp_path, clock, procs):
    hh, obs = _observer_harness(tmp_path, clock, procs, wait=1.0, serve=False)
    r = hh.call("get_route", {"route": ROUTE_1, "fresh": True})
    assert r["ok"] and "refresh_timeout" in [w["code"] for w in r["meta"]["warnings"]]
    assert obs.read_refresh_request()["requests"]["state"] is not None


def test_refresh_preserves_other_scope_nonces(tmp_path, clock, procs):
    hh, obs = _observer_harness(tmp_path, clock, procs, wait=0.5, serve=False)
    hh.call("get_finances", {"fresh": True})
    first = obs.read_refresh_request()["requests"]
    clock.advance(1)
    hh.call("list_routes", {"fresh": True})
    second = obs.read_refresh_request()["requests"]
    assert second["history"] == first["history"] and second["state"] is not None and second["state"] > first["history"]


def test_concurrent_fresh_calls_coalesce(tmp_path, clock, procs):
    hh, obs = _observer_harness(tmp_path, clock, procs, wait=2.0, serve=False)
    served = {"n": 0}

    def on_sleep(c):
        served["n"] += 1
        # Serve once all six calls have registered (each sleeps once after requesting). A call that arrived
        # after the request was served would rightly write a new nonce (PRD 13.2a).
        if served["n"] == 6:
            obs.serve_refresh("state")
    clock.on_sleep = on_sleep

    async def go():
        return await asyncio.gather(*(hh.acall("list_routes", {"fresh": True}) for _ in range(6)))

    results = asyncio.run(go())
    assert all(r["ok"] for r in results)
    assert hh.app.refresh.writes == 1
    assert len({r["meta"]["snapshot"]["seq"] for r in results}) == 1


def test_fresh_after_a_served_request_writes_a_new_nonce(tmp_path, clock, procs):
    """PRD 13.2a: only an outstanding (unserved) request is reused; seen live in gate E6."""
    hh, obs = _observer_harness(tmp_path, clock, procs, wait=3.0, serve=False)
    clock.on_sleep = lambda c: obs.serve_refresh("state")
    hh.call("list_routes", {"fresh": True})
    first = obs.read_refresh_request()["requests"]["state"]
    clock.advance(1)  # well inside refresh_wait_s
    hh.call("list_routes", {"fresh": True})
    second = obs.read_refresh_request()["requests"]["state"]
    assert hh.app.refresh.writes == 2 and second > first


def test_fresh_when_not_live_returns_lifecycle_error_without_writing(tmp_path, clock, procs):
    hh, obs = _observer_harness(tmp_path, clock, procs)
    obs.menu()
    r = hh.call("list_routes", {"fresh": True})
    assert r["error"]["code"] == "at_main_menu"
    assert obs.read_refresh_request() is None


# ------------------------------------------------------------------ no write semantics

SRC = Path(__file__).resolve().parents[1] / "src" / "roi_mcp"
AMBIGUOUS_CALLS = {"replace", "rename", "remove", "unlink", "copy", "move"}
WRITE_CALLS = {"write_text", "write_bytes", "replace", "rename", "unlink", "remove", "rmdir", "mkdir", "makedirs", "mkstemp",
               "touch", "utime", "chmod", "rmtree", "copy", "copyfile", "move", "fdopen", "RotatingFileHandler", "FileHandler"}


def _calls_in(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
            if name in AMBIGUOUS_CALLS:
                # str.replace / datetime.replace are not file operations; only os./shutil. calls count
                owner = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else None
                if owner not in ("os", "shutil", "Path", "pathlib"):
                    continue
            if name in WRITE_CALLS:
                out.add(name)
            if name == "open":
                for a in list(n.args[1:2]) + [k.value for k in n.keywords if k.arg == "mode"]:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str) and any(c in a.value for c in "wax+"):
                        out.add("open-write")
    return out


def test_only_refresh_writer_and_logger_write_files():
    offenders = {}
    for p in sorted(SRC.rglob("*.py")):
        if p.name == "response_schemas.py":
            continue  # developer generator (writes schemas/tool-responses), never called by the server
        calls = _calls_in(p)
        if calls:
            offenders[p.relative_to(SRC).as_posix()] = calls
    assert set(offenders) == {"refresh.py", "server.py"}, offenders
    assert offenders["server.py"] == {"RotatingFileHandler"}
    assert offenders["refresh.py"] <= {"mkstemp", "fdopen", "replace", "unlink"}
    src = (SRC / "refresh.py").read_text(encoding="utf-8")
    assert 'FILENAME = cfg.REFRESH_REQUEST_FILENAME' in src
    assert cfg.REFRESH_REQUEST_FILENAME == "refresh-request.json"


def test_no_tool_parameter_has_write_semantics(h):
    verbs = {"set", "write", "build", "demolish", "buy", "sell", "repay", "research", "dispatch", "command", "exec", "change",
             "update", "delete", "toggle", "pay", "bid", "unlock", "enqueue", "cancel", "save", "load"}
    for spec in h.app.specs.values():
        if spec.name not in TOOL_NAMES:
            continue   # advisor tools: tests/advisor/test_safety.py (what_if.change is a hypothetical, documented there)
        schema = spec.input_schema()
        for p, ps in schema["properties"].items():
            assert p.lower().split("_")[0] not in verbs, (spec.name, p)
            # every parameter is a scalar filter/selector, an array of selectors, or a selector map
            assert ps.get("type") in ("string", "integer", "number", "boolean", "array", "object"), (spec.name, p)


def test_exchange_dir_contains_only_expected_files_after_full_sweep(tmp_path, clock, procs):
    hh, obs = _observer_harness(tmp_path, clock, procs)
    for name in PRD_TOOLS:
        args = dict(VALID_CALLS[name])
        if REFRESH_SCOPE[name] != "none":
            args["fresh"] = True
        hh.call(name, args)
    names = {p.name for p in hh.exchange.iterdir()}
    assert names <= {"heartbeat.json", "static.json", "state.json", "history.json", "refresh-request.json"}, names


def test_history_window_is_never_mistaken_for_full_game_history(h):
    """V1 exports a bounded recent window; every windowed series states its bounds and whether it is truncated."""
    hist = h.call("get_building", {"building": f"building:{bf.B_PF1}", "include": ["history"]})["data"]["history"]
    assert "bounded recent window" in hist["window_note"]
    window_keys = {"window_months", "window_first_month", "window_last_month", "history_truncated", "first_month_available"}
    for series in hist["monthly_analysis"]:
        assert window_keys <= series.keys()
        assert series["window_months"] == len(bf.MONTHS)
    by_item = {s["item"]: s for s in hist["monthly_analysis"]}
    # The game still holds years of data before the window: flagged, with the oldest month the game holds.
    assert by_item["efficiency_pct"]["history_truncated"] is True
    assert by_item["efficiency_pct"]["first_month_available"] == "Y1-04"
    assert by_item["production"]["history_truncated"] is False
    prod = hist["production_monthly"]
    assert prod and all(window_keys <= p.keys() for p in prod)
    assert prod[0]["history_truncated"] is True and prod[0]["first_month_available"] is None


def test_finances_state_that_the_ledger_covers_the_game_retention(h):
    ret = h.call("get_finances", {"months": 3})["data"]["retention"]
    assert ret["history_truncated"] is False
    assert ret["window_months"] == len(bf.MONTHS)
    assert ret["retention_years"] == 3


@pytest.mark.parametrize("tool,args,scope", [("list_routes", {}, "state"), ("get_finances", {}, "history"),
                                            ("get_product", {"product": "Paint"}, "state")])
def test_fresh_snapshot_age_is_measured_after_the_wait(tmp_path, clock, procs, tool, args, scope):
    """Gate V8 (2026-10-04): a snapshot captured while the call waited for its refresh was reported with a
    negative age_s, because the call's clock was read before the wait. Age is measured when the answer is built."""
    hh, obs = _observer_harness(tmp_path, clock, procs, wait=3.0, serve=False)

    def on_sleep(c):
        if c.monotonic() >= 1001.5:              # the observer captures 1.5 s into the wait
            req = obs.read_refresh_request()
            if req and req["requests"].get(scope) is not None:
                if scope == "state":
                    obs.publish_state(capture_age_s=0.0)
                    obs.refresh_served["state"] = req["requests"]["state"]
                    obs.heartbeat("ready")
                else:
                    obs.serve_refresh(scope)
    clock.on_sleep = on_sleep
    r = hh.call(tool, {**args, "fresh": True})
    assert r["ok"] and "refresh_timeout" not in [w["code"] for w in r["meta"]["warnings"]]
    for snap in r["meta"]["snapshots"]:
        assert snap["age_s"] is None or snap["age_s"] >= 0, snap
