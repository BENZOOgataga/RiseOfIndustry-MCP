"""list_buildings, get_building, get_production_overview, find_production_issues (PRD 14.3)."""

from __future__ import annotations

from collections import defaultdict

from .. import derive
from ..app import COMMON_SUFFIX, CallContext, ToolSpec
from ..errors import ToolError
from ..index import StateIndex, make_id
from .common import (DEMAND_UNIT_NOTE, MAX_SEND_SEMANTICS, ROUTE_PROVENANCE, building_row, is_recipe_user, max_stock_ratio,
                     owner_filter, products_of_building, provenance, resolve_building_key, resolve_city, resolve_company,
                     resolve_product, resolve_region, route_row, route_semantics, status_of, theoretical_rate, vehicle_id)

BUILDING_KINDS = ["factory", "gatherer", "farm", "harvester", "field", "warehouse", "depot", "shop", "hq", "other"]
STATUS_FILTER = ["working", "idle", "disabled", "blocked"]


def _company_buildings(ctx: CallContext, ix: StateIndex, owners: set[int] | None) -> list[dict]:
    """Full rows for the player (and AI detail when exported), compact rows for other AI buildings."""
    want_player = owners is None or ix.player_actor_id in owners
    want_ai = owners is None or any(o != ix.player_actor_id for o in owners)
    rows: list[dict] = []
    if want_player:
        ctx.require_section("state", "buildings_player")
        rows.extend(b for b in ix.data.get("buildings_player") or [] if owners is None or b.get("owner_actor_id") in owners)
    if want_ai:
        detail = ctx.section("state", "buildings_ai_detail") if (ctx.used["state"].snap.data.get("buildings_ai_detail") is not None) else None
        detail_keys = set()
        for b in detail or []:
            if owners is None or b.get("owner_actor_id") in owners:
                rows.append(b)
                detail_keys.add(b["key"])
        if owners is not None and not want_player:
            compact = ctx.require_section("state", "buildings_ai")
        else:
            compact = ctx.section("state", "buildings_ai")
        for b in compact or []:
            if b["key"] in detail_keys or b["key"] in ix.buildings_full:
                continue
            if owners is None or b.get("owner_actor_id") in owners:
                rows.append(b)
    return rows


def list_buildings(ctx: CallContext) -> dict:
    ctx.static(required=False)
    ctx.snapshot("state")
    ix = ctx.state_index()
    six = ix.static
    a = ctx.args
    owners = owner_filter(ctx, ix, a.get("owner"))
    rows = _company_buildings(ctx, ix, owners)
    if a.get("kind"):
        rows = [b for b in rows if b.get("kind") == a["kind"]]
    if a.get("building_type"):
        bt = ctx.resolver().resolve(a["building_type"], ("building_type",), "building_type").key
        rows = [b for b in rows if b.get("prefab") == bt]
    if a.get("product"):
        p = resolve_product(ctx, a["product"])
        rows = [b for b in rows if p in set().union(*products_of_building(six, b))]
    if a.get("recipe"):
        r = ctx.resolver().resolve(a["recipe"], ("recipe",), "recipe").key
        rows = [b for b in rows if b.get("recipe") == r]
    if a.get("city"):
        cid = resolve_city(ctx, a["city"])
        rows = [b for b in rows if b.get("city_id") == cid]
    if a.get("region"):
        rid = resolve_region(ctx, a["region"])
        rows = [b for b in rows if b.get("region_id") == rid]
    out = [building_row(ix, b, ctx.full) for b in rows]
    if a.get("status"):
        out = [r for r in out if r["status"] == a["status"]]
    sort = a.get("sort") or "name"
    if sort == "name":
        out.sort(key=lambda r: ((r["display_name"] or "").casefold(), r["id"]))
    elif sort == "type":
        out.sort(key=lambda r: (r["type"] or "", (r["display_name"] or "").casefold(), r["id"]))
    elif sort == "stock_ratio":
        out.sort(key=lambda r: (r["max_stock_ratio"] is None, -(r["max_stock_ratio"] or 0), r["id"]))
    elif sort == "produced_last_month":
        out.sort(key=lambda r: (r["produced_last_month"] is None, -(r["produced_last_month"] or 0), r["id"]))
    elif sort == "upkeep":
        out.sort(key=lambda r: (r["upkeep_monthly"] is None, -(r["upkeep_monthly"] or 0), r["id"]))
    data = ctx.paginate("buildings", out)
    data["provenance"] = provenance(
        observed=["id", "display_name", "type", "owner", "city", "region", "coordinates", "kind", "recipe", "produced_last_month",
                  "upkeep_monthly"],
        derived=[("status/derived_status", "D-STATUS-1"), ("max_stock_ratio", "D-INV-1")],
        persistence={"buildings": "SAVE"})
    data["notes"] = {"ai_rows": "AI buildings are compact rows unless the optional buildings_ai_detail section is exported; "
                                "their max_stock_ratio and upkeep are null.",
                     "status": "status is working/idle/disabled/blocked; derived_status gives the D-STATUS-1 reason."}
    return data


def _inventory_rows(ctx: CallContext, ix: StateIndex, b: dict) -> list[dict]:
    out = []
    ws = ctx.used["state"].snap.world_session
    for inv in b.get("inventory") or []:
        series = ctx.app.window.series(b["key"], inv.get("product"), ws)
        acc = derive.accumulation(inv.get("role"), inv.get("count"), inv.get("slots"), series)
        out.append({"product": make_id("product", inv.get("product")), "role": inv.get("role"), "count": inv.get("count"),
                    "stored": inv.get("stored"), "slots": inv.get("slots"), "fill_ratio": acc["fill_ratio"],
                    "incoming_reserved": inv.get("incoming_reserved"), "outgoing_reserved": inv.get("outgoing_reserved"),
                    "inbound_cap": inv.get("inbound_cap"), "accumulation": acc,
                    "window_delta": (derive.inventory_delta(series[0][1], series[0][0], series[-1][1], series[-1][0], ws, ws)
                                     if len(series) >= 2 else None)})
    return out


def _production(ix: StateIndex, b: dict) -> dict:
    six = ix.static
    recipe_name = b.get("recipe")
    recipe = six.recipes.get(recipe_name) if recipe_name else None
    prod = b.get("production") or {}
    rate = theoretical_rate(six, b)
    cycle = b.get("cycle_days_effective")
    progress = prod.get("progress")
    remaining = None
    if isinstance(progress, (int, float)) and isinstance(cycle, (int, float)) and 0 <= progress <= 1:
        remaining = round((1 - progress) * cycle, 2)
    return {
        "recipe": make_id("recipe", recipe_name),
        "recipe_names": {"display_name": (recipe or {}).get("display_name"), "english_name": (recipe or {}).get("english_name")},
        "inputs": rate.get("inputs", []),
        "outputs": rate.get("outputs", []),
        "cycle_days_base": (recipe or {}).get("game_days"),
        "cycle_days_effective": cycle,
        "progress": progress,
        "remaining_days": {"value": remaining, "method": "(1 - progress) x cycle_days_effective"},
        "produced_this_month": prod.get("produced_this_month"),
        "produced_last_month": prod.get("produced_last_month", b.get("produced_last_month")),
        "total_produced": prod.get("total_produced"),
        "average_10_months": prod.get("average_10_months"),
        "uptime_ratio": derive.uptime_ratio(prod.get("frames_spent_producing"), prod.get("production_frames")),
        "final_speed": prod.get("final_speed"),
        "theoretical_output_per_30d": rate,
    }


# Window metadata the observer attaches to every windowed history series (bounded V1 export, PRD 12.4 / E3).
HISTORY_WINDOW_KEYS = ("window_months", "window_first_month", "window_last_month", "first_month_available", "history_truncated")
HISTORY_WINDOW_NOTE = ("History is a bounded recent window, not everything the game retains: each series states its window "
                       "(window_months, window_first_month..window_last_month) and history_truncated=true when the game still "
                       "holds older data (first_month_available, when known, is the oldest month the game holds).")

GET_BUILDING_INCLUDES = ["production", "inventory", "outgoing_routes", "incoming_routes", "requests", "modules", "history", "vehicles"]


def _raise_if_building_sections_unavailable(ctx: CallContext) -> None:
    """A building that cannot be found while a building section failed is unknown, not absent (PRD 13.3)."""
    used = ctx.used.get("state")
    if used is None:
        return
    for sec in ("building_index", "buildings_player", "buildings_ai"):
        st = used.snap.sections.get(sec) or {}
        if st.get("status") in ("failed", "disabled", "over_budget"):
            why = st["status"] + (f": {st.get('reason')}" if st.get("reason") else "")
            raise ToolError("section_unavailable", f"Section '{sec}' of state.json is unavailable ({why}); the building "
                            "cannot be looked up.", details={"section": sec, "reason": why})


def get_building(ctx: CallContext) -> dict:
    ctx.static(required=False)
    ctx.snapshot("state")
    ix = ctx.state_index()
    six = ix.static
    try:
        key = resolve_building_key(ctx, ctx.args["building"])
    except ToolError as exc:
        if exc.code == "not_found":
            _raise_if_building_sections_unavailable(ctx)
        raise
    b = ix.building(key)
    if b is None:
        _raise_if_building_sections_unavailable(ctx)
        raise ToolError("not_found", f"building {key!r} not in snapshot")
    include = set(ctx.args.get("include") or [i for i in GET_BUILDING_INCLUDES if i != "history"])
    full = ix.is_full(key)
    bt = six.building_types.get(b.get("prefab")) or {}
    st = status_of(six, b)
    data: dict = {
        "identity": {"id": make_id("building", key), "save_guid": b.get("save_guid"), "display_name": b.get("display_name"),
                     "type": make_id("building_type", b.get("prefab")), "english_type_name": bt.get("english_name"),
                     "kind": b.get("kind"), "owner": ix.actor_ref(b.get("owner_actor_id")),
                     "coordinates": {"x": b.get("x"), "y": b.get("y")}, "rotation": b.get("rotation"),
                     "region": ix.region_ref(b.get("region_id")), "city": ix.city_ref(b.get("city_id")),
                     "paid_to_build": b.get("paid_to_build"), "tags": b.get("tags"),
                     "is_module": b.get("is_module"), "module_owner": make_id("building", b.get("module_owner")),
                     "detail": "full" if full else ("shop" if b.get("_from_shops") else "compact")},
        "status": {**(b.get("flags") or {}), "derived_status": st["derived_status"], "status_class": st["status_class"],
                   "notifications": list(b.get("notifications") or []), "evidence": st["evidence"], "method": "D-STATUS-1"},
    }
    derived = [("status.derived_status/evidence", "D-STATUS-1")]
    game_computed = []
    if b.get("_from_shops"):
        data["status"] = None
        ctx.add_unavailable("status", "shop buildings owned by cities: use get_shop for stock, demand and prices")
    if full:
        data["efficiency"] = b.get("efficiency")
        data["upkeep"] = b.get("upkeep")
        data["pollution"] = {"at_tile": b.get("pollution_at_tile"), "is_polluted": b.get("is_polluted")}
        data["logistics"] = b.get("logistics")
    else:
        for f in ("efficiency", "upkeep"):
            ctx.add_unavailable(f, "compact AI/shop row (enable include_ai_building_detail in observer.config.json for AI detail)")
    if "production" in include:
        if b.get("recipe") or is_recipe_user(six, b):
            data["production"] = _production(ix, b)
            game_computed.append("production.cycle_days_effective")
            derived += [("production.inputs/outputs per_30d, theoretical_output_per_30d",
                         data["production"]["theoretical_output_per_30d"].get("method")),
                        ("production.uptime_ratio", "D-RATE-3"), ("production.remaining_days", "(1 - progress) x cycle")]
            if not full:
                ctx.add_unavailable("production.progress", "compact row")
        else:
            data["production"] = None
    if "inventory" in include:
        if full:
            data["inventory"] = _inventory_rows(ctx, ix, b)
            derived.append(("inventory[].fill_ratio/accumulation", "D-INV-1"))
            derived.append(("inventory[].window_delta", "D-INV-2"))
        else:
            data["inventory"] = None
            ctx.add_unavailable("inventory", "not exported for this building (compact row)")
    route_needed = "outgoing_routes" in include or "incoming_routes" in include
    if route_needed:
        ctx.section("state", "routes_player")
        if b.get("owner_actor_id") != ix.player_actor_id and ctx.section("state", "routes_ai") is None:
            # Routes of AI buildings are only in the optional routes_ai section (PRD 12.2, 14.3).
            for part in ("outgoing_routes", "incoming_routes"):
                if part in include:
                    ctx.add_unavailable(part, "AI routes are not exported (routes_ai section off or unavailable)")
    if "outgoing_routes" in include:
        data["outgoing_routes"] = [route_row(ctx, ix, r, full=True) for r in ix.routes_by_origin.get(key, [])]
    if "incoming_routes" in include:
        seen = set()
        incoming = []
        for r in ix.routes_by_destination.get(key, []):
            if r["route_key"] in seen:
                continue
            seen.add(r["route_key"])
            incoming.append(route_row(ctx, ix, r, full=True))
        data["incoming_routes"] = incoming
        derived.append(("incoming_routes", "reverse index of routes by destination"))
    if route_needed:
        data["route_semantics"] = route_semantics()
    if "requests" in include:
        ctx.section("state", "requests_player")
        data["requests"] = [_request_row(ix, q) for q in ix.requests_by_endpoint.get(key, [])]
    if "modules" in include:
        mods = b.get("modules")
        if mods:
            data["modules"] = {"count": mods.get("count"), "max": mods.get("max"), "module_prefab": mods.get("module_prefab"),
                               "items": [{"id": make_id("building", m.get("key")), "type": make_id("building_type", m.get("prefab")),
                                          "progress": m.get("progress"), "resource": make_id("product", m.get("resource")),
                                          "nodes": m.get("nodes"), "nodes_depleted": m.get("nodes_depleted"),
                                          "deposit_remaining": m.get("deposit_remaining"), "efficiency": m.get("efficiency"),
                                          "speed_replica": m.get("speed_replica"), "user_enabled": m.get("user_enabled"),
                                          "is_working": m.get("is_working")} for m in mods.get("items") or []]}
        else:
            data["modules"] = None if full else None
            if not full and b.get("module_count") is not None:
                data["modules"] = {"count": b.get("module_count"), "items": None}
    if "vehicles" in include:
        vehicles = ctx.section("state", "vehicles") or {}
        ws = ctx.used["state"].snap.world_session
        fleets = [f for f in vehicles.get("fleets_player") or [] if f.get("building") == key]
        rows = [v for v in vehicles.get("vehicles_player") or []
                if key in (v.get("fleet_building"), v.get("job_origin"), v.get("job_destination"))]
        data["vehicles"] = {"fleet": b.get("fleet") or (fleets[0] if fleets else None),
                            "vehicles": [_vehicle_row(ix, ws, v) for v in rows[:50]],
                            "identity_note": "vehicle ids are session-scoped pooled objects"}
    if "history" in include:
        hist = ctx.snapshot("history", required=False)
        if hist is not None:
            for sec, part in (("buildings_monthly_player", "history.monthly_analysis"),
                              ("production_monthly_player", "history.production_monthly")):
                status = (hist.sections.get(sec) or {}).get("status")
                if status not in (None, "ok"):
                    reason = (hist.sections.get(sec) or {}).get("reason")
                    ctx.add_unavailable(part, f"section_{status}" + (f": {reason}" if reason else ""))
            series = next((s for s in hist.data.get("buildings_monthly_player") or [] if s.get("building") == key), None)
            prod_series = [s for s in hist.data.get("production_monthly_player") or [] if s.get("building") == key]
            data["history"] = {"monthly_analysis": (series or {}).get("series"),
                               "production_monthly": [{"product": make_id("product", s.get("product")),
                                                       **{k: s.get(k) for k in HISTORY_WINDOW_KEYS},
                                                       "months": s.get("months")}
                                                      for s in prod_series],
                               "window_note": HISTORY_WINDOW_NOTE}
            if series is None:
                ctx.add_unavailable("history.monthly_analysis", "no BuildingAnalysis series exported for this building")
        else:
            data["history"] = None
    data["provenance"] = provenance(
        observed=["identity", "status (flags, notifications)", "efficiency", "upkeep", "production (progress, counters)",
                  "inventory", "outgoing_routes", "incoming_routes", "requests", "modules", "vehicles", "history"],
        definition=["identity.english_type_name", "production.cycle_days_base", "production.recipe_names",
                    "production.inputs/outputs amount_per_cycle"],
        game_computed=game_computed + ["routes: distance_tiles, dispatch_cost, vehicle_capacity"],
        derived=derived,
        persistence={"identity": "SAVE", "inventory": "SAVE", "production": "SAVE", "vehicles": "RUNTIME"})
    return data


def _request_row(ix: StateIndex, q: dict) -> dict:
    return {"request_id": make_id("request", q["request_key"]), "endpoint": ix.building_ref(q.get("endpoint")),
            "product": make_id("product", q.get("product")),
            "requested_amount": "fill" if q.get("fill") else q.get("requested_amount"), "fill": q.get("fill"),
            "remaining": q.get("remaining"), "amount_being_moved": q.get("amount_being_moved"), "priority": q.get("priority"),
            "active": q.get("active"), "use_full_vehicles": q.get("use_full_vehicles"), "fulfilled": q.get("fulfilled"),
            "allowed_depots": q.get("allowed_graphs"), "expenses_this_month": q.get("expenses_this_month"),
            "expenses_last_month": q.get("expenses_last_month"), "endpoint_pull_disabled": q.get("endpoint_pull_disabled")}


def _vehicle_row(ix: StateIndex, ws: str | None, v: dict) -> dict:
    return {"id": vehicle_id(ws, v.get("instance_id")), "trip_counter_id": v.get("trip_counter_id"), "prefab": v.get("prefab"),
            "transport_mode": v.get("transport_mode"), "fleet_building": make_id("building", v.get("fleet_building")),
            "cargo": {"product": make_id("product", v.get("product")), "amount": v.get("amount")},
            "origin": make_id("building", v.get("job_origin")), "destination": make_id("building", v.get("job_destination")),
            "job_product": make_id("product", v.get("job_product")), "job_amount": v.get("job_amount"),
            "going_home": v.get("going_home"), "position": {"x": v.get("position_x"), "z": v.get("position_z"),
                                                            "tile_x": v.get("tile_x"), "tile_y": v.get("tile_y")}}


def _shop_demand_by_product(ix: StateIndex, for_player: bool) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(lambda: {"per_interval": 0, "per_30d": 0.0, "shops": 0, "unknown_interval": 0})
    for s in ix.shops.values():
        if s.get("is_dead"):
            continue
        interval = (ix.cities.get(s.get("city_id")) or {}).get("consumption_interval_days")
        for p in s.get("products") or []:
            d = p.get("demand_for_player") if for_player else p.get("demand_raw")
            if d is None:
                continue
            row = out[p["product"]]
            row["per_interval"] += d
            row["shops"] += 1
            v30 = derive.per_interval_to_30d(d, interval)
            if v30 is None:
                row["unknown_interval"] += 1
            else:
                row["per_30d"] += v30
    return out


def get_production_overview(ctx: CallContext) -> dict:
    ctx.static(required=False)
    ctx.snapshot("state", optional_sections=("shops", "cities"))
    ix = ctx.state_index()
    six = ix.static
    actor = resolve_company(ctx, ix, ctx.args.get("company"))
    only = resolve_product(ctx, ctx.args["product"]) if ctx.args.get("product") else None
    rows = _company_buildings(ctx, ix, {actor})
    products: dict[str, dict] = {}

    def entry(p: str) -> dict:
        return products.setdefault(p, {"product": make_id("product", p), "producers": [], "consumers": [],
                                       "theoretical_supply_per_30d": 0.0, "theoretical_demand_internal_per_30d": 0.0,
                                       "produced_last_month": 0, "stock_total": 0, "approximate": False})
    for b in rows:
        rate = theoretical_rate(six, b)
        if not rate.get("available"):
            recipe = six.recipes.get(b.get("recipe")) if b.get("recipe") else None
            if recipe is None:
                continue
            # Known recipe, unknown cycle time: list the building with a null rate instead of dropping it.
            rate = {"available": False, "approximate": True,
                    "outputs": [{"product": r.get("product"), "per_30d": None} for r in recipe.get("results") or []],
                    "inputs": [{"product": i.get("product"), "per_30d": None} for i in recipe.get("ingredients") or []]}
            ctx.add_unavailable(f"rate:{make_id('building', b['key'])}", "cycle time unknown; theoretical rate not computed")
        working_flag = (b.get("flags") or {}).get("user_enabled", True)
        for o in rate.get("outputs") or []:
            if only and o["product"] != only:
                continue
            e = entry(o["product"])
            per = o["per_30d"] if working_flag else 0
            e["producers"].append({"building": make_id("building", b["key"]), "name": b.get("display_name"),
                                   "recipe": make_id("recipe", b.get("recipe")), "theoretical_per_30d": o["per_30d"],
                                   "enabled": working_flag,
                                   "produced_last_month": (b.get("production") or {}).get("produced_last_month", b.get("produced_last_month"))})
            e["theoretical_supply_per_30d"] += per or 0
            e["produced_last_month"] += e["producers"][-1]["produced_last_month"] or 0
            e["approximate"] = e["approximate"] or bool(rate.get("approximate"))
        for i in rate.get("inputs") or []:
            if only and i["product"] != only:
                continue
            e = entry(i["product"])
            per = i["per_30d"] if working_flag else 0
            e["consumers"].append({"building": make_id("building", b["key"]), "name": b.get("display_name"),
                                   "recipe": make_id("recipe", b.get("recipe")), "theoretical_need_per_30d": i["per_30d"],
                                   "enabled": working_flag})
            e["theoretical_demand_internal_per_30d"] += per or 0
    for b in rows:
        for inv in b.get("inventory") or []:
            p = inv.get("product")
            if p in products:
                products[p]["stock_total"] += inv.get("count") or 0
    is_player = actor == ix.player_actor_id
    shop_demand = _shop_demand_by_product(ix, for_player=is_player) if ix.shops else {}
    if not ix.shops:
        ctx.add_unavailable("shops_demand_total", "shops section unavailable")
    out = []
    for p, e in products.items():
        sd = shop_demand.get(p)
        e["shops_demand_total"] = ({"per_interval": sd["per_interval"], "per_30d": round(sd["per_30d"], 3), "shops": sd["shops"],
                                    "unit": "per_interval = units per each city's consumption interval; per_30d normalized (D)",
                                    "basis": "demand_for_player" if is_player else "demand_raw"} if sd else None)
        shop30 = sd["per_30d"] if sd else 0.0
        if sd and sd["unknown_interval"]:
            # Some shops have no known consumption interval: their demand cannot be normalised to 30 days.
            e["shops_demand_total"]["per_30d"] = None
            e["approximate"] = True
            ctx.add_unavailable(f"shops_demand_total.per_30d:{e['product']}",
                                f"consumption interval unknown for {sd['unknown_interval']} shop(s)")
        e["theoretical_supply_per_30d"] = round(e["theoretical_supply_per_30d"], 3)
        e["theoretical_demand_internal_per_30d"] = round(e["theoretical_demand_internal_per_30d"], 3)
        e["balance_per_30d"] = round(e["theoretical_supply_per_30d"] - e["theoretical_demand_internal_per_30d"] - shop30, 3)
        e["supply_demand_ratio"] = derive.supply_demand_ratio(e["theoretical_supply_per_30d"],
                                                              e["theoretical_demand_internal_per_30d"], shop30)
        out.append(e)
    out.sort(key=lambda e: e["product"])
    return {
        "company": make_id("company", actor),
        "products": out,
        "notes": {"demand_unit": DEMAND_UNIT_NOTE,
                  "theoretical": "Theoretical rates assume continuous production at the effective cycle time (D-RATE-1/2); "
                                 "disabled buildings contribute 0."},
        "provenance": provenance(observed=["producers[].produced_last_month", "stock_total", "shops_demand_total.per_interval"],
                                 definition=["recipe amounts"], game_computed=["cycle_days_effective"],
                                 derived=[("theoretical_per_30d / theoretical_need_per_30d", "D-RATE-1 / D-RATE-2"),
                                          ("shops_demand_total.per_30d", "per_interval x 30 / consumption_interval_days"),
                                          ("balance_per_30d", "supply - internal need - shop demand per 30 d"),
                                          ("supply_demand_ratio", "D-SUPDEM-1")]),
    }


ISSUE_KINDS = ["disabled", "no_recipe", "missing_input", "output_full", "no_modules", "deposit_depleted", "polluted",
               "requirements_unmet", "route_error", "route_dormant_auto_wh", "route_keep_all", "inventory_accumulating"]
REASON_TO_ISSUE = {"disabled": "disabled", "no_recipe": "no_recipe", "missing_input": "missing_input", "output_full": "output_full",
                   "no_modules": "no_modules", "deposit_depleted": "deposit_depleted", "polluted": "polluted",
                   "blocked": "requirements_unmet"}


def find_production_issues(ctx: CallContext) -> dict:
    ctx.static(required=False)
    ctx.snapshot("state")
    ix = ctx.state_index()
    six = ix.static
    actor = resolve_company(ctx, ix, ctx.args.get("company"))
    only = resolve_product(ctx, ctx.args["product"]) if ctx.args.get("product") else None
    kinds = set(ctx.args.get("kinds") or ISSUE_KINDS)
    rows = _company_buildings(ctx, ix, {actor})
    ws = ctx.used["state"].snap.world_session
    issues = []
    for b in rows:
        st = status_of(six, b)
        bid = make_id("building", b["key"])
        for ev in st["evidence"]:
            kind = REASON_TO_ISSUE.get(ev["reason"])
            if kind is None or kind not in kinds:
                continue
            product = ev.get("product")
            if only and product != only and not (product is None and only in set().union(*products_of_building(six, b))):
                continue
            evidence = dict(ev)
            if kind == "missing_input":
                inbound = [r for r in ix.routes_by_destination.get(b["key"], []) if r.get("product") == product]
                evidence["inbound_routes"] = [{"route_id": make_id("route", r["route_key"]), "origin": make_id("building", r["origin"]),
                                               "paused": r.get("paused"), "dormant": r.get("dormant_auto_warehouse"),
                                               "errors": r.get("errors"), "dispatch_amount_now": (r.get("dispatch_amount_now") or {}).get("value")}
                                              for r in inbound]
                evidence["warehouse_requests"] = [make_id("request", q["request_key"]) for q in ix.requests_by_endpoint.get(b["key"], [])
                                                  if q.get("product") == product]
            issues.append({"kind": kind, "building": bid, "building_name": b.get("display_name"),
                           "product": make_id("product", product), "route": None, "evidence": evidence, "since": None})
        if "inventory_accumulating" in kinds:
            for inv in b.get("inventory") or []:
                if only and inv.get("product") != only:
                    continue
                series = ctx.app.window.series(b["key"], inv.get("product"), ws)
                acc = derive.accumulation(inv.get("role"), inv.get("count"), inv.get("slots"), series)
                if acc["accumulating"]:
                    issues.append({"kind": "inventory_accumulating", "building": bid, "building_name": b.get("display_name"),
                                   "product": make_id("product", inv.get("product")), "route": None,
                                   "evidence": {"count": inv.get("count"), "slots": inv.get("slots"), "role": inv.get("role"),
                                                **acc}, "since": None})
    routes = [r for r in ix.routes.values() if (ix.building(r["origin"]) or {}).get("owner_actor_id") == actor]
    ctx.section("state", "routes_player")
    for r in routes:
        if only and r.get("product") != only:
            continue
        rid = make_id("route", r["route_key"])
        base = {"building": make_id("building", r["origin"]), "building_name": (ix.building(r["origin"]) or {}).get("display_name"),
                "product": make_id("product", r.get("product")), "route": rid, "since": None}
        if "route_error" in kinds and (r.get("errors") or r.get("has_error")):
            issues.append({"kind": "route_error", **base,
                           "evidence": {"errors": r.get("errors"), "validation_error": r.get("validation_error"),
                                        "path_status": r.get("path_status"), "destination": make_id("building", r["destination"])}})
        if "route_dormant_auto_wh" in kinds and r.get("dormant_auto_warehouse"):
            issues.append({"kind": "route_dormant_auto_wh", **base,
                           "evidence": {"dormant_auto_warehouse": True,
                                        "auto_warehouse": make_id("building", ((ix.building(r["origin"]) or {}).get("logistics") or {}).get("warehouse"))}})
        if "route_keep_all" in kinds and (r.get("min_keep") or {}).get("keep_all"):
            issues.append({"kind": "route_keep_all", **base, "evidence": {"min_keep": r.get("min_keep")}})
    order = {k: i for i, k in enumerate(ISSUE_KINDS)}
    issues.sort(key=lambda i: (order.get(i["kind"], 99), i["building"], i.get("product") or "", i.get("route") or ""))
    data = ctx.paginate("issues", issues)
    data["company"] = make_id("company", actor)
    data["notes"] = {"evidence": "Rows are evidence, not judgements. since is null: the game retains no per-building status history.",
                     "max_send": MAX_SEND_SEMANTICS}
    data["provenance"] = provenance(observed=["issues[].evidence (flags, inventory, route fields)"],
                                    derived=[("issues[].kind (building)", "D-STATUS-1"), ("inventory_accumulating", "D-INV-1")])
    return data


def specs() -> list[ToolSpec]:
    company_param = {"type": "string", "description": "company id or name; default the player"}
    return [
        ToolSpec(name="list_buildings",
                 description=("Buildings of the player (default), AI companies or all, with status (D-STATUS-1), recipe, "
                              "produced last month, max stock fill ratio (D-INV-1) and upkeep. AI rows are compact." + COMMON_SUFFIX),
                 scope="state", kind="runtime", list_tool=True,
                 sort=("name", "type", "stock_ratio", "produced_last_month", "upkeep"),
                 params={"owner": {"type": "string", "description": "player (default), ai, all, or a company id/name"},
                         "kind": {"type": "string", "enum": BUILDING_KINDS},
                         "building_type": {"type": "string"}, "product": {"type": "string", "description": "produces or consumes"},
                         "recipe": {"type": "string"}, "city": {"type": "string"}, "region": {"type": "string"},
                         "status": {"type": "string", "enum": STATUS_FILTER}},
                 handler=list_buildings),
        ToolSpec(name="get_building",
                 description=("Full inspection of one building: identity, status with evidence, efficiency, upkeep, production "
                              "(recipe, per-30-day rates), inventory with slots/reservations and inbound cap (= Max Send stored on "
                              "this building), outgoing and incoming routes with Max Send/Min Keep, warehouse requests, modules, "
                              "vehicles, optional monthly history (a bounded recent window: 24 months, each series flags history_truncated "
                              "when the game holds older data). " + MAX_SEND_SEMANTICS + COMMON_SUFFIX),
                 scope="state", kind="runtime",
                 params={"building": {"type": "string", "description": "building id or exact name"},
                         "include": {"type": "array", "items": {"type": "string", "enum": GET_BUILDING_INCLUDES}, "uniqueItems": True}},
                 required=("building",), handler=get_building),
        ToolSpec(name="get_production_overview",
                 description=("Per-product production/consumption balance for a company: producers and consumers with theoretical "
                              "rates per 30 days, produced last month, stock, shop demand (normalized to 30 days), balance and "
                              "supply/demand ratio. " + DEMAND_UNIT_NOTE + COMMON_SUFFIX),
                 scope="state", kind="runtime",
                 params={"company": company_param, "product": {"type": "string"}}, handler=get_production_overview),
        ToolSpec(name="find_production_issues",
                 description=("Evidence rows (not judgements) for disabled, starved (missing input, with inbound routes), "
                              "blocked, saturated or polluted buildings, depleted deposits, broken/dormant/keep-all routes and "
                              "accumulating inventories." + COMMON_SUFFIX),
                 scope="state", kind="runtime", list_tool=True, fields_param=False,
                 params={"company": company_param, "product": {"type": "string"},
                         "kinds": {"type": "array", "items": {"type": "string", "enum": ISSUE_KINDS}, "uniqueItems": True}},
                 handler=find_production_issues),
    ]
