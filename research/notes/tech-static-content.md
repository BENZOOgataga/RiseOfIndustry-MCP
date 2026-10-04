# Tech tree / research + static content system (Rise of Industry 1, Unity 2018.4 Mono)

> **Errata from the asset dump (2026-10-03, CONFIRMED, see `notes/static-dump.md`):**
> - `TechTreeManagerConfig`: `maxEnqueuedUnlocks` = 9 (code default 8), `efficiencyValues` = [0, 1, 2.5, 10, 25, 50]. Research Cost = 3333.333333 × efficiency per day; Research Time = (60 + tier³ × 60) / efficiency days (`notes/static-dump.md`).
> - For "the player" use `Player.humanPlayer`, not `Player.activeActor` (see `company-finance-time.md` §1.2).

Scope: static reading of the ILSpy output at
`research/_local/decompiled/Assembly-CSharp/ProjectAutomata/` (paths below are relative to that folder
unless prefixed `CCM/` = `../ProjectAutomata.ContentCreationModels/`). The only game file read was the
StreamingAssets scenario JSON, read-only.

Confidence labels:
- **CONFIRMED**: read in the source.
- **HIGH**: strongly implied by the source.
- **INFERRED**: a reasoned guess.
- **UNKNOWN**: not determined.

---

## 0. TL;DR for the MCP observer

| Need | Where (runtime) | Confidence |
|---|---|---|
| Global definition DB | `GameData.instance` (static; clone of `Resources/GDB/GDB`) -> `GameDataManifest` maps by type / name / group / category | CONFIRMED |
| Look up a definition | `GameData.instance.GetAsset<T>(string name)` (key = Unity `Object.name`) | CONFIRMED |
| Enumerate definitions safely | `GameData.instance.GetAssetsRO(typeof(T))` (returns a read-only view, no pooling) | CONFIRMED |
| Stable ID | the Unity asset `.name` (for Buildings, the **prefab** name: `building.prefab.name`) | CONFIRMED (savegames use it) |
| NOT stable | `GetInstanceID()` / `ProductDefinition.AssetId` / `GameData.GetAsset(int)` | CONFIRMED |
| Tech tree manager | `ManagerBehaviour<TechTreeManager>.instance` (`isEnabled`, `config`) | CONFIRMED |
| Player research state | `Player.activeActor.techTree` (`ITechTreeAgent`) -> `.research` (`TechTreeAgentResearchState`) | CONFIRMED |
| Unlock state | `ITechTreeAgent.IsUnlocked(unlock)` / `GetUnlocked()` / `IsBuildingUnlocked` / `IsRecipeUnlocked` | CONFIRMED |
| Queue / active / progress | `research.queue`, `research.activeUnlock`, `research.researchProgress`, `GetResearchProgress(u)`, `remainingTime`, `remainingCost` | CONFIRMED |
| Difficulty (economy) | `ManagerBehaviour<GameParametersManager>.instance.difficulty` (`DifficultyParameters`), per-actor via `GetDifficultyParameters(actor)` | CONFIRMED |
| English names while the game is in French | not in the definition fields (they are overwritten in place). Use localization key `<TypeName>.<assetName>.<field>` (lower-case, no spaces) against the en-US `LanguageData` assets | CONFIRMED mechanism; access path HIGH |

---

## 1. Tech tree data model (static definitions)

### 1.1 `TechTreeUnlock` (abstract ScriptableObject): the research node
`TechTreeUnlock.cs`
- L10: `TechTreeUnlock` is an abstract ScriptableObject that implements `IGameDataLoadedListener`. It does **not** implement `IGameDataObject`, so its group is the default `"STD"` (`GameDataManifest.cs:274`). CONFIRMED
- Fields (CONFIRMED, L30-55):
  - `unlockedByDefault` and `isTeaser` (bools; a teaser can never be unlocked, see `TechTreeAgent._Unlock` L216)
  - `icon` (ThemedSVGAsset), `unlockName` (string; display name, localized in place), `description` (string)
  - `requiredUnlocks` (array of `TechTreeUnlock`): the **prerequisites**
  - `includedUnlocks` (array of `TechTreeUnlock`): unlocked together with this one (`TechTreeAgent.Unlock` L237-248)
  - `researchCost` (`Formula`) = **daily money cost**, `researchTime` (`Formula`) = **days** (see section 2.3)
  - `tier` (int)
  - `trees` (list of `TechTreePlacement`, each holding a `tree`, an int `column`, and `overrideIcon`, `overrideName`, `overrideDescription`) (L14-26). A node can appear in several trees; its column is per tree.
- The abstract method `Unlock`, which receives the unlocking `TechTreeAgent` (L63), applies the effect.
- `ToString` returns `unlockName` (L121). Note: this is the localized display name, not the ID.
- ID = `unlock.name` (the Unity asset name). The saved queue and progress write each unlock's `.name` (`ResearchQueue.cs:34`, `TechTreeAgentResearchState.cs:16`). CONFIRMED

Concrete subclasses (all CONFIRMED):

| Class | File | Effect field(s) | `Unlock()` does |
|---|---|---|---|
| `TechTreeBuildingUnlock` | `TechTreeBuildingUnlock.cs` | `building` (Building), `category` (BuildingCategory) | calls the unlocker's `UnlockBuilding` for the building and/or every Building whose category is in that category (L15-32) |
| `TechTreeRecipeUnlock` | `TechTreeRecipeUnlock.cs` | `recipes` (Recipe array) | calls the unlocker's `UnlockRecipe` for each recipe. In `OnGameDataLoaded` it also copies the unlock's `tier` into each recipe's `tier` (L23-31) |
| `TechTreeBuildingPriceUnlock` | `TechTreeBuildingPriceUnlock.cs` | `buildings` (Building array), `percentage` (float) | calls `SetBuildingPriceOverride` for each building with price = base cost × (1 − percentage) (L19) |
| `TechTreeGenericUnlock` | `TechTreeGenericUnlock.cs` | `overrides` (TechTreeGenericUnlock array) | calls `TechTreeUser.OnGenericUnlock` on each registered user (building) of that agent. The effect is data-driven (see 1.4) |

### 1.2 `TechTree` (one tab/tree) and `TechTreeCategory`
- `TechTree.cs:8` is a ScriptableObject with `IGameDataObject`. Fields: `techTreeName` (display), `icon`, `TechTreeCategory category`, `int uiOrder`. At runtime it has `unlocks` (built in `OnGameDataLoaded` by keeping every `TechTreeUnlock` whose `ContainedByTree` check passes for this tree) and `tierCount` (L30-43). CONFIRMED
- `TechTreeCategory.cs:10` extends `DataCategory`. It has `trees` (sorted by `uiOrder`) and `unlocks` (union of its trees' unlocks), plus `CountUnlocked(agent)` (L61). CONFIRMED
- Init order is set by the order value of the `GameDataLoadedListener` attribute: TechTreeUnlock 0, TechTree 1, TechTreeCategory 2. Formula and RecipeDatabase use `int.MinValue` (`GameDataManifest.CompareListeners` L292). CONFIRMED

### 1.3 `TechTreeManagerConfig` (ScriptableObject, referenced by the manager)
`TechTreeManagerConfig.cs` (CONFIRMED). The default values are in code; the asset may override them (INFERRED):
- `maxEnqueuedUnlocks`: 8
- `efficiencyValues`: 0, 1, 2.5, 10, 25, 50, indexed by the research "efficiency" slider
- `efficiencyUnlocks` (list of `TechTreeUnlock`): slider level i needs the i-th entry of this list (`CanSetEfficiencyIndex` in the research state, L193)
- `researchCostsBillCategory`, `refundBillCategory` (MoneyBillCategory), `researchCompletedNotification`

### 1.4 Generic unlock effects (reflection-based modifiers on buildings)
- `TechTreeUser` (MonoBehaviour on building prefabs, `TechTreeUser.cs`) collects `TechTreeGenericUnlockEffect` components (L86-95). When a generic unlock fires, it runs the effects' `Execute`, passing the building's GameObject (L122-142).
- `TechTreeGenericUnlockEffectAction` (`TechTreeGenericUnlockEffectAction.cs`): `actionType` (ADD, SUBTRACT, ADD_PERCENTAGE, SUBTRACT_PERCENTAGE, FORMULA, INVOKE_METHOD), `componentType` (a string), `fieldPropertyName`, `operatorValue`, `formula`, `methodName`. It **writes component fields through reflection** (L103-133). Revert copies the prefab values back (L169-208). CONFIRMED
- Newly built buildings get effects through the prefab's `TechTreeUser` component, when present, via `TryApplyingUnlockEffects` (`Building.cs:276`). CONFIRMED
- Consequence for an observer: "what does generic unlock X do" can only be answered by reading `TechTreeGenericUnlockEffect.actions` on prefabs and global `TechTreeEffectCollection`s (`TechTreeUser.globalEffects`). There is no central table. HIGH
- Other consumers of unlocks:
  - `TechTreeLogistics` (`TechTreeLogistics.cs`): network tier, speed and cost multipliers, tunnels and bridges, vehicle capacity overrides keyed by `TechTreeGenericUnlock`
  - `UnlockableModifier` (`UnlockableModifier.cs`): building slider level i is gated by the i-th entry of `unlocks`

---

## 2. Research runtime state

### 2.1 Manager singleton: `TechTreeManager`
`TechTreeManager.cs` (CONFIRMED)
- L8-9: `TechTreeManager` derives from `ManagerBehaviour<TechTreeManager>` and carries the `[SavegameManagerObject]` attribute.
- `config` (TechTreeManagerConfig), `isEnabled` (bool, `[SavegameSerialized]`). `isEnabled` is set from `WorldParameters.techTree` at new game (`WorldInitializer.cs:117`). When it is false, every agent is force-unlocked at world start (L149-155) and research does not tick (`TechTreeAgent.Update` L311-317).
- `_agents: Dictionary<ITechTreeAgent, List<TechTreeUser>>`, filled from `ActorManager` actor registration.
- Reverse lookups built in `OnLateWorldBecameReady` (L131-185):
  - `GetUnlockForBuilding(Building prefab)` -> TechTreeBuildingUnlock
  - `GetUnlocksForRecipe(Recipe)` -> list of TechTreeRecipeUnlock
  - `GetTechTreeForUnlock(u)` -> first tree
- `isInitialized` plus `event initialized`.

### 2.2 Per-actor agent: `TechTreeAgent` (ActorComponent)
`TechTreeAgent.cs` (CONFIRMED). Every player (human and AI) is an actor with `ITechTreeAgent`, reached through `actor.techTree` (`Actor.cs:81`). The human player is `Player.activeActor` / `Player.humanPlayer` (`Player.cs:14-16`).
- Private state: `_unlockStates: Dictionary<TechTreeUnlock,bool>`, `_buildingUnlockState`, `_recipeUnlockState`, `_buildingPrices: Dictionary<Building,double>` (L24-30).
- Saved: `_research` (TechTreeAgentResearchState); `_saveUnlockStates: Dictionary<SavegamePrefabIdentifier,bool>`, written in `OnSavegameSerialize` with key `(AssemblyQualifiedName of TechTreeUnlock, unlock.name)` (L319-322).
- On load, it replays the unlocks in topological order of `requiredUnlocks` (L324-358).
- Read API (safe):

  | Member | Notes |
  |---|---|
  | `IsUnlocked(u)` | logs an error if `u` is unknown (L203-206) |
  | `GetUnlocked(preAlloc)` | returns a pooled list; see 6 |
  | `IsBuildingUnlocked(prefab)`, `IsRecipeUnlocked(r)` | |
  | `GetBuildingCost(b)` | includes price-unlock overrides |
  | `LockedByDemoMode(u)` | |
  | `isInitialized`, `research` | |
  | `freeUnlocks`, `initialEfficiency`, `maxUnlockableTierInDemo` | prefab config |

- `FullyUnlockedTechTree.cs` is an alternative `ITechTreeAgent` that returns true for everything. Used for some actors (State/AI?), INFERRED.

### 2.3 `TechTreeAgentResearchState`: queue, progress, costs
`TechTreeAgentResearchState.cs` (CONFIRMED)
- Saved fields: `_queue: ResearchQueue`, `_expenses: ResearchExpensesHandler`, `_unlockPoints`, `_efficiencyIndex`, `_researchProgress: ResearchProgress` (a `SavegameDictionary<TechTreeUnlock,float>` keyed by `unlock.name`, values 0..1) (L36-49).
- Non-saved: `_dailyProgress`, `_costToday`, `speedModifier` (tutorial only).
- Public reads:

  | Member | Meaning |
  |---|---|
  | `queue` | `List<TechTreeUnlock>`; this is the **live list**, see 6 |
  | `activeUnlock` | the first queue entry (`ResearchQueue.cs:16-26`) |
  | `researchProgress`, `smoothResearchProgress` | progress of the active unlock (smooth adds `_dailyProgress`) |
  | `GetResearchProgress(u)`, `HasResearchProgress(u)` | progress is kept for unlocks that are no longer active |
  | `remainingTime` | days |
  | `remainingCost`, `currentResearchCost` | money (current = spent so far on the active unlock) |
  | `unlockPoints` | free instant unlocks left |
  | `efficiencyIndex`, `efficiency` | the `config.efficiencyValues` entry at `efficiencyIndex` |
  | `GetResearchTime(u)`, `GetResearchDailyCost(u)`, `GetResearchCost(u)` | see formulas below |
  | `CanResearchUnlock(u)`, `CanUnlockWithPoints(u)`, `IsEnqueuedForResearch(u)` | state predicates |
  | `GetUnlockChain(u, list)` | prerequisites not yet unlocked or queued, then `u` (L378-389) |

- Formulas (L329-356):
  - research time (game days) = the unlock's `researchTime` formula evaluated at (efficiency, tier) / ((1 + `speedModifier`) × the actor's research-speed modifier, `GetResearchSpeedModifier`). Infinite if efficiency is 0.
  - daily cost (money per day) = the unlock's `researchCost` formula evaluated at (efficiency, tier).
  - `GetResearchCost` (UI total) = daily cost at efficiency 1 × research time at efficiency 1 / (1 + `speedModifier`).
  - The formula arguments are exactly `efficiency` and `tier` (`TechTreeUnlockFormulaArguments.cs:11-15`).
  - The formula text is in `Formula.formula`, compiled with Jace (`Formula.cs`).
- **There is no "research points" currency.** Research costs **money per day plus time**. "Unlock points" are a separate small pool of instant free unlocks (`freeUnlocks`, default 3) limited to unlocks whose tier is at most `_maxTierUnlockableWithPoints` (1; L32, L373-376). CONFIRMED
- Progression (CONFIRMED):
  1. `TechTreeAgent.Update` -> `research.Tick`, only when the manager `isEnabled` and the player's HQ is built (`hqBuilt`, L143, L358-371).
  2. Each tick, `_dailyProgress` grows by `Time.deltaTime` / (`secondsPerDay` × research time). `_costToday` keeps the larger of its current value and the daily cost.
  3. On `TimeManager.onDayEnd`, `AdvanceResearch` commits progress into the active unlock's entry of `_researchProgress`, then `PayResearchCost` sends `_costToday` from the actor to `State` through `MoneyManager.EasyMoneySend` with `researchCostsBillCategory` (L162-175; `ResearchExpensesHandler.cs:13-25`).
  4. Once progress reaches 1, `CompleteResearch` does: notification, analytics, `_agent.Unlock(active)`, remove the progress entry, pop the queue (L405-421).
  - Assumed (INFERRED): `Time.deltaTime` is scaled by game speed.
- `ResearchUnlock(u)` (the player action, L234-283) walks the chain. Each element is unlocked for free in the scenario editor, unlocked with points if allowed, or else appended to the queue (capped by `maxEnqueuedUnlocks`). Prerequisites therefore do **not** need to be done before you click; they are auto-queued.
- Cancel: `CancelResearch(u)` removes `u` and its queued descendants (`ResearchQueue.Remove` L67-96). The "refund" only resets the counter (`ResearchExpensesHandler.cs:27-30`; no money is returned). CONFIRMED

### 2.4 Deriving the node state (for an MCP `get_tech_tree`)
Suggested state, computed per unlock `u` for the agent `a` (`Player.activeActor.techTree`):
- `unlocked`: `a.IsUnlocked(u)`
- `teaser`: `u.isTeaser` (never unlockable)
- `demo_locked`: `a.LockedByDemoMode(u)`
- `researching`: `a.research.activeUnlock` is `u`
- `queued`: `a.research.IsEnqueuedForResearch(u)`; position = index of `u` in `a.research.queue`
- `available_now`: not unlocked, not teaser, and every `requiredUnlocks` entry is unlocked
- `researchable_via_chain`: `a.research.CanResearchUnlock(u)` (true even when prerequisites are missing)
- `partial_progress`: `a.research.GetResearchProgress(u)`

The UI uses the same predicates (`TechTreeUnlockUIViewModel.cs:43-133`). CONFIRMED (except the `available_now` definition, which is mine).

---

## 3. Static content system (definitions, registries, IDs)

### 3.1 `GameData`: the global database
`GameData.cs` (CONFIRMED)
- L9-10 ScriptableObject at `Resources/GDB/GDB` (L12, L62: loaded with `Resources.Load` from the path `GDB/GDB`).
- `GameData.instance` (L39-54): a lazily created **runtime clone** (a `Clone` of the loaded asset, then `Initialize` without enabling all manifests). Use this one.
- `GameData.editorInstance` (L26-37) is the original asset with all manifests enabled. It is also used at runtime by the localization table.
- It holds `commonManifest` (always on) plus `_manifests` (per DLC or GameModule). `EnableGameModuleManifests(module)` swaps manifests in and out (L145-160). The set of registered definitions therefore **depends on the current GameModule**. CONFIRMED
- Query API (CONFIRMED, L250-340):

  | Method | Notes |
  |---|---|
  | `GetAssetsRO(Type)`, `GetAssetsRO(Type, group)`, `GetAssetsRO(DataCategory)` | read-only views of the internal lists (preferred) |
  | `GetAssets<T>()`, `GetAssets(Type)` | return a **pooled** list (`ListPool<T>.GetIfNull`); the caller should `ListPool<T>.Return` |
  | `GetAsset<T>(string name)`, `GetAsset(Type, name)`, `TryGetAsset<T>(name, out)` | **lookup by name** |
  | `GetAsset(int instanceId)` | ID = `IGameDataAssetWithId.AssetId` or `GetInstanceID()`; session-only |
  | `GetAssetTypes()` | every registered type |

### 3.2 `GameDataManifest`: the indexes
`GameDataManifest.cs` (CONFIRMED)
- Indexes (L14-26): `_typeMap: Type->List`, `_groupMap: group->Type->List`, `_nameMap: Type->(name->Object)`, `_categoryMap: DataCategory->List`, `_instanceIdMap: int->Object`.
- An asset is registered under **every type in its inheritance chain** up to MonoBehaviour or ScriptableObject (`DetectTypeMapping` L280-290). So a `GetAsset<TechTreeUnlock>` lookup by name also finds a `TechTreeRecipeUnlock`.
- The name map is keyed by `asset.name`, and `Dictionary.Add` throws on duplicates. Names are therefore unique per type (HIGH).
- `OnGameDataLoaded()` (L115-162) calls the pre-loaded listeners, then the `IGameDataLoadedListener`s sorted by attribute order, then rebuilds `_instanceIdMap`.
- Re-run on every `GameModuleRepository.SetModule` (`GameModuleRepository.cs:315-329`). Derived caches such as `TechTree.unlocks` are then rebuilt. CONFIRMED

### 3.3 Boot and load order
`GameBootstrapper.cs:51-91` (CONFIRMED):
1. `ModLoader.InitializeModdingApi()` (Harmony)
2. `GameData.instance.EnableAllOwnedManifests()`
3. `ModLoader.LoadAssets()`
4. `ModLoader.LoadMods(...)`
5. `GameData.instance.DisableAllManifests()`
6. DLC ownership cache
7. load the next scene

At new game: `WorldInitializer.InitializeNewGame` -> `GameModuleRepository.SetModule(module)` -> enable that module's manifests -> `GameData.OnGameDataLoaded()` -> re-localize (`I18n.ReloadCurrentLanguage`) (`WorldInitializer.cs:106`; `GameModuleRepository.cs:315-329`).

### 3.4 Mod and JSON content pipeline
- `ModLoader` (`ModLoader.cs`): searches the user mods path `"Mods"`, then `StreamingAssets/DevMods` (L32, L282-290), then Steam Workshop. Each mod is a folder with `desc.json`, plus optional `code/`, `assets/` and `content/` (L398-415). Every `*.json` in `content/` goes to `ContentLoader.LoadContent` (L449-461). CONFIRMED
- `ContentLoader.LoadContent` (`ContentLoader.cs:35-95`), CONFIRMED:
  - JSON shape: `{ "type": "<FullTypeName>", "object": {...model fields...}, "override": "<existing asset name>"?, "components": [...]? }`
  - `type` is resolved by `ReflectionHelper.GetTypeFast` and may be either the entity type (e.g. `ProjectAutomata.ScenarioDefinition`) or the model type. `ContentCreationModelRegistry` maps between them.
  - `object` is deserialized with Newtonsoft into the model. `Validate()`, then `Construct()` creates the ScriptableObject, named from the model's `name` field (or `id` for scenarios). The result goes to `GameData.instance.RegisterModAsset` (name-only first).
  - `override` **deregisters** the existing asset of that name.
  - `FinishLoadingMod()` (L97-108) runs `model.LoadData()` (resolves cross-references **by name** through `ContentCreationUtil.ResolveGameDataAsset<T>`, which looks the name up with `GameData.instance.GetAsset<T>`, `ContentCreationUtil.cs:235-242`), then `FlushNameOnlyObjects()`.
  - Mod assets go into **commonManifest** (`GameData.RegisterModAsset` L227-231), so they are present in every module.
- `ContentCreationModelRegistry` (`ContentCreationModelRegistry.cs`): a static reflection scan of the assembly for the `ContentCreationModel` attribute (its `entityType` gives the entity->model mapping) and the `ContentCreationComponentModel` attribute (its `componentType`). CONFIRMED
- `AssetLoaderRegistry` (`AssetLoaderRegistry.cs`): a static map from file extension to `IAssetLoader`, built by scanning for the `AssetLoader` attribute (file ending and order) (SVG, PNG, JPG, audio...). It is used for mod `assets/` (raw icons and so on), which are resolved by `ContentCreationUtil.ResolveAsset<T>` -> `ModLoader.assets.GetObject<T>`. CONFIRMED
- Models found in `ProjectAutomata.ContentCreationModels/` (206 files). Relevant ones, all CONFIRMED:
  - `CCTechTreeModel` (`displayName`, `svgIcon`, `category`, `uiOrder`)
  - `CCTechTreeCategory`
  - `CCTechTreeUnlockModel<T>`: `displayName` -> unlockName, `description`, `svgIcon`, `isTeaser`, `unlockedByDefault`, `requiredUnlocks[]`, `includedUnlocks[]`, `researchCost` (Formula name), `researchTime` (Formula name), `tier`, `trees[{tree, column}]` (`CCM/CCTechTreeUnlockModel.cs:26-63`)
  - `CCTechTreeRecipeUnlockModel` (`recipes[]`), `CCTechTreeBuildingUnlockModel`, `CCTechTreeBuildingPriceUnlockModel`, `CCTechTreeGenericUnlockModel`, `CCTechTreeEffectCollectionModel`, `CCCTechTreeUserModel`, `CCCTechTreeGenericUnlockEffectModel`
  - `CCProductDefinitionModel` (`displayName`, `tags[]`, `category`, `isEndgameProduct`, `disableContracts`, `icon`, `svgIcon`, `priceFormula`)
  - `CCRecipeModel` (`gameDays`, `ingredients`, `result`, `requiredModules`, `displayName`...)
  - `CCScenarioDefinitionModel` (`CCM/CCScenarioDefinitionModel.cs`)
  - `CCSettlementNameList` (`displayName`, `uiOrder`, `names[]`), `CCAiPlayerNameList`
  - `CCBuildingModel`, and the `CCC*` component models
  - Every `CCSingleSOModel` has a public string field `name` that becomes the created asset's `.name` (`CCM/CCSingleSOModel.cs:9-28`).
- Only shipped JSON (read-only check): `StreamingAssets/DevMods/Scenarios/` with `desc.json` (`"Default Scenarios"`, version 230 / "2.3.0") and two scenario files, `Farmland-9bac...json` and `Waterworld-1304...json`. Their `"type": "ProjectAutomata.ScenarioDefinition"`. The core content (products, recipes, tech tree) is **not** shipped as JSON; it is serialized Unity assets in the GDB. CONFIRMED (only 3 JSON files exist under DevMods).

### 3.5 Main definition types (identity summary)

| Type | Kind | Display field (localized in place) | Stable key | Notes |
|---|---|---|---|---|
| `ProductDefinition` (`ProductDefinition.cs:11`) | SO, IGameDataObject, IGameDataAssetWithId | `productName` | `.name` | `AssetId` is the Unity instance ID (L102), session-only. `price: Formula`, `demandModifier`, `tags`, `category` (with `categoryProvider` override), `endGameProduct` |
| `Recipe` (`Recipe.cs`) | SO | `Title` | `.name` | `ingredients`, `result` (ProductList), `gameDays` (easy chains rounds to 15-day multiples, L46-58), `requiredModules`, runtime `tier` (set by the recipe unlock) |
| `RecipeDatabase` (`RecipeDatabase.cs`) | SO singleton in the GDB | n/a | n/a | `RecipeDatabase.instance` = first asset of that type. `GetRecipes(product)`, `GetRecipesWhereProductIsUsed(product)` |
| `Building` (`Building.cs:12`) | MonoBehaviour prefab, IGameDataObject | `buildingName`, `description` | **`prefab.name`** | **Instances** set their `name` to `buildingName` (L284), and players can rename (`SetBuildingName` L376). Use `building.prefab.name` for identity. `baseCost`, `category`, `tags` |
| `TechTree`, `TechTreeCategory`, `TechTreeUnlock*` | SO | `techTreeName` / `categoryName` / `unlockName`, `description` | `.name` | |
| `Formula` (`Formula.cs`) | SO | n/a (`formula` is `[NonLocalizedField]`) | `.name` | Compiled to a delegate at `OnGameDataLoaded` |
| `DataCategory` (`DataCategory.cs`) | SO | `categoryName` | `.name`; `categoryGroupName` is the group key | |
| `ScenarioDefinition` (`ScenarioDefinition.cs`) | SO | `scenarioParameters.scenarioName` / `scenarioDescription` | `id` (GUID, `[NonLocalizedField]`); the JSON `.name` = `id` | `difficultyParameters`, `worldParameters`, `gameModule`, `saveFilePath` |
| `GameModule` (`GameModule.cs`) | SO, **not** in the GDB maps | `displayName` (NonLocalized) | `id` (GUID) | Held by `GameModuleRepository._modules`. Lookup: `GetModule(id)`. Current: `GameModuleRepository.instance.currentModule`. The base module id seen in the scenario JSON is `"92a10bbb-99f4-4aef-96e1-fdac26417c81"` (INFERRED: standard module) |
| `SettlementNameList` (`SettlementNameList.cs`) | SO | `displayName` | `.name` | `names` are `[NonLocalizedField]`. `Next()` **mutates** `_nextNameIndex` |

### 3.6 Are names and IDs stable?
- Within a build: yes. Savegames persist definition references **by `.name`**: `ResearchQueue.Serialize` writes each unlock's `.name` (L32-36), `ResearchProgress` writes each key's `.name` (L14-18), `SavegamePrefabIdentifier` (type as an assembly-qualified name, plus the asset name) resolves through `GameData.GetAsset` by type and name (`SavegamePrefabIdentifier.cs:23-45`), and buildings use a `SavegamePrefabIdentifier` created from the type `Building` and the prefab's name (`Building.cs:292`). CONFIRMED
- Across game versions: HIGH confidence that they are stable. The load path warns "Found null unlock ... May be normal if loading a previous savegame version" (`TechTreeAgent.cs:334`), so renames do happen occasionally and are tolerated.
- Names are **not** uniformly formatted. The scenario JSON has both `"Automotive Factory"` (with a space) and `"ElectronicsFactory"`. Treat them as opaque strings. CONFIRMED
- `GetInstanceID()` and `ProductDefinition.AssetId` change every session, so never expose them as IDs. CONFIRMED
- The `SavegameGameDataObject` attribute, with a `gameDataType` string such as "products", "recipes" or "buildings", decorates many definition classes. No runtime consumer was found in Assembly-CSharp (UNKNOWN purpose; possibly external tooling or exporter category names). These strings could serve as MCP collection names.

---

## 4. Localization: display names vs internal names
- `I18n` (`I18n.cs`) is a DontDestroyOnLoad singleton (`I18n.instance`). It holds `_currentLanguageKeyValue` and `_fallbackKeyValue`, both private `Dictionary<string,string>` (OrdinalIgnoreCase). The fallback is **always en-US** (`DEFAULT_LANGUAGE_KEYCODE`, L10, L145-153). Data is loaded from `LanguageData` assets in the GDB, filtered to the requested language and ordered by priority (L155-170). CONFIRMED
- `GameDataLocalizationTable.DoLocalize` (`GameDataLocalizationTable.cs:8-14`) iterates `GameData.editorInstance.assets` and applies `UnityObjectLocalizer`, implemented by `GenericLocalizer.Localize` (CONFIRMED, `GenericLocalizer.cs:13-31`; behaviour summarised from the decompiled source, not reproduced here). For each localizable field it reads the field's current string, looks up the field's key with `I18n.Get` using that current string as the fallback, and writes the result back into the same field, through reflection. The field is **overwritten in place**.
  - Key = the object's concrete type name, the asset name and the field name, joined with dots and passed through `CleanUpKey` (lower-case, spaces removed) (`LocalizationUtility.cs:20-23`; `UnityObjectLocalizer.cs:12-16`).
  - It covers every `string` field (and `[LocalizedType]` structs and lists) **unless** the field has `[NonLocalizedField]` (L104-111).
  - It reruns on every `languageChanged` (`LocalizationTable.cs:250-253`).
- Consequence (HIGH): with the game in French, `ProductDefinition.productName`, `TechTreeUnlock.unlockName`, `Building.buildingName`, `Recipe.Title`, `DataCategory.categoryName` and similar fields **hold French text**. The editor and runtime clones share the same underlying SO and prefab objects, because `Instantiate` of the manifest copies references. The original English value is not kept on the object.
- Ways to get English or internal names (pick one):
  1. **Internal ID**: `asset.name` (or `building.prefab.name`). It is never localized and is the recommended primary key. CONFIRMED
  2. **English display name**: build the key `<TypeName>.<assetName>.<fieldName>`, remove spaces and lower-case it, then look it up in the en-US `LanguageData` assets. Enumerate the `LanguageData` assets with `GameData.instance.GetAssetsRO`, keep those whose `language.keycode` is "en-US", order by `priority`, and read the `keys` array of key/value pairs (`LanguageData.cs`). This is read-only. HIGH
     - Alternative: reflect `I18n.instance._fallbackKeyValue`, which is private. HIGH
     - Example key: `productdefinition.<assetname>.productname`. The `<assetname>` part is lower-cased with spaces removed, because CleanUpKey runs on the whole string.
     - Example tech-node key: `techtreerecipeunlock.<assetname>.unlockname`. The prefix is the **concrete** (runtime) type name of the object.
  3. Do **not** call `I18n.LoadLanguage(...)` to switch language, because it mutates every asset and the UI.
- The current language is `I18n.instance.currentLanguage.keycode`.

---

## 5. Difficulty and scenario parameters (economy)

### 5.1 Runtime location
- `GameParametersManager` (`GameParametersManager.cs`) is a `[SavegameManagerObject]` ManagerBehaviour. It has `[SavegameSerialized]` `WorldParameters world`, `DifficultyParameters difficulty`, and `List<KeyValuePair<string,int>> optionSelections`. CONFIRMED
- Set at new game in `WorldInitializer.cs:109-111` (from `WorldSourceGenerate`, i.e. the difficulty screen, a scenario, or a tutorial). Restored from the save otherwise. CONFIRMED
- Per-actor override: `GetDifficultyParameters(IActor)` (L20-27). AI players return a custom set built from their `difficultyPreset` (`PresetValue` assets). Only `dispatch`, `prices` and `upkeep` are filled, other fields are default (`AiPlayer.cs:163-187`). CONFIRMED
- `GetParameterValue<T>(id)` resolves `optionSelections` (id -> index) through `NewGameOptionIndexable` assets (L29-49). It uses a lazy cache that mutates on first call.

### 5.2 `DifficultyParameters` fields and their effects
`DifficultyParameters.cs` (CONFIRMED declaration):

| Field | Semantics / consumer | Confidence |
|---|---|---|
| `prices` | **Additive** offset. Shop: the shop modifier (`GetShopModifier`) = the shop's own modifier + `prices` (`Shop.cs:194-201`). Contracts: price change × (1 + `prices`) (`ContractGenerator.cs:63`). World-event money: amount × (1 + `prices`) (`WorldEventAgent.cs:338`). The scenario JSON uses 0.0 as neutral | CONFIRMED |
| `upkeep` | **Multiplicative** on building upkeep (`Upkeep.cs:183`). 1.0 = neutral | CONFIRMED |
| `dispatch` | Passed as the `difficulty` argument to the vehicle-dispatch cost Formula (`TransportRequestPaymentHandlerBehaviour.cs:14-18`; `TransportRequestPaymentFormulaArguments.cs`). 1.0 = neutral (INFERRED from defaults) | CONFIRMED (exact math lives in the Formula asset: UNKNOWN) |
| `loan` | Starter loan = `loan` × 1,000,000 from State (`LoansAgent.cs:118-122`) | CONFIRMED |
| `infiniteMoney` | `MoneyAgent.cs:163`, `LoansAgent.cs:83` | CONFIRMED |
| `easyChains` | `Recipe.gameDays` rounded to multiples of 15 (`Recipe.cs:46-58`) | CONFIRMED |
| `resourceDifficultyIndex`, `infiniteResources` | resource node amounts (`ResourceNode.cs:147,229`) | CONFIRMED |
| `eventChanceModifier`, `eventDifficulty` | world events (`WorldEventManager.cs:146`, `WorldEventCreator.cs:41`) | CONFIRMED |
| `pollutionIntensity`, `pollutionFalloff` | pollution (`PolluterBuilding.cs:169`, `ComputePollutionStrategy.cs:215-219`) | CONFIRMED |
| `scoreModifier` | end-game score (`EndGameManager.cs:47`) | CONFIRMED |
| `traffic`, `terraforming`, `showHelpButtons`, `spawnHelp`, `grantAllPermits`, `presetIndex` | gameplay toggles | CONFIRMED (grantAllPermits consumer not checked) |
| `demand`, `balance` | **no reads found** in Assembly-CSharp (grep of `.demand` / `.balance` returned nothing). They look vestigial; the scenario JSON stores 0.0 | CONFIRMED (absence by grep) |

- Product demand is driven by `ProductDefinition.demandModifier` plus the GlobalMarket formulas (`GlobalMarketPriceModifierFormulaArguments` passes `"demand"`). That is outside DifficultyParameters. HIGH
- `ScenarioParameters` (`ScenarioParameters.cs`): `scenarioName`, `scenarioDescription`, `objectives[{objective, constraint}]`, `constraints[]`, `demolishing`, `bankruptcy`, `loans`, `winOneMode`, `permanentEvents[]`, `restrictedBuildings[]`, `restrictedTags[]`, `restrictedBuildingCategories[]`. The runtime holder is `ScenarioManager` (not detailed here). INFERRED
- `ScenarioPropertiesViewFactory.cs:30-37` shows how the UI presents a scenario: money = `loan` × 1,000,000, and `prices` and `upkeep` formatted as percentages. CONFIRMED

---

## 6. Unsafe members for a read-only observer
All of these are CONFIRMED in source unless marked otherwise. All Unity, Manager and Formula access must be on the **main thread**. `ManagerBehaviour<T>.instance` may call `FindObjectOfType` and record the main thread id (`ManagerBehaviour.cs:13-37`). `Formula.Evaluate` uses a static, non-thread-safe `ObjectPool` (`Formula.cs:11, 32-39`).

**Mutating: never call**
- `ITechTreeAgent`:
  - `Unlock`, `ForceUnlock(s)`, `UnlockChain`, `UnlockSpendingPoints`
  - `UnlockBuilding`, `UnlockRecipe`
  - `SetBuildingPriceOverride`
  - `Initialize`
  - `OnTechTreeUserRegistered`
- `TechTreeAgentResearchState`:
  - `ResearchUnlock`, `CancelResearch`, `CompleteResearch(u)`
  - `MoveInFrontOf`
  - `SetEfficiency`, setter `efficiencyIndex`
  - `SpendUnlockPoints`
  - setters `freeUnlocks`, `speedModifier`
  - `Tick`
  - `Initialize` (subscribes to `onDayEnd`)
- `ResearchQueue.Add/Pop/Remove`. Also **`research.queue` returns the live internal `List`**: copy it, never modify it.
- `TechTreeManager.RegisterAgent/DeregisterAgent/RegisterUser/DeregisterUser`, setter `isEnabled`.
- `TechTreeUnlock.Unlock`, `OnGameDataLoaded`. `TechTreeRecipeUnlock.UpdateRequiredUnlocks/DetectTeaser` (editor helpers that write fields).
- `TechTreeGenericUnlockEffect(Action).Execute/Revert*` (reflection writes). `TechTreeUser.OnGenericUnlock/RevertAllExecutedEffects/Initialize/CleanUp` (CleanUp uses `DestroyImmediate`).
- `UnlockableModifier.SetIndex`, setter `modifierValueIndex`.
- `GameData`:
  - `EnableManifest`, `EnableAllOwnedManifests`, `EnableGameModuleManifests`, `DisableManifest(s)`
  - `RegisterAssetWithCommonManifest`, `RegisterModAsset`, `DeRegisterAsset`, `FlushNameOnlyObjects`
  - `UpdateAssetCategory`
  - **`OnGameDataLoaded`** (re-runs every listener)
  - `InvalidateRuntimeData`
  - `GameData.editorInstance` getter (calls `Initialize` with all manifests enabled on the original asset; avoid)
- `GameDataManifest.Register*/DeRegister*/Merge/Initialize/SortAssets`.
- `GameModuleRepository.SetModule` (switches manifests and re-localizes).
- `ContentLoader.*`, `ModLoader.Enable/DisableMod/SetModOrder/LoadMods/LoadAssets` (PlayerPrefs writes).
- `I18n.LoadLanguage`, `ReloadCurrentLanguage`, `EnableStringLengthDebugging`; `LocalizationTable.Localize` (rewrites asset fields).
- `GameParametersManager` fields are public and writable (`difficulty`, `world`, `optionSelections`). Read them only. `DifficultyParameters.Copy()` is safe if you want a snapshot.
- `SettlementNameList.Next/Shuffle`, `ProductDefinition.SetIcon/SetVisualRepresentation`, setter `category`, `Recipe.gameDays` setter, `Building.SetBuildingName/FakeClick/OrientCameraOnMe`.

**Side-effect or pooling hazards: use carefully**
- `GameData.GetAssets<T>()` / `GetAssets(Type)` / `ITechTreeAgent.GetUnlocked()` / `TechTreeHelper.ExcludeOverridden` return pooled lists from `ListPool<T>`. Not returning them only costs allocations; returning a list twice corrupts the pool. Prefer `GetAssetsRO`. HIGH
- `GameDataManifest.GetAssetsRO(type)` uses `_typeMap.GetSafe`, which **inserts** an empty entry for unknown types (`GameDataManifest.cs:302`; `GetSafe` tries `TryGetValue` and otherwise adds a new, default-constructed value under that key, `Utils.cs:269-278`, CONFIRMED). This is a benign mutation on the main thread, but a race if called off-thread. The `(Type, group)` overload inserts too.
- `GameData.GetAsset(int)` throws `KeyNotFoundException` for unknown IDs (`GameDataManifest.cs:405-413`).
- `TechTreeAgent.IsUnlocked(u)` logs an error for unknown unlocks (L205). `GetBuildingCost` logs for unknown buildings.
- `GameParametersManager.GetParameterValue` builds its cache lazily (first call writes `_optionSelectionDictionary`).
- Events (`TechTreeAgent.onUnlocked`, `research.onQueueChangeEvent`, `TechTreeManager.initialized`, `I18n.languageChanged`, `GameModuleRepository.gameModuleChanged`, `TimeManager.onDayEnd`) can be subscribed to, but subscription itself mutates game objects and leaks across scene reloads. Polling is safer.

**Safe read surface (main thread)**
- `GameData.instance.GetAssetsRO(...)`, `GetAsset<T>(name)`, `TryGetAsset<T>`
- Definition fields (treat them as read-only)
- `TechTreeAgent.IsUnlocked/IsBuildingUnlocked/IsRecipeUnlocked/GetBuildingCost/LockedByDemoMode/isInitialized`
- `research.activeUnlock/researchProgress/smoothResearchProgress/remainingTime/remainingCost/currentResearchCost/unlockPoints/efficiencyIndex/efficiency/GetResearchProgress/HasResearchProgress/IsEnqueuedForResearch/CanResearchUnlock/CanUnlockWithPoints/GetResearchTime/GetResearchDailyCost/GetResearchCost/GetUnlockChain(u, ownList)`
- `TechTreeManager.isEnabled/isInitialized/config/GetUnlockForBuilding/GetUnlocksForRecipe/GetTechTreeForUnlock`
- `TechTree.unlocks/tierCount`, `TechTreeCategory.trees/unlocks`
- `GameParametersManager.difficulty/world/GetDifficultyParameters(actor)`
- `GameModuleRepository.currentModule`
- `I18n.instance.currentLanguage`, `I18n.Get(key)`

---

## 7. Open questions
- Values of `TechTreeManagerConfig` and of the `researchCost` / `researchTime` Formula texts in the shipped GDB. They need an asset dump (UnityPy/AssetStudio on `resources.assets`), or a runtime read of `Formula.formula`. UNKNOWN
- Whether `Time.deltaTime` in `Tick` follows game speed (`Time.timeScale`) or the game uses its own time scaling. Check `TimeManager`. INFERRED: it follows game speed.
- Which actors get `FullyUnlockedTechTree` rather than `TechTreeAgent` (State? settlements?). UNKNOWN
- The scene object hosting `GameDataLocalizationTable` (DontDestroyOnLoad?) and whether localization runs before the first `GameData.instance` clone. HIGH confidence that it does not matter, because the objects are shared.
- `grantAllPermits` consumer, and the `dispatch` formula text. UNKNOWN
