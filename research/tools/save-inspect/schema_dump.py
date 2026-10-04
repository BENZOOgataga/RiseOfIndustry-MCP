# RESEARCH TOOLING ONLY - static analysis of save COPIES; not production code
#
# Builds a schema/field inventory from a parsed save: for every ObjectSaveData
# objectType (managers, entities, components, nested special-serialized objects)
# lists the field names, the serialized value kinds and one short sample value.
#
# Usage: python schema_dump.py <in.sav> <out.txt>

import json
import sys
from collections import defaultdict

import roi_save


def kind_of(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        inner = sorted({kind_of(x) for x in v[:20]})
        return "list[%s]" % ",".join(inner)
    if isinstance(v, dict):
        if "$kind" in v:
            return "OSD<%s>" % v["objectType"].split(".")[-1]
        if "$prefab" in v:
            return "prefab<%s>" % v["type"].split(".")[-1]
        if "$nrbf" in v:
            return "nrbf<%s>" % v["$nrbf"]
        if "$enum" in v:
            return "enum<%s>" % v["$enum"]
        if "$bytes" in v:
            return "bytes"
        if "$dict" in v:
            return "dict"
        return "obj"
    return type(v).__name__


def short(v, n=160):
    s = json.dumps(roi_save.shrink(v, 8), cls=roi_save._Enc, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "..."


def walk(osd, schema, counts, role):
    t = osd["objectType"]
    counts[(role, t)] += 1
    for name, val in osd.get("fields", {}).items():
        ent = schema[t].setdefault(name, {"kinds": set(), "sample": None})
        ent["kinds"].add(kind_of(val))
        if ent["sample"] is None and val not in (None, [], {}):
            ent["sample"] = short(val)
        for sub in iter_osd(val):
            walk(sub, schema, counts, "nested")
    for e in osd.get("entities", []) or []:
        walk(e, schema, counts, "entity")
        for c in e.get("components", []):
            walk(c, schema, counts, "component")
        for p in e.get("constructorParams", []):
            ent = schema[e["objectType"]].setdefault("<ctor-param>", {"kinds": set(), "sample": None})
            ent["kinds"].add(kind_of(p))
            if ent["sample"] is None:
                ent["sample"] = short(p)


def iter_osd(v):
    if isinstance(v, dict):
        if "$kind" in v:
            yield v
        elif "$dict" in v:
            for k, x in v["$dict"]:
                yield from iter_osd(k)
                yield from iter_osd(x)
    elif isinstance(v, list):
        for x in v:
            yield from iter_osd(x)


def main():
    tree, sp, timing = roi_save.load(sys.argv[1])
    schema = defaultdict(dict)
    counts = defaultdict(int)
    for mt, m in tree["managers"].items():
        walk(m, schema, counts, "manager")
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        f.write("# schema dump of %s\n# timing %s\n" % (sys.argv[1], json.dumps(timing)))
        f.write("# Serializer type-tag histogram (top 60):\n")
        for t, c in sorted(sp.stats_types.items(), key=lambda x: -x[1])[:60]:
            f.write("#   %6d %s\n" % (c, t))
        f.write("# NRBF class histogram (top 40):\n")
        for t, c in sorted(sp.nrbf_classes.items(), key=lambda x: -x[1])[:40]:
            f.write("#   %6d %s\n" % (c, t))
        roles = defaultdict(list)
        for (role, t), c in counts.items():
            roles[t].append("%s x%d" % (role, c))
        for t in sorted(schema):
            f.write("\n== %s  [%s]\n" % (t, ", ".join(roles[t])))
            for name, ent in schema[t].items():
                f.write("   %-40s %-45s %s\n" % (name, "|".join(sorted(ent["kinds"])), ent["sample"]))
    print("wrote", sys.argv[2])


if __name__ == "__main__":
    main()
