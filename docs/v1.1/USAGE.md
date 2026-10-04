# Using the V1.1 advisor tools

The advisor tools analyse **the player company** from the same snapshots as the V1 tools. They never change the
game. Contract: [PRD-ADDENDUM.md](PRD-ADDENDUM.md).

## Which tool for which question

| Question | Tool |
|---|---|
| "How is my company doing? What needs attention?" | `get_overview` |
| "Why is my Paint production low?" | `diagnose_chain(product: "Paint")` |
| "Which products make money?" | `get_profitability()` (one product: `product`) |
| "What should I produce or sell more of?" | `find_opportunities()` |
| "Are my routes OK? Which are expensive or stuck?" | `review_routes()` |
| "What do I need to build for 100 Paint a month?" | `plan_chain(product: "Paint", target_per_month: 100)` |
| "What happens if I set Max Send to 20 on this route?" | `what_if(change: {type: "set_max_send", route: <id>, value: 20})` |
| "What changed since I last looked?" | `what_changed(since_seq: <meta.snapshot.seq from an earlier answer>)` |
| "How does Max Send work?" | `explain_mechanic(topic: "max send")` |
| "How do I change Min Keep?" | `how_to(action: "change min keep")` |
| "How do I get to Polymers, and how long will it take?" | `research_path(target: "Polymers")` |
| "What should I research next?" | `suggest_research()` |
| "What would a bank loan cost me per month?" | `loan_calculator(loan: "BankLoan")` or explicit `principal`, `apr`, `duration_months` |
| "What would a route from here to there cost?" | `route_calculator(origin, destination, product)` (straight-line estimate, never a road path) |
| "Which of these shops/recipes/sources is better?" | `compare_options(kind, options, ...)` |
| "Where is the most central spot for these sites?" | `spatial_analysis(mode: "hub", locations, candidates)` |
| "Show me the production graph of Paint" | `get_chain_graph(product: "Paint", format: "mermaid")` |
| "When will I run out of cash?" | `forecast(kind: "cash")` |

Low-level facts (one building, one route, the catalogue) stay with the V1 tools (`get_building`, `get_route`,
`get_product`, ...).

## Smaller answers and French

Every advisor tool accepts `detail` (`summary` | `standard` | `full`) and `language` (`en` | `fr` | `both`).
`summary` keeps the result's top items, confidence, assumptions and warnings and drops supporting detail. `fr` uses the
game catalogue's own names (French on a French install) and authored French texts; it never machine-translates game
names and never changes ids or numbers.

## Reading an advisor answer

- `data.result` is the answer. Every number in it is a Quantity `{value, unit, kind}`:
  `observed` (read from the game), `definition` (catalogue), `game_computed`, `derived` (exact arithmetic on
  observed values), `estimate` (needs an assumption; has `method` and `confidence`), or `parameter`.
- `data.assumptions` lists every assumption used (stable ids such as `A-CONTINUOUS`).
- `data.confidence` is the lowest confidence of the answer's results, with the factors that lowered it.
- `data.degraded_inputs` and `data.unavailable` say what was missing; a missing value is `null`, never 0.
- `data.contradictions` lists observed values that disagree with each other.
- `data.basis` names the exact snapshots (seq, world session, game date, age) the answer was built from.
- `meta` is the V1 envelope: check `meta.stale` and `meta.warnings` as for every tool.

## Knowledge resources

`roi://knowledge/mechanics` (and `/mechanics/<id>`), `roi://knowledge/glossary`, `roi://knowledge/pitfalls`,
`roi://knowledge/how-to`. Each entry carries repository evidence and a verification status
(`CONFIRMED_IN_GAME`, `CONFIRMED_SOURCE`, `HIGH_CONFIDENCE`, `INFERRED`, `UNKNOWN`).

## Limits

- Estimates assume continuous production at theoretical rates, market-price input valuation (or the player's own
  cost with `input_cost_basis: "own_cost"`), and no competition or price reaction.
- Build costs exclude regional modifiers; company-specific production/upkeep modifiers are taken as 1.
- `what_changed` compares only snapshots this server process loaded while answering calls (≤ 20 / 30 minutes,
  current world session); there is no background sampling or persistent history.
