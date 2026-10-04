"""Gate T-12 evaluation (PRD 21, revised 2026-10-04): applies the criteria fixed in docs/VALIDATION-REPORT.md
("T-12 revised duration") to one run directory written by t12_soak.py and perf-report.ps1.

    python scripts/validation/t12_evaluate.py .local/t12/run1 [--out .local/t12/run1/evaluation.json]

Criteria (unchanged from the report): conditions and duration; no errors; memory M1 (growth), M2 (level),
M3 (trend) against R = range of the off-phase private bytes after warm-up; log sizes within cap.
Context only (not criteria): frame times, frames over 50 ms, observer work, captures, snapshot progression.
"""

import csv
import json
import math
import os
import statistics
import sys
from datetime import datetime
from pathlib import Path

ON_MIN, OFF_MIN = 45.0, 15.0
ON_WARMUP_MIN, OFF_WARMUP_MIN = 10.0, 3.0
EARLY = (10.0, 25.0)
LATE = (30.0, 45.0)
OBS_LOG_CAP, SRV_LOG_CAP = 1_000_000, 5 * 1024 * 1024
EXCHANGE = Path(os.environ.get("ROI_MCP_EXCHANGE_DIR") or Path(os.environ["LOCALAPPDATA"]) / "RoiMcp")


def t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def num(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def perf_rows(run: Path, label: str):
    files = sorted(run.glob(f"perf-{label}-*.csv"))
    if not files:
        return [], None
    rows = list(csv.DictReader(open(files[-1], encoding="utf-8-sig")))
    summ = sorted(run.glob(f"perf-{label}-*.json"))
    return rows, (json.load(open(summ[-1], encoding="utf-8-sig")) if summ else None)


def series(rows):
    t0 = t(rows[0]["utc"])
    return [((t(r["utc"]) - t0).total_seconds() / 60.0, num(r["private_bytes"])) for r in rows if num(r["private_bytes"]) is not None]


def window(ser, a, b):
    return [v for m, v in ser if a <= m < b]


def ols(xs, ys):
    n = len(xs)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    resid = [y - (my + slope * (x - mx)) for x, y in zip(xs, ys)]
    se = math.sqrt(sum(r * r for r in resid) / (n - 2) / sxx) if n > 2 else float("nan")
    tq = 2.04 if n - 2 >= 30 else {20: 2.09, 25: 2.06}.get(min((20, 25), key=lambda k: abs(k - (n - 2))), 2.1)
    return slope, se, tq


def main():
    run = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else run / "evaluation.json"
    events = [json.loads(l) for l in open(run / "events.jsonl", encoding="utf-8")]
    samples = [json.loads(l) for l in open(run / "samples.jsonl", encoding="utf-8")]
    ev = {e["event"] + (":" + e["phase"] if e.get("phase") else ""): e for e in events}
    on_rows, on_sum = perf_rows(run, "t12_on")
    off_rows, off_sum = perf_rows(run, "t12_off")
    res = {"run": run.name, "criteria": {}, "context": {}}
    C = res["criteria"]

    # ---- duration and conditions
    on_s = [s for s in samples if s["phase"] == "t12_on"]
    off_s = [s for s in samples if s["phase"] == "t12_off"]
    on_dur = (t(on_rows[-1]["utc"]) - t(on_rows[0]["utc"])).total_seconds() / 60 if on_rows else 0
    off_dur = (t(off_rows[-1]["utc"]) - t(off_rows[0]["utc"])).total_seconds() / 60 if off_rows else 0
    on_cond = all(r["state"] == "ready" and r["paused"] == "false" and r["speed_level"] == "3" for r in on_rows)
    sessions = {s["hb"]["world_session"] for s in on_s}
    ks = ev.get("kill_switch_off", {})
    ratio = ks.get("ratio")
    off_cond = all(r["state"] == "disabled" for r in off_rows) and ratio is not None and 0.9 <= ratio <= 1.1
    C["duration_and_conditions"] = {
        "pass": on_dur >= ON_MIN - 0.5 and off_dur >= OFF_MIN - 0.5 and on_cond and len(sessions) == 1 and off_cond,
        "on_minutes": round(on_dur, 2), "off_minutes": round(off_dur, 2), "on_rows": len(on_rows), "off_rows": len(off_rows),
        "on_all_ready_unpaused_10x": on_cond, "on_world_sessions": sorted(map(str, sessions)),
        "off_all_disabled": all(r["state"] == "disabled" for r in off_rows),
        "off_game_day_ratio_to_10x": ratio, "off_game_days": ks.get("game_days_advanced"), "off_expected_days": ks.get("expected_days_at_10x"),
        "kill_switch_seconds_to_disabled": ev.get("kill_switch_on", {}).get("seconds_to_disabled")}

    # ---- no errors
    offs = json.load(open(run / "log-offsets-pre.json"))
    obs_lines = open(EXCHANGE / "observer.log", encoding="utf-8", errors="replace").read().splitlines()[offs["observer_log_lines"]:]
    with open(EXCHANGE / "server.log", "rb") as f:
        f.seek(offs["server.log"])
        srv_lines = f.read().decode("utf-8", "replace").splitlines()
    end_utc = ev.get("kill_switch_off", {}).get("utc") or samples[-1]["utc"]
    obs_window = [l for l in obs_lines if l[:24] <= end_utc[:23] + "Z"]
    obs_err = [l for l in obs_window if " ERROR " in l]
    srv_err = [l for l in srv_lines if " ERROR " in l or "Traceback" in l]
    hb = [s["hb"] for s in samples]
    mcp = ev.get("mcp_totals", {}).get("mcp") or {}
    exch_invalid = [(s["utc"], k, v["error"]) for s in samples for k, v in s["exchange"].items() if not v["valid"]]
    not_resp = sum(1 for s in samples if (s.get("proc") or {}).get("responding") is False)
    no_game = sum(1 for s in samples if s.get("pid") is None)
    on_hb = [s["hb"] for s in on_s]
    C["no_errors"] = {
        "observer_log_error_lines": len(obs_err), "observer_log_error_samples": obs_err[:5],
        "heartbeat_errors_last_hour_max": max((h.get("errors_last_hour") or 0) for h in hb),
        "publish_failures_max": max((h.get("publish_failures") or 0) for h in hb),
        "log_dropped_max": max((h.get("log_dropped") or 0) for h in hb),
        "faulted_samples": sum(1 for h in hb if h.get("state") == "faulted"),
        "degraded_samples_on": sum(1 for h in on_hb if h.get("degraded")),
        "disabled_sections_on": sorted({json.dumps(h.get("disabled_sections")) for h in on_hb}),
        "server_log_error_lines": len(srv_err), "server_log_error_samples": srv_err[:5],
        "mcp_calls": mcp.get("calls"), "mcp_fresh": mcp.get("fresh"), "mcp_ok": mcp.get("ok"),
        "mcp_internal_error": mcp.get("internal_error"), "mcp_schema_invalid": mcp.get("schema_invalid"),
        "mcp_negative_age": mcp.get("negative_age"), "mcp_refresh_timeout": mcp.get("refresh_timeout"),
        "mcp_errors": mcp.get("errors"), "mcp_warnings": mcp.get("warnings"),
        "exchange_invalid_samples": exch_invalid[:5], "exchange_invalid_count": len(exch_invalid),
        "process_not_responding_samples": not_resp, "process_missing_samples": no_game,
    }
    n = C["no_errors"]
    n["pass"] = (n["observer_log_error_lines"] == 0 and n["heartbeat_errors_last_hour_max"] == 0 and n["publish_failures_max"] == 0
                 and n["log_dropped_max"] == 0 and n["faulted_samples"] == 0 and n["degraded_samples_on"] == 0
                 and n["disabled_sections_on"] in ([], ["[]"]) and n["server_log_error_lines"] == 0
                 and (mcp.get("internal_error") or 0) == 0 and (mcp.get("schema_invalid") or 0) == 0 and (mcp.get("negative_age") or 0) == 0
                 and n["exchange_invalid_count"] == 0 and not_resp == 0 and no_game == 0 and bool(mcp))

    # ---- memory
    son, soff = series(on_rows), series(off_rows)
    on_used = [(m, v) for m, v in son if m >= ON_WARMUP_MIN]
    off_used = [v for m, v in soff if m >= OFF_WARMUP_MIN]
    R = max(off_used) - min(off_used)
    early, late = window(son, *EARLY), window(son, *LATE)
    m1 = statistics.median(late) - statistics.median(early)
    m2 = abs(statistics.median(late) - statistics.median(off_used))
    per_min = {}
    for m, v in on_used:
        per_min.setdefault(int(m), []).append(v)
    xs = sorted(per_min)
    ys = [statistics.median(per_min[k]) for k in xs]
    slope, se, tq = ols(xs, ys)
    span = LATE[1] - ON_WARMUP_MIN
    all_on = [v for _, v in son]
    C["memory"] = {
        "R_off_range_bytes": R, "off_samples_used": len(off_used),
        "M1_growth_bytes": m1, "M1_pass": m1 <= R,
        "M2_level_bytes": m2, "M2_pass": m2 <= R,
        "M3_slope_bytes_per_min": slope, "M3_slope_ci95": [slope - tq * se, slope + tq * se],
        "M3_projected_over_window_bytes": slope * span, "M3_pass": slope * span <= R,
        "on_initial": all_on[0], "on_peak": max(all_on), "on_min": min(all_on), "on_final": all_on[-1],
        "on_median_early": statistics.median(early), "on_median_late": statistics.median(late),
        "off_min": min(off_used), "off_max": max(off_used), "off_median": statistics.median(off_used),
        "off_final": [v for _, v in soff][-1],
        "per_minute_medians_on": list(zip(xs, ys)),
    }
    C["memory"]["pass"] = C["memory"]["M1_pass"] and C["memory"]["M2_pass"] and C["memory"]["M3_pass"]

    # ---- logs within cap
    fs = [s["files"] for s in samples]
    obs_max = max(f["observer.log"] or 0 for f in fs)
    srv_max = max(f["server.log"] or 0 for f in fs)
    srv_rot = max(sum(1 for k in ("server.log.1", "server.log.2", "server.log.3") if f.get(k) is not None) for f in fs)
    C["logs_within_cap"] = {"observer_log_max": obs_max, "observer_log_rotated": any(f.get("observer.log.1") for f in fs),
                            "server_log_max": srv_max, "server_rotated_files_max": srv_rot,
                            "observer_log_growth": fs[-1]["observer.log"] - fs[0]["observer.log"],
                            "server_log_growth": fs[-1]["server.log"] - fs[0]["server.log"],
                            "pass": obs_max <= OBS_LOG_CAP and srv_max <= SRV_LOG_CAP and srv_rot <= 3}

    # ---- context
    def fr(sm):
        return (sm or {}).get("frame") or {}
    res["context"]["frames"] = {"on": fr(on_sum), "off": fr(off_sum)}
    if fr(on_sum).get("avg_frame_ms") and fr(off_sum).get("avg_frame_ms"):
        res["context"]["avg_frame_delta_pct"] = round((fr(on_sum)["avg_frame_ms"] / fr(off_sum)["avg_frame_ms"] - 1) * 100, 2)
    res["context"]["frames_over_50ms_per_hour"] = {
        k: (round(fr(sm)["frames_over_50ms_total"] / d * 60, 1) if fr(sm).get("frames_over_50ms_total") is not None and d else None)
        for k, sm, d in (("on", on_sum, on_dur), ("off", off_sum, off_dur))}
    res["context"]["captures_on"] = (on_sum or {}).get("captures")
    res["context"]["sizes_on"] = (on_sum or {}).get("sizes")
    seqs = [s["hb"]["seq"] for s in on_s]
    res["context"]["snapshot_progression_on"] = {k: [seqs[0].get(k), seqs[-1].get(k)] for k in ("state", "history", "static")}
    res["context"]["frame_avg_by_quarter_on"] = [round(statistics.fmean(
        [num(r["frame_ms_avg"]) for r in on_rows[i * len(on_rows) // 3:(i + 1) * len(on_rows) // 3] if num(r["frame_ms_avg"]) is not None]), 3)
        for i in range(3)]
    res["context"]["system_memory_load_pct"] = [samples[0]["system"]["load_pct"], max(s["system"]["load_pct"] for s in samples), samples[-1]["system"]["load_pct"]]
    res["context"]["handles"] = [samples[0]["proc"].get("handles"), max((s["proc"].get("handles") or 0) for s in samples), samples[-1]["proc"].get("handles")]
    res["context"]["cash"] = [on_s[0]["mcp"].get("cash"), on_s[-1]["mcp"].get("cash")] if on_s else None
    res["context"]["game_exit"] = ev.get("game_exit") or ev.get("exit_timeout")

    verdict = all(c["pass"] for c in C.values())
    res["verdict"] = "PASS" if verdict else "FAIL"
    out.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"verdict": res["verdict"], **{k: v["pass"] for k, v in C.items()}}, indent=1))


if __name__ == "__main__":
    main()
