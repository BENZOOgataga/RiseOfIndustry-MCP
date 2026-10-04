# Contributing

Thanks for your interest. This project is a **read-only** MCP integration for the original
Rise of Industry (Steam App 671440). Please read `PRD.md` (V1 design), `docs/v1.1/ARCHITECTURE.md` (advisor
layer) and `docs/READ-ONLY-GATE.md` before changing code.

## Ground rules

- **Read-only, always.** No change may add a code path that changes game state, saves, settings or
  input. The observer is checked by the IL gate (`observer/readonly-gate/`, see `docs/READ-ONLY-GATE.md`);
  never weaken or bypass it. New game members must be reviewed and added to `allowlist.json` with evidence.
- **Game stability first.** Observer work runs on the Unity main thread in small time slices. Keep the
  per-frame budget, avoid allocations in capture paths, never subscribe to game events.
- **Original Rise of Industry only.** Do not use Rise of Industry 2 documentation, code or assumptions.
- **No proprietary material.** Do not commit game binaries, assets, decompiled source, saves or
  snapshots of your own game. Type and member names needed for interoperability are fine.
- **No personal data.** No machine paths, user names, saves or private configuration in commits.

## Development setup

See `docs/INSTALL.md` (prerequisites and build). In short:

```powershell
pwsh scripts/build.ps1 -Test        # observer + gate + tests + MCP server tests
uv run --directory mcp-server pytest
```

Building the observer and running the gate fixture tests needs a local Rise of Industry install
(baseline build only). The MCP server and its tests run without the game.

## Pull requests

- Use Conventional Commits (`feat(observer): ...`, `fix(mcp): ...`, `docs: ...`).
- Keep changes focused; include tests (`observer/tests`, `observer/readonly-gate/RoiMcp.ReadOnlyGate.Tests`,
  `mcp-server/tests`).
- If you change a DTO, regenerate the schemas (`ROI_UPDATE_SCHEMAS=1 dotnet test observer/tests/...`) and
  update the server.
- If you add a game member to the read layer, include the allowlist entry with its review and transitive
  findings acknowledgement in the same PR.

