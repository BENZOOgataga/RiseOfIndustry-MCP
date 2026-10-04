# Troubleshooting

Start with `get_game_status`: it always answers and reports the game process, the observer's lifecycle
state, compatibility, capture statistics and the server version. The raw source is
`%LOCALAPPDATA%\RoiMcp\heartbeat.json` (rewritten every second while the game runs) and the observer log
`%LOCALAPPDATA%\RoiMcp\observer.log`.

## Error codes

| Code | Meaning | What to do |
|---|---|---|
| `game_not_running` | No process named `Rise of Industry` | Start the game through Steam. With `allow_stale: true` you get the last snapshot, flagged stale |
| `observer_not_detected` | The game runs but no heartbeat from this process (after a 60 s grace period from game start) | The mod is not installed or not loading: see "Mod not loading" below |
| `observer_unresponsive` | Heartbeat from this process older than 5 s | The game may be hung, or the observer's background thread stopped. Check `observer.log`; restart the game if it is frozen |
| `at_main_menu` | The game is in the main menu | Load a save |
| `loading` | A save is loading | Wait a few seconds |
| `observer_disabled` | Kill switch present or `"enabled": false` in `observer.config.json` | Delete `%LOCALAPPDATA%\RoiMcp\observer.disabled` / set `enabled` to true |
| `observer_faulted` | The observer stopped itself after repeated internal errors (the game is unaffected) | Collect diagnostics and report an issue; restart the game to reset |
| `unsupported_build` | The game build differs from the verified baseline 2.3.3 : 0507b (or `Assembly-CSharp.dll` was modified) | No capture is possible in V1. `get_game_status` shows detected vs expected values. Restore the official files (Steam "verify integrity") only if you modified them yourself |
| `snapshot_unavailable` | Ready, but no valid snapshot of the needed kind yet (just after loading, or every snapshot invalid) | Retry after a few seconds, or call with `fresh: true` |
| `section_unavailable` | The needed part of the snapshot is disabled, failed or switched off (e.g. route paths) | See `get_game_status.observer.disabled_sections`; optional sections are enabled in `observer.config.json` |
| `schema_mismatch` | Observer and server use different major snapshot versions | Update both from the same repository version (rebuild, reinstall the observer, restart the game and the server) |
| `not_found` | Id or name not found (ids are case-sensitive) | Use `search`, then pass the id exactly as returned |
| `ambiguous` | The name matches several entities | Pick an id from `candidates` |
| `stale_reference` | A vehicle id from another world session (after a load or quickload) | List vehicles again |
| `invalid_argument` | Bad parameter, including an empty or whitespace-only text argument | See the message; leave an optional filter out instead of passing `""` |
| `internal_error` | Unexpected server fault (the server keeps running) | Collect `server.log` and report an issue |

Warnings (non-fatal) include `stale`, `refresh_timeout` (the observer did not produce a new snapshot within
the wait — it may be backing off), `snapshot_invalid_using_previous`, `static_mismatch`,
`inconsistent_snapshot` (a game day ticked during a capture), `english_name_unavailable`,
`ui_label_unvalidated`, `section_degraded`, `active_actor_differs`, `catalog_from_previous_session` (catalogue
answer while the game is not live), `game_unresponsive` and `truncated` (the answer was shortened to stay
under the size cap).

## Mod not loading

1. **Launch through Steam.** The game finds `Mods\` relative to its working directory; Steam sets it to the
   install folder. `heartbeat.json` reports the observed `cwd`.
2. **Check the folder:** `<ROI_INSTALL>\Mods\RoiMcpObserver\desc.json` and
   `<ROI_INSTALL>\Mods\RoiMcpObserver\code\RoiMcpObserver.dll` must exist.
3. **Mods hygiene.** A subfolder of `Mods\` without a `desc.json` silently prevents **all** local mods from
   loading. Two mods with the same name (e.g. a local copy and a Workshop copy) stall the boot.
   `scripts/install-observer.ps1` checks both.
4. **Disabled in the Mod Manager.** If a mod throws during loading, the game disables it permanently and shows
   an error popup. Re-enable `RoiMcpObserver` in the game's Mod Manager, then report the problem with
   diagnostics. (The observer's load hooks only create its folder and start a thread, inside try/catch.)
5. **Wrong build.** On another game build the observer loads but reports `unsupported_build`.

## Saves and "missing mods"

Saves made while the observer is enabled list it in their header. Loading such a save without the observer
shows the game's "missing mods" notice; it is cosmetic. Achievements are not disabled by mods in this build.

## Kill switch

Create an empty file `%LOCALAPPDATA%\RoiMcp\observer.disabled`. Within a second the observer stops all reads
and captures; the heartbeat continues with state `disabled`. Delete the file to resume (a new world session
starts at the next ready frame). This works without restarting the game.

## Performance

`get_game_status.observer` shows the last capture's main-thread time, slice count and size, the effective
capture interval, `degraded` (captures exceeded `capture_ceiling_ms` and the observer backed off) and frame
statistics. To make the observer lighter, raise `running_interval_s` in
`%LOCALAPPDATA%\RoiMcp\observer.config.json`, for example:

```json
{"running_interval_s": 15, "paused_interval_s": 30, "frame_budget_ms": 1.0}
```

Out-of-range values are clamped and reported in `heartbeat.json` (`config_warnings`).

## Collecting diagnostics

```powershell
pwsh scripts/collect-diagnostics.ps1                  # heartbeat, logs, config, file sizes, versions
pwsh scripts/collect-diagnostics.ps1 -IncludeSnapshots  # also state/static/history (contain your game data)
```

The zip never contains saves or the `backups\` folder. Review it before sharing; snapshots contain your
company and building names.
