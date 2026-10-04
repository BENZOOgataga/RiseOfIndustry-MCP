"""Read-only views over the pinned state/static indexes shared by the advisor tools (architecture section 4).

Only the player company is analysed. Every value carries the snapshot path it came from so tools can emit
Quantity objects with a `source`.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median

from .. import derive
from ..index import StateIndex, StaticIndex, make_id
from ..tools.common import status_of, theoretical_rate
from . import economics as eco
from .contract import clean, num


class PlayerFacts:
    def __init__(self, ix: StateIndex):
        self.ix = ix
        self.six: StaticIndex = ix.static
        self.actor = ix.player_actor_id
        self.company = ix.companies.get(self.actor) or {}
        self.buildings = [b for b in ix.buildings_full.values() if b.get("owner_actor_id") == self.actor]
        self.by_key = {b["key"]: b for b in self.buildings}
        self.rates: dict[str, dict] = {}
        self.status: dict[str, dict] = {}
        self.producers: dict[str, list[dict]] = defaultdict(list)
        self.consumers: dict[str, list[dict]] = defaultdict(list)
        for b in self.buildings:
            self.status[b["key"]] = status_of(self.six, b)
            if not b.get("recipe"):
                continue
            rate = theoretical_rate(self.six, b)
            self.rates[b["key"]] = rate
            enabled = (b.get("flags") or {}).get("user_enabled", True) is not False
            recipe = self.six.recipes.get(b.get("recipe")) or {}
            total_res = sum(num(r.get("amount")) or 0.0 for r in recipe.get("results") or [])
            for o in rate.get("outputs") or []:
                amt = num(o.get("amount_per_cycle")) or 0.0
                self.producers[o["product"]].append({
                    "building": b, "per_30d": num(o.get("per_30d")) if rate.get("available") else None, "enabled": enabled,
                    "approximate": bool(rate.get("approximate")), "share": (amt / total_res) if total_res else 1.0})
            for i in rate.get("inputs") or []:
                self.consumers[i["product"]].append({
                    "building": b, "per_30d": num(i.get("per_30d")) if rate.get("available") else None, "enabled": enabled})
        self.routes = [r for r in ix.routes.values() if (ix.building(r["origin"]) or {}).get("owner_actor_id") == self.actor]
        self.routes_by_dest_product: dict[tuple, list[dict]] = defaultdict(list)
        for r in self.routes:
            self.routes_by_dest_product[(r.get("endpoint") or r["destination"], r.get("product"))].append(r)

    # ---- production balance (D-RATE-1/2, D-SUPDEM-1 inputs)
    def supply(self, product: str) -> tuple[float, bool, int]:
        """(theoretical supply per 30 d of enabled producers, approximate?, producers with unknown rate)."""
        tot, approx, unknown = 0.0, False, 0
        for p in self.producers.get(product, []):
            if not p["enabled"]:
                continue
            if p["per_30d"] is None:
                unknown += 1
                continue
            tot += p["per_30d"]
            approx = approx or p["approximate"]
        return tot, approx, unknown

    def internal_need(self, product: str) -> float:
        return sum(c["per_30d"] or 0.0 for c in self.consumers.get(product, []) if c["enabled"])

    def spare(self, product: str) -> float:
        return max(self.supply(product)[0] - self.internal_need(product), 0.0)

    def produced_last_month(self, product: str) -> float | None:
        vals = []
        for p in self.producers.get(product, []):
            prod = (p["building"].get("production") or {})
            v = num(prod.get("produced_last_month"))
            if v is None:
                return None
            # produced_last_month sums all result amounts of the recipe (research buildings-production 3.1)
            vals.append(v * p["share"])
        return sum(vals) if vals else None

    def stock(self, product: str) -> float:
        return sum(num(i.get("count")) or 0.0 for b in self.buildings for i in b.get("inventory") or [] if i.get("product") == product)

    def shop_demand(self, product: str) -> dict:
        """Player's shop demand for a product, per interval and normalised to 30 days (D-SHOP-1 inputs)."""
        per_interval = 0.0
        per_30 = 0.0
        unmet_30 = 0.0
        shops = 0
        unknown = 0
        prices = []
        for s in self.ix.shops_by_product.get(product, []):
            if s.get("is_dead"):
                continue
            sp = next((x for x in s.get("products") or [] if x.get("product") == product), None)
            if sp is None:
                continue
            d = num(sp.get("demand_for_player"))
            if d is None:
                continue
            shops += 1
            per_interval += d
            if d > 0 and num(sp.get("price_for_player")):
                prices.append(num(sp.get("price_for_player")))
            interval = (self.ix.cities.get(s.get("city_id")) or {}).get("consumption_interval_days")
            u = derive.unmet_demand(d, sp.get("player_delivered_stock"), interval)
            v30 = derive.per_interval_to_30d(d, interval)
            if v30 is None:
                unknown += 1
                continue
            per_30 += v30
            unmet_30 += u.get("per_30d") or 0.0
        return {"shops": shops, "per_interval": clean(per_interval), "per_30d": clean(per_30), "unmet_per_30d": clean(unmet_30),
                "shops_unknown_interval": unknown, "prices_with_demand": prices}

    # ---- prices
    def market_row(self, product: str) -> dict:
        return self.ix.market_prices.get(product) or {}

    def sale_price(self, product: str) -> dict:
        m = self.market_row(product)
        return eco.sale_price(self.shop_demand(product)["prices_with_demand"], m.get("final_price_for_player"), m.get("price"))

    def input_value(self, product: str) -> dict:
        m = self.market_row(product)
        return eco.input_value(m.get("final_price_for_player"), m.get("price"))

    # ---- costs
    @staticmethod
    def upkeep_of(b: dict) -> tuple[float | None, bool]:
        """(observed active monthly upkeep, fell back to monthly_full?)."""
        up = b.get("upkeep") or {}
        a = num(up.get("monthly_active"))
        if a is not None:
            return a, False
        f = num(up.get("monthly_full"))
        return (f, True) if f is not None else (None, False)

    def player_build_cost(self, btype: str | None) -> tuple[float | None, str]:
        if btype is None:
            return None, "unknown"
        table = (self.ix.player_research or {}).get("building_costs")
        if table is not None:
            for row in table:
                if row.get("building_type") == btype and num(row.get("cost")) is not None:
                    return num(row.get("cost")), "current_player_cost"
        return None, "base_cost"

    def unlocked(self) -> set | None:
        return self.ix.player_unlocked

    def recipe_unlocked(self, recipe: str) -> bool | None:
        return self.six.recipe_unlocked(recipe, self.unlocked())

    def building_unlocked(self, btype: str) -> bool | None:
        return self.six.building_unlocked(btype, self.unlocked())

    def locked_by(self, recipe: str | None, btype: str | None) -> list[str]:
        """Tech ids that would unlock the recipe / building type (empty when unlocked or unknown)."""
        out = []
        if recipe and self.recipe_unlocked(recipe) is False:
            out.extend(make_id("tech", u) for u in self.six.recipe_unlocked_by.get(recipe) or [])
        if btype and self.building_unlocked(btype) is False:
            out.extend(make_id("tech", u) for u in self.six.building_unlocked_by.get(btype) or [])
        return sorted(set(out))

    def peer_rates(self, recipe: str, btype: str) -> list[float]:
        """Observed per-building rates (first result) of the player's buildings running recipe on btype."""
        out = []
        r = self.six.recipes.get(recipe) or {}
        first = ((r.get("results") or [{}])[0]).get("product")
        for b in self.buildings:
            if b.get("recipe") != recipe or b.get("prefab") != btype:
                continue
            if (b.get("flags") or {}).get("user_enabled") is False:
                continue
            rate = self.rates.get(b["key"]) or {}
            if not rate.get("available") or rate.get("approximate"):
                continue
            v = next((num(o.get("per_30d")) for o in rate.get("outputs") or [] if o.get("product") == first), None)
            if not v:
                continue
            bt = self.effective_btype(self.six.building_types.get(btype)) or {}
            # A peer runs at its own efficiency; a new building starts at the type's initial index. The ratio of the
            # catalogue arrays keeps the company modifier that is part of the observed rate.
            arr = bt.get("efficiency_output") or []
            init, idx = bt.get("initial_efficiency_index"), (b.get("efficiency") or {}).get("index")
            if not (isinstance(init, int) and isinstance(idx, int) and 0 <= init < len(arr) and 0 <= idx < len(arr)
                    and num(arr[init]) and num(arr[idx])):
                continue
            v = v * num(arr[init]) / num(arr[idx])
            if eco.is_module_owner(bt):
                # A peer's rate reflects its own module count; a new hub is planned with the maximum module count.
                count = num((b.get("modules") or {}).get("count"))
                if not count:
                    continue
                v = v / count * (num(bt.get("max_module_count")) or count)
            out.append(v)
        return out

    def choose_recipe(self, product: str, explicit: dict | None = None) -> dict:
        """recipe_choice, else the recipe the player's producers use, else the first unlocked, else the first."""
        explicit = explicit or {}
        if product in explicit:
            return {"recipe": self.six.recipes.get(explicit[product]), "chosen_by": "recipe_choice"}
        used: dict[str, int] = defaultdict(int)
        for p in self.producers.get(product, []):
            used[p["building"].get("recipe")] += 1
        if used:
            name = max(sorted(used), key=lambda k: used[k])
            if name in self.six.recipes:
                return {"recipe": self.six.recipes[name], "chosen_by": "player_producers"}
        names = self.six.recipes_by_result.get(product) or []
        if not names:
            return {"recipe": None, "chosen_by": "none"}
        for n in names:
            if self.recipe_unlocked(n) is not False:
                return {"recipe": self.six.recipes[n], "chosen_by": "first_unlocked" if len(names) > 1 else "only_recipe"}
        return {"recipe": self.six.recipes[names[0]], "chosen_by": "first_recipe_locked"}

    def building_type_for(self, recipe: dict) -> dict | None:
        """The building type to build for a recipe: one the player already uses for it, else the first unlocked."""
        types = list(recipe.get("building_types") or [])
        for b in self.buildings:
            if b.get("recipe") == recipe.get("name") and b.get("prefab") in types:
                return self.six.building_types.get(b["prefab"])
        for t in types:
            if self.building_unlocked(t) is not False and t in self.six.building_types:
                return self.six.building_types[t]
        return self.six.building_types.get(types[0]) if types else None

    def difficulty(self) -> dict:
        return self.ix.session.get("difficulty") or {}

    def module_max(self, btype_name: str | None) -> int | None:
        """Module limit the player's buildings of this type report (state modules.max: it includes research
        upgrades the catalogue's max_module_count does not), when they all agree; else None."""
        seen = {int(m["max"]) for b in self.buildings if b.get("prefab") == btype_name and not b.get("is_module")
                for m in [b.get("modules") or {}] if num(m.get("max"))}
        return seen.pop() if len(seen) == 1 else None

    def effective_btype(self, btype: dict | None) -> dict | None:
        """The building type with max_module_count replaced by the player's observed module limit (module_max)."""
        if not btype or not eco.is_module_owner(btype):
            return btype
        mx = self.module_max(btype.get("name"))
        if mx is None or mx == num(btype.get("max_module_count")):
            return btype
        return {**btype, "max_module_count": mx, "max_module_count_basis": "observed_player_buildings"}

    def type_modifiers(self, btype_name: str | None) -> dict:
        """D-ADV-TYPEMOD-1: company (actor) modifiers of a building type, measured on the player's buildings of that
        type. output = output_multiplier / efficiency_output[index]; upkeep = monthly_full upkeep /
        (upkeep base x difficulty x upkeep_multiplier). A modifier is used only when every measured building agrees."""
        cache = self.__dict__.setdefault("_typemods", {})
        if btype_name in cache:
            return cache[btype_name]
        bt = self.six.building_types.get(btype_name) or {}
        mtype = self.six.building_types.get(bt.get("module_prefab")) if eco.is_module_owner(bt) else None
        diff = num(self.difficulty().get("upkeep"))
        arr = bt.get("efficiency_output") or []
        outs, upks, assumptions = [], [], set()
        for b in self.buildings:
            if b.get("prefab") != btype_name or b.get("is_module"):
                continue
            e = b.get("efficiency") or {}
            idx, om, um = e.get("index"), num(e.get("output_multiplier")), num(e.get("upkeep_multiplier"))
            if isinstance(idx, int) and 0 <= idx < len(arr) and om and num(arr[idx]):
                outs.append(om / num(arr[idx]))
            full = num((b.get("upkeep") or {}).get("monthly_full"))
            count = int(num((b.get("modules") or {}).get("count")) or 0) if mtype is not None else 0
            ub = eco.upkeep_base(bt, mtype, count)
            if full and um and diff and ub["value"]:
                upks.append(full / (ub["value"] * diff * um))
                assumptions.update(ub["assumptions"])

        def agreed(xs: list[float]) -> dict:
            if not xs:
                return {"value": None, "peers": 0, "range": None, "reason": "the player owns no measurable building of this type"}
            lo, hi = min(xs), max(xs)
            rng = [round(lo, 4), round(hi, 4)]
            if hi - lo > 1e-3 * max(abs(hi), 1e-9):
                return {"value": None, "peers": len(xs), "range": rng, "reason": "the player's buildings of this type disagree"}
            return {"value": median(xs), "peers": len(xs), "range": rng}

        out = {"method": "D-ADV-TYPEMOD-1", "output": agreed(outs), "upkeep": {**agreed(upks), "assumptions": sorted(assumptions)}}
        cache[btype_name] = out
        return out

    def new_building_economics(self, recipe: dict, btype: dict | None) -> dict:
        """Rate, upkeep and capex of one new building (D-ADV-NEWRATE-1, NEWUPKEEP-1, CAPEX-1), with the company
        modifiers measured on the player's buildings of the same type (D-ADV-TYPEMOD-1) when available."""
        btype = self.effective_btype(btype)
        tm = self.type_modifiers((btype or {}).get("name")) if btype else {"output": None, "upkeep": None}
        rate = eco.new_building_rate(recipe, btype, self.peer_rates(recipe["name"], (btype or {}).get("name")) if btype else (),
                                     tm["output"])
        modules = rate.get("modules") or 0 if eco.is_module_owner(btype) else 0
        if eco.is_module_owner(btype) and rate.get("basis") == "observed_peer":
            modules = int(num((btype or {}).get("max_module_count")) or 0)
        mtype = self.six.building_types.get((btype or {}).get("module_prefab")) if eco.is_module_owner(btype) else None
        upkeep = eco.new_building_upkeep(btype, mtype, modules, self.difficulty().get("upkeep"), tm["upkeep"]) if btype else \
            {"method": "D-ADV-NEWUPKEEP-1", "value": None, "confidence": None, "reason": "no building type"}
        pc, _ = self.player_build_cost((btype or {}).get("name"))
        mpc, _ = self.player_build_cost((mtype or {}).get("name"))
        cap = eco.capex(pc, (btype or {}).get("base_cost"), modules, mpc, (mtype or {}).get("base_cost")) if btype else \
            {"method": "D-ADV-CAPEX-1", "value": None, "confidence": None, "reason": "no building type"}
        return {"rate": rate, "upkeep": upkeep, "capex": cap, "modules": modules,
                "module_type": (mtype or {}).get("name"), "type_modifiers": tm,
                "module_max_basis": (btype or {}).get("max_module_count_basis", "catalogue") if mtype else None}

    def building_status(self, key: str) -> dict:
        return self.status.get(key) or {}
