"""Hardening: get_supply_chain, list_products, get_product, list_recipes, get_recipe, list_building_types,
get_building_type (PRD 7.1, 11.4, 11.7, 12.1, 13 (13.2-13.7), 14.5, 14.6, 14.9, 15 D-RATE-1 / D-REQ-1, 16).

Synthetic data only; deterministic (FakeClock / FakeProcs / FakeObserver). Every response (ok and error) is
validated with check_response (tool response schema + envelope keys + 30 KB cap). Expected values are
justified by the PRD/schemas in comments; where the PRD is silent only invariants are asserted and the
ambiguity is named in the test docstring. Implementation defects are kept as strict xfails ("DEFECT CA-<n>").
"""

from __future__ import annotations

import copy
import json
import math

import pytest

import build_fixtures as bf
from conftest import FakeClock, FakeProcs, Harness, game_proc  # noqa: F401
from test_tools_contract import VALID_CALLS, _observer_harness, check_response

TOOLS = ("get_supply_chain", "list_products", "get_product", "list_recipes", "get_recipe", "list_building_types",
         "get_building_type")
STATIC_LIVE = ("list_products", "get_product", "list_recipes", "list_building_types", "get_building_type")
LIST_TOOLS = {"list_products": "products", "list_recipes": "recipes", "list_building_types": "building_types"}

# Calls that need only static.json (PRD 13.4 / 13.7: static catalogue tools, get_recipe, supply chain mode=recipe).
STATIC_CALLS = {
    "list_products": {}, "get_product": {"product": "Paint"}, "list_recipes": {}, "get_recipe": {"recipe": "Chemicals"},
    "list_building_types": {}, "get_building_type": {"building_type": "PaintFactory"},
    "get_supply_chain": {"product": "Paint", "mode": "recipe"},
}


# ------------------------------------------------------------------ helpers

def call(h, name, args=None):
    r = h.call(name, args if args is not None else {})
    check_response(name, r)
    return r


def ok(r):
    assert r["ok"] is True, r.get("error")
    assert_finite(r["data"])
    return r["data"]


def err(r, code):
    assert r["ok"] is False, f"expected error {code}, got ok"
    assert r["error"]["code"] == code, r["error"]
    return r["error"]


def wcodes(r):
    return [w["code"] for w in r["meta"]["warnings"]]


def assert_finite(o, path="data"):
    """No NaN/Infinity anywhere in a response (JSON has no such numbers; schemas accept them silently)."""
    if isinstance(o, float):
        assert math.isfinite(o), path
    elif isinstance(o, dict):
        for k, v in o.items():
            assert_finite(v, f"{path}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            assert_finite(v, f"{path}[{i}]")


def rebuild(world, static_data=None, state_data=None, *, state_kw=None, hb_kw=None):
    """A consistent world (static_ref, content hashes, heartbeat families) from mutated static/state data."""
    w = copy.deepcopy(world)
    sdata = static_data if static_data is not None else w["static"]["data"]
    s = bf.envelope("static", sdata, seq=w["static"]["seq"] + 1, now=bf.BASE_TIME, capture_age_s=60.0,
                    sections=w["static"]["sections"])
    stdata = state_data if state_data is not None else w["state"]["data"]
    st = bf.build_state(bf.BASE_TIME, seq=w["state"]["seq"] + 1, static_doc=s, data=stdata, **(state_kw or {}))
    hi = bf.build_history(bf.BASE_TIME, seq=w["history"]["seq"] + 1, static_doc=s)
    hb = bf.build_heartbeat(bf.BASE_TIME, static_doc=s, state_doc=st, history_doc=hi, **(hb_kw or {}))
    return {"static": s, "state": st, "history": hi, "heartbeat": hb}


def use(h, world):
    h.write_world(world)
    h.world = world
    return h


def static_with(world, mutate):
    sd = copy.deepcopy(world["static"]["data"])
    mutate(sd)
    return rebuild(world, static_data=sd)


def prod(sd, name):
    return next(p for p in sd["products"] if p["name"] == name)


def recipe(sd, name):
    return next(r for r in sd["recipes"] if r["name"] == name)


def add_refining(sd):
    """Oil (no producer) -> Refining: Oil 4 -> Fuel 2 + Tar 1 in 20 days (multi-output recipe)."""
    for n, d, e in (("Oil", "Petrole", "Oil"), ("Fuel", "Carburant", "Fuel"), ("Tar", "Goudron", "Tar")):
        sd["products"].append(bf._product(n, d, e, "Components", "Components", ["component"], "Factories"))
    sd["recipes"].append(bf._recipe("Refining", "Raffinage", "Refining", [("Oil", 4)], [("Fuel", 2), ("Tar", 1)], 20,
                                    ["ChemicalPlant"]))


def edge_key(e):
    return (e["from"], e["to"], e["kind"], e.get("product"), e.get("route_id"))


# ------------------------------------------------------------------ valid calls, every parameter

VALID = [
    ("list_products", {}), ("list_products", {"fields": "full"}), ("list_products", {"fields": "compact"}),
    ("list_products", {"category": "Components"}), ("list_products", {"category": "components"}),
    ("list_products", {"tag": "paint"}), ("list_products", {"tag": "PAINT"}), ("list_products", {"query": "Paint"}),
    ("list_products", {"query": "zzz-nothing"}), ("list_products", {"unlocked_only": True}),
    ("list_products", {"unlocked_only": False}), ("list_products", {"limit": 1}), ("list_products", {"limit": 50}),
    ("list_products", {"category": "Components", "tag": "component", "query": "dye", "unlocked_only": True,
                       "fields": "full", "limit": 5}),
    ("get_product", {"product": "Paint"}), ("get_product", {"product": "product:Paint"}),
    ("get_product", {"product": "peinture"}), ("get_product", {"product": "PEINTURE"}), ("get_product", {"product": " Gaz "}),
    ("get_product", {"product": "Produits chimiques"}), ("get_product", {"product": "Water"}),
    ("list_recipes", {}), ("list_recipes", {"product": "Gas"}), ("list_recipes", {"product": "product:Chemicals"}),
    ("list_recipes", {"building_type": "PaintFactory"}), ("list_recipes", {"building_type": "Paint Factory"}),
    ("list_recipes", {"available_to_player": True}), ("list_recipes", {"fields": "full", "limit": 50}),
    ("list_recipes", {"product": "Paint", "building_type": "building_type:PaintFactory", "available_to_player": True,
                      "fields": "full"}),
    ("get_recipe", {"recipe": "Chemicals"}), ("get_recipe", {"recipe": "recipe:Paints"}),
    ("get_recipe", {"recipe": "peintures avancees"}), ("get_recipe", {"recipe": "Advanced paints"}),
    ("get_recipe", {"product": "Gas"}), ("get_recipe", {"product": "product:Chemicals"}),
    ("list_building_types", {}), ("list_building_types", {"tag": "factory"}), ("list_building_types", {"product": "Paint"}),
    ("list_building_types", {"unlocked_only": True}), ("list_building_types", {"fields": "full", "limit": 50}),
    ("list_building_types", {"tag": "gatherer", "product": "Gas", "unlocked_only": True, "fields": "full"}),
    ("get_building_type", {"building_type": "PaintFactory"}), ("get_building_type", {"building_type": "building_type:GasWell"}),
    ("get_building_type", {"building_type": "Petrochemical Plant"}), ("get_building_type", {"building_type": "usine pétrochimique"}),
    ("get_building_type", {"building_type": "GasPump"}), ("get_building_type", {"building_type": "HardwareStore"}),
    ("get_building_type", {"building_type": "Headquarters"}),
    ("get_supply_chain", {"product": "Paint"}), ("get_supply_chain", {"product": "Paint", "mode": "recipe"}),
    ("get_supply_chain", {"product": "Gas", "mode": "recipe", "direction": "downstream"}),
    ("get_supply_chain", {"product": "Chemicals", "mode": "recipe", "direction": "both", "depth": 12}),
    ("get_supply_chain", {"product": "Paint", "mode": "actual", "direction": "both", "depth": 1}),
    ("get_supply_chain", {"building": bf.B_PC1}), ("get_supply_chain", {"building": "usine petrochimique 2"}),
    ("get_supply_chain", {"building": bf.B_WH, "direction": "downstream"}),
    ("get_supply_chain", {"building": f"building:{bf.S_HW1}", "direction": "upstream"}),
    ("get_supply_chain", {"product": "Paint", "company": "Borealis Corp"}),
    ("get_supply_chain", {"product": "Paint", "company": "company:1"}),
    ("get_supply_chain", {"product": "Water", "company": "Borealis Corp"}),
    ("get_supply_chain", {"product": "Paint", "target_output_per_30d": 0.5}),
    ("get_supply_chain", {"product": "Paint", "target_output_per_30d": 1e9}),
    ("get_supply_chain", {"product": "Paint", "recipe_choice": {"Paint": "PaintsAdvanced"}}),
    ("get_supply_chain", {"product": "Paint", "mode": "recipe", "recipe_choice": {"peinture": "recipe:PaintsAdvanced"},
                          "target_output_per_30d": 30}),
    ("get_supply_chain", {"product": "Paint", "recipe_choice": {}}),
]


@pytest.mark.parametrize("name,args", VALID, ids=[f"{n}-{i}" for i, (n, _) in enumerate(VALID)])
def test_valid_calls(h, name, args):
    """PRD 13.2 / 14.5 / 14.6 parameters, individually and combined: ok, schema-valid, finite numbers."""
    d = ok(call(h, name, args))
    if name in LIST_TOOLS:
        rows = d[LIST_TOOLS[name]]
        assert len(rows) <= args.get("limit", 25)
        ids = [r["id"] for r in rows]
        assert len(ids) == len(set(ids))


def test_omitted_optionals_equal_documented_defaults(h):
    """PRD 13.2 (limit 25, fields compact) and 14.5 (direction upstream, depth 6, company player, mode actual)."""
    for name, explicit in (("list_products", {"limit": 25, "fields": "compact", "unlocked_only": False}),
                           ("list_recipes", {"limit": 25, "fields": "compact", "available_to_player": False}),
                           ("list_building_types", {"limit": 25, "fields": "compact", "unlocked_only": False})):
        a, b = ok(call(h, name)), ok(call(h, name, explicit))
        assert a[LIST_TOOLS[name]] == b[LIST_TOOLS[name]]
    a = ok(call(h, "get_supply_chain", {"product": "Paint"}))
    b = ok(call(h, "get_supply_chain", {"product": "Paint", "mode": "actual", "direction": "upstream", "depth": 6,
                                        "company": "player"}))
    assert a == b and a["mode"] == "actual" and a["direction"] == "upstream"


def test_fields_full_extends_compact(h):
    """PRD 13.2 fields compact|full: a full row carries at least the compact row's keys."""
    for name, key in LIST_TOOLS.items():
        compact = ok(call(h, name, {"limit": 50}))[key]
        full = ok(call(h, name, {"limit": 50, "fields": "full"}))[key]
        assert [r["id"] for r in compact] == [r["id"] for r in full]
        for c, f in zip(compact, full):
            assert set(c) <= set(f)
            assert {k: f[k] for k in c} == c


# ------------------------------------------------------------------ invalid arguments

INVALID = [
    ("list_products", {"limit": 0}), ("list_products", {"limit": 51}), ("list_products", {"limit": "5"}),
    ("list_products", {"limit": 2.5}), ("list_products", {"fields": "all"}), ("list_products", {"unlocked_only": "yes"}),
    ("list_products", {"category": 5}), ("list_products", {"query": "x" * 201}), ("list_products", {"cursor": 5}),
    ("list_products", {"cursor": "not-a-cursor"}), ("list_products", {"debug": True}),
    ("get_product", {}), ("get_product", {"product": 5}), ("get_product", {"product": None}), ("get_product", {"product": "   "}),
    ("get_product", {"product": "Paint", "fields": "full"}), ("get_product", {"product": "Paint", "limit": 5}),
    ("list_recipes", {"available_to_player": 1}), ("list_recipes", {"product": ["Paint"]}), ("list_recipes", {"limit": -1}),
    ("get_recipe", {}), ("get_recipe", {"recipe": "Chemicals", "product": "Chemicals"}), ("get_recipe", {"recipe": ""}),
    ("get_recipe", {"recipe": "Chemicals", "fresh": False}), ("get_recipe", {"recipe": "Chemicals", "allow_stale": True}),
    ("get_recipe", {"recipe": 7}), ("get_recipe", {"recipe": "Chemicals", "limit": 5}),
    ("list_building_types", {"unlocked_only": "true"}), ("list_building_types", {"tag": ["factory"]}),
    ("get_building_type", {}), ("get_building_type", {"building_type": ["PaintFactory"]}),
    ("get_supply_chain", {"product": "Paint", "direction": "sideways"}), ("get_supply_chain", {"product": "Paint", "mode": "static"}),
    ("get_supply_chain", {"product": "Paint", "depth": "6"}), ("get_supply_chain", {"product": "Paint", "depth": 13}),
    ("get_supply_chain", {"product": "Paint", "target_output_per_30d": "100"}),
    ("get_supply_chain", {"product": "Paint", "target_output_per_30d": True}),
    ("get_supply_chain", {"product": "Paint", "target_output_per_30d": float("inf")}),
    ("get_supply_chain", {"product": "Paint", "target_output_per_30d": float("-inf")}),
    ("get_supply_chain", {"product": "Paint", "recipe_choice": "Paints"}),
    ("get_supply_chain", {"product": "Paint", "recipe_choice": {"Paint": 5}}),
    ("get_supply_chain", {"product": "Paint", "building": bf.B_PF1}), ("get_supply_chain", {"mode": "recipe"}),
    ("get_supply_chain", {"product": "Paint", "limit": 5}), ("get_supply_chain", {"product": "Paint", "cursor": "x"}),
    ("get_supply_chain", {"product": "Paint", "fields": "full"}), ("get_supply_chain", {"product": ""}),
]


@pytest.mark.parametrize("name,args", INVALID, ids=[f"{n}-{i}" for i, (n, _) in enumerate(INVALID)])
def test_invalid_arguments(h, name, args):
    """PRD 13.3 invalid_argument for parameter validation (types, enums, bounds, unknown params, PRD 13.2 fresh /
    allow_stale only for tools with a refresh scope, paging/fields only for list tools)."""
    err(call(h, name, args), "invalid_argument")


# Regression test for DEFECT CA-1 (fixed).
@pytest.mark.parametrize("mode", ["recipe", "actual"])
def test_nan_target_is_invalid_argument(h, mode):
    """PRD 13.3: parameter validation -> invalid_argument. NaN survives MCP JSON-RPC parsing (pydantic accepts
    the NaN literal), and NaN compares False against exclusiveMinimum/maximum, so it reaches derive._clean."""
    err(call(h, "get_supply_chain", {"product": "Paint", "mode": mode, "target_output_per_30d": float("nan")}),
        "invalid_argument")


AMBIGUOUS_ARGS = [
    ("get_supply_chain", {"product": "Paint", "target_output_per_30d": 0}),
    ("get_supply_chain", {"product": "Paint", "target_output_per_30d": -5}),
    ("get_supply_chain", {"product": "Paint", "target_output_per_30d": 1e12}),
    ("get_supply_chain", {"product": "Paint", "depth": 0}),
    ("get_supply_chain", {"product": "Paint", "depth": -1}),
    ("get_supply_chain", {"building": bf.B_PC1, "mode": "recipe"}),
    ("get_supply_chain", {"product": "Paint", "mode": "recipe", "recipe_choice": {"Paint": "Chemicals"}}),
    ("get_supply_chain", {"product": "Paint", "mode": "recipe", "recipe_choice": {"Water": "Water"}}),
    ("get_supply_chain", {"product": "Paint", "mode": "recipe", "recipe_choice": {"Nope": "Paints"}}),
    ("get_supply_chain", {"product": "Paint", "company": "Nobody Inc"}),
    ("get_supply_chain", {"product": "Paint", "mode": "recipe", "company": "Borealis Corp"}),
    ("list_products", {"sort": "name"}), ("list_recipes", {"sort": "id"}), ("list_building_types", {"sort": "base_cost"}),
    ("list_products", {"category": "Nope"}), ("list_building_types", {"tag": "nope"}),
    ("get_product", {"product": "product:paint"}),
]


@pytest.mark.parametrize("name,args", AMBIGUOUS_ARGS, ids=[f"{n}-{i}" for i, (n, _) in enumerate(AMBIGUOUS_ARGS)])
def test_prd_silent_arguments_keep_invariants(h, name, args):
    """PRD-silent cases (only invariants): target 0/negative/huge (14.5 only says "number"), depth below 1,
    building with mode=recipe, recipe_choice naming a recipe that does not produce the product or a product
    outside the chain, unknown company, `sort` on catalogue list tools (13.2 lists sort but 14.6 defines no
    enum), unmatched filters, id with wrong case. Must never crash; errors use the PRD 13.3 vocabulary."""
    r = call(h, name, args)
    if r["ok"]:
        assert_finite(r["data"])
    else:
        assert r["error"]["code"] in ("invalid_argument", "not_found", "ambiguous"), r["error"]


# ------------------------------------------------------------------ name resolution (PRD 13.6, 7.1)

def test_names_resolve_to_the_same_id(h):
    """PRD 7.1 / 13.6: ids, asset names, display (French) names case- and accent-insensitive, English names."""
    w = static_with(h.world, lambda sd: prod(sd, "Dye").update(display_name="Teinture végétale"))
    use(h, w)
    for q in ("Dye", "product:Dye", "Teinture végétale", "TEINTURE VEGETALE", "teinture  végétale", "Dye "):
        assert ok(call(h, "get_product", {"product": q}))["definition"]["id"] == "product:Dye", q
    for q in ("PetrochemicalFactory", "Petrochemical Plant", "usine pétrochimique", "USINE PETROCHIMIQUE"):
        assert ok(call(h, "get_building_type", {"building_type": q}))["definition"]["id"] == "building_type:PetrochemicalFactory"
    for q in ("PaintsAdvanced", "Advanced paints", "PEINTURES AVANCÉES", "recipe:PaintsAdvanced"):
        assert ok(call(h, "get_recipe", {"recipe": q}))["definition"]["id"] == "recipe:PaintsAdvanced"
    d = ok(call(h, "list_recipes", {"product": "teinture vegetale"}))
    assert "recipe:Dye" in [r["id"] for r in d["recipes"]]
    d = ok(call(h, "get_supply_chain", {"product": "TEINTURE VÉGÉTALE", "mode": "recipe"}))
    assert d["root"] == "product:Dye"


@pytest.mark.parametrize("name,args", [
    ("get_product", {"product": "gaz"}), ("list_recipes", {"product": "GAZ"}), ("list_building_types", {"product": "gaz"}),
    ("get_supply_chain", {"product": "gaz", "mode": "recipe"}), ("get_supply_chain", {"product": "gaz"}),
    ("get_recipe", {"product": "gaz"}),
])
def test_ambiguous_names(h, name, args):
    """PRD 13.6: multiple matches return `ambiguous`; 13.3: candidates list ids with kind (max 10)."""
    use(h, static_with(h.world, lambda sd: prod(sd, "Dye").update(display_name="Gaz")))
    e = err(call(h, name, args), "ambiguous")
    ids = {c["id"] for c in e["candidates"]}
    assert ids == {"product:Gas", "product:Dye"} and len(e["candidates"]) <= 10


def test_ambiguous_building_name_in_supply_chain(h):
    """PRD 13.6 / 13.3: a building display name shared by the player and an AI company is ambiguous; candidates
    carry owner and coordinates context."""
    e = err(call(h, "get_supply_chain", {"building": "usine pétrochimique 1"}), "ambiguous")
    assert f"building:{bf.B_PC1}" in {c["id"] for c in e["candidates"]}
    assert all(c.get("owner") and c.get("coordinates") for c in e["candidates"])


def test_ambiguous_candidates_capped_at_ten(h):
    def mut(sd):
        for i in range(15):
            sd["products"].append(bf._product(f"Twin{i:02d}", "Jumeau", f"Twin {i}", "Components", "Components", [], "Factories"))
    use(h, static_with(h.world, mut))
    e = err(call(h, "get_product", {"product": "jumeau"}), "ambiguous")
    assert 1 < len(e["candidates"]) <= 10 and all(c["id"].startswith("product:") for c in e["candidates"])


@pytest.mark.parametrize("name,args", [
    ("get_product", {"product": "Nothing"}), ("get_product", {"product": "Pain"}), ("get_product", {"product": "Peintur"}),
    ("get_product", {"product": "Paintt"}), ("get_product", {"product": "recipe:Paints"}),
    ("get_product", {"product": "product:Nothing"}), ("get_product", {"product": "Paints"}),
    ("get_recipe", {"recipe": "Chemical"}), ("get_recipe", {"recipe": "product:Paint"}), ("get_recipe", {"product": "Pain"}),
    ("list_recipes", {"product": "Pain"}), ("list_recipes", {"building_type": "Paint Fact"}),
    ("list_building_types", {"product": "Chem"}), ("get_building_type", {"building_type": "Paint Fact"}),
    ("get_building_type", {"building_type": "product:Paint"}), ("get_building_type", {"building_type": "building_type:Nope"}),
    ("get_supply_chain", {"product": "Pain", "mode": "recipe"}), ("get_supply_chain", {"product": "Pain"}),
    ("get_supply_chain", {"building": "Usine de peint"}), ("get_supply_chain", {"building": "building:Nope@1,1"}),
])
def test_unknown_and_fuzzy_names_are_not_found(h, name, args):
    """PRD 13.6: fuzzy (prefix/substring/typo) matching only in `search`; a wrong-kind id is not resolvable."""
    err(call(h, name, args), "not_found")


def test_get_recipe_by_product(h):
    """PRD 14.6 get_recipe `recipe` or `product`. One producing recipe resolves; none -> not_found.
    PRD-silent: several producing recipes (Paint) -> ambiguous with the recipe ids, or one of them."""
    assert ok(call(h, "get_recipe", {"product": "Chemicals"}))["definition"]["id"] == "recipe:Chemicals"
    use(h, static_with(h.world, add_refining))
    err(call(h, "get_recipe", {"product": "Oil"}), "not_found")
    assert ok(call(h, "get_recipe", {"product": "Tar"}))["definition"]["id"] == "recipe:Refining"
    r = call(h, "get_recipe", {"product": "Paint"})
    if r["ok"]:
        assert r["data"]["definition"]["id"] in ("recipe:Paints", "recipe:PaintsAdvanced")
    else:
        assert {c["id"] for c in err(r, "ambiguous")["candidates"]} == {"recipe:Paints", "recipe:PaintsAdvanced"}


# ------------------------------------------------------------------ English names (PRD 12.1)

def test_english_names_unavailable_globally(h):
    """PRD 12.1: unresolved English names are null with warning english_name_unavailable; the server falls back
    to the asset name for lookup."""
    def mut(sd):
        sd["english_names_available"] = False
        for k in ("products", "recipes", "building_types", "tech_unlocks"):
            for x in sd[k]:
                x["english_name"] = None
    use(h, static_with(h.world, mut))
    for name, args in list(STATIC_CALLS.items()) + [("get_supply_chain", {"product": "Paint"})]:
        r = call(h, name, args)
        ok(r)
        assert "english_name_unavailable" in wcodes(r), name
    assert ok(call(h, "get_product", {"product": "Paint"}))["definition"]["english_name"] is None
    assert ok(call(h, "get_building_type", {"building_type": "PaintFactory"}))["definition"]["id"] == "building_type:PaintFactory"
    err(call(h, "get_building_type", {"building_type": "Paint Factory"}), "not_found")
    assert all(r["english_name"] is None for r in ok(call(h, "list_products", {"limit": 50}))["products"])


# Regression test for DEFECT CA-2 (fixed).
def test_single_missing_english_name_warns(h):
    """PRD 12.1 / KNOWN-LIMITATIONS U-EN: an English name that cannot be resolved is null WITH the warning
    english_name_unavailable. The server only reacts to the global english_names_available flag."""
    use(h, static_with(h.world, lambda sd: prod(sd, "Paint").update(english_name=None)))
    r = call(h, "get_product", {"product": "Paint"})
    assert ok(r)["definition"]["english_name"] is None
    assert "english_name_unavailable" in wcodes(r)


# ------------------------------------------------------------------ catalogue semantics (PRD 14.6, D-RATE-1)

def test_catalogue_filters(h):
    ids = lambda name, args: [r["id"] for r in ok(call(h, name, {"limit": 50, **args}))[LIST_TOOLS[name]]]  # noqa: E731
    assert ids("list_products", {"tag": "paint"}) == ["product:Paint"]
    assert set(ids("list_products", {"category": "Components"})) == {"product:Chemicals", "product:Dye"}
    assert "product:Paint" in ids("list_products", {"query": "Paint"})
    assert ids("list_products", {"query": "zzz-nothing"}) == []
    assert set(ids("list_recipes", {"building_type": "PaintFactory"})) == {"recipe:Paints", "recipe:PaintsAdvanced"}
    chem = ok(call(h, "list_recipes", {"product": "Chemicals", "limit": 50}))["recipes"]
    assert "recipe:Chemicals" in [r["id"] for r in chem]
    for r in chem:
        assert "product:Chemicals" in {x["product"] for x in r["inputs"] + r["outputs"]}
    assert ids("list_recipes", {"product": "Paint", "building_type": "PaintFactory", "available_to_player": True}) == ["recipe:Paints"]
    assert set(ids("list_building_types", {"tag": "factory"})) == {
        "building_type:PetrochemicalFactory", "building_type:ChemicalPlant", "building_type:PaintFactory"}
    assert "building_type:PaintFactory" in ids("list_building_types", {"product": "Paint"})
    # totals = static catalogue sizes (PRD 13.3 page.total)
    sd = h.world["static"]["data"]
    for name, key in (("list_products", "products"), ("list_recipes", "recipes"), ("list_building_types", "building_types")):
        assert call(h, name, {"limit": 50})["page"]["total"] == len(sd[key])


def test_live_parts_from_state(h):
    """PRD 14.6: prices/trend (O), unlocked (player research), upkeep_monthly_base = base_cost x percentage (D),
    recipes producing/consuming (S), State offer, price_history not available."""
    m = {p["product"]: p for p in h.world["state"]["data"]["market"]["prices"]}
    rows = {r["id"]: r for r in ok(call(h, "list_products", {"limit": 50}))["products"]}
    paint = rows["product:Paint"]
    assert (paint["base_price"], paint["current_price"], paint["trend"]) == (m["Paint"]["value"], m["Paint"]["price"], m["Paint"]["trend"])
    p = ok(call(h, "get_product", {"product": "Paint"}))
    assert p["market"]["price"] == m["Paint"]["price"] and p["market"]["final_price_for_player"] == m["Paint"]["final_price_for_player"]
    assert {r["id"] for r in p["recipes_producing"]} == {"recipe:Paints", "recipe:PaintsAdvanced"}
    assert p["recipes_consuming"] == [] and p["price_history"]["available"] is False
    assert f"building:{bf.B_PF1}" in {b["id"] for b in p["player_producers"]}
    assert ok(call(h, "get_product", {"product": "Gas"}))["state_offer"] == {"sold_by_state": True, "price": 130.0,
                                                                          "incoming_trade_allowed": True}
    rec = {r["id"]: r["unlocked"] for r in ok(call(h, "list_recipes", {"limit": 50}))["recipes"]}
    assert rec["recipe:Paints"] is True and rec["recipe:PaintsAdvanced"] is False
    bts = {r["id"]: r for r in ok(call(h, "list_building_types", {"limit": 50}))["building_types"]}
    assert bts["building_type:TrainTerminal"]["unlocked"] is False and bts["building_type:PaintFactory"]["unlocked"] is True
    assert bts["building_type:PaintFactory"]["upkeep_monthly_base"] == pytest.approx(500000 * 0.025)
    assert "building_type:TrainTerminal" not in {r["id"] for r in ok(call(h, "list_building_types", {"unlocked_only": True, "limit": 50}))["building_types"]}
    bt = ok(call(h, "get_building_type", {"building_type": "PaintFactory"}))
    assert {r["id"] for r in bt["recipes"]} == {"recipe:Paints", "recipe:PaintsAdvanced"}
    assert [u["id"] for u in bt["unlocked_by"]] == ["tech:Paints"] and bt["unlocked"] is True


# Regression test for DEFECT CA-3 (fixed).
def test_get_building_type_current_player_cost(h):
    """PRD 14.6: get_building_type returns the current player cost (game-computed). Always null today, listed in
    `unavailable` as a schema gap (no state/static field carries it)."""
    d = ok(call(h, "get_building_type", {"building_type": "PaintFactory"}))
    assert isinstance(d["current_player_cost"], (int, float))


def test_get_recipe_per_30d_d_rate_1(h):
    """PRD 14.6 + D-RATE-1: per-30-day I/O = amount x 30 / days. Multi-output recipe; recipe without ingredients."""
    use(h, static_with(h.world, add_refining))
    d = ok(call(h, "get_recipe", {"recipe": "Refining"}))
    outs = {o["product"]: o["per_30d"] for o in d["per_30d"]["outputs"]}
    ins = {i["product"]: i["per_30d"] for i in d["per_30d"]["inputs"]}
    assert outs == {"Fuel": 3, "Tar": 1.5} or outs == {"product:Fuel": 3, "product:Tar": 1.5}
    assert list(ins.values()) == [6]
    g = ok(call(h, "get_recipe", {"recipe": "Gas"}))
    assert g["definition"]["inputs"] == [] and g["per_30d"]["inputs"] == []
    assert [o["per_30d"] for o in g["per_30d"]["outputs"]] == [6]
    p = ok(call(h, "get_product", {"product": "Oil"}))
    assert p["recipes_producing"] == [] and [r["id"] for r in p["recipes_consuming"]] == ["recipe:Refining"]


@pytest.mark.parametrize("days", [0.0, -5.0, 1e-9])
def test_degenerate_recipe_days(h, days):
    """PRD-silent: game_days 0/negative/tiny (static schema allows any number). Division-by-zero risk in D-RATE-1:
    only invariants (no crash, finite numbers) for get_recipe, list_recipes and both supply chain modes."""
    use(h, static_with(h.world, lambda sd: recipe(sd, "Chemicals").update(game_days=days, game_days_for_price=days)))
    for name, args in (("get_recipe", {"recipe": "Chemicals"}), ("list_recipes", {"product": "Chemicals"}),
                       ("get_supply_chain", {"product": "Paint", "mode": "recipe", "target_output_per_30d": 100}),
                       ("get_supply_chain", {"product": "Paint"}), ("get_product", {"product": "Chemicals"})):
        ok(call(h, name, args))


def test_degenerate_amounts_and_dangling_references(h):
    """PRD-silent: zero result amount, zero ingredient amount, recipes naming unknown products/building types,
    building types naming unknown recipes. Invariants only."""
    def mut(sd):
        recipe(sd, "Paints")["results"][0]["amount"] = 0
        recipe(sd, "Dye")["ingredients"][0]["amount"] = 0
        recipe(sd, "Chemicals")["building_types"].append("GhostFactory")
        recipe(sd, "Chemicals")["ingredients"].append({"product": "GhostProduct", "amount": 2})
        next(b for b in sd["building_types"] if b["name"] == "ChemicalPlant")["recipes"].append("GhostRecipe")
    use(h, static_with(h.world, mut))
    for name, args in (("get_recipe", {"recipe": "Chemicals"}), ("get_recipe", {"recipe": "Paints"}),
                       ("get_building_type", {"building_type": "ChemicalPlant"}), ("list_recipes", {"limit": 50}),
                       ("list_building_types", {"product": "Chemicals"}), ("get_product", {"product": "Paint"}),
                       ("get_supply_chain", {"product": "Paint", "mode": "recipe", "direction": "both"}),
                       ("get_supply_chain", {"product": "Chemicals", "mode": "recipe", "target_output_per_30d": 10}),
                       ("get_supply_chain", {"product": "Paint", "target_output_per_30d": 10})):
        ok(call(h, name, args))


# ------------------------------------------------------------------ supply chain math (PRD 14.5, D-REQ-1)

def test_requirements_multi_output_and_raw_products_without_recipe(h):
    """D-REQ-1: runs = T / result_amount(r, P); ingredient need = runs x amount. A product without a producing
    recipe is a raw input (PRD 14.5 raw_inputs_total)."""
    use(h, static_with(h.world, add_refining))
    for target, product, oil in ((10, "Tar", 40), (10, "Fuel", 20), (0.5, "Fuel", 1)):
        d = ok(call(h, "get_supply_chain", {"product": product, "mode": "recipe", "target_output_per_30d": target}))
        req = d["requirements"]["products"]
        assert req[f"product:{product}"]["required_per_30d"] == pytest.approx(target)
        assert req["product:Oil"]["required_per_30d"] == pytest.approx(oil)
        assert d["raw_inputs_total"] == {"product:Oil": pytest.approx(oil)}
    d = ok(call(h, "get_supply_chain", {"product": "Oil", "mode": "recipe", "target_output_per_30d": 7}))
    assert d["raw_inputs_total"] == {"product:Oil": 7} and d["truncated_at_depth"] == []
    down = ok(call(h, "get_supply_chain", {"product": "Oil", "mode": "recipe", "direction": "downstream"}))
    keys = {(e["from"], e["to"]) for e in down["edges"]}
    assert {("product:Oil", "recipe:Refining"), ("recipe:Refining", "product:Fuel"), ("recipe:Refining", "product:Tar")} <= keys


def test_requirements_diamond_aggregates_shared_inputs(h):
    """D-REQ-1 recursion: a product needed through two branches gets the sum of both needs."""
    def mut(sd):
        for n in ("Top", "Left", "Right", "Base"):
            sd["products"].append(bf._product(n, n, n, "Components", "Components", [], "Factories"))
        sd["recipes"].append(bf._recipe("Top", "Top", "Top", [("Left", 1), ("Right", 1)], [("Top", 1)], 10, ["ChemicalPlant"]))
        sd["recipes"].append(bf._recipe("Left", "Left", "Left", [("Base", 2)], [("Left", 1)], 10, ["ChemicalPlant"]))
        sd["recipes"].append(bf._recipe("Right", "Right", "Right", [("Base", 3)], [("Right", 1)], 10, ["ChemicalPlant"]))
    use(h, static_with(h.world, mut))
    d = ok(call(h, "get_supply_chain", {"product": "Top", "mode": "recipe", "target_output_per_30d": 10}))
    req = {k: v["required_per_30d"] for k, v in d["requirements"]["products"].items()}
    assert req == {"product:Top": 10, "product:Left": 10, "product:Right": 10, "product:Base": 50}
    assert d["raw_inputs_total"] == {"product:Base": 50} and d["cycles_detected"] == []


def test_recipe_choice_and_first_recipe_flag(h):
    """D-REQ-1: recipe_choice wins; else (recipe mode, no producers) the first recipe of several, flagged."""
    d = ok(call(h, "get_supply_chain", {"product": "Paint", "mode": "recipe", "target_output_per_30d": 30,
                                        "recipe_choice": {"Paint": "PaintsAdvanced"}}))
    req = {k: v["required_per_30d"] for k, v in d["requirements"]["products"].items()}
    assert req == {"product:Paint": 30, "product:Chemicals": 20, "product:Dye": 10, "product:Gas": 30,
                   "product:Flowers": 10, "product:Water": 5}
    d = ok(call(h, "get_supply_chain", {"product": "Paint", "mode": "recipe", "target_output_per_30d": 100}))
    used = {k.split(":", 1)[-1]: v for k, v in d["requirements"]["recipes_used"].items()}
    assert used["Paint"]["recipe"].split(":", 1)[-1] == "Paints"
    assert "several" in used["Paint"]["chosen_by"]


def test_deep_chain_truncates_at_depth(h):
    """PRD 14.5 depth (default 6, max 12) and truncated_at_depth on a 30-level linear chain C_i <- 2 C_(i+1)."""
    def mut(sd):
        for i in range(31):
            sd["products"].append(bf._product(f"C{i:02d}", f"Chaine {i}", f"Chain {i}", "Components", "Components", [], "Factories"))
        for i in range(30):
            sd["recipes"].append(bf._recipe(f"C{i:02d}", f"C{i}", f"C{i}", [(f"C{i + 1:02d}", 2)], [(f"C{i:02d}", 1)], 10, ["ChemicalPlant"]))
    use(h, static_with(h.world, mut))
    for depth, expect in ((None, "product:C06"), (12, "product:C12"), (1, "product:C01")):
        args = {"product": "C00", "mode": "recipe", "target_output_per_30d": 1}
        if depth:
            args["depth"] = depth
        d = ok(call(h, "get_supply_chain", args))
        assert d["truncated_at_depth"] == [expect]
        assert f"product:C{int(expect[-2:]) + 1:02d}" not in {n["id"] for n in d["nodes"]}
    d = ok(call(h, "get_supply_chain", {"product": "C00", "mode": "recipe", "depth": 12, "target_output_per_30d": 1}))
    assert d["requirements"]["products"]["product:C11"]["required_per_30d"] == 2 ** 11
    assert all(math.isfinite(v["required_per_30d"]) for v in d["requirements"]["products"].values())


@pytest.mark.parametrize("cycle", ["two", "self"])
def test_recipe_cycles_are_reported_not_looped(h, cycle):
    """PRD 14.5 cycles_detected: A needs B needs A (and a self-consuming recipe) terminate, report the cycle and
    stay finite. PRD-silent: the required amount on a cycle (only invariants)."""
    def mut(sd):
        for n in ("Aa", "Bb"):
            sd["products"].append(bf._product(n, n, n, "Components", "Components", [], "Factories"))
        if cycle == "two":
            sd["recipes"].append(bf._recipe("Aa", "Aa", "Aa", [("Bb", 1)], [("Aa", 1)], 10, ["ChemicalPlant"]))
            sd["recipes"].append(bf._recipe("Bb", "Bb", "Bb", [("Aa", 2)], [("Bb", 1)], 10, ["ChemicalPlant"]))
        else:
            sd["recipes"].append(bf._recipe("Aa", "Aa", "Aa", [("Aa", 1), ("Water", 1)], [("Aa", 3)], 10, ["ChemicalPlant"]))
    use(h, static_with(h.world, mut))
    for args in ({"mode": "recipe", "direction": "both"}, {"mode": "recipe", "depth": 12}, {}):
        d = ok(call(h, "get_supply_chain", {"product": "Aa", "target_output_per_30d": 10, **args}))
        assert d["cycles_detected"], args


# Regression test for DEFECT CA-4 (fixed).
def test_requirement_truncation_is_reported_actual_mode(h):
    """PRD 14.5: requirements/raw_inputs_total plus truncated_at_depth. With depth=2 the building graph is not
    truncated, but D-REQ-1 stops at Gas/Flowers/Water (stack depth 2): raw_inputs_total == {} and
    truncated_at_depth == [] -> the raw Gas need (75 for 100 Paint, the PRD 14.5 oracle) silently disappears."""
    d = ok(call(h, "get_supply_chain", {"product": "Paint", "depth": 2, "target_output_per_30d": 100}))
    for p in ("Gas", "Flowers", "Water"):
        assert f"product:{p}" in d["raw_inputs_total"] or f"product:{p}" in d["truncated_at_depth"], (p, d["raw_inputs_total"])


# Regression test for DEFECT CA-4 (fixed).
def test_requirement_truncation_is_reported_recipe_mode(h):
    """Recipe mode, depth=1: the graph shows Fuel <- Refining <- Oil with nothing truncated (Oil has no recipe),
    yet raw_inputs_total is {} because D-REQ-1 truncates Oil before classifying it as raw."""
    use(h, static_with(h.world, add_refining))
    d = ok(call(h, "get_supply_chain", {"product": "Fuel", "mode": "recipe", "depth": 1, "target_output_per_30d": 10}))
    assert "product:Oil" in {n["id"] for n in d["nodes"]}
    assert d["raw_inputs_total"].get("product:Oil") == 20 or "product:Oil" in d["truncated_at_depth"]


# Regression test for DEFECT CA-5 (fixed).
def test_requirement_ids_use_prd_id_format(h):
    """PRD 7.1: all ids exposed by the MCP are `<kind>:<key>`. requirements.products uses product ids, but
    requirements.target.product, recipes_used keys (product) and recipes_used[].recipe are raw asset names."""
    for args in ({"mode": "recipe"}, {}):
        d = ok(call(h, "get_supply_chain", {"product": "Paint", "target_output_per_30d": 10, **args}))
        req = d["requirements"]
        assert req["target"]["product"] == "product:Paint"
        assert all(k.startswith("product:") for k in req["recipes_used"])
        assert all(v["recipe"].startswith("recipe:") for v in req["recipes_used"].values())


# Regression test for DEFECT CA-5 (fixed).
def test_get_recipe_per_30d_uses_product_ids(h):
    """PRD 7.1: `<kind>:<key>` ids. get_recipe.definition.inputs uses product ids, per_30d (D-RATE-1) raw names."""
    d = ok(call(h, "get_recipe", {"recipe": "Paints"}))
    assert {i["product"] for i in d["definition"]["inputs"]} == {"product:Chemicals", "product:Dye"}
    assert {i["product"] for i in d["per_30d"]["inputs"]} == {"product:Chemicals", "product:Dye"}


# Regression test for DEFECT CA-5 (fixed).
def test_cycles_detected_use_prd_id_format(h):
    def mut(sd):
        for n in ("Aa", "Bb"):
            sd["products"].append(bf._product(n, n, n, "Components", "Components", [], "Factories"))
        sd["recipes"].append(bf._recipe("Aa", "Aa", "Aa", [("Bb", 1)], [("Aa", 1)], 10, ["ChemicalPlant"]))
        sd["recipes"].append(bf._recipe("Bb", "Bb", "Bb", [("Aa", 2)], [("Bb", 1)], 10, ["ChemicalPlant"]))
    use(h, static_with(h.world, mut))
    d = ok(call(h, "get_supply_chain", {"product": "Aa", "mode": "recipe", "target_output_per_30d": 10}))
    assert d["cycles_detected"]
    assert all(isinstance(x, str) and x.startswith("product:") for c in d["cycles_detected"] for x in c), d["cycles_detected"]


# ------------------------------------------------------------------ actual-mode graph (PRD 14.5)

@pytest.mark.parametrize("building", [bf.B_PC1, bf.B_PF1, bf.B_WH, bf.B_GW1])
@pytest.mark.parametrize("depth", [1, 2, 6])
def test_both_is_union_of_directions_without_cycles(h, building, depth):
    """PRD 14.5 direction upstream/downstream/both: `both` contains every edge of each single direction."""
    up = ok(call(h, "get_supply_chain", {"building": building, "direction": "upstream", "depth": depth}))
    down = ok(call(h, "get_supply_chain", {"building": building, "direction": "downstream", "depth": depth}))
    both = ok(call(h, "get_supply_chain", {"building": building, "direction": "both", "depth": depth}))
    assert set(map(edge_key, up["edges"])) | set(map(edge_key, down["edges"])) <= set(map(edge_key, both["edges"]))
    node_ids = {n["id"] for n in both["nodes"]}
    for e in both["edges"]:
        assert e["to"] in node_ids and (e["from"] is None or e["from"] in node_ids)


def _cycle_world(world):
    st = copy.deepcopy(world["state"]["data"])
    r = copy.deepcopy(st["routes_player"][0])
    r.update(origin=bf.B_PF1, destination=bf.B_PC1, endpoint=bf.B_PC1, product="Paint",
             route_key=f"{bf.B_PF1}|Paint|{bf.B_PC1}|own|0")
    st["routes_player"].append(r)
    return rebuild(world, state_data=st)


# Regression test for DEFECT CA-6 (fixed).
def test_both_is_union_of_directions_with_route_cycle(h):
    """Route cycle PC1 -> PF1 -> PC1, depth 1: the upstream walk probes PF1 at the depth limit, deletes the probed
    edges but keeps their dedup keys, so the downstream walk can no longer add PC1 -> PF1 (2 Chemicals routes)."""
    use(h, _cycle_world(h.world))
    up = ok(call(h, "get_supply_chain", {"building": bf.B_PC1, "direction": "upstream", "depth": 1}))
    down = ok(call(h, "get_supply_chain", {"building": bf.B_PC1, "direction": "downstream", "depth": 1}))
    both = ok(call(h, "get_supply_chain", {"building": bf.B_PC1, "direction": "both", "depth": 1}))
    missing = (set(map(edge_key, up["edges"])) | set(map(edge_key, down["edges"]))) - set(map(edge_key, both["edges"]))
    assert not missing, missing


def test_route_cycle_detected_actual_mode(h):
    """PRD 14.5 cycles_detected on the configured-route graph (depth large enough to close the loop)."""
    use(h, _cycle_world(h.world))
    for direction in ("upstream", "downstream", "both"):
        d = ok(call(h, "get_supply_chain", {"building": bf.B_PC1, "direction": direction}))
        assert d["cycles_detected"], direction


def test_actual_mode_zero_and_missing_cycle_days(h):
    """Division-by-zero risk: buildings with cycle_days_effective 0 / None; a city with consumption interval 0."""
    st = copy.deepcopy(h.world["state"]["data"])
    for b in st["buildings_player"]:
        if b["key"] == bf.B_PC1:
            b["cycle_days_effective"] = 0.0
        if b["key"] == bf.B_PF1:
            b["cycle_days_effective"] = None
    for c in st["cities"]:
        c["consumption_interval_days"] = 0
    use(h, rebuild(h.world, state_data=st))
    d = ok(call(h, "get_supply_chain", {"product": "Paint", "direction": "both", "target_output_per_30d": 100}))
    nodes = {n["id"]: n for n in d["nodes"]}
    assert nodes[f"building:{bf.B_PF1}"]["theoretical_per_30d"] is None
    ok(call(h, "get_supply_chain", {"building": bf.B_PC1, "direction": "both"}))
    p = ok(call(h, "get_product", {"product": "Paint"}))
    assert p["shops_accepting"]["count"] >= 1


def test_supply_chain_company_without_producers(h):
    """PRD 14.5 company param: an AI company with no producer of the product -> empty graph, gap reported."""
    d = ok(call(h, "get_supply_chain", {"product": "Water", "company": "Borealis Corp", "target_output_per_30d": 10}))
    assert d["company"] == "company:2" and d["nodes"] == [] and d["edges"] == []
    assert d["requirements"]["products"]["product:Water"]["available_theoretical_per_30d"] == 0


def test_requirement_oracle_actual_mode_uses_player_recipe(h):
    """D-REQ-1 (else the recipe used by the player's producing buildings) and the PRD 14.5 Paint oracle."""
    d = ok(call(h, "get_supply_chain", {"product": "Paint", "target_output_per_30d": 100}))
    req = {k: v["required_per_30d"] for k, v in d["requirements"]["products"].items()}
    assert (req["product:Chemicals"], req["product:Dye"], req["product:Gas"]) == (50, 100, 75)
    for v in d["requirements"]["products"].values():
        assert v["gap"] == pytest.approx(v["available_theoretical_per_30d"] - v["required_per_30d"], abs=1e-3)


# ------------------------------------------------------------------ lifecycle / static-only (PRD 13.4, 13.7, 16)

def _live_fields_null(name, d):
    if name == "list_products":
        assert all(r["current_price"] is None and r["base_price"] is None and r["unlocked"] is None for r in d["products"])
    elif name == "get_product":
        assert d["market"] is None and d["player_producers"] is None and d["shops_accepting"] is None
    elif name == "list_recipes":
        assert all(r["unlocked"] is None for r in d["recipes"])
    elif name == "list_building_types":
        assert all(r["unlocked"] is None for r in d["building_types"])
    elif name == "get_building_type":
        assert d["unlocked"] is None
    assert d["unavailable"], name


def test_static_tools_when_game_not_running(h):
    """PRD 13.4 / 16 row "Game not running": static catalogue from the last static.json, source static_catalog,
    warning catalog_from_previous_session; static-plus-live live parts in `unavailable` (13.7). Runtime part
    (supply chain mode=actual) -> game_not_running."""
    h.procs.procs = []
    for name, args in STATIC_CALLS.items():
        r = call(h, name, args)
        d = ok(r)
        assert r["meta"]["source"] == "static_catalog" and r["meta"]["stale"] is False, name
        assert "catalog_from_previous_session" in wcodes(r), name
        if name in STATIC_LIVE:
            _live_fields_null(name, d)
    err(call(h, "get_supply_chain", {"product": "Paint"}), "game_not_running")


def test_static_live_tools_allow_stale_when_not_running(h):
    """PRD 13.7: the live part is served stale with allow_stale (13.4: source stale_snapshot, stale, reason)."""
    h.procs.procs = []
    for name in STATIC_LIVE:
        r = call(h, name, {**STATIC_CALLS[name], "allow_stale": True})
        ok(r)
        assert r["meta"]["source"] == "stale_snapshot" and r["meta"]["stale"] is True
        assert r["meta"]["stale_reason"] == "game_not_running"
    rows = {x["id"]: x for x in ok(call(h, "list_products", {"allow_stale": True}))["products"]}
    assert rows["product:Paint"]["current_price"] == 1450.0
    r = call(h, "get_supply_chain", {"product": "Paint", "allow_stale": True})
    ok(r)
    assert r["meta"]["stale"] is True and r["meta"]["stale_reason"] == "game_not_running"


@pytest.mark.parametrize("state,code", [("menu", "at_main_menu"), ("loading", "loading"), ("disabled", "observer_disabled"),
                                        ("faulted", "observer_faulted")])
def test_static_tools_outside_ready(tmp_path, clock, procs, state, code):
    """PRD 16 rows main menu / loading: static tools answer from the last static.json + warning; runtime parts
    return the lifecycle code. fresh=true on mode=recipe still reports fresh_not_applicable and writes nothing."""
    hh, obs = _observer_harness(tmp_path, clock, procs, serve=False)
    obs.heartbeat(state)
    for name, args in STATIC_CALLS.items():
        r = call(hh, name, args)
        ok(r)
        assert "catalog_from_previous_session" in wcodes(r) and r["meta"]["source"] == "static_catalog", name
    err(call(hh, "get_supply_chain", {"product": "Paint"}), code)
    r = call(hh, "get_supply_chain", {"product": "Paint", "mode": "recipe", "fresh": True})
    assert ok(r) and "fresh_not_applicable" in wcodes(r)
    assert obs.read_refresh_request() is None


def test_unsupported_build_blocks_static_tools(tmp_path, clock, procs):
    """PRD 13.4 exception / 16 last row: while the heartbeat reports unsupported_build, static tools also fail."""
    hh, obs = _observer_harness(tmp_path, clock, procs, serve=False)
    obs.unsupported_build()
    for name, args in list(STATIC_CALLS.items()) + [("get_supply_chain", {"product": "Paint"}),
                                                    ("list_products", {"allow_stale": True})]:
        r = call(hh, name, args)
        err(r, "unsupported_build")
        assert r["meta"]["compatibility"] == "unsupported_build"


def test_live_without_state_snapshot(h):
    """PRD 13.7 static-plus-live: no current state snapshot -> definitions + live part in `unavailable`;
    mode=actual needs state -> snapshot_unavailable (13.3: ready but no snapshot yet)."""
    (h.exchange / "state.json").unlink()
    h.restart()
    for name, args in STATIC_CALLS.items():
        r = call(h, name, args)
        d = ok(r)
        assert r["meta"]["source"] == "static_catalog" and "catalog_from_previous_session" not in wcodes(r)
        if name in STATIC_LIVE:
            _live_fields_null(name, d)
    err(call(h, "get_supply_chain", {"product": "Paint"}), "snapshot_unavailable")


def test_quickload_other_world_session(h):
    """PRD 16 quickload: an old-session state is never current; static-plus-live answer with the live part
    unavailable, or stale (world_session_changed) with allow_stale."""
    w = copy.deepcopy(h.world)
    w["heartbeat"] = bf.build_heartbeat(bf.BASE_TIME, world_session=bf.WORLD_SESSION_2, static_doc=w["static"],
                                        state_doc=w["state"], history_doc=w["history"])
    h.write_world(w, ("heartbeat",))
    for name in STATIC_LIVE:
        _live_fields_null(name, ok(call(h, name, STATIC_CALLS[name])))
        r = call(h, name, {**STATIC_CALLS[name], "allow_stale": True})
        ok(r)
        assert r["meta"]["stale"] is True and r["meta"]["stale_reason"] == "world_session_changed"
    err(call(h, "get_supply_chain", {"product": "Paint"}), "snapshot_unavailable")


def test_stale_by_age(h):
    """PRD 13.4: live but stale by age -> data returned with stale: true, stale_reason: age."""
    st = bf.build_state(bf.BASE_TIME, seq=30, static_doc=h.world["static"], capture_age_s=120.0)
    w = dict(h.world, state=st, heartbeat=bf.build_heartbeat(bf.BASE_TIME, static_doc=h.world["static"], state_doc=st,
                                                            history_doc=h.world["history"]))
    use(h, w)
    for name, args in [(n, STATIC_CALLS[n]) for n in STATIC_LIVE] + [("get_supply_chain", {"product": "Paint"})]:
        r = call(h, name, args)
        ok(r)
        assert r["meta"]["stale"] is True and r["meta"]["stale_reason"] == "age" and "stale" in wcodes(r), name
    for name in ("get_recipe",):
        r = call(h, name, STATIC_CALLS[name])
        assert ok(r) and r["meta"]["stale"] is False


@pytest.mark.parametrize("procs_running", [True, False])
def test_static_json_missing(h, procs_running):
    """PRD 16: static tools use the last static.json, else snapshot_unavailable (13.3)."""
    (h.exchange / "static.json").unlink()
    h.restart()
    if not procs_running:
        h.procs.procs = []
    for name, args in list(STATIC_CALLS.items()) + [("get_supply_chain", {"product": "Paint"})]:
        r = call(h, name, args)
        if not procs_running and name == "get_supply_chain" and "mode" not in args:
            assert r["error"]["code"] in ("snapshot_unavailable", "game_not_running")
        else:
            err(r, "snapshot_unavailable")


def test_nothing_published_game_not_running(empty_h):
    empty_h.procs.procs = []
    for name, args in STATIC_CALLS.items():
        err(call(empty_h, name, args), "snapshot_unavailable")


@pytest.mark.parametrize("bad", ["{not json", "", json.dumps({"schema": "roi-mcp/static", "schema_version": "1.0.0"})])
def test_static_json_malformed(h, bad):
    """PRD 11.4: invalid file -> keep the last good snapshot with snapshot_invalid_using_previous; without a
    previous one (fresh server) -> snapshot_unavailable."""
    for name, args in STATIC_CALLS.items():
        ok(call(h, name, args))
    h.write_raw("static", bad)
    for name, args in STATIC_CALLS.items():
        r = call(h, name, args)
        ok(r)
        assert "snapshot_invalid_using_previous" in wcodes(r), name
    h.restart()
    for name, args in STATIC_CALLS.items():
        err(call(h, name, args), "snapshot_unavailable")


def test_static_schema_major_mismatch(h):
    """PRD 11.4 / 16: a static.json with another schema major -> schema_mismatch for static tools."""
    w = copy.deepcopy(h.world)
    w["static"]["schema_version"] = "2.0.0"
    h.write_world(w, ("static",))
    h.restart()
    for name, args in list(STATIC_CALLS.items()) + [("get_supply_chain", {"product": "Paint"})]:
        err(call(h, name, args), "schema_mismatch")


def test_static_ref_mismatch_warns(h):
    """PRD 11.4: state whose static_ref does not match static.json (after one reload) -> warning static_mismatch
    on tools that use the state snapshot."""
    w = copy.deepcopy(h.world)
    w["state"]["static_ref"] = {"seq": 99, "content_hash": "0" * 64}
    w["state"]["seq"] = 40
    h.write_world(w, ("state",))
    for name, args in [(n, STATIC_CALLS[n]) for n in STATIC_LIVE] + [("get_supply_chain", {"product": "Paint"})]:
        r = call(h, name, args)
        ok(r)
        assert "static_mismatch" in wcodes(r), name


def test_section_failures(h):
    """PRD 13.3 section_unavailable for a required section (buildings_player for mode=actual); optional live
    sections (market, research) missing -> live parts unavailable, definitions still served."""
    st = copy.deepcopy(h.world["state"]["data"])
    st["market"] = None
    st["research"] = None
    w = rebuild(h.world, state_data=st)
    w["state"]["sections"]["market"] = bf.section_status(1512, 0, "failed", "exception")
    w["state"]["sections"]["research"] = bf.section_status(1512, 0, "disabled", "kill")
    w["state"]["content_hash"] = bf.content_hash(st)
    use(h, w)
    rows = ok(call(h, "list_products", {"limit": 50}))["products"]
    assert all(r["current_price"] is None and r["unlocked"] is None for r in rows)
    p = call(h, "get_product", {"product": "Paint"})
    assert ok(p)["market"] is None and p["data"]["unavailable"]
    assert all(r["unlocked"] is None for r in ok(call(h, "list_recipes", {"limit": 50}))["recipes"])
    st2 = copy.deepcopy(h.world["state"]["data"])
    st2["buildings_player"] = None
    w2 = rebuild(h.world, state_data=st2)
    w2["state"]["sections"]["buildings_player"] = bf.section_status(1512, 0, "failed", "exception")
    use(h, w2)
    err(call(h, "get_supply_chain", {"product": "Paint"}), "section_unavailable")
    ok(call(h, "get_supply_chain", {"product": "Paint", "mode": "recipe"}))


# ------------------------------------------------------------------ refresh scopes (PRD 13.2, 13.7)

FRESH_ARGS = {
    "list_products": {"tag": "paint", "fields": "full"}, "get_product": {"product": "peinture"},
    "list_recipes": {"available_to_player": True}, "list_building_types": {"unlocked_only": True},
    "get_building_type": {"building_type": "Paint Factory"},
    "get_supply_chain": {"building": bf.B_PC1, "direction": "both", "mode": "actual"},
}


@pytest.mark.parametrize("name", list(FRESH_ARGS))
def test_fresh_writes_exactly_state(tmp_path, clock, procs, name):
    """PRD 13.7: static-plus-live tools and get_supply_chain (actual) refresh `state` only."""
    hh, obs = _observer_harness(tmp_path, clock, procs)
    r = call(hh, name, {**FRESH_ARGS[name], "fresh": True})
    ok(r)
    req = obs.read_refresh_request()
    assert {k for k, v in req["requests"].items() if v is not None} == {"state"}
    assert {s["family"]: s["seq"] for s in r["meta"]["snapshots"]}.get("state") == obs.state_seq
    assert "refresh_timeout" not in wcodes(r)


@pytest.mark.parametrize("args", [{"product": "Paint", "mode": "recipe"},
                                  {"product": "Gas", "mode": "recipe", "direction": "both", "target_output_per_30d": 3}])
def test_recipe_mode_fresh_not_applicable(tmp_path, clock, procs, args):
    """PRD 13.7: mode=recipe uses only static data; no refresh request, warning fresh_not_applicable."""
    hh, obs = _observer_harness(tmp_path, clock, procs)
    r = call(hh, "get_supply_chain", {**args, "fresh": True})
    assert ok(r) and "fresh_not_applicable" in wcodes(r) and r["meta"]["source"] == "static_catalog"
    assert obs.read_refresh_request() is None and hh.app.refresh.writes == 0


def test_get_recipe_static_only(tmp_path, clock, procs):
    """PRD 13.7 get_recipe scope none: reads the static catalogue only, writes no refresh request."""
    hh, obs = _observer_harness(tmp_path, clock, procs)
    r = call(hh, "get_recipe", {"recipe": "Paints"})
    assert ok(r) and r["meta"]["source"] == "static_catalog" and [s["family"] for s in r["meta"]["snapshots"]] == ["static"]
    assert obs.read_refresh_request() is None


# ------------------------------------------------------------------ pagination, size cap, determinism (13.1, 13.2, 14.9)

def _big_catalogue(world, n=130, long_name=None):
    def mut(sd):
        for i in range(n):
            sd["products"].append(bf._product(f"Gen{i:03d}", long_name or f"Produit {i:03d}", f"Generated {i:03d}",
                                              "Components", "Components", ["generated"], "Factories"))
            sd["recipes"].append(bf._recipe(f"GenR{i:03d}", f"Recette {i:03d}", f"Gen recipe {i:03d}", [("Gas", 1)],
                                            [(f"Gen{i:03d}", 1)], 10, ["ChemicalPlant"]))
        for i in range(n // 2):
            sd["building_types"].append(bf._btype(f"GenB{i:03d}", f"Batiment {i:03d}", f"Gen building {i:03d}", 1000 + i,
                                                  ["generated"], "Factories"))
    return static_with(world, mut)


@pytest.mark.parametrize("name", list(LIST_TOOLS))
def test_pagination_large_catalogue(h, name):
    """PRD 13.2: default limit 25, max 50, opaque cursor; pages cover every row exactly once, deterministically."""
    use(h, _big_catalogue(h.world))
    key = LIST_TOOLS[name]
    first = call(h, name)
    assert len(ok(first)[key]) == 25 and first["page"]["next_cursor"]
    total = first["page"]["total"]
    assert total == len(h.world["static"]["data"][key])
    seen, cursor = [], None
    while True:
        r = call(h, name, {"limit": 50, **({"cursor": cursor} if cursor else {})})
        seen += [x["id"] for x in ok(r)[key]]
        assert r["page"]["total"] == total
        cursor = r["page"]["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == total
    assert [x["id"] for x in ok(call(h, name))[key]] == seen[:25]


def test_cursor_rejected_for_other_filters_or_tools(h):
    """PRD 13.2 opaque cursor: a cursor belongs to its call; reuse with other filters or tools is rejected."""
    use(h, _big_catalogue(h.world))
    c = call(h, "list_products", {"tag": "generated"})["page"]["next_cursor"]
    assert c
    ok(call(h, "list_products", {"tag": "generated", "cursor": c}))
    err(call(h, "list_products", {"tag": "paint", "cursor": c}), "invalid_argument")
    err(call(h, "list_products", {"cursor": c}), "invalid_argument")
    err(call(h, "list_recipes", {"cursor": c}), "invalid_argument")
    err(call(h, "list_products", {"tag": "generated", "cursor": c[:-2] + "zz"}), "invalid_argument")


def test_cursor_survives_server_restart(h):
    """PRD 13.1: restarting the server yields identical answers from the same files (cursors included)."""
    use(h, _big_catalogue(h.world))
    p1 = call(h, "list_recipes", {"limit": 50})
    p2 = call(h, "list_recipes", {"limit": 50, "cursor": p1["page"]["next_cursor"]})
    h.restart()
    p2b = call(h, "list_recipes", {"limit": 50, "cursor": p1["page"]["next_cursor"]})
    assert ok(p2b) == ok(p2) and p2b["page"] == p2["page"]


DETERMINISTIC_CALLS = [
    ("list_products", {"fields": "full", "limit": 50}), ("get_product", {"product": "Paint"}),
    ("list_recipes", {"fields": "full", "limit": 50}), ("get_recipe", {"recipe": "PaintsAdvanced"}),
    ("list_building_types", {"fields": "full", "limit": 50}), ("get_building_type", {"building_type": "GasWell"}),
    ("get_supply_chain", {"product": "Paint", "direction": "both", "target_output_per_30d": 100}),
    ("get_supply_chain", {"product": "Paint", "mode": "recipe", "direction": "both"}),
    ("get_supply_chain", {"building": bf.B_WH, "direction": "both"}),
]


def test_identical_answers_after_restart(h):
    """PRD 13.1: identical answers from the same files, across repeated calls and a server restart."""
    before = [call(h, n, a) for n, a in DETERMINISTIC_CALLS]
    again = [call(h, n, a) for n, a in DETERMINISTIC_CALLS]
    h.restart()
    after = [call(h, n, a) for n, a in DETERMINISTIC_CALLS]
    for b, g, a in zip(before, again, after):
        assert b["data"] == g["data"] == a["data"] and b["page"] == a["page"]


def test_size_cap_on_list_with_long_names(h):
    """PRD 14.9: <= ~30 KB; larger pages truncate with page.next_cursor and a `truncated` warning, and paging
    continues without gaps or duplicates."""
    use(h, _big_catalogue(h.world, n=80, long_name="Produit au nom tres long " * 40))
    seen, cursor, truncated = [], None, False
    while True:
        r = call(h, "list_products", {"limit": 50, "fields": "full", **({"cursor": cursor} if cursor else {})})
        seen += [x["id"] for x in ok(r)["products"]]
        truncated = truncated or "truncated" in wcodes(r)
        cursor = r["page"]["next_cursor"]
        if not cursor:
            break
    assert truncated and len(seen) == len(set(seen)) == len(h.world["static"]["data"]["products"])


def _mega_static(world, n_inputs=300, n_users=400):
    """A recipe with n_inputs ingredients (each produced from Gas by its own recipe) -> Paint; Gas consumed by
    n_users recipes. Synthetic stress shape (the real catalogue has no such recipe)."""
    def mut(sd):
        for i in range(n_inputs):
            sd["products"].append(bf._product(f"In{i:03d}", f"Intrant {i:03d}", f"Input {i:03d}", "Components", "Components", [], "Factories"))
        sd["recipes"].append(bf._recipe("Mega", "Mega", "Mega", [(f"In{i:03d}", 1) for i in range(n_inputs)], [("Paint", 1)], 10,
                                        ["PaintFactory"]))
        for i in range(n_users):
            sd["recipes"].append(bf._recipe(f"Use{i:03d}", f"Usage {i:03d}", f"Use {i:03d}", [("Gas", 1)], [(f"In{i % n_inputs:03d}", 1)],
                                            10, ["ChemicalPlant"]))
    return static_with(world, mut)


def test_size_cap_on_detail_and_graph(h):
    """PRD 14.9 on non-list tools: a product consumed by 400 recipes and a 700-node downstream recipe graph
    (lists are truncated with a `truncated` warning)."""
    use(h, _mega_static(h.world))
    for name, args in (("get_product", {"product": "Gas"}),
                       ("get_supply_chain", {"product": "Gas", "mode": "recipe", "direction": "downstream"}),
                       ("get_supply_chain", {"product": "Gas", "mode": "recipe", "direction": "both"}),
                       ("get_building_type", {"building_type": "ChemicalPlant"})):
        r = call(h, name, args)
        ok(r)
    assert "truncated" in wcodes(call(h, "get_product", {"product": "Gas"}))


# Regression test for DEFECT CA-7 (fixed).
@pytest.mark.parametrize("mode", ["recipe", "actual"])
def test_size_cap_on_large_requirement_maps(h, mode):
    """PRD 14.9: every response MUST be <= ~30 KB (check_response enforces 30 000 bytes). A requirement tree of
    ~600 products gives ~46 KB: requirements.products, recipes_used and raw_inputs_total are objects, which
    App._apply_size_cap/_largest_list never shorten."""
    use(h, _mega_static(h.world))
    call(h, "get_supply_chain", {"product": "Paint", "mode": mode, "recipe_choice": {"Paint": "Mega"},
                                 "target_output_per_30d": 10})
