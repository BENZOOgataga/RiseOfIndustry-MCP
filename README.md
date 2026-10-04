# Rise of Industry MCP

Read-only [Model Context Protocol](https://modelcontextprotocol.io) integration for the original
**Rise of Industry** by Dapper Penguin Studios. It lets an MCP client (Claude, ChatGPT or any client that
supports local stdio servers) inspect your **running** game as structured data: company and finances,
buildings and production, logistics routes with Max Send and Min Keep, inventories, cities, shops,
regions, market prices and research — without screenshots.

> Targets the original Rise of Industry, Steam App `671440`, verified build **2.3.3 : 0507b**
> (Steam build 9064059). It does **not** target Rise of Industry 2.

> **Unofficial project.** Rise of Industry MCP is an unofficial community project and is not affiliated with,
> endorsed by, or sponsored by Dapper Penguin Studios or Kasedo Games. Rise of Industry and related names and
> assets are trademarks and property of their respective owners.

Current version: **1.1.0** (MCP server 1.1.0 with observer build `D8F22442`).

```text
Rise of Industry.exe
 └─ RoiMcpObserver (official code mod, read-only, time-sliced on the main thread)
        │ files only: heartbeat / static / state / history snapshots
        ▼
%LOCALAPPDATA%\RoiMcp\
        │
        ▼
roi-mcp (separate Python process, MCP over stdio)  ──►  Claude / ChatGPT / other MCP client
```

The 29 data tools return observed facts, game-computed values and clearly labelled deterministic derivations
(rates per 30 days, supply-chain requirements, route cost per unit, ...). Version 1.1 adds 18 advisory tools
(overview, chain diagnosis, profitability, opportunities, route review, chain planning, what-if scenarios,
research paths, loan/route calculators, comparisons, forecasts) and curated game-knowledge resources. Every
advisory number is labelled observed, derived or estimate, with its assumptions and confidence; the advice is
for the player, and the MCP never acts in the game. See [docs/v1.1/USAGE.md](docs/v1.1/USAGE.md).

## What you can ask

| Area | Tools |
|---|---|
| Game and company | `get_game_status`, `search`, `list_companies`, `get_company`, `get_finances` |
| Buildings and production | `list_buildings`, `get_building`, `get_production_overview`, `find_production_issues`, `get_supply_chain` |
| Logistics | `list_routes`, `get_route`, `list_warehouse_requests`, `list_vehicles` |
| Catalogue | `list_products`, `get_product`, `list_recipes`, `get_recipe`, `list_building_types`, `get_building_type` |
| World and market | `list_cities`, `get_city`, `get_shop`, `find_shops`, `list_regions`, `get_region`, `get_market`, `get_tech_tree`, `get_research_state` |
| Advisor (1.1) | `get_overview`, `diagnose_chain`, `get_profitability`, `find_opportunities`, `review_routes`, `plan_chain`, `what_if`, `what_changed` |
| Planning and calculators (1.1) | `research_path`, `suggest_research`, `loan_calculator`, `route_calculator`, `compare_options`, `spatial_analysis`, `get_chain_graph`, `forecast` |
| Knowledge (1.1) | `explain_mechanic`, `how_to`, and the MCP resources `roi://knowledge/*` (mechanics, French/English glossary, pitfalls, how-to) |

47 tools and 51 resources in total. Advisor tools accept `language` (`en`, `fr`, `both`) and `detail`
(`summary`, `standard`, `full`). Estimates are labelled as estimates with their assumptions and confidence.

## Read-only, and what that rests on

Nothing in this project changes your game: no building, demolishing, recipe or efficiency changes, no route,
Max Send or Min Keep edits, no research, market, vehicle or money actions, no save edits, no keyboard or mouse
input. The only data that flows towards the game is an integer "please refresh" nonce.

Because the observer runs inside the game process, this is a property of the implementation, not a sandbox.
It is backed by a build-time **IL gate** that checks every game member the observer touches against a reviewed
allowlist, a denylist of disguised mutators and hard rules (no field writes, no setters, no reflection writes,
no events, no Harmony, no network), plus automated tests and an end-to-end save-comparison check (gate V8).
See [docs/READ-ONLY-GATE.md](docs/READ-ONLY-GATE.md) and [docs/KNOWN-LIMITATIONS.md](docs/KNOWN-LIMITATIONS.md).

Game stability comes first: the observer reads only on the Unity main thread in slices of at most ~2 ms per
frame, backs off automatically when captures get expensive, subscribes to no game event, and never throws
into the game. A kill switch (`%LOCALAPPDATA%\RoiMcp\observer.disabled`) stops it within a second.

## Requirements

- Windows, with Rise of Industry from Steam at the verified build 2.3.3 : 0507b (code mods load only in the
  full game, not the demo).
- .NET SDK 8 (builds the observer against your own installed copy of the game; no game files are included).
- Python ≥ 3.11 and [`uv`](https://docs.astral.sh/uv/) for the MCP server.
- An MCP client that can start a local **stdio** server (for example Claude Code, Claude Desktop or Codex).

## Quick start

```powershell
pwsh scripts/build.ps1                 # builds the IL gate and the observer (the gate runs on every build)
pwsh scripts/backup-saves.ps1          # copy-only, verified backup of your saves (recommended)
pwsh scripts/install-observer.ps1      # copies exactly two files into <game>\Mods\RoiMcpObserver\
```

Start the game **through Steam**, load a save, and configure your MCP client to run:

```text
uv --directory <path-to-this-repo>/mcp-server run roi-mcp
```

For example (use the absolute path of your clone):

```text
claude mcp add -s user rise-of-industry -- uv --directory <path-to-this-repo>/mcp-server run roi-mcp
codex mcp add rise-of-industry -- uv --directory <path-to-this-repo>/mcp-server run roi-mcp
```

Then ask your assistant to call `get_game_status`. The server can start before or after the game. Details:
[docs/INSTALL.md](docs/INSTALL.md) and [docs/MCP-CLIENTS.md](docs/MCP-CLIENTS.md).

## Known limitations

The MCP reads a live game only (no offline save reading), supports one verified game build, and cannot act
in the game. Distances are straight-line estimates, not road paths; it predicts how much a route would send,
not when; company-specific modifiers are measured where the player owns the building type and otherwise
assumed and flagged; opportunity and research rankings are advisory policies; estimates assume continuous
production and current prices; forecasts are short-term; history-based answers are marked stale unless
requested with `fresh=true`. Full lists: [docs/KNOWN-LIMITATIONS.md](docs/KNOWN-LIMITATIONS.md) and
[docs/v1.1/RELEASE-NOTES.md](docs/v1.1/RELEASE-NOTES.md#known-limitations).

## Documentation

| Document | Content |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | Prerequisites, build, observer install/update/uninstall, verification |
| [docs/MCP-CLIENTS.md](docs/MCP-CLIENTS.md) | Claude Desktop / Claude Code / Codex / generic stdio client configuration |
| [docs/USAGE.md](docs/USAGE.md) | Tools, example questions, semantics (Max Send, Min Keep, demand units), freshness |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Error codes, mod not loading, kill switch, diagnostics |
| [docs/KNOWN-LIMITATIONS.md](docs/KNOWN-LIMITATIONS.md) | What V1 does not do and what the game does not provide |
| [docs/SNAPSHOT-FORMAT.md](docs/SNAPSHOT-FORMAT.md) | Exchange files, envelope, ids, versioning |
| [docs/READ-ONLY-GATE.md](docs/READ-ONLY-GATE.md) | The IL gate's rules and how to review a new game member |
| [docs/VALIDATION-REPORT.md](docs/VALIDATION-REPORT.md) | In-game validation gates E1–E7, V8 and their evidence |
| [docs/v1.1/](docs/v1.1/USAGE.md) | V1.1 advisor layer: usage, architecture, semantic audit, live validation, release notes |
| [PRD.md](PRD.md) | The V1 design contract |
| [research/](research/RESEARCH.md) | Technical reconnaissance of the game, described in our own words (no game code) |

## Repository layout

```text
observer/        C# observer mod (net461), its tests, and the read-only IL gate (readonly-gate/)
mcp-server/      Python MCP server (uv project) and its tests
schemas/         JSON Schemas of the exchange files (generated from the observer DTOs) and tool responses
scripts/         build, install/uninstall, save backup, diagnostics, performance and validation tooling
docs/            user and developer documentation
research/        reconnaissance notes: game behaviour in our own words, type/member names for interoperability
```

## Data classes

Every value is one of: **STATIC** game definitions (products, recipes, building types, tech tree, formulas),
**SAVE** state persisted by the game (buildings, routes, money, research), **RUNTIME** values that exist only
in memory (shop demand, cached route distances, vehicle positions) and **DERIVED** values computed by the
observer or server (labelled with the derivation id, e.g. `D-RATE-1`). Unknown or unavailable data is reported
as unavailable, never invented. The game keeps no market price history; none is fabricated.

## Status

V1 (server 1.0.0, 29 tools) is implemented and validated: observer, IL gate, MCP server, scripts and
documentation, with automated tests. In-game gates E1–E7, V8 (end-to-end read-only check) and T-12 (soak) pass
on observer build `D8F22442`; the evidence and the release-readiness review are in
[docs/VALIDATION-REPORT.md](docs/VALIDATION-REPORT.md).

V1.1 (server 1.1.0, same observer) adds the advisory layer: 47 tools and the `roi://knowledge/*` resources.
Its live semantic validation, release notes and known limitations are in
[docs/v1.1/LIVE-VALIDATION-RESULTS.md](docs/v1.1/LIVE-VALIDATION-RESULTS.md) and
[docs/v1.1/RELEASE-NOTES.md](docs/v1.1/RELEASE-NOTES.md).

This public repository starts at version 1.1.0. Commit ids quoted in the validation documents refer to the
earlier development history, which is not published.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Changes must keep the integration read-only and must not add game
binaries, assets, decompiled source, saves or personal data.

## Security

Please report vulnerabilities privately, as described in [SECURITY.md](SECURITY.md), not in a public issue.

## License

This project's original code and documentation are licensed under the [MIT License](LICENSE). The license does
not cover Rise of Industry, its code, assets, data or trademarks, or any other third-party material; see
[NOTICE](NOTICE).

### Dependencies and licenses

Nothing below is bundled in this repository; it is installed by `uv` (Python) or NuGet (.NET) when you build.

| Component | Main dependencies | Licenses |
|---|---|---|
| MCP server | `mcp` (official SDK), `jsonschema`, `psutil` and their dependencies (pydantic, anyio, starlette, uvicorn, httpx, cryptography and others) | MIT, BSD-2/3-Clause, Apache-2.0, PSF |
| MCP server tests | `pytest` | MIT |
| Observer and IL gate | Mono.Cecil, Newtonsoft.Json, Microsoft.CodeAnalysis (Roslyn), .NET reference assemblies | MIT |
| .NET tests | xunit, Microsoft.NET.Test.Sdk | Apache-2.0, MIT |
| Save inspection tooling (optional) | `lz4` | BSD-3-Clause |
| Observer build (not redistributed) | the game's own `Assembly-CSharp`, Unity and Newtonsoft assemblies from your installed copy | owned by their respective owners |
