# Runtime lifecycle, managers, threading and safe observation

> **Errata from the asset dump (2026-10-03, CONFIRMED, see `notes/static-dump.md`):**
> - `AchievementManager.disableWithMods` = **0** in both the menu (level3) and game (level4) scenes: mods do not disable achievements.
> - `TimeManager.secondsPerDay` = 8.0 and `SpeedControls.speedLevels` = [1, 3, 6, 10], so at most one game day per frame at top speed in normal play.
> - Scene files: level0 boot/preload, level2 intro, level3 main menu, level4 game.
> - §8 says reading `.sav` files offline is out of scope: that referred to the research phase's constraints only. Parsing **copies** of saves is analysed in `notes/save-format.md` and recommended as a phase-2 fallback in `ARCHITECTURE.md`.

Scope: the ORIGINAL Rise of Industry (Steam App 671440, Unity 2018.4.11 Mono, build 9064059).
Source: ILSpy decompilation at `research/_local/decompiled/Assembly-CSharp` (paths below are relative to that folder,
`PA/` = `ProjectAutomata/`). This is static reading only. Scene and prefab data such as serialized inspector values and
which GameObjects live in which scene are NOT in the decompiled C#. Anything that depends on them is marked.

Labels: **CONFIRMED** = read in source. **HIGH** = strong inference from source or well-known Unity behaviour.
**INFERRED** = plausible, not verified. **UNKNOWN** = not determinable statically.

---

## 0. TL;DR for the observer mod design

1. Ship a single self-contained `code/*.dll` with a `ProjectAutomata.Mod` subclass. It is `AddComponent`ed to the
   persistent (DontDestroyOnLoad) ModLoader GameObject during the boot scene, before the intro and main menu. It lives
   for the whole process. (CONFIRMED)
2. Never throw from `OnModWasLoaded` / `OnAllModsLoaded`. In a release build, the mod gets permanently disabled through
   PlayerPrefs and the boot sequence halts on an error popup. (CONFIRMED)
3. Do ALL game-state reads on the Unity main thread. Run the MCP transport on a background thread and marshal requests
   through a queue that the Mod's `Update`/`LateUpdate` drains, with a time budget. (HIGH)
4. Vehicle movers are updated on ThreadPool workers that run from `VehicleMovementManager.Update` until the next
   frame's `Update`. Read vehicle `transform`s, not mover internals, or call
   `VehicleMovementManager.instance.WaitParallelUpdate()` first, as the save system does. (CONFIRMED)
5. "Save loaded and simulation ready" means the `game` scene is loaded, `World.instance.isWorldReady` is true, and the
   loading screen has finished. The best primitives are polling, or a `WorldReadyEvent` receiver registered on the
   game-scene `EventDispatcher` plus waiting for the first `TimeManager.onDayStart`. Mod components do NOT receive
   `IWorldReadyListener` callbacks (§3.4). (CONFIRMED)
6. The safest periodic hook is `TimeManager.onDayStart`/`onMonthStart`. Each delegate is invoked in its own try/catch
   (CONFIRMED). Throttle it, because days get very short at high speed.
7. Do NOT call `SavegameManager.CreateSavegame()`, `GuidMapper.GetGUIDForObject()`, or `DevConsole.Console.ExecuteCommand()`
   from the observer. All of them have side effects (§6, §8, §9).
8. Merely having the observer mod enabled has side effects. Saves record it in their header ("missing mod" dialog
   later). The version string gets a mod marker. Achievements may be disabled and persisted into saves when the
   prefab flag `disableWithMods` is set (§1.6).

---

## 1. Mod loading

### 1.1 Where `"Mods"` resolves
- `ModLoader.userModsPath` is a public string field whose default is "Mods" (`PA/ModLoader.cs:37`). It is used raw:
  `FindLocalMods` receives it unchanged and checks and enumerates it with `Directory.Exists` / `Directory.GetDirectories`
  (`PA/ModLoader.cs:274-285`). It is therefore relative to the **process current working directory**. CONFIRMED.
- No `Directory.SetCurrentDirectory` / `Environment.CurrentDirectory` anywhere in either assembly. CONFIRMED (grep).
- `ScenarioExporter.GetExportedScenariosModPath` builds the path explicitly: it takes the parent folder of
  `Application.dataPath` (everything before its last `/`) and combines it with `userModsPath` and the scenario sub-path
  (`PA/ScenarioExporter.cs:50-52`), i.e. `<install>/Mods/...`. The developers therefore expect CWD == install dir.
  CONFIRMED (expectation), HIGH (Steam launches with CWD = install dir). If the game is started from a shortcut
  with a different CWD, local `Mods/` mods silently won't load. INFERRED.
- Other sources: the `DevMods` folder under `Application.streamingAssetsPath` (`PA/ModLoader.cs:32`), and Steam Workshop
  subscriptions via `SteamUGC.GetItemInstallInfo`, only when `SteamManager.Initialized` is true (`PA/ModLoader.cs:78-91, 286-289`).
  CONFIRMED.
- The serialized fields `debugSkipLocalModsLoading` / `debugSkipSteamModsLoading` (`PA/ModLoader.cs:41-43`) are
  never read. CONFIRMED (grep).

### 1.2 When `LoadMods` runs (boot scene, before intro and main menu)
`PA/GameBootstrapper.cs:34-98` (`Start` → `BootstrapAsync` coroutine). Order of steps (behaviour summarised from the
decompiled source; not reproduced here):
1. Only in the full version (the `GameVersion` is not the demo):
   1. `ModLoader.InitializeModdingApi` creates the game's Harmony instance, id `com.riseofindustry.dapperpenguinstudios`.
   2. `GameData.instance.EnableAllOwnedManifests`.
   3. `ModLoader.LoadAssets`.
   4. `LoadMods` is stepped through by the coroutine (called with the arguments 0.5 and 0.9).
   5. If `ModLoader.loadingModsFailed` is set, the coroutine ends here: **boot stops**.
   6. Otherwise `GameData.instance.DisableAllManifests`, then a forced garbage collection.
2. DLC ownership is cached (`DlcManager.CacheDlcOwnership`).
3. The next scene is loaded: "MainMenu" when the command line contains `no-intro`, otherwise "Intro".

- Code mods load only in the full (non-demo) version. No other setting or toggle gates code mods. CONFIRMED.
- Order inside `ModLoader.LoadMods` (`PA/ModLoader.cs:186-205, 358-415`): FindMods → read disabled list from
  PlayerPrefs → PreLoad (desc.json) → order → for each enabled mod `LoadMod` (code, then assets, then content), then
  for each mod `OnAllModsLoaded`. CONFIRMED.
- `LoadCode` (`PA/ModLoader.cs:417-437`): for every `*.dll` in `code/`, `CodeLoader.LoadDLL` loads the file with
  `Assembly.LoadFile` and then enumerates `Assembly.GetTypes` (`PA/CodeLoader.cs:19-35`). Each mod type is then added
  as a component on the ModLoader GameObject (`AddComponent`), and its `OnModWasLoaded` is called. Unity runs
  `Awake`/`OnEnable` synchronously inside `AddComponent`, and `Start` comes on a later frame. CONFIRMED (code), HIGH (Unity semantics).
  - Every type assignable to `Mod` is AddComponent'ed, **including abstract ones**. Do not ship
    an abstract base class deriving from `Mod`. CONFIRMED (no abstract filter), HIGH (AddComponent on abstract type errors).
  - `GetTypes()` throws `ReflectionTypeLoadException` if any referenced assembly cannot be resolved. `Assembly.LoadFile`
    does no probing in the mod folder. So: one merged DLL, referencing only assemblies in `RiseOfIndustry_Data/Managed`.
    HIGH.
- Harmony is the 1.x API (namespace `Harmony`, type `HarmonyInstance`) (`PA/ModLoader.cs:6, 178`). CONFIRMED.

### 1.3 `validateMod` filter
`ModLoader.validateMod` is a public event whose handlers take a `ModDesc` and return a bool (`PA/ModLoader.cs:76`). It is
checked in `PreLoadMod` (`:326`). The only
subscriber is `ModsResetter.ValidateExportedScenarios`. It rejects (and **deletes from disk**) the user-scenarios mod
when its version is below `minExportedScenariosVersion` (`PA/ModsResetter.cs:141-153`). It does not affect other mods.
CONFIRMED.

### 1.4 Failure modes during load (important)
- **Folder without desc.json**: `PreLoadMod` returns false with `DESCRIPTION_JSON_NOT_FOUND`, which is not `SKIP`.
  `PreLoadMods` then returns false, and the `LoadMods` coroutine exits early (`PA/ModLoader.cs:192-195, 301-320`). Result: **no
  mod at all is loaded**, silently (`loadingModsFailed` is not set, so boot continues). CONFIRMED. Keep the `Mods/`
  folder clean.
- **desc.json parse error**: `OnModError` is called with no mod and sets `loadingModsFailed` → the `GameBootstrapper`
  coroutine ends → boot halts and the ErrorPopup (Exit button only) shows (`PA/ErrorPopupViewModel.cs:24-48`). CONFIRMED.
- **Duplicate mod `name`** (e.g. same mod in `Mods/` and Workshop): `_allModsByName.Add` throws outside any try
  (`PA/ModLoader.cs:345`). The exception escapes the bootstrap coroutine, which dies, and the game is stuck at boot.
  HIGH.
- **Exception in LoadMod / OnModWasLoaded / OnAllModsLoaded / GetTypes**: `OnModError` is called with the mod
  (`PA/ModLoader.cs:463-472`), in this order (behaviour summarised from the decompiled source; not reproduced here):
  1. The exception is logged with `Debug.LogException`.
  2. In a non-debug build (`Debug.isDebugBuild` false), and only when a mod is attributed, that mod is disabled
     (`DisableMod`). The disabled list is persisted in PlayerPrefs under the key
     `ProjectAutomata.ModLoader.DISABLED_MODS_PPKEY`.
  3. `loadingModsFailed` is set.
  4. The `modError` event is raised with the mod, its path and the exception; this shows the error popup.

  After this, remaining mods are skipped (the load loop exits early, `:374, :391`) and the boot halts. On the next launch the mod
  is disabled until re-enabled in the Mod Manager. CONFIRMED.
- Exceptions thrown later (e.g. in the Mod's `Update`) are just logged by Unity and do not disable the mod. HIGH.

### 1.5 Load order quirk
`OrderMods` only resolves `description.dependencies` for mods whose `ModInstance.dependencies` list is non-empty. That list
starts empty (`PA/ModInstance.cs:15`), so on the first ordering every mod lands in the "no deps" bucket, sorted by the
PlayerPrefs integer `ModOrder_<name>` (default -1) (`PA/ModLoader.cs:207-261, 171-174`). Declared dependencies are effectively
ignored, and ties (-1) give an unspecified order. CONFIRMED (logic).

### 1.6 Side effects of having any mod enabled
- `GameVersion` string appends `modIndicatorText` when `hasEnabledMods` (`PA/GameVersion.cs:102`). CONFIRMED.
- `AchievementManager.achievementsEnabled` returns false when both `disableWithMods` and `anyModEnabled` are true
  (`PA/AchievementManager.cs:10, 24-33`). `CreateSavegame` stores that getter value into `SavegameGameModeData`
  (`PA/SavegameManager.cs:214-217`), and loading applies it (`PA/SavegameGameModeData.cs:21-27`). If
  `disableWithMods` is true, every save made with the observer enabled permanently has achievements off.
  CONFIRMED (code path), UNKNOWN (value of the serialized `disableWithMods`).
- `SavegameHeader.Create` records `loadedMods` (name and version) (`PA/SavegameHeader.cs:76`). Loading such a save
  without the mod, or with a lower mod `version`, shows a "missing/outdated mods" dialog (`PA/SavegameUI.cs:150-162`,
  `PA/SavegameHelper.cs:48-75`). CONFIRMED. Never decrease `version` in desc.json.

---

## 2. Manager / singleton pattern

### 2.1 Base classes (CONFIRMED)
| Class | File | Resolution |
|---|---|---|
| `ManagerBehaviourBase<T>` | `PA/ManagerBehaviourBase.cs` | static `_instance`; `Awake` sets it, `OnDestroy` nulls it |
| `ManagerBehaviour<T>` | `PA/ManagerBehaviour.cs:7-55` | `instance`: when `_instance` is null, falls back to `FindObjectOfType<T>` at most once per frame; **off main thread it never calls Find** (the frame number reads as `int.MinValue`) and just returns the cached `_instance` |
| `CachedManagerBehaviour<T>` | `PA/CachedManagerBehaviour.cs:5-29` | `instance`: `FindObjectOfType` only on the first call ever (flag `_instanceGetterInvokedOnce`); later relies on Awake/OnDestroy; flag reset on destroy |
| `PersistentManagerBehaviour<T>` | `PA/PersistentManagerBehaviour.cs:5-17` | Cached + `DontDestroyOnLoad`, destroys duplicates |

How a mod gets managers: `ManagerBehaviour<TimeManager>.instance`, `CachedManagerBehaviour<ModLoader>.instance`, etc.
The static getter is inherited, so `TimeManager.instance` also compiles. Only call these on the main thread.
Off-thread, `CachedManagerBehaviour.instance` may call `FindObjectOfType` (Unity throws), and `ManagerBehaviour.instance`
can return a stale or destroyed object. CONFIRMED (code), HIGH (Unity throws off-thread).

Always use the Unity null check (Unity's overloaded comparison with null, or the implicit bool conversion), not
`ReferenceEquals` or the null-conditional operator, because destroyed managers stay referenced. HIGH.

### 2.2 Persistent vs game-scene
Persistent (DontDestroyOnLoad in code), CONFIRMED: `ModLoader`, `GameModuleRepository` (PersistentManagerBehaviour),
`CustomUpdateManager` (`PA/CustomUpdateManager.cs:172-182`), `AchievementManager`, `InputManager`, `SoundManager`,
`I18n`, `AnalyticsManager`, `EpicManager`, `SteamManager`, `VersionCheck`, `StoreClientActivator`, `SimpleTooltip`,
`LocalizationTable`, `DevConsole.Console` (if its serialized `dontDestroyOnLoad` is set; `DevConsole/Console.cs:37,254`).
`MainMenu` marks `mainMenuMainObject` DDOL (`PA/MainMenu.cs:219-228`), so it survives menu→game and is destroyed on
return to menu (`PA/MainMenu.cs:366, 379`).

Game-scene managers (plain `ManagerBehaviour<T>`, not DDOL): everything simulation-related. HIGH (not DDOL in code;
the scene placement itself is UNKNOWN statically). `EventDispatcher` is used in the main menu
(`PA/MainMenu.cs:416`) and in game, but is not DDOL. So there is most likely **one per scene**, and receivers added in
the menu are lost on scene change. HIGH.

### 2.3 Important managers (all `ManagerBehaviour<T>` unless noted; save-system flags from `[SavegameManagerObject]`)
| Domain | Manager / access | Notes (CONFIRMED unless marked) |
|---|---|---|
| World/map | `World` (`PA/World.cs`), static `World.Size`, `World.Block/Height/Water/Networks/ResourceNodes` | `isWorldReady`, `isLoadedFromSavegame`, `worldName`, `onWorldTileUpdated` |
| Buildings | `BuildingManager` (`PA/BuildingManager.cs`) | `buildingsList` (List<Building>), `GetBuilding(tile)`, delegates `OnBuildingRegistered/OnBuildingDestroyed` (public fields) |
| Companies/actors | `ActorManager` (`PA/ActorManager.cs`), `AiPlayerManager.aiPlayers`, static `Player.humanPlayer`, `Player.activeActor`, `State.instance` | `actors`, `GetActor(int id)`, events `actorRegistered/Deregistered`; `IActor.buildings` |
| Settlements | `SettlementManager.settlements` (List<SettlementBase>) | event `initialBuildCompleted` |
| Regions | `RegionManager.regions`, `GetRegionById(Guid)`, `GetRegionByTile` | regions keyed by persistent Guid |
| Vehicles | `VehicleManager.vehicles` (HashSet<Vehicle>), `VehicleMovementManager` | events `onVehicleActivated/Deactivated` |
| Time | `TimeManager` | `today` (GameDate), `day/month/year`, `secondsPerDay`, events `onDay/Week/Month/YearStart/End` |
| Speed/pause | `SpeedControls` | `level` (-1 = paused), `isPaused`, `speedLevels[]`, `onLevelChangeEvent`; `SpeedChangeEvent` via EventDispatcher |
| Money/market | `MoneyManager`, `GlobalMarket` (`pricesUpdated`), `AuctionsManager` | |
| Logistics | `LogisticRequestManager` (PooledEntityManager), `LogisticTicketManager`, `TrafficManager`, `ConnectivityNetworkManager` | |
| Research | `TechTreeManager` (`initialized` event), per-actor `actor.techTree` | |
| Save/load | `SavegameManager`, `AutosaveManager`, `QuicksaveManager`, static `SavegameStorage` | §8 |
| Scenario/meta | `ScenarioManager`, `CampaignController`, `TutorialManager`, `GameParametersManager` (`world`, `difficulty`), `EndGameManager`, `WorldEventManager` | |
| Events | `EventDispatcher` | §3.3 |

Statics that are NOT cleared on scene unload: `Player.humanPlayer` is only ever assigned
(`PA/HumanPlayer.cs:13`, `PA/Player.cs:16`), and `World.Size/SizeIndex/RegionCount` are static ints
(`PA/World.cs:20-24`). After returning to the menu they point at destroyed objects or old values. CONFIRMED.

---

## 3. Scenes, game states, load flow

### 3.1 Scene graph
Boot scene (contains `GameBootstrapper` + ModLoader; name UNKNOWN) → `"Intro"` (skipped with cmdline `no-intro`)
(`PA/GameBootstrapper.cs:18-22, 95-98`) → `"MainMenu"` → `"game"` (`PA/MainMenu.cs:49-51, 248-270`). There is also
an asset-viewer scene from the Mod Manager (`PA/ModManagerViewModel.cs:85`), `PreloadScene` (loads buildIndex+1), and
the demo flow. CONFIRMED.

Transitions:
- New game / load save / scenario: `WorldStartupParameters.Stage` → `MainMenu.StartGame` →
  `StartGameAsync`: hide menu, fade music, `Resources.UnloadUnusedAssets`, load the "game" scene asynchronously
  (`LoadSceneAsync`), then set `isIngame` (`PA/MainMenu.cs:241-270`).
- **Quickload from in-game** reloads `"game"` → `"game"` without visiting the menu (`PA/QuicksaveManager.cs:162-169`).
  Loading a save from the pause menu is presumably the same. INFERRED.
- Return to menu: `ReturnToMainMenu` (confirmation dialog) or `ReturnToMainMenuImmediately`. Both reset
  `Time.timeScale` to 1, destroy the `MainMenu` object, and load the "MainMenu" scene with `SceneManager.LoadScene`
  (`PA/MainMenu.cs:332-385`). The
  world-gen failure path also uses the immediate variant (`PA/World.cs:250`). `PauseMenuHelper` loads `"MainMenu"`
  directly (`PA/PauseMenuHelper.cs:62`). CONFIRMED.
- Quit: `MainMenu.OnApplicationQuit` kills the game's own process (`Process.Kill` on the current process). The Exit
  dialog also calls `SteamAPI.Shutdown` and then kills (`PA/MainMenu.cs:420-448`). **There is no graceful shutdown.**
  The observer must not depend on OnDestroy/OnApplicationQuit to flush or close. Use background threads (mark them
  `IsBackground`) and flush logs eagerly. CONFIRMED.

### 3.2 World initialization (game scene)
`WorldInitializer.Awake` (`PA/WorldInitializer.cs:38-65`) reads the staged `WorldStartupParameters` and builds the step
list:
- New game: `World.Generate` → `GenerateMeshes` → `FinishInitialization` (`:104-149`)
- Load save: `Savegame.Deserialize` on the bytes read from the save file → `SavegameUpdaterSystem.UpdateSavegame` →
  `World.PrepareWorld` → `LoadSavegame` (terrain, resource nodes, meshes, `SavegameManager.LoadSavegame`) →
  `FinishInitialization`, given the savegame updater (`:171-225`)
- Scenario: same as a load, from `scenario.saveFilePath` (`:151-169`)

These are driven by `WorldLoadingScreen.StartLoadingRoutine` (`PA/WorldLoadingScreen.cs:53-77`), one `MoveNext` per
frame. **Only `HandleLoadState` is in a try/catch, `MoveNext` is not.** An exception inside any load step kills the
coroutine, leaving the game stuck on the loading screen. At the end it sets `Time.timeScale` to 0, has `SpeedControls`
re-apply the current speed level (`UpdateTimeScale`), and deactivates the loading-screen GameObject. CONFIRMED.

`World.FinishInitialization` (`PA/World.cs:272-303`) is a coroutine. Order of steps (behaviour summarised from the
decompiled source; not reproduced here):
1. If a savegame updater was passed in, its `OnWorldReady` is called.
2. `isWorldReady` becomes true.
3. Wait one frame.
4. `OnWorldBecameReady` (with `isLoadedFromSavegame`) is invoked on every `IWorldReadyListener` found by
   `InvokeOnSceneObjects`, then a `WorldReadyEvent` carrying `isLoadedFromSavegame` is dispatched through
   `EventDispatcher`.
5. Wait one frame, then `OnLateWorldBecameReady` is invoked on every `ILateWorldReadyListener` in the scene. This is
   where `TimeManager` starts ticking (§4.2).
6. Wait one frame, then `StaticBatching.UpdateAllRegions`.
7. Wait one frame, then a forced garbage collection.
8. Wait one final frame.

`SavegameManager.LoadSavegame` also schedules `Savegame.FinishLoading()` (camera, game-mode data, metadata) at
end-of-frame (`PA/SavegameManager.cs:56-63`). CONFIRMED.

### 3.3 Events available to a mod
- `EventDispatcher` (`PA/EventDispatcher.cs`): `AddReceiver<T>` (a receiver object plus an `Action<T>` callback),
  `RemoveReceiver<T>`, `DispatchEvent<T>`. **No try/catch in DispatchEvent, and it iterates `Dictionary.Values`.** A receiver that throws,
  or that adds/removes receivers during dispatch, propagates the exception into the dispatching game code. For
  `WorldReadyEvent` that dispatching code is the loading coroutine (stuck loading screen). Wrap every handler body in
  try/catch and never (de)register inside a handler. CONFIRMED.
  - Useful events: `WorldReadyEvent` (carries `isLoadedFromSavegame`) (`PA/WorldReadyEvent.cs`), `SpeedChangeEvent`
    (`PA/SpeedControls.cs:54`), `GameModuleLoadedEvent` (menu), `ExecutingConsoleCommandEvent` /
    `ConsoleCommandExecutedEvent` (`DevConsole/Console.cs:604,607`), `GameActionPerformedEvent`,
    `WorldEventActivatedEvent`, etc.
- C# events on managers: `TimeManager.on*` (exception-isolated, §4.2), `BuildingManager.OnBuildingRegistered/
  OnBuildingDestroyed`, `ActorManager.actorRegistered`, `VehicleManager.onVehicleActivated`, `GlobalMarket.pricesUpdated`,
  `SettlementManager.initialBuildCompleted`, `TechTreeManager.initialized`, `SpeedControls.onLevelChangeEvent`,
  `World.onWorldTileUpdated`. These are not exception-isolated (except TimeManager's). CONFIRMED.
- No game-level "save started/finished", "game unloaded" or "GameStateManager" event exists. CONFIRMED (grep). Use
  Unity `SceneManager.sceneLoaded/sceneUnloaded/activeSceneChanged`. The game itself uses these
  (`ECS/ECSCleaningManager.cs:23`, `PA/AnalyticsDispatchAggregator.cs:41`).

### 3.4 Why `IWorldReadyListener` does not work on a Mod component
`World.InvokeOnSceneObjects<T>` only scans the root GameObjects of the active scene (`SceneManager.GetActiveScene`, `GetRootGameObjects`)
(`PA/World.cs:384-402`). Mod components live on the DDOL ModLoader GameObject, which is in the DontDestroyOnLoad scene,
so they are never called. CONFIRMED. (Workaround: when `sceneLoaded` reports the "game" scene, create a plain new
GameObject in the active scene with a listener component. HIGH, but unnecessary.)

### 3.5 Recommended state machine for the observer (HIGH)
- `MENU`: active scene name is not `"game"`, or `World.instance` is null.
- `LOADING`: in `"game"`, and `World.instance` exists but `isWorldReady` is false, or the loading screen is still active
  (`WorldLoadingScreen.instance` exists and its GameObject is `activeSelf`).
- `READY`: `isWorldReady`, the loading screen is inactive, and at least one `TimeManager.onDayStart` has been
  received since entering the scene (proves `OnLateWorldBecameReady` ran). Alternatively, register a `WorldReadyEvent`
  receiver on the game-scene `EventDispatcher` in `SceneManager.sceneLoaded` (scene objects have run `Awake` by then,
  and the load coroutine needs several more frames), then wait 2-3 frames.
- On `SceneManager.sceneUnloaded` of `"game"`, or `activeSceneChanged` away from it: drop all cached references,
  unsubscribe from TimeManager events (that instance is gone), and go to `MENU`. Handle `game` → `game` reloads.
- `MainMenu.IsShown` (static) while in-game means the pause/main menu overlay is open and `Time.timeScale` is 0
  (`PA/MainMenu.cs:74-84, 164-176`).

---

## 4. Simulation tick, speed, threading

### 4.1 Speed and pause
`SpeedControls.SetLevel` → `UpdateTimeScale`, which sets `Time.timeScale` to 0 when `level` is -1 (paused) and
otherwise to the `speedLevels` entry for the current `level` (`PA/SpeedControls.cs:47-99`). `ForceTimeScale` is used by the `hyperspeed` cheat (`:101-123`). Other writers of
`Time.timeScale`: MainMenu Show/Hide (0 / restore), `PauseMenuHelper`, `EndPopup`, and `WorldLoadingScreen` at the end
of loading (`PA/MainMenu.cs:168-182`, `PA/WorldLoadingScreen.cs:74-75`). `SpeedControls.Update` re-applies overrides
from Tutorial/Scenario/State/FullscreenPanel/MainMenu every frame (`:151-160`). The `speedLevels` values are serialized
(UNKNOWN). CONFIRMED.

### 4.2 Day ticks
`TimeManager.OnLateWorldBecameReady` schedules `NewDay` with Unity's `InvokeRepeating`, first after `secondsPerDay`
seconds and then every `secondsPerDay` seconds (`PA/TimeManager.cs:151-154`). This is scaled time, so it stops at pause and accelerates with speed. `NewDay`
(`:63-104`) increments `_days`, fires `onDayEnd` (plus week/month/year End), updates `day/month/year/today`, then fires
`onDayStart` (plus Starts). The calendar is 30-day months and 12-month years. `InvokeCallback` (`:106-126`) wraps
**each delegate** in try/catch and logs. CONFIRMED. The `secondsPerDay` value is UNKNOWN (serialized). Whether
Unity's `InvokeRepeating` fires more than once per frame at very high timeScale is UNKNOWN; assume days can be
sub-frame at hyperspeed.

Subscriber counts (grep of event subscriptions): onDayEnd 35, onMonthEnd 13, onDayStart 7, onMonthStart 3, onYearEnd 4, onYearStart 1,
week events 0. The daily economy (market, upkeep, settlements, pollution, contracts, stats, AI expansion) runs on
`onDayEnd`. Production runs per frame in `RecipeUser.Update` using `Time.deltaTime` (`PA/RecipeUser.cs:237-247`).
CONFIRMED. So reading on `onDayStart` sees a fully-settled day.

### 4.3 Other update plumbing
`CustomUpdateManager` (DDOL) dispatches `ICustomUpdateble.CustomUpdate` / `ICustomLateUpdatable.CustomLateUpdate` with
per-item try/catch (`PA/CustomUpdateManager.cs:200-262`). `World.Update` flushes tile updates (`PA/World.cs:404-407`).
CONFIRMED.

### 4.4 Multithreading (CONFIRMED unless noted)
The simulation is mostly main-thread, but several systems use the ThreadPool, `Parallel`, or Jobs. Most importantly:
| System | Mechanism | Lifetime across frames? |
|---|---|---|
| **Vehicle movement** `VehicleMovementManager.Update` (`PA/VehicleMovementManager.cs:47-119`) | each mover's `SyncUpdate` on the main thread (writes `transform`, `PA/WaypointMover.cs:~381`), then `ThreadPool.QueueUserWorkItem` → each mover's `ParallelFixedUpdate` (mutates mover `position/rotation/waypointIndex/currentIndex`, tile locks) | **Yes**: joined only at the next frame's `Update` (a wait with a 1000 ms timeout) or by `WaitParallelUpdate` |
| Vehicle path init `WaypointMover.Move` → `Utils.ThreadPoolQueue` with `_Initialize` (`PA/WaypointMover.cs:231-292`) | ThreadPool, guarded by `_updateLock`/`initializationLock` | yes |
| Path validation `PathValidationManager.LateUpdate` (`PA/PathValidationManager.cs:69-146`) | ThreadPool workers set `CachedPath.isValid` | yes (joined next run) |
| Pathfinding `PathfinderBase.FindPath` (dedicated `Thread`, priority Highest, or ThreadPool) (`PA/PathfinderBase.cs`), `StatePathfinder` (`:228,232,352`), `BreadthFirstSearchPlacementFinder` (`:56`), `RegionZones` (`:105,152`), `WorldWater.RecalculateWaterBodies` (`:60`) | Thread/ThreadPool | yes |
| Passive pollution `FindAffectedEffectsJob` (`PA/PassivePollutionEffectManager.cs:79-106`) | Unity Job scheduled on day end, completed next day end | yes (NativeArrays) |
| Traffic heatmap, static batching, CullManager | Jobs/Burst (rendering/visual) | Cull: completes immediately |
| `ComputePollutionStrategy`, `RegionPurchaseAgent`, `SettlementBase`, WG operations | `Parallel.For/ForEach` (blocking on caller) | no |
| Savegame file write `SavegameStorage.SaveSavegame` (`PA/SavegameStorage.cs:113-134`) | ThreadPool `File.WriteAllBytes` after main-thread serialization | yes (I/O only) |

**Conclusion (HIGH):** all reads must run on the Unity main thread, because Unity APIs and `FindObjectOfType` are
main-thread-only and most game collections are plain `List/Dictionary/HashSet` with no locks. Even on the main thread,
avoid reading `WaypointMover`/`TruckMover`/`TrainMover` internals and the `LockingManager` state while workers run.
Use `vehicle.transform.position` (synced on the main thread) or call
`ManagerBehaviour<VehicleMovementManager>.instance.WaitParallelUpdate()` first. The save system does exactly this
(`PA/WaypointMover.cs:365-370`, `PA/LockingManager.cs:340-342`). `CachedPath`/path objects are lock-protected but
mutate concurrently. Treat them as volatile.

**Safest hook (HIGH):** the Mod component's own `Update`/`LateUpdate`. It drains a request queue from the MCP thread
with a per-frame budget (e.g. ≤2 ms) and refreshes a cached immutable snapshot at low frequency. Refresh triggers:
`TimeManager.onDayStart` (or `onMonthStart`) gated by a real-time throttle (e.g. ≥1 s unscaled), plus on-demand
requests. Never block the main thread waiting on the network. The MCP thread only reads the last published snapshot,
handed over by atomic reference swap.

---

## 5. ECS
`Unity.Entities` is used only for **resource-node rendering**: `ECSEntityManager.CreateResourceNode` and friends,
called solely from `ResourceNodeVisuals` (`ECS/ECSEntityManager.cs:23-210`, `PA/ResourceNodeVisuals.cs:75-245`).
`CreateConnectivityTile` exists but has no callers. `ECSCleaningManager` destroys all entities on every
`sceneUnloaded` and on quit (`ECS/ECSCleaningManager.cs:17-54`). `DestroyEntitySystem` is the only system. **No gameplay
state (vehicles, buildings, economy) is in ECS.** Vehicles are MonoBehaviours in `VehicleManager.vehicles`. CONFIRMED.

---

## 6. Existing debug tooling and cheats
- **DevConsole** (`DevConsole/Console.cs`), IMGUI, toggled by `KeyCode.Backslash` (`:25, :281`). Built-ins: `DC_HELP`,
  `DC_INFO`, `DC_CLEAR`, `DC_CHANGE_KEY`, `DC_SHOW_TIMESTAMP`, `DC_SHOW_VERBOSE`, `showDebugLog`, `debugLog`,
  `debugWarning`, `debugError`, `exit`, `quit` (`:262`). Static `Console.ExecuteCommand(string)` is public (`:189`).
  Whether the Console object exists in release game scenes is UNKNOWN. HIGH that it does, because
  `CheatsUsedCondition` (help hint) exists.
- **PAConsole** game commands (`PA/PAConsole.cs:86-143`). All are mutation risks except `echo`, `coordinates`, `pqd`,
  `fps`, `farout` (visual):
  `echo, eval (SendMessage "Evaluate"), hyperspeed <f>, smallloan <f> (money from State to player),
  coordinates, pqd, force_input_unlock, trigger_static_event, trigger_dynamic_event, trigger_forced_static_event,
  trigger_pr_event, offer <product>, fps, farout, advance_tutorial, build <prefab>, advance_town <name>,
  create <product> <qty>, kill (kills selected settlement), reset_help, force_permit_offer, unlock_all_tech,
  force_purchase_shares, unlock_tech <name>, create_top_notification, skip_campaign_step, go_to_campaign_step <i>,
  pathfinding_multithreading_True/False, iworkedhard (unlocks an achievement), ai_debug (PRIVATE builds only)`.
- **Executing ANY console command except `iworkedhard*` counts as cheating.** `ExecutingConsoleCommandEvent` →
  `IsCheatCommand` returns true for everything else → `AchievementManager.achievementsEnabled` is set to false and
  `EndGameManager.usedCheats` and `ScenarioManager.usedCheats` are set to true (`PA/PAConsole.cs:49-84`). That includes
  `echo` and `DC_HELP`. Also, `ExecuteCommandInternal` runs **every** command whose name is a prefix of the input
  (`DevConsole/Console.cs:597-609`), so e.g. `create_top_notification` also matches `create`. The observer must
  never route anything through the console. CONFIRMED.
- Existing dumps: `DebugStartGameLogDump` logs module, tutorial, scenario, and World/Difficulty parameters via
  reflection on `OnLateWorldBecameReady` (`PA/DebugStartGameLogDump.cs`). `AiDebugPanel*` view models exist (PRIVATE
  builds). `RuntimeInspectorNamespace` (third-party runtime inspector) is only used by the modding asset viewer
  (`PA/RuntimeAssets.cs`, `PA/ExitModdingWindow.cs`), not in the game scene. No existing whole-state dumper. CONFIRMED.

## 7. Logging
- No custom `ILogHandler`, no `Debug.unityLogger.logEnabled` changes, no `persistentDataPath` usage (grep). Debug.Log
  goes to the standard Unity player log. CONFIRMED. On Unity 2018.3+ Windows, that file is
  `%USERPROFILE%\AppData\LocalLow\<Company>\<Product>\Player.log` (+ `Player-prev.log`). The 0-byte `output_log.txt` is
  therefore probably a leftover from an older engine version. HIGH. It is also possible the "Use Player Log" player
  setting is off or the game is launched with `-nolog`, but that cannot be checked without touching the install, so
  UNKNOWN.
- Log listeners: `PAConsole.Awake` mirrors every `logMessageReceived` into the DevConsole buffer
  (`PA/PAConsole.cs:463-468`). `BugReporter` keeps the last ~1000 entries via `logMessageReceivedThreaded`
  (`PA/BugReporter.cs:28-47`) and attaches them to bug reports POSTed to a HockeyApp URL (`:26, :62-100`). Anything the
  observer logs can therefore leave the machine if the user files an in-game bug report. Never log secrets or tokens.
  CONFIRMED.
- Implications: keep `Debug.Log` to lifecycle milestones only, because each call captures a stack trace and feeds two
  listeners. Write verbose observer logs to the observer's own file from a background writer, outside the game install
  and outside `%APPDATA%\RiseOfIndustry`. HIGH.

## 8. Save system
- Triggers: autosave, `AutosaveManager.Update` with `Time.unscaledDeltaTime`, so it **counts while paused**. Interval
  `GameOptions.autosaveInterval` = PlayerPrefs `"GameOptions_AutosaveInteval"`, default **1800 s real time**, slots
  default 3, names "Autosave N". Disabled in tutorial and demo (`PA/AutosaveManager.cs:22-79`,
  `PA/GameOptions.cs:149-171`). Quicksave key ("Quicksave") (`PA/QuicksaveManager.cs:123-193`). Manual save and
  overwrite via `SavegameUI.CreateSavegame/OverwriteGame` (`PA/SavegameUI.cs:167-190`). Bug report
  (`PA/BugReporterUI.cs:146`). Scenario export (`PA/ScenarioExporter.cs:31`). CONFIRMED.
- Flow: main thread `SavegameManager.CreateSavegame()` (`PA/SavegameManager.cs:193-237`) →
  `Resources.FindObjectsOfTypeAll<MonoBehaviour>()` → for each `[SavegameManagerObject]` type: invoke
  `OnSavegameSerialize` (side effects: e.g. `WaitParallelUpdate`, LockingManager snapshots), serialize
  `[SavegameSerialized]` fields and `[SavegameEntityList]` entities (assigning GUIDs) → `Savegame.Serialize` (LZ4,
  main thread) → ThreadPool file write to `%APPDATA%/RiseOfIndustry/<name>.sav` (`PA/SavegameStorage.cs:15-27,113-134`).
  `SavegameStorage.isSaving` is inverted: it returns true when NOT saving (`:21`). CONFIRMED.
- Hooking save events: there is no event. Options are Harmony prefix/postfix on `SavegameManager.CreateSavegame` or
  `SavegameStorage.SaveSavegame` (both synchronous on the main thread), or detecting `InputManager.IsInputLocked`.
  Harmony is available. HIGH.
- Do not add `[SavegameManagerObject]` (or other Savegame attributes) to observer types. `CreateSavegame` scans
  `FindObjectsOfTypeAll`, which includes DDOL mod components, so they would be written into saves, and loading would
  then expect them. CONFIRMED (scan scope). `SaveSystemReflection.InitializeReflectionData` also scans
  every type of every loaded assembly (`AppDomain.GetAssemblies`, `GetTypes`) (`PA/SaveSystemReflection.cs:242-262`), lazily on first save/load. A
  mod assembly with unresolvable types would throw there and break saving and loading. HIGH. This is another reason for
  a clean single DLL.
- Reusing the serializer read-only: **not safe and not cheap.** `CreateSavegame` invokes `OnSavegameSerialize` on
  every manager, entity and component (state-touching hooks, blocking waits). It allocates GUIDs for every entity in
  the global `GuidMapper`, walks the whole world (height/biome/water arrays), and reflects every field. It is a
  multi-hundred-millisecond main-thread hitch on large maps (HIGH; the game logs its own timing at `:234-235`). Reading
  the `.sav` files offline is out of scope (forbidden directory). Prefer targeted reads of manager collections.

## 9. Stable entity identity
- `GuidMapper` (`Assembly-CSharp/GuidMapper.cs`): global `Dictionary<Guid,object>` + `Dictionary<object,Guid>`.
  `GetGUIDForObject` **creates** a new GUID (`Guid.NewGuid`) lazily if absent (`:38-47`) — a mutation. GUIDs are assigned on save
  (`SavegameManager.SerializeEntity`, `:244`) and when a building is created for its owner (`PA/Building.cs:298`). They
  are restored on load via `RegisterObjectByGUID` (`PA/SavegameManager.cs:319, 344`). `Initialize()` (clear) runs only
  at the start of `LoadSavegame` (`:15`). Consequences: an entity's GUID is stable across save/load once it has been
  saved, but entities created since the last load/save have **no GUID** until the next save. The map also keeps strong
  refs to destroyed objects and is not cleared on new game. CONFIRMED. Observer: read-only lookup via reflection on the
  private `objMappings` (`TryGetValue`) is OK on the main thread. Never call `GetGUIDForObject`.
- Native IDs that are safer for the observer (CONFIRMED):
  - Actors/companies: `IActor.id` (int) from `ActorManager.GetUniqueActorId()`, counter `_uniqueActorId` is
    `[SavegameSerialized]` (`PA/ActorManager.cs:15-27`); lookup `GetActor(id)`.
  - Regions: `Region.id` (Guid, persisted, `PA/Region.cs:23, 51, 89-98`); `RegionManager.GetRegionById`.
  - Buildings: no id field. Use `(building.tile, prefab name)`. `tile` is the origin tile index (`PA/Building.cs:69,
    285`) and buildings cannot move. Constructor params also store `x, y, rotation, owner Guid` (`:292-300`).
  - Vehicles: `VehicleManager.NextVehicleIndex()` returns a `ushort` index (`PA/VehicleManager.cs:91`). Pooled and
    reused, so it is not globally stable. Vehicles are transient anyway.
  - Settlements: `settlementName` (unique in practice; INFERRED) + region id.
  - Fallback for a session-local id: Unity `GetInstanceID()` (stable for the object's lifetime, not across loads).

## 10. Open items (UNKNOWN, need runtime or asset inspection, not done here)
- Serialized values: `AchievementManager.disableWithMods`, `TimeManager.secondsPerDay`, `SpeedControls.speedLevels`,
  `Console.dontDestroyOnLoad`, the boot scene name, and which scene hosts each `EventDispatcher`.
- Actual Player.log location and content for this install.
- `Debug.isDebugBuild` is presumably false on Steam (HIGH), so the DisableMod-on-error path is active.
