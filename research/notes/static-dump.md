# Offline static definition dump (resources.assets)

Tool: `research/tools/asset-inspect/dump_definitions.py` (UnityPy 1.25.4 + TypeTreeGeneratorAPI; typetrees generated from the
**copied** assemblies in `research/_local/managed/`). The game's `resources.assets` and `globalgamemanagers.assets` were
opened read-only. Output: `research/_local/static-dump/<Class>.json` (gitignored; game data).

All values below are **CONFIRMED** asset data for build 2.3.3 / 0507b. They are the *serialized* values; runtime code may
apply further modifiers (difficulty, tech, actor modifiers). Display strings in the dump are the raw (English) asset values;
at runtime the localizer overwrites them in place with the UI language (French here).

Reproduce:

```powershell
research\_local\venv\Scripts\python research\tools\asset-inspect\dump_definitions.py ProductDefinition Recipe TechTreeRecipeUnlock TechTreeBuildingUnlock TechTreeGenericUnlock TechTreeBuildingPriceUnlock TechTree TechTreeManagerConfig ProductCategory MoneyBillCategory Formula SettlementTier SettlementType PermitType LoanInfo
research\_local\venv\Scripts\python research\tools\asset-inspect\dump_definitions.py Building ProductSpecificProductStorage Upkeep BuildingEfficiency ModuleOwner GathererHub Factory Farm JITVehicleFleet ManualDestinationManager Shop Harvester Field BuildingNameGenerator MoneyAgent
```

Implementation note: MonoBehaviour headers are decoded from raw bytes (script PPtr resolved through the file's externals to
`globalgamemanagers.assets`), and only wanted classes are typetree-parsed. Parsing every MonoBehaviour is ~100x slower.

## 1. Counts

| Class | Count | Notes |
|---|---|---|
| ProductDefinition | 226 | Includes the "2130" DLC module content. Price formula refs: Factories 182, Farms 22, Gatherers 15, Livestock 7 |
| Recipe | 225 | e.g. `Chemicals`: Gas ×3 → Chemicals ×2, 20 days; `Paints`: Chemicals ×1 + Dye ×2 → Paint ×2, 35 days; `GasPump`: → Gas ×1, 15 days, requires a harvester module |
| TechTreeRecipeUnlock / BuildingUnlock / GenericUnlock | 224 / 58 / 57 | 339 nodes (the sample save has 243 unlock states for its module) |
| TechTree | 26 | |
| Formula | 30 | see §2 |
| MoneyBillCategory | 24 | see §3 |
| SettlementTier / SettlementType | 12 / 6 | base and "2130" variants |
| LoanInfo | 5 | see §4 |
| Building prefabs (`Building` component) | 171 | factories, gatherers + harvesters, farms + fields, shops, houses, logistics (`WarehouseLogistical`, `TruckDepotLogistical`, `TrainLogistical`, `ZeppelinLogistical`, `ShippingLogistical`, `MagLev Logistical`, `Bulker Dock Logistical`, `Drop Ship Logistical`), town centres, `State`, `Headquarters`, `Wholesaler`, parks, services |

## 2. Formulas (resolves unknown U4)

| Asset name | Expression |
|---|---|
| ManualDestinationDispatchCost | `(250 + distance * 10) * difficulty * actor` |
| TruckDepotDispatchCost | `(350 + distance * 15) * difficulty * actor` |
| TrainTerminalDispatchCost | `(2250 + distance * 25) * difficulty * actor` |
| ZeppelinFieldDispatchCost | `(7500 + distance * 40) * difficulty * actor` |
| BoatDepotDispatchCost | `(4000 + distance * 20) * difficulty * actor` |
| AwhDispatchCost | `(250 + distance * 10) * difficulty * actor` |
| Shop Demand | `shopModifier + ceil(0.05 * ceil(population / 50000) * (30 / timeToProduce) * producedAmount * tierValue * difficulty)` |
| 2130ShopDemandFormula | `shopModifier + ceil(0.035 * ceil(population / 500000) * (30 / timeToProduce) * producedAmount * tierValue * difficulty)` |
| GlobalMarket (price modifier) | `2 * sign(demand - sold - stored) * sqrt(abs(demand - sold - stored))` (then scaled ×0.01 and jittered in code) |
| Factories (product price) | `(ingredientsValue + ((upkeep / 30) * recipeDays)) / recipeOutput` |
| Farms | `((ingredientsValue * 3) + ((upkeep / 30) * recipeDays)) / (recipeOutput * 3)` |
| Gatherers | `upkeep / ((3 * recipeOutput) * (30 / recipeDays))` |
| Livestock | `((((ingredientsValue * 3) + ((upkeep / 30) * recipeDays)) / (recipeOutput * 3)) * (recipeOutput - productOutput)) / recipeOutput` |
| FarmProduce | `ingredientsValue * 2.8` |
| RawResources | `75 * recipeDays * 3.25` |
| ProductPriceUpkeepComponent | `hubUpkeep + (moduleUpkeep * 3)` |
| FactoryRecipeTime | `tier * 15` |
| Research Cost (per day) | `3333.333333 * efficiency` |
| Research Time (days) | `(60 + (tier ^ 3) * 60) / efficiency` |
| Upkeep | `buildingCost * 0.025` (code does not use `Upkeep.monthlyUpkeep`; see §5) |
| LoanAmount / LoanAmount2130 | `ceil(population / 100000) * 5000000` / `ceil(population / 1000000) * 5000000` |
| LoanApr | `max(0.05, 0.01 * (10 + 2.5 * (duration / 12 - 3)))` |
| LoanDuration | `120` |
| Final Score | `(assets / (months ^ 1.5)) * difficulty` |
| ContractSpawnChance | `0.001 + min(failedSpawnCount * 0.0002, 0.05)` |
| Settlement Distance Restriction | `max(abs(x0 - x1), abs(y0 - y1))` |
| Hq Change Visuals Cost / Required Unlocks | `1000000 + (tier - 1) * 1500000` / `20 + (tier - 1) * 10` |
| Pollution Rate Efficiency | `ifmore(efficiency, 1, efficiency ^ 2, 1)` |

Which formula a given building's `DefaultTransportRequestPaymentHandlerBehaviour.formula` references is per prefab (HIGH
CONFIDENCE that manual destinations use `ManualDestinationDispatchCost`). The observer should report the formula name it
actually evaluates.

## 3. Money bill categories (asset name → `categoryName`)

`BuildingBuyout` Building Buyout · `BuildingConstruction` Building construction · `BuildingRefund` Building refund ·
`ChangeHQVisuals` · `CheatCategory` Cheating · `Contracts` · `Demolish` · `FinesAndGrants` Fines/Grants ·
`InfrastructureConstruction` · `Loan Payments` · `LoansOneTimePayments` Loans Taken Or Paid In Full · `PermitPurchase` ·
`ProductTrade` Product trade · `Renting` · `ResearchCosts` R&D Expenses · `ResearchRefund` R&D Refund ·
`RouteVehicleUpkeep` Route Vehicle Upkeep · `StarterLoanBillCategory` Starter Loan · `StocksPurchase` ·
`TerraformingBillCategory` Terraforming · `Upkeep` (categoryName "Renting") · `VehicleUpkeep` Vehicle Upkeep ·
`Vehicles` · `Auctions` (empty name).

Use the **asset name** as the category id; `categoryName` is localized at runtime.

## 4. Loans

| LoanInfo | type | amount | apr | duration (months) | grace months |
|---|---|---|---|---|---|
| Starter Loan Normal | STARTER | 7,500,000 | 0 | 120 | 24 |
| Starter Loan Veteran | STARTER | 5,000,000 | 0 | 120 | 24 |
| Bankruptcy Loan | BANKRUPTCY | 5,000,000 | 0.25 | 60 | 0 |
| Settlement Loan | SETTLEMENT | (formula) | (formula) | (formula) | 0 |
| Fine | FINE | (event) | 0 | 0 | 0 |

## 5. Building component values (prefab data; overrides code defaults)

| Component field | Values found | Code default | Consequence |
|---|---|---|---|
| `Upkeep.buildingCostPercentage` | **0.025** (52 prefabs), 0.0 (2) | 0.25 | Monthly upkeep ≈ **2.5 % of base cost** × modifiers (not 25 %). Notes that quote 0.25 refer to the code default. |
| `Upkeep.minUpkeep` | 0.25 | 0.25 | |
| `BuildingEfficiency.basicEfficiencyModifierValues` | {0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0} (all 48) | same | |
| `BuildingEfficiency.upkeepModifierValues` | **{0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0}** (all 48) | {0, 0.4, 0.6, 0.8, 1, 1.5, 2} | Upkeep scales 1:1 with the efficiency level |
| `BuildingEfficiency.initialEfficiencyIndex` | **3** (= 100 %) | 4 | |
| `ProductSpecificProductStorage.slots` | 40 (33 prefabs), 100 (21), 74 (2), 0, 1 | - | Per-product storage cap |
| `ModuleOwner.maxModuleCount` / `radius` | 3 / 15 (15), 3 / 18 (6), 3 / 23 (2) | 5 / 10 | Max 3 harvesters/fields per hub |
| `GathererHub.productionIsDeliveredToHub` | 0 (14), 1 (9) | - | Per hub prefab |
| `Factory.productionSpeed` | 1.0 (all 21) | - | |
| `ManualDestinationManager._slotCount` / `_infiniteSlots` | 3 / false (45), 9 / false (2) | 3 | Infinite slots come from the owner actor flag |
| `JITVehicleFleet.vehiclePrefab` | `Vehicle` (101), `TrainVehicle` (2) | - | |
| `BuildingNameGenerator.nameFormat` | `"%n %i"` (62), `"%n"` (26) | - | e.g. "GAS WELL 2" |
| `Shop.maxProducts` | 8 (all 21 shop prefabs) | 9 | Shop kinds: Diner, Ironmongery, Advanced Components, Nutrient Center, Showroom (+2130), Robotics, Pharmacy, Fabrication, Furniture (HomeGoods), Bookstore, Clothing, Parts, Liquor, Construction, Assorted Goods, Grocery, Hardware (`demandModifier` 5), Toy, Car, Farmers |
| `MoneyAgent` (actor prefabs) | HumanPlayer: keepsHistoricalData 1, range 3, infiniteMoney 0. **AiPlayer: keepsHistoricalData 1, range 3, `_infiniteMoney` 1.** StateActor: history 0, infinite 1. Settlement(s): history 0, infinite 0 | | **AI companies have infinite money**: `GetBalance` returns +∞ and `EasyMoneySend` skips their balance; the stored balance is not meaningful for AI. Report AI cash as "infinite" |

Product categories (asset names): `RawResources`, `FarmProduce`, `Livestock`, `Components`, `Row1`, `Row2`, `Row3`,
`PrototypeProducts` (105 products have no direct `_category` and use a `categoryProvider`).

## 6. Scene-resident managers (game scene = `level4`)

Dumped with `ROI_ASSET_FILE=level4` (outputs under `research/_local/static-dump/level4/`; the resources.assets outputs from
the first runs sit directly in `static-dump/`). `level3` is the main menu scene, `level2` the intro, `level0` the boot/preload
scene.

| Object | Field | Value | Consequence |
|---|---|---|---|
| TimeManager | `secondsPerDay` | **8.0** | 1 game day = 8 s scaled time. At top speed (10×) a day lasts 0.8 s (~48 frames at 60 fps), so at most one `NewDay` per frame in normal play (resolves U3, HIGH CONFIDENCE; cheats/editor speeds excluded). A 30-day month = 240 s at 1×, 24 s at 10× |
| TimeManager | `seasonStartMonth` | 3 | |
| SpeedControls | `speedLevels` | **[1, 3, 6, 10]** (`editorOnlySuperSpeed` 20) | `Time.timeScale` values per speed level |
| AchievementManager | `disableWithMods` | **0** (game and menu scenes) | Enabling a code mod does **not** disable achievements (resolves part of U2). The save header still records the mod |
| GlobalMarket | `_updateIntervalInDays` | **15** (code default 30) | Market price modifiers update every 15 game days |
| GlobalMarket | `_randomModifierChance` | 0.5 | |
| AutosaveManager | `autosaveSlotId` | 1 | interval comes from PlayerPrefs (default 1800 s real time) |
| MoneyManager | category fields | references to the §3 assets | e.g. `upkeepCategory` → asset `Upkeep` ("Renting") |

## 7. Still not dumped

- Localization (`LanguageData`) for en-US names.
- Per-prefab `DefaultTransportRequestPaymentHandlerBehaviour.formula` mapping and `TechTreeGenericUnlockEffect` actions
  (dump `DefaultTransportRequestPaymentHandlerBehaviour`, `TechTreeGenericUnlockEffect` if needed).
