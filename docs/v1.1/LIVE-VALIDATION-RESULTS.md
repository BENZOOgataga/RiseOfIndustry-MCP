# V1.1 live semantic validation — results

> Commit ids in this document refer to the project's earlier development history, which is not published;
> the public repository starts at version 1.1.0.

Run on 2026-10-04 against the V1.1 release candidate (development revision `1730357` at the start), with the validated V1
observer unchanged. Plan: [LIVE-VALIDATION-PLAN.md](LIVE-VALIDATION-PLAN.md). Audit tables:
[SEMANTIC-AUDIT.md](SEMANTIC-AUDIT.md).

## 1. Environment and starting state

| Item | Value |
|---|---|
| Game | Rise of Industry 2.3.3 build 0507b (compatibility `verified`), language fr-FR, pid 34636 |
| Observer | installed DLL SHA-256 `D8F2244218D14D474FE43B1FA978B5412C43CD6DD339D4F379D54D702B3E159E` (validated V1); no `observer.config.json`, no kill switch; observer sources unchanged on the branch (`git diff 1730357..HEAD -- observer/` empty) |
| Save | `roi-mcp-v11-validation.sav` (Save As from `roi-mcp-v8-r2c`, 15:17:52); saves backed up first (`saves-20261004-151535`, 23 files); the user's main save never opened |
| World session | `a9eaa382-aa7b-47e0-89e2-1d8d01869dcc`, start Y95-10-16, paused |
| Server | persistent in-process `App` (same code as the stdio server), restarted after each fix |

Changes made in the game by the user, one at a time, game paused: Max Send of `PUITS À GAZ 1 → MAGASIN DE
BRICOLAGE [Gas]` 8 → 10 (one click was not registered: the snapshot showed 9, then 10); Min Keep of
`USINE DE TEXTILE 1 → MAGASIN DE VÊTEMENTS [Fibers]` 4 → 3; efficiency of `USINE PÉTROCHIMIQUE 2` 100 % → next step.
Runs: Y95-10-16 → 10-19 and 10-19 → 11-04. The validation save was not overwritten.

## 2. Mechanic checks

Every prediction below was recorded before the user acted and compared with a fresh snapshot.

| # | Check | Prediction | Observed | Result |
|---|---|---|---|---|
| 1 | Dispatch, shared Max Send (UI: MAX ENVO 8, MIN. GARD 2, 89 cases, $1,14K$) | UI values = snapshot | all equal (distance 89, cost 1140, stock 4, efficiency 125 %) | verified |
| 1 | Max Send 8 → 10 | room 2; each well would send 1 (capacity 1) | room 2, 1 and 1 | verified |
| 1 | Unlimited Max Send, Min Keep 4 → 3 | available 1, would send 1, no Max Send room | same | verified |
| 1 | Run 3 days | wells ship 2 Gas in total; shop stored + incoming ≤ 10; Fibres stop at Min Keep 3 | 1 + 1 shipped, 10, 3 | verified |
| 2 | Production cycle, 3 days | textile plant: one cycle (+2); gas well: none (first module in 5.6 d) | +2; none; module progress +0.250 = 3/12 | verified |
| 2 | Production cycle, 16 days | gas well +5, oil well +5, textile plant +2, petrochemical plant 2 +0 | +5, +5, +2, +0 | verified |
| 3 | Efficiency +1 on `USINE PÉTROCHIMIQUE 2` | multiplier 0.75 → 0.9375, cycle 40 → 32 d, upkeep 16 500 → 20 625, Buttons 1.5 → 1.875 / 30 d | all equal; UI shows 94 % (the effective multiplier); cycle progress kept (22.8 %) | verified |
| 4 | Company production modifier = 1 | — | oil types (OilGatherer, OilSeaGatherer, PetrochemicalFactory) ×0.75; all other types 1 | **contradicted** → fixed |
| 4 | Company upkeep modifier = 1 | — | oil types ×1.2; all other types 1 (after dividing out each building's efficiency) | **contradicted** → fixed |
| 4 | Module upkeep combination | base × pct + n × module cost × pct | exact on every module-owner type (module pct is not exported; the owner's pct fits) | verified (assumption A-MODULE-UPKEEP-PCT) |
| 5 | Profitability chain (Buttons) | price 66 638 (median of 2 shops), upkeep/unit 20 625 / 1.875 = 11 000, inputs 1 × 28 252 + 0.5 × 18 206, distribution 610 | every term traced to the snapshot | verified (game product statistics empty in this save) |
| 6 | Research | Adhésif 1.8 M / 21.6 d (game-costed); Interface 5.6 M / 67.2 d (formula, calibrated); suggestions deterministic | UI $1,8M / 22 jours; $5,6M / 68 jours (UI rounds days up; 68 d would cost 5.67 M); same ranking twice | verified; scoring is policy |
| 7 | Route / spatial | straight line labelled as such; existing route 65 tiles → (250 + 10 × 65) × 1 = 900 | same; detour range fell below the straight line | **bug** → fixed |
| 8 | French names | catalogue names = UI | 11 / 11 recipe names of the petrochemical plant equal ignoring case (the game's own "ecaoutchouc" spelling kept); locked recipes (Adhésif, Interface, Tubes) = `locked_by`; "Peut se produire à l'usine pétrochimique" | verified (no fuzzy matching) |
| 9 | Forecast | insufficient data after a server restart; available after a run | `available: false`, "needs 2 snapshots on different game days"; 13 samples later → available | verified |
| 10 | Loan (non-invasive) | payment 750 000 / 5 = 150 000; remaining 3 × 150 000 = 450 000 = game early-repay amount; on 1 Nov: 2 left, 300 000 | ledger "Loan Payments" 150 000 in Y95-09, 10, 11; 2 / 300 000 | verified; no test loan needed |

Other observations: every route reports vehicle capacity 1 and every loaded truck carried exactly 1 unit; the route
setting "MANUEL – 2 CYCLES" (dispatch frequency) is not exported, so the MCP predicts dispatch amounts, never their
timing; route `in_flight.units_started` (12 then 14 across the two wells) exceeds the destination's reserved incoming
(8 then 10) — a V1 field the advisor does not use, not investigated further.

## 3. Tool sweep

All 18 advisor tools × summary/standard/full × en/fr/both (207 calls, compare_options in all 6 kinds): every call
answered, largest 24.3 KB (cap 30 KB), slowest 325 ms without `fresh`, about 4 s with `fresh=true` on the
`state+history` tools; same world session throughout; no internal error. Semantic checker
(`scripts/validation/v11_semantic_check.py`) passes all five hard checks, including the two added by this run:
new-building economics match the player's own buildings (16 / 16 type/recipe pairs, rate and upkeep within 1 %) and
the estimated path range never falls below the straight line (minimum 0.9989×).

## 4. Advisor quality review

| Recommendation | Trace | Verdict |
|---|---|---|
| `get_overview` deficits (Oil −164 / 30 d …) | supply − internal need − demand of every live shop in the world | correct maths, misleading word → note added |
| Attention: 10 buildings `deposit_depleted` | farms / water / sand modules have no deposit (`nodes 0`) | **false** → fixed (server rule, also V1) |
| Attention: HQ `blocked` | game flag `requirements_met: false` | observed, cause unknown; left as is |
| `find_opportunities` #1 Premade Dinner (4.8 M / 30 d) | 3 × 0.083 / 30 d × 19.3 M margin; capex 150 M; payback 31 months; no input produced | arithmetic correct, advice poor → `caveats` added; ranking policy unchanged |
| `suggest_research` #1 Car Prototype Facility | (367.8 M + 0) × 0.5 / 200 000 × 30 | correct; chain fit 0 (no input produced) — policy limitation |
| `review_routes` high unit cost (Wood 1 600 / unit vs 2 647) | dispatch cost / capacity 1 | correct and actionable |
| `review_routes` `zero_dispatch_now` (Max Send reached) | normal while trucks are on the road | noisy (medium severity) — policy limitation |

## 5. Bugs found and fixed

See the table "Fixes made by live validation" in [SEMANTIC-AUDIT.md](SEMANTIC-AUDIT.md). Two of them are server-side
bugs that also exist in V1 (prebuilt-gatherer rates, `deposit_depleted` on modules without deposits); neither needs
an observer change. Each fix has a regression test; the full suite passes (3745 passed, 52 skipped).

## 6. Limitations that remain

- A measured company modifier is the current one; the oil ×0.75 looks recent (10-month average 9.5 vs 7.5 now) and
  may be temporary. Types the player does not own keep modifier 1, flagged `mechanic_unverified`.
- Without `fresh=true`, history-reading tools report history as stale (correct: the observer re-verifies history
  only on request or at month end); with it they take about 4 s.
- Dispatch timing (cycles between dispatches) is not exported.
- Opportunity and research ranking are policies (monthly margin; demand value per research cost); they do not price
  the supply chain still to build — now flagged, not penalised.
- The game's per-product statistics were empty in this save, so profitability was checked against the ledger and
  the snapshot, not against the game's own profit per unit.
