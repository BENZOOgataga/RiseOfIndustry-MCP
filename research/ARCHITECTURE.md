# Architecture recommendation: read-only Rise of Industry MCP

Status: recommendation from the reconnaissance phase. Nothing here is implemented.
Evidence for every game-side claim is in `DATA-MAP.md` / `data-map.json` and `notes/`.

---

## 1. Recommendation in one paragraph

Build a **small in-process observer mod** loaded by the game's own official code-mod loader (`<install>\Mods\RoiMcpObserver\code\*.dll`, a `ProjectAutomata.Mod` subclass). It reads game state **only on the Unity main thread**, in short time-budgeted slices, at a low adaptive rate. It converts what it reads into plain DTOs that hold no references to game objects. A background thread serializes the DTOs to JSON and publishes them as **atomically replaced files** in an exchange directory outside the game and save folders (`%LOCALAPPDATA%\RoiMcp\`). A separate, **out-of-process MCP server** (Python, stdio transport) reads those files, builds indexes and the production graph, computes deterministic derived values, and exposes focused read-only tools. The only message that ever flows towards the game is a "please refresh" nonce file. There are no sockets or pipes in the game process, no Harmony patches, and no calls to anything that mutates state.

The architecture originally proposed in the brief (game → read-only observer → local state/cache → MCP server → client) is confirmed, with three refinements:

1. The cache is a set of **tiered snapshot files** (heartbeat, static, state, history).
2. Freshness comes from an **adaptive refresh plus a refresh-request file**, not from a request/response channel.
3. The MCP server can fall back to an **offline save-file reader** when the game is not running.

---

## 2. Components and process boundaries

```
 ┌──────────────────────────── Rise of Industry.exe (Unity 2018.4 Mono) ───────────────────────────┐
 │                                                                                                   │
 │  ModLoader (persistent GameObject, DontDestroyOnLoad)                                             │
 │   └─ RoiMcpObserver : ProjectAutomata.Mod  (MonoBehaviour)                                        │
 │        ├─ Lifecycle      : scene/world-ready state machine (MENU / LOADING / READY / UNLOADING)   │
 │        ├─ Scheduler      : decides when to capture (day change, interval, refresh request)        │
 │        ├─ Capturers      : main-thread, read-only, time-sliced (≤ budget ms per frame)            │
 │        │     company · buildings · production · logistics · cities/shops · regions · market ·     │
 │        │     tech · vehicles(summary) · static definitions                                        │
 │        ├─ ReadOnlyAccess : the ONLY place that touches game types (allowlisted members,           │
 │        │                   cached FieldInfo getters, TryGetValue on dictionaries)                 │
 │        └─ Publisher      : background thread: DTO → JSON → temp file → atomic replace             │
 │                                                                                                   │
 └───────────────────────────────────────────────┬───────────────────────────────────────────────────┘
                                                 │ files only (no sockets, no pipes)
                                                 ▼
        %LOCALAPPDATA%\RoiMcp\            (exchange directory; never inside the install or saves)
          heartbeat.json      ~1 KB    every 1 s real time
          static.json         ~1-2 MB  once per world load / game-module change
          state.json          ~0.5-3 MB  adaptive (see §5)
          history.json        ~0.2-2 MB  at month start + on request
          refresh-request.json  (written by the MCP server: {"nonce": <int>})
          observer.log (+ .1 rotation, size-capped)
          observer.config.json (optional, read once at boot and on change)
                                                 │
                                                 ▼
 ┌──────────────────────── roi-mcp server (separate process, Python, MCP stdio) ─────────────────────┐
 │  SnapshotStore   : loads + validates files (schema version, game build, seq), keeps last good    │
 │  Indexes         : by building key, name (FR/EN), product, city, region; reverse supplier index   │
 │  Derivations     : rates per 30 days, graph traversal, bottleneck evidence, supply/demand ratios  │
 │  HistoryStore    : SQLite of monthly samples (prices, ledger, production) the game does not keep  │
 │  SaveFallback    : offline reader of the newest .sav COPY when the game is not running (phase 2)  │
 │  Tools           : focused read-only MCP tools (§8)                                              │
 └───────────────────────────────────────────────┬───────────────────────────────────────────────────┘
                                                 │ MCP (stdio)
                                                 ▼
                              Claude / ChatGPT / any MCP client
```

Process boundaries:

- **Game process.** It contains only the observer. If the observer throws inside `Update`, Unity logs the exception and the game continues. Every capturer is wrapped in try/catch and is disabled after repeated faults, as described in §6.
- **MCP server process.** It is fully separate. If it crashes, the game is unaffected, and it can restart at any time and simply reload the latest files.
- **Exchange directory.** This is the only shared state. Writes are atomic replace operations, and the reader validates every file before using it.

---

## 3. Data flow

```
 game frame N        game frame N+1 … N+k                    background thread             MCP server
 ────────────        ─────────────────────                   ─────────────────             ──────────
 Scheduler:          Capturers run section by section,       Serialize DTO graph           Poll heartbeat (1 s)
 "capture due"  ──►  each slice ≤ budget (default 2 ms);  ──► (Newtonsoft 11, shipped) ──►  seq changed?
                     record game date + frame per section    write state.json.tmp          → load + validate
                     copy live collections into owned        File.Replace → state.json     → rebuild indexes
                     buffers before yielding                 heartbeat.seq++               → answer tools
```

Rules that make this safe:

- Capturers never keep game references across frames, except lists of building references that the capturer itself copied in the same frame. Even those are revalidated with the Unity null check before use, because destroyed objects stay referenced.
- DTOs contain only primitives, strings and DTOs, so the background thread never touches a game object.
- Each section records `captured_game_day` and `captured_frame`. The snapshot reports `consistent: true` only when all sections share the same game day. If the day ticks mid-capture, the snapshot is still published, with `consistent: false` and per-section dates.

---

## 4. Observer lifecycle

| Phase | Detection (CONFIRMED mechanisms) | Observer behaviour |
|---|---|---|
| Boot | `OnModWasLoaded` / `OnAllModsLoaded` (boot scene, before the main menu) | Do almost nothing: create the exchange directory, open the log, resolve the reflection table **lazily later**, never throw (an exception here disables the mod permanently and halts boot) |
| MENU | Active scene is not `"game"`, or `World.instance` is null | Heartbeat `state: "menu"`. Drop all cached references. No capture |
| LOADING | `"game"` scene loaded; `World.instance` is non-null but `isWorldReady` is false, or `WorldLoadingScreen` active | Heartbeat `state: "loading"`. No capture. Do not subscribe to `EventDispatcher` (a throwing handler there sticks the loading screen) |
| READY | `isWorldReady`, loading screen inactive, and `TimeManager.today` observed at least once (or first day change seen) | Run the reflection self-check (§6), export static.json, then capture state and serve refreshes |
| PAUSED | `SpeedControls.level` is -1, or `Time.timeScale` is 0 | Keep capturing on the slower paused interval and on refresh requests (players change Max Send / Min Keep while paused) |
| UNLOADING | `SceneManager.sceneUnloaded("game")` / `activeSceneChanged` away from `"game"`, including `game → game` quickload | Abandon any in-flight capture, clear references, heartbeat `state: "menu"` or `"loading"`. Never read statics such as `Player.humanPlayer` or `World.Size` outside READY (they are not cleared) |
| Quit | The game calls `Process.Kill()`: there is no shutdown callback | Nothing to do. All files are always in a valid state thanks to atomic replace. The heartbeat goes stale, and the MCP server detects the dead PID |

The heartbeat contains `pid`, `observer_version`, `game_version`, `savegame_version`, `state`, `game_date`, `speed_level`, `time_scale`, `paused`, `static_seq`, `state_seq`, `history_seq`, `last_capture_ms`, `last_capture_alloc_bytes` (if measurable), `disabled_sections[]`, `last_error`, and `written_utc`.

---

## 5. Snapshot strategy

### 5.1 Tiers

| File | Content (DATA-MAP categories) | Trigger | Expected size |
|---|---|---|---|
| `heartbeat.json` | status only | every 1 s real time (unscaled), in MENU too | < 1 KB |
| `static.json` | products, categories, recipes, building types (with key component values), tech trees and unlocks, formulas, bill categories, tiers, permit types, en-US names for every definition | on entering READY, and when `GameModuleRepository.currentModule` changes | 1–2 MB (226 products, 225 recipes, 171 prefabs, 339 unlocks) |
| `state.json` | game/time, companies (cash, loans, shares, stats), **non-house** buildings (production, inventory, efficiency, upkeep, flags, logistics config, destination slots with Max Send / Min Keep, logistic requests), cities, shops (stock, demand, price), regions/permits, market prices, research state, auctions/contracts, vehicle summary | adaptive, see §5.2 | 0.5–3 MB (the sample save has ~150 player buildings and ~2,000 settlement houses; houses are counted, not listed) |
| `history.json` | ledger by month and category (all retained months), production/consumption monthly totals per building, shop sales/demand monthly totals, BuildingAnalysis series | at `onMonthStart` (detected by polling the date) and on request with `scope: "history"` | 0.2–2 MB |

### 5.2 When to capture `state.json`

Capture when any of the following is true, and the minimum gap since the last capture has elapsed:

- The game day changed and at least `running_interval_s` (default **5 s** real time) has passed. At 10× speed a day is 0.8 s, so this yields roughly one capture every six days of game time.
- The game is paused or a menu is open, and `paused_interval_s` (default **15 s**) has passed. This catches setting changes made while paused. An optional cheap change detector can be added later: a hash of slot settings, max-accepted maps and recipes over player buildings.
- A refresh request carries a new nonce. This is served at the next READY frame with no minimum gap beyond `min_gap_s` (default 1 s).

Adaptive back-off: `effective_interval = max(configured_interval, 100 × last_capture_main_thread_ms / 1000 s)`. In other words, the observer spends at most about 1 % of wall time on the main thread. If one capture exceeds a hard ceiling (e.g. 50 ms summed main-thread time), the next interval doubles and the heartbeat reports it.

### 5.3 Time slicing

Each section is an iterator that yields after `frame_budget_ms` (default 2 ms) of main-thread time, measured with `Stopwatch`. Large sections such as buildings resume on the next frame from a copied list. Slices run in `LateUpdate`, after the game's own `Update`. When a vehicle summary is requested, slices read `transform.position` and the job fields only, never the mover internals that ThreadPool workers mutate.

### 5.4 Freshness contract

Every MCP response carries a `meta` block:

```json
"meta": {"source": "live", "observer_state": "ready", "snapshot_seq": 812, "game_date": "Y78-06-24",
         "captured_utc": "...", "age_s": 3.2, "consistent": true, "paused": false, "stale": false}
```

The MCP server may first write `refresh-request.json` with a new nonce and wait up to `refresh_wait_s` (default 3 s) for `state_seq` to advance. It does this when the caller passes `fresh: true`, or when the age exceeds a tool-specific threshold. If the wait times out, it answers from the last snapshot and sets `stale: true`.

---

## 6. Failure handling inside the observer

- **Never throw out of `OnModWasLoaded` or `OnAllModsLoaded`.** Wrap them in try/catch and log. A throw there disables the mod in PlayerPrefs and halts boot.
- **Reflection self-check at READY.** Resolve every private field the observer needs once, into a cached table: `GuidMapper.objMappings`, `PermitManager._permits`, `ProductSpecificProductStorage._maxAcceptedMap` / `_storage`, `Shop._deliveredByActors`, `TransportJob._origin…`, `Loan.freeMonthsLeft`, and so on. A missing member does not throw. It disables the sections that depend on it and lists them in `disabled_sections`.
- **Version gate.** If `GameVersion` is not 2.3.3 / 0507b / savegame 2304, run in "unverified build" mode. Capture still runs, the heartbeat flags it, and the MCP server surfaces it in `game_status`.
- **Per-section circuit breaker.** Every capturer runs in try/catch. After 3 consecutive faults the section is disabled until the next world load. The rest of the snapshot is still published.
- **Bounded resources.** One capture in flight at most, and one serialization buffer that is reused. The log is size-capped (e.g. 1 MB × 2 files). There are no unbounded queues, and there is no output other than the fixed file set.
- **Kill switch.** If `%LOCALAPPDATA%\RoiMcp\observer.disabled` exists, or the config sets `enabled: false`, the observer only writes the heartbeat (`state: "disabled"`). The background thread checks this once per second, without touching the game.
- **Logging.** Write to the observer's own file, not `Debug.Log`. Reserve `Debug.Log` for at most a few lifecycle lines. The bug reporter can upload `Debug.Log` content, and runtime logging appears disabled on this install anyway.

---

## 7. Read-only boundary (structural, not just by convention)

```
   UNTRUSTED/EXTERNAL                   TRUST BOUNDARY                       GAME
  MCP client ─► MCP server ─► refresh-request.json {nonce:int} ─► Observer (parses ONE int) ─► (no game writes)
                    ▲                                                   │
                    └──────── state/static/history/heartbeat.json ◄─────┘  read-only access layer
```

1. **No command channel.** The only inbound data is an integer nonce (and an optional config file of numeric intervals and booleans). The observer never interprets strings from outside as member names, queries or commands. This means even a compromised MCP server cannot make the observer call a game method.
2. **Single access layer.** All game reads go through one `ReadOnlyAccess` namespace. Capturers receive DTO builders, not raw game objects, wherever practical.
3. **Build-time IL allowlist (required).** A test uses Mono.Cecil to enumerate every `MemberReference` from the observer assembly into `Assembly-CSharp`, `Assembly-CSharp-firstpass`, `UnityEngine.*` and `0Harmony`. It fails the build on:
   - any reference to `0Harmony`;
   - any `set_*` accessor or `stfld` on a game type;
   - any method not on the explicit allowlist (which starts from the SAFE/CAUTION entries in `data-map.json`);
   - `FieldInfo.SetValue`, `PropertyInfo.SetValue`, `MethodBase.Invoke`, `Delegate.DynamicInvoke`, `Activator.CreateInstance` on game types;
   - `UnityEngine.Object.Instantiate/Destroy*`, `GameObject.SendMessage*`, `Time.set_timeScale`, `PlayerPrefs.Set*/DeleteKey/Save`, `Application.Quit`, `SceneManager.Load*`;
   - known disguised mutators: `Utils.GetSafe`, `MoneyManager.GetBalance/GetRawBalance/RegisterAgent`, `GuidMapper.GetGUIDForObject`, `ProductSpecificProductStorage.GetMaxAccepted/CanReserve*`, `PermitManager.GetPermitOwner/GetPermitCost/OwnsPermitFor*/IsPermitTaken/TryGetPermit`, `Shop.GetDeliveredByActorCount` and the per-actor sales getters, `GameData.editorInstance`, `GameData.GetAssets<T>()` (pooled), `DevConsole.Console.*`, `SavegameManager.*`, `ManualDestinationSlot.maxAcceptedAtDestination`, `CompanyStats.Compute*`, `Headquarters.totalAssets`, `GlobalMarket.UpdatePrices/ComputePriceModifier`.
4. **Reflection constrained to reads.** One helper exposes `ReadField<T>(object, CachedField)` and `TryGetDictionaryValue(...)` only. Field handles are created from a fixed table in code, never from external input.
5. **No event subscriptions on game objects.** The observer polls state rather than subscribing to `TimeManager`, `EventDispatcher` or the `BuildingManager` delegates. Subscribing mutates game delegate lists and risks exceptions inside game dispatch. The Unity `SceneManager` events are engine-level and acceptable.
6. **No savegame attributes** on observer types. `CreateSavegame` scans DDOL components, and the observer must stay invisible to saves.
7. **MCP server.** It exposes no mutation tools. It never writes anywhere except the refresh request and its own `history.sqlite`. It never opens the install directory. In fallback mode it reads saves only after copying them to a temp location.

Unavoidable capability: in-process code *can* call any game method, so the guarantee comes from (3) to (6) plus code review, not from the runtime. `RISKS.md` R-RO-1 documents this.

---

## 8. MCP integration and tool surface

Transport: **stdio** (an MCP client launches the server; no listening port). Language: **Python 3.12+** with the official `mcp` SDK. Python was chosen for fast iteration and because the save-format research parser is already in Python. JSON Schema files for the four snapshot files are the contract between the C# observer and the Python server, and both sides validate against them in tests.

Every tool returns structured JSON with a `meta` block (§5.4). List tools are paginated (`limit`, `cursor`) and return compact rows. Detail tools return a full object. Tools accept both internal keys and display names (French or English), with explicit disambiguation when a name matches several entities: "Factory 5" can exist for each owner and prefab.

| Tool | Answers | Main inputs | Data |
|---|---|---|---|
| `game_status` | Is the game running? Which state, date, speed, build? How fresh is the data? Which sections are disabled? | — | heartbeat + process check |
| `search` | "Factory 5", "Limoges", "peinture"/"paint" → entity ids | `query`, `kinds[]` | indexes (FR display, EN names, internal ids) |
| `list_companies` / `get_company` | Player and competitor summaries: cash (AI = infinite), loans, shares, value, cashflow label, buildings by type, top products, main tech tree | `company_id?` | state |
| `get_finances` | Revenues and expenses by month and category, profit trend, "what caused profit to fall" evidence | `company_id?`, `months` (≤ retained), `group_by` | history (ledger) |
| `list_buildings` | Filtered building rows | `owner`, `kind`, `product`, `recipe`, `city`, `region`, `status`, paging | state |
| `get_building` | Recipe, inputs/outputs, cycle time, progress, inventory and capacity, efficiency, upkeep, flags, blocking evidence, **outgoing destinations** (product, destination, Max Send, auto, Min Keep, mode, paused, fill, distance, dispatch cost, in-flight), **incoming suppliers**, logistic requests, monthly history | `building` (id or name) | state + history |
| `get_routes` | All configured routes matching filters (e.g. every gas route) | `origin?`, `destination?`, `product?`, `owner` | state |
| `get_supply_chain` | Actual production graph for a product or around a building, with per-edge settings and per-node theoretical and actual rates; resource consumption along the chain (e.g. gas used by the paint chain) | `product` or `building`, `direction`, `depth` | state + static |
| `get_production_overview` | Per product: producers, theoretical capacity per 30 days, produced last month, consumed, stock, sold | `owner`, `product?` | state + history |
| `find_bottlenecks` | Evidence rows: idle with missing input X (stock, need, inbound routes), output full, no modules, depleted deposit, paused, auto-WH dormant slots, routes with error | `owner`, `product?` | state (derived) |
| `list_products` / `get_product` | Static definition, base and current market price, trend, producers and consumers (static graph and the player's buildings), total city demand, State availability | `product` | static + state |
| `list_recipes` / `get_recipe` | Inputs/outputs, days, compatible buildings, tech unlock | `recipe` / `product` | static |
| `get_building_type` | Base cost, upkeep, storage slots, max modules, available recipes, unlock | `type` | static |
| `list_cities` / `get_city` | Tier, population, growth state, shops with accepted products, demand, stock, price, contract offer, advancement | `city` | state |
| `find_shops` | Which shops accept product P: demand, stock, price, delivered-by-player stock, straight-line distance from a given building, and whether a route already exists | `product`, `from_building?` | state |
| `list_regions` / `get_region` | Owner (permit), resources, city, permit cost, cooldowns | `region` | state |
| `get_market` | Global prices, modifiers and trends; State raw-resource offers and markups; active contracts and auctions | `products?` | state |
| `get_tech_tree` / `get_research_state` | Nodes with state (unlocked, available, queued, researching, teaser), prerequisites, unlocks, cost and time; queue and progress | `tree?`, `state?` | static + state |
| `list_vehicles` | Counts by owner and network, in-transit cargo by product, per-building fleet usage | `owner` | state |
| `get_history` (phase 2) | Sampled series the game does not keep (market price history, long-term finances) | `metric`, `entity`, `range` | HistoryStore |

There is deliberately **no** `dump_everything` tool. Static catalogues may also be offered as MCP resources (`roi://static/products`, …) for clients that support them.

Deterministic derivations stay in the server: rates per 30 days, capacity, graph traversal, ratios, straight-line distances and the Max Send interpretation. Economic judgement stays with the AI.

---

## 9. IPC options evaluated

| Option | Stability for the game | Simplicity | Freshness | Security | Verdict |
|---|---|---|---|---|---|
| **Atomic JSON snapshot files + refresh nonce file** | Best: no threads that touch game data, no listeners; the background writer only does file I/O | High; easy to inspect with any editor | Seconds (adaptive) | No network surface | **Chosen** |
| JSON Lines event stream | Good | Medium; consumer must replay | Good | OK | Rejected for v1: unbounded growth, replay complexity; snapshots suit the questions better |
| Named pipe request/response | Medium: a listener thread in game, every request marshalled to main thread, risk of per-request main-thread work driven by an external client | Medium | Best (on demand) | Needs pipe ACLs | Rejected for v1; possible v2 if freshness is insufficient, still serving pre-built snapshots only |
| Localhost HTTP / TCP in game | Medium | Medium | Best | Exposes a port (other local processes, browser-based attacks) | Rejected |
| Shared memory | Good | Low (custom framing, versioning) | Good | OK | Rejected: complexity without need |
| MCP server inside the game process | Poor: an MCP crash or hang hits the game | Medium | Best | Port or stdio in game | Rejected |

---

## 10. Alternatives to the in-process observer

| Alternative | Why rejected or relegated |
|---|---|
| **Save-file parsing only** (no mod) | Zero in-game risk, and research proved full parsing (`notes/save-format.md`). But it is only as fresh as the last save (autosave default 30 min), forces the user to save, and lacks runtime-only values (shop demand, base prices, route distances, dispatch costs, requirement status). **Kept as the offline fallback (phase 2)** and as a test oracle (GUID/tile joins). |
| External memory reading (ReadProcessMemory over Mono structures) | Fragile (Mono object layouts and runtime internals are version-specific), no main-thread consistency, needs process handles with read rights, close to "attach" semantics that this project forbids. Rejected. |
| BepInEx / MelonLoader / Doorstop injection | Unnecessary because an official loader exists. It adds a native injector and Harmony 2 in parallel with the game's Harmony 1.1. Rejected. |
| Harmony postfix hooks for events (save, day) | Polling is sufficient. Patching changes game code paths. Rejected. |
| Reusing the game's savegame serializer in memory | Runs every `OnSavegameSerialize` hook, mints GUIDs, blocks the main thread for hundreds of ms. Rejected. |
| UI scraping / OCR / screenshots | Unreliable and incomplete. This is the status quo the project replaces. |
| DevConsole commands | Any command except one sets cheat flags. Rejected. |

---

## 11. Implementation-phase experiments (not run during reconnaissance)

These require installing the observer, so they were deliberately not run. Each should be run on a **copy** of a save, ideally after the user has backed up `%APPDATA%\RiseOfIndustry`.

| ID | Experiment | Pass criteria |
|---|---|---|
| E1 | Install a minimal observer that only writes a heartbeat and runtime constants (`secondsPerDay`, `speedLevels`, `disableWithMods`, `Environment.CurrentDirectory`, `Debug.isDebugBuild`, `_days` delta per frame at 10×) | Loads without error popup; values match `notes/static-dump.md`; one day per frame max |
| E2 | Max Send / Min Keep correlation: the user sets distinct values (e.g. 7 and 3) on a known route in the UI, the observer exports | Exported `max_send` equals the UI "max", `min_keep` equals the UI "keep"; changing Max on one origin changes it for every origin to the same destination and product (shared semantics) |
| E3 | Performance on the largest save (Autosave 1: 535 vehicles, ~3,000 buildings incl. AI) | Main-thread capture time ≤ 2 ms per frame slice and ≤ 50 ms total; no measurable change in average frame time (±2 %) over 10 minutes at 10× speed; managed allocation per capture recorded; no extra GC spikes visible in frame-time trace |
| E4 | Lifecycle: boot → menu → load → quicksave/quickload → menu → load another save → quit | Heartbeat states correct; no stale references (no exceptions); no observer entry in save header other than the mod list; files valid after `Process.Kill` |
| E5 | Fault injection: force exceptions in each capturer (debug build of the observer) | Game unaffected, section disabled, heartbeat reports it |
| E6 | Freshness: change a setting while paused, call a tool with `fresh: true` | New value returned within `refresh_wait_s` |
| E7 | Identity: compare building keys (prefab + tile) and GUIDs (via `GuidMapper` reflection) with the save parser on the same save | 1:1 mapping; no tile collisions (U6) |

---

## 12. Packaging and installation (implementation phase)

- Mod folder: `<install>\Mods\RoiMcpObserver\desc.json` + `code\RoiMcpObserver.dll`. No `assets/` or `content/` folders. One merged DLL targeting .NET Framework 4.x (net46/net471), referencing only assemblies in `Rise of Industry_Data\Managed` (`Assembly.LoadFile` does no probing). No abstract `Mod` subclasses.
- `desc.json` `name` must be unique (a duplicate name sticks boot). Never decrease `version` (saves record it).
- The installer must never create a `Mods\` subfolder without `desc.json`: that silently disables **all** mods.
- Uninstall = delete the folder. Saves made with the mod show a "missing mod" notice afterwards, which is cosmetic.
- The MCP server installs separately (e.g. `uv tool install` / `pipx`) and is registered in the client's MCP config.
