"""what_if and what_changed (PRD addendum 6.7, 6.8). Scenarios are computed on copies; nothing is applied."""

from __future__ import annotations

from .. import derive
from ..app import CallContext
from ..errors import ToolError
from ..index import make_id
from ..tools.common import resolve_building_key, theoretical_rate
from ..util import month_key, normalize_name
from . import economics as eco
from .basis import pin_basis
from .common import assume_from, advisor_provenance, bref, pref, require_player, section_or_degrade
from .contract import Advice, derived, estimate, merge_confidence, num, observed, parameter
from .facts import PlayerFacts

CHANGE_FIELDS = {
    "add_buildings": ({"recipe"}, {"count", "building_type"}),
    "remove_building": ({"building"}, set()),
    "set_efficiency": ({"building", "index"}, set()),
    "change_recipe": ({"building", "recipe"}, set()),
    "set_max_send": ({"route", "value"}, set()),
    "set_min_keep": ({"route"}, {"value", "keep_all"}),
    "take_loan": ({"loan"}, set()),
}


def _check_change(change: dict) -> str:
    typ = change.get("type")
    required, optional = CHANGE_FIELDS[typ]
    extra = set(change) - {"type"} - required - optional
    if extra:
        raise ToolError("invalid_argument", f"change: field(s) {sorted(extra)} do not apply to type {typ!r}",
                        hint=f"{typ} accepts {sorted(required | optional)}")
    missing = sorted(required - set(change))
    if missing:
        raise ToolError("invalid_argument", f"change: type {typ!r} needs {missing}")
    if typ == "set_min_keep" and ("value" in change) == bool(change.get("keep_all")):
        raise ToolError("invalid_argument", "change: set_min_keep needs exactly one of value (0..99) or keep_all: true")
    return typ


def _player_building(ctx: CallContext, f: PlayerFacts, value: str) -> dict:
    key = resolve_building_key(ctx, value)
    b = f.by_key.get(key)
    if b is None:
        raise ToolError("invalid_argument", f"building {value!r} is not a player building with full detail",
                        hint="what_if analyses the player company only.")
    return b


def _route(ctx: CallContext, f: PlayerFacts, value: str) -> dict:
    key = value[len("route:"):] if value.startswith("route:") else value
    r = f.ix.routes.get(key)
    if r is None or r not in f.routes:
        raise ToolError("not_found", f"route {value!r} is not a configured route of the player in the current snapshot",
                        hint="Use list_routes to find route ids (route:<origin>|<product>|<destination>|<source>|<n>).")
    return r


def _balance_rows(f: PlayerFacts, adv: Advice, deltas: dict[str, dict], conf: dict) -> list[dict]:
    """Per product: supply/need before and after (deltas: product -> {"supply": d, "need": d})."""
    rows = []
    for p in sorted(deltas):
        sup, approx, _ = f.supply(p)
        need = f.internal_need(p)
        ds, dn = deltas[p].get("supply", 0.0), deltas[p].get("need", 0.0)
        c = adv.track(dict(conf)) if conf else None
        rows.append({"product": pref(f.ix, p),
                     "supply_before": estimate(sup, "units/30d", "D-RATE-1/2", c or conf),
                     "supply_after": estimate(sup + ds if ds is not None else None, "units/30d", "D-RATE-1/2", c or conf),
                     "internal_need_before": estimate(need, "units/30d", "D-RATE-1/2", c or conf),
                     "internal_need_after": estimate(need + dn if dn is not None else None, "units/30d", "D-RATE-1/2", c or conf),
                     "balance_after": estimate(sup + ds - need - dn if ds is not None and dn is not None else None, "units/30d",
                                               "D-SUPDEM-1", c or conf)})
    return rows


def _diff(new: dict, old: dict, p: str) -> float | None:
    """new - old for one product; a product absent from one side contributes 0, an unknown rate makes it None."""
    a, b = new.get(p, 0.0), old.get(p, 0.0)
    return None if a is None or b is None else a - b


def _shared_room(r: dict, rows: list[dict], max_send: int) -> dict:
    """A shared Max Send room is consumed by whichever origin dispatches first, so the per-route amounts are not
    additive: the combined amount for the next dispatches is min(sum of per-route amounts, room)."""
    inputs = (r.get("dispatch_amount_now") or {}).get("inputs") or {}
    slots, free = num(inputs.get("destination_slots")), num(inputs.get("free_space"))
    in_dest = slots - free if slots is not None and free is not None else None
    room = None if max_send == 0 else (max(max_send - in_dest, 0.0) if in_dest is not None else None)
    amounts = [num((x["scenario"] or {}).get("value")) for x in rows]
    total = None if any(a is None for a in amounts) else sum(amounts)
    combined = total if max_send == 0 else (min(total, room) if total is not None and room is not None else None)
    conf = merge_confidence(x["scenario"]["confidence"] for x in rows)
    return {"routes": len(rows),
            "destination_stored_and_incoming": derived(in_dest, "units", "destination_slots - free_space"),
            "max_send_room_scenario": None if max_send == 0 else derived(room, "units", "max_send - (destination_slots - free_space)"),
            "sum_of_route_amounts": estimate(total, "units", "D-ADV-DISPATCH-1", conf),
            "combined_max_next_dispatches": estimate(combined, "units", "D-ADV-DISPATCH-1", conf),
            "note": "routes share one Max Send room; the first origin to dispatch uses it up, so per-route amounts "
                    "do not add up beyond the room (dispatch order and timing are not exported)"}


def _net(m: dict) -> float | None:
    inc, exp = num(m.get("income_total")), num(m.get("expense_total"))
    return inc - exp if inc is not None and exp is not None else None


def _rate_dict(rate: dict, recipe: dict | None = None) -> tuple[dict, dict]:
    """Per-product output/input rates; when the rate is not computable but the recipe is known, its products map to
    None (unknown), never to an absent (= 0) entry."""
    if rate.get("available"):
        return ({o["product"]: num(o.get("per_30d")) for o in rate.get("outputs") or []},
                {i["product"]: num(i.get("per_30d")) for i in rate.get("inputs") or []})
    if recipe:
        return ({r["product"]: None for r in recipe.get("results") or []}, {i["product"]: None for i in recipe.get("ingredients") or []})
    return {}, {}


# ====================================================================== what_if

def what_if(ctx: CallContext) -> dict:
    change = dict(ctx.args["change"])
    typ = _check_change(change)
    basis = pin_basis(ctx, static="required", state_sections=("companies",),
                      history="optional" if typ == "take_loan" else "none")
    ix = ctx.state_index()
    require_player(ix)
    adv = Advice("what_if", basis)
    f = PlayerFacts(ix)
    out = evaluate_change(ctx, adv, f, basis, change, typ)
    adv.inputs = {"change_type": typ}
    return adv.finish(out, advisor_provenance(["baseline values"], ["recipes", "building types", "loan infos"], adv.methods))


def evaluate_change(ctx: CallContext, adv: Advice, f: PlayerFacts, basis, change: dict, typ: str) -> dict:
    """One hypothetical change on copies of the pinned snapshot (also used by compare_options)."""
    ix = f.ix
    six = f.six
    out: dict = {"change": change, "applied": False, "type": typ}
    side: list[dict] = []
    if typ in ("add_buildings", "remove_building", "set_efficiency", "change_recipe"):
        ctx.require_section("state", "buildings_player")
        adv.assume("A-CONTINUOUS")

    if typ == "add_buildings":
        recipe = six.recipes.get(ctx.resolver().resolve(change["recipe"], ("recipe",), "change.recipe").key)
        count = int(change.get("count") or 1)
        if not 1 <= count <= 50:
            raise ToolError("invalid_argument", "change.count must be 1..50")
        if change.get("building_type"):
            bt = ctx.resolver().resolve(change["building_type"], ("building_type",), "change.building_type").key
            if bt not in (recipe.get("building_types") or []):
                raise ToolError("invalid_argument", f"building type {bt!r} cannot run recipe {recipe['name']!r}",
                                hint=f"compatible: {recipe.get('building_types')}")
            btype = six.building_types.get(bt)
        else:
            btype = f.building_type_for(recipe)
        econ = f.new_building_economics(recipe, btype)
        assume_from(adv, econ)
        adv.assume("A-NEW-DEFAULT-EFFICIENCY", "A-ACTOR-MODIFIER-1", "A-NO-REGIONAL-COST")
        if econ["rate"].get("basis") == "static_recipe":
            adv.assume("A-STATIC-RECIPE-CYCLE")
        rate = econ["rate"]
        first = (recipe.get("results") or [{}])[0]
        r_first = num((rate.get("per_30d") or {}).get(first.get("product")))
        deltas: dict[str, dict] = {}
        for p, v in (rate.get("per_30d") or {}).items():
            deltas.setdefault(p, {})["supply"] = (num(v) or 0.0) * count if num(v) is not None else None
        for ing in recipe.get("ingredients") or []:
            per = r_first * (num(ing.get("amount")) or 0.0) / (num(first.get("amount")) or 1.0) if r_first is not None else None
            deltas.setdefault(ing.get("product"), {})["need"] = per * count if per is not None else None
        conf = adv.track(merge_confidence([rate.get("confidence"), econ["upkeep"].get("confidence"), econ["capex"].get("confidence")]))
        if any(v is None for d in deltas.values() for v in d.values()):
            adv.degrade("rate", "missing_value", "balance deltas null where the rate is unknown")
        out["baseline"] = {"count": sum(1 for b in f.buildings if b.get("recipe") == recipe["name"]),
                           "count_of": "player buildings running this recipe"}
        capex_one, up_one = num(econ["capex"].get("value")), num(econ["upkeep"].get("value"))
        out["scenario"] = {"recipe": make_id("recipe", recipe["name"]), "building_type": make_id("building_type", (btype or {}).get("name")),
                           "count": count, "rate_per_building": {**estimate(r_first, "units/30d", "D-ADV-NEWRATE-1", conf),
                                                                 "basis": rate.get("basis")},
                           "locked_by": f.locked_by(recipe["name"], (btype or {}).get("name"))}
        out["deltas"] = {"capex": estimate(capex_one * count if capex_one is not None else None, "money", "D-ADV-CAPEX-1", conf, 2),
                         "upkeep_per_30d": estimate(up_one * count if up_one is not None else None, "money/30d", "D-ADV-NEWUPKEEP-1", conf, 2),
                         "products": _balance_rows(f, adv, deltas, conf)}
        for row in out["deltas"]["products"]:
            if (row["balance_after"]["value"] or 0) < 0 <= ((row["supply_before"]["value"] or 0) - (row["internal_need_before"]["value"] or 0)):
                side.append({"kind": "input_goes_into_deficit", "product": row["product"]["id"], "balance_after": row["balance_after"]})
        if out["scenario"]["locked_by"]:
            side.append({"kind": "requires_unlock", "techs": out["scenario"]["locked_by"]})
        adv.method("deltas", "D-ADV-NEWRATE-1 / D-ADV-NEWUPKEEP-1 / D-ADV-CAPEX-1")

    elif typ == "remove_building":
        b = _player_building(ctx, f, change["building"])
        rate = f.rates.get(b["key"]) or {}
        outs, ins = _rate_dict(rate, six.recipes.get(b.get("recipe")) if b.get("recipe") else None)
        if b.get("recipe") and not rate.get("available"):
            adv.degrade(f"building:{b['key']}", "missing_value", "rate unknown: balance changes null")
        enabled = (b.get("flags") or {}).get("user_enabled", True) is not False
        deltas = {}
        if enabled:
            for p, v in outs.items():
                deltas.setdefault(p, {})["supply"] = -v if v is not None else None
            for p, v in ins.items():
                deltas.setdefault(p, {})["need"] = -v if v is not None else None
        conf = adv.conf("medium", ["approximate_rate"] if rate.get("approximate") else [])
        upkeep, fb = f.upkeep_of(b)
        paid = num(b.get("paid_to_build"))
        out["baseline"] = {"building": bref(ix, b["key"]), "enabled": enabled,
                           "upkeep_per_30d": observed(upkeep, "money/30d", "state.buildings_player[].upkeep." + ("monthly_full" if fb else "monthly_active")),
                           "paid_to_build": observed(paid, "money", "state.buildings_player[].paid_to_build")}
        out["scenario"] = {"removed": make_id("building", b["key"])}
        out["deltas"] = {"upkeep_per_30d": derived(-upkeep if upkeep is not None else None, "money/30d", "- observed upkeep"),
                         "refund": derived(0.75 * paid if paid is not None else None, "money", "0.75 x paid_to_build (mechanic build-cost)", 2),
                         "products": _balance_rows(f, adv, deltas, conf)}
        for row in out["deltas"]["products"]:
            if (row["balance_after"]["value"] or 0) < -1e-9 and (row["internal_need_after"]["value"] or 0) > 0:
                side.append({"kind": "consumers_left_short", "product": row["product"]["id"], "balance_after": row["balance_after"]})
        routes = [make_id("route", r["route_key"]) for r in ix.routes_by_origin.get(b["key"], [])] + \
                 [make_id("route", r["route_key"]) for r in ix.routes_by_destination.get(b["key"], [])]
        if routes:
            side.append({"kind": "routes_removed", "routes": sorted(set(routes))[:20], "count": len(set(routes))})

    elif typ == "set_efficiency":
        b = _player_building(ctx, f, change["building"])
        bt = six.building_types.get(b.get("prefab")) or {}
        eo, eu = bt.get("efficiency_output") or [], bt.get("efficiency_upkeep") or []
        idx = change["index"]
        if not isinstance(idx, int) or not 0 <= idx < len(eo):
            raise ToolError("invalid_argument", f"change.index must be 0..{len(eo) - 1} for {b.get('prefab')}")
        cur = (b.get("efficiency") or {}).get("index")
        if not isinstance(cur, int):
            raise ToolError("section_unavailable", "the building's current efficiency index is not in the snapshot",
                            details={"section": "buildings_player", "reason": "efficiency_index_missing"})
        rate = f.rates.get(b["key"]) or {}
        outs, ins = _rate_dict(rate, six.recipes.get(b.get("recipe")) if b.get("recipe") else None)
        if b.get("recipe") and not rate.get("available"):
            adv.degrade(f"building:{b['key']}", "missing_value", "rate unknown: balance changes null")
        upkeep, fb = f.upkeep_of(b)
        adv.assume("A-EFFICIENCY-SCALES-RATE")
        diff_up = f.difficulty().get("upkeep")
        res_o = eco.efficiency_change(outs, upkeep, eo, eu, cur, idx, bt.get("base_cost"), bt.get("upkeep_cost_percentage"),
                                      bt.get("min_upkeep"), diff_up)
        res_i = eco.efficiency_change(ins, upkeep, eo, eu, cur, idx)
        conf = adv.track(res_o.get("confidence")) or {"level": "low", "factors": ["partial_inputs"]}
        if rate.get("approximate"):
            conf = adv.track({"level": "medium" if conf["level"] == "high" else "low", "factors": conf["factors"] + ["approximate_rate"]})
        deltas = {}
        for p, v in outs.items():
            nv = num((res_o.get("rates") or {}).get(p))
            deltas.setdefault(p, {})["supply"] = (nv - v) if nv is not None and v is not None else None
        for p, v in ins.items():
            nv = num((res_i.get("rates") or {}).get(p))
            deltas.setdefault(p, {})["need"] = (nv - v) if nv is not None and v is not None else None
        locks = (six.tech_config or {}).get("efficiency_unlocks")
        # the game's multiplier includes company modifiers (live: oil types x0.75); the arrays above do not
        obs_om = num((b.get("efficiency") or {}).get("output_multiplier"))
        scen_om = obs_om * num(eo[idx]) / num(eo[cur]) if obs_om is not None and 0 <= cur < len(eo) and num(eo[cur]) and num(eo[idx]) is not None else None
        out["baseline"] = {"building": bref(ix, b["key"]), "index": cur,
                           "output_multiplier": quantity_def(eo, cur), "upkeep_multiplier": quantity_def(eu, cur),
                           "effective_output_multiplier": observed(obs_om, "ratio", "state.buildings_player[].efficiency.output_multiplier"),
                           "upkeep_per_30d": observed(upkeep, "money/30d", "state.buildings_player[].upkeep")}
        out["scenario"] = {"index": idx, "output_multiplier": quantity_def(eo, idx), "upkeep_multiplier": quantity_def(eu, idx),
                           "effective_output_multiplier": estimate(scen_om, "ratio", "D-ADV-EFF-1", conf),
                           "upkeep_per_30d": estimate(res_o.get("upkeep"), "money/30d", "D-ADV-EFF-1", conf, 2)}
        out["deltas"] = {"upkeep_per_30d": estimate(num(res_o.get("upkeep")) - upkeep if num(res_o.get("upkeep")) is not None and upkeep is not None else None,
                                                    "money/30d", "D-ADV-EFF-1", conf, 2),
                         "products": _balance_rows(f, adv, deltas, conf)}
        side.append({"kind": "unlock_requirement", "note": "higher efficiency levels may require a technology",
                     "unlock_table": "unknown" if locks is None else "see get_tech_tree; building efficiency unlocks are not exported per level"})
        adv.method("deltas", "D-ADV-EFF-1")

    elif typ == "change_recipe":
        b = _player_building(ctx, f, change["building"])
        rk = ctx.resolver().resolve(change["recipe"], ("recipe",), "change.recipe").key
        bt = six.building_types.get(b.get("prefab")) or {}
        if rk not in (bt.get("recipes") or []):
            raise ToolError("invalid_argument", f"recipe {rk!r} is not available on {b.get('prefab')}", hint=f"available: {bt.get('recipes')}")
        old = six.recipes.get(b.get("recipe")) if b.get("recipe") else None
        new = six.recipes.get(rk)
        cyc = num(b.get("cycle_days_effective"))
        od, nd = num((old or {}).get("game_days")), num(new.get("game_days"))
        if cyc is not None and od and nd:
            b2 = {**b, "recipe": rk, "cycle_days_effective": cyc * nd / od}
            basis_note = "observed cycle scaled by game_days ratio"
            conf = adv.conf("medium")
        else:
            econ = f.new_building_economics(new, bt)
            assume_from(adv, econ)
            b2 = None
            basis_note = "static recipe (no observed cycle)"
            conf = adv.conf("medium", ["approximate_rate"])
            adv.assume("A-STATIC-RECIPE-CYCLE")
        outs, ins = _rate_dict(f.rates.get(b["key"]) or {}, old)
        if b2 is not None:
            n_outs, n_ins = _rate_dict(theoretical_rate(six, b2), new)
        else:
            rate = econ["rate"]
            n_outs = {p: num(v) for p, v in (rate.get("per_30d") or {}).items()}
            first = (new.get("results") or [{}])[0]
            rf = num(n_outs.get(first.get("product")))
            n_ins = {i.get("product"): rf * (num(i.get("amount")) or 0.0) / (num(first.get("amount")) or 1.0) if rf is not None else None
                     for i in new.get("ingredients") or []}
        deltas = {}
        enabled = (b.get("flags") or {}).get("user_enabled", True) is not False
        for p in set(outs) | set(n_outs):
            deltas.setdefault(p, {})["supply"] = _diff(n_outs, outs, p) if enabled else 0.0
        for p in set(ins) | set(n_ins):
            deltas.setdefault(p, {})["need"] = _diff(n_ins, ins, p) if enabled else 0.0
        out["baseline"] = {"building": bref(ix, b["key"]), "recipe": make_id("recipe", b.get("recipe"))}
        out["scenario"] = {"recipe": make_id("recipe", rk), "rate_basis": basis_note,
                           "locked_by": f.locked_by(rk, None)}
        out["deltas"] = {"products": _balance_rows(f, adv, deltas, conf)}
        side.append({"kind": "storage_cleared", "note": "changing the recipe clears stock of products the new recipe does not use (mechanic storage-per-product)"})
        adv.method("deltas", "D-RATE-1 with scaled cycle")

    elif typ in ("set_max_send", "set_min_keep"):
        r = _route(ctx, f, change["route"])
        adv.assume("A-DISPATCH-NOT-THROUGHPUT")
        if typ == "set_max_send":
            value = change["value"]
            if not isinstance(value, int) or not 0 <= value <= 1_000_000:
                raise ToolError("invalid_argument", "change.value must be an integer 0..1000000 (0 = unlimited)")
            ms = r.get("max_send") or {}
            if ms.get("mode") == "auto_shop_demand":
                side.append({"kind": "auto_mode", "note": "this route uses auto Max Send (shop demand); a manual value applies only after turning auto off"})
            affected = f.routes_by_dest_product.get((r.get("endpoint") or r["destination"], r.get("product")), [r])
            kw = {"max_send": value}
            side.append({"kind": "shared_cap", "note": "Max Send is stored on the destination and shared by every origin shipping this product there",
                         "routes": len(affected)})
        else:
            if change.get("keep_all"):
                kw = {"keep_all": True, "min_keep": 2147483647}
            else:
                v = change["value"]
                if not isinstance(v, int) or not 0 <= v <= 99:
                    raise ToolError("invalid_argument", "change.value must be an integer 0..99 (or pass keep_all: true)")
                kw = {"min_keep": v, "keep_all": False}
            affected = [r]
        rows = []
        for ar in affected:
            inputs = (ar.get("dispatch_amount_now") or {}).get("inputs") or {}
            base = eco.dispatch_amount(inputs, destination_stock=ar.get("destination_stock"), destination_incoming=ar.get("destination_incoming_reserved"))
            scen = eco.dispatch_amount(inputs, destination_stock=ar.get("destination_stock"), destination_incoming=ar.get("destination_incoming_reserved"), **kw)
            observed_v = (ar.get("dispatch_amount_now") or {}).get("value")
            complete = (ar.get("dispatch_amount_now") or {}).get("complete", True)
            conf = adv.conf("high", [] if complete else ["partial_inputs"])
            if base.get("value") is not None and observed_v is not None and num(base["value"]) != num(observed_v):
                adv.degrade(f"route:{ar['route_key']}", "approximated", "server replica differs from the observer's value; both reported")
                conf = adv.track({"level": "medium" if conf["level"] == "high" else "low", "factors": conf["factors"] + ["contradictory_inputs"]})
            rows.append({"route_id": make_id("route", ar["route_key"]), "origin": bref(ix, ar["origin"]),
                         "dispatch_amount_now": observed(observed_v, "units/dispatch", "state.routes_player[].dispatch_amount_now.value"),
                         "replica_now": estimate(base.get("value"), "units/dispatch", "D-ADV-DISPATCH-1", conf),
                         "scenario": estimate(scen.get("value"), "units/dispatch", "D-ADV-DISPATCH-1", conf),
                         "limited_by_now": base.get("limited_by"), "limited_by_scenario": scen.get("limited_by"),
                         "complete": complete, "dormant_auto_wh": bool(ar.get("dormant_auto_warehouse")),
                         "paused": bool(ar.get("paused"))})
        ms, mk = r.get("max_send") or {}, r.get("min_keep") or {}
        src = "state.routes_player[]"
        out["baseline"] = {"route": make_id("route", r["route_key"]),
                           "max_send": observed(ms.get("value"), "units", src + ".max_send.value"),
                           "max_send_unlimited": ms.get("unlimited"), "max_send_mode": ms.get("mode"),
                           "max_send_headroom_now": derived(ms.get("headroom_now"), "units", "replica:ManualDestinationManager.GetRequestedAmount"),
                           "min_keep": observed(None if mk.get("keep_all") else mk.get("value"), "units", src + ".min_keep.value"),
                           "keep_all": mk.get("keep_all")}
        if typ == "set_max_send":
            out["scenario"] = {"max_send": parameter(kw["max_send"], "units"), "max_send_unlimited": kw["max_send"] == 0}
        else:
            out["scenario"] = {"min_keep": None if kw.get("keep_all") else parameter(kw["min_keep"], "units"),
                               "keep_all": bool(kw.get("keep_all"))}
        out["deltas"] = {"routes": rows}
        if typ == "set_max_send" and len(affected) > 1:
            out["deltas"]["shared_destination"] = _shared_room(r, rows, kw["max_send"])
        adv.method("deltas.routes[]", "D-ADV-DISPATCH-1")

    elif typ == "take_loan":
        infos = list(six.loan_infos)
        q = normalize_name(change["loan"])
        hit = [li for li in infos if q in (normalize_name(li.get("name")), normalize_name(li.get("title")))]
        if not hit:
            raise ToolError("not_found", f"loan {change['loan']!r} is not a loan info in the catalogue",
                            candidates=[{"id": li.get("name"), "kind": "loan_info"} for li in infos[:10]])
        li = hit[0]
        pay = derive.loan_monthly_payment(li.get("amount"), li.get("apr"), li.get("duration_months"))
        company = f.company
        cash = num((company.get("cash") or {}).get("value"))
        conf = adv.conf("high")
        out["baseline"] = {"cash": observed(cash, "money", "state.companies[player].cash.value"),
                           "loans": len(company.get("loans") or []), "max_loans": company.get("max_loans")}
        out["scenario"] = {"loan": li.get("name"), "type": li.get("type"), "amount": parameter(li.get("amount"), "money", "static.loan_infos"),
                           "apr": parameter(li.get("apr"), "ratio", "static.loan_infos"),
                           "duration_months": parameter(li.get("duration_months"), "months", "static.loan_infos"),
                           "grace_months": parameter(li.get("grace_months"), "months", "static.loan_infos")}
        net = None
        if basis.history is not None:
            ledger = section_or_degrade(ctx, adv, "history", "ledger_player", "monthly net omitted")
            months = sorted((ledger or {}).get("months") or [], key=lambda m: month_key(m.get("month")) or (0, 0))
            complete = [m for m in months if not m.get("current_month_to_date")]
            if complete:
                inc, exp = num(complete[-1].get("income_total")), num(complete[-1].get("expense_total"))
                net = inc - exp if inc is not None and exp is not None else None
        out["deltas"] = {"cash": derived(li.get("amount"), "money", "loan amount"),
                         "monthly_payment": derived(pay.get("value"), "money/30d", "D-LOAN-1", 2),
                         "net_after_payment": estimate(net - (pay.get("value") or 0.0) if net is not None and pay.get("value") is not None else None,
                                                       "money/30d", "last complete month net - payment", adv.conf("medium"), 2)}
        if company.get("max_loans") is not None and len(company.get("loans") or []) >= company["max_loans"]:
            side.append({"kind": "max_loans_reached", "loans": len(company.get("loans") or []), "max_loans": company["max_loans"]})
        if li.get("type") in ("STARTER", "BANKRUPTCY", "SETTLEMENT", "FINE"):
            side.append({"kind": "loan_type_availability", "note": f"{li.get('type')} loans are granted by game events, not freely taken"})
        adv.method("deltas.monthly_payment", "D-LOAN-1")

    out["side_effects"] = side
    out["note"] = "Hypothetical: computed on copies of the current snapshot. Nothing was changed in the game."
    return out


def quantity_def(arr: list, idx: int) -> dict:
    from .contract import definition
    return definition(arr[idx] if 0 <= idx < len(arr) else None, "ratio", "static.building_types[].efficiency arrays")


# ====================================================================== what_changed

UNIT_BY_FIELD = {"cash": "money", "loans_total": "money", "dispatch_amount_now": "units/dispatch", "price": "money/unit"}


def _q(field: str | None, value):
    if num(value) is None:
        return value
    unit = UNIT_BY_FIELD.get(field or "", "units" if (field or "").startswith("inventory.") else "count")
    return observed(value, unit, "state snapshot digest")


def _fmt_change(row: dict, basis: str, dates: tuple) -> dict:
    out = {"kind": row["kind"], "subject": row["subject"], "field": row.get("field"), "basis": basis,
           "before": _q(row.get("field"), row.get("before")), "after": _q(row.get("field"), row.get("after"))}
    if "delta" in row:
        unit = UNIT_BY_FIELD.get(row.get("field") or "", "units" if (row.get("field") or "").startswith("inventory.") else "count")
        out["delta"] = derived(row["delta"], unit, f"after - before ({dates[0]} -> {dates[1]})")
        out["relative"] = derived(row.get("relative"), "ratio", "delta / |before|")
    return out


def what_changed(ctx: CallContext) -> dict:
    basis = pin_basis(ctx, history="optional", static="optional")
    ix = ctx.state_index()
    require_player(ix)
    adv = Advice("what_changed", basis)
    horizon = ctx.args.get("horizon") or "both"
    limit = int(ctx.args.get("limit") or 25)
    since = ctx.args.get("since_seq")
    state = basis.state
    short = {"available": False}
    if horizon in ("short_term", "both"):
        entries = ctx.app.window.snapshots(state.world_session)
        seqs = [e.seq for e in entries]
        cur = next((e for e in reversed(entries) if e.seq == state.seq), None)
        if since is not None and since not in seqs:
            raise ToolError("not_found", f"since_seq {since} is not in this server's in-memory window of the current world session",
                            hint=f"Available seqs: {seqs[-20:]}. The window holds snapshots loaded while answering calls (<= 20 / 30 min).")
        frm = next((e for e in entries if e.seq == since), None) if since is not None else (entries[0] if entries else None)
        if cur is None or frm is None or frm.seq == cur.seq:
            short = {"available": False, "reason": "fewer than two snapshots of this world session in the in-memory window"
                     if since is None else "since_seq is the current snapshot", "window": {"entries": len(entries), "seqs": seqs}}
            adv.degrade("short_term", "missing_value", "omitted: needs two snapshots of the same world session")
        elif frm.digest is None or cur.digest is None:
            short = {"available": False, "reason": "a window entry could not be digested", "window": {"entries": len(entries), "seqs": seqs}}
            adv.degrade("short_term", "missing_value", "omitted")
        else:
            from .window import diff
            changes = diff(frm.digest, cur.digest)
            dates = (frm.game_date, cur.game_date)
            short = {"available": True, "from": {"seq": frm.seq, "game_date": frm.game_date},
                     "to": {"seq": cur.seq, "game_date": cur.game_date}, "window": {"entries": len(entries), "seqs": seqs},
                     "changes_total": len(changes), "changes": [_fmt_change(c, "in_memory_window", dates) for c in changes[:limit]]}
            adv.method("short_term.changes", "D-ADV-CHG-1")
    monthly = {"available": False}
    if horizon in ("monthly", "both"):
        if basis.history is None:
            monthly = {"available": False, "reason": "history.json unavailable or of another world session"}
        else:
            hist = basis.history.data
            ledger = section_or_degrade(ctx, adv, "history", "ledger_player", "ledger changes omitted")
            months = sorted((ledger or {}).get("months") or [], key=lambda m: month_key(m.get("month")) or (0, 0))
            complete = [m for m in months if not m.get("current_month_to_date")]
            changes = []
            fm = tm = None
            if len(complete) >= 2:
                prev, cur_m = complete[-2], complete[-1]
                fm, tm = prev.get("month"), cur_m.get("month")
                rows = [{"month": m.get("month"), "net": _net(m),
                         "by_category": [{"category": c.get("category"), "income": c.get("income"), "expense": c.get("expense"),
                                          "net": (num(c.get("income")) or 0) - (num(c.get("expense")) or 0)} for c in m.get("categories") or []]}
                        for m in (prev, cur_m)]
                mom = derive.month_over_month(rows)[0]
                changes.append({"kind": "ledger_net", "subject": "company:player", "field": "net", "basis": "history",
                                "delta": derived(mom["net_change"], "money/30d", f"D-FIN-1 ({fm} -> {tm})")})
                for c in mom["by_category"]:
                    if c["net_delta"]:
                        changes.append({"kind": "ledger_category", "subject": make_id("bill_category", c["category"]), "field": "net",
                                        "basis": "history", "delta": derived(c["net_delta"], "money/30d", f"D-FIN-1 ({fm} -> {tm})"),
                                        "share_of_total_change": derived(c["share_of_total_change"], "ratio", "D-FIN-1")})
                adv.method("monthly.ledger", "D-FIN-1")
            for s in hist.get("production_monthly_player") or []:
                ms = sorted(s.get("months") or [], key=lambda m: month_key(m.get("month")) or (0, 0))
                last = s.get("window_last_month")
                ms = [m for m in ms if m.get("month") != last]   # the window's last month is in progress
                if len(ms) >= 2:
                    a, b = num(ms[-2].get("produced")), num(ms[-1].get("produced"))
                    if a is not None and b is not None and a != b:
                        changes.append({"kind": "production", "subject": make_id("building", s.get("building")),
                                        "field": f"produced.{s.get('product')}", "basis": "history",
                                        "delta": derived(b - a, "units", f"history.production_monthly_player ({ms[-2].get('month')} -> {ms[-1].get('month')})"),
                                        "relative": derived((b - a) / abs(a) if a else None, "ratio", "delta / |before|")})
            for s in hist.get("shops_monthly") or []:
                ms = sorted(s.get("months") or [], key=lambda m: month_key(m.get("month")) or (0, 0))
                last = s.get("window_last_month")
                ms = [m for m in ms if m.get("month") != last]
                if len(ms) >= 2:
                    a, b = num(ms[-2].get("sold")), num(ms[-1].get("sold"))
                    if a is not None and b is not None and a != b:
                        changes.append({"kind": "shop_sales", "subject": make_id("building", s.get("shop")),
                                        "field": f"sold.{s.get('product')}", "basis": "history",
                                        "delta": derived(b - a, "units", f"history.shops_monthly ({ms[-2].get('month')} -> {ms[-1].get('month')})"),
                                        "relative": derived((b - a) / abs(a) if a else None, "ratio", "delta / |before|")})
            head, rest = changes[:1], changes[1:]
            rest.sort(key=lambda c: -abs((c.get("delta") or {}).get("value") or 0))
            changes = head + rest
            monthly = {"available": bool(changes) or fm is not None, "from_month": fm, "to_month": tm,
                       "changes_total": len(changes), "changes": changes[:limit],
                       "note": "Complete months only; the in-progress month is never compared as if complete."}
            if not monthly["available"]:
                monthly["reason"] = "fewer than two complete months retained"
    adv.inputs = {"horizon": horizon, "limit": parameter(limit, "count"), "since_seq": since}
    return adv.finish({"short_term": short, "monthly": monthly},
                      advisor_provenance(["in-memory window digests (observed)", "history ledger, production and shop series"], [], adv.methods))
