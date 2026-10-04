# Save-copy validation tools

`save_tools.py` supports the save-based gates of the PRD: E3 test-save selection, E7 identifier
correspondence, V8 read-only verification and T-10 local fixtures. It parses save **copies** with the
research parser (`research/tools/save-inspect/roi_save.py`), which it loads by file path at runtime.
Production code never imports from `research/`.

Run everything from the repository root:

```powershell
uv run scripts/validation/save_tools.py rank <folder of copies> [--out .local/....json]
uv run scripts/validation/save_tools.py extract <copy.sav> --out .local/validation/extract.json
uv run scripts/validation/save_tools.py compare-e7 .local/validation/extract.json <state.json> --out .local/validation/e7.json
uv run scripts/validation/save_tools.py diff-v8 <V8-a.sav> <V8-b.sav> <V8-c.sav> --out .local/validation/v8.json
uv run scripts/validation/save_tools.py fixture <copy.sav> --out .local/fixtures/<name>
uv run --with pytest --with lz4 pytest scripts/validation
```

Prefer `scripts/gen-fixtures-from-save-copy.ps1` (`-FromBackup latest` or `-SaveCopiesDir`). It
re-verifies every copy against the `manifest.json` written by `scripts/backup-saves.ps1`, works on
fresh copies under `.local/save-copies/<timestamp>/`, and records SHA-256 hashes before and after
parsing in `hashes.json`.

## Safety rules enforced by the tools

- Paths at or below `%APPDATA%\RiseOfIndustry` (the live saves) are refused. This is a string check,
  so the folder is never opened.
- Every output derived from a save must be below `<repo>/.local/`, which is gitignored.
- Committed tests use synthetic data only.

## Gate notes

- **E7.** Building keys are `<prefab>@<x>,<y>` from the constructor parameters. A collision gets the
  observer suffix `#<guid8>`. Players and AIs are told apart by owner actor type. Save-only
  buildings with a `DecorationVisualization` component are listed separately: the observer excludes
  buildings tagged `Decoration`, and this mapping is inferred. Route destinations resolve through
  the `BuildingLogistics` component GUID. Auto Max Send counts as effective only when the
  destination has a `Shop` component, which matches the observer's rule. Every mismatch is counted
  by kind, and `time_gap` shows how many game days separate the snapshot from the save.
- **V8.** The diff ignores the header name and timestamp and the camera state (`camera`,
  `CameraManager`, `CameraRotationController`). Entities, components, dictionaries, sets and decoded
  dictionary payloads are compared without regard to order. A change in order alone is listed under
  `ordering_only_b_to_c` and does not count as a difference, except in `_savegameSlots`, `_queue`
  and `modules`, where order matters. Blobs the parser cannot decode are compared by hash.
- **T-10.** `fixture.json` holds the ids, counts and checks, including `min_keep_4_present`.
  `state-subset.json` is a subset of `state.json` fields that `compare-e7` can read.
