"""Refresh requests (PRD 11.6, 13.2, 13.2a).

This module contains the ONLY code path through which the server writes into the exchange
directory (apart from its own log file): `RefreshRequestWriter.write`, which atomically replaces
exactly `refresh-request.json`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config as cfg
from .config import Clock
from .util import format_utc, parse_utc

log = logging.getLogger("roi_mcp.refresh")

SCOPES = ("state", "history", "static")


class RefreshRequestWriter:
    """Atomic writer of refresh-request.json (temp file in the same directory + os.replace)."""

    FILENAME = cfg.REFRESH_REQUEST_FILENAME

    def __init__(self, exchange_dir: Path, clock: Clock):
        self.exchange_dir = Path(exchange_dir)
        self.clock = clock

    @property
    def path(self) -> Path:
        return self.exchange_dir / self.FILENAME

    def read_existing(self) -> dict[str, int | None]:
        out: dict[str, int | None] = {s: None for s in SCOPES}
        try:
            raw = self.path.read_bytes()
            if len(raw) > 4096:
                return out
            doc = json.loads(raw.decode("utf-8"))
            req = doc.get("requests") if isinstance(doc, dict) else None
            if isinstance(req, dict):
                for s in SCOPES:
                    v = req.get(s)
                    if isinstance(v, int) and not isinstance(v, bool) and v >= 1:
                        out[s] = v
        except (OSError, ValueError, UnicodeDecodeError):
            pass
        return out

    def write(self, nonces: dict[str, int | None]) -> None:
        if not self.exchange_dir.is_dir():
            raise FileNotFoundError(f"exchange directory does not exist: {self.exchange_dir}")
        payload = {
            "schema": "roi-mcp/refresh-request",
            "schema_version": "1.0.0",
            "requests": {s: (int(nonces[s]) if nonces.get(s) is not None else None) for s in SCOPES},
            "requested_utc": format_utc(self.clock.now()),
        }
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        fd, tmp = tempfile.mkstemp(prefix=f"{self.FILENAME}.tmp-", dir=str(self.exchange_dir))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


@dataclass
class Outstanding:
    nonce: int
    written_mono: float
    baseline_seq: int | None
    baseline_verified: str | None
    served: bool = False  # set once a waiting call saw it served; a served request is no longer outstanding


class RefreshManager:
    """Keeps at most one outstanding nonce per scope; concurrent `fresh` calls for the same scope
    reuse it (PRD 13.2a)."""

    def __init__(self, writer: RefreshRequestWriter, clock: Clock, wait_s: float, poll_s: float = cfg.REFRESH_POLL_S):
        self.writer = writer
        self.clock = clock
        self.wait_s = max(0.0, min(float(wait_s), cfg.REFRESH_WAIT_MAX_S))
        self.poll_s = poll_s
        self.outstanding: dict[str, Outstanding] = {}
        self._last_nonce = 0
        self._lock: asyncio.Lock | None = None
        self._lock_loop: Any = None
        self.writes = 0

    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def _next_nonce(self) -> int:
        ms = int(self.clock.now().timestamp() * 1000)
        nonce = max(ms, self._last_nonce + 1)
        self._last_nonce = nonce
        return nonce

    async def request(self, scope: str, baseline_seq: int | None, baseline_verified: str | None,
                      served_nonce: int | None = None) -> Outstanding:
        """served_nonce: the heartbeat's refresh_served for this scope, as currently known to the caller."""
        if scope not in SCOPES:
            raise ValueError(f"unknown refresh scope {scope!r}")
        async with self._get_lock():
            now = self.clock.monotonic()
            cur = self.outstanding.get(scope)
            if cur is not None and isinstance(served_nonce, int) and served_nonce >= cur.nonce:
                cur.served = True  # served already, even if no waiting call has noticed yet
            # PRD 13.2a: reuse only a request that is still outstanding (not yet served). Reusing a served one
            # would answer a later call with a snapshot captured before that call was made.
            if cur is not None and not cur.served and now - cur.written_mono < max(self.wait_s, 0.001):
                return cur
            nonce = self._next_nonce()
            existing = self.writer.read_existing()
            nonces: dict[str, int | None] = {}
            for s in SCOPES:
                if s == scope:
                    nonces[s] = nonce
                elif s in self.outstanding:
                    nonces[s] = self.outstanding[s].nonce
                else:
                    nonces[s] = existing.get(s)
            self.writer.write(nonces)
            self.writes += 1
            out = Outstanding(nonce, now, baseline_seq, baseline_verified)
            self.outstanding[scope] = out
            log.info("refresh request written scope=%s nonce=%s", scope, nonce)
            return out

    def request_nowait_sync(self, scope: str) -> int | None:
        """Used only for the automatic static recovery (PRD 13.7 `static` scope). Never waits."""
        now = self.clock.monotonic()
        cur = self.outstanding.get(scope)
        if cur is not None and now - cur.written_mono < max(self.wait_s, 1.0) * 10:
            return None
        nonce = self._next_nonce()
        existing = self.writer.read_existing()
        nonces = {s: (nonce if s == scope else (self.outstanding[s].nonce if s in self.outstanding else existing.get(s)))
                  for s in SCOPES}
        try:
            self.writer.write(nonces)
        except OSError:
            return None
        self.writes += 1
        self.outstanding[scope] = Outstanding(nonce, now, None, None)
        return nonce


def served(hb_data: dict, scope: str, req: Outstanding, family: str) -> bool:
    """Heartbeat says the nonce was served AND the family's seq or last_verified_utc advanced."""
    rs = (hb_data.get("refresh_served") or {}).get(scope)
    if not isinstance(rs, int) or rs < req.nonce:
        return False
    fam = (hb_data.get("families") or {}).get(family) or {}
    seq = fam.get("seq")
    if isinstance(seq, int) and (req.baseline_seq is None or seq > req.baseline_seq):
        return True
    ver = parse_utc(fam.get("last_verified_utc"))
    base = parse_utc(req.baseline_verified)
    if ver is not None and (base is None or ver > base):
        return True
    return False


def baseline_of(hb_data: dict, family: str) -> tuple[Any, Any]:
    fam = (hb_data.get("families") or {}).get(family) or {}
    return fam.get("seq"), fam.get("last_verified_utc")
