"""research_path and suggest_research (phase-2 addendum 4.1, 4.5)."""

from __future__ import annotations

from ..app import CallContext
from ..errors import ToolError
from ..index import make_id
from .basis import pin_basis
from .common import advisor_provenance, bounded_page, pref, require_player, section_or_degrade
from .contract import Advice, confidence, derived, estimate, game_computed, merge_confidence, num, observed, parameter
from .facts import PlayerFacts
from .research import ALTERNATIVES_REASON, chain, node_costs, products_unlocked

COST_SRC = "state.research.player.costs[]"


def _research_inputs(ctx: CallContext, adv: Advice):
    ix = ctx.state_index()
    six = ix.static
    pr = ctx.require_section("state", "research").get("player") if ctx.used.get("state") else None
    if not pr:
        raise ToolError("section_unavailable", "The player's research state is not in the snapshot.",
                        details={"section": "research", "reason": "player_research_missing"})
    unlocked = set(pr.get("unlocked") or [])
    observed_costs = {c["unlock"]: c for c in pr.get("costs") or [] if c.get("unlock")}
    return ix, six, pr, unlocked, observed_costs


def _resolve_tech(ctx: CallContext, value: str) -> str:
    return ctx.resolver().resolve(value, ("tech",), "target").key


def _path(six, pr: dict, unlocked: set, observed_costs: dict, target: str) -> dict:
    ch = chain(target, six.unlocks, unlocked)
    costs = node_costs(ch["remaining"], six.unlocks, six.formulas, observed_costs, pr.get("efficiency"),
                       pr.get("active"), pr.get("remaining_days"), pr.get("remaining_cost"))
    return {"chain": ch, "costs": costs}


def _node_q(adv: Advice, row: dict) -> dict:
    basis = row.get("basis")
    if basis in ("game_computed", "game_active_remaining"):
        src = COST_SRC if basis == "game_computed" else "state.research.player.remaining_days/remaining_cost"
        days = game_computed(row.get("days"), "days", src)
        cost = derived(row.get("cost"), "money", "daily_cost x days" if basis == "game_computed" else "observed remaining_cost")
        daily = game_computed(row.get("daily_cost"), "money/day", src) if basis == "game_computed" else \
            derived(row.get("daily_cost"), "money/day", "remaining_cost / remaining_days")
    else:
        conf = adv.track(row.get("confidence")) if row.get("confidence") else {"level": "low", "factors": ["partial_inputs"]}
        days = estimate(row.get("days"), "days", "D-RES-PATH-1 formula", conf)
        cost = estimate(row.get("cost"), "money", "D-RES-PATH-1 formula", conf)
        daily = estimate(row.get("daily_cost"), "money/day", "D-RES-PATH-1 formula", conf)
    return {"days": days, "cost": cost, "daily_cost": daily}


def research_path(ctx: CallContext) -> dict:
    basis = pin_basis(ctx, static="required", state_sections=("research",))
    require_player(ctx.state_index())
    ctx.static(required=True, sections=("tech",))
    adv = Advice("research_path", basis)
    ix, six, pr, unlocked, observed_costs = _research_inputs(ctx, adv)
    target = _resolve_tech(ctx, ctx.args["target"])
    u = six.unlocks[target]
    p = _path(six, pr, unlocked, observed_costs, target)
    ch, costs = p["chain"], p["costs"]
    if any(r.get("basis", "").startswith("formula") for r in costs["nodes"] if r.get("basis")):
        adv.assume("A-RESEARCH-FORMULA")
    for r in costs["nodes"]:
        if r.get("basis") is None:
            adv.degrade(f"tech:{r['unlock']}", "missing_value", r.get("reason") or "cost unknown")
    active = pr.get("active")
    queue = list(pr.get("queue") or [])
    ahead = [q for q in ([active] if active else []) + queue if q and q not in ch["remaining"]]
    ahead_costs = node_costs(ahead, six.unlocks, six.formulas, observed_costs, pr.get("efficiency"), active,
                             pr.get("remaining_days"), pr.get("remaining_cost")) if ahead else None
    # A queued node of the chain is researched in queue order; the active node of the chain counts with its remaining time.
    nodes = []
    for r in costs["nodes"]:
        n = r["unlock"]
        uu = six.unlocks.get(n) or {}
        nodes.append({"tech": make_id("tech", n), "display_name": uu.get("display_name"), "english_name": uu.get("english_name"),
                      "tier": uu.get("tier"), "status": "active" if n == active else ("queued" if n in queue else "not_queued"),
                      "basis": r.get("basis"), "included": [make_id("tech", x) for x in uu.get("included") or []],
                      **_node_q(adv, r), "reason": r.get("reason")})
    total_conf = merge_confidence([r.get("confidence") for r in costs["nodes"]]) if costs["complete"] else \
        {"level": "low", "factors": ["partial_inputs"]}
    total_conf = adv.track(total_conf)
    eta = None
    if costs["total_days"] is not None and (ahead_costs is None or ahead_costs["complete"]):
        eta = costs["total_days"] + (ahead_costs["total_days"] if ahead_costs else 0.0)
    eff = num(pr.get("efficiency"))
    result = {
        "target": {"tech": make_id("tech", target), "display_name": u.get("display_name"), "english_name": u.get("english_name"),
                   "tier": u.get("tier"), "teaser": bool(u.get("teaser")), "already_unlocked": target in ch["already_unlocked"]},
        "remaining": nodes, "remaining_count": len(nodes),
        "already_unlocked": [make_id("tech", x) for x in ch["already_unlocked"]],
        "totals": {"days": estimate(costs["total_days"], "days", "D-RES-PATH-1", total_conf),
                   "cost": estimate(costs["total_cost"], "money", "D-RES-PATH-1", total_conf, 2),
                   "known_partial_days": derived(costs["known_partial"]["days"], "days", "sum of known nodes"),
                   "known_partial_cost": derived(costs["known_partial"]["cost"], "money", "sum of known nodes"),
                   "complete": costs["complete"]},
        "queue_ahead": [make_id("tech", x) for x in ahead],
        "estimated_completion_days": estimate(eta, "days", "D-RES-PATH-1 (queue ahead + chain)", total_conf),
        "alternatives": [], "alternatives_reason": ALTERNATIVES_REASON,
        "cycles": [[make_id("tech", x) for x in c] for c in ch["cycles"]],
    }
    if u.get("teaser"):
        adv.degrade(f"tech:{target}", "missing_value", "teaser node: shown in the tree but not researchable")
    adv.observed = {"research_efficiency": observed(eff, "ratio", "state.research.player.efficiency"),
                    "unlocked_count": len(unlocked)}
    cal = costs["calibration"]
    adv.inputs = {"calibration_days_factor": derived(cal["days_factor"], "ratio", "median(game days / formula days) over costed nodes"),
                  "calibration_cost_factor": derived(cal["cost_factor"], "ratio", "median(game daily cost / formula daily cost)"),
                  "calibration_samples": cal["samples"]}
    adv.method("remaining[] / totals", "D-RES-PATH-1")
    return adv.finish(result, advisor_provenance(["research state (unlocked, queue, active progress, game costs)"],
                                                 ["tech unlocks (required, included, tier, formulas)"], adv.methods))


# ====================================================================== suggest_research

def score_node(adv: Advice, f: PlayerFacts, ix, six, name: str, ch: dict, costs: dict, available: bool, shops_ok: bool) -> dict:
    """D-RES-SCORE-1 components and score for one research node (shared by suggest_research and compare_options)."""
    u = six.unlocks[name]
    if any((r.get("basis") or "").startswith("formula") for r in costs["nodes"]):
        adv.assume("A-RESEARCH-FORMULA")
    unl = products_unlocked(name, six.unlocks, six.recipes, six.building_types)
    demand_v, bottle_v, fit_num, fit_den = 0.0, 0.0, 0, 0
    value_known = True
    for prod in unl["products"]:
        sd = f.shop_demand(prod) if shops_ok else None
        if sd and (sd["unmet_per_30d"] or 0) > 0:
            price = f.sale_price(prod).get("value")
            if price is None:
                value_known = False
            else:
                demand_v += sd["unmet_per_30d"] * price
        deficit = max(f.internal_need(prod) - f.supply(prod)[0], 0.0)
        if deficit > 0:
            iv = f.input_value(prod).get("value")
            if iv is None:
                value_known = False
            else:
                bottle_v += deficit * iv
    for rn in unl["recipes"]:
        for ing in (six.recipes.get(rn) or {}).get("ingredients") or []:
            fit_den += 1
            fit_num += 1 if f.producers.get(ing.get("product")) else 0
    fit = (fit_num / fit_den) if fit_den else (1.0 if unl["recipes"] else None)
    cost = costs["total_cost"]
    value = demand_v + bottle_v
    score = None
    reason = None
    if not unl["recipes"]:
        reason = f"{u.get('kind')} unlock: no recipe or production building; value not expressible in money"
    elif not value_known:
        reason = "a needed price is unavailable"
    elif cost is None or cost <= 0:
        reason = "research cost unknown"
    else:
        score = value * (0.5 + 0.5 * (fit if fit is not None else 0.0)) / cost * 30.0
    cconf = merge_confidence([r.get("confidence") for r in costs["nodes"]]) if costs["complete"] else \
        confidence("medium", ["partial_inputs"])
    conf = adv.track(merge_confidence([cconf, confidence("medium")]))
    return {"tech": make_id("tech", name), "display_name": u.get("display_name"), "english_name": u.get("english_name"),
                 "available_now": available, "chain_length": len(ch["remaining"]),
                 "chain": [make_id("tech", x) for x in ch["remaining"]],
                 "unlocks_recipes": [make_id("recipe", x) for x in unl["recipes"]],
                 "unlocks_products": [pref(ix, x) for x in unl["products"]],
                 "components": {"demand_value_per_30d": estimate(demand_v if value_known else None, "money/30d", "D-RES-SCORE-1", conf, 2),
                                "bottleneck_value_per_30d": estimate(bottle_v if value_known else None, "money/30d", "D-RES-SCORE-1", conf, 2),
                                "chain_fit": derived(fit, "ratio", "produced ingredients / all ingredients"),
                                "research_cost": estimate(cost, "money", "D-RES-PATH-1", conf, 2),
                                "research_days": estimate(costs["total_days"], "days", "D-RES-PATH-1", conf)},
                 "score": estimate(score, "ratio", "D-RES-SCORE-1", conf), "unscored_reason": reason, "confidence": conf}


def suggest_research(ctx: CallContext) -> dict:
    basis = pin_basis(ctx, static="required", state_sections=("research",))
    require_player(ctx.state_index())
    ctx.static(required=True, sections=("tech", "recipes"))
    adv = Advice("suggest_research", basis)
    ix, six, pr, unlocked, observed_costs = _research_inputs(ctx, adv)
    f = PlayerFacts(ix)
    shops_ok = section_or_degrade(ctx, adv, "state", "shops", "demand value omitted") is not None
    section_or_degrade(ctx, adv, "state", "market", "prices unavailable: values null")
    section_or_degrade(ctx, adv, "state", "buildings_player", "bottleneck value and chain fit unknown")
    reachable = bool(ctx.args.get("include_reachable"))
    max_chain = int(ctx.args.get("max_chain") or 3)
    adv.assume("A-SCORE-WEIGHTS", "A-DEMAND-PERSISTS", "A-PRICE-SHOP", "A-INPUT-MARKET", "A-CONTINUOUS")
    rows = []
    for name in sorted(six.unlocks):
        u = six.unlocks[name]
        if name in unlocked or u.get("unlocked_by_default") or u.get("teaser"):
            continue
        p = _path(six, pr, unlocked, observed_costs, name)
        ch, costs = p["chain"], p["costs"]
        available = ch["remaining"] == [name]
        if not available and not (reachable and len(ch["remaining"]) <= max_chain):
            continue
        rows.append(score_node(adv, f, ix, six, name, ch, costs, available, shops_ok))
    rows.sort(key=lambda r: (r["score"]["value"] is None, -(r["score"]["value"] or 0),
                             r["components"]["research_days"]["value"] if r["components"]["research_days"]["value"] is not None else 1e18,
                             r["tech"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    data, bounded = bounded_page(ctx, "suggestions", rows)
    adv.inputs = {"formula": "score = (demand_value + bottleneck_value) x (0.5 + 0.5 x chain_fit) / research_cost x 30",
                  "chain_fit_weight": parameter(0.5, "ratio", "constant"), "include_reachable": reachable,
                  "max_chain": parameter(max_chain, "count")}
    adv.method("suggestions[].score", "D-RES-SCORE-1")
    return adv.finish({"suggestions": data["suggestions"], **({"page_bounded_by_size": True} if bounded else {})},
                      advisor_provenance(["research state", "shop demand and prices", "production balance"],
                                         ["tech unlocks", "recipes"], adv.methods))
