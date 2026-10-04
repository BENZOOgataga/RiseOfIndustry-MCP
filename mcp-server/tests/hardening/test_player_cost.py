"""CA-3: get_building_type reports the player's current build price (PRD 14.6, G techTree.GetBuildingCost)."""

import copy

from test_tools_contract import check_response


def test_cost_comes_from_the_players_price_table(h):
    r = h.call("get_building_type", {"building_type": "PaintFactory"})
    check_response("get_building_type", r)
    assert r["ok"] and r["data"]["current_player_cost"] == 180000.0
    assert "current_player_cost" not in [u["field"] for u in r["data"]["unavailable"]]


def test_type_missing_from_the_price_table_costs_its_base_cost(h):
    w = copy.deepcopy(h.world)
    w["state"]["data"]["research"]["player"]["building_costs"] = []
    h.write_world(w, ("state",))
    base = next(b["base_cost"] for b in h.world["static"]["data"]["building_types"] if b["name"] == "PaintFactory")
    r = h.call("get_building_type", {"building_type": "PaintFactory"})
    check_response("get_building_type", r)
    assert r["ok"] and r["data"]["current_player_cost"] == base


def test_unreadable_price_table_is_unavailable_not_zero(h):
    w = copy.deepcopy(h.world)
    w["state"]["data"]["research"]["player"]["building_costs"] = None
    h.write_world(w, ("state",))
    r = h.call("get_building_type", {"building_type": "PaintFactory"})
    check_response("get_building_type", r)
    assert r["ok"] and r["data"]["current_player_cost"] is None
    assert "current_player_cost" in [u["field"] for u in r["data"]["unavailable"]]


def test_no_live_state_gives_no_player_cost(h):
    h.procs.procs = []
    r = h.call("get_building_type", {"building_type": "PaintFactory"})
    check_response("get_building_type", r)
    assert r["ok"] and r["data"]["current_player_cost"] is None
