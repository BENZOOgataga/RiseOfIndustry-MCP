# /// script
# requires-python = ">=3.11"
# dependencies = ["lz4>=4.3"]
# ///
"""
Validation tooling over Rise of Industry save COPIES (PRD 6, 18, 21 T-10, 22 E3/E7/V8).

Run with uv from the repository root:

    uv run scripts/validation/save_tools.py rank <dir> [--out .local/...json]
    uv run scripts/validation/save_tools.py extract <save-copy> --out .local/<file>.json
    uv run scripts/validation/save_tools.py compare-e7 <extract.json> <state.json> [--out .local/<file>.json]
    uv run scripts/validation/save_tools.py diff-v8 <a.sav> <b.sav> <c.sav> [--out .local/<file>.json]
    uv run scripts/validation/save_tools.py fixture <save-copy> --out .local/<dir>

Safety:
  * Only COPIES are parsed. Paths below %APPDATA%\\RiseOfIndustry (the live saves) are refused.
  * Saves are opened read-only by the research parser; nothing is ever written next to them.
  * Every output derived from a save is refused unless it is below <repo>/.local/ (gitignored).
  * The research parser (research/tools/save-inspect/roi_save.py) is loaded by file path at runtime;
    nothing from research/ is imported as a package.

Exit codes: 0 = ok / PASS, 1 = gate FAIL, 2 = usage or input error.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import importlib.util
import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ROOT = REPO_ROOT / ".local"
PARSER_PATH = REPO_ROOT / "research" / "tools" / "save-inspect" / "roi_save.py"

TOOL_VERSION = "1.0.0"

# Paths removed before the V8 structural diff (PRD 22 V8 noise baseline: header name, timestamp, camera).
V8_NORMALIZED_AWAY = [
    ("header", "name"),
    ("header", "timestamp"),
    ("header", "timestamp_utc"),
    ("camera",),
    ("managers", "ProjectAutomata.CameraManager"),
    ("managers", "ProjectAutomata.CameraRotationController"),
]

# Lists whose order carries meaning: an order-only change there is a real difference.
ORDER_SENSITIVE_FIELDS = {"_savegameSlots", "_queue", "modules"}

# Scalar lists longer than this are summarised by hashes in the V8 diff.
LONG_LIST = 256

# Key fields used to turn decoded dictionary payloads (FixedSerializationData) into keyed maps.
DECODED_KEY_FIELDS = ("actor", "actorId", "product", "name")


class ToolError(Exception):
    """Usage or input error (exit code 2)."""


# ---------------------------------------------------------------------------
# Path safety (pure)
# ---------------------------------------------------------------------------


def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(p)))


def is_under(child, parent) -> bool:
    """True when child equals parent or lies below it (case-insensitive on Windows, no FS access)."""
    c = _norm(child)
    p = _norm(parent)
    if c == p:
        return True
    if not p.endswith(os.sep):
        p += os.sep
    return c.startswith(p)


def live_saves_dir() -> Path | None:
    """%APPDATA%\\RiseOfIndustry: path only, never opened."""
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "RiseOfIndustry" if appdata else None


def check_input_copy(path) -> Path:
    """Refuses the live saves folder; requires an existing file."""
    p = Path(path)
    live = live_saves_dir()
    if live is not None and is_under(p, live):
        raise ToolError("refusing to read below %s: only save COPIES may be parsed (PRD 18)" % live)
    if not p.is_file():
        raise ToolError("not a file: %s" % p)
    return p


def check_output_path(path, local_root: Path = LOCAL_ROOT) -> Path:
    """Save-derived outputs must stay below <repo>/.local/ (gitignored)."""
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    if not is_under(p, local_root) or _norm(p) == _norm(local_root):
        raise ToolError("refusing output path %s: save-derived output must be below %s" % (p, local_root))
    return p


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=_json_default)
    os.replace(tmp, path)


def _json_default(o):
    if isinstance(o, (bytes, bytearray)):
        return {"$bytes": len(o), "sha256": hashlib.sha256(bytes(o)).hexdigest()}
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=str)
    return repr(o)


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


# ---------------------------------------------------------------------------
# Research parser loading (by file path; production code never imports research/)
# ---------------------------------------------------------------------------

_PARSER = None


def load_parser():
    global _PARSER
    if _PARSER is not None:
        return _PARSER
    if not PARSER_PATH.is_file():
        raise ToolError(
            "research save parser not found at %s. It is research tooling (PRD 6) and must be present in the "
            "checkout to parse save copies." % PARSER_PATH)
    spec = importlib.util.spec_from_file_location("roi_save_research_parser", str(PARSER_PATH))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError as e:
        raise ToolError("cannot load the research save parser (%s); run through 'uv run' so lz4 is installed" % e)
    _PARSER = mod
    return mod


def parse_save(path):
    """Parses a save copy. Returns (tree, timing, unparsed_blob_count)."""
    p = check_input_copy(path)
    rs = load_parser()
    tree, sp, timing = rs.load(str(p))
    return tree, timing, len(getattr(sp, "unparsed_blobs", []) or [])


# ---------------------------------------------------------------------------
# Pure helpers: keys, values
# ---------------------------------------------------------------------------


def building_key(prefab, x, y) -> str:
    """Observer building key (PRD 7.1, BuildingKeys.BaseKey): <prefab>@<x>,<y>."""
    return "%s@%d,%d" % (prefab if prefab is not None else "unknown", int(x), int(y))


def base_key(key: str | None) -> str | None:
    """Strips the observer collision suffix (#<guid8> or #i<instanceId>)."""
    if key is None:
        return None
    return key.split("#", 1)[0]


def collision_suffix(guid: str | None, instance_id=None) -> str:
    """Observer collision suffix (PRD 7.2): '#' + first 8 hex chars of the save GUID, else '#i<instanceId>'."""
    if guid and len(guid) >= 8:
        return "#" + guid.replace("-", "")[:8]
    return "#i%s" % instance_id


def assign_final_keys(items):
    """
    items: list of dicts with 'base_key' and 'guid'. Sets 'key' (suffixed on collision, like the observer)
    and returns {base_key: [guid, ...]} for every colliding base key.
    """
    counts = Counter(it["base_key"] for it in items)
    collisions = {}
    for it in items:
        b = it["base_key"]
        if counts[b] > 1:
            it["key"] = b + collision_suffix(it.get("guid"))
            collisions.setdefault(b, []).append(it.get("guid"))
        else:
            it["key"] = b
    return collisions


def route_tuple(origin, product, destination, source) -> str:
    return "%s|%s|%s|%s" % (origin, product, destination, source)


def assign_route_keys(routes):
    """Observer route key (RouteMath.Build): <origin>|<product>|<destination>|<source>|<n>, n per identical tuple in slot order."""
    seen = Counter()
    for r in routes:
        t = route_tuple(r["origin_key"], r["product"], r["destination_key"], r["source"])
        r["occurrence"] = seen[t]
        seen[t] += 1
        r["route_key"] = t + "|" + str(r["occurrence"])
    return routes


def prefab_name(v):
    if isinstance(v, dict):
        return v.get("$prefab")
    return v if isinstance(v, str) else None


def enum_value(v):
    if isinstance(v, dict) and "$enum" in v:
        return v.get("value")
    if isinstance(v, dict) and "value" in v and isinstance(v.get("value"), int):
        return v["value"]
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def actor_id_from_ctor(ctor):
    """Actor.id is the trailing int constructor parameter (State [1], HumanPlayer [2], Settlement [prefab, n], AiPlayer [..., n])."""
    ints = [p for p in (ctor or []) if isinstance(p, int) and not isinstance(p, bool)]
    return ints[-1] if ints else None


def short_type(object_type: str | None) -> str | None:
    return object_type.rsplit(".", 1)[-1] if object_type else None


def fixed_decoded(v):
    """Decoded FixedSerializationData payload or None (not decoded by the research parser)."""
    if isinstance(v, dict) and v.get("$fixed"):
        return v.get("decoded")
    return None


def header_timestamp_utc(ts):
    # Utils.GetTimestamp(): seconds since 1971-01-01 UTC (research/notes/save-format.md section 1).
    if not isinstance(ts, int):
        return None
    return (_dt.datetime(1971, 1, 1, tzinfo=_dt.timezone.utc) + _dt.timedelta(seconds=ts)).isoformat()


def game_date(days, months):
    if not isinstance(days, int) or not isinstance(months, int):
        return None
    return "Y%d-%02d-%02d" % (months // 12 + 1, months % 12 + 1, days % 30 + 1)


# ---------------------------------------------------------------------------
# Entity extraction (pure over the parser's tree shape)
# ---------------------------------------------------------------------------


def _fields(o):
    return (o or {}).get("fields") or {}


def _manager(tree, name):
    return (tree.get("managers") or {}).get(name) or {}


def iter_entities(tree):
    for mname, m in (tree.get("managers") or {}).items():
        for e in m.get("entities") or []:
            yield mname, e


def _max_accepted_for_building(b):
    """Destination-side max accepted per product, from the storage components (PRD 12.3.1)."""
    out = {}
    kind = None
    reason = None
    for c in b.get("components") or []:
        t = short_type(c.get("objectType"))
        f = _fields(c)
        if t == "ProductSpecificProductStorage" and "_maxAcceptedMap" in f:
            dec = fixed_decoded(f["_maxAcceptedMap"])
            kind = "product_specific"
            if dec is None:
                reason = "ProductSpecificProductStorage._maxAcceptedMap not decoded by the parser"
                continue
            for e in dec:
                out[e.get("product")] = e.get("value")
        elif t == "InfiniteStorage" and "_maxAccepted" in f:
            dec = fixed_decoded(f["_maxAccepted"])
            kind = "infinite"
            if dec is None:
                reason = "InfiniteStorage._maxAccepted not decoded by the parser"
                continue
            for e in dec:
                out[e.get("product")] = e.get("value")
        elif t in ("SingleProductStorage", "ReservableSingleProductStorage", "AdvancedSingleProductStorage") and "_maxAccepted" in f:
            kind = "single_product"
            out[prefab_name(f.get("_product"))] = f.get("_maxAccepted")
    if kind is None:
        return None, None, None
    if reason and not out:
        return None, kind, reason
    return out, kind, reason


def extract_entities(tree, source_name=None):
    """Entity extraction for validation (E7, T-10). Pure: works on the research parser's tree shape."""
    notes = []
    header = tree.get("header") or {}
    actors_raw = _manager(tree, "ProjectAutomata.ActorManager").get("entities") or []
    actors = []
    actor_by_guid = {}
    for a in actors_raw:
        f = _fields(a)
        row = {
            "guid": a.get("guid"),
            "type": short_type(a.get("objectType")),
            "actor_id": actor_id_from_ctor(a.get("constructorParams")),
            "name": f.get("_actorName") or f.get("_settlementName"),
        }
        actors.append(row)
        actor_by_guid[row["guid"]] = row

    bents = _manager(tree, "ProjectAutomata.BuildingManager").get("entities") or []
    comp_owner = {}
    ent_by_guid = {}
    for b in bents:
        ent_by_guid[b.get("guid")] = b
        for c in b.get("components") or []:
            comp_owner[c.get("guid")] = (b.get("guid"), short_type(c.get("objectType")))

    buildings = []
    for b in bents:
        cp = b.get("constructorParams") or []
        f = _fields(b)
        row = {"guid": b.get("guid"), "prefab": None, "x": None, "y": None, "rotation": None,
               "owner_guid": None, "owner_actor_id": None, "owner_type": None, "name": f.get("buildingName")}
        if len(cp) >= 5 and isinstance(cp[1], int) and isinstance(cp[2], int):
            row["prefab"] = prefab_name(cp[0])
            row["x"], row["y"] = cp[1], cp[2]
            row["rotation"] = enum_value(cp[3])
            row["owner_guid"] = cp[4] if isinstance(cp[4], str) else None
        else:
            row["unparsed_reason"] = "unexpected Building constructorParams shape"
        owner = actor_by_guid.get(row["owner_guid"])
        if owner is not None:
            row["owner_actor_id"] = owner["actor_id"]
            row["owner_type"] = owner["type"]
        elif row["owner_guid"] is not None:
            row["owner_unresolved_reason"] = "owner GUID not in ActorManager"
        comps = [short_type(c.get("objectType")) for c in b.get("components") or []]
        row["components"] = comps
        row["is_shop"] = "Shop" in comps
        row["decoration_candidate"] = "DecorationVisualization" in comps
        row["paid_to_build"] = f.get("paidToBuild")
        mam, kind, reason = _max_accepted_for_building(b)
        row["max_accepted"] = mam
        row["storage_kind"] = kind
        if reason:
            row["max_accepted_reason"] = reason
        row["base_key"] = building_key(row["prefab"], row["x"], row["y"]) if row["x"] is not None else None
        buildings.append(row)

    keyed = [r for r in buildings if r["base_key"] is not None]
    collisions = assign_final_keys(keyed)
    by_guid = {r["guid"]: r for r in buildings}

    def resolve_building(guid):
        """Entity GUID or component GUID (e.g. BuildingLogistics) -> building row."""
        if guid is None:
            return None, None
        if guid in by_guid:
            return by_guid[guid], "entity"
        if guid in comp_owner:
            bg, ct = comp_owner[guid]
            return by_guid.get(bg), "component:" + (ct or "?")
        return None, None

    routes = []
    slots_total = 0
    for b in bents:
        origin = by_guid[b.get("guid")]
        mdms = [c for c in b.get("components") or [] if (c.get("objectType") or "").endswith("ManualDestinationManager")]
        if len(mdms) > 1:
            notes.append("building %s has %d manual destination managers; slots concatenated" % (origin["guid"], len(mdms)))
        index = 0
        for mdm in mdms:
            for s in _fields(mdm).get("_savegameSlots") or []:
                sf = _fields(s)
                slots_total += 1
                slot_index = index
                index += 1
                product = prefab_name(sf.get("_product"))
                dest_guid = sf.get("_destination")
                if product is None or dest_guid is None:
                    continue
                dest, dest_ref = resolve_building(dest_guid)
                src_guid = sf.get("_source")
                src_b, _src_ref = resolve_building(src_guid)
                route = {
                    "origin_guid": origin["guid"],
                    "origin_key": origin.get("key"),
                    "origin_owner_actor_id": origin["owner_actor_id"],
                    "origin_owner_type": origin["owner_type"],
                    "manager_type": short_type(mdm.get("objectType")),
                    "slot_index": slot_index,
                    "product": product,
                    "destination_ref_guid": dest_guid,
                    "destination_ref_kind": dest_ref,
                    "destination_guid": dest["guid"] if dest else None,
                    "destination_key": dest.get("key") if dest else None,
                    "destination_is_shop": dest["is_shop"] if dest else None,
                    "source_ref_guid": src_guid,
                    "source": "own" if src_guid is None else (src_b["prefab"] if src_b else None),
                    "min_stored_at_source": sf.get("_minStoredAtSource"),
                    "auto_max_accepted": sf.get("_autoMaxAccepted"),
                    "auto_effective": bool(sf.get("_autoMaxAccepted")) and bool(dest and dest["is_shop"]),
                    "paused": sf.get("paused"),
                    "wait_till_vehicle_full": sf.get("_waitTillVehicleFull"),
                }
                if dest is None:
                    route["destination_unresolved_reason"] = "destination GUID matches no building or building component"
                if src_guid is not None and src_b is None:
                    route["source_unresolved_reason"] = "source GUID matches no building or building component"
                # Max Send as stored on the destination for this product (0 = unlimited, missing = 0).
                if dest is not None and dest.get("max_accepted") is not None:
                    route["destination_max_accepted"] = dest["max_accepted"].get(product, 0)
                else:
                    route["destination_max_accepted"] = None
                routes.append(route)
    assign_route_keys(routes)

    bal = fixed_decoded(_fields(_manager(tree, "ProjectAutomata.MoneyManager")).get("_balances"))
    balance_keys = [e.get("actor") for e in bal] if bal is not None else None
    if bal is None:
        notes.append("MoneyManager._balances not decoded")

    guids = sorted(collect_object_guids(tree))
    endgame = _fields(_manager(tree, "ProjectAutomata.EndGameManager"))
    scenario = _fields(_manager(tree, "ProjectAutomata.ScenarioManager"))
    world = _fields(_manager(tree, "ProjectAutomata.GameParametersManager")).get("world")
    world_v = world.get("value") if isinstance(world, dict) else None
    tm = _fields(_manager(tree, "ProjectAutomata.TimeManager"))
    vehicles = _manager(tree, "ProjectAutomata.VehicleManager").get("entities") or []

    owner_type_counts = Counter(r["owner_type"] or "unresolved" for r in buildings)
    counts = {
        "buildings_total": len(buildings),
        "buildings_by_owner_type": dict(sorted(owner_type_counts.items())),
        "buildings_player": owner_type_counts.get("HumanPlayer", 0),
        "buildings_ai": owner_type_counts.get("AiPlayer", 0),
        "vehicles": len(vehicles),
        "actors": len(actors),
        "manual_slots_total": slots_total,
        "routes": len(routes),
        "routes_player": sum(1 for r in routes if r["origin_owner_type"] == "HumanPlayer"),
        "routes_ai": sum(1 for r in routes if r["origin_owner_type"] == "AiPlayer"),
        "guids": len(guids),
        "key_collisions": len(collisions),
    }
    return {
        "tool": "save_tools.extract",
        "tool_version": TOOL_VERSION,
        "source_file": source_name,
        "header": {
            "name": header.get("name"),
            "timestamp": header.get("timestamp"),
            "timestamp_utc": header_timestamp_utc(header.get("timestamp")),
            "save_format_version": header.get("saveFormatVersion"),
            "savegame_build": header.get("savegameBuild"),
            "mods": [{"name": m.get("name"), "version": m.get("version"), "version_string": m.get("versionString")}
                     for m in header.get("mods") or [] if isinstance(m, dict)],
            "module": header.get("module"),
        },
        "savegame_version": tree.get("savegameVersion"),
        "created_savegame_build": (tree.get("metadata") or {}).get("createdSavegameBuild"),
        "game_time": {"days": tm.get("_days"), "months": tm.get("_months"), "date": game_date(tm.get("_days"), tm.get("_months"))},
        "flags": {
            "achievements_enabled_game_mode": (tree.get("gameMode") or {}).get("achievementsEnabled"),
            "world_parameters_enable_achievements": world_v.get("enableAchievements") if isinstance(world_v, dict) else None,
            "end_game_used_cheats": endgame.get("usedCheats"),
            "scenario_used_cheats": scenario.get("usedCheats"),
        },
        "counts": counts,
        "actors": actors,
        "money_balance_keys": balance_keys,
        "buildings": buildings,
        "key_collisions": collisions,
        "routes": routes,
        "guids": guids,
        "notes": notes,
    }


def collect_object_guids(tree):
    """Every entity and component GUID (GuidMapper-backed objects) in all managers."""
    out = set()
    for _, e in iter_entities(tree):
        g = e.get("guid")
        if g:
            out.add(g)
        for c in e.get("components") or []:
            if c.get("guid"):
                out.add(c["guid"])
    return out


# ---------------------------------------------------------------------------
# rank (E3 test-save selection)
# ---------------------------------------------------------------------------


def rank_counts(tree):
    bents = _manager(tree, "ProjectAutomata.BuildingManager").get("entities") or []
    actors = _manager(tree, "ProjectAutomata.ActorManager").get("entities") or []
    atype = {a.get("guid"): short_type(a.get("objectType")) for a in actors}
    owner = Counter()
    routes = 0
    for b in bents:
        cp = b.get("constructorParams") or []
        owner[atype.get(cp[4]) if len(cp) > 4 else None] += 1
        for c in b.get("components") or []:
            if (c.get("objectType") or "").endswith("ManualDestinationManager"):
                for s in _fields(c).get("_savegameSlots") or []:
                    sf = _fields(s)
                    if sf.get("_product") is not None and sf.get("_destination") is not None:
                        routes += 1
    vehicles = len(_manager(tree, "ProjectAutomata.VehicleManager").get("entities") or [])
    return {
        "buildings_total": len(bents),
        "vehicles": vehicles,
        "buildings_ai": owner.get("AiPlayer", 0),
        "buildings_player": owner.get("HumanPlayer", 0),
        "routes": routes,
    }


def rank_sort(rows):
    """PRD 22 E3: rank by total buildings, then vehicles, then AI building count (descending). Failed parses last."""
    ok = [r for r in rows if r.get("parse_ok")]
    bad = [r for r in rows if not r.get("parse_ok")]
    ok.sort(key=lambda r: (-r["buildings_total"], -r["vehicles"], -r["buildings_ai"], r["file"]))
    for i, r in enumerate(ok, 1):
        r["rank"] = i
    for r in bad:
        r["rank"] = None
    return ok + bad


def cmd_rank(args):
    d = Path(args.dir)
    live = live_saves_dir()
    if live is not None and is_under(d, live):
        raise ToolError("refusing to scan %s: only folders of save COPIES may be ranked" % live)
    if not d.is_dir():
        raise ToolError("not a directory: %s" % d)
    files = sorted(p for p in d.glob("*.sav") if p.is_file())
    rows = []
    for p in files:
        row = {"file": p.name, "file_bytes": p.stat().st_size, "parse_ok": False}
        try:
            tree, timing, unparsed = parse_save(p)
            row.update(rank_counts(tree))
            row["parse_ok"] = True
            row["unparsed_blobs"] = unparsed
            row["parse_s"] = round(timing.get("parse_s", 0.0), 3)
            del tree
        except ToolError:
            raise
        except Exception as e:  # noqa: BLE001 - report and continue with the other copies
            row["error"] = "%s: %s" % (type(e).__name__, e)
        rows.append(row)
        print("parsed %s: %s" % (p.name, "ok" if row["parse_ok"] else row.get("error")), file=sys.stderr)
    result = {
        "tool": "save_tools.rank",
        "tool_version": TOOL_VERSION,
        "criteria": ["buildings_total desc", "vehicles desc", "buildings_ai desc"],
        "generated_utc": _utc_now(),
        "saves": rank_sort(rows),
    }
    top = next((r for r in result["saves"] if r.get("rank") == 1), None)
    result["selected"] = top["file"] if top else None
    _emit(result, args.out)
    return 0


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------


def cmd_extract(args):
    out = check_output_path(args.out)
    p = check_input_copy(args.save)
    sha_before = sha256_file(p)
    tree, timing, unparsed = parse_save(p)
    ex = extract_entities(tree, source_name=p.name)
    del tree
    sha_after = sha256_file(p)
    ex["source_sha256"] = sha_before
    ex["source_unchanged"] = sha_before == sha_after
    ex["parse"] = {"unparsed_blobs": unparsed, "parse_s": round(timing.get("parse_s", 0.0), 3),
                   "decompressed_bytes": timing.get("decompressed_bytes")}
    write_json(out, ex)
    print("extract: %s -> %s (%s)" % (p.name, out, json.dumps(ex["counts"])))
    if not ex["source_unchanged"]:
        print("ERROR: the save copy changed while it was parsed", file=sys.stderr)
        return 2
    return 0


# ---------------------------------------------------------------------------
# compare-e7 (pure comparison over an extract and an observer state.json)
# ---------------------------------------------------------------------------

OWNER_SCOPE = {"HumanPlayer": "player", "AiPlayer": "ai"}


def _observer_buildings(data):
    """Returns ([(scope, list_name, row)], sections_present)."""
    rows = []
    present = {}
    for name, scope in (("buildings_player", "player"), ("buildings_ai_detail", "ai"), ("buildings_ai", "ai")):
        lst = data.get(name)
        present[name] = lst is not None
        for r in lst or []:
            rows.append((scope, name, r))
    # buildings_ai_detail supersedes buildings_ai for the same key.
    detail_keys = {r.get("key") for s, n, r in rows if n == "buildings_ai_detail"}
    rows = [(s, n, r) for s, n, r in rows if not (n == "buildings_ai" and r.get("key") in detail_keys)]
    return rows, present


def compare_e7(extract, state, max_records=5000):
    data = (state or {}).get("data") or {}
    mismatches = []
    info = {}

    def mm(kind, **kw):
        mismatches.append(dict(kind=kind, **kw))

    save_b = [b for b in extract.get("buildings") or [] if b.get("owner_type") in OWNER_SCOPE and b.get("key")]
    save_by_key = {b["key"]: b for b in save_b}
    save_by_base = {}
    for b in save_b:
        save_by_base.setdefault(b["base_key"], []).append(b)

    obs_rows, present = _observer_buildings(data)
    ai_compared = present["buildings_ai"] or present["buildings_ai_detail"]
    info["observer_sections_present"] = present

    # Key collisions: observer keys must be unique; the save must have no base-key collisions.
    obs_key_counts = Counter(r.get("key") for _, _, r in obs_rows)
    for k, n in sorted(obs_key_counts.items(), key=lambda kv: str(kv[0])):
        if n > 1:
            mm("observer_duplicate_key", key=k, count=n)
    all_by_base = {}
    for b in extract.get("buildings") or []:
        if b.get("base_key"):
            all_by_base.setdefault(b["base_key"], []).append(b)
    for k, lst in sorted((extract.get("key_collisions") or {}).items()):
        members = all_by_base.get(k, [])
        mm("key_collision_in_save", base_key=k, count=len(lst),
           guids=lst, owner_types=sorted({str(m.get("owner_type")) for m in members}),
           components=sorted({c for m in members for c in (m.get("components") or [])
                              if c in ("Harvester", "Field", "DisconnectedHarvester", "ModuleOwner")}))
    obs_suffixed = sorted(k for k in obs_key_counts if k and "#" in k)
    info["observer_suffixed_keys"] = obs_suffixed

    matched_save = set()
    guid_null = 0
    guid_checked = 0
    for scope, list_name, r in obs_rows:
        k = r.get("key")
        b = save_by_key.get(k)
        if b is None:
            cands = save_by_base.get(base_key(k), [])
            if len(cands) == 1:
                b = cands[0]
        if b is None:
            mm("observer_only", list=list_name, key=k, prefab=r.get("prefab"), x=r.get("x"), y=r.get("y"))
            continue
        matched_save.add(b["guid"])
        if OWNER_SCOPE.get(b.get("owner_type")) != scope:
            mm("owner_scope", list=list_name, key=k, save_owner_type=b.get("owner_type"))
        for fld_obs, fld_save in (("prefab", "prefab"), ("x", "x"), ("y", "y"), ("owner_actor_id", "owner_actor_id")):
            if fld_obs in r and r.get(fld_obs) != b.get(fld_save):
                mm("field_" + fld_obs, list=list_name, key=k, observer=r.get(fld_obs), save=b.get(fld_save))
        if "rotation" in r and r.get("rotation") is not None and b.get("rotation") is not None and r["rotation"] != b["rotation"]:
            mm("field_rotation", list=list_name, key=k, observer=r["rotation"], save=b["rotation"])
        if k != b["key"]:
            mm("key_differs", list=list_name, observer=k, save=b["key"])
        if "save_guid" in r:
            if r.get("save_guid") is None:
                guid_null += 1
            else:
                guid_checked += 1
                if str(r["save_guid"]).lower() != str(b["guid"]).lower():
                    mm("save_guid_mismatch", list=list_name, key=k, observer=r["save_guid"], save=b["guid"])

    decoration_only = []
    for b in save_b:
        if b["guid"] in matched_save:
            continue
        scope = OWNER_SCOPE[b["owner_type"]]
        if scope == "ai" and not ai_compared:
            continue
        if b.get("decoration_candidate"):
            decoration_only.append(b["key"])
            continue
        mm("save_only", scope=scope, key=b["key"], guid=b["guid"], prefab=b["prefab"])
    info["save_only_decoration_candidates"] = decoration_only
    info["save_guid_null_in_observer"] = guid_null
    info["save_guid_checked"] = guid_checked

    # Routes
    obs_guid_by_key = {r.get("key"): r.get("save_guid") for _, _, r in obs_rows if r.get("save_guid")}
    route_sets = (("routes_player", "HumanPlayer"), ("routes_ai", "AiPlayer"))
    route_counts = {}
    for list_name, owner_type in route_sets:
        obs = data.get(list_name)
        save_routes = [r for r in extract.get("routes") or [] if r.get("origin_owner_type") == owner_type]
        route_counts[list_name] = {"observer": None if obs is None else len(obs), "save": len(save_routes)}
        if obs is None:
            if save_routes and list_name == "routes_player":
                mm("routes_section_missing", list=list_name, save_routes=len(save_routes))
            continue
        save_by_rk = {}
        for r in save_routes:
            save_by_rk.setdefault(r["route_key"], r)
        seen = set()
        for o in obs:
            rk = o.get("route_key")
            s = save_by_rk.get(rk)
            if s is None:
                mm("route_observer_only", list=list_name, route_key=rk)
                continue
            seen.add(rk)
            for a, b, name in ((o.get("origin"), s["origin_key"], "origin"),
                               (o.get("product"), s["product"], "product"),
                               (o.get("destination"), s["destination_key"], "destination"),
                               (o.get("source"), s["source"], "source")):
                if a != b:
                    mm("route_" + name, list=list_name, route_key=rk, observer=a, save=b)
            mk = (o.get("min_keep") or {}).get("value")
            if mk != s["min_stored_at_source"]:
                mm("route_min_keep", list=list_name, route_key=rk, observer=mk, save=s["min_stored_at_source"])
            obs_auto = (o.get("max_send") or {}).get("mode") == "auto_shop_demand"
            if obs_auto != s["auto_effective"]:
                mm("route_auto_mode", list=list_name, route_key=rk, observer_mode=(o.get("max_send") or {}).get("mode"),
                   save_auto_max_accepted=s["auto_max_accepted"], save_destination_is_shop=s["destination_is_shop"])
            for of, sf in (("paused", "paused"), ("wait_for_full_vehicle", "wait_till_vehicle_full"), ("slot_index", "slot_index")):
                if of in o and o.get(of) != s.get(sf):
                    mm("route_" + of, list=list_name, route_key=rk, observer=o.get(of), save=s.get(sf))
            ms = o.get("max_send") or {}
            if not obs_auto and ms.get("mode") == "manual" and s.get("destination_max_accepted") is not None \
                    and ms.get("value") != s["destination_max_accepted"]:
                mm("route_max_send_value", list=list_name, route_key=rk, observer=ms.get("value"), save=s["destination_max_accepted"])
            dg = obs_guid_by_key.get(o.get("destination"))
            if dg and s.get("destination_guid") and dg.lower() != s["destination_guid"].lower():
                mm("route_destination_guid", list=list_name, route_key=rk, observer=dg, save=s["destination_guid"])
        for rk, s in save_by_rk.items():
            if rk not in seen:
                mm("route_save_only", list=list_name, route_key=rk)

    by_kind = Counter(m["kind"] for m in mismatches)
    obs_day = ((state or {}).get("captured") or {}).get("game_day_end")
    save_day = (extract.get("game_time") or {}).get("days")
    time_gap = {
        "observer_game_day_end": obs_day,
        "observer_game_date": ((state or {}).get("captured") or {}).get("game_date"),
        "save_game_day": save_day,
        "save_game_date": (extract.get("game_time") or {}).get("date"),
        "delta_days": (save_day - obs_day) if isinstance(save_day, int) and isinstance(obs_day, int) else None,
        "note": "The snapshot and the save can be a few game minutes apart; differences caused by play in between "
                "are reported as mismatches and counted so the operator can judge.",
    }
    result = {
        "tool": "save_tools.compare-e7",
        "tool_version": TOOL_VERSION,
        "gate": "E7",
        "pass": not mismatches,
        "verdict": "PASS" if not mismatches else "FAIL",
        "counts": {
            "save_buildings_player": sum(1 for b in save_b if b["owner_type"] == "HumanPlayer"),
            "save_buildings_ai": sum(1 for b in save_b if b["owner_type"] == "AiPlayer"),
            "observer_buildings": len(obs_rows),
            "matched_buildings": len(matched_save),
            "routes": route_counts,
            "mismatches": len(mismatches),
        },
        "mismatch_counts_by_kind": dict(sorted(by_kind.items())),
        "mismatches": mismatches[:max_records],
        "mismatches_truncated": len(mismatches) > max_records,
        "informational": info,
        "time_gap": time_gap,
        "world_session": (state or {}).get("world_session"),
        "observer_seq": (state or {}).get("seq"),
    }
    return result


def cmd_compare_e7(args):
    ex = _read_json(args.extract)
    st = _read_json(args.state)
    res = compare_e7(ex, st)
    if args.out:
        out = check_output_path(args.out)
        write_json(out, res)
    print(json.dumps({k: res[k] for k in ("verdict", "counts", "mismatch_counts_by_kind", "time_gap")}, indent=1))
    return 0 if res["pass"] else 1


# ---------------------------------------------------------------------------
# diff-v8: normalisation + structural diff (pure)
# ---------------------------------------------------------------------------


def _h(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=_json_default).encode("utf-8")).hexdigest()


def _is_scalar(v):
    return v is None or isinstance(v, (bool, int, float, str))


def _canon_scalar(v):
    if isinstance(v, float) and math.isnan(v):
        return "NaN"
    return v


def _keyed(lst, field):
    if not lst or not all(isinstance(e, dict) and field in e for e in lst):
        return None
    keys = [str(e[field]) for e in lst]
    if len(set(keys)) != len(keys):
        return None
    return {k: e for k, e in zip(keys, lst)}


def normalize(v, _field=None):
    """
    Canonical form for the V8 diff:
      * bytes -> {"$bytes": n, "sha256": ...}
      * {"$dict": [[k, v], ...]} -> {"$dict": {canon(k): v}} (unordered)
      * HashSet NRBF objects -> {"$set": sorted canonical elements} (unordered; Version/Capacity dropped)
      * lists of entities/components -> {"$by_guid": {...}} (unordered)
      * decoded dictionary payloads -> {"$by_<field>": {...}} (unordered)
      * long scalar lists -> {"$list": n, "sha256": ordered hash, "sorted_sha256": order-free hash}
      * NaN -> "NaN"
    """
    if isinstance(v, (bytes, bytearray)):
        return {"$bytes": len(v), "sha256": hashlib.sha256(bytes(v)).hexdigest()}
    if isinstance(v, dict):
        if "$bytes" in v and "data" in v:
            data = v["data"]
            data = bytes(data) if isinstance(data, (bytes, bytearray)) else str(data).encode()
            return {"$bytes": v["$bytes"], "sha256": hashlib.sha256(data).hexdigest()}
        if "$dict" in v and isinstance(v["$dict"], list):
            pairs = [(normalize(p[0]), normalize(p[1])) for p in v["$dict"] if isinstance(p, (list, tuple)) and len(p) == 2]
            keys = [json.dumps(k, sort_keys=True, ensure_ascii=False, default=_json_default) for k, _ in pairs]
            if len(set(keys)) == len(keys):
                return {"$dict": {k: val for k, (_, val) in zip(keys, pairs)}}
            return {"$dict_multiset": sorted(_h([k, val]) for k, val in pairs)}
        t = v.get("$type")
        if isinstance(t, str) and t.startswith("System.Collections.Generic.HashSet`1"):
            elems = v.get("Elements") or []
            return {"$set": sorted(json.dumps(normalize(e), sort_keys=True, ensure_ascii=False, default=_json_default) for e in elems)}
        if v.get("$fixed") and isinstance(v.get("decoded"), list):
            out = {k: normalize(x, k) for k, x in v.items() if k != "decoded"}
            dec = v["decoded"]
            for f in DECODED_KEY_FIELDS:
                keyed = _keyed(dec, f)
                if keyed is not None:
                    out["decoded"] = {"$by_" + f: {k: normalize(e) for k, e in keyed.items()}}
                    break
            else:
                out["decoded"] = normalize(dec, "decoded")
            return out
        return {k: normalize(x, k) for k, x in v.items()}
    if isinstance(v, list):
        if v and all(isinstance(e, dict) and "guid" in e and "objectType" in e for e in v):
            keyed = _keyed(v, "guid")
            if keyed is not None:
                return {"$by_guid": {k: normalize(e) for k, e in keyed.items()}}
        if len(v) > LONG_LIST and all(_is_scalar(e) for e in v):
            cv = [_canon_scalar(e) for e in v]
            return {"$list": len(cv), "sha256": _h(cv), "sorted_sha256": _h(sorted(cv, key=lambda x: (str(type(x)), str(x))))}
        return [normalize(e, _field) for e in v]
    return _canon_scalar(v)


def drop_noise(tree, paths=V8_NORMALIZED_AWAY):
    """Removes the V8 noise paths in place; returns the list of paths actually removed."""
    removed = []
    for path in paths:
        node = tree
        for seg in path[:-1]:
            node = node.get(seg) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, dict) and path[-1] in node:
            del node[path[-1]]
            removed.append("/".join(path))
    return removed


def _short(v, n=200):
    s = json.dumps(v, ensure_ascii=False, default=_json_default, sort_keys=True)
    return s if len(s) <= n else s[:n] + "..."


def _seg(k, container_key=None):
    if container_key == "$by_guid":
        return "[guid=%s]" % k
    if container_key and container_key.startswith("$by_"):
        return "[%s=%s]" % (container_key[4:], k)
    if container_key == "$dict":
        return "[key=%s]" % k
    return str(k)


def diff_trees(a, b, path="", out=None, field=None):
    """Structural diff of two normalised trees. Returns a list of {path, kind, ...} records."""
    if out is None:
        out = []
    if a == b:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        if "$list" in a and "$list" in b:
            kind = "order_only" if a.get("sorted_sha256") == b.get("sorted_sha256") and a["$list"] == b["$list"] else "changed"
            if kind == "order_only" and field in ORDER_SENSITIVE_FIELDS:
                kind = "reordered"
            out.append({"path": path, "kind": kind, "len_a": a["$list"], "len_b": b["$list"]})
            return out
        keyed_container = len(a) == 1 and len(b) == 1 and next(iter(a)) == next(iter(b)) and next(iter(a)).startswith("$") \
            and isinstance(next(iter(a.values())), dict)
        if keyed_container:
            ck = next(iter(a))
            ia, ib = a[ck], b[ck]
            for k in sorted(set(ia) | set(ib)):
                p = path + "/" + _seg(k, ck)
                if k not in ib:
                    out.append({"path": p, "kind": "removed", "a": _short(ia[k])})
                elif k not in ia:
                    out.append({"path": p, "kind": "added", "b": _short(ib[k])})
                else:
                    diff_trees(ia[k], ib[k], p, out, field)
            return out
        for k in sorted(set(a) | set(b), key=str):
            p = path + "/" + str(k)
            if k not in b:
                out.append({"path": p, "kind": "removed", "a": _short(a[k])})
            elif k not in a:
                out.append({"path": p, "kind": "added", "b": _short(b[k])})
            else:
                diff_trees(a[k], b[k], p, out, k if not str(k).startswith("$") else field)
        return out
    if isinstance(a, list) and isinstance(b, list):
        ha = [_h(x) for x in a]
        hb = [_h(x) for x in b]
        if sorted(ha) == sorted(hb):
            out.append({"path": path, "kind": "reordered" if field in ORDER_SENSITIVE_FIELDS else "order_only",
                        "len_a": len(a), "len_b": len(b)})
            return out
        if len(a) == len(b):
            for i, (x, y) in enumerate(zip(a, b)):
                diff_trees(x, y, "%s[%d]" % (path, i), out, field)
            return out
        ca, cb = Counter(ha), Counter(hb)
        removed = list((ca - cb).elements())
        added = list((cb - ca).elements())
        ia = {h: x for h, x in zip(ha, a)}
        ib = {h: x for h, x in zip(hb, b)}
        out.append({"path": path, "kind": "list_changed", "len_a": len(a), "len_b": len(b),
                    "removed_count": len(removed), "added_count": len(added),
                    "removed_sample": [_short(ia[h]) for h in removed[:3]],
                    "added_sample": [_short(ib[h]) for h in added[:3]]})
        return out
    out.append({"path": path, "kind": "changed", "a": _short(a), "b": _short(b)})
    return out


def split_diff(records):
    """Separates ordering-only records (not differences) from real differences."""
    real = [r for r in records if r["kind"] != "order_only"]
    order = [r for r in records if r["kind"] == "order_only"]
    return real, order


def extra_differences(baseline, candidate):
    """Records of the candidate diff whose path does not occur in the baseline diff (PRD 22 V8)."""
    base_paths = {r["path"] for r in baseline}
    return [r for r in candidate if r["path"] not in base_paths]


def v8_facts(tree):
    """Facts for the explicit V8 checks, from a raw (non-normalised) tree."""
    max_acc = {}
    delivered = {}
    for _, e in iter_entities(tree):
        for c in e.get("components") or []:
            t = short_type(c.get("objectType"))
            f = _fields(c)
            if t == "ProductSpecificProductStorage":
                dec = fixed_decoded(f.get("_maxAcceptedMap"))
                if dec is not None:
                    max_acc["%s/_maxAcceptedMap" % c.get("guid")] = sorted(str(x.get("product")) for x in dec)
            elif t == "InfiniteStorage":
                dec = fixed_decoded(f.get("_maxAccepted"))
                if dec is not None:
                    max_acc["%s/_maxAccepted" % c.get("guid")] = sorted(str(x.get("product")) for x in dec)
            elif t == "Shop":
                dec = fixed_decoded(f.get("_deliveredByActors"))
                if dec is not None:
                    keys = []
                    for x in dec:
                        keys.append(str(x.get("actorId")))
                        for p in (x.get("products") or {}):
                            keys.append("%s:%s" % (x.get("actorId"), p))
                    delivered[str(c.get("guid"))] = sorted(keys)
    bal = fixed_decoded(_fields(_manager(tree, "ProjectAutomata.MoneyManager")).get("_balances"))
    endgame = _fields(_manager(tree, "ProjectAutomata.EndGameManager"))
    scenario = _fields(_manager(tree, "ProjectAutomata.ScenarioManager"))
    world = _fields(_manager(tree, "ProjectAutomata.GameParametersManager")).get("world")
    world_v = world.get("value") if isinstance(world, dict) else None
    return {
        "max_accepted_keys": max_acc,
        "delivered_by_actors_keys": delivered,
        "money_balance_keys": sorted(str(x.get("actor")) for x in bal) if bal is not None else None,
        "guids": sorted(collect_object_guids(tree)),
        "flags": {
            "achievements_enabled_game_mode": (tree.get("gameMode") or {}).get("achievementsEnabled"),
            "world_parameters_enable_achievements": world_v.get("enableAchievements") if isinstance(world_v, dict) else None,
            "end_game_used_cheats": endgame.get("usedCheats"),
            "scenario_used_cheats": scenario.get("usedCheats"),
        },
    }


def explicit_checks(fa, fb):
    """New dictionary entries / GUIDs and flag changes from fa to fb (PRD 22 V8 explicit list)."""
    res = {}

    def new_entries(ma, mb):
        added = {}
        for k, vb in (mb or {}).items():
            va = set((ma or {}).get(k, []))
            n = [x for x in vb if x not in va]
            if n:
                added[k] = n
        return added

    res["max_accepted_new_entries"] = new_entries(fa["max_accepted_keys"], fb["max_accepted_keys"])
    res["delivered_by_actors_new_entries"] = new_entries(fa["delivered_by_actors_keys"], fb["delivered_by_actors_keys"])
    ba, bb = fa["money_balance_keys"], fb["money_balance_keys"]
    res["money_balance_new_keys"] = None if ba is None or bb is None else sorted(set(bb) - set(ba))
    res["new_guids"] = sorted(set(fb["guids"]) - set(fa["guids"]))
    res["removed_guids"] = sorted(set(fa["guids"]) - set(fb["guids"]))
    res["flag_changes"] = {k: {"a": fa["flags"][k], "b": fb["flags"][k]} for k in fa["flags"] if fa["flags"][k] != fb["flags"].get(k)}
    res["violations"] = sorted(k for k in ("max_accepted_new_entries", "delivered_by_actors_new_entries",
                                           "money_balance_new_keys", "new_guids", "flag_changes") if res.get(k))
    return res


def _group_counts(records, depth=3):
    c = Counter()
    for r in records:
        segs = [s for s in r["path"].split("/") if s][:depth]
        c["/".join(segs)] += 1
    return dict(c.most_common(50))


def _load_normalized(path):
    tree, _timing, unparsed = parse_save(path)
    facts = v8_facts(tree)
    removed = drop_noise(tree)
    norm = normalize(tree)
    del tree
    return norm, facts, removed, unparsed


def cmd_diff_v8(args):
    out_path = check_output_path(args.out) if args.out else None
    paths = [check_input_copy(p) for p in (args.a, args.b, args.c)]
    hashes_before = [sha256_file(p) for p in paths]
    na, fa, removed_a, ua = _load_normalized(paths[0])
    nb, fb, removed_b, ub = _load_normalized(paths[1])
    d_ab = diff_trees(na, nb)
    del na
    nc, fc, removed_c, uc = _load_normalized(paths[2])
    d_bc = diff_trees(nb, nc)
    del nb, nc
    base_real, base_order = split_diff(d_ab)
    cand_real, cand_order = split_diff(d_bc)
    extra = extra_differences(base_real, cand_real)
    checks_bc = explicit_checks(fb, fc)
    checks_ab = explicit_checks(fa, fb)
    hashes_after = [sha256_file(p) for p in paths]
    mx = args.max_records
    passed = not extra and not checks_bc["violations"]
    res = {
        "tool": "save_tools.diff-v8",
        "tool_version": TOOL_VERSION,
        "gate": "V8",
        "files": {"a": paths[0].name, "b": paths[1].name, "c": paths[2].name},
        "sha256": dict(zip(("a", "b", "c"), hashes_before)),
        "copies_unchanged": hashes_before == hashes_after,
        "normalized_away": sorted(set(removed_a) | set(removed_b) | set(removed_c)),
        "unparsed_blobs": {"a": ua, "b": ub, "c": uc},
        "pass": passed and hashes_before == hashes_after,
        "verdict": "PASS" if passed and hashes_before == hashes_after else "FAIL",
        "counts": {
            "baseline_differences": len(base_real),
            "candidate_differences": len(cand_real),
            "extra_differences": len(extra),
            "baseline_ordering_only": len(base_order),
            "candidate_ordering_only": len(cand_order),
        },
        "extra_differences_by_area": _group_counts(extra),
        "baseline_differences_by_area": _group_counts(base_real),
        "explicit_checks_b_to_c": checks_bc,
        "explicit_checks_a_to_b": checks_ab,
        "baseline_differences": base_real[:mx],
        "extra_differences": extra[:mx],
        "ordering_only_b_to_c": cand_order[:mx],
        "truncated_at": mx,
        "notes": [
            "Ordering-only changes inside lists, dictionaries, sets and entity collections are not differences.",
            "Undecoded FixedSerializationData blobs are compared as raw bytes (hash); their internal ordering cannot be normalised.",
        ],
    }
    for k in ("new_guids", "removed_guids"):
        for chk in (checks_bc, checks_ab):
            if len(chk[k]) > mx:
                chk[k + "_count"] = len(chk[k])
                chk[k] = chk[k][:mx]
    if out_path:
        write_json(out_path, res)
    summary = {k: res[k] for k in ("verdict", "counts", "normalized_away", "copies_unchanged")}
    summary["explicit_violations_b_to_c"] = checks_bc["violations"]
    print(json.dumps(summary, indent=1))
    return 0 if res["pass"] else 1


# ---------------------------------------------------------------------------
# fixture (T-10)
# ---------------------------------------------------------------------------


def build_fixture(ex):
    """Realistic local fixture (ids, counts, route fields) from an extract. Pure."""
    keyed = [b for b in ex["buildings"] if b.get("key")]
    key_re = re.compile(r"^[^@|#]+@-?\d+,-?\d+(#(i-?\d+|[0-9a-f]{8}))?$")
    bad_keys = [b["key"] for b in keyed if not key_re.match(b["key"])]
    keys = [b["key"] for b in keyed]
    dup_final = [k for k, n in Counter(keys).items() if n > 1]
    rk = [r["route_key"] for r in ex["routes"]]
    dup_routes = [k for k, n in Counter(rk).items() if n > 1]
    mk4 = [r["route_key"] for r in ex["routes"] if r.get("min_stored_at_source") == 4]
    unresolved_dest = [r["route_key"] for r in ex["routes"] if r.get("destination_key") is None]

    def bshape(b, detail):
        row = {"key": b["key"], "prefab": b["prefab"], "owner_actor_id": b["owner_actor_id"], "x": b["x"], "y": b["y"]}
        if detail:
            row.update({"save_guid": b["guid"], "rotation": b["rotation"], "is_shop": b["is_shop"]})
        return row

    def rshape(r):
        return {
            "route_key": r["route_key"], "origin": r["origin_key"], "destination": r["destination_key"],
            "product": r["product"], "source": r["source"], "slot_index": r["slot_index"], "occurrence": r["occurrence"],
            "paused": r["paused"], "wait_for_full_vehicle": r["wait_till_vehicle_full"],
            "min_keep": {"value": r["min_stored_at_source"], "keep_all": r["min_stored_at_source"] == 2147483647},
            # Manual mode: the destination's stored max accepted (missing = 0 = unlimited), as the observer reports it.
            # Auto mode: the observer reports live shop demand, which a save cannot provide (null).
            "max_send": {"mode": "auto_shop_demand" if r["auto_effective"] else "manual",
                         "value": None if r["auto_effective"] or r["destination_max_accepted"] is None
                         else r["destination_max_accepted"],
                         "value_source": None if r["auto_effective"] else "save:destination max accepted"},
        }

    player = [b for b in keyed if b["owner_type"] == "HumanPlayer"]
    ai = [b for b in keyed if b["owner_type"] == "AiPlayer"]
    fixture = {
        "fixture": "roi-mcp/save-derived-fixture",
        "fixture_version": "1.0.0",
        "local_only": True,
        "note": "Derived from a save COPY. Gitignored (.local). Field subset of state.json; not schema-complete.",
        "source": {"file": ex.get("source_file"), "sha256": ex.get("source_sha256"),
                   "savegame_version": ex.get("savegame_version"), "build": ex["header"].get("savegame_build"),
                   "game_date": ex["game_time"].get("date")},
        "counts": ex["counts"],
        "actors": [{"actor_id": a["actor_id"], "type": a["type"], "guid": a["guid"]} for a in ex["actors"]],
        "checks": {
            "building_key_format_ok": not bad_keys,
            "building_key_format_failures": bad_keys[:50],
            "building_keys_unique_after_suffix": not dup_final,
            "base_key_collisions": ex["key_collisions"],
            "route_keys_unique": not dup_routes,
            "route_key_duplicates": dup_routes[:50],
            "routes_with_unresolved_destination": unresolved_dest[:50],
            "min_keep_4_present": bool(mk4),
            "min_keep_4_routes": mk4,
            "flags": ex["flags"],
        },
    }
    state_like = {
        "fixture": "roi-mcp/save-derived-state-subset",
        "local_only": True,
        "data": {
            "buildings_player": [bshape(b, True) for b in player],
            "buildings_ai": [bshape(b, False) for b in ai],
            "routes_player": [rshape(r) for r in ex["routes"] if r["origin_owner_type"] == "HumanPlayer"],
            "routes_ai": [rshape(r) for r in ex["routes"] if r["origin_owner_type"] == "AiPlayer"],
        },
    }
    return fixture, state_like


def cmd_fixture(args):
    out_dir = check_output_path(args.out)
    p = check_input_copy(args.save)
    sha_before = sha256_file(p)
    tree, _timing, unparsed = parse_save(p)
    ex = extract_entities(tree, source_name=p.name)
    del tree
    ex["source_sha256"] = sha_before
    fixture, state_like = build_fixture(ex)
    fixture["parse"] = {"unparsed_blobs": unparsed}
    sha_after = sha256_file(p)
    fixture["source"]["unchanged_after_parse"] = sha_before == sha_after
    write_json(out_dir / "fixture.json", fixture)
    write_json(out_dir / "state-subset.json", state_like)
    c = fixture["checks"]
    print("fixture: %s -> %s" % (p.name, out_dir))
    print(json.dumps({"counts": fixture["counts"], "min_keep_4_present": c["min_keep_4_present"],
                      "building_key_format_ok": c["building_key_format_ok"],
                      "building_keys_unique_after_suffix": c["building_keys_unique_after_suffix"],
                      "route_keys_unique": c["route_keys_unique"]}, indent=1))
    return 0 if sha_before == sha_after else 2


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _utc_now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _read_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _emit(obj, out):
    if out:
        p = check_output_path(out)
        write_json(p, obj)
        print("wrote %s" % p)
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=1, default=_json_default))


def build_arg_parser():
    ap = argparse.ArgumentParser(prog="save_tools.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("rank", help="rank save copies in a folder (E3 test-save selection)")
    p.add_argument("dir")
    p.add_argument("--out", help="output JSON (below <repo>/.local/); stdout when omitted")
    p.set_defaults(func=cmd_rank)
    p = sub.add_parser("extract", help="entity extraction for validation (E7, T-10)")
    p.add_argument("save")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_extract)
    p = sub.add_parser("compare-e7", help="E7 gate: observer state.json vs save extract")
    p.add_argument("extract")
    p.add_argument("state")
    p.add_argument("--out")
    p.set_defaults(func=cmd_compare_e7)
    p = sub.add_parser("diff-v8", help="V8 gate: structural diff of three save copies")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("c")
    p.add_argument("--out")
    p.add_argument("--max-records", type=int, default=2000)
    p.set_defaults(func=cmd_diff_v8)
    p = sub.add_parser("fixture", help="T-10 local fixture from a save copy")
    p.add_argument("save")
    p.add_argument("--out", required=True, help="output directory below <repo>/.local/")
    p.set_defaults(func=cmd_fixture)
    return ap


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    try:
        return args.func(args)
    except ToolError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
