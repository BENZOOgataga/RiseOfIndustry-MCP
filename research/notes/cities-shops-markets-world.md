# Cities, Shops, Markets, Regions & World — static analysis notes

> **Errata from the asset dump (2026-10-03, CONFIRMED, see `notes/static-dump.md`):**
> - `GlobalMarket._updateIntervalInDays` = **15** in the game scene (code default 30 quoted below).
> - Shop prefabs use `maxProducts` = 8 (code default 9). Shop demand and market formulas are listed in `notes/static-dump.md` §2.
> - The enumeration recipe in §6 uses `Player.activeActor`; use `Player.humanPlayer` instead.

Target: original **Rise of Industry** (Steam 671440, Unity 2018.4.11 Mono, build 9064059).
Source: ILSpy decompile at `research/_local/decompiled/Assembly-CSharp/ProjectAutomata/` (all paths below are relative to that folder unless stated).
Method: static reading only. No game install, process, or `%APPDATA%` access.

Confidence labels:
- **CONFIRMED** = read directly in decompiled source (file:line given).
- **HIGH CONFIDENCE** = strongly implied by source, single small gap.
- **INFERRED** = reasoned from naming/usage, not traced end to end.
- **UNKNOWN** = lives in game data (ScriptableObjects / content-creation JSON / asset bundles) or not found.

---

## 0. TL;DR for the MCP observer

| Need | Entry point | Label |
|---|---|---|
| All cities | `ManagerBehaviour<SettlementManager>.instance.settlements` (`List<SettlementBase>`) — `SettlementManager.cs:23` | CONFIRMED |
| All shops of a city | `settlement.buildings.shops` (`List<Shop>`) — `SettlementBuildingCollection.cs` (field `shops`) | CONFIRMED |
| All regions | `ManagerBehaviour<RegionManager>.instance.regions` (`IEnumerable<Region>`, dict values) — `RegionManager.cs:42` | CONFIRMED |
| Region of a tile | `RegionManager.instance.GetRegionByTile(int tile)` — `RegionManager.cs:74` | CONFIRMED |
| Region/city of a building | `building.region`, `building.settlement` (set at creation) — `Building.cs:61,64,289-290` | CONFIRMED |
| City of a region | `region.settlement` (null for empty regions) — `Region.cs:21`, `hasSettlement` `:49` | CONFIRMED |
| Permit owner of region | `PermitManager.instance.GetPermitOwner(region)` (default = `fullPermit`) — `PermitManager.cs:191` | CONFIRMED (mild side effect, see §8) |
| Global base price / trend | `GlobalMarket.instance.GetPricingInfo(product)` → `ProductPricingInfo` (`value`, `price`, `modifier`, `trend`) — `GlobalMarket.cs:56` | CONFIRMED |
| Shop sell price for actor | `shop.GetPrice(product, actor)` — `Shop.cs:188` | CONFIRMED |
| Shop demand for actor | `shop.GetDemand(product, actor)` — `Shop.cs:234` | CONFIRMED (read-only) |
| Shop stock | `shop.storage.Count(product)`; capacity = `ProductSpecificProductStorage.slots` (with `shop.storage` treated as that type) | CONFIRMED (via `ShopProductViewModel.cs:15,21`) |
| Government buyer/seller ("State") | `State.instance` (an `Actor`), `State.instance.tradingHandlers` | CONFIRMED |
| Auctions | `AuctionsManager.instance.currentAuction`, `.auctionQueue` | CONFIRMED |
| City delivery contract | `settlement.contracts.currentContract` (`DeliveryContract`) | CONFIRMED |
| Player's accepted contracts | `actor.contracts.activeContracts` (`IContractsAgent`) | CONFIRMED |
| Resources in region | `region.availableResources` (`Dictionary<ProductDefinition,int>` tile counts), `region.resourceSites` | CONFIRMED |
| Game date | `ManagerBehaviour<TimeManager>.instance.today` (public field, `GameDate`) — `TimeManager.cs:19` | CONFIRMED |

All of these are Unity main-thread objects. Read them on the main thread only (see §9).

---

## 1. Cities / settlements

### 1.1 Class hierarchy
- `SettlementBase : Actor, IKillable` (abstract) — `SettlementBase.cs:8`. Settlements **are Actors** (have id, money, modifiers, buildings) and are registered in `ActorManager` (`SettlementBase.cs:113-121`). CONFIRMED
- `Settlement : SettlementBase, IInitialBuildParticipant` — the only concrete implementation (`Settlement.cs:10`). CONFIRMED
- No `Town`/`City` classes. "City" is `Settlement`. CONFIRMED (filename search).
- `SettlementManager : ManagerBehaviour<SettlementManager>` — `SettlementManager.cs:9`, holds `List<SettlementBase> settlements` (`:23`), `settlementCount` (`:25`), `productionEnabled` (`:27`), `IsUrbanArea(int tile)` (`:81`). CONFIRMED

### 1.2 Identity / type / tier / population (serialized fields on `SettlementBase`)
Savegame-serialized protected fields (`SettlementBase.cs:15-34`):
- `_config` (`SettlementConfig`, stored as a game-data entity reference)
- `_settlementName` (string)
- `_type` (`SettlementType`, stored as a game-data entity reference)
- `_population` (int)
- `_isDead` (bool)
- `_region` (`Region`)
- `_tier` (`SettlementTier`)

Public getters: `settlementName` (`:38`), `tier` (`:40`, has setter), `type` (`:52`), `config` (`:54`), `population` (`:56`, has setter), `region` (`:68`), `isDead` (`:100`), `id`/`actorName` inherited from `Actor`. CONFIRMED

- **Name**: settlement name is the region name. World gen creates regions with names from `settlementNames.Next()` (`RegionsWGOperation.cs:208`) and settlements are created with `region.regionName` (`SettlementsWGOperation.cs:43-46`). `Settlement.Initialize` also copies `_settlementName` into the actor's `actorName` (`Settlement.cs:212-213`). CONFIRMED
- **Stable id**: `settlement.id` (Actor id, int) — CONFIRMED; region has an `id` of type Guid (`Region.cs:51`). Region Guid is persisted via constructor params (`Region.cs:93-111`). CONFIRMED
- **Type**: `SettlementType` ScriptableObject (`SettlementType.cs`): `settlementTypeName`, `townCenterPrefab`, `initialBuildings`, decorations. Concrete type names (e.g. farming/industrial town) are game data → UNKNOWN.
- **Tier**: `SettlementTier` ScriptableObject (`SettlementTier.cs:10`): `tierName`, `tierId`, `placedShopsCount`, `neededProducts` (`RandomProductAmount[]`), `threshold` (`RangedInt` population threshold), `houseCapacityPerTile`, `efficiency`, `nextTier`/`previousTier`, static `sortedTiers`. CONFIRMED. Actual tier names/thresholds: UNKNOWN (data).
- **Population**: `_population` int. Population capacity = sum of non-dead `ResidentialBuilding.capacity` clamped to `populationLimit` (`SettlementGrowth.cs:118-131`). CONFIRMED

### 1.3 Growth / prosperity state
Component `SettlementGrowth : SettlementBehaviour, ISettlementGrowth` (`SettlementGrowth.cs:7`), accessed as `settlement.growth` (`ISettlementGrowth`).
- Serialized private: `_desiredPopulation`, `_consumedProducts` (products consumed in last consumption tick), `_tierToPopulationLimit` (`Dictionary<int,int>`, tierId → randomized threshold). CONFIRMED (`:9-16`)
- `populationLimit` (`:18`) = threshold of next tier, or current population if top tier.
- `populationLimitReached` (`:30`), `IsGrowing()` is true when `_consumedProducts` is at least `growthThreshold` (from `config.growth`) (`:179`), `IsProspering()` is true when it is at least `prosperityThreshold` (`:184`), `IsWaitingForSponsor()` (`:189`), `GetPopulationCapacity()` (`:118`), `GetTierThreshold(tier)` (`:143`), `GetNeededResidentialBuildings()` (`:198`). CONFIRMED

The UI "prosperity" label is chosen by the first matching check, in this priority order (good template for the observer) — `SettlementUiViewModel.cs:98-125`:
1. waiting for a sponsor (`IsWaitingForSponsor()`) → "Waiting For Sponsor"
2. population limit reached (`populationLimitReached`) → "Bloated"
3. prospering (`IsProspering()`) → "Prospering"
4. growing (`IsGrowing()`) → "Growing"
5. otherwise → "Stagnating"

CONFIRMED (strings are serialized fields with these defaults; localization may override → HIGH CONFIDENCE on text).

Growth config (`SettlementGrowthConfig.cs`, via `settlement.config.growth`): `maxProductsAffectingGrowth` 5, `growthThreshold` 1, `prosperityThreshold` 5, `minGrowthPerConsumedProduct` / `maxGrowthPerConsumedProduct` 250 / 750, `consumeProductsInterval` 15, `maxAllowedPollution` 1. These are C# defaults; real values come from game data → defaults CONFIRMED, live values UNKNOWN.

Consumption cadence: `ShouldConsumeProducts(date)` is true on days whose total day count (`CountDays()`) is an exact multiple of `consumeProductsInterval` (`SettlementGrowthConfig.cs:64-71` in file). Run from `Settlement.DailyTick` on day start (`Settlement.cs:59-82`): consume products in all shops → `growth.TryGrow(consumed)` → `UpdateDemand()` on all shops (TryGrow always returns true, so demand is recomputed every consumption tick) → `needsFulfiller.TryFulfillNeeds()`. CONFIRMED. Skipped while tutorial active.

### 1.4 Advancement (tier-up via sponsor contracts)
`SettlementAdvancement` (`SettlementAdvancement.cs:8`), `settlement.advancement`:
- private enum `State` with values GROW, ACCEPT, DELIVER (`:10-15`). Public projections: `canGrow` (`:403`), `canAdvance` (`:405`, not dead and has next tier), `isAdvancing` (DELIVER) (`:433`), `canAccept` (ACCEPT) (`:435`), `contracts` (`IReadOnlyList<AdvancementContract>`, `:417`), `possibleShops`, `selectedShop`. CONFIRMED
- Advancement starts automatically when consumption can no longer grow pop, limit reached, can advance and the region permit is owned: `SettlementGrowth.TryGrow` `:159-162`. CONFIRMED
- `AdvancementContract.payOutSum`: payout = `payoutMarkup` × amount × the product's GlobalMarket price (`AdvancementContract.cs` `payOutSum`). CONFIRMED
- Config: `SettlementAdvancementConfig.advancementContracts` defaults to 4. CONFIRMED (default).

### 1.5 Needs
`SettlementNeedsFulfiller` iterates `config.needs` (`SettlementNeed[]`, each with `interval`, `requiresProductionEnabled`, `chance`) — `SettlementNeedsFulfiller.cs`. Needs are city-internal behaviours (e.g. `SettlementGrowthNeed` expands urban area, `SettlementTreesNeed`). They are **not** product needs; product "needs" are the shops' demand. CONFIRMED. The interval counters are private (`_intervals`) and not serialized.

### 1.6 Other city facets
- Loans: `settlement.GetLoanAmount(actor)`, `GetLoanApr(actor, duration)`, `GetLoanDuration(actor)` — evaluate formulas (`Settlement.cs:221-244`). Pure-ish (allocate formula arg struct, evaluate Jace formula). HIGH CONFIDENCE safe.
- Town center: `region.regionalBuilding` is the town center `Building` (`Settlement.cs:179`). `TownCenter` behaviour (`TownCenter.cs`). CONFIRMED
- Contract target building: `settlement.buildings.contractTarget` (`ContractsTarget`). CONFIRMED
- Houses: `settlement.buildings.houses` (`List<ResidentialBuilding>`, `capacity`, `tier`, `isDead`). CONFIRMED
- PR & Marketing: `PrAndMarketingEventSource` is a `SettlementBehaviour` on the settlement: `activeEvent`, `targetedActor`, `daysToEventConclusion`. CONFIRMED (`PrAndMarketingEventSource.cs`)
- Demand multiplier: `SettlementBase.GetDemandMultiplier()` = 0 if dead, else 1 (`SettlementBase.cs:123-126`). CONFIRMED
- Urban area: radius `config.urbanArea.urbanAreaRadius` (default 25) around `region.center` (`SettlementManager.cs:105-135`, `SettlementBase.cs:146-155`). CONFIRMED
- Player's home town: `Player.humanPlayer.hq.settlement` (`SettlementContractManager.cs:33-43`). CONFIRMED

---

## 2. Shops / stores

### 2.1 Class
`Shop : BuildingBehaviour, IBuildingBuiltCallbackReceiver, IWorldReadyListener, IKillable, IAutoMaxAcceptedProvider` (`Shop.cs:11`). One class for all store kinds; specific stores (Hardware, Grocery, etc.) are building prefabs differing in `soldTags`, `demandFormulaCategory`, `demandModifier`, etc. Store names = `shop.buildingName` (`BuildingBehaviour.cs:23`). Concrete store names/tags: UNKNOWN (game data).

Ownership: shops are owned by the settlement actor (built by city `buildingQueue`; when scenario allows player shop-building, `ShopOwnerOverride` reassigns owner to the region's settlement — `ShopOwnerOverride.cs:12-19`). So enumerating `settlement.buildings.shops` covers all shops. HIGH CONFIDENCE.

Which shops a city may get per tier: `settlement.type.GetShops(tier, preAlloc)` built from all `Building` prefabs with a `Shop` whose `settlementTypes`/`settlementTiers` match (`SettlementType.cs:61-73, 118-133`). CONFIRMED. Max shops per city `config.shops.max` (default 9, `SettlementShopsConfig.cs`). CONFIRMED (default).

### 2.2 Accepted products
- `Shop.sold` (`Shop.cs:132`, a public serialized list of `ProductDefinition`) — the products this shop buys. CONFIRMED
- Initialization: all products with any of `soldTags`, shuffled deterministically with a `System.Random` seeded by the settlement region's center tile, first `maxProducts` (default 9) — `Shop.cs:333-361`. CONFIRMED
- `sold` is **re-sorted in place** by monthly sold count when price modifiers update (`Shop.cs:537`). Don't hold references across frames; copy.

### 2.3 Inventory
- `shop.storage` (`IProductStorage`) — `storage.Count(product)` (`IProductStorage.cs:37,39`). Capacity per product: `ProductSpecificProductStorage.slots`, reading `shop.storage` as that type (as UI does, `ShopProductViewModel.cs:21`). CONFIRMED
- Per-actor delivered (owned) stock: `shop.GetDeliveredByActorCount(product, actor)` (`Shop.cs:298`) — **side effect**: it looks the actor up in `_deliveredByActors` through `GetSafe`, which inserts an empty dictionary for unseen actors into a **savegame-serialized** dictionary (`Shop.cs:484-487`, `Utils.cs:269-278`). Read `_deliveredByActors` via reflection with `TryGetValue` instead.

### 2.4 Demand
- Internal: private `_demand`, a dictionary product → int demand (`Shop.cs:83`, NOT serialized; rebuilt on load at `:671-674`).
- Public: `GetDemand(product, actor)` (`Shop.cs:234-246`) behaves as follows:
  - 0 if the shop is dead, or if the product has no entry in `_demand`.
  - Otherwise demand = stored demand × actor demand modifier × settlement demand multiplier, rounded to the nearest integer.
  - The actor demand modifier is the actor's `GetDemandModifier` for that product (region argument = the shop building's region); it is 1 when no actor is given.
  - The settlement demand multiplier is `GetDemandMultiplier()` (see §1.6).

Read-only. Pass a null actor for the raw demand. CONFIRMED
- Demand formula (`Shop.UpdateDemand`, `Shop.cs:363-396`): uses the first `Formula` in GameData with category `demandFormulaCategory`, evaluated with `ShopFormulaArguments` holding: `consumeInterval`; `difficulty` (1.0); `population`; `producedAmount` (recipe output amount); `shopModifier` (the shop's `demandModifier`); `tierValue` (the entry of `config.shops.demandTierModifiers` for the recipe's tier); `timeToProduce` (the recipe's `gameDays`). The result f gives demand = max(1, f × the product's `demandModifier`), capped by `GetProductDemandCap(product)` (from `ProductCategoryModifierInfo.GetDemandCap`, pseudo-random within range, seeded from the building tile plus the hash of the product name, `:572-587`). CONFIRMED. Formula text: UNKNOWN (game data).
- **Demand unit**: units consumed per consumption tick, i.e. per `config.growth.consumeProductsInterval` days (UI shows `consumeInterval` next to demand, `ShopProductViewModel.cs:13`). HIGH CONFIDENCE.
- Demand history: `_demandFigures` (`ProductInfoCollection`, serialized) — `GetDemandRanged(product, from, to, preAlloc)` (`Shop.cs:288`). Records max demand across actors per tick (`:437-443`). CONFIRMED
- Note `Region` arg to modifiers is ignored: `ActorModifiers.GetModifier` discards the region argument (replaces it with null) (`ActorModifiers.cs:174-178`), so regional modifiers are effectively global. CONFIRMED

### 2.5 Price
`Shop.GetPrice(product, actor)` and `Shop.GetShopModifier(product, actor)` (`Shop.cs:188-201`):
- shop price = global base `price` × (1 + global market `modifier` + shop modifier) × the actor's product price modifier (`GetProductPriceModifier` for that product, region argument = the shop building's region). The global values come from `GlobalMarket.instance.GetPricingInfo(product)`.
- shop modifier = the product's entry in `_priceModifiers` (or `defaultModifier` when the product has none) + the `prices` value of the actor's difficulty parameters (`GameParametersManager.instance.GetDifficultyParameters(actor)`).
- The actor must be non-null in `GetPrice` (NRE otherwise). Use `Player.activeActor` as UI does (`ShopUIViewModel.cs:50-52`). CONFIRMED
- UI "price modifier %" = (1 + global market `modifier` + `shop.GetShopModifier(...)`) × 100 (`ShopUIViewModel.cs:51`, `ProductPriceViewModel.cs:51`). CONFIRMED
- Per-shop modifiers `_priceModifiers` (serialized) are reassigned every `minDaysBetweenPriceUpdates..maxDaysBetweenPriceUpdates` days (default 45-90) when `updatePriceModifiers` is set: products sorted by monthly sold count ascending, buckets from `config.shops.priceModifiers` (`ShopPriceModifier` entries with `productCount` and `priceModifier`) — `Shop.cs:535-570`. So least-sold products get the first bucket. CONFIRMED. Countdown `_daysToNextPricesUpdate` is private serialized.

### 2.6 Sales / revenue
`ConsumeStoredProducts` → `ConsumeProduct(product)` (`Shop.cs:203-232, 414-473`):
1. Permit owner of the shop's region sells first, up to its own demand.
2. Then every other actor in `ActorManager.actors` order, up to that actor's demand minus the units already sold this tick.
3. For each sale: revenue = units × `GetPrice` for that product and actor; money flows `State.instance.money → actor.money` (the State pays, not the city) via `MoneyManager.EasyMoneySend` with `productTradeCategory`; `ProductSoldGameAction` + `PlayersProductSoldByShopEvent` dispatched.
4. Only products the actor delivered (`_deliveredByActors`) count.
CONFIRMED.

History (all `ProductInfoCollection`, day-bucketed `TimeTree<SaleInfo>`):
| Field | Meaning | Accessor | Serialized |
|---|---|---|---|
| `_overallSales` | units sold, all actors | `GetSoldCount(product, period|from,to)` | yes |
| `_demandFigures` | demand per tick | `GetDemandRanged(...)` | yes |
| `_sales` (per actor) | units sold by actor | `GetSoldCount(actor, product, ...)`, `GetSoldCountRanged` | via `_savegameSales` |
| `_salesFigures` (per actor) | revenue (int) by actor | `GetSalesFigures(actor, ...)`, `GetSalesFiguresRanged` | via `_savegameSalesFigures` |
| `_storageFigures` (per actor) | actor's stock after tick | `GetStorageRanged(...)` | via `_savegameStorageFigures` |
History older than ~1-2 years is pruned at year end (`Shop.cs:491-509`). CONFIRMED. Per-actor accessors use `GetSafe` (insert on miss) — see §8.

Supply ratio helper: `GetSupplyOverPeriod(product, actor, period)` = clamp01(sold/demand) (`Shop.cs:304-307`). Divides by demand (0 → Infinity/NaN, clamped). CONFIRMED.

City-level aggregation: `settlement.productStatistics` (`SettlementProductStatistics.cs`) sums `GetDemand`, `GetSoldCount`, `GetSalesFigures`, `GetStoredCount` across shops. CONFIRMED.

---

## 3. Regions & permits

### 3.1 Region
`Region : MonoBehaviour, IWorldReadyListener` (`Region.cs:9`), marked with the `SavegameEntityObject` attribute.
- `regionName` (`:47`), `id` Guid (`:51`), `center` tile index (`:53`), `tiles` (`ReadOnlyList<int>` struct wrapper, `:55`), `ContainsTile(tile)` (`:116`), `borders` (`List<RegionBorder>`), `settlement` (`:21`, public field), `hasSettlement` (`:49`), `regionalBuilding` (town center, `:17`). CONFIRMED
- Cooldowns: `permitAuctionCooldown`, `permitPurchaseCooldown` (days; decremented daily in `Tick`, `:208-218`). CONFIRMED
- Regions without a settlement exist when `settlementGenerationFrequency < 1` (`SettlementsWGOperation.cs:25-37`). CONFIRMED

### 3.2 Regional resources
- `availableResources` → live `Dictionary<ProductDefinition,int>` = count of resource-node tiles per raw product in region (`Region.cs:59, 140-178`). If any region tile is water, every product tagged `RegionManager.waterProductTag` is added with **`int.MaxValue`** sentinel (`:170-177`). CONFIRMED
- `IsResourceAvailable(p)`, `CountResource(p)`, `TryGetClosestResource(p)` (tile returned as out-parameter) (`:121-188`). CONFIRMED
- `resourceSites` (`List<ResourceSite>`): `center`, `radius`, `product`, `nodes`, `resourceAmount` (finite deposits) (`ResourceSite.cs`). CONFIRMED
- `FindAvailableResources()` is a **mutator** (clears & rebuilds), do not call.

### 3.3 RegionManager
`RegionManager.cs`: `regions` (`:42`), `regionCount` (`:44`), `GetRegionById(Guid)` (`:65`), `GetRegionByTile(int)` (`:74`, O(1) dict), `TileBelongsToRegion` (`:83`), `waterResources` (`:40`). CONFIRMED

Caveat: `Tile.GetRegionForTile` / `World.REGION_SIZE` (32) / `World.RegionCount` refer to **32x32 render chunks**, NOT political regions (`Tile.cs:534-556`, `World.cs:12-24`). CONFIRMED

### 3.4 Permits
- `Permit` members: `region`, `type` (`PermitType`), `owner` (`IActor`), `amountPaid`, `canBuildNetworks`, `canDemolishNetworks`, `canTerraform` (`Permit.cs`). CONFIRMED
- `PermitType` SO: `displayName`, `description`, `parent`, `auctionDefinition`, `costPerTile` (default 10), `costModifier` (default 1), flags, `topLevelParent`, `children` (`PermitType.cs:16-32`). CONFIRMED
- `PermitManager` (`PermitManager.cs`):
  - storage: private `_permits`, a nested dictionary region → permit type → `Permit` (`:34`), not directly serialized (rebuilt from `_savegamePermits`).
  - `fullPermit` (`:37`) — the default "own the region" permit.
  - `permitManagementEnabled` (`:53`): if false (non-permit game mode), all regions are granted to the player at new game (`:337-350` area, `GrantAllPermitsToPlayer` `:67`).
  - `GetPermitOwner(region, type)` (type optional) (`:191`), `IsPermitTaken` (`:139`), `OwnsPermitForRegion(actor, region, type)` (`:149`), `OwnsPermitForTile`, `HasAnyPermits(actor, type)` (`:217`, pure). CONFIRMED
  - Cost: `GetPermitCost(region, type)` (`:201`): cost = region tile count × `costPerTile` of the permit type's `topLevelParent` × the permit type's `costModifier`; when the region has no settlement, it is further multiplied by `_emptyRegionCostModifier` (0.75). The result is rounded to the nearest integer.
  CONFIRMED. Permits are acquired via auctions (`AuctionPermit`, State is auctioneer, cooldown 360 days) or region purchase offers (`RegionPurchaseOffer`, `RegionPurchaseAgent.TransferRegionOwnership`). CONFIRMED
- Enumerate "controlled regions" of an actor: iterate `RegionManager.regions` and keep the regions whose `GetPermitOwner(region)` is that actor (or reflect `_permits`). AI players additionally track regions (`AiPlayer.AddRegion`, `PermitManager.cs` `GrantPermitToActor`). HIGH CONFIDENCE.

---

## 4. Markets

### 4.1 GlobalMarket (world prices)
`GlobalMarket : ManagerBehaviour<GlobalMarket>` (`GlobalMarket.cs:10`).
- `_pricingInfoByProduct`: dictionary product → `ProductPricingInfo` (`:22`). `ProductPricingInfo` holds `value`, `price`, `modifier` (floats) and `trend` (`PriceTrend`) (`ProductPricingInfo.cs`). `PriceTrend` values: STABLE, GOING_UP, GOING_DOWN. CONFIRMED
- Base price computed once at world ready from recipes: `value` = the product's `price` formula evaluated with `ProductPriceFormulaArguments` (`upkeep`, `ingredientsValue`, `productOutput`, `recipeOutput`, `recipeDays`); `price` = `value` × the product category's `priceMultiplier` (`:131-229`). CONFIRMED. Formula text UNKNOWN (data).
- `GetPricingInfo(product)` (`:56`) — read-only (logs error + returns new empty object if missing). `GetFinalPrice(product, actor)` (actor optional): final price = `price` × (1 + `modifier`) × the actor's price modifier (`:66-71`). CONFIRMED
- Market-wide aggregates: `GetProductDemand(product, actor)` (`:73`), `GetStoredAmount(product)` (`:83`), `GetSoldAmount(product, period)` (`:93`, includes State sales). CONFIRMED, iterate all settlements/shops (O(cities*shops)).
- Modifier update: every `_updateIntervalInDays` (default 30) on day end, coroutine `UpdatePricesAsync` for **Player.activeActor** (`:257-297`). `ComputePriceModifier` evaluates `priceModifierFormula` with demand, sold, stored and net (= demand − sold − stored), each summed over all cities (+ State sales); it doubles negative values, adds ±1% random jitter with 50% chance, and scales the result by 0.01 (`:299-332`). End-game products and `_ignoredProducts` get modifier 0 (+ jitter). CONFIRMED
- **No price history**: only current `modifier` and last `trend` are stored (serialized `_serializedPricingInfo`). Historical global prices must be sampled by the MCP. CONFIRMED
- Event: `pricesUpdated` (`:54`). CONFIRMED

### 4.2 "State" (government actor) — trade & buyer of last resort
- `State : Actor` singleton `State.instance` (`State.cs:9,33`). Owns the state building (`stateBuilding`), off-map **state connections** (`stateConnections`: border tiles where roads connect to the outside world; `TryGetStateConnection`), and `tradingHandlers`. CONFIRMED
- `StateTradingHandler : ProductSeller` (`StateTradingHandler.cs`, component on state/trade buildings):
  - Sells: initial products = all `ProductDefinition`s in `rawResourcesCategory` (`InitializeSoldProducts`, `:45-57` in file) — i.e. the State **sells raw resources** to players. Sale price = `GlobalMarket.GetFinalPrice` for that product and actor × `saleMarkup` (1.25) × the actor's `GetStateSoldProductsPriceModifier()` (`ProductSeller.cs:23-26` + `StateTradingHandler.GetSalePrice`). CONFIRMED (markup defaults; live values from `CCCStateTradingHandlerModel`, UNKNOWN).
  - Buys (only if `allowIncomingTrade`): on delivery, pays per unit the product's global base `price` (from `GlobalMarket.GetPricingInfo`) × the actor's `GetStateBoughtProductsPriceModifier()` — note **base `price`, without the market modifier** (`StateTradingHandler.GetPurchasePrice`). Money only sent when the putter is the human `Player.activeActor` (`OnPut`). CONFIRMED
- State sales history: `State.GetSoldCount(product, period|from,to)` and `GetSalesFigures(product, from, to)` (`State.cs:79-92`); `_sales` pruned monthly to ~2 months (`:133-138`). CONFIRMED
- The State also **pays for every shop sale** (`Shop.cs:463`). CONFIRMED
- `GetClosestStateTradingHandler(tile|pos)` uses LINQ `OrderBy` (allocates). CONFIRMED

### 4.3 Contracts
- Base `Contract` (`Contract.cs`): `amount`, `delivered`, `reserved`, `product`, `issuer` (IActor, usually a Settlement), `target` (`ContractsTarget` building), `active`, `acceptedActor` (resolves `_actorId` through `ActorManager.GetActor`), `completed`. CONFIRMED
- `DeliveryContract` (city orders): `duration`, `reward`/`penalty` (`ContractResult` with an unsigned 64-bit `money` and an int `sign`), `priceChange`, `price` (set at creation to the product's `GlobalMarket.GetFinalPrice` × `priceChange`), private `endDate`, `remainingDays` (`DeliveryContract.cs`), `fulfilled`, `failed`, `ToString()` = contract name. CONFIRMED
- `AdvancementContract` (tier-up sponsor deliveries): `payOutSum`, `CanPayOut(actor)`, `settlement`. CONFIRMED
- Per city: `settlement.contracts` → `SettlementContractManager` (`currentContract`, `hasContract`) — one offer at a time (`SettlementContractManager.cs:30-57`). CONFIRMED
- Per actor: `actor.contracts` → `IContractsAgent` (members `maxContracts`, `canAcceptContracts`, `activeContracts`) (`IContractsAgent.cs`, `ContractsAgent.cs:17-21`). CONFIRMED
- Spawning: `ContractSpawner` daily rolls `_spawnChanceFormula`, picks random city without contract, `OfferNewContract()`; each offer is immediately turned into a **contract auction** (`ContractSpawner.cs:70-108`). `ContractGenerator` defaults: duration 90-180 days, priceChange 1.5-3.0, penalty 0.5 (`ContractGenerator.cs`). CONFIRMED (defaults).

### 4.4 Auctions
- `AuctionsManager` (`AuctionsManager.cs`): `currentAuction` (`:210`), `auctionQueue` (`Queue<Auction>`, **mutable reference exposed**, `:212`). Only one auction runs at a time; ends on `isOver` at day end or when bid threshold reached. May change game speed (`slowGameOnAuctions`). CONFIRMED
- `Auction` (`Auction.cs`): `title` (from `AuctionDefinition.title`), `description`, `duration`, `startBid`, `remainingDays`, `isOver`, `highestBid`, `bids` (`ReadOnlyList<Bid>`; each `Bid` has `bidder` and `amount`), `nextBid`, `auctioner`, `reward`, `definition`, `bidController`. CONFIRMED
- Reward types (`IAuctionReward`): `ContractAuctionReward` (`contract`), `PermitAuctionReward` (`region`, `permitType`), `AssetsAuctionReward` (`buildings`, `regions`, `permitType`; used for permit auctions by `PermitManager.AuctionPermit`), `BankruptcyAuctionReward` (derives from `AssetsAuctionReward`), `PrAndMarketingAuctionReward` (`rewardEvent`). CONFIRMED
- Contract auctions bid on **percentage of market value**: reward = `GetFinalPrice` of the product × bid % × amount, penalty = reward × `contractFailurePenalty` (0.5) (`ContractBidController.cs:10-45`). `BidType` values: ASCENDING, DESCENDING. CONFIRMED
- `ProjectAutomata.ContentCreationModels.Auctions.CCAuctionDefinitionModel` only loads `AuctionDefinition` data (bidController, rewardGenerator, title, startingBid, duration); there is a copy/paste bug: `customAuctionInfo` resolved from `bidController` string. CONFIRMED. Not runtime state.
- `AuctionDefinition.GenerateAuction(...)` creates objects — never call.

### 4.5 Wholesalers, trade, harbors, airports
- `Wholesaler : BuildingBehaviour` (`Wholesaler.cs`): buys products delivered to it at the product's `GlobalMarket.GetFinalPrice` × `saleMarkup` (1.25), paid by building owner to deliverer; `requests` (`List<BuyRequest>`), `GetRequestedAmount(p)`, `HasRequestForProduct(p)`. `WholesalerTradeFinder` distributes its stock to the settlement's shops and recipe users, with a 5% "magic ingredient" spawn (`WholesalerTradeFinder.cs:66-118`). HIGH CONFIDENCE this is a settlement-side market building; in-game display name UNKNOWN.
- **No dedicated import/export, harbor or international-market class found.** Water and air are transport networks: `WaterNetworkManager`, `AirNetworkManager` (`NetworkManager` subclasses, accessed via `World.Networks.GetNetwork(name)`), `Airfield` only stores spawn transform (`Airfield.cs`). "Import" = buying raw resources from the State; "export" = selling to State trading handlers (if `allowIncomingTrade`) or to shops/contracts. INFERRED (absence-of-evidence over filename + grep search).
- Product availability on the world market: the State sells every raw-resource-category product (always available, logistics permitting); there is no tracked global quantity. HIGH CONFIDENCE.

---

## 5. World map, tiles, resources, infrastructure

- World size: `World.Size` (static int, square), `World.SizeIndex` is `Size` squared (`World.cs:20-24`, set in `PrepareWorld`). CONFIRMED
- Tile index: index = x + y × `World.Size` (`Tile.cs:568-576`); `Tile.GetCoordinates` (index → x, y); `Tile.IsValid(index)` (`:908`). World position: `Tile.GetPosition(x, y)` gives the point (x, 0, y) (`:686`), `Tile.GetIndex(Vector3)`. CONFIRMED
- Height: `World.Height.GetHeightTile(index)` (byte), `GetHeightFloatTile` (`WorldHeight.cs:43,64`). Water level constant 12. CONFIRMED
- Water: `World.Water.IsWater(index)`, `IsPartiallyWater`, `GetWaterDepth`, `waterBodies` (`WorldWater.cs:33-104`). CONFIRMED
- Biome: `World.Biomes.GetBiome(index)` (`WorldBiomes.cs:9`). CONFIRMED
- Blocking: `World.Block.IsBlocked(index, BlockReason)` (`WorldBlock.cs:13`). CONFIRMED
- Resource nodes: `World.ResourceNodes.resourceNodes` (`ResourceNode[]` indexed by tile, null where none; `WorldResourceNodes.cs:9`), `GetResource(index)`; `ResourceNode` members: `displayName`, `resourceProduct`, `resourceAmount`, `reserved`/`reserver` (Harvester), `resourceSite`, `buildCost`, `canBeHarvestedIfDepleted` (`ResourceNode.cs`); spatial index `ResourceNode.All` (lazily created on first access). CONFIRMED
- Roads/rail: `ConnectivityNetworks.Road` / `.Rail` (`ConnectivityNetworks.cs`) → `.realityLayer` (`ConnectivityNetworkLayer`): `At(tile)` (`:621`), `GetOwner(tile)` (`:718`), `GetConnectivity(index)` (bitmask), `IsConnected(from,to)`. Other network managers by name via `World.Networks` (`WorldNetworks.cs`). CONFIRMED. `ConnectivityNetworks.Rail` getter has no null-guard on manager (NRE if called before world exists).
- Buildings on map: `BuildingManager.instance.buildingsList`, `GetBuilding(tile)`, `TryGetBuilding(tile)` (out-parameter variant) (`BuildingManager.cs:27,145,154`). CONFIRMED
- Pollution: `PollutionManager.instance.GetPollution(tile|x,y)` → 0..1 from GPU readback buffer (red channel of the `resultData` entry for the tile, divided by 255), returns 0 if not initialized or no automatic readback (`PollutionManager.cs:88-101`, `ComputePollutionStrategy.cs:487-499`). Read-only. CONFIRMED. `AsTexture()` returns a GPU texture (not for observer).
- Urban area bitmap: `SettlementManager.instance.IsUrbanArea(tile)`. CONFIRMED
- Building → region: `building.region` (set once in `Building.CreateBuildingInstance`, `Building.cs:280-290`) or `RegionManager.GetRegionByTile(building.tile)`. CONFIRMED

---

## 6. Recommended read-only enumeration recipe (main thread)

Our observer's read order (project design, described in prose):
1. Read the game date from `ManagerBehaviour<TimeManager>.instance.today`.
2. Take the player actor (`Player.activeActor` in the original recipe; per the errata at the top, use `Player.humanPlayer`).
3. Get `ManagerBehaviour<PermitManager>.instance`, but beware the `GetSafe` side effect of its getters (see §8).
4. For each region in `ManagerBehaviour<RegionManager>.instance.regions`:
   - read `regionName`, `id`, `center`, the tile count, a copy of `availableResources`, and `resourceSites`;
   - owner: reflect `PermitManager._permits`, look up the region, then the `fullPermit` entry, and read its owner (lookups only, no inserts);
   - permit cost: recompute it ourselves from the cost rule in §3.4 (tile count × top-level `costPerTile` × `costModifier`, × 0.75 when the region has no settlement);
   - skip the region if it has no `settlement`.
5. For each settlement, read `id`, `settlementName`, `type.settlementTypeName`, `tier.tierName` / `tierId`, `population`, `isDead`; from `growth`: `populationLimit` (on the concrete `SettlementGrowth`), `IsGrowing()`, `IsProspering()`, `IsWaitingForSponsor()`, `populationLimitReached`; from `advancement`: `canAccept`, `isAdvancing`, `contracts`; and `contracts.currentContract`.
6. For each shop in `settlement.buildings.shops`, iterate over a copy of `sold`, and for each product read: raw demand (`GetDemand` with a null actor), player demand (`GetDemand` with the player), stock (`storage.Count`), price (`GetPrice` with the player), and units sold over the last month (`GetSoldCount` with `GamePeriod.month`; the overall variant is safe, it does not use `GetSafe`).
7. For each product in GameData, read `GlobalMarket.instance.GetPricingInfo` (price, modifier, trend).
8. Read `ManagerBehaviour<AuctionsManager>.instance.currentAuction` plus a copy of `auctionQueue`.

HIGH CONFIDENCE (composed from CONFIRMED members; not executed).

---

## 7. Serialization notes (useful for an offline save-file reader)
- `SettlementBase` fields `_settlementName`, `_type`, `_population`, `_isDead`, `_region`, `_tier`, `_config` carry the `SavegameSerialized` attribute. CONFIRMED
- `Shop`: `_demandFigures`, `_overallSales`, `sold`, `soldTags`, `_priceModifiers`, `_deliveredByActors`, `_daysToNextPricesUpdate`, `_isDead`, `_savegameSales/_SalesFigures/_StorageFigures`. **`_demand` is NOT serialized** (recomputed). CONFIRMED
- `ProductInfoCollection` serializes as parallel `ushort` arrays `days`, `amounts`, `productIds` plus a `products` string ("|"-joined product asset names) (`ProductInfoCollection.cs:87-112`). Amounts are truncated to `ushort` (revenue > 65535 per day per product will wrap in saves). CONFIRMED
- `GlobalMarket`: `_daysSinceLastUpdate`, `_serializedPricingInfo` (product, modifier, trend); base prices recomputed. CONFIRMED
- `PermitManager`: `_savegamePermits` (actor id, region id Guid, permit type, amount paid), `_permitManagementEnabled`, `_auctionedRegions`. CONFIRMED
- `Region`: `_regionName`, `regionalBuilding`, `settlement`, `_resourceSites`, cooldowns; tiles/center via constructor params. CONFIRMED
- `AuctionsManager`: `_currentAuction`, `_savegameAuctions` (queue). CONFIRMED

---

## 8. Unsafe / side-effecting members (do NOT call from an observer)

| Member | Why | Evidence |
|---|---|---|
| `Utils.GetSafe(dict, key)` used by many getters | **Inserts** a new default-constructed value when the key is missing | `Utils.cs:269-278` |
| `Shop.GetDeliveredByActorCount`, `GetSoldCount(actor,…)`, `GetSalesFigures(actor,…)`, `GetSoldCountRanged`, `GetSalesFiguresRanged`, `GetStorageRanged` | use `GetSafe` on `_deliveredByActors` (serialized!) / `_sales` / `_salesFigures` / `_storageFigures` | `Shop.cs:258-301, 484-487` |
| `SettlementProductStatistics.GetSoldCount(p, period, actor)`, `GetSalesFigures(...)` | same, via shops | `SettlementProductStatistics.cs` |
| `PermitManager.TryGetPermit`, `GetPermitOwner`, `IsPermitTaken`, `OwnsPermitFor*`, `GetPermitCost`, `CanTerraformAt` | `GetSafe` on `_permits` with the region inserts an empty inner dict (harmless logically, but a dictionary write; unsafe off main thread) | `PermitManager.cs:177-215` |
| `Shop.UpdateDemand`, `Initialize`, `ConsumeStoredProducts`, `UpdatePriceModifiers` (private) | mutate demand/sales/money; price update consumes `World.deterministicRandom` and fires notification | `Shop.cs:203-232, 333-396, 535-570` |
| `Shop.GetValidProducts` | allocates & shuffles; harmless but pointless | `Shop.cs:345-361` |
| `GlobalMarket.ComputePriceModifier` / `UpdatePrices` (private) | consume `World.deterministicRandom` → desyncs deterministic RNG | `GlobalMarket.cs:299-332` |
| `SettlementGrowth.GetTier*` (`GetTierForCurrentPopulation`, `…Capacity`) | may call `InitializeTierThresholds()` which draws a deterministic `RandomInRange` value if not yet initialized; normally initialized after world ready | `SettlementGrowth.cs:64-101` |
| `SettlementGrowth.TryGrow`, `ChangePopulation`, `ForceGrowth` | mutate population | `SettlementGrowth.cs:77-82,152-208` |
| `SettlementNeedsFulfiller.TryFulfillNeeds` | decrements intervals, RNG, builds | `SettlementNeedsFulfiller.cs` |
| `SettlementAdvancement.StartAdvancement/Accept/Cancel/SelectShop/UpdateContracts` | state machine changes | `SettlementAdvancement.cs:441-470` |
| `SettlementContractManager.OfferNewContract/AcceptContract/RejectContract/CompleteContract` | mutate, play sounds | `SettlementContractManager.cs:96-196` |
| `DeliveryContract.Reject/Apply/RegisterContract`, `AdvancementContract.PayOut` | money transfers | `DeliveryContract.cs`, `AdvancementContract.cs` |
| `Region.FindAvailableResources`, `Tick` | rebuild resources / decrement cooldowns | `Region.cs:140-218` |
| `AuctionsManager.TryStartNextAuction/EndCurrentAuction/EnqueueAuction`; `Auction.MakeBid`; `auctionQueue` returned by reference | mutate auctions, money, game speed | `AuctionsManager.cs:229-313` |
| `PermitManager.AuctionPermit/GrantPermitToActor/TransferPermit/RevokePermit/GrantAllPermitsToPlayer` | ownership changes | `PermitManager.cs` |
| `RegionPurchaseOffer.Accept`, `RegionPurchaseAgent.TransferRegionOwnership` | ownership + money | `RegionPurchaseAgent.cs:95-120` |
| `SettlementBase.ResetAndSpawn`, `Kill`, `PerformInitialBuild` | destructive | `SettlementBase.cs:140-264` |
| `State.CreateBuilding/Create/AddStateConnection/RemoveStateConnection/OpenContextUI`, `StateBuilding.GetLookAtTargetTile` (advances `_lookAtIndex`) | mutate | `State.cs`, `StateBuilding.cs` |
| `TownCenter.OpenBuildingPanel*`, `Building.OpenBuildingPanel` | UI side effects | `TownCenter.cs` |
| `WorldResourceNodes.resourceNodeQuadTree` / `ResourceNode.All` | lazily allocates spatial index on first access (one-time) | `WorldResourceNodes.cs:18-28` |
| `GlobalMarket.GetPricingInfo` for unknown product | logs error, returns fresh object | `GlobalMarket.cs:56-64` |
| `Shop.GetPrice` with a null actor | NRE (it reads the actor's `modifiers`) | `Shop.cs:191` |
| Any `ProductInfoCollection.GetProductInfo/GetInRange` | uses static `ListPool` (not thread-safe) | `ProductInfoCollection.cs:62-85` |

Expensive (fine occasionally, avoid per-frame): `GlobalMarket.GetProductDemand/GetStoredAmount/GetSoldAmount` (all cities × shops × TimeTree range queries), `State.GetClosestStateTradingHandler` (LINQ sort), `Shop.GetProductDemandCap` (LINQ over GameData, never cached despite `_demandCaps`), `SettlementType.GetShops` (pooled list).

---

## 9. Threading / lifecycle

- Managers resolve via `ManagerBehaviour<T>.instance`, which calls `FindObjectOfType` only on the main thread; off-thread it returns the cached instance or null (`ManagerBehaviour.cs:13-46`). CONFIRMED
- All collections above are plain `List`/`Dictionary` mutated by game ticks (`TimeManager.onDayStart/onDayEnd/onMonthEnd/onYearEnd`) and coroutines (`GlobalMarket.UpdatePricesAsync` yields every 5 products, so prices can be **half-updated across frames** once a month). Read snapshots on the main thread, ideally right after a day tick. HIGH CONFIDENCE
- World readiness: `World.instance.isWorldReady` (`World.cs:111`); Shop `_demand`, GlobalMarket prices and permit dict are only populated after `OnWorldBecameReady`. CONFIRMED

---

## 10. Open questions / UNKNOWN
- Concrete store names, sold tags, demand formula, price formulas, tier thresholds, settlement types, ShopPriceModifier buckets, State markups — all game data (content-creation JSON / asset bundles). UNKNOWN.
- Display names of `Wholesaler` / state trading buildings in UI. UNKNOWN.
- Whether a harbor/airport building participates in trade (vs. pure logistics). INFERRED no (no trade logic in `Airfield`; no harbor class).
- `BuyRequest` structure for wholesaler requests — not inspected.
