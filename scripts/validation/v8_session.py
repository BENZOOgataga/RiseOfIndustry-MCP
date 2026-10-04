"""Gate V8 session driver (PRD 22): watches for the user's V8 saves and runs the sweep between V8-b and V8-c.

Run from the repository root, before or after the user starts the game:

    uv run --directory mcp-server python ../scripts/validation/v8_session.py ab --out-dir ../.local/v8
    uv run --directory mcp-server python ../scripts/validation/v8_session.py c --out-dir ../.local/v8

`ab`: waits until `roi-mcp-v8-a.sav` and then `roi-mcp-v8-b.sav` appear in the saves folder and are stable,
records the heartbeat at each save, then immediately runs the 5-minute V8 sweep (v8_sweep.py) and exits.
It fails if `roi-mcp-v8-c.sav` appears before the sweep has finished.

`c`: waits until `roi-mcp-v8-c.sav` appears and is stable, records the heartbeat, then waits for the game
process to exit and records the final exchange-file state.

Read-only towards the game and the saves: the saves folder is only listed (names, sizes, mtimes); save files
are never opened here. Copies for analysis are made afterwards with scripts/backup-saves.ps1.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v8_sweep  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SAVES = Path(os.environ["APPDATA"]) / "RiseOfIndustry"
EXCHANGE = v8_sweep.EXCHANGE
NAMES = {"a": "roi-mcp-v8-a.sav", "b": "roi-mcp-v8-b.sav", "c": "roi-mcp-v8-c.sav"}


def set_run_tag(tag: str) -> None:
    """A repeated run uses its own save names (roi-mcp-v8-<tag>a.sav, ...); earlier runs' saves are kept."""
    for k in NAMES:
        NAMES[k] = f"roi-mcp-v8-{tag}{k}.sav" if tag else f"roi-mcp-v8-{k}.sav"


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def game_running() -> bool:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Rise of Industry.exe", "/NH"], capture_output=True, text=True).stdout
    return "Rise of Industry.exe" in out


def find_save(name: str) -> Path | None:
    for p in SAVES.iterdir() if SAVES.exists() else []:
        if p.name.lower() == name:
            return p
    return None


def wait_stable(name: str, log, poll=1.0, stable_s=3.0) -> dict:
    """Waits until the save exists and its size and mtime have not changed for stable_s seconds."""
    last, since = None, None
    while True:
        p = find_save(name)
        if p is not None:
            st = p.stat()
            sig = (st.st_size, st.st_mtime_ns)
            if sig != last:
                last, since = sig, time.time()
            elif time.time() - since >= stable_s:
                hb = v8_sweep.heartbeat()
                d = hb.get("data") or {}
                rec = {"save": p.name, "detected_utc": utc(), "size": st.st_size,
                       "mtime_utc": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
                       "heartbeat": {"state": d.get("state"), "paused": d.get("paused"), "speed_level": d.get("speed_level"),
                                     "world_session": d.get("world_session"), "game_date": d.get("game_date"),
                                     "game_day": d.get("game_day"), "errors_last_hour": d.get("errors_last_hour")}}
                log(rec)
                return rec
        time.sleep(poll)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["ab", "c"])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--minutes", type=float, default=5.0)
    ap.add_argument("--exit-timeout-min", type=float, default=120.0)
    ap.add_argument("--run-tag", default="", help="save-name tag for a repeated run, e.g. r2 -> roi-mcp-v8-r2a.sav")
    a = ap.parse_args()
    set_run_tag(a.run_tag)
    out = Path(a.out_dir).resolve()
    if REPO / ".local" not in out.parents and out != REPO / ".local":
        sys.exit("--out-dir must be below .local/")
    out.mkdir(parents=True, exist_ok=True)
    events = out / "session-events.jsonl"

    def log(rec):
        rec = {"phase": a.phase, **rec}
        with events.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(json.dumps(rec, ensure_ascii=False), flush=True)

    if a.phase == "ab":
        for k in ("a", "b", "c"):
            if find_save(NAMES[k]) is not None:
                sys.exit(f"{NAMES[k]} already exists; V8 needs fresh saves")
        log({"event": "waiting", "for": [NAMES["a"], NAMES["b"]], "utc": utc(), "game_running": game_running()})
        ra = wait_stable(NAMES["a"], log)
        rb = wait_stable(NAMES["b"], log)
        log({"event": "sweep_start", "utc": utc()})
        asyncio.run(v8_sweep.run(a.minutes, out / "sweep.json"))
        c_early = find_save(NAMES["c"]) is not None
        log({"event": "sweep_end", "utc": utc(), "v8_c_saved_during_sweep": c_early,
             "same_session_a_b": ra["heartbeat"]["world_session"] == rb["heartbeat"]["world_session"],
             "same_game_day_a_b": ra["heartbeat"]["game_day"] == rb["heartbeat"]["game_day"]})
        print(f"SWEEP DONE: save V8-c now ({NAMES['c'][:-4]}).", flush=True)
        sys.exit(1 if c_early else 0)

    rc = wait_stable(NAMES["c"], log)
    log({"event": "waiting_for_game_exit", "utc": utc()})
    deadline = time.time() + a.exit_timeout_min * 60
    while game_running() and time.time() < deadline:
        time.sleep(2)
    hb = v8_sweep.heartbeat()
    log({"event": "game_exit" if not game_running() else "exit_timeout", "utc": utc(),
         "final_heartbeat_state": (hb.get("data") or {}).get("state"), "c": rc["save"]})


if __name__ == "__main__":
    main()
