# Rise of Industry MCP 1.1.0 — release notes

V1.1 adds an advisory layer on top of the V1 read-only MCP server. It needs **no observer change**: it runs with
the validated V1 observer (build `D8F22442`) on Rise of Industry 2.3.3 : 0507b, reads the same snapshot files and
keeps the 29 V1 tools unchanged in name, parameters, refresh scope and response schema.

## What is new

**18 advisor and calculator tools** (47 tools in total):

| Group | Tools |
|---|---|
| Company and chains | `get_overview`, `diagnose_chain`, `get_profitability`, `find_opportunities`, `review_routes`, `plan_chain` |
| Scenarios and change | `what_if` (hypothetical, never applied), `what_changed` |
| Knowledge | `explain_mechanic`, `how_to` |
| Planning | `research_path`, `suggest_research` |
| Calculators | `loan_calculator`, `route_calculator` |
| Comparison | `compare_options` (6 kinds) |
| Spatial / structure | `spatial_analysis`, `get_chain_graph` |
| Forecast | `forecast` (short linear trends, or "not available" when the data is insufficient) |

**Every number says what it is.** Each value is a Quantity `{value, unit, kind}` where `kind` is `observed`,
`definition`, `game_computed`, `derived` (exact arithmetic), `estimate` (needs a modelling assumption) or
`parameter`. Estimates carry their method, the assumptions they rely on (listed by id with text) and a confidence
level with reasons. Missing inputs give `null` plus a `degraded_inputs` entry; values are never invented.
Recommendations are advice for the player; the MCP never performs them.

**Knowledge resources.** Curated, evidence-backed game knowledge served as MCP resources:
`roi://knowledge/mechanics` (47 mechanics, each also at `roi://knowledge/mechanics/<id>`), `roi://knowledge/glossary`
(French/English terms), `roi://knowledge/pitfalls` and `roi://knowledge/how-to`. Each entry states its verification
level and evidence: 11 of 47 mechanics are confirmed in the live game (6 of them by the V1.1 live
validation), the others come from the game's code and assets; one mechanic is marked high-confidence only.

**French / English / both.** Advisor tools take `language` (`en`, `fr`, `both`) and `detail` (`summary`,
`standard`, `full`). Names come from the game's own catalogue (the display name in the game's language and the
English name); advisor sentences have a fixed French translation.

**Snapshot pinning.** One call reads each snapshot family once, so every number in an answer comes from the same
snapshot; the answer names the snapshots used, their game date, age and freshness. Tools that read both state and
history refresh both with `fresh=true` (one observer request per family, bounded by 2 × `refresh_wait_s`).

**Read-only, unchanged.** No tool or resource changes the game, a save or the observer; as in V1, the server writes only the
refresh request in the exchange folder and its own log file. No network access.

## Live-validated formulas

A live run (world `a9eaa382`, see [LIVE-VALIDATION-RESULTS.md](LIVE-VALIDATION-RESULTS.md)) checked every
prediction against a fresh snapshot and, where possible, the game UI:

- dispatch amount per route (shared and unlimited Max Send, Min Keep) — exact on 65 routes and after live changes;
- production cycles and efficiency changes (cycle, output, upkeep) — exact;
- building upkeep, including module upkeep — exact on every owned building type;
- profitability terms, research cost and duration, flat loan payments — traced to the snapshot, UI and ledger;
- new-building estimates (`plan_chain`, `find_opportunities`, `what_if`, `compare_options`) — match the player's own
  buildings at their starting efficiency for 16 of 16 building type / recipe pairs.

**Company modifiers.** The game applies company-specific production and upkeep modifiers that it does not export.
In the validation world the oil buildings (oil wells, offshore rigs, petrochemical plants) ran at **×0.75 output and
×1.2 upkeep**. V1.1 measures these modifiers on the player's own buildings of the same type (only when they all
agree) and uses them for new-building estimates. For building types the player does not own, the modifier is
assumed to be 1 and the estimate is flagged and its confidence lowered. A measured modifier is the current one; it
may change (the oil modifier looked recent).

## Fixed compared with V1

Two V1 server derivations were wrong; V1.1 corrects them without changing any V1 field or schema:

- **Map-prebuilt gatherers** (tag `PrebuiltGatherer`: pre-built gas, oil, copper, coal and iron hubs) were rated as
  factories, ignoring their modules: `get_building`, `get_production_overview` and `get_supply_chain` reported their
  theoretical output 4–5× too low.
- **Modules on no deposit** (crop fields, water and sand harvesters) were reported as `deposit_depleted`:
  `get_building`, `list_buildings` (status `idle`) and `find_production_issues` flagged working buildings.

V1 itself stays frozen; upgrading the server to 1.1.0 (same observer, same configuration) picks up both fixes.

## Known limitations

- **Distances are straight-line estimates**, not road or rail paths; path length is given as a range from the
  player's own routes. Travel time is not estimated.
- **Dispatch timing is not known**: the MCP predicts how much a route would send, not when (the route's
  dispatch frequency is not exported).
- **Company modifiers are measured per type when observable**, not known in general; unowned types assume 1.
- **Rankings are policies, not optimisation.** `find_opportunities` ranks by estimated monthly margin and
  `suggest_research` by demand value per research cost; neither prices the supply chain still to be built.
  Opportunities say so with `caveats` (`needs_new_supply_chain`, `long_payback`); research shows its `chain_fit`.
- **Estimates assume** continuous production, current prices and demand, and inputs valued at market prices;
  forecasts extend short observed trends and report "not available" with fewer than two samples.
- **History freshness**: without `fresh=true`, history-based answers are marked stale between month closes.
- **No control**: the MCP never builds, changes settings, trades, researches or saves; `what_if` only evaluates.
- The game's per-product statistics can be empty; profitability then relies on the ledger and the snapshot.
