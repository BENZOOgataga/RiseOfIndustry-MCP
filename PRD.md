# PRD: Rise of Industry read-only MCP (V1)

| Field | Value |
|---|---|
| Document | Implementation contract for V1 |
| Status | Draft for independent review. Implementation is **not** authorized until this PRD is approved |
| Date | 2026-10-03 |
| Target game | **Rise of Industry** (original), Dapper Penguin Studios, Steam App ID **671440**, Windows. **Not** Rise of Industry 2 |
| Technical source of truth | `research/` (reconnaissance). Field-level mapping: `research/data-map.json` (rendered as `research/DATA-MAP.md`) |
| Audience | Implementers and reviewers of V1 |

Requirement keywords: **MUST** / **MUST NOT** = mandatory, release-blocking. **SHOULD** = expected unless a documented, measured reason justifies deviation. **MAY** = optional.

---

## 0. How to use this document

1. Read this PRD completely, then `research/RESEARCH.md`, `research/ARCHITECTURE.md`, `research/RISKS.md`, `research/notes/static-dump.md` and `research/notes/logistics.md`. Consult `research/data-map.json` for every game member you read.
2. Where this PRD and `research/` differ, **this PRD wins**. Every deliberate deviation is listed in §27 with its reason.
3. Do not modify `research/` evidence to agree with the PRD. New evidence found during implementation is recorded in `docs/VALIDATION-REPORT.md` (and MAY be appended to research notes as clearly dated addenda).
4. Ids such as `logistics.min_keep` refer to entries in `research/data-map.json`. Ids such as R-PERF-1 refer to `research/RISKS.md`. Gates E1–E7 are defined in §22 of this PRD.

---

## 1. Product summary

A local, **read-only** integration that lets an MCP client (Claude, ChatGPT or another MCP client) inspect the state of the user's running Rise of Industry game as structured data. Covered areas: company, buildings, production, logistics routes including Max Send and Min Keep, inventories, cities, shops, regions, market, technology and finances. The integration replaces screenshots and manual transcription.

The system is an observation and data-access layer. It performs deterministic calculations (rates, graph traversal, cost arithmetic). It does not make strategic recommendations.

---

## 2. Hard constraints

### 2.1 Read-only (release-blocking)

The product MUST NOT provide any capability to:

- build or demolish anything;
- change recipes, efficiency, production settings, destinations, Max Send, Min Keep, logistics options or warehouse requests;
- change research;
- control vehicles;
- buy or sell through gameplay systems (auctions, shares, permits, contracts);
- change company finances;
- modify saves;
- issue gameplay or console commands;
- automate keyboard or mouse input;
- write to game memory.

**This is not a security sandbox.** The observer runs inside the game process through the game's official mod loader. In-process code can technically call any game method. The read-only guarantee is therefore a property of the implementation, enforced by:

1. the absence of any command channel (§11);
2. a single restricted read layer (§9.6);
3. a build-time IL gate that fails the build on prohibited references (§10);
4. automated tests (§21) and an end-to-end mutation check (gate V8, §22).

The evidence is of two different kinds:

- **Structural enforcement.** The IL gate's default-deny allowlist, the hard rules and denylist, and the transitive analysis (§10) constrain the observer's *entire* permitted call and read surface at build time.
- **Empirical verification.** V8 shows that the *exercised* full tool sweep produced no observable game or save mutation beyond a control baseline. V8 alone does not prove that mutation is impossible; it only covers what was exercised and what a save diff can observe.

The V1 read-only claim rests on the **combination** of structural enforcement, automated tests and V8 evidence. Documentation (§24, `docs/READ-ONLY-GATE.md`, `docs/VALIDATION-REPORT.md`) MUST describe it this way.

### 2.2 Stability (release-blocking)

The observer MUST NOT materially destabilize or degrade the game. Any of the following caused by the observer is a release-blocking defect:

- a crash;
- a hang;
- a stuck loading screen;
- disabled mod loading;
- save corruption;
- a measurable gameplay performance regression beyond §17.

Game stability has priority over data freshness.

---

## 3. Compatibility baseline

### 3.1 Verified baseline

All values below are CONFIRMED in `research/RESEARCH.md` §2 and `research/notes/install-runtime.md`. The `Assembly-CSharp.dll` hash was computed during PRD authoring from the installed file and is identical to the research copy.

| Item | Value |
|---|---|
| Steam App ID / build id | 671440 / 9064059 (depot 671441, manifest 8360881793189550917) |
| `GameVersion` asset | major 2, minor 3, revision 3, suffix `b`, build `0507`; string `Steam - Public - 2.3.3 : 0507b` |
| Commit hash | `76359e59644ebf55cafacf9a50b3d19800df22d7` |
| Savegame version | 2304 |
| `Assembly-CSharp.dll` SHA-256 | `D62599EFD0CFCB9F343E7FF74AAC19533F572062B9CECA82E1D3911507D04803` |
| `Assembly-CSharp-firstpass.dll` SHA-256 | `D5E6331DC5170B265945B5580E43CA22B811DCD726590091624F9CA2BA6BE690` |
| Engine / runtime | Unity 2018.4.11, Mono (`mono-2.0-bdwgc`), .NET 4.x API level, Boehm GC (non-incremental) |
| In-process libraries available | Newtonsoft.Json 11.0.2, Harmony 1.1 (MUST NOT be used), LZ4 (lz4net) |
| Install path on reference machine | `C:\Program Files (x86)\Steam\steamapps\common\RiseOfIndustry` |
| Game modules present | base content plus "2130" module assets (both in `GameData`) |

### 3.2 Behaviour on a non-baseline build

The observer MUST compare, at READY (§8.2):

- the `GameVersion` (major.minor.revision, suffix, build, commitHash);
- `savegameVersion`;
- the SHA-256 of the loaded `Assembly-CSharp.dll`, computed once on the background thread from the file path of the loaded assembly.

If any value differs from §3.1:

- The observer MUST enter `unsupported_build` for the rest of the process lifetime. It keeps writing the heartbeat but performs **zero** world reads beyond the version gate. That means no state, static or history capture, and no reflection self-check against game fields.
- The heartbeat MUST report `detected_game` (version, build, commit, savegame version, `Assembly-CSharp.dll` SHA-256) and `expected_game` (the §3.1 values), so the mismatch can be diagnosed.
- **No override exists in V1.** No configuration key, environment variable, file or command line can enable capture on a non-baseline build. Unknown keys in `observer.config.json` (including a stray `allow_unverified_build`) are ignored with a warning (§8.7).
- The MCP server MUST surface the state through `get_game_status` (with detected and expected values). Every runtime and static catalogue tool MUST return the error `unsupported_build` (§13.3, §16).
- `static.json`, `state.json` and `history.json` are only ever produced on the baseline build, so their envelope field `compatibility` is always `"verified"`. The server MUST reject any of these files whose `compatibility` is not `"verified"`. The heartbeat's `compatibility` is `verified` or `unsupported_build` (§11.5).

This is stricter than the research recommendation (capture with a flag, `research/ARCHITECTURE.md` §6). The reason: V1 forbids exposing potentially incorrect mappings. Support for other builds is out of scope for V1 (§4.2).

---

## 4. V1 scope

### 4.1 In scope

- A live observer mod and an out-of-process MCP server with the tool surface in §14.
- Static catalogue (definitions) exported from the running game.
- History that the game itself retains (§12.4).
- Installation, deployment, uninstallation, diagnostics and documentation.

### 4.2 Non-goals for V1

- Rise of Industry 2 in any form.
- Autonomous gameplay, strategy engines, recommendations embedded in tools.
- Any gameplay mutation, UI automation, input automation.
- Save editing.
- Offline save-based MCP mode. The research save parser (`research/tools/save-inspect/`) remains validation tooling only; an offline mode is a later phase.
- Arbitrary memory reading or writing, Cheat Engine, offsets, assembly patching, BepInEx/MelonLoader/Doorstop, Harmony patches.
- Network exposure: no listening sockets, HTTP, named pipes or shared memory in the game process. The MCP server uses stdio only.
- A generic command or query channel into the game.
- An independent long-term history database. The game keeps no market price history; V1 does not reconstruct one (§12.4).
- Any support for game builds other than the §3.1 baseline, including best-effort or opt-in capture (§3.2).
- Full-map exports: per-tile terrain, road and rail networks, pollution grids, resource-node grids. V1 exposes per-building and per-region aggregates only.
- Per-vehicle tracking across trips (no stable vehicle identity exists, §7.3).
- Multiplayer (the game has none), localization of MCP output, a GUI.

---

## 5. Architecture

### 5.1 Overview

```
 Rise of Industry.exe (Unity 2018.4 Mono)
 └─ ModLoader (DontDestroyOnLoad GameObject, official loader)
    └─ RoiMcpObserver : ProjectAutomata.Mod   ← the only in-process component
         Lifecycle · Scheduler · ReadLayer (main thread, time-sliced) · DTO builders
         Publisher + Heartbeat writer (background thread, file I/O only)
                     │ files only
                     ▼
 %LOCALAPPDATA%\RoiMcp\   heartbeat.json · static.json · state.json · history.json
                          refresh-request.json (written by server) · observer.log · observer.config.json
                     │
                     ▼
 roi-mcp server (separate process, Python, MCP over stdio)
   SnapshotStore · Indexes · Derivations · Tools
                     │ MCP stdio
                     ▼
 Claude / ChatGPT / other MCP client
```

Process boundaries:

- **Game.** It hosts only the observer. Observer faults are contained per section (§8.6) and never propagate into game code paths, because the observer subscribes to no game events (§9.5).
- **MCP server.** A separate OS process. If it crashes or hangs, the game is unaffected, and it can restart at any time.
- **Exchange directory.** This is the only shared state. Writers replace files atomically and readers validate them.

### 5.2 Technology decisions (settled)

| Component | Decision | Reason |
|---|---|---|
| Observer | C#, single assembly `RoiMcpObserver.dll`, target **net461**, C# language version ≤ 7.3, no NuGet runtime dependencies | net461 code mods are proven on 2.x builds (ROIData, `research/notes/public-projects.md`). Single DLL because `Assembly.LoadFile` does no dependency probing. Language features needing assemblies absent from `Managed\` (e.g. `System.ValueTuple`) are blocked by the IL gate's assembly allowlist |
| Observer references | Game's `Managed\` assemblies (`Assembly-CSharp`, `Assembly-CSharp-firstpass`, `UnityEngine.CoreModule`, `UnityEngine`, `Newtonsoft.Json`, BCL) with `Private=false`. BCL compile references from `Microsoft.NETFramework.ReferenceAssemblies.net461` | Never copy game binaries into the repository. The path is configurable (`ROI_GAME_DIR`, default the baseline path) |
| JSON in observer | Newtonsoft.Json 11 from the game's `Managed\` (already loaded by the game). `JsonTextWriter` streaming from DTOs on the background thread | No extra in-process dependency (R-SEC-5) |
| Observer tests | NUnit or xUnit on **net48** (.NET Framework on Windows). Pure builder and scheduler logic is tested without Unity | — |
| IL gate | .NET 8 console tool using **Mono.Cecil**, run as an MSBuild post-build step and in the test suite | §10 |
| MCP server | **Python ≥ 3.11**, official `mcp` SDK, `jsonschema`, `pytest`; packaged with `uv` (`pyproject.toml`) | Research recommendation; matches the existing research parser language |
| Schemas | JSON Schema 2020-12 in `schemas/` | Shared contract between C# and Python (R-VER-3) |
| Transport | MCP stdio only | No listening ports (R-SEC-1) |

---

## 6. Repository structure (target)

```
PRD.md                       this document
README.md                    existing (update its "status" section when implementation starts)
observer/
  RoiMcpObserver.sln
  src/RoiMcp.Observer/       mod source (Mod entry, Lifecycle, Scheduler, ReadLayer, Capture, Dto, Publish, Diagnostics, Config)
  mod-template/desc.json     {"author","name":"RoiMcpObserver","version","versionString","dependencies":[],"description"}
  tests/RoiMcp.Observer.Tests/
  readonly-gate/             Mono.Cecil gate tool + allowlist.json + denylist.json + gate tests
mcp-server/
  pyproject.toml
  src/roi_mcp/               server, snapshot store, indexes, derivations, tools, lifecycle
  tests/                     unit, contract, derivation, lifecycle, malformed-input tests
  tests/fixtures/            committed synthetic fixtures (no game binaries, no personal saves)
schemas/                     heartbeat / static / state / history / refresh-request / observer-config / tool-response schemas
scripts/                     build.ps1, install-observer.ps1, uninstall-observer.ps1, backup-saves.ps1,
                             collect-diagnostics.ps1, perf-report.ps1, gen-fixtures-from-save-copy.ps1
docs/                        INSTALL.md, MCP-CLIENTS.md, USAGE.md, TROUBLESHOOTING.md, KNOWN-LIMITATIONS.md,
                             VALIDATION-REPORT.md, SNAPSHOT-FORMAT.md, READ-ONLY-GATE.md
research/                    unchanged reconnaissance (tools stay here)
.local/                      gitignored: generated large fixtures, perf traces, deploy staging
```

Rules:

- Research tooling stays in `research/tools/`. Production code MUST NOT import from `research/`.
- Scripts MAY invoke the research save parser for fixture generation and validation (gate E7).
- Generated fixtures derived from the user's saves go to `.local/` (gitignored). Committed fixtures are synthetic.

---

## 7. Entity model and identifiers

### 7.1 Id format

All ids exposed by the MCP are strings of the form `<kind>:<key>`. Tools MUST accept these ids. Tools that resolve names MUST also accept display names and internal asset names, with explicit ambiguity errors (§13.3).

Ids are opaque and **case-sensitive**. They are matched exactly, kind prefix included, and never case-normalized: `BUILDING:PaintFactory@55,40` and `building:paintfactory@55,40` are not the id `building:PaintFactory@55,40` and resolve as `not_found`. A bare key (the part after `<kind>:`, e.g. `PaintFactory@55,40` or a company's actor id) is accepted where an entity is expected and is also matched exactly. Case- and accent-insensitive matching applies only to names (§13.6); for catalogue kinds the key is the asset name, which is also matched as a name.

| Kind | Id format | Native basis | Persistent across save/load? | Evidence |
|---|---|---|---|---|
| Company (actor) | `company:<actorId>` | `Actor.id` (int, persisted counter) | Yes | `id_strategies.actor` |
| City | `city:<actorId>` | settlement `Actor.id`; label `settlementName` (= region name) | Yes | `id_strategies.settlement` |
| Region | `region:<guid>` | `Region.id` (Guid) | Yes | `id_strategies.region` |
| Building (incl. shops, warehouses, harvesters, fields, HQ, State buildings) | `building:<prefab>@<x>,<y>` | `building.prefab.name` + origin tile from `Tile.GetCoordinates(building.tile)` | Yes (constructor params are saved) | `id_strategies.building` |
| Building type | `building_type:<prefab>` | prefab asset name | Yes | `id_strategies.definition` |
| Product | `product:<asset name>` | `ProductDefinition.name` | Yes | ″ |
| Recipe | `recipe:<asset name>` | `Recipe.name` | Yes | ″ |
| Technology | `tech:<asset name>` | `TechTreeUnlock.name` | Yes | ″ |
| Tech tree | `tech_tree:<asset name>` | `TechTree.name` | Yes | ″ |
| Route (manual destination) | `route:<origin building key>|<product asset>|<destination building key>|<source>|<n>` | **no native id**; `<source>` = depot module prefab name or `own`; `<n>` = 0-based occurrence index among slots with identical tuple, in slot order | Stable while the tuple exists; `<n>` > 0 only for duplicates | `id_strategies.destination_slot` |
| Warehouse request | `request:<endpoint building key>|<product asset>|<n>` | no native id | as above | `id_strategies.logistic_request` |
| Vehicle | `vehicle:<world_session>:<instanceId>` | `GetInstanceID()` of the pooled vehicle object | **No** (world-session only) | §7.3 |
| Contract | `contract:<city id>|<product asset>` | no native id | while active | — |
| Money category | `bill_category:<asset name>` | `MoneyBillCategory.name` | Yes | `notes/static-dump.md` §3 |

The `building:` key MUST use the prefab asset name, never the localized `buildingName`. Display names (French on the reference install, user-renamable, duplicated per owner and prefab, e.g. "USINE PÉTROCHIMIQUE 5") are returned as `display_name` only.

### 7.2 Building identity details

- **Uniqueness.** Two buildings cannot share an origin tile (`BuildingManager.GetBuilding(tile)`). Warehouse module buildings are unverified (U6). The observer MUST assert uniqueness of building keys on every capture. On a collision it MUST suffix the colliding keys with `#<first 8 hex chars of the save GUID>` (or `#i<instanceId>` if no GUID exists), log it once, and report `id_collisions` in the heartbeat.
- **Save GUID.** When `GuidMapper.objMappings` (private) contains the building, its GUID MUST be exported as `save_guid` (read via `TryGetValue`). Buildings created since the last save/load have none (`save_guid: null`). The observer MUST NOT call `GuidMapper.GetGUIDForObject` (it mints GUIDs).
- **Rebuilds.** Demolishing and rebuilding the same prefab on the same tile reuses the key (R-ID-4). Building DTOs MUST include `paid_to_build` so the server can detect replacement between snapshots.

### 7.3 Vehicles: known identity limitation

- `Vehicle.id` (ushort) is reassigned from a counter on every pool pull, i.e. per trip, and wraps at 65535. It MUST NOT be exposed as an identity. It MAY be exposed as `trip_counter_id` with that caveat.
- `GetInstanceID()` identifies the pooled vehicle object within one world session. The same object is reused for different trips and jobs.
- V1 therefore reports vehicles mainly in aggregate (counts and cargo per owner, network, product and fleet building). Per-vehicle rows carry session-scoped ids and the documented caveat `"identity": "session_pooled_object"`.

### 7.4 World session

At each entry into READY (§8.2), the observer generates a random GUID `world_session`. Quickload (`game → game`) and loading another save produce a new value. Every published file and every MCP response carries it. Session-scoped ids (vehicles) from another world session MUST be rejected with `stale_reference`.

### 7.5 Relationships (graph model)

- **Configured supply edge:** route `origin building → destination building` carrying `product`, attributes per §12.3.
- **Warehouse pull edge:** request on an endpoint building. Providers are resolved dynamically by the game, so the edge has no fixed source.
- **Observed transport edge:** from in-flight transport requests and vehicle jobs (origin, destination, product, amount). Labelled `observed_in_flight`.
- **Auto-warehouse edge:** `building → its warehouse` when the AUTO_WH option is set. In that case the building's manual routes are dormant.
- **Static recipe graph:** product ↔ recipe ↔ building type, from `RecipeDatabase` and recipe definitions.

---

## 8. Observer specification

### 8.1 Packaging and load-time behaviour

| ID | Requirement |
|---|---|
| OBS-1 | Deployed as `<install>\Mods\RoiMcpObserver\desc.json` + `<install>\Mods\RoiMcpObserver\code\RoiMcpObserver.dll`. No `assets/` or `content/` folders. Exactly one non-abstract, sealed `ProjectAutomata.Mod` subclass. No other type derives from any game type, and no type implements any game interface (prevents game callbacks such as `IWorldReadyListener`, `ICustomUpdateble`) |
| OBS-2 | `OnModWasLoaded` and `OnAllModsLoaded` MUST be wrapped in try/catch. They may only create the exchange dir, start the background thread and log one line. An exception there would permanently disable the mod via PlayerPrefs and halt boot (R-CRASH-1) |
| OBS-3 | `desc.json` `name` is `RoiMcpObserver` (unique). `version` is a positive integer that MUST never decrease between releases (saves record it) |
| OBS-4 | No `[Savegame*]` attributes. Nothing in the observer is reachable by the save system's reflection scan except plain types that resolve cleanly (R-CRASH-5) |

### 8.2 Lifecycle states

The observer maintains exactly one state, published in the heartbeat. All detection mechanisms are CONFIRMED in `research/notes/runtime-lifecycle.md` §3.

| State | Entry condition | Behaviour |
|---|---|---|
| `starting` | Mod component created | Heartbeat only |
| `menu` | Active scene ≠ `"game"`, or `World.instance` is null | Heartbeat only. All cached game references dropped. No game reads beyond the scene name |
| `loading` | Scene `"game"` active and (`!World.instance.isWorldReady` or `WorldLoadingScreen` active) | Heartbeat only. No captures. No subscriptions |
| `ready` | `isWorldReady`, loading screen inactive, and `TimeManager.instance.today` read successfully on ≥ 2 consecutive frames | On entry: new `world_session`; version gate (§3.2); reflection self-check (§9.4); then static export, state captures, history captures |
| `unsupported_build` | Version gate failed (§3.2). Terminal for the process lifetime; no override | Heartbeat only (detected and expected versions/hashes). Zero world/static/history capture |
| `disabled` | Kill switch present or `enabled: false` | Heartbeat only. Checked by the background thread every 1 s |
| `faulted` | Unrecoverable observer error (e.g. exchange dir unwritable after retries, background thread died and could not be restarted) | Best-effort heartbeat. No game reads |

Paused is a **flag**, not a state: `paused = (SpeedControls.level == -1) || (Time.timeScale == 0)`. Both raw values are exported (research: menus set `timeScale = 0` without changing `level`).

Transitions:

- `SceneManager.sceneUnloaded` for `"game"`, or `activeSceneChanged` away from `"game"`, abandons any in-flight capture immediately, clears all game references, and moves to `menu` or `loading`. Handlers are wrapped in try/catch.
- `game → game` (quickload) MUST produce `ready(old session) → loading → ready(new session)`.
- Static values such as `Player.humanPlayer` and `World.Size` are not cleared by the game. They MUST NOT be read outside `ready`.

Game shutdown: the game ends with `Process.Kill()`, so there is no callback. The observer MUST NOT rely on shutdown hooks. Because publication is atomic (§11.3), files are always valid. The heartbeat simply stops updating.

### 8.3 Threads

- **Main thread.** Reads all game state, only inside the mod's `LateUpdate`, in budgeted slices. Updates a small "main-thread status" record every frame: frame count, `Time.unscaledTime`, state, scene name, and, in `ready` only, the date, speed level and time scale.
- **One background thread** (`IsBackground = true`). It does three things:
  - writes the heartbeat every 1 s from the status record;
  - serializes completed DTO graphs and publishes files;
  - polls `refresh-request.json`, `observer.config.json` and the kill switch (mtime checks, at most every 1 s).
- The background thread MUST NOT touch any game or Unity object. The IL gate enforces that types in the publisher namespace reference no game or Unity members (§10.3 rule G9).

### 8.4 Capture scheduling

These defaults come from research (`ARCHITECTURE.md` §5). E3 MAY adjust them; any change MUST be backed by measurements recorded in `docs/VALIDATION-REPORT.md`.

| Parameter | Default | Allowed range (config) |
|---|---|---|
| `running_interval_s` (min gap between state captures while unpaused, measured in real unscaled time; a capture is due when the game day has changed since the last one) | 5 | 2–300 |
| `paused_interval_s` (periodic capture while paused, catches settings changed while paused) | 15 | 5–600 |
| `min_gap_s` (min gap for refresh-requested captures) | 1 | 1–60 |
| `frame_budget_ms` (main-thread time per frame slice) | 2.0 | 0.5–5.0 |
| `capture_ceiling_ms` (summed main-thread time per state capture) | 50 | 10–200 |
| Heartbeat period | 1 s | fixed (research) |
| History capture | on first `ready` frame of a new month (detected by polling `today`), on entering `ready`, and on a pending `history` refresh nonce (§11.6) | — |
| Static export | on entering `ready`, and when `GameModuleRepository.currentModule` id changes | — |

- **Adaptive back-off.** `effective_interval_s = max(configured interval, 100 × last_capture_main_thread_ms / 1000)`, which caps the observer's main-thread share at about 1 %. If a capture exceeds `capture_ceiling_ms`, the next effective interval doubles (cap 120 s) and the heartbeat reports `degraded: true`. After 3 consecutive ceiling breaches, optional sections (§12.2 `optional`) are skipped until the next world session, and this is reported.
- **Single flight.** At most one capture (state, history or static) is in flight at any time. Requests that arrive during a capture coalesce.

### 8.5 Time-sliced capture

- Each section is an iterator. It copies the live collection it walks (references only) in the same frame it starts, then processes items and yields when `Stopwatch` elapsed time ≥ `frame_budget_ms`. One item (e.g. one building) is the atomic unit and MUST be small.
- Every item resumed in a later frame MUST be revalidated with the Unity null check. Destroyed items are dropped and counted in `items_vanished`.
- Each section records `game_day` (absolute day count) and `frame` at start and end.
- A snapshot is `consistent: true` only if all sections share one `game_day`. Otherwise it is published with `consistent: false` and per-section dates. The server MAY request one recapture when consistency matters to a tool.
- Vehicle data MUST be read from `transform.position`, job fields and cargo only, never from mover internals (ThreadPool-mutated, R-CRASH-6). The observer MUST NOT call `VehicleMovementManager.WaitParallelUpdate` (it blocks the main thread).

### 8.6 Fault containment

- Every section and every item handler runs inside try/catch.
- A section that throws on 3 consecutive captures is disabled until the next world session and listed in `disabled_sections` with its last error signature.
- Exceptions are never rethrown into Unity.
- A missing reflection member (§9.4) disables only the sections that depend on it.
- Partial capture: if a section is disabled, failed or skipped, the snapshot is still published. The section has `status` ∈ {`ok`, `disabled`, `failed`, `skipped`, `over_budget`} and its data is absent (not partially filled).
- **Fault injection** for gate E5 exists only in a `DEBUG_FAULTS` compile-time build. The release build MUST NOT contain fault-injection code; the IL gate checks for the marker type.

### 8.7 Configuration and kill switch

- `observer.config.json` (schema `schemas/observer-config.schema.json`) has these fields: `enabled`, the intervals and budgets of §8.4, `include_ai_building_detail` (default false), `include_ai_routes` (default false), `include_route_paths` (default false) and `log_level`. There is **no** key that affects the compatibility gate (§3.2).
- Values out of range are clamped and warned about. Unknown fields are ignored and warned about. The file is read at `ready` entry and when its mtime changes (checked ≤ every 10 s).
- **Kill switch.** The presence of `%LOCALAPPDATA%\RoiMcp\observer.disabled` puts the observer into `disabled` within 1 s.

---

## 9. Read layer requirements

### 9.1 Main-thread rule

Every access to any type from `Assembly-CSharp`, `Assembly-CSharp-firstpass` or `UnityEngine.*` MUST happen on the Unity main thread, inside the scheduler's slices. This is enforced by structure: only the `ReadLayer` namespace may reference game and Unity members (gate rule G9).

### 9.2 Safe-read discipline

- Use the access paths in `research/data-map.json` (`access`). Never call members listed in any entry's `avoid` field.
- Use Unity null checks on all game objects and managers.
- Copy live collections (`List`, `HashSet`, `Queue`, `ReadOnlyList`) into observer-owned, reused buffers within the same frame.
- Do not enumerate `IProductStorage` (it allocates a `Product` per entry). Call `Count(def)` for the relevant products instead, or read `_storage` via reflection.
- Do not use LINQ, pooled game lists (`GetAssets<T>()`, `GetUnlocked()`), `Formula.Evaluate` loops, or the expensive aggregates `GlobalMarket.GetProductDemand/GetStoredAmount/GetSoldAmount`, `Headquarters.totalAssets` or `CompanyStats.Compute*`.
- Use `Player.humanPlayer` for the player. Export `active_actor_differs: true` when `Player.activeActor != Player.humanPlayer`. Never call `SetActiveActor`.

### 9.3 Disguised mutators (MUST NOT be referenced; enforced by the denylist in §10)

These are CONFIRMED to have side effects despite read-like names (`research/RISKS.md` R-RO-2). For each, the safe alternative is given.

| Forbidden member | Side effect | Safe alternative |
|---|---|---|
| `Utils.GetSafe` (any overload) | Inserts a missing key | `TryGetValue` on the underlying dictionary |
| `ProductSpecificProductStorage.GetMaxAccepted`, `ManualDestinationSlot.maxAcceptedAtDestination` | `GetSafe` insert into the saved `_maxAcceptedMap` | Reflection `_maxAcceptedMap.TryGetValue(product.AssetId)`. Missing key = 0 (unlimited) |
| `ProductSpecificProductStorage.CanReserve/CanReserveAll/CanStore/Reserve/Clear/Put/Pull` | Insert or mutate | `Count(def)`, `FreeSpace(def, …)` (pure on this class), reflection `_storage.TryGetValue` |
| `MoneyManager.GetBalance/GetRawBalance/RegisterAgent/SetRawBalance` | Register (insert) agent | `balances.TryGetValue(actor.money, out v)` |
| `GuidMapper.GetGUIDForObject/Write` | Mints GUIDs | Reflection `objMappings.TryGetValue` |
| `PermitManager.GetPermitOwner/GetPermitCost/OwnsPermitForRegion/OwnsPermitForTile/IsPermitTaken/TryGetPermit/CanTerraformAt` | `GetSafe` insert | Reflection `_permits.TryGetValue(region)` then `TryGetValue(fullPermit)` |
| `Shop.GetDeliveredByActorCount`, per-actor `GetSoldCount/GetSalesFigures/GetSoldCountRanged/GetSalesFiguresRanged/GetStorageRanged`; `SettlementProductStatistics` per-actor overloads | `GetSafe` insert (partly into saved dicts) | Reflection `_deliveredByActors/_sales/_salesFigures/_storageFigures` with `TryGetValue`; overall `GetSoldCount(product, period)` |
| `GameData.editorInstance`, `GameData.GetAssets<T>()` (pooled), `GameDataManifest.GetAssetsRO` with unknown types | Re-init / pool / insert | `GameData.instance.GetAssetsRO(knownType)` only for types in a fixed list |
| `CompanyStats.Compute*`, `CompanySharesAgent.GetCompanyValue/GetRegionsValue/GetBundle*Price`, `Headquarters.totalAssets` | `PermitManager.GetSafe`; `activeActor` bias; cost | Replicate (§15) |
| `GlobalMarket.UpdatePrices/ComputePriceModifier`, `Shop.UpdatePriceModifiers/UpdateDemand`, `SettlementGrowth.GetTier*` before world ready, `GameDate.RandomInRange` | RNG and state | Read stored values |
| `BuildingRequirementController.allRequirementsMet/Check`, any `IsRequirementMet`, `RecipeUser/GathererHub.CanStartProducing`, `Harvester.CanStartWork/HasResources` | Timers, reservations, flag writes | Derive status from fields (§15.6) |
| Any `TransportRequest(Handle).Validate/PayVehicleDispatch/Complete/Fail/CanReserve`, `ManualDestinationManager.ValidateRequest*`, `BuildingTransportManager.AddPath/RemovePath`, `PathCache.UpdateCachedPath` | Mutation / pathfinding | — |
| `DestinationsPanelViewModel.autoWhDispatches` and every `*ViewModel` / `UI.*` type | Mutates UI state | Recompute from model types |
| `DevConsole.Console.*`, `PAConsole.*` | Cheat flags | — |
| `SavegameManager.*`, `SavegameStorage.*`, `QuicksaveManager.*`, `AutosaveManager.*` mutators | Saving | — |
| `VehicleMovementManager.WaitParallelUpdate`, `VehicleManager.NextVehicleIndex` | Main-thread block / counter increment | — |
| `GlobalMarket.GetPricingInfo` for a product not in the market | Logs an error and allocates | Only call for products from the market's own key set (reflection `_pricingInfoByProduct` keys) |
| `TransportRequestHandle` getters on an invalid handle | `GetEntity` logs an error | Check `IsValidHandle` first |

### 9.4 Reflection

- Private and protected fields are accessed only through one `ReflectionTable`. It is a static, compile-time list of `(declaring type via typeof, field name literal, expected field type)` entries, resolved once on `ready` entry with `Type.GetField(name, BindingFlags.Instance|NonPublic|Public)`.
- Allowed operations: `FieldInfo.GetValue` and `TryGetValue` on the returned dictionary. Never `SetValue`, `Invoke`, `CreateDelegate`, `System.Reflection.Emit` or `Expression.Compile`.
- **Self-check report.** For each entry: resolved, type matches, dependent sections. It is published in the heartbeat and logged once per session.
- **Initial table** (from research; the implementation MAY add entries, each requiring allowlist review per §10.4):
  - `GuidMapper.objMappings`
  - `PermitManager._permits`
  - `ProductSpecificProductStorage._maxAcceptedMap`, `._storage`, `._reservations` (optional)
  - `SingleProductStorage._maxAccepted`
  - `InfiniteStorage._maxAccepted`
  - `Shop._deliveredByActors`, `._sales`, `._salesFigures`, `._storageFigures`, `._priceModifiers`, `._daysToNextPricesUpdate`
  - `TransportJob<T>._origin`, `._destination`, `._product`, `._productAmount`
  - `Loan.freeMonthsLeft`
  - `GlobalMarket._pricingInfoByProduct`
  - `MoneyAgent._bills` (only if the public range queries prove insufficient)
  - `Upkeep.upkeep`, `.daysUp`
  - `Harvester._resources`, `._efficiency`
  - `Field._efficiency`

### 9.5 No game event subscriptions

The observer MUST NOT subscribe to any game event or delegate (`TimeManager.on*`, `EventDispatcher`, `BuildingManager.On*`, `ActorManager.actor*`, `*.onUnlocked`, …). All change detection is by polling.

The only allowed subscriptions are the Unity engine events `SceneManager.sceneLoaded`, `sceneUnloaded` and `activeSceneChanged`, each with try/catch handlers. Reasons: R-CRASH-3, R-RO-1, and the fact that `EventDispatcher` has no exception isolation.

### 9.6 Read layer surface

- Only `RoiMcp.Observer.ReadLayer` may reference game and Unity members.
- Capture code outside it receives plain "fact" structs or classes and builds DTOs. This keeps DTO building unit-testable without Unity.

---

## 10. Read-only build gate (release gate)

### 10.1 What is inspected

The gate is a Mono.Cecil tool under `observer/readonly-gate/` that runs:

- as an MSBuild `AfterBuild` target of the observer project (it fails the build); and
- as a test in the test suite (it fails CI/local test runs).

It loads the built `RoiMcpObserver.dll` and, read-only, the baseline game assemblies from `ROI_GAME_DIR\Rise of Industry_Data\Managed\`. It verifies the `Assembly-CSharp.dll` SHA-256 against §3.1 and fails on mismatch. There is no override.

It inspects:

- every method body instruction with a `MethodReference` operand (`call`, `callvirt`, `newobj`, `ldftn`, `ldvirtftn`) or a `FieldReference` operand (`ldfld`, `ldflda`, `stfld`, `ldsfld`, `ldsflda`, `stsfld`);
- every `ldtoken` and `ldstr` that feeds a `ReflectionTable` entry;
- all type definitions (base types, interfaces, custom attributes);
- all assembly references and P/Invoke declarations.

### 10.2 Default-deny allowlist

- Every member reference that resolves into `Assembly-CSharp` or `Assembly-CSharp-firstpass` MUST match an entry in `allowlist.json` exactly: declaring type full name, member name, parameter types, and kind (method / getter / field-read).
- Every member reference into `UnityEngine*` MUST match the Unity allowlist. Initial content:
  - `Time.get_unscaledTime/get_timeScale/get_frameCount/get_unscaledDeltaTime`
  - `SceneManager.GetActiveScene`, `Scene.get_name`, `SceneManager.add_sceneLoaded/add_sceneUnloaded/add_activeSceneChanged`
  - `Object.op_Implicit/op_Equality/op_Inequality/GetInstanceID/get_name`
  - `Component.get_transform/get_gameObject`, `Component.GetComponent<T>` for allowlisted T
  - `Transform.get_position`
  - `GameObject.get_activeSelf/activeInHierarchy`
  - `Debug.Log/LogWarning/LogError` (limited by policy §19)
  - `Application.get_version`
  - `MonoBehaviour` / `Behaviour` base members needed for `LateUpdate`
- BCL references MUST resolve in the game's `Managed\` copies of `mscorlib`, `System` and `System.Core`.
- Allowed assembly references: `mscorlib`, `System`, `System.Core`, `UnityEngine`, `UnityEngine.CoreModule`, `Assembly-CSharp`, `Assembly-CSharp-firstpass`, `Newtonsoft.Json`. Any other reference fails the build. This includes `0Harmony`, `System.ValueTuple` and `System.Net.*` assemblies.

### 10.3 Hard rules (fail even if allowlisted)

| Rule | Forbidden |
|---|---|
| G1 | `stfld`, `stsfld`, `ldflda`, `ldsflda` on any field declared in a game or Unity assembly |
| G2 | Calls to `set_*` accessors, `add_*`/`remove_*` event accessors, or methods named `Set*`, `Add*`, `Remove*`, `Clear*`, `Toggle*`, `Increment*`, `Decrement*`, `Unlock*`, `Research*`, `Purchase*`, `Sell*`, `Repay*`, `Validate*`, `Cancel*`, `Destroy*`, `Kill`, `Execute*`, `Pay*`, `Register*`, `Deregister*`, `Enqueue*` on game types. The only exceptions are the three `SceneManager` event adds in §9.5 |
| G3 | Any member in `denylist.json`. Initial content is §9.3 plus the `avoid` members of `research/data-map.json`. The gate also fails if any allowlist entry is denylisted |
| G4 | Reflection: `FieldInfo.SetValue/SetValueDirect`, `PropertyInfo.SetValue/GetSetMethod`, `MethodBase.Invoke`, `Type.InvokeMember`, `Activator.CreateInstance` (on any type), `Delegate.CreateDelegate`, any `System.Reflection.Emit` member, `System.Linq.Expressions.*.Compile`, `AppDomain`/`Assembly.Load*`. `Type.GetField` and `FieldInfo.GetValue` are allowed only inside `ReflectionTable`. Every `GetField` name argument MUST be a literal matching an allowlisted private-field entry |
| G5 | Unity mutators: `Object.Instantiate/Destroy/DestroyImmediate/DontDestroyOnLoad`, `GameObject.AddComponent/SetActive/SendMessage*/BroadcastMessage`, `Component.SendMessage*`, `Time.set_*`, `PlayerPrefs.*`, `SceneManager.Load*/Unload*`, `Application.Quit/OpenURL`, `Input.*` |
| G6 | Any reference to `0Harmony`, `DevConsole.*`, `PAConsole`, `SavegameManager`, `SavegameStorage`, `QuicksaveManager`, `AutosaveManager`, any type in namespace `UI*` or named `*ViewModel` |
| G7 | Type rules: more than one `Mod` subclass; any abstract `Mod` subclass; any observer type deriving from a game type other than `ProjectAutomata.Mod`; any game interface implemented; any `ProjectAutomata` attribute (`Savegame*` etc.) applied; `DllImport`; `unsafe` code |
| G8 | Process, network and IPC APIs: `System.Diagnostics.Process.*` (except `Process.GetCurrentProcess().Id`), `System.Net.*`, `System.IO.Pipes.*`, `System.IO.MemoryMappedFiles.*`, `System.Threading.Thread.Abort/Suspend` |
| G9 | Layering: only types in namespace `RoiMcp.Observer.ReadLayer` may reference game or Unity members (apart from the `Mod` entry class's lifecycle overrides and `SceneManager` handlers). Only types in `RoiMcp.Observer.Io` may reference `System.IO` file APIs. Types in `RoiMcp.Observer.Publish` must not reference game or Unity members at all |
| G10 | The release build must not contain the `RoiMcp.Observer.DebugFaults` marker type |

### 10.4 Allowlist governance

Each allowlist entry is a JSON object:

```json
{"member": "...", "kind": "getter|method|field_read", "data_map_id": "...", "evidence": "File.cs:line",
 "review": "summary of the decompiled body", "transitive_findings_ack": [...]}
```

- **Transitive scan.** For every allowlisted game method or getter, the gate MUST walk the callee bodies inside `Assembly-CSharp`/`firstpass` to depth 4. It reports reachable `stfld`/`stsfld` on non-`this`-local objects, calls to denylisted members, `UnityEngine.Random`, and `World.deterministicRandom`.
  - Each finding MUST be acknowledged in `transitive_findings_ack` with a justification, e.g. "benign lazy cache: `LazyComponentRef._value`", "struct-local cache in `TransportRequestHandle._cache`".
  - An unacknowledged finding fails the gate.
  - An acknowledgement MUST NOT cover a write into a game dictionary or list.
- **Per-trip amount inputs.** `DynamicWorldEvent.NeededProductAmount` dispatches to the abstract `ProductTargetDynamicEventObjective.NeededProduct`, whose implementations were **not** reviewed in research. It MUST NOT be allowlisted until every implementation has been reviewed and the transitive scan is clean (see §12.3 `world_event_cap`).

### 10.5 Gate tests

- The gate has its own test suite with **negative fixtures**: small test assemblies that each contain exactly one violation (a setter call, `GetSafe`, `GetBalance`, `FieldInfo.SetValue`, a Harmony reference, a game interface, `stfld` on a game field, an off-layer game reference, an unlisted `GetField` literal, etc.).
- Every fixture MUST make the gate fail with the expected rule id.
- A positive fixture must pass.

---

## 11. Exchange directory, snapshots and IPC

### 11.1 Location and files

- The exchange directory is `%LOCALAPPDATA%\RoiMcp\` (resolved with `Environment.GetFolderPath(SpecialFolder.LocalApplicationData)`). Both components honour the override environment variable `ROI_MCP_EXCHANGE_DIR`.
- E1 MUST confirm that Unity's Mono resolves the expected path.
- The observer MUST NOT write anywhere else. In particular it MUST NOT write to the install directory or `%APPDATA%\RiseOfIndustry`.

| File | Writer | Content | Cadence |
|---|---|---|---|
| `heartbeat.json` | observer | lifecycle and diagnostics (§11.5) | 1 s |
| `static.json` | observer | definitions (§12.1) | per world session / module change |
| `state.json` | observer | live world state (§12.2) | §8.4 |
| `history.json` | observer | game-retained history (§12.4) | monthly / on request |
| `refresh-request.json` | MCP server | refresh nonce (§11.6) | on demand |
| `observer.config.json` | user | config (§8.7) | manual |
| `observer.disabled` | user | kill switch | manual |
| `observer.log`, `observer.log.1` | observer | log (§19) | rotating |
| `server.log*` | MCP server | log (§19) | rotating |

### 11.2 Envelope (all observer-written files)

```json
{
  "schema": "roi-mcp/state",                 // heartbeat | static | state | history
  "schema_version": "1.0.0",                 // semver; server accepts same major only
  "observer_version": "1.0.0",
  "compatibility": "verified",               // static/state/history: always "verified" (§3.2); heartbeat: verified | unsupported_build
  "game": {"version": "2.3.3", "build": "0507b", "commit": "76359e59…", "savegame_version": 2304,
           "assembly_sha256": "D62599EF…"},
  "pid": 20300,
  "world_session": "3f0c…",                  // null in heartbeat when not ready
  "seq": 812,                                // per file family, monotonic within observer process lifetime
  "content_hash": "sha256 of data",          // used to skip unchanged writes
  "captured": {"utc_start": "…", "utc_end": "…", "game_day_start": 27893, "game_day_end": 27893,
               "game_date": "Y78-06-24", "frames": [123456, 123470], "main_thread_ms": 11.7,
               "consistent": true},
  "static_ref": {"seq": 3, "content_hash": "…"},  // state/history only: which static.json they were built against
  "sections": {"buildings": {"status": "ok", "game_day": 27893, "items": 412, "items_vanished": 0}, "…": {}},
  "warnings": [],
  "data": { … }
}
```

- Field-level content of `data` is defined by the JSON Schemas in `schemas/`. Those schemas MUST derive their field semantics from `research/data-map.json` (each schema property SHOULD carry `x-data-map-id`). `data-map.json` remains the single source for member mapping; `schemas/` is the single source for wire format.
- `game_date` uses the format `Y<year>-<MM>-<DD>` (30-day months, 360-day years, year 1 = first year).

### 11.3 Atomic publication

1. Serialize to `<name>.json.tmp-<pid>` in the exchange directory, on the background thread.
2. `Flush(true)` and close.
3. `File.Replace(tmp, target, null)` if the target exists, else `File.Move`.
4. On `IOException`, retry 3 times (100/200/400 ms). Then skip the publication, keep the temp file deleted, and record `publish_failures` in the heartbeat.

Further rules:

- On observer start, delete leftover `*.json.tmp-*` files in the exchange directory (own pattern only).
- If `content_hash` equals the last published one, skip the write and update only `state_verified_utc` in the heartbeat (R-PERF-6).
- Size caps (enforced before writing): `heartbeat` ≤ 16 KB, `static` ≤ 4 MB, `state` ≤ 5 MB, `history` ≤ 5 MB. The `state` cap is the research limit (PRD-NOTES PERF-5). The others are hard caps with headroom over the research estimates (heartbeat < 1 KB, static 1–2 MB, history 0.2–2 MB; ARCHITECTURE §5.1).
  - Over the cap, optional sections are dropped in a documented priority order, and the drop is recorded in `warnings` and `sections`.
  - A truncated or partial JSON file MUST never be written.

### 11.4 Readers (MCP server)

- Open with `FileShare.ReadWrite | Delete` semantics (Python: plain read with retry), read fully, parse, and validate against the schema.
- On failure, retry once after 100 ms. Then keep the last good snapshot of the same `world_session` and add the warning `snapshot_invalid_using_previous`. If there is no previous one, return the error `snapshot_unavailable`.
- Reject a file whose `schema_version` major differs from the server's (error `schema_mismatch`).
- Reject `state`/`history` whose `static_ref` does not match the loaded `static.json`: reload static first, and if it still does not match, return the warning `static_mismatch`.

### 11.5 Heartbeat content

Fields: `state`, `paused`, `speed_level`, `time_scale`, `game_date`, `game_day`, `main_thread_last_tick_utc`, `frame_count`, `world_session`, `module_id`, `language`, `compatibility` (`verified` | `unsupported_build`), `detected_game` (versions and hashes), `expected_game` (§3.1 values), `reflection_self_check` (summary; full detail on change only), and for each family (`static`, `state`, `history`): `seq`, `last_published_utc`, `last_verified_utc`, `size_bytes`.

Capture statistics: `last_capture` (`main_thread_ms`, `slices`, `max_slice_ms`, `alloc_bytes_approx` via `GC.GetTotalMemory(false)` delta, `gc_count_delta` via `GC.CollectionCount(0)`), `effective_interval_s`, `degraded`, `disabled_sections`, `id_collisions`.

Frame statistics: `frame_ms_p50/p95/p99` over the last 60 s from `Time.unscaledDeltaTime`, for diagnostics and E3.

Other: `refresh_seen` and `refresh_served` (per scope: `state`, `history`, `static`), `publish_failures`, `errors_last_hour`, `observer_version`, `exchange_dir`, `cwd` (E1), `written_utc`.

The heartbeat contains no world data beyond these status values.

### 11.6 Refresh request: boundary

The refresh request is the **only** data flowing towards the game. It is not a command channel.

```json
{"schema": "roi-mcp/refresh-request", "schema_version": "1.0.0",
 "requests": {"state": 1730000000123, "history": null, "static": null}, "requested_utc": "…"}
```

- The file holds at most one pending nonce (int64 or null) per scope, under the three fixed keys `state`, `history` and `static`. The server is its only writer. It publishes the file atomically and preserves the other scopes' nonces when it updates one.
- The observer reads the file only when its mtime changes, rejects files larger than 1 KB, and parses only those three integer values. Anything else is ignored.
- Semantics, per scope: "produce a fresh snapshot of this family when it is safe to do so". The observer serves each pending scope at the next `ready` frame, subject to `min_gap_s`, the single-flight rule (one family at a time; `state` first, then `history`, then `static`) and back-off. It then reports the served nonce for that scope in the heartbeat (`refresh_served: {state, history, static}`).
- No string from the file is ever used as a member name, path, query, filter or entity id. There is no per-entity request in V1.
- Outside `ready`, nonces are recorded (`refresh_seen`) and served on the next `ready` entry. The captures performed on `ready` entry (static, state, history) satisfy all nonces seen before that entry; the observer reports them as served once those captures publish. Nonces that arrive while `unsupported_build`, `disabled` or `faulted` are never served (the server reports the lifecycle error).
- If implementation shows the mechanism adds risk, it MAY be removed in favour of purely periodic capture. The MCP `fresh` parameter (§13.2) then degrades to "wait up to the timeout for the next periodic capture", with identical response semantics.

### 11.7 Liveness and staleness rules (server)

| Condition | Server classification |
|---|---|
| No process named `Rise of Industry` | `game_not_running` |
| Process exists, no heartbeat with that PID, or heartbeat older than process start, after a 60 s grace from process start | `observer_not_detected` (grace period: `starting`) |
| Heartbeat PID matches but `written_utc` older than 5 s | `observer_unresponsive` |
| Heartbeat fresh but `main_thread_last_tick_utc` older than 5 s | `game_unresponsive` (warning; e.g. heavy loading) |
| Heartbeat `state` ∈ {`menu`, `loading`, `disabled`, `unsupported_build`, `faulted`} | that state |
| Heartbeat `ready` | live |

A state or history snapshot is **current** iff all of the following hold:

- the classification is live;
- `snapshot.world_session == heartbeat.world_session`;
- `snapshot.pid == heartbeat.pid`;
- `age ≤ max(15 s, 3 × effective_interval_s)`, where age is measured from `max(captured.utc_end, last_verified_utc)`.

Otherwise it is **stale**, with one `stale_reason` from: `age`, `world_session_changed`, `not_in_game`, `game_not_running`, `observer_unresponsive`.

---

## 12. Snapshot content (V1)

Field names and semantics are taken from `research/data-map.json`. The subsections below fix scope and the non-obvious semantics.

### 12.1 `static.json`

Content:

- products: id, display name, English name, category, tags, price formula name, demand modifier, endgame and contract flags;
- product categories;
- recipes: ingredients, results, `gameDays` (effective, respecting easy chains), `gameDaysForPriceCalculation`, required modules, tier, compatible building types;
- building types: base cost, tags, category, display and English names, and these component values:
  - available recipes;
  - storage `slots`;
  - `maxModuleCount`, `radius`, module prefab;
  - upkeep `buildingCostPercentage` and `minUpkeep`;
  - efficiency arrays;
  - fleet vehicle prefab and capacity;
  - manual destination slot count;
  - name format;
  - shop `maxProducts`, `soldTags`, `demandModifier`;
- tech trees and categories, and unlock nodes: kind, prerequisites, included unlocks, tier, placements, effects for building/recipe/price unlocks, generic unlocks as names only;
- formulas (name → text);
- money bill categories and overview groupings;
- settlement tiers and types, permit types, loan infos;
- `TechTreeManagerConfig` values;
- English names for every exported definition.

Rules:

- English names come from en-US `LanguageData` with key `"{ConcreteTypeName}.{assetName}.{field}"`, lower-cased with spaces removed (HIGH confidence; validated in E1). Until validated, an English name that cannot be resolved MUST be `null` with the warning `english_name_unavailable`. The server falls back to the asset name for search.
- The observer reads definitions from the live `GameData.instance`. It MUST NOT embed research dump values. Research values (`research/notes/static-dump.md`) are test oracles only.

### 12.2 `state.json` sections

| Section | Scope | Default | Data-map ids |
|---|---|---|---|
| `session` | date, day, speed level, time scale, paused, difficulty (copy), world parameters, module, language, mods, `active_actor_differs`, cheats used, achievements enabled | required | `game.*` |
| `companies` | the player in full (identity, HQ, cash, loans, shares, cashflow label, top production/sales, owned permits, main tech tree, building counts by tag/type, derived value inputs); every AI as a summary (the same minus loans detail; `cash: {"infinite": true}` when `MoneyAgent.infiniteMoney`) | required | `company.*` |
| `buildings_player` | every building owned by `Player.humanPlayer` except decoration: identity, type, tags, coordinates, rotation, region, city, flags, efficiency, upkeep (monthly, accrued, days up), `paid_to_build`, recipe, progress (factory or per module), counters, inventory for recipe products and accepted products (count, slots, put/pull reservations, inbound cap), module list, logistics options, `auto_wh` + target warehouse, requirement notifications, pollution at tile, `isPolluted` | required | `building.*`, `production.*`, `gatherer.*` |
| `routes_player` | all manual destination slots of player buildings with a product and destination (§12.3) | required | `logistics.*` |
| `requests_player` | warehouse pull requests of player endpoints | required | `logistics.logistic_requests` |
| `buildings_ai` | AI buildings: compact rows (identity, type, owner, coordinates, region, city, flags, recipe, produced last month) | required (compact) | `building.*` |
| `buildings_ai_detail` | full detail as for the player | optional, `include_ai_building_detail` | — |
| `routes_ai` | AI manual routes | optional, `include_ai_routes` | — |
| `shops` | all shops: identity, city, owner, accepted products; per product: stock, slots, player-delivered stock, demand raw and for the player, price for the player, price modifier, overall sold over the last 30 days; days to next price update | required | `shop.*` |
| `cities` | identity, type, tier, population, population limit, growth state inputs, dead flag, advancement, contract offer, consumption interval, house count | required | `city.*` |
| `regions` | identity, centre coordinates, tile count, city, resources (counts; water flagged), resource sites, permit owner and amount paid, derived permit cost, cooldowns | required | `region.*` |
| `market` | per product in the market key set: value, price, modifier, trend; State sold products and markups; State `allowIncomingTrade`; active contracts (player); current auction and queue copy | required | `market.*` |
| `research` | player: unlock states, queue, active unlock, progress map, remaining time and cost, efficiency index, unlock points, `building_costs` (current build price per building type: the value `TechTreeAgent.GetBuildingCost` returns, read from the implementer's private price table `_buildingPrices` without calling the method; `null` when the table is unreadable). AI: unlock state set and counts | required | `tech.*`, `building.cost` |
| `vehicles` | aggregates per owner × network × product (count, cargo in transit), per player fleet building (active/inactive count, capacity), and per-vehicle compact rows for player vehicles (session id, prefab, network, fleet building, cargo product and amount, job origin/destination, going home, position) | required | `logistics.vehicles`, `logistics.fleet` |
| `route_paths` | decimated path geometry per player route | optional, `include_route_paths` | `logistics.routes` |

Settlement houses (about 2,000 in the sample save) are counted per city, not listed.

### 12.3 Logistics: first-class semantics

Each route row (`routes_player`) MUST contain the fields below. Provenance: O = observed from game state; G = computed by the game and read through an allowlisted pure call; R = replica of a game formula computed by the observer from observed inputs; D = derived by the server.

| Field | Prov. | Definition / source |
|---|---|---|
| `route_id` | D | §7.1 |
| `origin` | O | building id, display name, coordinates `{x,y}`, owner |
| `destination` | O | `slot.destination` building id, display name, coordinates, owner, kind (`shop`, `warehouse`, `factory`, `gatherer`, `farm`, `state_trading`, `wholesaler`, `contract_target`, `other`, from tags/components), city id |
| `endpoint` | G | `GetDestinationForSlot(slot)` (module-resolved endpoint). `null` → `errors` gets `destination_module_missing` |
| `product` | O | product id |
| `source` | O | `own` or the depot module prefab of `slot.source` |
| `transport_mode` | G | `GetNetworkForSlot(slot).networkName` (`Road`, `Rail`, air network name per U5) |
| `paused`, `wait_for_full_vehicle` | O | slot fields |
| `dormant_auto_warehouse` | O | origin `logistics.options & AUTO_WH` |
| `max_send` | O / R | §12.3.1 |
| `min_keep` | O | §12.3.2 |
| `distance_tiles` | G | `slot.distance`: tile count of the game's cached path (Road/Rail), straight-line tile distance for air. Game value 0 means "no cached path" → export `null` with `path_status: "unavailable"`. Otherwise `path_status: "cached"` |
| `straight_line_tiles` | D | Euclidean and Chebyshev distance between origin and destination tiles, labelled as estimates |
| `dispatch_cost` | G | `slot.dispatchCost`: the game's predicted cost **per vehicle dispatch** (formula evaluation, main thread). For player routes using own trucks the formula is `ManualDestinationDispatchCost = (250 + distance × 10) × difficulty × actor`; depots use their own formulas (`notes/static-dump.md` §2). The observer also exports the formula asset name used. `0` with no path → `null` |
| `vehicle_capacity` | G | `GetTransportForSlot(slot).GetVehicleCapacityForNetwork(network, product)` (pure: dictionary `TryGetValue` + slots or tech override) |
| `dispatch_amount_now` | R | §12.3.3 |
| `cost_per_unit_at_capacity` | D | `dispatch_cost / vehicle_capacity` (server) |
| `in_flight` | O | from `slot.activeRequests`: for each handle with `IsValidHandle`, `status` (NEW/STARTED/…) and `productAmount`. Exported as `{requests_new, requests_started, units_requested, units_started}` |
| `destination_stock` | O | destination `Count(product)` (stored minus pull reservations) |
| `destination_incoming_reserved` | O | destination `_storage[product].puts` (reflection `TryGetValue`). This is what the Max Send cap counts in addition to stock |
| `destination_free_space` | O | destination `FreeSpace(product, ignorePut: false, ignorePull: true)` |
| `origin_stock` | O | origin `Count(product)` |
| `validation_error` | O | `slot.validationError` (error type name) / `hasError` |
| `errors[]` | D | `no_path`, `destination_module_missing`, `validation_error:<type>`, `destination_dead_city`, `product_not_accepted` (destination `acceptedProducts` lacks the product) |

#### 12.3.1 Max Send

**Internal semantics (CONFIRMED in source):**

- `maxAcceptedAtDestination` returns `destination.productStorage.GetMaxAccepted(product)`, or the shop's current demand `Shop.GetDemand(product, origin.owner)` when `slot.autoMaxAccepted` is set.
- The value is **stored on the destination building**, per product. It is **shared by every origin** that ships that product to that destination; changing it from one supplier's panel changes it for all.
- It caps the destination's stock **including incoming put reservations** (in-flight and queued deliveries). It is **not** a per-trip quantity and not a per-route quota.
- `0` means unlimited.

The game computes headroom as `max(maxAccepted − (slots − freeSpace), 0)`, where `slots − freeSpace` = stored + incoming reservations (`ManualDestinationManager.cs:595-626`).

**Exposed MCP semantics:**

```json
"max_send": {"value": 8, "unlimited": false, "mode": "manual",          // manual | auto_shop_demand
             "scope": "destination_product_shared",
             "headroom_now": 3,                                         // R: max(value − (stock + incoming_reserved), 0); null if unlimited
             "ui_label_validated": false}                              // true after E2
```

- In manual mode the value is read with `_maxAcceptedMap.TryGetValue` (missing = 0). For `SingleProductStorage` and `InfiniteStorage` destinations, use their `_maxAccepted` fields.
- In `auto_shop_demand` mode the value is the demand read via the pure `Shop.GetDemand(product, origin owner)`.
- Tool descriptions MUST state the shared, destination-side semantics.
- The UI label "Max Send" is mapped to `ManualDestinationSlotViewModel.maxAmount` (HIGH confidence; labels live in prefabs). Until E2 passes, responses MUST carry `ui_label_validated: false`.

#### 12.3.2 Min Keep

**Internal semantics (CONFIRMED):** `slot.minStoredAtSource` (saved field `_minStoredAtSource`) is **per slot (per route)**. It is the floor of origin stock that this route will not dispatch below. Its range is 0–99, and `int.MaxValue` means "keep all" (the route never dispatches).

**Exposed:**

```json
"min_keep": {"value": 1, "keep_all": false, "ui_label_validated": false}
```

#### 12.3.3 Dispatch amount (per trip)

The game's own computation, `ManualDestinationManager.GetRequestedAmount`, is **private** and calls `maxAcceptedAtDestination` (a `GetSafe` insert). It MUST NOT be called. The observer replicates it from pure inputs (`ManualDestinationManager.cs:595-626`):

```
cap       = vehicle_capacity                                   (G)
available = max(origin Count(product) − min_keep, 0)            (O)
free      = destination FreeSpace(product, ignorePut:false, ignorePull:true)   (O; reviewed pure for ProductSpecificProductStorage (CountHelper uses TryGetValue) and InfiniteStorage (returns int.MaxValue); every other IProductStorage implementation reachable as a destination MUST pass the allowlist review of §10.4 before use, else the route exports complete:false)
if max_send.value > 0: free = min(free, max(max_send.value − (slots − free_before), 0))
if active contract (owner.contracts.TryGetContract(product, destination.owner)):   (pure, reviewed)
      free = min(free, max(0, amount − (delivered + reserved))); cap = min(cap, free)
if active dynamic world event targets destination for product: world_event_cap (see below)
amount = min(cap, available, free); if wait_for_full_vehicle and amount < cap: amount = 0
```

Export:

```json
"dispatch_amount_now": {"value": 7, "complete": true, "limited_by": "available|cap|max_send|free_space|contract|world_event|wait_full",
                        "inputs": {…}, "method": "replica:ManualDestinationManager.GetRequestedAmount"}
```

The world-event cap depends on unreviewed abstract `NeededProduct` implementations (§10.4).

- Until they are reviewed and allowlisted, a route whose destination is targeted by an active dynamic world event for that product MUST export `complete: false` and `limited_by` including `world_event_unevaluated`.
- Detecting such an event uses only `worldEvents.GetActiveDynamicEvents()` (returns the field), `completed`, `targetBuilding` and `GetObjective<T>()` (a cast). `FilterProduct` purity MUST also be reviewed before use; until then, the presence of any active product-target event whose `targetBuilding.logistics == destination` sets `complete: false`.

Tick-to-tick, the game's actual dispatch also depends on the scheduler, vehicle availability and priority resolution (`TransportPriorityResolver`). `dispatch_amount_now` is "what the next dispatch on this route would request now". It is not a throughput guarantee.

#### 12.3.4 Route paths and other logistics data

- **Path geometry** (`include_route_paths`): copy `TryGetPath` tiles on the main thread in the same frame. Decimate to turning points, max 256 points per route. These paths are pooled, so they MUST NOT be held across frames. Export only when E3 shows it fits the budgets.
- **Warehouse requests:** product, requested amount (`int.MaxValue` → `fill`), remaining, amount being moved, priority, active, full vehicles, allowed depot graphs (prefab names), expenses this and last month, endpoint pull disabled, global `logisticRequestsEnabled`. Avoid `requestName`.
- **Transport history:** only what the game keeps (§12.4). There is no per-route history.

### 12.4 `history.json` (game-retained history only)

| Section | Content | Game retention (research) |
|---|---|---|
| `ledger_player` | Per month, per bill category asset, per counterparty kind: income, expense. Built from `MoneyAgent.GetIncome/GetExpenses(from=(Y,M,1), to=(Y,M,30), category)` for each retained month and category. `ListPool` rules apply; prefer the totals methods | Monthly aggregates; years Y-2..Y (up to ~4 years just before year end), pruned at year end. Counterparty history vanishes when an AI is removed |
| `buildings_monthly_player` | `BuildingAnalysis` series per player building: production, efficiency %, upkeep, uptime, dispatch cost, dispatch count | Game retention (UNKNOWN length; export what exists) |
| `production_monthly_player` | Per player recipe user and product: produced and consumed per month (aggregated from the daily `ProductInfoCollection` via range queries) | ~1–2 years daily |
| `shops_monthly` | Per shop and product: overall sold and demand per month | ~1–2 years |
| `player_product_stats` | `ProductionStatsTracker` per product for the current window: produced, production cost, distribution cost, sold, price sold, profit, markup | Current window |
| `state_sales` | State sold counts and figures | ~2 months |

**Bounded windows.** The per-building, per-product and per-shop series (`buildings_monthly_player`, `production_monthly_player`, `shops_monthly`) are a bounded recent window of at most 24 calendar months, not everything the game retains (exporting all retained values broke the E3 budgets). Every such series carries `window_months`, `window_first_month`, `window_last_month` (the current, in-progress month), `history_truncated` (`true` when the game holds data older than the window, `false` when the window covers everything, `null` if undetermined) and `first_month_available` (oldest month the game holds, when cheaply readable; else `null`). `ledger_player` covers the game's whole ledger retention and reports `retention_years`, `window_months`, `first_month_available`, `last_month` and `history_truncated`. Tools pass this metadata through (§14.2 `retention`, §14.3 `history`); `docs/SNAPSHOT-FORMAT.md` documents the fields.

V1 MUST NOT synthesize history the game does not keep. In particular:

- **Market price history does not exist**. `get_market` reports current values only, plus `price_history: {"available": false, "reason": "not_retained_by_game"}`.
- **No daily money ledger** exists.
- **No per-route history** exists.

The server MAY keep **in memory only** the last 20 state snapshots of the current world session, or 30 minutes of them, whichever is smaller. This supports short-term deltas (inventory delta between snapshots, labelled with the two snapshot dates). This bounded cache is justified by the "inventory delta" capability, is never persisted, and is cleared on `world_session` change.

---

## 13. MCP server: common behaviour

### 13.1 Lifecycle

- Start via stdio from the client. On start the server loads whatever valid files exist and classifies liveness (§11.7). It never blocks start-up waiting for the game.
- It re-reads `heartbeat.json` at most every 1 s, and only when needed by a call (lazy, with a 1 s cache).
- It reloads `state`, `history` and `static` when their seq or hash changes.
- Restarting the server while the game is open MUST yield identical answers from the same files.
- The server MUST write protocol messages only to stdout. Logs go to stderr and `server.log` (§19).

### 13.2 Common parameters

Every tool whose refresh scope (§13.7) is not `none` accepts:

- `fresh` (bool, default false). Only on tools whose refresh scope (§13.7) is not `none`. The server writes a refresh request for **that tool's scope** (the caller never chooses the family) and waits up to `refresh_wait_s` (default 3, max 10) for the heartbeat's served nonce of that scope to reach the request nonce and for that family's seq or `last_verified_utc` to advance. On timeout it answers from the latest snapshot with the warning `refresh_timeout`.
- `allow_stale` (bool, default false). See §13.4.

Paging, sorting and field selection are accepted exactly where §14 lists them; no tool accepts them for symmetry only:

- **Paged tools** (the ten `list_*` tools, plus `search`, `find_production_issues`, `find_shops` and `get_tech_tree`) accept `limit` (integer 1–50, default 25; `research/PRD-NOTES.md` MCP-6) and `cursor` (opaque: only a `page.next_cursor` value returned for the same tool and arguments). §14 writes this pair as "paging".
- `fields` (`compact` | `full`, default `compact`) is accepted by the ten `list_*` tools only. `list_warehouse_requests` and `list_vehicles` already return complete rows, so both values return the same rows there.
- `sort` exists only where §14 lists its values: `list_buildings`, `list_routes`, `list_cities`, `find_shops`.

Text arguments (names, ids, filters, queries, cursors, and the string items of array arguments) MUST contain a non-whitespace character. An empty or whitespace-only value is `invalid_argument`; it never means "no filter". Omitting an optional parameter means no filter.

### 13.2a Refresh coalescing (server)

- The server keeps at most one outstanding nonce per scope. A `fresh` call that arrives while a request for the same scope is outstanding and younger than `refresh_wait_s` reuses that nonce and waits on it; it does not write a new one.
- Writing a request for one scope preserves the other scopes' outstanding nonces in the file (§11.6).
- The observer additionally coalesces (single flight, `min_gap_s`, back-off). A `fresh` call can therefore time out under back-off; the answer then carries `refresh_timeout`. Timeout semantics are unchanged.

### 13.3 Response envelope and errors

```json
{
  "ok": true,
  "meta": {
    "server_version": "1.0.0", "schema_versions": {"state": "1.0.0", "static": "1.0.0"},
    "game_state": "ready",                       // §11.7 classification
    "compatibility": "verified",
    "world_session": "3f0c…",
    "source": "live_snapshot",                   // live_snapshot | static_catalog | stale_snapshot | none
    "snapshot": {"family": "state", "seq": 812, "captured_utc": "…", "age_s": 3.2, "game_date": "Y78-06-24",
                 "consistent": true, "sections_used": ["routes_player"], "sections_unavailable": []},
    "snapshots": [ /* every family used: state, history, static (in that order); snapshot = snapshots[0] */ ],
    "paused": false,
    "stale": false, "stale_reason": null,
    "warnings": []
  },
  "data": { … },
  "page": {"next_cursor": null, "total": 12}
}
```

Envelope rules:

- **`meta.source`** is defined by the data the answer was built from, with one deterministic precedence: `stale_snapshot` if any state or history snapshot used is stale (any `stale_reason`, including `age`, §13.4); else `live_snapshot` if a current state or history snapshot was used, also when static catalogue data was combined with it; else `static_catalog` if only `static.json` was used; else `none` (nothing read, e.g. `get_game_status` or an error before any data was read).
- **`meta.snapshots[]`** is the multi-source representation: one entry per family the answer used (`state`, `history`, `static`, in that order) with seq, capture time, age, game date, consistency, world session, sections used and unavailable, and, for state and history, `stale` and `stale_reason`. A mixed answer (e.g. `get_product` while live) lists both `state` and `static`. `meta.snapshot` is `snapshots[0]` (or `null`).
- **`meta.warnings[]`** elements are objects `{code, detail}`: `code` from the closed list below, `detail` free text or `null`.

Error form: `{"ok": false, "error": {"code": "...", "message": "...", "hint": "...", "candidates": [...]}, "meta": {...}}`.

| Error code | When |
|---|---|
| `game_not_running` | §11.7 |
| `observer_not_detected` | §11.7 (after the grace period; hint: installation docs) |
| `observer_unresponsive` | §11.7 |
| `at_main_menu`, `loading` | heartbeat state |
| `observer_disabled`, `observer_faulted`, `unsupported_build` | heartbeat state |
| `snapshot_unavailable` | `ready` but no valid snapshot of the required family yet |
| `section_unavailable` | the required section is disabled, failed or optional-off, in `state`, `history` or `static` (a failed static section the answer is built from is never read as an empty catalogue). The message names the section and reason |
| `schema_mismatch` | major version mismatch |
| `not_found` | id or name not resolvable |
| `ambiguous` | name matches multiple entities; `candidates` lists ids with type, owner and coordinates (max 10) |
| `stale_reference` | session-scoped id from another world session |
| `invalid_argument` | parameter validation |
| `internal_error` | unexpected server fault; the envelope is still returned (the server never crashes on a call) and the server log has the trace |

Warnings (non-fatal): `stale`, `refresh_timeout`, `fresh_not_applicable`, `snapshot_invalid_using_previous`, `static_mismatch`, `inconsistent_snapshot`, `english_name_unavailable`, `ui_label_unvalidated`, `section_degraded`, `active_actor_differs`, `catalog_from_previous_session` (§13.4), `game_unresponsive` (§11.7), `truncated` (§14.9). `section_degraded` is set when the observer reports a degraded capture or when a section the answer does not depend on failed; the missing parts are listed in `unavailable` (§13.5).

### 13.4 Stale handling (never silently stale)

- **Live and current:** normal response.
- **Live but stale by age** (same world session, `ready`): data is returned with `stale: true`, `stale_reason: "age"` and `source: "stale_snapshot"` (stale data is never labelled live, §13.3).
- **Not live, or different world session:** runtime tools return the corresponding error code. With `allow_stale: true` they return the last snapshot with `source: "stale_snapshot"`, `stale: true` and the reason.
- **Static catalogue tools** (products, recipes, building types, tech definitions) answer from `static.json` whenever it exists, with `source: "static_catalog"`. If the game is not live, they add the warning `catalog_from_previous_session`. Exception: while the heartbeat reports `unsupported_build`, static tools also return `unsupported_build` (§16).
- **Static-plus-live tools without usable live state.** Tools that combine the catalogue with live state (`search`, the catalogue tools of §14.6) answer from the catalogue alone when no usable current `state` snapshot exists: game not live (without `allow_stale`), no valid snapshot yet, the latest `state.json` schema-invalid with no previous valid one, or a major schema mismatch. The answer has `source: "static_catalog"`, and `unavailable` lists `live:state` with `<error code>: <reason>` (e.g. `snapshot_unavailable: No valid state snapshot: latest state.json is invalid (…)`) plus every live field left empty. An invalid or major-mismatched `state.json` is never read. When the latest `state.json` is invalid but a previous valid snapshot of the session exists, every tool uses that snapshot with the warning `snapshot_invalid_using_previous`.

### 13.5 Provenance model

Provenance is expressed at **section level** inside `data`. Individual fields are not annotated, except where a section mixes kinds. Each detail object contains:

- `observed`: values read from the game snapshot. `persistence` documents whether the value is SAVE-backed or RUNTIME-only, per schema annotations from `data-map.json` `source`.
- `definition`: static catalogue values.
- `game_computed`: values returned by allowlisted game calls (e.g. `distance_tiles`, `dispatch_cost`, `vehicle_capacity`, `GetFinalProductionTime`).
- `derived`: computed by the observer (replicas, `method: "replica:<game method>"`) or by the server (`method: "<derivation id>"`, §15).
- `unavailable`: list of `{field, reason}` for requested data the game does not provide or a disabled, failed or optional-off section prevents. `field` is a response path (e.g. `existing_route`, `history.monthly_analysis`), `live:<family>` for a whole missing live family, or `static.<section>` for a failed static section. Missing data is listed here, never reported as zero or empty.

Freshness is carried by `meta`. Schemas document each field's class (STATIC / SAVE / RUNTIME / DERIVED) via `x-source`.

### 13.6 Name resolution

The server indexes the following:

- ids;
- display names (as captured, i.e. French), case- and accent-insensitive;
- English names from static, when available;
- internal asset names;
- for buildings, display name plus city and owner context.

Matching rules:

- An exact match on one entity resolves directly.
- Multiple matches return `ambiguous`.
- Fuzzy matching is used **only** in `search`, never implicitly in other tools.
- Ids and bare id keys are matched exactly and case-sensitively (§7.1). Names (display, English, asset) are matched case- and accent-insensitively, with whitespace collapsed and the ligatures œ/æ folded to oe/ae.
- `search` scores names, keys and ids as text with these same normalization rules; its results always carry the exact id.

### 13.7 Refresh scope per tool (normative)

The server derives the refresh-request `scope` from this table. Tools never refresh families not listed for them.

| Scope | Tools |
|---|---|
| `none` (no `fresh` parameter; reads heartbeat or the static catalogue only) | `get_game_status`, `get_recipe` |
| `state` | `search`, `list_companies`, `get_company`, `list_buildings`, `get_building`, `get_production_overview`, `find_production_issues`, `list_routes`, `get_route`, `list_warehouse_requests`, `list_vehicles`, `get_supply_chain`, `list_products`, `get_product`, `list_recipes`, `list_building_types`, `get_building_type`, `list_cities`, `get_city`, `get_shop`, `find_shops`, `list_regions`, `get_region`, `get_market`, `get_tech_tree`, `get_research_state` |
| `history` | `get_finances` |
| `static` | no tool. Reserved for the server's automatic recovery when a `static_mismatch` persists after one reload (§11.4). The observer also re-exports static data by itself on `ready` entry and module change |

Rules for mixed tools:

- **`get_building` with `include: ["history"]`.** `fresh` refreshes `state` only. The history part comes from the latest `history.json`, and `meta.snapshot` reports both families (`history` with its own seq and age).
- **`get_supply_chain` with `mode: "recipe"`** uses only static data. The server writes no refresh request, and `fresh` is a no-op for that call (reported as the warning `fresh_not_applicable`).
- **Static-plus-live tools** (`list_products`, `get_product`, `list_recipes`, `list_building_types`, `get_building_type`, `get_tech_tree`) refresh `state`, because their live parts come from it: prices, unlocked flags, player costs and node states. When no current state snapshot exists, their definition part follows the static catalogue rules (§13.4) and the live part is listed in `unavailable` (or served stale with `allow_stale`).
- **`search`** refreshes `state`, so newly built or renamed entities become resolvable. Static names are always indexed.
- **While paused**, refresh requests are served normally (the observer is `ready`). Outside `ready` they are deferred (§11.6), so `fresh` returns the lifecycle error code or times out according to §13.4.

---

## 14. MCP tool catalogue (V1, required)

V1 requires exactly **29** tools: 2 status/lookup (§14.1), 3 company/finance (§14.2), 4 building/production (§14.3), 4 logistics (§14.4), 1 supply chain (§14.5), 6 catalogue (§14.6), 6 city/shop/region (§14.7) and 3 market/technology (§14.8). §13.7 gives each tool's refresh scope.

All tools are read-only and expose no parameter that could cause a game write. The only file the server ever writes in the exchange directory is `refresh-request.json`. In the "Prov." column, O = observed, S = static, G = game-computed, R = observer replica, D = server-derived.

Response shapes below are normative at the level of top-level keys. Exact field schemas live in `schemas/tool-responses/*.schema.json`.

### 14.1 Status and lookup

| Tool | `get_game_status` |
|---|---|
| Purpose | Liveness, lifecycle, freshness, compatibility, diagnostics |
| Params | none (refresh scope `none`, §13.7) |
| Response | `game` {running, pid, state, paused, speed_level, time_scale, game_date, world_session, module, language, compatibility, detected_game, expected_game}; `observer` {version, last captures per family with time/duration/size, effective_interval_s, degraded, disabled_sections, reflection_self_check summary, errors_last_hour}; `server` {version, schema versions}; `limitations` (static list of V1 unavailable data) |
| Errors | none. Always answers, including when the game is not running |
| Prov. | O (heartbeat) |

| Tool | `search` |
|---|---|
| Purpose | Resolve names ("Factory 5", "Limoges", "peinture", "Paint") to ids |
| Params | `query` (required); optional `kinds[]` (building, building_type, product, recipe, city, region, company, tech, shop), `owner` (player/ai/company id), paging |
| Response | `results[]` {id, kind, display_name, english_name, owner, city, coordinates, match_kind (exact/prefix/fuzzy), score} |
| Notes | Without usable live state (§13.4) it searches the static catalogue only: `source: "static_catalog"`, `unavailable` lists `live:state` (with the reason, e.g. the schema-invalid file) and `live_entities`; with `owner` it also lists `owner_filter` and returns no results, because ownership cannot be checked |
| Prov. | index over S + O |

### 14.2 Companies and finances

| Tool | `list_companies` |
|---|---|
| Purpose | Player and competitor overview |
| Params | optional paging, `fields` |
| Response | `companies[]` {id, name, is_player, color, hq_city, cash (number, or {infinite: true}), loans_total, cashflow_label, region_count, building_counts_by_tag, main_tech_tree, shares_owned_by_others} |
| Prov. | O (+D value) |

| Tool | `get_company` |
|---|---|
| Params | optional `company` (id/name; default player) |
| Response | `identity`; `cash`; `loans[]` {type, title, lender, principal, apr, duration_months, remaining_payments, monthly_payment (D), grace_months_left (O, reflection), early_repay_amount}; `shares` {bundles[] {owner}, owned_by_competitors}; `value` (D replica of CompanyStats with inputs); `total_assets` (D); `stats` {cashflow_label, top_production[], top_sales[], owned_permits[], main_tech_tree}; `buildings_summary` {by_type, by_tag}; AI only: `ai` {personality, owned_regions[], has_initiative, product_goals[]} (`product_goals` only if the brain-state read is allowlisted after review; else listed in `unavailable`) |
| Notes | AI cash is reported as infinite (prefab flag) |

| Tool | `get_finances` |
|---|---|
| Purpose | Revenue and expenses by month and category; evidence for profit changes |
| Params | optional `company` (default player; AI only if its ledger was exported, else `section_unavailable`), `months` (1 … retained, default 6), `categories[]`, `group_by` (`category` (default) or `overview_group`) |
| Response | `months[]` {month: "Y78-05", income_total, expense_total, net, by_category[] {category id, display/English name, income, expense, net}}; `month_over_month[]` (D: per category delta between consecutive months); `current_month_to_date` flag on the in-progress month; `retention` {first_month_available, last_month, retention_years, months_available, window_months, history_truncated, note} (§12.4); `balance_now` |
| Prov. | O (history) + D |
| Errors | `snapshot_unavailable` if no history captured yet; `fresh: true` triggers a `history` refresh (§13.7) |

### 14.3 Buildings and production

| Tool | `list_buildings` |
|---|---|
| Params | optional `owner` (player default / ai / company id / all), `kind` (factory, gatherer, farm, harvester, field, warehouse, depot, shop, hq, other), `building_type`, `product` (produces or consumes), `recipe`, `city`, `region`, `status` (working, idle, disabled, blocked), `sort` (name, type, stock_ratio, produced_last_month, upkeep), paging, `fields` |
| Response | rows {id, display_name, type, owner, city, region, coordinates, status, recipe, produced_last_month, max_stock_ratio (D), upkeep_monthly}. AI rows come from compact data |
| Prov. | O + D |

| Tool | `get_building` |
|---|---|
| Purpose | Full inspection of one building |
| Params | `building` (required: id or name); optional `include[]` subset of {`production`, `inventory`, `outgoing_routes`, `incoming_routes`, `requests`, `modules`, `history`, `vehicles`} (default all except `history`) |
| Response | `identity` {id, save_guid, display_name, type, english_type_name, owner, coordinates, rotation, region, city, paid_to_build}; `status` {user_enabled, requirements_met, is_working, derived_status, notifications[], evidence[] (D)}; `efficiency` {index, output_multiplier, upkeep_multiplier}; `upkeep` {monthly_full, monthly_active, accrued_this_month}; `production` {recipe, inputs[] {product, amount_per_cycle, per_30d (D)}, outputs[] {…}, cycle_days_base (S), cycle_days_effective (G), progress, remaining_days (D), produced_this_month, produced_last_month, total_produced, average_10_months, uptime_ratio, theoretical_output_per_30d (D)}; `inventory[]` {product, role (input/output/other), count, slots, fill_ratio (D), incoming_reserved, outgoing_reserved, inbound_cap (max_send stored here)}; `outgoing_routes[]` (§12.3 rows); `incoming_routes[]` (routes whose destination is this building; D reverse index); `requests[]`; `modules[]` {id, type, progress, resource, deposit_remaining}; `history` {monthly_analysis series, production_monthly[] with the window metadata of §12.4, window_note}; `vehicles` {fleet active/inactive, capacity} |
| Notes | Detail for AI buildings is limited to compact fields unless `buildings_ai_detail` exists; missing parts go in `unavailable` |

| Tool | `get_production_overview` |
|---|---|
| Purpose | Per-product production and consumption balance for a company |
| Params | optional `company` (default player), `product` |
| Response | `products[]` {product, producers[] {building id, recipe, theoretical_per_30d, produced_last_month}, consumers[] {building id, recipe, theoretical_need_per_30d}, theoretical_supply_per_30d (D), theoretical_demand_internal_per_30d (D), produced_last_month, stock_total, shops_demand_total (O, per consumption interval, normalized to 30 days D), balance_per_30d (D)} |

| Tool | `find_production_issues` |
|---|---|
| Purpose | Evidence rows (not judgements) for idle, starved, blocked or saturated buildings and broken routes |
| Params | optional `company` (default player), `product`, paging, `kinds[]` (`disabled`, `no_recipe`, `missing_input`, `output_full`, `no_modules`, `deposit_depleted`, `polluted`, `requirements_unmet`, `route_error`, `route_dormant_auto_wh`, `route_keep_all`, `inventory_accumulating`) |
| Response | `issues[]` {kind, building id, product?, route id?, evidence {fields and values}, since (if derivable)} |
| Prov. | D from O (§15.6) |

### 14.4 Logistics

| Tool | `list_routes` |
|---|---|
| Purpose | Configured logistics routes with Max Send, Min Keep, distance and cost |
| Params | optional `origin`, `destination`, `product`, `company` (default player), `transport_mode`, `include_dormant` (default true), `errors_only`, `sort` (distance, dispatch_cost, product, origin), paging, `fields` |
| Response | `routes[]` rows per §12.3 (compact: route_id, origin {id, name, coords}, destination {id, name, kind, city, coords}, product, transport_mode, max_send, min_keep, distance_tiles, dispatch_cost, vehicle_capacity, dispatch_amount_now.value, in_flight summary, paused, dormant, errors) |

| Tool | `get_route` |
|---|---|
| Params | `route` (required id); optional `include_path` (only if `route_paths` captured; else `section_unavailable`) |
| Response | full §12.3 row including inputs of `dispatch_amount_now`, max-send headroom, destination stock and reservations, origin stock, cost per unit at capacity (D), straight-line distances (D), in-flight requests[] {status, units}, vehicles currently assigned (session ids, cargo) |

| Tool | `list_warehouse_requests` |
|---|---|
| Params | optional `endpoint`, `product`, `company` (default player), paging, `fields` (`compact` and `full` return the same rows) |
| Response | rows per §12.3.4 |

| Tool | `list_vehicles` |
|---|---|
| Params | optional `company` (default player), `transport_mode`, `product`, `fleet_building`, `aggregate` (default true), `vehicle` (one vehicle id), paging, `fields` (`compact` and `full` return the same rows) |
| Response | aggregate: groups[] {owner, transport_mode, product, vehicles, units_in_transit}; non-aggregate: rows {vehicle id (session), prefab, mode, fleet building, cargo, origin, destination, going_home, position}; `identity_note` |
| `vehicle` lookup | Looks up one vehicle by its session-scoped id `vehicle:<world_session>:<instance id>` (§7.3) and returns its non-aggregate row (implies `aggregate: false`; player vehicles only, so with an AI `company` the answer is `section_unavailable`). An id of another world session → `stale_reference` (acceptance A15: a vehicle id kept across a quickload is rejected, never matched to the pooled object that now has the same instance id). A malformed id (wrong form, prefix not exactly `vehicle`, non-integer instance id) → `invalid_argument`. A well-formed id of the current session that is not active in the snapshot → `not_found`. The other filters still apply to that row |

### 14.5 Supply chains

| Tool | `get_supply_chain` |
|---|---|
| Purpose | Production graph for a product or around a building, plus theoretical upstream requirements |
| Params | one of `product` or `building` (required); optional `direction` (`upstream` default / `downstream` / `both`), `depth` (default 6, max 12), `company` (default player), `mode` (`actual` default: the company's buildings and configured routes; `recipe`: static recipe graph only), `target_output_per_30d` (number, for theoretical requirement scaling), `recipe_choice` (map product → recipe id, when several recipes exist) |
| Response | `nodes[]` {building id or product/recipe node, type, recipe, theoretical_per_30d, produced_last_month, stock}; `edges[]` {from, to, product, kind (`configured_route`, `warehouse`, `auto_wh`, `observed_in_flight`, `recipe`), route_id?, max_send?, min_keep?, distance_tiles?, dispatch_cost?}; `requirements` (D) {per product: required_per_30d to meet target or current consumer capacity, available_theoretical_per_30d, gap}; `raw_inputs_total` (D, e.g. total Gas per 30 days for the Paint chain); `cycles_detected`; `truncated_at_depth` |
| Ids | Every product, recipe and building reference in the output is a `<kind>:<key>` id (§7.1), including `requirements.target.product`, the keys and `recipe` values of `requirements.recipes_used`, `cycles_detected` and `truncated_at_depth`. `truncated_at_depth` also lists requirement products whose inputs lie beyond the depth limit |
| Notes | Recipe math uses recipe ratios: `Chemicals` (Gas 3 → Chemicals 2, 20 days) and `Paints` (Chemicals 1 + Dye 2 → Paint 2, 35 days) are research-confirmed examples and serve as test oracles |

### 14.6 Catalogue (static)

| Tool | Params | Response |
|---|---|---|
| `list_products` | optional `category`, `tag`, `query`, `unlocked_only` (player), paging, `fields` | rows {id, display/English name, category, base price (O runtime), current price (O), trend, unlocked} |
| `get_product` | `product` | `definition` (S); `market` {value, price, modifier, trend, final_price_for_player} (O); `recipes_producing[]`, `recipes_consuming[]` (S); `player_producers[]`, `player_consumers[]` (O); `shops_accepting` {count, total_demand_per_interval, consumption_interval_days}; `state_offer` {sold_by_state, price}; `price_history` {available: false, reason} |
| `list_recipes` | optional `product`, `building_type`, `available_to_player` (tech), paging, `fields` | rows {id, names, inputs, outputs, days, building types, unlocked} |
| `get_recipe` | `recipe` (id/name) or `product` | full definition + compatible building types + unlocking tech + per-30-day normalized I/O (D) |
| `list_building_types` | optional `tag`, `product`, `unlocked_only`, paging, `fields` | rows {id, names, base_cost, upkeep_monthly_base (D: base_cost × buildingCostPercentage), tags} |
| `get_building_type` | `building_type` | definition with component values (§12.1), recipes, unlock tech, `current_player_cost`: the player's current build price for this type, i.e. the value `TechTreeAgent.GetBuildingCost` returns, taken from state `research.player.building_costs` (§12.2), or `base_cost` when the type is not in the player's price table; regional cost modifiers are not applied. `null` with an `unavailable` entry when no current state exists or the price table was not readable |

### 14.7 Cities, shops, regions

| Tool | Params | Response |
|---|---|---|
| `list_cities` | optional paging, `fields`, `sort` (population, tier) | rows {id, name, tier, population, growth_state (D), region, shop_count} |
| `get_city` | `city` | identity, type, tier (+ next tier threshold), population, population_limit, growth_state (D per `SettlementUiViewModel` logic), dead, consumption_interval_days, advancement {state, contracts[]}, contract_offer, shops[] {id, type, accepted products with demand/stock/price}, houses_count, region |
| `get_shop` | `shop` (building id/name) | identity, city, accepted products[] {product, stock, slots, player_delivered_stock, demand_raw, demand_for_player, price_for_player, price_modifier_pct, sold_last_30d}, days_to_next_price_update, demand_unit_note ("units per consumption interval") |
| `find_shops` | `product` (required); optional `from_building` (id), `city`, `region`, paging, `sort` (`demand`, `price`, `distance`, `unmet_demand`) | rows {shop id, name, city, demand_for_player, stock, player_delivered_stock, price_for_player, unmet_demand (D), straight_line_tiles from `from_building` (D, labelled estimate), `existing_route_status`, existing_route, straight_line_cost_estimate}. `existing_route_status` is machine-readable route knowledge from `from_building` to the shop: `present` (a configured route exists: `existing_route` {route_id, distance_tiles, dispatch_cost, …, authoritative: true} carries the game's own values and `straight_line_cost_estimate` is `null`); `absent` (the origin's route section was read and has no route to the shop: `existing_route` is `null` and `straight_line_cost_estimate` (D-ROUTE-2) is `{"value": <number>, "distance_kind": "straight_line", "route_exists": false, "authoritative": false, "formula": "ManualDestinationDispatchCost"}`); `unavailable` (route existence cannot be established because the origin's route section was not captured: `routes_ai` for an AI or State origin, off by default, or a failed `routes_player`: `existing_route` is `null`, no claim that a route is absent is made, `straight_line_cost_estimate` MAY be returned with `"route_exists": null, "authoritative": false`, and `unavailable` lists `existing_route` with the section and reason); `null` when `from_building` is not given (then all three are `null`) |
| `list_regions` | optional `owner` (player/company id/unowned), `resource`, paging, `fields` | rows {id, name, city, owner, permit_cost (D), resources summary} |
| `get_region` | `region` | identity, centre, tile count, city, permit {owner, amount_paid, cost (D)}, cooldowns, resources[] {product, tile_count or `water_unlimited`}, resource_sites[], player/AI building counts in region |

### 14.8 Market and technology

| Tool | Params | Response |
|---|---|---|
| `get_market` | optional `products[]`, `include` {state, contracts, auctions} | `prices[]` {product, value, price, modifier, trend, final_price_for_player}; `next_update_in_days` (D, from interval 15 and the last update day if readable, else omitted); `state` {sold_products[] {product, price_for_player}, incoming_trade_allowed, purchase_rule}; `contracts` {player_active[], city_offers[]}; `auctions` {current, queue[]}; `price_history` {available: false} |
| `get_tech_tree` | optional `tree`, `state` filter (unlocked, available, queued, researching, locked, teaser), `company` (default player), paging | `trees[]`; `nodes[]` {id, names, tree placements, tier, prerequisites[], included[], unlocks {buildings[], recipes[], price_discounts[], generic}, state (D for `available`), progress, research_cost_per_day and research_days at current efficiency (G, CAUTION formula calls: computed once per capture for queued, active and available nodes only)} |
| `get_research_state` | optional `company` | active, queue[], progress, remaining_days, remaining_cost, efficiency index and value, unlock_points, unlocked_count, total_nodes |

### 14.9 Tool-surface constraints

- Every response MUST be ≤ ~30 KB serialized by default. Larger results paginate or truncate with `page.next_cursor` and a `truncated` warning.
- There is no `dump_*` / "everything" tool. MCP **resources** MAY expose the static catalogue (`roi://static/products`, …) as an extra.
- Tool descriptions MUST state that tools are read-only, name the relevant semantics (Max Send shared destination cap, AI cash infinite, demand unit, `straight_line_cost_estimate` is a non-authoritative estimate for routes that do not exist) and remind the model to check `meta.stale`.
- Text from the game (building, company and city names) is returned as data only. Tool descriptions note that such strings are user content (R-SEC-6).

---

## 15. Deterministic derivations (server and observer)

Each derivation has an id (used in `method`), a unit test with hand-computed fixtures, and returns inputs alongside outputs where practical.

| Id | Definition |
|---|---|
| D-RATE-1 | Factory theoretical output per 30 days per result = `amount × 30 / cycle_days_effective`, where `cycle_days_effective = GetFinalProductionTime()` (G; game days). Input need per 30 days analogous with ingredient amounts |
| D-RATE-2 | Gatherer/farm theoretical output per 30 days = Σ over modules of `amount × 30 / cycle_days_effective × module_speed`, where `module_speed` = harvester replica `clamp(Σ(node has amount ? 1 : depletedModifier)/maxResources × _efficiency, minGuaranteedProductionSpeed, 1)`, or `_efficiency` for fields (R, HIGH confidence). If inputs are unreadable, fall back to `moduleCount × amount × 30 / cycle_days_effective` and mark `approximate: true` |
| D-RATE-3 | Uptime ratio = `framesSpentProducing / productionFrames` (O) |
| D-REQ-1 | Supply chain requirement: for target output T of product P using recipe r, `runs = T / result_amount(r,P)`; each ingredient `i` needs `runs × amount(r,i)` per same period; recurse with the recipe chosen per §14.5 `recipe_choice`, else the recipe used by the player's producing buildings, else the first recipe in `RecipeDatabase.GetRecipes(P)` (flagged). Example oracle: 2 Paint needs 1 Chemicals; 2 Chemicals needs 3 Gas; so 1 Paint needs 0.5 Chemicals needs 0.75 Gas (per cycle ratios) |
| D-INV-1 | Fill ratio = `count / slots`; accumulation flag when fill ratio ≥ 0.9 for an output product, or when the count rose across the in-memory snapshot window (labelled with dates) |
| D-INV-2 | Inventory delta between two snapshots of the same world session: `count_t2 − count_t1` with both game dates; never across sessions |
| D-SHOP-1 | Unmet demand (player) = `max(demand_for_player − player_delivered_stock, 0)` per consumption interval; per 30 days = `× 30 / consumption_interval_days` |
| D-SUPDEM-1 | Supply/demand ratio per product = theoretical_supply_per_30d / (internal need + shop demand per 30 d), with a `null` denominator guard |
| D-ROUTE-1 | Cost per unit at capacity = `dispatch_cost / vehicle_capacity`; at current dispatch amount = `dispatch_cost / dispatch_amount_now.value` (null if 0) |
| D-ROUTE-2 | `straight_line_cost_estimate` for a shop/destination **without** a configured route: the `ManualDestinationDispatchCost` formula text from `static.json` evaluated with the straight-line tile distance (D-DIST-1, Euclidean), current `difficulty.dispatch`, and actor modifier = 1 unless the actor modifier is exported. Output `{value, distance_kind: "straight_line", route_exists: false, authoritative: false, formula}`. It MUST NOT be computed or shown for an existing route; existing routes use the game's `distance_tiles` and `dispatch_cost` (§12.3). When route existence is unknown (origin route section not captured), it MAY be shown with `route_exists: null` next to `existing_route_status: "unavailable"` (§14.7); it never asserts that no route exists |
| D-VAL-1 | Company value replica: Σ over regions whose full-permit owner is the company of (permit cost + Σ `paid_to_build` of the company's buildings in the region) × `max(1, 1 + 0.1 × bundles owned by competitors)`; bundle price = `min(round(0.1 × value), 999000000)` |
| D-PERMIT-1 | Permit cost = `round(tiles × costPerTile(top-level parent) × costModifier × (city ? 1 : 0.75))` |
| D-GROW-1 | Growth state: WaitingForSponsor → Bloated → Prospering → Growing → Stagnating, using the `SettlementUiViewModel` order and growth inputs exported by the observer (`IsWaitingForSponsor`, `populationLimitReached`, `IsProspering`, `IsGrowing` are pure reads per research; the observer evaluates them in the read layer if allowlisted, else exports inputs `_consumedProducts` and thresholds) |
| D-STATUS-1 | Building status (§15.6) |
| D-LOAN-1 | Monthly payment = `amount × (1 + apr) / duration × modifier` (modifier 1 unless a settlement loan modifier is exported) |
| D-FIN-1 | Month-over-month per category delta and share of total change |
| D-DIST-1 | Straight-line distances: Euclidean and Chebyshev on tile coordinates (the game's own `Settlement Distance Restriction` formula is Chebyshev) |

### 15.6 Building status derivation (D-STATUS-1)

Evaluated in order. All reasons that apply are reported as evidence; the first one sets `derived_status`.

1. `!UserEnabled` → `disabled`.
2. No recipe on a recipe user → `no_recipe`.
3. Module owner with `moduleCount == 0` → `no_modules`.
4. `!RequirementsMet` → `blocked`. Evidence: the notifications (player buildings) plus the checks below.
5. Missing input: an ingredient with `count < amount` while not working.
6. Output full: `slots − count(result) < amount` for some result (approximation that ignores reservations; labelled).
7. Polluted (`isPolluted`).
8. All modules with depleted deposits (from module resource data).
9. `IsWorking` → `working`; else `idle`.

---

## 16. Freshness and lifecycle behaviour (normative matrix)

| Situation | Observer | Heartbeat state | Server behaviour for runtime tools | Static tools |
|---|---|---|---|---|
| Game not running | — | file stale or absent | `game_not_running` (or stale data with `allow_stale`) | from last `static.json` + warning, else `snapshot_unavailable` |
| Game at main menu | heartbeat only | `menu` | `at_main_menu` | last static + warning |
| Save loading | heartbeat only | `loading` | `loading` | last static + warning |
| Save active | captures | `ready` | live responses | live static |
| Game paused | captures every 15 s + refresh requests per scope (§13.7) | `ready`, `paused: true` | live; `meta.paused: true` | live |
| Save unloading / return to menu | abandon capture, drop refs | `menu`/`loading` | `at_main_menu`/`loading`; old snapshot only via `allow_stale`, `stale_reason: not_in_game` | warning |
| Quickload (`game → game`) | new `world_session` after `ready` | `ready → loading → ready` | old-session snapshot never served as current; until the new snapshot exists: `snapshot_unavailable` (or stale with `world_session_changed`) | static reloaded if changed |
| Game closing / crash | nothing (killed) | stale | `game_not_running` (process gone) or `observer_unresponsive` (process hung) | warning |
| Observer starting | boot | `starting` | `observer_not_detected` during the grace period (SHOULD be 60 s from process start; engineering default, adjustable after E1), then actual state | — |
| Observer faulted / section failures | per §8.6 | `faulted` or `ready` + `disabled_sections` | `observer_faulted` or `section_unavailable` for affected tools only | — |
| MCP starting before game | — | — | `game_not_running` until the heartbeat appears; no restart needed | — |
| MCP restart while game open | unaffected | unchanged | reloads files; identical answers | — |
| Stale snapshot (age) | — | `ready` | data + `stale: true, stale_reason: age` | — |
| Corrupted/incomplete file | — | — | last good of same session + warning, else `snapshot_unavailable` | same |
| Schema mismatch | — | — | `schema_mismatch` with both versions | same |
| Unsupported build (§3.2) | heartbeat only (detected + expected versions/hashes); zero world/static/history capture; no override | `unsupported_build` | `unsupported_build` | `unsupported_build` |

---

## 17. Performance requirements

These limits come from `research/ARCHITECTURE.md` §5, `research/RISKS.md` §2 and `research/PRD-NOTES.md` §8. They are initial values. E3 MAY adjust them only with recorded measurements and a rationale in `docs/VALIDATION-REPORT.md`.

| ID | Requirement |
|---|---|
| PERF-1 | Observer main-thread work per frame ≤ `frame_budget_ms` = 2 ms (p99 ≤ 3 ms over a 10-minute run) |
| PERF-2 | Total main-thread time per `state` capture ≤ 50 ms on the largest available test save (§22 E3 selection). Exceeding it triggers back-off (§8.4); three breaches drop optional sections |
| PERF-3 | Average frame time with the observer active differs by ≤ 2 % from the observer disabled (kill switch), same save, same speed, 10 minutes at 10× (A/B measured by the observer's frame statistics) |
| PERF-4 | State capture interval ≥ 5 s real time while running, ≥ 15 s while paused; refresh-driven captures ≥ 1 s apart; adaptive cap keeps observer main-thread share ≤ ~1 % |
| PERF-5 | Managed allocation per `state` capture: target < 2 MB on the 2.8 MB sample-class save (sample-class save: ~150 player buildings, 249 routes, 91 vehicles); measured with `GC.GetTotalMemory` delta and `GC.CollectionCount(0)` delta. No LINQ, no storage enumeration, reused buffers and DTO pools in capture paths |
| PERF-6 | Serialization and file I/O only on the background thread |
| PERF-7 | Snapshot sizes: `state` ≤ 5 MB at 3× the sample save counts; `static` ≤ 4 MB; `history` ≤ 5 MB; `heartbeat` ≤ 16 KB |
| PERF-8 | Writes skipped when content is unchanged; at most one `state` write per effective interval |
| PERF-9 | (SHOULD; engineering target, not from research, server-side only so it cannot affect the game) MCP tool latency excluding the `fresh` wait ≤ 500 ms p95 on the largest fixture; server memory ≤ 500 MB |
| PERF-10 | No observer-induced GC spikes (R-PERF-1, ARCHITECTURE E3 "no extra GC spikes visible in frame-time trace"). Operationalized as: the number of frames > 50 ms during a 10-minute observer-active run MUST NOT exceed the range observed across two observer-disabled baseline runs of the same save and speed |

If a capture exceeds its budget, the observer MUST degrade (back-off, drop optional sections, mark `degraded`). It MUST NOT exceed per-frame budgets to "finish quickly".

---

## 18. Save safety

- The observer MUST NOT reference save APIs (gate G6) and MUST NOT write outside the exchange directory.
- The active save MUST never be edited, moved, renamed or deleted by any project component or by the implementer.
- Any save analysis in the implementation phase (E7, fixtures) MUST operate on copies, with SHA-256 of original and copy recorded before and after.
- Before the first observer deployment, the implementer MUST back up `%APPDATA%\RiseOfIndustry` by copying it to `%LOCALAPPDATA%\RoiMcp\backups\saves-<timestamp>\` with `scripts/backup-saves.ps1`. The script is copy-only: it MUST NOT modify, lock, rename, move or delete any source file. It opens sources read-only with sharing that allows the game to keep writing.
  - **Game not running** (no `Rise of Industry` process): copy all files and record source and copy SHA-256 in `manifest.json`. A file is valid when its copy hash equals its source hash.
  - **Game running:** for each file, record size, mtime and SHA-256 before copying; copy; then record size, mtime and SHA-256 of the source again and the SHA-256 of the copy. A file is valid only if all of these match (before = after = copy).
  - **Stability cannot be established** (any mismatch, or a read error): the script MUST discard that backup folder (it deletes only its own new backup folder) and stop with a message asking the user to return to the main menu, or close the game, and retry. A partial or unverified backup MUST NOT be reported as valid. The implementer MUST NOT deploy the observer until a valid backup exists.
  - `manifest.json` lists, per file: relative path, size, source mtime, `sha256_source_before`, `sha256_source_after` (running case), `sha256_copy`, plus `game_running`, the start/end time and `valid: true|false`.
- Mod side effect (accepted, documented): saves made while the observer is enabled list `RoiMcpObserver` in their header. Loading them later without it shows a cosmetic "missing mods" notice. Achievements are **not** disabled (`AchievementManager.disableWithMods` = 0, CONFIRMED from scene assets).

---

## 19. Logging and diagnostics

| Item | Requirement |
|---|---|
| Observer log | `%LOCALAPPDATA%\RoiMcp\observer.log`, UTF-8 lines `utc level component message`. Rotation at 1 MB to `observer.log.1` (max 2 files). Written by the background thread from a bounded in-memory queue (1,000 entries; overflow drops with a counter) |
| Observer log content | Startup (observer version, game versions, compatibility, exchange dir, CWD), lifecycle transitions, reflection self-check (once per session), capture summary at most once per minute (count, max/avg duration, sizes), errors with signature dedup (same signature ≤ 1 line per minute plus a suppressed count), config changes, refresh requests served. **Never** snapshot contents |
| Unity log | At most: one line on load and one line on fatal self-disable. No per-capture `Debug.Log` (feeds the in-game bug reporter, R-SEC-4) |
| Server log | stderr + `%LOCALAPPDATA%\RoiMcp\server.log`, rotation 5 MB × 3. Tool name, args digest (not full payloads), duration, result code, snapshot seq. No stdout logging |
| Diagnostics surface | `get_game_status` (§14.1). `scripts/collect-diagnostics.ps1` zips heartbeat, logs, config, schema versions and file sizes (never saves, never `state.json` unless `-IncludeSnapshots`) |
| Version info | Observer version, schema versions, game version, compatibility in heartbeat, every file envelope and `get_game_status` |

---

## 20. Installation boundary and approval protocol

Rules for the implementer:

1. **No approval needed:**
   - writing, building and testing code outside the game installation;
   - static analysis of game assemblies, read-only from the install or the research copies;
   - running the IL gate;
   - MCP server tests with fixtures;
   - parsing **copies** of saves;
   - copying saves for backup with `scripts/backup-saves.ps1` (copy-only, stability-checked, §18).
2. **Explicit user approval is required once, before the first operation that creates or changes anything inside the Rise of Industry installation directory.** The request MUST state the exact scope, for example:

   > "I will create `<install>\Mods\RoiMcpObserver\` containing `desc.json` and `code\RoiMcpObserver.dll`. During this implementation phase I will redeploy (overwrite) only these two files after rebuilds and delete the folder on uninstall. I will not modify any other file in the installation, any PlayerPrefs/registry value, or any save. I will not perform gameplay actions. Game restarts needed to load a new build will be requested from you."

3. That approval covers only the stated scope. It grants no permission for gameplay changes, save edits or other installation changes.
4. Later rebuild and redeploy cycles within that scope proceed without new approval. The implementer SHOULD batch deployments and the game restarts they require.
5. The implementer MUST NOT start, stop, kill or restart the game process unless the user explicitly authorizes a specific procedure. By default, game restarts and loading saves are requested from the user (batched).
6. **Manual gameplay actions for validation** (E2, E4, E6, V8) are requested from the user. The implementer never performs them through automation. Requests MUST be batched into as few sessions as practical. The implementer SHOULD recommend doing them in a dedicated duplicate save ("Save As" `RoiMcp-Validation`) so the user's real game is unaffected.
7. The implementer asks the user only for:
   - operations it cannot perform autonomously;
   - the installation approval above;
   - manual validation actions.

   All other engineering decisions follow this PRD or reasonable engineering judgment consistent with it.

`scripts/install-observer.ps1` MUST:

- auto-detect the install (Steam `libraryfolders.vdf` + `appmanifest_671440.acf`) or take `-GameDir`;
- verify the build and `Assembly-CSharp` hash (§3.1). On mismatch it MUST abort without installing and print the detected and expected values (the observer would only report `unsupported_build` there; V1 does not support other builds). There is no override flag;
- verify `Mods\` hygiene: every existing subfolder has `desc.json`, and no other mod is named `RoiMcpObserver`;
- copy exactly the two files;
- print what it did.

`scripts/uninstall-observer.ps1` removes exactly `Mods\RoiMcpObserver\`. Neither script touches PlayerPrefs, saves or other files.

---

## 21. Testing strategy

| ID | Level | Requirement |
|---|---|---|
| T-1 | Observer unit | DTO builders from synthetic fact structs; scheduler (intervals, back-off, single flight, coalescing); lifecycle state machine (including `game → game`); refresh-request parser (oversize, malformed, unknown fields or keys, non-integer nonces → ignored; per-scope served tracking; deferral outside `ready`; nonces satisfied by `ready`-entry captures); config clamping (including that no config key can bypass the version gate); version gate (baseline facts → normal; any differing version, build, commit, savegame version or hash → `unsupported_build` with zero capture calls); route replica (`dispatch_amount_now`) and max-send headroom against hand-computed cases from `ManualDestinationManager.cs:595-626`; id generation and collision suffixing; size-cap dropping order |
| T-2 | Publisher | Atomic replace (temp naming, retry, cleanup of stale temps), skip-unchanged, size caps, never-partial-file (kill during write simulation) |
| T-3 | Read-only gate | §10.5 negative and positive fixtures; gate runs on every build of the observer |
| T-4 | Schema | Observer output fixtures validate against `schemas/`; server rejects invalid files and major mismatches; every schema property maps to an `x-data-map-id` or is an envelope field |
| T-5 | Server unit | Snapshot store (reload on seq/hash, last-good fallback, world session switch); liveness classification matrix (§11.7) with fake process lists and heartbeats; staleness rules; name resolution and ambiguity |
| T-6 | Derivations | Each D-* id with hand-computed fixtures, including the Paint chain oracle (§14.5) and the gas-to-paint ratio |
| T-7 | MCP contract | Registered tool list equals the 29 tools of §14. Each tool: parameter validation, response schema, pagination, size cap, error codes, `meta` presence and correctness, provenance sections; `fresh` writes exactly the §13.7 scope (and nothing for `none` tools or recipe-mode supply chains); coalescing of concurrent `fresh` calls (§13.2a); no tool with write semantics (static test over the registered tool list) |
| T-8 | Malformed/stale | Truncated JSON, wrong schema, empty files, old `world_session`, dead PID, old heartbeat, missing static, `static_ref` mismatch |
| T-9 | Lifecycle integration | Scripted fake observer (writes files per scenario) driving the server through every row of §16 |
| T-10 | Fixtures from save copies | `scripts/gen-fixtures-from-save-copy.ps1` uses the research parser on save **copies** to produce realistic snapshot fixtures (gitignored `.local/`), validating ids, counts and route fields (e.g. `_minStoredAtSource=4` slot) |
| T-11 | In-game gates | E1–E7 and V8 (§22), results in `docs/VALIDATION-REPORT.md` |
| T-12 | Soak | 45 min at 10× with observer active vs a 15 min disabled (kill switch) baseline at 10×, same game session; no errors; memory of the game process (private bytes) within the baseline variance, judged on the trend across the observer-on period, not only its endpoints; log size within cap. Duration reduced from 2 h on 2026-10-04, before any result of the run was known; see `docs/VALIDATION-REPORT.md` (T-12 plan) for the rationale |

"Tools return data" is not completion. §25 governs.

---

## 22. Implementation gates (E1–E7 from research, plus V8)

All gates run after the installation approval (§20). Each produces a section in `docs/VALIDATION-REPORT.md` with the evidence. They MUST NOT be run during PRD review.

### E1: Minimal observer loading

| Aspect | Content |
|---|---|
| Purpose | Prove the official loader loads the observer safely, and read runtime constants |
| Prerequisite | Installation approval; saves backed up; observer build in "heartbeat + constants only" mode (no state capture) |
| Validates | Load without error popup or disabled mods; heartbeat in menu and game; exchange-dir path under Mono; `Environment.CurrentDirectory` (U9); `Debug.isDebugBuild`; `secondsPerDay` (expect 8.0), `speedLevels` (expect [1,3,6,10]), `disableWithMods` (expect 0); `_days` delta per frame at 10× (expect ≤ 1); English-name lookup (U-EN) for 10 known products; air network name and the set of network names (U5); reflection self-check all-resolved; `Player.log` presence (U10); `GameVersion` and assembly hash match |
| Evidence | Heartbeat files, observer log, screenshot-free user confirmation that the game behaved normally |
| Pass | All checks as expected; no exceptions in observer log; game unchanged in behaviour (user confirmation) |
| Fail | Error popup, mod disabled in Mod Manager, boot stall, mismatched constants, unresolved reflection entries |
| Decision if fail | Loader failure → fix packaging (single DLL, references, abstract types) before anything else. Constant mismatch → update defaults/derivations and record. Unresolved reflection entries → remove or replace dependent sections. CWD not install dir → document a launch requirement. English names unavailable → ship `english_name: null` with warning |

### E2: Max Send / Min Keep UI correspondence

| Aspect | Content |
|---|---|
| Purpose | Validate the label-to-member mapping (U1) and the shared Max Send semantics |
| Prerequisite | E1 passed; full observer; user in a validation save. **Manual user action** (batched): on a chosen route A→D for product P, set Max Send to 7 and Min Keep to 3 in the UI. Then, on a second origin B shipping P to the same D, read (not change) the displayed Max Send. Then toggle "auto" on a shop destination route |
| Validates | `max_send.value == 7` on A→D and on B→D (shared); `min_keep.value == 3` only on A→D; auto mode shows shop demand; `int.MaxValue` "keep all" mapping if the user sets Min Keep to ∞ |
| Evidence | `get_route` outputs before/after; user-reported UI values |
| Pass | All equal; B's UI shows 7 |
| Fail | Any mismatch |
| Decision if fail | Re-trace the view-model bindings; correct the mapping and tool descriptions; `ui_label_validated` stays false until resolved. Releasing with an unvalidated mapping is not allowed (DoD) |

### E3: Real capture performance

| Aspect | Content |
|---|---|
| Purpose | Measure main-thread cost, allocations, sizes and frame impact; tune defaults |
| Prerequisite | E1 passed. Test save selection is dynamic: the implementer copies every `.sav` currently in `%APPDATA%\RiseOfIndustry` (copy-only, same stability rules as `scripts/backup-saves.ps1`), parses the **copies** with the research parser, and ranks them by total buildings, then vehicles, then AI building count. It selects the top-ranked save, records the ranking with counts in `docs/VALIDATION-REPORT.md`, and asks the user to load that save (manual action). Selection depends only on the saves that exist at that time; no particular file name is assumed. If the largest available save is smaller than the research sample (research sample-save class), E3 still runs on it, and the report notes that PERF-2 is validated at that scale only |
| Validates | PERF-1…PERF-10; cost of optional sections (`buildings_ai_detail`, `routes_ai`, `route_paths`) to decide defaults; observer A/B via kill switch |
| Evidence | Heartbeat capture statistics over ≥ 10 minutes per configuration; frame statistics; `scripts/perf-report.ps1` output |
| Pass | All PERF limits met with default config |
| Fail | Any limit exceeded |
| Decision if fail | Increase intervals, reduce slice budget, move sections to history cadence, keep optional sections off, optimize allocations; record new defaults with measurements. If still failing at 2× default intervals, the snapshot scope is reduced (documented) before release |

### E4: Lifecycle and quickload

| Aspect | Content |
|---|---|
| Purpose | Validate §8.2 and §16 in the real game |
| Prerequisite | E1 passed. **Manual user actions (batched):** boot → menu → load save → quicksave → quickload → return to menu → load another save → quit via menu |
| Validates | Heartbeat state sequence; new `world_session` on each load including quickload; no observer exceptions; no references from the old session served; files valid after quit (`Process.Kill`); saves made with the observer load fine with it; `meta` behaviour of tools at each step |
| Evidence | Heartbeat transition log (observer log), MCP responses captured at each step |
| Pass | Sequence matches; zero observer errors; no stale data served as current |
| Fail | Any deviation |
| Decision if fail | Fix state detection (e.g. add checks on `WorldLoadingScreen` / `MainMenu.IsShown`) and retest |

### E5: Fault handling

| Aspect | Content |
|---|---|
| Purpose | Prove that observer faults cannot affect the game |
| Prerequisite | `DEBUG_FAULTS` build (never released) with config-selected faults: exception in each section, in the publisher, in the scene handler, an unwritable exchange dir, a missing reflection field |
| Validates | Game unaffected; affected section disabled after 3 faults; heartbeat reports it; other sections continue; `faulted` state when appropriate; recovery on next world session |
| Evidence | Logs and heartbeat per fault; user confirmation of normal gameplay |
| Pass | No game-visible effect for any fault |
| Fail | Any game effect (popup, stall, stuck loading, exception in game code) |
| Decision if fail | Redesign the containment for the failing path; this is release-blocking |

### E6: Freshness

| Aspect | Content |
|---|---|
| Purpose | Validate refresh-request and pause behaviour |
| Prerequisite | E2 build. **Manual user action:** while paused, change a setting (e.g. a Min Keep value in the validation save) |
| Validates | `get_route(fresh: true)` writes a `state`-scope request and returns the new value within `refresh_wait_s` (3 s); `get_finances(fresh: true)` writes a `history`-scope request only; without `fresh`, the new value appears within `paused_interval_s` (15 s); `meta.age_s` and `stale` are correct; refresh during `loading` is deferred |
| Pass | Both bounds met |
| Fail | Not met |
| Decision if fail | Adjust the polling of `refresh-request.json` or remove the refresh mechanism (§11.6) and document the periodic-only behaviour |

### E7: Entity identifier correspondence

| Aspect | Content |
|---|---|
| Purpose | Validate the building key scheme and GUID export against the save |
| Prerequisite | E1 passed. **Manual user action:** save the validation game (new save name). The implementer copies that save and parses the copy with the research parser |
| Validates | 1:1 mapping between observer `building:` keys and save entities (prefab + constructor x/y); `save_guid` equals the save GUID where present; zero key collisions (U6), including warehouse modules; route tuples equal save `ManualDestinationSlot` data (destination GUID, product, `_minStoredAtSource`, `_autoMaxAccepted`) |
| Pass | 100 % match for buildings and routes |
| Fail | Any mismatch or collision |
| Decision if fail | Adopt collision suffixing (§7.2) permanently and document; investigate tile semantics for modules |

### V8: End-to-end read-only verification (additional gate; not in research E-series)

| Aspect | Content |
|---|---|
| Purpose | Empirical end-to-end verification that the exercised full tool sweep produces no additional observable game/save mutation beyond the control baseline. It complements, and does not replace, the structural IL gate (§10); see §2.1 |
| Prerequisite | E1–E7 passed. **Manual user actions:** pause the game (validation save); save as `V8-a`; immediately save again as `V8-b` (control); the implementer runs a scripted sweep of every tool with `fresh: true` repeatedly for 5 minutes; user saves `V8-c` |
| Validates | Using save **copies** and the research parser: diff(`V8-a`, `V8-b`) defines the noise baseline (header name, timestamp, camera); diff(`V8-b`, `V8-c`) MUST contain no additional differences. In particular, there must be no new dictionary entries in `_maxAcceptedMap`, `_deliveredByActors` or money balances, no new GUIDs, and unchanged `usedCheats` / achievements flags |
| Pass | No differences beyond the control baseline |
| Fail | Any extra difference |
| Decision if fail | Identify the member responsible, add it to the denylist, fix, and rerun. Release-blocking |

---

## 23. Unresolved items register

| Id | Item | Why unresolved | Resolved by | Behaviour until resolved |
|---|---|---|---|---|
| U1 | UI labels "Max Send"/"Min Keep" ↔ `maxAmount`/`minAmount` | Labels live in UI prefabs | E2 | `ui_label_validated: false` + warning `ui_label_unvalidated` |
| U5 | Air network name; existence of ship transport | Prefab data | E1 enumeration | Export the raw `networkName` string; mode enum includes `other` |
| U6 | Tile uniqueness for warehouse module buildings | Not verifiable statically | E7 | Collision assertion + suffixing (§7.2) |
| U7 | Name of the currently loaded save at runtime | No member identified | E1 investigation (`WorldStartupParameters`, save UI) | `save_name: null` with reason in `get_game_status` |
| U8 | Full capture cost on large maps | Needs runtime | E3 | Defaults from §8.4; optional sections off |
| U9 | Process CWD = install dir (local `Mods\` discovery) | Runtime | E1 | Installation docs state "launch via Steam" |
| U10 | Unity `Player.log` state (0-byte `output_log.txt`, no `Player.log`) | Runtime/player settings | E1 | Observer uses its own log only |
| U-EN | English-name lookup path via `LanguageData` | HIGH, not executed | E1 | `english_name: null` + warning; search uses asset names |
| U-WE | Purity of `ProductTargetDynamicEventObjective.NeededProduct` implementations and `FilterProduct` | Not reviewed in research | Static review during implementation (allowlist process §10.4) | `dispatch_amount_now.complete: false` when a product-target event targets the destination |
| U-AI | Readability of AI brain-state goals without side effects (`AiProductGoal` methods unreviewed) | Not reviewed | Allowlist review | `product_goals` reported as unavailable |
| U-PAY | Which payment-handler formula each depot/building prefab uses; which bill category vehicle dispatch posts to (asset `VehicleUpkeep` seen in saves while `MoneyManager.vehicleUpkeepCategory` has no code call sites) | Per-prefab data not dumped | Runtime static export of handler formula names and bill categories | Export the formula asset name used and category asset names as-is |
| U-MODS | Behaviour with additional content mods | Only the base module, "2130" and two content mods were observed | Runtime | Definitions exported from live `GameData`; ids opaque |
| U-HIST | Retention length of `BuildingAnalysis` series | Runtime/prefab | E1/E3 observation | Export what exists; report first/last month |

---

## 24. Documentation deliverables (user-facing)

| File | Content |
|---|---|
| `README.md` (update) | What it is, read-only promise and its basis, quick start, links |
| `docs/INSTALL.md` | Prerequisites (Windows, Steam RoI 671440 build 9064059, .NET SDK 8 for building, .NET Framework 4.8 dev pack, Python ≥ 3.11 + `uv`); build (`scripts/build.ps1`); observer deployment (`install-observer.ps1`); verifying via `get_game_status`; updating (rebuild + reinstall + restart game); uninstalling (`uninstall-observer.ps1`, plus deleting `%LOCALAPPDATA%\RoiMcp` optionally); the save-header side effect note; "launch via Steam" |
| `docs/MCP-CLIENTS.md` | Claude Desktop/Claude Code configuration (stdio command using `uv --directory <repo>/mcp-server run roi-mcp`); generic MCP stdio configuration for other clients (ChatGPT desktop or any client supporting local stdio servers, as available); environment variables |
| `docs/USAGE.md` | Tool overview, example questions mapped to tools, semantics notes (Max Send shared cap, Min Keep per route, demand units, AI infinite cash, no price history), freshness and `fresh` |
| `docs/TROUBLESHOOTING.md` | Each error code with causes and fixes; mod not loading (Mods hygiene, Mod Manager disabled list); kill switch; collecting diagnostics |
| `docs/KNOWN-LIMITATIONS.md` | Everything in §4.2 and §23 still open at release, plus §12.4 history limits and §7.3 vehicle identity |
| `docs/SNAPSHOT-FORMAT.md` | Envelope, files, versioning, compatibility policy |
| `docs/READ-ONLY-GATE.md` | Gate rules, allowlist governance, how to review a new member |
| `docs/VALIDATION-REPORT.md` | E1–E7, V8 and soak results with measurements and decisions |

No documentation step may require editing game assemblies, using Cheat Engine, memory offsets, or changing game files other than the observer folder.

---

## 25. Acceptance scenarios (end to end)

Each scenario runs against a live validation save with the default config unless stated. Every scenario also requires `meta` to be correct and V8 to hold.

| # | Scenario | Given | When | Then |
|---|---|---|---|---|
| A1 | Building inspection | A player petrochemical plant producing Chemicals, with a gas supplier route and an outgoing route | `search("<its display name>")` then `get_building` | Returns identity (key `building:PetrochemicalFactory@x,y`, display name), recipe `recipe:Chemicals` with inputs Gas 3 → Chemicals 2 and effective cycle days, efficiency level and multipliers, inventory per product with slots and reservations, incoming routes (gas suppliers), outgoing routes each with `max_send` (value, mode, shared scope), `min_keep`, `distance_tiles`, `dispatch_cost`, `vehicle_capacity`, `dispatch_amount_now`; no game change |
| A2 | Production chain | The player produces Paint from Chemicals and Dye, Chemicals from Gas | `get_supply_chain(product: "Paint", target_output_per_30d: 100)` | Graph includes gas wells → petrochemical plant(s) → paint producer(s) → shops with configured edges and route attributes; `requirements` shows Chemicals 50, Dye 100 and Gas 75 per 30 days for 100 Paint (D-REQ-1; Dye's own upstream per its recipe), compared with theoretical capacities and the gap |
| A3 | Shop comparison | Product Paint accepted by several shops | `find_shops(product: "Paint", from_building: <paint plant>)` | Rows with demand for player, stock, player-delivered stock, price, unmet demand, straight-line distance (labelled estimate); for shops with an existing route, the game's `distance_tiles` and `dispatch_cost`; for shops without one, `straight_line_cost_estimate` with `route_exists: false, authoritative: false`. `existing_route_status` is `present` or `absent` accordingly (from a player building). No row shows both |
| A4 | Routes | Player has several gas routes | `list_routes(product: "Gas")` | All routes with Max Send/Min Keep, distances, dispatch costs, errors; dormant AUTO_WH routes flagged |
| A5 | Bottleneck evidence | A factory idle for lack of one input | `find_production_issues()` | Returns `missing_input` with the product, stock vs need, and the inbound routes for it (or none) |
| A6 | Inventory accumulation | A building with output ≥ 90 % full | `list_buildings(sort: "stock_ratio")` / `find_production_issues(kinds: ["inventory_accumulating"])` | The building ranks first with its fill ratio |
| A7 | Finances | Several months of play | `get_finances(months: 6)` | Monthly income and expense per category, net, month-over-month deltas; retention stated; no daily data claimed |
| A8 | City demand | A city named in the game (e.g. "Limoges" on a French-named map) | `get_city("Limoges")` | Shops, accepted products, demand (with unit note), stock and prices; growth state |
| A9 | Technology | Some techs unlocked and research active | `get_tech_tree(state: "unlocked")`, `get_research_state()` | Correct unlocked set, queue, progress, cost/day and days |
| A10 | Regions | Player owns some regions | `list_regions(owner: "player")`, `get_region(<id>)` | Owned regions, resources with counts (water flagged), permit data |
| A11 | Competitors | A save with ≥ 1 AI | `list_companies()`, `get_company(<ai>)`, `list_buildings(owner: <ai>)` | Summary (cash reported infinite), counts, cashflow label, top products, compact building rows; no full AI dump by default |
| A12 | Market | Any | `get_market()` | Current prices, modifiers, trends; State offers; contracts; auctions; `price_history.available: false` |
| A13 | Stale: game closed | Game running, snapshot exists | User quits the game; call `get_building` | `game_not_running`; with `allow_stale: true`, data flagged `stale_snapshot` with reason |
| A14 | Main menu / loading | — | User returns to menu; call tools; user loads a save; call during loading | `at_main_menu`, then `loading`, then live with new `world_session` |
| A15 | Quickload | Live | User quickloads | No response mixes sessions; vehicle ids from the old session → `stale_reference` (e.g. `list_vehicles(vehicle: <old id>)`) |
| A16 | Paused change | Paused | User changes Min Keep; `get_route(fresh: true)` | New value within 3 s |
| A17 | MCP restart | Live | Kill and restart the MCP server | Same answers; game unaffected |
| A18 | MCP before game | Game not running | Start the MCP, then the game, load save | `game_not_running` → `observer_not_detected` (grace) → `at_main_menu` → live, with no MCP restart |
| A19 | Corrupted snapshot | Live | Overwrite `state.json` with garbage (test harness) | Last good snapshot with warning, then recovery at the next capture |
| A20 | Unsupported build | Simulated version mismatch (unit/integration test of the version gate with fake version facts, plus a fake heartbeat for the server) | Start; also try adding `allow_unverified_build: true` to the config | Observer: heartbeat only, `unsupported_build`, detected and expected values reported, zero capture, the config key ignored with a warning. Server: every runtime and static tool returns `unsupported_build`; `get_game_status` shows detected vs expected |
| A21 | Safety | Live validation save | V8 procedure | No state difference beyond control |
| A22 | Performance | Largest test save at 10× | 10-minute runs observer on/off | PERF-1…PERF-10 met |

---

## 26. Definition of Done

V1 is done only when **all** of the following hold. The implementer MUST NOT declare completion with any failing item.

1. The observer builds in release configuration with the IL read-only gate passing (§10), including gate self-tests.
2. The MCP server installs and runs via stdio. All 29 §14 tools are implemented with schemas, pagination, errors, `meta` and the §13.7 refresh scopes. T-7 verifies that the registered tool list equals the §14 list exactly.
3. All JSON Schemas exist and validate the observer fixtures and real E-gate outputs.
4. All automated tests (T-1…T-10) pass. The T-12 soak passes.
5. E1–E7 and V8 passed, with evidence in `docs/VALIDATION-REPORT.md`. Every gate failure was resolved per its decision rule. U1 is resolved (`ui_label_validated: true`).
6. All performance criteria (§17) are met, or adjusted with recorded E3 measurements and rationale.
7. All lifecycle behaviour in §16 is verified (E4 + T-9).
8. There is no evidence of save corruption or gameplay mutation. The read-only claim is supported by both kinds of evidence (§2.1): structural (the IL gate, item 1) and empirical (V8 and the user's confirmation in E1/E5).
9. The installation, MCP client, usage, troubleshooting, uninstall, known limitations, snapshot format and read-only gate documentation (§24) exist and match the implementation.
10. All unresolved items in §23 are either resolved with evidence or documented in `docs/KNOWN-LIMITATIONS.md` with their interim behaviour.
11. The `README.md` status section is updated to reflect the implementation state, without weakening the safety rules.
12. Nothing was written to the game install beyond `Mods\RoiMcpObserver\`. No save was edited. No game file was changed.

---

## 27. Research consistency notes and deliberate deviations

### 27.1 Contradictions in research and their resolution

| # | Contradiction | Resolution in this PRD |
|---|---|---|
| C1 | Topic notes quote **code defaults** that prefab or scene assets override: upkeep `buildingCostPercentage` 0.25 vs asset **0.025**; efficiency upkeep multipliers {0, 0.4, …} vs asset {0.25 … 2.0}; initial efficiency index 4 vs **3**; `maxModuleCount` 5 vs **3**; shop `maxProducts` 9 vs **8**; `maxEnqueuedUnlocks` 8 vs **9**; GlobalMarket update interval 30 vs **15** days | Asset values (CONFIRMED in `research/notes/static-dump.md`) are authoritative. The observer reads live values, never code defaults. Errata blocks already exist at the top of the affected notes |
| C2 | `notes/buildings-production.md`, `notes/tech-static-content.md` and `notes/cities-shops-markets-world.md` use `Player.activeActor` for the player; `notes/company-finance-time.md` shows it can be switched by UI/debug | Use `Player.humanPlayer` (§9.2) |
| C3 | `notes/runtime-lifecycle.md` §8 calls offline save reading out of scope, while `notes/save-format.md` and `ARCHITECTURE.md` recommend a save fallback | Out-of-scope referred to the research agent's constraints. V1 excludes the offline MCP mode by product decision (§4.2). The save parser remains validation tooling (E7, V8, T-10) |
| C4 | `ARCHITECTURE.md` / `PRD-NOTES.md` (MCP-7) include a persistent `HistoryStore` (SQLite) and a `get_history` tool | Deferred by V1 decision. Only game-retained history plus a bounded in-memory window for deltas (§12.4) |
| C5 | `ARCHITECTURE.md` §6 proposes capturing on an unverified build with a flag | Stricter: `unsupported_build`, heartbeat only, zero capture, **no override** in V1 (§3.2) |
| C6 | `data-map.json` `logistics.per_trip_amount` only says "avoid calling `GetRequestedAmount`" | PRD-phase source check: the method is **private** and reads `maxAcceptedAtDestination` (`GetSafe` insert). Its other inputs are pure (`FreeSpace`/`CountHelper`, `GetVehicleCapacityForNetwork`, `TryGetDispatcherForNetwork`, `GetStorageCapacityOverride`, `ContractsAgent.TryGetContract` all use `TryGetValue` or plain loops), **except** the dynamic world-event path (`NeededProduct` is abstract; implementations unreviewed). Hence the replica with the `complete` flag (§12.3.3) and U-WE |
| C7 | `TransportRequestHandle` getters were not covered by research | PRD-phase source check: getters resolve through `AdvancedPooledEntityManager.GetEntity`, which **logs an error** for unknown handles, and cache into a struct-local field. Check `IsValidHandle` first (§9.3) |
| C8 | `RESEARCH.md` U10 notes no `Player.log` present, while `runtime-lifecycle.md` expects `Player.log` on Unity 2018.4 | Unresolved; E1 (U10). Irrelevant to function because the observer logs to its own file |
| C9 | Vehicle dispatch bill category: `MoneyManager.vehicleUpkeepCategory` has no call sites in code, yet saves contain bills in category `VehicleUpkeep` | Likely the asset referenced by the payment handler's `billCategory` (INFERRED). Exported as data; U-PAY |
| C10 | The project brief listed "wages/salaries" as desired data | Research CONFIRMED that RoI has no wages. The efficiency slider (with upkeep multiplier) is the equivalent and is exposed as `efficiency` |
| C11 | `ARCHITECTURE.md` lists `find_bottlenecks`, `get_routes`, `get_market` names | Final names in §14 (`find_production_issues`, `list_routes`/`get_route`, …). Semantics are preserved and evidence-based |

### 27.2 Deliberate deviations from research recommendations

- **Build gating stricter, without override** (C5).
- **No HistoryStore in V1** (C4).
- **Observer subscribes to no game events at all.** Research permitted `TimeManager` subscriptions as "benign"; polling is used instead (§9.5).
- **AI detail opt-in.** AI building and route detail is optional, off by default, pending E3.
- **V8 added.** It is an additional empirical end-to-end read-only gate, complementing the structural IL gate (§2.1).

### 27.3 Consistency checklist (performed for this draft)

| Check | Result |
|---|---|
| Max Send semantics: destination-side, shared, includes incoming reservations, not per-trip | Consistent with `notes/logistics.md` §1 and source re-check |
| Min Keep semantics: per slot, 0–99, `int.MaxValue` = keep all | Consistent |
| Game version: 2.3.3 / 0507b / build 9064059 / savegame 2304 / commit 76359e59… | Consistent |
| Historical claims: only game-retained history; no price history; monthly-only ledger | Consistent |
| Live vs save classification | Shop `_demand` and base prices are runtime-only; routes, max-accepted maps, ledger and tech are save-backed; all are captured live in V1 |
| Unsafe getters | All `GetSafe` users, `GetBalance`, `GetGUIDForObject`, `GetMaxAccepted`, permit getters and shop per-actor getters are on the denylist; none appear in tool data paths |
| Vehicle ids | Not treated as stable (§7.3) |
| Mutation capabilities | None in tools. Refresh nonces only (§11.6). Structural enforcement by the IL gate in the observer; empirical evidence from V8; the claim rests on both (§2.1) |
| IPC | Files only; no sockets, pipes or HTTP in the game; stdio MCP |
| Lifecycle | No game event subscriptions; scene events + polling; `game → game` handled |
| Performance values | Taken from research (2 ms per frame, 50 ms per capture, 5 s / 15 s intervals, 5 MB state, < 2 MB alloc target, ≤ 2 % frame change); adjustable only via E3 |
| Rise of Industry 2 contamination | None. All sources are RoI 1 (App 671440) |
| Compatibility | Baseline build/hash only; `unsupported_build` = heartbeat only, zero capture, no override, in §3.2, §8.2, §8.7, §10.1, §11.2, §11.5, §13.3, §13.4, §16, §20, T-1, A20, C5 |
| Tool count | 29 tools in §14 (2+3+4+4+1+6+6+3); §13.7 maps each one to exactly one refresh scope (2 `none`, 26 `state`, 1 `history`); DoD item 2 and T-7 check the list |
| Refresh | Per-scope nonces (§11.6), server coalescing (§13.2a), tool-to-scope mapping (§13.7); timeout semantics unchanged (3 s default, 10 s max, `refresh_timeout`) |
| Route cost terminology | Existing routes: game `distance_tiles` / `dispatch_cost` only. Non-existing routes: `straight_line_cost_estimate` (`authoritative: false`, D-ROUTE-2) in `find_shops` only |
| Read-only evidence | Structural (IL gate) + automated tests + empirical (V8); V8 alone is not a proof (§2.1) |
| Backup | Copy-only, stability-checked, aborts when it cannot verify (§18) |
