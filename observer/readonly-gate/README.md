# RoiMcp read-only IL gate

Build-time gate for `RoiMcpObserver.dll` (PRD §10). It is a .NET 8 console tool built on Mono.Cecil.
It loads the observer and, **read-only**, the baseline game assemblies from
`<game-dir>\Rise of Industry_Data\Managed\`. It fails if the observer references anything it is not
explicitly allowed to reference.

```
readonly-gate/
  RoiMcp.ReadOnlyGate/          gate tool (net8.0, Mono.Cecil)
  RoiMcp.ReadOnlyGate.Tests/    xUnit tests: negative/positive fixtures compiled in-test with Roslyn
  RoiMcp.ReadOnlyGate.targets   MSBuild import that runs the gate after every observer build
  allowlist.json                default-deny allowlist ("unity" section + "game" section)
  denylist.json                 forbidden members/types (PRD §9.3 + data-map "avoid" + extras)
  RoiMcp.ReadOnlyGate.sln
```

## Running

```powershell
dotnet build observer/readonly-gate/RoiMcp.ReadOnlyGate.sln -c Release
dotnet observer/readonly-gate/RoiMcp.ReadOnlyGate/bin/Release/net8.0/RoiMcp.ReadOnlyGate.dll `
  --assembly <path>\RoiMcpObserver.dll `
  --allowlist observer/readonly-gate/allowlist.json `
  --denylist  observer/readonly-gate/denylist.json `
  [--game-dir <ROI install dir>] [--report gate-report.json] [--suggest] [--allow-debug-faults]

# Check that every denylist entry still matches something in the baseline game:
dotnet ...RoiMcp.ReadOnlyGate.dll --check-denylist --denylist observer/readonly-gate/denylist.json
```

- `--game-dir` defaults to `%ROI_GAME_DIR%`, otherwise `C:\Program Files (x86)\Steam\steamapps\common\RiseOfIndustry`.
- Exit codes: `0` pass, `1` violations, `2` usage or configuration error (missing file, invalid JSON, invalid entry).
- Each violation is printed as `RULE-ID  location  message`. The location is `Type::Method IL_xxxx` for
  instructions, `Type::Method` / `Type::field` / `Type` for declarations, and `allowlist game[i]` for allowlist checks.
- `--report` writes JSON (`schema: roi-mcp/readonly-gate-report`): violations, warnings, every transitive root
  with its findings (id, kind, target, container, `via` path, ackable, acknowledged, justification), the Unity
  boundary calls reached, and stats.
- `--suggest` also prints (and puts in the report) skeleton allowlist entries for every unlisted game/Unity
  reference and for every unlisted `GetField` literal, with the transitive findings pre-filled as acks.
  Skeletons contain `TODO` placeholders, which the gate rejects, so they must be reviewed before use.
- `--allow-debug-faults` is **only** for the `DEBUG_FAULTS` validation build (PRD §22 E5). Never use it for a release build.

The game assemblies are read into memory through streams opened with `FileAccess.Read`; nothing in the game
directory is written. The gate refuses to run against a non-baseline game (`G0-HASH`, no override).

### MSBuild integration

Import the targets file in the observer csproj. Building the gate separately first is not required: the
targets file builds the gate project itself through the `MSBuild` task (restore + build, `Release`, without
inheriting the observer's framework/output properties):

```xml
<Import Project="..\..\readonly-gate\RoiMcp.ReadOnlyGate.targets" />
```

The `RoiMcpReadOnlyGate` target runs `AfterTargets="Build"`, executes
`dotnet RoiMcp.ReadOnlyGate.dll --assembly $(TargetPath) ...` and fails the build on a non-zero exit code.
Properties: `RoiGameDir` (default `$(ROI_GAME_DIR)`, then the Steam path), `RoiGateAllowDebugFaults`
(`true` only for DEBUG_FAULTS builds; emits a warning), `RoiGateAllowlist`, `RoiGateDenylist`,
`RoiGateReport` (default `obj\...\readonly-gate-report.json`), `RoiGateSuggest`, `RoiGateConfiguration`.
There is no property that skips the gate.

### Tests

```powershell
dotnet test observer/readonly-gate/RoiMcp.ReadOnlyGate.sln
```

Fixture tests compile small C# snippets with Roslyn against the game's own `Managed\` assemblies
(old-style `mscorlib`, `UnityEngine.CoreModule`, `Assembly-CSharp`, ...) into a temp directory and run the gate
on them. They are skipped with a message when the Managed folder is missing (set `ROI_GAME_DIR`); the unit
tests (glob/parsing/hash/CLI/config validation) always run.

## Canonical member strings

Used by `allowlist.json`, `denylist.json`, findings and reports.

| Kind | Format | Example |
|---|---|---|
| method / ctor | `Declaring.Type::Name(ParamType,ParamType)` | `ProjectAutomata.Mod::.ctor()` |
| getter | `Declaring.Type::get_Prop()` | `ProjectAutomata.World::get_seed()` |
| generic method | `Declaring.Type::Name<ArgType>(...)` | `UnityEngine.Component::GetComponent<ProjectAutomata.Building>()` |
| field | `Declaring.Type::fieldName` | `ProjectAutomata.ManualDestinationSlot::_minStoredAtSource` |

Type names:

- Namespace-qualified Cecil full names; nested types use `/` (`ProjectAutomata.TimeManager/TimeManagerCallback`).
- Generic instances: `` Name`N<Arg1,Arg2> `` with no spaces (`` ProjectAutomata.ManagerBehaviour`1<ProjectAutomata.World> ``).
- Arrays `[]`, by-ref `&` (`out`/`ref` parameters), pointers `*`.
- The **declaring type** is written as referenced in the IL, i.e. closed if the member is accessed through a
  generic instance (`` ProjectAutomata.ManagerBehaviour`1<ProjectAutomata.World>::get_instance() `` and
  `` ...`1<ProjectAutomata.GlobalMarket>::get_instance() `` are different entries).
- **Parameter types** are those of the resolved *definition*, so generic parameters keep their declared names
  (`` ProjectAutomata.Utils::GetSafe(System.Collections.Generic.IDictionary`2<T1,T2>,T1,T2) ``).
- `--suggest` always prints the exact string; copy it rather than writing it by hand.

## allowlist.json

```json
{
  "schema": "roi-mcp/readonly-gate-allowlist",
  "schema_version": 1,
  "unity": [ { "member": "UnityEngine.Time::get_frameCount()", "kind": "getter", "review": "...", "source": "PRD 10.2" } ],
  "game": [
    {
      "member": "ProjectAutomata.World::get_seed()",
      "kind": "getter",                    // getter | method | field_read
      "data_map_id": "game.seed",
      "evidence": "World.cs:123",
      "review": "auto-property: returns the static backing field",
      "transitive_findings_ack": [ { "finding": "<finding id or glob>", "justification": "..." } ]
    },
    { "member": "ProjectAutomata.PermitManager::_permits", "kind": "field_read", "private": true,
      "data_map_id": "region.permits", "evidence": "PermitManager.cs:20", "review": "Dictionary read with TryGetValue only" }
  ]
}
```

(Comments above are illustrative; the file is strict JSON.)

Rules enforced when loading (violations are configuration errors, exit 2): exact member strings (no wildcards),
kind ∈ `getter|method|field_read`, getters must name `get_X`, methods need a parameter list, `private` only on
`field_read`, game entries need non-empty `evidence` and `review`, acks need `finding` and `justification`,
no `TODO` left in game entries or ack justifications, no duplicates.

- `kind`: `getter` = property getter, `method` = any other method including `.ctor` (a `newobj` of a game type
  needs an explicit `.ctor` entry), `field_read` = `ldfld`/`ldsfld` of a public field, or, with `"private": true`,
  a `ReflectionTable` `GetField` lookup.
- Private reflection entries for generic types may use the open name (`` ProjectAutomata.TransportJob`1::_origin ``);
  the closed name written in `typeof(...)` is also accepted.
- `unity`: the initial content of PRD §10.2 plus what it implies (`Vector3.x/y/z` reads for `Transform.position`,
  the `UnityAction` constructors used by the three `SceneManager` subscriptions). `Component.GetComponent<T>`
  is not pre-listed: add one entry per reviewed `T`. Unity entries must resolve in `UnityEngine.CoreModule`.
- `game`: empty. The observer author fills it.

### Reviewing and adding a game entry (PRD §10.4)

1. Build the observer; the gate fails with `G-ALLOW` for the new reference. Run with `--suggest` (or
   `-p:RoiGateSuggest=true`) and copy the skeleton.
2. Read the decompiled body (`research/_local/decompiled/...`), fill `data_map_id`, `evidence` (`File.cs:line`) and
   a `review` summarising the body. Confirm it is not a disguised mutator (PRD §9.3).
3. For each pre-filled `transitive_findings_ack`, confirm the finding is benign and write the justification
   (e.g. "benign lazy singleton cache: `ManagerBehaviourBase<T>._instance`"). If you cannot justify it, do not
   allowlist the member. Findings of kind `collection-write` can never be acknowledged.
4. Re-run the gate. Unused acks and unreferenced entries are reported as warnings; remove them.

## Rules

| Rule | Checks |
|---|---|
| `G0-HASH` | `Assembly-CSharp.dll` SHA-256 = `D62599EF…D04803` (PRD §3.1). No override |
| `G-ASM` | Assembly references limited to `mscorlib, System, System.Core, UnityEngine, UnityEngine.CoreModule, Assembly-CSharp, Assembly-CSharp-firstpass, Newtonsoft.Json` |
| `G-ALLOW` | Default deny: every member reference into `Assembly-CSharp*` must exactly match a `game` entry, into `UnityEngine*` a `unity` entry (same member string and kind). Also: allowlist entries that no longer resolve in the game / `UnityEngine.CoreModule`, and kind mismatches |
| `G-BCL` | Every type/member reference into `mscorlib`/`System`/`System.Core` must resolve in the game's own `Managed\` copies |
| `G1` | `stfld`/`stsfld`/`ldflda`/`ldsflda` on a field declared in a game or Unity assembly |
| `G2` | Calls to game/Unity members named `set_*`, `add_*`, `remove_*`, `Set*`, `Add*`, `Remove*`, `Clear*`, `Toggle*`, `Increment*`, `Decrement*`, `Unlock*`, `Research*`, `Purchase*`, `Sell*`, `Repay*`, `Validate*`, `Cancel*`, `Destroy*`, `Execute*`, `Pay*`, `Register*`, `Deregister*`, `Enqueue*`, `Kill` (literal, case-sensitive prefixes). Only exceptions: `SceneManager.add_sceneLoaded/add_sceneUnloaded/add_activeSceneChanged` |
| `G3` | Any reference matching `denylist.json` (members and types, including generic arguments). A `callvirt` of a game virtual/interface method whose override/implementation is denylisted also fails (e.g. `IProductStorage.GetMaxAccepted`). Self-check: an allowlist entry matching the denylist fails |
| `G4` | `FieldInfo.SetValue/SetValueDirect/GetValueDirect/GetFieldFromHandle`, `PropertyInfo.GetValue/SetValue/GetGetMethod/GetSetMethod/GetAccessors`, `MethodBase/MethodInfo/ConstructorInfo.Invoke/CreateDelegate`, `EventInfo` handler/accessor APIs, `Type.InvokeMember/GetFields/GetMember(s)/GetProperty(ies)/GetMethod(s)/GetConstructor(s)/GetEvent(s)/FindMembers/GetType(string…)`, `TypeInfo.GetDeclared*/Declared*`, `RuntimeReflectionExtensions.*`, `Module.Resolve*/GetField*/GetMethod*`, `Activator.*`, `AppDomain.*`, `Assembly.Load*/CreateInstance`, `Delegate.CreateDelegate/DynamicInvoke`, `FormatterServices.*`, `RuntimeHelpers.RunClassConstructor/…`, `TypedReference.*`, `System.Reflection.Emit.*`, `System.Linq.Expressions.*.Compile`, `ldtoken` of a field/method handle. `Type.GetField` and `FieldInfo.GetValue` only inside `RoiMcp.Observer.ReadLayer.ReflectionTable`, and every `GetField` must be exactly `ldtoken T; call Type::GetTypeFromHandle; ldstr "name"; ldc.i4 flags; callvirt Type::GetField(string, BindingFlags)` (no branch into the sequence, lookup-only flags, `T` a game type, field declared on `T`, `(T, name)` a `"private": true` `field_read` entry) |
| `G5` | `Object.Instantiate*/Destroy/DestroyImmediate/DontDestroyOnLoad`, `GameObject.AddComponent*/SetActive/SendMessage*/BroadcastMessage`, `Component.SendMessage*/BroadcastMessage`, `Time.set_*`, `PlayerPrefs.*`, `SceneManager.Load*/Unload*`, `Application.Quit/OpenURL`, `Input.*` |
| `G6` | Any use of `0Harmony`, namespace `DevConsole*`, `PAConsole`, `SavegameManager`, `SavegameStorage`, `QuicksaveManager`, `AutosaveManager`, any game/Unity type in a `UI` namespace (`UI`, `UI.*`, `*.UI`, `*.UI.*`) or named `*ViewModel` |
| `G7` | Exactly one `ProjectAutomata.Mod` subclass, non-abstract, sealed, deriving directly from `Mod`; no other type deriving from a game/Unity type; no game/Unity interface implemented (incl. explicit implementations); no game/Unity attribute applied anywhere; no P/Invoke, `InternalCall`, native module reference; no unsafe code (pointer/function-pointer types, `UnverifiableCode`, `SkipVerification`, `localloc`, `cpblk`, `initblk`, `calli`, `Marshal.*`, `GCHandle.*`) |
| `G8` | `System.Diagnostics.Process.*` except `GetCurrentProcess()` and `get_Id`; `ProcessStartInfo`; `System.Net*`, `System.IO.Pipes*`, `System.IO.MemoryMappedFiles*` (types or members); `Thread.Abort/Suspend/Resume`. `Stopwatch` is allowed |
| `G9` | Only types in `RoiMcp.Observer.ReadLayer(.*)` may use game/Unity types or members (member references, signatures, fields, locals, type operands, generic arguments). Exception: the `Mod` subclass (and its compiler-generated nested types) may use its base-chain members (`Mod`, `MonoBehaviour`, `Behaviour`, `Component`, `Object`; still allowlisted), the `Scene`/`LoadSceneMode` types and `Scene` members, the `UnityAction` constructors and `SceneManager.add_sceneLoaded/add_sceneUnloaded/add_activeSceneChanged`. Only `RoiMcp.Observer.Io(.*)` may use `File`, `FileInfo`, `FileStream`, `Directory`, `DirectoryInfo`, `FileSystemInfo`, `FileSystemWatcher`, `DriveInfo`, `System.IO.IsolatedStorage*` or path-taking `StreamWriter`/`StreamReader` constructors (`Path`, `IOException`, `Stream`, `MemoryStream` are allowed everywhere). `RoiMcp.Observer.Publish(.*)` violations are reported with a Publish-specific message. Compiler-generated nested types belong to their outermost type's namespace |
| `G10` | No type `RoiMcp.Observer.DebugFaults` unless `--allow-debug-faults` |
| `G-TRANS` | Transitive scan (below): an unacknowledged finding |
| `G-TRANS-COLL` | Transitive scan: a write into a game collection; acknowledgements are ignored |

### Transitive scan (PRD §10.4)

For every `game` entry of kind `method`/`getter`, the gate walks the bodies of callees inside
`Assembly-CSharp`/`Assembly-CSharp-firstpass` to depth 4 (the entry itself is depth 0), following
`call`, `callvirt`, `newobj`, `ldftn`, `ldvirtftn`. For `callvirt` on virtual/interface methods it also walks
every override/implementation found in the game assemblies (fan-out capped at 32 per call site; total budget
4000 methods per root). Dispatch is narrowed to the receiver's static type when it is known from IL, including
generic arguments bound by the entry (for ``ManagerBehaviour`1<World>`` only `World`'s overrides are walked). Calls into
Unity stop the walk (listed in the report as `unity_boundary_calls`, not a failure).

Findings (`kind`):

- `stfld`: field store whose target object is not "this-local" or method-local. This-local = `ldarg.0` of the
  root instance method, or of a callee invoked on a this-local receiver, or the fresh object of a `newobj`.
  Method-local = the address of a local struct (`ldloca`) or of a by-value struct argument (`ldarga`).
  Determined by a backward stack analysis that follows join points; anything it cannot prove is reported.
- `stsfld`: every static field store.
- `denylisted-call`: a callee (or a dispatch target) matching the denylist. The walk does not enter it.
- `unity-random`: any `UnityEngine.Random` member. `deterministic-random`: any access to `World.deterministicRandom`.
- `collection-write`: `Add/Insert/set_Item/Remove*/Clear/Enqueue/Dequeue/Push/Pop/Sort/...` on
  `System.Collections(.Generic|.Concurrent|.ObjectModel)` types, unless the receiver is provably a collection
  created by `newobj` in the same method. **Not acknowledgeable** (`G-TRANS-COLL`).
- `caller-collection-write`: the same writes, but into a collection that provably arrives unchanged from the
  entry's own arguments (i.e. a buffer the observer passes in, such as `GetSafe(dict, ...)` or a
  `List<T> result` parameter). Acknowledgeable; the justification must state that the observer passes its own
  buffer. If the game code may substitute a pooled list (`ref` / `ListPool.GetIfNull`), it stays `collection-write`.
- `walk-capped`: fan-out or budget cap reached (ackable, so the reviewer must accept the incomplete analysis).

Finding id: `<entry member> -> <containing method definition> : <kind> <target>`, e.g.
`` ProjectAutomata.ManagerBehaviour`1<ProjectAutomata.World>::get_instance() -> ProjectAutomata.ManagerBehaviourBase`1::InitializeSingletonInstance(TInstance) : stsfld ProjectAutomata.ManagerBehaviourBase`1<TInstance>::_instance ``.
An ack's `finding` is a glob (`*`, `?`) matched against the full id or the id without the
`<entry member> -> ` prefix.

## Limitations

- The gate is structural; it is one of the four pillars of the read-only claim (PRD §2.1), not a sandbox.
- Writes through game-owned arrays (`ldfld` array + `stelem`) and calls to BCL collection mutators on game-owned
  collections made *directly by the observer* (e.g. `building.someList.Clear()`) are not detected by G1/G2; they are
  only caught transitively inside game code. Review ReadLayer code for this.
- BCL virtual calls dispatched to game overrides (`object.ToString()`, `Equals`, `GetHashCode` on game objects)
  are not followed.
- In the transitive scan, `Interlocked.*` on game fields, `initobj`/`stobj` through field addresses and writes via
  `ref` locals are not reported as field stores.
- Runtime preconditions in PRD §9.3 (`GlobalMarket.GetPricingInfo` only for market products,
  `TransportRequestHandle` getters only on valid handles, `GameDataManifest.GetAssetsRO` only for known types)
  cannot be checked statically and are not in the denylist; `SettlementGrowth.GetTier*` is denied outright.
- G2 prefixes are literal (`Set*` also matches `Settings()`); hard rules cannot be overridden by the allowlist.
- `new T()` in observer generic code compiles to `Activator.CreateInstance<T>()` and fails G4.
