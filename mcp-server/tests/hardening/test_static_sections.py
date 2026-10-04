"""SRV-1: a failed static section is reported, never served as an empty catalogue (PRD 13.3, 13.5).

Seen live in gate E5: when one static section failed, the observer published it as null and the server answered
recipe lookups with not_found as if the catalogue were empty."""

import copy

import pytest

from test_tools_contract import check_response


def _fail_static(h, section, fields):
    w = copy.deepcopy(h.world)
    template = copy.deepcopy(next(iter(w["static"]["sections"].values())))  # a schema-valid section record
    w["static"]["sections"][section] = {**template, **w["static"]["sections"].get(section, {}), "status": "failed",
                                        "reason": "InvalidOperationException"}
    for f in fields:
        w["static"]["data"][f] = None
    h.write_world(w, ("static",))


@pytest.mark.parametrize("tool,args,section,fields", [
    ("get_recipe", {"recipe": "Chemicals"}, "recipes", ["recipes"]),
    ("list_recipes", {}, "recipes", ["recipes"]),
    ("get_product", {"product": "Paint"}, "products", ["products", "product_categories"]),
    ("list_products", {}, "products", ["products", "product_categories"]),
    ("get_building_type", {"building_type": "PaintFactory"}, "building_types", ["building_types"]),
    ("list_building_types", {}, "building_types", ["building_types"]),
    ("get_tech_tree", {}, "technology", ["tech_trees", "tech_categories", "tech_unlocks", "tech_config"]),
    ("get_supply_chain", {"product": "Paint", "mode": "recipe"}, "recipes", ["recipes"]),
])
def test_tool_built_on_a_failed_static_section_answers_section_unavailable(h, tool, args, section, fields):
    _fail_static(h, section, fields)
    r = h.call(tool, args)
    check_response(tool, r)
    assert not r["ok"] and r["error"]["code"] == "section_unavailable"
    assert section in r["error"]["message"]


def test_other_tools_report_a_failed_static_section_as_degraded(h):
    _fail_static(h, "recipes", ["recipes"])
    r = h.call("list_routes", {})
    check_response("list_routes", r)
    assert r["ok"]
    assert "section_degraded" in [w["code"] for w in r["meta"]["warnings"]]
    assert {"field": "static.recipes", "reason": "section_failed"} in r["data"]["unavailable"]


def test_healthy_static_sections_add_no_warning(h):
    r = h.call("list_recipes", {})
    assert r["ok"] and "section_degraded" not in [w["code"] for w in r["meta"]["warnings"]]


def test_ligatures_fold_like_accents():
    """PRD 13.6 exact matching is case- and accent-insensitive; French ligatures (Œufs) fold the same way."""
    from roi_mcp.util import normalize_name
    assert normalize_name("Œufs frais") == normalize_name("oeufs FRAIS") == "oeufs frais"
    assert normalize_name("Ætherium") == "aetherium"


def test_get_city_with_a_healthy_empty_shops_section_lists_nothing_unavailable(h):
    import copy
    w = copy.deepcopy(h.world)
    w["state"]["data"]["shops"] = []
    for c in w["state"]["data"]["cities"]:
        c["shops"] = []
    h.write_world(w, ("state",))
    r = h.call("get_city", {"city": "Valmont"})
    check_response("get_city", r)
    assert r["ok"] and "shops[].products" not in [u["field"] for u in r["data"]["unavailable"]]
