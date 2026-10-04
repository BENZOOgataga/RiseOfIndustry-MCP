"""get_profitability, find_opportunities and plan_chain (PRD addendum 6.3, 6.4, 6.6)."""

from __future__ import annotations

from collections import defaultdict

from .. import derive
from ..app import CallContext
from ..errors import ToolError
from ..index import make_id
from ..tools.common import resolve_product
from ..util import month_key
from . import economics as eco
from .basis import pin_basis
from .common import assume_from, advisor_provenance, bounded_page, est_from, pref, require_player, section_or_degrade
from .contract import Advice, derived, estimate, lower, merge_confidence, num, observed, parameter
from .facts import PlayerFacts

OWN_COST_DEPTH = 6


# ---------------------------------------------------------------------- shared unit economics

LONG_PAYBACK_MONTHS = 24     # caveat threshold (parameter), not a ranking input

def unit_cost_existing(f: PlayerFacts, product: str, basis_mode: str, _stack: tuple = ()) -> dict:
    """D-ADV-UNITCOST-1 at the player's enabled producers of `product` (output-weighted over their recipes)."""
    producers = [p for p in f.producers.get(product, []) if p["enabled"]]
    up_rows = []
    by_recipe: dict[str, float] = defaultdict(float)
    for p in producers:
        b = p["building"]
        upkeep, fallback = f.upkeep_of(b)
        up_rows.append({"building": make_id("building", b["key"]), "upkeep": upkeep, "output_per_30d": p["per_30d"],
                        "share": p["share"], "approximate": p["approximate"], "upkeep_fallback": fallback})
        if p["per_30d"]:
            by_recipe[b.get("recipe")] += p["per_30d"]
    upk = eco.upkeep_per_unit(up_rows)
    if any(r["upkeep_fallback"] for r in up_rows) and upk.get("confidence"):
        upk["confidence"] = lower(upk["confidence"], "partial_inputs")
    total_out = sum(by_recipe.values())
    ingredients = []
    for rname, out in sorted(by_recipe.items()):
        recipe = f.six.recipes.get(rname) or {}
        res_amt = next((num(r.get("amount")) for r in recipe.get("results") or [] if r.get("product") == product), None)
        if not res_amt:
            continue
        w = out / total_out
        for ing in recipe.get("ingredients") or []:
            ip = ing.get("product")
            if basis_mode == "own_cost" and f.producers.get(ip) and ip not in _stack and len(_stack) < OWN_COST_DEPTH:
                own = unit_cost_existing(f, ip, basis_mode, _stack + (product,))
                value = {"value": own.get("value"), "basis": "own_cost", "confidence": own.get("confidence")}
                if own.get("value") is None:
                    value = f.input_value(ip)
            else:
                value = f.input_value(ip)
                if basis_mode == "own_cost" and ip in _stack and value.get("confidence"):
                    value = {**value, "confidence": lower(value["confidence"], "partial_inputs")}
            ingredients.append({"product": ip, "per_unit": w * (num(ing.get("amount")) or 0.0) / res_amt, "value": value})
    merged: dict[str, dict] = {}
    for ing in ingredients:
        m = merged.setdefault(ing["product"], {"product": ing["product"], "per_unit": 0.0, "value": ing["value"]})
        m["per_unit"] += ing["per_unit"]
    return eco.unit_cost(upk, merged.values())


def distribution_existing(f: PlayerFacts, product: str) -> dict:
    keys = {p["building"]["key"] for p in f.producers.get(product, [])}
    per = []
    for k in keys:
        for r in f.ix.routes_by_origin.get(k, []):
            if r.get("product") != product or r.get("errors") or r.get("dormant_auto_warehouse"):
                continue
            uc = derive.route_unit_costs(r.get("dispatch_cost"), r.get("vehicle_capacity"), None)
            per.append(uc.get("cost_per_unit_at_capacity"))
    return eco.distribution_cost(per)


def product_economics(f: PlayerFacts, product: str, basis_mode: str = "market_value") -> dict:
    price = f.sale_price(product)
    cost = unit_cost_existing(f, product, basis_mode)
    dist = distribution_existing(f, product)
    vol = f.produced_last_month(product)
    vbasis = "produced_last_month"
    if vol is None:
        vol = f.supply(product)[0]
        vbasis = "theoretical"
    mg = eco.margin(price, cost, dist, vol, vbasis)
    return {"price": price, "cost": cost, "dist": dist, "margin": mg, "volume": vol, "volume_basis": vbasis}


def _economics_out(adv: Advice, e: dict, prefix: str) -> dict:
    cost = e["cost"]
    return {
        "sale_price": {**est_from(e["price"], "money/unit", adv, f"{prefix}.sale_price"), "basis": e["price"].get("basis")},
        "unit_cost": {"upkeep_per_unit": estimate(cost.get("upkeep_per_unit"), "money/unit", "D-ADV-UPKEEPSHARE-1",
                                                  adv.track(cost.get("confidence")) or {"level": "low", "factors": ["partial_inputs"]}),
                      "inputs_per_unit": estimate(cost.get("inputs_per_unit"), "money/unit", "D-ADV-INPUTVAL-1",
                                                  adv.track(cost.get("confidence")) or {"level": "low", "factors": ["partial_inputs"]}),
                      "total": est_from(cost, "money/unit", adv, f"{prefix}.unit_cost.total"),
                      "missing": cost.get("missing")},
        "distribution_cost_per_unit": est_from(e["dist"], "money/unit", adv, f"{prefix}.distribution_cost_per_unit"),
        "margin_per_unit": est_from(e["margin"], "money/unit", adv, f"{prefix}.margin_per_unit", key="per_unit"),
        "volume_per_30d": {**(observed(e["volume"], "units/30d", "state.buildings_player[].production.produced_last_month (result share)")
                              if e["volume_basis"] == "produced_last_month" else
                              estimate(e["volume"], "units/30d", "D-RATE-1/2", adv.conf("medium", ["approximate_rate"]))),
                           "basis": e["volume_basis"]},
        "margin_per_30d": est_from(e["margin"], "money/30d", adv, f"{prefix}.margin_per_30d", key="per_30d", digits=2),
    }


# ====================================================================== get_profitability

def _game_stats(row: dict | None, adv: Advice) -> dict | None:
    if row is None:
        return None
    src = "history.player_product_stats[]"
    out = {k: observed(row.get(k), unit, f"{src}.{k}") for k, unit in
           (("produced", "units"), ("sold", "units"), ("production_cost", "money"), ("distribution_cost", "money"),
            ("total_cost", "money"), ("price_sold", "money/unit"), ("profit", "money"), ("markup", "ratio"))}
    sold, profit = num(row.get("sold")), num(row.get("profit"))
    out["profit_per_unit_sold"] = derived(profit / sold if sold and profit is not None else None, "money/unit",
                                          "profit / sold (game statistics)")
    out["window"] = "the game's current statistics window (ProductionStatsTracker)"
    return out


def get_profitability(ctx: CallContext) -> dict:
    basis = pin_basis(ctx, history="optional", static="required", state_sections=("buildings_player",))
    ix = ctx.state_index()
    require_player(ix)
    adv = Advice("get_profitability", basis)
    f = PlayerFacts(ix)
    mode = ctx.args.get("input_cost_basis") or "market_value"
    limit = int(ctx.args.get("limit") or 10)
    section_or_degrade(ctx, adv, "state", "market", "prices unavailable: unit costs and margins null")
    section_or_degrade(ctx, adv, "state", "shops", "sale price falls back to the market price")
    section_or_degrade(ctx, adv, "state", "routes_player", "distribution cost omitted")
    stats = {}
    ledger = None
    if basis.history is not None:
        rows = section_or_degrade(ctx, adv, "history", "player_product_stats", "game statistics omitted")
        stats = {r.get("product"): r for r in rows or []}
        ledger = section_or_degrade(ctx, adv, "history", "ledger_player", "company month omitted")
    adv.assume("A-CONTINUOUS", "A-UPKEEP-ACTIVE", "A-INPUT-OWN" if mode == "own_cost" else "A-INPUT-MARKET",
               "A-NO-TRANSPORT-IN-UNITCOST", "A-FULL-VEHICLES", "A-PRICE-SHOP")
    if ctx.args.get("product"):
        products = [resolve_product(ctx, ctx.args["product"])]
    else:
        products = sorted(p for p, rows in f.producers.items() if any(r["enabled"] for r in rows))
    out = []
    for p in products:
        e = product_economics(f, p, mode)
        econ = _economics_out(adv, e, "products[]")
        gs = _game_stats(stats.get(p), adv)
        if gs is not None:
            g, est_v = gs["profit_per_unit_sold"]["value"], econ["margin_per_unit"]["value"]
            if g is not None and est_v is not None:
                big = max(abs(g), abs(est_v))
                if (g > 0) != (est_v > 0) or (big and abs(g - est_v) > 0.5 * big):
                    adv.contradiction({"kind": "game_stats_vs_estimate", "subject": make_id("product", p),
                                       "values": {"game_profit_per_unit_sold": g, "advisor_margin_per_unit": est_v},
                                       "effect": "both reported; prefer the game's statistics for past performance"})
                    econ["margin_per_unit"]["confidence"] = lower(econ["margin_per_unit"]["confidence"], "contradictory_inputs")
                    adv.track(econ["margin_per_unit"]["confidence"])
        if not f.producers.get(p):
            adv.degrade(f"product:{p}", "missing_value", "no player producer: estimate null")
        out.append({"product": pref(ix, p), "game_stats": gs, "estimate": econ,
                    "confidence": merge_confidence([econ["margin_per_unit"].get("confidence")])})
    out.sort(key=lambda r: (r["estimate"]["margin_per_30d"]["value"] is None, -(r["estimate"]["margin_per_30d"]["value"] or 0),
                            r["product"]["id"]))
    total = len(out)
    company_month = None
    if ledger is not None:
        months = sorted(ledger.get("months") or [], key=lambda m: month_key(m.get("month")) or (0, 0))
        complete = [m for m in months if not m.get("current_month_to_date")]
        if complete:
            m = complete[-1]
            inc, exp = num(m.get("income_total")), num(m.get("expense_total"))
            src = "history.ledger_player.months[]"
            company_month = {"month": m.get("month"), "income": observed(inc, "money", src + ".income_total"),
                             "expense": observed(exp, "money", src + ".expense_total"),
                             "net": observed(inc - exp if inc is not None and exp is not None else None, "money", src)}
    result = {"products": out[:limit], "products_total": total, "input_cost_basis": mode, "company_month": company_month}
    if total > limit:
        ctx.add_unavailable("products", f"{total - limit} more product(s); pass product or a larger limit")
    adv.inputs = {"input_cost_basis": mode, "limit": parameter(limit, "count")}
    return adv.finish(result, advisor_provenance(
        ["game_stats (history.player_product_stats)", "company_month (ledger)", "upkeep", "produced_last_month", "prices"],
        ["recipes"], adv.methods))


# ====================================================================== find_opportunities

OPP_TYPES = ("route_surplus", "expand_production", "new_product", "research_unlock", "contract_offer")


def _new_production(f: PlayerFacts, adv: Advice, product: str, volume_wanted: float, max_buildings: int, recipe: dict,
                    btype: dict | None) -> dict:
    econ = f.new_building_economics(recipe, btype)
    assume_from(adv, econ)
    rate = num((econ["rate"].get("per_30d") or {}).get(product))
    price = f.sale_price(product)
    upk = econ["upkeep"]
    inputs = []
    res_amt = next((num(r.get("amount")) for r in recipe.get("results") or [] if r.get("product") == product), None)
    for ing in recipe.get("ingredients") or []:
        inputs.append({"product": ing.get("product"), "per_unit": (num(ing.get("amount")) or 0.0) / res_amt if res_amt else None,
                       "value": f.input_value(ing.get("product"))})
    up_pu = {"method": "D-ADV-NEWUPKEEP-1", "value": (num(upk.get("value")) / rate) if rate and num(upk.get("value")) is not None else None,
             "confidence": merge_confidence([upk.get("confidence"), econ["rate"].get("confidence")]) if rate and upk.get("confidence") else None}
    cost = eco.unit_cost(up_pu, inputs)
    if rate:
        buildings = min(eco.ceil_count(volume_wanted / rate), max_buildings)
        volume = min(volume_wanted, buildings * rate)
    else:
        buildings, volume = None, None
    mg = eco.margin(price, cost, None, volume, "theoretical")
    capex_one = num(econ["capex"].get("value"))
    capex_total = capex_one * buildings if capex_one is not None and buildings is not None else None
    monthly = mg.get("per_30d")
    payback = capex_total / monthly if capex_total is not None and monthly and monthly > 0 else None
    conf = merge_confidence([mg.get("confidence"), econ["capex"].get("confidence"), econ["rate"].get("confidence")])
    return {"econ": econ, "rate": rate, "buildings": buildings, "volume": volume, "margin": mg, "capex": capex_total,
            "payback": payback, "confidence": conf, "unit_cost": cost, "price": price}


def find_opportunities(ctx: CallContext) -> dict:
    basis = pin_basis(ctx, static="required", state_sections=("shops",))
    ix = ctx.state_index()
    require_player(ix)
    adv = Advice("find_opportunities", basis)
    f = PlayerFacts(ix)
    types = set(ctx.args.get("types") or [t for t in OPP_TYPES if t != "research_unlock"])
    if ctx.args.get("include_locked"):
        types.add("research_unlock")
    max_b = int(ctx.args.get("max_buildings") or 3)
    section_or_degrade(ctx, adv, "state", "cities", "shops without a known consumption interval are excluded")
    section_or_degrade(ctx, adv, "state", "market", "prices unavailable: margins null")
    section_or_degrade(ctx, adv, "state", "buildings_player", "existing production unknown")
    research = section_or_degrade(ctx, adv, "state", "research", "unlock state unknown: locked recipes not detected")
    adv.assume("A-DEMAND-PERSISTS", "A-PRICE-SHOP", "A-CONTINUOUS", "A-INPUT-MARKET")
    rows = []
    for p in sorted(ix.shops_by_product):
        sd = f.shop_demand(p)
        unmet = num(sd["unmet_per_30d"]) or 0.0
        if sd["shops_unknown_interval"]:
            adv.degrade(f"shop_demand:{p}", "missing_value", f"{sd['shops_unknown_interval']} shop(s) without consumption interval excluded")
        if unmet <= 1e-9:
            continue
        producing = bool([x for x in f.producers.get(p, []) if x["enabled"]])
        spare = f.spare(p)
        if f.supply(p)[2]:
            adv.degrade(f"supply:{p}", "missing_value", f"spare supply excludes {f.supply(p)[2]} producer(s) with an unknown rate")
        evidence = {"unmet_demand_per_30d": estimate(unmet, "units/30d", "D-SHOP-1", adv.conf("high", ["partial_inputs"] if sd["shops_unknown_interval"] else [])),
                    "shops_with_demand": sd["shops"], "spare_supply_per_30d": estimate(spare, "units/30d", "D-RATE-1/2", adv.conf("medium"))}
        if producing and spare >= 1.0 and "route_surplus" in types:
            e = product_economics(f, p)
            vol = min(unmet, spare)
            mpu = e["margin"].get("per_unit")
            monthly = mpu * vol if mpu is not None else None
            conf = adv.track(e["margin"].get("confidence")) or {"level": "low", "factors": ["partial_inputs"]}
            rows.append({"type": "route_surplus", "product": pref(ix, p), "volume_per_30d": estimate(vol, "units/30d", "D-ADV-OPP-1", conf),
                         "margin_per_unit": estimate(mpu, "money/unit", "D-ADV-MARGIN-1", conf),
                         "monthly_margin": estimate(monthly, "money/30d", "D-ADV-OPP-1", conf, 2), "capex": None, "payback_months": None,
                         "buildings_needed": 0, "building_type": None, "recipe": None, "locked_by": [], "evidence": evidence,
                         "confidence": conf, "summary": "Spare supply exists while shops still have unmet demand: route more of it to shops."})
        want = unmet - (spare if producing else 0.0)
        if want <= 1e-9:
            continue
        choice = f.choose_recipe(p)
        recipe = choice.get("recipe")
        if recipe is None:
            continue
        btype = f.building_type_for(recipe)
        locked = f.locked_by(recipe.get("name"), (btype or {}).get("name"))
        if producing:
            typ = "expand_production"
        else:
            typ = "research_unlock" if locked else "new_product"
        if typ not in types:
            continue
        adv.assume("A-NEW-DEFAULT-EFFICIENCY", "A-ACTOR-MODIFIER-1", "A-NO-REGIONAL-COST")
        if eco.is_module_owner(btype):
            adv.assume("A-FULL-DEPOSITS")
        n = _new_production(f, adv, p, want, max_b, recipe, btype)
        conf = adv.track(n["confidence"]) or {"level": "low", "factors": ["partial_inputs"]}
        ingredients = [i.get("product") for i in recipe.get("ingredients") or []]
        evidence.update({"recipe_chosen_by": choice.get("chosen_by"),
                         "rate_per_building_per_30d": estimate(n["rate"], "units/30d", "D-ADV-NEWRATE-1", conf),
                         "rate_basis": n["econ"]["rate"].get("basis"),
                         "inputs_produced_by_player": sorted(make_id("product", i) for i in ingredients if f.producers.get(i)),
                         "inputs_not_produced": sorted(make_id("product", i) for i in ingredients if not f.producers.get(i))})
        rows.append({"type": typ, "product": pref(ix, p), "volume_per_30d": estimate(n["volume"], "units/30d", "D-ADV-OPP-1", conf),
                     "margin_per_unit": estimate(n["margin"].get("per_unit"), "money/unit", "D-ADV-MARGIN-1", conf),
                     "monthly_margin": estimate(n["margin"].get("per_30d"), "money/30d", "D-ADV-OPP-1", conf, 2),
                     "capex": estimate(n["capex"], "money", "D-ADV-CAPEX-1", conf, 2),
                     "payback_months": estimate(n["payback"], "months", "D-ADV-OPP-1", conf, 2),
                     "buildings_needed": n["buildings"], "building_type": make_id("building_type", (btype or {}).get("name")),
                     "recipe": make_id("recipe", recipe.get("name")), "locked_by": locked, "evidence": evidence, "confidence": conf,
                     "summary": {"expand_production": "Shop demand exceeds the company's spare supply: more producers could serve it.",
                                 "new_product": "Unmet shop demand for a product the company does not make yet (unlocked).",
                                 "research_unlock": "Unmet shop demand for a product whose recipe or building is still locked."}[typ]})
    if "contract_offer" in types:
        for off in (ix.market or {}).get("city_contract_offers") or []:
            if off.get("active") or off.get("completed"):
                continue
            p = off.get("product")
            src = "state.market.city_contract_offers[]"
            rows.append({"type": "contract_offer", "product": pref(ix, p), "volume_per_30d": None,
                         "margin_per_unit": None, "monthly_margin": estimate(None, "money/30d", "D-ADV-OPP-1", adv.conf("medium", ["partial_inputs"])),
                         "capex": None, "payback_months": None, "buildings_needed": None, "building_type": None, "recipe": None,
                         "locked_by": [],
                         "evidence": {"amount": observed(off.get("amount"), "units", src + ".amount"),
                                      "price": observed(off.get("price"), "money/unit", src + ".price"),
                                      "reward": observed(off.get("reward"), "money", src + ".reward"),
                                      "penalty": observed(off.get("penalty"), "money", src + ".penalty"),
                                      "remaining_days": observed(off.get("remaining_days"), "days", src + ".remaining_days"),
                                      "issuer": make_id("city", off.get("issuer_actor_id")),
                                      "player_produces": bool(f.producers.get(p))},
                         "confidence": adv.conf("medium", ["partial_inputs"]),
                         "summary": "A city offers a delivery contract (observed terms); its profitability is not estimated."})
    if research is None:
        adv.degrade("state.research", "section_unavailable", "locked recipes not detected")

    for r in rows:
        v = (r.get("monthly_margin") or {}).get("value")
        r["viable"] = None if v is None else v > 0
        # Ranking stays by monthly margin (D-ADV-OPP-1); these flags make the costs the margin hides explicit
        # (live validation: the top pick needed three unproduced inputs and paid back in 31 months).
        ev = r.get("evidence") or {}
        caveats = []
        if ev.get("inputs_not_produced") and not ev.get("inputs_produced_by_player"):
            caveats.append("needs_new_supply_chain")
        pb = (r.get("payback_months") or {}).get("value")
        if pb is not None and pb > LONG_PAYBACK_MONTHS:
            caveats.append("long_payback")
        r["caveats"] = caveats
    if not ctx.args.get("include_unviable"):
        dropped = [r for r in rows if r["viable"] is False]
        if dropped:
            ctx.add_unavailable("opportunities[viable=false]", f"{len(dropped)} candidate(s) with a non-positive estimated monthly "
                                "margin omitted; pass include_unviable=true to list them")
        rows = [r for r in rows if r["viable"] is not False]

    def key(r):
        v = (r.get("monthly_margin") or {}).get("value")
        return (v is None, -(v or 0), {"high": 0, "medium": 1, "low": 2}[r["confidence"]["level"]], r["product"]["id"] if r.get("product") else "")
    rows.sort(key=key)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    data, bounded = bounded_page(ctx, "opportunities", rows)
    adv.inputs = {"types": sorted(types), "max_buildings": parameter(max_b, "count")}
    adv.method("opportunities[]", "D-ADV-OPP-1")
    result = {"opportunities": data["opportunities"]}
    if bounded:
        result["page_bounded_by_size"] = True
    return adv.finish(result, advisor_provenance(["shops demand/stock/price", "market prices", "buildings", "contract offers"],
                                                 ["recipes", "building types"], adv.methods))


# ====================================================================== plan_chain

def plan_chain(ctx: CallContext) -> dict:
    basis = pin_basis(ctx, static="required", state_sections=("buildings_player",))
    ix = ctx.state_index()
    require_player(ix)
    ctx.static(required=True, sections=("recipes", "building_types"))
    adv = Advice("plan_chain", basis)
    f = PlayerFacts(ix)
    product = resolve_product(ctx, ctx.args["product"])
    target = float(ctx.args["target_per_month"])
    mode = ctx.args.get("existing_capacity") or "spare"
    depth = int(ctx.args.get("max_depth") or 8)
    explicit = {}
    for k, v in (ctx.args.get("recipe_choice") or {}).items():
        pk = ctx.resolver().resolve(k, ("product",), "recipe_choice key").key
        rk = ctx.resolver().resolve(v, ("recipe",), f"recipe_choice[{k}]").key
        if pk not in {r.get("product") for r in (f.six.recipes.get(rk) or {}).get("results") or []}:
            raise ToolError("invalid_argument", f"recipe_choice[{k}]: recipe {rk!r} does not produce {pk!r}")
        explicit[pk] = rk
    section_or_degrade(ctx, adv, "state", "research", "build prices and unlock state unknown (base costs used)")
    adv.assume("A-CONTINUOUS", "A-NEW-DEFAULT-EFFICIENCY", "A-ACTOR-MODIFIER-1", "A-NO-REGIONAL-COST", "A-NO-LOGISTICS")
    spare = {}
    if mode == "spare":
        adv.assume("A-SPARE-AVAILABLE")
        for p in sorted(set(f.producers)):
            spare[p] = f.spare(p)
            if f.supply(p)[2]:
                adv.degrade(f"supply:{p}", "missing_value", f"spare supply excludes {f.supply(p)[2]} producer(s) with an unknown rate")
    plan = eco.plan_chain(product, target, choose=lambda p: f.choose_recipe(p, explicit), spare=spare, max_depth=depth)
    state_sold = {s.get("product") for s in ((ix.market or {}).get("state") or {}).get("sold") or []}
    steps = []
    tot_b, tot_capex, tot_up = 0, 0.0, 0.0
    complete = plan["complete"]
    raw = {}
    for s in plan["steps"]:
        p = s["product"]
        choice = s.get("choice") or {}
        recipe = choice.get("recipe")
        row = {"product": pref(ix, p), "depth": s["depth"], "status": s["status"],
               "required_per_30d": estimate(s["required_per_30d"], "units/30d", "D-ADV-PLAN-1", adv.conf("high")),
               "from_existing_per_30d": estimate(s["from_existing_per_30d"], "units/30d", "D-ADV-PLAN-1", adv.conf("medium")),
               "new_per_30d": estimate(s["new_per_30d"], "units/30d", "D-ADV-PLAN-1", adv.conf("high")),
               "recipe": make_id("recipe", (recipe or {}).get("name")), "recipe_chosen_by": choice.get("chosen_by"),
               "building_type": None, "buildings": None, "rate_per_building": None, "capex": None, "upkeep_per_30d": None,
               "locked_by": []}
        if recipe is not None and not (recipe.get("ingredients") or []):
            raw[p] = s["new_per_30d"]
        if s["status"] == "unplannable":
            row["note"] = "no recipe produces this product" + ("; the State sells it" if p in state_sold else "")
            row["sold_by_state"] = p in state_sold
            raw[p] = s["new_per_30d"]
        if (num(s["new_per_30d"]) or 0) > 1e-9 and recipe is not None and s["status"] in ("ok", "cycle", "truncated"):
            btype = f.building_type_for(recipe)
            econ = f.new_building_economics(recipe, btype)
            assume_from(adv, econ)
            rate = num((econ["rate"].get("per_30d") or {}).get(p))
            n = eco.ceil_count(num(s["new_per_30d"]) / rate) if rate else None
            capex_one, up_one = num(econ["capex"].get("value")), num(econ["upkeep"].get("value"))
            conf = adv.track(merge_confidence([econ["rate"].get("confidence"), econ["capex"].get("confidence"),
                                               econ["upkeep"].get("confidence")])) or {"level": "low", "factors": ["partial_inputs"]}
            row.update({"building_type": make_id("building_type", (btype or {}).get("name")), "buildings": n,
                        "rate_per_building": {**estimate(rate, "units/30d", "D-ADV-NEWRATE-1", conf), "basis": econ["rate"].get("basis")},
                        "modules_per_building": econ["modules"] or None,
                        "capex": estimate(capex_one * n if capex_one is not None and n is not None else None, "money", "D-ADV-CAPEX-1", conf, 2),
                        "upkeep_per_30d": estimate(up_one * n if up_one is not None and n is not None else None, "money/30d", "D-ADV-NEWUPKEEP-1", conf, 2),
                        "locked_by": f.locked_by(recipe.get("name"), (btype or {}).get("name"))})
            if econ["rate"].get("basis") == "static_recipe":
                adv.assume("A-STATIC-RECIPE-CYCLE")
            if eco.is_module_owner(btype):
                adv.assume("A-FULL-DEPOSITS")
            if n is None or capex_one is None or up_one is None:
                complete = False
                adv.degrade(f"plan:{p}", "missing_value", "building count, capex or upkeep null for this step")
            else:
                tot_b += n
                tot_capex += capex_one * n
                tot_up += up_one * n
        steps.append(row)
    adv.method("steps[]", "D-ADV-PLAN-1")
    tot_conf = adv.overall()
    result = {"target": {"product": pref(ix, product), "per_month": parameter(target, "units/30d")},
              "existing_capacity": mode, "steps": steps[:60], "steps_total": len(steps), "cycles": [[make_id("product", x) for x in c] for c in plan["cycles"]],
              "totals": {"buildings": tot_b, "capex": estimate(tot_capex, "money", "D-ADV-PLAN-1", tot_conf, 2),
                         "upkeep_per_30d": estimate(tot_up, "money/30d", "D-ADV-PLAN-1", tot_conf, 2),
                         "raw_inputs_per_30d": {make_id("product", k): estimate(v, "units/30d", "D-ADV-PLAN-1", tot_conf) for k, v in raw.items()},
                         "locked_by": sorted({t for s in steps for t in s["locked_by"]}),
                         "complete": complete},
              "note": "per_month = per 30 game days (the game has 30-day months)."}
    adv.inputs = {"target_per_month": parameter(target, "units/30d"), "existing_capacity": mode, "max_depth": parameter(depth, "count"),
                  "recipe_choice": {make_id("product", k): make_id("recipe", v) for k, v in explicit.items()}}
    return adv.finish(result, advisor_provenance(["existing producers (spare capacity)", "player build prices"],
                                                 ["recipes", "building types (costs, efficiency arrays, modules)"], adv.methods))
