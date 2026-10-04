"""review_routes (PRD addendum 6.5)."""

from __future__ import annotations

from collections import Counter

from ..app import CallContext
from ..index import make_id
from ..tools.common import resolve_building_key, resolve_product
from . import economics as eco
from .basis import pin_basis, route_stock_contradictions
from .common import SEVERITY_ORDER, advisor_provenance, bounded_page, bref, pref, require_player, section_or_degrade
from .contract import Advice, lower, parameter
from .facts import PlayerFacts

RULE_ORDER = {k: i for i, k in enumerate(eco.ROUTE_FINDING_KINDS)}


def review_routes(ctx: CallContext) -> dict:
    basis = pin_basis(ctx, static="optional", state_sections=("routes_player",))
    ix = ctx.state_index()
    require_player(ix)
    adv = Advice("review_routes", basis)
    f = PlayerFacts(ix)
    a = ctx.args
    product = resolve_product(ctx, a["product"]) if a.get("product") else None
    origin = resolve_building_key(ctx, a["origin"], "origin") if a.get("origin") else None
    dest = resolve_building_key(ctx, a["destination"], "destination") if a.get("destination") else None
    kinds = set(a.get("kinds") or eco.ROUTE_FINDING_KINDS)
    min_sev = a.get("min_severity") or "low"
    threshold = float(a.get("cost_ratio_threshold") or 0.2)
    shops_ok = section_or_degrade(ctx, adv, "state", "shops", "sale price falls back to the market price") is not None
    market_ok = section_or_degrade(ctx, adv, "state", "market", "high_unit_cost not evaluated without prices") is not None
    section_or_degrade(ctx, adv, "state", "buildings_player", "route stock cross-checks omitted")
    routes = [r for r in f.routes
              if (product is None or r.get("product") == product) and (origin is None or r["origin"] == origin)
              and (dest is None or dest in (r["destination"], r.get("endpoint")))]
    contradicted = set()
    for row in route_stock_contradictions(ix, routes):
        adv.contradiction(row)
        contradicted.add(row["subject"])
    prices: dict[str, dict] = {}
    rows = []
    reviewed = 0
    for r in sorted(routes, key=lambda x: x["route_key"]):
        reviewed += 1
        p = r.get("product")
        if p not in prices:
            prices[p] = f.sale_price(p) if (shops_ok or market_ok) else {"value": None, "confidence": None, "basis": None}
            if prices[p].get("value") is None:
                adv.degrade(f"sale_price:{p}", "missing_value", "high_unit_cost not evaluated for this product")
        sharing = len(f.routes_by_dest_product.get((r.get("endpoint") or r["destination"], p), []))
        rid = make_id("route", r["route_key"])
        for fnd in eco.route_findings(r, prices[p].get("value"), threshold, sharing):
            if fnd["kind"] not in kinds or SEVERITY_ORDER[fnd["severity"]] > SEVERITY_ORDER[min_sev]:
                continue
            if fnd["kind"] == "high_unit_cost":
                conf = adv.conf("medium", (prices[p].get("confidence") or {}).get("factors") or [])
                fnd["evidence"]["sale_price"]["confidence"] = conf
                fnd["evidence"]["sale_price"]["basis"] = prices[p].get("basis")
                adv.assume("A-FULL-VEHICLES", "A-PRICE-SHOP")
            else:
                conf = adv.conf("high")
            if rid in contradicted and fnd["kind"] in ("zero_dispatch_now", "max_send_saturated", "underfilled_dispatch"):
                conf = lower(conf, "contradictory_inputs")
                adv.track(conf)
            if fnd["kind"] == "zero_dispatch_now":
                adv.assume("A-DISPATCH-NOT-THROUGHPUT")
            rows.append({"severity": fnd["severity"], "kind": fnd["kind"], "route_id": rid, "origin": bref(ix, r["origin"]),
                         "destination": bref(ix, r["destination"]), "product": pref(ix, p), "transport_mode": r.get("transport_mode"),
                         "evidence": fnd["evidence"], "suggestion": fnd["suggestion"], "confidence": conf})
    rows.sort(key=lambda x: (SEVERITY_ORDER[x["severity"]], RULE_ORDER.get(x["kind"], 99), x["route_id"]))
    for i, x in enumerate(rows, 1):
        x["rank"] = i
    summary = {"routes_reviewed": reviewed, "findings": len(rows),
               "findings_by_kind": dict(Counter(x["kind"] for x in rows)),
               "findings_by_severity": dict(Counter(x["severity"] for x in rows)),
               "routes_with_findings": len({x["route_id"] for x in rows})}
    data, bounded = bounded_page(ctx, "findings", rows)
    adv.inputs = {"cost_ratio_threshold": parameter(threshold, "ratio"), "underfilled_fraction": parameter(0.5, "ratio", "constant"),
                  "min_severity": min_sev, "kinds": sorted(kinds)}
    adv.method("findings[]", "D-ADV-ROUTE-RULES-1")
    adv.method("findings[high_unit_cost].evidence.cost_per_unit_at_capacity", "D-ROUTE-1")
    result = {"summary": summary, "findings": data["findings"]}
    if bounded:
        result["page_bounded_by_size"] = True
    return adv.finish(result,
                      advisor_provenance(["route fields (errors, paused, Max Send, Min Keep, dispatch_amount_now)", "shop prices"],
                                         [], adv.methods))
