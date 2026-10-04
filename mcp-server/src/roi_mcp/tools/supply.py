"""get_supply_chain (PRD 14.5)."""

from __future__ import annotations

from collections import Counter, deque

from .. import derive
from ..app import COMMON_SUFFIX, CallContext, ToolSpec
from ..errors import ToolError
from ..index import StateIndex, StaticIndex, make_id
from .common import (MAX_SEND_SEMANTICS, MIN_KEEP_SEMANTICS, products_of_building, provenance, resolve_building_key,
                     resolve_company, resolve_product, theoretical_rate)

MAX_NODES = 400


def _recipe_choice(ctx: CallContext) -> dict[str, str]:
    out = {}
    raw = ctx.args.get("recipe_choice") or {}
    res = ctx.resolver()
    for prod, rec in raw.items():
        p = res.resolve(prod, ("product",), "recipe_choice key").key
        r = res.resolve(rec, ("recipe",), f"recipe_choice[{prod}]").key
        out[p] = r
    return out


def _chooser(six: StaticIndex, choice: dict[str, str], used_by_company: dict[str, Counter]):
    def choose(product: str):
        if product in choice:
            return six.recipes.get(choice[product]), "recipe_choice"
        counts = used_by_company.get(product)
        if counts:
            name = counts.most_common(1)[0][0]
            if name in six.recipes:
                return six.recipes[name], "company_producers"
        names = six.recipes_by_result.get(product) or []
        if not names:
            return None, "none"
        return six.recipes[names[0]], ("first_recipe" if len(names) == 1 else "first_recipe_of_several")
    return choose


def _recipe_graph(six: StaticIndex, product: str, direction: str, depth: int) -> tuple[list, list, list, list]:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    truncated: set[str] = set()
    cycles: list = []

    def pnode(p: str) -> str:
        nid = make_id("product", p)
        if nid not in nodes:
            d = six.products.get(p) or {}
            nodes[nid] = {"id": nid, "node_kind": "product", "display_name": d.get("display_name"), "english_name": d.get("english_name")}
        return nid

    def rnode(r: dict) -> str:
        nid = make_id("recipe", r["name"])
        if nid not in nodes:
            nodes[nid] = {"id": nid, "node_kind": "recipe", "display_name": r.get("display_name"), "english_name": r.get("english_name"),
                          "game_days": r.get("game_days"), "building_types": [make_id("building_type", b) for b in r.get("building_types") or []]}
        return nid

    def walk(start: str, up: bool) -> None:
        q = deque([(start, 0, (start,))])
        seen = {start}
        while q:
            p, d, path = q.popleft()
            pnode(p)
            recipe_names = six.recipes_by_result.get(p, []) if up else six.recipes_by_ingredient.get(p, [])
            if recipe_names and d >= depth:
                truncated.add(make_id("product", p))
                continue
            for rn in recipe_names:
                r = six.recipes[rn]
                rid = rnode(r)
                if up:
                    edges.append({"from": rid, "to": make_id("product", p), "product": make_id("product", p), "kind": "recipe",
                                  "amount": next((x["amount"] for x in r["results"] if x["product"] == p), None)})
                    nxt = [(i["product"], i["amount"]) for i in r.get("ingredients") or []]
                    for ip, amt in nxt:
                        edges.append({"from": make_id("product", ip), "to": rid, "product": make_id("product", ip), "kind": "recipe", "amount": amt})
                else:
                    edges.append({"from": make_id("product", p), "to": rid, "product": make_id("product", p), "kind": "recipe",
                                  "amount": next((x["amount"] for x in r["ingredients"] if x["product"] == p), None)})
                    nxt = [(o["product"], o["amount"]) for o in r.get("results") or []]
                    for op, amt in nxt:
                        edges.append({"from": rid, "to": make_id("product", op), "product": make_id("product", op), "kind": "recipe", "amount": amt})
                for np_, _ in nxt:
                    pnode(np_)
                    if np_ in path:
                        cycles.append([make_id("product", x) for x in path + (np_,)])
                        continue
                    if np_ not in seen:
                        seen.add(np_)
                        q.append((np_, d + 1, path + (np_,)))

    if direction in ("upstream", "both"):
        walk(product, True)
    if direction in ("downstream", "both"):
        walk(product, False)
    uniq = []
    seen_e = set()
    for e in edges:
        k = (e["from"], e["to"], e["kind"])
        if k not in seen_e:
            seen_e.add(k)
            uniq.append(e)
    return list(nodes.values()), uniq, sorted(truncated), cycles


def _building_node(ix: StateIndex, key: str) -> dict:
    six = ix.static
    b = ix.building(key) or {}
    rate = theoretical_rate(six, b) if b.get("recipe") else None
    stock = {}
    for inv in b.get("inventory") or []:
        stock[make_id("product", inv.get("product"))] = inv.get("count")
    kind = "shop" if (b.get("kind") == "shop" or key in ix.shops) else ("warehouse" if b.get("kind") == "warehouse" else "building")
    return {"id": make_id("building", key), "node_kind": kind, "name": b.get("display_name"),
            "type": make_id("building_type", b.get("prefab")), "owner": ix.actor_ref(b.get("owner_actor_id")),
            "recipe": make_id("recipe", b.get("recipe")),
            "theoretical_per_30d": ({make_id("product", o["product"]): o["per_30d"] for o in rate.get("outputs") or []}
                                    if rate and rate.get("available") else None),
            "produced_last_month": (b.get("production") or {}).get("produced_last_month", b.get("produced_last_month")),
            "stock": stock or None}


def _actual_graph(ctx: CallContext, ix: StateIndex, starts: list[str], direction: str, depth: int, actor: int,
                  product_filter: str | None) -> tuple[list, list, list, list]:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    edge_keys: set = set()
    truncated: set[str] = set()
    cycles: list = []
    auto_wh_children: dict[str, list[str]] = {}
    for key, b in ix.buildings_full.items():
        lg = b.get("logistics") or {}
        if lg.get("auto_wh") and lg.get("warehouse"):
            auto_wh_children.setdefault(lg["warehouse"], []).append(key)
    vehicles = ix.vehicles.get("vehicles_player") or []

    def add_edge(e: dict) -> None:
        k = (e["from"], e["to"], e.get("product"), e["kind"], e.get("route_id"))
        if k in edge_keys:
            return
        edge_keys.add(k)
        edges.append(e)

    def route_edge(r: dict) -> dict:
        return {"from": make_id("building", r["origin"]), "to": make_id("building", r["destination"]),
                "product": make_id("product", r.get("product")), "kind": "configured_route",
                "route_id": make_id("route", r["route_key"]), "max_send": r.get("max_send"), "min_keep": r.get("min_keep"),
                "distance_tiles": r.get("distance_tiles"), "dispatch_cost": r.get("dispatch_cost"),
                "dormant": r.get("dormant_auto_warehouse"), "paused": r.get("paused"), "errors": r.get("errors")}

    def neighbours(key: str, up: bool) -> list[str]:
        out = []
        if up:
            for r in ix.routes_by_destination.get(key, []):
                add_edge(route_edge(r))
                out.append(r["origin"])
            for q in ix.requests_by_endpoint.get(key, []):
                add_edge({"from": None, "to": make_id("building", key), "product": make_id("product", q.get("product")),
                          "kind": "warehouse", "request_id": make_id("request", q["request_key"]),
                          "note": "pull request; providers are resolved dynamically by the game"})
            for child in auto_wh_children.get(key, []):
                add_edge({"from": make_id("building", child), "to": make_id("building", key), "product": None, "kind": "auto_wh"})
                out.append(child)
            for v in vehicles:
                if v.get("job_destination") == key and v.get("job_origin"):
                    add_edge({"from": make_id("building", v["job_origin"]), "to": make_id("building", key),
                              "product": make_id("product", v.get("job_product")), "kind": "observed_in_flight",
                              "units": v.get("job_amount")})
                    out.append(v["job_origin"])
        else:
            for r in ix.routes_by_origin.get(key, []):
                add_edge(route_edge(r))
                out.append(r["destination"])
            lg = (ix.building(key) or {}).get("logistics") or {}
            if lg.get("auto_wh") and lg.get("warehouse"):
                add_edge({"from": make_id("building", key), "to": make_id("building", lg["warehouse"]), "product": None, "kind": "auto_wh"})
                out.append(lg["warehouse"])
            for v in vehicles:
                if v.get("job_origin") == key and v.get("job_destination"):
                    add_edge({"from": make_id("building", key), "to": make_id("building", v["job_destination"]),
                              "product": make_id("product", v.get("job_product")), "kind": "observed_in_flight",
                              "units": v.get("job_amount")})
                    out.append(v["job_destination"])
        return out

    def walk(up: bool) -> None:
        q = deque((s, 0, (s,)) for s in starts)
        seen = set(starts)
        while q:
            key, d, path = q.popleft()
            if len(nodes) >= MAX_NODES:
                truncated.add(make_id("building", key))
                continue
            nodes.setdefault(make_id("building", key), _building_node(ix, key))
            if d >= depth:
                before = len(edges)
                if neighbours(key, up):
                    truncated.add(make_id("building", key))
                for e in edges[before:]:
                    edge_keys.discard((e["from"], e["to"], e.get("product"), e["kind"], e.get("route_id")))
                del edges[before:]
                continue
            for n in neighbours(key, up):
                nodes.setdefault(make_id("building", n), _building_node(ix, n))
                if n in path:
                    cycles.append([make_id("building", x) for x in path + (n,)])
                    continue
                if n not in seen:
                    seen.add(n)
                    q.append((n, d + 1, path + (n,)))

    if direction in ("upstream", "both"):
        walk(True)
    if direction in ("downstream", "both"):
        walk(False)
    for s in starts:
        nodes.setdefault(make_id("building", s), _building_node(ix, s))
    valid = set(nodes)
    edges = [e for e in edges if (e["from"] in valid or e["from"] is None) and e["to"] in valid]
    return list(nodes.values()), edges, sorted(truncated), cycles


def _requirement_ids(req: dict) -> dict:
    """D-REQ-1 output with <kind>:<key> ids (PRD 7.1) instead of bare asset names."""
    return {
        "target": {**req["target"], "product": make_id("product", req["target"]["product"])},
        "recipes_used": {make_id("product", p): {**v, "recipe": make_id("recipe", v.get("recipe"))}
                         for p, v in req["recipes_used"].items()},
        "cycles_detected": [[make_id("product", x) for x in c] for c in req["cycles_detected"]],
        "truncated_at_depth": [make_id("product", p) for p in req["truncated_at_depth"]],
    }


def get_supply_chain(ctx: CallContext) -> dict:
    a = ctx.args
    if bool(a.get("product")) == bool(a.get("building")):
        raise ToolError("invalid_argument", "pass exactly one of product or building")
    mode = a.get("mode") or "actual"
    direction = a.get("direction") or "upstream"
    depth = int(a.get("depth") or 6)
    target = a.get("target_output_per_30d")
    ctx.static(required=True, sections=("recipes",))
    six = ctx.static_index()
    if mode == "recipe":
        if a.get("building"):
            raise ToolError("invalid_argument", "mode 'recipe' works on a product (static recipe graph only)")
        product = ctx.resolver().resolve(a["product"], ("product",), "product").key
        choice = _recipe_choice(ctx)
        nodes, edges, truncated, cycles = _recipe_graph(six, product, direction, depth)
        req = derive.requirements(product, float(target) if target is not None else 1.0, _chooser(six, choice, {}), max_depth=depth)
        rid = _requirement_ids(req)
        requirements = {make_id("product", p): {"required_per_30d": v, "available_theoretical_per_30d": None, "gap": None}
                        for p, v in req["required_per_30d"].items()}
        ctx.add_unavailable("requirements.*.available_theoretical_per_30d", "recipe mode uses static data only")
        return {
            "mode": "recipe", "direction": direction, "root": make_id("product", product),
            "nodes": nodes, "edges": edges,
            "requirements": {"basis": "target_output_per_30d" if target is not None else "per_unit_of_output",
                             "target": rid["target"], "products": requirements, "recipes_used": rid["recipes_used"],
                             "method": "D-REQ-1"},
            "raw_inputs_total": {make_id("product", p): v for p, v in req["raw_inputs_total"].items()},
            "cycles_detected": cycles + rid["cycles_detected"],
            "truncated_at_depth": sorted(set(truncated) | set(rid["truncated_at_depth"])),
            "provenance": provenance(definition=["nodes", "edges"], derived=[("requirements", "D-REQ-1"), ("raw_inputs_total", "D-REQ-1")]),
        }
    ctx.snapshot("state", sections=("buildings_player",), optional_sections=("routes_player", "requests_player", "vehicles"))
    ix = ctx.state_index()
    actor = resolve_company(ctx, ix, a.get("company"))
    company_buildings = {k: b for k, b in ix.buildings_full.items() if b.get("owner_actor_id") == actor}
    if actor != ix.player_actor_id:
        for k, b in ix.buildings_compact.items():
            if b.get("owner_actor_id") == actor:
                company_buildings.setdefault(k, b)
    used_by_company: dict[str, Counter] = {}
    supply: dict[str, float] = {}
    approximate = False
    for b in company_buildings.values():
        if not b.get("recipe"):
            continue
        rate = theoretical_rate(six, b)
        if not rate.get("available"):
            continue
        approximate = approximate or bool(rate.get("approximate"))
        enabled = (b.get("flags") or {}).get("user_enabled", True)
        for o in rate.get("outputs") or []:
            used_by_company.setdefault(o["product"], Counter())[b["recipe"]] += 1
            if enabled:
                supply[o["product"]] = supply.get(o["product"], 0.0) + (o["per_30d"] or 0)
    if a.get("building"):
        start_key = resolve_building_key(ctx, a["building"])
        starts = [start_key]
        b = ix.building(start_key) or {}
        produces, _ = products_of_building(six, b)
        root_product = sorted(produces)[0] if produces else None
        root = make_id("building", start_key)
    else:
        root_product = resolve_product(ctx, a["product"])
        starts = sorted(k for k, b in company_buildings.items() if root_product in products_of_building(six, b)[0])
        root = make_id("product", root_product)
        if not starts:
            ctx.add_unavailable("nodes", f"the company has no building producing {root_product}")
    nodes, edges, truncated, cycles = _actual_graph(ctx, ix, starts, direction, depth, actor, root_product)
    choice = _recipe_choice(ctx)
    requirements = None
    raw_total = None
    req_cycles: list = []
    req_truncated: list = []
    if root_product is not None:
        # PRD-ambiguity: without target_output_per_30d the requirement is computed for the company's current
        # theoretical producer capacity of the root product ("current consumer capacity" in PRD 14.5 is read
        # as the capacity of the chain being inspected).
        if target is not None:
            basis, tgt = "target_output_per_30d", float(target)
        else:
            basis, tgt = "current_producer_capacity", supply.get(root_product, 0.0)
        req = derive.requirements(root_product, tgt, _chooser(six, choice, used_by_company), max_depth=depth)
        prods = {}
        for p, v in req["required_per_30d"].items():
            avail = round(supply.get(p, 0.0), 3)
            prods[make_id("product", p)] = {"required_per_30d": v, "available_theoretical_per_30d": avail,
                                            "gap": round(avail - (v or 0), 3)}
        rid = _requirement_ids(req)
        requirements = {"basis": basis, "target": rid["target"], "products": prods, "recipes_used": rid["recipes_used"],
                        "method": "D-REQ-1", "available_method": "D-RATE-1/D-RATE-2 over the company's enabled producers",
                        "approximate": approximate}
        raw_total = {make_id("product", p): v for p, v in req["raw_inputs_total"].items()}
        req_cycles = rid["cycles_detected"]
        req_truncated = rid["truncated_at_depth"]
    return {
        "mode": "actual", "direction": direction, "root": root, "company": make_id("company", actor),
        "nodes": nodes, "edges": edges,
        "requirements": requirements,
        "raw_inputs_total": raw_total,
        "cycles_detected": cycles + req_cycles,
        "truncated_at_depth": sorted(set(truncated) | set(req_truncated)),
        "semantics": {"max_send": MAX_SEND_SEMANTICS, "min_keep": MIN_KEEP_SEMANTICS},
        "provenance": provenance(observed=["nodes (buildings, stock, produced_last_month)", "edges (configured_route, warehouse, auto_wh, observed_in_flight)"],
                                 definition=["recipe amounts"], game_computed=["edges[].distance_tiles", "edges[].dispatch_cost"],
                                 derived=[("nodes[].theoretical_per_30d", "D-RATE-1/D-RATE-2"), ("requirements", "D-REQ-1"),
                                          ("raw_inputs_total", "D-REQ-1")],
                                 persistence={"edges": "SAVE", "nodes": "SAVE"}),
    }


def specs() -> list[ToolSpec]:
    return [ToolSpec(
        name="get_supply_chain",
        description=("Production graph for a product or around a building. mode=actual (default): the company's buildings with "
                     "configured routes (Max Send/Min Keep/distance/cost), warehouse pulls, AUTO_WH links and observed in-flight "
                     "jobs. mode=recipe: the static recipe graph only (no refresh; fresh is not applicable). Includes theoretical "
                     "upstream requirements for target_output_per_30d (D-REQ-1, e.g. 100 Paint/30 d needs 50 Chemicals and "
                     "100 Dye, 75 Gas) versus the company's theoretical capacity. " + MAX_SEND_SEMANTICS + COMMON_SUFFIX),
        scope="state", kind="runtime",
        params={"product": {"type": "string"}, "building": {"type": "string"},
                "direction": {"type": "string", "enum": ["upstream", "downstream", "both"], "default": "upstream"},
                "depth": {"type": "integer", "minimum": 1, "maximum": 12, "default": 6},
                "company": {"type": "string"}, "mode": {"type": "string", "enum": ["actual", "recipe"], "default": "actual"},
                "target_output_per_30d": {"type": "number", "exclusiveMinimum": 0, "maximum": 1e9},
                "recipe_choice": {"type": "object", "additionalProperties": {"type": "string"}, "maxProperties": 50}},
        handler=get_supply_chain)]
