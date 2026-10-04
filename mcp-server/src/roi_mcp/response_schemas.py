"""Source of the per-tool response JSON Schemas (`schemas/tool-responses/<tool>.schema.json`).

The committed schema files are generated from this module:

    uv run --directory mcp-server python -m roi_mcp.response_schemas ../schemas/tool-responses

A test asserts the committed files equal the generated ones, and every tool response in the test
suite is validated against its file. Top-level `data` keys are normative (PRD 14); nested objects
allow additional properties unless stated.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .errors import ERROR_CODES, WARNING_CODES
from .advisor.response_schemas import ADVISOR_DATA, ADVISOR_DEFS
from .tools import ADVISOR_TOOL_NAMES, ALL_TOOL_NAMES, TOOL_NAMES

S = {"type": "string"}
I = {"type": "integer"}
N = {"type": "number"}
B = {"type": "boolean"}
O = {"type": "object"}
A = {"type": "array"}
ANY = {}


def null(x: dict) -> dict:
    return {"anyOf": [x, {"type": "null"}]}


def obj(required: dict, optional: dict | None = None, closed: bool = False) -> dict:
    props = dict(required)
    props.update(optional or {})
    out = {"type": "object", "properties": props, "required": list(required)}
    if closed:
        out["additionalProperties"] = False
    return out


def arr(items: dict) -> dict:
    return {"type": "array", "items": items}


def idp(kind: str) -> dict:
    return {"type": "string", "pattern": f"^{kind}:"}


REF = {k: {"$ref": f"#/$defs/{k}"} for k in ("Provenance", "Unavailable", "ActorRef", "Ref", "Coords", "MaxSend", "MinKeep",
                                            "RouteCompact", "Method")}

COMMON_DEFS = {
    "Warning": obj({"code": {"enum": list(WARNING_CODES)}, "detail": null(S)}),
    "SnapshotRef": obj({"family": {"enum": ["state", "history", "static"]}, "seq": null(I), "captured_utc": null(S),
                        "age_s": null(N), "game_date": null(S), "consistent": null(B), "sections_used": arr(S),
                        "sections_unavailable": arr(S)}),
    "Meta": obj({
        "server_version": S, "schema_versions": O,
        "game_state": null({"enum": ["ready", "game_not_running", "observer_not_detected", "starting", "observer_unresponsive",
                                     "menu", "loading", "disabled", "unsupported_build", "faulted"]}),
        "compatibility": null(S), "world_session": null(S),
        "source": {"enum": ["live_snapshot", "static_catalog", "stale_snapshot", "none"]},
        "snapshot": null({"$ref": "#/$defs/SnapshotRef"}), "snapshots": arr({"$ref": "#/$defs/SnapshotRef"}),
        "paused": null(B), "stale": B,
        "stale_reason": null({"enum": ["age", "world_session_changed", "not_in_game", "game_not_running", "observer_unresponsive"]}),
        "warnings": arr({"$ref": "#/$defs/Warning"}),
    }),
    "Page": obj({"next_cursor": null(S), "total": null(I)}),
    "Provenance": obj({"observed": obj({"fields": arr(S), "persistence": O}), "definition": arr(S), "game_computed": arr(S),
                       "derived": arr(obj({"field": S, "method": null(S)}))}),
    "Unavailable": arr(obj({"field": S, "reason": S}, closed=True)),
    "ActorRef": null(obj({"actor_id": I, "kind": {"enum": ["company", "city", "state", "other"]}})),
    "Ref": null(obj({"id": S})),
    "Coords": null(obj({"x": null(N), "y": null(N)})),
    "Method": obj({"method": S}),
    "MaxSend": obj({"value": I, "unlimited": B, "mode": {"enum": ["manual", "auto_shop_demand"]},
                    "scope": {"const": "destination_product_shared"}, "headroom_now": null(I), "ui_label_validated": B}),
    "MinKeep": obj({"value": I, "keep_all": B, "ui_label_validated": B}),
    "RouteCompact": obj({
        "route_id": idp("route"),
        "origin": obj({"id": idp("building"), "name": null(S), "coordinates": REF["Coords"]}),
        "destination": obj({"id": idp("building"), "name": null(S), "kind": S, "city": REF["Ref"], "coordinates": REF["Coords"]}),
        "product": idp("product"), "transport_mode": null(S), "max_send": REF["MaxSend"], "min_keep": REF["MinKeep"],
        "distance_tiles": null(I), "dispatch_cost": null(N), "vehicle_capacity": null(I), "dispatch_amount_now": ANY,
        "in_flight": null(O), "paused": B, "dormant": B, "errors": arr(S)}),
}


def data_schema(required: dict, optional: dict | None = None, provenance: bool = True) -> dict:
    req = dict(required)
    if provenance:
        req["provenance"] = REF["Provenance"]
    req["unavailable"] = REF["Unavailable"]
    return obj(req, optional)


ROUTE_FULL = obj({**COMMON_DEFS["RouteCompact"]["properties"],
                  "dispatch_amount_now": obj({"value": null(I), "complete": B, "limited_by": arr(S), "inputs": O, "method": S}),
                  "destination_stock": null(I), "destination_incoming_reserved": null(I), "origin_stock": null(I),
                  "cost_per_unit_at_capacity": null(N), "unit_costs": obj({"method": {"const": "D-ROUTE-1"}}),
                  "straight_line_tiles": obj({"method": {"const": "D-DIST-1"}, "euclidean": null(N), "chebyshev": null(N),
                                              "estimate": {"const": True}})})

ESTIMATE = obj({"value": null(N), "distance_kind": {"const": "straight_line"}, "route_exists": {"enum": [False, None]},
                "authoritative": {"const": False}, "formula": {"const": "ManualDestinationDispatchCost"},
                "method": {"const": "D-ROUTE-2"}})

PRICE_HISTORY = obj({"available": {"const": False}, "reason": S})

DATA: dict[str, dict] = {
    "get_game_status": data_schema({
        "game": obj({"running": B, "pid": null(I), "state": S, "paused": null(B), "speed_level": null(I), "time_scale": null(N),
                     "game_date": null(S), "world_session": null(S), "module": null(O), "language": null(S),
                     "compatibility": null(S), "detected_game": null(O), "expected_game": null(O)}),
        "observer": null(obj({"version": null(S), "last_captures": O, "effective_interval_s": null(N), "degraded": null(B),
                              "disabled_sections": null(A), "reflection_self_check": null(O), "errors_last_hour": null(I)})),
        "server": obj({"version": S, "schema_versions": O}),
        "limitations": arr(S)}),
    "search": data_schema({"results": arr(obj({
        "id": S, "kind": {"enum": ["building", "shop", "building_type", "product", "recipe", "tech", "tech_tree", "bill_category",
                                   "company", "city", "region"]},
        "display_name": null(S), "english_name": null(S), "owner": REF["ActorRef"], "city": REF["Ref"], "coordinates": REF["Coords"],
        "match_kind": {"enum": ["exact", "prefix", "fuzzy"]}, "score": N}))}),
    "list_companies": data_schema({"companies": arr(obj({
        "id": idp("company"), "name": null(S), "is_player": B, "color": null(S), "hq_city": REF["Ref"],
        "cash": {"anyOf": [N, {"type": "null"}, obj({"infinite": {"const": True}})]}, "loans_total": null(N),
        "cashflow_label": null(S), "region_count": I, "building_counts_by_tag": null(O), "main_tech_tree": null(S),
        "shares_owned_by_others": null(I)}))}),
    "get_company": data_schema({
        "identity": obj({"id": idp("company"), "name": null(S), "is_player": B}),
        "cash": {"anyOf": [N, {"type": "null"}, obj({"infinite": {"const": True}})]},
        "loans": arr(obj({"type": null(S), "principal": N, "apr": N, "duration_months": I, "remaining_payments": I,
                          "monthly_payment": obj({"value": null(N), "method": {"const": "D-LOAN-1"}}),
                          "grace_months_left": null(I), "early_repay_amount": N})),
        "shares": null(obj({"bundles": arr(obj({"owner": REF["ActorRef"]})), "owned_by_competitors": null(I)})),
        "value": obj({"method": {"const": "D-VAL-1"}, "value": null(N)}),
        "total_assets": obj({"value": null(N), "method": S}),
        "stats": obj({"cashflow_label": null(S), "top_production": A, "top_sales": A, "owned_permits": A, "main_tech_tree": null(S)}),
        "buildings_summary": obj({"by_type": null(O), "by_tag": null(O)})},
        {"ai": obj({"personality": null(S), "owned_regions": A, "has_initiative": null(B), "product_goals": {"type": "null"}})}),
    "get_finances": data_schema({
        "months": arr(obj({"month": S, "current_month_to_date": B, "income_total": N, "expense_total": N, "net": N,
                           "by_category": arr(obj({"category": S, "income": N, "expense": N, "net": N}))})),
        "month_over_month": arr(obj({"from": S, "to": S, "net_change": N, "method": {"const": "D-FIN-1"}, "by_category": A})),
        "current_month_to_date": null(S),
        "retention": obj({"first_month_available": null(S), "last_month": null(S), "note": S}),
        "balance_now": {"anyOf": [N, {"type": "null"}, obj({"infinite": {"const": True}})]}}),
    "list_buildings": data_schema({"buildings": arr(obj({
        "id": idp("building"), "display_name": null(S), "type": null(S), "owner": REF["ActorRef"], "city": REF["Ref"],
        "region": REF["Ref"], "coordinates": REF["Coords"], "status": {"enum": ["working", "idle", "disabled", "blocked"]},
        "derived_status": S, "recipe": null(S), "produced_last_month": null(N), "max_stock_ratio": null(N),
        "upkeep_monthly": null(N)}))}),
    "get_building": data_schema({
        "identity": obj({"id": idp("building"), "save_guid": null(S), "display_name": null(S), "type": null(S),
                         "english_type_name": null(S), "owner": REF["ActorRef"], "coordinates": REF["Coords"], "rotation": null(I),
                         "region": REF["Ref"], "city": REF["Ref"], "paid_to_build": null(N)}),
        "status": null(obj({"derived_status": S, "notifications": arr(S), "evidence": A, "method": {"const": "D-STATUS-1"}}))},
        {"efficiency": null(O), "upkeep": null(O),
         "production": null(obj({"recipe": null(S), "inputs": A, "outputs": A, "cycle_days_base": null(N),
                                 "cycle_days_effective": null(N), "progress": null(N), "remaining_days": O,
                                 "produced_this_month": null(I), "produced_last_month": null(I), "total_produced": null(I),
                                 "average_10_months": null(N), "uptime_ratio": obj({"value": null(N), "method": {"const": "D-RATE-3"}}),
                                 "theoretical_output_per_30d": REF["Method"]})),
         "inventory": null(arr(obj({"product": S, "role": S, "count": I, "slots": I, "fill_ratio": null(N),
                                    "incoming_reserved": null(I), "outgoing_reserved": null(I), "inbound_cap": null(I)}))),
         "outgoing_routes": arr(ROUTE_FULL), "incoming_routes": arr(ROUTE_FULL), "requests": A, "modules": null(O),
         "history": null(O), "vehicles": O}),
    "get_production_overview": data_schema({"products": arr(obj({
        "product": idp("product"), "producers": arr(obj({"building": idp("building"), "recipe": null(S), "theoretical_per_30d": null(N),
                                                         "produced_last_month": null(N)})),
        "consumers": arr(obj({"building": idp("building"), "recipe": null(S), "theoretical_need_per_30d": null(N)})),
        "theoretical_supply_per_30d": N, "theoretical_demand_internal_per_30d": N, "produced_last_month": N, "stock_total": N,
        "shops_demand_total": null(O), "balance_per_30d": N, "supply_demand_ratio": obj({"method": {"const": "D-SUPDEM-1"}})}))}),
    "find_production_issues": data_schema({"issues": arr(obj({
        "kind": {"enum": ["disabled", "no_recipe", "missing_input", "output_full", "no_modules", "deposit_depleted", "polluted",
                          "requirements_unmet", "route_error", "route_dormant_auto_wh", "route_keep_all", "inventory_accumulating"]},
        "building": idp("building"), "product": null(S), "route": null(S), "evidence": O, "since": {"type": "null"}}))}),
    "list_routes": data_schema({"routes": arr(REF["RouteCompact"]), "semantics": obj({"max_send": S, "min_keep": S})}),
    "get_route": data_schema({**ROUTE_FULL["properties"], "in_flight_requests": null(O), "vehicles_assigned": A,
                              "semantics": obj({"max_send": S, "min_keep": S})}),
    "list_warehouse_requests": data_schema({"requests": arr(obj({
        "request_id": idp("request"), "endpoint": REF["Ref"], "product": null(S),
        "requested_amount": {"anyOf": [I, {"const": "fill"}, {"type": "null"}]}, "remaining": I, "amount_being_moved": I,
        "priority": I, "active": B}))}),
    "list_vehicles": {"allOf": [data_schema({"identity_note": S}, {
        "groups": arr(obj({"owner": REF["ActorRef"], "transport_mode": null(S), "product": null(S), "vehicles": I, "units_in_transit": I})),
        "vehicles": arr(obj({"id": {"type": "string", "pattern": "^vehicle:[^:]+:-?[0-9]+$"}, "prefab": null(S), "transport_mode": null(S),
                             "fleet_building": null(S), "cargo": O, "origin": null(S), "destination": null(S), "going_home": B,
                             "position": O}))}),
        {"anyOf": [{"required": ["groups"]}, {"required": ["vehicles"]}]}]},
    "get_supply_chain": data_schema({
        "mode": {"enum": ["actual", "recipe"]}, "root": S,
        "nodes": arr(obj({"id": S, "node_kind": S})),
        "edges": arr(obj({"from": null(S), "to": S, "product": null(S),
                          "kind": {"enum": ["configured_route", "warehouse", "auto_wh", "observed_in_flight", "recipe"]}})),
        "requirements": null(obj({"method": {"const": "D-REQ-1"}, "products": O})),
        "raw_inputs_total": null(O), "cycles_detected": A, "truncated_at_depth": A}),
    "list_products": data_schema({"products": arr(obj({
        "id": idp("product"), "display_name": null(S), "english_name": null(S), "category": null(S), "base_price": null(N),
        "current_price": null(N), "trend": null(S), "unlocked": null(B)}))}),
    "get_product": data_schema({
        "definition": obj({"id": idp("product"), "display_name": null(S), "english_name": null(S), "category": null(S)}),
        "market": null(obj({"value": N, "price": N, "modifier": N, "trend": S, "final_price_for_player": null(N)})),
        "recipes_producing": A, "recipes_consuming": A, "player_producers": null(A), "player_consumers": null(A),
        "shops_accepting": null(obj({"count": I, "total_demand_per_interval": N, "consumption_interval_days": O})),
        "state_offer": null(obj({"sold_by_state": B, "price": null(N)})), "price_history": PRICE_HISTORY}),
    "list_recipes": data_schema({"recipes": arr(obj({
        "id": idp("recipe"), "display_name": null(S), "english_name": null(S), "inputs": A, "outputs": A, "game_days": N,
        "building_types": arr(S), "unlocked": null(B)}))}),
    "get_recipe": data_schema({
        "definition": obj({"id": idp("recipe"), "inputs": A, "outputs": A, "game_days": N}), "building_types": A,
        "unlocked_by": A, "per_30d": obj({"inputs": A, "outputs": A, "method": {"const": "D-RATE-1"}})}),
    "list_building_types": data_schema({"building_types": arr(obj({
        "id": idp("building_type"), "display_name": null(S), "english_name": null(S), "base_cost": N, "upkeep_monthly_base": null(N),
        "tags": arr(S), "unlocked": null(B)}))}),
    "get_building_type": data_schema({
        "definition": obj({"id": idp("building_type"), "base_cost": N}), "recipes": A, "unlocked_by": A,
        "current_player_cost": null(N), "upkeep_monthly_base": O, "unlocked": null(B)}),
    "list_cities": data_schema({"cities": arr(obj({
        "id": idp("city"), "name": null(S), "tier": null(S), "population": I, "growth_state": null(S), "region": REF["Ref"],
        "shop_count": I}))}),
    "get_city": data_schema({
        "identity": obj({"id": idp("city"), "name": null(S)}), "region": REF["Ref"], "tier": O, "population": I,
        "population_limit": null(I), "growth_state": obj({"method": {"const": "D-GROW-1"}, "value": null(S)}), "dead": B,
        "consumption_interval_days": null(I), "advancement": null(O), "contract_offer": null(O),
        "shops": arr(obj({"id": idp("building")})), "houses_count": I, "demand_unit_note": S}),
    "get_shop": data_schema({
        "identity": obj({"id": idp("building"), "name": null(S)}), "city": REF["Ref"],
        "products": arr(obj({"product": idp("product"), "stock": I, "slots": null(I), "player_delivered_stock": null(I),
                             "demand_raw": I, "demand_for_player": I, "price_for_player": null(N), "price_modifier_pct": null(N),
                             "sold_last_30d": null(I), "unmet_demand": obj({"method": {"const": "D-SHOP-1"}})})),
        "days_to_next_price_update": null(I), "demand_unit_note": S}),
    "find_shops": data_schema({"shops": arr({
        **obj({"shop": idp("building"), "name": null(S), "city": REF["Ref"], "demand_for_player": null(I), "stock": null(I),
               "player_delivered_stock": null(I), "price_for_player": null(N),
               "unmet_demand": obj({"method": {"const": "D-SHOP-1"}}),
               "straight_line_tiles": null(obj({"euclidean": null(N), "chebyshev": null(N), "estimate": {"const": True}})),
               "existing_route_status": {"enum": ["present", "absent", "unavailable", None]},
               "existing_route": null(obj({"route_id": idp("route"), "distance_tiles": null(I), "dispatch_cost": null(N)})),
               "straight_line_cost_estimate": null(ESTIMATE)}),
        # PRD 14.7 / D-ROUTE-2: existing_route_status decides which of the two may be filled.
        "allOf": [
            {"if": {"properties": {"existing_route_status": {"const": "present"}}},
             "then": {"properties": {"existing_route": O, "straight_line_cost_estimate": {"type": "null"}}}},
            {"if": {"properties": {"existing_route_status": {"const": "absent"}}},
             "then": {"properties": {"existing_route": {"type": "null"},
                                     "straight_line_cost_estimate": null(obj({"route_exists": {"const": False}}))}}},
            {"if": {"properties": {"existing_route_status": {"const": "unavailable"}}},
             "then": {"properties": {"existing_route": {"type": "null"},
                                     "straight_line_cost_estimate": null(obj({"route_exists": {"const": None}}))}}},
            {"if": {"properties": {"existing_route_status": {"const": None}}},
             "then": {"properties": {"existing_route": {"type": "null"}, "straight_line_cost_estimate": {"type": "null"}}}},
        ]}),
        "product": idp("product")}),
    "list_regions": data_schema({"regions": arr(obj({
        "id": idp("region"), "name": null(S), "city": REF["Ref"], "owner": REF["ActorRef"],
        "permit_cost": obj({"value": null(N), "method": {"const": "D-PERMIT-1"}}), "resources": A}))}),
    "get_region": data_schema({
        "identity": obj({"id": idp("region"), "name": null(S), "center": O, "tile_count": I}), "city": REF["Ref"],
        "permit": obj({"owner": REF["ActorRef"], "amount_paid": null(N), "cost": obj({"method": {"const": "D-PERMIT-1"}})}),
        "cooldowns": O, "resources": arr(obj({"product": S, "tile_count": null(I), "water_unlimited": B})), "resource_sites": A,
        "building_counts": obj({"player": I, "ai": A})}),
    "get_market": data_schema({
        "prices": arr(obj({"product": idp("product"), "value": N, "price": N, "modifier": N, "trend": S,
                           "final_price_for_player": null(N)})),
        "price_history": PRICE_HISTORY},
        {"state": obj({"sold_products": A, "incoming_trade_allowed": null(B)}),
         "contracts": obj({"player_active": A, "city_offers": A}), "auctions": null(obj({"current": null(O), "queue": A})),
         "next_update_in_days": O}),
    "get_tech_tree": data_schema({
        "trees": arr(obj({"id": idp("tech_tree")})),
        "nodes": arr(obj({"id": idp("tech"), "display_name": null(S), "english_name": null(S), "tier": I, "placements": A,
                          "prerequisites": arr(S), "included": arr(S), "unlocks": obj({"buildings": A, "recipes": A, "price_discounts": A}),
                          "state": null({"enum": ["unlocked", "available", "queued", "researching", "locked", "teaser"]}),
                          "progress": null(N), "research_cost_per_day": null(N), "research_days": null(N)}))}),
    "get_research_state": data_schema({
        "company": idp("company"), "active": null(S), "queue": null(A), "progress": null(N), "remaining_days": null(N),
        "remaining_cost": null(N), "efficiency": null(O), "unlock_points": null(I), "unlocked_count": null(I),
        "total_nodes": null(I)}),
}

assert set(DATA) == set(TOOL_NAMES), set(TOOL_NAMES) ^ set(DATA)
assert set(ADVISOR_DATA) == set(ADVISOR_TOOL_NAMES), set(ADVISOR_TOOL_NAMES) ^ set(ADVISOR_DATA)
DATA.update(ADVISOR_DATA)


def tool_schema(name: str) -> dict:
    defs = dict(COMMON_DEFS)
    advisor = name in ADVISOR_DATA
    if advisor:
        defs.update(ADVISOR_DEFS)
    defs["Data"] = DATA[name]
    defs["Success"] = obj({"ok": {"const": True}, "meta": {"$ref": "#/$defs/Meta"}, "data": {"$ref": "#/$defs/Data"},
                           "page": {"$ref": "#/$defs/Page"}}, closed=True)
    defs["Error"] = obj({"ok": {"const": False},
                         "error": obj({"code": {"enum": list(ERROR_CODES)}, "message": S, "hint": null(S)},
                                      {"candidates": arr(obj({"id": S, "kind": S})), "details": O}),
                         "meta": {"$ref": "#/$defs/Meta"}}, closed=True)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://roi-mcp.local/schemas/tool-responses/{name}.schema.json",
        "title": f"roi-mcp tool response: {name}",
        "description": "Generated from mcp-server/src/roi_mcp/response_schemas.py (PRD 13.3 envelope + PRD 14 top-level data keys).",
        "x-schema-version": "1.1.0" if advisor else "1.0.0",
        "oneOf": [{"$ref": "#/$defs/Success"}, {"$ref": "#/$defs/Error"}],
        "$defs": defs,
    }


def render(name: str) -> str:
    return json.dumps(tool_schema(name), indent=2, ensure_ascii=False) + "\n"


def write_all(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name in ALL_TOOL_NAMES:
        p = out_dir / f"{name}.schema.json"
        p.write_text(render(name), encoding="utf-8", newline="\n")
        written.append(p)
    return written


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    out = Path(argv[0]) if argv else Path(__file__).resolve().parents[3] / "schemas" / "tool-responses"
    for p in write_all(out):
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
