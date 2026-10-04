"""ToolSpecs of the V1.1 advisor tools (PRD addendum section 6, phase-2 addendum section 2).

Every advisor tool accepts `detail` (summary/standard/full) and `language` (en/fr/both), applied uniformly after
the handler by `presentation.present`. Descriptions start with a role tag and say when (not) to use the tool
(phase-2 addendum section 6). Calculation helpers stay internal.
"""

from __future__ import annotations

from typing import Callable

from ..app import COMMON_SUFFIX, CallContext, ToolSpec
from ..tools.common import MAX_SEND_SEMANTICS
from . import economics as eco
from .common import DESCRIPTION_SUFFIX, SEVERITIES
from .presentation import PARAMS as PRESENTATION_PARAMS, present
from .tools_calculators import forecast, loan_calculator, route_calculator, spatial_analysis
from .tools_compare import KINDS as COMPARE_KINDS, compare_options
from .tools_economy import OPP_TYPES, find_opportunities, get_profitability, plan_chain
from .tools_graph import get_chain_graph
from .tools_knowledge import explain_mechanic, how_to
from .tools_logistics import review_routes
from .tools_overview import diagnose_chain, get_overview
from .tools_research import research_path, suggest_research
from .tools_scenario import CHANGE_FIELDS, what_changed, what_if

S = {"type": "string"}
NB = {"type": "string", "pattern": r"\S"}   # nested strings: non-blank (PRD 13.2)


def _int(lo: int, hi: int, default: int, desc: str = "") -> dict:
    out = {"type": "integer", "minimum": lo, "maximum": hi, "default": default}
    if desc:
        out["description"] = desc
    return out


CHANGE_PROPS = {
    "type": {"type": "string", "enum": list(CHANGE_FIELDS)},
    "recipe": NB, "building_type": NB, "building": NB, "route": NB, "loan": NB,
    "count": {"type": "integer", "minimum": 1, "maximum": 50},
    "index": {"type": "integer", "minimum": 0, "maximum": 20},
    "value": {"type": "integer", "minimum": 0, "maximum": 1000000},
    "keep_all": {"type": "boolean"},
}
CHANGE_SCHEMA = {
    "type": "object",
    "description": "One hypothetical change. type and its fields: " + "; ".join(
        f"{t}: {sorted(req)}" + (f" (+ optional {sorted(opt)})" if opt else "") for t, (req, opt) in CHANGE_FIELDS.items()),
    "properties": CHANGE_PROPS, "required": ["type"], "additionalProperties": False,
}
LOCATIONS = {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 30}


def _presented(handler: Callable[[CallContext], dict]) -> Callable[[CallContext], dict]:
    def run(ctx: CallContext) -> dict:
        data = handler(ctx)
        static_lang = ctx.static_index().data.get("language") if "static" in ctx.used else None
        return present(data, ctx.args.get("detail") or "standard", ctx.args.get("language") or "en", static_lang)
    run.__name__ = handler.__name__
    return run


def _spec(name: str, role: str, use: str, not_for: str, scope: str, params: dict, handler, *, required=(), list_tool=False,
          kind="runtime", extra: str = "") -> ToolSpec:
    suffix = COMMON_SUFFIX if kind == "knowledge" else DESCRIPTION_SUFFIX
    desc = f"[{role}] {use} Not for: {not_for}{extra}{suffix}"
    return ToolSpec(name=name, description=desc, scope=scope, kind=kind, params={**params, **PRESENTATION_PARAMS},
                    handler=_presented(handler), required=tuple(required), list_tool=list_tool, fields_param=False)


def specs() -> list[ToolSpec]:
    return [
        _spec("get_overview", "OVERVIEW",
              "Use first for 'how is my company doing / what needs attention': cash, loans, last month's finances, buildings "
              "by status, production deficits/surpluses (estimates), logistics and research summary, issue counts and ranked "
              "attention items naming the tool to call next.",
              "the cause of one problem (diagnose_chain, review_routes) or monthly details (get_finances).", "state+history",
              {"max_attention": _int(1, 10, 10, "maximum number of attention items")}, get_overview),
        _spec("diagnose_chain", "DIAGNOSIS",
              "Use for 'why is production of product X low/stuck': walks the player's chain for X upstream (recipe inputs, depth "
              "default 3) and returns ranked findings with evidence (stopped producers, missing inputs, blocked inbound routes, "
              "outputs piling up while demand is unmet).",
              "logistics-wide checks (review_routes) or building plans (plan_chain).", "state",
              {"product": {"type": "string", "description": "product id or name"}, "depth": _int(1, 6, 3)}, diagnose_chain,
              required=("product",)),
        _spec("get_profitability", "DIAGNOSIS",
              "Use for 'which products make money': the game's own product statistics (observed, from history) beside an "
              "advisor estimate of sale price, unit cost (upkeep share + inputs), distribution cost and margin per unit and "
              "per month. fresh refreshes history; prices and upkeep come from the latest state snapshot.",
              "company-wide monthly accounts (get_finances) or new products (find_opportunities).", "state+history",
              {"product": {"type": "string"},
               "input_cost_basis": {"type": "string", "enum": ["market_value", "own_cost"], "default": "market_value"},
               "limit": _int(1, 25, 10)}, get_profitability),
        _spec("find_opportunities", "PLANNING",
              "Use for 'what should I produce or sell more of': ranked candidates from unmet shop demand (route spare supply, "
              "expand production, new unlocked products, research unlocks with include_locked, city contract offers) with "
              "estimated monthly margin, capex and payback. Competition is not modelled.",
              "the build list of a chosen target (plan_chain) or research ranking (suggest_research).", "state",
              {"types": {"type": "array", "items": {"type": "string", "enum": list(OPP_TYPES)}, "uniqueItems": True, "minItems": 1},
               "include_locked": {"type": "boolean", "default": False},
               "include_unviable": {"type": "boolean", "default": False,
                                    "description": "also list candidates whose estimated monthly margin is not positive"},
               "max_buildings": _int(1, 20, 3)}, find_opportunities, list_tool=True),
        _spec("review_routes", "DIAGNOSIS",
              "Use for 'are my routes OK': findings over the player's configured routes (errors, keep-all Min Keep, rejected "
              "products, paused, dormant AUTO_WH, zero dispatch now, saturated or shared Max Send, high transport cost "
              "relative to value, underfilled dispatches, duplicates), each with evidence and a suggested player action.",
              "a route that does not exist yet (route_calculator) or one route's raw fields (get_route).", "state",
              {"product": S, "origin": S, "destination": S,
               "kinds": {"type": "array", "items": {"type": "string", "enum": list(eco.ROUTE_FINDING_KINDS)}, "uniqueItems": True, "minItems": 1},
               "min_severity": {"type": "string", "enum": list(SEVERITIES), "default": "low"},
               "cost_ratio_threshold": {"type": "number", "minimum": 0.01, "maximum": 5, "default": 0.2}},
              review_routes, list_tool=True, extra=" " + MAX_SEND_SEMANTICS),
        _spec("plan_chain", "PLANNING",
              "Use for 'what do I need to build for N of product X per month': recursive requirements through the recipe chain, "
              "spare existing supply used first, buildings per step, capex, monthly upkeep, raw inputs, locked technologies. "
              "Computed on copies; logistics are not planned.",
              "evaluating one specific change (what_if) or comparing recipes (compare_options).", "state",
              {"product": S, "target_per_month": {"type": "number", "exclusiveMinimum": 0, "maximum": 1e6},
               "existing_capacity": {"type": "string", "enum": ["spare", "none"], "default": "spare"},
               "recipe_choice": {"type": "object", "additionalProperties": {"type": "string", "pattern": r"\S"}, "maxProperties": 50},
               "max_depth": _int(1, 12, 8)}, plan_chain, required=("product", "target_per_month")),
        _spec("what_if", "SIMULATION",
              "Use for 'what happens if I ...' with ONE change: add_buildings, remove_building, set_efficiency, change_recipe, "
              "set_max_send (shared by every origin to that destination), set_min_keep, take_loan. Returns baseline, scenario, "
              "deltas and side effects. Nothing is changed in the game.",
              "several alternatives side by side (compare_options with kind=scenarios) or loan schedules (loan_calculator).",
              "state+history", {"change": CHANGE_SCHEMA}, what_if, required=("change",)),
        _spec("what_changed", "CHANGES",
              "Use for 'what changed since I last looked': (short_term) between an earlier state snapshot this server loaded "
              "in the current world session (since_seq = an earlier meta.snapshot.seq) and now; (monthly) between the last two "
              "complete months of game-retained history. No background sampling.",
              "projections (forecast) or the full monthly ledger (get_finances).", "state+history",
              {"since_seq": {"type": "integer", "minimum": 0},
               "horizon": {"type": "string", "enum": ["short_term", "monthly", "both"], "default": "both"},
               "limit": _int(1, 50, 25)}, what_changed),
        _spec("explain_mechanic", "MECHANICS",
              "Use for 'how does <game mechanic> work': curated, evidence-backed explanation (formula, verification status, "
              "caveats, related pitfalls, glossary terms, matching catalogue names in the game language and English). "
              "Resources: roi://knowledge/mechanics, /glossary, /pitfalls, /how-to.",
              "live values of the player's game (V1 tools) or step-by-step actions (how_to).", "none",
              {"topic": S}, explain_mechanic, required=("topic",), kind="knowledge"),
        _spec("how_to", "MECHANICS",
              "Use for 'how do I do X': curated steps in the game UI (the player does it; this MCP cannot) or with this MCP's "
              "tools, with evidence and verification status.",
              "explanations of game rules (explain_mechanic).", "none",
              {"action": S}, how_to, required=("action",), kind="knowledge"),
        # ------------------------------------------------------------------ phase 2
        _spec("research_path", "PLANNING",
              "Use for 'how do I get to technology T': missing prerequisites in research order, already-unlocked nodes, per-node "
              "and total research cost and days (the game's values where exported, else catalogue formulas calibrated on them), "
              "queue ahead and estimated completion. The research graph has no alternative paths (prerequisites are all required).",
              "choosing what to research (suggest_research).", "state",
              {"target": {"type": "string", "description": "tech id or name"}}, research_path, required=("target",)),
        _spec("suggest_research", "PLANNING",
              "Use for 'what should I research next': ranks researchable technologies by an explicit score "
              "(demand value + bottleneck value) x (0.5 + 0.5 x chain fit) / research cost, returning every component.",
              "the path to one known target (research_path).", "state",
              {"include_reachable": {"type": "boolean", "default": False,
                                     "description": "also rank nodes whose missing prerequisite chain has at most max_chain nodes"},
               "max_chain": _int(1, 6, 3)}, suggest_research, list_tool=True),
        _spec("loan_calculator", "CALCULATOR",
              "Use for loan maths: payment, schedule, total repayment, financing cost (flat interest, the game's verified loan "
              "formula) for a catalogue loan or explicit terms, compared with last month's net and cash; existing=true lists "
              "the player's loans with remaining payments.",
              "the effect of a loan on the whole company (what_if with take_loan).", "state+history",
              {"loan": S, "principal": {"type": "number", "exclusiveMinimum": 0, "maximum": 1e12},
               "apr": {"type": "number", "minimum": 0, "maximum": 10},
               "duration_months": {"type": "integer", "minimum": 1, "maximum": 1200},
               "grace_months": {"type": "integer", "minimum": 0, "maximum": 600},
               "modifier": {"type": "number", "minimum": 0.01, "maximum": 100}, "existing": {"type": "boolean", "default": False}},
              loan_calculator),
        _spec("route_calculator", "CALCULATOR",
              "Use for a route that may not exist yet (origin -> destination): straight-line distance (NOT a road path), a path "
              "range from your own routes' observed detour factor, estimated cost per trip and per unit, and derivable "
              "throughput limits (supply, demand, trips). Shows the game's own values when the route exists.",
              "reviewing existing routes (review_routes) or ranking shops (compare_options kind=shop_destinations).", "state",
              {"origin": S, "destination": S, "product": S,
               "source": {"type": "string", "enum": ["own", "TruckDepot", "TrainTerminal"], "default": "own"},
               "vehicle_capacity": {"type": "integer", "minimum": 1, "maximum": 100000}},
              route_calculator, required=("origin", "destination")),
        _spec("compare_options", "COMPARISON",
              "Use to compare 2-8 options of ONE kind side by side on the metrics defined for that kind: recipes (sharing a "
              "product), building_types, supply_sources (to a destination), shop_destinations (from an origin), research nodes, "
              "or 2-5 what_if scenarios. Metrics missing for an option are listed, never invented.",
              "a single option (use the dedicated tool) or mixed kinds.", "state+history",
              {"kind": {"type": "string", "enum": list(COMPARE_KINDS)},
               "options": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 8},
               "scenarios": {"type": "array", "items": CHANGE_SCHEMA, "minItems": 2, "maxItems": 5},
               "product": S, "origin": S, "destination": S, "recipe": S},
              compare_options, required=("kind",)),
        _spec("spatial_analysis", "SPATIAL",
              "Use for geometry between known locations (buildings, shops, cities, regions): mode=matrix (<= 12 locations), "
              "mode=hub (rank candidate central locations by weighted straight-line distance, plus the geometric median), "
              "mode=chain (supplier -> producer -> outlet legs for a product). All distances are straight-line estimates, "
              "not road paths.",
              "transport costs of one route (route_calculator).", "state",
              {"mode": {"type": "string", "enum": ["matrix", "hub", "chain"]}, "locations": LOCATIONS,
               "candidates": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20},
               "weights": {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1e9}, "maxItems": 30},
               "product": S}, spatial_analysis, required=("mode",)),
        _spec("get_chain_graph", "STRUCTURE",
              "Use when you need the production/supply graph of a product as data to reason over: products, recipes, the "
              "player's producers and consumers, inputs/outputs, observed links (buildings running recipes, routes, AUTO_WH, "
              "warehouse requests) distinguished from catalogue and hypothetical recipe links; optional Mermaid text.",
              "bottleneck findings (diagnose_chain) or route attributes (list_routes).", "state",
              {"product": S, "depth": _int(1, 8, 4), "format": {"type": "string", "enum": ["json", "mermaid"], "default": "json"}},
              get_chain_graph, required=("product",)),
        _spec("forecast", "FORECAST",
              "Use for limited projections from observed data: kind=cash (months until cash runs out from the last complete "
              "months' net, as a range) or kind=stock (days until a building's stock of a product fills or empties, from this "
              "server's recent snapshots). Answers available=false when the data is insufficient.",
              "what already changed (what_changed) or hypothetical changes (what_if).", "state+history",
              {"kind": {"type": "string", "enum": ["cash", "stock"]}, "months": _int(2, 12, 3), "building": S, "product": S},
              forecast, required=("kind",)),
    ]
