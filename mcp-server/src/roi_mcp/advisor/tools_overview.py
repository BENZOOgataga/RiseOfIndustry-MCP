"""get_overview and diagnose_chain (PRD addendum 6.1, 6.2)."""

from __future__ import annotations

from collections import Counter

from .. import derive
from ..app import CallContext
from ..index import make_id
from ..tools.buildings import REASON_TO_ISSUE
from ..tools.common import resolve_product
from ..util import month_key
from ..util import json_size
from .basis import ledger_balance_contradiction, pin_basis, route_stock_contradictions
from .common import (SEVERITY_ORDER, advisor_provenance, bref, pref, rank_findings, require_player,
                     section_or_degrade)
from .contract import Advice, derived, estimate, num, observed, parameter
from .facts import PlayerFacts

BLOCKING = {"disabled", "no_recipe", "no_modules", "blocked", "deposit_depleted"}
PRODUCERS_PER_LEVEL = 8
MAX_FINDINGS = 30
DIAGNOSE_BUDGET = 17_000


def _ledger_months(ledger: dict | None) -> tuple[list[dict], dict | None]:
    months = sorted((ledger or {}).get("months") or [], key=lambda m: month_key(m.get("month")) or (0, 0))
    complete = [m for m in months if not m.get("current_month_to_date")]
    mtd = next((m for m in months if m.get("current_month_to_date")), None)
    return complete, mtd


def _month_row(m: dict, src: str) -> dict:
    inc, exp = num(m.get("income_total")), num(m.get("expense_total"))
    return {"month": m.get("month"), "income": observed(inc, "money", src + ".income_total"),
            "expense": observed(exp, "money", src + ".expense_total"),
            "net": observed(inc - exp if inc is not None and exp is not None else None, "money", src + " (income_total - expense_total)")}


def _balance(f: PlayerFacts, p: str, shop30: float | None) -> float:
    return f.supply(p)[0] - f.internal_need(p) - (shop30 or 0.0)


# ====================================================================== get_overview

def get_overview(ctx: CallContext) -> dict:
    basis = pin_basis(ctx, history="optional", static="optional", state_sections=("companies",))
    ix = ctx.state_index()
    actor = require_player(ix)
    adv = Advice("get_overview", basis)
    f = PlayerFacts(ix)
    company = f.company
    max_att = int(ctx.args.get("max_attention") or 10)
    buildings_ok = section_or_degrade(ctx, adv, "state", "buildings_player") is not None
    routes_ok = section_or_degrade(ctx, adv, "state", "routes_player") is not None
    research = section_or_degrade(ctx, adv, "state", "research")
    shops_ok = section_or_degrade(ctx, adv, "state", "shops", "shop demand omitted from balances") is not None
    section_or_degrade(ctx, adv, "state", "cities", "shop demand per 30 d unavailable")

    cash = company.get("cash") or {}
    cash_q = observed(None if cash.get("infinite") else cash.get("value"), "money", "state.companies[player].cash.value")
    loans = company.get("loans")
    loans_part = None
    if isinstance(loans, list):
        unknown_remaining = any(num(l.get("remaining_payments")) is None for l in loans)
        payments = [derive.loan_monthly_payment(l.get("principal"), l.get("apr"), l.get("duration_months")) for l in loans
                    if (num(l.get("remaining_payments")) or 0) > 0]
        total_pay = sum(p["value"] or 0 for p in payments)
        known = all(p["value"] is not None for p in payments) and not unknown_remaining
        adv.method("loans.monthly_payment_total", "D-LOAN-1")
        loans_part = {"count": len(loans),
                      "principal_total": observed(sum(num(l.get("principal")) or 0 for l in loans), "money", "state.companies[player].loans[].principal"),
                      "monthly_payment_total": derived(total_pay if known else None, "money/30d", "D-LOAN-1")}
        if not known:
            adv.degrade("loans", "missing_value", "monthly payment total null: a loan lacks principal, apr or duration")

    # finances (history, optional)
    finances = {"available": False, "reason": "history.json unavailable or of another world session"}
    complete: list = []
    ledger = None
    if basis.history is not None:
        ledger = section_or_degrade(ctx, adv, "history", "ledger_player")
        if ledger is not None:
            complete, mtd = _ledger_months(ledger)
            src = "history.ledger_player.months[]"
            finances = {"available": True,
                        "last_complete_month": _month_row(complete[-1], src) if complete else None,
                        "previous_complete_month": _month_row(complete[-2], src) if len(complete) >= 2 else None,
                        "month_to_date": _month_row(mtd, src) if mtd else None}
            if len(complete) >= 2:
                a, b = finances["previous_complete_month"]["net"]["value"], finances["last_complete_month"]["net"]["value"]
                finances["net_change_vs_previous"] = derived(b - a if a is not None and b is not None else None, "money/30d", "D-FIN-1")
                adv.method("finances.net_change_vs_previous", "D-FIN-1")
        else:
            finances = {"available": False, "reason": "history section ledger_player unavailable"}
    c = ledger_balance_contradiction(basis.state, basis.history, cash.get("value"))
    if c:
        adv.contradiction(c)

    # buildings and issues
    by_status: Counter = Counter()
    issue_counts: Counter = Counter()
    if buildings_ok:
        for key, st in f.status.items():
            by_status[st.get("status_class")] += 1
            for e in st.get("evidence") or []:
                k = REASON_TO_ISSUE.get(e["reason"])
                if k:
                    issue_counts[k] += 1
    route_stats = None
    if routes_ok:
        route_stats = {"routes": len(f.routes), "with_errors": sum(1 for r in f.routes if r.get("errors") or r.get("has_error")),
                       "paused": sum(1 for r in f.routes if r.get("paused")),
                       "dormant_auto_wh": sum(1 for r in f.routes if r.get("dormant_auto_warehouse")),
                       "keep_all": sum(1 for r in f.routes if (r.get("min_keep") or {}).get("keep_all"))}
        issue_counts["route_error"] = route_stats["with_errors"]
        issue_counts["route_dormant_auto_wh"] = route_stats["dormant_auto_wh"]
        issue_counts["route_keep_all"] = route_stats["keep_all"]
        for row in route_stock_contradictions(ix, f.routes):
            adv.contradiction(row)

    # production balance
    production = None
    balances = []
    if buildings_ok:
        adv.assume("A-CONTINUOUS")
        for p in sorted(set(f.producers) | set(f.consumers)):
            sd = f.shop_demand(p) if shops_ok else None
            shop30 = sd["per_30d"] if sd else None
            sup, approx, unknown = f.supply(p)
            bal = _balance(f, p, shop30)
            partial = not shops_ok or unknown or (sd is not None and sd["shops_unknown_interval"])
            factors = (["approximate_rate"] if approx else []) + (["partial_inputs"] if partial else [])
            balances.append((bal, p, approx, factors, sup, shop30))
        adv.method("production.balance_per_30d", "D-RATE-1/2 - internal need - shop demand (D-SHOP-1 normalised)")

        def row(item):
            bal, p, approx, factors, sup, shop30 = item
            return {"product": pref(ix, p), "balance_per_30d": estimate(bal, "units/30d", "D-SUPDEM-1", adv.conf("medium", factors)),
                    "supply_per_30d": estimate(sup, "units/30d", "D-RATE-1/2", adv.conf("medium", ["approximate_rate"] if approx else [])),
                    "shop_demand_per_30d": estimate(shop30, "units/30d", "D-SHOP-1", adv.conf("medium")) if shop30 is not None else None}
        deficits = sorted((b for b in balances if b[0] < -1e-9), key=lambda b: b[0])[:5]
        surpluses = sorted((b for b in balances if b[0] > 1e-9), key=lambda b: -b[0])[:5]
        production = {"products": len(balances), "deficits": [row(b) for b in deficits], "surpluses": [row(b) for b in surpluses],
                      "note": "balance = supply - internal need - demand of every live shop in the world; a deficit is mostly "
                              "unserved market demand, not an input shortage (those are attention items input_deficit)"}

    research_part = None
    if research is not None and ix.player_research is not None:
        pr = ix.player_research
        research_part = {"active": make_id("tech", pr.get("active")), "progress": observed(pr.get("active_progress"), "ratio", "state.research.player.active_progress"),
                         "queue_length": len(pr.get("queue") or []), "unlocked_count": len(pr.get("unlocked") or [])}

    # attention items (deterministic rules, addendum 6.1)
    att = []
    cv = num(cash.get("value"))
    if cv is not None and cv < 0:
        att.append(("high", 0, "negative_cash", "company:player", "Cash is negative: the game checks bankruptcy at month end.",
                    {"cash": cv}, "get_finances"))
    if complete:
        net = _month_row(complete[-1], "")["net"]["value"]
        if net is not None and net < 0:
            att.append(("high", 1, "monthly_loss", f"month:{complete[-1].get('month')}", "The last complete month ended with a net loss.",
                        {"month": complete[-1].get("month"), "net": derived(net, "money", "income_total - expense_total")}, "get_finances"))
    if buildings_ok:
        groups: dict[str, list] = {}
        for key, st in f.status.items():
            ds = st.get("derived_status")
            if ds in ("blocked", "no_modules", "deposit_depleted", "missing_input", "output_full"):
                groups.setdefault(ds, []).append(make_id("building", key))
        for ds, sev, order in (("blocked", "high", 2), ("no_modules", "high", 3), ("deposit_depleted", "high", 4),
                               ("missing_input", "medium", 6), ("output_full", "medium", 7)):
            if groups.get(ds):
                ids = sorted(groups[ds])
                att.append((sev, order, f"buildings_{ds}", ids[0] if len(ids) == 1 else f"{len(ids)} buildings",
                            f"{len(ids)} building(s) with status {ds}.", {"buildings": ids[:5], "count": len(ids)}, "diagnose_chain"))
        for bal, p, approx, factors, sup, shop30 in sorted((b for b in balances if b[0] < -1e-9 and f.internal_need(b[1]) > b[4] + 1e-9),
                                                            key=lambda b: b[0])[:3]:
            att.append(("medium", 8, "input_deficit", make_id("product", p),
                        "Internal need for this product exceeds the company's theoretical supply (estimate).",
                        {"supply_per_30d": estimate(sup, "units/30d", "D-RATE-1/2", adv.conf("medium", factors)),
                         "internal_need_per_30d": estimate(f.internal_need(p), "units/30d", "D-RATE-1/2", adv.conf("medium"))},
                        "diagnose_chain"))
    if route_stats:
        if route_stats["with_errors"]:
            att.append(("high", 5, "route_errors", f"{route_stats['with_errors']} routes", "Some routes report errors (e.g. no path).",
                        {"count": route_stats["with_errors"]}, "review_routes"))
        if route_stats["keep_all"]:
            att.append(("medium", 9, "routes_keep_all", f"{route_stats['keep_all']} routes", "Some routes keep all stock and never dispatch.",
                        {"count": route_stats["keep_all"]}, "review_routes"))
    if research_part is not None and not ix.player_research.get("active") and not (ix.player_research.get("queue") or []):
        att.append(("low", 10, "research_idle", "research:player", "No research is active or queued.", {}, "get_research_state"))
    att.sort(key=lambda a: (SEVERITY_ORDER[a[0]], a[1], a[3]))
    attention = [{"rank": i, "severity": a[0], "kind": a[2], "subject": a[3], "summary": a[4], "evidence": a[5], "next_tool": a[6]}
                 for i, a in enumerate(att[:max_att], 1)]

    adv.observed = {"cash": cash_q, "game_date": (ix.session or {}).get("game_date"), "paused": (ix.session or {}).get("paused")}
    adv.inputs = {"max_attention": parameter(max_att, "count")}
    result = {
        "company": {"id": make_id("company", actor), "name": company.get("name")},
        "cash": cash_q,
        "loans": loans_part,
        "finances": finances,
        "buildings": ({"count": len(f.buildings), "by_status": dict(by_status)} if buildings_ok else {"available": False}),
        "production": production if production is not None else {"available": False},
        "logistics": route_stats if route_stats is not None else {"available": False},
        "research": research_part,
        "issue_counts": dict(issue_counts),
        "attention": attention,
    }
    return adv.finish(result, advisor_provenance(
        ["cash", "loans", "finances (history ledger)", "buildings.by_status inputs", "logistics", "research"], ["recipes"],
        [("buildings.by_status", "D-STATUS-1")] + adv.methods))


# ====================================================================== diagnose_chain

def _route_blocked(r: dict) -> list[str]:
    why = []
    if r.get("paused"):
        why.append("paused")
    if r.get("errors") or r.get("has_error"):
        why.append("error")
    if r.get("dormant_auto_warehouse"):
        why.append("dormant_auto_wh")
    if (r.get("min_keep") or {}).get("keep_all"):
        why.append("keep_all")
    return why


def diagnose_chain(ctx: CallContext) -> dict:
    basis = pin_basis(ctx, static="required", state_sections=("buildings_player",))
    ix = ctx.state_index()
    require_player(ix)
    ctx.static(required=True, sections=("recipes",))
    adv = Advice("diagnose_chain", basis)
    f = PlayerFacts(ix)
    six = f.six
    product = resolve_product(ctx, ctx.args["product"])
    depth_max = int(ctx.args.get("depth") or 3)
    routes_ok = section_or_degrade(ctx, adv, "state", "routes_player", "route checks omitted") is not None
    shops_ok = section_or_degrade(ctx, adv, "state", "shops", "outlet check omitted") is not None
    section_or_degrade(ctx, adv, "state", "requests_player", "warehouse pulls not considered as inbound sources")
    adv.assume("A-CONTINUOUS")
    state_sold = {s.get("product") for s in ((ix.market or {}).get("state") or {}).get("sold") or []}

    levels = []
    findings = []
    seen = set()
    frontier = [(product, 0, None)]
    while frontier:
        p, d, parent = frontier.pop(0)
        if p in seen:
            continue
        seen.add(p)
        prods = f.producers.get(p, [])
        sup, approx, unknown = f.supply(p)
        need = f.internal_need(p)
        sd = f.shop_demand(p) if shops_ok else None
        rate_conf = adv.conf("medium", ["approximate_rate"] if approx or unknown else [])
        level = {"product": pref(ix, p), "depth": d, "parent": make_id("product", parent),
                 "supply_per_30d": estimate(sup, "units/30d", "D-RATE-1/2", rate_conf),
                 "internal_need_per_30d": estimate(need, "units/30d", "D-RATE-1/2", adv.conf("medium")),
                 "shop_demand_per_30d": estimate(sd["per_30d"], "units/30d", "D-SHOP-1", adv.conf("medium")) if sd and sd["shops"] else None,
                 "stock": observed(f.stock(p), "units", "state.buildings_player[].inventory[].count"),
                 "producers": []}
        choice = f.choose_recipe(p)
        recipe = choice.get("recipe")
        level["recipe"] = make_id("recipe", (recipe or {}).get("name"))
        level["producers_total"] = len(prods)
        for pr in sorted(prods, key=lambda x: x["building"]["key"])[:PRODUCERS_PER_LEVEL]:
            b = pr["building"]
            st = f.building_status(b["key"])
            level["producers"].append({"building": bref(ix, b["key"]), "status": st.get("status_class"), "derived_status": st.get("derived_status"),
                                       "produced_last_month": observed((b.get("production") or {}).get("produced_last_month"), "units",
                                                                       "state.buildings_player[].production.produced_last_month"),
                                       "theoretical_per_30d": estimate(pr["per_30d"], "units/30d", "D-RATE-1/2",
                                                                       adv.conf("medium", ["approximate_rate"] if pr["approximate"] else []))})
        levels.append(level)

        def add(sev, kind, summary, evidence, building=None, route=None, conf=None):
            findings.append({"_rank": (SEVERITY_ORDER[sev], d, kind, building or "", route or ""), "severity": sev, "kind": kind,
                             "product": make_id("product", p), "building": building, "route": route, "summary": summary,
                             "evidence": evidence, "confidence": conf or adv.conf("high")})

        if not prods:
            if recipe is None:
                add("medium" if d else "info", "no_recipe", "No recipe produces this product; it cannot be manufactured.",
                    {"sold_by_state": p in state_sold})
            else:
                add("high", "no_producer", "The company has no building producing this product.",
                    {"recipe": make_id("recipe", recipe.get("name")), "sold_by_state": p in state_sold,
                     "locked_by": f.locked_by(recipe.get("name"), (f.building_type_for(recipe) or {}).get("name"))})
        elif all(not pr["enabled"] for pr in prods):
            add("high", "producer_disabled", "Every producer of this product is disabled.",
                {"producers": [make_id("building", pr["building"]["key"]) for pr in prods][:10]})
        for pr in prods:
            b = pr["building"]
            st = f.building_status(b["key"])
            ds = st.get("derived_status")
            bid = make_id("building", b["key"])
            ev = next((e for e in st.get("evidence") or [] if e.get("reason") == ds), {})
            if ds in ("blocked", "no_modules", "deposit_depleted") and pr["enabled"]:
                add("high", {"blocked": "producer_blocked"}.get(ds, ds), f"Producer stopped: {ds}.", ev, building=bid)
            elif ds == "missing_input":
                add("medium", "producer_missing_input", "Producer idle for lack of an input.", ev, building=bid)
            elif ds == "output_full":
                add("medium", "output_blocked", "Producer's output storage is full: production stalls until stock leaves.", ev, building=bid)
            for e in st.get("evidence") or []:
                if e.get("reason") == "polluted":
                    add("low", "polluted", "Producer is polluted.", e, building=bid)
            for inv in b.get("inventory") or []:
                if inv.get("product") == p and inv.get("role") == "output":
                    ratio = derive.fill_ratio(inv.get("count"), inv.get("slots"))
                    if ratio is not None and ratio >= 0.9 and ds != "output_full":
                        add("low", "output_accumulating", "Output storage is at least 90 % full (D-INV-1).",
                            {"count": inv.get("count"), "slots": inv.get("slots"), "fill_ratio": derived(ratio, "ratio", "D-INV-1")},
                            building=bid)
        if prods and need > sup + 1e-9:
            add("medium", "input_supply_deficit", "The company's internal need exceeds its theoretical supply of this product (estimate).",
                {"supply_per_30d": estimate(sup, "units/30d", "D-RATE-1/2", rate_conf),
                 "internal_need_per_30d": estimate(need, "units/30d", "D-RATE-1/2", adv.conf("medium"))},
                conf=adv.conf("medium", ["approximate_rate"] if approx else []))
        # inbound sources at the consumers of this product that belong to the diagnosed chain
        if routes_ok and parent is not None:
            for c in f.consumers.get(p, []):
                b = c["building"]
                if b.get("recipe") is None or not c["enabled"]:
                    continue
                pres = {o["product"] for o in (f.rates.get(b["key"]) or {}).get("outputs") or []}
                if parent not in pres:
                    continue
                inbound = [r for r in ix.routes_by_destination.get(b["key"], []) if r.get("product") == p]
                pulls = [q for q in ix.requests_by_endpoint.get(b["key"], []) if q.get("product") == p]
                bid = make_id("building", b["key"])
                if not inbound and not pulls:
                    add("high", "no_inbound_source", "No route or warehouse request brings this input to the building.",
                        {"consumer": bid, "input": make_id("product", p)}, building=bid)
                elif inbound and not pulls:
                    blocked = {make_id("route", r["route_key"]): _route_blocked(r) for r in inbound}
                    zero = {make_id("route", r["route_key"]): (r.get("dispatch_amount_now") or {}).get("limited_by")
                            for r in inbound if (r.get("dispatch_amount_now") or {}).get("value") == 0}
                    if all(blocked.values()):
                        add("medium", "inbound_route_blocked", "Every inbound route for this input is paused, errored, dormant or keep-all.",
                            {"consumer": bid, "routes": blocked}, building=bid)
                    elif zero and len(zero) == len(inbound):
                        add("medium", "inbound_route_blocked", "Every inbound route for this input would dispatch 0 units now.",
                            {"consumer": bid, "routes_limited_by": zero}, building=bid)
        if d == 0 and prods and shops_ok:
            out_routes = [r for pr in prods for r in ix.routes_by_origin.get(pr["building"]["key"], []) if r.get("product") == p]
            if not out_routes and not any((pr["building"].get("logistics") or {}).get("auto_wh") for pr in prods):
                add("medium", "no_outbound_route", "No configured route ships this product out of its producers.",
                    {"producers": [make_id("building", pr["building"]["key"]) for pr in prods][:10]})
            unmet = num((sd or {}).get("unmet_per_30d")) or 0.0
            piling = []
            for pr in prods:
                for inv in pr["building"].get("inventory") or []:
                    ratio = derive.fill_ratio(inv.get("count"), inv.get("slots"))
                    if inv.get("product") == p and inv.get("role") == "output" and ratio is not None and ratio >= 0.9:
                        piling.append({"building": make_id("building", pr["building"]["key"]), "fill_ratio": derived(ratio, "ratio", "D-INV-1")})
            if piling and unmet > 1e-9:
                add("medium", "outbound_constrained",
                    "Output piles up at the producers while shops still have unmet demand: outbound logistics limit sales.",
                    {"producers_full": piling[:10], "unmet_shop_demand_per_30d": estimate(unmet, "units/30d", "D-SHOP-1", adv.conf("medium")),
                     "outbound_routes": [{"route_id": make_id("route", r["route_key"]),
                                          "dispatch_amount_now": (r.get("dispatch_amount_now") or {}).get("value"),
                                          "limited_by": (r.get("dispatch_amount_now") or {}).get("limited_by"),
                                          "errors": r.get("errors"), "paused": r.get("paused")} for r in out_routes][:10]},
                    conf=adv.conf("medium"))
            if sd and sd["shops"] and sd["shops_unknown_interval"] == 0 and sup > need + (sd["per_30d"] or 0) + 1e-9:
                add("medium", "outlet_shortfall", "Theoretical supply exceeds internal need plus the shop demand the company sees (estimate).",
                    {"supply_per_30d": estimate(sup, "units/30d", "D-RATE-1/2", rate_conf),
                     "internal_need_per_30d": estimate(need, "units/30d", "D-RATE-1/2", adv.conf("medium")),
                     "shop_demand_per_30d": estimate(sd["per_30d"], "units/30d", "D-SHOP-1", adv.conf("medium"))},
                    conf=adv.conf("medium", ["approximate_rate"] if approx else []))
        if recipe is not None and d < depth_max:
            for ing in recipe.get("ingredients") or []:
                frontier.append((ing.get("product"), d + 1, p))
    if routes_ok:
        rel = [r for r in f.routes if r.get("product") in seen]
        for row in route_stock_contradictions(ix, rel):
            adv.contradiction(row)
    findings = rank_findings(findings)
    shown = findings[:MAX_FINDINGS]
    result = {"product": pref(ix, product), "depth": depth_max, "levels": levels, "findings": shown,
              "findings_total": len(findings),
              "healthy": not any(x["severity"] in ("high", "medium") for x in findings)}
    # bound the answer by size (never by dropping chain levels): fewer findings, then fewer producers per level
    while json_size(result) > DIAGNOSE_BUDGET and len(result["findings"]) > 5:
        result["findings"] = result["findings"][:max(5, int(len(result["findings"]) * 0.7))]
    for lv in levels:
        if json_size(result) <= DIAGNOSE_BUDGET:
            break
        lv["producers"] = lv["producers"][:3]
    for lv in levels:
        lv["producers_omitted"] = lv["producers_total"] - len(lv["producers"])
    if len(result["findings"]) < len(findings):
        ctx.add_unavailable("findings", f"{len(findings) - len(result['findings'])} lower-ranked finding(s) omitted to stay under "
                            "the size cap; narrow with depth or diagnose an input product directly")
    adv.inputs = {"depth": parameter(depth_max, "count"), "accumulation_fill_ratio": parameter(0.9, "ratio", "D-INV-1 constant")}
    adv.method("levels[].supply_per_30d / internal_need_per_30d", "D-RATE-1/2")
    adv.method("findings[].kind", "D-ADV-SEV-1 over D-STATUS-1 evidence")
    return adv.finish(result, advisor_provenance(
        ["levels[].producers (status, produced_last_month)", "stock", "routes", "warehouse requests"], ["recipes"], adv.methods))
