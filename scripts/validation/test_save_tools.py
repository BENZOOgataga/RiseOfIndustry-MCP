"""
Unit tests for scripts/validation/save_tools.py. Synthetic inputs only (no saves).

    uv run --with pytest --with lz4 pytest scripts/validation
"""

import copy
import os
from pathlib import Path

import pytest
import save_tools as st

PLAYER = "11111111-1111-1111-1111-111111111111"
AI = "22222222-2222-2222-2222-222222222222"
TOWN = "33333333-3333-3333-3333-333333333333"


# ---------------------------------------------------------------------------
# synthetic trees in the research parser's shape
# ---------------------------------------------------------------------------


def comp(t, guid, **fields):
    return {"$kind": "component", "objectType": "ProjectAutomata." + t, "fields": fields, "guid": guid}


def building(guid, prefab, x, y, owner, comps=(), rot=0, name="B"):
    return {
        "$kind": "entity_inline", "objectType": "ProjectAutomata.Building", "guid": guid,
        "fields": {"buildingName": name, "paidToBuild": 10.0},
        "components": list(comps),
        "constructorParams": [{"$prefab": prefab, "type": "ProjectAutomata.Building"}, x, y,
                              {"$enum": "ProjectAutomata.Direction", "value": rot}, owner, None],
    }


def slot(dest=None, product=None, source=None, min_keep=0, auto=False, paused=False, wait=False):
    return {"$kind": "object", "objectType": "ProjectAutomata.ManualDestinationSlot", "fields": {
        "destinationManager": "x", "activeRequests": None, "paused": paused, "_destination": dest, "_source": source,
        "_product": {"$prefab": product, "type": "ProjectAutomata.ProductDefinition"} if product else None,
        "_waitTillVehicleFull": wait, "_minStoredAtSource": min_keep, "_autoMaxAccepted": auto}}


def fixed(decoded):
    return {"$fixed": True, "decoded": decoded, "consumed_all": True}


def make_tree():
    actors = [
        {"objectType": "ProjectAutomata.State", "guid": "00000000-0000-0000-0000-000000000001", "fields": {"_actorName": "State"},
         "components": [], "constructorParams": [1]},
        {"objectType": "ProjectAutomata.HumanPlayer", "guid": PLAYER, "fields": {"_actorName": "Co"}, "components": [],
         "constructorParams": [2]},
        {"objectType": "ProjectAutomata.Settlement", "guid": TOWN, "fields": {"_settlementName": "Town"}, "components": [],
         "constructorParams": [{"$prefab": "Settlement"}, 3]},
        {"objectType": "ProjectAutomata.AiPlayer", "guid": AI, "fields": {"_actorName": "Rival"}, "components": [],
         "constructorParams": [{"$prefab": "AiPlayer"}, {"$prefab": "P"}, "Rival", {"x": 1}, 11]},
    ]
    shop_logi = "aaaaaaaa-0000-0000-0000-00000000000b"
    wh_logi = "bbbbbbbb-0000-0000-0000-00000000000b"
    buildings = [
        building("f0000000-0000-0000-0000-000000000001", "Farm", 10, 20, PLAYER, [
            comp("ManualDestinationManager", "f0000000-0000-0000-0000-0000000000aa", _savegameSlots=[
                slot(),  # empty slot (index 0)
                slot(dest=shop_logi, product="Wheat", min_keep=4),
                slot(dest=shop_logi, product="Wheat", auto=True),
                slot(dest=wh_logi, product="Wheat", paused=True, wait=True),
            ]),
        ], rot=2),
        building("a0000000-0000-0000-0000-000000000002", "Shop", 30, 40, TOWN, [
            comp("BuildingLogistics", shop_logi),
            comp("Shop", "a0000000-0000-0000-0000-0000000000cc",
                 _deliveredByActors=fixed([{"actorId": 2, "products": {"Wheat": 3}}])),
            comp("ProductSpecificProductStorage", "a0000000-0000-0000-0000-0000000000dd",
                 _maxAcceptedMap=fixed([{"product": "Wheat", "value": 8}])),
        ]),
        building("b0000000-0000-0000-0000-000000000003", "Warehouse", 50, 60, PLAYER, [
            comp("BuildingLogistics", wh_logi),
            comp("WarehouseManualDestinationManager", "b0000000-0000-0000-0000-0000000000ee", _savegameSlots=[]),
        ]),
        building("c0000000-0000-0000-0000-000000000004", "Mine", 70, 80, AI),
        building("d0000000-0000-0000-0000-000000000005", "Park 1", 90, 90, PLAYER, [comp("DecorationVisualization", "dd")]),
    ]
    return {
        "header": {"name": "save", "timestamp": 0, "saveFormatVersion": 2304, "savegameBuild": "0507b", "mods": [], "module": "m"},
        "metadata": {"createdSavegameBuild": "0507b"},
        "camera": {"position": [1, 2, 3], "rotation": [0, 0, 0, 1], "zoom": 1.0, "mode": None},
        "gameMode": {"achievementsEnabled": True},
        "managers": {
            "ProjectAutomata.ActorManager": {"fields": {}, "entities": actors},
            "ProjectAutomata.BuildingManager": {"fields": {}, "entities": buildings},
            "ProjectAutomata.VehicleManager": {"fields": {}, "entities": []},
            "ProjectAutomata.MoneyManager": {"fields": {"_balances": fixed([{"actor": PLAYER, "balance": 1.0}, {"actor": AI, "balance": 2.0}])}, "entities": []},
            "ProjectAutomata.EndGameManager": {"fields": {"usedCheats": False}, "entities": []},
            "ProjectAutomata.ScenarioManager": {"fields": {"usedCheats": False}, "entities": []},
            "ProjectAutomata.GameParametersManager": {"fields": {"world": {"$nrbf": "W", "value": {"enableAchievements": True}}}, "entities": []},
            "ProjectAutomata.TimeManager": {"fields": {"_days": 27893, "_months": 929}, "entities": []},
            "ProjectAutomata.CameraRotationController": {"fields": {"targetYaw": 0.0}, "entities": []},
        },
        "savegameVersion": 2304,
    }


# ---------------------------------------------------------------------------
# keys
# ---------------------------------------------------------------------------


def test_building_key_format():
    assert st.building_key("TextileFactory", 45, 388) == "TextileFactory@45,388"
    assert st.building_key(None, 1, 2) == "unknown@1,2"


def test_collision_suffix_and_base_key():
    g = "8431aae2-efa5-4fc5-ba84-2144abbc6e17"
    assert st.collision_suffix(g) == "#8431aae2"
    assert st.collision_suffix(None, 77) == "#i77"
    assert st.base_key("A@1,2#8431aae2") == "A@1,2"
    assert st.base_key("A@1,2") == "A@1,2"


def test_assign_final_keys_suffixes_only_colliding():
    items = [{"base_key": "W@1,1", "guid": "aaaaaaaa-1"}, {"base_key": "W@1,1", "guid": "bbbbbbbb-2"}, {"base_key": "F@2,2", "guid": "c"}]
    coll = st.assign_final_keys(items)
    assert [i["key"] for i in items] == ["W@1,1#aaaaaaaa", "W@1,1#bbbbbbbb", "F@2,2"]
    assert coll == {"W@1,1": ["aaaaaaaa-1", "bbbbbbbb-2"]}


def test_route_keys_occurrence():
    rs = [{"origin_key": "O", "product": "P", "destination_key": "D", "source": "own"} for _ in range(2)]
    rs.append({"origin_key": "O", "product": "Q", "destination_key": "D", "source": "own"})
    st.assign_route_keys(rs)
    assert [r["route_key"] for r in rs] == ["O|P|D|own|0", "O|P|D|own|1", "O|Q|D|own|0"]


def test_actor_id_from_ctor():
    assert st.actor_id_from_ctor([2]) == 2
    assert st.actor_id_from_ctor([{"$prefab": "S"}, 3]) == 3
    assert st.actor_id_from_ctor([{"$prefab": "A"}, {"$prefab": "P"}, "n", {"v": 1}, 11]) == 11
    assert st.actor_id_from_ctor([True]) is None
    assert st.actor_id_from_ctor([]) is None


def test_header_timestamp_uses_1971_epoch():
    assert st.header_timestamp_utc(1759500654).startswith("2026-10-03T14:10:54")
    assert st.game_date(27893, 929) == "Y78-06-24"


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------


def test_extract_entities():
    ex = st.extract_entities(make_tree(), "synthetic.sav")
    b = {x["key"]: x for x in ex["buildings"]}
    farm = b["Farm@10,20"]
    assert farm["owner_actor_id"] == 2 and farm["owner_type"] == "HumanPlayer" and farm["rotation"] == 2
    assert b["Mine@70,80"]["owner_actor_id"] == 11
    assert b["Shop@30,40"]["max_accepted"] == {"Wheat": 8} and b["Shop@30,40"]["is_shop"]
    assert b["Park 1@90,90"]["decoration_candidate"]
    assert ex["counts"]["routes"] == 3 and ex["counts"]["manual_slots_total"] == 4
    r0, r1, r2 = ex["routes"]
    assert r0["route_key"] == "Farm@10,20|Wheat|Shop@30,40|own|0" and r0["slot_index"] == 1
    assert r0["min_stored_at_source"] == 4 and r0["destination_max_accepted"] == 8
    assert r1["route_key"].endswith("|1") and r1["auto_effective"] is True
    assert r2["destination_key"] == "Warehouse@50,60" and r2["destination_ref_kind"] == "component:BuildingLogistics"
    assert r2["auto_effective"] is False and r2["paused"] is True
    assert ex["money_balance_keys"] == [PLAYER, AI]
    assert ex["flags"]["achievements_enabled_game_mode"] is True
    assert ex["game_time"]["date"] == "Y78-06-24"
    assert ex["key_collisions"] == {}


def test_extract_reports_collisions_and_unresolved():
    t = make_tree()
    bl = t["managers"]["ProjectAutomata.BuildingManager"]["entities"]
    bl.append(building("e0000000-0000-0000-0000-000000000006", "Warehouse", 50, 60, PLAYER))
    bl[0]["components"][0]["fields"]["_savegameSlots"].append(slot(dest="99999999-dead", product="Corn"))
    ex = st.extract_entities(t)
    assert "Warehouse@50,60" in ex["key_collisions"]
    keys = {x["key"] for x in ex["buildings"]}
    assert "Warehouse@50,60#b0000000" in keys and "Warehouse@50,60#e0000000" in keys
    bad = next(r for r in ex["routes"] if r["product"] == "Corn")
    assert bad["destination_key"] is None and "destination_unresolved_reason" in bad


def test_rank_sort():
    rows = [
        {"file": "a.sav", "parse_ok": True, "buildings_total": 10, "vehicles": 5, "buildings_ai": 0},
        {"file": "b.sav", "parse_ok": True, "buildings_total": 10, "vehicles": 7, "buildings_ai": 0},
        {"file": "c.sav", "parse_ok": True, "buildings_total": 20, "vehicles": 1, "buildings_ai": 9},
        {"file": "d.sav", "parse_ok": False},
    ]
    out = st.rank_sort(rows)
    assert [r["file"] for r in out] == ["c.sav", "b.sav", "a.sav", "d.sav"]
    assert out[0]["rank"] == 1 and out[-1]["rank"] is None


def test_rank_counts():
    c = st.rank_counts(make_tree())
    assert c == {"buildings_total": 5, "vehicles": 0, "buildings_ai": 1, "buildings_player": 3, "routes": 3}


# ---------------------------------------------------------------------------
# compare-e7
# ---------------------------------------------------------------------------


def observer_state_from(ex):
    def brow(b):
        return {"key": b["key"], "save_guid": b["guid"], "prefab": b["prefab"], "owner_actor_id": b["owner_actor_id"],
                "x": b["x"], "y": b["y"], "rotation": b["rotation"]}
    player = [brow(b) for b in ex["buildings"] if b["owner_type"] == "HumanPlayer" and not b["decoration_candidate"]]
    ai = [{k: v for k, v in brow(b).items() if k not in ("save_guid", "rotation")} for b in ex["buildings"] if b["owner_type"] == "AiPlayer"]
    routes = [{
        "route_key": r["route_key"], "origin": r["origin_key"], "destination": r["destination_key"], "product": r["product"],
        "source": r["source"], "slot_index": r["slot_index"], "paused": r["paused"], "wait_for_full_vehicle": r["wait_till_vehicle_full"],
        "min_keep": {"value": r["min_stored_at_source"]},
        "max_send": {"mode": "auto_shop_demand" if r["auto_effective"] else "manual", "value": r["destination_max_accepted"] or 0},
    } for r in ex["routes"] if r["origin_owner_type"] == "HumanPlayer"]
    return {"world_session": "w", "seq": 1, "captured": {"game_day_end": 27890, "game_date": "Y78-06-21"},
            "data": {"buildings_player": player, "buildings_ai_detail": None, "buildings_ai": ai,
                     "routes_player": routes, "routes_ai": []}}


def test_compare_e7_pass():
    ex = st.extract_entities(make_tree())
    res = st.compare_e7(ex, observer_state_from(ex))
    assert res["pass"], res["mismatches"]
    assert res["informational"]["save_only_decoration_candidates"] == ["Park 1@90,90"]
    assert res["time_gap"]["delta_days"] == 3
    assert res["counts"]["matched_buildings"] == 3


def test_compare_e7_detects_mismatches():
    ex = st.extract_entities(make_tree())
    state = observer_state_from(ex)
    d = state["data"]
    d["buildings_player"][0]["save_guid"] = "ffffffff-0000-0000-0000-000000000000"
    d["buildings_player"][1]["save_guid"] = None  # built after the last load: informational only
    d["buildings_player"].append({"key": "New@1,1", "prefab": "New", "owner_actor_id": 2, "x": 1, "y": 1, "save_guid": None})
    d["buildings_ai"] = []
    d["routes_player"][0]["min_keep"]["value"] = 5
    d["routes_player"][1]["max_send"]["mode"] = "manual"
    d["routes_player"].pop()
    res = st.compare_e7(ex, state)
    kinds = res["mismatch_counts_by_kind"]
    assert not res["pass"]
    assert kinds["save_guid_mismatch"] == 1
    assert kinds["observer_only"] == 1
    assert kinds["save_only"] == 1  # the AI mine
    assert kinds["route_min_keep"] == 1
    assert kinds["route_auto_mode"] == 1
    assert kinds["route_save_only"] == 1
    assert res["informational"]["save_guid_null_in_observer"] == 1


def test_compare_e7_collisions_and_suffix():
    t = make_tree()
    t["managers"]["ProjectAutomata.BuildingManager"]["entities"].append(
        building("e0000000-0000-0000-0000-000000000006", "Warehouse", 50, 60, PLAYER))
    ex = st.extract_entities(t)
    state = observer_state_from(ex)
    res = st.compare_e7(ex, state)
    assert res["mismatch_counts_by_kind"] == {"key_collision_in_save": 1}
    assert sorted(res["informational"]["observer_suffixed_keys"]) == ["Warehouse@50,60#b0000000", "Warehouse@50,60#e0000000"]
    state["data"]["buildings_player"].append(dict(state["data"]["buildings_player"][0]))
    res = st.compare_e7(ex, state)
    assert res["mismatch_counts_by_kind"]["observer_duplicate_key"] == 1


# ---------------------------------------------------------------------------
# V8 normalisation and diff
# ---------------------------------------------------------------------------


def norm_of(tree):
    t = copy.deepcopy(tree)
    st.drop_noise(t)
    return st.normalize(t)


def test_drop_noise_removes_header_and_camera():
    t = make_tree()
    removed = st.drop_noise(t)
    assert "header/name" in removed and "header/timestamp" in removed and "camera" in removed
    assert "managers/ProjectAutomata.CameraRotationController" in removed
    assert "name" not in t["header"] and "camera" not in t


def test_diff_identical_after_noise():
    a = make_tree()
    b = make_tree()
    b["header"]["name"] = "other"
    b["header"]["timestamp"] = 99
    b["camera"]["zoom"] = 3.0
    b["managers"]["ProjectAutomata.CameraRotationController"]["fields"]["targetYaw"] = 90.0
    assert st.diff_trees(norm_of(a), norm_of(b)) == []


def test_ordering_only_is_not_a_difference():
    a = make_tree()
    b = make_tree()
    ents = b["managers"]["ProjectAutomata.BuildingManager"]["entities"]
    ents.reverse()  # entity order
    b["managers"]["ProjectAutomata.MoneyManager"]["fields"]["_balances"]["decoded"].reverse()  # decoded dictionary
    a["managers"]["X"] = {"fields": {"d": {"$dict": [["k1", 1], ["k2", 2]]},
                                     "s": {"$type": "System.Collections.Generic.HashSet`1[[System.String]]", "Version": 1, "Capacity": 3, "Elements": ["a", "b"]},
                                     "ids": [1, 2, 3]}, "entities": []}
    b["managers"]["X"] = {"fields": {"d": {"$dict": [["k2", 2], ["k1", 1]]},
                                     "s": {"$type": "System.Collections.Generic.HashSet`1[[System.String]]", "Version": 7, "Capacity": 5, "Elements": ["b", "a"]},
                                     "ids": [3, 2, 1]}, "entities": []}
    recs = st.diff_trees(norm_of(a), norm_of(b))
    real, order = st.split_diff(recs)
    assert real == []
    assert [r["path"] for r in order] == ["/managers/X/fields/ids"]


def test_order_sensitive_slots_reorder_is_real():
    a = make_tree()
    b = make_tree()
    b["managers"]["ProjectAutomata.BuildingManager"]["entities"][0]["components"][0]["fields"]["_savegameSlots"].reverse()
    real, _order = st.split_diff(st.diff_trees(norm_of(a), norm_of(b)))
    assert len(real) == 1 and real[0]["kind"] == "reordered" and real[0]["path"].endswith("_savegameSlots")


def test_long_lists_summarised_and_order_detected():
    a = {"v": list(range(1000))}
    b = {"v": list(reversed(range(1000)))}
    c = {"v": list(range(999)) + [5]}
    na, nb, nc = st.normalize(a), st.normalize(b), st.normalize(c)
    assert na["v"]["$list"] == 1000
    assert st.diff_trees(na, nb) == [{"path": "/v", "kind": "order_only", "len_a": 1000, "len_b": 1000}]
    assert st.diff_trees(na, nc)[0]["kind"] == "changed"


def test_bytes_and_nan_normalisation():
    a = {"blob": {"$bytes": 3, "data": b"abc"}, "f": float("nan")}
    b = {"blob": {"$bytes": 3, "data": b"abc"}, "f": float("nan")}
    c = {"blob": {"$bytes": 3, "data": b"abd"}, "f": float("nan")}
    assert st.diff_trees(st.normalize(a), st.normalize(b)) == []
    d = st.diff_trees(st.normalize(a), st.normalize(c))
    assert len(d) == 1 and d[0]["path"] == "/blob/sha256"


def test_diff_detects_new_entity_and_dict_entry():
    a = make_tree()
    b = make_tree()
    b["managers"]["ProjectAutomata.BuildingManager"]["entities"][1]["components"][2]["fields"]["_maxAcceptedMap"]["decoded"].append(
        {"product": "Corn", "value": 0})
    b["managers"]["ProjectAutomata.BuildingManager"]["entities"].append(building("e9", "Silo", 1, 1, PLAYER))
    recs = st.diff_trees(norm_of(a), norm_of(b))
    kinds = sorted((r["kind"], r["path"].rsplit("/", 1)[-1]) for r in recs)
    assert ("added", "[product=Corn]") in kinds
    assert ("added", "[guid=e9]") in kinds


def test_extra_differences_against_baseline():
    base = [{"path": "/x", "kind": "changed"}]
    cand = [{"path": "/x", "kind": "changed"}, {"path": "/y", "kind": "added"}]
    assert st.extra_differences(base, cand) == [{"path": "/y", "kind": "added"}]


def test_explicit_checks():
    a = make_tree()
    b = make_tree()
    shop = b["managers"]["ProjectAutomata.BuildingManager"]["entities"][1]
    shop["components"][1]["fields"]["_deliveredByActors"]["decoded"].append({"actorId": 11, "products": {"Corn": 1}})
    shop["components"][2]["fields"]["_maxAcceptedMap"]["decoded"].append({"product": "Corn", "value": 0})
    b["managers"]["ProjectAutomata.MoneyManager"]["fields"]["_balances"]["decoded"].append({"actor": TOWN, "balance": 0.0})
    b["managers"]["ProjectAutomata.EndGameManager"]["fields"]["usedCheats"] = True
    b["managers"]["ProjectAutomata.BuildingManager"]["entities"].append(building("e9", "Silo", 1, 1, PLAYER))
    chk = st.explicit_checks(st.v8_facts(a), st.v8_facts(b))
    assert chk["max_accepted_new_entries"] == {"a0000000-0000-0000-0000-0000000000dd/_maxAcceptedMap": ["Corn"]}
    assert chk["delivered_by_actors_new_entries"] == {"a0000000-0000-0000-0000-0000000000cc": ["11", "11:Corn"]}
    assert chk["money_balance_new_keys"] == [TOWN]
    assert chk["new_guids"] == ["e9"]
    assert chk["flag_changes"] == {"end_game_used_cheats": {"a": False, "b": True}}
    assert chk["violations"] == ["delivered_by_actors_new_entries", "flag_changes", "max_accepted_new_entries",
                                 "money_balance_new_keys", "new_guids"]
    assert st.explicit_checks(st.v8_facts(a), st.v8_facts(make_tree()))["violations"] == []


# ---------------------------------------------------------------------------
# fixture and path safety
# ---------------------------------------------------------------------------


def test_build_fixture_min_keep_4_and_checks():
    ex = st.extract_entities(make_tree(), "synthetic.sav")
    fx, state_like = st.build_fixture(ex)
    c = fx["checks"]
    assert c["min_keep_4_present"] and c["min_keep_4_routes"] == ["Farm@10,20|Wheat|Shop@30,40|own|0"]
    assert c["building_key_format_ok"] and c["building_keys_unique_after_suffix"] and c["route_keys_unique"]
    assert len(state_like["data"]["routes_player"]) == 3
    assert state_like["data"]["routes_player"][1]["max_send"]["mode"] == "auto_shop_demand"
    t = make_tree()
    t["managers"]["ProjectAutomata.BuildingManager"]["entities"][0]["components"][0]["fields"]["_savegameSlots"][1]["fields"]["_minStoredAtSource"] = 1
    fx2, _ = st.build_fixture(st.extract_entities(t))
    assert fx2["checks"]["min_keep_4_present"] is False


def test_output_path_must_be_under_local(tmp_path):
    local = tmp_path / ".local"
    assert st.check_output_path(local / "x" / "y.json", local) == local / "x" / "y.json"
    with pytest.raises(st.ToolError):
        st.check_output_path(tmp_path / "elsewhere.json", local)
    with pytest.raises(st.ToolError):
        st.check_output_path(local, local)
    with pytest.raises(st.ToolError):
        st.check_output_path(tmp_path / ".local-evil" / "a.json", local)


def test_is_under():
    root = Path(os.path.abspath("some_root"))
    assert st.is_under(root / "a" / "b", root)
    assert st.is_under(root, root)
    assert not st.is_under(Path(str(root) + "2") / "a", root)


def test_live_saves_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    live = tmp_path / "RiseOfIndustry"
    with pytest.raises(st.ToolError):
        st.check_input_copy(live / "x.sav")  # refused before any file access


def test_parser_missing_message(monkeypatch, tmp_path):
    monkeypatch.setattr(st, "PARSER_PATH", tmp_path / "missing.py")
    monkeypatch.setattr(st, "_PARSER", None)
    with pytest.raises(st.ToolError, match="research save parser not found"):
        st.load_parser()
