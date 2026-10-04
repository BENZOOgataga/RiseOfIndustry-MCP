# RESEARCH TOOLING ONLY - static analysis of save COPIES; not production code
#
# Static parser for Rise of Industry (original, Steam 671440) .sav files.
# Never executes game code. Reads a save COPY, decompresses the lz4net
# LZ4Stream framing, then walks the ProjectAutomata.Serializer tagged format
# and parses embedded MS-NRBF (BinaryFormatter) blobs structurally.
#
# Format reference (decompiled ProjectAutomata):
#   Savegame.Serialize / SerializeRaw, SavegameHeader, Serializer,
#   ObjectSaveData / ManagerSaveData / EntitySaveData / EntityComponentSaveData,
#   SavegameWorldData.
#
# Usage:
#   python roi_save.py decompress <in.sav> <out.bin>
#   python roi_save.py header    <in.sav>
#   python roi_save.py summary   <in.sav> [--json out.json]

import io
import json
import re
import struct
import sys
import time
import uuid

import lz4.block

# ---------------------------------------------------------------------------
# lz4net (Milosz Krajewski) LZ4Stream framing
#   chunk := varint flags | varint originalLength | [varint compressedLength if flags&1] | data
#   flags: 1=Compressed, 2=HighCompression, bits>=2 -> passes (unsupported >1)
# ---------------------------------------------------------------------------


def _varint(buf, pos):
    result = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def lz4net_decompress(data):
    out = bytearray()
    pos = 0
    chunks = 0
    n = len(data)
    while pos < n:
        flags, pos = _varint(data, pos)
        orig_len, pos = _varint(data, pos)
        compressed = bool(flags & 1)
        if compressed:
            comp_len, pos = _varint(data, pos)
        else:
            comp_len = orig_len
        block = data[pos:pos + comp_len]
        pos += comp_len
        if compressed:
            if (flags >> 2) > 1:
                raise ValueError("multi-pass chunks not supported")
            out += lz4.block.decompress(block, uncompressed_size=orig_len)
        else:
            out += block
        chunks += 1
    return bytes(out), chunks


# ---------------------------------------------------------------------------
# .NET BinaryReader primitives
# ---------------------------------------------------------------------------


class Reader:
    __slots__ = ("buf", "pos")

    def __init__(self, buf, pos=0):
        self.buf = buf
        self.pos = pos

    def u8(self):
        v = self.buf[self.pos]
        self.pos += 1
        return v

    def bool(self):
        return self.u8() != 0

    def _s(self, fmt, size):
        v = struct.unpack_from(fmt, self.buf, self.pos)[0]
        self.pos += size
        return v

    def i8(self): return self._s("<b", 1)
    def i16(self): return self._s("<h", 2)
    def u16(self): return self._s("<H", 2)
    def i32(self): return self._s("<i", 4)
    def u32(self): return self._s("<I", 4)
    def i64(self): return self._s("<q", 8)
    def u64(self): return self._s("<Q", 8)
    def f32(self): return self._s("<f", 4)
    def f64(self): return self._s("<d", 8)

    def bytes(self, n):
        v = self.buf[self.pos:self.pos + n]
        if len(v) != n:
            raise EOFError("unexpected end of data")
        self.pos += n
        return v

    def guid(self):
        return str(uuid.UUID(bytes_le=bytes(self.bytes(16))))

    def str7(self):
        # BinaryWriter.Write(string): 7-bit encoded length + UTF-8
        n, self.pos = _varint(self.buf, self.pos)
        return self.bytes(n).decode("utf-8", errors="replace")

    def char(self):
        b0 = self.buf[self.pos]
        ln = 1 if b0 < 0x80 else 2 if b0 < 0xE0 else 3 if b0 < 0xF0 else 4
        return self.bytes(ln).decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# MS-NRBF (BinaryFormatter) structural parser
# ---------------------------------------------------------------------------

PRIM_FMT = {1: ("<?", 1), 2: ("<B", 1), 6: ("<d", 8), 7: ("<h", 2), 8: ("<i", 4),
            9: ("<q", 8), 10: ("<b", 1), 11: ("<f", 4), 12: ("<q", 8), 13: ("<Q", 8),
            14: ("<H", 2), 15: ("<I", 4), 16: ("<Q", 8)}
PRIM_ARR = {1: "?", 2: "B", 6: "d", 7: "h", 8: "i", 9: "q", 10: "b", 11: "f", 12: "q",
            13: "Q", 14: "H", 15: "I", 16: "Q"}


class Ref:
    __slots__ = ("id",)

    def __init__(self, i):
        self.id = i


class NObj(dict):
    """A BinaryFormatter class instance: dict of member -> value, plus $type."""


class Nulls:
    __slots__ = ("n",)

    def __init__(self, n):
        self.n = n


class NrbfParser:
    def __init__(self, buf, pos=0):
        self.r = Reader(buf, pos)
        self.objects = {}
        self.classes = {}  # metadataId -> (name, members, types, extra)
        self.libraries = {}
        self.root_id = None
        self.class_names = {}  # stats: class name -> count

    def read_prim(self, t):
        r = self.r
        if t in PRIM_FMT:
            fmt, sz = PRIM_FMT[t]
            v = struct.unpack_from(fmt, r.buf, r.pos)[0]
            r.pos += sz
            return v
        if t == 3:
            return r.char()
        if t == 5:
            return r.str7()  # decimal as string
        if t == 18:
            return r.str7()
        if t == 17:
            return None
        raise ValueError("unknown primitive type %d" % t)

    def class_info(self):
        r = self.r
        oid = r.i32()
        name = r.str7()
        cnt = r.i32()
        members = [r.str7() for _ in range(cnt)]
        return oid, name, members

    def member_type_info(self, cnt):
        r = self.r
        bts = [r.u8() for _ in range(cnt)]
        extra = []
        for bt in bts:
            if bt in (0, 7):
                extra.append(r.u8())
            elif bt == 3:
                extra.append(r.str7())
            elif bt == 4:
                extra.append((r.str7(), r.i32()))
            else:
                extra.append(None)
        return bts, extra

    def read_members(self, oid, name, members, bts, extra):
        obj = NObj()
        obj["$type"] = name
        self.objects[oid] = obj
        self.class_names[name] = self.class_names.get(name, 0) + 1
        for i, m in enumerate(members):
            if bts is not None and bts[i] == 0:
                obj[m] = self.read_prim(extra[i])
            else:
                v = self.read_record()
                if isinstance(v, Nulls):
                    v = None
                obj[m] = v
        return obj

    def read_elements(self, count, prim=None):
        r = self.r
        if prim is not None:
            if prim in PRIM_ARR:
                fmt = PRIM_ARR[prim]
                sz = struct.calcsize(fmt)
                vals = list(struct.unpack_from("<%d%s" % (count, fmt), r.buf, r.pos))
                r.pos += sz * count
                return vals
            return [self.read_prim(prim) for _ in range(count)]
        out = []
        while len(out) < count:
            v = self.read_record()
            if isinstance(v, Nulls):
                out.extend([None] * v.n)
            else:
                out.append(v)
        return out

    def read_record(self):
        r = self.r
        rt = r.u8()
        if rt == 0:
            self.root_id = r.i32()
            r.i32(); r.i32(); r.i32()
            return self.read_record()
        if rt == 1:  # ClassWithId
            oid = r.i32()
            mid = r.i32()
            name, members, bts, extra = self.classes[mid]
            return self.read_members(oid, name, members, bts, extra)
        if rt in (2, 3):  # untyped members
            oid, name, members = self.class_info()
            if rt == 3:
                r.i32()
            self.classes[oid] = (name, members, None, None)
            return self.read_members(oid, name, members, None, None)
        if rt in (4, 5):
            oid, name, members = self.class_info()
            bts, extra = self.member_type_info(len(members))
            if rt == 5:
                r.i32()  # library id
            self.classes[oid] = (name, members, bts, extra)
            return self.read_members(oid, name, members, bts, extra)
        if rt == 6:
            oid = r.i32()
            s = r.str7()
            self.objects[oid] = s
            return s
        if rt == 7:  # BinaryArray
            oid = r.i32()
            atype = r.u8()
            rank = r.i32()
            lengths = [r.i32() for _ in range(rank)]
            if atype in (3, 4, 5):
                [r.i32() for _ in range(rank)]
            bt = r.u8()
            extra = None
            if bt in (0, 7):
                extra = r.u8()
            elif bt == 3:
                extra = r.str7()
            elif bt == 4:
                extra = (r.str7(), r.i32())
            total = 1
            for ln in lengths:
                total *= ln
            vals = self.read_elements(total, extra if bt == 0 else None)
            self.objects[oid] = vals
            return vals
        if rt == 8:
            t = r.u8()
            return self.read_prim(t)
        if rt == 9:
            return Ref(r.i32())
        if rt == 10:
            return None
        if rt == 11:
            return StopIteration
        if rt == 12:
            lid = r.i32()
            self.libraries[lid] = r.str7()
            return self.read_record()
        if rt == 13:
            return Nulls(r.u8())
        if rt == 14:
            return Nulls(r.i32())
        if rt == 15:
            oid = r.i32()
            ln = r.i32()
            t = r.u8()
            if t == 2:
                vals = bytes(r.bytes(ln))
            else:
                vals = self.read_elements(ln, t)
            self.objects[oid] = vals
            return vals
        if rt in (16, 17):
            oid = r.i32()
            ln = r.i32()
            vals = self.read_elements(ln)
            self.objects[oid] = vals
            return vals
        raise ValueError("unsupported NRBF record type %d at %d" % (rt, r.pos - 1))

    def parse(self):
        while True:
            v = self.read_record()
            if v is StopIteration:
                break
        self._resolve()
        return self.objects.get(self.root_id)

    def _resolve(self):
        def fix(v):
            if isinstance(v, Ref):
                return self.objects.get(v.id)
            return v
        for o in self.objects.values():
            if isinstance(o, NObj):
                for k in list(o.keys()):
                    o[k] = fix(o[k])
            elif isinstance(o, list):
                for i, x in enumerate(o):
                    if isinstance(x, Ref):
                        o[i] = fix(x)


def simplify_nrbf(v, depth=0, seen=None):
    """Turn common BCL shapes (List`1, Dictionary`2, enums) into plain values."""
    if seen is None:
        seen = set()
    if isinstance(v, NObj):
        if id(v) in seen or depth > 40:
            return {"$cycle": v.get("$type")}
        seen = seen | {id(v)}
        t = v.get("$type", "")
        if t.startswith("System.Collections.Generic.List`1") and "_items" in v:
            items = v.get("_items") or []
            return [simplify_nrbf(x, depth + 1, seen) for x in items[: v.get("_size", 0)]]
        if t.startswith("System.Collections.Generic.Dictionary`2"):
            kv = v.get("KeyValuePairs") or []
            return {"$dict": [[simplify_nrbf(p.get("key"), depth + 1, seen), simplify_nrbf(p.get("value"), depth + 1, seen)]
                              for p in kv if isinstance(p, NObj)]}
        if set(v.keys()) == {"$type", "value__"}:
            return {"$enum": t, "value": v["value__"]}
        return {k: simplify_nrbf(x, depth + 1, seen) for k, x in v.items()}
    if isinstance(v, list):
        return [simplify_nrbf(x, depth + 1, seen) for x in v]
    if isinstance(v, (bytes, bytearray)):
        return {"$bytes": len(v), "data": bytes(v)}
    return v


def parse_nrbf(buf):
    p = NrbfParser(buf)
    root = p.parse()
    return root, p


# ---------------------------------------------------------------------------
# ProjectAutomata.Serializer tagged format
# ---------------------------------------------------------------------------

_ASM_RE = re.compile(r",\s*[^,\[\]]+,\s*Version=[^,\]]+,\s*Culture=[^,\]]+,\s*PublicKeyToken=[^,\]]+")


def canon(aqn):
    s = _ASM_RE.sub("", aqn)
    return s


SCALARS = {
    "System.Int32": lambda r: r.i32(),
    "System.String": lambda r: r.str7(),
    "System.Single": lambda r: r.f32(),
    "System.Double": lambda r: r.f64(),
    "System.Boolean": lambda r: r.bool(),
    "System.Guid": lambda r: r.guid(),
    "UnityEngine.Vector3": lambda r: [r.f32(), r.f32(), r.f32()],
    "UnityEngine.Vector2": lambda r: [r.f32(), r.f32()],
    "UnityEngine.Quaternion": lambda r: [r.f32(), r.f32(), r.f32(), r.f32()],
    "ProjectAutomata.GameDate": lambda r: _gamedate(r),
    "ProjectAutomata.SavegamePrefabIdentifier": lambda r: {"$prefab": r.str7(), "type": canon(r.str7())},
    "ProjectAutomata.BlockReason": lambda r: r.u32(),
    "ProjectAutomata.Biome": lambda r: r.u8(),
}


def _gamedate(r):
    d = r.i32(); m = r.i32(); y = r.i32()
    return "%04d-%02d-%02d" % (y, m, d)


# ---------------------------------------------------------------------------
# Decoders for ObjectSaveData+FixedSerializationData (ISavegameSerializable).
# The byte payload format is type-specific (each class's Serialize(BinaryWriter)),
# so we only decode the ones we have reverse-read from the decompiled source.
# Keyed by (declaring objectType, field name).
# ---------------------------------------------------------------------------


def _dict(r, kv):
    n = r.i32()
    return [kv(r) for _ in range(n)]


def _dec_balances(r):  # MoneyManager.Balances : SavegameDictionary<IMoneyAgent,double>
    return _dict(r, lambda r: {"actor": r.guid(), "balance": r.f64()})


def _dec_bill(r):  # MoneyBill.Serialize
    amount = r.f64(); y = r.i32(); m = r.i32(); d = r.i32()
    return {"amount": amount, "date": "%04d-%02d-%02d" % (y, m, d), "category": r.str7(),
            "recipient": r.guid(), "originator": r.guid()}


def _dec_storage(r):  # StorageSavegameDictionary: product name + [pulls, puts, stored]
    return _dict(r, lambda r: {"product": r.str7(), "pull": r.i32(), "put": r.i32(), "store": r.i32()})


def _dec_name_int(r):  # MaxAcceptedDictionary
    return _dict(r, lambda r: {"product": r.str7(), "value": r.i32()})


def _dec_name_float(r):  # PriceModifiers / ResearchProgress
    return _dict(r, lambda r: {"name": r.str7(), "value": r.f32()})


def _dec_delivered(r):  # Shop.DeliveredByActors
    def kv(r):
        actor = r.i32()
        n = r.i32()
        return {"actorId": actor, "products": {r.str7(): r.i32() for _ in range(n)}}
    return _dict(r, kv)


def _dec_research_queue(r):  # ResearchQueue: Serializer.SerializeList(name)
    n = r.i32()
    return [None if r.bool() else r.str7() for _ in range(n)]


FIXED_DECODERS = {
    ("ProjectAutomata.MoneyManager", "_balances"): ("single", _dec_balances),
    ("ProjectAutomata.MoneyAgent", "_savegameBills"): ("each", _dec_bill),
    ("ProjectAutomata.ProductSpecificProductStorage", "_storage"): ("single", _dec_storage),
    ("ProjectAutomata.ProductSpecificProductStorage", "_maxAcceptedMap"): ("single", _dec_name_int),
    ("ProjectAutomata.InfiniteStorage", "_maxAccepted"): ("single", _dec_name_int),
    ("ProjectAutomata.Shop", "_priceModifiers"): ("single", _dec_name_float),
    ("ProjectAutomata.Shop", "_deliveredByActors"): ("single", _dec_delivered),
    ("ProjectAutomata.TechTreeAgentResearchState", "_researchProgress"): ("single", _dec_name_float),
    ("ProjectAutomata.TechTreeAgentResearchState", "_queue"): ("single", _dec_research_queue),
}


def decode_fixed(owner, field, val):
    dec = FIXED_DECODERS.get((owner, field))
    if dec is None or not isinstance(val, dict):
        return None
    inner = val.get("value") if "$nrbf" in val else val
    if not isinstance(inner, dict) or "data" not in inner:
        return None
    blob = inner["data"]
    data = blob.get("data") if isinstance(blob, dict) else blob
    r = Reader(data)
    mode, fn = dec
    if mode == "single":
        out = fn(r)
    else:
        out = [fn(r) for _ in range(inner.get("elementCount", 0))]
    return {"decoded": out, "consumed_all": r.pos == len(data)}


class SaveParser:
    def __init__(self, buf, keep_nrbf=True):
        self.r = Reader(buf)
        self.stats_types = {}
        self.nrbf_blobs = 0
        self.nrbf_bytes = 0
        self.nrbf_classes = {}
        self.keep_nrbf = keep_nrbf
        self.unparsed_blobs = []

    # --- element dispatch for registered serializers ---
    def _elem_reader(self, t):
        if t in SCALARS:
            return SCALARS[t]
        if t in ("ProjectAutomata.ObjectSaveData",):
            return lambda r: self.object_save_data(kind="object")
        if t == "ProjectAutomata.ManagerSaveData":
            return lambda r: self.object_save_data(kind="manager")
        if t == "ProjectAutomata.EntitySaveData":
            return lambda r: self.object_save_data(kind="entity")
        return None

    def _dict_reader(self, t):
        m = re.fullmatch(r"System\.Collections\.Generic\.Dictionary`2\[\[(.+)\],\[(.+)\]\]", t)
        if not m:
            return None
        kt, vt = m.group(1), m.group(2)
        kr = self._elem_reader(kt)
        vr = self._elem_reader(vt) or self._dict_reader(vt)
        if kr is None or vr is None:
            return None
        allowed = {
            ("ProjectAutomata.GameDate", "System.Double"),
            ("ProjectAutomata.GameDate", "System.Single"),
            ("ProjectAutomata.GameDate", "System.Int32"),
            ("ProjectAutomata.GameDate", "ProjectAutomata.SavegamePrefabIdentifier"),
            ("ProjectAutomata.SavegamePrefabIdentifier",
             "System.Collections.Generic.Dictionary`2[[ProjectAutomata.GameDate],[System.Int32]]"),
        }
        if (kt, vt) not in allowed:
            return None

        def rd(r):
            if r.bool():
                return None
            n = r.i32()
            out = []
            for _ in range(n):
                kn = r.bool(); vn = r.bool()
                k = None if kn else kr(r)
                v = None if vn else vr(r)
                out.append([k, v])
            return {"$dict": out}
        return rd

    def _reader_for(self, t):
        if t == "System.Byte[]":
            def rb(r):
                n = r.i32()
                return {"$bytes": n, "data": bytes(r.bytes(n))}
            return rb
        er = self._elem_reader(t) or self._dict_reader(t)
        if er is not None:
            return er
        inner = None
        if t.endswith("[]"):
            inner = t[:-2]
        else:
            m = re.fullmatch(r"System\.Collections\.Generic\.List`1\[\[(.+)\]\]", t)
            if m:
                inner = m.group(1)
        if inner is not None and inner != "System.Byte":
            ier = self._elem_reader(inner) or self._dict_reader(inner)
            if ier is not None:
                def ra(r):
                    n = r.i32()
                    out = []
                    for _ in range(n):
                        out.append(None if r.bool() else ier(r))
                    return out
                return ra
        return None

    def value(self):
        """Serializer.Deserialize"""
        r = self.r
        if r.bool():
            return None
        aqn = r.str7()
        t = canon(aqn)
        self.stats_types[t] = self.stats_types.get(t, 0) + 1
        rd = self._reader_for(t)
        if rd is not None:
            return rd(r)
        n = r.i32()
        blob = r.bytes(n)
        self.nrbf_blobs += 1
        self.nrbf_bytes += n
        try:
            root, p = parse_nrbf(blob)
            for k, c in p.class_names.items():
                self.nrbf_classes[k] = self.nrbf_classes.get(k, 0) + c
            val = simplify_nrbf(root)
            if isinstance(val, dict) and "$type" not in val and "$enum" not in val and "$dict" not in val:
                val = {"$type": t, "value": val}
            return {"$nrbf": t, "value": val} if not isinstance(val, dict) or "$enum" not in val else val
        except Exception as e:  # keep going; record failure
            self.unparsed_blobs.append((t, n, repr(e)))
            return {"$nrbf_unparsed": t, "len": n, "err": repr(e)}

    def object_save_data(self, kind):
        r = self.r
        o = {"$kind": kind, "objectType": canon(r.str7())}
        cnt = r.i32()
        fields = {}
        for _ in range(cnt):
            name = r.str7()
            fields[name] = self.value()
            dec = decode_fixed(o["objectType"], name, fields[name])
            if dec is not None:
                fields[name] = {"$fixed": True, **dec}
        o["fields"] = fields
        if kind == "manager":
            n = r.i32()
            o["entities"] = [self.object_save_data("entity_inline") for _ in range(n)]
        elif kind in ("entity", "entity_inline"):
            o["guid"] = r.guid()
            n = r.i32()
            o["components"] = [self.object_save_data("component") for _ in range(n)]
            n = r.i32()
            o["constructorParams"] = [self.value() for _ in range(n)]
        elif kind == "component":
            o["guid"] = r.guid()
        return o

    def parse(self):
        r = self.r
        out = {}
        hlen = r.i32()
        hblob = r.bytes(hlen)
        hroot, _ = parse_nrbf(hblob)
        out["header"] = simplify_nrbf(hroot)
        out["metadata"] = {"createdSavegameBuild": self.value()}
        wd = {}
        wd["randomState"] = self.value()
        wd["sizeX"] = self.value()
        wd["sizeY"] = self.value()
        wd["worldName"] = self.value()
        wd["height"] = self.value()
        wd["biomes"] = self.value()
        wd["water"] = self.value()
        wd["blocked"] = self.value()
        n = r.i32()
        keys = [r.str7() for _ in range(n)]
        nodes = {}
        for k in keys:
            c = r.i32()
            nodes[k] = [[r.i32(), r.i32(), r.i32(), r.i32()] for _ in range(c)]
        wd["resourceNodes"] = nodes
        out["worldData"] = wd
        out["camera"] = {"position": self.value(), "rotation": self.value(), "zoom": self.value(), "mode": self.value()}
        out["gameMode"] = {"achievementsEnabled": self.value()}
        # managerSaveData: Serializer.Serialize(Dictionary<Type,ManagerSaveData>)
        isnull = r.bool()
        managers = {}
        if not isnull:
            t = canon(r.str7())
            assert t.startswith("System.Collections.Generic.Dictionary`2[[System.Type],[ProjectAutomata.ManagerSaveData]]"), t
            n = r.i32()
            for _ in range(n):
                mt = canon(r.str7())
                managers[mt] = self.object_save_data("manager")
        out["managers"] = managers
        out["savegameVersion"] = self.value()
        out["worldParameters"] = self.value()
        if isinstance(out["savegameVersion"], int) and out["savegameVersion"] >= 200:
            # Serializer.SerializeString: raw BinaryWriter string, no null/type tag
            out["module"] = r.str7()
        out["$trailing_bytes"] = len(r.buf) - r.pos
        return out


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def load(path):
    t0 = time.perf_counter()
    raw = open(path, "rb").read()
    t1 = time.perf_counter()
    dec, chunks = lz4net_decompress(raw)
    t2 = time.perf_counter()
    sp = SaveParser(dec)
    tree = sp.parse()
    t3 = time.perf_counter()
    timing = {"read_s": t1 - t0, "decompress_s": t2 - t1, "parse_s": t3 - t2,
              "file_bytes": len(raw), "decompressed_bytes": len(dec), "lz4_chunks": chunks}
    return tree, sp, timing


class _Enc(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, (bytes, bytearray)):
            return {"$bytes": len(o), "head": bytes(o[:32]).hex()}
        return repr(o)


def shrink(v, maxlist=200):
    if isinstance(v, dict):
        if "$bytes" in v and "data" in v:
            return {"$bytes": v["$bytes"], "head": v["data"][:32].hex()}
        return {k: shrink(x, maxlist) for k, x in v.items()}
    if isinstance(v, list):
        if len(v) > maxlist and all(not isinstance(x, (dict, list)) for x in v[:50]):
            return {"$list_len": len(v), "head": v[:16]}
        return [shrink(x, maxlist) for x in v]
    return v


def read_header(path):
    """Header only: decompress just the first LZ4 chunk(s) needed for the
    int32 length + BinaryFormatter SavegameHeader (mirrors SavegameStorage.GetAllSavegames)."""
    import datetime
    raw = open(path, "rb").read(4 * 1024 * 1024)
    pos = 0
    out = bytearray()
    while pos < len(raw):
        flags, pos = _varint(raw, pos)
        orig_len, pos = _varint(raw, pos)
        comp_len, pos = _varint(raw, pos) if flags & 1 else (orig_len, pos)
        blk = raw[pos:pos + comp_len]
        pos += comp_len
        out += lz4.block.decompress(blk, uncompressed_size=orig_len) if flags & 1 else blk
        if len(out) >= 4 and len(out) >= 4 + struct.unpack_from("<i", out)[0]:
            break
    hlen = struct.unpack_from("<i", out)[0]
    root, _ = parse_nrbf(bytes(out[4:4 + hlen]))
    h = simplify_nrbf(root)
    # Utils.GetTimestamp(): seconds since DateTime.MinValue.AddYears(1970) == 1971-01-01 UTC
    ts = h.get("timestamp")
    if isinstance(ts, int):
        h["timestamp_utc"] = (datetime.datetime(1971, 1, 1, tzinfo=datetime.timezone.utc)
                              + datetime.timedelta(seconds=ts)).isoformat()
    return h


def main(argv):
    if len(argv) < 3:
        print(__doc__ or "see header")
        return 2
    cmd, src = argv[1], argv[2]
    if cmd == "decompress":
        dec, chunks = lz4net_decompress(open(src, "rb").read())
        open(argv[3], "wb").write(dec)
        print("chunks=%d decompressed=%d" % (chunks, len(dec)))
        return 0
    if cmd == "header":
        t0 = time.perf_counter()
        h = read_header(src)
        print(json.dumps(h, cls=_Enc, ensure_ascii=False, indent=1))
        print("header_s=%.4f" % (time.perf_counter() - t0))
        return 0
    if cmd == "summary":
        tree, sp, timing = load(src)
        out_json = None
        if "--json" in argv:
            out_json = argv[argv.index("--json") + 1]
        print(json.dumps(timing, indent=1))
        print("header:", json.dumps(tree["header"], cls=_Enc)[:2000])
        print("savegameVersion:", tree["savegameVersion"], "module:", tree.get("module"),
              "trailing:", tree["$trailing_bytes"])
        print("NRBF blobs:", sp.nrbf_blobs, "bytes:", sp.nrbf_bytes, "unparsed:", len(sp.unparsed_blobs))
        for m, d in sorted(tree["managers"].items()):
            ents = d.get("entities") or []
            et = {}
            for e in ents:
                et[e["objectType"]] = et.get(e["objectType"], 0) + 1
            print("MANAGER %s fields=%s entities=%d %s" % (m, list(d["fields"].keys()), len(ents),
                                                         dict(sorted(et.items(), key=lambda x: -x[1])[:12])))
        if out_json:
            with open(out_json, "w", encoding="utf-8") as f:
                json.dump(shrink(tree), f, cls=_Enc, indent=1)
            print("wrote", out_json)
        return 0
    print("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
