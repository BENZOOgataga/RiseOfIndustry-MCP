"""Response-schema source for the ten V1.1 advisor tools (merged into roi_mcp.response_schemas).

The advisor part of `data` is strict (PRD addendum section 3); tool-specific `result` objects require their
top-level keys and type the Quantity values they contain.
"""

from __future__ import annotations

from .contract import DEGRADED_REASONS, FACTORS, KINDS, LEVELS, UNITS

S = {"type": "string"}
I = {"type": "integer"}
N = {"type": "number"}
B = {"type": "boolean"}
O = {"type": "object"}
A = {"type": "array"}


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


R = {k: {"$ref": f"#/$defs/{k}"} for k in ("Quantity", "Confidence", "ProductRef", "BuildingRef", "Unavailable", "Provenance")}
Q = R["Quantity"]
QN = null(Q)

ADVISOR_DEFS = {
    "Confidence": obj({"level": {"enum": list(LEVELS)}, "factors": arr({"enum": list(FACTORS)})}, closed=True),
    "Quantity": {
        "type": "object",
        "properties": {"value": null(N), "unit": {"enum": list(UNITS)}, "kind": {"enum": list(KINDS)}, "method": S, "source": S,
                       "confidence": {"$ref": "#/$defs/Confidence"}, "basis": null(S)},
        "required": ["value", "unit", "kind"],
        "allOf": [
            {"if": {"properties": {"kind": {"const": "estimate"}}}, "then": {"required": ["method", "confidence"]}},
            {"if": {"properties": {"kind": {"const": "derived"}}}, "then": {"required": ["method"]}},
        ],
    },
    "ProductRef": null(obj({"id": {"type": "string", "pattern": "^product:"}, "display_name": null(S), "english_name": null(S)})),
    "BuildingRef": null(obj({"id": {"type": "string", "pattern": "^building:"}, "name": null(S)})),
    "AdvisorHeader": obj({"contract_version": {"const": "1.1.0"}, "tool": S, "player_only": {"const": True},
                          "read_only": {"const": True}}, closed=True),
    "Assumption": obj({"id": {"type": "string", "pattern": "^A-[A-Z0-9-]+$"}, "text": S}, closed=True),
    "Degraded": obj({"input": S, "reason": {"enum": list(DEGRADED_REASONS)}, "effect": S}, closed=True),
    "Contradiction": obj({"kind": S, "subject": S, "values": O, "effect": S}, closed=True),
    "BasisSnapshot": obj({"family": {"enum": ["state", "history", "static"]}, "seq": null(I), "content_hash": null(S),
                          "world_session": null(S), "game_date": null(S), "captured_utc": null(S), "age_s": null(N),
                          "stale": B, "stale_reason": null(S)}, closed=True),
    "Basis": null({"anyOf": [
        obj({"snapshots": arr({"$ref": "#/$defs/BasisSnapshot"}),
             "consistency": obj({"same_world_session": B, "history_same_session": null(B),
                                 "static_ref_matches": null(B), "state_consistent": null(B)}),
             "freshness": obj({"state_age_s": null(N), "history_age_s": null(N), "state_game_date": null(S),
                               "history_game_date": null(S)})}),
        # detail=summary (phase-2 addendum 3)
        obj({"snapshots": arr(obj({"family": {"enum": ["state", "history", "static"]}, "seq": null(I), "game_date": null(S),
                                   "age_s": null(N), "stale": B}, closed=True))}, closed=True)]}),
    "Finding": obj({"rank": I, "severity": {"enum": ["high", "medium", "low", "info"]}, "kind": S, "evidence": O,
                    "confidence": {"$ref": "#/$defs/Confidence"}}),
}


def advisor_data(result: dict) -> dict:
    return obj({
        "advisor": {"$ref": "#/$defs/AdvisorHeader"},
        "result": result,
        "observed": O,
        "assumptions": arr({"$ref": "#/$defs/Assumption"}),
        "calculation_inputs": O,
        "confidence": {"$ref": "#/$defs/Confidence"},
        "degraded_inputs": arr({"$ref": "#/$defs/Degraded"}),
        "contradictions": arr({"$ref": "#/$defs/Contradiction"}),
        "basis": {"$ref": "#/$defs/Basis"},
        "provenance": R["Provenance"],
        "unavailable": R["Unavailable"],
    }, {"contradictions_total": I, "applies_to": O, "detail": {"enum": ["summary", "standard", "full"]},
        "language": {"enum": ["en", "fr", "both"]}, "text_translation": O})


FINDING = {"$ref": "#/$defs/Finding"}
MONTH = null(obj({"month": null(S), "income": Q, "expense": Q, "net": Q}))
UNAV = obj({"available": {"const": False}})

ADVISOR_DATA = {
    "get_overview": advisor_data(obj({
        "company": obj({"id": S, "name": null(S)}), "cash": Q,
        "loans": null(obj({"count": I, "principal_total": Q, "monthly_payment_total": Q})),
        "finances": {"anyOf": [obj({"available": {"const": True}, "last_complete_month": MONTH, "month_to_date": MONTH},
                                   {"previous_complete_month": MONTH, "net_change_vs_previous": Q}),
                               obj({"available": {"const": False}, "reason": S})]},
        "buildings": O, "production": O, "logistics": O, "research": null(O), "issue_counts": O,
        "attention": arr(obj({"rank": I, "severity": {"enum": ["high", "medium", "low", "info"]}, "kind": S, "subject": S,
                              "summary": S, "evidence": O, "next_tool": S}))})),
    "diagnose_chain": advisor_data(obj({
        "product": R["ProductRef"], "depth": I,
        "levels": arr(obj({"product": R["ProductRef"], "depth": I, "supply_per_30d": Q, "internal_need_per_30d": Q,
                           "shop_demand_per_30d": QN, "stock": Q, "recipe": null(S), "producers": A})),
        "findings": arr(FINDING), "findings_total": I, "healthy": B})),
    "get_profitability": advisor_data(obj({
        "products": arr(obj({"product": R["ProductRef"], "game_stats": null(O),
                             "estimate": obj({"sale_price": Q, "unit_cost": obj({"upkeep_per_unit": Q, "inputs_per_unit": Q, "total": Q}),
                                              "distribution_cost_per_unit": Q, "margin_per_unit": Q, "volume_per_30d": Q,
                                              "margin_per_30d": Q}),
                             "confidence": {"$ref": "#/$defs/Confidence"}})),
        "products_total": I, "input_cost_basis": {"enum": ["market_value", "own_cost"]}, "company_month": MONTH})),
    "find_opportunities": advisor_data(obj({"opportunities": arr(obj({
        "rank": I, "type": {"enum": ["route_surplus", "expand_production", "new_product", "research_unlock", "contract_offer"]},
        "product": R["ProductRef"], "volume_per_30d": QN, "margin_per_unit": QN, "monthly_margin": QN, "capex": QN,
        "payback_months": QN, "buildings_needed": null(I), "building_type": null(S), "recipe": null(S), "locked_by": arr(S),
        "evidence": O, "confidence": {"$ref": "#/$defs/Confidence"}, "summary": S, "viable": null(B),
        "caveats": arr({"enum": ["needs_new_supply_chain", "long_payback"]})}))})),
    "review_routes": advisor_data(obj({
        "summary": obj({"routes_reviewed": I, "findings": I, "findings_by_kind": O, "findings_by_severity": O, "routes_with_findings": I}),
        "findings": arr(obj({"rank": I, "severity": {"enum": ["high", "medium", "low", "info"]}, "kind": S,
                             "route_id": {"type": "string", "pattern": "^route:"}, "origin": R["BuildingRef"],
                             "destination": R["BuildingRef"], "product": R["ProductRef"], "evidence": O, "suggestion": S,
                             "confidence": {"$ref": "#/$defs/Confidence"}}))})),
    "plan_chain": advisor_data(obj({
        "target": obj({"product": R["ProductRef"], "per_month": Q}), "existing_capacity": {"enum": ["spare", "none"]},
        "steps": arr(obj({"product": R["ProductRef"], "depth": I,
                          "status": {"enum": ["ok", "uses_existing", "unplannable", "cycle", "truncated"]},
                          "required_per_30d": Q, "from_existing_per_30d": Q, "new_per_30d": Q, "recipe": null(S),
                          "building_type": null(S), "buildings": null(I), "rate_per_building": QN, "capex": QN,
                          "upkeep_per_30d": QN, "locked_by": arr(S)})),
        "steps_total": I, "cycles": A,
        "totals": obj({"buildings": I, "capex": Q, "upkeep_per_30d": Q, "raw_inputs_per_30d": O, "locked_by": arr(S), "complete": B})})),
    "what_if": advisor_data(obj({
        "change": obj({"type": S}), "applied": {"const": False}, "type": S, "baseline": O, "scenario": O, "deltas": O,
        "side_effects": arr(obj({"kind": S})), "note": S})),
    "what_changed": advisor_data(obj({
        "short_term": obj({"available": B}, {"changes": arr(obj({"kind": S, "subject": S, "basis": S}))}),
        "monthly": obj({"available": B}, {"changes": arr(obj({"kind": S, "subject": S, "basis": S}))})})),
    "explain_mechanic": advisor_data(obj({
        "topic": S,
        "matches": arr(obj({"id": S, "title": S, "summary": S, "formula": null(S),
                            "evidence": arr(obj({"file": S, "location": S, "note": S})),
                            "verification": {"enum": ["CONFIRMED_IN_GAME", "CONFIRMED_SOURCE", "HIGH_CONFIDENCE", "INFERRED", "UNKNOWN"]},
                            "confidence": {"$ref": "#/$defs/Confidence"}, "resource_uri": S})),
        "glossary": A, "catalog_terms": A, "pitfalls": A})),
    "how_to": advisor_data(obj({
        "action": S,
        "matches": arr(obj({"id": S, "title": S, "audience": {"enum": ["player_in_game", "mcp_client"]}, "steps": arr(S),
                            "evidence": A, "verification": S, "confidence": {"$ref": "#/$defs/Confidence"}})),
        "resource_uri": S})),
}


QL = arr(Q)
ADVISOR_DATA.update({
    "research_path": advisor_data(obj({
        "target": obj({"tech": {"type": "string", "pattern": "^tech:"}, "tier": null(I), "teaser": B, "already_unlocked": B}),
        "remaining": arr(obj({"tech": S, "status": {"enum": ["active", "queued", "not_queued"]}, "basis": null(S),
                              "days": Q, "cost": Q, "daily_cost": Q})),
        "remaining_count": I, "already_unlocked": arr(S),
        "totals": obj({"days": Q, "cost": Q, "known_partial_days": Q, "known_partial_cost": Q, "complete": B}),
        "queue_ahead": arr(S), "estimated_completion_days": Q, "alternatives": {"type": "array", "maxItems": 0},
        "alternatives_reason": S, "cycles": A})),
    "suggest_research": advisor_data(obj({"suggestions": arr(obj({
        "rank": I, "tech": S, "available_now": B, "chain_length": I, "chain": arr(S), "unlocks_recipes": arr(S),
        "unlocks_products": arr(R["ProductRef"]),
        "components": obj({"demand_value_per_30d": Q, "bottleneck_value_per_30d": Q, "chain_fit": Q, "research_cost": Q,
                           "research_days": Q}),
        "score": Q, "unscored_reason": null(S), "confidence": {"$ref": "#/$defs/Confidence"}}))})),
    "loan_calculator": advisor_data(obj({}, {
        "loan": obj({"inputs": O, "payment": Q, "total_repayment": Q, "financing_cost": Q, "first_payment_month_offset": I,
                     "last_payment_month_offset": I,
                     "schedule": arr(obj({"payment_number": I, "month_offset": I, "payment": Q, "remaining_balance_after": Q,
                                          "early_repay_after": Q})),
                     "schedule_rows_omitted": I, "cash_flow": O, "caveat": S}),
        "existing_loans": arr(obj({"principal": Q, "payment": Q, "remaining_payments": Q, "early_repay_amount": Q})),
        "max_loans": null(I)})),
    "route_calculator": advisor_data(obj({
        "origin": R["BuildingRef"], "destination": R["BuildingRef"], "product": R["ProductRef"],
        "source": {"enum": ["own", "TruckDepot", "TrainTerminal"]},
        "straight_line": obj({"euclidean": Q, "chebyshev": Q, "distance_kind": {"const": "straight_line"},
                              "is_path_distance": {"const": False}}),
        "path_tiles_range_estimate": null(obj({"min": Q, "median": Q, "max": Q, "samples": I})),
        "existing_route": null(obj({"route_id": S, "authoritative": {"const": True}, "distance_tiles": Q, "dispatch_cost": Q})),
        "cost_per_trip": obj({"at_straight_line": Q, "formula": S}), "vehicle_capacity": Q, "cost_per_unit_at_capacity": Q,
        "throughput": null(O), "warning": S})),
    "compare_options": advisor_data(obj({
        "kind": {"enum": ["recipes", "building_types", "supply_sources", "shop_destinations", "research", "scenarios"]},
        "context": O,
        "metrics": arr(obj({"id": S, "label": S, "unit": S, "better": {"enum": ["higher", "lower", "none"]}}, closed=True)),
        "options": arr(obj({"id": S, "values": {"type": "object", "additionalProperties": QN}, "unavailable": arr(S)})),
        "differences": arr(obj({"metric": S, "better": S, "best_option": S, "worst_option": S, "spread": Q, "ranking": arr(S),
                                "compared_options": I}))})),
    "spatial_analysis": advisor_data(obj({"mode": {"enum": ["matrix", "hub", "chain"]},
                                          "distance_kind": {"const": "straight_line"}, "is_path_distance": {"const": False}},
                                         {"pairs": arr(obj({"from": S, "to": S, "euclidean": Q})),
                                          "ranking": arr(obj({"rank": I, "id": S, "weighted_distance_sum": Q})),
                                          "geometric_median": O, "producers": A})),
    "get_chain_graph": advisor_data(obj({
        "product": {"type": "string", "pattern": "^product:"}, "depth": I,
        "nodes": arr(obj({"id": S, "kind": {"enum": ["product", "recipe", "building"]}})),
        "edges": arr(obj({"from": S, "to": S, "relation": S, "basis": {"enum": ["observed", "catalogue", "hypothetical"]}})),
        "counts": O, "truncated": obj({"nodes": I, "edges": I}), "basis_legend": O}, {"mermaid": S})),
    "forecast": advisor_data(obj({"kind": {"enum": ["cash", "stock"]}, "available": B})),
})
