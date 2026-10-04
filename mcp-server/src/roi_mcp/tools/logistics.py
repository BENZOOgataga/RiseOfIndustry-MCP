"""list_routes, get_route, list_warehouse_requests, list_vehicles (PRD 14.4)."""

from __future__ import annotations

from ..app import COMMON_SUFFIX, CallContext, ToolSpec
from ..errors import ToolError
from ..index import make_id, split_id
from .buildings import _request_row, _vehicle_row
from .common import (DISPATCH_SEMANTICS, MAX_SEND_SEMANTICS, MIN_KEEP_SEMANTICS, ROUTE_PROVENANCE, VEHICLE_IDENTITY_NOTE,
                     check_vehicle_id, provenance, resolve_building_key, resolve_company, resolve_product, route_row,
                     route_semantics)


def _routes_for_company(ctx: CallContext, ix, actor: int) -> list[dict]:
    if actor == ix.player_actor_id:
        # routes_player holds the player's routes only: no owner lookup needed.
        return list(ctx.require_section("state", "routes_player"))
    rows = ctx.require_section("state", "routes_ai")
    out, unknown = [], 0
    for r in rows:
        owner = (ix.building(r["origin"]) or {}).get("owner_actor_id")
        if owner is None:
            unknown += 1
        elif owner == actor:
            out.append(r)
    if unknown:
        # Never attribute a route whose origin owner is unknown to the requested company.
        ctx.add_unavailable("routes", f"{unknown} AI route(s) omitted: origin owner unknown (AI building section unavailable)")
    return out


def list_routes(ctx: CallContext) -> dict:
    ctx.static(required=False)
    ctx.snapshot("state")
    ix = ctx.state_index()
    a = ctx.args
    actor = resolve_company(ctx, ix, a.get("company"))
    rows = _routes_for_company(ctx, ix, actor)
    if a.get("origin"):
        k = resolve_building_key(ctx, a["origin"], "origin")
        rows = [r for r in rows if r["origin"] == k]
    if a.get("destination"):
        k = resolve_building_key(ctx, a["destination"], "destination")
        rows = [r for r in rows if r["destination"] == k or r.get("endpoint") == k]
    if a.get("product"):
        p = resolve_product(ctx, a["product"])
        rows = [r for r in rows if r.get("product") == p]
    if a.get("transport_mode"):
        m = a["transport_mode"].casefold()
        rows = [r for r in rows if (r.get("transport_mode") or "").casefold() == m]
    if a.get("include_dormant") is False:
        rows = [r for r in rows if not r.get("dormant_auto_warehouse")]
    if a.get("errors_only"):
        rows = [r for r in rows if r.get("errors") or r.get("has_error")]
    sort = a.get("sort") or "origin"
    big = float("inf")
    if sort == "distance":
        rows.sort(key=lambda r: (r.get("distance_tiles") is None, big if r.get("distance_tiles") is None else r["distance_tiles"],
                                 r["route_key"]))
    elif sort == "dispatch_cost":
        rows.sort(key=lambda r: (r.get("dispatch_cost") is None, big if r.get("dispatch_cost") is None else r["dispatch_cost"],
                                 r["route_key"]))
    elif sort == "product":
        rows.sort(key=lambda r: (r.get("product") or "", r["route_key"]))
    else:
        rows.sort(key=lambda r: (r["origin"], r.get("slot_index") or 0, r["route_key"]))
    out = [route_row(ctx, ix, r, ctx.full) for r in rows]
    data = ctx.paginate("routes", out)
    data["company"] = make_id("company", actor)
    data["semantics"] = route_semantics()
    data["provenance"] = ROUTE_PROVENANCE
    return data


def _resolve_route(ctx: CallContext, ix, value: str) -> dict:
    parsed = split_id(value) if isinstance(value, str) else None
    if parsed is None or parsed[0] != "route":
        raise ToolError("invalid_argument", "route must be a route id (route:<origin>|<product>|<destination>|<source>|<n>)",
                        hint="Get route ids from list_routes or get_building.")
    r = ix.routes.get(parsed[1])
    if r is None:
        raise ToolError("not_found", f"route {value!r} does not exist in the current snapshot",
                        hint="Routes have no native id; the id changes when origin, product, destination or source change. Ids are "
                             "case-sensitive.")
    return r


def get_route(ctx: CallContext) -> dict:
    ctx.static(required=False)
    ctx.snapshot("state", sections=("routes_player",), optional_sections=("vehicles",))
    ix = ctx.state_index()
    r = _resolve_route(ctx, ix, ctx.args["route"])
    row = route_row(ctx, ix, r, full=True)
    ws = ctx.used["state"].snap.world_session
    vehicles = [v for v in (ix.vehicles.get("vehicles_player") or [])
                if v.get("job_origin") == r["origin"] and v.get("job_destination") == r["destination"]
                and v.get("job_product") == r.get("product")]
    row["vehicles_assigned"] = [_vehicle_row(ix, ws, v) for v in vehicles]
    row["in_flight_requests"] = r.get("in_flight")
    if ctx.args.get("include_path"):
        paths = ctx.require_section("state", "route_paths")
        p = next((x for x in paths if x.get("route_key") == r["route_key"]), None)
        row["path"] = p
        if p is None:
            ctx.add_unavailable("path", "no decimated path exported for this route")
    row["semantics"] = route_semantics()
    row["provenance"] = ROUTE_PROVENANCE
    return row


def list_warehouse_requests(ctx: CallContext) -> dict:
    ctx.static(required=False)
    ctx.snapshot("state")
    ix = ctx.state_index()
    a = ctx.args
    actor = resolve_company(ctx, ix, a.get("company"))
    if actor != ix.player_actor_id:
        raise ToolError("section_unavailable", "Warehouse requests are exported for the player only (requests_player).",
                        details={"section": "requests_player", "reason": "ai_requests_not_exported"})
    rows = list(ctx.require_section("state", "requests_player"))
    if a.get("endpoint"):
        k = resolve_building_key(ctx, a["endpoint"], "endpoint")
        rows = [q for q in rows if q["endpoint"] == k]
    if a.get("product"):
        p = resolve_product(ctx, a["product"])
        rows = [q for q in rows if q.get("product") == p]
    rows.sort(key=lambda q: (q["endpoint"], q.get("priority") or 0, q["request_key"]))
    data = ctx.paginate("requests", [_request_row(ix, q) for q in rows])
    data["logistic_requests_enabled"] = ix.session.get("logistic_requests_enabled")
    data["notes"] = {"pull": "Warehouse pull requests: providers are resolved dynamically by the game, so a request has no fixed source.",
                     "fill": "requested_amount 'fill' means the game's int.MaxValue (fill the warehouse)."}
    data["provenance"] = provenance(observed=["requests"], persistence={"requests": "SAVE"})
    return data


def list_vehicles(ctx: CallContext) -> dict:
    ctx.static(required=False)
    ctx.snapshot("state", sections=("vehicles",))
    ix = ctx.state_index()
    a = ctx.args
    ws = ctx.used["state"].snap.world_session
    veh = ix.vehicles
    actor = resolve_company(ctx, ix, a.get("company"))
    product = resolve_product(ctx, a["product"]) if a.get("product") else None
    mode = a.get("transport_mode")
    fleet = resolve_building_key(ctx, a["fleet_building"], "fleet_building") if a.get("fleet_building") else None
    single = None
    if a.get("vehicle"):
        single = check_vehicle_id(a["vehicle"], ws)
    aggregate = a.get("aggregate", True) and single is None
    notes = {"identity": VEHICLE_IDENTITY_NOTE}
    if aggregate:
        groups = [g for g in veh.get("groups") or [] if g.get("owner_actor_id") == actor]
        if mode:
            groups = [g for g in groups if (g.get("transport_mode") or "").casefold() == mode.casefold()]
        if product:
            groups = [g for g in groups if g.get("product") == product]
        rows = [{"owner": ix.actor_ref(g.get("owner_actor_id")), "transport_mode": g.get("transport_mode"),
                 "product": make_id("product", g.get("product")), "vehicles": g.get("vehicles"),
                 "units_in_transit": g.get("units_in_transit")} for g in groups]
        rows.sort(key=lambda g: (g["transport_mode"] or "", g["product"] or ""))
        data = ctx.paginate("groups", rows)
        if actor == ix.player_actor_id:
            fl = [f for f in veh.get("fleets_player") or [] if fleet is None or f.get("building") == fleet]
            data["fleets"] = [{"building": make_id("building", f.get("building")), "vehicle_prefab": f.get("vehicle_prefab"),
                               "transport_mode": f.get("transport_mode"), "active": f.get("active"), "inactive": f.get("inactive"),
                               "max": f.get("max"), "infinite": f.get("infinite")} for f in fl]
        data["total_active_all_owners"] = veh.get("total_active")
    else:
        if actor != ix.player_actor_id:
            raise ToolError("section_unavailable", "Per-vehicle rows are exported for the player only; use aggregate=true.",
                            details={"section": "vehicles.vehicles_player", "reason": "player_only"})
        rows = list(veh.get("vehicles_player") or [])
        if single is not None:
            rows = [v for v in rows if v.get("instance_id") == single]
            if not rows:
                raise ToolError("not_found", f"vehicle {a['vehicle']!r} is not active in the current snapshot")
        if mode:
            rows = [v for v in rows if (v.get("transport_mode") or "").casefold() == mode.casefold()]
        if product:
            rows = [v for v in rows if v.get("product") == product or v.get("job_product") == product]
        if fleet:
            rows = [v for v in rows if v.get("fleet_building") == fleet]
        rows.sort(key=lambda v: v.get("instance_id") or 0)
        data = ctx.paginate("vehicles", [_vehicle_row(ix, ws, v) for v in rows])
    data["identity_note"] = VEHICLE_IDENTITY_NOTE
    data["provenance"] = provenance(observed=["groups", "fleets", "vehicles"], persistence={"vehicles": "RUNTIME"})
    return data


def specs() -> list[ToolSpec]:
    route_desc = MAX_SEND_SEMANTICS + " " + MIN_KEEP_SEMANTICS + " " + DISPATCH_SEMANTICS
    return [
        ToolSpec(name="list_routes",
                 description=("Configured logistics routes (manual destinations) with Max Send, Min Keep, game distance "
                              "(distance_tiles, the game's cached path length) and dispatch cost per vehicle dispatch, vehicle "
                              "capacity, next dispatch amount, in-flight summary, paused/dormant flags and errors. Dormant = "
                              "origin uses AUTO_WH (manual routes inactive). " + route_desc + COMMON_SUFFIX),
                 scope="state", kind="runtime", list_tool=True, sort=("distance", "dispatch_cost", "product", "origin"),
                 params={"origin": {"type": "string"}, "destination": {"type": "string"}, "product": {"type": "string"},
                         "company": {"type": "string", "description": "default the player; AI routes need the optional routes_ai section"},
                         "transport_mode": {"type": "string"}, "include_dormant": {"type": "boolean", "default": True},
                         "errors_only": {"type": "boolean", "default": False}},
                 handler=list_routes),
        ToolSpec(name="get_route",
                 description=("One route in full: Max Send (value, mode, shared scope, headroom), Min Keep, dispatch amount with "
                              "its inputs, destination stock and incoming reservations, origin stock, cost per unit at capacity "
                              "(D-ROUTE-1), straight-line distances (estimates, D-DIST-1), in-flight requests and assigned "
                              "vehicles. " + route_desc + COMMON_SUFFIX),
                 scope="state", kind="runtime",
                 params={"route": {"type": "string", "description": "route id from list_routes"},
                         "include_path": {"type": "boolean", "default": False}},
                 required=("route",), handler=get_route),
        ToolSpec(name="list_warehouse_requests",
                 description=("Warehouse pull requests of the player's endpoints: product, requested amount ('fill' = unlimited), "
                              "remaining, amount being moved, priority, allowed depots, expenses." + COMMON_SUFFIX),
                 scope="state", kind="runtime", list_tool=True,
                 params={"endpoint": {"type": "string"}, "product": {"type": "string"}, "company": {"type": "string"}},
                 handler=list_warehouse_requests),
        ToolSpec(name="list_vehicles",
                 description=("Vehicles in aggregate (per owner x mode x product: count and units in transit; player fleets) or, "
                              "with aggregate=false, per player vehicle (cargo, job origin/destination, position). "
                              + VEHICLE_IDENTITY_NOTE + COMMON_SUFFIX),
                 scope="state", kind="runtime", list_tool=True,
                 params={"company": {"type": "string"}, "transport_mode": {"type": "string"}, "product": {"type": "string"},
                         "fleet_building": {"type": "string"}, "aggregate": {"type": "boolean", "default": True},
                         # PRD 14.4 `vehicle` lookup: another world session's id -> stale_reference (A15).
                         "vehicle": {"type": "string", "description": ("one vehicle id vehicle:<world_session>:<instance id> "
                                                                       "(implies aggregate=false; an id of another world "
                                                                       "session is rejected with stale_reference)")}},
                 handler=list_vehicles),
    ]
