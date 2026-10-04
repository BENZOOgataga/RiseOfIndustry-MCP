# Public modding projects and resources: original Rise of Industry (App 671440)

Researched 2026-10-03. The scope is the ORIGINAL Rise of Industry by Dapper Penguin Studios (Unity 2018.4.11, Mono, Steam build 9064059). Rise of Industry 2 (App 2532490, 2024-2025) is out of scope. Every project below was checked to be RoI 1. They all reference `ProjectAutomata`, App 671440, or `Rise of Industry_Data`.

Labels: **CONFIRMED** means verified from source or primary data. **HIGH CONFIDENCE** means strong indirect evidence. **INFERRED** means reasoned but not verified. **UNKNOWN** means not determinable here.

"Installed build" means the decompiled `research/_local/decompiled/Assembly-CSharp/ProjectAutomata/*.cs`. Line numbers refer to those files.

---

## 0. Key takeaways for the MCP integration

1. **The official code-mod loader is present in the final build and is simple.** (CONFIRMED, from `ModLoader.cs`, `CodeLoader.cs`, `GameBootstrapper.cs`)
   - It loads every `*.dll` in `<mod>/code/` with `Assembly.LoadFile` and attaches every type assignable to `ProjectAutomata.Mod` as a component on the persistent `ModLoader` GameObject (`DontDestroyOnLoad`).
   - It then calls `OnModWasLoaded()` and, after all mods are loaded, `OnAllModsLoaded()`.
   - Because `Mod : MonoBehaviour`, a mod gets `Update()` and coroutines for the whole session, including in-game.
2. **Live in-game state can be read from a code mod.**
   - ROIData (2021) is CONFIRMED to have done this from its decompiled code. It read `ActorManager`, `MoneyManager`, `TimeManager.onDayEnd`, shops and demand, loans, pollution, tech tree, and so on, every frame or day, and sent the data over HTTP.
   - Nearly all of the APIs it used still exist in the installed build (§2.4).
3. **No public project reads logistics or Max Send settings.** None reads per-building dispatch configuration or exposes an IPC or MCP channel. Every project found either dumps static registries at load, mutates data assets, or (ROIData) pushes aggregates to a web server. *(CONFIRMED for the projects reviewed.)*
4. **Harmony 1.1.0 is bundled and the game creates a Harmony instance itself.** `ModLoader.InitializeModdingApi()` (`ModLoader.cs:176-179`, id `com.riseofindustry.dapperpenguinstudios`, exposed as `ModLoader.harmony`). The API is `Harmony.HarmonyInstance`, not Harmony 2.x. *(CONFIRMED. `0Harmony.dll` FileVersion is 1.1.0.0, and Newtonsoft.Json is 11.0.0.0.)* A read-only MCP mod should not need Harmony.
5. **Loader pitfalls for our own mod** (CONFIRMED from code):
   - **A folder without `desc.json` stops all mods loading.** If any subfolder of `Mods/` lacks `desc.json`, `PreLoadMod` returns `DESCRIPTION_JSON_NOT_FOUND`, which is not `SKIP`, so `PreLoadMods` returns false and no mods load (`ModLoader.cs:301-321`).
   - **An exception in our mod blocks startup.** An exception in `OnModWasLoaded`, `OnAllModsLoaded` or the DLL load raises the mod-error popup, whose only action is `Application.Quit` (`ErrorPopupViewModel.cs`). It also sets `loadingModsFailed`, which stops the bootstrap (`GameBootstrapper.cs:67`), and in non-debug builds the mod is disabled in PlayerPrefs (`ModLoader.cs:463-468`). Our mod must wrap its load hooks in try/catch.
   - **Unknown file types in `assets/` crash loading.** A file with an unrecognised extension in `<mod>/assets/` gives an NRE in `AssetLoaderRegistry.GetLoadOrder` (`AssetLoaderRegistry.cs:43-47`). This matches the crash reported in the Steam "Harmony support?" thread (§3).
   - **Unity objects need the main thread.** `ManagerBehaviour<T>.instance` will not resolve a singleton off the main thread (`ManagerBehaviour.cs`, `GetFrameNumber` returns `int.MinValue` off-thread). ROI-CustomMod's `System.Timers.Timer` approach is therefore unsafe. MCP requests must be marshalled to the Unity main thread, for example with a queue drained in `Update()`.
6. **The game API has been stable since 2022.** The last public build is 9064059, updated 2022-09-09 (CONFIRMED from Steam app info). Development ended with the 2.3.x line in 2021-2022. The engine and API will not change again (§4).

---

## 1. roiroy/ExportStuffMod (Task 1)

- URL: https://github.com/roiroy/ExportStuffMod. Cloned to `research/references/ExportStuffMod` at `aad9ec4` (2019-05-12).
- History: 2 commits, "Initial" (2019-05-11) and "Exporting buildings and harvesters cost" (2019-05-12). The repo was created 2019-05-11, has 0 stars, and is public domain. It is based on sanasol/ROI-CustomMod (README).

### 1.1 What it does and how it is structured (CONFIRMED)

- **`desc.json`:** `{author:"roiroy", name:"ExportStuffMod", version:1, versionString:"0.0.1", dependencies:[]}`. This matches `ProjectAutomata.ModDesc` (`ModDesc.cs:3-16`). The installed `ModDesc` also has an optional `description` field.
- **Layout:** `code/ExportStuffMod.dll` (8.7 KB, prebuilt), `src/ExportStuffMod.cs` (the only logic file), `src/ExportStuffMod.csproj`, `src/Properties/AssemblyInfo.cs`, `exports/exports.json` and `exports/exports.csv`. There are no `assets/` or `content/` folders.
- **Entry point:** class `ExportStuffMod`, a `Mod` subclass overriding `OnAllModsLoaded`. It runs once, during game bootstrap, after all mods have loaded and before the main menu. It does not run in-game.
- **Behaviour:**
  - It builds `ExportedStuff {timestamp, recipes[], producers[]}`.
  - It serialises this with `Newtonsoft.Json` (`Formatting.Indented`) and writes it to the log with `UnityEngine.Debug.Log`.
  - It writes no file of its own. The README says to read it from `%LOCALAPPDATA%Low\Dapper Penguin Studios\Rise of Industry\output_log.txt`. On Unity 2018.4 this is `Player.log`, so the path is INFERRED to have changed.
- **Harmony:** none. The csproj references `0Harmony.dll`, but the code never uses it.
- **Build target:**
  - The csproj targets .NET Framework v4.0 and references `Assembly-CSharp`, `UnityEngine`, `UnityEngine.CoreModule`, `0Harmony`, `UnityEngine.JSONSerializeModule`, `UnityScript` and `Newtonsoft.Json`, all from the `Rise of Industry_Data\Managed` folder.
  - A post-build step copies the DLL into `...\RiseOfIndustry\Mods\ExportStuff\code`.
- **Which game version it targeted:** HIGH CONFIDENCE it was 1.0.1 (`1.0.1:0705b`, public on 2019-05-07). The export timestamp is `2019-05-11T11:35:27Z`, after the 1.0 full release (2019-05-02) and before Megapatch 1.1 went public (2019-05-21).
- **The data:** 145 recipes and 30 producers. In the committed export the producer entries lack `cost`, because the export predates commit 2. `exports.csv` has a "Factory" column that the code does not produce, so it was INFERRED to be hand-assembled. This is pre-2.0 data and is stale for 2.3.x.

### 1.2 Every game API it uses, checked against the installed build

All references were checked in the compiled DLL's IL (`ilspycmd -il`) as well as in the source. Every IL member reference resolves against the installed build, so the DLL is binary-compatible. One runtime behaviour has changed (see `Recipe.gameDays`).

| API used (exact) | IL form | Installed build | Status |
|---|---|---|---|
| `ProjectAutomata.Mod` (base class), virtual method `OnAllModsLoaded` | `extends` | `Mod.cs:5`, `Mod.cs:15` (also `OnModWasLoaded` at `:11` and `modLoader` at `:9`) | PRESENT |
| `GameData.instance` (static) | `call get_instance()` | `GameData.cs:39` | PRESENT |
| `GameData.GetAssets<T>` (optional pre-allocated `List<T>` argument), with `T` as `Recipe` and as `Building` | `callvirt GetAssets<T>(List<T>)` | `GameData.cs:270` | PRESENT (same signature) |
| `Recipe.name` (`UnityEngine.Object.name`) | `get_name` | Unity | PRESENT |
| `Recipe.gameDays` (float getter) | `callvirt get_gameDays()` | `Recipe.cs:46-61` | **CHANGED in behaviour.** The getter now reads `ManagerBehaviour<GameParametersManager>.instance.difficulty.easyChains` (`Recipe.cs:50`). At bootstrap there is INFERRED to be no `GameParametersManager`, so this would throw an NRE. That would raise the fatal mod-error popup. `gameDaysForPriceCalculation` (`Recipe.cs:63`) returns raw `_gameDays` without a manager and is the safe replacement. |
| `Recipe.ingredients`, `Recipe.result` (`ProductList` fields) | `ldfld` | `Recipe.cs:21`, `Recipe.cs:23` | PRESENT |
| `ProductList : IEnumerable<Product>` | Linq `Select` | `ProductList.cs:10` | PRESENT |
| `Product.amount` (int field) | `ldfld` | `Product.cs:20` | PRESENT |
| `Product.definition` (property) | `callvirt get_definition` | `Product.cs:22` | PRESENT |
| `ProductDefinition.price` (`Formula` field) | `ldfld` | `ProductDefinition.cs:39` | PRESENT |
| `Formula.formula` (string field) | `ldfld` | `Formula.cs:21` | PRESENT |
| `Building.baseCost` (float field) | `ldfld` | `Building.cs:32` | PRESENT |
| `Building.recipeUser` (property) | `callvirt get_recipeUser` | `Building.cs:165` | PRESENT |
| `RecipeUser.availableRecipes` (`Recipe[]` field) | `ldfld` | `RecipeUser.cs:38` | PRESENT |
| `Building.harvester` (property) | `callvirt get_harvester` | `Building.cs:167` | PRESENT |
| `GathererHub` (via `GetComponent<GathererHub>()`) | generic call | `GathererHub.cs:8` (`: RecipeUser`) | PRESENT |
| Hard-coded hub-to-harvester name map (`CoalGatherer` → `CoalMineHarvester`, etc.) | data | — | UNKNOWN whether the asset names still match. Check against an asset dump. |

### 1.3 What it proves and does not prove

**It PROVES:**
- **CONFIRMED:**
  - A C# DLL in `Mods/<name>/code/` with a `desc.json` is picked up by the official loader.
  - `Mod.OnAllModsLoaded` runs.
  - Static registries are reachable through `GameData.instance.GetAssets<T>()` (recipes, buildings, products, price formulas, building costs, recipe-to-building mapping).
  - The game's own Newtonsoft.Json is usable from a mod.
- **HIGH CONFIDENCE:** this worked on 1.0.1 in May 2019, and the loader is unchanged in kind in the installed build.

**It does NOT prove:**
- That it runs on 2.3.x as-is. The `gameDays` NRE risk above makes this INFERRED to fail.
- Any access to live in-game state (world, actors, money, buildings placed, storage, vehicles).
- Access to logistics and dispatch settings, including Max Send, destinations or priorities.
- Main-thread-safe polling, persistence across scenes, or any I/O channel other than the log.
- That Harmony works.

---

## 2. Other code mods (cloned)

### 2.1 sanasol/ROI-CustomMod
- URL: https://github.com/sanasol/ROI-CustomMod. Commit `92c332b`, 2019-05-06. This is ExportStuffMod's ancestor.
- `source/MyClass.cs`:
  - `OnModWasLoaded()` starts a `System.Timers.Timer` that polls `World.instance.isWorldReady`.
  - When the world is ready it registers a console command named "somecommand" through `DevConsole.Console.AddCommand` (a `DevConsole.Command<string,string>` instance) and logs `World.seed`.
- `UsageOfInternalGameData.cs` dumps recipes as JSON in `OnModWasLoaded` and patches `Formula` assets and `Vehicle.maxSpeed`.
- Installed build:
  - `World.isWorldReady` is at `World.cs:111` and `World.seed` (static) at `World.cs:87`. **PRESENT.**
  - `DevConsole.Console.AddCommand(CommandBase)` is at `DevConsole/Console.cs:207`. **PRESENT.**
  - `GameData.GetAsset<T>(string)` is at `GameData.cs:295`. **PRESENT.**
  - `Vehicle.maxSpeed` is at `Vehicle.cs:39`. **PRESENT.**
- Significance:
  - It is the earliest public mod to touch in-game state (`World`).
  - It shows the in-game DevConsole can be extended with commands, which could be a debug hook for an MCP mod (INFERRED).
  - Its Timer-thread polling is not thread-safe (§0.5).

### 2.2 pjf/TransportCostsRebalanced
- URL: https://github.com/pjf/TransportCostsRebalanced. Commit `d637f81`, 2018-12-03. MIT licence. Steam Workshop item [1581416174](https://steamcommunity.com/sharedfiles/filedetails/?id=1581416174), with 2,101 lifetime subscriptions.
- It targeted Alpha 8 (`versionString "A8.0.1"`) and .NET 3.5.
- In `OnModWasLoaded` it looks up `Formula` assets by name with `GameData.instance.GetAsset<Formula>` (e.g. "AwhDispatchCost") and replaces their `formula` text, and sets the `maxSpeed` of the `Vehicle` asset "CargoBoat" to 1.
- APIs: `GetAsset<T>(string)` and `Formula.formula` are PRESENT. The formula asset names are UNKNOWN until checked in an asset dump.
- **Workshop install folder:** `workshopitem.vdf` shows `contentfolder .../SteamApps/common/RiseOfIndustry/Mods/TransportCostsRebalanced`, so the game-dir `Mods/` folder was used for development (CONFIRMED). The mod is a write mod, which is out of scope for a read-only MCP, but it confirms the data-asset model.

### 2.3 rockymine/RiseOfIndustry: `ROIData` mod (binary only)
- URL: https://github.com/rockymine/RiseOfIndustry. Commit `ae04ff9`, 2021-06-12. No licence.
- Contents:
  - `ROIData/desc.json` (author "rockyground") and `ROIData/code/ROIData.dll`.
  - A second variant `ROIData.dll` at the repo root.
  - `SoSe2021*.sav` and a zip.
- This was a university business-game ("Planspiel") project, summer semester 2021. It was decompiled statically to `_local/decompiled/thirdparty/ROIData/`. Its assembly targets net461 and references Assembly-CSharp, Assembly-CSharp-firstpass, 0Harmony, Newtonsoft.Json and UnityWebRequestModule.
- **What it does (CONFIRMED from decompiled code):**
  - **Harmony:** `ROIDataMod` (a `Mod` subclass), in `Awake`, creates a Harmony instance with id `ROIData.SettlementPatch` and applies all its patches (`PatchAll`). It patches `BuildingManager.CanBuild(Building)` and `CanBuild(int)`, `PermitManager.AuctionPermit`, `AuctionDefinition.GenerateAuction`, settlement generation, and shop demand, price and product. So Harmony 1.x patches from a code mod worked on a 2.x build.
  - **Every frame (`Update`):** it waits until `ActorManager.instance.actors` is non-empty and `MoneyManager.instance.balances` is available, then subscribes to `TimeManager.instance.onDayEnd`. On each day end it builds a `SaveDataModel`, writes `%APPDATA%\RiseOfIndustry\SaveData\<day>.json`, and POSTs it to `https://roi.jgdev.de/api/Data` with `UnityWebRequest` via `StartCoroutine`. Every 10 seconds of real time it GETs tasks from `.../api/TaskAPI?groupSteamID=`.
  - **Live state it read:**
    - Player: the first actor in `ActorManager.actors` that is a `HumanPlayer`.
    - Balance: `MoneyManager.GetBalance(IMoneyAgent)`.
    - Profit: `MoneyAgent.GetProfit(GameDate, GameDate)` and private `MoneyAgent._bills` (by reflection).
    - Company value: `CompanyStats.ComputeCompanyValue()`.
    - Shop demand: private `Shop._demand` (by reflection), `Shop.GetSoldCount(IActor, ProductDefinition, GamePeriod)`, `SettlementManager.settlements`, `SettlementBase.settlementName`.
    - Loans: `LoansAgent.loans`, `Loan.amount`, `Loan.apr`, `Loan.amountToPay`.
    - Machine uptime: `Actor.buildings.recipeUsers` with `BuildingAnalysis.GetLastMonthValue(Upkeep.uptimeAnalysisItemDef)`.
    - Pollution: private `RegionManager._serializedRegions`, `PermitManager.OwnsPermitForRegion`, `PollutionManager.GetPollution(int)`.
    - Tech tree: `TechTreeAgent.GetUnlocked()`.
    - Buildings: `BuildingManager.buildingsList`, `Building.buildingOwner`.
    - Date: `TimeManager.day`, `month`, `year` and `today`.
  - **Not read-only:** it also triggered custom world events, through private `WorldEventManager._staticEvents`, and paused time.
- **Existence in the installed build:**
  - **PRESENT:**
    - `ActorManager.actors` (`ActorManager.cs:18`), `HumanPlayer` (`HumanPlayer.cs:7`), `MoneyManager.balances` (`:81`), `MoneyManager.GetBalance` (`:104`).
    - `MoneyAgent._bills` (`MoneyAgent.cs:32`), `MoneyAgent.GetProfit` (`:244`).
    - `Actor.money` (`Actor.cs:79`), `Actor.Get<T>` (`:171`), `Actor.buildings` (`:77`), `ActorBuildingCollection.recipeUsers` (`:33`).
    - `TimeManager.onDayEnd` (`TimeManager.cs:45`; its delegate type `TimeManagerCallback`, which takes a `GameDate`, is at `:10`), `TimeManager.today`, `day`, `month`, `year` (`:19-25`).
    - `BuildingManager.buildingsList` (`:27`), `Building.buildingOwner` (`Building.cs:66`), `CompanyStats.ComputeCompanyValue` (`:22`, returns int).
    - `Player.hq` (`Player.cs:22`), `Building.settlement` (`Building.cs:64`).
    - `Shop._demand` (`Shop.cs:83`), `Shop.GetSoldCount(IActor, ProductDefinition, GamePeriod)` (`Shop.cs:258`), `GamePeriod.month` (`:11`).
    - `SettlementManager.settlements` (`:23`), `SettlementBase.settlementName` (`:38`).
    - `LoansAgent.loans` (`:35`), `Loan.amount`, `apr`, `amountToPay` (`Loan.cs:44-66`).
    - `BuildingAnalysis.GetLastMonthValue` (`:199`), `Upkeep.uptimeAnalysisItemDef` (`:40`).
    - `RegionManager._serializedRegions` (`:27`), `PermitManager.OwnsPermitForRegion` (`:149`), `PollutionManager.GetPollution(int)` (`:88`).
    - `TechTreeAgent.GetUnlocked` (`:298`), `WorldEventManager._staticEvents` (`:38`), `PolluterBuilding._affectedTiles` (`:36`).
  - **MISSING:** `TimeManager.canAdvanceTime`, which is not found anywhere in the installed Assembly-CSharp. It was either removed after mid-2021 or came from a modified reference assembly (UNKNOWN). Pausing is out of scope for a read-only MCP anyway.
- Also useful: `Player.activeActor` (static, `Player.cs:14`) exists in the installed build as a simpler way to get the human player than ROIData's scan.
- **Significance:** this is the strongest public precedent for our design. It shows that a code mod can, in-game:
  - (a) read rich live economic state every frame or on day end,
  - (b) run coroutines and network I/O,
  - (c) apply Harmony 1.x patches.

  It does NOT touch logistics and dispatch settings (Max Send, destinations), vehicles in flight, or storage contents, and it exposes no inbound channel (it is push-only).

### 2.4 twikantoro/RiseOfIndustry-AlwaysNightMod (2026)
- URL: https://github.com/twikantoro/RiseOfIndustry-AlwaysNightMod. Commit `34519db`, 2026-09-22. README says "Tested on Game Version: `2.3.3 : 0507b*` (Steam - Public)".
- It does not use the official loader. `Patcher.cs` uses Mono.Cecil to rewrite `Rise of Industry_Data\Managed\Assembly-CSharp.dll`, keeping a `.bak`, and inserts `call AlwaysNightHook.Init()` at the start of `ProjectAutomata.TimeManager.Awake`. The hook DLL is placed in `Managed/`.
- **Relevance:**
  - (a) It CONFIRMS the latest public version string is **2.3.3 (build tag 0507b)**. The trailing `*` is INFERRED to be `GameVersion.modIndicatorText`, shown when mods are present.
  - (b) It is an anti-pattern for us. It permanently modifies game files, breaks on Steam verify or update, and violates our "do not install into the game binaries" rule. The official `Mods/` loader achieves the same persistence with no binary patching.

---

## 3. Other resources (web)

| Resource | URL | What it is | RoI 1? | Technical facts / relevance |
|---|---|---|---|---|
| Official modding wiki page (fandom, ex-gamepedia) | https://riseofindustry.fandom.com/wiki/Modding (fetched via the MediaWiki API, last revision 2021-03-04) | Dapper Penguin's own modding guide | Yes | See the facts list below this table. |
| Official class reference docs | http://dapperpenguinstudios.com/roimodding/index.html | Code documentation for `ProjectAutomata` | Yes | **Dead.** HTTP 401 on 2026-10-03, as are `RoIWorkshopUploader.7z`, `Trumpet.7z` and `AcidResource.7z`. Our decompiled source replaces it. |
| Mod "mega-guide" (Google Doc) | https://docs.google.com/document/d/1EacUmSAb-yxO9Mgwa_glasLeZUfgRDRCxFw336IQkFU | Community or dev comprehensive guide linked from the wiki | Yes | Not accessible (401). UNKNOWN content. |
| Steam "Modding Guide" (pinned) | https://steamcommunity.com/app/671440/discussions/7/1746720717351313559/ | Dev post by "Alex, Master Penguin", 2018-08-13 | Yes | Mods require Alpha 7 or later. Points to the wiki and a Discord (discord.gg/dCWVGNP). No API detail. |
| Steam "How to Create a Mod" | https://steamcommunity.com/app/671440/discussions/7/1651043320660625923/ | Q&A, 2019-05-05 | Yes | Dev confirms the `Mods` folder does not exist by default: "You need to make it". (CONFIRMED that the user must create `<install>/Mods`.) |
| Steam "Harmony support?" | https://steamcommunity.com/app/671440/discussions/7/2245552086104193921/ | Q&A, 2020-05-08 | Yes | A user tried bundled Harmony 1.1.0 and Harmony 2.0 and got an NRE in `AssetLoaderRegistry.GetLoadOrder()`, i.e. during asset loading. A dev replied that Harmony "should work". Unresolved. Consistent with the `assets/` pitfall in §0.5. |
| Steam "Modding still available?" | https://steamcommunity.com/app/671440/discussions/7/2262439950687857396/ | Q&A, May-Jun 2020 | Yes | A dev says modding works. Content-JSON tech-tree mods were fragile across 2.x updates. GonumeN's toolkit broke after updates. |
| Steam Modding forum index | https://steamcommunity.com/app/671440/discussions/7/ | Forum | Yes | Low activity after 2021. Nothing about IPC or external tools. |
| Steam Workshop | https://steamcommunity.com/workshop/browse/?appid=671440 | 396 items (mostly names, translations, layouts, content) | Yes | See the Workshop list below this table. |
| Nexus Mods | https://www.nexusmods.com/riseofindustry (queried via the public GraphQL API) | 7 mods | Yes | See the Nexus list below this table. |
| Dapper Penguin Water Tower mod (official example) | Nexus mod 2, Workshop "[OLD] Water Tower" 1477582836 | Official code + content + asset-bundle example (`WaterTowerMod : Mod`, `WaterDistributor`, custom BuildingPanelModule) | Yes | Source is not public. Its usage is documented in the wiki (`GameData.instance.GetAsset<BuildingPanelModule>` in `OnAllModsLoaded`). |
| Jettucis/roimodexamples (cloned) | https://github.com/Jettucis/roimodexamples | Commented content-JSON examples, 2020 | Yes | Recipe, ProductDefinition and TechTree JSON shapes. These match the `CC*Model` content creation. |
| aBitEmberous/RoI-Industry-Expanded-Mod | https://github.com/aBitEmberous/RoI-Industry-Expanded-Mod | Content-JSON mod skeleton, 2020 | Yes | Only `desc.json` and examples. Low value. |
| Saturate/roi-settlementnames | https://github.com/Saturate/roi-settlementnames | Settlement names content mod, 2021 | Yes | Shows the `mod/desc.json` + `mod/content/*.json` layout. Low value. |
| andrey-zakharov/roicalc | https://github.com/andrey-zakharov/roicalc (live: https://andrey-zakharov.github.io/roicalc/) | Calculator, 2019-2021 | Yes | Ships extracted game data (`public/static/RoI/{recipes,buildings,prods,...}.json`, a "RoI 2130" variant, and `lang-langdata_*` localisation). The extraction method is undocumented. This is a possible cross-check for our own asset dump. Not cloned (17 MB). |
| roiroy/recalc | https://github.com/roiroy/recalc | React ratio calculator, 2019 | Yes | Consumes ExportStuffMod's `exports.json` (`src/roidata/exports.json`). |
| Other calculators | boriscallens/RiseOfIndustry (2018), KieranW26/ROIRatios (2019), mondchopers/roi (2018), BobbieBarker/roi_abacus (2022), hapenia/roi-calc (2023), joseph-walker/rise-of-industry-calc (2023), mickvangelderen/rise-of-industry-calculator (2023), jirayuwat12/ROI-helper (2023), harishisham22/rise-of-industry-calculator (2025), liobio/Rise-of-Industry-Formula-Quantification (2023, C# WinForms with `Formula.json`) | Recipe and ratio tools | Yes (by description) | Data only. None reads live game state. Not cloned. |
| Excluded | phh1stmpd9f6h3a/rise-of-industry-8055 ("private cheat ... god mode, ESP") | Malware-style bait repo | — | Ignored. Not cloned. |
| Excluded | MichaelHind2130/Rise-Of-Industry (HOI4 mod), wfq1971/SomaSimSaves-Rise-of-Industry-2- (RoI 2) | Different games | No | Ignored. |

**Facts from the official modding wiki page (all CONFIRMED by the source code):**
- Code mods are .NET class-library DLLs that reference `Rise of Industry_Data/Managed/{Assembly-CSharp, UnityEngine, UnityEngine.CoreModule}.dll`.
- The namespace is `ProjectAutomata`, a holdover from the game's earlier name.
- Entry point: subclass `Mod` and override `OnAllModsLoaded`.
- The mod object is a `MonoBehaviour` kept alive with `DontDestroyOnLoad` and "able to receive the regular Unity messages". The wiki's example uses `Update()`.
- DLL path: `[install]/Mods/MyMod/code`.
- Mod root folders are `assets/`, `code/`, `content/`, plus `desc.json` with fields `author`, `name`, `version`, `versionString`, `dependencies`.
- Content is JSON `{type, object, components}` processed by `CC<Type>Model`.
- Supported asset types: `.bundle`, `.svg`, `.png`, `.sprite`, `.sae`, `.cae`.
- The Workshop Uploader is `ProjectAutomata.WorkshopUploader.exe`.

The wiki recommends Unity 2018.1.0f2. The installed game is Unity 2018.4.11, so the wiki is older than the final build.

**Workshop items worth noting** (from `GetPublishedFileDetails`):
- "[Outdated] Power Up!" (1510415068, by Hohoz & GonumeN, 2018) is a large code-plus-content mod, now outdated.
- "[OLD] Auto Delivery Priority" (1545496396, 2018) was a logistics-priority code mod, abandoned at A8 when the base game added Settings > Gameplay > Dispatch Priority Order.
- "[Working] Unlimited Warehouse Storage" (2429912227, 2021) has an author note: tested working 2024-07-17 and 2026-04-02. It is INFERRED to be a code or content mod that still loads on the final build. It was not downloaded.
- Others: "Instant Research" (1643658989), "Free Terraforming" (1615319369, updated 2023), "New Products Toolkit" (1607026282).
- None is a data-export or IPC tool.

**Nexus mods:**
1. Steam Workshop Mod Uploader (Dapper Penguin, 2018)
2. Water Tower (Dapper Penguin, 2018)
3. Czech city names (2018)
4. Polish city names (2023)
5. Russian city names (2023)
6. Peruvian city names (2025)
7. **"Rise Of Industry Overhaul"** (id 10, author Shibiii, 2025-12-26, v1.0.0)
   - A code mod installed to `..\RiseOfIndustry\Mods\RiseOfIndustryOverhaul`.
   - It auto-generates `config/*.json` next to the DLL.
   - It overrides formulas by name ("GameData assets → Resources fallback"), changes vehicle speed and production speed multipliers, and has debug dumps of vehicles, formulas and unlocks.
   - HIGH CONFIDENCE that this is evidence the official `Mods/<name>/code` loader works on the final 2.3.3 build (uploaded 2025, after the 2022 final build). It is a write mod. Its source is not public.

**Not found (UNKNOWN or none exist):**
- No official SDK, "Mod Kit" or Unity package for RoI 1 is publicly downloadable today. The wiki mentions "our tools for packing game data" and an "Asset Viewer", and a forum post mentions a "Modding SDK" to import into Unity. Their hosting is dead (401).
- No save editors or save-format reverse-engineering notes for RoI 1.
- No r/riseofindustry modding threads with technical content surfaced in search.
- No mod.io presence.
- No BepInEx or MelonLoader projects for RoI 1.

---

## 4. Version history and API stability

Sources: Steam news API (`ISteamNews/GetNewsForApp`, appid 671440), the Steam "Patch Notes" forum (https://steamcommunity.com/app/671440/discussions/4/), and Steam app info (api.steamcmd.net).

| Date | Version / event | Note |
|---|---|---|
| 2018-02-09 | Early Access | — |
| 2018-08-31 / 2018-09-12 | Alpha 7 / "Steam Workshop Modding is now ready to be tested" | Modding introduced (Alpha 7 is the minimum per the dev guide) |
| 2018-11-29 → 2019-04 | Alpha 8 to A11 | TransportCostsRebalanced is from A8 |
| 2019-05-02 | 1.0 full release | — |
| 2019-05-07 | 1.0.1:0705b | **ExportStuffMod era** |
| 2019-05-21 | Megapatch 1.1 | — |
| 2019-06-25 / 07-15 / 09-16 | 1.2 (Logistics v5), 1.3, 1.4 (HQ specs) | — |
| 2019-10-24 | 2.0.0:2510b + "2130" DLC | — |
| 2019-11 → 2020-04 | 2.1.0 to 2.1.6 | — |
| 2020-04-28 → 2020-07-03 | 2.2.x (2.2.2 to 2.2.4) | — |
| 2021-01-07 / 01-08 | "Industrious 2.3 Update", 2.3.0:0701b | — |
| 2021-01-14 | 2.3.1:1301a | Experimental branch build 6076857 (2021-01-13) |
| 2021-06 | — | ROIData era (2.3.1 public) |
| 2021-09-17 | Branch changes: `previousbuild` = 6081664, `noburst` = 7376113 | — |
| **2022-09-09** | **Public build 9064059** | Matches the installed build. Version string **2.3.3 : 0507b** per the AlwaysNight README (HIGH CONFIDENCE) |
| 2024-02 / 2025-06-03 | Only RoI 2 announcements afterwards | RoI 1 is no longer developed |

Patch notes for 2.3.2 and 2.3.3 were not found in the forum listing (UNKNOWN content). The locally seen "Default Scenarios" version 230 is consistent with the 2.3.x line, and INFERRED to relate to the `ModsResetter.minExportedScenariosVersion` and `ScenarioExporter` scenario-mod mechanism.

**Assessment:**
- **HIGH CONFIDENCE the API is effectively frozen.** No public build since 2022-09-09, and the developer has moved on to RoI 2.
- **The load-time registry API is stable from 2019 to the final build.** All of ExportStuffMod's IL references resolve, and ROI-CustomMod's and pjf's APIs are present.
- **The live-state API is stable from 2021 to the final build.** Every ROIData member checked is present except `TimeManager.canAdvanceTime`.
- **Runtime semantics did change.** The `Recipe.gameDays` difficulty dependency is one example. We should code against the installed decompiled source, not the 2019 examples.
- **Steam can still update or roll back the files.** `previousbuild` and `noburst` branches exist. Our mod should record `GameVersion.Get()` (`GameVersion.cs`, logged by `GameBootstrapper.DebugDump`) and refuse or degrade on a mismatch.
