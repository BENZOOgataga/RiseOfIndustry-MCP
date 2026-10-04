# RESEARCH TOOLING ONLY - renders research/DATA-MAP.md from research/data-map.json; not production code.
# Keeps the human-readable and machine-readable data maps consistent (single source of truth = JSON).
#
# Usage: python research/tools/datamap/render_datamap.py
import json
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SRC = os.path.join(ROOT, "data-map.json")
DST = os.path.join(ROOT, "DATA-MAP.md")

DEFAULT_ASM = "Assembly-CSharp"
DEFAULT_NS = "ProjectAutomata"


def cell(v):
    if v is None:
        return ""
    if isinstance(v, list):
        v = "; ".join(str(x) for x in v)
    s = str(v).replace("|", "\\|").replace("\n", " ")
    return s


def main():
    with open(SRC, encoding="utf-8") as f:
        dm = json.load(f)

    out = []
    w = out.append
    g = dm["game"]
    w("# DATA-MAP: Rise of Industry concepts to internal members")
    w("")
    w("> Generated from `data-map.json` by `tools/datamap/render_datamap.py`. Edit the JSON, then re-render.")
    w("")
    w(f"Target: {g['title']} (Steam {g['steam_app_id']}, build {g['steam_build_id']}, version {g['game_version']}, "
      f"savegame {g['savegame_version']}, Unity {g['unity']} {g['scripting_backend']}).")
    w("")
    w("## How to read this map")
    w("")
    w(f"- **Asm / NS**: `AC / PA` means `{DEFAULT_ASM}.dll`, namespace `{DEFAULT_NS}` (the default). Other values are spelled out. `-` means a derived value.")
    w("- **Access**: the recommended read path (member paths and plain-language steps). It always runs on the Unity main thread.")
    w("- **Source**: where the value lives. " + "; ".join(f"`{k}` = {v}" for k, v in dm["enums"]["source"].items()))
    w("- **Safety**: " + "; ".join(f"`{k}` = {v}" for k, v in dm["enums"]["read_safety"].items()))
    w("- **Conf.**: " + "; ".join(f"`{k}` = {v}" for k, v in dm["enums"]["confidence"].items()))
    w("- **Avoid**: members that look like reads but must not be called, and why.")
    w("- **Evidence**: file:line in `research/_local/decompiled/Assembly-CSharp/ProjectAutomata/` unless prefixed; `notes/...` refers to `research/notes/`.")
    w("")
    w("## Global rules for every read")
    w("")
    for r in dm["global_rules"]:
        w(f"- {r}")
    w("")
    w("## Identifier strategies")
    w("")
    w("| Entity | Key | Stability / notes | Conf. | Evidence |")
    w("|---|---|---|---|---|")
    for k, v in dm["id_strategies"].items():
        notes = v.get("stability", "")
        if v.get("secondary_key"):
            notes = f"Secondary: {v['secondary_key']}. {notes}"
        w(f"| `{k}` | {cell(v['key'])} | {cell(notes)} | {cell(v.get('confidence', ''))} | {cell(v.get('evidence', []))} |")
    w("")

    for cat in dm["categories"]:
        w(f"## {cat['title']}")
        w("")
        w("| ID | Concept | Asm / NS | Type | Access | Kind | ID strategy | Source | Update | Safety | Conf. | Avoid | Evidence | Notes |")
        w("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for e in cat["entries"]:
            asm = e.get("assembly", DEFAULT_ASM)
            ns = e.get("namespace", DEFAULT_NS)
            if e.get("type", "").startswith("(derived)") or e.get("member_kind") == "derived":
                asmns = "-" if asm == DEFAULT_ASM and ns == DEFAULT_NS else f"{asm} / {ns}"
            elif asm == DEFAULT_ASM and ns == DEFAULT_NS:
                asmns = "AC / PA"
            else:
                asmns = f"{asm} / {ns}"
            w("| " + " | ".join([
                f"`{e['id']}`", cell(e["concept"]), cell(asmns), cell(e.get("type")), cell(e.get("access")),
                cell(e.get("member_kind")), cell(e.get("id_strategy")), cell(e.get("source")),
                cell(e.get("update_frequency")), cell(e.get("read_safety")), cell(e.get("confidence")),
                cell(e.get("avoid")), cell(e.get("evidence")), cell(e.get("notes")),
            ]) + " |")
        w("")

    w("## Unsupported, absent or unknown")
    w("")
    w("| Concept | Status | Notes |")
    w("|---|---|---|")
    for u in dm["unsupported_or_unknown"]:
        w(f"| {cell(u['concept'])} | {cell(u['status'])} | {cell(u['notes'])} |")
    w("")

    total = sum(len(c["entries"]) for c in dm["categories"])
    w(f"_{total} mapped concepts in {len(dm['categories'])} categories._")
    w("")
    with open(DST, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out))
    print(f"wrote {DST} ({total} entries)")


if __name__ == "__main__":
    main()
