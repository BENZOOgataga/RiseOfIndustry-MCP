"""Large SYNTHETIC fixture for PERF-9 (not committed). About 3x the sample-save counts:
~450 player buildings, ~750 routes, 270 player vehicles, ~2,000 AI buildings.

    uv run --directory mcp-server python tests/perf/build_large_fixture.py [out_dir]

Default output: <repo>/.local/large-fixture/ (gitignored).
"""

from __future__ import annotations

import copy
import sys
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "fixtures"))

import build_fixtures as bf  # noqa: E402

REPO = HERE.parents[2]
DEFAULT_OUT = REPO / ".local" / "large-fixture"

CLUSTERS = 35
SHOP_CITIES = 20
AI_BUILDINGS = 2000
PLAYER_VEHICLES = 270
HISTORY_MONTHS = 36


def shift_key(key: str, dx: int, dy: int) -> str:
    prefab, rest = key.split("@", 1)
    x, y = (int(v) for v in rest.split("#")[0].split(","))
    return f"{prefab}@{x + dx},{y + dy}"


def build_large(now=bf.BASE_TIME) -> dict:
    data = bf.state_data()
    base_buildings = data["buildings_player"]
    base_routes = data["routes_player"]
    shops_base = bf.shops()
    cities_base = bf.cities()

    cities, shops, regions = [], [], []
    for c in range(SHOP_CITIES):
        tmpl = copy.deepcopy(cities_base[c % len(cities_base)])
        cid = 100 + c
        rid = f"b2000000-0000-4000-8000-{c:012d}"
        cx, cy = 600 + (c % 5) * 120, 100 + (c // 5) * 120
        city_shops = []
        for s_i, stmpl in enumerate(shops_base):
            s = copy.deepcopy(stmpl)
            s["building"] = f"{stmpl['prefab']}@{cx + s_i * 3},{cy + s_i}"
            s["city_id"] = cid
            s["owner_actor_id"] = cid
            s["display_name"] = f"{stmpl['display_name']} {c}"
            shops.append(s)
            city_shops.append(s["building"])
        tmpl.update({"city_id": cid, "name": f"Ville {c:02d}", "region_id": rid, "shops": city_shops, "center_x": cx, "center_y": cy})
        cities.append(tmpl)
        regions.append({**bf.regions()[0], "region_id": rid, "name": f"Ville {c:02d}", "city_id": cid, "center_x": cx, "center_y": cy,
                        "permit": {"owner_actor_id": bf.AI_B if c % 2 else None, "amount_paid": 0.0, "type": "RegionFull"}})
    for r in range(40):
        rid = f"b3000000-0000-4000-8000-{r:012d}"
        regions.append({**bf.regions()[3], "region_id": rid, "name": f"Campagne {r:02d}",
                        "permit": {"owner_actor_id": bf.PLAYER if r < CLUSTERS else None, "amount_paid": 1.0, "type": "RegionFull"}})

    buildings, routes = [], []
    for k in range(CLUSTERS):
        dx, dy = (k % 7) * 80, (k // 7) * 90
        keymap = {b["key"]: shift_key(b["key"], dx, dy) for b in base_buildings}
        region = f"b3000000-0000-4000-8000-{k:012d}"
        for b in base_buildings:
            nb = copy.deepcopy(b)
            nb["key"] = keymap[b["key"]]
            nb["x"] += dx
            nb["y"] += dy
            nb["display_name"] = f"{b['display_name']} C{k}"
            nb["region_id"] = region
            if nb.get("modules"):
                for m in nb["modules"]["items"]:
                    m["key"] = shift_key(m["key"], dx, dy)
            if nb.get("logistics") and nb["logistics"].get("warehouse"):
                nb["logistics"]["warehouse"] = keymap.get(nb["logistics"]["warehouse"], nb["logistics"]["warehouse"])
            buildings.append(nb)
        for r in base_routes:
            nr = copy.deepcopy(r)
            nr["origin"] = keymap.get(r["origin"], r["origin"])
            dest = keymap.get(r["destination"])
            if dest is None:  # shop destination: point to a shop of a large-fixture city
                s = shops[(k * 4 + base_routes.index(r)) % len(shops)]
                dest = s["building"]
                nr["destination_city_id"] = s["city_id"]
                nr["destination_owner_actor_id"] = s["owner_actor_id"]
            nr["destination"] = dest
            nr["endpoint"] = dest
            nr["route_key"] = f"{nr['origin']}|{nr['product']}|{dest}|{nr['source']}|{nr['occurrence']}"
            routes.append(nr)
        pf1 = keymap[bf.B_PF1]
        for j in range(8):
            s = shops[(k * 8 + j) % len(shops)]
            nr = copy.deepcopy(base_routes[8])
            nr.update({"origin": pf1, "destination": s["building"], "endpoint": s["building"], "destination_city_id": s["city_id"],
                       "destination_owner_actor_id": s["owner_actor_id"], "slot_index": 3 + j})
            nr["route_key"] = f"{pf1}|Paint|{s['building']}|own|0"
            routes.append(nr)
    seen = set()
    unique_routes = []
    for r in routes:
        if r["route_key"] in seen:
            continue
        seen.add(r["route_key"])
        unique_routes.append(r)

    ai = []
    tmpl_ai = bf.ai_buildings()
    for i in range(AI_BUILDINGS):
        t = copy.deepcopy(tmpl_ai[i % len(tmpl_ai)])
        x, y = 1000 + (i % 50) * 9, 1000 + (i // 50) * 9
        t["key"] = f"{t['prefab']}@{x},{y}"
        t["x"], t["y"] = x, y
        t["display_name"] = f"{t['display_name']} AI{i}"
        t["owner_actor_id"] = bf.AI_B if i % 2 else bf.AI_C
        ai.append(t)

    veh = bf.vehicles()
    rows = []
    for i in range(PLAYER_VEHICLES):
        v = copy.deepcopy(veh["vehicles_player"][i % 4])
        v["instance_id"] = -100000 - i
        v["trip_counter_id"] = 1000 + i
        r = unique_routes[i % len(unique_routes)]
        if v["product"]:
            v["job_origin"], v["job_destination"], v["product"], v["job_product"] = r["origin"], r["destination"], r["product"], r["product"]
        rows.append(v)
    veh["vehicles_player"] = rows

    requests = []
    for k in range(CLUSTERS):
        wh = shift_key(bf.B_WH, (k % 7) * 80, (k // 7) * 90)
        for q in bf.warehouse_requests():
            q = copy.deepcopy(q)
            q["endpoint"] = wh
            q["request_key"] = f"{wh}|{q['product']}|0"
            requests.append(q)

    data.update({"buildings_player": buildings, "routes_player": unique_routes, "buildings_ai": ai, "shops": shops,
                 "cities": cities, "regions": regions, "vehicles": veh, "requests_player": requests})
    data["companies"][0]["building_count"] = len(buildings)

    static_doc = bf.build_static(now)
    state_doc = bf.build_state(now, static_doc=static_doc, data=data)

    hist = bf.history_data()
    months = [f"Y{2 + i // 12}-{i % 12 + 1:02d}" for i in range(HISTORY_MONTHS)]
    ledger_months = []
    for i, m in enumerate(months):
        tmpl_m = copy.deepcopy(hist["ledger_player"]["months"][i % 5])
        tmpl_m["month"] = m
        tmpl_m["current_month_to_date"] = i == len(months) - 1
        ledger_months.append(tmpl_m)
    hist["ledger_player"].update({"months": ledger_months, "first_month_available": months[0], "last_month": months[-1],
                                  "current_month": months[-1]})
    win = {"window_months": 24, "window_first_month": months[-24], "window_last_month": months[-1]}
    hist["buildings_monthly_player"] = [{"building": b["key"], "series": [{"item": "production", "aggregation": "sum",
                                                                            **win, "history_truncated": True,
                                                                            "first_month_available": months[0],
                                                                            "values_retained": len(months),
                                                                            "months": [{"month": m, "value": 3.0} for m in months[-12:]]}]}
                                        for b in buildings]
    hist["production_monthly_player"] = [{"building": b["key"], "product": b["recipe"], **win, "history_truncated": False,
                                          "first_month_available": None,
                                          "months": [{"month": m, "produced": 3, "consumed": 1} for m in months[-12:]]}
                                         for b in buildings if b.get("recipe")]
    history_doc = bf.build_history(now, static_doc=static_doc, data=hist)
    hb = bf.build_heartbeat(now, static_doc=static_doc, state_doc=state_doc, history_doc=history_doc)
    return {"heartbeat": hb, "static": static_doc, "state": state_doc, "history": history_doc}


def main(argv=None) -> int:
    out = Path(argv[0]) if argv else DEFAULT_OUT
    world = build_large()
    bf.write_world(out, world)
    d = world["state"]["data"]
    sizes = {f: (out / f"{f}.json").stat().st_size for f in world}
    print(f"wrote {out}: player buildings={len(d['buildings_player'])} routes={len(d['routes_player'])} "
          f"vehicles={len(d['vehicles']['vehicles_player'])} ai buildings={len(d['buildings_ai'])} shops={len(d['shops'])} sizes={sizes}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
