# V1.1 advisor architecture

Companion to [PRD-ADDENDUM.md](PRD-ADDENDUM.md). Server-side only; the observer, the snapshot format and the
exchange directory are unchanged.

## 1. Layering

```
MCP client
   │ stdio (tools + resources)
   ▼
server.py ── list_tools / call_tool (V1) + list_resources / read_resource (V1.1, knowledge only)
   │
app.py (V1 framework: liveness, refresh, CallContext, envelope, size cap)   ← pinning added to CallContext.snapshot
   │
   ├── tools/*.py          29 V1 tools (unchanged contracts)
   └── advisor/            V1.1
         contract.py       Quantity helpers, Advice builder (observed / assumptions / inputs / confidence / degraded)
         basis.py          pinned snapshot basis, cross-family consistency, contradiction detection
         economics.py      pure derivations D-ADV-* (no snapshot access; unit tested)
         facts.py          read-only views over StateIndex/StaticIndex shared by advisor tools
         window.py         compact per-snapshot digests for what_changed (fed by the V1 StateWindow)
         knowledge.py      loading + lookup of knowledge/*.json, MCP resource listing
         research.py       (phase 2) research graph traversal and costing, pure
         calculators.py    (phase 2) loan schedule, spatial maths, forecasts, pure
         localize.py       (phase 2) en/fr names from the catalogue + authored message catalogue
         presentation.py   (phase 2) detail modes (summary/standard/full) and language, applied after every handler
         tools_*.py        the 18 tool handlers; tools.py holds the ToolSpecs (role-tagged descriptions)
   knowledge/*.json        curated mechanics, glossary, pitfalls, how-to (+ knowledge.schema.json)
```

Rules:

- `advisor/economics.py` is pure: plain values in, plain dicts out. Every function returns its `method` id, inputs,
  and a confidence record. Tools never compute economics inline.
- Advisor handlers read snapshots only through `CallContext` (so V1 liveness, stale handling, `meta`, provenance and
  the size cap apply unchanged) and only through `advisor/basis.pin_basis`, which pins the families they use.
- No helper (route maths, research ranking, scoring) is registered as an MCP tool. Public: the ten phase-1 tools and
  the eight phase-2 tools (PHASE2-ADDENDUM.md); comparison is exposed only as the constrained `compare_options`.
- Every advisor handler is wrapped by `presentation.present` (detail, language); handlers themselves always build
  the standard answer, so modes cannot change a conclusion.
- The advisor never writes anything. The refresh request (scope per addendum §5) is written by the unchanged V1
  `App.call` path.

## 2. Snapshot pinning

`CallContext.snapshot(family)` returns the snapshot already pinned in `ctx.used[family]` when the family was loaded
earlier in the same call (sections are still checked). Before V1.1 a second call re-ran `store.load`, which could
swap in a newly published file mid-call. `CallContext.static()` already behaved this way.

`basis.pin_basis(ctx, history=…)` loads static (optional), state (required) and history (optional or required),
then:

1. drops history when `history.world_session/pid ≠ state.world_session/pid` (records
   `history_world_session_mismatch`);
2. records `static_ref_matches`, `captured.consistent`;
3. returns a `Basis` object: identity rows for `data.basis`, confidence factors (`stale_data`,
   `inconsistent_snapshot`, `static_mismatch`) and degraded-input rows.

Nothing about pinning is public; `data.basis` reports what was pinned.

## 3. Confidence and degradation

`contract.Advice` collects, during a handler run:

- `observed` facts (Quantity, `kind: observed`),
- `assumptions` (ids from addendum §4.3; text resolved from one table),
- `calculation_inputs`,
- `degraded_inputs`, `contradictions`,
- per-result confidence records produced by `economics.confidence(start, factors)`.

`Advice.finish(result)` merges everything into `data` with the overall confidence (minimum level, union of factors)
and the V1 `provenance` block (`derived` lists every D-ADV method used).

## 4. Facts layer

`advisor/facts.py` builds, once per call, plain views used by several tools:

- `ProductBalance`: per product, player's enabled producers and consumers with D-RATE-1/2 rates
  (re-using `tools.common.theoretical_rate`), spare supply, shop demand per 30 d for the player;
- price lookups (D-ADV-PRICE-1, D-ADV-INPUTVAL-1);
- upkeep lookups and the player's current build prices (as V1 `get_building_type`);
- route rows with their dispatch inputs.

Facts carry the evidence path for each value so tools can emit Quantity objects with `source`.

## 5. what_changed window

The V1 `StateWindow` (PRD §12.4: ≤ 20 snapshots / ≤ 30 min, current world session, memory only) now also stores a
compact digest per snapshot (`advisor/window.digest(snapshot)`): cash, loans total, per-building recipe/flags/
efficiency index/inventory counts, per-route Max Send/Min Keep/paused/errors, research active and unlocked set,
market price/trend per product. The digest is built in `StateWindow.add` (called by the store when a state snapshot
is loaded for a call). The existing `series()` API used by `find_production_issues` is unchanged.

There is no background sampling: the prefetcher still only prepares files. A history sampler is deferred (addendum
§1).

## 6. Resources

`server.build_server` registers `on_list_resources` and `on_read_resource`. The handlers serve the JSON files of
`roi_mcp/knowledge/` (validated at load). Reading resources touches no snapshot and no game file. Unknown URIs return
an MCP error.

## 7. Schemas

`response_schemas.py` gains an `ADVISOR_DATA` table generated into `schemas/tool-responses/<tool>.schema.json` for the
ten tools, with shared `$defs`: `Quantity`, `Confidence`, `Assumption`, `Degraded`, `Contradiction`, `Basis`,
`AdvisorHeader`. Generation and the committed-equals-generated test cover all 39 tools.

## 8. Tests

| Area | File |
|---|---|
| Derivations (unit + golden + seeded property) | `tests/advisor/test_economics.py` |
| Pinning, basis, contradictions | `tests/advisor/test_basis.py` |
| Each tool: contract, golden scenario, missing/degraded/stale/contradictory, malformed args | `tests/advisor/test_tools_*.py` |
| Knowledge base schema and evidence files, resources over MCP | `tests/advisor/test_knowledge.py` |
| Size limits on the large fixture | `tests/advisor/test_size.py` |
| Window digests and what_changed | `tests/advisor/test_window.py` |
| V1 regression | the whole existing suite (unchanged except the registered-tool-list assertions) |

All fixtures are synthetic (V1 builder plus variants); nothing is read from the live exchange directory.

## 9. Real-game validation needed after T-12

Server-side tests cannot establish these; each needs a live session on the baseline build (manual user actions per
PRD §20 where noted):

1. **Estimate plausibility (V11-1).** On a live save, compare `get_profitability` estimates with the in-game product
   statistics panel and `game_stats`; record the ratio per product.
2. **Cycle-time model (V11-2).** For a recipe the player does not run yet, compare D-ADV-NEWRATE-1
   (`static_recipe` basis) with the effective cycle of a freshly built building (manual build by the user in a
   validation save).
3. **Upkeep model (V11-3).** Compare D-ADV-NEWUPKEEP-1 with the observed `upkeep.monthly_active` of a freshly built
   building and with an efficiency change (D-ADV-EFF-1), including a module owner (module upkeep composition, Q2).
4. **Dispatch replica (V11-4).** After the user changes Max Send/Min Keep on a route (validation save), compare
   `what_if` predicted `dispatch_amount_now` with the observer's next value.
5. **what_changed window (V11-5).** Over a 30-minute session with periodic calls, confirm the short-term changes
   match the user's in-game actions and that a quickload empties the window.
6. **Response sizes (V11-6).** All ten tools under 30 KB on the largest available save (E3 save).
7. **Knowledge accuracy (V11-7).** Spot-check French glossary terms and how-to UI steps in the game UI.
8. **Latency (V11-8).** PERF-9 (≤ 500 ms p95) for the advisor tools on the largest fixture and on the live save.
