# Known limitations

This page lists what V1 does not do, what the game does not provide, and the open items of PRD §23 with
their current behaviour. See [VALIDATION-REPORT.md](VALIDATION-REPORT.md) for the status of the in-game
validation gates. The limitations of the V1.1 advisory tools, and two V1 derivation errors corrected in server
1.1.0 (pre-built gatherer rates, `deposit_depleted` on modules without deposits), are in
[v1.1/RELEASE-NOTES.md](v1.1/RELEASE-NOTES.md).

## Scope (by design)

- **Original Rise of Industry only**, and only the verified build 2.3.3 : 0507b (Steam build 9064059,
  savegame version 2304, `Assembly-CSharp.dll` SHA-256 `D62599EF…`). Any other build: the observer reports
  `unsupported_build`, captures nothing, and every tool returns `unsupported_build`. There is no override.
- **Read-only.** No tool builds, demolishes, changes recipes, efficiency, destinations, Max Send/Min Keep,
  research, vehicles, markets, finances or saves, and nothing sends keyboard or mouse input.
- **Live game only.** There is no offline mode that reads `.sav` files; the research save parser is
  validation tooling only.
- **No long-term history database.** Only history the game itself retains is exposed (below). The server
  keeps at most 20 snapshots / 30 minutes of the current world session in memory for short-term deltas.
- **Bounded history windows.** Per-building analysis and production series cover the last 24 months, shop
  series the last 12. The game may hold more (some buildings keep decades of daily values); each series says
  so with `history_truncated` and states its window (see SNAPSHOT-FORMAT.md, "Bounded history windows").
- **Short freshness window after a world change.** The heartbeat is written every second and the server reads
  it with a 1 s cache (PRD 13.1), so for up to about 2 s after a save is unloaded a call can still see the
  previous world as current.
- **Size cap truncation is coarse.** When a response exceeds ~30 KB, lists are halved until it fits (with a
  `truncated` warning and, for paged lists, `page.next_cursor`); a non-paged tool can therefore return fewer
  rows than would fit. Narrow the request to see the rest.
- **Build cost without regional modifiers.** `get_building_type.current_player_cost` is the tech-tree price
  (`GetBuildingCost`); regional cost modifiers applied at placement are not included.
- **Supply-chain requirement maths recurses per path.** Real catalogues answer in milliseconds; an artificial
  recipe lattice (several products per level, 12 levels) can take seconds.
- **No map exports**: no per-tile terrain, road/rail networks, pollution grids or resource-node grids;
  only per-building and per-region values. Route paths are optional and decimated.
- **No stable vehicle identity.** The game reuses pooled vehicle objects and reassigns `Vehicle.id` on
  every trip. Vehicles are reported mostly in aggregate; per-vehicle ids are valid in one world session
  only (`stale_reference` otherwise).
- No network listener of any kind (stdio MCP only), no GUI, no multiplayer, no localisation of MCP output.

## Data the game does not provide

| Missing | Why | What V1 does |
|---|---|---|
| Market price history | The game stores only the current price modifier and trend | `price_history: {"available": false, "reason": "not_retained_by_game"}` |
| Daily money ledger | The game aggregates bills per month | Monthly ledger per bill category only |
| Ledger per counterparty kind | Only the per-category totals API is used (its pooled bill lists are avoided) | Income/expense per month and category; counterparty not reported |
| Per-route history | Manual destination slots keep no history | Current route state only; building-level dispatch cost series where the game keeps them |
| Wages / salaries | Not a concept in Rise of Industry | The efficiency slider and its upkeep multiplier are exposed as `efficiency` |
| AI cash | AI companies and the State have infinite money in this game | Reported as `cash: {"infinite": true}` |
| Name of the loaded save (U7) | No runtime member identified | `save_name: null` with a reason in the heartbeat |

## Unresolved items (PRD §23)

| Id | Item | Interim behaviour |
|---|---|---|
| U1 | UI labels "Max Send" / "Min Keep" ↔ `maxAcceptedAtDestination` / `minStoredAtSource` | **Resolved** by gate E2: labels, sharing and "∞" confirmed in the game; `ui_label_validated: true`. No "auto" Max Send control was offered on the shop route tested, so the auto mode is reported from the save data but was not exercised in the UI |
| U5 | Air network name; ship transport | The raw `networkName` string is exported as `transport_mode`; E1 records the network names of the running game |
| U6 | Building-key uniqueness for warehouse module buildings | Uniqueness is asserted on every capture; collisions get a `#<guid8>` / `#i<instance>` suffix and are counted in the heartbeat (`id_collisions`) |
| U7 | Name of the loaded save | `null` with reason |
| U8 | Capture cost on very large maps | Default intervals and budgets of PRD §8.4, adaptive back-off; optional sections off by default |
| U9 | Process working directory = install dir (local `Mods\` discovery) | Launch the game through Steam; the heartbeat reports `cwd` |
| U10 | Unity `Player.log` state | The observer logs to its own `observer.log` only |
| U-EN | English-name lookup through `LanguageData` | `english_name: null` when not found, with the warning `english_name_unavailable`; search falls back to asset names |
| U-WE | Purity of the world-event product-target objective implementations | A route whose destination is targeted by an active product-target dynamic world event reports `dispatch_amount_now.complete: false` with `world_event_unevaluated` |
| U-AI | AI product goals (brain state) | Not exported; listed as unavailable |
| U-PAY | Which dispatch-cost formula each depot uses | The formula asset name actually used is exported per route (`dispatch_formula`) and per building type |
| U-MODS | Content mods | Definitions are read from the live game data, so mod content appears with its own asset names (opaque ids) |
| U-HIST | Retention of per-building analysis series | Exported as retained by the game; the first/last months are visible in the data |

## Behavioural notes

- **Max Send is a shared, destination-side cap.** Changing it from one supplier's panel changes it for
  every origin shipping that product to that destination. It caps the destination's stock *including*
  in-flight deliveries. It is not a per-trip amount.
- **Min Keep is per route.**
- **Shop demand** is in units per consumption interval of the city (`consumption_interval_days`, usually
  15 days), not per month.
- **`dispatch_amount_now`** is what the next dispatch on a route would request now, replicated from the
  game's formula. The game's scheduler, vehicle availability and transport priorities decide actual
  throughput.
- **`straight_line_cost_estimate`** in `find_shops` is a non-authoritative estimate for shops without a known
  route; existing routes always use the game's own `distance_tiles` and `dispatch_cost`. From an AI or State
  building, route existence is unknown while `include_ai_routes` is off (the default):
  `existing_route_status` is `unavailable` and any estimate carries `route_exists: null`.
- **Without usable live state** (game not live, no valid `state.json`, or a schema major mismatch), `search`
  and the catalogue tools answer from the static catalogue only (`source: "static_catalog"`) and list
  `live:state` in `unavailable` with the reason. An invalid or mismatched `state.json` is never read.
- **Freshness.** Snapshots are taken every ≥ 5 s of real time while the game runs (only when the game day
  changed) and every 15 s while paused; `fresh: true` asks for a new snapshot (up to 3 s wait by default).
  Always check `meta.stale` and `meta.snapshot.age_s`.
- **Exchange files are replaced, not rewritten in place.** The observer writes a temporary file and swaps it in
  with `File.Replace` (PRD §11.3), so a file is never partial. On Windows the swap leaves no file under the
  target name for a moment; a reader that looks in that instant sees it missing (once in 540 reads at 5 s in the
  T-12 soak). The MCP server keeps the last good snapshot in that case; other readers should retry.
- **Saves made with the observer enabled** list the mod in their header (cosmetic "missing mods" notice if
  later loaded without it).

## Read-only guarantee and its limits

The observer runs inside the game process through the official mod loader, so in principle in-process code
could call any game method. The read-only property is a property of this implementation, supported by:

1. **Structural enforcement** — the IL gate checks every member the observer references against a
   default-deny allowlist, a denylist of known disguised mutators, hard rules (no field writes on game
   types, no setters or mutator-named methods, no reflection writes, no Harmony, no events, no network,
   layering) and a transitive scan of allowlisted game methods. See [READ-ONLY-GATE.md](READ-ONLY-GATE.md).
2. **Automated tests** of the gate (negative fixtures) and of the observer logic.
3. **Empirical verification** — gate V8 compares saves made before and after a full tool sweep against a
   control pair. It covers what was exercised and what a save diff can observe; it is not a proof.

It is not a security sandbox against a malicious build of the observer.
