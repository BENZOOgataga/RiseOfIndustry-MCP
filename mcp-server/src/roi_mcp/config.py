"""Server configuration from environment variables, plus the injectable clock and process lister."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import __version__

SERVER_VERSION = __version__
SUPPORTED_SCHEMA_MAJOR = 1
GAME_PROCESS_NAME = "Rise of Industry"

# PRD 11.7
OBSERVER_GRACE_S = 60.0
HEARTBEAT_STALE_S = 5.0
MAIN_THREAD_STALE_S = 5.0
CURRENT_MIN_AGE_S = 15.0
# PRD 13.1
HEARTBEAT_CACHE_S = 1.0
# PRD 11.4
READ_RETRY_DELAY_S = 0.1
# PRD 13.2
REFRESH_WAIT_DEFAULT_S = 3.0
REFRESH_WAIT_MAX_S = 10.0
REFRESH_POLL_S = 0.25
# PRD 12.4
WINDOW_MAX_SNAPSHOTS = 20
WINDOW_MAX_AGE_S = 30 * 60
# PRD 14.9
RESPONSE_SIZE_CAP_BYTES = 30_000
LIST_LIMIT_DEFAULT = 25
LIST_LIMIT_MAX = 50
# PRD 19
SERVER_LOG_MAX_BYTES = 5 * 1024 * 1024
SERVER_LOG_BACKUPS = 3

REFRESH_REQUEST_FILENAME = "refresh-request.json"
SERVER_LOG_FILENAME = "server.log"


def default_exchange_dir() -> Path:
    """`ROI_MCP_EXCHANGE_DIR`, else `%LOCALAPPDATA%\\RoiMcp` (PRD 11.1)."""
    override = os.environ.get("ROI_MCP_EXCHANGE_DIR")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "RoiMcp"


def default_schema_dir() -> Path:
    """Where the snapshot JSON Schemas are loaded from.

    Order: `ROI_MCP_SCHEMA_DIR`; a `_schemas` directory bundled inside the package (if a packager
    copies it there); the repository `schemas/` directory resolved relative to this source file
    (`mcp-server/src/roi_mcp/` -> repo root). `uv --directory <repo>/mcp-server run roi-mcp` installs
    the project in editable mode, so the repository path is the normal case.
    """
    override = os.environ.get("ROI_MCP_SCHEMA_DIR")
    if override:
        return Path(override)
    here = Path(__file__).resolve().parent
    bundled = here / "_schemas"
    if (bundled / "state.schema.json").is_file():
        return bundled
    return here.parents[2] / "schemas"


def refresh_wait_from_env() -> float:
    raw = os.environ.get("ROI_MCP_REFRESH_WAIT_S")
    try:
        value = float(raw) if raw else REFRESH_WAIT_DEFAULT_S
    except ValueError:
        value = REFRESH_WAIT_DEFAULT_S
    if value != value:
        value = REFRESH_WAIT_DEFAULT_S
    return max(0.0, min(value, REFRESH_WAIT_MAX_S))


class Clock:
    """Wall clock (UTC) and monotonic clock; replaced by a fake in tests."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        import anyio

        await anyio.sleep(seconds)


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    name: str
    create_time: float  # epoch seconds


def psutil_process_lister() -> list[ProcInfo]:
    """Read-only enumeration of running processes named like the game executable."""
    import psutil

    out: list[ProcInfo] = []
    for p in psutil.process_iter(["pid", "name", "create_time"]):
        try:
            info = p.info
            name = info.get("name") or ""
            if is_game_process_name(name):
                out.append(ProcInfo(int(info["pid"]), name, float(info.get("create_time") or 0.0)))
        except Exception:  # process vanished or access denied: skip
            continue
    return out


def is_game_process_name(name: str) -> bool:
    n = (name or "").strip()
    if n.lower().endswith(".exe"):
        n = n[:-4]
    return n.casefold() == GAME_PROCESS_NAME.casefold()


@dataclass
class ServerConfig:
    exchange_dir: Path = field(default_factory=default_exchange_dir)
    schema_dir: Path = field(default_factory=default_schema_dir)
    refresh_wait_s: float = field(default_factory=refresh_wait_from_env)
    clock: Clock = field(default_factory=Clock)
    process_lister: Callable[[], list[ProcInfo]] = psutil_process_lister
    heartbeat_cache_s: float = HEARTBEAT_CACHE_S
    refresh_poll_s: float = REFRESH_POLL_S
    file_logging: bool = True
