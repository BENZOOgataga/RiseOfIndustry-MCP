# Installation

This integration has two parts:

1. **The observer** — a small read-only code mod (`RoiMcpObserver`) loaded by Rise of Industry's official
   mod loader. It writes snapshot files to `%LOCALAPPDATA%\RoiMcp\`.
2. **The MCP server** — a separate Python process (`roi-mcp`) started by your MCP client over stdio. It
   reads those files and answers tool calls.

> Only the original **Rise of Industry** (Steam App 671440) is supported, and only the verified build
> **2.3.3 : 0507b** (Steam build id 9064059, savegame version 2304). On any other build the observer
> reports `unsupported_build` and captures nothing. Rise of Industry 2 is not supported.

## Prerequisites

| For | You need |
|---|---|
| Running | Windows 10/11, Rise of Industry from Steam at the baseline build, Python ≥ 3.11 and [`uv`](https://docs.astral.sh/uv/) |
| Building the observer | .NET SDK 8 (the observer targets .NET Framework 4.6.1; reference assemblies come from NuGet, so no targeting pack install is needed), and the game installed locally (the build compiles against the game's own `Managed\` assemblies, read-only) |
| Development tests | the above; the observer unit tests run on .NET Framework 4.8 (built into Windows) |

Throughout this document `<ROI_INSTALL>` is your game folder, by default
`C:\Program Files (x86)\Steam\steamapps\common\RiseOfIndustry`, and `<REPO>` is your clone of this
repository. If the game is elsewhere, set `ROI_GAME_DIR=<ROI_INSTALL>` (or pass `-GameDir`).

## 1. Build

```powershell
pwsh <REPO>\scripts\build.ps1            # Release build of the IL gate and the observer
pwsh <REPO>\scripts\build.ps1 -Test      # also runs observer, gate and MCP server tests
```

Building the observer runs the **read-only IL gate** automatically; the build fails if the observer
references anything that is not explicitly allowed (see [READ-ONLY-GATE.md](READ-ONLY-GATE.md)). The
result is `observer\src\RoiMcp.Observer\bin\Release\net461\RoiMcpObserver.dll`.

## 2. Back up your saves (recommended)

```powershell
pwsh <REPO>\scripts\backup-saves.ps1
```

This copies `%APPDATA%\RiseOfIndustry` to `%LOCALAPPDATA%\RoiMcp\backups\saves-<timestamp>\` with a
`manifest.json` of SHA-256 hashes. It never modifies, locks, moves or deletes your saves. If the game is
running and a save changes during the copy, the backup is discarded and you are asked to retry from the
main menu.

## 3. Install the observer

```powershell
pwsh <REPO>\scripts\install-observer.ps1                 # auto-detects the Steam install
pwsh <REPO>\scripts\install-observer.ps1 -GameDir "<ROI_INSTALL>"
pwsh <REPO>\scripts\install-observer.ps1 -DryRun         # show what would happen
```

The script:

- verifies the Steam build id and the SHA-256 of `Assembly-CSharp.dll` against the baseline and aborts on
  any mismatch (there is no override);
- checks `<ROI_INSTALL>\Mods\` hygiene: every subfolder must contain a `desc.json` (a folder without one
  silently stops **all** mods from loading) and no other mod may be named `RoiMcpObserver`;
- refuses to run while the game is running from that folder (the game keeps the loaded observer DLL
  open, so it cannot be replaced; quit the game normally first);
- copies exactly two files:
  `<ROI_INSTALL>\Mods\RoiMcpObserver\desc.json` and `<ROI_INSTALL>\Mods\RoiMcpObserver\code\RoiMcpObserver.dll`.

It changes nothing else: no other game file, no PlayerPrefs/registry value, no save.

Then **start the game through Steam**. The game discovers `Mods\` relative to its working directory,
which Steam sets to the install folder; launching the exe from a shortcut with another working directory
can prevent local mods from loading.

### Side effect on saves (cosmetic)

Like any mod, an enabled observer is listed in the header of saves made while it is active. Loading such
a save later without the observer shows the game's "missing mods" notice. This does not change the game;
achievements are not disabled by mods in this game build.

## 4. Configure your MCP client

See [MCP-CLIENTS.md](MCP-CLIENTS.md). For Claude Desktop / Claude Code the server command is:

```text
uv --directory <REPO>/mcp-server run roi-mcp
```

## 5. Verify

Load a save, then ask your client to call `get_game_status`. Expected: `game.state` is `ready`,
`game.compatibility` is `verified`, and `observer.version` is shown. In the main menu the state is
`menu`; while loading it is `loading`.

You can also look at `%LOCALAPPDATA%\RoiMcp\heartbeat.json` (updated every second) and
`%LOCALAPPDATA%\RoiMcp\observer.log`.

## Updating

1. `git pull` and rebuild: `pwsh scripts/build.ps1`.
2. Quit the game normally (the running game holds the observer DLL open).
3. Reinstall: `pwsh scripts/install-observer.ps1` (overwrites only the two observer files).
4. Start the game through Steam (the mod DLL is loaded once at game start).

The MCP server needs no reinstall; restart your MCP client (or the server) to pick up server changes.

## Disabling without uninstalling

Create an empty file `%LOCALAPPDATA%\RoiMcp\observer.disabled`. Within a second the observer stops all
captures and only writes its heartbeat (`state: disabled`). Delete the file to resume. Alternatively set
`"enabled": false` in `%LOCALAPPDATA%\RoiMcp\observer.config.json`.

## Uninstalling

```powershell
pwsh <REPO>\scripts\uninstall-observer.ps1               # removes exactly <ROI_INSTALL>\Mods\RoiMcpObserver\
pwsh <REPO>\scripts\uninstall-observer.ps1 -RemoveExchangeDir   # also deletes %LOCALAPPDATA%\RoiMcp (keeps backups\)
```

Add `-IncludeBackups` to also delete the save backups. Restart the game afterwards. Remove the server
entry from your MCP client configuration.
