"""compare_options (phase-2 addendum 4.4, D-CMP-1): constrained comparison of options of one kind.

Only metrics defined for the kind are produced; a metric that cannot be computed for one option is null there and
named in that option's `unavailable`. No common metric is fabricated across kinds.
"""

from __future__ import annotations

from .. import derive
from ..app import CallContext
from ..errors import ToolError
from ..index import make_id
from ..tools.common import resolve_building_key, resolve_product
from . import economics as eco
from .basis import pin_basis
from .common import assume_from, advisor_provenance, pref, require_player, section_or_degrade
from .contract import Advice, definition, derived, estimate, game_computed, merge_confidence, num, observed
from .facts import PlayerFacts
from .tools_research import _path, _research_inputs, score_node
from .tools_scenario import _check_change, evaluate_change

KINDS = ("recipes", "building_types", "supply_sources", "shop_destinations", "research", "scenarios")

METRICS = {
    "recipes": [("output_per_30d_per_building", "units/30d", "higher"), ("input_cost_per_unit", "money/unit", "lower"),
                ("upkeep_per_unit", "money/unit", "lower"), ("unit_cost", "money/unit", "lower"), ("game_days", "days", "none")],
    "building_types": [("build_cost", "money", "lower"), ("upkeep_per_30d", "money/30d", "lower"),
                       ("output_per_30d", "units/30d", "higher"), ("storage_slots", "units", "higher"), ("max_modules", "count", "none")],
    "supply_sources": [("straight_line_tiles", "tiles", "lower"), ("cost_per_trip", "money", "lower"),
                       ("output_per_30d", "units/30d", "higher"), ("stock", "units", "higher")],
    "shop_destinations": [("price_for_player", "money/unit", "higher"), ("unmet_demand_per_30d", "units/30d", "higher"),
                          ("straight_line_tiles", "tiles", "lower"), ("cost_per_unit", "money/unit", "lower"),
                          ("net_price_per_unit", "money/unit", "higher")],
    "research": [("research_cost", "money", "lower"), ("research_days", "days", "lower"), ("score", "ratio", "higher"),
                 ("demand_value_per_30d", "money/30d", "higher")],
    "scenarios": [("capex", "money", "lower"), ("upkeep_delta_per_30d", "money/30d", "lower"),
                  ("product_supply_delta_per_30d", "units/30d", "higher"), ("product_balance_after_per_30d", "units/30d", "higher")],
}
LABELS = {"output_per_30d_per_building": "output per building per 30 days", "input_cost_per_unit": "input cost per unit (market)",
          "upkeep_per_unit": "upkeep per unit (new building)", "unit_cost": "unit cost (new building)", "game_days": "recipe game days",
          "build_cost": "build cost (player)", "upkeep_per_30d": "monthly upkeep (new building)", "output_per_30d": "output per 30 days",
          "storage_slots": "storage slots per product", "max_modules": "max modules", "straight_line_tiles": "straight-line distance",
          "cost_per_trip": "cost per trip", "stock": "stock now", "price_for_player": "shop price for the player",
          "unmet_demand_per_30d": "unmet demand per 30 days", "cost_per_unit": "transport cost per unit",
          "net_price_per_unit": "price minus transport per unit", "research_cost": "research cost", "research_days": "research days",
          "score": "suggest_research score", "demand_value_per_30d": "addressable demand value per 30 days", "capex": "capex",
          "upkeep_delta_per_30d": "upkeep change per 30 days", "product_supply_delta_per_30d": "supply change of product",
          "product_balance_after_per_30d": "balance of product after"}


def _value(q):
    return None if q is None else q.get("value")


def _differences(kind: str, options: list[dict]) -> list[dict]:
    out = []
    for mid, _unit, better in METRICS[kind]:
        vals = [(o["values"].get(mid), o["id"]) for o in options]
        vals = [(_value(q), i) for q, i in vals if _value(q) is not None]
        if better == "none" or len(vals) < 2:
            continue
        ranking = sorted(vals, key=lambda t: (t[0] if better == "lower" else -t[0], t[1]))
        out.append({"metric": mid, "better": better, "best_option": ranking[0][1], "worst_option": ranking[-1][1],
                    "spread": derived(max(v for v, _ in vals) - min(v for v, _ in vals), next(u for m, u, _ in METRICS[kind] if m == mid),
                                      "max - min"),
                    "ranking": [i for _, i in ranking], "compared_options": len(vals)})
    return out


def _option(oid: str, label, values: dict, adv: Advice, extra: dict | None = None) -> dict:
    unavailable = sorted(m for m, q in values.items() if _value(q) is None)
    return {"id": oid, "label": label, "values": values, "unavailable": unavailable, **(extra or {})}


def compare_options(ctx: CallContext) -> dict:
    a = ctx.args
    kind = a["kind"]
    basis = pin_basis(ctx, static="required", history="optional" if kind == "scenarios" else "none")
    ix = ctx.state_index()
    require_player(ix)
    adv = Advice("compare_options", basis)
    f = PlayerFacts(ix)
    six = f.six
    res = ctx.resolver()
    raw = a.get("scenarios") if kind == "scenarios" else a.get("options")
    if not raw:
        raise ToolError("invalid_argument", "kind 'scenarios' needs scenarios[]" if kind == "scenarios" else f"kind {kind!r} needs options[]")
    lo, hi = (2, 5) if kind == "scenarios" else (2, 8)
    if not lo <= len(raw) <= hi:
        raise ToolError("invalid_argument", f"{kind}: pass {lo}..{hi} options")
    options: list[dict] = []
    if kind in ("recipes", "building_types", "research", "supply_sources", "shop_destinations"):
        rk = {"recipes": ("recipe",), "building_types": ("building_type",), "research": ("tech",),
              "supply_sources": ("building",), "shop_destinations": ("building", "shop")}[kind]
        keys = [res.resolve(v, rk, "options[]").key for v in raw]
        if len(set(keys)) != len(keys):
            raise ToolError("invalid_argument", "options contain duplicates")

    if kind == "recipes":
        recipes = [six.recipes[k] for k in keys]
        common = set.intersection(*({r["product"] for r in rc.get("results") or []} for rc in recipes))
        product = resolve_product(ctx, a["product"]) if a.get("product") else (sorted(common)[0] if common else None)
        if product is None or product not in common:
            raise ToolError("invalid_argument", "recipes are comparable only when they share a result product (pass product)")
        adv.assume("A-NEW-DEFAULT-EFFICIENCY", "A-ACTOR-MODIFIER-1", "A-INPUT-MARKET", "A-CONTINUOUS")
        for rc in recipes:
            btype = f.building_type_for(rc)
            econ = f.new_building_economics(rc, btype)
            assume_from(adv, econ)
            rate = num((econ["rate"].get("per_30d") or {}).get(product))
            res_amt = next(num(r.get("amount")) for r in rc["results"] if r["product"] == product)
            inputs = [{"product": i["product"], "per_unit": (num(i.get("amount")) or 0) / res_amt, "value": f.input_value(i["product"])}
                      for i in rc.get("ingredients") or []]
            up = num(econ["upkeep"].get("value"))
            upu = {"value": up / rate if up is not None and rate else None, "confidence": econ["upkeep"].get("confidence")}
            uc = eco.unit_cost(upu, inputs)
            conf = adv.track(merge_confidence([econ["rate"].get("confidence"), econ["upkeep"].get("confidence")]) if rate else None) or \
                {"level": "low", "factors": ["partial_inputs"]}
            vals = {"output_per_30d_per_building": estimate(rate, "units/30d", "D-ADV-NEWRATE-1", conf),
                    "input_cost_per_unit": estimate(uc.get("inputs_per_unit"), "money/unit", "D-ADV-INPUTVAL-1", conf),
                    "upkeep_per_unit": estimate(upu["value"], "money/unit", "D-ADV-NEWUPKEEP-1 / rate", conf),
                    "unit_cost": estimate(uc.get("value"), "money/unit", "D-ADV-UNITCOST-1", conf),
                    "game_days": definition(rc.get("game_days"), "days", "static.recipes[].game_days")}
            options.append(_option(make_id("recipe", rc["name"]), rc.get("english_name") or rc["name"], vals, adv,
                                   {"building_type": make_id("building_type", (btype or {}).get("name")),
                                    "locked_by": f.locked_by(rc["name"], (btype or {}).get("name")),
                                    "rate_basis": econ["rate"].get("basis")}))
        context = {"product": pref(ix, product)}

    elif kind == "building_types":
        recipe = six.recipes.get(res.resolve(a["recipe"], ("recipe",), "recipe").key) if a.get("recipe") else None
        adv.assume("A-NEW-DEFAULT-EFFICIENCY", "A-ACTOR-MODIFIER-1", "A-NO-REGIONAL-COST")
        for k in keys:
            bt = six.building_types[k]
            econ = f.new_building_economics(recipe, bt) if recipe and k in (recipe.get("building_types") or []) else None
            assume_from(adv, econ)
            mtype = six.building_types.get(bt.get("module_prefab")) if eco.is_module_owner(bt) else None
            mods = int(num((f.effective_btype(bt) or {}).get("max_module_count")) or 0) if mtype else 0
            pc, _ = f.player_build_cost(k)
            mpc, _ = f.player_build_cost((mtype or {}).get("name"))
            cap = eco.capex(pc, bt.get("base_cost"), mods, mpc, (mtype or {}).get("base_cost"))
            upk = eco.new_building_upkeep(bt, mtype, mods, f.difficulty().get("upkeep"), f.type_modifiers(k)["upkeep"])
            assume_from(adv, upk)
            conf = adv.track(merge_confidence([cap.get("confidence"), upk.get("confidence")]))
            out_v = None
            if econ is not None:
                first = recipe["results"][0]["product"]
                out_v = estimate((econ["rate"].get("per_30d") or {}).get(first), "units/30d", "D-ADV-NEWRATE-1",
                                 adv.track(econ["rate"].get("confidence")) or conf)
            vals = {"build_cost": estimate(cap.get("value"), "money", "D-ADV-CAPEX-1", conf, 2),
                    "upkeep_per_30d": estimate(upk.get("value"), "money/30d", "D-ADV-NEWUPKEEP-1", conf, 2),
                    "output_per_30d": out_v,
                    "storage_slots": definition(bt.get("storage_slots"), "units", "static.building_types[].storage_slots"),
                    "max_modules": definition(bt.get("max_module_count"), "count", "static.building_types[].max_module_count"),
                    "max_modules_player": observed(f.module_max(k), "count", "state.buildings_player[].modules.max")}
            options.append(_option(make_id("building_type", k), bt.get("english_name") or k, vals, adv,
                                   {"locked_by": f.locked_by(None, k),
                                    "can_run_recipe": (k in (recipe.get("building_types") or [])) if recipe else None}))
        context = {"recipe": make_id("recipe", (recipe or {}).get("name"))}

    elif kind in ("supply_sources", "shop_destinations"):
        product = resolve_product(ctx, a["product"]) if a.get("product") else None
        if product is None:
            raise ToolError("invalid_argument", f"kind {kind!r} needs product")
        anchor_arg = "destination" if kind == "supply_sources" else "origin"
        if not a.get(anchor_arg):
            raise ToolError("invalid_argument", f"kind {kind!r} needs {anchor_arg}")
        anchor = resolve_building_key(ctx, a[anchor_arg], anchor_arg)
        section_or_degrade(ctx, adv, "state", "routes_player", "existing routes unknown: estimates only")
        text = six.formulas.get("ManualDestinationDispatchCost")
        diff = (ix.session.get("difficulty") or {}).get("dispatch")
        adv.assume("A-STRAIGHT-LINE", "A-ACTOR-MODIFIER-1")
        caps = [num(r.get("vehicle_capacity")) for r in f.routes if r.get("source") == "own" and r.get("product") == product]
        caps = [c for c in caps if c]
        cap_med = eco.median(caps) if caps else None
        ac = ix.coords(anchor) or {}
        for k in keys:
            oc = ix.coords(k) or {}
            o, d = (k, anchor) if kind == "supply_sources" else (anchor, k)
            dist = derive.distances((ix.coords(o) or {}).get("x"), (ix.coords(o) or {}).get("y"),
                                    (ix.coords(d) or {}).get("x"), (ix.coords(d) or {}).get("y"))
            route = next((r for r in ix.routes_by_origin.get(o, []) if d in (r["destination"], r.get("endpoint"))
                          and r.get("product") == product), None)
            if route is not None:
                trip = game_computed(route.get("dispatch_cost"), "money", f"state.routes_player[{route['route_key']}].dispatch_cost")
                capv = num(route.get("vehicle_capacity"))
                cpu = derived(num(route.get("dispatch_cost")) / capv if capv and num(route.get("dispatch_cost")) is not None else None,
                              "money/unit", "D-ROUTE-1 (existing route)")
            else:
                est = derive.straight_line_cost_estimate(text, dist["euclidean"], diff, route_exists=False)
                conf = adv.conf("medium", [] if cap_med else ["partial_inputs"])
                trip = estimate(est.get("value"), "money", "D-ROUTE-2 (straight line)", conf, 2)
                cpu = estimate(est["value"] / cap_med if est.get("value") is not None and cap_med else None, "money/unit",
                               "D-ROUTE-2 / median observed capacity", conf)
                adv.assume("A-FULL-VEHICLES")
            vals = {"straight_line_tiles": derived(dist["euclidean"], "tiles", "D-DIST-1")}
            if kind == "supply_sources":
                b = f.by_key.get(k)
                if b is None or product not in {o_["product"] for o_ in (f.rates.get(k) or {}).get("outputs") or []}:
                    raise ToolError("invalid_argument", f"{make_id('building', k)} is not a player producer of {product}")
                rate = next((num(x.get("per_30d")) for x in (f.rates.get(k) or {}).get("outputs") or [] if x.get("product") == product), None)
                inv = next((i for i in b.get("inventory") or [] if i.get("product") == product), None)
                vals.update({"cost_per_trip": trip,
                             "output_per_30d": estimate(rate, "units/30d", "D-RATE-1/2", adv.conf("medium")),
                             "stock": observed((inv or {}).get("count"), "units", "state.buildings_player[].inventory[].count")})
                extra = {"existing_route": make_id("route", route["route_key"]) if route else None}
            else:
                shop = ix.shops.get(k)
                sp = next((p for p in (shop or {}).get("products") or [] if p.get("product") == product), None) if shop else None
                if sp is None:
                    raise ToolError("invalid_argument", f"{make_id('building', k)} is not a shop accepting {product}")
                interval = (ix.cities.get(shop.get("city_id")) or {}).get("consumption_interval_days")
                um = derive.unmet_demand(sp.get("demand_for_player"), sp.get("player_delivered_stock"), interval)
                price = num(sp.get("price_for_player"))
                net = price - _value(cpu) if price is not None and _value(cpu) is not None else None
                vals.update({"price_for_player": observed(price, "money/unit", "state.shops[].products[].price_for_player"),
                             "unmet_demand_per_30d": estimate(um.get("per_30d"), "units/30d", "D-SHOP-1", adv.conf("medium")),
                             "cost_per_unit": cpu,
                             "net_price_per_unit": estimate(net, "money/unit", "price - transport per unit", adv.conf("medium"))})
                extra = {"existing_route": make_id("route", route["route_key"]) if route else None}
            options.append(_option(make_id("building", k), (ix.building(k) or {}).get("display_name"), vals, adv, extra))
        context = {"product": pref(ix, product), anchor_arg: make_id("building", anchor), "distance_kind": "straight_line",
                   "is_path_distance": False, "anchor_coordinates_known": bool(ac)}

    elif kind == "research":
        ix_, six_, pr, unlocked, observed_costs = _research_inputs(ctx, adv)
        shops_ok = section_or_degrade(ctx, adv, "state", "shops", "demand value omitted") is not None
        adv.assume("A-SCORE-WEIGHTS")
        for k in keys:
            p = _path(six, pr, unlocked, observed_costs, k)
            row = score_node(adv, f, ix, six, k, p["chain"], p["costs"], p["chain"]["remaining"] == [k], shops_ok)
            comp = row["components"]
            vals = {"research_cost": comp["research_cost"], "research_days": comp["research_days"], "score": row["score"],
                    "demand_value_per_30d": comp["demand_value_per_30d"]}
            options.append(_option(make_id("tech", k), row.get("english_name") or k, vals, adv,
                                   {"chain_length": row["chain_length"], "already_unlocked": k in unlocked,
                                    "unscored_reason": row["unscored_reason"]}))
        context = {}

    else:  # scenarios
        product = resolve_product(ctx, a["product"]) if a.get("product") else None
        for i, ch in enumerate(raw):
            change = dict(ch)
            typ = _check_change(change)
            out = evaluate_change(ctx, adv, f, basis, change, typ)
            d = out.get("deltas") or {}
            prow = next((r for r in d.get("products") or [] if product and r["product"]["id"] == make_id("product", product)), None)
            vals = {"capex": d.get("capex"), "upkeep_delta_per_30d": d.get("upkeep_per_30d"),
                    "product_supply_delta_per_30d": None, "product_balance_after_per_30d": prow["balance_after"] if prow else None}
            if prow:
                sb, sa = _value(prow["supply_before"]), _value(prow["supply_after"])
                vals["product_supply_delta_per_30d"] = estimate(sa - sb if sa is not None and sb is not None else None, "units/30d",
                                                                "supply_after - supply_before", prow["supply_after"]["confidence"])
            options.append(_option(f"scenario:{i + 1}", typ, vals, adv, {"change": change, "side_effects": out.get("side_effects")}))
        context = {"product": pref(ix, product) if product else None,
                   "note": "Hypothetical: computed on copies of the current snapshot. Nothing was changed in the game."}

    metrics = [{"id": m, "label": LABELS[m], "unit": u, "better": b} for m, u, b in METRICS[kind]]
    result = {"kind": kind, "context": context, "metrics": metrics, "options": options, "differences": _differences(kind, options)}
    adv.method("differences[]", "D-CMP-1")
    return adv.finish(result, advisor_provenance(["per-kind observed inputs"], ["recipes", "building types", "formulas"], adv.methods))
