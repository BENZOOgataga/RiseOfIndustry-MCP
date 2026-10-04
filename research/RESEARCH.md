# Rise of Industry MCP: technical reconnaissance

Phase: reconnaissance only. Nothing in this repository is production code.
Date: 2026-10-03.
Target: the **original Rise of Industry** (Dapper Penguin Studios, Steam App **671440**). Rise of Industry 2 was not researched and is out of scope.

This file is the entry point. The other deliverables are:

| File | Purpose |
|---|---|
| `DATA-MAP.md` / `data-map.json` | Concept → assembly/type/member mapping with confidence and safety notes |
| `ARCHITECTURE.md` | Recommended implementation architecture and rejected alternatives |
| `RISKS.md` | Crash, performance, threading, coupling and security risks with mitigations |
| `PRD-NOTES.md` | Concrete requirements and acceptance criteria for the implementation phase |
| `notes/*.md` | Detailed evidence per topic (file:line references into the decompiled build) |
| `tools/` | Disposable research tooling (static analysis only) |
| `references/README.md` | Third-party repositories cloned for reading |

Confidence labels used everywhere:

- **CONFIRMED**: read directly in the decompiled source of the installed build, in the game files, or in the bytes of a save copy.
- **HIGH CONFIDENCE**: strongly implied; one link not directly observed (typically a value stored in Unity prefab/asset data).
- **INFERRED**: reasoned from naming or usage; not verified.
- **UNKNOWN**: not determined.

---

## 1. Safety statement for this phase

What was done:

- Read-only listing of the Steam install, Steam metadata, `%APPDATA%\RiseOfIndustry` (names, sizes, timestamps only), the LocalLow log folder and the PlayerPrefs registry key.
- Managed assemblies **copied** into `research/_local/managed/` and decompiled there with ilspycmd 11.1.0.9782.
- Three saves **copied** into `research/_local/saves/`; SHA-256 of original and copy verified identical before analysis and the copies re-verified after analysis (`research/_local/saves/HASHES.txt`). Only the copies were parsed.
- The game's `resources.assets` was opened read-only with UnityPy to read the `GameVersion` asset and definition assets.
- Passive process metadata via `Get-Process` / `Win32_Process` (path, start time, thread count, memory, loaded module names).
- Public repositories cloned into `research/references/` (gitignored contents). Two third-party mod DLLs decompiled statically. Nothing third-party was built or run.

What was **not** done: no process attach, no memory read or write, no injection, no debugger, no suspension, no UI/keyboard/mouse automation, no mod installed, no file written into the install directory or `%APPDATA%\RiseOfIndustry`, no game configuration change, no save opened in place. The running game (PID 20300) was not touched beyond reading its process metadata.

`research/_local/` (assemblies, decompiled source, save copies, dumps, venv) is gitignored: it contains copyrighted game code and the user's personal saves.

---

## 2. Installed game and build

All CONFIRMED (details and commands in `notes/install-runtime.md`).

| Item | Value |
|---|---|
| Steam App ID | 671440 ("Rise of Industry") |
| Steam build id | 9064059 (depot 671441, manifest 8360881793189550917) |
| Game version | **2.3.3, build 0507b**, `Steam - Public`, commit `76359e59644ebf55cafacf9a50b3d19800df22d7` (from the `GameVersion` ScriptableObject in `resources.assets`, path_id 40454) |
| Save format version | 2304 (`GameVersion.savegameVersion`; also in save headers) |
| Unity | 2018.4.11 (exe FileVersion 2018.4.11.7379119) |
| Scripting backend | **Mono** (`MonoBleedingEdge\EmbedRuntime\mono-2.0-bdwgc.dll`), .NET 4.x API level (`scripting-runtime-version=latest`) |
| GC | Boehm (bdwgc), non-incremental in Unity 2018.4 (HIGH CONFIDENCE) |
| Bundled libraries | `0Harmony.dll` 1.1.0.0 (Harmony 1.x API), `Newtonsoft.Json` 11.0.2, `LZ4.dll` (lz4net 1.0.10.93), Unity.Entities/Jobs/Burst, BehaviorDesigner |
| UI language | French (`appmanifest` and PlayerPrefs `GameOptions_Language = fr-FR`) |
| Game status upstream | Last public build 2022-09-09; no further RoI 1 updates expected (HIGH CONFIDENCE, `notes/public-projects.md` §4) |

### Paths

| Purpose | Path |
|---|---|
| Steam library | `C:\Program Files (x86)\Steam` |
| Install dir | `C:\Program Files (x86)\Steam\steamapps\common\RiseOfIndustry` |
| Executable | `...\RiseOfIndustry\Rise of Industry.exe` |
| Managed assemblies | `...\RiseOfIndustry\Rise of Industry_Data\Managed\` (`Assembly-CSharp.dll` 3.66 MB, `Assembly-CSharp-firstpass.dll`) |
| Local mods (code + content) | `<install>\Mods\<ModName>\` (does not exist yet). Resolved relative to the process working directory, which Steam sets to the install dir |
| Dev mods | `<install>\Rise of Industry_Data\StreamingAssets\DevMods\` (one content-only scenario mod) |
| Workshop mods | `C:\Program Files (x86)\Steam\steamapps\workshop\content\671440\` (one subscribed item 1731403319, content-only JSON) |
| Saves | `%APPDATA%\RiseOfIndustry\*.sav` (Steam Cloud) |
| Keybindings | `%APPDATA%\RiseOfIndustry\Keybindings.json` |
| Unity log dir | `%USERPROFILE%\AppData\LocalLow\Dapper Penguin Studios\Rise of Industry\` (`output_log.txt` is 0 bytes, no `Player.log` present) |
| PlayerPrefs | `HKCU\Software\Dapper Penguin Studios\Rise of Industry` (mod enable/disable list, mod order, language, autosave settings) |

### Running process (passive observation)

PID 20300, started 12:15, 132 threads, ~5.8 GB working set, **~8.2 GB private bytes**, ~38,000 CPU-seconds in ~4 h. The managed heap is large; this drives several stability requirements (RISKS.md R-PERF-1).

---

## 3. Modding system

All CONFIRMED in `ProjectAutomata/ModLoader.cs`, `CodeLoader.cs`, `Mod.cs`, `GameBootstrapper.cs` unless marked (`notes/runtime-lifecycle.md` §1).

- The game has an **official C# code-mod loader**. A mod is a folder containing `desc.json` (`{author, name, version, versionString, dependencies, description?}`) and optional `code/`, `assets/`, `content/` subfolders.
- Mod folders are discovered in `Mods/` (relative to CWD), `StreamingAssets/DevMods`, and Steam Workshop subscriptions.
- For every `*.dll` in `code/`, `Assembly.LoadFile` + `GetTypes()` run, and every type assignable to `ProjectAutomata.Mod` (an abstract `MonoBehaviour` with virtual `OnModWasLoaded()` / `OnAllModsLoaded()`) is `AddComponent`ed to the persistent (`DontDestroyOnLoad`) ModLoader GameObject. The component lives for the whole process, so it gets `Update`/`LateUpdate` and coroutines in the main menu and in game.
- Mods load in the **boot scene**, before the intro and main menu (`GameBootstrapper.BootstrapAsync`). Code mods are only gated by "not demo".
- Harmony 1.1 is shipped and the game creates its own `HarmonyInstance`; mods may patch. A read-only observer does not need Harmony.
- `content/*.json` files use the shape `{"type": "ProjectAutomata.X", "object": {...}, "override"?: name, "components"?: [...]}` and register ScriptableObject definitions into `GameData` by name.
- Failure modes that matter for the future observer (CONFIRMED):
  - A folder in `Mods/` without `desc.json` silently stops **all** mods from loading.
  - An exception in `GetTypes`, `OnModWasLoaded` or `OnAllModsLoaded` permanently disables the mod (PlayerPrefs) in release builds and halts boot on a quit-only error popup.
  - A duplicate mod `name` throws outside any try/catch during boot (HIGH CONFIDENCE stuck boot).
  - Any enabled mod is recorded in every save header; loading such a save without the mod shows a "missing mods" dialog. The version string gets a `*`. Achievements are **not** disabled by mods: `AchievementManager.disableWithMods` is 0 in both the menu and game scenes (CONFIRMED from scene assets).

Public evidence that code mods work on the final build: ROIData (2021, 2.x build) read live state from a code mod; "Rise Of Industry Overhaul" on Nexus (December 2025) is a code mod in `Mods/<name>` (`notes/public-projects.md`).

---

## 4. Relevant existing projects

Details: `notes/public-projects.md`, clones listed in `references/README.md`.

| Project | What it is | Relevance |
|---|---|---|
| roiroy/ExportStuffMod (2019-05) | `Mod.OnAllModsLoaded` dumps recipes + building costs from `GameData.instance.GetAssets<T>()` as JSON into the Unity log | Proves code-mod loading and static registry access. Does **not** prove live state, logistics, threading or IPC. Every API it calls still exists; `Recipe.gameDays` now reads `GameParametersManager` and would likely throw at boot (INFERRED) |
| sanasol/ROI-CustomMod (2019-05) | Ancestor of ExportStuff; polls `World.isWorldReady` from a `System.Timers.Timer` thread, adds a console command | Shows an anti-pattern (off-main-thread access) |
| rockymine/RiseOfIndustry "ROIData" (2021, binary only) | Code mod reading money, profit, shops, demand, loans, pollution, tech tree, buildings on `onDayEnd`, POSTing to a web server | Strongest evidence that live state is readable from a code mod on a 2.x build |
| pjf/TransportCostsRebalanced (2018) | Mutates `Formula` assets at load | Confirms `GameData.GetAsset<Formula>(name)` and the `Mods/` folder; write mod, not reusable |
| RiseOfIndustry-AlwaysNightMod (2026) | Binary-patches `Assembly-CSharp.dll` | Must not be copied; confirms latest version string 2.3.3 : 0507b |

No public project reads destination slots, Max Send/Min Keep, or exposes an MCP/IPC channel.

---

## 5. Save format findings

Details: `notes/save-format.md`; tooling `tools/save-inspect/`.

- File = lz4net `LZ4Stream` framing (1 MiB chunks, LEB128 varint headers, raw LZ4 blocks). CONFIRMED on three copies.
- Inside: `int32 headerLen`, a BinaryFormatter `SavegameHeader` (name, timestamp with **1971-01-01 epoch**, saveFormatVersion 2304, build "0507b", mods, module; no thumbnail, no company name), then a **custom, self-describing tagged format** (`ProjectAutomata.Serializer`): managers → entities → components → fields **by field name**. Some values are vanilla MS-NRBF blobs; some are per-class opaque byte arrays (`ISavegameSerializable`).
- Entity references are GUIDs from `GuidMapper`; definition references are `{asset name, type}`.
- All three copies parse to the last byte with a pure-Python parser, without executing game code: ~1.6 s for a 2.8 MB save (23.8 MB decompressed), ~3.2 s for 9.6 MB. Header-only parsing takes ~3 ms.
- Persisted (CONFIRMED in bytes): date, buildings (GUID, prefab, tile x/y, rotation, owner, name, flags), recipe, production counters and daily history, storage (per product), max-accepted maps, **all `ManualDestinationSlot` fields including `_minStoredAtSource`**, transport requests, vehicles, money balances, loans, ~3-year monthly bills, cities, shops (sold list, price modifiers, deliveries, sales/demand history), regions, permits, market modifiers/trends, tech unlock states and research queue/progress, AI players.
- Not persisted: base prices, recipe definitions, display names, shop `_demand` (recomputed on load). These are static asset data.
- Building GUIDs are stable across saves (2123 of 2146 shared between two saves 15 game-months apart).
- The game writes saves with a non-atomic `File.WriteAllBytes` on a ThreadPool thread; external readers must copy and verify stability before parsing.

Conclusion: saves are a viable **offline/fallback** source (game not running) and a useful cross-check, but they are only as fresh as the last save or autosave (default autosave interval 1800 s real time).

---

## 6. Inspection methodology

1. Locate install via running process path and `appmanifest_671440.acf`; confirm App ID and build.
2. Copy `Managed/*.dll` → `research/_local/managed/`; decompile `Assembly-CSharp` and `-firstpass` with `ilspycmd -p` (5,349 `.cs` files; 2,101 in `ProjectAutomata/`).
3. Read version data from `resources.assets` with UnityPy (`tools/asset-inspect/game_version.py`).
4. Static source analysis split by domain (logistics, buildings/production, company/finance/time, cities/shops/markets/world, tech/static content, runtime lifecycle/threading) with file:line evidence; key claims spot-checked a second time (Max Send chain, `Utils.GetSafe` insert, `MoneyManager.GetBalance` insert, vehicle ThreadPool update, `Player.humanPlayer`).
5. Save copies parsed structurally (`tools/save-inspect/`).
6. Public research: GitHub, Steam Workshop/discussions, wiki, Nexus. Repos cloned shallow into `research/references/`.
7. Static definition dump attempt from `resources.assets` with UnityPy + TypeTreeGeneratorAPI (`tools/asset-inspect/dump_definitions.py`); see §9 for status.

Reproducible commands are listed at the end of each note file.

---

## 7. Major technical findings

1. **A supported, in-process, read-capable entry point exists**: the official code-mod loader. Mods are MonoBehaviours on a persistent GameObject and can run main-thread code every frame. (CONFIRMED)
2. **Max Send is not a per-route value.** The UI "max amount" of a destination slot is `ManualDestinationSlot.maxAcceptedAtDestination`, which reads the **destination building's** storage cap for that product (`IProductStorage.GetMaxAccepted(product)`), or the shop's current demand when `autoMaxAccepted` is on. It caps destination stock including in-flight deliveries and is shared by every origin shipping that product to that destination. 0 = unlimited. (CONFIRMED member chain; label-to-member link HIGH CONFIDENCE because label text lives in prefabs.)
3. **Min Keep is per-slot**: `ManualDestinationSlot._minStoredAtSource` (0..99, `int.MaxValue` = keep all). (CONFIRMED)
4. Per-trip quantity is derived (`ManualDestinationManager.GetRequestedAmount`): `min(vehicle capacity, origin stock − minKeep, room under max at destination)` with contract/event caps. (CONFIRMED)
5. **Several "getters" mutate state.** `Utils.GetSafe` inserts missing keys and is used by `ProductSpecificProductStorage.GetMaxAccepted`, `CanReserve`, `PermitManager.GetPermitOwner/GetPermitCost`, `Shop.GetDeliveredByActorCount`/per-actor sales getters, `GameDataManifest.GetAssetsRO`. `MoneyManager.GetBalance/GetRawBalance` insert a 0 balance; `GuidMapper.GetGUIDForObject` mints GUIDs. The observer must read underlying dictionaries with `TryGetValue`. (CONFIRMED)
6. **All reads must be on the Unity main thread.** Game collections are unsynchronized; `ManagerBehaviour<T>.instance` uses Unity APIs; `ListPool`/`Formula` pools are static and not thread-safe; vehicle movers run on ThreadPool workers across frames. (CONFIRMED)
7. **No gameplay state lives in ECS**; ECS only renders resource nodes. (CONFIRMED)
8. **Stable identifiers**: actors `Actor.id` (int, persisted counter); regions `Region.id` (Guid, persisted); definitions by asset `name`; buildings have no id field: use origin `tile` + `prefab.name` (persisted via constructor params), optionally the save GUID read non-mutatingly from `GuidMapper.objMappings`. Vehicle ids are reassigned per trip. Destination slots have no id. (CONFIRMED)
9. **Display strings are localized in place** (French here). Internal asset names are the only stable keys; English names come from the en-US `LanguageData` assets via key `"{type}.{assetname}.{field}"` lower-cased without spaces. (CONFIRMED mechanism)
10. **History is limited in-game**: money ledger is monthly totals for ~3 years; production/shop history is daily for ~1–2 years; **global prices have no history at all** (only current modifier + trend). The MCP side must sample and persist history it wants. (CONFIRMED)
11. **"Company" is an `Actor`** (`HumanPlayer`/`AiPlayer`) with components (`MoneyAgent`, `LoansAgent`, `CompanySharesAgent`, `CompanyStats`, `ActorStatisticsAgent`, `TechTreeAgent`). Use `Player.humanPlayer`, not `Player.activeActor` (which UI/debug can switch). (CONFIRMED)
12. **The "State" is a government actor** that sells raw resources (market price × markup) and pays for every shop sale. There is no separate "World Market" class: `GlobalMarket` is the price index (base price × (1 + modifier), trend). No import/export/harbor trade logic exists. (CONFIRMED / INFERRED for absence)
13. The game has **no save/unload events**; lifecycle must be inferred from Unity scene events plus `World.isWorldReady`. Quitting kills the process (`Process.Kill()`), so nothing in the observer can rely on shutdown callbacks. (CONFIRMED)
14. The in-game DevConsole marks the game as cheated for almost any command; the observer must never touch it. (CONFIRMED)
15. Runtime Unity logging appears disabled for this install (0-byte `output_log.txt`, no `Player.log`) (INFERRED); the observer needs its own log file.
16. **Asset data overrides several code defaults** (CONFIRMED from `resources.assets` / game scene): AI players and the State have infinite money (AI cash is meaningless); monthly upkeep is 2.5 % of base cost; one game day is 8 s at 1× (speed levels 1/3/6/10); market modifiers update every 15 days; mods do not disable achievements. All 30 formula texts (dispatch cost, demand, prices, research) are known (`notes/static-dump.md`).

---

## 8. Data source classification (summary)

The full per-concept classification is in `DATA-MAP.md`.

| Class | Meaning | Examples |
|---|---|---|
| **STATIC** | Game definitions in `GameData` (assets / mod content). Same for every save of a given game module; changes only with game update or content mods | products, recipes (inputs/outputs/days), building types (base cost, recipes, storage slots, module counts), tech tree nodes/prereqs/cost formulas, permit types, settlement tiers, money bill categories, formulas, en-US names |
| **SAVE** | Persisted in `.sav` and restored on load | buildings, recipes selected, storage, destination slots, Min Keep, max-accepted maps, money, loans, bills, tech state, regions/permits, cities/shops (minus `_demand`), market modifiers, AI players, date |
| **RUNTIME** | Only in live memory (recomputed or transient) | shop `_demand`, base prices (`GlobalMarket` value/price), cached paths and route distance, dispatch cost estimates, requirement status flags/notifications, vehicle positions, research daily progress, pause/speed, scene state, current auction progress |
| **DERIVED** | Computed deterministically by observer or MCP from the above | effective Max Send (with auto), per-trip amount, production per 30 days, theoretical capacity, blocking reason for AI buildings, supply/demand ratios, production graph, controlled regions, English names, price history (sampled) |

---

## 9. Static asset extraction status

- `GameVersion` was decoded by hand from raw bytes (CONFIRMED values above).
- `tools/asset-inspect/dump_definitions.py` decodes selected definition classes from `resources.assets` and scene files using typetrees generated from the copied assemblies. Output goes to `research/_local/static-dump/` (gitignored; game data). Obtained (CONFIRMED, `notes/static-dump.md`): 226 products, 225 recipes, 339 tech unlocks, 26 tech trees, all 30 formula texts, 24 money bill categories, settlement tiers/types, loan infos, 171 building prefabs with key component values (storage slots, upkeep %, efficiency tables, max modules, fleets, name formats, shop kinds), and game-scene manager values (`secondsPerDay` 8.0, speed levels [1, 3, 6, 10], `disableWithMods` 0, market update interval 15 days).
- Notable corrections to code defaults found this way: monthly upkeep is **2.5 %** of base cost (`Upkeep.buildingCostPercentage` = 0.025 in prefabs; code default 0.25); efficiency upkeep multipliers equal the output multipliers {0.25 … 2.0}; default efficiency index is 3 (100 %); hubs take at most 3 modules; **AI players have `_infiniteMoney` = 1** (their cash balance is not meaningful).
- The production design does **not** depend on offline asset extraction: the observer reads definitions at runtime from `GameData.instance`, which already reflects the active game module and content mods. Offline dumps are for documentation and test fixtures only.

---

## 10. Unresolved questions

| # | Question | Why it matters | How to resolve (implementation phase) |
|---|---|---|---|
| U1 | Exact on-screen labels bound to `maxAmount` / `minAmount` | Confirms the Max Send / Min Keep naming (member chain is CONFIRMED) | Observer experiment E2 in `ARCHITECTURE.md` §11 (compare UI values to exported values) |
| U2 | ~~Serialized values~~ **Resolved from assets** (`notes/static-dump.md` §5-6): `secondsPerDay` = 8.0, `speedLevels` = [1, 3, 6, 10], `disableWithMods` = 0, `MoneyAgent` history range 3 years for players, AI `_infiniteMoney` = 1. Remaining: per-prefab payment-handler formula mapping | - | Confirm at runtime (E1) |
| U3 | Several `NewDay` per frame at top speed? **Resolved (HIGH)**: 10× speed gives 0.8 s per day, so at most one day per frame in normal play | Per-day snapshot granularity | E1 logs `_days` deltas anyway |
| U4 | ~~Formula texts~~ **Resolved** from assets (`notes/static-dump.md` §2), e.g. manual destination dispatch cost (250 + distance × 10) × difficulty × actor | - | Observer exports `Formula.formula` at runtime |
| U5 | Air network `networkName` string; whether ships exist as transport | Transport mode labelling | Runtime enumeration of `World.Networks` |
| U6 | Uniqueness of `Building.tile` for warehouse module buildings | Building ID scheme | Runtime check for collisions on ID build; fall back to GUID |
| U7 | Which current save is loaded (name) at runtime | `game_status` reporting | Inspect `WorldStartupParameters` / `SavegameUI` at runtime; else UNKNOWN |
| U8 | Cost of a full snapshot on a large map (e.g. 2,000+ buildings, 500 vehicles) | Refresh interval tuning | E3 timing/allocation measurement |
| U9 | Whether the Steam process CWD is always the install dir | Local `Mods/` discovery | Observer logs `Environment.CurrentDirectory` on load |
| U10 | Player.log location/state | Diagnostics | Check after first observer run |

---

## 11. Notes index

| Note | Topic |
|---|---|
| `notes/install-runtime.md` | Paths, version, runtime facts, save copy hashes |
| `notes/logistics.md` | Destinations, Max Send / Min Keep, logistic requests, vehicles, side-effecting setters |
| `notes/buildings-production.md` | Buildings, identity, production, recipes, storage, gatherers, static definitions |
| `notes/company-finance-time.md` | Actors/companies, money, ledger, loans, shares, competitors, calendar, speed |
| `notes/cities-shops-markets-world.md` | Settlements, shops, demand, prices, regions, permits, State, contracts, auctions, map |
| `notes/tech-static-content.md` | Tech tree, research state, GameData registry, content mods, localization, difficulty |
| `notes/runtime-lifecycle.md` | Mod loading, managers, scenes, readiness, tick, threading, ECS, console, logging, saving, identity |
| `notes/save-format.md` | Save container, serializer, persisted data, external parsing feasibility |
| `notes/public-projects.md` | ExportStuffMod and other public projects |
| `notes/static-dump.md` | Results of the offline definition dump |
