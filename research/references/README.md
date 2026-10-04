# Cloned reference repositories

All clones are `git clone --depth 1`, for reading only. Nothing here was built, run, or installed into the game.
Retrieved 2026-10-03. All of them target the ORIGINAL Rise of Industry (Steam App 671440), not Rise of Industry 2.

| Folder | URL | Commit (HEAD) | Commit date | Why it's here |
|---|---|---|---|---|
| `ExportStuffMod/` | https://github.com/roiroy/ExportStuffMod | `aad9ec4840e419d4fc6d806c8b26bc11cb37db9f` | 2019-05-12 | Official-loader code mod (`Mod` subclass) that dumps recipes and producers from `GameData` as JSON to the Unity log. Includes a 2019 data export (`exports/exports.json`, `exports.csv`). |
| `ROI-CustomMod/` | https://github.com/sanasol/ROI-CustomMod (formerly S-anasol) | `92c332bb047c5509fff0e438450a4afce2a96f57` | 2019-05-06 | ExportStuffMod's ancestor. Sample code mod with a DevConsole command, a `World.instance.isWorldReady` poll, a recipe JSON dump, and formula patching. |
| `TransportCostsRebalanced/` | https://github.com/pjf/TransportCostsRebalanced | `d637f8120a951c854e5194b5e919e4276ad915e5` | 2018-12-03 | Early (Alpha 8) code mod that edits `Formula` assets and `Vehicle.maxSpeed` at load. Published as Workshop item 1581416174. MIT licence. |
| `rockymine-RiseOfIndustry/` | https://github.com/rockymine/RiseOfIndustry | `ae04ff99d1a7dbf9be8343f46d69d3bdb7c9ef0c` | 2021-06-12 | Only a compiled `ROIData` code mod (no source), from a university "Planspiel" project (SoSe2021). It reads live in-game state every frame or day, uses Harmony 1.x patches, and POSTs JSON to a web API. The DLL was decompiled statically for reading into `research/_local/decompiled/thirdparty/ROIData*`. The repo also holds 2 `.sav` files and a 14 MB zip, which were not examined. |
| `RiseOfIndustry-AlwaysNightMod/` | https://github.com/twikantoro/RiseOfIndustry-AlwaysNightMod | `34519dbc9cf61957e309db3e8d5867af19a0e001` | 2026-09-22 | A recent (2026) mod "tested on 2.3.3 : 0507b". It does NOT use the official loader. It rewrites `Assembly-CSharp.dll` with Mono.Cecil to inject a hook into `TimeManager.Awake`. This is a negative example and should not be copied. |
| `roimodexamples/` | https://github.com/Jettucis/roimodexamples | `6ca2b605c0340af5b2fe96d304f593b36260fc00` | 2020-07-14 | Commented content-JSON examples (Recipe, ProductDefinition, TechTree unlocks, names). Data mods only, no code. |

Decompiled third-party DLLs (static ILSpy output, never executed) are in
`research/_local/decompiled/thirdparty/{ExportStuffMod,ROIData,ROIData-root}`.

Repos found but not cloned (data or calculators only, or not relevant) are listed in `research/notes/public-projects.md` §3.
