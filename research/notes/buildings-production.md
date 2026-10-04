# Buildings, production, recipes, inventory, gatherers and static definitions

> **Errata from the asset dump (2026-10-03, CONFIRMED, see `notes/static-dump.md`):**
> - `Upkeep.buildingCostPercentage` is **0.025** in prefabs (code default 0.25 quoted below): monthly upkeep ≈ 2.5 % of base cost × modifiers.
> - `BuildingEfficiency` prefab arrays: output and upkeep multipliers are both {0.25, 0.5, 0.75, 1, 1.25, 1.5, 2}; `initialEfficiencyIndex` = 3 (100 %), not the code defaults quoted below.
> - `ModuleOwner.maxModuleCount` = 3 for all hubs (code default 5). Storage `slots` = 40 or 100 for most prefabs. `BuildingNameGenerator.nameFormat` = `"%n %i"` or `"%n"`.
> - For "the player" use `Player.humanPlayer`, not `Player.activeActor` (UI/debug can switch the latter; see `company-finance-time.md` §1.2). Where this note says `Player.activeActor`, read `Player.humanPlayer`.

These notes come from static reading of the ILSpy-decompiled `Assembly-CSharp` (Rise of Industry 2.3.3 / Steam build 9064059 / Unity 2018.4.11 Mono).
All paths are relative to `research/_local/decompiled/Assembly-CSharp/ProjectAutomata/` unless stated otherwise. Line numbers refer to the decompiled files.

Labels:
- **CONFIRMED**: read directly in the source.
- **HIGH**: high confidence.
- **INFERRED**: deduced from the code but not proven.
- **UNKNOWN**: could not be determined.

---

## 0. TL;DR for the MCP observer

| Need | Read this | Label |
|---|---|---|
| All buildings (every owner) | `ManagerBehaviour<BuildingManager>.instance.buildingsList` (`List<Building>`) | CONFIRMED `BuildingManager.cs:15,27` |
| Buildings of one company | `actor.buildings` (`ActorBuildingCollection`, has a `ReaderWriterLockSlim`: `EnterReadLock/ExitReadLock`) | CONFIRMED `ActorBuildingCollection.cs:12,48-56` |
| Player / AI actors | `Player.activeActor`; `ManagerBehaviour<AiPlayerManager>.instance.aiPlayers` | CONFIRMED `Player.cs:14`, `Building.cs:482` |
| Stable building ID | `GuidMapper.instance` GUID (persisted in savegame). Avoid `GetGUIDForObject` on unknown objects: it mints a new GUID | HIGH `GuidMapper.cs:38-47`, `SavegameManager.cs:319` |
| Building type ID | `building.prefab.name` (Unity asset name, not localized) | CONFIRMED `Building.cs:294` |
| Display name ("GAS WELL 2") | `building.buildingName` (instance field, generated, upper-cased, can be custom) | CONFIRMED `BuildingNameGenerator.cs:131-133` |
| Position | `building.tile` (int tile index), `Tile.GetCoordinates` (tile index → x, y), `building.rotation` | CONFIRMED `Building.cs:69-72,285` |
| Region / settlement | `building.region` (`Region.regionName`, `Region.id` Guid), `building.settlement` | CONFIRMED `Building.cs:61,64,289-290`, `Region.cs:47,51` |
| Owner | `building.buildingOwner` / `GetOwner()` (`IActor`: Player / AiPlayer / SettlementBase / State) | CONFIRMED `Building.cs:66,635` |
| On / off (pause) | `building.IsStateFlagSet(BuildingStateFlags.UserEnabled)` | CONFIRMED `BuildingStateFlags.cs`, `BuildingPanelViewModel.cs:120-128` |
| Working | flags `IsWorking` and `RequirementsMet` | CONFIRMED |
| Recipe | `building.recipeUser.currentRecipe` | CONFIRMED `RecipeUser.cs:120` |
| Progress (factory) | `Factory.currentProgress` (0..1), with `recipeUser` read as a `Factory` | CONFIRMED `Factory.cs:9` |
| Progress (gatherer/farm) | `currentProgress` of each entry in `moduleOwner.modules` (per harvester/field) | CONFIRMED `Module.cs:13`, `GathererHub.cs:100-107` |
| Inventory | `building.storage` (`IProductStorage`); enumerate it, or call `Count(def)` / `GetSlots()` | CONFIRMED |
| Efficiency ("wages") | `GetComponent<BuildingEfficiency>().efficiencyIndex` / `.efficiency` / `.upkeepModifier` | CONFIRMED `BuildingEfficiency.cs` |
| Upkeep | `GetComponent<Upkeep>().totalActiveMonthlyUpkeep` | CONFIRMED `Upkeep.cs:58` |
| Production counters | `recipeUser.producedThisMonth`, `producedLastMonth`, `totalProduced`, `currentProducedAverage` | CONFIRMED `RecipeUser.cs:51-61` |
| Blocking reasons | `GetComponent<BuildingRequirementController>().notifications` (player only, debounced), plus flags and the logic in §4.4 | CONFIRMED |
| All recipes / products / building types | `GameData.instance.GetAssets<Recipe>()` / `<ProductDefinition>()` / `<Building>()` | CONFIRMED (`ExportStuffMod`) |
| Product graph | `RecipeDatabase.instance.GetRecipes(product)`, `GetRecipesWhereProductIsUsed`, `GetOriginsOfRecipe` | CONFIRMED `RecipeDatabase.cs` |

**Threading:** read everything on the Unity main thread (via a Harmony postfix on an `Update`/`LateUpdate`, or a main-thread dispatcher). Many getters touch Unity APIs (`GetComponent` through `LazyComponentRef`), `ListPool` (static, not thread-safe), or non-thread-safe dictionaries. CONFIRMED patterns; see §9.

---

## 1. Building class and component structure

### 1.1 `Building` (the root component)

`Building.cs:10-12`: `Building` is a `MonoBehaviour` marked with the `SavegameEntityObject` and `SavegameGameDataObject` attributes (game-data type "buildings"). It implements `IGameDataObject`, `IActorOwned`, `ICopyableSettings`, `IDemolishable` and further interfaces.
- One `GameObject` per building. Prefab assets live in GameData (type `Building`). Each instance points back to its prefab through `building.prefab` (`Building.cs:58`, set at `:283`). CONFIRMED.
- Role-specific behaviour is added through sibling components that are cached lazily (`Building.cs:84-110`, getters `:145-175`):
  `topology`, `validator`, `connectionManager`, `analysis` (`BuildingAnalysis`), `storage` (`IProductStorage`), `logistics` (`BuildingLogistics`), `module` (`Module`), `moduleOwner` (`ModuleOwner`), `recipeUser` (`RecipeUser`), `harvester` (`Harvester`), `house` (`ResidentialBuilding`), `placementEvaluator`, `manualDestinations`, `outline`. CONFIRMED.
- `isModule` / `isModuleOwner` are true when the `module` / `moduleOwner` component is present (`:159,163`). CONFIRMED.
- Every other behaviour derives from `BuildingBehaviour` (`BuildingBehaviour.cs`), which exposes `building`, `logistics`, `analysis`, `techTreeUser`, `transport`, and `storage`. CONFIRMED.

### 1.2 Role hierarchy (components, not subclasses of `Building`)

```
BuildingBehaviour
├─ RecipeUser                       (RecipeUser.cs)        production base
│   ├─ Factory                      (Factory.cs)           progress lives on the factory
│   └─ GathererHub                  (GathererHub.cs)       progress lives on the modules (harvesters)
│       ├─ Farm  (empty subclass)   (Farm.cs)
│       └─ DisconnectedGathererHub  [Obsolete, throws in Awake]
├─ Module (abstract)                (Module.cs)            currentProgress, moduleOwner, extra upkeep
│   └─ Harvester                    (Harvester.cs)         mines / wells / lumber / fishing...
│       └─ DisconnectedHarvester    (DisconnectedHarvester.cs)
│           └─ Field                (Field.cs)             farm fields: speed = pollution efficiency
├─ ModuleOwner                      (ModuleOwner.cs)       modules list, modulePrefab, maxModuleCount, radius
│   └─ MultiModuleOwner             (MultiModuleOwner.cs)
├─ BuildingLogistics                (accepted/outgoing products, warehouse options)
├─ Warehouse                        (Warehouse.cs)
├─ Upkeep, BuildingEfficiency / BuildingSettlementEfficiency
├─ BuildingNameGenerator
├─ BuildingAnalysis                 (per-building time-series stats)
├─ BuildingRequirementBase → BuildingRequirement → ... (blocking conditions)
├─ PolluterBuilding, *PollutionEffect
└─ Shop, ResidentialBuilding, InfiniteStorage, ModuleSharedProductStorage ...
```
All of these are CONFIRMED by reading the class declarations.

`Field : DisconnectedHarvester`; its only change is that `GetProductionSpeed()` returns the pollution efficiency `_efficiency` (`Field.cs`). `Farm : GathererHub` has an empty body (`Farm.cs`). Both CONFIRMED. A "farm" is a `GathererHub` whose modules are `Field`s. A "gatherer" (mine, well, lumberyard) is a `GathererHub` whose modules are `Harvester`s.

### 1.3 How buildings are enumerated at runtime

**Global registry: `BuildingManager` (CONFIRMED, `BuildingManager.cs`)**
- `buildings` (`:15`): protected list of `Building`, saved as a savegame entity list.
- `buildingsList` (`:27`): public read access to that same list (not a copy).
- `OnBuildingRegistered` (`:21`) and `OnBuildingDestroyed` (`:23`): public `Action<Building>` fields.
- `OnCreateOrDestroyBuildingOnTile` (`:19`): public `Action<int>` field (tile index).
- `GetBuilding(tileIndex)` (`:145`): tile → building, for every tile of the footprint.
- `TryGetBuilding(tileIndex)` (`:154`): same lookup, returning success plus the building as an out-parameter.
- `Building.Create` calls `BuildingManager.Register` (`Building.cs:215`). `OnDemolish` calls `DeRegister` (`Building.cs:684`). CONFIRMED.
- The registry contains **every** building: player, AI, settlement/urban buildings, and modules (harvesters and fields are `Building`s too). Filter with `isModule`, `recipeUser`, tags, or owner. CONFIRMED (no type filter in `Register`).
- `LateUpdate` calls `building.UpdateBuilding()` on every building each frame (`BuildingManager.cs:221-227`). This only toggles visuals. CONFIRMED.

**Per-actor collection: `actor.buildings` → `ActorBuildingCollection` (CONFIRMED)**
- `_buildings`, `_buildingsByPrefab`, `_buildingsByTag`, `recipeUsers` (`List<RecipeUser>`), `affectedByPollution` (`ActorBuildingCollection.cs:12-35`).
- It has a `ReaderWriterLockSlim` with public `EnterReadLock()` / `ExitReadLock()` (`:12,48-56`). The game writes under the write lock (`Add`, `:58-71`). This is the only explicitly thread-aware building collection found.
- API: `Filter(Building prefab)`, `Filter(BuildingTag)`, `Count(prefab)`, `Count(tag)`, `Contains(prefab, zone)`, struct `GetEnumerator()` (`:135-191`). CONFIRMED.
- Events: `onBuildingAdded`, `onBuildingRemoved` (`:37-39`). CONFIRMED.
- Savegame: `_savegameBuildings` is a `List<Guid>` built with `GuidMapper.GetGUIDForObject(building)` (`:273-281`). CONFIRMED.

**Other enumeration helpers:**
- `ProductionStatsTracker.instance.recipeUsers` holds the **player's** recipe users only (`ProductionStatsTracker.cs:115`, and the `RegisterBuilding` filter at `:543+` keeps only buildings whose `GetOwner()` equals `Player.activeActor`). CONFIRMED.
- `Building.onDemolish` (`Action<Building, IActor, IActor>`) and `Building.onOwnerChanged` are per-instance events (`Building.cs:207-209`). CONFIRMED.

---

## 2. Building identity and state

### 2.1 Identifiers
| Item | Member | Notes | Label |
|---|---|---|---|
| Type/definition | `building.prefab` (a `Building` prefab asset), `prefab.name` | Savegame stores a `SavegamePrefabIdentifier` built from the `Building` type and `prefab.name` as the first entry of `constructorParams` (`Building.cs:292-300`). `prefab.name` is the stable, non-localized type key (e.g. `CropFarm`, `PetrochemicalFactory`, `WaterWellHarvester`, per the ExportStuff export) | CONFIRMED |
| Instance GUID | `GuidMapper.instance` maps object ↔ Guid | Restored from savegame through `RegisterObjectByGUID` (`SavegameManager.cs:319,344`). A building created this session gets a GUID lazily on the first `GetGUIDForObject` call (normally at save). Use `TryGetObjectByGUID` for reverse lookup. `GuidMapper` uses a plain `Dictionary`, so it is not thread-safe | HIGH |
| Unity instance ID | `building.GetInstanceID()` | Stable for the session only | HIGH |
| Display/custom name | `building.buildingName` (savegame-serialized, `Building.cs:25-26`) | See §2.2 | CONFIRMED |
| Panel name | `building.buildingPanelName` returns the prefab's `_buildingPanelName` if one is set, otherwise `buildingName` (`:129-143`) | | CONFIRMED |
| GameObject name | `building.name` is set to the **prefab's** `buildingName` at creation (`:284`) and is not updated afterwards | | CONFIRMED |
| Constructor params | `constructorParams` holds, in order: prefab id, x, y, rotation, owner GUID, and an empty sixth entry (`:292-300`). On an owner change, the fifth entry (index 4, owner GUID) is updated (`:620`) | | CONFIRMED |

### 2.2 Numbered names ("FACTORY 5", "GAS WELL 2")
`BuildingNameGenerator` (`BuildingNameGenerator.cs`), CONFIRMED:
- The prefab field `nameFormat` uses these tokens: `%n` = `prefab.buildingName`, `%s` = settlement name, `%r` = current recipe asset `name`, `%i` = per-owner, per-prefab index. `\%` is an escaped percent sign (`:325-382`).
- Savegame-serialized fields `index` (int) and `usingCustomName` (bool) (`:257-262`).
- `Initialize()` is called from `BuildingManager.Register` (`BuildingManager.cs:64-68`). It scans `buildingsList` for buildings with the same owner and the same `prefab.buildingName`, sorts their indices, and takes the first gap (or max+1) (`:303-321`). An index loaded from a save (>0) is kept (`:294`).
- `UpdateBuildingName()` writes the upper-cased formatted name into `building.buildingName` (`:380`). It runs on every `CustomUpdate` but returns early unless the name is stale or the recipe changed (`:327`).
- The user can rename through `Building.SetBuildingName(newName)`, which trims, upper-cases, and sets `usingCustomName` (`Building.cs:376-385`).
- Copy-paste settings (`WriteMySettingsTo`) call `Utils.IncreaseIndex(buildingName)` (`Building.cs:665`, `Utils.cs:31`).
- `BuildingManager.buildingCounters` (`:10`, incremented at `:59-69`) is a separate per-owner, per-prefab counter. It is private and unused for naming. CONFIRMED.
- **Localization:** `prefab.buildingName` is a localized string field. `GameDataLocalizationTable` localizes GameData assets in place through `UnityObjectLocalizer` with key prefix `<Type>.<asset name>` (`GameDataLocalizationTable.cs`, `UnityObjectLocalizer.cs`). Generated names are therefore in the UI language (French on this install). Use `prefab.name` for identity. HIGH.

### 2.3 State flags
`BuildingStateFlags.cs` (CONFIRMED): a bit-flags enum over `uint` with these values:
- None = 0
- UserEnabled = 1
- RequirementsMet = 2
- IsWorking = 4
- CanWork = UserEnabled and RequirementsMet combined (3)

- Stored in the savegame-serialized field `building.buildingStateFlags` (`Building.cs:74-75`). `Awake` sets it to `UserEnabled` (`:575`).
- `IsStateFlagSet(flag)` is true only when every bit of the requested flag is set (`:359-362`), so `IsStateFlagSet(CanWork)` means enabled **and** requirements met.
- **UserEnabled** is the player's on/off toggle (`BuildingPanelViewModel.cs:120-128`). AI, settlements, and repurposing also set it (`AiPlayerContractsAgent.cs:26`, `SettlementDeathManager.cs:89`, `RepurposingAgent.cs:139,297`). This is the "paused/active" state.
- **RequirementsMet** is set by `BuildingRequirementController.CheckRequirements()` every 3–5 s of unscaled time (`BuildingRequirementController.cs:85-89,179-194,235`).
- **IsWorking** is set by `Factory.isProducing` (`Factory.cs:15-25`), `GathererHub.CanStartProducing` (`GathererHub.cs:70`), `Harvester.isWorking` (`Harvester.cs:52-62`), and for logistics buildings by `LogisticalBuildingWorkingCheck` (= storage not empty).

### 2.4 Position, region, owner
- `building.tile` (int, not serialized) and `building.rotation` (`Direction`) are rebuilt from `constructorParams` on load through the savegame entity constructor `Create(prefab, x, y, rotation, owner)` (`Building.cs:211-224,280-311`). `Tile.GetCoordinates` converts a tile index to x, y (`Tile.cs:506`). CONFIRMED.
- `building.topology.GetAffectedTiles(list)` gives the footprint (`Building.cs:503`). CONFIRMED.
- `building.region` is set from `RegionManager.GetRegionByTile(tile)` at creation (`:289`). `Region.regionName`, `Region.id` (Guid), `Region.settlement` (`Region.cs:21,47,51`). CONFIRMED.
- `building.settlement` is the settlement of `building.region` (null when there is no region) (`:290`). CONFIRMED.
- Owner: `building.buildingOwner` (`IActor`). `IActor.id`, `IActor.actorName` (`IActor.cs`, `Actor.cs:37-57`). Implementations: `Player` (human), `AiPlayer : Player`, `SettlementBase` (towns own urban buildings), `State`. Test for the player by comparing the owner with `Player.activeActor`. CONFIRMED.
- Owner change: `ChangeOwner(newOwner)` moves the building between actor collections, cascades to modules, and fires `onOwnerChanged` (`:616-632`). CONFIRMED.

### 2.5 Cost, upkeep, efficiency ("wage" slider)
- **Build cost:** `building.baseCost` (prefab). Actual cost for an actor is `GetCostForActor(actor, tile)`, which applies the tech tree and modifiers (`Building.cs:404-423`). What was actually paid is `paidToBuildAmount` (savegame field `paidToBuild`), and the refund is 75 % of that (`:189,193,582-585`). CONFIRMED.
- **Upkeep** (`Upkeep.cs`, CONFIRMED):
  - base = `baseCost` × `buildingCostPercentage` (default 0.25), plus registered additional upkeep. Each `Module` adds the module building's `baseCost` × the module's `buildingCostPercentage` (`Module.cs:95-101`).
  - Modifiers (`GetUpkeepModifier`): difficulty × actor modifiers × efficiency `upkeepModifier` (`:178-194`).
  - Floor: `baseCost` × `buildingCostPercentage` × `minUpkeep` (min 0.25) (`:89,102,114-117`).
  - `totalMonthlyUpkeep` (full), `totalActiveMonthlyUpkeep` (min upkeep when not `shouldPayUpkeep`, i.e. not `CanWork`/disabled) (`:56-74,105-112`).
  - Accrual: each day `upkeep` grows by `totalActiveMonthlyUpkeep` / 30. The amount is paid at month end to the settlement or the State (`:143-176`). The savegame-serialized float `upkeep` holds the current month's accrual and the int `daysUp` holds uptime days (`:44-48`).
  - A `monthlyUpkeep` field of type `Formula` (`:23`) is declared but not used inside `Upkeep`. INFERRED as legacy.
- **Efficiency slider** (`BuildingEfficiency.cs`, CONFIRMED). This is the game's "wage/salary" setting. Separate wage or worker objects were not found.
  - The savegame-serialized int `_efficiencyIndex` is exposed as `efficiencyIndex` (`:72-77`). Default `initialEfficiencyIndex` is 4.
  - `basicEfficiencyModifierValues` defaults to {0.25, 0.5, 0.75, 1, 1.25, 1.5, 2} and `upkeepModifierValues` to {0, 0.4, 0.6, 0.8, 1, 1.5, 2} (prefab defaults, `:59-61`; may be overridden per prefab, UNKNOWN).
  - efficiency = the `basicEfficiencyModifierValues` entry at the current index × the owner's production-efficiency modifier for this building (`GetProductionEfficiencyModifier`) (`:88-95`). The getter writes the private cache `_efficiencyMultiplier`, which is benign.
  - `upkeepModifier` = the `upkeepModifierValues` entry at the current index (`:97`).
  - Higher indices are gated by `unlocks[]` tech (`CanSetEfficiencyModifier`, `:144-156`).
  - Some buildings use `BuildingSettlementEfficiency` instead (efficiency = settlement tier efficiency, upkeepModifier 1) (`BuildingSettlementEfficiency.cs`).
  - Read it as `GetComponent<IBuildingEfficiency>()` to cover both.
- **Pollution:** `PolluterBuilding.pollutionAmount` = `pollutionRate` × (`efficiencyFormula` evaluated for this polluter) × `_pollutionFactor` × difficulty factor (`PolluterBuilding.cs:49`). This getter **evaluates a Formula**, which may allocate (INFERRED). `HarvesterPollutionEffect` reduces `harvester.efficiency` (`HarvesterPollutionEffect.cs:17-44`). `BuildingRequirementNotPolluted` disables a building when `ThresholdBuildingPollutionEffect.isPolluted`. CONFIRMED.

---

## 3. Production

### 3.1 `RecipeUser` (base for factories and gatherer hubs)
`RecipeUser.cs`, CONFIRMED:
| Member | Line | Meaning |
|---|---|---|
| `currentRecipe` (`Recipe`, backed by the savegame-serialized `_currentRecipe`) | 48-49,120-139 | Selected recipe. **The setter has heavy side effects:** it clears storage, drops reservations, updates logistics, and fires events (`NewRecipeWasSelected`, `:463-499`). Never set it |
| `availableRecipes` (`Recipe` array), `initialRecipe` | 31-38 | From the prefab |
| `knownRecipes` (`IEnumerable<Recipe>`) | 107-118 | Filtered by the owner's tech tree (LINQ `Where`, which allocates) |
| `productionSpeed` (float) | 34 | Prefab base speed |
| `GetFinalProductionSpeed(mod)` (mod defaults to ALL) | 501-504 | `productionSpeed` × the actor's recipe-time modifier (for the region and recipe) × max(efficiency, 0) |
| `GetFinalProductionTime(mod)` | 520-527 | The current recipe's `gameDays` / final production speed, in game days |
| `producedThisMonth`, `producedLastMonth`, `totalProduced` | 51-58 | Counts the **sum of all result amounts** per completed cycle (via `ProductList.Count` on the recipe result, which sums amounts) (`:329-331`) |
| `currentProducedAverage` (`AverageStatistic`, 10 months) | 61, 178 | `currentAverage` uses LINQ `Sum` |
| `productionFrames`, `framesSpentProducing` | 66-70 | Uptime ratio. Logged monthly as an efficiency analysis item: 100 × frames spent producing / production frames (`:529-539`) |
| `producedGoods`, `consumedGoods` (`ProductInfoCollection`) | 72-76 | Per-product time series, pruned yearly (`:541-551`) |
| `GetProducedInRange(def, from, to)`, `GetConsumedInRange` | 361-379 | Uses `ListPool` (main thread only) |
| `currentOutput` (`Product`) | 141-155 | First result entry |
| `onProductProduced` (event of type `ProductionCompleteDelegate`, arguments: building, product definition, amount) | 23,172,334 | Fired on each finished cycle per result entry |
| `onRecipeChanged` (`RecipeEvent`, a UnityEvent) | 93 | |
| `openReservations` | 63-64 | Output slot reservations |

Production loop (`Update`, `:237-260`): each frame `productionFrames` grows by `productionFactor`. If `CanStartProducing()`, it adds `StartProducing()` to `productionLines`. Each line is ticked with `Time.deltaTime`; on completion it calls `FinishProduction()`. CONFIRMED.

Base `CanStartProducing` (`:381-396`): recipe set, `CanWork`, `storage.ContainsAll(ingredients)`, `storage.CanReserveAll(result, PUT)`. CONFIRMED.

### 3.2 `Factory`
`Factory.cs`, CONFIRMED:
- `currentProgress`: savegame-serialized float, 0..1 (`:8-9`). It is also exposed through `FactoryProductionProgressProvider` (an `IProductionProgressProvider`).
- `isProducing` is mapped to `BuildingStateFlags.IsWorking` (`:15-25`).
- Ingredients are consumed and output slots reserved **at cycle start**, when `currentProgress` is 0 (`:39-50`).
- Tick: progress increases by final production speed / the recipe's `realTime` × frame delta time, clamped (`:102-114`). `realTime` = `gameDays` × `TimeManager.secondsPerDay` (`Recipe.cs:227`). Progress only advances while `CanWork`.
- `CanStartProducing` additionally returns true to resume when `currentProgress` is above 0 (`:73-100`).
- On finish, results are delivered into reserved slots and progress resets (`:122-127`).
- **Remaining time (derived):** (1 − `currentProgress`) × final production time, in game days. INFERRED.

### 3.3 `GathererHub` / `Farm` with `Harvester` / `Field` modules
`GathererHub.cs`, `Harvester.cs`, `DisconnectedHarvester.cs`, `Field.cs`, CONFIRMED:
- The hub has a `ModuleOwner` with `modules`, `modulePrefab`, `maxModuleCount` (default 5), and `radius` (default 10) (`ModuleOwner.cs:120-134`). `modulePrefab` is overwritten with the first entry of the current recipe's `requiredModules` (`GathererHub.cs:126-132`). This is how to map a hub to its harvester or field prefab, rather than ExportStuff's hard-coded `HARVESTERS` table.
- `productionFactor` is max(1, `moduleCount`) (`:32`). The hub runs one production line per harvester that can start.
- `isProducing` is true when the hub has at least one module and the `IsWorking` flag is set (`:20-30`).
- `CanStartProducing()` **writes the `IsWorking` flag** (`:53-72`) and calls `TryFindSuitableHarvester` → `Harvester.CanStartWork()`, which **mutates** (see §9). Do not call it.
- The hub's displayed progress is the `currentProgress` of its first module (`:100-107`). Per-module progress is in `Module.currentProgress` (savegame-serialized, `Module.cs:62-63`).
- Ingredients (for example Water for farms, as in the ExportStuff export where `Apples` needs `Water 1`) are consumed from the **hub** storage when a module starts at progress 0 (`:109-119`).
- `productionIsDeliveredToHub` controls delivery. When false, the hub reserves and receives the results on finish (`:114-117,179-186`). When true, the harvester puts output into its own storage (`Harvester.Produce`, `:175-181`). The per-prefab values are UNKNOWN (asset data).
- **Harvester fields:**
  - `radius` (2), `maxResources` (1), `consumption` (1), `minGuaranteedProductionSpeed`, and the savegame-serialized `hub` (`GathererHub`).
  - Private `_resources` (`List<ResourceNode>`) and `_efficiency` (pollution factor, set by `HarvesterPollutionEffect`) (`Harvester.cs:11-50`).
- **Harvester speed** (`:186-206`):
  - progress delta per frame (`GetProductionDelta`) = frame delta time × the hub's final production speed / the hub recipe's `realTime` × the harvester's production speed.
  - harvester production speed (`GetProductionSpeed`): each assigned resource node contributes 1 if it still has resources, otherwise the depleted modifier (`depletedModifier`); the sum is divided by `maxResources`, multiplied by `_efficiency`, and clamped between `minGuaranteedProductionSpeed` and 1. A `Field` overrides this with `_efficiency` (`Field.cs`).
- **Resource deposits:**
  - `ResourceNode` has `resourceProduct`, `resourceAmount` (or the site's amount), `canBeHarvestedIfDepleted`, `productionSpeedModifierIfDepleted` (0.25), `reserver`, `resourceSite`, and `displayName`.
  - Spatial index: `ResourceNode.All` (quad tree). Per tile: `World.ResourceNodes.resourceNodes` (indexed by tile) / `World.ResourceNodes.GetResource(tile)` (`ResourceNode.cs`, `Building.cs:507`).
  - `Region.availableResources` (`Dictionary<ProductDefinition,int>`) and `Region.resourceSites` (`Region.cs:59-61`).
  - Each extraction calls `ResourceNode.MineResource(consumption)` in `StartWork` (`Harvester.cs:141`).
- **Field/harvester count:** `building.moduleOwner.moduleCount` / `maxModuleCount` / `canBuildModules`. CONFIRMED.
- **Water/fertility:** no fertility concept exists (grep `fertil` returns no hits). CONFIRMED absent. Water only appears in three forms. First, as a recipe ingredient. Second, as `Recipe.usedByWaterResourceHarvester`, which matches nodes with `generation.canSpawnOnWater` for placement metrics (`ResourceWithinRange.cs:55`). Third, as the Water product harvested by water wells and pumps. Fields are slowed or blocked by pollution (`HarvesterPollutionEffect`, with an optional `reversedEffect`). CONFIRMED.

### 3.4 Recipes
`Recipe.cs` (`ScriptableObject`, marked as a savegame game-data object of game-data type "recipes"), CONFIRMED:
| Member | Line | Note |
|---|---|---|
| `name` (asset name) | | Stable ID, e.g. `Adhesive`, `AppleSmoothie` |
| `Title` | 179 | Display, likely localized (HIGH) |
| `ingredients`, `result` (both `ProductList`) | 183-185 | `entries`: a list of `Product` (each with `definition` and `amount`) |
| `gameDays` | 208-223 | `_gameDays`, or the easy-chains value (rounded to 15, min 15) when `difficulty.easyChains`. Can come from `gameDaysFormula` evaluated at data load (`:245-253`) |
| `gameDaysForPriceCalculation` | 225 | Raw `_gameDays` |
| `realTime` | 227 | `gameDays` × `TimeManager.secondsPerDay` (real seconds at 1x) |
| `requiredModules` (`Building[]`) | 198-199 | Harvester/field prefab for hub recipes |
| `usedByWaterResourceHarvester`, `excludeInRecipeGraph` | 187,201-202 | |
| `tier` | 229 | Set from `TechTreeRecipeUnlock.tier` at data load (`TechTreeRecipeUnlock.cs:24-31`) |
| `firstIngredient`, `firstResult` | 204-206 | These throw when the list is empty |

`ProductList.Count(def)` (def optional; without it, all entries) sums amounts (`ProductList.cs`). CONFIRMED.

### 3.5 What blocks production (status/error mapping)
There is no single status enum. Status is the combination of flags and `BuildingRequirement` components. CONFIRMED.

`BuildingRequirementController` (`BuildingRequirementController.cs`):
- It holds `_requirements`. Every 3–5 s it sets the `RequirementsMet` flag to false if any requirement with `causesBuildingDeactivation` fails (`:211-235`).
- It raises notifications only for the **player's** buildings, and only after **5 consecutive failed checks** (`:73,236-247`). Public `notifications` (`IEnumerable<Notification>`) / `notificationCount` (`:103-105`). `Notification.specification` is a `NotificationSpecification` asset whose `.name` serves as the reason key.
- `allRequirementsMet` (`:107-120`) re-evaluates every requirement. That has side effects (see §9).

Requirement classes, all CONFIRMED (the `IsRequirementMet` semantics are listed in the right column):
| Class | Fails when |
|---|---|
| `RecipeUserRequireRecipeSelected` | No recipe |
| `BuildingRequirementRecipeUserInput` (`Delayed`, `daysDelay`) | Enabled, not working, ingredients missing, and not all modules in progress, for longer than `daysDelay` days |
| `BuildingRequirementRecipeUserStorageSpace` | For any result, the free space for that product (`FreeSpace`, ignoring pending puts) is below the result amount (output full) |
| `BuildingRequirementStorageSpace` | Overall free space (`FreeSpace` with no product, ignoring pending puts) is zero or less |
| `HubRequirementIngredientsForHarvesters` | Hub storage lacks recipe ingredients |
| `ModuleOwnerRequireModules` | `moduleCount` is 0 (no harvesters/fields) |
| `HarvesterRequirementResourcesInRange` | `Harvester.HasResources()` is false (deposit depleted or none in range) |
| `HarvesterRequirementEnabledHub` | Hub is not `CanWork` |
| `HarvesterRequirementRoadConnection` | No road path from the harvester to the hub |
| `BuildingRequirementNotPolluted` | `ThresholdBuildingPollutionEffect.isPolluted` |
| `BuildingRequirementConnectedToNetwork`, `...WarehouseInRange`, `...WarehouseReachable`, `...WarehouseCanReachShops`, `...LogisticUserCanReachWarehouse` | Logistics connectivity (not read in detail) |
| `BuildingRequirementTransportError` | Any `ITransportErrorProvider.hasErrors` |

Suggested **read-only status derivation** (INFERRED; built from fields only, without calling requirement methods):
1. `UserEnabled` flag not set → "disabled by user".
2. `recipeUser.currentRecipe` is null → "no recipe".
3. `RequirementsMet` flag not set → "blocked". The detail comes from `controller.notifications` (player buildings), or from your own pure re-check below.
4. A pure re-check uses only `storage.Count(def)` (non-mutating on `ProductSpecificProductStorage`) and `storage.GetSlots()`:
   - "missing input": any ingredient whose stored count is below the required amount.
   - "output full": slots minus the stored count of a result product is below that result's amount, an approximation that ignores reservations.
   - "no modules": `moduleOwner.moduleCount` is 0.
5. Factory with the `IsWorking` flag set and `currentProgress` above 0 → "producing".

---

## 4. Inventory and storage

### 4.1 Interface
`IProductStorage` (`IProductStorage.cs`), CONFIRMED:
- Properties: `occupiedSlots`, `freeSlots`, `isEmpty`.
- Methods: `GetSlots(def)`, `Count(def)`, `Count(def, ignorePut, ignorePull)`, `FreeSpace(...)`, `CanTake`, `CanReserve`, `GetMaxAccepted(def)`, `WriteAvailableProducts(list)`, plus mutators (`Put`, `Pull`, `Reserve`, `Clear`, `SetMaxAccepted`, ...).
- Enumeration: `IEnumerable<Product>`.

Implementations (CONFIRMED):
| Class | Used by | Model |
|---|---|---|
| `ProductSpecificProductStorage` | Factories, hubs, warehouses (HIGH, prefab data not read) | `slots` (int) is the **per-product** cap. `_storage` is a `StorageSavegameDictionary` keyed by `ProductDefinition.AssetId` (the Unity instance ID), with `StorageData` values (`pulls`, `puts`, `storage`). `_maxAcceptedMap`, `_reservations` |
| `ModuleSharedProductStorage` | Modules (harvesters/fields) | Proxies the **owner hub's** storage (`building.module.moduleOwner.storage`) |
| `SingleProductStorage` / `ReservableSingleProductStorage` / `AdvancedSingleProductStorage` | Single-product holders (INFERRED: vehicles/wagons/slots) | `slots`, `_occupied`, `_product`, `_maxAccepted` |
| `InfiniteStorage` | State/trade-type buildings (INFERRED) | `occupiedSlots` is always 0, `freeSlots` is always `int.MaxValue` |
| `ContractConsumeStorage : ProductSpecificProductStorage` | Contracts | |

### 4.2 Input vs output
- **There is no separate input/output storage.** A factory has one `ProductSpecificProductStorage`. Inputs are the products in `currentRecipe.ingredients` and outputs are those in `currentRecipe.result`. Each product is capped at `slots` (`ProductSpecificProductStorage.cs:30,172,192,326`). CONFIRMED.
- When the recipe changes, only products in the new recipe are kept (`RecipeUser.cs:463-483`). CONFIRMED.
- Accepted products: `building.logistics.acceptedProducts` and `outgoingProducts` (both savegame-serialized). These are set from the recipe on change (`RecipeUser.cs:484-493`, `BuildingLogistics.cs:39-42,140-185`). When `canAcceptAndProvideAll` is set (warehouses, INFERRED), they include all products. CONFIRMED.

### 4.3 Reading counts safely
- `ProductSpecificProductStorage.Count(def)` returns the entry's `storage` minus its `pulls` (`:138-149`). It only reads, so it is non-mutating. `GetSlots()` returns `slots`. CONFIRMED.
- Enumeration (`GetEnumerator`, `:390-400`) yields one `Product` per entry (built with `Product.Create` from the definition and stored amount), which **allocates** a new `Product` per entry plus an iterator. Resolving each definition through `GameData.instance.GetAsset<ProductDefinition>` by instance ID is a dictionary lookup. CONFIRMED.
- For low allocation, iterate `GameData.instance.GetAssetsRO(typeof(ProductDefinition))` (or the recipe's products) and call `Count(def)`. Alternatively, reflect `_storage` and walk the dictionary (`Dictionary<int, StorageData>` subclass, HIGH). INFERRED.
- **`CanReserve`, `Clear(product)` and `Reserve` call `Utils.GetSafe`, which inserts an empty `StorageData` when the key is missing** (`ProductSpecificProductStorage.cs:284,313,322`; `Utils.cs:269-278`). `CanReserve` therefore mutates the dictionary. Never call `CanReserve` / `CanReserveAll` / `CanStore` from the observer (`CanStore` → `FreeSpace` is fine on this class, but avoid it anyway for uniformity). CONFIRMED.

---

## 5. Static definitions and registries

### 5.1 `GameData` (central asset registry)
`GameData.cs`, `GameDataManifest.cs`, CONFIRMED:
- `GameData.instance` is the runtime clone of the `GameData` asset loaded from the Resources path `GDB/GDB`. It is **lazily created on first access** (`:39-54`), and in-game it already exists.
- **Do not use `GameData.editorInstance`.** It re-runs `Initialize` with all manifests enabled on every access (`:26-37`). The game itself uses it in `Recipe.FindRecipe` and `TechTreeRecipeUnlock`, but the observer must not.
- `GetAssetsRO(Type)` → `ReadOnlyList<Object>` (no copy). `GetAssets<T>(preAlloc)` copies into a list. `GetAssets<T>(DataCategory)`. `GetAsset<T>(name)` looks the asset up in the manifest's `_nameMap`, by type then asset name. `GetAsset<T>(instanceId)` (`GameData.cs:250-328`, `GameDataManifest.cs:16-26,387-445`).
- The asset **name is the stable key**. Savegames store asset references as asset-name strings (`GameDataManifest.cs:415-449`). Mods register through `ContentLoader` → `GameData.RegisterModAsset` and can override by name (`ContentLoader.cs:61-73`).

### 5.2 `ProductDefinition`
`ProductDefinition.cs` (`ScriptableObject`, game-data type "products"), CONFIRMED:
- `name` is the stable asset ID (e.g. `Apples`, `Chemicals`). `productName` is the display name (localized, HIGH).
- `category` (`DataCategory`, possibly overridden by `categoryProvider`) / `productCategory` (`ProductCategory`) / `groupName`.
- `tags` (`ProductTag[]`), `endGameProduct`, `disableContracts`, `demandModifier`.
- `price` (a `Formula`): the base price formula. In the ExportStuff export it reads as: price = (ingredients value + (upkeep / 30) × recipe days) / recipe output. `price.formula` is the formula string.
- `AssetId` (int) returns the Unity instance ID. It is **session-only** (`:215,264-269`) and is the key used by storages.

`ProductCategory : DataCategory` adds `priceMultiplier` and `growthMultiplier` (from `ProductCategoryModifierInfo`) (`ProductCategory.cs`). `DataCategory` has `parentCategory`, `categoryName`, `categoryGroupName`, `uiOrder`, `visibleInProductionPanel`, and `IsInCategory()`. CONFIRMED.

**Product tier:** `ProductDefinition` has no tier field. A tier is available through the producing recipe: the `tier` of the first recipe returned by `RecipeDatabase.GetRecipes(p)`. INFERRED. Category likely encodes raw/T1/T2... (UNKNOWN without asset dump).

**Prices (runtime):** `ManagerBehaviour<GlobalMarket>.instance.GetPricingInfo(def)` → `ProductPricingInfo` (`value`, `price`, `modifier`, `trend`). Final price for an actor (`GetFinalPrice(def, actor)`) = `price` × (1 + `modifier`) × the actor's price modifier (`GlobalMarket.cs:56-71`). `GetPricingInfo` logs an error when the product is missing. CONFIRMED. These belong to the economy topic; details live in that note.

### 5.3 Building definitions
- Building prefabs are `GameData.instance.GetAssetsRO(typeof(Building))` (used by `RecipeDatabase.cs:94`, `TechTreeAgent.cs:84`, etc.). CONFIRMED.
- Per prefab fields: `name` (ID), `buildingName` (display, localized), `description`, `baseCost`, `tags` (`BuildingTag[]`), `category` (`DataCategory`/`BuildingCategory`), `buildPermit`/`buyPermit`, `uiOrder`. Read components through `GetComponent<RecipeUser>().availableRecipes`, `GetComponent<ModuleOwner>().modulePrefab/maxModuleCount/radius`, `GetComponent<Upkeep>()`, `GetComponent<ProductSpecificProductStorage>().slots`. CONFIRMED (fields). The values themselves are UNKNOWN (asset data).
- Named tags (`BuildingTag.cs`): `Decoration`, `Factory`, `Farm`, `Gatherer`, `Industrial`, `Logistics`, `Module`, `Module Owner`, `Recipe User`, `Residential`, `Shop`, `Urban`, `Wholesaler`, `PollutionManagement`. Use `building.HasTag(tag)` for classification. CONFIRMED.

### 5.4 `RecipeDatabase` (product graph)
`RecipeDatabase.cs`, CONFIRMED:
- Singleton: `RecipeDatabase.instance` is the first `RecipeDatabase` asset registered in `GameData.instance`, initialized on game-data load.
- `GetRecipes(product)` (recipes producing it), `GetRecipesWhereProductIsUsed(product)`, `GetOriginsOfRecipe(recipe)` (building prefabs that can run it), `GetOriginsOfProduct(product)`. All return a `ReadOnlyList` wrapper (struct; a default-initialized value has `notNull` false).
- Recipes with `excludeInRecipeGraph` are excluded.

### 5.5 Enumerating everything (ExportStuff pattern)
`research/references/ExportStuffMod/src/ExportStuffMod.cs` runs in `Mod.OnAllModsLoaded()`. CONFIRMED. What it exports:
- From `GameData.instance.GetAssets<Recipe>()`: recipe `name`, `gameDays`, and for each ingredient/result entry the definition `name`, the `amount`, and the definition's `price.formula`.
- From `GameData.instance.GetAssets<Building>()`: building `name`, `baseCost`, and the names of the `availableRecipes` of its `recipeUser` (when it has one).
Improvements over that mod:
- Use `GetAssetsRO` (no list copy).
- Map hub → harvester through the first entry of `recipe.requiredModules` instead of the hard-coded `HARVESTERS` dictionary.
- Read `recipe.gameDays`, which respects easy-chains; `gameDaysForPriceCalculation` gives the raw value.
- Static definitions can be dumped once at world load and cached. They only change when mods load. CONFIRMED.

---

## 6. Statistics and history

| Source | Scope | Members | Label |
|---|---|---|---|
| `RecipeUser` | Per building | `producedThisMonth`, `producedLastMonth`, `totalProduced`, `currentProducedAverage` (10 months), `GetProducedInRange/GetConsumedInRange(def, from, to)` (about a 1–2 year window, pruned at year end) | CONFIRMED |
| `BuildingAnalysis` | Per building | `GetAllAnalysisItemDefs()`, `GetValues(def)`, `GetOverallValue(def)`, `GetLastMonthValue(def)`, `GetLastYearValue(def)`. Items: `recipeUser.productionAnalysisItemDef` (units per cycle), `efficiencyAnalysisItemDef` (monthly % uptime), `Upkeep.upkeepAnalysisItemDef` (monthly upkeep), `uptimeAnalysisItemDef` (% days up) (`BuildingAnalysis.cs:122-205`, `RecipeUser.cs:40-44,338,533`, `Upkeep.cs:36-40,164-168`) | CONFIRMED |
| `ProductionStatsTracker` (manager) | **Player only**, per product | `GetProducedAmount`, `GetProductionCost`, `GetDistributionCost`, `GetTotalCost`, `GetSoldCount`, `GetPriceSold`, `GetProfit`, `GetMarkup`, `GetUsedCount`, `GetRecipeUsersForProduct`. These are caches refreshed daily for the `dateFrom` to `dateTo` window (shifted 30 days each month) (`ProductionStatsTracker.cs:115-245,352-540`) | CONFIRMED |
| `Upkeep` | Per building | Current month accrued `upkeep`, `daysUp` (private) | CONFIRMED |

`ProductionStatsTracker.SetStatsTimeRange` mutates the window used by the UI. Do not call it. `GetIngredientsValue` and `GetProductValue` populate caches lazily, which is benign. CONFIRMED.

---

## 7. Events useful for incremental tracking (subscribe only if necessary)
- `BuildingManager.OnBuildingRegistered` / `OnBuildingDestroyed` / `OnCreateOrDestroyBuildingOnTile` (public `Action` fields, combined with `Delegate.Combine` by the game itself, `ProductionStatsTracker.cs:262`).
- `ActorBuildingCollection.onBuildingAdded/onBuildingRemoved`.
- `Building.onDemolish`, `Building.onOwnerChanged`.
- `RecipeUser.onProductProduced`, `RecipeUser.onRecipeChanged`.
- `ModuleOwner.onModuleBuilt/onModuleRemoved`.
- `BuildingEfficiency.onEfficiencyChanged`.

Subscribing mutates the delegate lists and keeps objects alive. The safer choice for an observer is polling on the main thread. INFERRED.

---

## 8. Unknowns
- Per-prefab numeric values: storage `slots`, `productionSpeed`, `maxModuleCount`, `nameFormat`, `productionIsDeliveredToHub`, efficiency arrays, upkeep percentages. These are asset data that needs an asset dump (UnityPy). UNKNOWN.
- Exact warehouse storage class and capacity semantics (assumed `ProductSpecificProductStorage` with `canAcceptAndProvideAll`). INFERRED.
- Whether `Recipe.Title`, `ProductDefinition.productName` and `Building.buildingName` are all localized (`GenericLocalizer` reflection over string fields not read in detail). HIGH.
- Serialization format of `StorageSavegameDictionary` (instance-ID keys are not stable across sessions, so it must map names on save). Not needed for live reads. UNKNOWN.
- `ManagerBehaviour<T>.instance` calls `FindObjectOfType` when the instance is null and is only frame-guarded on the main thread (`ManagerBehaviour.cs`). Off the main thread it can call Unity APIs. Main thread only. CONFIRMED risk.
- Production-blocking reasons for **AI** buildings: notifications are only generated for the player, so a re-derivation from fields is needed (§3.5). CONFIRMED.

---

## 9. Unsafe for a read-only observer (do not call)

| Member | Why | Ref |
|---|---|---|
| `RecipeUser.currentRecipe` **setter**, `SetInitialRecipe`, `WriteMySettingsTo` (all `ICopyableSettings`) | Clears storage and reservations, updates logistics, fires events, may rebuild modules | `RecipeUser.cs:126-139,303-325,463-499`; `GathererHub.cs:134-171` |
| `RecipeUser.CanStartProducing` / `GathererHub.CanStartProducing` | The hub version writes the `IsWorking` flag and calls `Harvester.CanStartWork` | `GathererHub.cs:53-72` |
| `Harvester.CanStartWork`, `Harvester.HasResources`, `HarvesterRequirementResourcesInRange.IsRequirementMet` | Remove dead nodes from `_resources`, reserve resource nodes (`ResourceNode.Reserve`) | `Harvester.cs:76-124,208-246` |
| `Harvester.StartWork/StopWork/Produce` | Mine resources, dispatch events | `Harvester.cs:126-184` |
| `BuildingRequirementController.allRequirementsMet` / `Check()` / any `IsRequirementMet()` | `BuildingRequirementDelayed` updates `_lastCheckDate`; harvester checks mutate; `Check()` sets flags and notifications | `BuildingRequirementDelayed.cs`, `BuildingRequirementController.cs:107-249` |
| `IProductStorage.CanReserve` / `CanReserveAll` (StorageHelper) / `Reserve` / `Put` / `Pull` / `Clear` / `SetMaxAccepted` | `GetSafe` inserts entries; the others mutate | `ProductSpecificProductStorage.cs:169-330`, `Utils.cs:269` |
| `IProductStorage` enumeration | Allocates a `Product` per entry plus an iterator (GC pressure on an 8 GB Boehm heap) | `ProductSpecificProductStorage.cs:390-400` |
| `GuidMapper.GetGUIDForObject(obj)` | Mints and stores a new GUID if absent. Prefer `TryGetObjectByGUID`, or accept the benign insert | `GuidMapper.cs:38-47` |
| `GameData.editorInstance` | Re-initializes manifests on each access | `GameData.cs:26-37` |
| `Building.SetBuildingName`, `SetStateFlag`, `ChangeOwner`, `OnDemolish`, `FakeClick`, `OrientCameraOnMe`, `UpdateBuilding` | Mutate state, UI, or camera | `Building.cs` |
| `BuildingEfficiency.SetEfficiencyIndex`, `UpdateEfficiencyIcon` | Mutates state, fires events | `BuildingEfficiency.cs:131-142` |
| `BuildingNameGenerator.Initialize/UpdateBuildingName` | Rewrites `buildingName` / `index` | `BuildingNameGenerator.cs` |
| `ProductionStatsTracker.SetStatsTimeRange` | Changes the UI stats window and recomputes caches | `ProductionStatsTracker.cs:240-245` |
| `ModuleOwner.StartBuildNode` | Starts build mode | `ModuleOwner.cs:171-186` |
| Lazy getters (`building.storage/recipeUser/...`, `BuildingEfficiency.efficiency`, `RecipeUser.productStorage`) | Benign caching, but the first call runs `GetComponent` (main thread only) | `LazyComponentRef.cs` |
| `RecipeUser.GetProducedInRange` / `ProductInfoCollection.GetProductInfo` | Use the static `ListPool` (main thread only), allocate on first use | `ProductInfoCollection.cs:62-76` |
| `PolluterBuilding.pollutionAmount`, `Recipe.OnGameDataLoaded`, any `Formula.Evaluate` | Formula evaluation (Jace) may allocate. INFERRED | `PolluterBuilding.cs:49` |
| `RecipeUser.knownRecipes`, `AverageStatistic.currentAverage` | LINQ allocations (minor) | `RecipeUser.cs:107-118` |

**Safe reads (plain fields/properties, main thread):**
- `Building`: `buildingName`, `prefab`, `prefab.name`, `tile`, `rotation`, `region`, `settlement`, `buildingOwner`, `buildingStateFlags`, `baseCost`, `paidToBuildAmount`, `tags`.
- `RecipeUser`: `currentRecipe` (getter), `producedThisMonth/LastMonth/totalProduced`, `productionSpeed`, `GetFinalProductionSpeed/Time` (pure arithmetic plus modifier lookups; HIGH that the modifier getters are pure, not verified).
- `Factory.currentProgress`, `Module.currentProgress`, `ModuleOwner.modules/moduleCount/maxModuleCount`.
- `ProductSpecificProductStorage.Count(def)`, `GetSlots()`, `slots`.
- `BuildingEfficiency.efficiencyIndex`, `upkeepModifier`.
- `Upkeep.totalMonthlyUpkeep/totalActiveMonthlyUpkeep` (iterate a delegate list; pure).
- `BuildingLogistics.acceptedProducts/outgoingProducts`.
- `BuildingRequirementController.notifications/notificationCount`.
