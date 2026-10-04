# PRD notes: requirements and acceptance criteria supported by reconnaissance

This is **not** the PRD. It records concrete, evidence-backed requirements for the implementation agent.
References: `DATA-MAP.md` / `data-map.json` entry ids (e.g. `logistics.min_keep`), risk ids from `RISKS.md` (e.g. R-PERF-1), experiment ids from `ARCHITECTURE.md` §11 (E1–E7).

---

## 1. Scope and non-goals

- **In scope**
  - A read-only view of the running original Rise of Industry (App 671440, build 9064059, version 2.3.3 : 0507b, savegame 2304) for MCP clients.
  - An optional offline view from save files.
- **Non-goals**
  - Any gameplay action: building, demolition, settings, research, trading, speed, saving, loading, UI interaction or console commands.
  - Economic decision-making inside the MCP.
  - Rise of Industry 2.
  - Multiplayer (the game has none).

---

## 2. Data known to be accessible (live, via observer)

All entries below are CONFIRMED unless marked. Ids refer to `data-map.json`.

| Area | Accessible data | Ids |
|---|---|---|
| Session | version, scene state, date, speed and pause, difficulty, module, mods, language | `game.*` (not the save name: U7) |
| Company | player and AI identity, cash (player; AI = infinite), loans and payments, monthly ledger by category (~3 years), cashflow label, shares, value (replicated formula), assets (derived), AI goals and regions | `company.*` |
| Buildings | all buildings with stable keys, type, name, position, region and city, owner, flags, efficiency level, upkeep, cost, pollution, logistics config, monthly analysis series | `building.*` |
| Production | recipe, I/O, cycle time, progress, counters, history (~1–2 years daily), inventory per product, capacity, inbound cap (Max Send), player per-product cost/profit stats | `production.*` |
| Gatherers/farms | hub, modules (≤ 3), resource, deposits, rate (replicated formula, HIGH), water as ingredient | `gatherer.*` |
| Logistics | destination slots: product, destination, Max Send (shared destination cap, or auto = shop demand), Min Keep (per slot), paused, fill vehicles, transport mode, distance, dispatch cost, in-flight requests, errors; auto-warehouse mode; warehouse pull requests; vehicles; fleets; dispatch cost history | `logistics.*` |
| Graph | configured edges, warehouse/observed edges (HIGH), static recipe graph | `graph.*` |
| Cities/regions | settlements, tier, population, growth state, advancement, contract offer, shops; regions, resources, permits/ownership, permit cost | `city.*`, `region.*` |
| Shops | accepted products, stock, delivered-by-actor stock, demand, interval, price, multiplier, sales/demand history | `shop.*` |
| Static | products, categories, recipes, compatible buildings, building types, base prices (runtime), formulas, tiers, loans, bill categories, English names (HIGH) | `static.*` |
| Technology | full tree, unlock effects, costs/time, unlocked, available (derived), research queue and progress, competitor tech | `tech.*` |
| Markets | global prices, modifiers, trends, aggregates, State offers and purchase rules, State sales (2 months), contracts, auctions, wholesalers (HIGH) | `market.*` |
| World | tile grid, terrain, resources, roads/rail, networks, pollution, urban areas, building at tile | `world.*` |

## 3. Explicitly unsupported or unavailable

The MCP must say so explicitly instead of inventing values.

- **Global market price history:** the game stores none. The MCP must sample it (HistoryStore).
- **Daily money ledger:** monthly aggregates only.
- **Per-route history:** none.
- **Stable vehicle identity:** none.
- **Wages/salaries:** none exist. The equivalent is the efficiency slider. **Fertility:** none exists.
- **Import/export/harbor trade:** not found (INFERRED absent). The "World Market" UI concept maps to `market.global_prices` + `market.state_sells` (INFERRED).
- **AI cash:** infinite by prefab flag, so it is not a meaningful number.
- **Currently loaded save name:** UNKNOWN until E-series investigation (U7).
- **UI label text for Max Send / Min Keep:** HIGH-confidence mapping, to be confirmed by E2.

---

## 4. Observer requirements (in-game mod)

| ID | Requirement | Acceptance criteria |
|---|---|---|
| OBS-1 | Loaded only via the official loader as `Mods\RoiMcpObserver\{desc.json, code\RoiMcpObserver.dll}`; one merged DLL; .NET Framework 4.x; references only `Rise of Industry_Data\Managed\*`; no abstract `Mod` subclasses | E1 passes: no error popup, heartbeat appears in the main menu |
| OBS-2 | `OnModWasLoaded`/`OnAllModsLoaded` cannot throw (try/catch); no game API use there beyond creating the exchange dir and log | Fault-injection build throwing inside them still boots (the observer disables itself) |
| OBS-3 | All game reads on the Unity main thread, inside `LateUpdate` slices of ≤ `frame_budget_ms` (default 2) | Profiling (E3) shows no slice above 2 ms (p99 ≤ 3 ms) |
| OBS-4 | Lifecycle states MENU / LOADING / READY / PAUSED / DISABLED / UNVERIFIED_BUILD as in ARCHITECTURE §4, gating every capture | E4 transition script produces the expected heartbeat sequence; no exceptions in observer log |
| OBS-5 | No subscriptions to game events or delegates (`TimeManager`, `EventDispatcher`, `BuildingManager`, …); only Unity `SceneManager` events | IL test finds no `add_*` on game types |
| OBS-6 | Read-only access layer with reflection table resolved at READY; missing members disable dependent sections and are reported | Removing a field name from the table (test build) disables one section only; heartbeat lists it |
| OBS-7 | Never calls the disguised mutators listed in `data-map.json` `avoid` fields or RISKS R-RO-2 | IL allowlist/denylist test (TEST-3) passes |
| OBS-8 | Stable ids per `id_strategies`; building key `prefab@x,y`, plus GUID when present (read via `GuidMapper.objMappings` TryGetValue) | E7: 1:1 match with the save parser; no key collisions |
| OBS-9 | Writes only to `%LOCALAPPDATA%\RoiMcp\`; atomic replace; fixed file set; log capped 2 × 1 MB | File-system monitor during E3/E4 shows no other writes |
| OBS-10 | Adaptive capture schedule (ARCHITECTURE §5.2) with refresh nonce support and back-off | E6: refresh served ≤ 3 s; at 10× speed, captures ≤ 1 per 5 s by default |
| OBS-11 | Snapshot metadata: `schema_version`, `observer_version`, `game_version`, `savegame_version`, `seq`, `captured_utc`, per-section `game_day` + `frame`, `consistent` | Schema validation in TEST-2 |
| OBS-12 | Kill switch (`observer.disabled` file or config) honoured within 1 s, without touching the game | Manual test |
| OBS-13 | Version gate: unexpected build → `UNVERIFIED_BUILD` flag; reflection self-check still runs | Unit test with a faked version |
| OBS-14 | Report the player as `Player.humanPlayer`; flag when `Player.activeActor` differs from `humanPlayer` | Unit test on DTO builder |
| OBS-15 | Max Send exported as `{value, unlimited, auto, source, shared_scope}` (`unlimited` true when value is 0; `auto` bool; `source` "destination_storage" or "shop_demand"; `shared_scope` "destination+product"); Min Keep as `{value, keep_all}` (`keep_all` true when value is `int.MaxValue`) | E2 |

## 5. Error states (MCP-visible)

Every tool returns either data with `meta` or a structured error `{code, message, hint, meta}`:

| Code | Condition |
|---|---|
| `game_not_running` | No `Rise of Industry.exe` process, or heartbeat PID dead |
| `observer_not_installed` | Game running but no heartbeat ever seen (or older than the process start) |
| `observer_disabled` | Heartbeat `state: "disabled"` |
| `at_main_menu` | Heartbeat `state: "menu"` |
| `loading` | Heartbeat `state: "loading"` |
| `snapshot_unavailable` | READY but no valid `state.json` yet |
| `stale_snapshot` | Data returned but older than the threshold. This is a warning flag in `meta`, not an error |
| `section_disabled` | The requested data depends on a disabled section |
| `unverified_build` | Warning flag: game build differs from 2.3.3 / 0507b / 2304 |
| `schema_mismatch` | Snapshot major version unknown to the server |
| `not_found` / `ambiguous` | Entity resolution failed. `ambiguous` returns candidates |
| `save_fallback` | Warning flag: data comes from a save file (with save name and timestamp) |

Required tolerance (all must hold):

- The MCP starts before the game.
- The game is at the main menu.
- A save is loading.
- A save is unloaded (return to menu, quickload game → game).
- The game closes or crashes while the MCP keeps running.
- The MCP restarts while the game keeps running.

In each case the MCP returns the appropriate code above, never a crash and never stale data presented as live.

## 6. IPC and snapshot requirements

| ID | Requirement |
|---|---|
| IPC-1 | Exchange dir `%LOCALAPPDATA%\RoiMcp\` with `heartbeat.json`, `static.json`, `state.json`, `history.json`, `refresh-request.json`, `observer.log`, optional `observer.config.json` / `observer.disabled` |
| IPC-2 | JSON Schemas for each file are versioned in the repo and shared by both components |
| IPC-3 | Writes are temp file + atomic replace in the same directory. Readers keep the last good copy on validation failure |
| IPC-4 | Inbound to the observer: only `{"nonce": int, "scope": "state"|"history"|"static"}` and numeric/bool config. Anything else is ignored |
| IPC-5 | Heartbeat every 1 s real time in every state (including menu and paused) |
| IPC-6 | `static.json` exported on READY and on module change; `history.json` at month start (polled date on day 1 after a month change) and on request |

## 7. MCP server requirements

| ID | Requirement | Acceptance criteria |
|---|---|---|
| MCP-1 | Separate process, stdio transport, Python, no listening sockets | `netstat` shows no listener from the server |
| MCP-2 | Read-only tool surface as in ARCHITECTURE §8. No tool can cause a game write | Tool list review. The only file the server writes in the exchange dir is `refresh-request.json` |
| MCP-3 | Every response includes `meta` (source, observer_state, seq, game_date, captured_utc, age_s, consistent, paused, stale, warnings[]) | Contract tests |
| MCP-4 | Name resolution over French display names, English names (from static export), internal ids and building keys, with `ambiguous` errors | Tests with duplicated "FACTORY 5" across owners and prefabs |
| MCP-5 | Deterministic derivations (rates per 30 days, capacities, graph traversal, ratios, straight-line distances, Max Send interpretation) unit-tested against fixtures | TEST-4 |
| MCP-6 | Pagination and response size caps (e.g. ≤ 50 rows per page, ≤ ~30 KB per response by default) | Tests on the largest fixture |
| MCP-7 | HistoryStore (phase 2): SQLite, monthly samples of market prices, ledger categories and production totals per save identity (world seed + company name + start date) | Survives restarts; no unbounded growth (retention configurable) |
| MCP-8 | Save fallback (phase 2): newest `.sav` header-scan (~3 ms each), copy then parse, clearly flagged `save_fallback` | Parses the three research saves; identical building keys to live (E7) |

### 7.1 Coverage of the brief's example questions

| Question | Tool(s) | Data ids | Feasible |
|---|---|---|---|
| What is Factory 5 producing? | `search`, `get_building` | `production.recipe`, `production.io` | Yes |
| What buildings supply Factory 5? | `get_building` (incoming) / `get_routes(destination=…)` | `graph.edges_configured` (reverse index), `graph.edges_warehouse` | Yes for manual routes; warehouse pulls = observed edges |
| Where does Factory 5 send its output? | `get_building` (outgoing) | `logistics.*` | Yes |
| Max Send / Min Keep per destination | `get_building`, `get_routes` | `logistics.max_send`, `logistics.max_send_auto`, `logistics.min_keep` | Yes (E2 confirms labels) |
| Is this factory bottlenecked? Which input limits it? | `get_building`, `find_bottlenecks` | `building.blocking`, `production.inventory`, `production.bottleneck` | Yes (evidence-based) |
| Which buildings accumulate inventory? | `list_buildings(sort=stock_ratio)` | `production.inventory`, `production.capacity` | Yes |
| Which chains overproduce? | `get_production_overview`, `get_supply_chain` | rates + stock + shop demand | Yes (derived) |
| Which products are profitable? | `get_product`, `get_production_overview` | `production.player_product_stats`, `market.global_prices`, `shop.price` | Yes (player stats are game-computed) |
| Actual production graph for paint | `get_supply_chain(product="Paint")` | `graph.*`, `static.recipes` (Paints: Chemicals 1 + Dye 2 → Paint 2, 35 days; Chemicals: Gas 3 → 2, 20 days) | Yes |
| Gas consumed by the paint chain | `get_supply_chain(product="Paint", upstream)` | recipe ratios × actual counters | Yes (derived) |
| Which shop should receive a product? | `find_shops(product, from_building)` | `shop.*`, `world.spatial` | Yes (data; choice left to AI) |
| What is demanded in Limoges? | `get_city("Limoges")` | `city.*`, `shop.demand` | Yes |
| Shop prices and demand | `get_shop` / `get_city` | `shop.price`, `shop.demand` | Yes |
| Company revenues and expenses | `get_company`, `get_finances` | `company.ledger` | Yes (monthly) |
| Why did profit fall recently? | `get_finances(months=6, group_by=category)` + `get_production_overview` | ledger + production/shop history | Yes (evidence); explanation by AI |
| Which technologies are unlocked? | `get_tech_tree(state=unlocked)` | `tech.unlocked` | Yes |
| What recipes are available? | `list_recipes(available=true)` | `static.recipes`, `tech.unlocked` | Yes |
| Resources in each region | `list_regions` / `get_region` | `region.resources` | Yes |
| Which regions does the player control? | `list_regions(owner=player)` | `region.permits` | Yes |
| What are competitors doing? | `list_companies`, `get_company(ai)` | `company.ai_state`, `company.cashflow_label`, `company.buildings` | Yes (AI cash infinite) |
| What is available through the State / World Market? | `get_market` | `market.state_sells`, `market.global_prices` | Yes |

## 8. Performance constraints

| ID | Constraint |
|---|---|
| PERF-1 | Main-thread time per frame ≤ 2 ms (configurable), total per capture ≤ 50 ms on the largest known save; average frame time change ≤ 2 % during E3 |
| PERF-2 | Default capture interval ≥ 5 s real time while running, ≥ 15 s while paused; adaptive back-off keeps observer main-thread share ≤ ~1 % |
| PERF-3 | Managed allocation per `state.json` capture measured and budgeted (target < 2 MB on the 2.8 MB sample save). No LINQ or storage enumeration in capture paths |
| PERF-4 | Serialization and I/O off the main thread |
| PERF-5 | Expected sizes (from sample saves): ~150 player buildings (up to ~500 per AI), ~2,000 houses (counted only), 249 destination slots, 91–535 vehicles, 7 cities with ≤ 8 products per shop, 7 regions, 226 products, 225 recipes, 339 tech unlocks, 151 market entries, ~192 monthly bill aggregates. Snapshots must stay < 5 MB at 3× these numbers |
| PERF-6 | Static data cached per session or module. Dynamic data refreshes per §6. History data monthly |

## 9. Testing requirements

| ID | Test |
|---|---|
| TEST-1 | Unit tests for DTO builders using mock data (no game). Lifecycle state machine tests |
| TEST-2 | JSON Schema contract tests: observer output fixtures validate. Server rejects invalid or unknown versions |
| TEST-3 | **IL allowlist/denylist test** (Mono.Cecil) over the observer DLL against pinned game assemblies (see ARCHITECTURE §7.3). Must run in CI |
| TEST-4 | Derivation tests in the server with fixtures built from the research save copies (keys, recipes, slots) and hand-computed expectations (rates, graph for Paint, Max Send semantics) |
| TEST-5 | In-game experiments E1–E7 (manual, with a save backup first), recorded in a test report |
| TEST-6 | Soak test: 2 hours at 10× speed with the observer, comparing frame time and memory with and without the observer |
| TEST-7 | Save-fallback parser regression on the three research saves (byte-exact consumption, same counts as `notes/save-format.md`) |

## 10. Installation constraints

- Code mods load from `<install>\Mods\<name>\`, resolved relative to the process CWD (Steam sets it to the install dir; verify in E1, U9).
- The installer:
  - must never leave a `Mods\` subfolder without `desc.json` (that disables all mods);
  - must never install the same mod name twice (local + Workshop);
  - must not modify any game file or PlayerPrefs.
- Installing into the game directory needs the user's explicit consent in the implementation phase. Uninstalling means deleting the folder.
- Inform the user that saves made while the mod is enabled list it in their header. Loading them later without the mod shows a cosmetic "missing mod" notice. Achievements are not affected (`disableWithMods` = 0).
- The MCP server installs separately (Python package) and is registered in the client config. It needs no admin rights.

## 11. Compatibility constraints

- Target: Steam build 9064059, GameVersion 2.3.3 / 0507b / savegame 2304, Unity 2018.4.11 Mono, .NET 4.x API level, Harmony 1.1 (unused), Newtonsoft 11 available in-process.
- Must work with the base module and the "2130" module content present in the data (definitions are exported at runtime, never hard-coded).
- Must tolerate content mods (e.g. the subscribed Workshop name-list mod). Ids are opaque strings.
- UI language independent: ids never depend on localized text. English names come from `LanguageData` (HIGH; verify in E1).
- Windows only, the user's platform. Paths use `%LOCALAPPDATA%`.

## 12. Decisions left for the PRD

1. Whether phase 1 includes the save fallback, or live-only first. Recommendation: live-only first, then fallback.
2. HistoryStore retention policy and save-identity heuristic.
3. Exact default intervals and budgets after E3 measurements.
4. Whether to export AI buildings in full detail by default, or on request (size/perf trade-off; up to ~500 buildings per AI).
5. Whether to add a named-pipe v2 channel if file freshness proves insufficient. It would still serve pre-built snapshots only.
