# Installation & runtime facts (collected 2026-10-03)

All items CONFIRMED by direct read-only inspection unless marked otherwise.

## Steam / install

| Item | Value | Evidence |
|---|---|---|
| Steam App ID | 671440 ("Rise of Industry") | `C:\Program Files (x86)\Steam\steamapps\appmanifest_671440.acf` (`"appid" "671440"`, `"name" "Rise of Industry"`) |
| Steam build id | 9064059 | appmanifest `buildid`; `appworkshop_671440.acf` `LastBuildID` |
| Depot / manifest | 671441 / 8360881793189550917 | appmanifest `InstalledDepots` |
| Steam library | `C:\Program Files (x86)\Steam` (library folder 0) | `steamapps\libraryfolders.vdf` |
| Install dir | `C:\Program Files (x86)\Steam\steamapps\common\RiseOfIndustry` | appmanifest `installdir`, process path |
| Executable | `...\RiseOfIndustry\Rise of Industry.exe` (650,752 B) | `Get-Process` path |
| Exe FileVersion | 2018.4.11.7379119 (= Unity player version) | `VersionInfo` |
| Size on disk | 1,959,055,311 B | appmanifest |
| Language config | french | appmanifest `UserConfig.language` — UI strings will be French; use internal names, not display names |
| Files last written | 2026-10-01 19:31 (recent Steam update/verify) | directory timestamps |
| Workshop | 1 subscribed item `1731403319` ("Noms de villes Françaises", content-only JSON: `ProjectAutomata.SettlementNameList`) | `steamapps\workshop\content\671440\1731403319\` |

## Game version (from assets)

Extracted with `research/tools/asset-inspect/game_version.py` (UnityPy, read-only) from the `ProjectAutomata.GameVersion` ScriptableObject named `Version` in `resources.assets` (path_id 40454):

```
buildType=2 (STEAM) releaseType=1 (PUBLIC) major=2 minor=3 revision=3 suffix='b' build='0507'
commitHash='76359e59644ebf55cafacf9a50b3d19800df22d7' savegameVersion=2304
```

`GameVersion.ToString()` therefore renders `Steam - Public - 2.3.3 : 0507b` (plus `*` when mods are enabled — `ProjectAutomata/GameVersion.cs`).
DevMods "Default Scenarios" `desc.json` reports version 230 / "2.3.0".

## Unity / .NET runtime

| Item | Value | Evidence |
|---|---|---|
| Unity | 2018.4.11 | exe FileVersion, `UnityPlayer.dll` |
| Scripting backend | **Mono** (not IL2CPP) | `MonoBleedingEdge\EmbedRuntime\mono-2.0-bdwgc.dll` loaded in process; `Managed\*.dll` present |
| Scripting runtime | .NET 4.x equivalent (`scripting-runtime-version=latest`) | `Rise of Industry_Data\boot.config`; `mscorlib` 4.0.0.0 (file 4.6.57.0) |
| GC | Boehm (bdwgc), Unity 2018.4 = **non-incremental, stop-the-world** (incremental GC arrived in Unity 2019.1) | module name; Unity release history (HIGH CONFIDENCE) |
| Harmony | `0Harmony.dll` 1.1.0.0 (Harmony 1.x API: `HarmonyInstance`) shipped by the game | assembly name; `ModLoader.InitializeModdingApi()` |
| Newtonsoft.Json | 11.0.2 shipped | `Managed\Newtonsoft.Json.dll` |
| LZ4 | `LZ4.dll` 1.0.10.93 (lz4net) | assembly name |
| ECS | Unity.Entities / Jobs / Burst present (`lib_burst_generated.dll`) | Managed + Plugins dirs |
| Other plugins | `steam_api64.dll`, `EOSSDK-Win64-Shipping.dll` (Epic), `xaudio2_9redist.dll` | `Plugins\` |
| Main game code | `Assembly-CSharp.dll` (3.66 MB, ~5,300 decompiled files, ~2,100 in `ProjectAutomata` namespace), `Assembly-CSharp-firstpass.dll` | `Managed\` |

## Running process (observed passively, 2026-10-03 ~16:30)

`Get-Process`/`Win32_Process` only (no handle with write/debug rights, no memory reads):

- PID 20300, started 2026-10-03 12:15:13, command line has no arguments.
- 132 threads, working set ≈ 5.8 GB, **private bytes ≈ 8.2 GB**, ~38,000 CPU-seconds in ~4 h wall time (multi-core load).
- Loaded modules include `UnityPlayer.dll`, `mono-2.0-bdwgc.dll`, `lib_burst_generated.dll`, `steam_api64.dll`, `d3d11.dll`.

Implication: very large managed + native heap. Any observer-induced GC allocation pressure can trigger a full, non-incremental Boehm collection over a multi-GB heap → visible frame stalls. Observer must be allocation-frugal (see RISKS.md).

## User data locations

| Purpose | Path | Notes |
|---|---|---|
| Saves | `%APPDATA%\RiseOfIndustry\*.sav` (= `C:\Users\<user>\AppData\Roaming\RiseOfIndustry`) | 11 saves incl. `Autosave 1..3.sav`; Steam Cloud (`steam_autocloud.vdf`) |
| Keybindings | `%APPDATA%\RiseOfIndustry\Keybindings.json` | |
| Unity player log dir | `%USERPROFILE%\AppData\LocalLow\Dapper Penguin Studios\Rise of Industry\` | `output_log.txt` exists but is **0 bytes** while the game runs (see runtime-lifecycle notes) |
| Unity analytics cache | `...\LocalLow\Dapper Penguin Studios\Rise of Industry\Unity\` | |
| PlayerPrefs | Windows registry `HKCU\Software\Dapper Penguin Studios\Rise of Industry` (Unity default; INFERRED from `app.info` company/product) | ModLoader stores disabled mods + mod order in PlayerPrefs |
| Dev mods | `<install>\Rise of Industry_Data\StreamingAssets\DevMods\Scenarios` | content-only mod (scenario JSON + .save) |
| User mods | `ModLoader.userModsPath` is "Mods" (relative) — see runtime-lifecycle notes for resolution | no `Mods` folder currently exists in install dir |

## Save copies used for analysis

Copied (never opened in place) to `research/_local/saves/` with SHA-256 verified identical (`HASHES.txt`):

Three saves were copied: a manual save (called `sample-a` in these notes) and two autosaves (`Autosave 1`,
`Autosave 2`). Their SHA-256 hashes were recorded locally to verify the copies; the saves and hashes are not
published.

## Reproducible commands

```powershell
Get-Process | ? ProcessName -match 'Rise'            # path, start time
Get-Content 'C:\Program Files (x86)\Steam\steamapps\appmanifest_671440.acf'
(Get-Item '<install>\Rise of Industry.exe').VersionInfo
Get-Content '<install>\Rise of Industry_Data\boot.config'
ilspycmd -p -o research/_local/decompiled/Assembly-CSharp research/_local/managed/Assembly-CSharp.dll   # ilspycmd 11.1.0.9782
research/_local/venv/Scripts/python research/tools/asset-inspect/game_version.py                    # UnityPy 1.25.4
```
