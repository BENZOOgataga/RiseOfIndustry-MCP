"""Records lifecycle transitions from heartbeat.json (read-only) for validation gates E1/E4.

    python scripts/validation/heartbeat_trace.py --out .local/validation/heartbeat-trace.jsonl [--minutes 120]

Writes one JSON line per change of (pid, state, world_session, compatibility, scene) plus a line per family
publication (seq change). Never writes into the exchange directory.
"""

import argparse
import json
import os
import time


def exchange_dir():
    d = os.environ.get("ROI_MCP_EXCHANGE_DIR")
    if d:
        return d
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "RoiMcp")


def read(path):
    for _ in range(3):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            time.sleep(0.05)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--minutes", type=float, default=180)
    ap.add_argument("--interval", type=float, default=0.5)
    a = ap.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    hb_path = os.path.join(exchange_dir(), "heartbeat.json")
    last_key, last_seq = None, {}
    deadline = time.time() + a.minutes * 60
    with open(a.out, "a", encoding="utf-8") as out:
        while time.time() < deadline:
            hb = read(hb_path)
            if hb:
                d = hb.get("data") or {}
                key = (hb.get("pid"), d.get("state"), d.get("world_session"), d.get("compatibility"), d.get("scene"))
                now = time.strftime("%Y-%m-%dT%H:%M:%S")
                if key != last_key:
                    rec = {"t": now, "event": "state", "pid": key[0], "state": key[1], "world_session": key[2],
                           "compatibility": key[3], "scene": key[4], "reason": d.get("state_reason"),
                           "game_date": d.get("game_date"), "errors_last_hour": d.get("errors_last_hour")}
                    out.write(json.dumps(rec) + "\n")
                    out.flush()
                    last_key = key
                fams = d.get("families") or {}
                for name, f in fams.items():
                    seq = (f or {}).get("seq")
                    if seq is not None and last_seq.get(name) != seq:
                        last_seq[name] = seq
                        lc = d.get("last_capture") or {}
                        out.write(json.dumps({"t": now, "event": "publish", "family": name, "seq": seq,
                                              "size": f.get("size_bytes"),
                                              "last_capture": {k: lc.get(k) for k in ("family", "main_thread_ms", "slices", "max_slice_ms", "alloc_bytes_approx", "gc_count_delta")}}) + "\n")
                        out.flush()
            time.sleep(a.interval)


if __name__ == "__main__":
    main()
