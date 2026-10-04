"""Shared test harness: fake clock, fake process lister, temporary exchange directories."""

from __future__ import annotations

import asyncio
import copy
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE.parent / "src"))

import build_fixtures as bf  # noqa: E402
from roi_mcp.app import App  # noqa: E402
from roi_mcp.config import Clock, ProcInfo, ServerConfig  # noqa: E402

REPO = HERE.parent.parent
SCHEMA_DIR = REPO / "schemas"
TOOL_SCHEMA_DIR = SCHEMA_DIR / "tool-responses"


class FakeClock(Clock):
    def __init__(self, now: datetime = bf.BASE_TIME):
        self._now = now
        self._mono = 1000.0
        self.on_sleep = None

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)
        self._mono += seconds

    def set(self, now: datetime) -> None:
        delta = (now - self._now).total_seconds()
        self.advance(delta)

    async def sleep(self, seconds: float) -> None:
        self.advance(max(seconds, 0.001))
        if self.on_sleep is not None:
            self.on_sleep(self)
        await asyncio.sleep(0)


class FakeProcs:
    def __init__(self, procs=None):
        self.procs = list(procs) if procs is not None else [game_proc()]

    def __call__(self):
        return list(self.procs)


def game_proc(pid: int = bf.PID, start: datetime = bf.PROCESS_START, name: str = "Rise of Industry.exe") -> ProcInfo:
    return ProcInfo(pid, name, start.timestamp())


class Harness:
    def __init__(self, exchange: Path, clock: FakeClock, procs: FakeProcs, refresh_wait_s: float = 3.0):
        self.exchange = exchange
        self.clock = clock
        self.procs = procs
        self.refresh_wait_s = refresh_wait_s
        self.app = self.new_app()

    def new_app(self) -> App:
        cfg = ServerConfig(exchange_dir=self.exchange, schema_dir=SCHEMA_DIR, refresh_wait_s=self.refresh_wait_s,
                           clock=self.clock, process_lister=self.procs, heartbeat_cache_s=0.0, file_logging=False)
        app = App(cfg)
        app.store.retry_delay_s = 0.0
        return app

    def restart(self) -> App:
        self.app = self.new_app()
        return self.app

    def write(self, family: str, doc) -> None:
        bf.write_json(self.exchange / f"{family}.json", doc)

    def write_raw(self, family: str, text: str) -> None:
        p = self.exchange / f"{family}.json"
        p.write_text(text, encoding="utf-8")
        # make sure the stat signature changes even within one timestamp tick
        st = p.stat()
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    def write_world(self, world: dict, families=("heartbeat", "static", "state", "history")) -> None:
        for f in families:
            self.write(f, world[f])
            p = self.exchange / f"{f}.json"
            st = p.stat()
            os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    def call(self, name: str, args=None) -> dict:
        return asyncio.run(self.app.call(name, args or {}))

    async def acall(self, name: str, args=None) -> dict:
        return await self.app.call(name, args or {})


@pytest.fixture
def world():
    return copy.deepcopy(bf.build_world())


@pytest.fixture
def exchange(tmp_path, monkeypatch):
    d = tmp_path / "RoiMcp"
    d.mkdir()
    monkeypatch.setenv("ROI_MCP_EXCHANGE_DIR", str(d))
    return d


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def procs():
    return FakeProcs()


@pytest.fixture
def h(exchange, clock, procs, world):
    harness = Harness(exchange, clock, procs)
    harness.write_world(world)
    harness.world = world
    return harness


@pytest.fixture
def empty_h(exchange, clock, procs):
    return Harness(exchange, clock, procs)
