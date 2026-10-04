# Snapshot format

The observer (in the game) and the MCP server (outside it) exchange data only through files in the
**exchange directory**:

```
%LOCALAPPDATA%\RoiMcp\        (override: environment variable ROI_MCP_EXCHANGE_DIR, honoured by both sides)
```

| File | Writer | Content | Cadence |
|---|---|---|---|
| `heartbeat.json` | observer | lifecycle and diagnostics, no world data | every 1 s |
| `static.json` | observer | definitions (products, recipes, building types, tech, formulas, ...) | per world session and game-module change |
| `state.json` | observer | live world state | every ≥ 5 s while running (only if the game day changed), ≥ 15 s while paused, on refresh request |
| `history.json` | observer | history the game itself retains | on world load, first frame of a new month, on refresh request |
| `refresh-request.json` | MCP server | refresh nonces | on demand |
| `observer.config.json` | user | observer configuration | manual |
| `observer.disabled` | user | kill switch (its presence disables the observer within 1 s) | manual |
| `observer.log`, `observer.log.1` | observer | log, rotated at 1 MB | — |
| `server.log*` | MCP server | log, rotated at 5 MB × 3 | — |
| `backups\` | `scripts/backup-saves.ps1` | copy-only save backups with `manifest.json` | manual |

The JSON Schemas (2020-12) in [`schemas/`](../schemas) are the authoritative wire format:

- `heartbeat.schema.json`, `static.schema.json`, `state.schema.json`, `history.schema.json` are
  **generated** from the observer DTO classes (`observer/src/RoiMcp.Observer/Dto/`) by the observer test
  suite; a test fails when they drift. Each property carries `x-data-map-id` (the concept id in
  `research/data-map.json`) and `x-source` (`STATIC`, `SAVE`, `RUNTIME`, `DERIVED`) where applicable,
  either on the property or on its enclosing object.
- `refresh-request.schema.json` and `observer-config.schema.json` are hand-written.
- `schemas/tool-responses/` describes the MCP tool responses.

## Envelope

Every observer-written file has the same envelope; the payload is in `data`.

```json
{
  "schema": "roi-mcp/state",
  "schema_version": "1.0.0",
  "observer_version": "1.0.0",
  "compatibility": "verified",
  "game": {"version": "2.3.3", "build": "0507b", "commit": "76359e59…", "savegame_version": 2304,
           "assembly_sha256": "D62599EF…", "release": null},
  "pid": 20300,
  "world_session": "3f0c…",
  "seq": 812,
  "content_hash": "sha256 hex of the serialized data value",
  "written_utc": "2026-10-03T17:00:00.000Z",
  "captured": {"utc_start": "…", "utc_end": "…", "game_day_start": 27893, "game_day_end": 27893,
               "game_date": "Y78-06-24", "frames": [123456, 123470], "main_thread_ms": 11.7,
               "slices": 9, "max_slice_ms": 1.9, "consistent": true},
  "static_ref": {"seq": 3, "content_hash": "…"},
  "sections": {"buildings_player": {"status": "ok", "reason": null, "game_day": 27893, "frame_start": 0,
               "frame_end": 0, "items": 412, "items_vanished": 0, "main_thread_ms": 3.1}},
  "warnings": [{"code": "inconsistent_snapshot", "detail": "…"}],
  "data": { }
}
```

- Field order is fixed (`data` last); `content_hash` is the SHA-256 of the exact bytes of the `data` value.
  An unchanged `content_hash` is not rewritten; the heartbeat then advances `families.<f>.last_verified_utc`.
- `seq` is monotonic per file family within one observer process.
- `world_session` is a new random GUID at every entry into the `ready` state (every save load, including
  quickload). Data from different world sessions must never be mixed.
- `static_ref` (state and history only) names the `static.json` the snapshot was built against
  (`seq` 0 and an empty hash when no static file of the same world session was published).
- `sections` lists each section with `status` ∈ `ok`, `disabled`, `failed`, `skipped`, `over_budget`.
  A section whose status is not `ok` has `null` data — never partial data.
- `consistent` is true only when all sections were read on the same game day.
- `game_date` is `Y<year>-<MM>-<DD>` (30-day months, 360-day years, year 1 is the first year);
  `game_day` is the absolute day count (1 = Y1-01-01).

### Compatibility

`static.json`, `state.json` and `history.json` are only ever produced on the verified baseline build, so
their `compatibility` is always `verified`; the server rejects any other value. The heartbeat's
`compatibility` is:

- `pending` until the version gate has run (it runs at the first `ready` of the game process, because
  the observer makes no game reads in the main menu). This value is an addition to PRD §11.2, which only
  lists `verified` and `unsupported_build`; it avoids claiming a verification that has not happened.
- `verified` after a passing gate.
- `unsupported_build` after a failing gate: heartbeat only, zero capture, for the rest of the process.
  `data.detected_game` and `data.expected_game` explain the mismatch. There is no override.

## Keys and ids

Snapshot files use **raw keys**; the MCP server composes the `<kind>:<key>` ids of PRD §7.1.

| Entity | Raw key in snapshots | MCP id |
|---|---|---|
| Building (incl. shops, modules, HQ) | `key`: `<prefab asset name>@<x>,<y>`, suffixed `#<8 hex of save GUID>` or `#i<instance id>` on a collision | `building:<key>` |
| Building type | prefab asset name | `building_type:<name>` |
| Product / recipe / tech unlock / tech tree / bill category | asset name | `product:` / `recipe:` / `tech:` / `tech_tree:` / `bill_category:` |
| Company (player or AI) | `actor_id` (int) | `company:<actor_id>` |
| City | `city_id` (settlement actor id) | `city:<city_id>` |
| Region | `region_id` (GUID string) | `region:<guid>` |
| Route (manual destination slot) | `route_key`: `<origin>\|<product>\|<destination>\|<source>\|<n>` | `route:<route_key>` |
| Warehouse request | `request_key`: `<endpoint>\|<product>\|<n>` | `request:<request_key>` |
| Vehicle | `instance_id` (Unity instance id of the pooled object) | `vehicle:<world_session>:<instance_id>` |

- `<source>` is `own` (the origin's own fleet) or the depot module prefab; `<n>` is the 0-based occurrence
  among slots with the same tuple, in slot order.
- Display names (`display_name`) are the in-game strings in the UI language and can be renamed by the
  player; they are not keys. English names come from the game's en-US localization (`english_name`,
  `null` when unavailable).
- Vehicle ids are only valid within one world session (the game reuses pooled vehicle objects;
  `trip_counter_id` is `Vehicle.id`, reassigned on every trip).

## `state.json` sections

`building_index` (internal: key assignment and per-owner aggregates; no data of its own), `session`,
`companies`, `buildings_player`, `buildings_ai` (compact), `buildings_ai_detail` (optional),
`routes_player`, `routes_ai` (optional), `requests_player`, `shops`, `cities`, `regions`, `market`,
`research`, `vehicles`, `route_paths` (optional). `research.player.building_costs` is the player's current
build price per building type (the value `TechTreeAgent.GetBuildingCost` returns, before regional modifiers;
null if the price table cannot be read). Optional sections are off by default
(`observer.config.json`: `include_ai_building_detail`, `include_ai_routes`, `include_route_paths`) and are
skipped automatically after three captures over `capture_ceiling_ms`.

Route rows carry the semantics of PRD §12.3:

- `max_send` — the cap stored **on the destination building, per product, shared by every origin**
  shipping that product there; it caps the destination stock including incoming reservations; `0` =
  unlimited. `mode` is `manual` or `auto_shop_demand` (the shop's current demand). `headroom_now` =
  `max(value − (stored + incoming reserved), 0)`.
- `min_keep` — per route: the origin stock floor this route will not dispatch below; `keep_all` when the
  game value is `int.MaxValue`.
- `dispatch_amount_now` — a replica of the game's private `GetRequestedAmount`, computed from pure inputs
  (`inputs` included); `complete: false` when an input is unavailable or a dynamic world event targets the
  destination (its evaluation is not reviewed).
- `distance_tiles` and `dispatch_cost` are the game's own cached values; `null` with
  `path_status: "unavailable"` when the game has no cached path.
- `ui_label_validated` is `true`: validation gate E2 confirmed in the game that the UI labels "Max Send"
  and "Min Keep" map to these members, that Max Send is shared by every origin of a destination and
  product, and that "∞" is `value: 0, unlimited: true`. Observers older than that gate report `false`.

## `history.json` sections

`ledger_player` (monthly income/expense per bill category, as retained by the game: about 3 years),
`buildings_monthly_player` (the game's per-building analysis series, last 24 months), `production_monthly_player`
(produced/consumed per month, last 24 months), `shops_monthly` (sold and demand per month, last 12 months),
`player_product_stats` (the game's current statistics window), `state_sales` (State sales over 30/60 days).
The game keeps no market price history, no daily money ledger and no per-route history; none is invented.

### Bounded history windows

The windowed series are a **bounded recent window, not all history the game retains**. Some buildings keep
their analysis series for the whole save (decades, sometimes one value per day); exporting that raw broke
the performance budgets (validation gate E3). Every series in `buildings_monthly_player`,
`production_monthly_player` and `shops_monthly` therefore carries:

| Field | Meaning |
|---|---|
| `window_months` | Calendar months in the exported window, counting the current month (fewer only early in a save) |
| `window_first_month`, `window_last_month` | Oldest and newest month of the window; the last one is the current, in-progress month |
| `history_truncated` | `true` when the game still holds data older than `window_first_month`; `false` when the window covers everything it holds; `null` if undetermined |
| `first_month_available` | Oldest month the game still holds, when it can be read cheaply (analysis series); `null` for production and shop series |

Analysis series also report `aggregation` (`sum` or `average`, the game's own rule for combining one month)
and `values_retained` (raw values the game holds, before aggregation). Truncation is detected from the
oldest retained value (analysis) or with one range query before the window (production, shops). The
game prunes production, consumption and shop records at each year end (about two years kept), and analysis
series only for production buildings, so truncation genuinely differs per series.

`ledger_player` covers the game's whole ledger retention (`retention_years`); it reports `window_months` and
`history_truncated` (`false` unless the game's retention exceeds the 3 years queried).

## Size caps

`heartbeat` ≤ 16 KB, `static` ≤ 4 MB, `state` ≤ 5 MB, `history` ≤ 5 MB. Over a cap, whole sections are
dropped in this order and marked `skipped` / `size_cap` with a warning:

- state: `route_paths`, `routes_ai`, `buildings_ai_detail`, `buildings_ai`
- history: `shops_monthly`, `production_monthly_player`, `buildings_monthly_player`

If the file still exceeds the cap it is not written (`publish_failures` in the heartbeat). A truncated or
partial file is never written.

## Atomic publication

Each file is written to `<name>.tmp-<pid>`, flushed to disk and moved over the target with `File.Replace`
(or `File.Move` when the target does not exist). Readers therefore always see a complete file. Leftover
`*.json.tmp-*` files are deleted when the observer starts.

## Refresh requests

`refresh-request.json` is the only data flowing towards the game:

```json
{"schema": "roi-mcp/refresh-request", "schema_version": "1.0.0",
 "requests": {"state": 1730000000123, "history": null, "static": null}, "requested_utc": "…"}
```

The observer reads it only when its modification time changes, ignores files over 1 KB and reads only the
three integers. Each scope means "produce a fresh snapshot of this family when it is safe to do so". Served
nonces are reported in the heartbeat (`refresh_served`). Nothing in the file is ever used as a name, path,
filter or id.

## Versioning

`schema_version` is semver. The server accepts files with the same major version only (`schema_mismatch`
otherwise). Additive changes bump the minor version.
