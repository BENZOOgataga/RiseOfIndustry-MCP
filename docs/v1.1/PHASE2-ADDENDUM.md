# PRD addendum, phase 2: planning, calculators, spatial analysis, presentation

| Field | Value |
|---|---|
| Extends | [PRD-ADDENDUM.md](PRD-ADDENDUM.md) (V1.1 phase 1) and `PRD.md` (V1) |
| Branch | V1.1 development branch (phase 1 checkpoint before phase 2) |
| Scope rule | Only K (curated knowledge), D (derivations from existing snapshots) and server-local computation. No new observer read, no snapshot-format change, no game write, no network access |
| Status | Implemented server-side; real-game validation pending (§12) |

Every phase-1 rule applies: player company only, the advisor contract (`data.result`, Quantities, assumptions,
confidence, degraded inputs, contradictions, basis), V1 envelope, error and warning codes, 30 KB cap.

---

## 1. Data sufficiency review (quality rule)

Each requested capability was checked against the V1 snapshots before implementation. Field classes:
`observed`, `derived`, `estimate`, `curated` (knowledge base). The Quantity `kind` values map as observed →
`observed`/`game_computed`/`definition`, derived → `derived`, estimate → `estimate`; curated text carries a
`verification` status.

| # | Capability | Available inputs | Decision |
|---|---|---|---|
| 1 | `research_path` | static `tech_unlocks` (`required`, `included`, `tier`, cost/time formula names), static `formulas`, state `research.player` (`unlocked`, `active`, `queue`, `progress`, `active_progress`, `efficiency`, `costs[]` for queued/active/available nodes, `remaining_days`, `remaining_cost`) | **Implemented.** Prerequisites are conjunctive (`requiredUnlocks`, `GetUnlockChain`, CONFIRMED_SOURCE), so no alternative path exists in the game's graph; the tool says so instead of inventing alternatives. Costs and days are the game's own values where exported (`costs[]`); for deeper nodes they are evaluated from the catalogue formula texts at the current efficiency, calibrated against the exported nodes, and labelled estimates; when the formula cannot be evaluated the value is `null` |
| 2 | `loan_calculator` | D-LOAN-1 (CONFIRMED_SOURCE: flat interest, instalment = amount × (1 + apr) / duration × modifier, paid on day 1 of each month after the grace months; early repayment = remaining / duration × amount), static `loan_infos`, state `companies[player].loans`, history ledger | **Implemented to the verified boundary.** The settlement-loan `modifier` is not exported: it is 1 for non-settlement loans and an explicit `parameter` (default 1, flagged) for settlement loans. Interest during grace is not modelled separately (flat total, CONFIRMED). Which calendar month carries the first instalment after a loan is taken is not verified: the schedule is expressed in month offsets (`month_offset` 1 = first month start after taking the loan) with that caveat |
| 3 | `route_calculator` | coordinates (state), `ManualDestinationDispatchCost` and depot formula texts (static), `difficulty.dispatch` (state), existing routes (`distance_tiles`, `dispatch_cost`, `vehicle_capacity`), D-RATE, D-SHOP-1 | **Implemented.** Straight-line distances only (no road or terrain data). An observed detour factor (path tiles / straight-line tiles) from the player's own routes gives a labelled path-length range when at least 3 routes are available. Travel time is **unavailable** (vehicle speeds are not exported) |
| 4 | `compare_options` | as the other tools | **Implemented for six kinds**: `recipes`, `building_types`, `supply_sources`, `shop_destinations`, `research`, `scenarios` (what_if changes). Only metrics that exist for every option of a kind are compared; a metric missing for one option is listed in that option's `unavailable`, never filled |
| 5 | `suggest_research` | as 1 + shops, production balance, market | **Implemented** with the explicit score of §4.5. Nodes whose value cannot be expressed in money (generic unlocks, price discounts without planned builds) are listed as `unscored` with the reason |
| 6 | `spatial_analysis` | building/city/region coordinates (state) | **Implemented** (matrix ≤ 12 locations, hub ranking ≤ 20 candidates, chain geometry ≤ 15 producers). All distances are straight-line tile estimates |
| 7 | `get_chain_graph` | recipes (static), player buildings and routes (state), warehouse requests | **Implemented** as structured graph; Mermaid text optional (`format: "mermaid"`) and deterministic |
| 8 | Language | static `language`, product/recipe/building/tech `display_name` (game language) and `english_name` | **Implemented** for game names (catalogue only, never machine-translated) and for advisor-authored texts (fixed message catalogue en/fr). Curated knowledge summaries remain English (`text_language: "en"`); their titles have authored French versions |
| 9 | Response modes | — | **Implemented** (`detail`: `summary` / `standard` / `full`) on the 10 phase-1 and 8 phase-2 advisor tools |
| 10 | Tool-choice guidance | — | **Implemented**: every advisor description starts with its role tag and says when (not) to use it |
| F | `forecast` | history ledger (monthly), in-memory state window (inventory counts with game days) | **Implemented, limited**: cash exhaustion from the last complete months' net; stock fill/depletion from ≥ 2 window snapshots on different game days. Otherwise explicit `available: false` |

Rejected or reduced: road-network distance (no data); travel time and trips per vehicle per month (no vehicle speed);
regional build costs (V1 limitation); alternative research paths (the graph has none); French text for curated
mechanic summaries (no verified source; authored translation of long technical text deferred); AI research goals
(competitor intelligence deferred).

## 2. Public contract additions (phase 2)

### 2.1 Tools (8)

| Tool | Role | Refresh scope | Paged |
|---|---|---|---|
| `research_path` | planning | `state` | no |
| `suggest_research` | planning | `state` | yes |
| `loan_calculator` | calculator | `state+history` | no |
| `route_calculator` | calculator | `state` | no |
| `compare_options` | comparison | `state+history` | no |
| `spatial_analysis` | spatial (calculator) | `state` | no |
| `get_chain_graph` | structure | `state` | no |
| `forecast` | forecast | `state+history` | no |

Registered order: 29 V1 tools, 10 phase-1 advisor tools, these 8 → **47 tools**.

Phase 1 kept comparison internal; phase 2 exposes exactly one constrained comparison tool, `compare_options`, on
explicit request, limited to the six kinds of §4.4. Other helpers (route maths, research ranking, scoring) stay
internal.

`loan_calculator`, `forecast` and `compare_options` (kind `scenarios`) read history as well as state, so they refresh
both (`state+history`, one request per family; PRD addendum §5).

### 2.2 Common advisor parameters (all 18 advisor tools)

| Parameter | Values | Default | Effect |
|---|---|---|---|
| `detail` | `summary`, `standard`, `full` | `standard` | §3 |
| `language` | `en`, `fr`, `both` | `en` | §5 |

`explain_mechanic` and `how_to` accept both too.

## 3. Response modes

- `standard`: the phase-1 response.
- `summary`: keeps `advisor`, `result` (lists cut to the first 5 entries with `<list>_omitted` counts; `evidence`
  kept only on the first 3 entries of a list, later entries carry `{"omitted_in_summary": true}`), `confidence`, `assumptions` (id and text), `degraded_inputs`,
  `contradictions` (first 5), `unavailable`, `provenance`; empties `observed` and `calculation_inputs`; reduces `basis`
  to `{snapshots: [{family, seq, game_date, age_s, stale}]}`; drops Quantity `source` strings. `data.detail` says
  which mode was applied. Nothing that changes a conclusion is removed: the top-ranked items, all confidence records
  and all warnings stay.
- `full`: `standard` plus tool-specific detail (e.g. the full loan schedule, both distance metrics in a matrix,
  calibration samples). Still bounded by the 30 KB cap.

## 4. Derivations (phase 2)

### 4.1 D-RES-PATH-1: research path

- **Chain.** `chain(target)` = every prerequisite (transitively, `required`) not yet unlocked, in a deterministic
  topological order (depth-first over `required` in catalogue order), followed by the target. `included` unlocks of
  each step are listed (they arrive with it). Unlocked nodes (player set, or `unlocked_by_default`) are `already_unlocked`.
- **Per-node cost and time.** Basis order: (1) `research.player.costs[]` for the node (`game_computed`:
  `daily_cost`, `days`) — and for the active node the observed `remaining_days`/`remaining_cost`; (2) otherwise the
  node's `research_cost_formula` / `research_time_formula` texts evaluated with `tier` and the current research
  `efficiency` (`estimate`, factor `approximate_rate`), multiplied by the calibration factor `k = median(days_game /
  days_formula)` over the nodes of (1) when ≥ 1 such node exists (`calibrated: true`; the factor and its sample are
  returned); (3) otherwise `null` with the reason. Node cost = `daily_cost × days`.
- **Totals.** `total_days = Σ days` and `total_cost = Σ cost` over the remaining nodes; `null` if any node is
  `null` (the known partial sum is returned separately as `known_partial`). Queue position: nodes already queued
  ahead of the chain (observed `queue` order, plus the active node's remaining days) are added to
  `estimated_completion_days` and listed. Research efficiency 0 → time unavailable (the game's formula divides by
  efficiency).
- **Alternatives.** Always `[]` with `alternatives_reason: "prerequisites are conjunctive (requiredUnlocks); the game
  queues every missing prerequisite (GetUnlockChain)"` (evidence: `research/notes/tech-static-content.md` §2).
- **Edge cases.** Target already unlocked → `already_unlocked: true`, empty chain. Cycle in `required` (should not
  exist) → reported in `cycles`, chain truncated. Teaser node → `teaser: true` (not researchable). Unknown node →
  `not_found`.

### 4.2 D-LOAN-SCHED-1: loan schedule

- **Inputs.** Either `loan` (a static `loan_infos` name/title) or explicit `principal` (> 0, ≤ 1e12), `apr` (0–10),
  `duration_months` (1–1200), `grace_months` (0–600), optional `modifier` (0.01–100, default 1).
- **Formulas (CONFIRMED_SOURCE).** `payment = principal × (1 + apr) / duration × modifier`;
  `total_repayment = payment × duration`; `financing_cost = total_repayment − principal`;
  `early_repay_after(n payments) = (duration − n) / duration × principal`.
- **Schedule.** `month_offset = grace + 1 … grace + duration`, constant payment, `remaining_balance_after =
  payment × (duration − i)` (flat). `standard` returns the first 12 rows and the last row; `full` the first 48 and the
  last (`schedule_rows_omitted` counts the rest; the response stays under the size cap without truncation).
- **Cash flow.** With history: last complete month net (observed) → `net_after_payment = net − payment`,
  `payment_share_of_net = payment / net` (null if net ≤ 0), `months_covered_by_cash = cash / payment` (cash from state).
  Without history: those fields `null`, `degraded_inputs` names history.
- **Existing loans** (`existing: true`): for each player loan, the observed `remaining_payments`,
  `early_repay_amount`, and the D-LOAN-1 payment; no new values are invented for missing fields.
- **Edge cases.** `apr = 0` → financing cost 0. Duration 1 → one payment. Zero/negative principal or duration →
  `invalid_argument`.

### 4.3 D-ROUTE-CALC-1: hypothetical route

- **Distance.** Straight-line Euclidean and Chebyshev tiles (D-DIST-1), `kind: derived`, labelled
  `distance_kind: "straight_line"`, `is_path_distance: false`.
- **Existing route.** If a player route origin→destination exists for the product (or any product), its
  `distance_tiles`, `dispatch_cost`, `vehicle_capacity` are returned as `game_computed`/`observed` with
  `authoritative: true` and the estimate below is computed for comparison only.
- **Detour factor.** `f_i = distance_tiles_i / euclidean_i` over the player's routes with a cached path and
  euclidean ≥ 5 tiles; with ≥ 3 samples: `path_tiles_range = [euclid × min f, euclid × max f]`, median also returned
  (`estimate`, method `observed detour factor`, sample size). Fewer samples → `null`.
- **Cost per trip.** The source's formula (`own` → `ManualDestinationDispatchCost`; `TruckDepot` →
  `TruckDepotDispatchCost`; `TrainTerminal` → `TrainTerminalDispatchCost`, from static `formulas`; the
  depot-to-formula mapping is HIGH_CONFIDENCE → factor `mechanic_unverified`) evaluated with `distance` = straight-line
  Euclidean tiles (and, when available, the detour range), `difficulty = session.difficulty.dispatch`, `actor = 1`
  (A-ACTOR-MODIFIER-1). Range when the detour range exists.
- **Capacity and cost per unit.** Vehicle capacity: `vehicle_capacity` parameter, else the median observed capacity
  of the player's routes of the same source and product, else of the same source; else `null`. `cost_per_unit =
  cost_per_trip / capacity` (A-FULL-VEHICLES).
- **Throughput constraints (derivable only).** `origin_supply_per_30d` (D-RATE-1/2 of the origin for the product),
  `origin_spare_per_30d`, `destination_demand_per_30d` (D-SHOP-1 for a shop; D-RATE-1/2 need for a consumer
  building), `flow_cap_per_30d = min` of the known ones; `trips_per_30d = flow / capacity` (ceil), `transport_cost_per_30d =
  trips × cost_per_trip`. Travel time and vehicle turnover: `unavailable` (vehicle speed not exported).

### 4.4 D-CMP-1: comparison

Common structure: `kind`, `metrics[]` {id, label, unit, better (`higher`|`lower`|`none`)}, `options[]` {id, label,
values {metric: Quantity|null}, unavailable[]}, `differences[]` {metric, best_option, worst_option, spread
(derived), ranking[]}, `assumptions`, `confidence`. A metric appears in `differences` only when ≥ 2 options have a
value. Metrics per kind:

| kind | options | metrics |
|---|---|---|
| `recipes` | 2–8 recipes (must share a result product, else `invalid_argument`) | output_per_30d_per_building (product), input_cost_per_unit (market), upkeep_per_unit (new building), unit_cost, game_days, locked |
| `building_types` | 2–8 types | build_cost (player), upkeep_per_30d (new), output_per_30d for a given recipe (if given and runnable), storage_slots, max_modules (catalogue), max_modules_player (observed `modules.max`, used for new hubs when the player's buildings agree), locked |
| `supply_sources` | 2–8 player buildings producing `product`, toward `destination` | straight_line_tiles, existing_route (bool), dispatch_cost or estimate per trip, spare_supply_per_30d, stock |
| `shop_destinations` | 2–8 shops, from `origin`, `product` | price_for_player, unmet_demand_per_30d, straight_line_tiles, cost_per_unit (existing route or estimate), net_price_per_unit (price − cost per unit) |
| `research` | 2–8 tech nodes | total_cost, total_days (D-RES-PATH-1), suggest score components (D-RES-SCORE-1) |
| `scenarios` | 2–5 `what_if` changes | capex, upkeep_delta_per_30d, supply delta of `product`, balance_after of `product` |

### 4.5 D-RES-SCORE-1: research suggestion score

For each candidate node (available now: not unlocked, not teaser, every prerequisite unlocked; with
`include_reachable: true` also nodes whose chain length ≤ `max_chain`):

- `unlocked_products` = results of the recipes the node (and its `included` unlocks) unlock, plus products of
  recipes runnable on the building types it unlocks.
- `demand_value_per_30d = Σ_p unmet_shop_demand_per_30d(p) × sale_price(p)` (D-SHOP-1 × D-ADV-PRICE-1; estimate).
- `bottleneck_value_per_30d = Σ_p max(internal_need(p) − supply(p), 0) × input_value(p)` (deficits the node can
  produce; estimate).
- `chain_fit = (ingredients of the unlocked recipes the player already produces) / (all those ingredients)`, 1 when
  no ingredients (raw), `null` when no recipe.
- `research_cost` = D-RES-PATH-1 `total_cost` of its chain; `research_days` likewise.
- **Score** = `(demand_value + bottleneck_value) × (0.5 + 0.5 × chain_fit) / research_cost × 30` — monthly value
  per unit of research money, expressed per 30 days (dimension: 1/30d × 30 = ratio); equivalently
  `1 / payback_months` scaled by fit. Nodes with value 0 have score 0; nodes with `research_cost` null or value null
  are `unscored` (reason listed).
- **Rank.** Score descending, then research_days ascending, then id. Every component is returned.

### 4.6 D-SPATIAL-1: matrix, hub, chain geometry

- Locations: building, shop, city (centre), region (centre) ids; ≤ 12 for `matrix`, ≤ 20 candidates and ≤ 30
  weighted points for `hub`, ≤ 15 producers for `chain`. Duplicates are removed (first occurrence kept, listed);
  unknown ids → `not_found`; locations without coordinates → `unavailable`.
- `matrix`: upper triangle of Euclidean distances (`full`: plus Chebyshev), deterministic order = input order.
- `hub`: weighted sum `S(c) = Σ_i w_i × euclid(c, p_i)` for each candidate (default weight 1; `weights` per point
  ≥ 0); ranked ascending; plus the weighted geometric median (Weiszfeld, ≤ 500 iterations, tolerance 1e-4 tiles)
  as an unconstrained reference point (`kind: derived`, not a buildable location).
- `chain` (for a product): each player producer → the nearest (straight-line) player producer of each of its
  ingredients and → its configured route destinations or the nearest shop with demand for the player;
  leg distances and totals per producer.
- Every distance: `distance_kind: "straight_line"`, `is_path_distance: false`.

### 4.7 D-GRAPH-1: chain graph

Nodes: `product`, `recipe` (catalogue), `building` (player). Edges with `basis`:
`catalogue` (recipe consumes/produces product: definition), `observed` (a building runs a recipe; a configured route
building→building for a product; an AUTO_WH link; a warehouse request), `hypothetical` (a recipe link where the player
has no producer: catalogue-only path the player could build). Depth ≤ 8 upstream from the product (and 1 downstream),
≤ 150 nodes and ≤ 300 edges (else `truncated` with counts). Node and edge ordering is sorted by id. `format: "mermaid"`
adds a `mermaid` string (`graph LR`, ids sanitised to `n<index>`, labels in the requested language; observed edges
solid, catalogue dotted, hypothetical dashed); no image is rendered.

### 4.8 D-FORECAST-1: forecasts

- **Cash.** Sample: the last `months` (2–12, default 3) complete ledger months. `net_i` observed. If every
  `net_i ≥ 0` → `exhaustion: "not_projected"` (cash not decreasing). Else `months_to_zero_range =
  [cash / −min(net_i), cash / −mean(net_i)]` using only negative nets for the bound (`estimate`, A-TREND-PERSISTS),
  `null` for the optimistic bound when the mean is ≥ 0. Fewer than 2 complete months, or no history → `available:
  false`. Cash ≤ 0 → `already_negative: true`.
- **Stock.** For `building` + `product`: the in-memory window samples `(game_day, count)` of the current world
  session with distinct game days (≥ 2, else `available: false`). Rate per game day: overall `(c_last −
  c_first)/(d_last − d_first)` and the min/max over consecutive pairs → range. Time to full = `(slots − count) /
  rate` for rate > 0; time to empty = `count / −rate` for rate < 0; rate 0 → `stable`. Units: game days.
- Assumption A-TREND-PERSISTS: the recent observed trend continues; confidence `low` with fewer than 4 samples,
  `medium` otherwise.

### 4.9 Assumption ids added

| Id | Text |
|---|---|
| A-TREND-PERSISTS | The recent observed trend (sample listed) continues unchanged |
| A-RESEARCH-FORMULA | Research cost and time of nodes the game has not costed come from the catalogue formulas at the current efficiency, scaled by the calibration factor from costed nodes |
| A-STRAIGHT-LINE | Distances are straight-line tile distances, not road or rail path lengths |
| A-LOAN-MODIFIER | The loan payment modifier is 1 (settlement loan modifier not exported) unless given |
| A-SCORE-WEIGHTS | Research score weights: demand and bottleneck value in money per 30 days, chain fit weight 0.5, divided by research cost |

## 5. Language

- `en`: names = `english_name`, falling back to the asset name (`name_source: "asset"`).
- `fr`: names = `display_name` when the catalogue's `language` is French (`name_source: "catalogue_fr"`); otherwise
  the English name with `name_source: "fallback_en"` (no machine translation). Advisor-authored strings (summaries,
  suggestions, notes, side effects) come from a fixed message catalogue with French versions.
- `both`: `name` (en) and `name_fr`; `summary` (en) and `summary_fr` (likewise for suggestions/notes).
- Ids, enum values, units, assumption ids and numbers never change. Curated knowledge: titles en/fr, summaries and
  formulas English only (`text_language: "en"`).

## 6. Tool-choice guidance

Advisor descriptions start with a role tag:
`[OVERVIEW]`, `[DIAGNOSIS]`, `[PLANNING]`, `[SIMULATION]`, `[COMPARISON]`, `[CALCULATOR]`, `[SPATIAL]`, `[STRUCTURE]`,
`[FORECAST]`, `[CHANGES]`, `[MECHANICS]`, followed by "Use for …" and "Not for: …" sentences that point to the
neighbouring tool. Assignment: OVERVIEW get_overview; DIAGNOSIS diagnose_chain, get_profitability, review_routes;
PLANNING find_opportunities, plan_chain, research_path, suggest_research; SIMULATION what_if; COMPARISON
compare_options; CALCULATOR loan_calculator, route_calculator; SPATIAL spatial_analysis; STRUCTURE get_chain_graph;
FORECAST forecast; CHANGES what_changed; MECHANICS explain_mechanic, how_to. V1 tools remain the factual layer and keep their descriptions.

## 7–11. Testing, non-regression, compatibility

As phase 1 (addendum §8–§11), plus for calculators: zero, extreme valid, division-by-zero, missing, negative, rounding,
units; for graph/spatial: cycles, disconnected graphs, duplicates, unknown ids, bounded large inputs, deterministic
ordering; localization and detail-mode tests.

## 11a. Response sizes (large synthetic fixture: 455 player buildings, 727 routes, 2,000 AI buildings, 80 shops; `language: both`)

Measured by `tests/advisor/test_window_knowledge.py` (release candidate). No advisor answer needs the generic
truncation backstop (asserted): paged tools shorten their own pages and move `page.next_cursor`
(`page_bounded_by_size: true`, no row lost across pages, asserted); `diagnose_chain` and `get_chain_graph` reduce
their own lists coherently.

| Tool | full | standard | summary | worst case (bytes) |
|---|---|---|---|---|
| get_overview | 15,471 | 15,475 | 10,230 | |
| diagnose_chain | 24,918 | 24,922 | 16,085 | |
| get_profitability (limit 25) | 15,697 | 15,701 | 12,156 | |
| find_opportunities (limit 50) | 12,848 | 12,852 | 10,886 | |
| review_routes (limit 50) | 26,151 | 26,155 | 8,420 | |
| plan_chain | 12,874 | 12,878 | 10,712 | |
| what_if | 6,028 | 6,032 | 5,085 | |
| what_changed | 5,437 | 5,441 | 4,229 | |
| explain_mechanic | 6,298 | 6,302 | 6,301 | |
| how_to | 2,065 | 2,069 | 2,068 | |
| research_path | 5,568 | 5,572 | 4,454 | 5,568 (full path) |
| suggest_research (limit 50) | 9,979 | 9,983 | 9,067 | |
| loan_calculator | 23,856 | 11,188 | 6,751 | 24,986 (1,200 months + existing) |
| route_calculator | 6,204 | 6,208 | 5,537 | |
| compare_options (8 shops) | 12,726 | 12,730 | 9,469 | |
| spatial_analysis (hub 30/20) | 6,753 | 6,757 | 3,476 | 20,586 (matrix, 12 locations, full) |
| get_chain_graph (depth 8, Mermaid) | 24,015 | 24,019 | 9,042 | 24,015 |
| forecast | 4,308 | 4,312 | 3,245 | |

Summary is 10–68 % smaller where the answer has lists; the two knowledge tools are already small and gain ~0 %.

Release review (after the live-validation fixes, `full`): get_overview 15,900 (+429, deficit note), find_opportunities
13,265 (+417, `caveats`), plan_chain 13,112 (+238), what_if 7,013 (+985, `shared_destination` /
`effective_output_multiplier`), route_calculator 6,556 (+352); every other tool unchanged. The largest answer is
still review_routes at 26,155 bytes (cap 30,000).

## 11b. Latency (same fixture, 30 calls after one warm-up; `tests/perf/run_advisor_perf.py`)

Median / p95 / max ms: get_overview 9.4 / 11.3 / 17.8; diagnose_chain 14.2 / 15.4 / 21.7; get_profitability 8.6 / 9.3 /
15.8; find_opportunities 8.3 / 12.2 / 14.3; review_routes 18.6 / 24.0 / 41.8; plan_chain 6.5 / 7.6 / 18.2; what_if
(all change types) 5.3–5.8 / ≤ 9.2 / ≤ 20.6; what_changed 6.7 / 9.2 / 9.4; explain_mechanic 9.6 / 9.9 / 12.0;
how_to 2.5 / 2.7 / 2.9; research_path 1.8 / 1.9 / 2.5; suggest_research 6.9 / 7.2 / 9.5; loan_calculator 2.0 / 2.2 /
2.6; route_calculator 8.9 / 10.5 / 16.2; compare_options 6.2 / 9.4 / 13.9; spatial_analysis 2.5 / 2.6 / 2.6;
get_chain_graph 10.4 / 10.7 / 18.7; forecast 1.8 / 1.9 / 2.1. The first call of a session parses and validates the
large state file (~1.1 s, V1 behaviour). No quadratic behaviour was found; no optimisation was needed.

## 12. Real-game validation needed after T-12

- V12-1 research costs/times from formulas vs the game for non-available nodes (compare after unlocking prerequisites).
- V12-2 loan first-instalment month and settlement loan modifier.
- V12-3 detour-factor ranges vs real path tiles for new routes; depot formula mapping (U-PAY).
- V12-4 French display names on a French install (catalogue `language`), and French message rendering.
- V12-5 forecast plausibility (cash, stock) over a live session.
