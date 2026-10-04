"""The 29 read-only MCP tools of PRD section 14, plus the 18 V1.1 advisor tools (docs/v1.1/PRD-ADDENDUM.md, PHASE2-ADDENDUM.md)."""

from __future__ import annotations

TOOL_NAMES = (
    # 14.1 status and lookup
    "get_game_status", "search",
    # 14.2 companies and finances
    "list_companies", "get_company", "get_finances",
    # 14.3 buildings and production
    "list_buildings", "get_building", "get_production_overview", "find_production_issues",
    # 14.4 logistics
    "list_routes", "get_route", "list_warehouse_requests", "list_vehicles",
    # 14.5 supply chains
    "get_supply_chain",
    # 14.6 catalogue
    "list_products", "get_product", "list_recipes", "get_recipe", "list_building_types", "get_building_type",
    # 14.7 cities, shops, regions
    "list_cities", "get_city", "get_shop", "find_shops", "list_regions", "get_region",
    # 14.8 market and technology
    "get_market", "get_tech_tree", "get_research_state",
)

# V1.1 advisor tools (PRD addendum section 2.1), registered after the V1 tools in this order.
ADVISOR_PHASE1_TOOL_NAMES = (
    "get_overview", "diagnose_chain", "get_profitability", "find_opportunities", "review_routes",
    "plan_chain", "what_if", "what_changed", "explain_mechanic", "how_to",
)
# V1.1 phase 2 (docs/v1.1/PHASE2-ADDENDUM.md section 2.1).
ADVISOR_PHASE2_TOOL_NAMES = (
    "research_path", "suggest_research", "loan_calculator", "route_calculator", "compare_options",
    "spatial_analysis", "get_chain_graph", "forecast",
)
ADVISOR_TOOL_NAMES = ADVISOR_PHASE1_TOOL_NAMES + ADVISOR_PHASE2_TOOL_NAMES

ALL_TOOL_NAMES = TOOL_NAMES + ADVISOR_TOOL_NAMES

# PRD 13.7 normative refresh scope per tool (V1.1: addendum section 5).
REFRESH_SCOPE = {name: "state" for name in ALL_TOOL_NAMES}
REFRESH_SCOPE.update({"get_game_status": "none", "get_recipe": "none", "get_finances": "history",
                      "explain_mechanic": "none", "how_to": "none"})
# Advisor tools that read both state and history refresh both (live validation V1.1: the observer re-verifies
# history only on request, so a state-only refresh left these answers flagged stale with low confidence).
REFRESH_SCOPE.update({n: "state+history" for n in ("get_overview", "get_profitability", "what_if", "what_changed",
                                                     "loan_calculator", "compare_options", "forecast")})


def build_specs():
    from . import buildings, catalog, company, logistics, market, status, supply, world
    from ..advisor import tools as advisor_tools

    specs = []
    for mod in (status, company, buildings, logistics, supply, catalog, world, market):
        specs.extend(mod.specs())
    v1 = {s.name: s for s in specs}
    assert len(specs) == len(v1) == 29
    advisor = {s.name: s for s in advisor_tools.specs()}
    by_name = {**v1, **advisor}
    ordered = [by_name[n] for n in ALL_TOOL_NAMES]
    for s in ordered:
        assert s.scope == REFRESH_SCOPE[s.name], (s.name, s.scope)
    assert len(ordered) == len(by_name) == 47
    return ordered
