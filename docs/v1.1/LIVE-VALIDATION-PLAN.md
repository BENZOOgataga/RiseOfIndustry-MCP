# V1.1 supervised live validation (semantic, ~25 minutes)

Purpose: check the advisor's **models** against a real company. Not an observer stability test: the observer is the
validated V1 build (D8F22442, source unchanged since 112ef43) and V1.1 adds no game read, no snapshot change and no
game write, so V8, E3 and T-12 are not repeated.

Preconditions (user): game on the baseline build, validated observer installed (unchanged), a **validation save**
("Save As" `RoiMcp-V11-Validation`) of a company with at least two producing chains, several routes, a few months of
history, at least one researchable technology. The MCP server is the V1.1 candidate. No other heavy process runs.

Outputs go to `.local/validation/v11/` (gitignored). Every operator step is read-only; the only file the server writes is
`refresh-request.json` when `fresh` is used.

| # | Min | Who | Step | Pass criterion / measurement |
|---|---|---|---|---|
| 0 | 2 | user | Load the validation save, set speed 1×, unpause | `get_game_status` → `ready`, compatibility `verified` |
| 1 | 3 | operator | `uv run --directory mcp-server python ../scripts/validation/v11_semantic_check.py --out ../.local/validation/v11/check-1.json` | Hard checks: every advisor tool answers in all modes under 30 KB; the server dispatch replica equals the observer's `dispatch_amount_now` on **every** route; `route_calculator` never claims a path distance |
| 2 | 0 | operator | Read the measurements of step 1 (automatic, same file) | (a) `cycle_observed_over_static_model`: median ≈ 1 validates A-STATIC-RECIPE-CYCLE; a constant ≠ 1 measures the company recipe-time modifier (A-ACTOR-MODIFIER-1). (b) `upkeep_observed_over_model` for buildings at the initial efficiency index without modules (validates D-ADV-NEWUPKEEP-1 vs `paid_to_build`-based reality). (c) `research_days_game_over_formula` = 1 validates the formula path of `research_path`. (d) `profitability_vs_game_stats` per product: sign agreement and ratio. (e) `detour_factor_path_over_straight_line` range (expected ≥ 1 for road routes). (f) `catalogue_language` French, display names differ from English, glossary entry "Usine pétrochimique" vs the catalogue |
| 3 | 4 | operator | `get_overview`, `diagnose_chain` (two products), `review_routes`, `find_opportunities`, `suggest_research`, all with `language: "both"`; user reads the top items | User confirms each top attention item / finding / opportunity is real in the game (yes / no / unclear per item; record) |
| 4 | 5 | user + operator | Operator: `what_if set_max_send` on a route chosen by the operator (records the predicted `dispatch_amount_now` of every route sharing the cap) and `what_if set_min_keep` on another route. User: applies exactly those two values in the UI (paused). Operator: `get_route(fresh: true)` on each | Observed `dispatch_amount_now` after the change equals the prediction (shared routes included); Max Send shown on the other origins equals the new value |
| 5 | 3 | user + operator | User: changes the efficiency of one building by +1 step. Operator: before the change `what_if set_efficiency`; after, `get_building(fresh: true)` | Observed `cycle_days_effective` and `upkeep.monthly_full` match the predicted rate/upkeep multipliers (±1 %) |
| 6 | 4 | user + operator | Operator: `loan_calculator(loan: "<catalogue bank loan>")` and `existing: true`. User (optional, validation save only): takes that loan, then runs 10× until the next month start, pauses | Payment equals the game's instalment; `remaining_payments` of the new loan decrements at the first month start after taking it (validates `month_offset 1`); ledger "Loan Payments" grows by the payment |
| 7 | 4 | operator | During steps 3–6, call `what_changed` and `forecast(kind: "stock")` for one filling output every ~30 s (game at 3×) | `what_changed` lists the user's changes of steps 4–6 (route/building changes) with correct before/after; the stock forecast's days-to-full is within its range at the next sample |
| 8 | 0 | operator | Re-run step 1 into `check-2.json` | Hard checks still pass |

Total ≈ 25 minutes. Steps 4–6 are the only in-game actions; all are in the validation save. Step 6 is optional.

Record in `docs/VALIDATION-REPORT.md` under a new "V1.1 semantic validation" section (do not modify the V1 sections):
the check files, the measurements, user confirmations, and every deviation with its decision (fix the model, add a
caveat, or lower the confidence).

## Decision rules

- A hard check failing → fix before release.
- Model ratio median outside [0.98, 1.02] (cycle, upkeep, research) → keep the estimate but add the measured factor
  as a documented caveat, or switch the tool to the observed basis only; never silently calibrate.
- Profitability sign disagreement with the game on a product → the contradiction is already reported; document the
  cause (window mismatch, multi-output allocation, upkeep basis) and decide whether the estimate stays.
- Any user "no" in step 3 → inspect the evidence; a wrong rule is fixed, an unclear one gets a caveat.
