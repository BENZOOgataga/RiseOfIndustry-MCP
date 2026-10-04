"""Helpers shared by the tool implementations: provenance sections, entity rows, filters."""

from __future__ import annotations

from typing import Any

from .. import derive
from ..app import CallContext
from ..errors import ToolError
from ..index import StateIndex, StaticIndex, make_id
from ..util import parse_building_key

MAX_SEND_SEMANTICS = ("max_send: cap on the DESTINATION's stock of this product, counting stock plus incoming reservations "
                      "(in-flight/queued deliveries). Stored on the destination building per product and SHARED by every "
                      "origin shipping that product there (scope destination_product_shared). Not a per-trip amount, not a "
                      "per-route quota. value 0 = unlimited. mode auto_shop_demand = the shop's current demand.")
MIN_KEEP_SEMANTICS = ("min_keep: per route (per slot) floor of ORIGIN stock below which this route does not dispatch; "
                      "0..99, keep_all = the route never dispatches.")
DISPATCH_SEMANTICS = ("dispatch_amount_now: what the next dispatch on this route would request now (observer replica of "
                      "the game computation); not a throughput guarantee.")
DEMAND_UNIT_NOTE = "Shop demand is in units per consumption interval (consumption_interval_days of the city)."
AI_CASH_NOTE = "AI companies have infinite money in Rise of Industry: their cash is reported as {\"infinite\": true}."
VEHICLE_IDENTITY_NOTE = ("Vehicle ids are session-scoped pooled objects (vehicle:<world_session>:<instance id>): the same "
                         "object is reused for different trips; ids from another world session are rejected.")
PRICE_HISTORY = {"available": False, "reason": "not_retained_by_game"}


def provenance(observed: list | None = None, definition: list | None = None, game_computed: list | None = None,
               derived: list | None = None, persistence: dict | None = None) -> dict:
    """PRD 13.5 provenance sections.

    PRD-ambiguity: PRD 14 fixes the top-level `data` keys (identity, status, production, ...) while PRD
    13.5 asks for observed/definition/game_computed/derived/unavailable sections. Both are kept: values
    stay under the PRD 14 keys, and every response carries `provenance` (the four classes, naming the
    fields/sections of each, with method ids for derived values) plus a sibling `unavailable` list.
    Derived values that are objects also carry their own `method`. Values stay at the PRD 14 top-level keys; this block names which
    fields/sections are observed (with SAVE/RUNTIME persistence), definitions (static catalogue),
    game-computed, or derived (with the method id). `unavailable` is a sibling key of `provenance`."""
    return {
        "observed": {"fields": list(observed or []), "persistence": dict(persistence or {})},
        "definition": list(definition or []),
        "game_computed": list(game_computed or []),
        "derived": [{"field": f, "method": m} for f, m in (derived or [])],
    }


def product_id(name: str | None) -> str | None:
    return make_id("product", name)


def product_ref(six: StaticIndex, name: str | None) -> dict | None:
    if name is None:
        return None
    ref = six.product_ref(name)
    return ref


def names_of(defn: dict | None) -> dict:
    defn = defn or {}
    return {"display_name": defn.get("display_name"), "english_name": defn.get("english_name")}


# ---------------------------------------------------------------- companies / owners

def resolve_company(ctx: CallContext, ix: StateIndex, value: Any, label: str = "company") -> int:
    if value is None or (isinstance(value, str) and value.strip().lower() == "player"):
        if ix.player_actor_id is None:
            raise ToolError("section_unavailable", "The player company is not known (companies/session section missing).")
        return ix.player_actor_id
    ent = ctx.resolver().resolve(value, ("company",), label)
    return int(ent.key)


def owner_filter(ctx: CallContext, ix: StateIndex, value: Any, default: str = "player") -> set[int] | None:
    v = default if value is None else value
    if isinstance(v, str):
        low = v.strip().lower()
        if low == "all":
            return None
        if low == "player":
            if ix.player_actor_id is None:
                raise ToolError("section_unavailable", "The player company is not known (companies/session section missing).")
            return {ix.player_actor_id}
        if low == "ai":
            return ix.ai_actor_ids()
    ent = ctx.resolver().resolve(v, ("company",), "owner")
    return {int(ent.key)}


def resolve_building_key(ctx: CallContext, value: Any, label: str = "building") -> str:
    ent = ctx.resolver().resolve(value, ("building",), label)
    return ent.key


def resolve_product(ctx: CallContext, value: Any, label: str = "product") -> str:
    return ctx.resolver().resolve(value, ("product",), label).key


def resolve_city(ctx: CallContext, value: Any, label: str = "city") -> int:
    return int(ctx.resolver().resolve(value, ("city",), label).key)


def resolve_region(ctx: CallContext, value: Any, label: str = "region") -> str:
    return ctx.resolver().resolve(value, ("region",), label).key


# ---------------------------------------------------------------- buildings

RECIPE_USER_KINDS = {"factory", "gatherer", "farm"}


def is_recipe_user(six: StaticIndex, b: dict) -> bool:
    bt = six.building_types.get(b.get("prefab")) or {}
    if bt.get("recipes"):
        return True
    return b.get("kind") in RECIPE_USER_KINDS and not b.get("is_module")


def status_of(six: StaticIndex, b: dict) -> dict:
    recipe = six.recipes.get(b.get("recipe")) if b.get("recipe") else None
    return derive.building_status(b, recipe, is_recipe_user(six, b))


def theoretical_rate(six: StaticIndex, b: dict) -> dict:
    """D-RATE-1 for factories, D-RATE-2 for gatherers/farms."""
    recipe = six.recipes.get(b.get("recipe")) if b.get("recipe") else None
    kind = b.get("kind")
    if kind == "other" and "PrebuiltGatherer" in (b.get("tags") or []) and (b.get("modules") or {}).get("count"):
        # Map-prebuilt gatherers are exported with kind "other" but produce through their harvester modules like any
        # gatherer (live validation V1.1: 9 gas/oil/copper/coal/iron hubs; D-RATE-1 ignored their modules).
        kind = "gatherer"
    if kind in ("gatherer", "farm"):
        mods = b.get("modules")
        items = (mods or {}).get("items") if mods else None
        count = (mods or {}).get("count") if mods else b.get("module_count")
        return derive.rate_gatherer(recipe, b.get("cycle_days_effective"), items, kind, count)
    return derive.rate_factory(recipe, b.get("cycle_days_effective"))


def max_stock_ratio(b: dict) -> float | None:
    best = None
    for inv in b.get("inventory") or []:
        r = derive.fill_ratio(inv.get("count"), inv.get("slots"))
        if r is not None and (best is None or r > best):
            best = r
    return best


def building_row(ix: StateIndex, b: dict, full: bool = False) -> dict:
    six = ix.static
    key = b["key"]
    row = {
        "id": make_id("building", key),
        "display_name": b.get("display_name"),
        "type": make_id("building_type", b.get("prefab")),
        "owner": ix.actor_ref(b.get("owner_actor_id")),
        "city": ix.city_ref(b.get("city_id")),
        "region": ix.region_ref(b.get("region_id")),
        "coordinates": {"x": b.get("x"), "y": b.get("y")},
        "kind": b.get("kind"),
        "recipe": make_id("recipe", b.get("recipe")),
        "produced_last_month": ((b.get("production") or {}).get("produced_last_month")
                                if "production" in b else b.get("produced_last_month")),
        "detail": "full" if ix.is_full(key) else "compact",
    }
    st = status_of(six, b)
    row["status"] = st["status_class"]
    row["derived_status"] = st["derived_status"]
    row["max_stock_ratio"] = max_stock_ratio(b) if "inventory" in b else None
    row["upkeep_monthly"] = (b.get("upkeep") or {}).get("monthly_full") if b.get("upkeep") else None
    if full:
        row["flags"] = b.get("flags")
        row["tags"] = b.get("tags")
        row["status_evidence"] = st["evidence"]
        row["cycle_days_effective"] = b.get("cycle_days_effective")
        if ix.is_full(key):
            row["efficiency"] = b.get("efficiency")
            row["paid_to_build"] = b.get("paid_to_build")
            row["module_count"] = (b.get("modules") or {}).get("count") if b.get("modules") else None
        else:
            row["module_count"] = b.get("module_count")
    return row


def products_of_building(six: StaticIndex, b: dict) -> tuple[set, set]:
    produces, consumes = set(), set()
    recipe = six.recipes.get(b.get("recipe")) if b.get("recipe") else None
    if recipe:
        produces.update(r["product"] for r in recipe.get("results") or [])
        consumes.update(i["product"] for i in recipe.get("ingredients") or [])
    for inv in b.get("inventory") or []:
        if inv.get("role") == "output":
            produces.add(inv.get("product"))
        elif inv.get("role") == "input":
            consumes.add(inv.get("product"))
    return produces, consumes


# ---------------------------------------------------------------- routes

def route_row(ctx: CallContext, ix: StateIndex, r: dict, full: bool = False) -> dict:
    six = ix.static
    key = r["route_key"]
    dest_city = r.get("destination_city_id")
    ms = dict(r.get("max_send") or {})
    mk = dict(r.get("min_keep") or {})
    if ms.get("ui_label_validated") is False or mk.get("ui_label_validated") is False:
        ctx.warn("ui_label_unvalidated", "UI labels 'Max Send'/'Min Keep' are mapped with HIGH confidence; validation gate E2 pending")
    errors = list(r.get("errors") or [])
    row = {
        "route_id": make_id("route", key),
        "origin": {"id": make_id("building", r["origin"]), "name": (ix.building(r["origin"]) or {}).get("display_name"),
                   "coordinates": ix.coords(r["origin"])},
        "destination": {"id": make_id("building", r["destination"]),
                        "name": (ix.building(r["destination"]) or {}).get("display_name"),
                        "kind": r.get("destination_kind"), "city": ix.city_ref(dest_city), "coordinates": ix.coords(r["destination"])},
        "product": make_id("product", r.get("product")),
        "transport_mode": r.get("transport_mode"),
        "max_send": ms,
        "min_keep": mk,
        "distance_tiles": r.get("distance_tiles"),
        "dispatch_cost": r.get("dispatch_cost"),
        "vehicle_capacity": r.get("vehicle_capacity"),
        "dispatch_amount_now": (r.get("dispatch_amount_now") or {}).get("value"),
        "in_flight": _in_flight_summary(r.get("in_flight")),
        "paused": r.get("paused"),
        "dormant": r.get("dormant_auto_warehouse"),
        "errors": errors,
    }
    if full:
        o = ix.coords(r["origin"]) or {}
        d = ix.coords(r["destination"]) or {}
        row.update({
            "source": r.get("source"),
            "endpoint": make_id("building", r.get("endpoint")),
            "destination": {**row["destination"], "owner": ix.actor_ref(r.get("destination_owner_actor_id"))},
            "origin": {**row["origin"], "owner": ix.actor_ref((ix.building(r["origin"]) or {}).get("owner_actor_id"))},
            "wait_for_full_vehicle": r.get("wait_for_full_vehicle"),
            "path_status": r.get("path_status"),
            "dispatch_formula": r.get("dispatch_formula"),
            "dispatch_amount_now": r.get("dispatch_amount_now"),
            "in_flight": r.get("in_flight"),
            "destination_stock": r.get("destination_stock"),
            "destination_incoming_reserved": r.get("destination_incoming_reserved"),
            "destination_free_space": r.get("destination_free_space"),
            "destination_slots": r.get("destination_slots"),
            "origin_stock": r.get("origin_stock"),
            "validation_error": r.get("validation_error"),
            "has_error": r.get("has_error"),
            "destination_accepts_product": r.get("destination_accepts_product"),
            "destination_dead_city": r.get("destination_dead_city"),
            "unit_costs": derive.route_unit_costs(r.get("dispatch_cost"), r.get("vehicle_capacity"),
                                                  (r.get("dispatch_amount_now") or {}).get("value")),
            "straight_line_tiles": derive.distances(o.get("x"), o.get("y"), d.get("x"), d.get("y")),
        })
        row["cost_per_unit_at_capacity"] = row["unit_costs"]["cost_per_unit_at_capacity"]
    return row


def _in_flight_summary(f: dict | None) -> dict | None:
    if not f:
        return None
    return {"requests": f.get("requests_total"), "units_requested": f.get("units_requested"), "units_started": f.get("units_started")}


ROUTE_PROVENANCE = provenance(
    observed=["origin", "destination", "product", "source", "paused", "wait_for_full_vehicle", "dormant", "max_send.value",
              "max_send.mode", "min_keep", "in_flight", "destination_stock", "destination_incoming_reserved",
              "destination_free_space", "origin_stock", "validation_error"],
    game_computed=["endpoint", "transport_mode", "distance_tiles", "dispatch_cost", "vehicle_capacity"],
    derived=[("max_send.headroom_now", "replica:ManualDestinationManager.GetRequestedAmount"),
             ("dispatch_amount_now", "replica:ManualDestinationManager.GetRequestedAmount"),
             ("unit_costs / cost_per_unit_at_capacity", "D-ROUTE-1"), ("straight_line_tiles", "D-DIST-1"),
             ("errors", "observer+server checks")],
    persistence={"max_send": "SAVE", "min_keep": "SAVE", "paused": "SAVE", "in_flight": "SAVE", "destination_stock": "SAVE"},
)


def route_semantics() -> dict:
    return {"max_send": MAX_SEND_SEMANTICS, "min_keep": MIN_KEEP_SEMANTICS, "dispatch_amount_now": DISPATCH_SEMANTICS}


def vehicle_id(world_session: str | None, instance_id: Any) -> str:
    return f"vehicle:{world_session}:{instance_id}"


def check_vehicle_id(value: str, world_session: str | None) -> int:
    """PRD 14.4: a malformed id is invalid_argument; a well-formed id of another world session is stale_reference."""
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != "vehicle" or not parts[1]:
        raise ToolError("invalid_argument", "vehicle ids have the form vehicle:<world_session>:<instance id>")
    try:
        instance = int(parts[2])
    except ValueError:
        raise ToolError("invalid_argument", "vehicle instance id must be an integer") from None
    if parts[1] != world_session:
        raise ToolError("stale_reference", f"vehicle id {value!r} belongs to another world session",
                        hint="Vehicle ids are session-scoped pooled objects; list vehicles again.")
    return instance


def building_coords_from_key(key: str) -> dict | None:
    p = parse_building_key(key)
    return {"x": p["x"], "y": p["y"]} if p else None
