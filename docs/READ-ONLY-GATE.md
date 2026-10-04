# Read-only gate

The observer runs **inside** the Rise of Industry process through the game's official mod loader. In-process
code can technically call any game method, so "read-only" is a property of this implementation, not a
sandbox. It rests on three kinds of evidence (PRD §2.1):

1. **Structural enforcement (this gate).** Every member the observer references is checked at build time
   against a default-deny allowlist, a denylist and hard rules; allowlisted game methods are scanned
   transitively for writes.
2. **Automated tests** of the gate (negative fixtures) and of the observer logic.
3. **Empirical verification (gate V8)**: saves taken before and after a full tool sweep are compared against a
   control pair (see [VALIDATION-REPORT.md](VALIDATION-REPORT.md)). V8 covers what was exercised and what a
   save diff can observe; it is not a proof.

Architecturally, the observer also has **no command channel**: the only data flowing towards the game is an
integer refresh nonce (`refresh-request.json`), and nothing read from a file is ever used as a member name,
path or filter. The observer subscribes to no game event; it polls. The only subscriptions are Unity's three
`SceneManager` scene events.

## Where it runs

- `observer/readonly-gate/RoiMcp.ReadOnlyGate/` — .NET 8 tool using Mono.Cecil.
- `observer/readonly-gate/RoiMcp.ReadOnlyGate.targets` — imported by `RoiMcp.Observer.csproj`; the gate runs
  after every observer build and fails the build on any violation. There is no switch to skip it.
- `observer/readonly-gate/RoiMcp.ReadOnlyGate.Tests/` — xUnit tests. Fixture tests compile small observer-like
  assemblies with Roslyn against the game's own `Managed\` assemblies, each with exactly one violation, and
  assert the expected rule id; a positive fixture must pass.

It loads the built `RoiMcpObserver.dll` and, read-only, the baseline game assemblies from
`<ROI_INSTALL>\Rise of Industry_Data\Managed\`. It refuses to run if `Assembly-CSharp.dll` does not have the
baseline SHA-256 (rule `G0-HASH`, no override).

```powershell
dotnet build observer/RoiMcpObserver.sln -c Release      # builds the gate, the observer, runs the gate
dotnet test  observer/readonly-gate/RoiMcp.ReadOnlyGate.Tests -c Release
dotnet observer/readonly-gate/RoiMcp.ReadOnlyGate/bin/Release/net8.0/RoiMcp.ReadOnlyGate.dll `
  --assembly observer/src/RoiMcp.Observer/bin/Release/net461/RoiMcpObserver.dll `
  --allowlist observer/readonly-gate/allowlist.json --denylist observer/readonly-gate/denylist.json `
  --report gate-report.json [--suggest]
```

## Rules

| Rule | Checks |
|---|---|
| `G-ALLOW` | Default deny: every member reference into `Assembly-CSharp`/`firstpass` must match a `game` allowlist entry exactly (declaring type, name, parameter types, kind `getter`/`method`/`field_read`); every reference into `UnityEngine*` must match a `unity` entry. Stale entries fail too |
| `G-BCL` | BCL references must resolve in the game's own `mscorlib`/`System`/`System.Core` (Unity's Mono profile) |
| `G-ASM` | Assembly references limited to mscorlib, System, System.Core, UnityEngine, UnityEngine.CoreModule, Assembly-CSharp, Assembly-CSharp-firstpass, Newtonsoft.Json |
| `G1` | No `stfld`/`stsfld`/`ldflda`/`ldsflda` on game or Unity fields |
| `G2` | No setters, event `add_`/`remove_`, or mutator-named methods (`Set*`, `Add*`, `Remove*`, `Clear*`, `Toggle*`, `Unlock*`, `Purchase*`, `Sell*`, `Pay*`, `Register*`, `Enqueue*`, …) on game or Unity types, except the three `SceneManager` event subscriptions |
| `G3` | No denylisted member (disguised mutators of PRD §9.3 and the `avoid` members of `research/data-map.json`, plus pools, consoles, save system, …); an allowlist entry that is denylisted also fails |
| `G4` | Reflection: no `SetValue`, `Invoke`, `InvokeMember`, `Activator`, `CreateDelegate`, `Emit`, expression compilation or assembly loading. `Type.GetField`/`FieldInfo.GetValue` only inside `ReadLayer.ReflectionTable`, with the declaring type from `ldtoken` and the name from a string literal that matches an allowlisted private `field_read` entry |
| `G5` | No Unity mutators (`Instantiate`, `Destroy`, `AddComponent`, `SetActive`, `SendMessage`, `Time.set_*`, `PlayerPrefs`, scene loading, `Application.Quit`, `Input`) |
| `G6` | No Harmony, DevConsole, PAConsole, save-system types, `UI*` namespaces or `*ViewModel` types |
| `G7` | Exactly one sealed, non-abstract `ProjectAutomata.Mod` subclass; no other type derives from a game/Unity type or implements a game interface; no game attributes; no `DllImport`, no unsafe code |
| `G8` | No process, network, pipe or memory-mapped-file APIs (except `Process.GetCurrentProcess().Id`), no `Thread.Abort/Suspend/Resume` |
| `G9` | Layering: only `RoiMcp.Observer.ReadLayer` may reference game/Unity members (plus the mod class's lifecycle and scene handlers); only `RoiMcp.Observer.Io` may use file APIs; `RoiMcp.Observer.Publish` references no game/Unity member at all |
| `G10` | The release build must not contain the `RoiMcp.Observer.DebugFaults` marker type (fault injection for gate E5 exists only in `DEBUG_FAULTS` builds, which are built to a separate output folder) |
| `G-TRANS` / `G-TRANS-COLL` | Transitive scan, below |

## Transitive scan and acknowledgements

For every allowlisted game method or getter the gate walks callee bodies inside `Assembly-CSharp`/`firstpass`
to depth 4 (following virtual dispatch to overrides, narrowed by generic arguments) and reports:

- `stfld` on objects other than the root's own instance, every `stsfld`;
- calls to denylisted members;
- `UnityEngine.Random`, `World.deterministicRandom`;
- writes into `System.Collections(.Generic)` collections (`collection-write`) — and, separately, writes into
  a collection the observer itself passed in (`caller-collection-write`).

Every finding must be acknowledged in the entry's `transitive_findings_ack` with a justification, or the gate
fails (`G-TRANS`). A `collection-write` cannot be acknowledged (`G-TRANS-COLL`) — **except** within one of
three narrowly defined `collection_write_categories` in `allowlist.json`. A categorized acknowledgement only
counts when the category's patterns match the method that performs the write (or a method on its call path):

| Category | What it covers | Observer guarantee |
|---|---|---|
| `scratch_pool` | `ObjectPool`/`ListPool` free lists, pooled lists returned by `TimeTree.GetInRange`, and pooled formula-argument dictionaries filled by `*FormulaArguments.GetFormulaArguments` | Main thread only; the game method returns pooled objects before it returns; the observer passes its own buffers where possible |
| `guarded_actor_component_cache` | `Actor.Get(Type)` inserting into the actor's private `_componentMap` when a type is missing | Before every call that reaches `Actor.Get` the observer checks `_componentMap.ContainsKey(type)` (`ReadLayer.Guard`) and skips the value otherwise. `Actor.Awake` pre-fills the map with every component type and interface, so the insert path is never taken |
| `unreachable_lazy_init` | One-time singleton/registry initialization (`InitializeSingletonInstance`, `GameData` runtime clone and manifest registration) | Game managers and `GameData` are only read in the `ready` state, after every scene manager's `Awake` resolved its singleton and after boot created `GameData.instance` |

**Why the categories exist (deliberate interpretation of PRD §10.4).** PRD §10.4 says an acknowledgement
must not cover a write into a game dictionary or list. Read literally, that also forbids the transient pool
bookkeeping performed by every formula evaluation and every `ListPool`-backed range query, and the lazy
initialization inside `ManagerBehaviour<T>.instance`. Those are required by the PRD itself (route
`dispatch_cost` and research cost/time are game formula evaluations, §12.3 and §14.8; history uses range
queries where "ListPool rules apply", §12.4; every manager is reached through `.instance`). Without the
categories no observer satisfying the PRD could pass the gate. The categories keep the rule's purpose — no
write into game *state* collections — intact: they cannot match state collections such as `_maxAcceptedMap`,
`_deliveredByActors`, money balances or permit dictionaries, and each one names the runtime guarantee the
observer relies on. They are checked by gate tests (a category with non-matching patterns, or an undefined
category, still fails `G-TRANS-COLL`).

Unity calls reachable from game methods are not walked (assembly boundary) but are listed per root in the
report (`unity_boundary_calls`). The reviewed list for the shipped allowlist contains only reads, logging on
error paths that the observer avoids (e.g. it only asks `GlobalMarket` for products in the market's own key
set, never builds invalid `GameDate`s, and checks `IsValidHandle` before reading transport request handles),
and object creation inside the unreachable lazy-initialization paths above.

## Allowlist entries

```json
{"member": "ProjectAutomata.ManualDestinationSlot::get_minStoredAtSource()", "kind": "getter",
 "data_map_id": "logistics.min_keep", "evidence": "ProjectAutomata/ManualDestinationSlot.cs:175",
 "review": "Decompiled body: … Transitive scan (depth 4): no writes, denylisted calls or RNG.",
 "transitive_findings_ack": []}
```

Canonical member strings: `Declaring.Type::Name(ParamType,…)` for methods and getters (getters name the
accessor, `get_X`), `Declaring.Type::field` for fields; nested types use `/`; generic types are written as
referenced (``ProjectAutomata.ManagerBehaviour`1<ProjectAutomata.World>::get_instance()``). Private fields read
through `ReflectionTable` are `field_read` entries with `"private": true`. `evidence` points into the
decompiled game source used for the review (not part of this repository). Entries without evidence or
review, or with a leftover `TODO`, are rejected.

### Reviewing a new member

1. Read the member's decompiled body. Reject it if it writes game state, consumes RNG, pathfinds, touches UI
   or the save system, or appears in `denylist.json`.
2. Add the code to the read layer and build; the gate fails with `G-ALLOW`.
3. Run the gate with `--suggest --report report.json`. The report contains a skeleton entry with the
   transitive findings pre-filled.
4. Fill `data_map_id`, `evidence`, `review`, and a justification for each finding. Use a
   `collection_write_categories` category only when the write really is pool scratch, a guarded cache or an
   unreachable initialization path — and add the runtime guard to the observer if the category requires one.
5. Rebuild; the gate must pass. Mention the new entry in the pull request.

## Known limits of the gate

- It inspects the observer's own IL and game code reachable from allowlisted members to depth 4; deeper
  paths, Unity internals and `ToString`/`Equals`/`GetHashCode` dispatch into game overrides are not walked.
- If observer code itself wrote into a game-owned collection instance it received (e.g. `someGameList.Clear()`),
  only code review would catch it; the observer copies game collections into its own lists and never mutates
  them.
- It cannot prove runtime guards correct; they are small, reviewed and tested.
