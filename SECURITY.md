# Security policy

## Scope

The integration consists of an in-game observer mod and a local MCP server that talks to MCP clients
over stdio. It opens no network ports. The only data that flows towards the game is an integer refresh
nonce in `%LOCALAPPDATA%\RoiMcp\refresh-request.json`.

Relevant issues include, for example: any way to make the observer change game state, write outside the
exchange directory, crash or stall the game; any way for file content in the exchange directory to be
interpreted as code, a member name or a path; prompt-injection risks through game strings (building,
company or city names are returned as data and must never be treated as instructions).

## Reporting

Please report vulnerabilities privately through GitHub's "Report a vulnerability" (Security tab) of this
repository rather than in a public issue. Include the game build, observer and server versions
(`get_game_status`), steps to reproduce and the diagnostics zip from `scripts/collect-diagnostics.ps1`
(it never contains saves).

## Trust model

The exchange directory has the same trust level as your Windows user account. Another process running as
you can write fake snapshot files; the server validates them against the schemas and the running game's
PID, but it cannot detect a malicious local process with your privileges.
