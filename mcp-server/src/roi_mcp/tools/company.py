"""list_companies, get_company, get_finances (PRD 14.2)."""

from __future__ import annotations

from .. import config as cfg
from .. import derive
from ..app import COMMON_SUFFIX, CallContext, ToolSpec
from ..errors import ToolError
from ..index import make_id
from ..util import json_size, month_key
from .common import AI_CASH_NOTE, provenance, resolve_company


def _cash(c: dict) -> object:
    cash = c.get("cash") or {}
    if cash.get("infinite"):
        return {"infinite": True}
    return cash.get("value")


def _region_count(ix, actor_id: int) -> int:
    return sum(1 for r in ix.regions.values() if (r.get("permit") or {}).get("owner_actor_id") == actor_id)


def list_companies(ctx: CallContext) -> dict:
    ctx.static(required=False)
    ctx.snapshot("state", sections=("companies",), optional_sections=("regions",))
    ix = ctx.state_index()
    rows = []
    for c in sorted(ix.companies.values(), key=lambda c: (not c.get("is_player"), c["actor_id"])):
        loans = c.get("loans")
        row = {
            "id": make_id("company", c["actor_id"]), "name": c.get("name"), "is_player": bool(c.get("is_player")),
            "kind": c.get("kind"), "color": c.get("color"), "hq_city": ix.city_ref(c.get("hq_city_id")),
            "cash": _cash(c),
            "loans_total": None if loans is None else round(sum(l.get("principal") or 0 for l in loans), 2),
            "loans_count": None if loans is None else len(loans),
            "cashflow_label": (c.get("stats") or {}).get("cashflow"),
            "region_count": _region_count(ix, c["actor_id"]) if ix.regions else len((c.get("stats") or {}).get("owned_permits") or []),
            "building_counts_by_tag": c.get("building_counts_by_tag"),
            "building_count": c.get("building_count"),
            "main_tech_tree": make_id("tech_tree", (c.get("stats") or {}).get("main_tech_tree")),
            "shares_owned_by_others": (c.get("shares") or {}).get("owned_by_competitors"),
        }
        if ctx.full:
            row["building_counts_by_type"] = c.get("building_counts_by_type")
            row["stats"] = c.get("stats")
        rows.append(row)
    data = ctx.paginate("companies", rows)
    data["notes"] = {"ai_cash": AI_CASH_NOTE}
    data["provenance"] = provenance(observed=["companies"], derived=[("loans_total", "sum of loan principals"),
                                                                     ("region_count", "count of regions whose permit owner is the company")],
                                    persistence={"companies": "SAVE"})
    return data


def get_company(ctx: CallContext) -> dict:
    ctx.static(required=False)
    ctx.snapshot("state", sections=("companies",), optional_sections=("regions",))
    ix = ctx.state_index()
    actor = resolve_company(ctx, ix, ctx.args.get("company"))
    c = ix.companies.get(actor)
    if c is None:
        raise ToolError("not_found", f"company {actor} not in snapshot")
    stats = c.get("stats") or {}
    loans = []
    derived = [("value", "D-VAL-1"), ("total_assets", "replica:Headquarters.totalAssets")]
    if c.get("loans") is None:
        ctx.add_unavailable("loans", "not exported for this company (AI companies: summary only)")
    for l in c.get("loans") or []:
        mp = derive.loan_monthly_payment(l.get("principal"), l.get("apr"), l.get("duration_months"))
        loans.append({"type": l.get("type"), "title": l.get("title"),
                      "lender": ix.actor_ref(l.get("lender_actor_id")) if l.get("lender_actor_id") is not None else {"name": l.get("lender_name")},
                      "lender_name": l.get("lender_name"), "principal": l.get("principal"), "apr": l.get("apr"),
                      "duration_months": l.get("duration_months"), "remaining_payments": l.get("remaining_payments"),
                      "amount_with_apr": l.get("amount_with_apr"), "monthly_payment": mp,
                      "grace_months_left": l.get("grace_months_left"), "early_repay_amount": l.get("early_repay_amount"),
                      "settlement_loan": l.get("settlement_loan")})
    if loans:
        derived.append(("loans[].monthly_payment", "D-LOAN-1"))
    shares = c.get("shares")
    shares_out = None
    if shares is not None:
        shares_out = {"bundle_count": shares.get("bundle_count"), "bundle_size": shares.get("bundle_size"),
                      "bundles": [{"owner": ix.actor_ref(o)} for o in shares.get("bundle_owners") or []],
                      "owned_by_competitors": shares.get("owned_by_competitors")}
    value = derive.company_value(c.get("value_inputs"), (shares or {}).get("owned_by_competitors"))
    if c.get("value_inputs") is None:
        ctx.add_unavailable("value", "value_inputs not exported for this company")
    data = {
        "identity": {"id": make_id("company", actor), "name": c.get("name"), "kind": c.get("kind"),
                     "is_player": bool(c.get("is_player")), "color": c.get("color"),
                     "hq_building": ix.building_ref(c.get("hq_building")), "hq_city": ix.city_ref(c.get("hq_city_id"))},
        "cash": _cash(c),
        "loans": loans,
        "max_loans": c.get("max_loans"),
        "shares": shares_out,
        "value": value,
        "total_assets": {"value": c.get("paid_to_build_total"), "method": "replica:Headquarters.totalAssets",
                         "definition": "sum of paid_to_build over the company's buildings"},
        "stats": {"cashflow_label": stats.get("cashflow"),
                  "top_production": [{"product": make_id("product", p["product"]), "amount": p["amount"]} for p in stats.get("top_production") or []],
                  "top_sales": [{"product": make_id("product", p["product"]), "amount": p["amount"]} for p in stats.get("top_sales") or []],
                  "owned_permits": [ix.region_ref(r) for r in stats.get("owned_permits") or []],
                  "main_tech_tree": make_id("tech_tree", stats.get("main_tech_tree"))},
        "buildings_summary": {"total": c.get("building_count"), "by_type": c.get("building_counts_by_type"),
                              "by_tag": c.get("building_counts_by_tag")},
        "contracts": c.get("contracts"),
        "notes": {"ai_cash": AI_CASH_NOTE},
    }
    if c.get("ai") is not None or not c.get("is_player"):
        ai = c.get("ai") or {}
        data["ai"] = {"personality": ai.get("personality"), "owned_regions": [ix.region_ref(r) for r in ai.get("owned_regions") or []],
                      "has_initiative": ai.get("has_initiative"), "product_goals": None}
        ctx.add_unavailable("ai.product_goals", "AI brain-state goals are not exported (reads not reviewed, PRD U-AI)")
    data["provenance"] = provenance(observed=["identity", "cash", "loans", "shares", "stats", "buildings_summary", "contracts", "ai"],
                                    derived=derived, persistence={"cash": "SAVE", "loans": "SAVE", "shares": "SAVE", "stats": "SAVE"})
    return data


def get_finances(ctx: CallContext) -> dict:
    ctx.static(required=False)
    snap = ctx.snapshot("history", sections=())
    ledger = ctx.require_section("history", "ledger_player")
    six = ctx.static_index()
    company = ctx.args.get("company")
    player_id = ledger.get("actor_id")
    if company is not None and not (isinstance(company, str) and company.strip().lower() == "player"):
        # Resolve against the state snapshot if available (names), else accept the id form only.
        state = ctx.snapshot("state", required=False)
        if state is not None:
            actor = resolve_company(ctx, ctx.state_index(), company)
            # The state snapshot only resolved the name; the answer comes from history alone (PRD 13.7), so the
            # state snapshot must not decide staleness or meta.snapshot.
            ctx.used.pop("state", None)
        else:
            if isinstance(company, str) and company.startswith("company:") and company[8:].lstrip("-").isdigit():
                actor = int(company[8:])
            else:
                raise ToolError("not_found", f"company {company!r} cannot be resolved without a current state snapshot",
                                hint="Pass the id form company:<actor id>.")
        if actor != player_id:
            raise ToolError("section_unavailable", "Only the player's ledger is exported (history section ledger_player).",
                            details={"section": "ledger_player", "reason": "ai_ledger_not_exported"})
    months_n = int(ctx.args.get("months") or 6)
    cat_filter = None
    if ctx.args.get("categories"):
        res = ctx.resolver()
        cat_filter = {res.resolve(c, ("bill_category",), "categories[]").key for c in ctx.args["categories"]}
    group_by = ctx.args.get("group_by") or "category"
    all_months = sorted(ledger.get("months") or [], key=lambda m: month_key(m.get("month")) or (0, 0))
    selected = all_months[-months_n:] if months_n > 0 else []

    def group_name(cat: str) -> str:
        return six.bill_to_overview.get(cat, "other") if group_by == "overview_group" else cat

    out_months = []
    for m in selected:
        groups: dict[str, dict] = {}
        for c in m.get("categories") or []:
            cat = c.get("category")
            if cat_filter is not None and cat not in cat_filter:
                continue
            g = group_name(cat)
            row = groups.setdefault(g, {"income": 0.0, "expense": 0.0, "members": []})
            row["income"] += c.get("income") or 0.0
            row["expense"] += c.get("expense") or 0.0
            row["members"].append(cat)
        by_cat = []
        for g, row in groups.items():
            if group_by == "overview_group":
                defn = next((o for o in six.overview if o["name"] == g), None) or {}
                entry = {"category": g, "display_name": defn.get("display_name"), "english_name": defn.get("english_name"),
                         "bill_categories": [make_id("bill_category", x) for x in row["members"]]}
            else:
                defn = six.bill_categories.get(g) or {}
                entry = {"category": make_id("bill_category", g), "display_name": defn.get("display_name"),
                         "english_name": defn.get("english_name")}
            entry.update({"income": round(row["income"], 2), "expense": round(row["expense"], 2),
                          "net": round(row["income"] - row["expense"], 2)})
            by_cat.append(entry)
        by_cat.sort(key=lambda r: -(abs(r["income"]) + abs(r["expense"])))
        income = m.get("income_total") or 0.0
        expense = m.get("expense_total") or 0.0
        out_months.append({"month": m.get("month"), "current_month_to_date": bool(m.get("current_month_to_date")),
                           "income_total": income, "expense_total": expense, "net": round(income - expense, 2),
                           "by_category": by_cat})
    # PRD 14.9 + 14.2: when the answer is too large, drop whole months from the OLD end (the newest months and the
    # in-progress month are what the request is about), keeping every category of the months returned.
    budget = cfg.RESPONSE_SIZE_CAP_BYTES - 6000
    dropped = 0
    while len(out_months) > 1 and json_size(out_months) * 2 > budget:
        out_months.pop(0)
        dropped += 1
    if dropped:
        ctx.warn("truncated", f"months: returned the {len(out_months)} most recent of {len(out_months) + dropped} requested "
                              f"({out_months[0]['month']}..{out_months[-1]['month']}) to stay under the response size cap; "
                              "request fewer months or filter categories for older months")
    mom = derive.month_over_month(out_months)
    data = {
        "company": make_id("company", player_id),
        "months": out_months,
        "month_over_month": mom,
        "current_month_to_date": next((m["month"] for m in out_months if m["current_month_to_date"]), None),
        "retention": {"first_month_available": ledger.get("first_month_available"), "last_month": ledger.get("last_month"),
                      "retention_years": ledger.get("retention_years"), "months_available": len(all_months),
                      "window_months": ledger.get("window_months"), "history_truncated": ledger.get("history_truncated"),
                      "note": "Monthly aggregates only, as retained by the game (pruned at year end). No daily ledger exists. "
                              "history_truncated=false means the export covers the game's whole retention period."},
        "balance_now": {"infinite": True} if ledger.get("balance_infinite") else ledger.get("balance_now"),
        "group_by": group_by,
        "totals_scope": "income_total/expense_total/net cover all categories of the month" if cat_filter else "all categories",
        "provenance": provenance(observed=["months", "retention", "balance_now"],
                                 definition=["by_category[].display_name/english_name"],
                                 derived=[("month_over_month", "D-FIN-1"), ("months[].net", "income_total - expense_total")],
                                 persistence={"months": "SAVE"}),
    }
    if months_n > len(all_months):
        ctx.add_unavailable("months", f"only {len(all_months)} months are retained by the game")
    return data


def specs() -> list[ToolSpec]:
    company_param = {"type": "string", "description": "company id (company:<actor id>) or name; default the player"}
    return [
        ToolSpec(name="list_companies",
                 description=("Player and competitor overview: cash, loans, cashflow label, regions, building counts, shares. "
                              + AI_CASH_NOTE + COMMON_SUFFIX),
                 scope="state", kind="runtime", list_tool=True, params={}, handler=list_companies),
        ToolSpec(name="get_company",
                 description=("One company in detail: identity, cash, loans (monthly payment derived, D-LOAN-1), shares, company "
                              "value replica (D-VAL-1, with inputs), total assets, stats, building summary; AI personality and "
                              "regions for AI companies. " + AI_CASH_NOTE + COMMON_SUFFIX),
                 scope="state", kind="runtime", params={"company": company_param}, handler=get_company),
        ToolSpec(name="get_finances",
                 description=("Monthly income and expense by bill category (or overview group), net, month-over-month deltas "
                              "(D-FIN-1), retention range and current balance, from the game's retained monthly ledger "
                              "(history snapshot; fresh=true refreshes the history family). No daily data exists." + COMMON_SUFFIX),
                 scope="history", kind="runtime",
                 params={"company": company_param,
                         "months": {"type": "integer", "minimum": 1, "maximum": 60, "default": 6},
                         "categories": {"type": "array", "items": {"type": "string"}, "maxItems": 50},
                         "group_by": {"type": "string", "enum": ["category", "overview_group"], "default": "category"}},
                 handler=get_finances),
    ]
