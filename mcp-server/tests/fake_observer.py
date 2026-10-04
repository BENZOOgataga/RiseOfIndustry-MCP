"""Scripted FAKE OBSERVER: writes heartbeat/static/state/history files into a temporary exchange
directory per scenario, using the deterministic fixture builder and the test's fake clock."""

from __future__ import annotations

import copy
import json
import os
from datetime import timedelta
from pathlib import Path

import build_fixtures as bf
from conftest import FakeClock, FakeProcs, game_proc


class FakeObserver:
    def __init__(self, exchange: Path, clock: FakeClock, procs: FakeProcs, pid: int = bf.PID):
        self.dir = Path(exchange)
        self.clock = clock
        self.procs = procs
        self.pid = pid
        self.hb_seq = 1
        self.world_session = None
        self.static_doc = None
        self.state_doc = None
        self.history_doc = None
        self.state_seq = 10
        self.refresh_served = {"state": None, "history": None, "static": None}
        self.refresh_seen = {"state": None, "history": None, "static": None}
        self.min_keep_override = None
        self.verified = False  # the version gate runs at the first READY of the game process

    # ---------------------------------------------------------------- file helpers
    def _write(self, family: str, doc) -> None:
        p = self.dir / f"{family}.json"
        tmp = self.dir / f"{family}.json.tmp-{self.pid}"
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        os.replace(tmp, p)
        st = p.stat()
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000 * self.hb_seq))

    def heartbeat(self, state: str = "ready", **kw) -> dict:
        self.hb_seq += 1
        if state in ("starting", "menu", "loading", "disabled") and not self.verified:
            kw.setdefault("compatibility", "pending")
        args = dict(state=state, pid=self.pid, world_session=self.world_session, seq=self.hb_seq, static_doc=self.static_doc,
                    state_doc=self.state_doc, history_doc=self.history_doc, refresh_served=dict(self.refresh_served),
                    refresh_seen=dict(self.refresh_seen))
        args.update(kw)
        hb = bf.build_heartbeat(self.clock.now(), **args)
        self._write("heartbeat", hb)
        return hb

    # ---------------------------------------------------------------- lifecycle
    def start_process(self, start=None) -> None:
        self.procs.procs = [game_proc(self.pid, start or self.clock.now())]

    def kill_process(self) -> None:
        self.procs.procs = []

    def _compat(self) -> dict:
        return {} if self.verified else {"compatibility": "pending"}

    def menu(self) -> dict:
        self.world_session = None
        return self.heartbeat("menu", **self._compat())

    def loading(self) -> dict:
        self.world_session = None
        return self.heartbeat("loading", **self._compat())

    def ready(self, world_session: str = bf.WORLD_SESSION, publish: bool = True) -> dict:
        self.world_session = world_session
        self.verified = True
        if publish:
            self.publish_static()
            self.publish_state()
            self.publish_history()
        return self.heartbeat("ready")

    def publish_static(self) -> dict:
        self.static_doc = bf.build_static(self.clock.now(), seq=(self.static_doc or {}).get("seq", 0) + 1,
                                          world_session=self.world_session, pid=self.pid)
        self._write("static", self.static_doc)
        return self.static_doc

    def publish_state(self, data: dict | None = None, capture_age_s: float = 0.5) -> dict:
        self.state_seq += 1
        data = data if data is not None else bf.state_data()
        if self.min_keep_override is not None:
            data["routes_player"][0]["min_keep"]["value"] = self.min_keep_override
        self.state_doc = bf.build_state(self.clock.now(), seq=self.state_seq, static_doc=self.static_doc,
                                        world_session=self.world_session, pid=self.pid, data=data, capture_age_s=capture_age_s)
        self._write("state", self.state_doc)
        return self.state_doc

    def publish_history(self) -> dict:
        seq = (self.history_doc or {}).get("seq", 0) + 1
        self.history_doc = bf.build_history(self.clock.now(), seq=seq, static_doc=self.static_doc, world_session=self.world_session,
                                            pid=self.pid)
        self._write("history", self.history_doc)
        return self.history_doc

    def read_refresh_request(self) -> dict | None:
        p = self.dir / "refresh-request.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def serve_refresh(self, scope: str = "state") -> bool:
        """Serve the pending nonce for `scope` like the real observer: capture, publish, report served."""
        req = self.read_refresh_request()
        if not req or req["requests"].get(scope) is None:
            return False
        nonce = req["requests"][scope]
        self.refresh_seen[scope] = nonce
        if self.refresh_served.get(scope) is not None and self.refresh_served[scope] >= nonce:
            return False
        if scope == "state":
            self.publish_state()
        elif scope == "history":
            self.publish_history()
        else:
            self.publish_static()
        self.refresh_served[scope] = nonce
        self.heartbeat("ready")
        return True

    def unsupported_build(self) -> dict:
        self.world_session = None
        detected = dict(bf.GAME)
        detected["build"] = "0600a"
        detected["assembly_sha256"] = "0" * 64
        return self.heartbeat("unsupported_build", compatibility="unsupported_build", detected_game=detected,
                              envelope_game=detected)
