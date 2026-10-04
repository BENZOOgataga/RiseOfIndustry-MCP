# Usage

This page covers the 29 V1 data tools; the 18 V1.1 advisor tools and the knowledge resources are documented in
[v1.1/USAGE.md](v1.1/USAGE.md). All 47 tools are **read-only**. Every response has the same envelope:

```json
{"ok": true, "meta": {...}, "data": {...}, "page": {"next_cursor": null, "total": 12}}
```

`meta` tells you how fresh the answer is: `game_state`, `world_session`, `source`, `snapshots` (one entry per
data family the answer used: `state`, `history`, `static`), `snapshot.age_s`, `snapshot.game_date`, `paused`,
`stale` and `stale_reason`, and `warnings` (`{code, detail}` objects). `source` follows one rule: `stale_snapshot`
if any live data used is stale, else `live_snapshot` if live data was used (also when combined with the
catalogue), else `static_catalog` if only the catalogue was used, else `none`. **Always check `meta.stale`.**
Data that could not be provided is listed in `data.unavailable` with a reason; it is never reported as zero. Errors use
`{"ok": false, "error": {"code", "message", "hint", "candidates"}}`; codes are listed in
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## Tools

| Area | Tools |
|---|---|
| Status and lookup | `get_game_status`, `search` |
| Companies and finances | `list_companies`, `get_company`, `get_finances` |
| Buildings and production | `list_buildings`, `get_building`, `get_production_overview`, `find_production_issues` |
| Logistics | `list_routes`, `get_route`, `list_warehouse_requests`, `list_vehicles` |
| Supply chains | `get_supply_chain` |
| Catalogue | `list_products`, `get_product`, `list_recipes`, `get_recipe`, `list_building_types`, `get_building_type` |
| Cities, shops, regions | `list_cities`, `get_city`, `get_shop`, `find_shops`, `list_regions`, `get_region` |
| Market and technology | `get_market`, `get_tech_tree`, `get_research_state` |

Ids look like `building:PetrochemicalFactory@120,85`, `product:Chemicals`, `recipe:Paints`, `company:3`,
`city:12`, `region:<guid>`, `route:<origin>|<product>|<destination>|<source>|<n>`. Tools that take an entity
also accept display names (in your game's language, case- and accent-insensitive), English names and
internal asset names; an ambiguous name returns `ambiguous` with up to 10 candidates. Fuzzy matching happens
only in `search`. **Ids are case-sensitive**: pass them exactly as returned (`building:paintfactory@55,40` is not
`building:PaintFactory@55,40`).

The `list_*` tools, `search`, `find_production_issues`, `find_shops` and `get_tech_tree` take `limit` (1–50,
default 25) and `cursor` (the `page.next_cursor` of the previous page). The `list_*` tools also take `fields`
(`compact` or `full`). `sort` exists on `list_buildings`, `list_routes`, `list_cities` and `find_shops`.
Responses stay under about 30 KB; larger results are paginated. An empty or whitespace-only text argument is
rejected with `invalid_argument`; leave an optional filter out to not filter.

## Example questions

| Question | Tools |
|---|---|
| "Is the game running and is the data fresh?" | `get_game_status` |
| "Inspect my Petrochemical Plant 5." | `search` → `get_building` |
| "Where does my gas go, and with what Max Send / Min Keep?" | `list_routes(product: "Gas")`, `get_route` |
| "How much gas do I need for 100 paint a month?" | `get_supply_chain(product: "Paint", target_output_per_30d: 100)` |
| "Which shops buy paint, and which are closest to my paint factory?" | `find_shops(product: "Paint", from_building: …)` |
| "Why is this factory idle?" | `find_production_issues`, `get_building` |
| "Which buildings are filling up?" | `list_buildings(sort: "stock_ratio")`, `find_production_issues(kinds: ["inventory_accumulating"])` |
| "How did my income and expenses change over 6 months?" | `get_finances(months: 6)` |
| "What does Limoges need?" | `get_city("Limoges")`, `get_shop` |
| "What am I researching and what is available next?" | `get_research_state`, `get_tech_tree(state: "available")` |
| "Who are my competitors?" | `list_companies`, `get_company`, `list_buildings(owner: …)` |
| "What are current market prices?" | `get_market` |

## Semantics to keep in mind

- **Max Send** (`max_send`) is stored **on the destination building, per product, and shared by every origin**
  that ships that product there. It caps the destination's stock *including* deliveries already on the way.
  `0` means unlimited. In `auto_shop_demand` mode it follows the shop's current demand. It is not a per-trip
  amount. `headroom_now` is how much more the cap currently allows.
- **Min Keep** (`min_keep`) is per route: the origin keeps at least this much and the route does not
  dispatch below it. `keep_all: true` means the route never dispatches.
- **`dispatch_amount_now`** is what the next dispatch on a route would request right now, replicated from the
  game's own formula with its inputs. The game's scheduler and vehicle availability decide actual throughput.
- **`distance_tiles` / `dispatch_cost`** are the game's own cached values for existing routes (cost is per
  vehicle dispatch). `find_shops` states per shop whether a route from `from_building` exists
  (`existing_route_status`: `present`, `absent`, or `unavailable` when it cannot be known, e.g. from an AI
  building while AI routes are not exported). For `absent` and `unavailable` it gives a
  `straight_line_cost_estimate` that is explicitly non-authoritative; with `unavailable` it does not claim that
  no route exists (`route_exists: null`).
- **`list_vehicles(vehicle: …)`** looks up one of your vehicles by its session id. Vehicle ids are valid only in
  the world session that produced them: after a load or quickload an old id is rejected with
  `stale_reference`.
- **Shop demand** is in units per city consumption interval (`consumption_interval_days`, usually 15 days).
  Derived values like unmet demand are also given per 30 days.
- **AI companies have infinite cash** in this game; their cash is reported as `{"infinite": true}`.
- **No price history**: the game keeps only the current price modifier and trend
  (`price_history.available: false`).
- **Names are game data.** Building, company and city names come from the game (and the player) and are
  returned as data, never as instructions.
- Max Send / Min Keep match the game UI (validated in-game, gate E2). Max Send is shared by all origins
  sending one product to one destination; "∞" in the UI is `unlimited: true`. If an older observer is
  installed, values carry `ui_label_validated: false` and the warning `ui_label_unvalidated`.

## Freshness

The observer captures the world every ≥ 5 s of real time while the game runs (when the game day changed) and
every 15 s while paused. Tools that read live data accept `fresh: true`: the server asks the observer for a
new snapshot of the relevant kind and waits up to 3 s (configurable up to 10 s); on timeout it answers from the
latest snapshot with the warning `refresh_timeout`. `get_finances(fresh: true)` refreshes the history
snapshot; most other tools refresh the state snapshot; catalogue answers come from the static catalogue.

When the game is not live (closed, main menu, loading), runtime tools return the corresponding error. Pass
`allow_stale: true` to get the last snapshot anyway, flagged `source: "stale_snapshot"` with a `stale_reason`.
