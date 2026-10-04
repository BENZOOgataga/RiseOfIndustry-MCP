# V1.1 semantic audit (release candidate)

> Commit ids in this document refer to the project's earlier development history, which is not published;
> the public repository starts at version 1.1.0.

Every advisor number is a Quantity whose `kind` is `observed` / `definition` / `game_computed` (all read from the
game's snapshots), `derived` (exact arithmetic, `method` given), `estimate` (needs a modelling assumption; `method`
and `confidence` given) or `parameter` (caller input or documented constant). Curated knowledge carries a
`verification` status instead. The test suite walks every response and fails on any number outside these forms
(raw numbers are allowed only as counts/ranks/ids or inside `evidence` objects, which hold unchanged snapshot values).

Class key below: **O** observed (incl. definition / game_computed), **D** derived, **E** estimate, **K** curated knowledge.

## Per tool

| Tool | O | D | E | K |
|---|---|---|---|---|
| get_overview | cash, loan principals, ledger income/expense, research progress, counts | loan payment total (D-LOAN-1), month net, net change (D-FIN-1) | supply/need/shop demand per 30 d and balances | — |
| diagnose_chain | producer status evidence, produced last month, stock, route fields | fill ratio (D-INV-1) | supply/need/shop demand per 30 d, unmet demand | — |
| get_profitability | game product statistics, ledger month, prices used | profit per unit sold (game stats) | sale price basis, upkeep per unit, input cost, unit cost, distribution, margin | — |
| find_opportunities | contract-offer terms | — | unmet demand, spare supply, rates, margins, capex, payback | — |
| review_routes | all route evidence (errors, Max Send, Min Keep, dispatch now, cost) | cost per unit at capacity, cost ratio, cost per unit now | sale price used for the cost ratio | — |
| plan_chain | — | required per 30 d (recipe ratios) | spare used, new amount, buildings, rate per building, capex, upkeep | — |
| what_if | baseline values (route caps, upkeep, paid to build, cash) | loan payment, refund (0.75 × paid), upkeep removed | rates, balances, efficiency upkeep, dispatch replica | — |
| what_changed | before/after values of window digests and history | deltas, relative changes, D-FIN-1 shares | — | — |
| explain_mechanic / how_to | catalogue names (`catalog_terms`) | — | — | mechanics, glossary, pitfalls, how-to (each with evidence + verification) |
| research_path | game costs/days for costed nodes, active remaining days/cost, efficiency | node cost = daily × days for costed nodes, known partial sums | formula-based days/costs of uncosted nodes, totals, completion | "no alternative path" rule (source-confirmed) |
| suggest_research | — | chain fit | demand value, bottleneck value, research cost/days, score | — |
| loan_calculator | existing loans, cash, ledger net | payment, totals, schedule, early repayment, cash-flow ratios | — | flat-interest rule (source-confirmed) |
| route_calculator | existing route values (authoritative), difficulty | straight-line Euclidean / Chebyshev | detour path range, cost per trip, capacity (median), cost per unit, throughput | — |
| compare_options | prices, stock, catalogue values | straight-line distance, existing-route cost per unit, spreads | everything model-based per kind | — |
| spatial_analysis | — | all distances, weighted sums, geometric median | — | — |
| get_chain_graph | buildings running recipes, routes, AUTO_WH links, requests | — | producer rates | recipe links are catalogue definitions (`basis: catalogue/hypothetical`) |
| forecast | cash, sample nets, stock samples | mean net | months to zero, stock rate, days to full/empty | — |

## Formulas

Unit "per 30 d" = per game month (30-day months). Missing input → the result is `null` with a `degraded_inputs`
row; no formula substitutes a plausible value for a missing game value. Constants used where the game does not
export a value are explicit assumptions (listed in `data.assumptions`) and lower confidence.

| Id | Formula | Units | Inputs | Assumptions | Missing data | Confidence | Limitations |
|---|---|---|---|---|---|---|---|
| D-ADV-PRICE-1 | median shop price (demand > 0) → market final → market price | money/unit | shops, market | A-PRICE-SHOP | null | high; fallback −1 | no delivery-weighted price |
| D-ADV-INPUTVAL-1 | market final → market price | money/unit | market | A-INPUT-MARKET | null → unit cost null | medium | opportunity cost, not purchase cost |
| D-ADV-UPKEEPSHARE-1 | Σ upkeep×share / Σ output | money/unit | active upkeep, D-RATE | A-CONTINUOUS, A-UPKEEP-ACTIVE | producers without upkeep/rate skipped (partial) | medium | quantity share for multi-output recipes |
| D-ADV-UNITCOST-1 | upkeep/unit + Σ (amount_i / result) × value_i | money/unit | recipe, values | as above + A-INPUT-* | any null term → null | medium | inbound transport excluded |
| D-ADV-DISTCOST-1 | mean(dispatch cost / capacity) of outbound routes | money/unit | routes | A-FULL-VEHICLES | no route → null | medium | full vehicles |
| D-ADV-MARGIN-1 | price − cost − distribution; × volume | money/unit, money/30d | above, produced last month | — | null if price/cost null | min of inputs | game statistics window differs |
| D-ADV-NEWRATE-1 | peer median (normalised to eff_out[initial] / eff_out[peer], per module × module limit) else Σ amount × 30 / (game days / (speed × eff_out[initial] × company output modifier)) × modules | units/30d | peers, catalogue, D-ADV-TYPEMOD-1, module limit (observed `modules.max` when the player's buildings agree, else catalogue) | A-NEW-DEFAULT-EFFICIENCY, A-ACTOR-MODIFIER-1, A-FULL-DEPOSITS, A-STATIC-RECIPE-CYCLE | game days missing → null; speed / modules missing → 1 **flagged** partial; modifier unmeasured → 1 flagged mechanic_unverified | high (peer) / medium−1 (static) | a measured modifier is the current one (may be temporary) |
| D-ADV-NEWUPKEEP-1 | max((base×pct + n × module cost × module pct) × difficulty × company upkeep modifier × eff_upk[initial], base×pct×min_upkeep) | money/30d | catalogue, difficulty, D-ADV-TYPEMOD-1, module limit | A-ACTOR-MODIFIER-1, A-MODULE-UPKEEP-PCT | cost/pct missing → null; module cost missing → null; module pct missing → owner pct (assumption); min upkeep missing → no floor, flagged; modifier unmeasured → 1 flagged | medium (low when the modifier is assumed) | live: matched 16/16 owned type/recipe pairs |
| D-ADV-TYPEMOD-1 | output = output_multiplier / eff_out[index]; upkeep = monthly_full / (upkeep base × difficulty × upkeep_multiplier); used only when all the player's buildings of the type agree (±0.1 %) | ratio | state buildings, catalogue arrays | A-MODULE-UPKEEP-PCT | no owned building or disagreement → null (caller assumes 1, flagged) | — | live: oil types ×0.75 output, ×1.2 upkeep; others 1 |
| D-ADV-CAPEX-1 | player price else base cost (+ modules) | money | research price table | A-NO-REGIONAL-COST | null | high; fallback −1 | regional modifiers excluded |
| D-ADV-PLAN-1 | spare first, new = rest; recurse new × ratio | units/30d | recipes, spare | A-SPARE-AVAILABLE, A-NO-LOGISTICS | unplannable step → complete=false | per step | logistics not planned |
| D-ADV-DISPATCH-1 | min(cap, stock − min keep, free ∧ max-send room) | units/dispatch | route inputs | A-DISPATCH-NOT-THROUGHPUT | any unknown input (incl. Min Keep, Max Send) → null | high | not throughput |
| D-ADV-EFF-1 | rate × eff_out[b]/eff_out[a]; upkeep × eff_upk[b]/eff_upk[a] (floor) | units/30d, money/30d | arrays, upkeep | A-EFFICIENCY-SCALES-RATE | unknown rate → null balances | high | gated levels not exported |
| D-ADV-OPP-1 | volume × margin; payback = capex / monthly margin | money/30d, months | above | A-DEMAND-PERSISTS | null → unranked last | min of inputs | no competition |
| D-ADV-ROUTE-RULES-1 | rule table (addendum 4.1) | — | route fields | — | rule not evaluated without inputs | high (cost rule medium) | thresholds are parameters |
| D-RES-PATH-1 | game days/cost; else formula(tier, efficiency) × median(game/formula) | days, money, money/day | research state, formulas | A-RESEARCH-FORMULA | efficiency 0 / formula missing → null | high (game) / medium (calibrated) / low (uncalibrated) | speed modifiers only via calibration |
| D-RES-SCORE-1 | (demand + bottleneck) × (0.5 + 0.5 fit) / cost × 30 | ratio | above | A-SCORE-WEIGHTS | unscored with reason | min of inputs | weights are a design choice |
| D-LOAN-1 / D-LOAN-SCHED-1 | principal × (1 + apr) / duration × modifier; totals; early = remaining/duration × principal | money/30d, money | catalogue or arguments | A-LOAN-MODIFIER | grace unknown → offsets null | high | first-instalment month not verified |
| D-ROUTE-CALC-1 | formula(distance = straight line) ; detour range = straight × observed factors (≥ 3; state-trading routes excluded) | money, tiles | coordinates, formulas, routes | A-STRAIGHT-LINE, A-ACTOR-MODIFIER-1, A-FULL-VEHICLES | no capacity → per-unit null; travel time always null | medium (depot formulas −1) | no road data |
| D-CMP-1 | per-kind metrics; spread = max − min | per metric | per kind | per kind | metric null → listed in `unavailable` | per metric | six kinds only |
| D-SPATIAL-1 | Euclid / Chebyshev; Σ w × Euclid; Weiszfeld (+ vertex check) | tiles | coordinates | A-STRAIGHT-LINE | no coordinates → listed | derived | straight line |
| D-GRAPH-1 | graph build with basis labels | — | recipes, buildings, routes | — | sections missing → links omitted, degraded | — | 150 nodes / 300 edges |
| D-FORECAST-1 | cash / −min(net) … cash / −mean(net); Δcount / Δgame days | months, days | ledger, window | A-TREND-PERSISTS | < 2 months / < 2 game days → available false | medium; −1 with < 4 samples | linear trend only |

## Fixes made by this audit (release-candidate step 7)

| Found | Fix |
|---|---|
| Dispatch replica treated an unknown Min Keep as 0 and an unknown Max Send as unlimited | Returns null with the reason |
| `get_overview` excluded loans whose remaining payments were unknown from the payment total | Total becomes null and is flagged |
| `what_if` (set_efficiency, change_recipe, remove_building, add_buildings) turned unknown rates into 0 deltas | Unknown rates propagate as null "after" values with a degraded input |
| `what_if take_loan` and `what_changed` monthly treated a missing income/expense as 0 | Null |
| `loan_calculator` treated an unknown catalogue grace period as 0 | Payment maths unchanged (independent of grace); month offsets null, flagged |
| New-building rate/upkeep silently assumed production speed 1, 1 module, no upkeep floor when the catalogue lacked them | Same values, now flagged `partial_inputs` (confidence lowered) |
| Spare supply silently excluded producers with an unknown rate | Flagged in `degraded_inputs` (find_opportunities, plan_chain) |
| Glossary: French type name inferred from one instance name was marked source-confirmed | Downgraded to HIGH_CONFIDENCE with an explanatory note |
| Paged advisor tools keep their rows under `data.result`, so the generic size cap shortened pages **without moving the cursor** (rows lost between pages) on the large fixture | Pages are bounded by size before the envelope (`page_bounded_by_size`), the cursor moves; a test pages through every row of the large fixture |
| `diagnose_chain` relied on the generic cap on large worlds (it could drop whole chain levels) | Bounded itself: ≤ 8 producers per level (`producers_total`, `producers_omitted`), ≤ 30 findings then size-guided, levels never dropped |
| `get_chain_graph` relied on the generic cap (dangling edges); summary mode cut nodes and edges independently | Bounded by player buildings per recipe (counts kept on recipe nodes); summary returns a coherent product/recipe skeleton |
| `loan_calculator` full worst case was 29.5 KB | Full schedule 48 rows + last (worst case 25.0 KB) |

## Fixes made by live validation (world a9eaa382, Y95-10-16 → Y95-11-04)

Details and evidence: [LIVE-VALIDATION-RESULTS.md](LIVE-VALIDATION-RESULTS.md).

| Found live | Layer | Fix | Commit |
|---|---|---|---|
| Company modifiers ≠ 1 (oil types ×0.75 output, ×1.2 upkeep); module upkeep % not exported (new gatherer upkeep 4–6× low); catalogue module limit 3 below the researched 5; peer rates not normalised for efficiency | advisor | D-ADV-TYPEMOD-1, A-MODULE-UPKEEP-PCT, observed module limit, peer normalisation | `87fe5cb` |
| Map-prebuilt gatherers (`kind: other`, tag PrebuiltGatherer) rated as factories (modules ignored; 2.5 vs ~10 per 30 d) | server (also in V1) | D-RATE-2 for module owners exported as `other` | `87fe5cb` |
| Shared Max Send room: per-route dispatch amounts added beyond the room | advisor | `what_if` `deltas.shared_destination` | `87fe5cb` |
| `what_if set_efficiency` showed the array multiplier (1 → 1.25) while the game showed 0.75 → 0.9375 (94 %) | advisor | `effective_output_multiplier` | `4801e0c` |
| Detour range below the straight line (state-trading routes measure to a trade point) | advisor | excluded from detour samples | `711c007` |
| Glossary petrochemical definition carried fixture recipe numbers (20 days; live 15 days, 11 recipes) | knowledge | definition points to the live catalogue | `041656c` |
| `fresh=true` refreshed one family; history stayed stale (confidence low) on tools reading both | advisor | scope `state+history` | `bed7b1b` |
| 10 working farms / water / sand gatherers reported `deposit_depleted` (modules on no deposit) | server (also in V1) | `nodes == 0` is not depletable | `9fc4e98` |
| `get_overview` production deficits read as shortages (they include world-wide shop demand) | advisor | explanatory note | `1384992` |
| Top opportunity needed three unproduced inputs, payback 31 months | advisor | `caveats` (`needs_new_supply_chain`, `long_payback`); ranking unchanged | `c0266f7` |
