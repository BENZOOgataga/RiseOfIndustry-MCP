# PRD addendum: V1.1 advisor layer

| Field | Value |
|---|---|
| Document | Contract addendum for V1.1 (extends `PRD.md`, the V1 contract) |
| Base | Frozen V1 server revision `8e30117` |
| Status | Implemented and released in 1.1.0; live semantic validation recorded in `LIVE-VALIDATION-RESULTS.md` |
| Date | 2026-10-04 |
| Target game | Rise of Industry (original, Steam App 671440), baseline build of PRD §3.1 only |

Requirement keywords as in `PRD.md`. Where this addendum and `PRD.md` differ, `PRD.md` wins for every V1 tool and
for every hard constraint (§2 of `PRD.md`); this addendum only adds.

---

## 1. Purpose and boundaries

V1 is a read-only observation layer. V1.1 adds an **advisor layer** on the server: ten tools that combine V1 data into
diagnoses, estimates and ranked candidates, plus curated game knowledge served as MCP resources.

V1.1 changes nothing on the game side:

- no new observer read, no change to the snapshot format (`schemas/{heartbeat,static,state,history}.schema.json`
  are unchanged), no new file in the exchange directory, no game write, no network access;
- the only file the server writes is still `refresh-request.json` (PRD §11.6), with the per-tool scopes of §5.

V1.1 deliberately relaxes one V1 non-goal, PRD §4.2 "recommendations embedded in tools", and only for the ten
advisor tools. Every V1 tool keeps its V1 contract unchanged. Advisor output is advisory: it never claims more than
its inputs support (§3), and the AI client stays responsible for the decision.

Deferred (not in V1.1): persistent notebook, goals, persistent plans, decision review, history sampler or database,
session recap, persistent alerts or watch rules, maps and charts, offline-save mode, save portfolio, competitor
intelligence, any new game-side read, any game-side write. Advisor tools therefore analyse **the player company
only**; AI companies appear only as V1 already exposes them.

---

## 2. Public contract additions

### 2.1 Tools (10)

| Tool | Purpose | Refresh scope | Families read |
|---|---|---|---|
| `get_overview` | One-call company dashboard: cash, finances, production balance, issue counts, attention items | `state+history` | state (required), history (optional), static (optional) |
| `diagnose_chain` | Bottleneck diagnosis of the player's production chain for one product | `state` | state, static (required for recipes) |
| `get_profitability` | Per-product unit economics: the game's own product stats plus a labelled cost/margin estimate | `state+history` | history (optional), state (required), static (required) |
| `find_opportunities` | Ranked candidates: unmet shop demand, expansion, new products, contract offers | `state` | state, static |
| `review_routes` | Logistics findings over the player's configured routes | `state` | state, static (optional) |
| `plan_chain` | Buildings, capex and upkeep needed to reach a target output per month | `state` | state, static |
| `what_if` | Scenario evaluation of one hypothetical change, computed on copies; never applied | `state+history` | state, static, history (optional: `take_loan`) |
| `what_changed` | Changes since an earlier in-memory snapshot of this world session, and month over month from game-retained history | `state+history` | state (required), history (optional) |
| `explain_mechanic` | Curated, evidence-backed explanation of a game mechanic | `none` | knowledge base only |
| `how_to` | How to do something (in the game UI, or with this MCP) | `none` | knowledge base only |

Scope `state+history` (live validation V1.1): the observer re-verifies `history.json` only on request, so a
single-family refresh left the other family stale and the advisor answer at confidence low. Tools that read both
families write one refresh request per family, state first, then history (about 4 s with `fresh=true` live).
Each family uses the V1 mechanism unchanged: concurrent calls share one outstanding nonce per family, each family
waits at most `refresh_wait_s` (so a combined call waits at most 2 × `refresh_wait_s`), and a family that is not
served within its wait is answered from its latest snapshot with warning `refresh_timeout` and its own age/stale
flags. Without `fresh`, history older than the currency limit is reported `stale: age` and lowers confidence
(`stale_data`): the observer re-verifies history only on request or when a month closes.
`diagnose_chain`, `find_opportunities`, `review_routes` and `plan_chain` read state and static only.

The registered tool list is the 29 V1 tools in PRD §14 order followed by these ten in the order above (39 tools).

### 2.2 Resources

| URI | Content |
|---|---|
| `roi://knowledge/mechanics` | All curated mechanics (index + records) |
| `roi://knowledge/mechanics/<id>` | One mechanic |
| `roi://knowledge/glossary` | French/English glossary of game terms |
| `roi://knowledge/pitfalls` | Known misunderstandings and their corrections |
| `roi://knowledge/how-to` | Curated how-to entries |

All resources are `application/json`, read-only, built into the server package
(`mcp-server/src/roi_mcp/knowledge/*.json`) and independent of the game state.

### 2.3 Unchanged

The response envelope (PRD §13.3: `ok`, `meta`, `data`, `page`), error codes and warning codes are unchanged; no new
code is added to either closed list. Advisor-specific degradation is reported inside `data` (§3). Common parameters
`fresh` and `allow_stale` (PRD §13.2) apply to every advisor tool whose scope is not `none`.

---

## 3. Advisor contract

### 3.1 Data shape

Every advisor response (`ok: true`) has these `data` keys in addition to the V1 `provenance` and `unavailable`:

| Key | Content |
|---|---|
| `advisor` | `{contract_version: "1.1.0", tool, player_only: true, read_only: true}` |
| `result` | The tool's answer (recommendations, findings, plan, scenario, …), tool-specific (§6) |
| `observed` | Observed facts the result relies on (values read from snapshots; Quantity objects, §3.2) |
| `assumptions` | `[{id, text}]` every assumption the calculations make; ids are stable (§4.3) |
| `calculation_inputs` | Inputs of the calculations that are not already in `observed` (definitions, parameters, thresholds) |
| `confidence` | Overall `{level, factors[]}` (§3.4) |
| `degraded_inputs` | `[{input, reason, effect}]`: inputs that were unavailable, stale, contradictory or approximated (§3.5) |
| `contradictions` | `[{kind, subject, values, effect}]`: observed values that disagree with each other (§3.6) |
| `basis` | Snapshot identity, freshness and cross-family consistency (§3.3) |

### 3.2 Machine-distinguishable values (Quantity)

Every number an advisor tool reports in `result`, `observed` or `calculation_inputs` is either

- a **Quantity** object `{value, unit, kind, method?, source?, confidence?}`, where `kind` ∈
  `observed` (read from `state`/`history`; carries `source`), `definition` (static catalogue; `source`),
  `game_computed` (an allowlisted game call, as in V1; `source`), `derived` (an exact, deterministic function of
  observed/definition values with no modelling assumption, e.g. D-FIN-1, D-LOAN-1, a ratio; carries `method`),
  `estimate` (relies on at least one modelling assumption; carries `method` and `confidence`), `parameter` (a caller
  argument or a documented constant); or
- a plain count, rank, id or enum (integers that are not measurements, e.g. `rank`, `count` of findings); or
- a raw number inside an `evidence` object, which holds only values copied unchanged from the snapshots (state,
  history or static); anything the advisor computes inside evidence is again a Quantity. `what_if` echoes its
  `change` argument unchanged.

The test suite walks every advisor response and fails on any number outside these three forms.

`value: null` means "not computable": the reason is in `degraded_inputs` and/or `unavailable`. A missing value is
never replaced by 0, by a default, or by a guess. `unit` strings are fixed: `money`, `money/30d`, `money/unit`,
`units`, `units/30d`, `days`, `months`, `tiles`, `ratio`, `count`, `percent`.

"Per month" means per 30 game days: the game's calendar has 30-day months (PRD §11.2), so `per_month` ≡
`per_30d`.

### 3.3 Snapshot basis (pinned, consistent)

`basis` = `{snapshots: [{family, seq, content_hash, world_session, game_date, captured_utc, age_s, stale,
stale_reason}], consistency: {same_world_session, static_ref_matches, state_consistent, history_same_session},
freshness: {state_age_s, history_age_s, history_game_date, state_game_date}}`.

Rules (internal infrastructure, not a public tool):

1. **Pinning.** Within one call, each family is loaded once; every calculation of that call reads the same
   snapshot object. A second request for a family inside the same call returns the pinned snapshot (also for V1
   tools).
2. **Same world session.** History is combined with state only when both have the same `world_session` and
   `pid`. Otherwise the history part is dropped and listed in `degraded_inputs` (`history_world_session_mismatch`)
   and `unavailable`.
3. **Static reference.** When `state.static_ref` does not match the pinned static catalogue (after the V1 reload
   rule, PRD §11.4), definitions are still used and the confidence factor `static_mismatch` applies.
4. **Within-capture consistency.** `captured.consistent: false` (sections read on different game days) applies the
   factor `inconsistent_snapshot`.

Public exposure is not needed: advisor answers are computed within one call, and `what_changed` refers to earlier
snapshots by the `seq` already published in `meta.snapshot` (§6.8).

### 3.4 Confidence

`confidence` = `{level, factors[]}`, `level` ∈ `high` | `medium` | `low`. Computed by D-ADV-CONF-1:

| Start level | When |
|---|---|
| `high` | The value is observed, a definition, or a deterministic formula CONFIRMED in the game source applied to observed inputs |
| `medium` | The value is an estimate that needs at least one documented modelling assumption (§4.3) |

Each factor lowers the level by one step (`high → medium → low`; floor `low`):

| Factor | Trigger |
|---|---|
| `stale_data` | A state or history snapshot used is stale (`meta.stale`) |
| `inconsistent_snapshot` | `captured.consistent: false` |
| `static_mismatch` | §3.3 rule 3 |
| `approximate_rate` | A D-RATE-2 rate fell back to the approximate formula, or a cycle time came from the static recipe instead of the building (§4.1 D-ADV-NEWRATE-1) |
| `price_fallback` | A price came from a fallback basis (§4.1 D-ADV-PRICE-1) |
| `mechanic_unverified` | A formula used is not CONFIRMED (HIGH CONFIDENCE or INFERRED in research) |
| `contradictory_inputs` | A contradiction (§3.6) touches an input of the value |
| `partial_inputs` | Some optional inputs were unavailable and the value covers only part of the subject |

Factors are listed even when the level is already `low`. The overall `confidence` of a response is the minimum of
its results' levels, with the union of their factors.

### 3.5 Degraded and missing inputs

`degraded_inputs[]` entries: `{input, reason, effect}` with `reason` ∈ `section_unavailable`, `family_unavailable`,
`stale`, `history_world_session_mismatch`, `static_mismatch`, `inconsistent_snapshot`, `missing_value`,
`approximated`, `contradictory`. `effect` states what the advisor did (`omitted`, `value_null`,
`confidence_lowered`, `fallback_used:<basis>`).

A section the answer cannot do without raises the V1 error `section_unavailable` (PRD §13.3). Optional sections
(e.g. `shops`, `routes_player` for `get_overview`) degrade: the parts that need them are omitted or null, the
section is in `unavailable` (V1 rule) and in `degraded_inputs`.

### 3.6 Contradictions

Detected by D-ADV-CONTRA-1 (§4.1) and reported, never silently resolved:

| Kind | Rule |
|---|---|
| `route_origin_stock_mismatch` | A route's `origin_stock` ≠ the origin building's inventory `count` for that product in the same snapshot |
| `route_destination_stock_mismatch` | A route's `destination_stock` ≠ the destination building's inventory `count` |
| `ledger_balance_mismatch` | `history.ledger_player.balance_now` ≠ `state.companies[player].cash.value` while both snapshots are of the same game day |
| `game_stats_vs_estimate` | `get_profitability`: the game's own profit per unit and the advisor estimate differ in sign, or by more than 50 % of the larger magnitude |

The observed values are both reported; calculations use the building's own value (the route copy is a convenience
field), and the affected results carry `contradictory_inputs`.

### 3.7 Size

Every advisor response respects the V1 cap (PRD §14.9, ~30 KB). Tools bound their own output (`limit`, top-N
lists, §6); the V1 truncation machinery remains the backstop. `review_routes` and `find_opportunities` are paged
(`limit` 1–50 default 25, `cursor`), like V1 paged tools.

### 3.8 Read-only statements

Advisor descriptions state that the tool is read-only, that recommendations are advisory, and that `what_if` and
`plan_chain` compute on copies and never change the game. Recommendations are phrased as player actions
("set Max Send on …"), never as something the MCP will do.

---

## 4. Derived calculations

Each derivation has an id used in `method`, unit tests with hand-computed values, and the specification below. V1
derivations (PRD §15: D-RATE-1/2/3, D-REQ-1, D-INV-1/2, D-SHOP-1, D-SUPDEM-1, D-ROUTE-1/2, D-VAL-1, D-PERMIT-1,
D-GROW-1, D-STATUS-1, D-LOAN-1, D-FIN-1, D-DIST-1) are reused unchanged.

### 4.1 Definitions

#### D-ADV-PRICE-1: unit sale price basis

- **Formula.** For product P: the median of `price_for_player` over live (not `is_dead`) shops that accept P and
  have `demand_for_player > 0` (basis `shop_median`); else the market `final_price_for_player` (basis
  `market_final`); else the market `price` (basis `market_price`); else `null`.
- **Units.** money/unit.
- **Inputs.** `state.shops` (optional), `state.market` (optional).
- **Unavailable.** Both sections missing or P absent from both: `value: null`, `degraded_inputs`
  `missing_value`.
- **Assumptions.** A-PRICE-SHOP (the player sells at shop prices; a delivery-weighted price is not exported).
- **Confidence.** `high` for `shop_median` (observed); `market_*` adds `price_fallback`.
- **Edge cases.** Even number of shops: mean of the two middle prices. Prices ≤ 0 are ignored.

#### D-ADV-INPUTVAL-1: input valuation

- **Formula.** Value of one unit of an ingredient I = D-ADV-PRICE-1 market basis only:
  `final_price_for_player`, else `price` (opportunity cost: what the unit would cost or fetch on the market).
- **Units.** money/unit. **Inputs.** `state.market`.
- **Unavailable.** `null` → the unit cost becomes `null` (never a partial sum presented as complete).
- **Assumptions.** A-INPUT-MARKET.
- **Confidence.** `medium` (assumption-based).

#### D-ADV-UPKEEPSHARE-1: upkeep per unit at existing producers

- **Formula.** For product P and the set B of the player's enabled producers of P:
  `upkeep_per_unit = Σ_b upkeep_b × share_b(P) / Σ_b output_b(P)`, where `upkeep_b` is the observed
  `upkeep.monthly_active` (fallback `monthly_full`, factor `approximated`), `output_b(P)` is D-RATE-1/2 per 30 days,
  and `share_b(P)` = 1 for single-output recipes, else P's share of the recipe's result amounts (quantity share).
- **Units.** money/unit (upkeep is charged per 30-day month; output per 30 days).
- **Unavailable.** No enabled producer with a known rate → `null`. A producer without an upkeep value is excluded
  from both sums (`partial_inputs`).
- **Assumptions.** A-CONTINUOUS (producers run at their theoretical rate), A-UPKEEP-ACTIVE.
- **Confidence.** `medium`; `approximate_rate` when a D-RATE-2 fallback was used.
- **Edge cases.** Σ output = 0 → `null`.

#### D-ADV-UNITCOST-1: production cost per unit

- **Formula.** `unit_cost(P) = upkeep_per_unit(P) + Σ_i (amount_i / result_amount_P) × value_i`, i over the recipe
  ingredients (D-ADV-INPUTVAL-1). Two bases (parameter `input_cost_basis`):
  `market_value` (default) values inputs at market; `own_cost` values an input the player produces at its own
  `unit_cost` (recursive, depth ≤ 6, cycle guard → `market_value` for the repeated product, factor
  `approximated`).
- **Units.** money/unit. **Inputs.** recipes (static), producers (state), market (state).
- **Unavailable.** Any term `null` → `unit_cost: null` with the missing term named.
- **Assumptions.** A-CONTINUOUS, A-INPUT-MARKET or A-INPUT-OWN, A-NO-TRANSPORT-IN-UNITCOST.
- **Confidence.** `medium`, lowered by the factors of its terms.
- **Edge cases.** Raw products (no ingredients): unit cost = upkeep per unit.

#### D-ADV-DISTCOST-1: distribution cost per unit

- **Formula.** Mean of D-ROUTE-1 `cost_per_unit_at_capacity` over the player's non-errored, non-dormant routes
  carrying P from a producer of P (`dispatch_cost / vehicle_capacity`).
- **Units.** money/unit. **Inputs.** `state.routes_player`.
- **Unavailable.** No such route → `null` ("no configured outbound route"), not 0.
- **Assumptions.** A-FULL-VEHICLES (cost per unit at capacity).
- **Confidence.** `medium` (`dispatch_cost` is game-computed per dispatch; per-unit needs the full-vehicle
  assumption).

#### D-ADV-MARGIN-1: margin per unit and per month

- **Formula.** `margin_per_unit = sale_price − unit_cost − distribution_cost` (distribution term omitted, and named
  in `assumptions`, when `null`); `margin_per_30d = margin_per_unit × volume`, volume = observed
  `produced_last_month` summed over producers (basis `produced_last_month`), else theoretical output (basis
  `theoretical`, factor `approximated`).
- **Units.** money/unit, money/30d.
- **Unavailable.** Price or unit cost `null` → margin `null`.
- **Confidence.** min of the inputs' levels.

#### D-ADV-NEWRATE-1: output of one new building

- **Formula.** For recipe r on building type T: when the player already runs r on T, the median observed
  per-building D-RATE-1/2 rate of those enabled buildings whose rate is not approximate (basis `observed_peer`, level
  `high`; module owners per module × `max_module_count`). Else
  `rate = Σ result_amount × 30 / (game_days / (production_speed × eff_out[initial_index]))`, where
  `production_speed` and `eff_out`/`initial_efficiency_index` are the static building-type values (basis
  `static_recipe`, factor `approximate_rate`). Gatherers and farms: × `max_module_count` modules at speed 1.
- **Units.** units/30d per building.
- **Unavailable.** `game_days` ≤ 0 or missing → `null`.
- **Assumptions.** A-NEW-DEFAULT-EFFICIENCY, A-ACTOR-MODIFIER-1, A-FULL-DEPOSITS (gatherers), A-CONTINUOUS.
- **Edge cases.** `production_speed` missing → 1 with factor `approximated`; `max_module_count` missing → 1 module.

#### D-ADV-NEWUPKEEP-1: monthly upkeep of one new building

- **Formula.** `base = base_cost × upkeep_cost_percentage + modules × (module_base_cost × module_pct)`;
  `upkeep = max(base × difficulty.upkeep × eff_upk[initial_index], base_cost × upkeep_cost_percentage × min_upkeep)`.
- **Units.** money/30d. **Inputs.** static building types, `state.session.difficulty.upkeep`.
- **Source.** `research/notes/buildings-production.md` §2.5 (CONFIRMED components; the composition of module upkeep
  with modifiers is INFERRED → factor `mechanic_unverified`).
- **Assumptions.** A-ACTOR-MODIFIER-1, A-NEW-DEFAULT-EFFICIENCY.

#### D-ADV-CAPEX-1: build cost of one new building

- **Formula.** `current_player_cost(T)` from `state.research.player.building_costs` (as V1 `get_building_type`),
  else `base_cost(T)` (factor `price_fallback`); plus `max_module_count × cost(module type)` for module owners.
- **Units.** money. **Assumptions.** A-NO-REGIONAL-COST (regional modifiers not applied, V1 limitation).

#### D-ADV-PLAN-1: chain plan for a target output

- **Formula.** `plan(P, amount)`: `use = min(spare(P), amount)`, `spare(P)` decremented; `new = amount − use`;
  if `new > 0`: `buildings = ceil(new / rate_new(P))` (D-ADV-NEWRATE-1); for each ingredient I of the chosen recipe:
  `plan(I, new × amount_I / result_amount_P)`. `spare(P) = max(supply(P) − internal_need(P), 0)` over the player's
  enabled buildings (D-RATE-1/2) when `existing_capacity: "spare"` (default), 0 when `"none"`.
  Totals: Σ buildings, Σ capex (D-ADV-CAPEX-1), Σ upkeep (D-ADV-NEWUPKEEP-1), raw-input needs.
- **Recipe choice.** `recipe_choice[P]`, else the recipe used by the player's producers of P, else the first
  recipe unlocked for the player, else the first recipe (flagged `locked`).
- **Units.** units/30d, count, money, money/30d.
- **Unavailable.** A product with no recipe and no producer → `unplannable` step with reason; the totals then carry
  `complete: false`.
- **Edge cases.** Cycles: the repeated product is not expanded again (`cycle` step). Depth > 12: `truncated` step.
  Target ≤ 0 or non-finite: `invalid_argument`.
- **Assumptions.** A-CONTINUOUS, A-SPARE-AVAILABLE, plus the D-ADV-NEWRATE/NEWUPKEEP/CAPEX assumptions. Logistics
  (routes, vehicles) are not planned: listed as A-NO-LOGISTICS.

#### D-ADV-DISPATCH-1: dispatch amount replica (server side)

- **Formula.** PRD §12.3.3 replica over a route's exported `dispatch_amount_now.inputs` with one input changed:
  `available = max(origin_stock − min_keep, 0)` (0 if keep-all); `free = free_space`; if `max_send > 0`:
  `free = min(free, max(max_send − (destination_stock + destination_incoming_reserved), 0))`; contract room as
  exported; `amount = min(cap, available, free)`; `wait_for_full_vehicle` and `amount < cap` → 0.
- **Units.** units per dispatch.
- **Unavailable.** Any of cap / origin_stock / free_space `null` → `null`. A route whose exported result is
  `complete: false` (world event) stays `complete: false`.
- **Confidence.** `high` (replica of CONFIRMED source, applied to observed inputs) — this is a "what the next
  dispatch would request", not throughput (A-DISPATCH-NOT-THROUGHPUT).

#### D-ADV-EFF-1: efficiency change

- **Formula.** For a building at index a moved to index b (type arrays `efficiency_output`, `efficiency_upkeep`):
  `rate_b = rate_a × eff_out[b] / eff_out[a]`;
  `upkeep_b = max(upkeep_a × eff_upk[b] / eff_upk[a], base_cost × pct × min_upkeep)`; when `eff_upk[a] = 0`:
  `upkeep_b = max(base_cost × pct × difficulty.upkeep × eff_upk[b], floor)` (factor `mechanic_unverified`).
- **Source.** `GetFinalProductionSpeed = productionSpeed × actorRecipeTimeModifier × efficiency`,
  `efficiency = eff_out[idx] × actor modifier`, upkeep modifier = `eff_upk[idx]`
  (`research/notes/buildings-production.md` §2.5, §3.1, CONFIRMED).
- **Unavailable.** Arrays missing, index out of range → `invalid_argument` (index) or `null` (arrays).
- **Edge cases.** Index gated by technology: reported as `requires_unlock` from `tech_config`/type data when known,
  else `unknown`.

#### D-ADV-OPP-1: opportunity scoring

- **Candidates.** For each product P with live shop demand for the player:
  `unmet_30d(P) = Σ_shops D-SHOP-1 per_30d` (shops with unknown consumption interval excluded, `partial_inputs`).
  Types: `route_surplus` (the player has spare supply of P ≥ 1 unit/30d: ship more), `expand_production` (the
  player produces P but spare < unmet), `new_product` (the player produces no P; its recipe and building type are
  unlocked), `research_unlock` (locked; only with `include_locked: true`), `contract_offer` (a city contract offer
  for P, from `market.city_contract_offers`).
- **Value.** `volume = min(unmet_30d, spare)` for `route_surplus`, else `unmet_30d` (capped by
  `max_buildings × rate_new` for new production); `monthly_margin = volume × margin_per_unit`, with
  `margin_per_unit` from D-ADV-MARGIN-1 (existing producers) or `price − unitcost_new` (new production, unit
  cost from D-ADV-NEWUPKEEP-1 / rate_new + inputs at market); `payback_months = capex / monthly_margin` when both
  are positive.
- **Rank.** By `monthly_margin` descending (`null` last), then confidence, then product id.
- **Peer rates of module owners.** An observed peer rate of a gatherer or farm is normalised per module and scaled to
  the type's `max_module_count` (a new hub is planned with all modules).
- **Assumptions.** A-DEMAND-PERSISTS (current demand persists for the payback horizon), A-PRICE-SHOP, the cost
  assumptions above. Competition is not modelled (competitor intelligence is deferred).

#### D-ADV-ROUTE-RULES-1: route findings

| Kind | Rule | Severity |
|---|---|---|
| `route_error` | `errors[]` non-empty or `has_error` | high |
| `keep_all` | `min_keep.keep_all` | high |
| `destination_rejects_product` | `destination_accepts_product == false` | high |
| `dead_city_destination` | `destination_dead_city == true` | high |
| `paused` | `paused` | medium |
| `dormant_auto_wh` | `dormant_auto_warehouse` | low (informational: AUTO_WH is set on the origin) |
| `zero_dispatch_now` | `dispatch_amount_now.value == 0`, with `limited_by` | medium (`available`/`wait_full`: low) |
| `max_send_saturated` | `max_send.headroom_now == 0` and not unlimited | low; evidence lists every origin sharing the cap |
| `high_unit_cost` | `cost_per_unit_at_capacity / sale_price ≥ cost_ratio_threshold` (default 0.20) | medium (≥ 2× threshold: high) |
| `underfilled_dispatch` | `0 < dispatch_amount_now.value < 0.5 × vehicle_capacity` and not `wait_for_full_vehicle` | low |
| `shared_max_send` | ≥ 2 routes share one destination×product cap | info |
| `duplicate_route` | `occurrence > 0` (same origin/product/destination/source tuple) | low |

Severity order `high > medium > low > info`; findings sort by severity, then route id.

#### D-ADV-SEV-1: diagnosis severity

`diagnose_chain`/`get_overview` findings: `high` = production of the product (or an ingredient) is stopped or has
no source (`no_producer`, `producer_disabled`, `producer_blocked`, `no_modules`, `no_inbound_source`,
`deposit_depleted`); `medium` = rate-limiting (`input_supply_deficit`, `producer_missing_input`,
`inbound_route_blocked`, `output_blocked`, `no_outbound_route`, `outbound_constrained` (an output ≥ 90 % full while
shops still show unmet demand for the player), `outlet_shortfall` (theoretical supply above internal need plus shop
demand)); `low` = waste or risk (`output_accumulating`, `polluted`); `info` = context (`no_recipe` on the diagnosed
product itself).

#### D-ADV-CHG-1: change detection

- **Short term.** Between two entries of the in-memory state window (PRD §12.4, current world session only) the
  advisor compares compact digests: cash, loans total, building set (added/removed), per-building `recipe`,
  `user_enabled`, `is_working`, efficiency index, per-inventory counts, route set and per-route Max Send value,
  Min Keep value, `paused`, `errors`; research active/unlocked; per-product market price and trend. Numeric
  changes report `before`, `after`, `delta` (Quantity, `kind: observed`) and both game dates.
- **Monthly.** From `history.json`: ledger D-FIN-1 between the last two complete months; per-building production
  and per-shop sales between the last two complete months of their series. The in-progress month is never compared
  as if complete.
- **Ranking.** Removed/added entities and status flips first, then numeric changes by |relative delta|.
- **Unavailable.** Fewer than two window entries → short-term part `available: false` with the reason; history
  missing or of another world session → monthly part `available: false`.

#### D-ADV-CONTRA-1: contradiction detection — §3.6.

#### D-ADV-CONF-1: confidence — §3.4.

### 4.2 Thresholds and constants

| Constant | Value | Where |
|---|---|---|
| `cost_ratio_threshold` default | 0.20 | `review_routes` (parameter, 0.01–5) |
| underfilled fraction | 0.5 × capacity | `review_routes` |
| fill ratio "accumulating" | ≥ 0.9 (V1 D-INV-1) | `diagnose_chain`, `get_overview` |
| contradiction tolerance (profit) | sign differs or > 50 % | `get_profitability` |
| `max_buildings` default (new production cap per opportunity) | 3 | `find_opportunities` (parameter 1–20) |
| attention items in `get_overview` | ≤ 10 | |
| chain diagnosis depth | default 3, max 6 | `diagnose_chain` |

### 4.3 Assumption ids

| Id | Text |
|---|---|
| A-CONTINUOUS | Producers run continuously at their theoretical rate (D-RATE-1/2); stoppages are not forecast |
| A-UPKEEP-ACTIVE | Upkeep uses the observed active monthly upkeep (the amount the game accrues while the building works) |
| A-INPUT-MARKET | Inputs are valued at the current market price for the player (opportunity cost), not at the player's own production cost |
| A-INPUT-OWN | Inputs the player produces are valued at the advisor's own unit-cost estimate |
| A-NO-TRANSPORT-IN-UNITCOST | Inbound transport cost is not part of the unit cost; outbound distribution is reported separately |
| A-FULL-VEHICLES | Distribution cost per unit assumes full vehicles (cost per unit at capacity) |
| A-PRICE-SHOP | Sales happen at current shop prices for the player (median over shops with demand) |
| A-NEW-DEFAULT-EFFICIENCY | New buildings run at the building type's initial efficiency index |
| A-ACTOR-MODIFIER-1 | Company-specific (actor) production and upkeep modifiers are 1 (they are not exported) |
| A-FULL-DEPOSITS | New gatherers get their maximum module count on undepleted resources at module speed 1 |
| A-NO-REGIONAL-COST | Build costs exclude regional modifiers applied at placement |
| A-SPARE-AVAILABLE | Spare existing supply (theoretical supply minus internal need) can be redirected to the plan |
| A-NO-LOGISTICS | Routes, vehicles and transport costs of a plan are not planned |
| A-DEMAND-PERSISTS | Current shop demand persists; price and demand reactions to new supply are not modelled |
| A-DISPATCH-NOT-THROUGHPUT | A dispatch amount is what the next dispatch would request now, not monthly throughput |
| A-EFFICIENCY-SCALES-RATE | Production rate scales with the efficiency output multiplier; upkeep with the upkeep multiplier |
| A-STATIC-RECIPE-CYCLE | Cycle time from the static recipe (`game_days / production_speed`) when no building runs the recipe |

---

## 5. Refresh scopes (PRD §13.7 extension)

| Scope | Tools |
|---|---|
| `none` | `explain_mechanic`, `how_to` |
| `state` | `diagnose_chain`, `find_opportunities`, `review_routes`, `plan_chain` |
| `state+history` | `get_overview`, `get_profitability`, `what_if`, `what_changed` (one request per family) |

---

## 6. Tool specifications

All parameters are validated by JSON Schema (`additionalProperties: false`, non-blank strings, finite numbers). Names
and ids resolve with the V1 rules (PRD §13.6).

### 6.1 `get_overview`

Params: `max_attention` (1–10, default 10). Result: `company` {id, name}, `cash` (Quantity), `loans`
{count, principal_total, monthly_payment_total (D-LOAN-1)}, `finances` {last_complete_month {month, income, expense,
net}, month_to_date {…}, net_change_vs_previous (D-FIN-1)} or `available: false`, `buildings` {count, by_status},
`production` {products: count, deficits[] / surpluses[] top 5 by |balance_per_30d|}, `logistics` {routes,
with_errors, paused, dormant, keep_all}, `research` {active, progress, queue_length, unlocked_count}, `issue_counts`
{kind: count} (V1 issue kinds), `attention[]` {rank, severity, kind, subject, summary, evidence, next_tool}.

### 6.2 `diagnose_chain`

Params: `product` (required), `depth` (1–6, default 3). Result: `product`, `levels[]` per product in the recipe
chain {product, depth, recipe, supply_per_30d (estimate), internal_need_per_30d (estimate), shop_demand_per_30d,
producers[] {building, status, derived_status, produced_last_month, theoretical_per_30d}}, `findings[]` {rank,
severity, kind, product, building?, route?, summary, evidence, confidence}, `healthy` (bool: no high/medium findings).
Errors: product not produced by any recipe → result with `no_recipe` finding (not an error).

### 6.3 `get_profitability`

Params: optional `product`, `input_cost_basis` (`market_value` default | `own_cost`), `limit` (1–25, default 10).
Result: `products[]` {product, game_stats (observed, from `history.player_product_stats`, or `null`),
estimate {sale_price, unit_cost {upkeep_per_unit, inputs_per_unit, total}, distribution_cost_per_unit,
margin_per_unit, volume_per_30d, margin_per_30d}, confidence}, `company_month` (ledger last complete month: income,
expense, net), sorted by `margin_per_30d` (estimate) descending. Without history: `game_stats` null and
`company_month` unavailable (degraded, not an error).

### 6.4 `find_opportunities`

Params: `types[]` (subset of the D-ADV-OPP-1 types), `include_locked` (default false), `include_unviable`
(default false), `max_buildings` (1–20, default 3), `limit`/`cursor`. Result: `opportunities[]` {rank, type, product,
volume_per_30d, margin_per_unit, monthly_margin, capex, payback_months, buildings_needed, building_type, recipe,
locked_by[], evidence, confidence, summary, viable, caveats[]}. `viable` = estimated monthly margin > 0 (`null` when unknown).
`caveats` (live validation) names costs the margin does not price: `needs_new_supply_chain` (the player produces none
of the recipe's inputs) and `long_payback` (payback over 24 months); they do not change the ranking.
Candidates with `viable: false` are omitted unless `include_unviable` is true; the omission is listed in
`unavailable`. Contract offers are listed with their observed terms only (`monthly_margin` null): their
profitability is not estimated.

### 6.5 `review_routes`

Params: optional `product`, `origin`, `destination`, `kinds[]`, `min_severity` (default `low`),
`cost_ratio_threshold`, `limit`/`cursor`. Result: `summary` {routes_reviewed, findings_by_kind, findings_by_severity},
`findings[]` {rank, severity, kind, route_id, origin, destination, product, evidence, suggestion}.

### 6.6 `plan_chain`

Params: `product` (required), `target_per_month` (required, > 0, ≤ 1e6), `existing_capacity` (`spare` default |
`none`), `recipe_choice` (map product → recipe), `max_depth` (1–12, default 8). Result: `target`, `steps[]` {product,
depth, required_per_30d, from_existing_per_30d, new_per_30d, recipe, building_type, buildings, rate_per_building,
capex, upkeep_per_30d, locked_by[], status (`ok`, `uses_existing`, `unplannable`, `cycle`, `truncated`)},
`totals` {buildings, capex, upkeep_per_30d, raw_inputs_per_30d, complete}.

### 6.7 `what_if`

Params: `change` (required) = `{type, …}` with `type` ∈:

| type | fields | evaluation |
|---|---|---|
| `add_buildings` | `recipe` (required), `count` (1–50, default 1), `building_type` (optional) | D-ADV-NEWRATE-1, NEWUPKEEP-1, CAPEX-1; product balance before/after per product touched |
| `remove_building` | `building` | lost output, freed input need, upkeep saved (observed), balance per product, downstream consumers left short |
| `set_efficiency` | `building`, `index` | D-ADV-EFF-1 |
| `change_recipe` | `building`, `recipe` (one of the type's recipes) | balance per product before/after; rate from the building's observed cycle scaled by `game_days` ratio |
| `set_max_send` | `route`, `value` (0–1e6; 0 = unlimited) | D-ADV-DISPATCH-1 for every route sharing the destination×product cap |
| `set_min_keep` | `route`, `value` (0–99) or `keep_all: true` | D-ADV-DISPATCH-1 for that route only |
| `take_loan` | `loan` (loan info name from static `loan_infos`) | D-LOAN-1 monthly payment; cash after; months of net income (last complete month) to cover payment |

Result: `change` (echo), `applied: false`, `baseline`, `scenario`, `deltas` (Quantity: `derived` for exact
consequences such as the loan payment or the 75 % demolition refund, `estimate` otherwise), `side_effects[]`.
Fields that do not belong to `type` → `invalid_argument`. For `set_max_send` every route sharing the destination ×
product cap is recomputed and each row also reports the observer's current value next to the server replica (a
difference is flagged in `degraded_inputs`); when several routes share the cap, `deltas.shared_destination` gives
the shared room and `combined_max_next_dispatches` = min(sum of the per-route amounts, room), because the first
origin to dispatch uses the room up. For `set_efficiency`, `baseline`/`scenario.effective_output_multiplier` give
the multiplier the game applies (observed, including company modifiers; the game UI shows it as a percentage) next
to the efficiency-array values. `what_if.change` is the only advisor parameter whose name is a verb; it
describes a hypothetical evaluated on copies and is never applied.

### 6.8 `what_changed`

Params: optional `since_seq` (a state `seq` published in an earlier `meta.snapshot.seq` of this world session),
`horizon` (`short_term` | `monthly` | `both`, default `both`), `limit` (1–50, default 25).
Result: `short_term` {available, from {seq, game_date}, to {seq, game_date}, window {entries, seqs[]},
changes[]}, `monthly` {available, from_month, to_month, changes[]}. `since_seq` not in the window →
`not_found` with the available seqs in the hint.

The in-memory window holds only snapshots this server process loaded while answering calls (no background
sampling: a history sampler is deferred). After a server restart or a world-session change the short-term part is
unavailable until a second snapshot has been seen.

### 6.9 `explain_mechanic`

Params: `topic` (required; id, title, alias or French/English term). Result: `matches[]` (best first, ≤ 3)
{id, title, summary, formula, details, evidence[], verification, confidence, caveats, related[], resource_uri},
`glossary[]` matching terms, `pitfalls[]` related. No match → `not_found` with the closest topic ids as
`candidates`.

### 6.10 `how_to`

Params: `action` (required, free text or id). Result: `matches[]` {id, title, audience (`player_in_game` |
`mcp_client`), steps[], evidence[], verification, caveats, related_tools[]}. Entries for in-game actions say that the
MCP cannot perform them. No match → `not_found` with candidate ids.

---

## 7. Knowledge base

- Files: `mechanics.json`, `glossary.json`, `pitfalls.json`, `how_to.json` under `mcp-server/src/roi_mcp/knowledge/`.
- Every mechanic, glossary term, pitfall and how-to entry carries `evidence[]` (`{file, location, note}` pointing at
  repository files: `research/…`, `PRD.md`, `docs/VALIDATION-REPORT.md`) and `verification` ∈
  `CONFIRMED_IN_GAME`, `CONFIRMED_SOURCE`, `HIGH_CONFIDENCE`, `INFERRED`, `UNKNOWN`, plus `confidence`
  (`high` for the two CONFIRMED levels, `medium` for HIGH_CONFIDENCE, `low` otherwise).
- A test validates every entry against `knowledge.schema.json` and checks that every evidence file exists in the
  repository.
- Content is curated from repository evidence only; nothing is taken from Rise of Industry 2.

## 8. Testing (V1.1)

Unit tests per derivation; seeded property/invariant tests (no new dependency); golden economic scenarios with
hand-computed expected values; missing-data, degraded-section, stale, contradictory-data and malformed-argument
cases; response-schema validation of every advisor response; response-size limits on the large fixture; the full
V1 suite as regression. Generated response schemas: `schemas/tool-responses/<advisor tool>.schema.json`.

## 9. Compatibility

- No change to snapshot schemas or observer. V1.1 servers read V1 observer output.
- V1 tool responses are unchanged except that `meta.server_version` reports the V1.1 server version.
- Pinning (§3.3 rule 1) applies to V1 tools too; it only removes the theoretical case of a family reloading
  mid-call.

## 9a. Response sizes (large synthetic fixture, ~3x the sample save)

Measured by `tests/advisor/test_window_knowledge.py::test_all_advisor_tools_respect_size_cap_on_large_fixture`
(bytes): get_overview 14,519; diagnose_chain 26,339; get_profitability (limit 25) 14,962; find_opportunities (limit
50) 11,525; review_routes (limit 50) 24,956; plan_chain 11,965; what_if 5,650; what_changed 5,179;
explain_mechanic 5,707; how_to 1,825. All under the 30,000-byte cap.

## 10. Open questions

- Q1: Is the median shop price the best sale-price basis? A delivery-weighted price would need a new read.
- Q2: Module upkeep composition with modifiers (D-ADV-NEWUPKEEP-1) is INFERRED.
- Q3: Actor-specific production/upkeep modifiers are not exported; estimates assume 1.

## 11. Non-regression

The 29 V1 tools, their schemas and their tests are unchanged in behaviour. The test asserting the registered tool
list now expects the 29 V1 tools followed by the ten V1.1 tools.

## 12. Real-game validation required after T-12

See `docs/v1.1/ARCHITECTURE.md` §9.
