"""get_market, get_tech_tree, get_research_state (PRD 14.8)."""

from __future__ import annotations

from ..app import COMMON_SUFFIX, CallContext, ToolSpec
from ..errors import ToolError
from ..index import make_id
from .common import PRICE_HISTORY, provenance, resolve_company

# PRD-ambiguity: PRD 14.8 writes `include {state, contracts, auctions}`; it is an array of those names
# (default all), like get_building's include[].
MARKET_INCLUDES = ["state", "contracts", "auctions"]
NODE_STATES = ["unlocked", "available", "queued", "researching", "locked", "teaser"]


def _contract(ix, c: dict) -> dict:
    out = dict(c)
    out["id"] = make_id("contract", f"{c.get('issuer_actor_id')}|{c.get('product')}") if c.get("issuer_actor_id") is not None else None
    out["product"] = make_id("product", c.get("product"))
    out["issuer"] = ix.actor_ref(c.get("issuer_actor_id"))
    out["target_building"] = make_id("building", c.get("target_building"))
    return out


def get_market(ctx: CallContext) -> dict:
    ctx.static(required=False)
    ctx.snapshot("state", sections=("market",), optional_sections=("companies",))
    ix = ctx.state_index()
    m = ix.market or {}
    a = ctx.args
    products = None
    if a.get("products"):
        res = ctx.resolver()
        products = {res.resolve(p, ("product",), "products[]").key for p in a["products"]}
    include = set(a.get("include") or MARKET_INCLUDES)
    prices = [{"product": make_id("product", p["product"]), "value": p.get("value"), "price": p.get("price"),
               "modifier": p.get("modifier"), "trend": p.get("trend"), "final_price_for_player": p.get("final_price_for_player")}
              for p in m.get("prices") or [] if products is None or p["product"] in products]
    prices.sort(key=lambda p: p["product"])
    data: dict = {"prices": prices, "price_history": dict(PRICE_HISTORY)}
    interval = ix.session.get("market_update_interval_days")
    since = ix.session.get("market_days_since_update")
    if isinstance(interval, int) and isinstance(since, int):
        data["next_update_in_days"] = {"value": max(interval - since, 0), "method": "market_update_interval_days - market_days_since_update",
                                       "interval_days": interval}
    else:
        ctx.add_unavailable("next_update_in_days", "market update interval or last update day not readable")
    if "state" in include:
        st = m.get("state") or {}
        data["state"] = {"sold_products": [{"product": make_id("product", s["product"]), "price_for_player": s.get("price_for_player")}
                                           for s in st.get("sold") or [] if products is None or s["product"] in products],
                         "incoming_trade_allowed": st.get("incoming_trade_allowed"), "sale_markup": st.get("sale_markup"),
                         "trading_handlers": st.get("trading_handlers"), "purchase_rule": None}
        ctx.add_unavailable("state.purchase_rule", "not exported by the observer")
    if "contracts" in include:
        player = ix.companies.get(ix.player_actor_id) or {}
        active = ((player.get("contracts") or {}).get("active") or [])
        data["contracts"] = {"player_active": [_contract(ix, c) for c in active if products is None or c.get("product") in products],
                             "city_offers": [_contract(ix, c) for c in m.get("city_contract_offers") or []
                                             if products is None or c.get("product") in products]}
    if "auctions" in include:
        au = m.get("auctions")
        if au is None:
            data["auctions"] = None
            ctx.add_unavailable("auctions", "no auction data exported")
        else:
            def fix(x):
                if x is None:
                    return None
                y = dict(x)
                y["highest_bidder"] = ix.actor_ref(x.get("highest_bidder_actor_id"))
                y["region"] = ix.region_ref(x.get("region_id"))
                y["contract_product"] = make_id("product", x.get("contract_product"))
                return y
            data["auctions"] = {"current": fix(au.get("current")), "queue": [fix(q) for q in au.get("queue") or []]}
    data["provenance"] = provenance(observed=["prices", "state", "contracts", "auctions"],
                                    derived=[("next_update_in_days", "interval - days since last update")],
                                    persistence={"prices": "RUNTIME", "contracts": "SAVE", "auctions": "SAVE"})
    return data


def _node_state(u: dict, unlocked: set, active: str | None, queue: list, default_unlocked: set) -> str:
    name = u["name"]
    if name in unlocked or name in default_unlocked:
        return "unlocked"
    if active == name:
        return "researching"
    if name in queue:
        return "queued"
    if u.get("teaser"):
        return "teaser"
    req = u.get("required") or []
    if all(r in unlocked or r in default_unlocked for r in req):
        return "available"
    return "locked"


def get_tech_tree(ctx: CallContext) -> dict:
    ctx.static(sections=("technology",))
    six = ctx.static_index()
    snap = ctx.snapshot("state", required=False, optional_sections=("research",))
    ix = ctx.state_index() if snap is not None else None
    a = ctx.args
    tree = ctx.resolver().resolve(a["tree"], ("tech_tree",), "tree").key if a.get("tree") else None
    research = None
    actor = None
    if ix is not None:
        actor = resolve_company(ctx, ix, a.get("company"))
        if actor == ix.player_actor_id:
            research = ix.player_research
        else:
            ai = ix.ai_research.get(actor)
            research = {"unlocked": ai.get("unlocked") or [], "active": None, "queue": []} if ai else None
            ctx.add_unavailable("nodes[].progress/research_cost_per_day/research_days", "only the unlocked set is exported for AI companies")
    if research is None:
        ctx.add_unavailable("nodes[].state", "no current research state")
    unlocked = set((research or {}).get("unlocked") or [])
    active = (research or {}).get("active")
    queue = list((research or {}).get("queue") or [])
    progress = {p["unlock"]: p["progress"] for p in (research or {}).get("progress") or []}
    costs = {c["unlock"]: c for c in (research or {}).get("costs") or []}
    rows = []
    for u in six.unlocks.values():
        placements = u.get("placements") or []
        if tree and tree not in {p.get("tree") for p in placements}:
            continue
        state = _node_state(u, unlocked, active, queue, six.default_unlocked) if research is not None else None
        if a.get("state") and state != a["state"]:
            continue
        c = costs.get(u["name"])
        row = {"id": make_id("tech", u["name"]), "display_name": u.get("display_name"), "english_name": u.get("english_name"),
               "kind": u.get("kind"), "tier": u.get("tier"),
               "placements": [{"tree": make_id("tech_tree", p.get("tree")), "column": p.get("column")} for p in placements],
               "prerequisites": [make_id("tech", r) for r in u.get("required") or []],
               "included": [make_id("tech", r) for r in u.get("included") or []],
               "unlocks": {"buildings": [make_id("building_type", b) for b in u.get("buildings") or []] if u.get("kind") != "building_price" else [],
                           "recipes": [make_id("recipe", r) for r in u.get("recipes") or []],
                           "price_discounts": ([{"building_types": [make_id("building_type", b) for b in u.get("buildings") or []],
                                                 "price_percentage": u.get("price_percentage")}] if u.get("kind") == "building_price" else []),
                           "generic": u["name"] if u.get("kind") in ("generic", "other") else None},
               "state": state, "progress": progress.get(u["name"]),
               "research_cost_per_day": c.get("daily_cost") if c else None, "research_days": c.get("days") if c else None,
               "teaser": u.get("teaser")}
        rows.append(row)
    rows.sort(key=lambda r: (r["tier"] or 0, r["id"]))
    data = ctx.paginate("nodes", rows)
    data["trees"] = [{"id": make_id("tech_tree", t["name"]), "display_name": t.get("display_name"), "english_name": t.get("english_name"),
                      "category": t.get("category"), "tier_count": t.get("tier_count")} for t in six.trees.values()
                     if tree is None or t["name"] == tree]
    data["company"] = make_id("company", actor) if actor is not None else None
    data["notes"] = {"cost": "research_cost_per_day and research_days are computed by the game per capture for queued, active "
                             "and available nodes only (at the current research efficiency)."}
    data["provenance"] = provenance(observed=["nodes[].progress"], definition=["trees", "nodes (names, tier, prerequisites, unlocks)"],
                                    game_computed=["nodes[].research_cost_per_day", "nodes[].research_days"],
                                    derived=[("nodes[].state", "unlocked set + active + queue + prerequisites (available)")])
    return data


def get_research_state(ctx: CallContext) -> dict:
    ctx.static(required=False)
    ctx.snapshot("state", sections=("research",))
    ix = ctx.state_index()
    six = ix.static
    actor = resolve_company(ctx, ix, ctx.args.get("company"))
    total = len(six.unlocks) if six.available else None
    if actor == ix.player_actor_id:
        r = ix.player_research or {}
        unlocked = set(r.get("unlocked") or []) | six.default_unlocked
        return {
            "company": make_id("company", actor),
            "active": make_id("tech", r.get("active")),
            "queue": [make_id("tech", q) for q in r.get("queue") or []],
            "progress": r.get("active_progress"),
            "progress_by_unlock": [{"tech": make_id("tech", p["unlock"]), "progress": p["progress"]} for p in r.get("progress") or []],
            "remaining_days": r.get("remaining_days"),
            "remaining_cost": r.get("remaining_cost"),
            "current_cost_per_day": r.get("current_cost"),
            "efficiency": {"index": r.get("efficiency_index"), "value": r.get("efficiency")},
            "unlock_points": r.get("unlock_points"),
            "unlocked_count": len(unlocked),
            "total_nodes": total,
            "max_enqueued_unlocks": six.tech_config.get("max_enqueued_unlocks"),
            "provenance": provenance(observed=["active", "queue", "progress", "efficiency", "unlock_points"],
                                     game_computed=["remaining_days", "remaining_cost", "current_cost_per_day"],
                                     derived=[("unlocked_count", "observed unlocked set + default unlocks")],
                                     persistence={"queue": "SAVE", "progress": "SAVE"}),
        }
    ai = ix.ai_research.get(actor)
    if ai is None:
        raise ToolError("not_found", f"no research state exported for company {actor}")
    for f in ("active", "queue", "progress", "remaining_days", "remaining_cost", "efficiency", "unlock_points"):
        ctx.add_unavailable(f, "only the unlocked set is exported for AI companies")
    return {"company": make_id("company", actor), "active": None, "queue": None, "progress": None, "remaining_days": None,
            "remaining_cost": None, "efficiency": None, "unlock_points": None, "unlocked_count": ai.get("unlocked_count"),
            "unlocked": [make_id("tech", u) for u in ai.get("unlocked") or []], "total_nodes": total,
            "provenance": provenance(observed=["unlocked_count", "unlocked"], persistence={"unlocked": "SAVE"})}


def specs() -> list[ToolSpec]:
    return [
        ToolSpec(name="get_market",
                 description=("Current World Market values per product (value, price, modifier, trend, final price for the "
                              "player), days to the next price update, State offers, contracts, auctions. Market price history "
                              "does NOT exist in the game: price_history.available=false." + COMMON_SUFFIX),
                 scope="state", kind="runtime",
                 params={"products": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
                         "include": {"type": "array", "items": {"type": "string", "enum": MARKET_INCLUDES}, "uniqueItems": True}},
                 handler=get_market),
        ToolSpec(name="get_tech_tree",
                 description=("Technology nodes (definitions from the static catalogue) with the company's node state (unlocked, "
                              "researching, queued, available, locked, teaser), progress and the game's research cost per day "
                              "and days." + COMMON_SUFFIX),
                 scope="state", kind="static_live", list_tool=True, fields_param=False,
                 params={"tree": {"type": "string"}, "state": {"type": "string", "enum": NODE_STATES}, "company": {"type": "string"}},
                 handler=get_tech_tree),
        ToolSpec(name="get_research_state",
                 description=("Research status: active unlock, queue, progress, remaining days and cost, efficiency, unlock points, "
                              "unlocked count of total nodes. AI companies: unlocked set only." + COMMON_SUFFIX),
                 scope="state", kind="runtime", params={"company": {"type": "string"}}, handler=get_research_state),
    ]
