"""Hardening tests for the PRD 14.3 building/production tools:
list_buildings, get_building, get_production_overview, find_production_issues.

Every response (ok and error) is validated with check_response (tool response schema + 30 KB cap).
Expected values are justified by PRD.md sections, the response schemas, docs/SNAPSHOT-FORMAT.md or
docs/KNOWN-LIMITATIONS.md; where those are silent only invariants are asserted.
Implementation contradictions of the PRD are kept as strict xfails tagged "DEFECT BU-<n>".
"""

from __future__ import annotations

import copy
from datetime import timedelta

import pytest

import build_fixtures as bf
from conftest import FakeClock, FakeProcs, Harness, game_proc  # noqa: F401  (FakeClock/FakeProcs kept for helpers)
from test_tools_contract import VALID_CALLS, _observer_harness, check_response

TOOLS = ("list_buildings", "get_building", "get_production_overview", "find_production_issues")

PLAYER_KEYS = [bf.B_HQ, bf.B_GW1, bf.B_GW2, bf.B_GW3, bf.B_PC1, bf.B_PC2, bf.B_FF, bf.B_WS, bf.B_CP, bf.B_PF1, bf.B_PF2,
               bf.B_WH, bf.B_TD]
AI_KEYS_B = ["Headquarters@140,110", bf.AI_PF, "GasWell@160,118", "PetrochemicalFactory@170,125"]
AI_KEYS_C = ["Headquarters@240,50", "FlowerFarm@250,60", "ChemicalPlant@255,70"]
STATE_KEYS = ["TradingPost@5,5"]
GET_BUILDING_PARTS = ["production", "inventory", "outgoing_routes", "incoming_routes", "requests", "modules", "history", "vehicles"]


# ============================================================================ helpers

def call(h, name, args=None):
    resp = h.call(name, args or {})
    check_response(name, resp)
    return resp


def ok(h, name, args=None):
    resp = call(h, name, args)
    assert resp["ok"], resp.get("error")
    return resp


def err(h, name, args, *codes):
    resp = call(h, name, args)
    assert resp["ok"] is False, resp.get("data")
    if codes:
        assert resp["error"]["code"] in codes, resp["error"]
    return resp


def bid(key):
    return f"building:{key}"


def ids(resp, key="buildings"):
    return [r["id"] for r in resp["data"][key]]


def publish(h, w, *families):
    """Re-publish mutated families with a new seq and a matching content hash (like the observer)."""
    for fam in families:
        doc = w[fam]
        doc["seq"] = doc["seq"] + 1
        doc["content_hash"] = bf.content_hash(doc["data"])
    h.write_world(w, families)


def fail_section(w, family, sec, status="failed", reason="exception"):
    """state/history schema: a section's data is null whenever its envelope status is not ok."""
    w[family]["data"][sec] = None
    w[family]["sections"][sec] = bf.section_status(1512, 0, status, reason)


def pbuilding(w, key):
    return next(b for b in w["state"]["data"]["buildings_player"] if b["key"] == key)


def unavailable_fields(resp):
    return [u["field"] for u in resp["data"]["unavailable"]]


def warn_codes(resp):
    return [x["code"] for x in resp["meta"]["warnings"]]


def all_pages(h, name, args, key):
    rows, cursor, pages, totals = [], None, 0, set()
    while True:
        a = dict(args)
        if cursor:
            a["cursor"] = cursor
        r = ok(h, name, a)
        rows.extend(r["data"][key])
        totals.add(r["page"]["total"])
        pages += 1
        cursor = r["page"]["next_cursor"]
        if not cursor:
            return rows, pages, totals
        assert pages < 500


def add_buildings(w, n, *, name=lambda i: f"USINE TEST {i:03d}", owner=bf.PLAYER, produced=lambda i: i % 7):
    """n synthetic paint factories (copies of PF1) on distinct tiles."""
    base = pbuilding(w, bf.B_PF1)
    rows = []
    for i in range(n):
        b = copy.deepcopy(base)
        x, y = 1000 + i, 2000 + (i % 13)
        b.update(key=f"PaintFactory@{x},{y}", x=x, y=y, display_name=name(i), save_guid=None, owner_actor_id=owner)
        b["production"]["produced_last_month"] = produced(i)
        rows.append(b)
    w["state"]["data"]["buildings_player"].extend(rows)
    return [r["key"] for r in rows]


def later_heartbeat(h, w, seconds):
    """Advance the clock and publish a fresh heartbeat while the snapshots stay as they were."""
    h.clock.advance(seconds)
    hb = bf.build_heartbeat(h.clock.now(), static_doc=w["static"], state_doc=w["state"], history_doc=w["history"],
                            seq=w["heartbeat"]["seq"] + 1)
    w["heartbeat"] = hb
    h.write_world(w, ("heartbeat",))


# ============================================================================ common contract: valid, unknown, types

@pytest.mark.parametrize("name", TOOLS)
def test_valid_call_from_contract_table(h, name):
    r = ok(h, name, VALID_CALLS[name])
    assert r["meta"]["source"] == "live_snapshot" and r["meta"]["stale"] is False  # PRD 13.3 / 13.4
    assert r["meta"]["snapshot"]["family"] == "state"                              # PRD 13.7: scope state
    assert r["meta"]["world_session"] == bf.WORLD_SESSION


@pytest.mark.parametrize("name", TOOLS)
def test_all_optionals_omitted(h, name):
    args = {"building": bid(bf.B_PF1)} if name == "get_building" else {}
    ok(h, name, args)


@pytest.mark.parametrize("name", TOOLS)
@pytest.mark.parametrize("extra", [{"bogus": 1}, {"set_recipe": "Paints"}, {"cursor_": "x"}, {"Fresh": True}])
def test_unknown_parameter_is_invalid_argument(h, name, extra):
    args = {"building": bid(bf.B_PF1)} if name == "get_building" else {}
    err(h, name, {**args, **extra}, "invalid_argument")  # PRD 13.3 invalid_argument; input schema closed


@pytest.mark.parametrize("name,args", [
    ("list_buildings", {"owner": 5}), ("list_buildings", {"kind": "plant"}), ("list_buildings", {"kind": ""}),
    ("list_buildings", {"status": "broken"}), ("list_buildings", {"status": "polluted"}), ("list_buildings", {"sort": "bogus"}),
    ("list_buildings", {"sort": "stock_ratio desc"}), ("list_buildings", {"fields": "all"}), ("list_buildings", {"limit": 0}),
    ("list_buildings", {"limit": 51}), ("list_buildings", {"limit": -1}), ("list_buildings", {"limit": "10"}),
    ("list_buildings", {"limit": 2.5}), ("list_buildings", {"limit": True}), ("list_buildings", {"cursor": 7}),
    ("list_buildings", {"fresh": "yes"}), ("list_buildings", {"allow_stale": 1}), ("list_buildings", {"product": ["Gas"]}),
    ("list_buildings", {"cursor": "garbage"}), ("list_buildings", {"cursor": "djF8MXx4"}),
    ("get_building", {}), ("get_building", {"building": 12}), ("get_building", {"building": None}),
    ("get_building", {"building": ""}), ("get_building", {"building": "   "}),
    ("get_building", {"building": bf.B_PF1, "include": "production"}),
    ("get_building", {"building": bf.B_PF1, "include": ["bogus"]}),
    ("get_building", {"building": bf.B_PF1, "include": ["production", "production"]}),
    ("get_building", {"building": bf.B_PF1, "limit": 5}), ("get_building", {"building": bf.B_PF1, "fields": "full"}),
    ("get_production_overview", {"company": 1}), ("get_production_overview", {"product": 3}),
    ("get_production_overview", {"limit": 5}), ("get_production_overview", {"company": ""}),
    ("get_production_overview", {"sort": "name"}),
    ("find_production_issues", {"kinds": "disabled"}), ("find_production_issues", {"kinds": ["bogus"]}),
    ("find_production_issues", {"kinds": ["disabled", "disabled"]}), ("find_production_issues", {"limit": 0}),
    ("find_production_issues", {"limit": 51}), ("find_production_issues", {"company": ""}),
    ("find_production_issues", {"cursor": "garbage"}),
])
def test_invalid_values_and_types(h, name, args):
    err(h, name, args, "invalid_argument")


@pytest.mark.parametrize("limit", [1, 50])
def test_limit_boundaries_accepted(h, limit):
    r = ok(h, "list_buildings", {"owner": "all", "limit": limit})
    assert len(r["data"]["buildings"]) == min(limit, 21) and r["page"]["total"] == 21
    r = ok(h, "find_production_issues", {"limit": limit})
    assert len(r["data"]["issues"]) == min(limit, 9)


# ============================================================================ list_buildings: filters

def test_list_buildings_default_owner_is_player(h):
    r = ok(h, "list_buildings", {"limit": 50})
    assert sorted(ids(r)) == sorted(bid(k) for k in PLAYER_KEYS)          # PRD 14.3 owner default player
    assert r["page"]["total"] == 13 and r["page"]["next_cursor"] is None
    assert all(row["owner"]["actor_id"] == bf.PLAYER for row in r["data"]["buildings"])
    assert "buildings_player" in r["meta"]["snapshot"]["sections_used"]


@pytest.mark.parametrize("owner,expected", [
    ("player", PLAYER_KEYS), ("PLAYER", PLAYER_KEYS), ("company:1", PLAYER_KEYS), ("Acme Industries", PLAYER_KEYS),
    ("acme industries", PLAYER_KEYS),
    ("ai", AI_KEYS_B + AI_KEYS_C), ("company:2", AI_KEYS_B), ("Borealis Corp", AI_KEYS_B), ("company:3", AI_KEYS_C),
    ("all", PLAYER_KEYS + AI_KEYS_B + AI_KEYS_C + STATE_KEYS), ("ALL", PLAYER_KEYS + AI_KEYS_B + AI_KEYS_C + STATE_KEYS),
])
def test_list_buildings_owner_filter(h, owner, expected):
    r = ok(h, "list_buildings", {"owner": owner, "limit": 50})
    assert sorted(ids(r)) == sorted(bid(k) for k in expected)


def test_list_buildings_ai_rows_are_compact(h):
    rows = ok(h, "list_buildings", {"owner": "ai", "limit": 50})["data"]["buildings"]
    assert rows and all(r["detail"] == "compact" for r in rows)       # PRD 14.3: AI rows from compact data
    assert all(r["max_stock_ratio"] is None and r["upkeep_monthly"] is None for r in rows)
    pf = next(r for r in rows if r["id"] == bid(bf.AI_PF))
    assert pf["recipe"] == "recipe:Paints" and pf["produced_last_month"] == 4


@pytest.mark.parametrize("owner", ["Nobody Inc", "company:99", "city:10", "building:" + bf.B_PF1])
def test_list_buildings_unknown_owner_not_found(h, owner):
    err(h, "list_buildings", {"owner": owner}, "not_found")


@pytest.mark.parametrize("kind,expected", [
    ("factory", [bf.B_PC1, bf.B_PC2, bf.B_CP, bf.B_PF1, bf.B_PF2]), ("gatherer", [bf.B_GW1, bf.B_GW2, bf.B_GW3, bf.B_WS]),
    ("farm", [bf.B_FF]), ("hq", [bf.B_HQ]), ("warehouse", [bf.B_WH]), ("depot", [bf.B_TD]),
    ("harvester", []), ("field", []), ("shop", []), ("other", []),
])
def test_list_buildings_kind_filter(h, kind, expected):
    r = ok(h, "list_buildings", {"kind": kind, "limit": 50})
    assert sorted(ids(r)) == sorted(bid(k) for k in expected)
    assert r["page"]["total"] == len(expected)


@pytest.mark.parametrize("bt", ["PaintFactory", "building_type:PaintFactory", "Paint Factory", "usine de peinture"])
def test_list_buildings_building_type_filter(h, bt):
    r = ok(h, "list_buildings", {"building_type": bt})
    assert sorted(ids(r)) == sorted([bid(bf.B_PF1), bid(bf.B_PF2)])
    assert {row["type"] for row in r["data"]["buildings"]} == {"building_type:PaintFactory"}


@pytest.mark.parametrize("arg", [{"building_type": "Nope"}, {"building_type": "building_type:Nope"}, {"product": "Unobtainium"},
                                 {"recipe": "recipe:Nope"}, {"city": "Atlantis"}, {"region": "region:nope"},
                                 {"building_type": "product:Paint"}])
def test_list_buildings_unresolvable_filter_not_found(h, arg):
    err(h, "list_buildings", arg, "not_found")


@pytest.mark.parametrize("product,expected", [
    ("Paint", [bf.B_PF1]), ("product:Paint", [bf.B_PF1]), ("Peinture", [bf.B_PF1]),
    ("Chemicals", [bf.B_PC1, bf.B_PC2, bf.B_PF1]), ("Gas", [bf.B_GW1, bf.B_GW2, bf.B_GW3, bf.B_PC1, bf.B_PC2]),
    ("Water", [bf.B_WS, bf.B_CP]),
])
def test_list_buildings_product_filter_produces_or_consumes(h, product, expected):
    r = ok(h, "list_buildings", {"product": product, "limit": 50})
    assert sorted(ids(r)) == sorted(bid(k) for k in expected)


def test_list_buildings_product_filter_covers_ai_compact_rows(h):
    r = ok(h, "list_buildings", {"product": "Paint", "owner": "all", "limit": 50})
    assert sorted(ids(r)) == sorted([bid(bf.B_PF1), bid(bf.AI_PF)])


@pytest.mark.parametrize("recipe,expected", [("Chemicals", [bf.B_PC1, bf.B_PC2]), ("recipe:Paints", [bf.B_PF1]),
                                             ("PaintsAdvanced", []), ("Peintures avancees", [])])
def test_list_buildings_recipe_filter(h, recipe, expected):
    r = ok(h, "list_buildings", {"recipe": recipe})
    assert sorted(ids(r)) == sorted(bid(k) for k in expected)


@pytest.mark.parametrize("city,expected", [
    ("Valmont", [bf.B_HQ, bf.B_PF1, bf.B_PF2, bf.B_WH]), ("city:10", [bf.B_HQ, bf.B_PF1, bf.B_PF2, bf.B_WH]),
    ("valmont", [bf.B_HQ, bf.B_PF1, bf.B_PF2, bf.B_WH]), ("Saint-Eloi", []), ("SAINT-ÉLOI", []), ("Brindlewick", []),
])
def test_list_buildings_city_filter(h, city, expected):
    r = ok(h, "list_buildings", {"city": city})
    assert sorted(ids(r)) == sorted(bid(k) for k in expected)


@pytest.mark.parametrize("region,expected", [
    ("Greyhollow", [bf.B_GW1, bf.B_GW2, bf.B_PC1, bf.B_FF, bf.B_WS, bf.B_CP, bf.B_TD]),
    ("Northmarch", [bf.B_GW3, bf.B_PC2]), (f"region:{bf.R_NOR}", [bf.B_GW3, bf.B_PC2]),
    ("Valmont", [bf.B_HQ, bf.B_PF1, bf.B_PF2, bf.B_WH]), ("Saint-Éloi", []),
])
def test_list_buildings_region_filter(h, region, expected):
    r = ok(h, "list_buildings", {"region": region, "limit": 50})
    assert sorted(ids(r)) == sorted(bid(k) for k in expected)


def test_list_buildings_status_filter_partitions_rows(h):
    full = ok(h, "list_buildings", {"limit": 50})["data"]["buildings"]
    seen = []
    for st in ("working", "idle", "disabled", "blocked"):
        rows = ok(h, "list_buildings", {"status": st, "limit": 50})["data"]["buildings"]
        assert all(r["status"] == st for r in rows)
        seen += [r["id"] for r in rows]
    assert sorted(seen) == sorted(r["id"] for r in full)                # the 4 classes partition the rows
    disabled = ok(h, "list_buildings", {"status": "disabled"})
    assert ids(disabled) == [bid(bf.B_GW3)]                              # D-STATUS-1 step 1
    assert ok(h, "list_buildings", {"status": "blocked"})["page"]["total"] == 0


def test_list_buildings_status_blocked(h):
    w = copy.deepcopy(h.world)
    pbuilding(w, bf.B_PC1)["flags"]["requirements_met"] = False
    pbuilding(w, bf.B_PC1)["notifications"] = ["NoRoadConnection"]
    publish(h, w, "state")
    r = ok(h, "list_buildings", {"status": "blocked"})
    assert ids(r) == [bid(bf.B_PC1)]                                     # D-STATUS-1 step 4
    assert r["data"]["buildings"][0]["derived_status"] == "blocked"
    g = ok(h, "get_building", {"building": bid(bf.B_PC1)})["data"]["status"]
    assert g["derived_status"] == "blocked" and g["notifications"] == ["NoRoadConnection"]


def test_list_buildings_combined_filters(h):
    r = ok(h, "list_buildings", {"kind": "factory", "status": "idle", "region": "Northmarch", "product": "Gas",
                                  "recipe": "Chemicals", "building_type": "PetrochemicalFactory"})
    assert ids(r) == [bid(bf.B_PC2)]
    r = ok(h, "list_buildings", {"kind": "factory", "city": "Valmont", "sort": "type", "fields": "full"})
    assert sorted(ids(r)) == sorted([bid(bf.B_PF1), bid(bf.B_PF2)])
    r = ok(h, "list_buildings", {"owner": "all", "kind": "factory", "product": "Paint"})
    assert sorted(ids(r)) == sorted([bid(bf.B_PF1), bid(bf.AI_PF)])
    r = ok(h, "list_buildings", {"kind": "hq", "product": "Paint"})                   # zero results
    assert r["data"]["buildings"] == [] and r["page"] == {**r["page"], "total": 0, "next_cursor": None}


def test_list_buildings_row_fields_compact_and_full(h):
    compact = ok(h, "list_buildings", {"limit": 50})["data"]["buildings"]
    full = ok(h, "list_buildings", {"limit": 50, "fields": "full"})["data"]["buildings"]
    assert [r["id"] for r in compact] == [r["id"] for r in full]
    required = {"id", "display_name", "type", "owner", "city", "region", "coordinates", "status", "recipe",
                "produced_last_month", "max_stock_ratio", "upkeep_monthly"}                # PRD 14.3 row keys
    for c, f in zip(compact, full):
        assert required <= c.keys() and required <= f.keys()
        assert set(c) <= set(f)
    pf1 = next(r for r in compact if r["id"] == bid(bf.B_PF1))
    assert pf1["coordinates"] == {"x": 55, "y": 40} and pf1["recipe"] == "recipe:Paints"
    assert pf1["max_stock_ratio"] == pytest.approx(38 / 40)                                 # D-INV-1 max over inventory
    assert pf1["upkeep_monthly"] == 12500.0 and pf1["produced_last_month"] == 2
    assert pf1["city"]["id"] == "city:10" and pf1["region"]["id"] == f"region:{bf.R_VAL}"


@pytest.mark.parametrize("sort", ["name", "type", "stock_ratio", "produced_last_month", "upkeep"])
def test_list_buildings_sort_is_total_and_deterministic(h, sort):
    a = ok(h, "list_buildings", {"owner": "all", "sort": sort, "limit": 50})
    b = ok(h, "list_buildings", {"owner": "all", "sort": sort, "limit": 50})
    assert ids(a) == ids(b) and len(ids(a)) == 21 and len(set(ids(a))) == 21
    h.restart()
    assert ids(ok(h, "list_buildings", {"owner": "all", "sort": sort, "limit": 50})) == ids(a)   # PRD 13.1
    field = {"stock_ratio": "max_stock_ratio", "produced_last_month": "produced_last_month", "upkeep": "upkeep_monthly"}.get(sort)
    if field:
        vals = [r[field] for r in a["data"]["buildings"] if r[field] is not None]
        assert vals == sorted(vals) or vals == sorted(vals, reverse=True)
    if sort == "name":
        names = [(r["display_name"] or "").casefold() for r in a["data"]["buildings"]]
        assert names == sorted(names)
    if sort == "type":
        types = [r["type"] for r in a["data"]["buildings"]]
        assert types == sorted(types)


def test_list_buildings_default_sort_equals_name(h):
    assert ids(ok(h, "list_buildings", {"limit": 50})) == ids(ok(h, "list_buildings", {"limit": 50, "sort": "name"}))


def test_list_buildings_ties_broken_by_id(h):
    w = copy.deepcopy(h.world)
    keys = add_buildings(w, 12, name=lambda i: "MEME NOM", produced=lambda i: 5)
    publish(h, w, "state")
    rows = ok(h, "list_buildings", {"sort": "name", "limit": 50})["data"]["buildings"]
    tied = [r["id"] for r in rows if r["display_name"] == "MEME NOM"]
    assert tied == sorted(bid(k) for k in keys)
    rows = ok(h, "list_buildings", {"sort": "produced_last_month", "limit": 50})["data"]["buildings"]
    tied = [r["id"] for r in rows if r["produced_last_month"] == 5]
    assert tied == sorted(tied)


# ============================================================================ list_buildings: paging, size, large worlds

def test_list_buildings_pagination_round_trip(h):
    full = ids(ok(h, "list_buildings", {"owner": "all", "limit": 50}))
    rows, pages, totals = all_pages(h, "list_buildings", {"owner": "all", "limit": 5}, "buildings")
    assert [r["id"] for r in rows] == full and pages == 5 and totals == {21}


def test_list_buildings_last_page_and_single_page(h):
    first = ok(h, "list_buildings", {"owner": "all", "limit": 20})
    assert len(first["data"]["buildings"]) == 20 and first["page"]["next_cursor"]
    last = ok(h, "list_buildings", {"owner": "all", "limit": 20, "cursor": first["page"]["next_cursor"]})
    assert len(last["data"]["buildings"]) == 1 and last["page"]["next_cursor"] is None
    one = ok(h, "list_buildings", {"owner": "all", "limit": 21})
    assert one["page"]["next_cursor"] is None and one["page"]["total"] == 21


def test_list_buildings_cursor_with_changed_filters_rejected(h):
    cur = ok(h, "list_buildings", {"owner": "all", "limit": 5})["page"]["next_cursor"]
    for changed in ({"owner": "player"}, {"owner": "all", "sort": "type"}, {"owner": "all", "kind": "factory"},
                    {}):
        err(h, "list_buildings", {**changed, "limit": 5, "cursor": cur}, "invalid_argument")
    err(h, "find_production_issues", {"cursor": cur}, "invalid_argument")       # cursor of another tool


def test_list_buildings_cursor_tolerates_limit_fields_and_restart(h):
    first = ok(h, "list_buildings", {"owner": "all", "limit": 5})
    cur = first["page"]["next_cursor"]
    nxt = ok(h, "list_buildings", {"owner": "all", "limit": 5, "cursor": cur})
    h.restart()
    again = ok(h, "list_buildings", {"owner": "all", "limit": 5, "cursor": cur})
    assert again["data"] == nxt["data"] and again["page"] == nxt["page"]        # PRD 13.1 restart => identical
    full = ok(h, "list_buildings", {"owner": "all", "limit": 5, "cursor": cur, "fields": "full"})
    assert ids(full) == ids(nxt)


def test_list_buildings_default_limit_25_and_max_50(tmp_path, clock, procs, world):
    hh = Harness(tmp_path, clock, procs)
    add_buildings(world, 120)
    world["state"]["content_hash"] = bf.content_hash(world["state"]["data"])
    hh.write_world(world)
    r = ok(hh, "list_buildings")
    assert len(r["data"]["buildings"]) == 25 and r["page"]["total"] == 133 and r["page"]["next_cursor"]   # PRD 13.2
    r = ok(hh, "list_buildings", {"limit": 50})
    assert len(r["data"]["buildings"]) == 50
    rows, pages, totals = all_pages(hh, "list_buildings", {}, "buildings")
    assert len(rows) == 133 and len({x["id"] for x in rows}) == 133 and pages == 6 and totals == {133}


def test_list_buildings_size_cap_truncation_keeps_every_row_reachable(tmp_path, clock, procs, world):
    hh = Harness(tmp_path, clock, procs)
    long_name = lambda i: f"USINE {'X' * 180} {i:03d}"  # noqa: E731
    add_buildings(world, 160, name=long_name)
    world["state"]["content_hash"] = bf.content_hash(world["state"]["data"])
    hh.write_world(world)
    first = ok(hh, "list_buildings", {"limit": 50, "fields": "full"})
    assert "truncated" in warn_codes(first) and first["page"]["next_cursor"]       # PRD 14.9
    assert len(first["data"]["buildings"]) < 50
    rows, pages, totals = all_pages(hh, "list_buildings", {"limit": 50, "fields": "full"}, "buildings")
    expected = ids(ok(hh, "list_buildings", {"limit": 50}))  # sanity: first compact page is a prefix of the full order
    assert [r["id"] for r in rows][:len(expected)] == expected
    assert len(rows) == 173 and len({r["id"] for r in rows}) == 173 and totals == {173}


# ============================================================================ list_buildings: sections, empties, nulls

def test_list_buildings_no_player_buildings(h):
    w = copy.deepcopy(h.world)
    w["state"]["data"]["buildings_player"] = []
    publish(h, w, "state")
    r = ok(h, "list_buildings")
    assert r["data"]["buildings"] == [] and r["page"]["total"] == 0 and r["page"]["next_cursor"] is None
    r = ok(h, "list_buildings", {"owner": "all", "limit": 50})
    assert sorted(ids(r)) == sorted(bid(k) for k in AI_KEYS_B + AI_KEYS_C + STATE_KEYS)


@pytest.mark.parametrize("status", ["failed", "disabled", "skipped", "over_budget"])
def test_list_buildings_player_section_unavailable(h, status):
    w = copy.deepcopy(h.world)
    fail_section(w, "state", "buildings_player", status)
    publish(h, w, "state")
    for args in ({}, {"owner": "player"}, {"owner": "all"}, {"owner": "company:1"}):
        r = err(h, "list_buildings", args, "section_unavailable")                  # PRD 13.3 / 16
        assert "buildings_player" in r["error"]["message"] and status in r["error"]["message"]
    r = ok(h, "list_buildings", {"owner": "ai", "limit": 50})                      # affected tools/params only
    assert sorted(ids(r)) == sorted(bid(k) for k in AI_KEYS_B + AI_KEYS_C)


def test_list_buildings_ai_section_unavailable(h):
    w = copy.deepcopy(h.world)
    fail_section(w, "state", "buildings_ai", "failed")
    publish(h, w, "state")
    r = ok(h, "list_buildings", {"owner": "all", "limit": 50})
    assert sorted(ids(r)) == sorted(bid(k) for k in PLAYER_KEYS)
    assert "buildings_ai" in unavailable_fields(r)                                 # PRD 13.5 unavailable
    assert "buildings_ai" in r["meta"]["snapshot"]["sections_unavailable"]
    assert "section_degraded" in warn_codes(r)
    for owner in ("ai", "company:2"):
        err(h, "list_buildings", {"owner": owner}, "section_unavailable")


def test_list_buildings_with_ai_detail_section(h):
    w = copy.deepcopy(h.world)
    detail = copy.deepcopy(pbuilding(w, bf.B_PF1))
    detail.update(key=bf.AI_PF, x=150, y=120, owner_actor_id=bf.AI_B, display_name="USINE DE PEINTURE 1", city_id=bf.CITY_BRI,
                  region_id=bf.R_BRI)
    w["state"]["data"]["buildings_ai_detail"] = [detail]
    w["state"]["sections"]["buildings_ai_detail"] = bf.section_status(1512, 1)
    publish(h, w, "state")
    rows = ok(h, "list_buildings", {"owner": "company:2", "limit": 50})["data"]["buildings"]
    assert sorted(r["id"] for r in rows) == sorted(bid(k) for k in AI_KEYS_B)   # detail row not duplicated
    pf = next(r for r in rows if r["id"] == bid(bf.AI_PF))
    assert pf["detail"] == "full" and pf["upkeep_monthly"] == 12500.0
    g = ok(h, "get_building", {"building": bid(bf.AI_PF)})["data"]
    assert g["identity"]["detail"] == "full" and g["inventory"] is not None


# Regression test for DEFECT BU-5 (fixed).
def test_list_buildings_player_unknown_is_not_silently_empty(h):
    w = copy.deepcopy(h.world)
    fail_section(w, "state", "session")
    fail_section(w, "state", "companies")
    publish(h, w, "state")
    # get_production_overview reports the condition (PRD 13.3 section_unavailable) ...
    err(h, "get_production_overview", {}, "section_unavailable")
    # ... list_buildings must not claim the player owns nothing: either the 13 buildings or section_unavailable.
    r = call(h, "list_buildings", {"limit": 50})
    assert (not r["ok"] and r["error"]["code"] == "section_unavailable") or (r["ok"] and r["page"]["total"] == 13)


def test_list_buildings_null_and_missing_game_fields(h):
    w = copy.deepcopy(h.world)
    b = pbuilding(w, bf.B_PF2)
    b.update(display_name="", city_id=None, region_id=None, upkeep=None, efficiency=None, production=None, modules=None,
             logistics=None, inventory=[], cycle_days_effective=None, pollution_at_tile=None, is_polluted=None, save_guid=None)
    ai = next(x for x in w["state"]["data"]["buildings_ai"] if x["key"] == bf.AI_PF)
    ai.update(produced_last_month=None, cycle_days_effective=None, recipe=None, region_id=None, city_id=None, module_count=None)
    publish(h, w, "state")
    for sort in ("name", "type", "stock_ratio", "produced_last_month", "upkeep"):
        for fields in ("compact", "full"):
            ok(h, "list_buildings", {"owner": "all", "sort": sort, "fields": fields, "limit": 50})
    row = next(r for r in ok(h, "list_buildings", {"limit": 50})["data"]["buildings"] if r["id"] == bid(bf.B_PF2))
    assert row["city"] is None and row["region"] is None and row["upkeep_monthly"] is None and row["max_stock_ratio"] is None
    ok(h, "get_building", {"building": bid(bf.B_PF2)})
    ok(h, "get_building", {"building": bid(bf.AI_PF)})
    ok(h, "get_production_overview", {"company": "company:2"})
    ok(h, "find_production_issues", {})


def test_list_buildings_numeric_edges(h):
    w = copy.deepcopy(h.world)
    pf1 = pbuilding(w, bf.B_PF1)
    pf1["production"]["produced_last_month"] = 2**31 - 1
    pf1["upkeep"]["monthly_full"] = 1e12
    pf1["paid_to_build"] = 4e13
    pf1["inventory"][2].update(count=2**31 - 1, slots=40)                       # count far above slots
    pc2 = pbuilding(w, bf.B_PC2)
    pc2["production"]["produced_last_month"] = -5                                # schema: integer, no minimum
    pc2["inventory"][0].update(count=0, slots=0)
    pc2["inventory"][1].update(count=0, slots=0)
    pbuilding(w, bf.B_HQ)["upkeep"]["monthly_full"] = 0.0
    publish(h, w, "state")
    for sort in ("stock_ratio", "produced_last_month", "upkeep"):
        rows = ok(h, "list_buildings", {"sort": sort, "limit": 50})["data"]["buildings"]
        assert len(rows) == 13
    rows = {r["id"]: r for r in ok(h, "list_buildings", {"limit": 50})["data"]["buildings"]}
    assert rows[bid(bf.B_PF1)]["produced_last_month"] == 2**31 - 1
    assert rows[bid(bf.B_PC2)]["produced_last_month"] == -5
    assert rows[bid(bf.B_PC2)]["max_stock_ratio"] is None                        # D-INV-1 undefined for 0 slots
    assert rows[bid(bf.B_HQ)]["upkeep_monthly"] == 0.0
    inv = {i["product"]: i for i in ok(h, "get_building", {"building": bid(bf.B_PC2)})["data"]["inventory"]}
    assert inv["product:Gas"]["fill_ratio"] is None
    ok(h, "get_production_overview", {})
    ok(h, "find_production_issues", {"limit": 50})


# ============================================================================ get_building: resolution

@pytest.mark.parametrize("ref,key", [
    (bid(bf.B_PF1), bf.B_PF1), (bf.B_PF1, bf.B_PF1), (f"  {bid(bf.B_PF1)}  ", bf.B_PF1),
    ("USINE DE PEINTURE 2", bf.B_PF2), ("usine de peinture 2", bf.B_PF2), ("Usine De Peinture 2", bf.B_PF2),
    ("USINE PETROCHIMIQUE 2", bf.B_PC2), ("usine pétrochimique 2", bf.B_PC2), ("ENTREPOT 1", bf.B_WH),
    ("entrepôt 1", bf.B_WH), ("SIEGE BOREALIS", "Headquarters@140,110"), ("Dépôt de camions 1", bf.B_TD),
])
def test_get_building_resolution(h, ref, key):
    r = ok(h, "get_building", {"building": ref})
    assert r["data"]["identity"]["id"] == bid(key)                               # PRD 7.1 / 13.6


@pytest.mark.parametrize("ref", ["USINE DE PEINTURE 1", "usine de peinture 1", "PUITS DE GAZ 1", "USINE PÉTROCHIMIQUE 1",
                                 "usine chimique 1", "FERME FLORALE 1", "QUINCAILLERIE"])
def test_get_building_ambiguous_names(h, ref):
    r = err(h, "get_building", {"building": ref}, "ambiguous")                   # PRD 13.3 ambiguous
    cands = r["error"]["candidates"]
    assert 2 <= len(cands) <= 10 and len({c["id"] for c in cands}) == len(cands)
    for c in cands:
        assert c["id"].startswith("building:")
        assert {"type", "owner", "coordinates"} <= c.keys()                    # PRD 13.3: type, owner, coordinates


def test_get_building_ambiguous_candidates_capped_at_10(h):
    w = copy.deepcopy(h.world)
    keys = add_buildings(w, 15, name=lambda i: "USINE CLONE")
    publish(h, w, "state")
    r = err(h, "get_building", {"building": "usine clone"}, "ambiguous")
    assert len(r["error"]["candidates"]) == 10
    assert {c["id"] for c in r["error"]["candidates"]} <= {bid(k) for k in keys}
    assert "15" in r["error"]["message"]
    r2 = err(h, "get_building", {"building": "USINE CLONE"}, "ambiguous")
    assert r2["error"]["candidates"] == r["error"]["candidates"]                 # deterministic candidate order


@pytest.mark.parametrize("ref", ["building:PaintFactory@1,1", "PaintFactory@1,1", "Nope Factory", "USINE DE PEINTURE",
                                 "USINE DE PEINTURE 11", "building:PaintFactory@55,40#deadbeef"])
def test_get_building_unknown_not_found(h, ref):
    err(h, "get_building", {"building": ref}, "not_found")


@pytest.mark.parametrize("ref", ["BUILDING:PaintFactory@55,40", "building:paintfactory@55,40", "paintfactory@55,40"])
def test_get_building_case_variant_ids(h, ref):
    # PRD 7.1 / 13.6: ids and bare id keys are case-sensitive; a re-cased id is not_found.
    err(h, "get_building", {"building": ref}, "not_found")


@pytest.mark.parametrize("ref", ["building_type:PaintFactory", "product:Paint", "recipe:Paints", "city:10", "company:1",
                                 f"region:{bf.R_VAL}"])
def test_get_building_wrong_kind_id(h, ref):
    # PRD is silent on kind-mismatched ids; it must be an error, never another entity's data.
    err(h, "get_building", {"building": ref}, "not_found", "invalid_argument")


def test_get_building_collision_suffixed_keys(h):
    """PRD 7.2: colliding keys are suffixed #<guid8> or #i<instanceId>; each suffixed id is its own building."""
    w = copy.deepcopy(h.world)
    base = pbuilding(w, bf.B_WH)
    k1, k2 = "Warehouse@61,47#0badf00d", "Warehouse@61,47#i-4711"
    for k, name in ((k1, "ENTREPÔT JUMEAU"), (k2, "ENTREPÔT JUMEAU")):
        b = copy.deepcopy(base)
        b.update(key=k, x=61, y=47, display_name=name, save_guid="0badf00d-0000" if "#0" in k else None)
        w["state"]["data"]["buildings_player"].append(b)
    publish(h, w, "state")
    rows = ok(h, "list_buildings", {"kind": "warehouse", "limit": 50})["data"]["buildings"]
    assert sorted(r["id"] for r in rows) == sorted([bid(bf.B_WH), bid(k1), bid(k2)])
    for k in (k1, k2):
        g = ok(h, "get_building", {"building": bid(k)})["data"]
        assert g["identity"]["id"] == bid(k) and g["identity"]["coordinates"] == {"x": 61, "y": 47}
        assert ok(h, "get_building", {"building": k})["data"]["identity"]["id"] == bid(k)
    err(h, "get_building", {"building": "building:Warehouse@61,47"}, "not_found")
    r = err(h, "get_building", {"building": "entrepot jumeau"}, "ambiguous")
    assert {c["id"] for c in r["error"]["candidates"]} == {bid(k1), bid(k2)}
    issues = ok(h, "find_production_issues", {"limit": 50})["data"]["issues"]
    assert all(i["building"] not in (bid(k1), bid(k2)) or i["kind"] == "inventory_accumulating" for i in issues)


def test_get_building_city_shop(h):
    r = ok(h, "get_building", {"building": bid(bf.S_HW1)})
    d = r["data"]
    assert d["identity"]["id"] == bid(bf.S_HW1) and d["identity"]["owner"]["kind"] == "city"
    assert d["status"] is None and "status" in unavailable_fields(r)


# ============================================================================ get_building: include

def test_get_building_default_include_is_all_but_history(h):
    d = ok(h, "get_building", {"building": bid(bf.B_PF1)})["data"]
    for part in GET_BUILDING_PARTS:
        if part == "history":
            assert "history" not in d                                            # PRD 14.3 default excludes history
        else:
            assert part in d, part
    for k in ("identity", "status", "efficiency", "upkeep"):
        assert k in d
    ident = d["identity"]
    assert {"id", "save_guid", "display_name", "type", "english_type_name", "owner", "coordinates", "rotation", "region", "city",
            "paid_to_build"} <= ident.keys()
    assert ident["english_type_name"] == "Paint Factory" and ident["paid_to_build"] == 500000.0
    assert d["status"]["derived_status"] == "working" and d["status"]["method"] == "D-STATUS-1"


@pytest.mark.parametrize("part", GET_BUILDING_PARTS)
def test_get_building_single_include(h, part):
    d = ok(h, "get_building", {"building": bid(bf.B_PF1), "include": [part]})["data"]
    assert part in d
    for other in GET_BUILDING_PARTS:
        if other != part:
            assert other not in d, other
    assert "identity" in d and "status" in d


def test_get_building_all_includes(h):
    d = ok(h, "get_building", {"building": bid(bf.B_PF1), "include": GET_BUILDING_PARTS})["data"]
    assert all(p in d for p in GET_BUILDING_PARTS)


def test_get_building_include_empty_list(h):
    # PRD-ambiguity: include=[] is "a subset" (none) or "omitted" (default); only invariants are asserted.
    r = ok(h, "get_building", {"building": bid(bf.B_PF1), "include": []})
    assert "identity" in r["data"] and "history" not in r["data"]


def test_get_building_production_values(h):
    p = ok(h, "get_building", {"building": bid(bf.B_PF1), "include": ["production"]})["data"]["production"]
    assert p["recipe"] == "recipe:Paints" and p["cycle_days_base"] == 35.0 and p["cycle_days_effective"] == 35.0
    outs = {o["product"]: o for o in p["outputs"]}
    ins = {i["product"]: i for i in p["inputs"]}
    assert outs["Paint"]["per_30d"] == pytest.approx(2 * 30 / 35, abs=1e-5)          # D-RATE-1
    assert ins["Chemicals"]["per_30d"] == pytest.approx(30 / 35, abs=1e-5)
    assert ins["Dye"]["per_30d"] == pytest.approx(60 / 35, abs=1e-5)
    assert p["uptime_ratio"]["value"] == pytest.approx(0.85)                          # D-RATE-3 850/1000
    assert p["remaining_days"]["value"] == pytest.approx(17.5)
    assert (p["produced_this_month"], p["produced_last_month"], p["total_produced"]) == (10, 2, 500)


def test_get_building_gatherer_rate_d_rate_2(h):
    p = ok(h, "get_building", {"building": bid(bf.B_GW1), "include": ["production", "modules"]})["data"]
    out = p["production"]["outputs"][0]
    assert out["product"] == "Gas" and out["per_30d"] == pytest.approx(2 * 30 / 10 * (1.0 + 0.8))   # D-RATE-2
    assert p["production"]["theoretical_output_per_30d"]["method"] == "D-RATE-2"
    assert p["modules"]["count"] == 2 and len(p["modules"]["items"]) == 2


def test_get_building_inventory_values(h):
    inv = {i["product"]: i for i in ok(h, "get_building", {"building": bid(bf.B_PC1), "include": ["inventory"]})["data"]["inventory"]}
    gas = inv["product:Gas"]
    assert gas["role"] == "input" and gas["count"] == 9 and gas["slots"] == 40 and gas["incoming_reserved"] == 4
    assert gas["inbound_cap"] == 8 and gas["fill_ratio"] == pytest.approx(9 / 40)        # PRD 14.3 inbound_cap (Max Send)


def test_get_building_routes_and_requests(h):
    d = ok(h, "get_building", {"building": bid(bf.B_PC1), "include": ["incoming_routes", "outgoing_routes"]})["data"]
    incoming = {r["route_id"] for r in d["incoming_routes"]}
    assert incoming == {f"route:{bf.B_GW1}|Gas|{bf.B_PC1}|own|0", f"route:{bf.B_GW2}|Gas|{bf.B_PC1}|own|0",
                        f"route:{bf.B_GW3}|Gas|{bf.B_PC1}|own|0"}
    assert len(d["outgoing_routes"]) == 3
    assert all(r["max_send"]["scope"] == "destination_product_shared" for r in d["incoming_routes"])
    assert "route_semantics" in d
    wh = ok(h, "get_building", {"building": bid(bf.B_WH), "include": ["requests", "vehicles"]})["data"]
    assert {q["request_id"] for q in wh["requests"]} == {f"request:{bf.B_WH}|Paint|0", f"request:{bf.B_WH}|Chemicals|0"}
    td = ok(h, "get_building", {"building": bid(bf.B_TD), "include": ["vehicles"]})["data"]["vehicles"]
    assert td["fleet"]["active"] == 3 and all(v["id"].startswith(f"vehicle:{bf.WORLD_SESSION}:") for v in td["vehicles"])


def test_get_building_empty_collections(h):
    d = ok(h, "get_building", {"building": bid(bf.B_PF2), "include": [p for p in GET_BUILDING_PARTS if p != "history"]})["data"]
    assert d["inventory"] == [] and d["outgoing_routes"] == [] and d["incoming_routes"] == [] and d["requests"] == []
    assert d["status"]["derived_status"] == "no_recipe"
    hq = ok(h, "get_building", {"building": bid(bf.B_HQ)})["data"]
    assert hq["production"] is None and hq["modules"] is None


def test_get_building_no_routes_sections_listed_unavailable(h):
    w = copy.deepcopy(h.world)
    fail_section(w, "state", "routes_player")
    fail_section(w, "state", "requests_player", "disabled", "kill_switch")
    fail_section(w, "state", "vehicles")
    publish(h, w, "state")
    r = ok(h, "get_building", {"building": bid(bf.B_PC1)})
    un = unavailable_fields(r)
    assert {"routes_player", "requests_player", "vehicles"} <= set(un)                 # PRD 13.5 unavailable
    assert r["data"]["outgoing_routes"] == [] and r["data"]["incoming_routes"] == []
    assert set(r["meta"]["snapshot"]["sections_unavailable"]) >= {"routes_player", "requests_player", "vehicles"}


def test_get_building_ai_compact_missing_parts_in_unavailable(h):
    r = ok(h, "get_building", {"building": bid(bf.AI_PF)})
    un = unavailable_fields(r)
    assert r["data"]["identity"]["detail"] == "compact"
    assert "inventory" in un and r["data"]["inventory"] is None                 # PRD 14.3 notes
    assert {"efficiency", "upkeep"} <= set(un)


# Regression test for DEFECT BU-1 (fixed).
def test_get_building_ai_outgoing_routes_unavailable_when_routes_ai_off(h):
    r = ok(h, "get_building", {"building": bid(bf.AI_PF)})
    assert h.world["state"]["sections"]["routes_ai"]["status"] == "skipped"
    un = unavailable_fields(r)
    assert r["data"]["outgoing_routes"] is None or any("route" in f for f in un), (r["data"]["outgoing_routes"], un)


# Regression test for DEFECT BU-7 (fixed).
def test_get_building_player_section_failed_is_section_unavailable(h):
    w = copy.deepcopy(h.world)
    fail_section(w, "state", "buildings_player")
    publish(h, w, "state")
    err(h, "get_building", {"building": bid(bf.B_PF1)}, "section_unavailable")


# ============================================================================ get_building: history

WINDOW_KEYS = ("window_months", "window_first_month", "window_last_month", "history_truncated", "first_month_available")


def test_get_building_history_window_passthrough(h):
    w = copy.deepcopy(h.world)
    hist = w["history"]["data"]
    s = hist["production_monthly_player"][1]                                        # PC1 / Chemicals
    s.update(window_months=24, window_first_month="Y3-04", window_last_month="Y5-03", history_truncated=None,
             first_month_available=None)
    a = hist["buildings_monthly_player"][1]["series"][0]                            # PC1 analysis
    a.update(window_months=24, window_first_month="Y3-04", window_last_month="Y5-03", history_truncated=True,
             first_month_available="Y1-01")
    publish(h, w, "history")
    d = ok(h, "get_building", {"building": bid(bf.B_PC1), "include": ["history"]})["data"]["history"]
    prod = {p["product"]: p for p in d["production_monthly"]}
    assert set(prod) == {"product:Chemicals", "product:Gas"}
    assert {k: prod["product:Chemicals"][k] for k in WINDOW_KEYS} == {k: s[k] for k in WINDOW_KEYS}   # SNAPSHOT-FORMAT windows
    assert prod["product:Chemicals"]["months"] == s["months"]
    an = d["monthly_analysis"][0]
    assert {k: an[k] for k in WINDOW_KEYS} == {k: a[k] for k in WINDOW_KEYS}
    assert an["aggregation"] == "sum" and an["values_retained"] == len(bf.MONTHS)
    assert "bounded recent window" in d["window_note"]


def test_get_building_history_meta_reports_both_families(h):
    r = ok(h, "get_building", {"building": bid(bf.B_PF1), "include": ["history", "production"]})
    snaps = {s["family"]: s for s in r["meta"]["snapshots"]}
    # PRD 13.7: both families reported, history with its own seq and age.
    # PRD 13.3: meta.snapshots lists every family used (state, history, static); meta.snapshot = snapshots[0].
    assert snaps["state"]["seq"] == h.world["state"]["seq"] and snaps["history"]["seq"] == h.world["history"]["seq"]
    assert snaps["history"]["age_s"] == pytest.approx(5.0) and snaps["state"]["age_s"] == pytest.approx(2.0)
    assert r["meta"]["snapshot"]["family"] == "state"
    r2 = ok(h, "get_building", {"building": bid(bf.B_PF1)})
    assert "history" not in {s["family"] for s in r2["meta"]["snapshots"]}           # history only read when included


def test_get_building_history_for_building_without_series(h):
    r = ok(h, "get_building", {"building": bid(bf.B_GW1), "include": ["history"]})
    d = r["data"]["history"]
    assert d["monthly_analysis"] is None and d["production_monthly"] == []
    assert "history.monthly_analysis" in unavailable_fields(r)


def test_get_building_history_missing_file(h):
    (h.exchange / "history.json").unlink()
    r = ok(h, "get_building", {"building": bid(bf.B_PF1), "include": ["history", "production"]})
    assert r["data"]["history"] is None
    assert any("history" in f for f in unavailable_fields(r))                       # PRD 13.5
    assert "history" not in {s["family"] for s in r["meta"]["snapshots"]}
    assert r["data"]["production"]["recipe"] == "recipe:Paints"                      # state part still served
    assert r["meta"]["stale"] is False


@pytest.mark.parametrize("text", ["", "{not json", "[]", '{"schema": "roi-mcp/state"}'])
def test_get_building_history_invalid_file(h, text):
    h.write_raw("history", text)
    r = ok(h, "get_building", {"building": bid(bf.B_PF1), "include": ["history"]})
    assert r["data"]["history"] is None and any("history" in f for f in unavailable_fields(r))


def test_get_building_history_schema_mismatch(h):
    w = copy.deepcopy(h.world)
    w["history"]["schema_version"] = "2.0.0"
    publish(h, w, "history")
    r = ok(h, "get_building", {"building": bid(bf.B_PF1), "include": ["history"]})
    assert r["data"]["history"] is None and any("history" in f for f in unavailable_fields(r))


def test_get_building_history_other_world_session(h):
    w = copy.deepcopy(h.world)
    w["history"]["world_session"] = bf.WORLD_SESSION_2
    publish(h, w, "history")
    r = ok(h, "get_building", {"building": bid(bf.B_PF1), "include": ["history"]})
    assert r["data"]["history"] is None and r["meta"]["stale"] is False             # never served as current (PRD 16)
    r = ok(h, "get_building", {"building": bid(bf.B_PF1), "include": ["history"], "allow_stale": True})
    assert r["data"]["history"] is not None
    assert r["meta"]["stale"] is True and r["meta"]["stale_reason"] == "world_session_changed"   # never silently stale
    snaps = {s["family"]: s for s in r["meta"]["snapshots"]}
    assert snaps["history"]["stale"] is True and snaps["state"]["stale"] is False


def test_get_building_history_stale_by_age(h):
    w = copy.deepcopy(h.world)
    data = bf.history_data()
    data["state_sales"][0]["sold_last_30d"] = 41          # new content: the heartbeat's last_verified does not apply
    w["history"] = bf.build_history(h.clock.now(), seq=w["history"]["seq"] + 1, static_doc=w["static"], data=data,
                                    capture_age_s=120.0)
    h.write_world(w, ("history",))
    r = ok(h, "get_building", {"building": bid(bf.B_PF1), "include": ["history"]})
    snaps = {s["family"]: s for s in r["meta"]["snapshots"]}
    assert snaps["history"]["stale"] is True and snaps["history"]["stale_reason"] == "age"
    assert r["meta"]["stale"] is True                                                  # PRD 13.4 never silently stale


def test_get_building_history_section_skipped_analysis(h):
    w = copy.deepcopy(h.world)
    fail_section(w, "history", "buildings_monthly_player", "skipped", "size_cap")
    publish(h, w, "history")
    r = ok(h, "get_building", {"building": bid(bf.B_PF1), "include": ["history"]})
    assert r["data"]["history"]["monthly_analysis"] is None
    assert any("monthly_analysis" in f or "buildings_monthly_player" in f for f in unavailable_fields(r))


# Regression test for DEFECT BU-3 (fixed).
def test_get_building_history_section_skipped_production(h):
    w = copy.deepcopy(h.world)
    fail_section(w, "history", "production_monthly_player", "skipped", "size_cap")   # SNAPSHOT-FORMAT size caps
    publish(h, w, "history")
    r = ok(h, "get_building", {"building": bid(bf.B_PF1), "include": ["history"]})
    un = unavailable_fields(r)
    assert r["data"]["history"]["production_monthly"] is None or any("production_monthly" in f for f in un), un


def test_get_building_fresh_with_history_writes_only_state(tmp_path, clock, procs):
    hh, obs = _observer_harness(tmp_path, clock, procs)
    r = ok(hh, "get_building", {"building": bid(bf.B_PF1), "include": ["history", "production"], "fresh": True})
    req = obs.read_refresh_request()
    assert {k for k, v in req["requests"].items() if v is not None} == {"state"}       # PRD 13.7 mixed-tool rule
    snaps = {s["family"]: s for s in r["meta"]["snapshots"]}
    assert snaps["state"]["seq"] == obs.state_seq and snaps["history"]["seq"] == obs.history_doc["seq"] == 1
    assert "refresh_timeout" not in warn_codes(r)
    assert r["data"]["history"]["monthly_analysis"]


def test_get_building_fresh_timeout(tmp_path, clock, procs):
    hh, obs = _observer_harness(tmp_path, clock, procs, wait=1.0, serve=False)
    r = ok(hh, "get_building", {"building": bid(bf.B_PF1), "include": ["history"], "fresh": True})
    assert "refresh_timeout" in warn_codes(r)
    assert {k for k, v in obs.read_refresh_request()["requests"].items() if v is not None} == {"state"}


@pytest.mark.parametrize("name", ["list_buildings", "get_production_overview", "find_production_issues"])
def test_fresh_writes_state_scope(tmp_path, clock, procs, name):
    hh, obs = _observer_harness(tmp_path, clock, procs)
    r = ok(hh, name, {"fresh": True})
    assert {k for k, v in obs.read_refresh_request()["requests"].items() if v is not None} == {"state"}
    assert r["meta"]["snapshot"]["seq"] == obs.state_seq


# ============================================================================ stale / lifecycle

def _args(name):
    return {"building": bid(bf.B_PF1)} if name == "get_building" else {}


@pytest.mark.parametrize("name", TOOLS)
def test_stale_by_age_still_served_and_flagged(h, name):
    w = copy.deepcopy(h.world)
    later_heartbeat(h, w, 40)
    r = ok(h, name, _args(name))
    assert r["meta"]["stale"] is True and r["meta"]["stale_reason"] == "age"          # PRD 13.4 / 16
    assert r["meta"]["source"] in ("stale_snapshot", "live_snapshot")
    assert "stale" in warn_codes(r)


@pytest.mark.parametrize("name", TOOLS)
def test_game_not_running(h, name):
    h.procs.procs = []
    r = err(h, name, _args(name), "game_not_running")
    assert r["meta"]["source"] == "none"
    r = ok(h, name, {**_args(name), "allow_stale": True})
    assert r["meta"]["source"] == "stale_snapshot" and r["meta"]["stale"] is True
    assert r["meta"]["stale_reason"] == "game_not_running"


@pytest.mark.parametrize("hb_state,code,reason", [("menu", "at_main_menu", "not_in_game"), ("loading", "loading", "not_in_game")])
@pytest.mark.parametrize("name", TOOLS)
def test_lifecycle_states(h, name, hb_state, code, reason):
    w = copy.deepcopy(h.world)
    w["heartbeat"] = bf.build_heartbeat(h.clock.now(), state=hb_state, seq=w["heartbeat"]["seq"] + 1)
    h.write_world(w, ("heartbeat",))
    err(h, name, _args(name), code)
    r = ok(h, name, {**_args(name), "allow_stale": True})
    assert r["meta"]["stale"] is True and r["meta"]["stale_reason"] == reason and r["meta"]["source"] == "stale_snapshot"


@pytest.mark.parametrize("name", TOOLS)
def test_quickload_other_world_session(h, name):
    w = copy.deepcopy(h.world)
    w["heartbeat"] = bf.build_heartbeat(h.clock.now(), world_session=bf.WORLD_SESSION_2, seq=w["heartbeat"]["seq"] + 1)
    h.write_world(w, ("heartbeat",))
    err(h, name, _args(name), "snapshot_unavailable")                                  # PRD 16 quickload row
    r = ok(h, name, {**_args(name), "allow_stale": True})
    assert r["meta"]["stale_reason"] == "world_session_changed" and r["meta"]["stale"] is True


@pytest.mark.parametrize("name", TOOLS)
def test_no_state_snapshot_yet(h, name):
    (h.exchange / "state.json").unlink()
    err(h, name, _args(name), "snapshot_unavailable")


@pytest.mark.parametrize("name", TOOLS)
def test_paused_flag_passthrough(h, name):
    w = copy.deepcopy(h.world)
    w["heartbeat"] = bf.build_heartbeat(h.clock.now(), paused=True, static_doc=w["static"], state_doc=w["state"],
                                        history_doc=w["history"], seq=w["heartbeat"]["seq"] + 1)
    h.write_world(w, ("heartbeat",))
    r = ok(h, name, _args(name))
    assert r["meta"]["paused"] is True and r["meta"]["stale"] is False                # PRD 16 paused row


def test_unsupported_build(h):
    w = copy.deepcopy(h.world)
    det = dict(bf.GAME, build="0600a")
    w["heartbeat"] = bf.build_heartbeat(h.clock.now(), state="unsupported_build", compatibility="unsupported_build",
                                        detected_game=det, envelope_game=det, seq=w["heartbeat"]["seq"] + 1)
    h.write_world(w, ("heartbeat",))
    for name in TOOLS:
        err(h, name, _args(name), "unsupported_build")
        err(h, name, {**_args(name), "allow_stale": True}, "unsupported_build")


@pytest.mark.parametrize("name", TOOLS)
def test_restart_yields_identical_answers(h, name):
    args = {"building": bid(bf.B_PF1), "include": GET_BUILDING_PARTS} if name == "get_building" else {}
    a = ok(h, name, args)
    h.restart()
    b = ok(h, name, args)
    assert a["data"] == b["data"] and a["page"] == b["page"]                            # PRD 13.1
    assert a["meta"]["snapshots"] == b["meta"]["snapshots"]


# ============================================================================ get_production_overview

def _products(resp):
    return {p["product"]: p for p in resp["data"]["products"]}


def test_production_overview_player_values(h):
    r = ok(h, "get_production_overview")
    assert r["data"]["company"] == "company:1"
    p = _products(r)
    assert set(p) == {"product:Gas", "product:Water", "product:Flowers", "product:Chemicals", "product:Dye", "product:Paint"}
    gas = p["product:Gas"]
    # D-RATE-2: GW1 2 x 3 x (1.0 + 0.8) = 10.8; GW2 2 x 3 x 3 modules (one approximate) = 18; GW3 disabled -> 0
    assert gas["theoretical_supply_per_30d"] == pytest.approx(28.8)
    assert gas["theoretical_demand_internal_per_30d"] == pytest.approx(9.0)              # 2 x Chemicals 3 x 30 / 20
    assert gas["produced_last_month"] == 246 and gas["stock_total"] == 52
    assert gas["shops_demand_total"] is None and gas["balance_per_30d"] == pytest.approx(19.8)
    assert gas["supply_demand_ratio"]["value"] == pytest.approx(3.2)                      # D-SUPDEM-1
    assert {x["building"] for x in gas["producers"]} == {bid(bf.B_GW1), bid(bf.B_GW2), bid(bf.B_GW3)}
    assert {x["building"] for x in gas["consumers"]} == {bid(bf.B_PC1), bid(bf.B_PC2)}
    paint = p["product:Paint"]
    sd = paint["shops_demand_total"]
    # demand_for_player per interval: 10 + 8 (30 d) + 6 (15 d) + 5 (10 d) = 29; per 30 d: 10 + 8 + 12 + 15 = 45
    assert sd["per_interval"] == 29 and sd["per_30d"] == pytest.approx(45.0) and sd["shops"] == 4
    assert paint["balance_per_30d"] == pytest.approx(2 * 30 / 35 - 45, abs=1e-3)
    assert paint["stock_total"] == 53


def test_production_overview_ai_company(h):
    r = ok(h, "get_production_overview", {"company": "Borealis Corp"})
    assert r["data"]["company"] == "company:2"
    p = _products(r)
    assert {x["building"] for x in p["product:Paint"]["producers"]} == {bid(bf.AI_PF)}
    assert p["product:Gas"]["theoretical_supply_per_30d"] == pytest.approx(18.0)        # D-RATE-2 fallback, 3 modules
    sd = p["product:Paint"]["shops_demand_total"]
    assert sd["basis"] == "demand_raw" and sd["per_interval"] == 12 + 9 + 7 + 5
    assert ok(h, "get_production_overview", {"company": "company:2"})["data"] == r["data"]


@pytest.mark.parametrize("company", ["player", "company:1", "Acme Industries", "ACME INDUSTRIES"])
def test_production_overview_company_aliases(h, company):
    assert ok(h, "get_production_overview", {"company": company})["data"]["company"] == "company:1"


@pytest.mark.parametrize("company", ["Nobody", "company:42", "city:10"])
def test_production_overview_unknown_company(h, company):
    err(h, "get_production_overview", {"company": company}, "not_found")


@pytest.mark.parametrize("product", ["Paint", "product:Paint", "peinture", "PEINTURE"])
def test_production_overview_product_filter(h, product):
    r = ok(h, "get_production_overview", {"product": product})
    assert [p["product"] for p in r["data"]["products"]] == ["product:Paint"]


def test_production_overview_product_filter_zero_and_unknown(h):
    r = ok(h, "get_production_overview", {"company": "company:3", "product": "Gas"})
    assert r["data"]["products"] == []
    err(h, "get_production_overview", {"product": "Unobtainium"}, "not_found")
    err(h, "get_production_overview", {"product": "recipe:Paints"}, "not_found")


def test_production_overview_deterministic_order(h):
    a = ok(h, "get_production_overview")
    names = [p["product"] for p in a["data"]["products"]]
    assert names == sorted(names)
    h.restart()
    assert ok(h, "get_production_overview")["data"] == a["data"]


def test_production_overview_empty_company(h):
    w = copy.deepcopy(h.world)
    w["state"]["data"]["buildings_player"] = []
    publish(h, w, "state")
    assert ok(h, "get_production_overview")["data"]["products"] == []


def test_production_overview_shops_section_unavailable(h):
    w = copy.deepcopy(h.world)
    fail_section(w, "state", "shops")
    publish(h, w, "state")
    r = ok(h, "get_production_overview")
    assert all(p["shops_demand_total"] is None for p in r["data"]["products"])
    assert "shops" in unavailable_fields(r) or "shops_demand_total" in unavailable_fields(r)   # PRD 13.5


@pytest.mark.parametrize("sec", ["buildings_player"])
def test_production_overview_player_section_unavailable(h, sec):
    w = copy.deepcopy(h.world)
    fail_section(w, "state", sec)
    publish(h, w, "state")
    r = err(h, "get_production_overview", {}, "section_unavailable")
    assert sec in r["error"]["message"]
    ok(h, "get_production_overview", {"company": "company:2"})                      # affected params only


def test_production_overview_ai_section_unavailable(h):
    w = copy.deepcopy(h.world)
    fail_section(w, "state", "buildings_ai")
    publish(h, w, "state")
    err(h, "get_production_overview", {"company": "company:2"}, "section_unavailable")
    ok(h, "get_production_overview")


# Regression test for DEFECT BU-4 (fixed).
def test_production_overview_unknown_consumption_interval(h):
    w = copy.deepcopy(h.world)
    for c in w["state"]["data"]["cities"]:
        c["consumption_interval_days"] = None                                     # state schema: integer|null
    publish(h, w, "state")
    r = ok(h, "get_production_overview", {"product": "Paint"})
    sd = r["data"]["products"][0]["shops_demand_total"]
    assert sd["per_interval"] == 29
    # D-SHOP-1 / 14.3: per-30-day normalization needs the interval; it must be null or reported unavailable, not 0.
    assert sd["per_30d"] is None or any("shops_demand" in f for f in unavailable_fields(r)), (sd, unavailable_fields(r))


# Regression test for DEFECT BU-6 (fixed).
def test_production_overview_keeps_producer_with_unknown_cycle(h):
    w = copy.deepcopy(h.world)
    pbuilding(w, bf.B_PC1)["cycle_days_effective"] = None                          # state schema: number|null
    publish(h, w, "state")
    p = _products(ok(h, "get_production_overview"))
    producers = {x["building"]: x for x in p["product:Chemicals"]["producers"]}
    assert bid(bf.B_PC1) in producers, sorted(producers)
    assert producers[bid(bf.B_PC1)]["theoretical_per_30d"] is None
    assert p["product:Chemicals"]["produced_last_month"] == 3


def test_production_overview_disabled_producer_contributes_zero(h):
    gas = _products(ok(h, "get_production_overview"))["product:Gas"]
    gw3 = next(x for x in gas["producers"] if x["building"] == bid(bf.B_GW3))
    assert gw3["produced_last_month"] == 0 and gw3.get("enabled") is False


def test_production_overview_large_world_under_size_cap(tmp_path, clock, procs, world):
    hh = Harness(tmp_path, clock, procs)
    add_buildings(world, 400)
    world["state"]["content_hash"] = bf.content_hash(world["state"]["data"])
    hh.write_world(world)
    r = ok(hh, "get_production_overview")                                            # check_response asserts <= 30 KB
    assert "truncated" in warn_codes(r)                                                # PRD 14.9
    paint = ok(hh, "get_production_overview", {"product": "Paint"})
    p = _products(paint)["product:Paint"]
    assert p["theoretical_supply_per_30d"] == pytest.approx(401 * 2 * 30 / 35, abs=0.01)   # totals use every producer


# Regression test for DEFECT BU-8 (fixed).
def test_production_overview_truncation_keeps_products_reachable(tmp_path, clock, procs, world):
    hh = Harness(tmp_path, clock, procs)
    add_buildings(world, 400)
    world["state"]["content_hash"] = bf.content_hash(world["state"]["data"])
    hh.write_world(world)
    r = ok(hh, "get_production_overview")
    # PRD 14.9: larger results paginate or truncate WITH page.next_cursor; the 6 products must stay reachable.
    assert len(r["data"]["products"]) == 6 or r["page"]["next_cursor"] is not None,         ([p["product"] for p in r["data"]["products"]], r["page"], warn_codes(r))


# ============================================================================ find_production_issues

FIXTURE_ISSUES = {
    ("disabled", bf.B_GW3, None), ("no_recipe", bf.B_PF2, None), ("missing_input", bf.B_PC2, "Gas"),
    ("no_modules", bf.B_GW3, None), ("polluted", bf.B_CP, None), ("route_error", bf.B_PF1, "Paint"),
    ("route_dormant_auto_wh", bf.B_GW3, "Gas"), ("route_keep_all", bf.B_GW2, "Gas"),
    ("inventory_accumulating", bf.B_PF1, "Paint"),
}


def _issue_set(resp):
    return {(i["kind"], i["building"].split(":", 1)[1], i["product"].split(":", 1)[1] if i["product"] else None)
            for i in resp["data"]["issues"]}


def test_find_issues_fixture(h):
    r = ok(h, "find_production_issues", {"limit": 50})
    assert _issue_set(r) == FIXTURE_ISSUES                                          # D-STATUS-1 / D-INV-1 / route fields
    assert r["page"]["total"] == 9 and r["data"]["company"] == "company:1"
    for i in r["data"]["issues"]:
        assert i["since"] is None and isinstance(i["evidence"], dict)
        if i["kind"].startswith("route_"):
            assert i["route"] and i["route"].startswith("route:")
    mi = next(i for i in r["data"]["issues"] if i["kind"] == "missing_input")
    assert mi["evidence"]["count"] == 1 and mi["evidence"]["needed_per_cycle"] == 3
    assert [x["route_id"] for x in mi["evidence"]["inbound_routes"]] == [f"route:{bf.B_GW2}|Gas|{bf.B_PC2}|own|0"]


@pytest.mark.parametrize("kind", sorted({k for k, _, _ in FIXTURE_ISSUES} | {"output_full", "deposit_depleted",
                                                                               "requirements_unmet"}))
def test_find_issues_single_kind(h, kind):
    r = ok(h, "find_production_issues", {"kinds": [kind], "limit": 50})
    assert _issue_set(r) == {x for x in FIXTURE_ISSUES if x[0] == kind}


def test_find_issues_multiple_kinds_and_product(h):
    r = ok(h, "find_production_issues", {"kinds": ["disabled", "route_keep_all", "polluted"]})
    assert _issue_set(r) == {x for x in FIXTURE_ISSUES if x[0] in ("disabled", "route_keep_all", "polluted")}
    r = ok(h, "find_production_issues", {"product": "Gas", "limit": 50})
    got = _issue_set(r)
    assert {("missing_input", bf.B_PC2, "Gas"), ("route_dormant_auto_wh", bf.B_GW3, "Gas"),
            ("route_keep_all", bf.B_GW2, "Gas")} <= got
    assert all(p in (None, "Gas") for _, _, p in got)
    assert ("polluted", bf.B_CP, None) not in got and ("route_error", bf.B_PF1, "Paint") not in got
    r = ok(h, "find_production_issues", {"product": "product:Paint", "kinds": ["inventory_accumulating"]})
    assert _issue_set(r) == {("inventory_accumulating", bf.B_PF1, "Paint")}


def test_find_issues_kinds_empty_list(h):
    # PRD-ambiguity: kinds=[] could mean "no kinds" or "default (all)"; only invariants are asserted.
    ok(h, "find_production_issues", {"kinds": []})


def test_find_issues_product_unknown(h):
    err(h, "find_production_issues", {"product": "Unobtainium"}, "not_found")
    err(h, "find_production_issues", {"company": "Nobody"}, "not_found")


def test_find_issues_more_derived_kinds(h):
    w = copy.deepcopy(h.world)
    pf1 = pbuilding(w, bf.B_PF1)
    pf1["inventory"][2]["count"] = 39                                  # slots 40 - 39 < 2 per cycle -> output_full (15.6 step 6)
    pc1 = pbuilding(w, bf.B_PC1)
    pc1["flags"]["requirements_met"] = False                           # 15.6 step 4
    gw1 = pbuilding(w, bf.B_GW1)
    for m in gw1["modules"]["items"]:
        m["deposit_remaining"] = 0                                     # 15.6 step 8
    publish(h, w, "state")
    got = _issue_set(ok(h, "find_production_issues", {"limit": 50}))
    assert ("output_full", bf.B_PF1, "Paint") in got
    assert ("requirements_unmet", bf.B_PC1, None) in got
    assert ("deposit_depleted", bf.B_GW1, None) in got


def test_find_issues_ai_company(h):
    r = ok(h, "find_production_issues", {"company": "company:3"})
    assert r["data"]["company"] == "company:3"
    assert all(i["building"] in {bid(k) for k in AI_KEYS_C} for i in r["data"]["issues"])


def test_find_issues_pagination_and_order(h):
    full = ok(h, "find_production_issues", {"limit": 50})["data"]["issues"]
    rows, pages, totals = all_pages(h, "find_production_issues", {"limit": 2}, "issues")
    assert rows == full and pages == 5 and totals == {9}
    h.restart()
    assert ok(h, "find_production_issues", {"limit": 50})["data"]["issues"] == full
    cur = ok(h, "find_production_issues", {"limit": 2})["page"]["next_cursor"]
    err(h, "find_production_issues", {"limit": 2, "cursor": cur, "product": "Gas"}, "invalid_argument")


def test_find_issues_large_world_paging(tmp_path, clock, procs, world):
    hh = Harness(tmp_path, clock, procs)
    keys = add_buildings(world, 140)
    for b in world["state"]["data"]["buildings_player"]:
        if b["key"] in keys:
            b["flags"]["user_enabled"] = False
            b["is_polluted"] = True
    world["state"]["content_hash"] = bf.content_hash(world["state"]["data"])
    hh.write_world(world)
    first = ok(hh, "find_production_issues")
    assert len(first["data"]["issues"]) == 25 and first["page"]["next_cursor"]       # default limit 25
    rows, _, totals = all_pages(hh, "find_production_issues", {"limit": 50}, "issues")
    assert len(rows) == totals.pop() == 9 + 140 * 3                                   # disabled + polluted + accumulating
    keys_seen = [(r["kind"], r["building"], r["product"], r["route"]) for r in rows]
    assert len(set(keys_seen)) == len(keys_seen)


def test_find_issues_empty_world(h):
    w = copy.deepcopy(h.world)
    w["state"]["data"]["buildings_player"] = []
    w["state"]["data"]["routes_player"] = []
    w["state"]["data"]["requests_player"] = []
    publish(h, w, "state")
    r = ok(h, "find_production_issues")
    assert r["data"]["issues"] == [] and r["page"]["total"] == 0


def test_find_issues_buildings_section_unavailable(h):
    w = copy.deepcopy(h.world)
    fail_section(w, "state", "buildings_player")
    publish(h, w, "state")
    r = err(h, "find_production_issues", {}, "section_unavailable")
    assert "buildings_player" in r["error"]["message"]


# Regression test for DEFECT BU-2 (fixed).
def test_find_issues_routes_section_unavailable_is_reported(h):
    w = copy.deepcopy(h.world)
    fail_section(w, "state", "routes_player")
    publish(h, w, "state")
    r = call(h, "find_production_issues", {"kinds": ["route_error", "route_dormant_auto_wh", "route_keep_all"]})
    if not r["ok"]:
        assert r["error"]["code"] == "section_unavailable"
        return
    assert "routes_player" in unavailable_fields(r) or "routes_player" in r["meta"]["snapshot"]["sections_unavailable"], \
        (r["data"]["issues"], unavailable_fields(r))
