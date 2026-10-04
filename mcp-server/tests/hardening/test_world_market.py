"""Hardening: cities, shops, regions, market and technology tools (PRD 14.7 / 14.8).

Scope: list_cities, get_city, get_shop, find_shops, list_regions, get_region, get_market, get_tech_tree,
get_research_state. Every response (ok and error) is validated with `check_response`.

Expected behaviour is asserted only where PRD / schemas / docs fix it; elsewhere only invariants are
asserted (valid envelope, schema-valid, no internal_error) and the ambiguity is named in a comment
("AMBIGUITY: ..."). Implementation/PRD contradictions are kept as strict xfails ("DEFECT WM-<n>").
"""

from __future__ import annotations

import asyncio
import copy
from datetime import timedelta

import pytest

import build_fixtures as bf
from conftest import game_proc
from fake_observer import FakeObserver
from test_tools_contract import VALID_CALLS, _observer_harness, check_response

TOOLS = ["list_cities", "get_city", "get_shop", "find_shops", "list_regions", "get_region", "get_market", "get_tech_tree",
         "get_research_state"]
RUNTIME_TOOLS = [t for t in TOOLS if t != "get_tech_tree"]
LIST_TOOLS = {"list_cities": "cities", "find_shops": "shops", "list_regions": "regions", "get_tech_tree": "nodes"}

ALL_TECH = {"BasicGas", "BasicFarming", "Petrochemistry", "Dyes", "Paints", "AdvancedPaints", "Railways", "Plastics",
            "Polymers", "FutureTech", "PaintDiscount"}


# ============================================================================ helpers

def call(h, name, args=None):
    resp = h.call(name, args or {})
    check_response(name, resp)
    assert not (resp["ok"] is False and resp["error"]["code"] == "internal_error"), resp["error"]
    return resp


def ok(h, name, args=None):
    resp = call(h, name, args)
    assert resp["ok"], resp.get("error")
    return resp


def err(h, name, args, code):
    resp = call(h, name, args)
    assert resp["ok"] is False, resp.get("data")
    assert resp["error"]["code"] == code, resp["error"]
    return resp


def codes(resp):
    return [w["code"] for w in resp["meta"]["warnings"]]


def unavailable_fields(resp):
    return [u["field"] for u in resp["data"]["unavailable"]]


def _heartbeat(h, w):
    w["heartbeat"] = bf.build_heartbeat(h.clock.now(), static_doc=w["static"], state_doc=w["state"], history_doc=w["history"])


def restate(h, w, failed=(), status="failed"):
    """Republish w['state'] (new seq + content hash); `failed` sections get data null and the given status."""
    st = w["state"]
    st["seq"] += 1
    for s in failed:
        st["data"][s] = None
    st["content_hash"] = bf.content_hash(st["data"])
    st["sections"] = bf.state_sections(st["data"])
    for s in failed:
        st["sections"][s] = bf.section_status(1512, 0, status, "exception" if status == "failed" else "disabled_by_config")
    _heartbeat(h, w)
    h.write_world(w, ("state", "heartbeat"))


def restatic(h, w):
    """Republish w['static'] and keep the state/history static_ref consistent with it."""
    sd = w["static"]
    sd["seq"] += 1
    sd["content_hash"] = bf.content_hash(sd["data"])
    for fam in ("state", "history"):
        w[fam]["static_ref"] = bf.static_ref_of(sd)
    w["state"]["seq"] += 1
    _heartbeat(h, w)
    h.write_world(w, ("static", "state", "history", "heartbeat"))


def world_of(h):
    return copy.deepcopy(h.world)


def walk_pages(h, name, args, key, limit):
    """Follow page.next_cursor to the end; returns the concatenated rows and the number of pages."""
    rows, cursor, pages = [], None, 0
    while True:
        a = dict(args, limit=limit)
        if cursor:
            a["cursor"] = cursor
        r = ok(h, name, a)
        rows.extend(r["data"][key])
        pages += 1
        cursor = r["page"]["next_cursor"]
        assert pages < 100
        if not cursor:
            return rows, pages, r


def monotonic(values):
    vals = [v for v in values if v is not None]
    return vals == sorted(vals) or vals == sorted(vals, reverse=True)


# ============================================================================ valid calls (every parameter, alone and combined)

VALID_MATRIX = [
    ("list_cities", {}), ("list_cities", {"sort": "population"}), ("list_cities", {"sort": "tier"}),
    ("list_cities", {"fields": "compact"}), ("list_cities", {"fields": "full"}), ("list_cities", {"limit": 1}),
    ("list_cities", {"limit": 50, "sort": "tier", "fields": "full"}), ("list_cities", {"allow_stale": True}),
    ("list_cities", {"fresh": False, "allow_stale": False}),
    ("get_city", {"city": "Valmont"}), ("get_city", {"city": "city:11"}), ("get_city", {"city": "saint-eloi"}),
    ("get_city", {"city": "SAINT-ÉLOI", "allow_stale": True}),
    ("get_shop", {"shop": f"building:{bf.S_HW1}"}), ("get_shop", {"shop": bf.S_HW2}), ("get_shop", {"shop": "épicerie générale"}),
    ("find_shops", {"product": "Paint"}), ("find_shops", {"product": "product:Paint"}), ("find_shops", {"product": "peinture"}),
    ("find_shops", {"product": "Paint", "from_building": bf.B_PF1}), ("find_shops", {"product": "Paint", "city": "Valmont"}),
    ("find_shops", {"product": "Paint", "region": "Brindlewick"}), ("find_shops", {"product": "Chemicals"}),
    ("find_shops", {"product": "Paint", "sort": "demand"}), ("find_shops", {"product": "Paint", "sort": "price"}),
    ("find_shops", {"product": "Paint", "sort": "distance", "from_building": bf.B_PF1}),
    ("find_shops", {"product": "Paint", "sort": "unmet_demand"}),
    ("find_shops", {"product": "Paint", "from_building": f"building:{bf.B_PF1}", "city": "city:10", "region": f"region:{bf.R_VAL}",
                    "sort": "distance", "limit": 2}),
    ("list_regions", {}), ("list_regions", {"owner": "player"}), ("list_regions", {"owner": "ai"}), ("list_regions", {"owner": "unowned"}),
    ("list_regions", {"owner": "company:2"}), ("list_regions", {"owner": "Cobalt Works"}), ("list_regions", {"resource": "Gas"}),
    ("list_regions", {"resource": "eau"}), ("list_regions", {"owner": "player", "resource": "Water", "fields": "full", "limit": 1}),
    ("get_region", {"region": "Greyhollow"}), ("get_region", {"region": f"region:{bf.R_NOR}"}), ("get_region", {"region": "saint-éloi"}),
    ("get_market", {}), ("get_market", {"products": ["Paint"]}), ("get_market", {"products": ["Gaz", "product:Water"]}),
    ("get_market", {"include": ["state"]}), ("get_market", {"include": ["contracts"]}), ("get_market", {"include": ["auctions"]}),
    ("get_market", {"include": ["state", "contracts", "auctions"], "products": ["Paint", "Gas"]}),
    ("get_tech_tree", {}), ("get_tech_tree", {"tree": "Chemistry"}), ("get_tech_tree", {"tree": "Logistique"}),
    ("get_tech_tree", {"tree": "tech_tree:Logistics", "state": "queued"}), ("get_tech_tree", {"company": "player"}),
    ("get_tech_tree", {"company": "Borealis Corp", "state": "unlocked"}), ("get_tech_tree", {"limit": 3}),
    *[("get_tech_tree", {"state": s}) for s in ("unlocked", "available", "queued", "researching", "locked", "teaser")],
    ("get_research_state", {}), ("get_research_state", {"company": "player"}), ("get_research_state", {"company": "company:1"}),
    ("get_research_state", {"company": "Cobalt Works"}),
]


@pytest.mark.parametrize("name,args", VALID_MATRIX)
def test_valid_call_matrix(h, name, args):
    r = ok(h, name, args)
    assert r["meta"]["game_state"] == "ready" and r["meta"]["stale"] is False
    assert r["meta"]["source"] == "live_snapshot"
    if name in LIST_TOOLS:
        key = LIST_TOOLS[name]
        assert len(r["data"][key]) <= (args.get("limit") or 25)
        assert r["page"]["total"] is not None and r["page"]["total"] >= len(r["data"][key])


@pytest.mark.parametrize("name", TOOLS)
def test_omitted_optionals_equal_explicit_defaults(h, name):
    """PRD 13.2: fresh/allow_stale default false; list tools default limit 25 and fields compact."""
    base = dict(VALID_CALLS[name]) if name in ("get_city", "get_shop", "find_shops", "get_region") else {}
    a = ok(h, name, base)
    explicit = dict(base, fresh=False, allow_stale=False)
    if name in LIST_TOOLS:
        explicit["limit"] = 25
    if name in ("list_cities", "list_regions"):
        explicit["fields"] = "compact"
    b = ok(h, name, explicit)
    assert a["data"] == b["data"] and a["page"]["total"] == b["page"]["total"]


# ============================================================================ parameter validation

BOGUS_PARAMS = ["set_price", "owner_id", "Product", "page", "offset", "query", "include_history", "", "fields "]


@pytest.mark.parametrize("name", TOOLS)
@pytest.mark.parametrize("bogus", BOGUS_PARAMS)
def test_unknown_parameters_rejected(h, name, bogus):
    base = dict(VALID_CALLS[name])
    base[bogus] = True
    err(h, name, base, "invalid_argument")


INVALID = [
    ("list_cities", {"limit": 0}), ("list_cities", {"limit": 51}), ("list_cities", {"limit": -1}), ("list_cities", {"limit": "5"}),
    ("list_cities", {"limit": 2.5}), ("list_cities", {"limit": True}), ("list_cities", {"sort": "name"}),
    ("list_cities", {"sort": "Population"}), ("list_cities", {"fields": "all"}), ("list_cities", {"cursor": "!!!"}),
    ("list_cities", {"cursor": 5}), ("list_cities", {"fresh": "yes"}), ("list_cities", {"allow_stale": 1}),
    ("get_city", {}), ("get_city", {"city": 10}), ("get_city", {"city": None}), ("get_city", {"city": ""}), ("get_city", {"city": "   "}),
    ("get_city", {"city": ["Valmont"]}),
    ("get_shop", {}), ("get_shop", {"shop": 3}), ("get_shop", {"shop": ""}),
    ("get_shop", {"shop": f"vehicle:{bf.WORLD_SESSION}:-1203"}),
    ("find_shops", {}), ("find_shops", {"from_building": bf.B_PF1}), ("find_shops", {"product": ""}), ("find_shops", {"product": 1}),
    ("find_shops", {"product": "Paint", "sort": "name"}), ("find_shops", {"product": "Paint", "limit": 0}),
    ("find_shops", {"product": "Paint", "limit": 51}),
    ("find_shops", {"product": "Paint", "city": 10}), ("find_shops", {"product": ["Paint"]}),
    ("list_regions", {"owner": ""}), ("list_regions", {"owner": 1}), ("list_regions", {"resource": 7}), ("list_regions", {"limit": 99}),
    ("list_regions", {"sort": "name"}), ("list_regions", {"fields": "FULL"}),
    ("get_region", {}), ("get_region", {"region": 5}), ("get_region", {"region": " "}),
    ("get_market", {"products": "Paint"}), ("get_market", {"products": [1]}), ("get_market", {"include": ["prices"]}),
    ("get_market", {"include": "state"}), ("get_market", {"products": ["Paint"] * 101}), ("get_market", {"products": [""]}),
    ("get_tech_tree", {"state": "bogus"}), ("get_tech_tree", {"state": "Unlocked"}), ("get_tech_tree", {"tree": 1}),
    ("get_tech_tree", {"limit": 0}), ("get_tech_tree", {"company": 1}),
    ("get_research_state", {"company": 2}), ("get_research_state", {"company": ""}),
]


@pytest.mark.parametrize("name,args", INVALID)
def test_invalid_values_and_types(h, name, args):
    err(h, name, args, "invalid_argument")


@pytest.mark.parametrize("name,args", [
    ("find_shops", {"product": "Paint", "from_building": ""}), ("find_shops", {"product": "Paint", "city": ""}),
    ("find_shops", {"product": "Paint", "region": ""}), ("list_regions", {"resource": ""}), ("get_tech_tree", {"tree": ""}),
    ("get_tech_tree", {"company": ""}),
])
def test_empty_optional_selectors(h, name, args):
    # PRD 13.2: an empty optional selector is invalid_argument, never "absent".
    err(h, name, args, "invalid_argument")


@pytest.mark.parametrize("name", TOOLS)
def test_non_object_arguments_rejected(h, name):
    resp = asyncio.run(h.app.call(name, ["not", "an", "object"]))
    check_response(name, resp)
    assert resp["error"]["code"] == "invalid_argument"


# ============================================================================ name resolution (PRD 13.6)

RESOLVE = [
    ("get_city", {"city": "city:10"}, "city:10"), ("get_city", {"city": "valmont"}, "city:10"),
    ("get_city", {"city": "VALMONT"}, "city:10"), ("get_city", {"city": "  Brindlewick "}, "city:11"),
    ("get_city", {"city": "Saint-Éloi"}, "city:12"), ("get_city", {"city": "saint-eloi"}, "city:12"),
    ("get_city", {"city": "SAINT-ELOI"}, "city:12"),
    ("get_shop", {"shop": "ÉPICERIE GÉNÉRALE"}, f"building:{bf.S_GS}"), ("get_shop", {"shop": "epicerie generale"}, f"building:{bf.S_GS}"),
    ("get_shop", {"shop": "magasin de construction"}, f"building:{bf.S_CS1}"),
    ("get_shop", {"shop": f"building:{bf.S_HW2}"}, f"building:{bf.S_HW2}"),
    ("get_region", {"region": "greyhollow"}, f"region:{bf.R_GRE}"), ("get_region", {"region": "Saint-Eloi"}, f"region:{bf.R_SEL}"),
    ("get_region", {"region": "Valmont"}, f"region:{bf.R_VAL}"),
]


@pytest.mark.parametrize("name,args,expected", RESOLVE)
def test_name_resolution_ids_and_display_names(h, name, args, expected):
    d = ok(h, name, args)["data"]
    assert d["identity"]["id"] == expected


NOT_FOUND = [
    ("get_city", {"city": "city:99"}), ("get_city", {"city": "city:abc"}), ("get_city", {"city": "Atlantis"}),
    ("get_city", {"city": f"region:{bf.R_VAL}"}), ("get_city", {"city": "Saint Eloi"}),  # hyphen is not whitespace
    ("get_city", {"city": "Vallmont"}),  # no fuzzy matching outside search (PRD 13.6)
    ("get_shop", {"shop": f"building:{bf.B_PF1}"}), ("get_shop", {"shop": "building:HardwareStore@1,1"}),
    ("get_shop", {"shop": "Quincailler"}), ("get_shop", {"shop": "city:10"}),
    ("find_shops", {"product": "Nothing"}), ("find_shops", {"product": "Paints"}),  # recipe name, not a product
    ("find_shops", {"product": "Paint", "from_building": "building:Nope@1,1"}), ("find_shops", {"product": "Paint", "city": "Atlantis"}),
    ("find_shops", {"product": "Paint", "region": "Atlantis"}), ("find_shops", {"product": "product:Paint", "city": f"region:{bf.R_VAL}"}),
    ("list_regions", {"owner": "Nobody Inc"}), ("list_regions", {"owner": "company:99"}), ("list_regions", {"resource": "Unobtainium"}),
    ("get_region", {"region": "region:00000000-0000-0000-0000-000000000000"}), ("get_region", {"region": "Atlantis"}),
    ("get_region", {"region": "city:10"}),
    ("get_market", {"products": ["Paint", "Nothing"]}), ("get_market", {"products": ["Paints"]}),
    ("get_tech_tree", {"tree": "NoTree"}), ("get_tech_tree", {"tree": "tech:Paints"}), ("get_tech_tree", {"company": "Nobody Inc"}),
    ("get_research_state", {"company": "company:99"}), ("get_research_state", {"company": "Valmont"}),
]


@pytest.mark.parametrize("name,args", NOT_FOUND)
def test_unknown_names_and_ids_are_not_found(h, name, args):
    err(h, name, args, "not_found")


def test_ambiguous_shop_name_lists_candidates_with_context(h):
    """PRD 13.3: ambiguous -> candidates (max 10) with type, owner and coordinates."""
    e = err(h, "get_shop", {"shop": "QUINCAILLERIE"}, "ambiguous")["error"]
    ids = {c["id"] for c in e["candidates"]}
    assert ids == {f"building:{bf.S_HW1}", f"building:{bf.S_HW2}"}
    for c in e["candidates"]:
        assert c["type"] == "building_type:HardwareStore" and c["owner"] is not None and c["coordinates"] is not None


def test_ambiguous_from_building_and_city(h):
    e = err(h, "find_shops", {"product": "Paint", "from_building": "USINE DE PEINTURE 1"}, "ambiguous")["error"]
    assert {c["id"] for c in e["candidates"]} == {f"building:{bf.B_PF1}", f"building:{bf.AI_PF}"}
    w = world_of(h)
    dup = copy.deepcopy(w["state"]["data"]["cities"][1])
    dup.update(city_id=13, name="VALMONT", shops=[], region_id=None, contract_offer=None)
    w["state"]["data"]["cities"].append(dup)
    restate(h, w)
    for name, args in (("get_city", {"city": "valmont"}), ("find_shops", {"product": "Paint", "city": "Valmont"})):
        e = err(h, name, args, "ambiguous")["error"]
        assert {c["id"] for c in e["candidates"]} == {"city:10", "city:13"}
    assert ok(h, "get_city", {"city": "city:13"})["data"]["identity"]["name"] == "VALMONT"


def test_ambiguous_candidates_are_capped_at_ten(h):
    w = world_of(h)
    shops = w["state"]["data"]["shops"]
    for i in range(15):
        s = copy.deepcopy(shops[0])
        s["building"] = f"HardwareStore@{600 + i},600"
        shops.append(s)
    restate(h, w)
    e = err(h, "get_shop", {"shop": "QUINCAILLERIE"}, "ambiguous")["error"]
    assert 2 <= len(e["candidates"]) <= 10


def test_vehicle_id_of_another_session_is_stale_reference(h):
    err(h, "get_shop", {"shop": f"vehicle:{bf.WORLD_SESSION_2}:-1203"}, "stale_reference")
    err(h, "find_shops", {"product": "Paint", "from_building": f"vehicle:{bf.WORLD_SESSION_2}:-1203"}, "stale_reference")


# ============================================================================ list_cities / get_city

def test_list_cities_rows(h):
    r = ok(h, "list_cities")
    rows = {c["id"]: c for c in r["data"]["cities"]}
    assert set(rows) == {"city:10", "city:11", "city:12"} and r["page"]["total"] == 3
    assert rows["city:10"]["shop_count"] == 2 and rows["city:11"]["shop_count"] == 1 and rows["city:12"]["shop_count"] == 1
    assert rows["city:12"]["name"] == "Saint-Éloi" and rows["city:10"]["tier"] == "Town"
    assert rows["city:10"]["region"]["id"] == f"region:{bf.R_VAL}"
    # D-GROW-1: observer state first; else WaitingForSponsor -> Bloated -> Prospering -> Growing -> Stagnating
    assert rows["city:10"]["growth_state"] == "Growing"
    assert rows["city:11"]["growth_state"] == "Stagnating"   # consumed 50 < growth 100 and < prosperity 300
    assert rows["city:12"]["growth_state"] == "WaitingForSponsor"


@pytest.mark.parametrize("sort,key", [("population", "population"), ("tier", "tier_id")])
def test_list_cities_sort_is_monotonic_and_deterministic(h, sort, key):
    w = world_of(h)
    cities = w["state"]["data"]["cities"]
    for i, (pop, tier) in enumerate([(52000, 1), (52000, 3), (0, 2), (999999999, 1)]):
        c = copy.deepcopy(cities[1])
        c.update(city_id=20 + i, name=f"Ville {i}", population=pop, tier_id=tier, shops=[], contract_offer=None)
        cities.append(c)
    restate(h, w)
    a = ok(h, "list_cities", {"sort": sort, "fields": "full"})["data"]["cities"]
    b = ok(h, "list_cities", {"sort": sort, "fields": "full"})["data"]["cities"]
    assert a == b
    if sort == "population":
        assert monotonic([c["population"] for c in a])
    else:
        # AMBIGUITY: PRD 14.7 names the sort keys but not the direction; only monotonic order is asserted.
        tiers = {c["city_id"]: c["tier_id"] for c in cities}
        assert monotonic([tiers[int(c["id"].split(":")[1])] for c in a])


def test_list_cities_full_is_a_superset_of_compact(h):
    compact = ok(h, "list_cities")["data"]["cities"]
    full = ok(h, "list_cities", {"fields": "full"})["data"]["cities"]
    assert [c["id"] for c in compact] == [c["id"] for c in full]
    for c, f in zip(compact, full):
        assert set(c) <= set(f)
        assert all(f[k] == v for k, v in c.items())


@pytest.mark.parametrize("growth,reached,expected", [
    ({"waiting_for_sponsor": False, "prospering": None, "growing": None, "consumed_products": 350, "growth_threshold": 100,
      "prosperity_threshold": 300, "state": None}, False, "Prospering"),
    ({"waiting_for_sponsor": False, "prospering": None, "growing": None, "consumed_products": 150, "growth_threshold": 100,
      "prosperity_threshold": 300, "state": None}, False, "Growing"),
    ({"waiting_for_sponsor": False, "prospering": True, "growing": True, "consumed_products": None, "growth_threshold": None,
      "prosperity_threshold": None, "state": None}, True, "Bloated"),
    ({"waiting_for_sponsor": True, "prospering": True, "growing": True, "consumed_products": None, "growth_threshold": None,
      "prosperity_threshold": None, "state": None}, True, "WaitingForSponsor"),
    ({"waiting_for_sponsor": False, "prospering": False, "growing": False, "consumed_products": 0, "growth_threshold": 100,
      "prosperity_threshold": 300, "state": None}, False, "Stagnating"),
])
def test_growth_state_d_grow_1_order(h, growth, reached, expected):
    w = world_of(h)
    c = w["state"]["data"]["cities"][1]
    c["growth"] = growth
    c["population_limit_reached"] = reached
    restate(h, w)
    assert ok(h, "get_city", {"city": "city:11"})["data"]["growth_state"]["value"] == expected
    rows = {x["id"]: x for x in ok(h, "list_cities")["data"]["cities"]}
    assert rows["city:11"]["growth_state"] == expected


def test_growth_state_without_inputs_is_null_not_guessed(h):
    w = world_of(h)
    w["state"]["data"]["cities"][1]["growth"] = None
    w["state"]["data"]["cities"][1]["population_limit_reached"] = None
    restate(h, w)
    g = ok(h, "get_city", {"city": "city:11"})["data"]["growth_state"]
    assert g["method"] == "D-GROW-1" and g["value"] is None


def test_get_city_content(h):
    d = ok(h, "get_city", {"city": "Valmont"})["data"]
    assert d["identity"] == {**d["identity"], "id": "city:10", "name": "Valmont"}
    assert d["population"] == 52000 and d["population_limit"] == 80000 and d["consumption_interval_days"] == 30
    assert d["tier"]["name"] == "Town" and d["tier"]["next_tier"] == "City"
    assert d["tier"]["next_tier_threshold"] == 80000  # static settlement tier Town threshold_max
    assert d["houses_count"] == 80 and d["dead"] is False
    assert d["demand_unit_note"].startswith("Shop demand is in units per consumption interval")
    shops = {s["id"]: s for s in d["shops"]}
    assert set(shops) == {f"building:{bf.S_HW1}", f"building:{bf.S_CS1}"}
    paint = {p["product"]: p for p in shops[f"building:{bf.S_HW1}"]["products"]}["product:Paint"]
    assert paint["demand_for_player"] == 10 and paint["stock"] == 4 and paint["price_for_player"] == 1450.5
    # D-SHOP-1: max(10 - 3, 0) per 30-day interval = 7; per 30 days = 7
    assert paint["unmet_demand"]["per_interval"] == 7 and paint["unmet_demand"]["per_30d"] == 7
    b = ok(h, "get_city", {"city": "Brindlewick"})["data"]
    assert b["contract_offer"]["product"] == "Paint"
    assert ok(h, "get_city", {"city": "city:12"})["data"]["contract_offer"] is None


def test_city_with_no_shops_and_no_region(h):
    w = world_of(h)
    c = copy.deepcopy(w["state"]["data"]["cities"][1])
    c.update(city_id=30, name="Hameau", shops=[], region_id=None, contract_offer=None, population=0, consumption_interval_days=None,
             tier="Hamlet", tier_id=None)
    w["state"]["data"]["cities"].append(c)
    restate(h, w)
    d = ok(h, "get_city", {"city": "Hameau"})["data"]
    assert d["shops"] == [] and d["region"] is None and d["population"] == 0
    assert d["tier"]["next_tier_threshold"] is None  # unknown tier: no static definition
    rows = {x["id"]: x for x in ok(h, "list_cities", {"sort": "tier"})["data"]["cities"]}
    assert rows["city:30"]["shop_count"] == 0 and rows["city:30"]["region"] is None


def test_dead_city_flagged(h):
    w = world_of(h)
    w["state"]["data"]["cities"][2]["dead"] = True
    w["state"]["data"]["shops"][3]["is_dead"] = True
    restate(h, w)
    assert ok(h, "get_city", {"city": "city:12"})["data"]["dead"] is True
    row = next(r for r in ok(h, "find_shops", {"product": "Paint"})["data"]["shops"] if r["shop"] == f"building:{bf.S_GS}")
    assert row["is_dead"] is True


def test_get_city_lists_shop_missing_from_shops_section(h):
    w = world_of(h)
    w["state"]["data"]["cities"][0]["shops"].append("HardwareStore@7,7")
    restate(h, w)
    d = ok(h, "get_city", {"city": "Valmont"})["data"]
    assert "building:HardwareStore@7,7" in [s["id"] for s in d["shops"]]


def test_get_city_with_many_shops_stays_under_size_cap(h):
    w = world_of(h)
    shops = w["state"]["data"]["shops"]
    city = w["state"]["data"]["cities"][0]
    for i in range(250):
        s = copy.deepcopy(shops[0])
        s["building"] = f"HardwareStore@{1000 + i},5"
        s["products"] = [copy.deepcopy(shops[0]["products"][0]) for _ in range(4)]
        shops.append(s)
        city["shops"].append(s["building"])
    restate(h, w)
    r = ok(h, "get_city", {"city": "Valmont"})  # check_response asserts <= 30 KB
    assert "truncated" in codes(r)


# ============================================================================ get_shop

def test_get_shop_content(h):
    d = ok(h, "get_shop", {"shop": bf.S_HW2})["data"]
    assert d["identity"]["id"] == f"building:{bf.S_HW2}" and d["identity"]["name"] == "QUINCAILLERIE"
    assert d["city"]["id"] == "city:11" and d["days_to_next_price_update"] == 9
    assert d["demand_unit_note"].startswith("Shop demand is in units per consumption interval")
    p = d["products"][0]
    assert p["product"] == "product:Paint" and p["demand_raw"] == 7 and p["demand_for_player"] == 6
    assert p["player_delivered_stock"] == 0 and p["sold_last_30d"] == 5 and p["price_modifier_pct"] == 3.0
    assert p["unmet_demand"]["per_interval"] == 6 and p["unmet_demand"]["per_30d"] == 12  # interval 15 d
    assert d["identity"]["owner"]["kind"] == "city"


def test_shop_selling_nothing_and_without_city(h):
    w = world_of(h)
    w["state"]["data"]["shops"].append({"building": "GeneralStore@500,500", "display_name": "BOUTIQUE VIDE", "prefab": "GeneralStore",
                                        "city_id": None, "owner_actor_id": 77, "is_dead": False, "days_to_next_price_update": None,
                                        "products": []})
    restate(h, w)
    d = ok(h, "get_shop", {"shop": "boutique vide"})["data"]
    assert d["products"] == [] and d["city"] is None and d["days_to_next_price_update"] is None
    rows = ok(h, "find_shops", {"product": "Paint", "limit": 50})["data"]["shops"]
    assert "building:GeneralStore@500,500" not in [r["shop"] for r in rows]


@pytest.mark.parametrize("demand,delivered,interval,per_interval,per_30d", [
    (0, 0, 30, 0, 0), (0, 5, 30, 0, 0), (5, 9, 15, 0, 0), (2147483647, 0, 30, 2147483647, 2147483647),
    (12, None, 10, 12, 36), (10, 3, None, 7, None),
])
def test_d_shop_1_unmet_demand_edges(h, demand, delivered, interval, per_interval, per_30d):
    """D-SHOP-1: max(demand_for_player - player_delivered_stock, 0) per interval; x 30 / interval."""
    w = world_of(h)
    sp = w["state"]["data"]["shops"][2]["products"][0]
    sp.update(demand_for_player=demand, player_delivered_stock=delivered)
    w["state"]["data"]["cities"][1]["consumption_interval_days"] = interval
    restate(h, w)
    p = ok(h, "get_shop", {"shop": bf.S_HW2})["data"]["products"][0]
    assert p["unmet_demand"]["method"] == "D-SHOP-1"
    assert p["unmet_demand"]["per_interval"] == per_interval
    assert p["unmet_demand"]["per_30d"] == per_30d
    row = next(r for r in ok(h, "find_shops", {"product": "Paint"})["data"]["shops"] if r["shop"] == f"building:{bf.S_HW2}")
    assert row["unmet_demand"]["per_interval"] == per_interval


def test_shop_prices_missing_and_negative_modifiers(h):
    w = world_of(h)
    sp = w["state"]["data"]["shops"][0]["products"][0]
    sp.update(price_for_player=None, price_modifier_pct=-50.0, shop_modifier=None, market_modifier=-0.5, sold_last_30d=None, slots=None)
    restate(h, w)
    p = {x["product"]: x for x in ok(h, "get_shop", {"shop": bf.S_HW1})["data"]["products"]}["product:Paint"]
    assert p["price_for_player"] is None and p["price_modifier_pct"] == -50.0 and p["sold_last_30d"] is None
    rows = ok(h, "find_shops", {"product": "Paint", "sort": "price"})["data"]["shops"]
    assert len(rows) == 4 and monotonic([r["price_for_player"] for r in rows])


# ============================================================================ find_shops

def _by_shop(rows):
    return {r["shop"]: r for r in rows}


def test_find_shops_with_from_building_route_vs_estimate(h):
    d = ok(h, "find_shops", {"product": "Paint", "from_building": bf.B_PF1})["data"]
    by = _by_shop(d["shops"])
    assert set(by) == {f"building:{s}" for s in (bf.S_HW1, bf.S_CS1, bf.S_HW2, bf.S_GS)}
    for row in d["shops"]:
        # PRD 14.7 / D-ROUTE-2: existing_route XOR straight_line_cost_estimate
        assert (row["existing_route"] is None) != (row["straight_line_cost_estimate"] is None)
        assert row["straight_line_tiles"]["estimate"] is True
    hw1 = by[f"building:{bf.S_HW1}"]["existing_route"]
    assert hw1["route_id"] == f"route:{bf.B_PF1}|Paint|{bf.S_HW1}|own|0"
    assert hw1["distance_tiles"] == 75 and hw1["dispatch_cost"] == 1250.0  # the game's own values
    # unreachable destination: the route exists (no path) -> still the authoritative route, never an estimate
    cs1 = by[f"building:{bf.S_CS1}"]
    assert cs1["existing_route"] is not None and cs1["existing_route"]["distance_tiles"] is None
    assert cs1["straight_line_cost_estimate"] is None
    for key, tiles in ((bf.S_HW2, 182.003), (bf.S_GS, 245.0)):
        est = by[f"building:{key}"]["straight_line_cost_estimate"]
        assert est["route_exists"] is False and est["authoritative"] is False and est["distance_kind"] == "straight_line"
        assert est["formula"] == "ManualDestinationDispatchCost"
        # (250 + distance * 10) * difficulty(1.25) * actor(1), Euclidean distance (D-DIST-1)
        assert est["value"] == pytest.approx((250 + tiles * 10) * 1.25, abs=0.02)
        assert by[f"building:{key}"]["straight_line_tiles"]["euclidean"] == pytest.approx(tiles, abs=0.001)
    assert d["product"] == "product:Paint" and d["from_building"] == f"building:{bf.B_PF1}"


def test_find_shops_without_from_building_has_no_distances(h):
    d = ok(h, "find_shops", {"product": "Paint"})["data"]
    for row in d["shops"]:
        assert row["straight_line_tiles"] is None and row["existing_route"] is None and row["straight_line_cost_estimate"] is None
    assert d["unavailable"], "distances requested implicitly must be explained in unavailable"


@pytest.mark.parametrize("origin", [bf.B_HQ, bf.B_WH, bf.B_GW1, bf.AI_PF, bf.S_GS, "TradingPost@5,5"])
def test_find_shops_from_non_producers_and_foreign_buildings(h, origin):
    """from_building need not produce the product (PRD 14.7 only asks for an id)."""
    rows = ok(h, "find_shops", {"product": "Paint", "from_building": origin})["data"]["shops"]
    assert len(rows) == 4
    for row in rows:
        assert row["straight_line_tiles"] is not None
        assert (row["existing_route"] is None) != (row["straight_line_cost_estimate"] is None)


def test_find_shops_same_location_distance_zero(h):
    row = _by_shop(ok(h, "find_shops", {"product": "Paint", "from_building": bf.S_GS})["data"]["shops"])[f"building:{bf.S_GS}"]
    assert row["straight_line_tiles"]["euclidean"] == 0 and row["straight_line_tiles"]["chebyshev"] == 0
    assert row["straight_line_cost_estimate"]["value"] == pytest.approx(250 * 1.25)


def test_find_shops_route_for_other_product_to_same_shop(h):
    """AMBIGUITY: PRD 14.7 says "a configured route to the shop" without naming the product. Invariant only:
    never both, and an existing_route always carries the game's values."""
    rows = ok(h, "find_shops", {"product": "Chemicals", "from_building": bf.B_PF1})["data"]["shops"]
    row = _by_shop(rows)[f"building:{bf.S_HW1}"]
    assert (row["existing_route"] is None) != (row["straight_line_cost_estimate"] is None)
    if row["existing_route"] is not None:
        assert row["existing_route"]["distance_tiles"] == 75


@pytest.mark.parametrize("args,expected", [
    ({"city": "Valmont"}, {bf.S_HW1, bf.S_CS1}), ({"city": "city:11"}, {bf.S_HW2}),
    ({"region": "Saint-Eloi"}, {bf.S_GS}), ({"region": "Greyhollow"}, set()),
    ({"city": "Valmont", "region": "Brindlewick"}, set()), ({"city": "Brindlewick", "region": f"region:{bf.R_BRI}"}, {bf.S_HW2}),
])
def test_find_shops_city_region_filters(h, args, expected):
    r = ok(h, "find_shops", {"product": "Paint", **args})
    assert {x["shop"] for x in r["data"]["shops"]} == {f"building:{s}" for s in expected}
    assert r["page"]["total"] == len(expected)


def test_find_shops_product_without_shops(h):
    r = ok(h, "find_shops", {"product": "Gas", "from_building": bf.B_GW1})
    assert r["data"]["shops"] == [] and r["page"]["total"] == 0 and r["page"]["next_cursor"] is None


@pytest.mark.parametrize("sort,key", [
    ("demand", lambda r: r["demand_for_player"]), ("price", lambda r: r["price_for_player"]),
    ("distance", lambda r: r["straight_line_tiles"]["euclidean"]), ("unmet_demand", lambda r: r["unmet_demand"]["per_30d"]),
])
def test_find_shops_sorts(h, sort, key):
    # AMBIGUITY: PRD 14.7 does not state sort directions; monotonic order + determinism only.
    a = ok(h, "find_shops", {"product": "Paint", "from_building": bf.B_PF1, "sort": sort})["data"]["shops"]
    assert monotonic([key(r) for r in a])
    assert a == ok(h, "find_shops", {"product": "Paint", "from_building": bf.B_PF1, "sort": sort})["data"]["shops"]


def test_find_shops_distance_sort_without_from_building(h):
    rows = ok(h, "find_shops", {"product": "Paint", "sort": "distance"})["data"]["shops"]
    assert len(rows) == 4 and all(r["straight_line_tiles"] is None for r in rows)


def test_find_shops_estimate_without_formula(h):
    w = world_of(h)
    w["static"]["data"]["formulas"] = [f for f in w["static"]["data"]["formulas"] if f["name"] != "ManualDestinationDispatchCost"]
    restatic(h, w)
    d = ok(h, "find_shops", {"product": "Paint", "from_building": bf.B_PF1})["data"]
    est = _by_shop(d["shops"])[f"building:{bf.S_HW2}"]["straight_line_cost_estimate"]
    assert est["value"] is None and est["authoritative"] is False and est["route_exists"] is False
    assert any("straight_line_cost_estimate" in f for f in unavailable_fields({"data": d}))
    assert _by_shop(d["shops"])[f"building:{bf.S_HW1}"]["existing_route"]["dispatch_cost"] == 1250.0


# Regression test for DEFECT WM-2 (fixed).
def test_find_shops_never_claims_no_route_when_routes_are_unknown(h):
    """D-ROUTE-2 / PRD 14.7: with routes_player unavailable the server cannot know which routes exist (PF1->HW1
    and PF1->CS1 do). Route status is `unavailable`; an estimate, if shown, never claims route_exists=false."""
    w = world_of(h)
    restate(h, w, failed=("routes_player",))
    r = ok(h, "find_shops", {"product": "Paint", "from_building": bf.B_PF1})
    for row in r["data"]["shops"]:
        assert row["existing_route_status"] == "unavailable" and row["existing_route"] is None
        est = row["straight_line_cost_estimate"]
        assert est is None or (est["route_exists"] is None and est["authoritative"] is False), row


def test_find_shops_routes_unavailable_is_reported(h):
    """Invariant part of WM-2 that holds today: the missing section is named."""
    w = world_of(h)
    restate(h, w, failed=("routes_player",))
    r = ok(h, "find_shops", {"product": "Paint", "from_building": bf.B_PF1})
    assert "routes_player" in r["meta"]["snapshot"]["sections_unavailable"]


def _many_shops(h, n):
    w = world_of(h)
    shops = w["state"]["data"]["shops"]
    for i in range(n):
        s = copy.deepcopy(shops[0])
        s["building"] = f"HardwareStore@{400 + i},{400 + (i * 7) % 50}"
        s["display_name"] = f"QUINCAILLERIE {i}"
        s["products"][0]["demand_for_player"] = i % 13
        shops.append(s)
    restate(h, w)


def test_find_shops_large_result_set_pagination_with_truncation(h):
    _many_shops(h, 120)
    first = ok(h, "find_shops", {"product": "Paint"})
    assert len(first["data"]["shops"]) == 25 and first["page"]["total"] == 124 and first["page"]["next_cursor"]
    rows, pages, last = walk_pages(h, "find_shops", {"product": "Paint", "from_building": bf.B_PF1}, "shops", 50)
    ids = [r["shop"] for r in rows]
    assert len(ids) == 124 and len(set(ids)) == 124 and last["page"]["total"] == 124
    reference, _, _ = walk_pages(h, "find_shops", {"product": "Paint", "from_building": bf.B_PF1}, "shops", 7)
    assert [r["shop"] for r in reference] == ids  # page size never changes the order


# ============================================================================ list_regions / get_region

@pytest.mark.parametrize("args,expected", [
    ({}, {bf.R_VAL, bf.R_BRI, bf.R_SEL, bf.R_GRE, bf.R_NOR}),
    ({"owner": "player"}, {bf.R_VAL, bf.R_GRE}), ({"owner": "PLAYER"}, {bf.R_VAL, bf.R_GRE}),
    ({"owner": "company:1"}, {bf.R_VAL, bf.R_GRE}), ({"owner": "Acme Industries"}, {bf.R_VAL, bf.R_GRE}),
    ({"owner": "ai"}, {bf.R_BRI, bf.R_SEL}), ({"owner": "company:2"}, {bf.R_BRI}), ({"owner": "cobalt works"}, {bf.R_SEL}),
    ({"owner": "unowned"}, {bf.R_NOR}), ({"resource": "Gas"}, {bf.R_BRI, bf.R_GRE, bf.R_NOR}),
    ({"resource": "Water"}, {bf.R_VAL, bf.R_GRE}), ({"resource": "Flowers"}, set()),
    ({"owner": "player", "resource": "Gas"}, {bf.R_GRE}), ({"owner": "unowned", "resource": "Water"}, set()),
])
def test_list_regions_filters(h, args, expected):
    r = ok(h, "list_regions", args)
    assert {x["id"] for x in r["data"]["regions"]} == {f"region:{g}" for g in expected}
    assert r["page"]["total"] == len(expected)


def test_list_regions_rows_and_permit_cost(h):
    rows = {r["id"]: r for r in ok(h, "list_regions", {"fields": "full"})["data"]["regions"]}
    val = rows[f"region:{bf.R_VAL}"]
    assert val["owner"]["id"] == "company:1" and val["city"]["id"] == "city:10"
    assert val["permit_cost"] == {**val["permit_cost"], "value": 1500000, "method": "D-PERMIT-1"}
    assert val["resources"] == [{"product": "product:Water", "tiles": None, "water_unlimited": True}]
    nor = rows[f"region:{bf.R_NOR}"]
    assert nor["owner"] is None and nor["city"] is None
    # D-PERMIT-1 on the server: round(1600 tiles x 1000 (top-level Region) x 1.0 x 0.75 (no city))
    assert nor["permit_cost"]["value"] == 1200000 and nor["permit_cost"]["method"] == "D-PERMIT-1"
    compact = {r["id"]: r for r in ok(h, "list_regions")["data"]["regions"]}
    for rid, c in compact.items():
        assert set(c) <= set(rows[rid])


def test_region_without_permit_record(h):
    w = world_of(h)
    reg = w["state"]["data"]["regions"][4]
    reg["permit"] = None
    restate(h, w)
    ids = {r["id"] for r in ok(h, "list_regions", {"owner": "unowned"})["data"]["regions"]}
    assert f"region:{bf.R_NOR}" in ids
    d = ok(h, "get_region", {"region": "Northmarch"})["data"]
    assert d["permit"]["owner"] is None and d["permit"]["cost"]["method"] == "D-PERMIT-1"


def test_get_region_content(h):
    d = ok(h, "get_region", {"region": "Greyhollow"})["data"]
    assert d["identity"]["id"] == f"region:{bf.R_GRE}" and d["identity"]["tile_count"] == 1600
    assert d["identity"]["center"] == {"x": 30, "y": 30} and d["city"] is None
    assert d["permit"]["owner"]["id"] == "company:1" and d["permit"]["amount_paid"] == 1200000.0
    res = {r["product"]: r for r in d["resources"]}
    assert res["product:Gas"]["tile_count"] == 48 and res["product:Gas"]["water_unlimited"] is False
    assert res["product:Water"]["water_unlimited"] is True
    assert len(d["resource_sites"]) == 1 and d["resource_sites"][0]["product"] == "product:Gas"
    assert d["building_counts"]["player"] == 7 and d["building_counts"]["ai"] == []
    b = ok(h, "get_region", {"region": "Brindlewick"})["data"]["building_counts"]
    assert b["player"] == 0 and [(x["owner"]["id"], x["count"]) for x in b["ai"]] == [("company:2", 4)]


# Regression test for DEFECT WM-1 (fixed).
def test_get_region_ai_counts_with_ai_building_detail(h):
    """PRD 14.7: get_region reports player/AI building counts in the region. Enabling the optional
    buildings_ai_detail section (PRD 12.2) must not change them."""
    w = world_of(h)
    det = copy.deepcopy(next(b for b in w["state"]["data"]["buildings_player"] if b["key"] == bf.B_PF1))
    det.update(key=bf.AI_PF, owner_actor_id=bf.AI_B, region_id=bf.R_BRI, city_id=bf.CITY_BRI, x=150, y=120)
    w["state"]["data"]["buildings_ai_detail"] = [det]
    restate(h, w)
    b = ok(h, "get_region", {"region": "Brindlewick"})["data"]["building_counts"]
    assert b["player"] == 0
    assert [(x["owner"]["id"], x["count"]) for x in b["ai"]] == [("company:2", 4)]


def test_large_region_and_city_sets_paginate(h):
    w = world_of(h)
    regs, cities = w["state"]["data"]["regions"], w["state"]["data"]["cities"]
    for i in range(70):
        r = copy.deepcopy(regs[4])
        r.update(region_id=f"b2000000-0000-4000-8000-{i:012d}", name=f"Région {i:02d}", permit_cost=None)
        regs.append(r)
        c = copy.deepcopy(cities[1])
        c.update(city_id=100 + i, name=f"Ville {i:02d}", population=1000 + (i * 37) % 500, shops=[], contract_offer=None)
        cities.append(c)
    restate(h, w)
    for name, key, total in (("list_regions", "regions", 75), ("list_cities", "cities", 73)):
        first = ok(h, name)
        assert len(first["data"][key]) == 25 and first["page"]["total"] == total
        rows, pages, _ = walk_pages(h, name, {"fields": "full"}, key, 50)
        assert pages == 2 and len({r["id"] for r in rows}) == total
        h.restart()
        again, _, _ = walk_pages(h, name, {"fields": "full"}, key, 50)
        assert again == rows


# ============================================================================ get_market

def test_market_content(h):
    d = ok(h, "get_market")["data"]
    prices = {p["product"]: p for p in d["prices"]}
    assert set(prices) == {f"product:{p}" for p in ("Gas", "Water", "Flowers", "Chemicals", "Dye", "Paint")}
    assert prices["product:Paint"] == {"product": "product:Paint", "value": 1400.0, "price": 1450.0, "modifier": 0.03,
                                       "trend": "GOING_UP", "final_price_for_player": 1455.0}
    assert d["price_history"] == {"available": False, "reason": "not_retained_by_game"}
    assert d["next_update_in_days"]["value"] == 9  # interval 15 - 6 days since the last update
    assert {s["product"] for s in d["state"]["sold_products"]} == {"product:Water", "product:Gas"}
    assert d["state"]["incoming_trade_allowed"] is True
    assert len(d["contracts"]["player_active"]) == 1 and len(d["contracts"]["city_offers"]) == 1
    assert d["auctions"]["current"]["region"]["id"] == f"region:{bf.R_NOR}" and d["auctions"]["queue"] == []


@pytest.mark.parametrize("args", [
    {}, {"products": ["Paint"]}, {"include": ["state"]}, {"include": ["auctions"]}, {"include": ["contracts"], "products": ["Gas"]},
    {"allow_stale": True}, {"include": ["state", "contracts", "auctions"]},
])
def test_price_history_is_always_unavailable(h, args):
    """PRD 12.4 / KNOWN-LIMITATIONS: market price history does not exist."""
    assert ok(h, "get_market", args)["data"]["price_history"] == {"available": False, "reason": "not_retained_by_game"}


@pytest.mark.parametrize("include", [["state"], ["contracts"], ["auctions"], ["state", "auctions"]])
def test_market_include_subsets(h, include):
    d = ok(h, "get_market", {"include": include})["data"]
    for part in ("state", "contracts", "auctions"):
        assert (part in d) == (part in include), part
    assert d["prices"]


def test_market_include_empty_list(h):
    # AMBIGUITY: PRD 14.8 does not say whether include=[] means "nothing" or "default". Invariant only.
    d = ok(h, "get_market", {"include": []})["data"]
    assert d["prices"] and d["price_history"]["available"] is False


def test_market_products_filter(h):
    d = ok(h, "get_market", {"products": ["Gaz", "Peinture"]})["data"]
    assert [p["product"] for p in d["prices"]] == sorted(["product:Gas", "product:Paint"])
    assert [s["product"] for s in d["state"]["sold_products"]] == ["product:Gas"]
    assert all(c["product"] == "product:Paint" for c in d["contracts"]["player_active"] + d["contracts"]["city_offers"])
    d2 = ok(h, "get_market", {"products": ["Water"]})["data"]
    assert d2["contracts"] == {"player_active": [], "city_offers": []}


def test_market_edge_values(h):
    w = world_of(h)
    m = w["state"]["data"]["market"]
    m["prices"][0].update(modifier=0.0, final_price_for_player=None)
    m["prices"][1].update(modifier=-0.99, price=0.4)
    m["state"]["sold"] = []
    m["city_contract_offers"] = []
    m["auctions"] = {"current": None, "queue": []}
    w["state"]["data"]["companies"][0]["contracts"] = None
    restate(h, w)
    d = ok(h, "get_market")["data"]
    prices = {p["product"]: p for p in d["prices"]}
    assert prices["product:Gas"]["modifier"] == 0.0 and prices["product:Gas"]["final_price_for_player"] is None
    assert prices["product:Water"]["modifier"] == -0.99
    assert d["state"]["sold_products"] == [] and d["contracts"] == {"player_active": [], "city_offers": []}
    assert d["auctions"] == {"current": None, "queue": []}


def test_market_auctions_missing_and_update_day_unreadable(h):
    w = world_of(h)
    w["state"]["data"]["market"]["auctions"] = None
    w["state"]["data"]["session"]["market_days_since_update"] = None
    restate(h, w)
    d = ok(h, "get_market")["data"]
    assert d["auctions"] is None and "auctions" in unavailable_fields({"data": d})
    # PRD 14.8: next_update_in_days only "if readable, else omitted"
    assert "next_update_in_days" not in d


def test_market_update_overdue_never_negative(h):
    w = world_of(h)
    w["state"]["data"]["session"]["market_days_since_update"] = 40
    restate(h, w)
    assert ok(h, "get_market")["data"]["next_update_in_days"]["value"] >= 0


def test_market_empty_price_list(h):
    w = world_of(h)
    w["state"]["data"]["market"]["prices"] = []
    restate(h, w)
    d = ok(h, "get_market", {"products": ["Paint"]})["data"]
    assert d["prices"] == [] and d["price_history"]["available"] is False


# ============================================================================ get_tech_tree

EXPECTED_STATES = {
    "unlocked": {"BasicGas", "BasicFarming", "Petrochemistry", "Dyes", "Paints"},
    "researching": {"AdvancedPaints"}, "queued": {"Railways"}, "teaser": {"FutureTech"},
    "available": {"Plastics", "PaintDiscount"},  # every prerequisite unlocked, not yet researched
    "locked": {"Polymers"},
}


@pytest.mark.parametrize("state", list(EXPECTED_STATES))
def test_tech_tree_state_filter(h, state):
    r = ok(h, "get_tech_tree", {"state": state, "limit": 50})
    names = {n["id"].split(":", 1)[1] for n in r["data"]["nodes"]}
    assert names == EXPECTED_STATES[state]
    assert all(n["state"] == state for n in r["data"]["nodes"]) and r["page"]["total"] == len(names)


def test_tech_tree_nodes_and_costs(h):
    r = ok(h, "get_tech_tree", {"limit": 50})
    nodes = {n["id"]: n for n in r["data"]["nodes"]}
    assert set(nodes) == {f"tech:{t}" for t in ALL_TECH}
    assert {t["id"] for t in r["data"]["trees"]} == {"tech_tree:Chemistry", "tech_tree:Logistics"}
    paints = nodes["tech:Paints"]
    assert paints["prerequisites"] == ["tech:Petrochemistry", "tech:Dyes"] and paints["tier"] == 2
    assert paints["unlocks"]["buildings"] == ["building_type:PaintFactory"] and paints["unlocks"]["recipes"] == ["recipe:Paints"]
    disc = nodes["tech:PaintDiscount"]["unlocks"]
    assert disc["buildings"] == [] and disc["price_discounts"][0]["building_types"] == ["building_type:PaintFactory"]
    assert nodes["tech:AdvancedPaints"]["progress"] == 0.4
    # PRD 14.8: research cost/days computed for queued, active and available nodes only
    with_cost = {i for i, n in nodes.items() if n["research_cost_per_day"] is not None or n["research_days"] is not None}
    assert with_cost == {"tech:AdvancedPaints", "tech:Railways", "tech:Plastics", "tech:PaintDiscount"}
    assert nodes["tech:Railways"]["research_days"] == 540.0
    for i, n in nodes.items():
        if n["state"] in ("unlocked", "locked", "teaser"):
            assert n["research_cost_per_day"] is None and n["research_days"] is None, i


@pytest.mark.parametrize("tree,expected", [
    ("Logistics", {"Railways"}), ("Logistique", {"Railways"}), ("tech_tree:Chemistry", ALL_TECH - {"Railways"}),
])
def test_tech_tree_tree_filter(h, tree, expected):
    r = ok(h, "get_tech_tree", {"tree": tree, "limit": 50})
    assert {n["id"].split(":", 1)[1] for n in r["data"]["nodes"]} == expected
    assert len(r["data"]["trees"]) == 1


def test_tech_tree_for_ai_company(h):
    r = ok(h, "get_tech_tree", {"company": "Borealis Corp", "limit": 50})
    nodes = {n["id"]: n for n in r["data"]["nodes"]}
    assert r["data"]["company"] == "company:2"
    unlocked = {i.split(":", 1)[1] for i, n in nodes.items() if n["state"] == "unlocked"}
    assert unlocked == {"BasicGas", "Petrochemistry", "Paints", "BasicFarming"}  # AI set + default unlocks
    assert all(n["state"] not in ("researching", "queued") for n in nodes.values())
    assert nodes["tech:Dyes"]["state"] == "available"
    assert all(n["research_cost_per_day"] is None and n["progress"] is None for n in nodes.values())
    assert unavailable_fields(r)


def test_tech_tree_node_without_cost_formula(h):
    """The observer skips cost nodes without a formula (null researchCost): no cost, state unchanged."""
    w = world_of(h)
    for u in w["static"]["data"]["tech_unlocks"]:
        if u["name"] == "Plastics":
            u["research_cost_formula"] = None
            u["research_time_formula"] = None
    restatic(h, w)
    w["state"]["data"]["research"]["player"]["costs"] = [
        c for c in w["state"]["data"]["research"]["player"]["costs"] if c["unlock"] != "Plastics"]
    restate(h, w)
    n = {x["id"]: x for x in ok(h, "get_tech_tree", {"limit": 50})["data"]["nodes"]}["tech:Plastics"]
    assert n["state"] == "available" and n["research_cost_per_day"] is None and n["research_days"] is None


def test_tech_tree_prerequisite_cycles_terminate_as_locked(h):
    w = world_of(h)
    unlocks = w["static"]["data"]["tech_unlocks"]
    tmpl = copy.deepcopy(unlocks[7])
    for name, req in (("CycleA", ["CycleB"]), ("CycleB", ["CycleA"]), ("Ouroboros", ["Ouroboros"]), ("Ghost", ["DoesNotExist"])):
        u = copy.deepcopy(tmpl)
        u.update(name=name, display_name=name, english_name=name, required=req)
        unlocks.append(u)
    restatic(h, w)
    nodes = {x["id"]: x for x in ok(h, "get_tech_tree", {"limit": 50})["data"]["nodes"]}
    for t in ("CycleA", "CycleB", "Ouroboros", "Ghost"):
        assert nodes[f"tech:{t}"]["state"] == "locked"
    assert ok(h, "get_research_state")["data"]["total_nodes"] == len(ALL_TECH) + 4


@pytest.mark.parametrize("eff_unlocks", [[None, None, None, None, None], [], ["A"], [None, "X", None, "Y", None, "Z", None]])
def test_tech_config_efficiency_unlocks_with_nulls(h, eff_unlocks):
    w = world_of(h)
    w["static"]["data"]["tech_config"]["efficiency_unlocks"] = eff_unlocks
    restatic(h, w)
    ok(h, "get_tech_tree")
    d = ok(h, "get_research_state")["data"]
    assert d["efficiency"] == {"index": 2, "value": 1.0}


def test_tech_tree_static_only_when_not_live(h):
    """PRD 13.7: static-plus-live; definitions follow the static catalogue rules, live part in unavailable."""
    h.procs.procs = []
    r = ok(h, "get_tech_tree", {"limit": 50})
    assert r["meta"]["source"] == "static_catalog" and "catalog_from_previous_session" in codes(r)
    assert len(r["data"]["nodes"]) == len(ALL_TECH) and all(n["state"] is None for n in r["data"]["nodes"])
    assert unavailable_fields(r)
    # AMBIGUITY: with no live state a state filter cannot be evaluated; invariant only (no error, valid envelope).
    ok(h, "get_tech_tree", {"state": "unlocked"})
    s = ok(h, "get_tech_tree", {"allow_stale": True, "state": "unlocked", "limit": 50})
    assert s["meta"]["stale"] is True and s["meta"]["source"] == "stale_snapshot"
    assert {n["id"].split(":", 1)[1] for n in s["data"]["nodes"]} == EXPECTED_STATES["unlocked"]


def test_tech_tree_without_static_is_snapshot_unavailable(h):
    (h.exchange / "static.json").unlink()
    h.restart()
    err(h, "get_tech_tree", {}, "snapshot_unavailable")
    # runtime tools of this group keep answering from state (definitions listed as unavailable)
    r = ok(h, "get_city", {"city": "city:10"})
    assert "definition" in unavailable_fields(r)


def test_tech_tree_large_tree_paginates(h):
    w = world_of(h)
    unlocks = w["static"]["data"]["tech_unlocks"]
    for i in range(140):
        u = copy.deepcopy(unlocks[8])
        u.update(name=f"Node{i:03d}", display_name=f"Noeud {i}", english_name=f"Node {i}", tier=1 + i % 5, required=["Plastics"])
        unlocks.append(u)
    restatic(h, w)
    first = ok(h, "get_tech_tree")
    assert len(first["data"]["nodes"]) == 25 and first["page"]["total"] == 151
    rows, pages, _ = walk_pages(h, "get_tech_tree", {}, "nodes", 50)
    assert len({n["id"] for n in rows}) == 151
    locked = ok(h, "get_tech_tree", {"state": "locked", "limit": 50})
    assert locked["page"]["total"] == 141
    bad = call(h, "get_tech_tree", {"state": "unlocked", "cursor": first["page"]["next_cursor"]})
    # AMBIGUITY: cursors are opaque (PRD 13.2); reuse with other filters is not specified. Invariant only.
    assert bad["ok"] or bad["error"]["code"] == "invalid_argument"


# ============================================================================ get_research_state

def test_research_state_player(h):
    d = ok(h, "get_research_state")["data"]
    assert d["company"] == "company:1" and d["active"] == "tech:AdvancedPaints" and d["queue"] == ["tech:Railways"]
    assert d["progress"] == 0.4 and d["remaining_days"] == 324.0 and d["remaining_cost"] == 1080000.0
    assert d["efficiency"] == {"index": 2, "value": 1.0} and d["unlock_points"] == 3
    assert d["unlocked_count"] == 5 and d["total_nodes"] == len(ALL_TECH)


def test_research_state_idle(h):
    w = world_of(h)
    w["state"]["data"]["research"]["player"].update(active=None, queue=[], progress=[], costs=[], active_progress=0.0,
                                                    remaining_days=0.0, remaining_cost=0.0, unlock_points=0)
    restate(h, w)
    d = ok(h, "get_research_state")["data"]
    assert d["active"] is None and d["queue"] == [] and d["unlock_points"] == 0
    nodes = ok(h, "get_tech_tree", {"limit": 50})["data"]["nodes"]
    assert not [n for n in nodes if n["state"] in ("researching", "queued")]
    assert all(n["research_cost_per_day"] is None for n in nodes)
    assert {n["id"] for n in nodes if n["state"] == "available"} == {"tech:AdvancedPaints", "tech:Railways", "tech:Plastics",
                                                                    "tech:PaintDiscount"}


def test_research_non_finite_values_exported_as_null(h):
    """Observer Finite() -> null for non-finite costs; huge finite values pass through."""
    w = world_of(h)
    p = w["state"]["data"]["research"]["player"]
    for c in p["costs"]:
        c["total_cost_at_efficiency_1"] = None
    p["costs"][0]["daily_cost"] = 1e300
    p["remaining_days"] = 1e308
    restate(h, w)
    assert ok(h, "get_research_state")["data"]["remaining_days"] == 1e308
    n = {x["id"]: x for x in ok(h, "get_tech_tree", {"limit": 50})["data"]["nodes"]}
    assert n["tech:AdvancedPaints"]["research_cost_per_day"] == 1e300


@pytest.mark.parametrize("company,count,unlocked", [("Borealis Corp", 3, {"BasicGas", "Petrochemistry", "Paints"}),
                                                    ("company:3", 2, {"BasicFarming", "Dyes"})])
def test_research_state_ai(h, company, count, unlocked):
    r = ok(h, "get_research_state", {"company": company})
    d = r["data"]
    assert d["unlocked_count"] == count and {u.split(":", 1)[1] for u in d["unlocked"]} == unlocked
    for f in ("active", "queue", "progress", "remaining_days", "remaining_cost", "efficiency", "unlock_points"):
        assert d[f] is None
        assert f in unavailable_fields(r)


def test_research_state_ai_without_export(h):
    # AMBIGUITY: PRD 13.3 has no code for "entity known, data not exported"; invariant only.
    w = world_of(h)
    w["state"]["data"]["research"]["ai"] = []
    restate(h, w)
    r = call(h, "get_research_state", {"company": "Borealis Corp"})
    assert r["ok"] or r["error"]["code"] in ("not_found", "section_unavailable")
    call(h, "get_tech_tree", {"company": "Borealis Corp"})


# ============================================================================ sections disabled / failed

SECTION_CASES = [
    # (tool, args, section, required?)  required -> section_unavailable; optional -> ok + named in unavailable
    ("list_cities", {}, "cities", True), ("get_city", {"city": "city:10"}, "cities", True),
    ("get_city", {"city": "city:10"}, "shops", False), ("get_shop", {"shop": bf.S_HW1}, "shops", True),
    ("get_shop", {"shop": bf.S_HW1}, "cities", False), ("find_shops", {"product": "Paint"}, "shops", True),
    ("find_shops", {"product": "Paint", "from_building": bf.B_PF1}, "routes_player", False),
    ("find_shops", {"product": "Paint"}, "cities", False), ("list_regions", {}, "regions", True),
    ("get_region", {"region": f"region:{bf.R_GRE}"}, "regions", True),
    ("get_region", {"region": f"region:{bf.R_GRE}"}, "buildings_player", False),
    ("get_region", {"region": f"region:{bf.R_GRE}"}, "buildings_ai", False),
    ("get_market", {}, "market", True), ("get_market", {}, "companies", False),
    ("get_research_state", {}, "research", True), ("get_tech_tree", {}, "research", False),
]


@pytest.mark.parametrize("status", ["failed", "disabled"])
@pytest.mark.parametrize("name,args,section,required", SECTION_CASES)
def test_section_unavailable_matrix(h, name, args, section, required, status):
    """PRD 13.3: section_unavailable for a required section that is disabled or failed; PRD 13.5:
    otherwise the gap is listed in `unavailable`; PRD 16: affected tools only."""
    w = world_of(h)
    restate(h, w, failed=(section,), status=status)
    r = call(h, name, args)
    if required:
        assert r["ok"] is False and r["error"]["code"] == "section_unavailable"
        assert section in r["error"]["message"]
    else:
        assert r["ok"], r.get("error")
        assert section in unavailable_fields(r) and section in r["meta"]["snapshot"]["sections_unavailable"]


@pytest.mark.parametrize("name,args", [(n, VALID_CALLS[n]) for n in TOOLS if n != "get_market"])
def test_unrelated_section_failure_does_not_affect_tool(h, name, args):
    """PRD 16: section failures affect the affected tools only (vehicles is used by none of these)."""
    w = world_of(h)
    restate(h, w, failed=("vehicles",))
    r = ok(h, name, args)
    assert "vehicles" not in unavailable_fields(r)


# Regression test for DEFECT WM-3 (fixed).
@pytest.mark.parametrize("name,args,section,ref", [
    ("list_cities", {}, "regions", lambda d: [c["region"] for c in d["cities"]]),
    ("get_city", {"city": "city:10"}, "regions", lambda d: [d["region"]]),
    ("list_regions", {}, "cities", lambda d: [r["city"] for r in d["regions"]]),
    ("get_region", {"region": f"region:{bf.R_VAL}"}, "cities", lambda d: [d["city"]]),
])
def test_cross_section_names_never_silently_null(h, name, args, section, ref):
    w = world_of(h)
    restate(h, w, failed=(section,))
    r = ok(h, name, args)
    refs = [x for x in ref(r["data"]) if x is not None]
    assert refs
    named = all(x.get("name") is not None for x in refs)
    assert named or any(section in f for f in unavailable_fields(r)), (refs, r["data"]["unavailable"])


def test_city_filter_when_cities_section_failed(h):
    # AMBIGUITY: with `cities` failed a city filter cannot be resolved; PRD 13.3 offers not_found or
    # section_unavailable. Invariant: an error envelope, never internal_error or a silent unfiltered list.
    w = world_of(h)
    restate(h, w, failed=("cities",))
    r = call(h, "find_shops", {"product": "Paint", "city": "city:10"})
    assert r["ok"] is False and r["error"]["code"] in ("not_found", "section_unavailable")


# ============================================================================ lifecycle and staleness (PRD 11.7, 13.4, 16)

def _set_state(h, state):
    obs = FakeObserver(h.exchange, h.clock, h.procs)
    obs.verified = True
    if state == "game_not_running":
        h.procs.procs = []
    elif state == "unsupported_build":
        obs.unsupported_build()
    elif state == "observer_unresponsive":
        h.clock.advance(10)
    elif state == "observer_not_detected":
        h.procs.procs = [game_proc(pid=555, start=h.clock.now() - timedelta(minutes=5))]
    else:
        obs.heartbeat(state)
    return obs


LIFECYCLE = [("menu", "at_main_menu"), ("loading", "loading"), ("disabled", "observer_disabled"), ("faulted", "observer_faulted"),
             ("game_not_running", "game_not_running"), ("unsupported_build", "unsupported_build"),
             ("observer_unresponsive", "observer_unresponsive"), ("observer_not_detected", "observer_not_detected")]


@pytest.mark.parametrize("state,code", LIFECYCLE)
@pytest.mark.parametrize("name", RUNTIME_TOOLS)
def test_runtime_tools_lifecycle_errors(h, name, state, code):
    _set_state(h, state)
    r = err(h, name, VALID_CALLS[name], code)
    assert r["meta"]["source"] == "none"


@pytest.mark.parametrize("state,code", LIFECYCLE)
def test_tech_tree_lifecycle(h, state, code):
    _set_state(h, state)
    r = call(h, "get_tech_tree", {})
    if state == "unsupported_build":
        assert r["ok"] is False and r["error"]["code"] == "unsupported_build"  # PRD 13.4 exception / PRD 16
    elif state in ("menu", "loading", "game_not_running"):
        # PRD 16: static tools answer from the last static.json with a warning
        assert r["ok"] and r["meta"]["source"] == "static_catalog" and "catalog_from_previous_session" in codes(r)
        assert all(n["state"] is None for n in r["data"]["nodes"])
    else:
        # AMBIGUITY: PRD 16 has no static-tool entry for disabled/faulted/unresponsive/not-detected.
        assert r["ok"] or r["error"]["code"] == code


@pytest.mark.parametrize("state,reason", [("menu", "not_in_game"), ("loading", "not_in_game"), ("game_not_running", "game_not_running")])
@pytest.mark.parametrize("name", TOOLS)
def test_allow_stale_returns_last_snapshot_flagged(h, name, state, reason):
    _set_state(h, state)
    r = ok(h, name, {**VALID_CALLS[name], "allow_stale": True})
    assert r["meta"]["stale"] is True and r["meta"]["source"] == "stale_snapshot" and r["meta"]["stale_reason"] == reason
    assert "stale" in codes(r)


@pytest.mark.parametrize("name", TOOLS)
def test_allow_stale_never_bypasses_unsupported_build(h, name):
    _set_state(h, "unsupported_build")
    err(h, name, {**VALID_CALLS[name], "allow_stale": True}, "unsupported_build")


@pytest.mark.parametrize("name", TOOLS)
def test_world_session_changed(h, name):
    """PRD 16 quickload row: old-session snapshot never current; snapshot_unavailable or stale with allow_stale."""
    obs = FakeObserver(h.exchange, h.clock, h.procs)
    obs.verified = True
    obs.heartbeat("ready", world_session=bf.WORLD_SESSION_2)
    r = call(h, name, VALID_CALLS[name])
    if name == "get_tech_tree":
        assert r["ok"] and all(n["state"] is None for n in r["data"]["nodes"])
    else:
        assert r["ok"] is False and r["error"]["code"] == "snapshot_unavailable"
    s = ok(h, name, {**VALID_CALLS[name], "allow_stale": True})
    assert s["meta"]["stale"] is True and s["meta"]["stale_reason"] == "world_session_changed"


@pytest.mark.parametrize("name", TOOLS)
def test_live_but_stale_by_age(h, name):
    """PRD 13.4: same session, ready, age > max(15 s, 3 x interval): data with stale=true, stale_reason=age."""
    w = world_of(h)
    h.clock.advance(40)
    _heartbeat(h, w)
    h.write_world(w, ("heartbeat",))
    r = ok(h, name, VALID_CALLS[name])
    assert r["meta"]["stale"] is True and r["meta"]["stale_reason"] == "age" and "stale" in codes(r)


@pytest.mark.parametrize("name", TOOLS)
def test_paused_game_is_live(h, name):
    w = world_of(h)
    w["heartbeat"] = bf.build_heartbeat(h.clock.now(), static_doc=w["static"], state_doc=w["state"], history_doc=w["history"],
                                        paused=True)
    h.write_world(w, ("heartbeat",))
    r = ok(h, name, VALID_CALLS[name])
    assert r["meta"]["paused"] is True and r["meta"]["stale"] is False


@pytest.mark.parametrize("name", TOOLS)
def test_corrupted_state_falls_back_to_last_good(h, name):
    """PRD 16: corrupted file -> last good snapshot of the same session + warning."""
    ok(h, name, VALID_CALLS[name])
    h.write_raw("state", "{ not json")
    r = ok(h, name, VALID_CALLS[name])
    assert "snapshot_invalid_using_previous" in codes(r)


@pytest.mark.parametrize("name", RUNTIME_TOOLS)
def test_no_state_snapshot_yet(empty_h, world, name):
    """PRD 13.3: ready but no valid snapshot of the required family -> snapshot_unavailable."""
    empty_h.write_world(world, ("heartbeat", "static"))
    err(empty_h, name, VALID_CALLS[name], "snapshot_unavailable")


# ============================================================================ fresh (PRD 13.2 / 13.7)

FRESH_CASES = [
    ("list_cities", {"sort": "tier", "fields": "full"}), ("get_city", {"city": "Saint-Éloi"}), ("get_shop", {"shop": "épicerie générale"}),
    ("find_shops", {"product": "Paint"}), ("find_shops", {"product": "Dye", "from_building": bf.B_CP, "sort": "distance"}),
    ("list_regions", {"owner": "unowned"}), ("get_region", {"region": "Northmarch"}),
    ("get_market", {"include": ["auctions"]}), ("get_market", {"products": ["Paint"], "include": ["contracts"]}),
    ("get_tech_tree", {}), ("get_tech_tree", {"company": "Borealis Corp", "tree": "Logistics"}),
    ("get_research_state", {}), ("get_research_state", {"company": "Cobalt Works"}),
]


@pytest.mark.parametrize("name,args", FRESH_CASES)
def test_fresh_writes_exactly_the_state_scope(tmp_path, clock, procs, name, args):
    hh, obs = _observer_harness(tmp_path, clock, procs)
    r = hh.call(name, {**args, "fresh": True})
    check_response(name, r)
    assert r["ok"], r.get("error")
    req = obs.read_refresh_request()
    assert {k for k, v in req["requests"].items() if v is not None} == {"state"}
    assert "refresh_timeout" not in codes(r)
    assert {s["family"]: s["seq"] for s in r["meta"]["snapshots"]}["state"] == obs.state_seq


@pytest.mark.parametrize("name", TOOLS)
def test_fresh_timeout_answers_with_warning(tmp_path, clock, procs, name):
    hh, obs = _observer_harness(tmp_path, clock, procs, wait=0.5, serve=False)
    r = hh.call(name, {**VALID_CALLS[name], "fresh": True})
    check_response(name, r)
    assert r["ok"] and "refresh_timeout" in codes(r)


@pytest.mark.parametrize("name", TOOLS)
def test_fresh_when_not_live_writes_nothing(tmp_path, clock, procs, name):
    hh, obs = _observer_harness(tmp_path, clock, procs)
    obs.menu()
    r = hh.call(name, {**VALID_CALLS[name], "fresh": True})
    check_response(name, r)
    assert obs.read_refresh_request() is None
    if name != "get_tech_tree":
        assert r["error"]["code"] == "at_main_menu"


# ============================================================================ determinism, restart, cursors

@pytest.mark.parametrize("name,args", VALID_MATRIX[::3])
def test_identical_answers_after_restart(h, name, args):
    """PRD 13.1: restarting the server while the game is open yields identical answers from the same files."""
    a = ok(h, name, args)
    b = ok(h, name, args)
    h.restart()
    c = ok(h, name, args)
    assert a == b == c


@pytest.mark.parametrize("name,args", [("list_cities", {"fields": "full"}), ("find_shops", {"product": "Paint", "sort": "price"}),
                                       ("list_regions", {"owner": "ai"}), ("get_tech_tree", {"tree": "Chemistry"})])
def test_cursor_survives_restart_and_reaches_last_page(h, name, args):
    key = LIST_TOOLS[name]
    full = ok(h, name, {**args, "limit": 50})["data"][key]
    first = ok(h, name, {**args, "limit": 1})
    assert first["data"][key] == full[:1]
    if len(full) > 1:
        assert first["page"]["next_cursor"]
        h.restart()
        nxt = ok(h, name, {**args, "limit": 1, "cursor": first["page"]["next_cursor"]})
        assert nxt["data"][key] == full[1:2]
    rows, pages, last = walk_pages(h, name, args, key, 1)
    assert rows == full and pages == len(full) and last["page"]["next_cursor"] is None


@pytest.mark.parametrize("name,args,changed", [
    ("list_cities", {"sort": "population"}, {"sort": "tier"}),
    ("find_shops", {"product": "Paint"}, {"product": "Paint", "city": "Valmont"}),
    ("list_regions", {"owner": "player"}, {"owner": "ai"}),
    ("get_tech_tree", {"state": "unlocked"}, {"state": "available"}),
])
def test_cursor_with_changed_filters(h, name, args, changed):
    first = ok(h, name, {**args, "limit": 1})
    cur = first["page"]["next_cursor"]
    assert cur
    # AMBIGUITY: PRD 13.2 only says "opaque"; reuse under other filters is not specified. Invariant only.
    r = call(h, name, {**changed, "limit": 1, "cursor": cur})
    assert r["ok"] or r["error"]["code"] == "invalid_argument"


@pytest.mark.parametrize("name", list(LIST_TOOLS))
def test_garbage_cursors_rejected(h, name):
    base = dict(VALID_CALLS[name])
    for cur in ("garbage", "djF8LTF8eA", "", "x" * 400):
        r = call(h, name, {**base, "cursor": cur})
        if cur == "":
            assert r["ok"] or r["error"]["code"] == "invalid_argument"  # AMBIGUITY: empty cursor == absent?
        else:
            assert r["ok"] is False and r["error"]["code"] == "invalid_argument"


def test_meta_sections_used(h):
    r = ok(h, "get_city", {"city": "city:10"})
    assert {"cities", "shops"} <= set(r["meta"]["snapshot"]["sections_used"])
    r = ok(h, "get_market")
    assert "market" in r["meta"]["snapshot"]["sections_used"]
    r = ok(h, "get_tech_tree")
    assert {s["family"] for s in r["meta"]["snapshots"]} == {"state", "static"}
