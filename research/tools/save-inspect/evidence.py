# RESEARCH TOOLING ONLY - static analysis of save COPIES; not production code
#
# Extracts gameplay-relevant evidence from a parsed save copy (task 3 of the
# save-format research): date, company/money, buildings + positions + recipes,
# inventories, ManualDestinationSlot settings, transport requests, vehicles,
# shops, market prices, regions/permits, tech, AI players.
#
# Usage: python evidence.py <in.sav> [<in2.sav> ...]

import json
import sys
from collections import Counter

import roi_save


def comp(entity, short):
    for c in entity.get("components", []):
        if c["objectType"] == "ProjectAutomata." + short:
            return c
    return None


def f(o, name, default=None):
    return (o or {}).get("fields", {}).get(name, default)


def pname(p):
    return p.get("$prefab") if isinstance(p, dict) else p


def j(v, n=400):
    s = json.dumps(roi_save.shrink(v, 12), cls=roi_save._Enc, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "..."


def nrbf_val(v):
    if isinstance(v, dict) and "$nrbf" in v:
        v = v["value"]
    if isinstance(v, dict) and "m_value" in v:
        return v["m_value"]
    return v


def report(path):
    tree, sp, timing = roi_save.load(path)
    M = tree["managers"]
    print("=" * 100)
    print("SAVE", path)
    print("timing", j(timing))
    h = tree["header"]
    print("header: name=%r timestamp=%s saveFormatVersion=%s build=%s module=%s mods=%s" % (
        h.get("name"), h.get("timestamp"), h.get("saveFormatVersion"), h.get("savegameBuild"), h.get("module"),
        [m.get("name") for m in h.get("mods") or []]))
    wd = tree["worldData"]
    print("world: name=%r size=%sx%s resourceNodeTypes=%d" % (wd["worldName"], wd["sizeX"], wd["sizeY"], len(wd["resourceNodes"])))

    tm = M["ProjectAutomata.TimeManager"]["fields"]
    d, mo = tm["_days"], tm["_months"]
    print("DATE: _days=%d _months=%d -> Y%d-%02d-%02d (30-day months)" % (d, mo, mo // 12 + 1, mo % 12 + 1, d % 30 + 1))

    actors = M["ProjectAutomata.ActorManager"]["entities"]
    guid_name = {}
    for a in actors:
        guid_name[a["guid"]] = (a["objectType"].split(".")[-1], f(a, "_actorName") or f(a, "_settlementName"))
    print("ACTORS:", [(v[0], v[1], g[:8]) for g, v in guid_name.items()])
    bal = f(M["ProjectAutomata.MoneyManager"], "_balances")
    for b in bal["decoded"]:
        print("  balance %-12s %-20s %16.2f" % (guid_name.get(b["actor"], ("?", "?"))[0], guid_name.get(b["actor"], ("?", "?"))[1], b["balance"]))

    for a in actors:
        t = a["objectType"].split(".")[-1]
        if t in ("HumanPlayer", "AiPlayer"):
            print("PLAYER %s name=%r guid=%s ctor=%s fields=%s" % (t, f(a, "_actorName"), a["guid"], a["constructorParams"],
                                                                 [k for k in a["fields"]]))
            print("   components:", [c["objectType"].split(".")[-1] for c in a["components"]])
            ma = comp(a, "MoneyAgent")
            bills = (f(ma, "_savegameBills") or {}).get("decoded") or []
            if bills:
                cats = Counter(b["category"] for b in bills)
                print("   bills: n=%d dates %s..%s categories=%s" % (len(bills), bills[0]["date"], bills[-1]["date"], dict(cats.most_common(8))))
                print("   bill sample:", j(bills[-1]))
            print("   loans:", j(f(comp(a, "LoansAgent"), "_loans")))
            tt = comp(a, "TechTreeAgent")
            if tt:
                unl = nrbf_val(f(tt, "_saveUnlockStates"))
                items = unl.get("$dict", []) if isinstance(unl, dict) else []
                unlocked = [k.get("name") for k, v in items if v]
                print("   tech unlocks: %d entries, %d true; sample=%s" % (len(items), len(unlocked), unlocked[:8]))
                rs = f(tt, "_research")
                print("   research queue:", j(f(rs, "_queue")), "progress:", j(f(rs, "_researchProgress"), 200))
            print("   statistics:", j(f(comp(a, "ActorStatisticsAgent"), "_topSales"), 300))

    # buildings
    bents = M["ProjectAutomata.BuildingManager"]["entities"]
    prefabs = Counter(pname(b["constructorParams"][0]) for b in bents)
    owners = Counter(guid_name.get(b["constructorParams"][4], ("?", str(b["constructorParams"][4])[:8]))[1] for b in bents)
    print("BUILDINGS: n=%d distinct prefabs=%d top=%s" % (len(bents), len(prefabs), prefabs.most_common(15)))
    print("   owners:", owners.most_common(10))
    sample_done = set()
    for b in bents:
        cp = b["constructorParams"]
        ctypes = [c["objectType"].split(".")[-1] for c in b["components"]]
        key = None
        for k in ("Factory", "Farm", "GathererHub", "Shop", "Warehouse"):
            if k in ctypes and k not in sample_done:
                key = k
        if key is None and not sample_done:
            key = "first"
        if key:
            sample_done.add(key)
            print("  SAMPLE[%s] guid=%s prefab=%s x=%s y=%s rot=%s owner=%s ctx=%s name=%r region=%s flags=%s paid=%s" % (
                key, b["guid"], pname(cp[0]), cp[1], cp[2], j(cp[3], 80), guid_name.get(cp[4], cp[4]), cp[5] if len(cp) > 5 else "-",
                f(b, "buildingName"), f(b, "region"), j(f(b, "buildingStateFlags"), 80), f(b, "paidToBuild")))
            print("      components:", ctypes)
            for c in b["components"]:
                if c["objectType"].split(".")[-1] in ("Factory", "Farm", "GathererHub", "ProductSpecificProductStorage",
                                                       "SingleProductStorage", "BuildingLogistics", "Upkeep", "BuildingEfficiency"):
                    print("      %s: %s" % (c["objectType"].split(".")[-1], j(c["fields"], 600)))
    # recipes
    recipes = Counter()
    for b in bents:
        for c in b["components"]:
            r = f(c, "_currentRecipe")
            if r:
                recipes[(c["objectType"].split(".")[-1], pname(r))] += 1
    print("RECIPES selected (component, recipe):", recipes.most_common(20))
    # inventories
    inv = Counter()
    for b in bents:
        for c in b["components"]:
            st = f(c, "_storage")
            if isinstance(st, dict) and st.get("$fixed"):
                for e in st["decoded"]:
                    inv[e["product"]] += e["store"]
            if c["objectType"].endswith("SingleProductStorage") and f(c, "_product"):
                inv[pname(f(c, "_product"))] += f(c, "_occupied") or 0
    print("INVENTORY totals over all storages (top 15):", inv.most_common(15))
    # manual destination slots
    slots = []
    for b in bents:
        mdm = comp(b, "ManualDestinationManager")
        if mdm:
            for s in f(mdm, "_savegameSlots") or []:
                slots.append((b, s))
    print("MANUAL DESTINATION SLOTS: n=%d in %d buildings" % (len(slots), len({id(b) for b, _ in slots})))
    for b, s in slots[:3]:
        print("   building %s (%s) slot: %s" % (b["guid"][:8], pname(b["constructorParams"][0]), j(s["fields"], 500)))
    nondef = [s for _, s in slots if f(s, "_minStoredAtSource") or f(s, "_autoMaxAccepted") or f(s, "_waitTillVehicleFull") or f(s, "paused")]
    print("   slots with non-default min/auto/wait/paused:", len(nondef), j([x["fields"] for x in nondef[:2]], 600))
    # transport requests
    trm = M["ProjectAutomata.TransportRequestManager"]["fields"]
    trs = trm.get("_savegameEntities") or []
    print("TRANSPORT REQUESTS: n=%d idCounter=%s statuses=%s" % (len(trs), nrbf_val(trm["_idCounter"]),
                                                                Counter(j(f(t, "status"), 80) for t in trs).most_common()))
    if trs:
        print("   sample:", j(trs[0]["fields"], 500))
    print("LOGISTIC REQUEST MANAGER entities:", len(M["ProjectAutomata.LogisticRequestManager"]["entities"] or []))
    # vehicles
    vents = M["ProjectAutomata.VehicleManager"]["entities"]
    print("VEHICLES: n=%d prefabs=%s" % (len(vents), Counter(pname(v["constructorParams"][0]) for v in vents).most_common(10)))
    if vents:
        v = vents[0]
        print("   sample ctor=%s pos=%s job=%s" % (j(v["constructorParams"]), f(v, "_save_position"), j(f(v, "_activeJob"), 500)))
    # shops
    shops = [(b, comp(b, "Shop")) for b in bents if comp(b, "Shop")]
    print("SHOPS: n=%d" % len(shops))
    for b, s in shops[:1]:
        print("   shop %s settlement=%s sold=%s" % (pname(b["constructorParams"][0]), f(b, "settlement"), [pname(p) for p in f(s, "sold") or []][:10]))
        print("   priceModifiers:", j(f(s, "_priceModifiers"), 300))
        print("   deliveredByActors:", j(f(s, "_deliveredByActors"), 300))
        print("   demandFigures:", j(f(s, "_demandFigures"), 400))
    # global market
    gm = M["ProjectAutomata.GlobalMarket"]["fields"]
    pi = nrbf_val(gm["_serializedPricingInfo"])
    print("GLOBAL MARKET pricing entries:", len(pi) if isinstance(pi, list) else pi, "sample:", j(pi[:2] if isinstance(pi, list) else pi, 600))
    # settlements
    for a in actors:
        if a["objectType"].endswith("Settlement"):
            print("SETTLEMENT %r pop=%s tier=%s type=%s region=%s" % (f(a, "_settlementName"), f(a, "_population"), pname(f(a, "_tier")),
                                                                  pname(f(a, "_type")), f(a, "_region")))
    # regions / permits
    for r in M["ProjectAutomata.RegionManager"]["entities"]:
        print("REGION %r guid=%s ctor=%s sites=%d" % (f(r, "_regionName"), r["guid"][:8], j([p for p in r["constructorParams"] if not isinstance(p, list)], 200),
                                                   len(f(r, "_resourceSites") or [])))
    print("PERMITS:", j(nrbf_val(f(M["ProjectAutomata.PermitManager"], "_savegamePermits")), 800))
    print("AI MANAGER:", j(M["ProjectAutomata.AiPlayerManager"]["fields"], 600))
    print("GAME PARAMETERS:", j(M["ProjectAutomata.GameParametersManager"]["fields"], 900))
    print("PRODUCTION STATS (cost record) sample:", j(nrbf_val(f(M["ProjectAutomata.ProductionStatsTracker"], "_savegameProductCostRecord"))[:2], 500))


if __name__ == "__main__":
    for p in sys.argv[1:]:
        report(p)
