"""Supervised validation session recorder for gates E2, E4, E6 and E7 (read-only).

Run from the repository root while the user performs the manual steps:

    uv run --directory mcp-server python ../scripts/validation/session_harness.py --plan ../.local/validation/session/plan.json

The plan (kept under .local/, it names buildings of the user's save) lists the routes to watch:

    {"routes": {"A": {"origin": "<key>", "product": "<asset>", "destination": "<key>"}, "B": {...}, ...},
     "e2_expected": {"A": {"max_send": 7, "min_keep": 3}, "B": {"max_send": 7}},
     "e6_min_keep": 5, "e7_save_prefix": "roi-mcp-e7", "out_dir": "<dir under .local>"}

What it does, without ever touching the game:
  * polls heartbeat.json every 0.5 s and logs every lifecycle transition (E4);
  * when the planned routes first appear, records their MCP values before any manual change (E2 baseline);
  * watches route A without `fresh` until the E2 change appears, then with `fresh: true` every 4 s, timing each
    call (E6); calls get_finances(fresh) once and records which refresh scopes it requested;
  * while the game is loading, issues one `fresh` request and records how it is deferred (E6);
  * on each lifecycle state, records what the tools return (no stale data served as current, E4);
  * when a save whose name starts with the E7 prefix appears, copies the snapshots taken at that moment (E7);
  * after the game exits, checks that every exchange file still parses and validates (E4).
The MCP server's only write is refresh-request.json (for `fresh: true`); this script writes only into out_dir.
"""

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXCHANGE = Path(os.environ.get("ROI_MCP_EXCHANGE_DIR") or Path(os.environ["LOCALAPPDATA"]) / "RoiMcp")
SAVES = Path(os.environ["APPDATA"]) / "RiseOfIndustry"
GAME_PROCESS = "Rise of Industry.exe"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def read_json(path: Path):
    for _ in range(3):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            time.sleep(0.05)
    return None


def route_id(r: dict) -> str:
    return f"route:{r['origin']}|{r['product']}|{r['destination']}|own|0"


def summarize_route(body: dict) -> dict:
    meta = body.get("meta") or {}
    snap = meta.get("snapshot") or {}
    out = {"ok": body.get("ok"), "game_state": meta.get("game_state"), "world_session": meta.get("world_session"),
           "source": meta.get("source"), "stale": meta.get("stale"), "stale_reason": meta.get("stale_reason"),
           "captured_utc": snap.get("captured_utc"), "age_s": snap.get("age_s"), "warnings": meta.get("warnings")}
    if not body.get("ok"):
        out["error"] = (body.get("error") or {}).get("code")
        return out
    d = body.get("data") or {}
    r = d.get("route") or d
    ms, mk = r.get("max_send") or {}, r.get("min_keep") or {}
    out.update({"max_send": ms.get("value"), "max_send_mode": ms.get("mode"), "max_send_unlimited": ms.get("unlimited"),
                "min_keep": mk.get("value"), "keep_all": mk.get("keep_all"),
                "origin_name": (r.get("origin") or {}).get("name"), "destination_name": (r.get("destination") or {}).get("name")})
    return out


class Recorder:
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        self.log = open(out_dir / "events.jsonl", "a", encoding="utf-8")
        self.fresh = open(out_dir / "fresh-calls.jsonl", "a", encoding="utf-8")

    def fresh_call(self, **data):
        self.fresh.write(json.dumps({"t": utc_now(), **data}, ensure_ascii=False) + "\n")
        self.fresh.flush()

    def event(self, kind: str, **data):
        rec = {"t": utc_now(), "event": kind, **data}
        self.log.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.log.flush()
        print(json.dumps(rec, ensure_ascii=False)[:400], flush=True)


def game_running() -> bool:
    import psutil
    for p in psutil.process_iter(["name"]):
        if (p.info.get("name") or "").lower() == GAME_PROCESS.lower():
            return True
    return False


def list_saves(prefix: str) -> dict:
    out = {}
    try:
        for e in os.scandir(SAVES):
            if e.is_file() and e.name.lower().endswith(".sav"):
                st = e.stat()
                out[e.name] = (st.st_mtime, st.st_size)
    except OSError:
        pass
    return out


def validate_exchange() -> dict:
    import jsonschema
    res = {}
    for fam in ("heartbeat", "static", "state", "history"):
        p = EXCHANGE / f"{fam}.json"
        if not p.exists():
            res[fam] = "missing"
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except ValueError as e:
            res[fam] = f"invalid JSON: {e}"
            continue
        schema = json.loads((REPO / "schemas" / f"{fam}.schema.json").read_text(encoding="utf-8"))
        errs = list(jsonschema.Draft202012Validator(schema).iter_errors(doc))
        res[fam] = "valid" if not errs else f"{len(errs)} schema error(s): {errs[0].message[:120]}"
    leftovers = [e.name for e in os.scandir(EXCHANGE) if ".tmp-" in e.name]
    res["temp_files_left"] = leftovers
    return res


async def main(plan_path: Path, max_hours: float):
    from mcp import Client, StdioServerParameters

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    out_dir = Path(plan["out_dir"]).resolve()
    if REPO / ".local" not in out_dir.parents:
        raise SystemExit("out_dir must be below <repo>/.local/")
    rec = Recorder(out_dir)
    routes = plan["routes"]
    rid = {k: route_id(v) for k, v in routes.items()}
    exp = plan.get("e2_expected", {})
    e6_value = plan.get("e6_min_keep")
    prefix = plan.get("e7_save_prefix", "roi-mcp-e7").lower()

    params = StdioServerParameters(command=sys.executable, args=["-m", "roi_mcp.server"], env=dict(os.environ))
    async with Client(params, read_timeout_seconds=120) as client:

        async def call(name, args):
            t0 = time.perf_counter()
            res = await client.call_tool(name, args)
            return json.loads(res.content[0].text), round(time.perf_counter() - t0, 3)

        rec.event("start", exchange_dir=str(EXCHANGE), plan_routes=rid)
        last_key = None
        baseline_done = False
        phase = "watch"  # watch (no fresh) -> fresh
        last_a = None
        finances_done = False
        loading_probe_session = None
        e6_seen = False
        saves_before = list_saves(prefix)
        rec.event("saves_at_start", count=len(saves_before))
        next_route_poll = 0.0
        next_save_poll = 0.0
        game_gone_since = None
        seen_running = False
        ready_sessions = []
        deadline = time.time() + max_hours * 3600

        while time.time() < deadline:
            now = time.time()
            hb = read_json(EXCHANGE / "heartbeat.json") or {}
            d = hb.get("data") or {}
            key = (hb.get("pid"), d.get("state"), d.get("world_session"), d.get("compatibility"), d.get("scene"))
            if key != last_key:
                rec.event("lifecycle", pid=key[0], state=key[1], world_session=key[2], compatibility=key[3], scene=key[4],
                          reason=d.get("state_reason"), errors_last_hour=d.get("errors_last_hour"),
                          refresh_served=d.get("refresh_served"), refresh_seen=d.get("refresh_seen"))
                # What the tools return in this state (E4: no stale data served as current).
                st, _ = await call("get_game_status", {})
                ra, dt = await call("get_route", {"route": rid["A"]})
                rec.event("tools_in_state", state=key[1], heartbeat_world_session=key[2],
                          status_game_state=(st.get("data") or {}).get("game", {}).get("state") if st.get("ok") else (st.get("error") or {}).get("code"),
                          route_a=summarize_route(ra), dt=dt)
                if key[1] == "ready" and key[2] and key[2] not in ready_sessions:
                    ready_sessions.append(key[2])
                if key[1] == "loading" and loading_probe_session != key[0:3]:
                    loading_probe_session = key[0:3]
                    before = read_json(EXCHANGE / "refresh-request.json")
                    rl, dt = await call("get_route", {"route": rid["A"], "fresh": True})
                    rec.event("e6_fresh_during_loading", response=summarize_route(rl), dt=dt,
                              refresh_request_before=before, refresh_request_after=read_json(EXCHANGE / "refresh-request.json"))
                last_key = key

            if d.get("state") == "ready" and now >= next_route_poll:
                next_route_poll = now + (4.0 if phase == "fresh" else 2.0)
                if not baseline_done:
                    vals = {}
                    for k in routes:
                        body, _ = await call("get_route", {"route": rid[k]})
                        vals[k] = summarize_route(body)
                    if vals["A"].get("ok"):
                        baseline_done = True
                        (out_dir / "e2-baseline.json").write_text(json.dumps(vals, indent=1, ensure_ascii=False), encoding="utf-8")
                        rec.event("e2_baseline", values={k: {f: v.get(f) for f in ("max_send", "max_send_mode", "min_keep", "keep_all", "origin_name", "destination_name")} for k, v in vals.items()})
                        last_a = vals["A"]
                elif phase == "watch":
                    body, dt = await call("get_route", {"route": rid["A"]})
                    a = summarize_route(body)
                    if a.get("ok") and last_a and (a.get("max_send"), a.get("max_send_mode"), a.get("min_keep")) != \
                            (last_a.get("max_send"), last_a.get("max_send_mode"), last_a.get("min_keep")):
                        rec.event("route_a_changed_periodic", before=last_a, after=a, dt=dt)
                    if a.get("ok"):
                        last_a = a
                    want = exp.get("A", {})
                    if a.get("ok") and all(a.get(f) == v for f, v in want.items()):
                        others = {}
                        for k in routes:
                            if k != "A":
                                b, _ = await call("get_route", {"route": rid[k]})
                                others[k] = summarize_route(b)
                        rec.event("e2_change_seen", route_a=a, others=others, expected=exp)
                        phase = "fresh"
                else:
                    if not finances_done:
                        finances_done = True
                        before = read_json(EXCHANGE / "refresh-request.json")
                        fb, dt = await call("get_finances", {"months": 2, "fresh": True})
                        rec.event("e6_finances_fresh", ok=fb.get("ok"), dt=dt, meta_source=(fb.get("meta") or {}).get("source"),
                                  refresh_request_before=before, refresh_request_after=read_json(EXCHANGE / "refresh-request.json"))
                    t_req = utc_now()
                    body, dt = await call("get_route", {"route": rid["A"], "fresh": True})
                    a = summarize_route(body)
                    rec.fresh_call(requested_utc=t_req, dt=dt, route_a=a, meta=body.get("meta"))
                    changed = a.get("ok") and last_a and (a.get("max_send"), a.get("max_send_mode"), a.get("min_keep")) != \
                        (last_a.get("max_send"), last_a.get("max_send_mode"), last_a.get("min_keep"))
                    if changed:
                        rec.event("route_a_changed_fresh", requested_utc=t_req, dt=dt, before=last_a, after=a)
                    if a.get("ok"):
                        if e6_value is not None and a.get("min_keep") == e6_value and not e6_seen:
                            e6_seen = True
                            rec.event("e6_change_seen_fresh", requested_utc=t_req, dt=dt, response=a)
                        last_a = a
                    elif dt > 0:
                        rec.event("fresh_call_not_ok", requested_utc=t_req, dt=dt, response=a)

            if now >= next_save_poll:
                next_save_poll = now + 2.0
                cur = list_saves(prefix)
                for name, (mt, size) in cur.items():
                    if saves_before.get(name) != (mt, size):
                        time.sleep(1.0)
                        if list_saves(prefix).get(name) != (mt, size):
                            continue  # still being written; next poll
                        is_e7 = name.lower().startswith(prefix)
                        rec.event("save_written", e7=is_e7, size=size,
                                  name=name if is_e7 or name.lower().startswith(("quicksave", "autosave")) else "(other)")
                        if is_e7:
                            snap_dir = out_dir / "e7-snapshots"
                            snap_dir.mkdir(exist_ok=True)
                            for fam in ("heartbeat", "state", "static", "history"):
                                src = EXCHANGE / f"{fam}.json"
                                if src.exists():
                                    shutil.copyfile(src, snap_dir / f"{fam}.json")
                            rec.event("e7_snapshots_copied", heartbeat_world_session=d.get("world_session"),
                                      state_seq=(read_json(EXCHANGE / "state.json") or {}).get("seq"))
                saves_before = cur

            running = game_running()
            seen_running = seen_running or running
            if not running and seen_running:
                game_gone_since = game_gone_since or now
                if now - game_gone_since > 5:
                    rec.event("game_exited", ready_sessions=ready_sessions, exchange_files=validate_exchange())
                    break
            else:
                game_gone_since = None
            await asyncio.sleep(0.5)
        rec.event("stop", ready_sessions=ready_sessions, e6_seen=e6_seen, phase=phase)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--max-hours", type=float, default=3.0)
    a = ap.parse_args()
    asyncio.run(main(Path(a.plan), a.max_hours))
