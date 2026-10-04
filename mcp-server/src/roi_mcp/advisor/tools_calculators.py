"""loan_calculator, route_calculator, spatial_analysis and forecast (phase-2 addendum 4.2, 4.3, 4.6, 4.8)."""

from __future__ import annotations

from .. import derive
from ..app import CallContext
from ..errors import ToolError
from ..index import make_id
from ..tools.common import resolve_building_key, resolve_product
from ..util import month_key, normalize_name
from . import calculators as calc
from .basis import pin_basis
from .common import advisor_provenance, bref, pref, require_player, section_or_degrade
from .contract import Advice, confidence, derived, estimate, game_computed, num, observed, parameter
from .facts import PlayerFacts

STRAIGHT = {"distance_kind": "straight_line", "is_path_distance": False}
SOURCE_FORMULA = {"own": "ManualDestinationDispatchCost", "TruckDepot": "TruckDepotDispatchCost",
                  "TrainTerminal": "TrainTerminalDispatchCost"}


def _last_complete_net(ctx: CallContext, adv: Advice, basis) -> tuple[float | None, str | None]:
    if basis.history is None:
        return None, None
    ledger = section_or_degrade(ctx, adv, "history", "ledger_player", "cash-flow comparison omitted")
    months = sorted((ledger or {}).get("months") or [], key=lambda m: month_key(m.get("month")) or (0, 0))
    complete = [m for m in months if not m.get("current_month_to_date")]
    if not complete:
        return None, None
    m = complete[-1]
    inc, exp = num(m.get("income_total")), num(m.get("expense_total"))
    return (inc - exp if inc is not None and exp is not None else None), m.get("month")


# ====================================================================== loan_calculator

def loan_calculator(ctx: CallContext) -> dict:
    a = ctx.args
    basis = pin_basis(ctx, static="optional", history="optional", state_sections=("companies",))
    ix = ctx.state_index()
    require_player(ix)
    adv = Advice("loan_calculator", basis)
    company = (ix.companies.get(ix.player_actor_id) or {})
    detail = a.get("detail") or "standard"
    explicit = [k for k in ("principal", "apr", "duration_months") if k in a]
    if a.get("loan") and explicit:
        raise ToolError("invalid_argument", "pass either loan (a catalogue loan) or principal/apr/duration_months, not both")
    if not a.get("loan") and not a.get("existing") and len(explicit) != 3:
        raise ToolError("invalid_argument", "pass loan, or all of principal, apr and duration_months, or existing: true")
    rows_max = 48 if detail == "full" else 12
    result: dict = {}
    adv.assume("A-LOAN-MODIFIER")
    if a.get("loan") or explicit:
        if a.get("loan"):
            q = normalize_name(a["loan"])
            infos = list(ctx.static_index().loan_infos)
            hit = next((li for li in infos if q in (normalize_name(li.get("name")), normalize_name(li.get("title")))), None)
            if hit is None:
                raise ToolError("not_found", f"loan {a['loan']!r} is not a loan info in the catalogue",
                                candidates=[{"id": li.get("name"), "kind": "loan_info"} for li in infos[:10]])
            principal, apr, dur, grace = num(hit.get("amount")), num(hit.get("apr")), num(hit.get("duration_months")), \
                num(hit.get("grace_months"))
            grace_unknown = grace is None
            if grace_unknown:
                grace = 0
                adv.degrade("loan.grace_months", "missing_value", "grace months unknown: payment offsets null")
            src = "static.loan_infos[]"
            inputs = {"loan": hit.get("name"), "type": hit.get("type"), "principal": parameter(principal, "money", src),
                      "apr": parameter(apr, "ratio", src), "duration_months": parameter(dur, "months", src),
                      "grace_months": parameter(grace, "months", src)}
            if hit.get("type") == "SETTLEMENT":
                adv.degrade("loan.modifier", "missing_value", "settlement loan modifier not exported: modifier 1 unless given")
        else:
            principal, apr, dur, grace = float(a["principal"]), float(a["apr"]), int(a["duration_months"]), int(a.get("grace_months") or 0)
            grace_unknown = False
            inputs = {"principal": parameter(principal, "money"), "apr": parameter(apr, "ratio"),
                      "duration_months": parameter(dur, "months"), "grace_months": parameter(grace, "months")}
        modifier = float(a.get("modifier") or 1.0)
        inputs["modifier"] = parameter(modifier, "ratio")
        if principal is None or apr is None or not dur:
            raise ToolError("section_unavailable", "the catalogue loan lacks amount, apr or duration",
                            details={"section": "loan_infos", "reason": "incomplete_loan_info"})
        s = calc.loan_schedule(principal, apr, int(dur), int(grace), modifier, max_rows=rows_max)
        net, month = _last_complete_net(ctx, adv, basis)
        cash = num((company.get("cash") or {}).get("value"))
        pay = s["payment"]
        cf = {"last_complete_month": month, "net": observed(net, "money/30d", "history.ledger_player (income_total - expense_total)"),
              "net_after_payment": derived(net - pay if net is not None else None, "money/30d", "net - payment"),
              "payment_share_of_net": derived(pay / net if net and net > 0 else None, "ratio", "payment / net"),
              "cash": observed(cash, "money", "state.companies[player].cash.value"),
              "months_covered_by_cash": derived(cash / pay if cash is not None and pay else None, "months", "cash / payment")}
        if net is None:
            adv.degrade("history.ledger_player", "family_unavailable", "cash-flow comparison null")
        result["loan"] = {
            "inputs": inputs,
            "payment": derived(pay, "money/30d", "D-LOAN-1: principal x (1 + apr) / duration x modifier", 2),
            "total_repayment": derived(s["total_repayment"], "money", "payment x duration", 2),
            "financing_cost": derived(s["financing_cost"], "money", "total_repayment - principal", 2),
            "first_payment_month_offset": None if grace_unknown else s["first_payment_month_offset"],
            "last_payment_month_offset": None if grace_unknown else s["last_payment_month_offset"],
            "schedule": [{"payment_number": r["payment_number"], "month_offset": None if grace_unknown else r["month_offset"],
                          "payment": derived(r["payment"], "money/30d", "D-LOAN-1", 2),
                          "remaining_balance_after": derived(r["remaining_balance_after"], "money", "payment x remaining payments", 2),
                          "early_repay_after": derived(r["early_repay_after"], "money", "remaining / duration x principal", 2)}
                         for r in s["schedule"]],
            "schedule_rows_omitted": s["rows_omitted"],
            "cash_flow": cf,
            "caveat": "month_offset 1 = first month start after taking the loan; the exact first instalment month is not verified",
        }
        adv.method("loan.payment", "D-LOAN-1")
        adv.method("loan.schedule", "D-LOAN-SCHED-1")
    if a.get("existing"):
        loans = company.get("loans")
        if loans is None:
            adv.degrade("state.companies[player].loans", "missing_value", "existing loans unavailable")
        rows = []
        for i, l in enumerate(loans or []):
            p = derive.loan_monthly_payment(l.get("principal"), l.get("apr"), l.get("duration_months"))
            src = f"state.companies[player].loans[{i}]"
            rows.append({"type": l.get("type"), "title": l.get("title"),
                         "principal": observed(l.get("principal"), "money", src + ".principal"),
                         "apr": observed(l.get("apr"), "ratio", src + ".apr"),
                         "remaining_payments": observed(l.get("remaining_payments"), "months", src + ".remaining_payments"),
                         "grace_months_left": observed(l.get("grace_months_left"), "months", src + ".grace_months_left"),
                         "payment": derived(p.get("value"), "money/30d", "D-LOAN-1", 2),
                         "remaining_to_pay": derived(p["value"] * l["remaining_payments"] if p.get("value") is not None and
                                                     num(l.get("remaining_payments")) is not None else None, "money", "payment x remaining", 2),
                         "early_repay_amount": observed(l.get("early_repay_amount"), "money", src + ".early_repay_amount")})
        result["existing_loans"] = rows
        result["max_loans"] = company.get("max_loans")
    return adv.finish(result, advisor_provenance(["cash", "existing loans", "ledger net"], ["loan infos"], adv.methods))


# ====================================================================== route_calculator

def _location_coords(ix, key: str) -> dict | None:
    return ix.coords(key)


def route_calculator(ctx: CallContext) -> dict:
    a = ctx.args
    basis = pin_basis(ctx, static="required", state_sections=("session",))
    ix = ctx.state_index()
    require_player(ix)
    adv = Advice("route_calculator", basis)
    f = PlayerFacts(ix)
    six = f.six
    origin = resolve_building_key(ctx, a["origin"], "origin")
    dest = resolve_building_key(ctx, a["destination"], "destination")
    if origin == dest:
        raise ToolError("invalid_argument", "origin and destination are the same building")
    product = resolve_product(ctx, a["product"]) if a.get("product") else None
    source = a.get("source") or "own"
    routes_ok = section_or_degrade(ctx, adv, "state", "routes_player", "no existing-route check, no detour factor, no observed capacity") is not None
    o, d = ix.coords(origin), ix.coords(dest)
    if not o or not d or o.get("x") is None or d.get("x") is None:
        raise ToolError("section_unavailable", "coordinates of origin or destination are unknown",
                        details={"section": "buildings", "reason": "coordinates_missing"})
    adv.assume("A-STRAIGHT-LINE", "A-ACTOR-MODIFIER-1")
    dist = derive.distances(o["x"], o["y"], d["x"], d["y"])
    eu = dist["euclidean"]
    existing = None
    if routes_ok:
        cand = [r for r in ix.routes_by_origin.get(origin, []) if dest in (r["destination"], r.get("endpoint"))]
        same = [r for r in cand if r.get("product") == product] if product else cand
        r = (same or cand or [None])[0]
        if r is not None:
            src = f"state.routes_player[{r['route_key']}]"
            existing = {"route_id": make_id("route", r["route_key"]), "product": make_id("product", r.get("product")),
                        "authoritative": True,
                        "distance_tiles": game_computed(r.get("distance_tiles"), "tiles", src + ".distance_tiles"),
                        "dispatch_cost": game_computed(r.get("dispatch_cost"), "money", src + ".dispatch_cost"),
                        "vehicle_capacity": game_computed(r.get("vehicle_capacity"), "units", src + ".vehicle_capacity"),
                        "source": r.get("source")}
    det = calc.detour_range(((r.get("distance_tiles"), derive.distances(*(ix.coords(r["origin"]) or {}).values(),
                                                                         *(ix.coords(r["destination"]) or {}).values())["euclidean"])
                             for r in f.routes if r.get("path_status") == "cached" and ix.coords(r["origin"]) and ix.coords(r["destination"])
                             # state-trading routes measure their path to a trade point, not to the destination's
                             # coordinates (live: ratios 0.24-0.40), so they are no detour samples
                             and r.get("destination_kind") != "state_trading")
                            if routes_ok else [])
    path_range = None
    conf_det = adv.conf("medium", [] if det.get("available") and det["samples"] >= 5 else ["partial_inputs"])
    if det.get("available") and eu is not None:
        path_range = {"min": estimate(eu * det["min"], "tiles", "observed detour factor x straight line", conf_det),
                      "median": estimate(eu * det["median"], "tiles", "observed detour factor x straight line", conf_det),
                      "max": estimate(eu * det["max"], "tiles", "observed detour factor x straight line", conf_det),
                      "samples": det["samples"]}
    else:
        adv.degrade("detour_factor", "missing_value", det.get("reason") or "routes unavailable")
    formula_name = SOURCE_FORMULA[source]
    text = six.formulas.get(formula_name)
    diff = (ix.session.get("difficulty") or {}).get("dispatch")
    factors = ["mechanic_unverified"] if source != "own" else []

    def cost_at(tiles):
        return derive.straight_line_cost_estimate(text, tiles, diff, formula_name=formula_name, route_exists=existing is not None)

    c_straight = cost_at(eu)
    cconf = adv.conf("medium", factors + (["partial_inputs"] if c_straight.get("value") is None else []))
    trip = {"at_straight_line": estimate(c_straight.get("value"), "money", "D-ROUTE-CALC-1 / " + formula_name, cconf, 2),
            "formula": formula_name, "formula_text": text,
            "difficulty_dispatch": observed(diff, "ratio", "state.session.difficulty.dispatch")}
    if path_range:
        lo, hi = cost_at(path_range["min"]["value"]), cost_at(path_range["max"]["value"])
        trip["range_with_detour"] = [estimate(lo.get("value"), "money", "D-ROUTE-CALC-1", cconf, 2),
                                     estimate(hi.get("value"), "money", "D-ROUTE-CALC-1", cconf, 2)]
    if text is None:
        adv.degrade(f"static.formulas.{formula_name}", "missing_value", "cost per trip null")
    cap = num(a.get("vehicle_capacity"))
    cap_basis = "parameter" if cap is not None else None
    if cap is None and routes_ok:
        caps = [num(r.get("vehicle_capacity")) for r in f.routes if r.get("source") == source and (product is None or r.get("product") == product)]
        caps = [c for c in caps if c]
        if not caps:
            caps = [c for c in (num(r.get("vehicle_capacity")) for r in f.routes if r.get("source") == source) if c]
        if caps:
            from .economics import median
            cap, cap_basis = median(caps), "observed_median_same_source"
    if cap is None:
        adv.degrade("vehicle_capacity", "missing_value", "cost per unit and trips null")
    else:
        adv.assume("A-FULL-VEHICLES")
    cpu = c_straight.get("value") / cap if cap and c_straight.get("value") is not None else None
    # throughput constraints (derivable only)
    flow = {}
    if product:
        b_o = f.by_key.get(origin)
        sup = None
        if b_o is not None:
            rate = f.rates.get(origin) or {}
            sup = next((num(x.get("per_30d")) for x in rate.get("outputs") or [] if x.get("product") == product), None)
        flow["origin_supply_per_30d"] = estimate(sup, "units/30d", "D-RATE-1/2", adv.conf("medium"))
        dem = None
        if dest in ix.shops:
            sp = next((p for p in ix.shops[dest].get("products") or [] if p.get("product") == product), None)
            interval = (ix.cities.get(ix.shops[dest].get("city_id")) or {}).get("consumption_interval_days")
            dem = derive.per_interval_to_30d((sp or {}).get("demand_for_player"), interval) if sp else None
            basis_d = "D-SHOP-1 (shop demand for the player)"
        else:
            rate = f.rates.get(dest) or {}
            dem = next((num(x.get("per_30d")) for x in rate.get("inputs") or [] if x.get("product") == product), None)
            basis_d = "D-RATE-1/2 (consumer need)"
        flow["destination_demand_per_30d"] = estimate(dem, "units/30d", basis_d, adv.conf("medium"))
        known = [v for v in (sup, dem) if v is not None]
        fc = min(known) if known else None
        flow["flow_cap_per_30d"] = estimate(fc, "units/30d", "min(known supply, demand)", adv.conf("medium", [] if len(known) == 2 else ["partial_inputs"]))
        trips = -(-fc // cap) if fc is not None and cap else None
        flow["trips_per_30d_at_full_vehicles"] = estimate(trips, "count", "ceil(flow / capacity)", adv.conf("medium"))
        flow["transport_cost_per_30d"] = estimate(trips * c_straight["value"] if trips is not None and c_straight.get("value") is not None else None,
                                                  "money/30d", "trips x cost per trip", cconf, 2)
        flow["travel_time"] = None
        adv.degrade("travel_time", "missing_value", "vehicle speeds are not exported: travel time and vehicle turnover unavailable")
    result = {"origin": bref(ix, origin), "destination": bref(ix, dest), "product": pref(ix, product), "source": source,
              "straight_line": {"euclidean": derived(eu, "tiles", "D-DIST-1"), "chebyshev": derived(dist["chebyshev"], "tiles", "D-DIST-1"),
                                **STRAIGHT},
              "path_tiles_range_estimate": path_range, "existing_route": existing,
              "cost_per_trip": trip,
              "vehicle_capacity": {**(parameter(cap, "units") if cap_basis == "parameter" else estimate(cap, "units", "median observed capacity", adv.conf("medium"))),
                                   "basis": cap_basis},
              "cost_per_unit_at_capacity": estimate(cpu, "money/unit", "cost per trip / capacity", cconf),
              "throughput": flow or None,
              "warning": "Straight-line distance is not a road or rail path length; real routes are usually longer."}
    adv.method("cost_per_trip", "D-ROUTE-CALC-1")
    return adv.finish(result, advisor_provenance(["coordinates", "existing routes", "difficulty"], ["dispatch cost formulas"], adv.methods))


# ====================================================================== spatial_analysis

MATRIX_MAX, HUB_CANDIDATES_MAX, HUB_POINTS_MAX, CHAIN_PRODUCERS_MAX = 12, 20, 30, 15


def _resolve_locations(ctx: CallContext, ix, values: list, label: str) -> tuple[list[dict], list[str], list[str]]:
    out, dup, nocoord = [], [], []
    seen = set()
    for v in values:
        ent = ctx.resolver().resolve(v, ("building", "shop", "city", "region"), label)
        if ent.id in seen:
            dup.append(ent.id)
            continue
        seen.add(ent.id)
        if ent.kind in ("building", "shop"):
            c = ix.coords(ent.key)
            xy = (c["x"], c["y"]) if c and c.get("x") is not None else None
            name = (ix.building(ent.key) or {}).get("display_name")
        elif ent.kind == "city":
            cty = ix.cities.get(int(ent.key)) or {}
            xy = (cty.get("center_x"), cty.get("center_y")) if cty.get("center_x") is not None else None
            name = cty.get("name")
        else:
            rg = ix.regions.get(ent.key) or {}
            xy = (rg.get("center_x"), rg.get("center_y")) if rg.get("center_x") is not None else None
            name = rg.get("name")
        if xy is None:
            nocoord.append(ent.id)
            continue
        out.append({"id": ent.id, "name": name, "xy": xy})
    return out, dup, nocoord


def spatial_analysis(ctx: CallContext) -> dict:
    a = ctx.args
    mode = a["mode"]
    basis = pin_basis(ctx, static="optional")
    ix = ctx.state_index()
    require_player(ix)
    adv = Advice("spatial_analysis", basis)
    adv.assume("A-STRAIGHT-LINE")
    detail = a.get("detail") or "standard"
    result: dict = {"mode": mode, **STRAIGHT}
    if mode in ("matrix", "hub") and not a.get("locations"):
        raise ToolError("invalid_argument", f"mode {mode!r} needs locations")
    if mode == "matrix":
        if len(a["locations"]) > MATRIX_MAX:
            raise ToolError("invalid_argument", f"matrix accepts at most {MATRIX_MAX} locations")
        locs, dup, nocoord = _resolve_locations(ctx, ix, a["locations"], "locations[]")
        pairs = []
        for i in range(len(locs)):
            for j in range(i + 1, len(locs)):
                row = {"from": locs[i]["id"], "to": locs[j]["id"],
                       "euclidean": derived(calc.euclid(locs[i]["xy"], locs[j]["xy"]), "tiles", "D-DIST-1")}
                if detail == "full":
                    row["chebyshev"] = derived(calc.chebyshev(locs[i]["xy"], locs[j]["xy"]), "tiles", "D-DIST-1")
                pairs.append(row)
        result.update({"locations": [{"id": l["id"], "name": l["name"], "x": derived(l["xy"][0], "tiles", "coordinates"),
                                      "y": derived(l["xy"][1], "tiles", "coordinates")} for l in locs],
                       "pairs": pairs, "duplicates_removed": dup, "without_coordinates": nocoord})
    elif mode == "hub":
        if len(a["locations"]) > HUB_POINTS_MAX or len(a.get("candidates") or []) > HUB_CANDIDATES_MAX:
            raise ToolError("invalid_argument", f"hub accepts at most {HUB_POINTS_MAX} locations and {HUB_CANDIDATES_MAX} candidates")
        weights = a.get("weights") or []
        if weights and len(weights) != len(a["locations"]):
            raise ToolError("invalid_argument", "weights must have one entry per location")
        locs, dup, nocoord = _resolve_locations(ctx, ix, a["locations"], "locations[]")
        wmap = {}
        for v, w in zip(a["locations"], weights or [1.0] * len(a["locations"])):
            wmap.setdefault(ctx.resolver().resolve(v, ("building", "shop", "city", "region"), "locations[]").id, float(w))
        pts = [(l["xy"], wmap.get(l["id"], 1.0)) for l in locs]
        cands, cdup, cnocoord = _resolve_locations(ctx, ix, a.get("candidates") or a["locations"], "candidates[]")
        ranking = sorted(({"id": c["id"], "name": c["name"],
                           "weighted_distance_sum": derived(calc.weighted_sum(c["xy"], pts), "tiles", "sum w_i x euclid")}
                          for c in cands), key=lambda r: (r["weighted_distance_sum"]["value"], r["id"]))
        for i, r in enumerate(ranking, 1):
            r["rank"] = i
        gm = calc.geometric_median(pts)
        result.update({"ranking": ranking,
                       "geometric_median": {"x": derived(gm["point"][0] if gm["point"] else None, "tiles", "Weiszfeld"),
                                            "y": derived(gm["point"][1] if gm["point"] else None, "tiles", "Weiszfeld"),
                                            "iterations": gm["iterations"], "converged": gm["converged"],
                                            "weighted_distance_sum": derived(calc.weighted_sum(gm["point"], pts) if gm["point"] else None,
                                                                             "tiles", "sum w_i x euclid"),
                                            "note": "unconstrained reference point, not a buildable location"},
                       "duplicates_removed": dup + cdup, "without_coordinates": nocoord + cnocoord})
    else:  # chain
        if not a.get("product"):
            raise ToolError("invalid_argument", "mode 'chain' needs product")
        product = resolve_product(ctx, a["product"])
        f = PlayerFacts(ix)
        six = f.six
        prods = [p["building"] for p in f.producers.get(product, []) if p["enabled"]][:CHAIN_PRODUCERS_MAX]
        rows = []
        for b in sorted(prods, key=lambda b: b["key"]):
            bxy = (b.get("x"), b.get("y"))
            legs_in = []
            recipe = six.recipes.get(b.get("recipe")) or {}
            for ing in recipe.get("ingredients") or []:
                sup = [p["building"] for p in f.producers.get(ing["product"], []) if p["enabled"]]
                best = min(((calc.euclid(bxy, (s.get("x"), s.get("y"))), s["key"]) for s in sup if s.get("x") is not None), default=None)
                legs_in.append({"input": make_id("product", ing["product"]),
                                "nearest_player_supplier": make_id("building", best[1]) if best else None,
                                "euclidean": derived(best[0] if best else None, "tiles", "D-DIST-1")})
            outs = [r for r in ix.routes_by_origin.get(b["key"], []) if r.get("product") == product]
            legs_out = [{"destination": make_id("building", r["destination"]), "route_id": make_id("route", r["route_key"]),
                         "euclidean": derived(calc.euclid(bxy, tuple((ix.coords(r["destination"]) or {}).values())), "tiles", "D-DIST-1")}
                        for r in outs if ix.coords(r["destination"])]
            if not legs_out:
                shops = [s for s in ix.shops_by_product.get(product, []) if not s.get("is_dead")]
                best = min(((calc.euclid(bxy, tuple(ix.coords(s["building"]).values())), s["building"]) for s in shops if ix.coords(s["building"])),
                           default=None)
                if best:
                    legs_out = [{"destination": make_id("building", best[1]), "route_id": None, "nearest_shop": True,
                                 "euclidean": derived(best[0], "tiles", "D-DIST-1")}]
            tot_in = sum(l["euclidean"]["value"] or 0 for l in legs_in)
            tot_out = min((l["euclidean"]["value"] for l in legs_out if l["euclidean"]["value"] is not None), default=None)
            rows.append({"producer": bref(ix, b["key"]), "inbound": legs_in, "outbound": legs_out[:5],
                         "inbound_total": derived(tot_in if legs_in else None, "tiles", "sum of nearest supplier legs"),
                         "nearest_outbound": derived(tot_out, "tiles", "min outbound leg")})
        result.update({"product": pref(ix, product), "producers": rows,
                       "producers_omitted": max(len([p for p in f.producers.get(product, []) if p["enabled"]]) - CHAIN_PRODUCERS_MAX, 0)})
    adv.method("distances", "D-SPATIAL-1")
    return adv.finish(result, advisor_provenance(["coordinates", "routes"], [], adv.methods))


# ====================================================================== forecast

def forecast(ctx: CallContext) -> dict:
    a = ctx.args
    kind = a["kind"]
    basis = pin_basis(ctx, static="optional", history="optional" if kind == "cash" else "none",
                      state_sections=("companies",) if kind == "cash" else ("buildings_player",))
    ix = ctx.state_index()
    require_player(ix)
    adv = Advice("forecast", basis)
    adv.assume("A-TREND-PERSISTS")
    result: dict = {"kind": kind}
    if kind == "cash":
        months_n = int(a.get("months") or 3)
        cash = num(((ix.companies.get(ix.player_actor_id) or {}).get("cash") or {}).get("value"))
        nets, used = [], []
        if basis.history is not None:
            ledger = section_or_degrade(ctx, adv, "history", "ledger_player", "cash forecast unavailable")
            months = sorted((ledger or {}).get("months") or [], key=lambda m: month_key(m.get("month")) or (0, 0))
            complete = [m for m in months if not m.get("current_month_to_date")][-months_n:]
            for m in complete:
                inc, exp = num(m.get("income_total")), num(m.get("expense_total"))
                nets.append(inc - exp if inc is not None and exp is not None else None)
                used.append(m.get("month"))
        fc = calc.cash_forecast(cash, nets)
        result.update({"available": fc["available"], "cash": observed(cash, "money", "state.companies[player].cash.value"),
                       "sample_months": used,
                       "sample_net": [observed(n, "money/30d", f"history.ledger_player {m}") for n, m in zip(nets, used)]})
        if not fc["available"]:
            result["reason"] = fc["reason"]
            adv.degrade("history.ledger_player", "missing_value", fc["reason"])
        else:
            conf = adv.track(fc.get("confidence") or confidence("medium"))
            result.update({"projection": fc["projection"], "already_negative": fc["already_negative"],
                           "mean_net": derived(fc["mean_net"], "money/30d", "mean of sample nets"),
                           "months_to_zero_pessimistic": estimate((fc.get("months_to_zero_range") or [None, None])[0], "months",
                                                                  "D-FORECAST-1 cash / -min(net)", conf, 2),
                           "months_to_zero_at_mean": estimate((fc.get("months_to_zero_range") or [None, None])[1], "months",
                                                              "D-FORECAST-1 cash / -mean(net)", conf, 2),
                           "reason": fc.get("reason")})
        adv.inputs = {"months": parameter(months_n, "count")}
    else:
        key = resolve_building_key(ctx, a["building"]) if a.get("building") else None
        product = resolve_product(ctx, a["product"]) if a.get("product") else None
        if key is None or product is None:
            raise ToolError("invalid_argument", "kind 'stock' needs building and product")
        b = (PlayerFacts(ix).by_key.get(key))
        if b is None:
            raise ToolError("invalid_argument", "building is not a player building with inventory detail")
        inv = next((i for i in b.get("inventory") or [] if i.get("product") == product), None)
        if inv is None:
            raise ToolError("not_found", f"the building holds no inventory row for {product!r}")
        entries = ctx.app.window.snapshots(basis.state.world_session)
        samples = [((e.digest or {}).get("game_day"), ((e.digest or {}).get("buildings") or {}).get(key, {}).get("inventory", {}).get(product))
                   for e in entries if e.digest]
        fc = calc.stock_forecast(samples, inv.get("slots"))
        result.update({"building": bref(ix, key), "product": pref(ix, product), "available": fc["available"],
                       "count_now": observed(inv.get("count"), "units", "state.buildings_player[].inventory[].count"),
                       "slots": observed(inv.get("slots"), "units", "state.buildings_player[].inventory[].slots"),
                       "samples": fc.get("samples")})
        if not fc["available"]:
            result["reason"] = fc["reason"]
            adv.degrade("in_memory_window", "missing_value", fc["reason"])
        else:
            conf = adv.track(fc["confidence"])
            lo, hi = fc["rate_range_per_day"]
            result.update({"trend": fc["trend"], "from_game_day": fc["from_game_day"], "to_game_day": fc["to_game_day"],
                           "rate_per_game_day": estimate(fc["rate_per_day"], "units/day", "D-FORECAST-1 (count change / game days)", conf),
                           "rate_range_per_game_day": [estimate(lo, "units/day", "D-FORECAST-1", conf), estimate(hi, "units/day", "D-FORECAST-1", conf)]})
            for k in ("days_to_full", "days_to_empty"):
                if k in fc:
                    result[k] = estimate(fc[k], "days", "D-FORECAST-1", conf, 2)
                    rng = fc.get(f"{k}_range") or [None, None]
                    result[f"{k}_range"] = [estimate(rng[0], "days", "D-FORECAST-1", conf, 2), estimate(rng[1], "days", "D-FORECAST-1", conf, 2)]
    adv.method("forecast", "D-FORECAST-1")
    return adv.finish(result, advisor_provenance(["cash", "ledger nets", "in-memory inventory samples"], [], adv.methods))
