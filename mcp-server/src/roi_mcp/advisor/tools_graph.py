"""get_chain_graph (phase-2 addendum 4.7, D-GRAPH-1): structured production/supply graph with explicit link basis.

Edge basis: `catalogue` (a recipe consumes/produces a product: definition), `observed` (a player building runs a
recipe, a configured route, an AUTO_WH link, a warehouse request), `hypothetical` (a catalogue recipe link for which
the player has no producer). Optional Mermaid text is generated deterministically; no image is rendered.

The graph bounds itself so it is never cut by the generic size cap (which could leave edges pointing at removed
nodes): every product and recipe of the chain is kept; player buildings are limited per recipe (most recent limit
listed in `building_limit_per_recipe`, omitted counts on each recipe node), lowered until the answer fits.
"""

from __future__ import annotations

import re

from ..app import CallContext
from ..index import make_id
from ..tools.common import resolve_product
from ..util import json_size
from .basis import pin_basis
from .common import advisor_provenance, require_player, section_or_degrade
from .contract import Advice, definition, estimate
from .facts import PlayerFacts

MAX_NODES = 150
MAX_EDGES = 300
BUILDING_LIMITS = (8, 4, 2, 1, 0)
RESULT_BUDGET = 20_000          # bytes for data.result before the envelope, names and summary/full handling
MAX_EXTRA_DESTINATIONS = 10


def _build(ctx: CallContext, adv: Advice, f: PlayerFacts, product: str, depth: int, per_recipe: int, routes_ok: bool) -> dict:
    ix, six = f.ix, f.six
    nodes: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}
    truncated = {"nodes": 0, "edges": 0}

    def node(nid: str, kind: str, **kw) -> bool:
        if nid in nodes:
            return True
        if len(nodes) >= MAX_NODES:
            truncated["nodes"] += 1
            return False
        nodes[nid] = {"id": nid, "kind": kind, **kw}
        return True

    def edge(src: str, dst: str, rel: str, basis_: str, **kw) -> None:
        if src not in nodes or dst not in nodes:
            return
        key = (src, dst, rel, kw.get("route_id"))
        if key in edges:
            return
        if len(edges) >= MAX_EDGES:
            truncated["edges"] += 1
            return
        edges[key] = {"from": src, "to": dst, "relation": rel, "basis": basis_, **{k: x for k, x in kw.items() if x not in (False, [], None)}}

    def pnode(p: str) -> str:
        d = six.products.get(p) or {}
        nid = make_id("product", p)
        node(nid, "product", display_name=d.get("display_name"), english_name=d.get("english_name"),
             player_produces=bool(f.producers.get(p)))
        return nid

    def rnode(rn: str) -> str | None:
        r = six.recipes.get(rn) or {}
        rid = make_id("recipe", rn)
        ok = node(rid, "recipe", display_name=r.get("display_name"), english_name=r.get("english_name"),
                  game_days=definition(r.get("game_days"), "days", "static.recipes[].game_days"), locked_by=f.locked_by(rn, None))
        return rid if ok else None

    def bnode(key: str) -> str | None:
        b = ix.building(key) or {}
        nid = make_id("building", key)
        if not node(nid, "building", name=b.get("display_name"), building_type=make_id("building_type", b.get("prefab")),
                    owner_is_player=b.get("owner_actor_id") == ix.player_actor_id):
            return None
        return nid

    frontier = [(product, 0)]
    seen = set()
    buildings_to_add: list[tuple[str, str, dict]] = []
    while frontier:
        p, d = frontier.pop(0)
        if p in seen:
            continue
        seen.add(p)
        pid = pnode(p)
        for rn in list(six.recipes_by_result.get(p) or []):
            r = six.recipes[rn]
            rid = rnode(rn)
            if rid is None:
                continue
            used = sorted((pr for pr in f.producers.get(p, []) if pr["building"].get("recipe") == rn), key=lambda pr: pr["building"]["key"])
            link_basis = "catalogue" if used else "hypothetical"
            amt = next((x.get("amount") for x in r.get("results") or [] if x.get("product") == p), None)
            edge(rid, pid, "produces", link_basis, amount_per_cycle=definition(amt, "units", "static.recipes[].results[].amount"))
            for ing in r.get("ingredients") or []:
                iid = pnode(ing["product"])
                edge(iid, rid, "consumed_by", link_basis,
                     amount_per_cycle=definition(ing.get("amount"), "units", "static.recipes[].ingredients[].amount"))
                if d + 1 < depth:
                    frontier.append((ing["product"], d + 1))
            nodes[rid]["player_buildings"] = len(used)
            nodes[rid]["player_buildings_omitted"] = max(len(used) - per_recipe, 0)
            for pr in used[:per_recipe]:
                buildings_to_add.append((pr["building"]["key"], rid, pr))
    # buildings after the catalogue part, so products and recipes are never displaced by buildings
    for key, rid, pr in buildings_to_add:
        bid = bnode(key)
        if bid:
            nodes[bid]["theoretical_per_30d"] = estimate(pr["per_30d"], "units/30d", "D-RATE-1/2",
                                                         adv.conf("medium", ["approximate_rate"] if pr["approximate"] else []))
            nodes[bid]["enabled"] = pr["enabled"]
            edge(bid, rid, "runs", "observed")
    consumers = sorted(f.consumers.get(product, []), key=lambda c: c["building"]["key"])
    for c in consumers[:per_recipe]:
        rn = c["building"].get("recipe")
        rid = rnode(rn)
        if rid:
            edge(make_id("product", product), rid, "consumed_by", "catalogue")
            bid = bnode(c["building"]["key"])
            if bid:
                edge(bid, rid, "runs", "observed")
    if routes_ok:
        extra = 0
        for r in sorted(f.routes, key=lambda x: x["route_key"]):
            if make_id("product", r.get("product")) not in nodes:
                continue
            o, dst = make_id("building", r["origin"]), make_id("building", r["destination"])
            if o in nodes and dst not in nodes and r.get("destination_kind") in ("shop", "warehouse") and extra < MAX_EXTRA_DESTINATIONS * (per_recipe > 0):
                if bnode(r["destination"]):
                    extra += 1
            edge(o, dst, "ships", "observed", product=make_id("product", r.get("product")), route_id=make_id("route", r["route_key"]),
                 dormant=bool(r.get("dormant_auto_warehouse")), paused=bool(r.get("paused")), errors=list(r.get("errors") or []))
        for b in f.buildings:
            lg = b.get("logistics") or {}
            if lg.get("auto_wh") and lg.get("warehouse"):
                edge(make_id("building", b["key"]), make_id("building", lg["warehouse"]), "auto_warehouse", "observed")
    for q in (ix.requests_by_endpoint or {}).values():
        for req in q:
            eid, pid = make_id("building", req["endpoint"]), make_id("product", req.get("product"))
            if eid in nodes and pid in nodes:
                edge(pid, eid, "pulled_by_request", "observed", request_id=make_id("request", req["request_key"]))
    node_list = sorted(nodes.values(), key=lambda n: (n["kind"], n["id"]))
    edge_list = sorted(edges.values(), key=lambda e: (e["from"], e["to"], e["relation"], e.get("route_id") or ""))
    return {"product": make_id("product", product), "depth": depth, "building_limit_per_recipe": per_recipe,
            "nodes": node_list, "edges": edge_list,
            "counts": {"nodes": len(node_list), "edges": len(edge_list),
                       "observed_edges": sum(1 for e in edge_list if e["basis"] == "observed"),
                       "catalogue_edges": sum(1 for e in edge_list if e["basis"] == "catalogue"),
                       "hypothetical_edges": sum(1 for e in edge_list if e["basis"] == "hypothetical"),
                       "player_buildings_omitted": sum(n.get("player_buildings_omitted", 0) for n in node_list)},
            "truncated": truncated,
            "basis_legend": {"observed": "seen in the game snapshot (a building runs a recipe, a route, an AUTO_WH link, a request)",
                             "catalogue": "recipe definition used by at least one of the player's producers",
                             "hypothetical": "recipe definition the player does not run (a possible, unbuilt path)"}}


def get_chain_graph(ctx: CallContext) -> dict:
    a = ctx.args
    basis = pin_basis(ctx, static="required", state_sections=("buildings_player",))
    ix = ctx.state_index()
    require_player(ix)
    ctx.static(required=True, sections=("recipes",))
    adv = Advice("get_chain_graph", basis)
    f = PlayerFacts(ix)
    product = resolve_product(ctx, a["product"])
    depth = int(a.get("depth") or 4)
    routes_ok = section_or_degrade(ctx, adv, "state", "routes_player", "observed route links omitted") is not None
    section_or_degrade(ctx, adv, "state", "requests_player", "warehouse request links omitted")
    mermaid = a.get("format") == "mermaid"
    result = None
    for limit in BUILDING_LIMITS:
        result = _build(ctx, adv, f, product, depth, limit, routes_ok)
        if mermaid:
            result["mermaid"] = to_mermaid(result["nodes"], result["edges"])
        if json_size(result) <= RESULT_BUDGET:
            break
    summary = a.get("detail") == "summary"
    if summary:
        # coherent skeleton: products and recipes with the links between them; buildings only counted
        keep = {n["id"] for n in result["nodes"] if n["kind"] != "building"}
        result["nodes"] = [n for n in result["nodes"] if n["id"] in keep]
        result["edges"] = [e for e in result["edges"] if e["from"] in keep and e["to"] in keep]
        result["summary_skeleton"] = True
        if mermaid:
            result["mermaid"] = to_mermaid(result["nodes"], result["edges"])
    if result["counts"]["player_buildings_omitted"]:
        ctx.add_unavailable("nodes[kind=building]", f"{result['counts']['player_buildings_omitted']} player building(s) omitted "
                            f"(at most {result['building_limit_per_recipe']} per recipe to stay under the size cap); "
                            "recipe nodes carry player_buildings and player_buildings_omitted")
    if result["truncated"]["nodes"] or result["truncated"]["edges"]:
        ctx.add_unavailable("graph", f"truncated at {MAX_NODES} nodes / {MAX_EDGES} edges; lower depth")
    adv.observed = {"player_buildings_in_graph": sum(1 for n in result["nodes"] if n["kind"] == "building")}
    adv.method("graph", "D-GRAPH-1")
    data = adv.finish(result, advisor_provenance(["buildings running recipes", "routes", "requests"], ["recipes"], adv.methods))
    if summary:
        data["_summary_keep"] = ["nodes", "edges"]
    return data


_SAFE = re.compile(r'["\[\]{}()<>|]')


def to_mermaid(nodes: list[dict], edges: list[dict]) -> str:
    idx = {n["id"]: f"n{i}" for i, n in enumerate(nodes)}
    lines = ["graph LR"]
    for n in nodes:
        label = n.get("english_name") or n.get("name") or n["id"]
        label = _SAFE.sub(" ", str(label))[:60]
        shape = {"product": ("([", "])"), "recipe": ("[", "]"), "building": ("[[", "]]")}[n["kind"]]
        lines.append(f'  {idx[n["id"]]}{shape[0]}"{label}"{shape[1]}')
    arrow = {"observed": "-->", "catalogue": "-.->", "hypothetical": "-. hypothetical .->"}
    for e in edges:
        lines.append(f'  {idx[e["from"]]} {arrow[e["basis"]]} {idx[e["to"]]}')
    return "\n".join(lines)
