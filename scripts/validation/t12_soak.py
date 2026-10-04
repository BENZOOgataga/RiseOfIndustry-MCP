"""Gate T-12 soak driver (PRD 21): observer on for 2 h at 10x, then a kill-switch baseline, same game session.

Run from the repository root (it runs for hours; start it detached):

    uv run --directory mcp-server python ../scripts/validation/t12_soak.py --out-dir ../.local/t12

Phases:
  wait    until the heartbeat shows `ready`, unpaused, speed level 3 (10x) continuously for --settle-s seconds;
  on      --on-min minutes: scripts/perf-report.ps1 samples the heartbeat and the game's private bytes every 5 s;
          every 60 s this script records the heartbeat, validates the exchange files against their schemas,
          records file and log sizes, system memory and game process counters, and makes representative MCP
          reads through the real stdio server (several with `fresh: true`), each validated against its schema;
  off     creates the kill switch (`observer.disabled`), waits for the `disabled` state, settles, then samples
          --off-min minutes the same way (MCP: get_game_status only);
  verify  removes the kill switch, waits for the observer to report the game again and compares the game day
          advance with 10x speed over the whole off period;
  exit    waits for the game to exit (the user quits normally), then records final file and log state.

Read-only towards the game and the saves. Writes only below --out-dir, plus the kill-switch file, which the
user authorised for this gate. The output names entities of the loaded save, so --out-dir must be below .local/.
"""

import argparse
import asyncio
import ctypes
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXCHANGE = Path(os.environ.get("ROI_MCP_EXCHANGE_DIR") or Path(os.environ["LOCALAPPDATA"]) / "RoiMcp")
SCHEMAS = REPO / "schemas"
KILL = EXCHANGE / "observer.disabled"
GAME = "Rise of Industry"
SECONDS_PER_DAY_1X = 8.0


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def read_json(path: Path):
    for _ in range(5):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.2)
    return None


def hb_data() -> dict:
    return (read_json(EXCHANGE / "heartbeat.json") or {}).get("data") or {}


def game_pid() -> int | None:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {GAME}.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if parts and parts[0].lower() == f"{GAME}.exe".lower():
            return int(parts[1])
    return None


def process_counters() -> dict:
    cmd = (f"$p = Get-Process -Name '{GAME}' -ErrorAction SilentlyContinue | Select-Object -First 1; "
           "if ($p) { [pscustomobject]@{private=$p.PrivateMemorySize64; ws=$p.WorkingSet64; handles=$p.HandleCount; "
           "threads=$p.Threads.Count; cpu_s=$p.TotalProcessorTime.TotalSeconds; responding=$p.Responding} | ConvertTo-Json -Compress }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True).stdout.strip()
    try:
        return json.loads(out) if out else {}
    except ValueError:
        return {}


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def system_memory() -> dict:
    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return {"load_pct": m.dwMemoryLoad, "total": m.ullTotalPhys, "avail": m.ullAvailPhys,
            "commit_total": m.ullTotalPageFile, "commit_avail": m.ullAvailPageFile}


def file_sizes() -> dict:
    names = ["heartbeat.json", "state.json", "static.json", "history.json", "observer.log", "observer.log.1",
             "server.log", "server.log.1", "server.log.2", "server.log.3", "refresh-request.json"]
    return {n: (EXCHANGE / n).stat().st_size if (EXCHANGE / n).exists() else None for n in names}


class Validator:
    def __init__(self):
        from jsonschema import Draft202012Validator
        self.v = {f: Draft202012Validator(json.loads((SCHEMAS / f"{f}.schema.json").read_text(encoding="utf-8")))
                  for f in ("heartbeat", "static", "state", "history")}
        self.tools = {}

    def exchange(self) -> dict:
        out = {}
        for f, v in self.v.items():
            doc = read_json(EXCHANGE / f"{f}.json")
            if doc is None:
                out[f] = {"valid": False, "error": "unreadable"}
                continue
            errs = list(v.iter_errors(doc))
            out[f] = {"valid": not errs, "seq": doc.get("seq"), "world_session": doc.get("world_session"),
                      "error": (f"{'/'.join(map(str, errs[0].absolute_path))}: {errs[0].message[:160]}" if errs else None)}
        return out

    def tool(self, name: str, body: dict) -> str | None:
        from jsonschema import Draft202012Validator
        if name not in self.tools:
            self.tools[name] = Draft202012Validator(json.loads((SCHEMAS / "tool-responses" / f"{name}.schema.json").read_text(encoding="utf-8")))
        errs = list(self.tools[name].iter_errors(body))
        return f"{'/'.join(map(str, errs[0].absolute_path))}: {errs[0].message[:160]}" if errs else None


def hb_summary(d: dict) -> dict:
    fs = d.get("frame_stats") or {}
    lc = d.get("last_capture") or {}
    fam = d.get("families") or {}
    return {"state": d.get("state"), "paused": d.get("paused"), "speed_level": d.get("speed_level"),
            "time_scale": d.get("time_scale"), "world_session": d.get("world_session"), "game_day": d.get("game_day"),
            "game_date": d.get("game_date"), "degraded": d.get("degraded"), "disabled_sections": d.get("disabled_sections"),
            "optional_sections_suppressed": d.get("optional_sections_suppressed"), "errors_last_hour": d.get("errors_last_hour"),
            "publish_failures": d.get("publish_failures"), "log_dropped": d.get("log_dropped"),
            "effective_interval_s": d.get("effective_interval_s"),
            "seq": {k: (v or {}).get("seq") for k, v in fam.items()},
            "size": {k: (v or {}).get("size_bytes") for k, v in fam.items()},
            "frame": {k: fs.get(k) for k in ("frames", "frame_ms_avg", "frame_ms_p95", "frame_ms_p99", "frames_over_50ms",
                                             "observer_ms_p99", "observer_ms_max", "observer_ticks_with_gc")},
            "last_capture": {k: lc.get(k) for k in ("family", "utc", "main_thread_ms", "alloc_bytes", "max_slice_ms", "size_bytes")}}


class Mcp:
    """Representative reads through the real stdio server (one session for the whole run)."""

    def __init__(self, validator: Validator):
        self.val = validator
        self.client = None
        self.ids = {}
        self.stats = {"calls": 0, "ok": 0, "fresh": 0, "errors": Counter(), "warnings": Counter(), "schema_invalid": 0,
                      "internal_error": 0, "refresh_timeout": 0, "negative_age": 0, "max_s": 0.0, "problems": []}

    async def call(self, name, args):
        t0 = time.perf_counter()
        res = await self.client.call_tool(name, args)
        dt = time.perf_counter() - t0
        body = json.loads(res.content[0].text)
        s = self.stats
        s["calls"] += 1
        s["fresh"] += 1 if args.get("fresh") else 0
        s["max_s"] = max(s["max_s"], round(dt, 3))
        bad = self.val.tool(name, body)
        if bad:
            s["schema_invalid"] += 1
            s["problems"].append({"utc": utc(), "tool": name, "kind": "schema_invalid", "detail": bad})
        meta = body.get("meta") or {}
        for w in meta.get("warnings") or []:
            s["warnings"][w.get("code")] += 1
            if w.get("code") == "refresh_timeout":
                s["refresh_timeout"] += 1
        for snap in meta.get("snapshots") or []:
            if snap.get("age_s") is not None and snap["age_s"] < 0:
                s["negative_age"] += 1
        if body.get("ok"):
            s["ok"] += 1
        else:
            code = (body.get("error") or {}).get("code")
            s["errors"][code] += 1
            if code == "internal_error":
                s["internal_error"] += 1
                s["problems"].append({"utc": utc(), "tool": name, "kind": "internal_error", "detail": body["error"].get("message")})
        return body

    async def discover(self):
        b = await self.call("list_buildings", {"limit": 5, "sort": "produced_last_month"})
        self.ids["building"] = ((b.get("data") or {}).get("buildings") or [{}])[0].get("id")
        r = await self.call("list_routes", {"limit": 5})
        self.ids["route"] = ((r.get("data") or {}).get("routes") or [{}])[0].get("route_id")

    async def round_on(self) -> dict:
        out = {}
        st = await self.call("get_game_status", {})
        out["status_state"] = ((st.get("data") or {}).get("game") or {}).get("state")
        c = await self.call("get_company", {"fresh": True})
        cash = (c.get("data") or {}).get("cash")
        out["cash"] = cash
        await self.call("list_routes", {"fresh": True, "limit": 25})
        if self.ids.get("building"):
            await self.call("get_building", {"building": self.ids["building"], "fresh": True})
        if self.ids.get("route"):
            await self.call("get_route", {"route": self.ids["route"]})
        await self.call("get_finances", {"fresh": True, "months": 3})
        await self.call("find_production_issues", {"limit": 25})
        await self.call("get_market", {"include": ["state"]})
        await self.call("search", {"query": "a", "limit": 10})
        return out

    async def round_off(self) -> dict:
        st = await self.call("get_game_status", {})
        return {"status_state": ((st.get("data") or {}).get("game") or {}).get("state")}


def summary_of(samples: list) -> dict:
    def col(path):
        out = []
        for x in samples:
            v = x
            for p in path:
                v = (v or {}).get(p) if isinstance(v, dict) else None
            if v is not None:
                out.append(v)
        return out
    return {"samples": len(samples),
            "states": dict(Counter(str(x["hb"]["state"]) for x in samples)),
            "paused": dict(Counter(str(x["hb"]["paused"]) for x in samples)),
            "speed_levels": dict(Counter(str(x["hb"]["speed_level"]) for x in samples)),
            "world_sessions": dict(Counter(str(x["hb"]["world_session"]) for x in samples)),
            "errors_last_hour": sorted(set(col(["hb", "errors_last_hour"]))),
            "degraded": dict(Counter(str(x["hb"]["degraded"]) for x in samples)),
            "disabled_sections": sorted({json.dumps(x["hb"]["disabled_sections"]) for x in samples}),
            "publish_failures": sorted(set(col(["hb", "publish_failures"]))),
            "log_dropped": sorted(set(col(["hb", "log_dropped"]))),
            "exchange_invalid": sum(1 for x in samples for v in (x.get("exchange") or {}).values() if not v["valid"]),
            "private_bytes": [min(col(["proc", "private"]), default=None), max(col(["proc", "private"]), default=None)],
            "handles": [min(col(["proc", "handles"]), default=None), max(col(["proc", "handles"]), default=None)],
            "not_responding": sum(1 for x in samples if (x.get("proc") or {}).get("responding") is False)}


async def run(a):
    out = Path(a.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    events = out / "events.jsonl"
    samples_file = out / "samples.jsonl"

    def log(**rec):
        rec = {"utc": utc(), **rec}
        with events.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(json.dumps(rec, ensure_ascii=False), flush=True)

    if KILL.exists():
        log(event="abort", reason="kill switch present before the soak")
        return 2
    val = Validator()
    log(event="waiting", want="ready, unpaused, speed_level 3 (10x)", settle_s=a.settle_s, files=file_sizes())

    # ---- wait for ready + 10x, held for settle_s
    since = None
    while True:
        d = hb_data()
        good = d.get("state") == "ready" and d.get("paused") is False and d.get("speed_level") == 3
        if good:
            since = since or time.time()
            if time.time() - since >= a.settle_s:
                break
        else:
            since = None
        time.sleep(2)
    start_hb = hb_summary(hb_data())
    log(event="conditions_met", heartbeat=start_hb, pid=game_pid(), process=process_counters(), system=system_memory(),
        files=file_sizes(), exchange=val.exchange())

    from mcp import Client, StdioServerParameters
    params = StdioServerParameters(command=sys.executable, args=["-m", "roi_mcp.server"], env=dict(os.environ))
    async with Client(params, read_timeout_seconds=120) as client:
        mcp = Mcp(val)
        mcp.client = client
        await mcp.discover()

        async def phase(label: str, minutes: float, on: bool) -> list:
            perf = subprocess.Popen(["pwsh", "-NoProfile", "-File", str(REPO / "scripts" / "perf-report.ps1"), "-Label", label,
                                     "-Minutes", str(minutes), "-IntervalSeconds", "5", "-SampleProcess", "-OutDir", str(out)],
                                    stdout=open(out / f"perf-{label}.out", "w"), stderr=subprocess.STDOUT)
            log(event="phase_start", phase=label, minutes=minutes)
            samples = []
            end = time.time() + minutes * 60
            while time.time() < end:
                t0 = time.time()
                pid = game_pid()
                rec = {"utc": utc(), "phase": label, "pid": pid, "hb": hb_summary(hb_data()), "proc": process_counters(),
                       "system": system_memory(), "files": file_sizes(), "exchange": val.exchange()}
                rec["mcp"] = await (mcp.round_on() if on else mcp.round_off())
                samples.append(rec)
                with samples_file.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                if pid is None:
                    log(event="game_exited_during_phase", phase=label)
                    break
                if rec["hb"]["state"] == "faulted":
                    log(event="observer_faulted", phase=label)
                    break
                await asyncio.sleep(max(0.0, 60.0 - (time.time() - t0)))
            perf.wait()
            log(event="phase_end", phase=label, summary=summary_of(samples))
            return samples

        on_samples = await phase("t12_on", a.on_min, True)
        if game_pid() is None or (on_samples and on_samples[-1]["hb"]["state"] == "faulted"):
            log(event="abort", reason="game gone or observer faulted after the on phase", mcp=dict(mcp.stats, errors=dict(mcp.stats["errors"]), warnings=dict(mcp.stats["warnings"])))
            return 1

        # ---- kill switch
        before_off = hb_summary(hb_data())
        KILL.write_text("", encoding="utf-8")
        t_kill = time.time()
        while hb_data().get("state") != "disabled" and time.time() - t_kill < 30:
            await asyncio.sleep(0.5)
        log(event="kill_switch_on", heartbeat_before=before_off, state_after=hb_data().get("state"),
            seconds_to_disabled=round(time.time() - t_kill, 1))
        await asyncio.sleep(a.off_settle_s)
        off_samples = await phase("t12_off", a.off_min, False)

        # ---- verify 10x held through the off period
        KILL.unlink(missing_ok=True)
        t_rm = time.time()
        d = {}
        while time.time() - t_rm < 120:
            d = hb_data()
            if d.get("state") == "ready" and d.get("game_day") is not None:
                break
            await asyncio.sleep(1)
        after = hb_summary(d)
        elapsed = time.time() - t_kill
        days = None if after["game_day"] is None or before_off["game_day"] is None else after["game_day"] - before_off["game_day"]
        expected = elapsed / (SECONDS_PER_DAY_1X / 10.0)
        log(event="kill_switch_off", seconds_to_ready=round(time.time() - t_rm, 1), heartbeat_after=after,
            off_period_s=round(elapsed, 1), game_days_advanced=days, expected_days_at_10x=round(expected, 1),
            ratio=(round(days / expected, 3) if days is not None else None))
        await asyncio.sleep(30)
        final_round = await mcp.round_on()
        log(event="mcp_totals", mcp=dict(mcp.stats, errors=dict(mcp.stats["errors"]), warnings=dict(mcp.stats["warnings"])),
            final_round=final_round)

    # ---- wait for the user to quit the game
    log(event="waiting_for_game_exit", pid=game_pid())
    deadline = time.time() + a.exit_timeout_min * 60
    last_proc = process_counters()
    while game_pid() is not None and time.time() < deadline:
        last_proc = process_counters() or last_proc
        time.sleep(5)
    log(event="game_exit" if game_pid() is None else "exit_timeout", last_process=last_proc, files=file_sizes(),
        exchange=val.exchange(), final_heartbeat=hb_summary(hb_data()))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--on-min", type=float, default=120.0)
    ap.add_argument("--off-min", type=float, default=60.0)
    ap.add_argument("--settle-s", type=float, default=120.0)
    ap.add_argument("--off-settle-s", type=float, default=60.0)
    ap.add_argument("--exit-timeout-min", type=float, default=600.0)
    a = ap.parse_args()
    out = Path(a.out_dir).resolve()
    if REPO / ".local" not in out.parents:
        sys.exit("--out-dir must be below .local/")
    sys.exit(asyncio.run(run(a)))


if __name__ == "__main__":
    main()
