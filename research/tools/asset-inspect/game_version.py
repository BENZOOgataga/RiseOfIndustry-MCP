# RESEARCH TOOLING ONLY - static, read-only inspection of Rise of Industry asset files; not production code.
# Opens the game's resources.assets read-only (UnityPy reads into memory) and decodes the
# ProjectAutomata.GameVersion ScriptableObject named "Version" by hand from its raw bytes.
# Field order taken from decompiled ProjectAutomata/GameVersion.cs.
import struct
import sys

import UnityPy

GAME_DATA = r"C:\Program Files (x86)\Steam\steamapps\common\RiseOfIndustry\Rise of Industry_Data"


class R:
    def __init__(self, b):
        self.b, self.o = b, 0

    def i32(self):
        v = struct.unpack_from("<i", self.b, self.o)[0]
        self.o += 4
        return v

    def i64(self):
        v = struct.unpack_from("<q", self.b, self.o)[0]
        self.o += 8
        return v

    def u8(self):
        v = self.b[self.o]
        self.o += 1
        return v

    def align(self):
        self.o = (self.o + 3) & ~3

    def s(self):
        n = self.i32()
        v = self.b[self.o:self.o + n].decode("utf-8", "replace")
        self.o += n
        self.align()
        return v


def main():
    env = UnityPy.load(GAME_DATA + r"\resources.assets")
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        raw = obj.get_raw_data()
        r = R(raw)
        r.i32(); r.i64()          # m_GameObject
        r.u8(); r.align()         # m_Enabled
        r.i32(); r.i64()          # m_Script
        try:
            name = r.s()
        except Exception:
            continue
        if name != "Version":
            continue
        try:
            d = dict(buildType=r.i32(), releaseType=r.i32(), major=r.i32(), minor=r.i32(), revision=r.i32(),
                     suffix=r.s(), modIndicatorText=r.s(), build=r.s(), commitHash=r.s(),
                     savegameVersion=r.i32(), bugreporterEnabled=r.u8())
        except Exception as e:  # not the GameVersion object
            print("candidate 'Version' not decodable:", e, file=sys.stderr)
            continue
        print(obj.path_id, d)


if __name__ == "__main__":
    main()
