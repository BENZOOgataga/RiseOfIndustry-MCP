"""Indexes over the static catalogue and the state snapshot, id composition (PRD 7.1) and name
resolution (PRD 13.6).

Snapshot files use RAW keys; the server composes `<kind>:<key>` ids.
"""

from __future__ import annotations

import difflib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from .errors import ToolError
from .util import normalize_name, parse_building_key

ID_KINDS = ("building", "building_type", "product", "recipe", "tech", "tech_tree", "bill_category", "company", "city",
            "region", "route", "request", "vehicle", "contract")


def make_id(kind: str, key: Any) -> str | None:
    if key is None:
        return None
    return f"{kind}:{key}"


def split_id(value: str) -> tuple[str, str] | None:
    if not isinstance(value, str) or ":" not in value:
        return None
    kind, key = value.split(":", 1)
    if kind in ID_KINDS and key:
        return kind, key
    return None


@dataclass
class Entity:
    id: str
    kind: str          # building | shop | building_type | product | recipe | tech | tech_tree | bill_category | company | city | region
    key: str
    display_name: str | None = None
    english_name: str | None = None
    asset_name: str | None = None
    owner_actor_id: int | None = None
    city_id: int | None = None
    x: int | None = None
    y: int | None = None
    extra_names: tuple = ()

    def resolvable_names(self) -> list[str]:
        """Human-readable names matched case- and accent-insensitively by exact resolution (PRD 13.6).

        Ids are not names: they are opaque and matched exactly, case included (PRD 7.1)."""
        return [n for n in (self.display_name, self.english_name, self.asset_name) + tuple(self.extra_names) if n]

    def names(self) -> list[str]:
        """Every text `search` scores against (names, key and id)."""
        out = []
        for n in (self.display_name, self.english_name, self.asset_name, self.key, self.id) + tuple(self.extra_names):
            if n:
                out.append(n)
        return out


class StaticIndex:
    def __init__(self, data: dict | None):
        data = data or {}
        self.data = data
        self.available = bool(data)
        self.english_available = bool(data.get("english_names_available"))
        self.products = {p["name"]: p for p in data.get("products") or []}
        self.categories = {c["name"]: c for c in data.get("product_categories") or []}
        self.recipes = {r["name"]: r for r in data.get("recipes") or []}
        self.building_types = {b["name"]: b for b in data.get("building_types") or []}
        self.unlocks = {u["name"]: u for u in data.get("tech_unlocks") or []}
        self.trees = {t["name"]: t for t in data.get("tech_trees") or []}
        self.tech_categories = {t["name"]: t for t in data.get("tech_categories") or []}
        self.formulas = {f["name"]: f.get("text") for f in data.get("formulas") or []}
        self.bill_categories = {b["name"]: b for b in data.get("bill_categories") or []}
        self.overview = list(data.get("overview_categories") or [])
        self.bill_to_overview: dict[str, str] = {}
        for o in self.overview:
            for bc in o.get("bill_categories") or []:
                self.bill_to_overview.setdefault(bc, o["name"])
        self.tiers = {t["name"]: t for t in data.get("settlement_tiers") or []}
        self.permit_types = {p["name"]: p for p in data.get("permit_types") or []}
        self.loan_infos = list(data.get("loan_infos") or [])
        self.tech_config = data.get("tech_config") or {}

        self.recipes_by_result: dict[str, list[str]] = defaultdict(list)
        self.recipes_by_ingredient: dict[str, list[str]] = defaultdict(list)
        for r in self.recipes.values():
            for res in r.get("results") or []:
                self.recipes_by_result[res["product"]].append(r["name"])
            for ing in r.get("ingredients") or []:
                self.recipes_by_ingredient[ing["product"]].append(r["name"])
        self.recipe_unlocked_by: dict[str, list[str]] = defaultdict(list)
        self.building_unlocked_by: dict[str, list[str]] = defaultdict(list)
        for u in self.unlocks.values():
            for r in u.get("recipes") or []:
                self.recipe_unlocked_by[r].append(u["name"])
            if u.get("kind") != "building_price":
                for b in u.get("buildings") or []:
                    self.building_unlocked_by[b].append(u["name"])
        self.default_unlocked = {u["name"] for u in self.unlocks.values() if u.get("unlocked_by_default")}

        self.entities: list[Entity] = []
        for p in self.products.values():
            self.entities.append(Entity(make_id("product", p["name"]), "product", p["name"], p.get("display_name"),
                                        p.get("english_name"), p["name"]))
        for r in self.recipes.values():
            self.entities.append(Entity(make_id("recipe", r["name"]), "recipe", r["name"], r.get("display_name"),
                                        r.get("english_name"), r["name"]))
        for b in self.building_types.values():
            self.entities.append(Entity(make_id("building_type", b["name"]), "building_type", b["name"], b.get("display_name"),
                                        b.get("english_name"), b["name"]))
        for u in self.unlocks.values():
            self.entities.append(Entity(make_id("tech", u["name"]), "tech", u["name"], u.get("display_name"),
                                        u.get("english_name"), u["name"]))
        for t in self.trees.values():
            self.entities.append(Entity(make_id("tech_tree", t["name"]), "tech_tree", t["name"], t.get("display_name"),
                                        t.get("english_name"), t["name"]))
        for b in self.bill_categories.values():
            self.entities.append(Entity(make_id("bill_category", b["name"]), "bill_category", b["name"], b.get("display_name"),
                                        b.get("english_name"), b["name"]))

    # ---- helpers
    def product_ref(self, name: str | None) -> dict | None:
        if name is None:
            return None
        p = self.products.get(name) or {}
        return {"id": make_id("product", name), "display_name": p.get("display_name"), "english_name": p.get("english_name")}

    def recipe_unlocked(self, recipe: str, unlocked: set[str] | None) -> bool | None:
        if unlocked is None:
            return None
        by = self.recipe_unlocked_by.get(recipe) or []
        if not by:
            return True
        return any(u in unlocked or u in self.default_unlocked for u in by)

    def building_unlocked(self, btype: str, unlocked: set[str] | None) -> bool | None:
        if unlocked is None:
            return None
        by = self.building_unlocked_by.get(btype) or []
        if not by:
            return True
        return any(u in unlocked or u in self.default_unlocked for u in by)


class StateIndex:
    def __init__(self, data: dict | None, static: StaticIndex, world_session: str | None):
        data = data or {}
        self.data = data
        self.static = static
        self.world_session = world_session
        self.session = data.get("session") or {}
        self.player_actor_id = self.session.get("player_actor_id")
        self.companies = {c["actor_id"]: c for c in data.get("companies") or []}
        if self.player_actor_id is None:
            for c in self.companies.values():
                if c.get("is_player"):
                    self.player_actor_id = c["actor_id"]
        self.cities = {c["city_id"]: c for c in data.get("cities") or []}
        self.regions = {r["region_id"]: r for r in data.get("regions") or []}
        self.buildings_full: dict[str, dict] = {}
        for b in data.get("buildings_player") or []:
            self.buildings_full[b["key"]] = b
        for b in data.get("buildings_ai_detail") or []:
            self.buildings_full.setdefault(b["key"], b)
        self.buildings_compact = {b["key"]: b for b in data.get("buildings_ai") or []}
        self.shops = {s["building"]: s for s in data.get("shops") or []}
        self.routes: dict[str, dict] = {}
        for r in (data.get("routes_player") or []) + (data.get("routes_ai") or []):
            self.routes[r["route_key"]] = r
        self.routes_by_origin: dict[str, list[dict]] = defaultdict(list)
        self.routes_by_destination: dict[str, list[dict]] = defaultdict(list)
        for r in self.routes.values():
            self.routes_by_origin[r["origin"]].append(r)
            self.routes_by_destination[r["destination"]].append(r)
            if r.get("endpoint") and r["endpoint"] != r["destination"]:
                self.routes_by_destination[r["endpoint"]].append(r)
        self.requests = {q["request_key"]: q for q in data.get("requests_player") or []}
        self.requests_by_endpoint: dict[str, list[dict]] = defaultdict(list)
        for q in self.requests.values():
            self.requests_by_endpoint[q["endpoint"]].append(q)
        self.vehicles = data.get("vehicles") or {}
        self.vehicle_rows = {v["instance_id"]: v for v in self.vehicles.get("vehicles_player") or []}
        self.shops_by_product: dict[str, list[dict]] = defaultdict(list)
        for s in self.shops.values():
            for p in s.get("products") or []:
                self.shops_by_product[p["product"]].append(s)
        self.state_actor_ids: set[int] = set()
        for r in self.routes.values():
            if r.get("destination_kind") == "state_trading":
                self.state_actor_ids.add(r.get("destination_owner_actor_id"))
        for b in self.buildings_compact.values():
            if "state_trading" in (b.get("tags") or []) or b.get("prefab") == "TradingPost":
                pass
        research = data.get("research") or {}
        self.player_research = research.get("player") or None
        self.player_unlocked: set[str] | None = set(self.player_research.get("unlocked") or []) if self.player_research else None
        self.ai_research = {a["actor_id"]: a for a in research.get("ai") or []}
        self.market = data.get("market") or None
        self.market_prices = {p["product"]: p for p in (self.market or {}).get("prices") or []}

        self.entities: list[Entity] = []
        for key, b in self.buildings_full.items():
            self.entities.append(self._building_entity(key, b))
        for key, b in self.buildings_compact.items():
            if key not in self.buildings_full:
                self.entities.append(self._building_entity(key, b))
        for key, s in self.shops.items():
            parts = parse_building_key(key) or {}
            self.entities.append(Entity(make_id("building", key), "shop", key, s.get("display_name"), None, s.get("prefab"),
                                        s.get("owner_actor_id"), s.get("city_id"), parts.get("x"), parts.get("y")))
        for c in self.companies.values():
            self.entities.append(Entity(make_id("company", c["actor_id"]), "company", str(c["actor_id"]), c.get("name"),
                                        owner_actor_id=c["actor_id"]))
        for c in self.cities.values():
            self.entities.append(Entity(make_id("city", c["city_id"]), "city", str(c["city_id"]), c.get("name"),
                                        city_id=c["city_id"], x=c.get("center_x"), y=c.get("center_y")))
        for r in self.regions.values():
            owner = (r.get("permit") or {}).get("owner_actor_id")
            self.entities.append(Entity(make_id("region", r["region_id"]), "region", r["region_id"], r.get("name"),
                                        owner_actor_id=owner, city_id=r.get("city_id"), x=r.get("center_x"), y=r.get("center_y")))

    def _building_entity(self, key: str, b: dict) -> Entity:
        bt = self.static.building_types.get(b.get("prefab")) or {}
        return Entity(make_id("building", key), "building", key, b.get("display_name"), None, None,
                      b.get("owner_actor_id"), b.get("city_id"), b.get("x"), b.get("y"),
                      extra_names=())

    # ---- lookups
    def building(self, key: str) -> dict | None:
        """Full detail, else compact, else a minimal row synthesized from the shops section."""
        b = self.buildings_full.get(key) or self.buildings_compact.get(key)
        if b is not None:
            return b
        s = self.shops.get(key)
        if s is not None:
            parts = parse_building_key(key) or {}
            return {"key": key, "prefab": s.get("prefab"), "display_name": s.get("display_name"),
                    "owner_actor_id": s.get("owner_actor_id"), "x": parts.get("x"), "y": parts.get("y"),
                    "city_id": s.get("city_id"), "region_id": (self.cities.get(s.get("city_id")) or {}).get("region_id"),
                    "kind": "shop", "tags": ["shop"], "_from_shops": True}
        return None

    def is_full(self, key: str) -> bool:
        return key in self.buildings_full

    def coords(self, key: str | None) -> dict | None:
        if not key:
            return None
        b = self.buildings_full.get(key) or self.buildings_compact.get(key)
        if b is not None and b.get("x") is not None:
            return {"x": b["x"], "y": b["y"]}
        parts = parse_building_key(key)
        if parts:
            return {"x": parts["x"], "y": parts["y"]}
        return None

    def actor_ref(self, actor_id: int | None) -> dict | None:
        if actor_id is None:
            return None
        c = self.companies.get(actor_id)
        if c is not None:
            return {"id": make_id("company", actor_id), "actor_id": actor_id, "kind": "company", "name": c.get("name"),
                    "is_player": bool(c.get("is_player"))}
        city = self.cities.get(actor_id)
        if city is not None:
            return {"id": make_id("city", actor_id), "actor_id": actor_id, "kind": "city", "name": city.get("name")}
        if actor_id in self.state_actor_ids:
            return {"actor_id": actor_id, "kind": "state"}
        return {"actor_id": actor_id, "kind": "other"}

    def city_ref(self, city_id: int | None) -> dict | None:
        if city_id is None:
            return None
        c = self.cities.get(city_id) or {}
        return {"id": make_id("city", city_id), "name": c.get("name")}

    def region_ref(self, region_id: str | None) -> dict | None:
        if region_id is None:
            return None
        r = self.regions.get(region_id) or {}
        return {"id": make_id("region", region_id), "name": r.get("name")}

    def building_ref(self, key: str | None, full: bool = False) -> dict | None:
        if key is None:
            return None
        b = self.building(key) or {}
        ref = {"id": make_id("building", key), "name": b.get("display_name"), "coordinates": self.coords(key)}
        if full:
            ref["type"] = make_id("building_type", b.get("prefab") or (parse_building_key(key) or {}).get("prefab"))
            ref["owner"] = self.actor_ref(b.get("owner_actor_id"))
            ref["city"] = self.city_ref(b.get("city_id"))
        return ref

    def is_player(self, actor_id: int | None) -> bool:
        return actor_id is not None and actor_id == self.player_actor_id

    def ai_actor_ids(self) -> set[int]:
        return {a for a, c in self.companies.items() if c.get("kind") == "ai" or not c.get("is_player")}


# ---------------------------------------------------------------------- name resolution

KIND_ALIASES = {"shop": ("shop", "building"), "building": ("building", "shop")}


class Resolver:
    def __init__(self, static: StaticIndex | None, state: StateIndex | None):
        self.static = static
        self.state = state
        self.by_id: dict[str, list[Entity]] = defaultdict(list)
        self.by_name: dict[str, list[Entity]] = defaultdict(list)
        self.by_key: dict[str, list[Entity]] = defaultdict(list)
        self.all: list[Entity] = []
        for src in (static, state):
            if src is None:
                continue
            for e in src.entities:
                self.all.append(e)
                self.by_id[e.id].append(e)
                self.by_key[e.key].append(e)
                for n in set(normalize_name(x) for x in e.resolvable_names()):
                    if n:
                        self.by_name[n].append(e)

    @staticmethod
    def _kind_ok(e: Entity, kinds: Iterable[str]) -> bool:
        ks = set()
        for k in kinds:
            ks.update(KIND_ALIASES.get(k, (k,)))
        return e.kind in ks

    def resolve(self, query: Any, kinds: Iterable[str], label: str, world_session: str | None = None) -> Entity:
        kinds = tuple(kinds)
        if not isinstance(query, str) or not query.strip():
            raise ToolError("invalid_argument", f"{label} must be a non-empty string")
        q = query.strip()
        parsed = split_id(q)
        if parsed is not None:
            kind, key = parsed
            if kind == "vehicle":
                ws = self.state.world_session if self.state is not None else None
                if key.split(":", 1)[0] != ws:
                    raise ToolError("stale_reference", f"{label}: vehicle id {q!r} belongs to another world session",
                                    hint="Vehicle ids are session-scoped pooled objects; list vehicles again.")
                raise ToolError("invalid_argument", f"{label}: vehicle ids are not accepted here; use list_vehicles")
            hits = [e for e in self.by_id.get(q, []) if self._kind_ok(e, kinds)]
            if hits:
                return _dedupe(hits)[0]
            raise ToolError("not_found", f"{label} {q!r} does not exist in the current snapshot/catalogue",
                            hint=f"Expected an id of kind {', '.join(kinds)} or a name (ids are case-sensitive); use `search` to find ids.")
        prefix = q.split(":", 1)[0] if ":" in q else None
        if prefix is not None and prefix not in ID_KINDS and prefix.lower() in ID_KINDS:
            raise ToolError("not_found", f"{label} {q!r} does not exist in the current snapshot/catalogue",
                            hint=f"Ids are case-sensitive; the kind prefix is {prefix.lower()!r}.")
        # A bare id key (e.g. "PaintFactory@55,40") is part of an id: matched exactly, case included.
        hits = _dedupe([e for e in self.by_key.get(q, []) if self._kind_ok(e, kinds)])
        if len(hits) == 1:
            return hits[0]
        norm = normalize_name(q)
        hits = _dedupe([e for e in self.by_name.get(norm, []) if self._kind_ok(e, kinds)])
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise ToolError("ambiguous", f"{label} {q!r} matches {len(hits)} entities",
                            hint="Pass one of the candidate ids.", candidates=[self.candidate(e) for e in hits[:10]])
        raise ToolError("not_found", f"{label} {q!r} was not found (exact match on id, display name, English name or asset name; "
                        "ids are case-sensitive)",
                        hint="Use `search` for fuzzy lookup.")

    def candidate(self, e: Entity) -> dict:
        out = {"id": e.id, "kind": e.kind, "display_name": e.display_name}
        if e.kind in ("building", "shop") and self.state is not None:
            b = self.state.building(e.key) or {}
            out["type"] = make_id("building_type", b.get("prefab"))
            out["owner"] = self.state.actor_ref(e.owner_actor_id)
            out["city"] = self.state.city_ref(e.city_id)
            out["coordinates"] = {"x": e.x, "y": e.y} if e.x is not None else None
        elif e.english_name:
            out["english_name"] = e.english_name
        return out

    def search(self, query: str, kinds: Iterable[str] | None, owner_ids: set[int] | None) -> list[dict]:
        norm = normalize_name(query)
        if not norm:
            return []
        kind_set = set(kinds) if kinds else None
        results: dict[str, dict] = {}
        for e in self.all:
            if kind_set is not None and e.kind not in kind_set:
                continue
            if owner_ids is not None and e.owner_actor_id not in owner_ids:
                continue
            best = None
            for name in e.names():
                n = normalize_name(name)
                if not n:
                    continue
                if n == norm:
                    cand = ("exact", 1.0)
                elif n.startswith(norm):
                    cand = ("prefix", round(0.9 - min(len(n) - len(norm), 40) * 0.002, 4))
                elif norm in n:
                    cand = ("fuzzy", round(0.75 - min(len(n) - len(norm), 40) * 0.002, 4))
                else:
                    sm = difflib.SequenceMatcher(None, norm, n)
                    # real_quick_ratio/quick_ratio are cheap upper bounds of ratio()
                    if sm.real_quick_ratio() < 0.6 or sm.quick_ratio() < 0.6:
                        continue
                    ratio = sm.ratio()
                    if ratio < 0.6:
                        continue
                    cand = ("fuzzy", round(ratio * 0.7, 4))
                if best is None or cand[1] > best[1]:
                    best = cand
            if best is None:
                continue
            prev = results.get(e.id)
            if prev is None or best[1] > prev["score"]:
                results[e.id] = {"entity": e, "match_kind": best[0], "score": best[1]}
        rows = sorted(results.values(), key=lambda r: (-r["score"], r["entity"].kind, r["entity"].id))
        return rows


def _dedupe(entities: list[Entity]) -> list[Entity]:
    seen: dict[str, Entity] = {}
    for e in entities:
        if e.id not in seen:
            seen[e.id] = e
        elif seen[e.id].kind == "shop" and e.kind == "building":
            seen[e.id] = e
    return list(seen.values())
