"""Catalogue tools (PRD 14.6): list_products, get_product, list_recipes, get_recipe,
list_building_types, get_building_type. Definitions come from static.json; live parts (prices,
unlocked flags, player producers) from the current state snapshot when one exists."""

from __future__ import annotations

from .. import derive
from ..app import COMMON_SUFFIX, CallContext, ToolSpec
from ..errors import ToolError
from ..index import StateIndex, StaticIndex, make_id
from ..util import normalize_name
from .common import DEMAND_UNIT_NOTE, PRICE_HISTORY, products_of_building, provenance

LIVE_NOTE = (" Definitions come from the static catalogue (available even when the game is not live, with warning "
             "catalog_from_previous_session); live parts (prices, unlocked flags) need a current state snapshot and are "
             "otherwise listed in `unavailable`.")


def _live(ctx: CallContext) -> StateIndex | None:
    snap = ctx.snapshot("state", required=False, optional_sections=("market", "research"))
    if snap is None:
        return None
    return ctx.state_index()


def _amounts(items) -> list[dict]:
    return [{"product": make_id("product", i["product"]), "amount": i["amount"]} for i in items or []]


# PRD-ambiguity: there is no per-product unlock flag; a product counts as unlocked when any recipe producing
# it is unlocked for the player (recipe unlocks from static tech data vs the player's unlocked set).
def _product_unlocked(six: StaticIndex, ix: StateIndex | None, product: str) -> bool | None:
    if ix is None or ix.player_unlocked is None:
        return None
    recipes = six.recipes_by_result.get(product) or []
    if not recipes:
        return None
    return any(six.recipe_unlocked(r, ix.player_unlocked) for r in recipes)


def _match_query(defn: dict, query: str | None) -> bool:
    if not query:
        return True
    q = normalize_name(query)
    return any(q in normalize_name(n) for n in (defn.get("name"), defn.get("display_name"), defn.get("english_name")) if n)


def list_products(ctx: CallContext) -> dict:
    ctx.static(sections=("products",))
    six = ctx.static_index()
    ix = _live(ctx)
    a = ctx.args
    rows = []
    for p in six.products.values():
        if a.get("category") and normalize_name(a["category"]) not in (normalize_name(p.get("category")), normalize_name(p.get("category_group"))):
            continue
        if a.get("tag") and normalize_name(a["tag"]) not in {normalize_name(t) for t in p.get("tags") or []}:
            continue
        if not _match_query(p, a.get("query")):
            continue
        mp = ix.market_prices.get(p["name"]) if ix else None
        unlocked = _product_unlocked(six, ix, p["name"])
        if a.get("unlocked_only") and unlocked is not True:
            continue
        row = {"id": make_id("product", p["name"]), "display_name": p.get("display_name"), "english_name": p.get("english_name"),
               "category": p.get("category"), "base_price": mp.get("value") if mp else None,
               "current_price": mp.get("price") if mp else None, "trend": mp.get("trend") if mp else None, "unlocked": unlocked}
        if ctx.full:
            row.update({"tags": p.get("tags"), "category_group": p.get("category_group"), "price_formula": p.get("price_formula"),
                        "demand_modifier": p.get("demand_modifier"), "end_game": p.get("end_game"),
                        "final_price_for_player": mp.get("final_price_for_player") if mp else None})
        rows.append(row)
    if ix is None:
        ctx.add_unavailable("base_price/current_price/trend/unlocked", "no current state snapshot")
    elif not ix.market_prices:
        ctx.add_unavailable("base_price/current_price/trend", "market section unavailable or product not in the market")
    rows.sort(key=lambda r: r["id"])
    data = ctx.paginate("products", rows)
    data["provenance"] = provenance(observed=["base_price", "current_price", "trend"], definition=["id", "names", "category"],
                                    derived=[("unlocked", "any producing recipe unlocked by the player's research")],
                                    persistence={"base_price": "RUNTIME", "current_price": "RUNTIME"})
    return data


def get_product(ctx: CallContext) -> dict:
    ctx.static(sections=("products",))
    six = ctx.static_index()
    ix = _live(ctx)
    res = ctx.resolver()
    name = res.resolve(ctx.args["product"], ("product",), "product").key
    p = six.products.get(name)
    if p is None:
        raise ToolError("not_found", f"product {name!r} is not in the static catalogue")
    data: dict = {
        "definition": {"id": make_id("product", name), "display_name": p.get("display_name"), "english_name": p.get("english_name"),
                       "category": p.get("category"), "category_group": p.get("category_group"), "tags": p.get("tags"),
                       "price_formula": p.get("price_formula"), "demand_modifier": p.get("demand_modifier"),
                       "end_game": p.get("end_game"), "disable_contracts": p.get("disable_contracts")},
        "recipes_producing": [_recipe_brief(six, six.recipes[r]) for r in six.recipes_by_result.get(name, [])],
        "recipes_consuming": [_recipe_brief(six, six.recipes[r]) for r in six.recipes_by_ingredient.get(name, [])],
        "price_history": dict(PRICE_HISTORY),
    }
    if ix is None:
        for f in ("market", "player_producers", "player_consumers", "shops_accepting", "state_offer"):
            data[f] = None
        ctx.add_unavailable("market/player_producers/player_consumers/shops_accepting/state_offer", "no current state snapshot")
    else:
        mp = ix.market_prices.get(name)
        data["market"] = ({k: mp.get(k) for k in ("value", "price", "modifier", "trend", "final_price_for_player")} if mp else None)
        if mp is None:
            ctx.add_unavailable("market", "product not in the market key set or market section unavailable")
        prods, cons = [], []
        for b in ix.data.get("buildings_player") or []:
            produces, consumes = products_of_building(six, b)
            ref = {"id": make_id("building", b["key"]), "name": b.get("display_name"), "recipe": make_id("recipe", b.get("recipe"))}
            if name in produces:
                prods.append(ref)
            if name in consumes:
                cons.append(ref)
        data["player_producers"] = prods
        data["player_consumers"] = cons
        shops = [s for s in ix.shops_by_product.get(name, []) if not s.get("is_dead")]
        total = 0
        total30 = 0.0
        intervals = {}
        for s in shops:
            sp = next(x for x in s["products"] if x["product"] == name)
            total += sp.get("demand_for_player") or 0
            city = ix.cities.get(s.get("city_id")) or {}
            iv = city.get("consumption_interval_days")
            intervals[make_id("city", s.get("city_id"))] = iv
            v30 = derive.per_interval_to_30d(sp.get("demand_for_player"), iv)
            total30 += v30 or 0
        data["shops_accepting"] = {"count": len(shops), "total_demand_per_interval": total,
                                   "total_demand_per_30d": {"value": round(total30, 3), "method": "per_interval x 30 / consumption_interval_days per city"},
                                   "consumption_interval_days": intervals, "demand_basis": "demand_for_player",
                                   "unit_note": DEMAND_UNIT_NOTE}
        state = (ix.market or {}).get("state") or {}
        sold = next((x for x in state.get("sold") or [] if x.get("product") == name), None)
        data["state_offer"] = {"sold_by_state": sold is not None, "price": sold.get("price_for_player") if sold else None,
                               "incoming_trade_allowed": state.get("incoming_trade_allowed")} if ix.market else None
    data["provenance"] = provenance(observed=["market", "player_producers", "player_consumers", "shops_accepting", "state_offer"],
                                    definition=["definition", "recipes_producing", "recipes_consuming"],
                                    derived=[("shops_accepting.total_demand_per_30d", "normalized to 30 days")],
                                    persistence={"market": "RUNTIME", "shops_accepting": "RUNTIME"})
    return data


def _recipe_brief(six: StaticIndex, r: dict) -> dict:
    return {"id": make_id("recipe", r["name"]), "display_name": r.get("display_name"), "english_name": r.get("english_name"),
            "inputs": _amounts(r.get("ingredients")), "outputs": _amounts(r.get("results")), "game_days": r.get("game_days"),
            "building_types": [make_id("building_type", b) for b in r.get("building_types") or []]}


def list_recipes(ctx: CallContext) -> dict:
    ctx.static(sections=("recipes",))
    six = ctx.static_index()
    ix = _live(ctx)
    a = ctx.args
    res = ctx.resolver()
    prod = res.resolve(a["product"], ("product",), "product").key if a.get("product") else None
    bt = res.resolve(a["building_type"], ("building_type",), "building_type").key if a.get("building_type") else None
    unlocked_set = ix.player_unlocked if ix else None
    rows = []
    for r in six.recipes.values():
        if prod and prod not in {x["product"] for x in (r.get("results") or []) + (r.get("ingredients") or [])}:
            continue
        if bt and bt not in (r.get("building_types") or []):
            continue
        unlocked = six.recipe_unlocked(r["name"], unlocked_set)
        if a.get("available_to_player") and unlocked is not True:
            continue
        row = {**_recipe_brief(six, r), "unlocked": unlocked}
        if ctx.full:
            row.update({"tier": r.get("tier"), "required_modules": [make_id("building_type", m) for m in r.get("required_modules") or []],
                        "game_days_for_price": r.get("game_days_for_price"),
                        "unlocked_by": [make_id("tech", u) for u in six.recipe_unlocked_by.get(r["name"], [])]})
        rows.append(row)
    if ix is None:
        ctx.add_unavailable("unlocked", "no current state snapshot")
    if a.get("available_to_player") and ix is None:
        ctx.add_unavailable("available_to_player", "filter needs the player's research state")
    rows.sort(key=lambda r: r["id"])
    data = ctx.paginate("recipes", rows)
    data["provenance"] = provenance(definition=["id", "names", "inputs", "outputs", "game_days", "building_types"],
                                    derived=[("unlocked", "tech unlocks of the recipe vs the player's unlocked set")])
    return data


def get_recipe(ctx: CallContext) -> dict:
    a = ctx.args
    if bool(a.get("recipe")) == bool(a.get("product")):
        raise ToolError("invalid_argument", "pass exactly one of recipe or product")
    ctx.static(sections=("recipes",))
    six = ctx.static_index()
    res = ctx.resolver()
    if a.get("recipe"):
        name = res.resolve(a["recipe"], ("recipe",), "recipe").key
    else:
        prod = res.resolve(a["product"], ("product",), "product").key
        names = six.recipes_by_result.get(prod) or []
        if not names:
            raise ToolError("not_found", f"no recipe produces {prod}")
        if len(names) > 1:
            # PRD-ambiguity: get_recipe(product) with several producing recipes is reported as ambiguous
            # (exact-match-only rule) instead of picking one.
            raise ToolError("ambiguous", f"{len(names)} recipes produce {prod}", hint="Pass one of the recipe ids.",
                            candidates=[{"id": make_id("recipe", n), "kind": "recipe", "display_name": six.recipes[n].get("display_name"),
                                         "english_name": six.recipes[n].get("english_name")} for n in names[:10]])
        name = names[0]
    r = six.recipes[name]
    days = r.get("game_days")
    rate = derive.rate_factory(r, days)
    return {
        "definition": {**_recipe_brief(six, r), "tier": r.get("tier"), "game_days_for_price": r.get("game_days_for_price"),
                       "required_modules": [make_id("building_type", m) for m in r.get("required_modules") or []],
                       "used_by_water_harvester": r.get("used_by_water_harvester")},
        "building_types": [{"id": make_id("building_type", b), "display_name": (six.building_types.get(b) or {}).get("display_name"),
                            "english_name": (six.building_types.get(b) or {}).get("english_name")} for b in r.get("building_types") or []],
        "unlocked_by": [{"id": make_id("tech", u), "display_name": (six.unlocks.get(u) or {}).get("display_name"),
                         "english_name": (six.unlocks.get(u) or {}).get("english_name")} for u in six.recipe_unlocked_by.get(name, [])],
        "per_30d": {"inputs": [{**i, "product": make_id("product", i.get("product"))} for i in rate.get("inputs") or []],
                    "outputs": [{**o, "product": make_id("product", o.get("product"))} for o in rate.get("outputs") or []],
                    "basis": "one building at the base cycle (game_days)",
                    "method": "D-RATE-1"},
        "provenance": provenance(definition=["definition", "building_types", "unlocked_by"], derived=[("per_30d", "D-RATE-1")]),
    }


def _btype_unlocked(six: StaticIndex, ix: StateIndex | None, name: str) -> bool | None:
    return six.building_unlocked(name, ix.player_unlocked if ix else None)


def list_building_types(ctx: CallContext) -> dict:
    ctx.static(sections=("building_types",))
    six = ctx.static_index()
    ix = _live(ctx)
    a = ctx.args
    prod = ctx.resolver().resolve(a["product"], ("product",), "product").key if a.get("product") else None
    rows = []
    for b in six.building_types.values():
        if a.get("tag") and normalize_name(a["tag"]) not in {normalize_name(t) for t in b.get("tags") or []}:
            continue
        if prod:
            prods = set()
            for rn in b.get("recipes") or []:
                r = six.recipes.get(rn) or {}
                prods.update(x["product"] for x in (r.get("results") or []) + (r.get("ingredients") or []))
            if prod not in prods:
                continue
        unlocked = _btype_unlocked(six, ix, b["name"])
        if a.get("unlocked_only") and unlocked is not True:
            continue
        pct = b.get("upkeep_cost_percentage")
        row = {"id": make_id("building_type", b["name"]), "display_name": b.get("display_name"), "english_name": b.get("english_name"),
               "base_cost": b.get("base_cost"),
               "upkeep_monthly_base": None if pct is None else round((b.get("base_cost") or 0) * pct, 2),
               "tags": b.get("tags"), "category": b.get("category"), "unlocked": unlocked}
        if ctx.full:
            row.update({"recipes": [make_id("recipe", r) for r in b.get("recipes") or []], "is_module": b.get("is_module"),
                        "storage_slots": b.get("storage_slots"), "max_module_count": b.get("max_module_count")})
        rows.append(row)
    if ix is None:
        ctx.add_unavailable("unlocked", "no current state snapshot")
    rows.sort(key=lambda r: r["id"])
    data = ctx.paginate("building_types", rows)
    data["provenance"] = provenance(definition=["id", "names", "base_cost", "tags", "category"],
                                    derived=[("upkeep_monthly_base", "base_cost x upkeep_cost_percentage"),
                                             ("unlocked", "tech unlocks of the building type vs the player's unlocked set")])
    return data


def _player_cost(ctx: CallContext, ix: StateIndex | None, name: str, b: dict) -> float | None:
    """PRD 14.6: the player's current build price, as TechTreeAgent.GetBuildingCost returns it (the agent's price
    table, else the base cost), before regional cost modifiers."""
    costs = (ix.player_research or {}).get("building_costs") if ix is not None else None
    if costs is None:
        ctx.add_unavailable("current_player_cost", "no current state snapshot" if ix is None else
                            "the player's price table is not available (research section or field unavailable)")
        return None
    entry = next((c for c in costs if c.get("building_type") == name), None)
    return entry["cost"] if entry is not None else b.get("base_cost")


def get_building_type(ctx: CallContext) -> dict:
    ctx.static(sections=("building_types",))
    six = ctx.static_index()
    ix = _live(ctx)
    name = ctx.resolver().resolve(ctx.args["building_type"], ("building_type",), "building_type").key
    b = six.building_types[name]
    pct = b.get("upkeep_cost_percentage")
    data = {
        "definition": {"id": make_id("building_type", name), **{k: v for k, v in b.items() if k != "name"},
                       "recipes": [make_id("recipe", r) for r in b.get("recipes") or []],
                       "module_prefab": make_id("building_type", b.get("module_prefab"))},
        "recipes": [_recipe_brief(six, six.recipes[r]) for r in b.get("recipes") or [] if r in six.recipes],
        "unlocked_by": [{"id": make_id("tech", u), "display_name": (six.unlocks.get(u) or {}).get("display_name"),
                         "english_name": (six.unlocks.get(u) or {}).get("english_name")} for u in six.building_unlocked_by.get(name, [])],
        "price_unlocks": [{"id": make_id("tech", u["name"]), "price_percentage": u.get("price_percentage")}
                          for u in six.unlocks.values() if u.get("kind") == "building_price" and name in (u.get("buildings") or [])],
        "upkeep_monthly_base": {"value": None if pct is None else round((b.get("base_cost") or 0) * pct, 2),
                                "method": "base_cost x upkeep_cost_percentage"},
        "unlocked": _btype_unlocked(six, ix, name),
        "current_player_cost": _player_cost(ctx, ix, name, b),
    }
    if ix is None:
        ctx.add_unavailable("unlocked", "no current state snapshot")
    data["provenance"] = provenance(definition=["definition", "recipes", "unlocked_by", "price_unlocks"],
                                    game_computed=["current_player_cost (TechTreeAgent.GetBuildingCost: the player's price "
                                                   "table, else base_cost; regional cost modifiers not applied)"],
                                    derived=[("upkeep_monthly_base", "base_cost x upkeep_cost_percentage"),
                                             ("unlocked", "tech unlocks vs the player's unlocked set")])
    return data


def specs() -> list[ToolSpec]:
    return [
        ToolSpec(name="list_products", description="Product catalogue with current market price, trend and unlocked flag." + LIVE_NOTE + COMMON_SUFFIX,
                 scope="state", kind="static_live", list_tool=True,
                 params={"category": {"type": "string"}, "tag": {"type": "string"}, "query": {"type": "string", "maxLength": 200},
                         "unlocked_only": {"type": "boolean", "default": False}},
                 handler=list_products),
        ToolSpec(name="get_product",
                 description=("One product: definition, current market values (no price history: the game keeps none), recipes "
                              "producing/consuming it, the player's producers/consumers, shops accepting it with total demand "
                              "and State offer. " + DEMAND_UNIT_NOTE + LIVE_NOTE + COMMON_SUFFIX),
                 scope="state", kind="static_live", params={"product": {"type": "string"}}, required=("product",), handler=get_product),
        ToolSpec(name="list_recipes", description="Recipe catalogue (inputs, outputs, game days, building types, unlocked)." + LIVE_NOTE + COMMON_SUFFIX,
                 scope="state", kind="static_live", list_tool=True,
                 params={"product": {"type": "string"}, "building_type": {"type": "string"},
                         "available_to_player": {"type": "boolean", "default": False}},
                 handler=list_recipes),
        ToolSpec(name="get_recipe",
                 description=("One recipe from the static catalogue (by recipe, or by product when exactly one recipe produces it): "
                              "inputs/outputs, game days, compatible building types, unlocking tech, per-30-day normalized I/O "
                              "(D-RATE-1). Static only: no fresh parameter." + COMMON_SUFFIX),
                 scope="none", kind="static", params={"recipe": {"type": "string"}, "product": {"type": "string"}},
                 handler=get_recipe),
        ToolSpec(name="list_building_types", description="Building type catalogue with base cost, base monthly upkeep and unlocked flag." + LIVE_NOTE + COMMON_SUFFIX,
                 scope="state", kind="static_live", list_tool=True,
                 params={"tag": {"type": "string"}, "product": {"type": "string"}, "unlocked_only": {"type": "boolean", "default": False}},
                 handler=list_building_types),
        ToolSpec(name="get_building_type",
                 description=("One building type: component values (storage slots, modules, upkeep percentage, efficiency arrays, "
                              "fleet, destination slots, shop values), recipes and unlocking tech." + LIVE_NOTE + COMMON_SUFFIX),
                 scope="state", kind="static_live", params={"building_type": {"type": "string"}}, required=("building_type",),
                 handler=get_building_type),
    ]
