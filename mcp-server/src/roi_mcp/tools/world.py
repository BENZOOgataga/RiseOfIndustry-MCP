"""Cities, shops and regions (PRD 14.7)."""

from __future__ import annotations

from collections import Counter

from .. import derive
from ..app import COMMON_SUFFIX, CallContext, ToolSpec
from ..errors import ToolError
from ..index import StateIndex, make_id
from .common import DEMAND_UNIT_NOTE, provenance, resolve_building_key, resolve_city, resolve_product, resolve_region

ESTIMATE_NOTE = ("existing_route_status per row: present (existing_route carries the game's own distance_tiles and "
                 "dispatch_cost), absent (no route; straight_line_cost_estimate with route_exists=false) or unavailable "
                 "(route existence unknown because the origin's route section, e.g. AI routes, was not captured; any "
                 "estimate has route_exists=null). straight_line_cost_estimate is always NON-AUTHORITATIVE (D-ROUTE-2: "
                 "the game's ManualDestinationDispatchCost formula evaluated with the straight-line distance) and only "
                 "given for shops WITHOUT an existing route.")


def _growth(c: dict) -> dict:
    return derive.growth_state(c.get("growth"), c.get("population_limit_reached"))


def list_cities(ctx: CallContext) -> dict:
    ctx.static(required=False)
    ctx.snapshot("state", sections=("cities",), optional_sections=("regions",))
    ix = ctx.state_index()
    rows = []
    for c in ix.cities.values():
        g = _growth(c)
        row = {"id": make_id("city", c["city_id"]), "name": c.get("name"), "tier": c.get("tier"), "tier_id": c.get("tier_id"),
               "population": c.get("population"), "growth_state": g.get("value"), "region": ix.region_ref(c.get("region_id")),
               "shop_count": len(c.get("shops") or []), "dead": c.get("dead")}
        if ctx.full:
            row.update({"population_limit": c.get("population_limit"), "consumption_interval_days": c.get("consumption_interval_days"),
                        "house_count": c.get("house_count"), "center": {"x": c.get("center_x"), "y": c.get("center_y")},
                        "growth": g})
        rows.append(row)
    sort = ctx.args.get("sort") or "population"
    if sort == "tier":
        rows.sort(key=lambda r: (-(r["tier_id"] or 0), -(r["population"] or 0), r["id"]))
    else:
        rows.sort(key=lambda r: (-(r["population"] or 0), r["id"]))
    data = ctx.paginate("cities", rows)
    data["provenance"] = provenance(observed=["id", "name", "tier", "population", "region", "shop_count"],
                                    derived=[("growth_state", "D-GROW-1")], persistence={"cities": "SAVE"})
    return data


def _shop_product_row(ix: StateIndex, shop: dict, sp: dict) -> dict:
    city = ix.cities.get(shop.get("city_id")) or {}
    unmet = derive.unmet_demand(sp.get("demand_for_player"), sp.get("player_delivered_stock"), city.get("consumption_interval_days"))
    return {"product": make_id("product", sp["product"]), "stock": sp.get("stock"), "slots": sp.get("slots"),
            "player_delivered_stock": sp.get("player_delivered_stock"), "demand_raw": sp.get("demand_raw"),
            "demand_for_player": sp.get("demand_for_player"), "price_for_player": sp.get("price_for_player"),
            "price_modifier_pct": sp.get("price_modifier_pct"), "shop_modifier": sp.get("shop_modifier"),
            "market_modifier": sp.get("market_modifier"), "sold_last_30d": sp.get("sold_last_30d"), "unmet_demand": unmet}


def get_city(ctx: CallContext) -> dict:
    ctx.static(required=False)
    ctx.snapshot("state", sections=("cities",), optional_sections=("shops", "regions"))
    ix = ctx.state_index()
    six = ix.static
    cid = resolve_city(ctx, ctx.args["city"])
    c = ix.cities.get(cid)
    if c is None:
        raise ToolError("not_found", f"city {cid} not in snapshot")
    tier_def = six.tiers.get(c.get("tier")) or {}
    shops = []
    for key in c.get("shops") or []:
        s = ix.shops.get(key)
        if s is None:
            shops.append({"id": make_id("building", key), "detail": None})
            continue
        shops.append({"id": make_id("building", key), "name": s.get("display_name"), "type": make_id("building_type", s.get("prefab")),
                      "owner": ix.actor_ref(s.get("owner_actor_id")), "is_dead": s.get("is_dead"),
                      "products": [_shop_product_row(ix, s, sp) for sp in s.get("products") or []]})
    if ix.data.get("shops") is None:  # section unavailable (an empty but healthy section is not "unavailable")
        ctx.add_unavailable("shops[].products", "shops section unavailable")
    return {
        "identity": {"id": make_id("city", cid), "name": c.get("name"), "type": c.get("type"),
                     "center": {"x": c.get("center_x"), "y": c.get("center_y")}},
        "region": ix.region_ref(c.get("region_id")),
        "tier": {"name": c.get("tier"), "tier_id": c.get("tier_id"), "next_tier": c.get("next_tier"),
                 "threshold_min": tier_def.get("threshold_min"), "next_tier_threshold": tier_def.get("threshold_max"),
                 "display_name": tier_def.get("display_name"), "english_name": tier_def.get("english_name")},
        "population": c.get("population"),
        "population_limit": c.get("population_limit"),
        "population_limit_reached": c.get("population_limit_reached"),
        "growth_state": _growth(c),
        "growth_inputs": c.get("growth"),
        "dead": c.get("dead"),
        "consumption_interval_days": c.get("consumption_interval_days"),
        "advancement": c.get("advancement"),
        "contract_offer": c.get("contract_offer"),
        "shops": shops,
        "houses_count": c.get("house_count"),
        "demand_unit_note": DEMAND_UNIT_NOTE,
        "provenance": provenance(observed=["identity", "population", "advancement", "contract_offer", "shops", "houses_count"],
                                 definition=["tier.threshold_min", "tier.next_tier_threshold", "tier names"],
                                 derived=[("growth_state", "D-GROW-1"), ("shops[].products[].unmet_demand", "D-SHOP-1")],
                                 persistence={"population": "SAVE", "shops[].products[].demand_raw": "RUNTIME"}),
    }


def _resolve_shop(ctx: CallContext, ix: StateIndex, value: str) -> dict:
    key = ctx.resolver().resolve(value, ("shop",), "shop").key
    s = ix.shops.get(key)
    if s is None:
        raise ToolError("not_found", f"{value!r} is not a shop", hint="Use find_shops or search(kinds=['shop']).")
    return s


def get_shop(ctx: CallContext) -> dict:
    ctx.static(required=False)
    ctx.snapshot("state", sections=("shops",), optional_sections=("cities",))
    ix = ctx.state_index()
    s = _resolve_shop(ctx, ix, ctx.args["shop"])
    city = ix.cities.get(s.get("city_id")) or {}
    return {
        "identity": {"id": make_id("building", s["building"]), "name": s.get("display_name"), "type": make_id("building_type", s.get("prefab")),
                     "owner": ix.actor_ref(s.get("owner_actor_id")), "coordinates": ix.coords(s["building"]), "is_dead": s.get("is_dead")},
        "city": ix.city_ref(s.get("city_id")),
        "consumption_interval_days": city.get("consumption_interval_days"),
        "products": [_shop_product_row(ix, s, sp) for sp in s.get("products") or []],
        "days_to_next_price_update": s.get("days_to_next_price_update"),
        "demand_unit_note": DEMAND_UNIT_NOTE,
        "provenance": provenance(observed=["identity", "products (stock, slots, delivered, demand, price, modifiers, sold)",
                                           "days_to_next_price_update"],
                                 derived=[("products[].unmet_demand", "D-SHOP-1")],
                                 persistence={"products.stock": "SAVE", "products.demand_raw": "RUNTIME", "products.demand_for_player": "RUNTIME"}),
    }


def find_shops(ctx: CallContext) -> dict:
    ctx.static(required=False)
    ctx.snapshot("state", sections=("shops",), optional_sections=("cities", "routes_player"))
    ix = ctx.state_index()
    six = ix.static
    a = ctx.args
    product = resolve_product(ctx, a["product"])
    origin = resolve_building_key(ctx, a["from_building"], "from_building") if a.get("from_building") else None
    city = resolve_city(ctx, a["city"]) if a.get("city") else None
    region = resolve_region(ctx, a["region"]) if a.get("region") else None
    formula_text = six.formulas.get("ManualDestinationDispatchCost")
    difficulty = ((ix.session.get("difficulty") or {}).get("dispatch"))
    ocoords = ix.coords(origin) if origin else None
    routes_known = True
    if origin is not None:
        # Routes of the player's buildings are in routes_player; routes of AI/State buildings only in the optional
        # routes_ai section (off by default). Without the origin's route section, route existence is unknown
        # (PRD 14.7 existing_route_status "unavailable"), never "absent".
        route_section = "routes_player" if ix.is_player((ix.building(origin) or {}).get("owner_actor_id")) else "routes_ai"
        if ctx.section("state", route_section) is None:
            routes_known = False
            ctx.add_unavailable("existing_route", f"{route_section} section unavailable: whether a route from "
                                "from_building to each shop exists is unknown (existing_route_status=unavailable)")
    rows = []
    for s in ix.shops_by_product.get(product, []):
        if city is not None and s.get("city_id") != city:
            continue
        if region is not None and (ix.cities.get(s.get("city_id")) or {}).get("region_id") != region:
            continue
        sp = next(x for x in s["products"] if x["product"] == product)
        cty = ix.cities.get(s.get("city_id")) or {}
        unmet = derive.unmet_demand(sp.get("demand_for_player"), sp.get("player_delivered_stock"), cty.get("consumption_interval_days"))
        row = {"shop": make_id("building", s["building"]), "name": s.get("display_name"), "city": ix.city_ref(s.get("city_id")),
               "is_dead": s.get("is_dead"), "demand_for_player": sp.get("demand_for_player"), "stock": sp.get("stock"),
               "player_delivered_stock": sp.get("player_delivered_stock"), "price_for_player": sp.get("price_for_player"),
               "unmet_demand": unmet, "consumption_interval_days": cty.get("consumption_interval_days"),
               "straight_line_tiles": None, "existing_route_status": None, "existing_route": None,
               "straight_line_cost_estimate": None}
        if origin is not None:
            scoords = ix.coords(s["building"]) or {}
            dist = derive.distances((ocoords or {}).get("x"), (ocoords or {}).get("y"), scoords.get("x"), scoords.get("y"))
            row["straight_line_tiles"] = {"euclidean": dist["euclidean"], "chebyshev": dist["chebyshev"], "estimate": True,
                                          "method": "D-DIST-1"}
            candidates = [r for r in ix.routes_by_origin.get(origin, []) if r["destination"] == s["building"] or r.get("endpoint") == s["building"]]
            # PRD-ambiguity: "a configured route to the shop" is matched for the same product first, else any
            # product; distance and dispatch cost of a route do not depend on the product.
            same = [r for r in candidates if r.get("product") == product]
            route = (same or candidates or [None])[0]
            if route is not None:
                row["existing_route_status"] = "present"
                row["existing_route"] = {"route_id": make_id("route", route["route_key"]), "product": make_id("product", route.get("product")),
                                         "distance_tiles": route.get("distance_tiles"), "dispatch_cost": route.get("dispatch_cost"),
                                         "path_status": route.get("path_status"), "errors": route.get("errors"), "authoritative": True}
            elif routes_known:
                row["existing_route_status"] = "absent"
                row["straight_line_cost_estimate"] = derive.straight_line_cost_estimate(formula_text, dist["euclidean"], difficulty)
            else:
                # Not authoritative and not a claim that no route exists (route_exists null).
                row["existing_route_status"] = "unavailable"
                row["straight_line_cost_estimate"] = derive.straight_line_cost_estimate(formula_text, dist["euclidean"], difficulty,
                                                                                        route_exists=None)
        rows.append(row)
    if origin is None:
        ctx.add_unavailable("straight_line_tiles/existing_route_status/existing_route/straight_line_cost_estimate",
                            "pass from_building to get distances and costs")
    elif formula_text is None:
        ctx.add_unavailable("straight_line_cost_estimate", "ManualDestinationDispatchCost formula not in static.json")
    sort = a.get("sort") or "demand"
    if sort == "price":
        rows.sort(key=lambda r: (r["price_for_player"] is None, -(r["price_for_player"] or 0), r["shop"]))
    elif sort == "distance":
        rows.sort(key=lambda r: ((r["straight_line_tiles"] or {}).get("euclidean") is None,
                                 (r["straight_line_tiles"] or {}).get("euclidean") or 0, r["shop"]))
    elif sort == "unmet_demand":
        rows.sort(key=lambda r: (-(r["unmet_demand"].get("per_30d") or r["unmet_demand"].get("per_interval") or 0), r["shop"]))
    else:
        rows.sort(key=lambda r: (-(r["demand_for_player"] or 0), r["shop"]))
    data = ctx.paginate("shops", rows)
    data["product"] = make_id("product", product)
    data["from_building"] = make_id("building", origin)
    data["notes"] = {"estimate": ESTIMATE_NOTE, "demand_unit": DEMAND_UNIT_NOTE}
    data["provenance"] = provenance(observed=["demand_for_player", "stock", "player_delivered_stock", "price_for_player"],
                                    game_computed=["existing_route.distance_tiles", "existing_route.dispatch_cost"],
                                    derived=[("unmet_demand", "D-SHOP-1"), ("straight_line_tiles", "D-DIST-1"),
                                             ("straight_line_cost_estimate", "D-ROUTE-2")],
                                    persistence={"demand_for_player": "RUNTIME", "stock": "SAVE"})
    return data


def _permit_cost(ix: StateIndex, r: dict) -> dict:
    if r.get("permit_cost") is not None:
        return {"value": r["permit_cost"], "method": "D-PERMIT-1", "computed_by": "observer"}
    ptype = (r.get("permit") or {}).get("type")
    six = ix.static
    pt = six.permit_types.get(ptype) if ptype else None
    if pt is None:
        return {"value": None, "method": "D-PERMIT-1", "reason": "permit type unknown"}
    top = six.permit_types.get(pt.get("top_level") or pt.get("name")) or pt
    out = derive.permit_cost(r.get("tile_count"), top.get("cost_per_tile"), pt.get("cost_modifier"), r.get("city_id") is not None)
    out["computed_by"] = "server"
    return out


def _resources_summary(r: dict) -> list[dict]:
    return [{"product": make_id("product", x.get("product")), "tiles": x.get("tiles"), "water_unlimited": x.get("water_unlimited")}
            for x in r.get("resources") or []]


def list_regions(ctx: CallContext) -> dict:
    ctx.static(required=False)
    ctx.snapshot("state", sections=("regions",), optional_sections=("cities",))
    ix = ctx.state_index()
    a = ctx.args
    owner = a.get("owner")
    owner_ids = None
    unowned = False
    if owner is not None:
        low = owner.strip().lower()
        if low == "unowned":
            unowned = True
        elif low == "player":
            owner_ids = {ix.player_actor_id}
        elif low == "ai":
            owner_ids = ix.ai_actor_ids()
        else:
            owner_ids = {int(ctx.resolver().resolve(owner, ("company",), "owner").key)}
    resource = resolve_product(ctx, a["resource"], "resource") if a.get("resource") else None
    rows = []
    for r in ix.regions.values():
        o = (r.get("permit") or {}).get("owner_actor_id")
        if unowned and o is not None:
            continue
        if owner_ids is not None and o not in owner_ids:
            continue
        if resource and resource not in {x.get("product") for x in r.get("resources") or []}:
            continue
        row = {"id": make_id("region", r["region_id"]), "name": r.get("name"), "city": ix.city_ref(r.get("city_id")),
               "owner": ix.actor_ref(o), "permit_cost": _permit_cost(ix, r), "resources": _resources_summary(r)}
        if ctx.full:
            row.update({"tile_count": r.get("tile_count"), "center": {"x": r.get("center_x"), "y": r.get("center_y")},
                        "amount_paid": (r.get("permit") or {}).get("amount_paid")})
        rows.append(row)
    rows.sort(key=lambda r: ((r["name"] or "").casefold(), r["id"]))
    data = ctx.paginate("regions", rows)
    data["provenance"] = provenance(observed=["id", "name", "city", "owner", "resources"], derived=[("permit_cost", "D-PERMIT-1")],
                                    persistence={"regions": "SAVE"})
    return data


def get_region(ctx: CallContext) -> dict:
    ctx.static(required=False)
    ctx.snapshot("state", sections=("regions",), optional_sections=("buildings_player", "buildings_ai", "cities"))
    ix = ctx.state_index()
    rid = resolve_region(ctx, ctx.args["region"])
    r = ix.regions.get(rid)
    if r is None:
        raise ToolError("not_found", f"region {rid} not in snapshot")
    counts = Counter()
    ai_by_owner = Counter()
    for b in ix.buildings_full.values():
        if b.get("region_id") == rid:
            if ix.is_player(b.get("owner_actor_id")):
                counts["player"] += 1
            else:
                ai_by_owner[b.get("owner_actor_id")] += 1
    for b in ix.buildings_compact.values():
        if b.get("region_id") == rid and b["key"] not in ix.buildings_full:
            ai_by_owner[b.get("owner_actor_id")] += 1
    permit = r.get("permit") or {}
    return {
        "identity": {"id": make_id("region", rid), "name": r.get("name"), "center": {"x": r.get("center_x"), "y": r.get("center_y")},
                     "tile_count": r.get("tile_count")},
        "city": ix.city_ref(r.get("city_id")),
        "permit": {"owner": ix.actor_ref(permit.get("owner_actor_id")), "amount_paid": permit.get("amount_paid"),
                   "type": permit.get("type"), "cost": _permit_cost(ix, r)},
        "cooldowns": {"permit_auction": r.get("permit_auction_cooldown"), "permit_purchase": r.get("permit_purchase_cooldown")},
        "resources": [{"product": make_id("product", x.get("product")), "tile_count": x.get("tiles"),
                       "water_unlimited": x.get("water_unlimited")} for x in r.get("resources") or []],
        "resource_sites": [{"product": make_id("product", s.get("product")), "center": {"x": s.get("center_x"), "y": s.get("center_y")},
                            "radius": s.get("radius"), "amount": s.get("amount"), "nodes": s.get("nodes")} for s in r.get("resource_sites") or []],
        "building_counts": {"player": counts.get("player", 0),
                            "ai": [{"owner": ix.actor_ref(a), "count": n} for a, n in sorted(ai_by_owner.items(), key=lambda t: str(t[0]))]},
        "provenance": provenance(observed=["identity", "city", "permit.owner/amount_paid", "cooldowns", "resources", "resource_sites",
                                           "building_counts"], derived=[("permit.cost", "D-PERMIT-1")], persistence={"permit": "SAVE"}),
    }


def specs() -> list[ToolSpec]:
    return [
        ToolSpec(name="list_cities", description="Cities with tier, population, growth state (D-GROW-1), region and shop count." + COMMON_SUFFIX,
                 scope="state", kind="runtime", list_tool=True, sort=("population", "tier"), params={}, handler=list_cities),
        ToolSpec(name="get_city",
                 description=("One city: tier and next-tier threshold, population and limit, growth state (D-GROW-1), consumption "
                              "interval, advancement, contract offer, shops with accepted products (demand, stock, price, unmet "
                              "demand). " + DEMAND_UNIT_NOTE + COMMON_SUFFIX),
                 scope="state", kind="runtime", params={"city": {"type": "string"}}, required=("city",), handler=get_city),
        ToolSpec(name="get_shop",
                 description=("One shop: per accepted product stock, slots, player-delivered stock, raw demand and demand for the "
                              "player, price for the player, modifiers, sold in the last 30 days, unmet demand (D-SHOP-1), days "
                              "to the next price update. " + DEMAND_UNIT_NOTE + COMMON_SUFFIX),
                 scope="state", kind="runtime", params={"shop": {"type": "string"}}, required=("shop",), handler=get_shop),
        ToolSpec(name="find_shops",
                 description=("Shops accepting a product with demand for the player, stock, price and unmet demand; with "
                              "from_building also straight-line distance (estimate) and either the existing route's game "
                              "distance/dispatch cost or a straight_line_cost_estimate. " + ESTIMATE_NOTE + " " + DEMAND_UNIT_NOTE
                              + COMMON_SUFFIX),
                 scope="state", kind="runtime", list_tool=True, fields_param=False, sort=("demand", "price", "distance", "unmet_demand"),
                 params={"product": {"type": "string"}, "from_building": {"type": "string"}, "city": {"type": "string"},
                         "region": {"type": "string"}},
                 required=("product",), handler=find_shops),
        ToolSpec(name="list_regions",
                 description="Regions with city, permit owner, permit cost (D-PERMIT-1) and resources (water flagged unlimited)." + COMMON_SUFFIX,
                 scope="state", kind="runtime", list_tool=True,
                 params={"owner": {"type": "string", "description": "player, ai, unowned, or a company id/name"},
                         "resource": {"type": "string"}},
                 handler=list_regions),
        ToolSpec(name="get_region",
                 description=("One region: centre, tile count, city, permit (owner, amount paid, cost), cooldowns, resources with "
                              "tile counts, resource sites, and player/AI building counts in the region." + COMMON_SUFFIX),
                 scope="state", kind="runtime", params={"region": {"type": "string"}}, required=("region",), handler=get_region),
    ]
