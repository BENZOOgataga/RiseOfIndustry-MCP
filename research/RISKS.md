# Risks and mitigations

Scope: the architecture recommended in `ARCHITECTURE.md` (in-process read-only observer mod, file exchange, out-of-process MCP server).
Each risk has an ID referenced by `PRD-NOTES.md`. "Likelihood/Impact" are qualitative (L/M/H).

---

## 1. Game crash and stability risks

| ID | Risk | L / I | Evidence | Mitigation |
|---|---|---|---|---|
| R-CRASH-1 | Exception during mod load (`GetTypes`, `OnModWasLoaded`, `OnAllModsLoaded`) disables the mod permanently in PlayerPrefs and halts boot on a quit-only popup | M / H | `ModLoader.cs:463-472`, `GameBootstrapper.cs:51-93` | Load hooks do nothing but trivial setup inside try/catch. Single merged DLL referencing only `Managed\` assemblies. No abstract `Mod` subclasses. Test E1 |
| R-CRASH-2 | A `Mods\` subfolder without `desc.json` silently stops all mods. A duplicate mod `name` throws during boot (stuck boot) | M / H | `ModLoader.cs:301-345` | The installer validates the folder layout. Use a unique `name` (`RoiMcpObserver`). Document "never copy the mod twice (local + Workshop)" |
| R-CRASH-3 | Observer subscribes to `EventDispatcher` / game events and throws inside game dispatch (stuck loading screen) | M / H | `EventDispatcher.cs` (no try/catch), `WorldLoadingScreen.cs:53-77` | **No game event subscriptions.** Poll state. Use only Unity `SceneManager` events, with handlers wrapped in try/catch |
| R-CRASH-4 | Accessing destroyed objects / statics after leaving the game scene (`Player.humanPlayer`, `World.Size` stay set) | M / M | `HumanPlayer.cs:13`, `World.cs:20-24` | The lifecycle state machine gates every capture on READY. Drop references on `sceneUnloaded`. Use Unity null checks everywhere |
| R-CRASH-5 | Observer types picked up by the save system (`[Savegame*]` attributes or unresolvable types breaking `SaveSystemReflection` scan of all assemblies) → save/load failures | L / H | `SavegameManager.cs:193-237`, `SaveSystemReflection.cs:242-262` | No savegame attributes on observer types. All types resolvable (single DLL, references only `Managed\`). Test E4 (save + load with observer) |
| R-CRASH-6 | Reading vehicle mover internals while ThreadPool workers mutate them → torn reads / exceptions | M / M | `VehicleMovementManager.cs:42-119` | Read only `transform.position` and job fields on the main thread, or skip vehicle detail. Never call `WaitParallelUpdate` (it blocks the main thread) |
| R-CRASH-7 | Unbounded growth of observer memory, threads or files | L / H | — | Exactly one capture in flight and one background writer thread. Fixed file set. Log capped at 2 × 1 MB. Reuse buffers |

## 2. Performance risks

| ID | Risk | L / I | Evidence | Mitigation |
|---|---|---|---|---|
| R-PERF-1 | **GC stalls.** Unity 2018.4 Mono uses non-incremental Boehm GC. The running game has ~8.2 GB private bytes. Observer allocations hasten full collections that scan the whole heap, causing visible hitches | M / H | `mono-2.0-bdwgc.dll` loaded; process metrics in `notes/install-runtime.md` | Allocation budget per capture (target < 2 MB managed for `state.json` on the sample save; measure in E3). Reuse DTO pools and string builders. No LINQ in capture paths. Avoid storage enumeration (allocates per item), `GetAssets<T>()` copies and `Formula.Evaluate` loops. Adaptive interval (§5.2) |
| R-PERF-2 | Long main-thread work per frame | M / H | All reads must be main-thread | Time-sliced capture, 2 ms per frame budget, measured with `Stopwatch`. Hard ceiling triggers back-off. Heavy history sections only monthly or on request |
| R-PERF-3 | Expensive "reads": `GlobalMarket.GetProductDemand/GetStoredAmount/GetSoldAmount` (cities × shops × time trees), `Headquarters.totalAssets` (all buildings), `CompanyStats` (regions × buildings), shop history range queries | M / M | `notes/cities-shops-markets-world.md` §8, `notes/company-finance-time.md` §7.2 | Compute aggregates from per-shop values already captured. Replicate formulas from fields. Fetch history only monthly |
| R-PERF-4 | Serialization cost on the main thread | L / M | — | Serialize on a background thread from DTOs only. Never hold game objects in DTOs |
| R-PERF-5 | Snapshot too frequent at high game speed (a day every 0.8 s at 10×) | M / M | `secondsPerDay` = 8, speed levels [1, 3, 6, 10] | Real-time minimum interval (default 5 s), independent of game speed |
| R-PERF-6 | Disk churn from frequent multi-MB writes | L / L | — | Only write state when the capture changed (compare hash). Typical rate ≤ 1 write per 5 s |

## 3. Unity threading risks

| ID | Risk | Mitigation |
|---|---|---|
| R-THR-1 | Any game read off the main thread: `ManagerBehaviour<T>.instance` may call `FindObjectOfType` (throws off-thread) or return stale objects; game `List`/`Dictionary` are unsynchronized; `ListPool`/`Formula` pools are static and not thread-safe | The background thread only touches DTOs and files. Code review plus a test that the publisher assembly area has no references to game types |
| R-THR-2 | Live collections mutated between frames (`buildingsList`, `VehicleManager.vehicles`, `Shop.sold` re-sorted, `research.queue`, `auctionQueue`) | Copy into observer-owned buffers within the same frame. Re-validate references on resume |
| R-THR-3 | Monthly `GlobalMarket.UpdatePricesAsync` spreads updates across frames (prices half-updated for a few frames) | Record capture frame. Optionally re-capture market if the date changed during capture. Flag `consistent` |

## 4. Reflection risks

| ID | Risk | Mitigation |
|---|---|---|
| R-REF-1 | Private fields renamed or removed by an update (low probability: the game has been frozen since 2022) | Resolve all fields once at READY into a table. A missing field disables only its section and is reported in the heartbeat. Version gate on 2.3.3 / 0507b / 2304 |
| R-REF-2 | Accidental write through reflection | The helper exposes only `GetValue`/`TryGetValue`. The IL allowlist bans `SetValue`/`Invoke` on game types |
| R-REF-3 | Reflection cost | Cache `FieldInfo` and use compiled getter delegates where hot (or plain `GetValue`; measure) |

## 5. Read-only violations

| ID | Risk | Mitigation |
|---|---|---|
| R-RO-1 | **In-process code can technically call any game method.** The read-only property is a code property, not a runtime sandbox | The structural controls in ARCHITECTURE §7: no command channel, single access layer, IL allowlist test in CI, no Harmony, no event subscriptions, code review checklist |
| R-RO-2 | **Disguised mutators.** `Utils.GetSafe` inserts into dictionaries, and is used by `ProductSpecificProductStorage.GetMaxAccepted` (hence `ManualDestinationSlot.maxAcceptedAtDestination`), `CanReserve`, `PermitManager.*`, `Shop.GetDeliveredByActorCount`, per-actor sales getters and `GameDataManifest.GetAssetsRO` (unknown types). `MoneyManager.GetBalance/GetRawBalance` register agents. `GuidMapper.GetGUIDForObject` mints GUIDs. `ManagerBehaviour` lookups cache instances. RNG-consuming methods include `GlobalMarket` and `Shop` price updates and `GameDate.RandomInRange` | IL denylist (ARCHITECTURE §7.3). Read the dictionaries with `TryGetValue` via the reflection table. Unit tests on the allowlist. These mutations are benign for gameplay, but some write into saved dictionaries, so they are still forbidden |
| R-RO-3 | Observer changes UI state (for example by touching view models such as `DestinationsPanelViewModel.autoWhDispatches`, which mutates lists) | View-model types are on the denylist (namespace `UI.*` and `*ViewModel`). The observer needs none of them |
| R-RO-4 | Observer presence changes save metadata (mod list in the save header; "missing mod" dialog if the save is later loaded without it) | Documented and accepted (cosmetic). `disableWithMods` = 0, so achievements are unaffected (CONFIRMED). Inform the user at install time |
| R-RO-5 | Observer marks the game as cheated | Never use DevConsole (any command except `iworkedhard` sets cheat flags). The IL denylist includes `DevConsole.*`. Heartbeat exports `usedCheats`, so a regression is visible |

## 6. Save corruption risks

| ID | Risk | Mitigation |
|---|---|---|
| R-SAVE-1 | The observer writes into `%APPDATA%\RiseOfIndustry` | The exchange dir is `%LOCALAPPDATA%\RoiMcp`. The observer has no code path to the save directory. The IL denylist includes `SavegameStorage` and `SavegameManager` |
| R-SAVE-2 | The save fallback reads a save while the game writes it (non-atomic `File.WriteAllBytes` on a ThreadPool thread) | Copy, wait for size and mtime to be stable (two checks ≥ 1 s apart), parse the copy, verify the LZ4 framing ends cleanly, discard on error |
| R-SAVE-3 | Disguised mutators insert entries into saved dictionaries (`_deliveredByActors`, `_maxAcceptedMap`, money balances), slightly changing what the next save contains | Covered by R-RO-2. With the denylist in place there are no inserts |
| R-SAVE-4 | Reconnaissance itself: original saves touched | Only copies were parsed. SHA-256 of originals and copies recorded and re-verified (`research/_local/saves/HASHES.txt`) |

## 7. Version coupling

| ID | Risk | Mitigation |
|---|---|---|
| R-VER-1 | Game update changes types or members | Very unlikely (last update 2022-09). Version gate, reflection self-check, per-section degradation. The IL test runs against a pinned copy of the game assemblies |
| R-VER-2 | Content mods or the "2130" module add products, buildings or types | Export static data at runtime from `GameData` (it reflects the active module and mods). Re-export on module change. Treat ids as opaque strings |
| R-VER-3 | Snapshot schema drift between observer and server | Explicit `schema_version` in every file. The server refuses unknown major versions with a clear error. Shared JSON Schemas with contract tests on both sides |
| R-VER-4 | Save format drift (fallback reader) | Header `saveFormatVersion` gate (2304). Opaque blob decoders are versioned. Unknown types are skipped by length prefix |

## 8. Stale snapshot risks

| ID | Risk | Mitigation |
|---|---|---|
| R-STALE-1 | AI answers from old data without knowing | A mandatory `meta` block with `age_s`, `game_date`, `stale` and `source` on every tool response. `game_status` tool. Tool descriptions instruct the model to check `meta` |
| R-STALE-2 | User changed a setting while paused; snapshot predates it | Paused-interval refresh (15 s) plus `fresh: true` refresh requests |
| R-STALE-3 | Game closed or crashed; files remain | The heartbeat carries the PID. The server checks that the process exists and that the heartbeat age is under 5 s. Otherwise it reports `game_not_running` and offers save-fallback data explicitly labelled as such |
| R-STALE-4 | Inconsistent snapshot (day ticked mid-capture) | Per-section dates plus a `consistent` flag. The server can request a recapture |

## 9. Entity ID stability

| ID | Risk | Mitigation |
|---|---|---|
| R-ID-1 | Display names are localized (French), user-editable and duplicated ("FACTORY 5" exists per owner and prefab) | Never key by name. Use building key = `prefab@x,y`. Names are search aliases only, with disambiguation |
| R-ID-2 | `GetInstanceID`/`AssetId` are session-only; `Vehicle.id` is reassigned per trip; slots have no ids and their indices shift | Use `id_strategies` from `data-map.json`. Vehicles are reported in aggregate or with session ids clearly marked |
| R-ID-3 | Tile collision for warehouse module buildings (U6) | The capture asserts uniqueness. On collision it appends the GUID or a module index and logs it. Test E7 |
| R-ID-4 | Demolish and rebuild on the same tile reuses the key | Acceptable. Include `paidToBuild` and prefab in the key payload so the server can detect replacement |

## 10. IPC failure

| ID | Risk | Mitigation |
|---|---|---|
| R-IPC-1 | Partial or corrupt file read | Atomic `File.Replace` (temp in the same directory). The reader validates JSON and schema and keeps the last good snapshot |
| R-IPC-2 | Exchange dir not writable or disk full | The observer catches the error, keeps running, and reports it in the heartbeat if the heartbeat itself can still be written. Otherwise the server sees a stale heartbeat |
| R-IPC-3 | Antivirus or indexer locks a file during replace | Retry with back-off (bounded). Never block the main thread (the publisher is a background thread) |
| R-IPC-4 | Refresh-request flood | The observer coalesces nonces and enforces `min_gap_s`. The server debounces |

## 11. MCP failure

| ID | Risk | Mitigation |
|---|---|---|
| R-MCP-1 | Server crash or hang | Out-of-process. The game is unaffected. The client restarts the server, which reloads from files |
| R-MCP-2 | Oversized responses overwhelm the LLM context | Pagination, compact list rows, detail-on-demand tools, hard caps per response |
| R-MCP-3 | Wrong derived numbers mislead the AI | Derivations unit-tested against hand-computed fixtures from real snapshots. Raw inputs included alongside derived values. Assumptions labelled (e.g. `distance_kind: "straight_line_estimate"`) |
| R-MCP-4 | Ambiguous names resolved to the wrong entity | `search` returns candidates. Detail tools error with a candidate list instead of guessing |

## 12. Security considerations

| ID | Risk | Mitigation |
|---|---|---|
| R-SEC-1 | Network exposure | No sockets in the game, stdio MCP transport, no listening ports at all |
| R-SEC-2 | Untrusted input into the game process | The only inbound data is an integer nonce and numeric config values, parsed defensively. No strings are interpreted as code, member names or paths |
| R-SEC-3 | Exchange-dir tampering by other local processes (fake snapshots) | Same trust level as the user account. Documented. The server validates the schema and the observer PID against the running process |
| R-SEC-4 | Data leakage | Snapshots contain only game data. The company name comes from PlayerPrefs. No Steam IDs or paths beyond the install path. Avoid `Debug.Log` (the bug reporter can upload log lines) |
| R-SEC-5 | Supply chain: third-party code in the game process | The observer depends only on assemblies already shipped with the game (Newtonsoft 11, Unity). Nothing extra is loaded in-process |
| R-SEC-6 | Prompt injection via game strings (user-named buildings or companies) | The server returns names as data fields, never as instructions. Tool descriptions remind the client that names are user content |
