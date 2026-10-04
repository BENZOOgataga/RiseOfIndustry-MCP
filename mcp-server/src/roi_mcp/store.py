"""Snapshot store (PRD 11.4, 13.1), liveness classification and staleness (PRD 11.7), and the
bounded in-memory state window (PRD 12.4).

The store only ever READS files in the exchange directory.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import config as cfg
from .config import Clock, ProcInfo
from .schemas import SchemaSet
from .util import parse_utc

log = logging.getLogger("roi_mcp.store")

SNAPSHOT_FAMILIES = ("static", "state", "history")


@dataclass
class Snapshot:
    family: str
    doc: dict
    seq: int | None
    content_hash: str | None
    world_session: str | None
    pid: int | None
    schema_version: str | None
    captured_end: datetime | None
    sig: tuple
    loaded_at: float

    @property
    def data(self) -> dict:
        d = self.doc.get("data")
        return d if isinstance(d, dict) else {}

    @property
    def captured(self) -> dict:
        c = self.doc.get("captured")
        return c if isinstance(c, dict) else {}

    @property
    def static_ref(self) -> dict | None:
        r = self.doc.get("static_ref")
        return r if isinstance(r, dict) else None

    @property
    def sections(self) -> dict:
        s = self.doc.get("sections")
        return s if isinstance(s, dict) else {}


@dataclass
class FamilyStatus:
    current: Snapshot | None = None
    last_sig: tuple | None = None
    invalid: str | None = None          # error of the latest file read (None when it loaded)
    mismatch: dict | None = None        # {"file_version", "server_major"} when the latest file has another major
    missing: bool = True
    reload_count: int = 0


def _major(version: Any) -> int | None:
    if not isinstance(version, str):
        return None
    head = version.split(".", 1)[0]
    return int(head) if head.isdigit() else None


class SnapshotStore:
    def __init__(self, exchange_dir: Path, schemas: SchemaSet, clock: Clock,
                 heartbeat_cache_s: float = cfg.HEARTBEAT_CACHE_S, retry_delay_s: float = cfg.READ_RETRY_DELAY_S,
                 on_state_loaded: Callable[[Snapshot], None] | None = None):
        self.exchange_dir = Path(exchange_dir)
        self.schemas = schemas
        self.clock = clock
        self.heartbeat_cache_s = heartbeat_cache_s
        self.retry_delay_s = retry_delay_s
        self.families: dict[str, FamilyStatus] = {f: FamilyStatus() for f in ("heartbeat",) + SNAPSHOT_FAMILIES}
        self._hb_checked_at: float | None = None
        self._lock = threading.RLock()
        self._static_reloaded_for: set[tuple] = set()
        self.on_state_loaded = on_state_loaded
        # Parsed + validated files prepared off the call path by the prefetcher: family -> (sig, kind, payload)
        self._prepared: dict[str, tuple] = {}
        self._prepared_lock = threading.Lock()

    # ------------------------------------------------------------------ file loading

    def path(self, family: str) -> Path:
        return self.exchange_dir / f"{family}.json"

    def _read_once(self, family: str, path: Path) -> tuple[str, Any]:
        """Return ('ok', doc) | ('invalid', reason) | ('mismatch', version)."""
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return "missing", None
        except OSError as exc:
            return "invalid", f"read_error: {exc.__class__.__name__}"
        if not raw.strip():
            return "invalid", "empty_file"
        try:
            doc = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return "invalid", f"json_error: {exc}"
        if not isinstance(doc, dict):
            return "invalid", "not_an_object"
        expected = f"roi-mcp/{family}"
        if doc.get("schema") != expected:
            return "invalid", f"wrong_schema: {doc.get('schema')!r} (expected {expected})"
        version = doc.get("schema_version")
        major = _major(version)
        if major is None:
            return "invalid", f"bad_schema_version: {version!r}"
        if major != cfg.SUPPORTED_SCHEMA_MAJOR:
            return "mismatch", version
        err = self.schemas.validate(family, doc)
        if err is not None:
            return "invalid", f"schema_validation: {err}"
        if family != "heartbeat" and doc.get("compatibility") != "verified":
            return "invalid", f"compatibility_not_verified: {doc.get('compatibility')!r}"
        return "ok", doc

    def load(self, family: str, force: bool = False) -> FamilyStatus:
        with self._lock:
            st = self.families[family]
            path = self.path(family)
            try:
                stat = path.stat()
            except FileNotFoundError:
                st.missing = True
                st.last_sig = None
                st.invalid = None
                st.mismatch = None
                return st
            except OSError as exc:
                st.invalid = f"stat_error: {exc.__class__.__name__}"
                return st
            st.missing = False
            sig = (stat.st_mtime_ns, stat.st_size)
            if not force and sig == st.last_sig:
                return st
            prepared = self._take_prepared(family, sig)
            kind, payload = prepared if prepared is not None else self._read_once(family, path)
            if kind == "invalid":
                # PRD 11.4: retry once after 100 ms.
                if self.retry_delay_s > 0:
                    time.sleep(self.retry_delay_s)
                try:
                    stat = path.stat()
                    sig = (stat.st_mtime_ns, stat.st_size)
                except OSError:
                    pass
                kind, payload = self._read_once(family, path)
            st.last_sig = sig
            if kind == "missing":
                st.missing = True
                st.last_sig = None
                return st
            if kind == "invalid":
                st.invalid = str(payload)
                st.mismatch = None
                log.warning("invalid %s.json: %s", family, payload)
                return st
            if kind == "mismatch":
                st.mismatch = {"file_version": payload, "server_major": cfg.SUPPORTED_SCHEMA_MAJOR,
                               "server_schema_version": self.schemas.version(family)}
                st.invalid = None
                log.warning("schema major mismatch for %s.json: %s", family, payload)
                return st
            doc = payload
            st.invalid = None
            st.mismatch = None
            seq = doc.get("seq")
            chash = doc.get("content_hash")
            cur = st.current
            if cur is not None and cur.seq == seq and cur.content_hash == chash and cur.world_session == doc.get("world_session") \
                    and family != "heartbeat":
                cur.sig = sig
                return st
            captured = doc.get("captured") if isinstance(doc.get("captured"), dict) else {}
            snap = Snapshot(family=family, doc=doc, seq=seq, content_hash=chash, world_session=doc.get("world_session"),
                            pid=doc.get("pid"), schema_version=doc.get("schema_version"),
                            captured_end=parse_utc(captured.get("utc_end")), sig=sig, loaded_at=self.clock.monotonic())
            st.current = snap
            st.reload_count += 1
            if family == "state" and self.on_state_loaded is not None:
                try:
                    self.on_state_loaded(snap)
                except Exception:  # never let window bookkeeping break loading
                    log.exception("state window update failed")
            return st

    def _take_prepared(self, family: str, sig: tuple) -> tuple[str, Any] | None:
        with self._prepared_lock:
            item = self._prepared.get(family)
            if item is not None and item[0] == sig:
                del self._prepared[family]
                return item[1], item[2]
        return None

    def prefetch_once(self) -> list[str]:
        """Parse and validate changed state/history/static files without holding the store lock, so a
        later tool call only swaps the prepared snapshot in. The heartbeat is never prefetched (it stays
        lazy, PRD 13.1). Returns the families prepared."""
        done = []
        for family in SNAPSHOT_FAMILIES:
            path = self.path(family)
            try:
                stat = path.stat()
            except OSError:
                continue
            sig = (stat.st_mtime_ns, stat.st_size)
            if sig == self.families[family].last_sig:
                continue
            with self._prepared_lock:
                item = self._prepared.get(family)
                if item is not None and item[0] == sig:
                    continue
            kind, payload = self._read_once(family, path)
            if kind == "missing":
                continue
            with self._prepared_lock:
                self._prepared[family] = (sig, kind, payload)
            done.append(family)
        return done

    def heartbeat(self, max_age_s: float | None = None) -> FamilyStatus:
        """Lazy heartbeat read with a short cache (PRD 13.1: at most every 1 s)."""
        ttl = self.heartbeat_cache_s if max_age_s is None else max_age_s
        now = self.clock.monotonic()
        with self._lock:
            if self._hb_checked_at is None or now - self._hb_checked_at >= ttl:
                self._hb_checked_at = now
                self.load("heartbeat")
            return self.families["heartbeat"]

    def snapshot_family(self, family: str) -> FamilyStatus:
        return self.load(family)

    def static_ref_matches(self, snap: Snapshot) -> bool:
        """PRD 11.4: check state/history `static_ref` against the loaded static; reload static once
        on mismatch. Returns True when it matches after (at most) one reload."""
        ref = snap.static_ref
        if not ref:
            return True
        st = self.load("static")
        if self._matches(ref, st.current):
            return True
        key = (snap.family, snap.seq, snap.content_hash, st.current.seq if st.current else None)
        if key not in self._static_reloaded_for:
            self._static_reloaded_for.add(key)
            st = self.load("static", force=True)
            if self._matches(ref, st.current):
                return True
        return False

    @staticmethod
    def _matches(ref: dict, static: Snapshot | None) -> bool:
        return static is not None and static.seq == ref.get("seq") and static.content_hash == ref.get("content_hash")


# ---------------------------------------------------------------------- liveness (PRD 11.7)

LIFECYCLE_ERROR = {
    "game_not_running": "game_not_running",
    "observer_not_detected": "observer_not_detected",
    "starting": "observer_not_detected",
    "observer_unresponsive": "observer_unresponsive",
    "menu": "at_main_menu",
    "loading": "loading",
    "disabled": "observer_disabled",
    "faulted": "observer_faulted",
    "unsupported_build": "unsupported_build",
}

LIFECYCLE_HINT = {
    "game_not_running": "Rise of Industry is not running. Start the game (via Steam) and load a save.",
    "observer_not_detected": "The game runs but no observer heartbeat matches it. Check the observer installation (docs/INSTALL.md) and the Mod Manager.",
    "starting": "The observer is starting (grace period after game start). Retry in a few seconds.",
    "observer_unresponsive": "The observer heartbeat stopped updating; the game may be hung or closing.",
    "menu": "The game is at the main menu. Load a save to get live data.",
    "loading": "A save is loading. Retry when loading has finished.",
    "disabled": "The observer is disabled (kill switch observer.disabled or enabled:false in observer.config.json).",
    "faulted": "The observer reported an unrecoverable fault; see observer.log.",
    "unsupported_build": "The running game build is not the verified baseline; V1 captures no data on other builds.",
}

# allow_stale reasons (PRD 11.7 stale_reason vocabulary).
# PRD-ambiguity: the vocabulary has no reason for observer_not_detected / starting / disabled / faulted;
# observer_not_detected maps to observer_unresponsive, the in-game-but-not-capturing states to not_in_game.
STALE_REASON = {
    "game_not_running": "game_not_running",
    "observer_unresponsive": "observer_unresponsive",
    "observer_not_detected": "observer_unresponsive",
    "starting": "not_in_game",
    "menu": "not_in_game",
    "loading": "not_in_game",
    "disabled": "not_in_game",
    "faulted": "not_in_game",
}


@dataclass
class Liveness:
    game_state: str
    running: bool
    pid: int | None = None
    process_start: datetime | None = None
    heartbeat: Snapshot | None = None
    heartbeat_age_s: float | None = None
    compatibility: str | None = None
    unsupported_build: bool = False
    heartbeat_mismatch: dict | None = None
    warnings: list = field(default_factory=list)

    @property
    def live(self) -> bool:
        return self.game_state == "ready" and not self.unsupported_build

    @property
    def hb_data(self) -> dict:
        if self.heartbeat is None:
            return {}
        d = self.heartbeat.doc.get("data")
        return d if isinstance(d, dict) else {}

    @property
    def world_session(self) -> str | None:
        if self.heartbeat is None:
            return None
        return self.hb_data.get("world_session") or self.heartbeat.world_session

    def error_code(self) -> str | None:
        if self.unsupported_build:
            return "unsupported_build"
        if self.heartbeat_mismatch is not None and self.running:
            return "schema_mismatch"
        return LIFECYCLE_ERROR.get(self.game_state)

    def hint(self) -> str | None:
        if self.unsupported_build:
            return LIFECYCLE_HINT["unsupported_build"]
        return LIFECYCLE_HINT.get(self.game_state)


def classify(procs: list[ProcInfo], hb_status: FamilyStatus, now: datetime) -> Liveness:
    """PRD 11.7 liveness classification."""
    hb = hb_status.current
    hb_doc = hb.doc if hb is not None else None
    hb_data = (hb_doc.get("data") or {}) if hb_doc else {}
    written = parse_utc(hb_data.get("written_utc") or (hb_doc or {}).get("written_utc"))
    hb_age = (now - written).total_seconds() if written else None
    compat = None
    if hb_doc is not None:
        compat = hb_doc.get("compatibility") or hb_data.get("compatibility")
    says_unsupported = hb_doc is not None and (
        hb_doc.get("compatibility") == "unsupported_build"
        or hb_data.get("compatibility") == "unsupported_build"
        or hb_data.get("state") == "unsupported_build")

    lv = Liveness(game_state="game_not_running", running=bool(procs), heartbeat=hb, heartbeat_age_s=hb_age,
                  compatibility=compat, heartbeat_mismatch=hb_status.mismatch)
    match = None
    if hb is not None:
        for p in procs:
            if p.pid == hb.pid:
                match = p
                break
    # PRD-ambiguity: "while the heartbeat reports unsupported_build" is applied when the latest heartbeat
    # says so and it belongs to a running game process, or when no game process runs at all (the last
    # known build was unsupported). A heartbeat from an older process does not block a newly started game.
    if says_unsupported and (match is not None or not procs):
        lv.unsupported_build = True
    if not procs:
        lv.game_state = "game_not_running"
        return lv
    newest = max(procs, key=lambda p: p.create_time)
    ref = match or newest
    lv.pid = ref.pid
    lv.process_start = datetime.fromtimestamp(ref.create_time, tz=now.tzinfo)
    since_start = now.timestamp() - ref.create_time
    if match is None or written is None or written.timestamp() < ref.create_time:
        lv.game_state = "starting" if since_start < cfg.OBSERVER_GRACE_S else "observer_not_detected"
        return lv
    if hb_age is not None and hb_age > cfg.HEARTBEAT_STALE_S:
        lv.game_state = "observer_unresponsive"
        return lv
    state = hb_data.get("state")
    if state in ("menu", "loading", "disabled", "unsupported_build", "faulted", "starting"):
        # PRD-ambiguity: heartbeat state "starting" is not in the PRD 11.7 list; it is reported as
        # "starting" and mapped to observer_not_detected (PRD 16 "Observer starting" row).
        lv.game_state = state
    elif state == "ready":
        lv.game_state = "ready"
        tick = parse_utc(hb_data.get("main_thread_last_tick_utc"))
        if tick is not None and (now - tick).total_seconds() > cfg.MAIN_THREAD_STALE_S:
            lv.warnings.append({"code": "game_unresponsive",
                                "detail": f"main thread last tick {round((now - tick).total_seconds(), 1)} s ago"})
    else:
        lv.game_state = "observer_not_detected"
    return lv


@dataclass
class Currency:
    current: bool
    stale: bool
    stale_reason: str | None
    age_s: float | None
    same_session: bool


def currency(snap: Snapshot, lv: Liveness, now: datetime) -> Currency:
    """PRD 11.7: a snapshot is current iff live, same world session, same pid and
    age <= max(15 s, 3 x effective_interval_s), with age measured from
    max(captured.utc_end, heartbeat families.<family>.last_verified_utc)."""
    hbd = lv.hb_data
    ref_time = snap.captured_end
    fam = ((hbd.get("families") or {}).get(snap.family) or {}) if hbd else {}
    lv_utc = parse_utc(fam.get("last_verified_utc")) if fam else None
    # last_verified applies to the content the observer last verified; only use it for the same content.
    if lv_utc is not None and (fam.get("content_hash") in (None, snap.content_hash)) \
            and fam.get("world_session") in (None, snap.world_session):
        if ref_time is None or lv_utc > ref_time:
            ref_time = lv_utc
    age = (now - ref_time).total_seconds() if ref_time else None
    same_session = (lv.world_session is not None and snap.world_session == lv.world_session
                    and lv.heartbeat is not None and snap.pid == lv.heartbeat.pid)
    if not lv.live:
        return Currency(False, True, STALE_REASON.get(lv.game_state, "not_in_game"), age, same_session)
    if not same_session:
        return Currency(False, True, "world_session_changed", age, False)
    eff = hbd.get("effective_interval_s")
    eff = float(eff) if isinstance(eff, (int, float)) and not isinstance(eff, bool) else 0.0
    limit = max(cfg.CURRENT_MIN_AGE_S, 3.0 * eff)
    if age is None or age > limit:
        return Currency(False, True, "age", age, True)
    return Currency(True, False, None, age, True)


# ---------------------------------------------------------------------- in-memory window (PRD 12.4)

@dataclass
class WindowEntry:
    seq: int | None
    world_session: str | None
    game_date: str | None
    captured_end: datetime | None
    counts: dict  # (building_key, product) -> count
    digest: dict | None = None  # V1.1 what_changed digest (advisor/window.py)


class StateWindow:
    """Last <= 20 state snapshots or 30 minutes of them (whichever is smaller), current world session
    only. Holds only inventory counts. Never persisted; cleared on world_session change."""

    def __init__(self, max_snapshots: int = cfg.WINDOW_MAX_SNAPSHOTS, max_age_s: float = cfg.WINDOW_MAX_AGE_S):
        self.max_snapshots = max_snapshots
        self.max_age_s = max_age_s
        self.entries: deque[WindowEntry] = deque()
        self.world_session: str | None = None

    def add(self, snap: Snapshot) -> None:
        if snap.world_session != self.world_session:
            self.entries.clear()
            self.world_session = snap.world_session
        if self.entries and self.entries[-1].seq == snap.seq and self.entries[-1].captured_end == snap.captured_end:
            return
        counts: dict = {}
        for b in snap.data.get("buildings_player") or []:
            key = b.get("key")
            for inv in b.get("inventory") or []:
                counts[(key, inv.get("product"))] = inv.get("count")
        try:
            from .advisor.window import digest
            dg = digest(snap.data)
        except Exception:  # a digest failure must never break loading; what_changed reports the entry as unusable
            log.exception("state window digest failed")
            dg = None
        entry = WindowEntry(snap.seq, snap.world_session, (snap.captured or {}).get("game_date"), snap.captured_end, counts, dg)
        # keep chronological order by capture time
        self.entries.append(entry)
        newest = entry.captured_end
        while len(self.entries) > self.max_snapshots:
            self.entries.popleft()
        if newest is not None:
            while self.entries and self.entries[0].captured_end is not None and \
                    (newest - self.entries[0].captured_end).total_seconds() > self.max_age_s:
                self.entries.popleft()

    def series(self, building_key: str, product: str, world_session: str | None) -> list[tuple[str | None, Any]]:
        if world_session is None or world_session != self.world_session:
            return []
        return [(e.game_date, e.counts.get((building_key, product))) for e in self.entries
                if (building_key, product) in e.counts]

    def snapshots(self, world_session: str | None) -> list[WindowEntry]:
        """Entries of the given world session in capture order (V1.1 what_changed)."""
        if world_session is None or world_session != self.world_session:
            return []
        return list(self.entries)

    def clear(self) -> None:
        self.entries.clear()
        self.world_session = None
