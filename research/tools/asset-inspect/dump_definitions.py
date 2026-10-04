# RESEARCH TOOLING ONLY - static, read-only dump of Rise of Industry ScriptableObject definitions; not production code.
# Reads the game's resources.assets (opened read-only by UnityPy) and decodes MonoBehaviour/ScriptableObject
# typetrees generated from COPIES of the managed assemblies in research/_local/managed.
# Output: research/_local/static-dump/<ClassName>.json (gitignored; contains game data) + summary on stdout.
#
# Usage: research/_local/venv/Scripts/python research/tools/asset-inspect/dump_definitions.py [ClassName ...]
import collections
import json
import os
import struct
import sys

import UnityPy
from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
GAME_DATA = r"C:\Program Files (x86)\Steam\steamapps\common\RiseOfIndustry\Rise of Industry_Data"
MANAGED_COPY = os.path.join(ROOT, "research", "_local", "managed")
OUT = os.path.join(ROOT, "research", "_local", "static-dump", os.environ.get("ROI_ASSET_FILE", "resources.assets").replace(".", "_"))
UNITY_VERSION = "2018.4.11f1"

DEFAULT_CLASSES = {
    "ProductDefinition", "Recipe", "TechTreeUnlock", "TechTree", "TechTreeCategory", "ProductCategory",
    "GameVersion", "GameModule", "ResourceDefinition", "BuildingCategory",
}


def jsonable(o, names):
    if isinstance(o, dict):
        if set(o.keys()) == {"m_FileID", "m_PathID"}:
            return {"ref": names.get((o["m_FileID"], o["m_PathID"]), [o["m_FileID"], o["m_PathID"]])}
        return {k: jsonable(v, names) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v, names) for v in o]
    if isinstance(o, bytes):
        return o.hex()
    return o


def main():
    wanted = set(sys.argv[1:]) or DEFAULT_CLASSES
    gen = TypeTreeGenerator(UNITY_VERSION)
    gen.load_local_dll_folder(MANAGED_COPY)
    # MonoScripts referenced by resources.assets live in globalgamemanagers.assets (m_FileID != 0).
    script_env = UnityPy.load(os.path.join(GAME_DATA, "globalgamemanagers.assets"))
    scripts_by_file = collections.defaultdict(dict)
    for obj in script_env.objects:
        if obj.type.name == "MonoScript":
            s = obj.read()
            scripts_by_file["globalgamemanagers.assets"][obj.path_id] = (s.m_AssemblyName, s.m_Namespace, s.m_ClassName)

    env = UnityPy.load(os.path.join(GAME_DATA, os.environ.get("ROI_ASSET_FILE", "resources.assets")))
    env.typetree_generator = gen
    asset_file = next(iter(env.files.values()))
    externals = [os.path.basename(e.path).lower() for e in asset_file.externals]

    scripts = {}
    names = {}
    mbs = []
    for obj in env.objects:
        if obj.type.name == "MonoScript":
            s = obj.read()
            scripts[(0, obj.path_id)] = (s.m_AssemblyName, s.m_Namespace, s.m_ClassName)
        elif obj.type.name == "MonoBehaviour":
            mbs.append(obj)
        elif obj.type.name == "GameObject":
            try:
                names[(0, obj.path_id)] = "GameObject:" + obj.read().m_Name
            except Exception:
                pass

    counts = collections.Counter()
    decoded = collections.defaultdict(list)
    for obj in mbs:
        # Decode only the fixed MonoBehaviour header from raw bytes (no typetree parse):
        # m_GameObject PPtr(i32,i64) | m_Enabled u8 + align | m_Script PPtr(i32,i64) | m_Name string
        try:
            raw = obj.get_raw_data()
            sf = struct.unpack_from("<i", raw, 4 + 8 + 4)[0]
            sp = struct.unpack_from("<q", raw, 4 + 8 + 4 + 4)[0]
            n = struct.unpack_from("<i", raw, 28)[0]
            name = raw[32:32 + n].decode("utf-8", "replace") if 0 <= n < 512 else "?"
        except Exception:
            continue
        if sf == 0:
            cls = scripts.get((0, sp), (None, None, "?"))[2]
        else:
            ext = externals[sf - 1] if 0 < sf <= len(externals) else "?"
            cls = scripts_by_file.get(ext, {}).get(sp, (None, None, "?"))[2]
        counts[cls] += 1
        names[(0, obj.path_id)] = f"{cls}:{name}"
        if cls in wanted:
            decoded[cls].append(obj)

    os.makedirs(OUT, exist_ok=True)
    for cls, objs in decoded.items():
        rows = []
        for obj in objs:
            try:
                tree = obj.read_typetree(check_read=False)
            except Exception as e:
                rows.append({"_path_id": obj.path_id, "_error": repr(e)})
                continue
            row = jsonable(tree, names)
            row["_path_id"] = obj.path_id
            rows.append(row)
        with open(os.path.join(OUT, cls + ".json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        print(f"dumped {cls}: {len(rows)}")

    with open(os.path.join(OUT, "_monobehaviour_class_counts.json"), "w", encoding="utf-8") as f:
        json.dump(counts.most_common(), f, indent=1)
    print("top classes:", counts.most_common(60))


if __name__ == "__main__":
    main()
