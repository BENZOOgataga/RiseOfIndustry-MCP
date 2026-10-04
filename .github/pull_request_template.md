## Summary

## Checklist
- [ ] Read-only: no new code path changes game state, saves, settings or input
- [ ] New game members are in `observer/readonly-gate/allowlist.json` with review and transitive acks
- [ ] Tests added/updated; `pwsh scripts/build.ps1 -Test` passes locally
- [ ] Schemas regenerated if DTOs changed
- [ ] No game binaries, assets, decompiled source, saves, personal paths or snapshots committed
