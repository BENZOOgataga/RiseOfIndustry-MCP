"""Deterministic builder of SYNTHETIC snapshot fixtures (heartbeat/static/state/history).

All names are invented. Running this module writes the committed sample files into
`tests/fixtures/sample/`:

    uv run --directory mcp-server python tests/fixtures/build_fixtures.py

Tests import the builder functions to create scenario-specific variants in temporary exchange
directories. The synthetic world:

* companies: player "Acme Industries" (actor 1), AI "Borealis Corp" (2), AI "Cobalt Works" (3);
  State actor 5; cities Valmont (10), Brindlewick (11), Saint-Eloi with accent (12)
* chain: gas wells -> petrochemical plant (recipe Chemicals: Gas 3 -> Chemicals 2, 20 d)
  -> paint factory (recipe Paints: Chemicals 1 + Dye 2 -> Paint 2, 35 d) -> shops / warehouse;
  Dye from Flowers + Water
* routes with shared Max Send, auto shop demand, keep-all Min Keep, dormant AUTO_WH, a no-path
  error, a duplicate slot and a rail route
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
PID = 4242
PROCESS_START = BASE_TIME - timedelta(minutes=30)
WORLD_SESSION = "5e5510a0-0000-4000-8000-000000000001"
WORLD_SESSION_2 = "5e5510a0-0000-4000-8000-000000000002"

GAME = {
    "version": "2.3.3", "build": "0507b", "commit": "76359e59644ebf55cafacf9a50b3d19800df22d7",
    "savegame_version": 2304,
    "assembly_sha256": "D62599EFD0CFCB9F343E7FF74AAC19533F572062B9CECA82E1D3911507D04803",
    "release": "Steam - Public",
}

PLAYER, AI_B, AI_C, STATE_ACTOR = 1, 2, 3, 5
CITY_VAL, CITY_BRI, CITY_SEL = 10, 11, 12
R_VAL = "a1000000-0000-4000-8000-000000000001"
R_BRI = "a1000000-0000-4000-8000-000000000002"
R_SEL = "a1000000-0000-4000-8000-000000000003"
R_GRE = "a1000000-0000-4000-8000-000000000004"
R_NOR = "a1000000-0000-4000-8000-000000000005"

B_HQ = "Headquarters@50,50"
B_GW1 = "GasWell@20,30"
B_GW2 = "GasWell@24,36"
B_GW3 = "GasWell@80,80"
B_PC1 = "PetrochemicalFactory@40,32"
B_PC2 = "PetrochemicalFactory@44,60"
B_FF = "FlowerFarm@70,20"
B_WS = "WaterSiphon@72,26"
B_CP = "ChemicalPlant@60,30"
B_PF1 = "PaintFactory@55,40"
B_PF2 = "PaintFactory@90,90"
B_WH = "Warehouse@58,44"
B_TD = "TruckDepot@30,35"
S_HW1 = "HardwareStore@100,100"
S_CS1 = "ConstructionStore@104,98"
S_HW2 = "HardwareStore@200,150"
S_GS = "GeneralStore@300,40"
AI_PF = "PaintFactory@150,120"

INT_MAX = 2147483647
DISPATCH_DIFFICULTY = 1.25
MANUAL_FORMULA = "(250 + distance * 10) * difficulty * actor"


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def content_hash(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


# ============================================================================ static

def _named(name, display, english):
    return {"name": name, "display_name": display, "english_name": english}


def _product(name, display, english, category, group, tags, formula, demand=1.0):
    return {**_named(name, display, english), "category": category, "category_group": group, "tags": tags,
            "price_formula": formula, "demand_modifier": demand, "end_game": False, "disable_contracts": False}


def _recipe(name, display, english, ingredients, results, days, buildings, modules=(), tier=1, water=False):
    return {**_named(name, display, english),
            "ingredients": [{"product": p, "amount": a} for p, a in ingredients],
            "results": [{"product": p, "amount": a} for p, a in results],
            "game_days": float(days), "game_days_for_price": float(days), "required_modules": list(modules),
            "tier": tier, "building_types": list(buildings), "used_by_water_harvester": water}


def _btype(name, display, english, cost, tags, category, recipes=(), slots=40, module_prefab=None, max_modules=None,
           is_module=False, shop=None, fleet=None, slots_dest=3, upkeep=0.025):
    eff = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
    return {**_named(name, display, english), "base_cost": float(cost), "tags": list(tags), "category": category,
            "recipes": list(recipes), "production_speed": 1.0 if recipes else None, "storage_slots": slots,
            "storage_kind": "product_specific", "max_module_count": max_modules,
            "module_radius": 15.0 if max_modules else None, "module_prefab": module_prefab,
            "delivered_to_hub": (False if max_modules else None), "upkeep_cost_percentage": upkeep, "min_upkeep": 0.25,
            "efficiency_output": eff, "efficiency_upkeep": eff, "initial_efficiency_index": 3,
            "fleet_vehicle_prefab": fleet, "fleet_max_vehicles": 5 if fleet else None,
            "manual_destination_slots": slots_dest, "manual_destination_infinite": False,
            "name_format": "%n %i", "shop": shop, "is_module": is_module,
            "dispatch_formula": None if is_module or shop else "ManualDestinationDispatchCost"}


def _unlock(name, kind, display, english, tier, required=(), buildings=(), recipes=(), teaser=False, default=False,
            tree="Chemistry", column=0, price=None):
    return {**_named(name, display, english), "kind": kind, "tier": tier, "required": list(required), "included": [],
            "teaser": teaser, "unlocked_by_default": default, "placements": [{"tree": tree, "column": column}],
            "research_cost_formula": "Research Cost", "research_time_formula": "Research Time",
            "buildings": list(buildings), "building_category": None, "recipes": list(recipes),
            "price_percentage": price}


def static_data() -> dict:
    products = [
        _product("Gas", "Gaz", "Gas", "RawResources", "RawResources", ["raw"], "RawResources"),
        _product("Water", "Eau", "Water", "RawResources", "RawResources", ["raw", "water"], "RawResources"),
        _product("Flowers", "Fleurs", "Flowers", "FarmProduce", "FarmProduce", ["farm"], "FarmProduce"),
        _product("Chemicals", "Produits chimiques", "Chemicals", "Components", "Components", ["component"], "Factories"),
        _product("Dye", "Teinture", "Dye", "Components", "Components", ["component"], "Factories"),
        _product("Paint", "Peinture", "Paint", "Row1", "Products", ["paint", "consumer"], "Factories", 1.2),
    ]
    categories = [
        {**_named("RawResources", "Ressources brutes", "Raw resources"), "parent": None, "price_multiplier": 1.0, "growth_multiplier": None},
        {**_named("FarmProduce", "Produits agricoles", "Farm produce"), "parent": None, "price_multiplier": 1.0, "growth_multiplier": None},
        {**_named("Components", "Composants", "Components"), "parent": None, "price_multiplier": 1.0, "growth_multiplier": None},
        {**_named("Row1", "Biens de consommation", "Consumer goods"), "parent": None, "price_multiplier": 1.1, "growth_multiplier": 1.0},
    ]
    recipes = [
        _recipe("Gas", "Gaz", "Gas", [], [("Gas", 2)], 10, ["GasWell"], ["GasPump"]),
        _recipe("Water", "Eau", "Water", [], [("Water", 4)], 10, ["WaterSiphon"], ["WaterPump"], water=True),
        _recipe("Flowers", "Fleurs", "Flowers", [], [("Flowers", 3)], 30, ["FlowerFarm"], ["FlowerField"]),
        _recipe("Chemicals", "Produits chimiques", "Chemicals", [("Gas", 3)], [("Chemicals", 2)], 20, ["PetrochemicalFactory"]),
        _recipe("Dye", "Teinture", "Dye", [("Flowers", 2), ("Water", 1)], [("Dye", 2)], 20, ["ChemicalPlant"]),
        _recipe("Paints", "Peintures", "Paints", [("Chemicals", 1), ("Dye", 2)], [("Paint", 2)], 35, ["PaintFactory"]),
        _recipe("PaintsAdvanced", "Peintures avancees", "Advanced paints", [("Chemicals", 2), ("Dye", 1)], [("Paint", 3)], 30,
                ["PaintFactory"], tier=2),
    ]
    shop_paint = {"max_products": 8, "sold_tags": ["paint", "component"], "demand_modifier": 5.0}
    shop_general = {"max_products": 8, "sold_tags": ["paint", "consumer"], "demand_modifier": 1.0}
    btypes = [
        _btype("Headquarters", "Siege", "Headquarters", 0, ["hq"], "Special", slots=0, slots_dest=0),
        _btype("GasWell", "Puits de gaz", "Gas Well", 120000, ["gatherer"], "Gatherers", ["Gas"], module_prefab="GasPump", max_modules=3),
        _btype("GasPump", "Pompe a gaz", "Gas Pump", 20000, ["harvester"], "Gatherers", is_module=True, slots=0),
        _btype("WaterSiphon", "Siphon", "Water Siphon", 80000, ["gatherer"], "Gatherers", ["Water"], module_prefab="WaterPump", max_modules=3),
        _btype("WaterPump", "Pompe a eau", "Water Pump", 10000, ["harvester"], "Gatherers", is_module=True, slots=0),
        _btype("FlowerFarm", "Ferme florale", "Flower Farm", 90000, ["farm"], "Farms", ["Flowers"], slots=100, module_prefab="FlowerField", max_modules=3),
        _btype("FlowerField", "Champ de fleurs", "Flower Field", 5000, ["field"], "Farms", is_module=True, slots=0),
        _btype("PetrochemicalFactory", "Usine petrochimique", "Petrochemical Plant", 400000, ["factory"], "Factories", ["Chemicals"]),
        _btype("ChemicalPlant", "Usine chimique", "Chemical Plant", 300000, ["factory"], "Factories", ["Dye"]),
        _btype("PaintFactory", "Usine de peinture", "Paint Factory", 500000, ["factory"], "Factories", ["Paints", "PaintsAdvanced"]),
        _btype("Warehouse", "Entrepot", "Warehouse", 150000, ["warehouse"], "Logistics", slots=100, slots_dest=9),
        _btype("TruckDepot", "Depot de camions", "Truck Depot", 100000, ["depot"], "Logistics", slots=0, fleet="Vehicle"),
        _btype("TrainTerminal", "Terminal ferroviaire", "Train Terminal", 250000, ["depot", "module"], "Logistics", is_module=True,
               slots=0, fleet="TrainVehicle"),
        _btype("HardwareStore", "Quincaillerie", "Hardware Store", 0, ["shop"], "Shops", shop=shop_paint),
        _btype("ConstructionStore", "Magasin de construction", "Construction Store", 0, ["shop"], "Shops", shop=shop_paint),
        _btype("GeneralStore", "Epicerie generale", "General Store", 0, ["shop"], "Shops", shop=shop_general),
        _btype("TradingPost", "Comptoir d'Etat", "State Trading Post", 0, ["state_trading"], "Special", slots=0),
    ]
    unlocks = [
        _unlock("BasicGas", "building", "Gaz de base", "Basic gas", 1, buildings=["GasWell", "GasPump"], recipes=["Gas"], default=True),
        _unlock("BasicFarming", "building", "Agriculture", "Basic farming", 1, buildings=["FlowerFarm", "FlowerField", "WaterSiphon", "WaterPump"],
                recipes=["Flowers", "Water"], default=True, column=1),
        _unlock("Petrochemistry", "building", "Petrochimie", "Petrochemistry", 1, buildings=["PetrochemicalFactory"], recipes=["Chemicals"], column=2),
        _unlock("Dyes", "building", "Teintures", "Dyes", 1, buildings=["ChemicalPlant"], recipes=["Dye"], column=3),
        _unlock("Paints", "building", "Peintures", "Paints", 2, required=["Petrochemistry", "Dyes"], buildings=["PaintFactory"],
                recipes=["Paints"], column=4),
        _unlock("AdvancedPaints", "recipe", "Peintures avancees", "Advanced paints", 3, required=["Paints"], recipes=["PaintsAdvanced"], column=5),
        _unlock("Railways", "building", "Chemins de fer", "Railways", 2, buildings=["TrainTerminal"], tree="Logistics", column=0),
        _unlock("Plastics", "generic", "Plastiques", "Plastics", 3, required=["Petrochemistry"], column=6),
        _unlock("Polymers", "other", "Polymeres", "Polymers", 4, required=["Plastics"], column=7),
        _unlock("FutureTech", "other", "Technologie future", "Future tech", 5, teaser=True, column=8),
        _unlock("PaintDiscount", "building_price", "Remise peinture", "Paint discount", 2, required=["Paints"],
                buildings=["PaintFactory"], price=0.9, column=9),
    ]
    formulas = [
        {"name": "ManualDestinationDispatchCost", "text": MANUAL_FORMULA},
        {"name": "TruckDepotDispatchCost", "text": "(350 + distance * 15) * difficulty * actor"},
        {"name": "TrainTerminalDispatchCost", "text": "(2250 + distance * 25) * difficulty * actor"},
        {"name": "Research Cost", "text": "3333.333333 * efficiency"},
        {"name": "Research Time", "text": "(60 + (tier ^ 3) * 60) / efficiency"},
        {"name": "Settlement Distance Restriction", "text": "max(abs(x0 - x1), abs(y0 - y1))"},
    ]
    bills = [_named(n, d, e) for n, d, e in [
        ("ProductTrade", "Commerce", "Product trade"), ("Upkeep", "Location", "Renting"),
        ("RouteVehicleUpkeep", "Entretien vehicules de route", "Route Vehicle Upkeep"),
        ("ResearchCosts", "R&D", "R&D Expenses"), ("Loan Payments", "Remboursements", "Loan Payments"),
        ("BuildingConstruction", "Construction", "Building construction"), ("PermitPurchase", "Permis", "Permit purchase")]]
    overview = [
        {**_named("Operations", "Operations", "Operations"), "type": "REOCCURRING", "ui_order": 0,
         "bill_categories": ["ProductTrade", "Upkeep", "RouteVehicleUpkeep", "ResearchCosts"]},
        {**_named("Financing", "Financement", "Financing"), "type": "REOCCURRING", "ui_order": 1, "bill_categories": ["Loan Payments"]},
        {**_named("Investments", "Investissements", "Investments"), "type": "ONE_TIME", "ui_order": 2,
         "bill_categories": ["BuildingConstruction", "PermitPurchase"]},
    ]
    tiers = [
        {**_named("Village", "Village", "Village"), "tier_id": 1, "threshold_min": 0, "threshold_max": 20000, "placed_shops_count": 2, "efficiency": 1.0, "next_tier": "Town"},
        {**_named("Town", "Bourg", "Town"), "tier_id": 2, "threshold_min": 20000, "threshold_max": 80000, "placed_shops_count": 4, "efficiency": 1.2, "next_tier": "City"},
        {**_named("City", "Ville", "City"), "tier_id": 3, "threshold_min": 80000, "threshold_max": 400000, "placed_shops_count": 6, "efficiency": 1.5, "next_tier": None},
    ]
    return {
        "module": {"id": "base", "name": "Base Game"},
        "language": "French",
        "english_names_available": True,
        "products": products,
        "product_categories": categories,
        "recipes": recipes,
        "building_types": btypes,
        "tech_trees": [
            {**_named("Chemistry", "Chimie", "Chemistry"), "category": "Industry", "ui_order": 0, "tier_count": 5},
            {**_named("Logistics", "Logistique", "Logistics"), "category": "Industry", "ui_order": 1, "tier_count": 3},
        ],
        "tech_categories": [{**_named("Industry", "Industrie", "Industry"), "trees": ["Chemistry", "Logistics"]}],
        "tech_unlocks": unlocks,
        "tech_config": {"max_enqueued_unlocks": 9, "efficiency_values": [0.5, 0.75, 1.0, 1.25, 1.5],
                        # Positional: null where an efficiency level needs no unlock (as in the live game).
                        "efficiency_unlocks": [None, None, "ResearchEfficiencyL2", "ResearchEfficiencyL3", "ResearchEfficiencyL4"]},
        "formulas": formulas,
        "bill_categories": bills,
        "overview_categories": overview,
        "settlement_tiers": tiers,
        "settlement_types": [_named("Settlement", "Ville", "Settlement")],
        "permit_types": [
            {**_named("Region", "Region", "Region"), "cost_per_tile": 1000.0, "cost_modifier": 1.0, "parent": None, "top_level": "Region"},
            {**_named("RegionFull", "Permis complet", "Full permit"), "cost_per_tile": 0.0, "cost_modifier": 1.0, "parent": "Region", "top_level": "Region"},
        ],
        "loan_infos": [
            {"name": "StarterLoanNormal", "type": "STARTER", "title": "Starter Loan", "amount": 7500000.0, "apr": 0.0, "duration_months": 120, "grace_months": 24},
            {"name": "BankLoan", "type": "BANK", "title": "Bank Loan", "amount": 1000000.0, "apr": 0.08, "duration_months": 60, "grace_months": 0},
        ],
        "mods": [],
    }


# ============================================================================ envelope helpers

def section_status(game_day: int, items: int = 1, status: str = "ok", reason=None) -> dict:
    return {"status": status, "reason": reason, "game_day": game_day, "frame_start": 1000, "frame_end": 1010,
            "items": items, "items_vanished": 0, "main_thread_ms": 1.5}


def envelope(family: str, data: dict, *, seq: int, now: datetime, world_session: str = WORLD_SESSION, pid: int = PID,
             static_ref: dict | None = None, sections: dict | None = None, consistent: bool = True,
             game_date: str = "Y5-03-12", game_day: int = 1512, capture_age_s: float = 2.0,
             schema_version: str = "1.0.0") -> dict:
    end = now - timedelta(seconds=capture_age_s)
    start = end - timedelta(milliseconds=300)
    return {
        "schema": f"roi-mcp/{family}",
        "schema_version": schema_version,
        "observer_version": "1.0.0",
        "compatibility": "verified",
        "game": dict(GAME),
        "pid": pid,
        "world_session": world_session,
        "seq": seq,
        "content_hash": content_hash(data),
        "written_utc": iso(end + timedelta(milliseconds=50)),
        "captured": {"utc_start": iso(start), "utc_end": iso(end), "game_day_start": game_day, "game_day_end": game_day,
                     "game_date": game_date, "frames": [1000, 1010], "main_thread_ms": 12.5, "slices": 8,
                     "max_slice_ms": 1.9, "consistent": consistent},
        "static_ref": static_ref,
        "sections": sections if sections is not None else {k: section_status(game_day) for k in data},
        "warnings": [],
        "data": data,
    }


def build_static(now: datetime = BASE_TIME, seq: int = 1, world_session: str = WORLD_SESSION, pid: int = PID) -> dict:
    data = static_data()
    return envelope("static", data, seq=seq, now=now, world_session=world_session, pid=pid, capture_age_s=60.0,
                    sections={k: section_status(1512) for k in ("products", "recipes", "building_types", "tech", "formulas")})


def static_ref_of(static_doc: dict) -> dict:
    return {"seq": static_doc["seq"], "content_hash": static_doc["content_hash"]}


# ============================================================================ state

def _flags(enabled=True, req=True, working=True):
    return {"user_enabled": enabled, "requirements_met": req, "is_working": working}


def _inv(product, role, count, slots=40, incoming=0, outgoing=0, cap=0):
    return {"product": product, "role": role, "count": count, "slots": slots, "stored": count + outgoing,
            "incoming_reserved": incoming, "outgoing_reserved": outgoing, "inbound_cap": cap}


def _module(key, prefab, progress=0.5, speed=1.0, efficiency=1.0, nodes=4, depleted=0, remaining=5000, resource="Gas",
            enabled=True, working=True):
    return {"key": key, "prefab": prefab, "progress": progress, "efficiency": efficiency, "min_guaranteed_speed": 0.25,
            "max_resources": 4, "speed_replica": speed, "resource": resource, "nodes": nodes, "nodes_depleted": depleted,
            "deposit_remaining": remaining, "user_enabled": enabled, "requirements_met": True, "is_working": working}


def _building(key, display, kind, tags, *, region=R_GRE, city=None, paid=100000.0, recipe=None, cycle=None,
              flags=None, inventory=(), modules=None, production=None, notifications=(), polluted=False,
              logistics=None, fleet=None, is_module=False, module_owner=None, owner=PLAYER, eff_index=3):
    prefab, rest = key.split("@", 1)
    x, y = (int(v) for v in rest.split("#")[0].split(","))
    upkeep_full = round(paid * 0.025, 2)
    return {
        "key": key, "save_guid": None if "PaintFactory@90" in key else f"guid-{abs(hash_int(key)):08x}",
        "prefab": prefab, "display_name": display, "owner_actor_id": owner, "x": x, "y": y, "rotation": 0,
        "region_id": region, "city_id": city, "tags": list(tags), "kind": kind,
        "flags": flags or _flags(), "paid_to_build": paid,
        "efficiency": {"kind": "building", "index": eff_index, "output_multiplier": [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0][eff_index],
                       "upkeep_multiplier": [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0][eff_index]},
        "upkeep": {"monthly_full": upkeep_full, "monthly_active": upkeep_full, "accrued_this_month": round(upkeep_full * 0.4, 2), "days_up": 12},
        "recipe": recipe, "cycle_days_effective": cycle, "production": production, "modules": modules,
        "inventory": list(inventory), "logistics": logistics, "notifications": list(notifications),
        "pollution_at_tile": 0.4 if polluted else 0.0, "is_polluted": polluted, "fleet": fleet,
        "is_module": is_module, "module_owner": module_owner, "is_shop": False,
    }


def hash_int(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)


def _prod(progress=0.5, this_month=10, last_month=40, total=500, avg=38.5, frames=1000, spent=850):
    return {"progress": progress, "produced_this_month": this_month, "produced_last_month": last_month,
            "total_produced": total, "average_10_months": avg, "production_frames": frames,
            "frames_spent_producing": spent, "final_speed": 1.0}


def _logi(accepted=(), outgoing=(), auto_wh=False, warehouse=None, slots=3):
    return {"options": 1 if auto_wh else 0, "auto_wh": auto_wh, "warehouse": warehouse, "accepted": list(accepted),
            "outgoing": list(outgoing), "manual_slot_count": slots}


def player_buildings() -> list[dict]:
    b = []
    b.append(_building(B_HQ, "SIEGE ACME", "hq", ["hq"], region=R_VAL, city=CITY_VAL, paid=0.0, logistics=None))
    b.append(_building(B_GW1, "PUITS DE GAZ 1", "gatherer", ["gatherer"], paid=180000.0, recipe="Gas", cycle=10.0,
                       production=_prod(last_month=96), modules={"count": 2, "max": 3, "module_prefab": "GasPump", "delivered_to_hub": False,
                                                                 "items": [_module("GasPump@21,31", "GasPump", speed=1.0),
                                                                           _module("GasPump@19,29", "GasPump", speed=0.8)]},
                       inventory=[_inv("Gas", "output", 12)], logistics=_logi(outgoing=["Gas"])))
    b.append(_building(B_GW2, "PUITS DE GAZ 2", "gatherer", ["gatherer"], paid=200000.0, recipe="Gas", cycle=10.0,
                       production=_prod(last_month=150), modules={"count": 3, "max": 3, "module_prefab": "GasPump", "delivered_to_hub": False,
                                                                  "items": [_module("GasPump@25,37", "GasPump", speed=1.0),
                                                                            _module("GasPump@23,35", "GasPump", speed=1.0),
                                                                            _module("GasPump@26,34", "GasPump", speed=None, depleted=1)]},
                       inventory=[_inv("Gas", "output", 30)], logistics=_logi(outgoing=["Gas"])))
    b.append(_building(B_GW3, "PUITS DE GAZ 3", "gatherer", ["gatherer"], region=R_NOR, paid=120000.0, recipe="Gas", cycle=10.0,
                       flags=_flags(enabled=False, working=False), production=_prod(this_month=0, last_month=0),
                       modules={"count": 0, "max": 3, "module_prefab": "GasPump", "delivered_to_hub": False, "items": []},
                       inventory=[_inv("Gas", "output", 0)], logistics=_logi(outgoing=["Gas"], auto_wh=True, warehouse=B_WH)))
    b.append(_building(B_PC1, "USINE PÉTROCHIMIQUE 1", "factory", ["factory"], paid=400000.0, recipe="Chemicals", cycle=20.0,
                       production=_prod(last_month=3), inventory=[_inv("Gas", "input", 9, incoming=4, cap=8),
                                                                  _inv("Chemicals", "output", 10)],
                       logistics=_logi(accepted=["Gas"], outgoing=["Chemicals"])))
    b.append(_building(B_PC2, "USINE PÉTROCHIMIQUE 2", "factory", ["factory"], region=R_NOR, paid=400000.0, recipe="Chemicals", cycle=20.0,
                       flags=_flags(working=False), production=_prod(this_month=0, last_month=0, spent=100),
                       inventory=[_inv("Gas", "input", 1), _inv("Chemicals", "output", 0)],
                       logistics=_logi(accepted=["Gas"], outgoing=["Chemicals"])))
    b.append(_building(B_FF, "FERME FLORALE 1", "farm", ["farm"], paid=105000.0, recipe="Flowers", cycle=30.0,
                       production=_prod(last_month=9), modules={"count": 3, "max": 3, "module_prefab": "FlowerField", "delivered_to_hub": False,
                                                                "items": [_module(f"FlowerField@{70 + i},{22 + i}", "FlowerField", speed=None,
                                                                                  efficiency=1.0, resource=None, nodes=None, remaining=None)
                                                                          for i in range(3)]},
                       inventory=[_inv("Flowers", "output", 20, slots=100)], logistics=_logi(outgoing=["Flowers"])))
    b.append(_building(B_WS, "SIPHON 1", "gatherer", ["gatherer"], paid=90000.0, recipe="Water", cycle=10.0,
                       production=_prod(last_month=12), modules={"count": 1, "max": 3, "module_prefab": "WaterPump", "delivered_to_hub": False,
                                                                 "items": [_module("WaterPump@73,27", "WaterPump", resource="Water")]},
                       inventory=[_inv("Water", "output", 8)], logistics=_logi(outgoing=["Water"])))
    b.append(_building(B_CP, "USINE CHIMIQUE 1", "factory", ["factory"], paid=300000.0, recipe="Dye", cycle=20.0, polluted=True,
                       production=_prod(last_month=3), inventory=[_inv("Flowers", "input", 6), _inv("Water", "input", 4),
                                                                  _inv("Dye", "output", 8)],
                       logistics=_logi(accepted=["Flowers", "Water"], outgoing=["Dye"])))
    b.append(_building(B_PF1, "USINE DE PEINTURE 1", "factory", ["factory"], region=R_VAL, city=CITY_VAL, paid=500000.0,
                       recipe="Paints", cycle=35.0, production=_prod(last_month=2),
                       inventory=[_inv("Chemicals", "input", 5, cap=10), _inv("Dye", "input", 6), _inv("Paint", "output", 38)],
                       logistics=_logi(accepted=["Chemicals", "Dye"], outgoing=["Paint"])))
    b.append(_building(B_PF2, "USINE DE PEINTURE 2", "factory", ["factory"], region=R_VAL, city=CITY_VAL, paid=500000.0,
                       flags=_flags(working=False), production=_prod(this_month=0, last_month=0, total=0, avg=None, frames=0, spent=0),
                       inventory=[], logistics=_logi()))
    b.append(_building(B_WH, "ENTREPÔT 1", "warehouse", ["warehouse"], region=R_VAL, city=CITY_VAL, paid=150000.0,
                       inventory=[_inv("Paint", "accepted", 15, slots=100, incoming=5, cap=20), _inv("Chemicals", "accepted", 0, slots=100)],
                       logistics=_logi(accepted=["Paint", "Chemicals"], outgoing=["Paint", "Chemicals"], slots=9)))
    b.append(_building(B_TD, "DÉPÔT DE CAMIONS 1", "depot", ["depot"], paid=100000.0,
                       fleet={"vehicle_prefab": "Vehicle", "active": 3, "inactive": 1, "max": 5, "infinite": False}))
    return b


def ai_buildings() -> list[dict]:
    def row(key, display, owner, kind, region, city=None, recipe=None, produced=None, cycle=None, working=True, modules=None):
        prefab, rest = key.split("@", 1)
        x, y = (int(v) for v in rest.split(","))
        return {"key": key, "prefab": prefab, "display_name": display, "owner_actor_id": owner, "x": x, "y": y,
                "region_id": region, "city_id": city, "kind": kind, "tags": [kind], "flags": _flags(working=working),
                "recipe": recipe, "produced_last_month": produced, "cycle_days_effective": cycle, "module_count": modules,
                "is_module": False}
    return [
        row("Headquarters@140,110", "SIEGE BOREALIS", AI_B, "hq", R_BRI, CITY_BRI),
        row(AI_PF, "USINE DE PEINTURE 1", AI_B, "factory", R_BRI, CITY_BRI, "Paints", 4, 35.0),
        row("GasWell@160,118", "PUITS DE GAZ 1", AI_B, "gatherer", R_BRI, None, "Gas", 120, 10.0, modules=3),
        row("PetrochemicalFactory@170,125", "USINE PÉTROCHIMIQUE 1", AI_B, "factory", R_BRI, None, "Chemicals", 6, 20.0),
        row("Headquarters@240,50", "SIEGE COBALT", AI_C, "hq", R_SEL, CITY_SEL),
        row("FlowerFarm@250,60", "FERME FLORALE 1", AI_C, "farm", R_SEL, None, "Flowers", 9, 30.0, modules=3),
        row("ChemicalPlant@255,70", "USINE CHIMIQUE 1", AI_C, "factory", R_SEL, None, "Dye", 0, 20.0, working=False),
        row("TradingPost@5,5", "COMPTOIR D'ETAT", STATE_ACTOR, "other", R_NOR),
    ]


def _max_send(value, mode="manual", stock=0, incoming=0):
    unlimited = value == 0 and mode == "manual"
    return {"value": value, "unlimited": unlimited, "mode": mode, "scope": "destination_product_shared",
            "headroom_now": None if unlimited else max(value - (stock + incoming), 0), "ui_label_validated": False,
            "storage_kind": "product_specific"}


def _min_keep(value):
    keep_all = value >= INT_MAX
    return {"value": value, "keep_all": keep_all, "ui_label_validated": False}


def _dispatch(cap, origin_stock, min_keep, free, max_send_value, dest_slots, max_send_room, wait_full=False, extra_limits=()):
    available = None if origin_stock is None else max(origin_stock - (0 if min_keep >= INT_MAX else min_keep), 0)
    if min_keep >= INT_MAX:
        available = 0
    eff_free = free
    limits = []
    if max_send_value and max_send_room is not None and free is not None:
        eff_free = min(free, max_send_room)
    candidates = {"cap": cap, "available": available, "free_space": eff_free}
    vals = [v for v in candidates.values() if v is not None]
    amount = min(vals) if vals else None
    if amount is not None:
        for name, v in candidates.items():
            if v == amount:
                if name == "free_space" and max_send_value and max_send_room is not None and max_send_room <= (free or 0):
                    limits.append("max_send")
                else:
                    limits.append(name)
    if wait_full and amount is not None and cap is not None and amount < cap:
        amount = 0
        limits = ["wait_full"]
    limits.extend(extra_limits)
    return {"value": amount, "complete": "world_event_unevaluated" not in limits, "limited_by": limits,
            "inputs": {"vehicle_capacity": cap, "origin_stock": origin_stock, "min_keep": min_keep, "available": available,
                       "free_space": free, "max_send": max_send_value, "destination_slots": dest_slots,
                       "max_send_room": max_send_room, "contract_room": None, "world_event_targets_destination": False,
                       "wait_for_full_vehicle": wait_full},
            "method": "replica:ManualDestinationManager.GetRequestedAmount"}


def _route(origin, dest, product, *, occurrence=0, source="own", mode="Road", kind="factory", dest_owner=PLAYER,
           dest_city=None, max_send=0, max_mode="manual", min_keep=0, distance=20, cap=10, origin_stock=10,
           dest_stock=0, incoming=0, dest_slots=40, paused=False, wait_full=False, dormant=False, formula="ManualDestinationDispatchCost",
           formula_base=(250, 10), validation_error=None, errors=(), slot_index=0, in_flight=None):
    key = f"{origin}|{product}|{dest}|{source}|{occurrence}"
    free = dest_slots - dest_stock - incoming
    room = None if (max_send == 0 and max_mode == "manual") else max(max_send - (dest_stock + incoming), 0)
    ms = _max_send(max_send, max_mode, dest_stock, incoming)
    if max_mode == "auto_shop_demand":
        ms["storage_kind"] = None
    cost = None if distance is None else round((formula_base[0] + distance * formula_base[1]) * DISPATCH_DIFFICULTY * 1.0, 2)
    return {
        "route_key": key, "origin": origin, "destination": dest, "endpoint": dest, "destination_kind": kind,
        "destination_owner_actor_id": dest_owner, "destination_city_id": dest_city, "product": product, "source": source,
        "transport_mode": mode, "slot_index": slot_index, "occurrence": occurrence, "paused": paused,
        "wait_for_full_vehicle": wait_full, "dormant_auto_warehouse": dormant, "max_send": ms, "min_keep": _min_keep(min_keep),
        "distance_tiles": distance, "path_status": "cached" if distance is not None else "unavailable",
        "dispatch_cost": cost, "dispatch_formula": formula, "vehicle_capacity": cap,
        "dispatch_amount_now": _dispatch(cap, origin_stock, min_keep, free, ms["value"] if not ms["unlimited"] else 0,
                                         dest_slots, room, wait_full),
        "in_flight": in_flight or {"requests_total": 0, "requests_new": 0, "requests_started": 0, "requests_other": 0,
                                   "units_requested": 0, "units_started": 0, "invalid_handles": 0},
        "destination_stock": dest_stock, "destination_incoming_reserved": incoming, "destination_free_space": free,
        "destination_slots": dest_slots, "origin_stock": origin_stock, "validation_error": validation_error,
        "has_error": validation_error is not None, "destination_accepts_product": True, "destination_dead_city": False,
        "errors": list(errors),
    }


def player_routes() -> list[dict]:
    started = {"requests_total": 1, "requests_new": 0, "requests_started": 1, "requests_other": 0,
               "units_requested": 4, "units_started": 4, "invalid_handles": 0}
    return [
        _route(B_GW1, B_PC1, "Gas", max_send=8, min_keep=1, distance=22, origin_stock=12, dest_stock=9, incoming=4, in_flight=started),
        _route(B_GW2, B_PC1, "Gas", max_send=8, min_keep=0, distance=18, origin_stock=30, dest_stock=9, incoming=4),
        _route(B_GW2, B_PC2, "Gas", max_send=0, min_keep=INT_MAX, distance=26, origin_stock=30, dest_stock=1, slot_index=1),
        _route(B_PC1, B_PF1, "Chemicals", max_send=10, min_keep=2, distance=17, origin_stock=10, dest_stock=5, dest_city=CITY_VAL),
        _route(B_PC1, B_PF1, "Chemicals", occurrence=1, max_send=10, min_keep=2, distance=17, origin_stock=10, dest_stock=5,
               paused=True, slot_index=1, dest_city=CITY_VAL),
        _route(B_CP, B_PF1, "Dye", distance=18, origin_stock=8, dest_stock=6, dest_city=CITY_VAL),
        _route(B_FF, B_CP, "Flowers", distance=11, origin_stock=20, dest_stock=6),
        _route(B_WS, B_CP, "Water", distance=13, origin_stock=8, dest_stock=4),
        _route(B_PF1, S_HW1, "Paint", kind="shop", dest_owner=CITY_VAL, dest_city=CITY_VAL, max_send=10, max_mode="auto_shop_demand",
               distance=75, origin_stock=38, dest_stock=4, incoming=2),
        _route(B_PF1, B_WH, "Paint", kind="warehouse", dest_city=CITY_VAL, max_send=20, distance=5, origin_stock=38, dest_stock=15,
               incoming=5, dest_slots=100, wait_full=True, slot_index=1),
        _route(B_PF1, S_CS1, "Paint", kind="shop", dest_owner=CITY_VAL, dest_city=CITY_VAL, distance=None, origin_stock=38,
               dest_stock=0, validation_error="NoPath", errors=["no_path", "validation_error:NoPath"], slot_index=2),
        _route(B_GW3, B_PC1, "Gas", max_send=8, distance=48, origin_stock=0, dest_stock=9, incoming=4, dormant=True),
        _route(B_PC1, B_WH, "Chemicals", kind="warehouse", source="TrainTerminal", mode="Rail", dest_city=CITY_VAL, distance=40,
               cap=40, origin_stock=10, dest_stock=0, dest_slots=100, formula="TrainTerminalDispatchCost", formula_base=(2250, 25),
               slot_index=2),
    ]


def warehouse_requests() -> list[dict]:
    def req(product, occurrence, amount, fill, remaining, moving, priority):
        return {"request_key": f"{B_WH}|{product}|{occurrence}", "endpoint": B_WH, "product": product,
                "occurrence": occurrence, "requested_amount": amount, "fill": fill, "remaining": remaining,
                "amount_being_moved": moving, "priority": priority, "active": True, "use_full_vehicles": False,
                "fulfilled": False, "allowed_graphs": ["TruckDepot"], "expenses_this_month": 1200.0,
                "expenses_last_month": 3400.0, "endpoint_pull_disabled": False}
    return [req("Paint", 0, None, True, 85, 5, 1), req("Chemicals", 0, 50, False, 50, 0, 2)]


def shops() -> list[dict]:
    def sp(product, stock, delivered, raw, player, price, sold, slots=40):
        return {"product": product, "stock": stock, "slots": slots, "player_delivered_stock": delivered, "demand_raw": raw,
                "demand_for_player": player, "price_for_player": price, "shop_modifier": 1.05, "market_modifier": 0.98,
                "price_modifier_pct": 3.0, "sold_last_30d": sold}
    return [
        {"building": S_HW1, "display_name": "QUINCAILLERIE", "prefab": "HardwareStore", "city_id": CITY_VAL, "owner_actor_id": CITY_VAL,
         "is_dead": False, "days_to_next_price_update": 4, "products": [sp("Paint", 4, 3, 12, 10, 1450.5, 22), sp("Chemicals", 0, 0, 6, 5, 610.0, 3)]},
        {"building": S_CS1, "display_name": "MAGASIN DE CONSTRUCTION", "prefab": "ConstructionStore", "city_id": CITY_VAL,
         "owner_actor_id": CITY_VAL, "is_dead": False, "days_to_next_price_update": 4, "products": [sp("Paint", 0, 0, 9, 8, 1460.0, 0)]},
        {"building": S_HW2, "display_name": "QUINCAILLERIE", "prefab": "HardwareStore", "city_id": CITY_BRI, "owner_actor_id": CITY_BRI,
         "is_dead": False, "days_to_next_price_update": 9, "products": [sp("Paint", 2, 0, 7, 6, 1500.0, 5)]},
        {"building": S_GS, "display_name": "ÉPICERIE GÉNÉRALE", "prefab": "GeneralStore", "city_id": CITY_SEL, "owner_actor_id": CITY_SEL,
         "is_dead": False, "days_to_next_price_update": 1, "products": [sp("Paint", 1, 0, 5, 5, 1520.0, 1)]},
    ]


def cities() -> list[dict]:
    def city(cid, name, region, tier, tier_id, nxt, pop, limit, growth, interval, shops_, cx, cy, reached=False, offer=None):
        return {"city_id": cid, "name": name, "region_id": region, "type": "Settlement", "tier": tier, "tier_id": tier_id,
                "next_tier": nxt, "population": pop, "population_limit": limit, "population_limit_reached": reached,
                "growth": growth, "dead": False,
                "advancement": {"can_advance": False, "can_accept": True, "is_advancing": False, "contracts": []},
                "contract_offer": offer, "consumption_interval_days": interval, "house_count": 40 + tier_id * 20,
                "shops": shops_, "center_x": cx, "center_y": cy}
    offer = {"product": "Paint", "amount": 200, "delivered": 0, "reserved": 0, "issuer_actor_id": CITY_BRI, "issuer_name": "Brindlewick",
             "target_building": S_HW2, "accepted_actor_id": None, "active": False, "completed": False, "price": 1600.0,
             "reward": 50000.0, "penalty": 10000.0, "remaining_days": 30, "duration_days": 120, "fulfilled": False, "failed": False}
    return [
        city(CITY_VAL, "Valmont", R_VAL, "Town", 2, "City", 52000, 80000,
             {"waiting_for_sponsor": False, "prospering": False, "growing": True, "consumed_products": 120, "growth_threshold": 100,
              "prosperity_threshold": 300, "state": "Growing"}, 30, [S_HW1, S_CS1], 102, 101),
        city(CITY_BRI, "Brindlewick", R_BRI, "Village", 1, "Town", 15000, 20000,
             {"waiting_for_sponsor": False, "prospering": None, "growing": None, "consumed_products": 50, "growth_threshold": 100,
              "prosperity_threshold": 300, "state": None}, 15, [S_HW2], 201, 151, offer=offer),
        city(CITY_SEL, "Saint-Éloi", R_SEL, "Village", 1, "Town", 8000, 20000,
             {"waiting_for_sponsor": True, "prospering": None, "growing": None, "consumed_products": None, "growth_threshold": None,
              "prosperity_threshold": None, "state": None}, 10, [S_GS], 301, 41),
    ]


def regions() -> list[dict]:
    def reg(rid, name, cx, cy, tiles, city, resources, permit, cost, sites=()):
        return {"region_id": rid, "name": name, "center_x": cx, "center_y": cy, "tile_count": tiles, "city_id": city,
                "resources": resources, "resource_sites": list(sites), "permit": permit, "permit_cost": cost,
                "permit_auction_cooldown": 0, "permit_purchase_cooldown": 0}
    water = {"product": "Water", "tiles": None, "water_unlimited": True}
    return [
        reg(R_VAL, "Valmont", 100, 100, 1500, CITY_VAL, [water], {"owner_actor_id": PLAYER, "amount_paid": 1500000.0, "type": "RegionFull"}, 1500000),
        reg(R_BRI, "Brindlewick", 200, 150, 1400, CITY_BRI, [{"product": "Gas", "tiles": 30, "water_unlimited": False}],
            {"owner_actor_id": AI_B, "amount_paid": 1400000.0, "type": "RegionFull"}, 1400000),
        reg(R_SEL, "Saint-Éloi", 300, 40, 1200, CITY_SEL, [], {"owner_actor_id": AI_C, "amount_paid": 1200000.0, "type": "RegionFull"}, 1200000),
        reg(R_GRE, "Greyhollow", 30, 30, 1600, None, [{"product": "Gas", "tiles": 48, "water_unlimited": False}, water],
            {"owner_actor_id": PLAYER, "amount_paid": 1200000.0, "type": "RegionFull"}, 1200000,
            sites=[{"product": "Gas", "center_x": 22, "center_y": 32, "radius": 6.0, "amount": 40000, "nodes": 12}]),
        reg(R_NOR, "Northmarch", 80, 70, 1600, None, [{"product": "Gas", "tiles": 12, "water_unlimited": False}],
            {"owner_actor_id": None, "amount_paid": 0.0, "type": "RegionFull"}, None),
    ]


def companies(paid_total: float) -> list[dict]:
    player = {
        "actor_id": PLAYER, "name": "Acme Industries", "kind": "human", "is_player": True, "color": "#3366CC",
        "hq_building": B_HQ, "hq_city_id": CITY_VAL, "cash": {"value": 2500000.0, "infinite": False, "registered": True},
        "loans": [
            {"type": "STARTER", "title": "Starter Loan", "lender_actor_id": STATE_ACTOR, "lender_name": "State", "principal": 7500000.0,
             "apr": 0.0, "duration_months": 120, "remaining_payments": 100, "amount_with_apr": 7500000.0, "early_repay_amount": 6250000.0,
             "grace_months_left": 0, "settlement_loan": False},
            {"type": "BANK", "title": "Bank Loan", "lender_actor_id": STATE_ACTOR, "lender_name": "State", "principal": 1000000.0,
             "apr": 0.08, "duration_months": 60, "remaining_payments": 50, "amount_with_apr": 1080000.0, "early_repay_amount": 900000.0,
             "grace_months_left": None, "settlement_loan": False},
        ],
        "max_loans": 3,
        "shares": {"bundle_count": 10, "bundle_size": 0.1, "bundle_owners": [PLAYER] * 9 + [AI_B], "owned_by_competitors": 1},
        "stats": {"cashflow": "Positive", "top_production": [{"product": "Gas", "amount": 246.0}, {"product": "Paint", "amount": 2.0}],
                  "top_sales": [{"product": "Paint", "amount": 22.0}], "owned_permits": [R_VAL, R_GRE], "main_tech_tree": "Chemistry"},
        "building_counts_by_tag": {"gatherer": 4, "factory": 5, "farm": 1, "warehouse": 1, "depot": 1, "hq": 1},
        "building_counts_by_type": {"GasWell": 3, "PetrochemicalFactory": 2, "FlowerFarm": 1, "WaterSiphon": 1, "ChemicalPlant": 1,
                                    "PaintFactory": 2, "Warehouse": 1, "TruckDepot": 1, "Headquarters": 1},
        "building_count": 13, "paid_to_build_total": paid_total,
        "value_inputs": [{"region_id": R_VAL, "permit_cost": 1500000, "paid_to_build_in_region": 1150000.0},
                         {"region_id": R_GRE, "permit_cost": 1200000, "paid_to_build_in_region": 1365000.0}],
        "ai": None,
        "contracts": {"max_contracts": 3, "can_accept": True, "active": [
            {"product": "Paint", "amount": 100, "delivered": 40, "reserved": 5, "issuer_actor_id": CITY_VAL, "issuer_name": "Valmont",
             "target_building": S_HW1, "accepted_actor_id": PLAYER, "active": True, "completed": False, "price": 1550.0,
             "reward": 30000.0, "penalty": 8000.0, "remaining_days": 45, "duration_days": 90, "fulfilled": False, "failed": False}]},
    }

    def ai(actor, name, color, hq, city, regions_, personality):
        return {"actor_id": actor, "name": name, "kind": "ai", "is_player": False, "color": color, "hq_building": hq, "hq_city_id": city,
                "cash": {"value": None, "infinite": True, "registered": True}, "loans": None, "max_loans": None,
                "shares": {"bundle_count": 10, "bundle_size": 0.1, "bundle_owners": [actor] * 10, "owned_by_competitors": 0},
                "stats": {"cashflow": "Neutral", "top_production": [{"product": "Paint", "amount": 4.0}], "top_sales": [],
                          "owned_permits": list(regions_), "main_tech_tree": "Chemistry"},
                "building_counts_by_tag": {"factory": 2, "hq": 1}, "building_counts_by_type": {"PaintFactory": 1, "Headquarters": 1},
                "building_count": 4, "paid_to_build_total": 900000.0, "value_inputs": None,
                "ai": {"personality": personality, "owned_regions": list(regions_), "has_initiative": True}, "contracts": None}
    return [player, ai(AI_B, "Borealis Corp", "#CC3333", "Headquarters@140,110", CITY_BRI, [R_BRI], "Aggressive"),
            ai(AI_C, "Cobalt Works", "#33AA55", "Headquarters@240,50", CITY_SEL, [R_SEL], "Balanced")]


def market() -> dict:
    def price(p, value, pr, mod, trend, final):
        return {"product": p, "value": value, "price": pr, "modifier": mod, "trend": trend, "final_price_for_player": final}
    return {
        "prices": [price("Gas", 90.0, 95.0, 0.05, "GOING_UP", 96.0), price("Water", 40.0, 40.0, 0.0, "STABLE", 40.0),
                   price("Flowers", 120.0, 115.0, -0.04, "GOING_DOWN", 114.0), price("Chemicals", 600.0, 610.0, 0.02, "STABLE", 612.0),
                   price("Dye", 700.0, 690.0, -0.01, "STABLE", 688.0), price("Paint", 1400.0, 1450.0, 0.03, "GOING_UP", 1455.0)],
        "state": {"sold": [{"product": "Water", "price_for_player": 55.0}, {"product": "Gas", "price_for_player": 130.0}],
                  "incoming_trade_allowed": True, "sale_markup": 1.4, "trading_handlers": 1},
        "city_contract_offers": [cities()[1]["contract_offer"]],
        "auctions": {"current": {"definition": "RegionAuction", "title": "Northmarch permit", "reward_kind": "region", "remaining_days": 12,
                                 "duration_days": 30, "start_bid": 500000.0, "highest_bid": 650000.0, "highest_bidder_actor_id": AI_B,
                                 "next_bid": 700000.0, "bid_count": 3, "region_id": R_NOR, "contract_product": None},
                     "queue": []},
    }


def research() -> dict:
    return {
        "player": {"active": "AdvancedPaints", "queue": ["Railways"], "active_progress": 0.4,
                   "progress": [{"unlock": "AdvancedPaints", "progress": 0.4}],
                   "remaining_days": 324.0, "remaining_cost": 1080000.0, "current_cost": 3333.33, "efficiency_index": 2,
                   "efficiency": 1.0, "unlock_points": 3,
                   "unlocked": ["BasicGas", "BasicFarming", "Petrochemistry", "Dyes", "Paints"],
                   "costs": [{"unlock": "AdvancedPaints", "daily_cost": 3333.33, "days": 1680.0, "total_cost_at_efficiency_1": 5600000.0},
                             {"unlock": "Railways", "daily_cost": 3333.33, "days": 540.0, "total_cost_at_efficiency_1": 1800000.0},
                             {"unlock": "Plastics", "daily_cost": 3333.33, "days": 1680.0, "total_cost_at_efficiency_1": 5600000.0},
                             {"unlock": "PaintDiscount", "daily_cost": 3333.33, "days": 540.0, "total_cost_at_efficiency_1": 1800000.0}],
                   # Player price table (TechTreeAgent._buildingPrices); PaintFactory discounted by a price unlock.
                   "building_costs": [{"building_type": "GasWell", "cost": 80000.0},
                                      {"building_type": "PaintFactory", "cost": 180000.0}]},
        "ai": [{"actor_id": AI_B, "unlocked_count": 3, "unlocked": ["BasicGas", "Petrochemistry", "Paints"]},
               {"actor_id": AI_C, "unlocked_count": 2, "unlocked": ["BasicFarming", "Dyes"]}],
    }


def vehicles(world_session_unused: str = WORLD_SESSION) -> dict:
    def veh(iid, trip, prefab, mode, fleet, product, amount, origin, dest, home=False, tx=30, ty=33):
        return {"instance_id": iid, "trip_counter_id": trip, "prefab": prefab, "transport_mode": mode, "fleet_building": fleet,
                "product": product, "amount": amount, "job_origin": origin, "job_destination": dest,
                "job_product": product, "job_amount": amount if product else None, "going_home": home,
                "position_x": float(tx) + 0.5, "position_z": float(ty) + 0.5, "tile_x": tx, "tile_y": ty}
    return {
        "groups": [{"owner_actor_id": PLAYER, "transport_mode": "Road", "product": "Gas", "vehicles": 2, "units_in_transit": 8},
                   {"owner_actor_id": PLAYER, "transport_mode": "Road", "product": "Paint", "vehicles": 1, "units_in_transit": 10},
                   {"owner_actor_id": PLAYER, "transport_mode": "Road", "product": None, "vehicles": 1, "units_in_transit": 0},
                   {"owner_actor_id": AI_B, "transport_mode": "Road", "product": "Paint", "vehicles": 2, "units_in_transit": 14}],
        "fleets_player": [{"building": B_TD, "vehicle_prefab": "Vehicle", "transport_mode": "Road", "active": 3, "inactive": 1, "max": 5,
                           "infinite": False}],
        "vehicles_player": [
            veh(-1201, 501, "Vehicle", "Road", B_TD, "Gas", 4, B_GW1, B_PC1, tx=30, ty=31),
            veh(-1202, 502, "Vehicle", "Road", B_TD, "Gas", 4, B_GW2, B_PC1, tx=33, ty=34),
            veh(-1203, 503, "Vehicle", "Road", B_TD, "Paint", 10, B_PF1, S_HW1, tx=70, ty=60),
            veh(-1204, 504, "Vehicle", "Road", B_TD, None, 0, None, None, home=True, tx=31, ty=35),
        ],
        "total_active": 6,
        "identity": "session_pooled_object",
    }


def session(game_date="Y5-03-12", game_day=1512) -> dict:
    return {"game_date": game_date, "game_day": game_day, "year": 5, "month": 3, "day": 12, "speed_level": 1, "time_scale": 1.0,
            "paused": False, "speed_levels": [1.0, 3.0, 6.0, 10.0], "seconds_per_day": 8.0,
            "difficulty": {"name": "Normal", "prices": 1.0, "upkeep": 1.0, "dispatch": DISPATCH_DIFFICULTY, "loan": 1.0,
                           "score_modifier": 1.0, "easy_chains": False, "infinite_money": False},
            "world": {"size": 512, "ai_count": 2, "tech_tree": True, "seed": 12345},
            "module": {"id": "base", "name": "Base Game"}, "language": "French", "mods": [], "player_actor_id": PLAYER,
            "active_actor_differs": False, "used_cheats": False, "achievements_enabled": True, "logistic_requests_enabled": True,
            "tech_tree_enabled": True, "permit_management_enabled": True, "market_update_interval_days": 15,
            "market_days_since_update": 6}


def state_data() -> dict:
    pb = player_buildings()
    paid_total = sum(b["paid_to_build"] for b in pb)
    return {
        "session": session(),
        "companies": companies(paid_total),
        "buildings_player": pb,
        "routes_player": player_routes(),
        "requests_player": warehouse_requests(),
        "buildings_ai": ai_buildings(),
        "buildings_ai_detail": None,
        "routes_ai": None,
        "shops": shops(),
        "cities": cities(),
        "regions": regions(),
        "market": market(),
        "research": research(),
        "vehicles": vehicles(),
        "route_paths": None,
    }


OPTIONAL_OFF = ("buildings_ai_detail", "routes_ai", "route_paths")


def state_sections(data: dict, game_day: int = 1512) -> dict:
    out = {}
    for k, v in data.items():
        if k in OPTIONAL_OFF and v is None:
            out[k] = section_status(game_day, 0, "skipped", "optional_off")
        else:
            out[k] = section_status(game_day, len(v) if isinstance(v, list) else 1)
    return out


def build_state(now: datetime = BASE_TIME, seq: int = 10, static_doc: dict | None = None, world_session: str = WORLD_SESSION,
                pid: int = PID, data: dict | None = None, capture_age_s: float = 2.0, consistent: bool = True) -> dict:
    static_doc = static_doc or build_static(now, world_session=world_session, pid=pid)
    data = data if data is not None else state_data()
    return envelope("state", data, seq=seq, now=now, world_session=world_session, pid=pid, static_ref=static_ref_of(static_doc),
                    sections=state_sections(data), capture_age_s=capture_age_s, consistent=consistent)


# ============================================================================ history

MONTHS = ["Y4-10", "Y4-11", "Y4-12", "Y5-01", "Y5-02", "Y5-03"]


def history_data() -> dict:
    months = []
    for i, m in enumerate(MONTHS):
        mtd = m == MONTHS[-1]
        scale = 0.4 if mtd else 1.0
        cats = [
            {"category": "ProductTrade", "income": round((400000 + 25000 * i) * scale, 2), "expense": round(20000 * scale, 2)},
            {"category": "Upkeep", "income": 0.0, "expense": round((90000 + 5000 * i) * scale, 2)},
            {"category": "RouteVehicleUpkeep", "income": 0.0, "expense": round((30000 + 1000 * i) * scale, 2)},
            {"category": "ResearchCosts", "income": 0.0, "expense": round(100000 * scale, 2)},
            {"category": "Loan Payments", "income": 0.0, "expense": round(80000 * scale, 2)},
        ]
        if m == "Y5-01":
            cats.append({"category": "BuildingConstruction", "income": 0.0, "expense": 500000.0})
        months.append({"month": m, "current_month_to_date": mtd,
                       "income_total": round(sum(c["income"] for c in cats), 2),
                       "expense_total": round(sum(c["expense"] for c in cats), 2), "categories": cats})
    ledger = {"actor_id": PLAYER, "current_month": MONTHS[-1], "months": months, "first_month_available": MONTHS[0],
              "last_month": MONTHS[-1], "retention_years": 3, "window_months": len(MONTHS), "history_truncated": False,
              "balance_now": 2500000.0, "balance_infinite": False}

    def window(truncated, first_available=None):
        # Fixture windows span the sample months; one series per family is truncated (the game holds older data).
        return {"window_months": len(MONTHS), "window_first_month": MONTHS[0], "window_last_month": MONTHS[-1],
                "history_truncated": truncated, "first_month_available": first_available}
    return {
        "ledger_player": ledger,
        "buildings_monthly_player": [
            {"building": B_PF1, "series": [{"item": "production", "aggregation": "sum", **window(False, MONTHS[0]),
                                            "values_retained": len(MONTHS),
                                            "months": [{"month": m, "value": float(2 + i % 2)} for i, m in enumerate(MONTHS)]},
                                           {"item": "efficiency_pct", "aggregation": "average", **window(True, "Y1-04"),
                                            "values_retained": 30 * 1137,
                                            "months": [{"month": m, "value": 100.0} for m in MONTHS]}]},
            {"building": B_PC1, "series": [{"item": "production", "aggregation": "sum", **window(False, MONTHS[0]),
                                            "values_retained": len(MONTHS),
                                            "months": [{"month": m, "value": 3.0} for m in MONTHS]}]},
        ],
        "production_monthly_player": [
            {"building": B_PF1, "product": "Paint", **window(True), "months": [{"month": m, "produced": 2, "consumed": 0} for m in MONTHS]},
            {"building": B_PC1, "product": "Chemicals", **window(False), "months": [{"month": m, "produced": 3, "consumed": 0} for m in MONTHS]},
            {"building": B_PC1, "product": "Gas", **window(False), "months": [{"month": m, "produced": 0, "consumed": 4} for m in MONTHS]},
        ],
        "shops_monthly": [{"shop": S_HW1, "product": "Paint", **window(True), "months": [{"month": m, "sold": 20 + i, "demand": 12} for i, m in enumerate(MONTHS)]}],
        "player_product_stats": [{"product": "Paint", "produced": 12.0, "production_cost": 9000.0, "distribution_cost": 1200.0,
                                  "total_cost": 10200.0, "sold": 10.0, "price_sold": 1450.0, "profit": 4300.0, "markup": 0.42, "used": 0.0}],
        "state_sales": [{"product": "Water", "sold_last_30d": 40, "sold_last_60d": 75}],
    }


def build_history(now: datetime = BASE_TIME, seq: int = 3, static_doc: dict | None = None, world_session: str = WORLD_SESSION,
                  pid: int = PID, data: dict | None = None, capture_age_s: float = 5.0) -> dict:
    static_doc = static_doc or build_static(now, world_session=world_session, pid=pid)
    data = data if data is not None else history_data()
    return envelope("history", data, seq=seq, now=now, world_session=world_session, pid=pid, static_ref=static_ref_of(static_doc),
                    sections={k: section_status(1512, 1) for k in data}, capture_age_s=capture_age_s)


# ============================================================================ heartbeat

def family_status(doc: dict | None, last_verified: datetime | None = None) -> dict:
    if doc is None:
        return {"seq": 0, "last_published_utc": None, "last_verified_utc": None, "size_bytes": 0, "content_hash": None,
                "world_session": None}
    end = doc["captured"]["utc_end"]
    return {"seq": doc["seq"], "last_published_utc": doc["written_utc"],
            "last_verified_utc": iso(last_verified) if last_verified else end,
            "size_bytes": len(json.dumps(doc)), "content_hash": doc["content_hash"], "world_session": doc["world_session"]}


def build_heartbeat(now: datetime = BASE_TIME, *, state: str = "ready", pid: int = PID, world_session: str | None = WORLD_SESSION,
                    seq: int = 100, static_doc: dict | None = None, state_doc: dict | None = None, history_doc: dict | None = None,
                    compatibility: str = "verified", written_age_s: float = 0.5, tick_age_s: float = 0.2,
                    effective_interval_s: float = 5.0, refresh_served: dict | None = None, refresh_seen: dict | None = None,
                    paused: bool = False, degraded: bool = False, disabled_sections: list | None = None,
                    detected_game: dict | None = None, envelope_game: dict | None = "default",
                    schema_version: str = "1.0.0") -> dict:
    written = now - timedelta(seconds=written_age_s)
    in_game = state == "ready"
    ws = world_session if in_game else None
    expected = dict(GAME)
    detected = detected_game if detected_game is not None else (dict(GAME) if compatibility != "pending" else None)
    data = {
        "state": state, "state_reason": None, "paused": paused, "speed_level": 1 if in_game else None,
        "time_scale": 1.0 if in_game else None, "game_date": "Y5-03-12" if in_game else None, "game_day": 1512 if in_game else None,
        "main_thread_last_tick_utc": iso(now - timedelta(seconds=tick_age_s)), "frame_count": 123456,
        "scene": "game" if in_game or state == "loading" else "menu", "world_session": ws, "module_id": "base" if in_game else None,
        "module_name": "Base Game" if in_game else None, "language": "French", "compatibility": compatibility,
        "detected_game": detected, "expected_game": expected,
        "reflection_self_check": {"total": 24, "resolved": 24, "type_mismatch": 0, "missing": 0, "problems": []},
        "families": {"static": family_status(static_doc), "state": family_status(state_doc), "history": family_status(history_doc)},
        "last_capture": {"family": "state", "utc": iso(now - timedelta(seconds=2)), "main_thread_ms": 12.5, "slices": 8,
                         "max_slice_ms": 1.9, "alloc_bytes_approx": 800000, "gc_count_delta": 0, "size_bytes": 250000,
                         "serialize_ms": 30.0} if in_game else None,
        "effective_interval_s": effective_interval_s, "degraded": degraded, "optional_sections_suppressed": False,
        "disabled_sections": disabled_sections or [], "id_collisions": 0,
        "frame_stats": {"window_s": 60.0, "frames": 3600, "frame_ms_p50": 16.6, "frame_ms_p95": 20.1, "frame_ms_p99": 25.0,
                        "frame_ms_avg": 16.9, "frames_over_50ms": 0, "observer_ms_p99": 1.2, "observer_ms_max": 1.9,
                        "observer_ms_max_without_gc": 1.9, "observer_ticks_with_gc": 0},
        "refresh_seen": refresh_seen or {"state": None, "history": None, "static": None},
        "refresh_served": refresh_served or {"state": None, "history": None, "static": None},
        "publish_failures": 0, "errors_last_hour": 0, "log_dropped": 0, "observer_version": "1.0.0",
        "exchange_dir": "C:\\Users\\example\\AppData\\Local\\RoiMcp", "cwd": "C:\\Games\\RiseOfIndustry",
        "kill_switch": state == "disabled", "config_enabled": True, "config_warnings": [], "active_actor_differs": False,
        "save_name": {"value": None, "reason": "no_member_identified"},
        "runtime_constants": {"seconds_per_day": 8.0, "speed_levels": [1.0, 3.0, 6.0, 10.0], "disable_with_mods": False,
                              "is_debug_build": False, "application_version": "2.3.3", "max_days_delta_per_frame": 1,
                              "network_names": ["Road", "Rail"], "world_size": 512, "game_version_string": "Steam - Public - 2.3.3 : 0507b"},
        "written_utc": iso(written),
    }
    return {
        "schema": "roi-mcp/heartbeat", "schema_version": schema_version, "observer_version": "1.0.0",
        "compatibility": compatibility,
        # before the version gate has run (first READY of the process) the observer reports "pending" and game null
        "game": ((None if compatibility == "pending" else dict(GAME)) if envelope_game == "default" else envelope_game),
        "pid": pid, "world_session": ws, "seq": seq, "content_hash": None, "written_utc": iso(written),
        "captured": None, "static_ref": None, "sections": None, "warnings": [], "data": data,
    }


# ============================================================================ writing

def build_world(now: datetime = BASE_TIME, world_session: str = WORLD_SESSION, pid: int = PID) -> dict:
    static_doc = build_static(now, world_session=world_session, pid=pid)
    state_doc = build_state(now, static_doc=static_doc, world_session=world_session, pid=pid)
    history_doc = build_history(now, static_doc=static_doc, world_session=world_session, pid=pid)
    hb = build_heartbeat(now, pid=pid, world_session=world_session, static_doc=static_doc, state_doc=state_doc, history_doc=history_doc)
    return {"heartbeat": hb, "static": static_doc, "state": state_doc, "history": history_doc}


def write_json(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp-build")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_world(directory: Path, world: dict, families=("heartbeat", "static", "state", "history")) -> None:
    for fam in families:
        write_json(Path(directory) / f"{fam}.json", world[fam])


SAMPLE_DIR = Path(__file__).resolve().parent / "sample"


def main(argv=None) -> int:
    out = Path(argv[0]) if argv else SAMPLE_DIR
    write_world(out, copy.deepcopy(build_world()))
    print(f"wrote sample fixtures to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
