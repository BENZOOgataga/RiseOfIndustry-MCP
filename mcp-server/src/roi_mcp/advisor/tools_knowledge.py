"""explain_mechanic and how_to (PRD addendum 6.9, 6.10): curated knowledge, independent of the game state.

When the static catalogue is available (and the build is supported), explain_mechanic also lists catalogue entries
whose names match the topic, with the display name in the game's language and the English name (observed).
"""

from __future__ import annotations

from ..app import CallContext
from ..errors import ToolError
from ..index import make_id
from ..tools.common import provenance
from ..util import normalize_name
from .contract import CONTRACT_VERSION
from .knowledge import RESOURCES, get_knowledge

LEVEL_FACTORS = {"CONFIRMED_IN_GAME": [], "CONFIRMED_SOURCE": [], "HIGH_CONFIDENCE": ["mechanic_unverified"],
                 "INFERRED": ["mechanic_unverified"], "UNKNOWN": ["mechanic_unverified"]}


def _conf(entry: dict) -> dict:
    return {"level": entry["confidence"], "factors": list(LEVEL_FACTORS[entry["verification"]])}


def _build_match(ctx: CallContext) -> bool | None:
    lv = ctx.lv
    if lv.unsupported_build:
        return False
    if lv.compatibility == "verified":
        return True
    return None


def _wrap(ctx: CallContext, tool: str, result: dict, confs: list[dict], degraded: list[dict]) -> dict:
    from .contract import merge_confidence
    kb = get_knowledge()
    return {
        "advisor": {"contract_version": CONTRACT_VERSION, "tool": tool, "player_only": True, "read_only": True},
        "result": result,
        "observed": {},
        "assumptions": [],
        "calculation_inputs": {},
        "confidence": merge_confidence(confs) if confs else {"level": "low", "factors": ["partial_inputs"]},
        "degraded_inputs": degraded,
        "contradictions": [],
        "basis": None,
        "applies_to": {**kb.applies_to, "build_match": _build_match(ctx)},
        "provenance": provenance(definition=["result (curated knowledge base: mcp-server/src/roi_mcp/knowledge)"]),
    }


def _catalog_terms(ctx: CallContext, topic: str) -> tuple[list[dict], list[dict]]:
    if ctx.lv.unsupported_build:
        return [], [{"input": "static", "reason": "family_unavailable", "effect": "omitted (unsupported build)"}]
    snap = ctx.static(required=False)
    if snap is None:
        return [], [{"input": "static", "reason": "family_unavailable", "effect": "catalogue names omitted"}]
    six = ctx.static_index()
    q = normalize_name(topic)
    if len(q) < 3:
        return [], []
    out = []
    for kind, table in (("product", six.products), ("building_type", six.building_types), ("recipe", six.recipes),
                        ("tech", six.unlocks)):
        for name, d in table.items():
            names = [normalize_name(x) for x in (d.get("display_name"), d.get("english_name"), name) if x]
            if any(q == n or q in n for n in names):
                out.append({"id": make_id(kind, name), "kind": kind, "display_name": d.get("display_name"),
                            "english_name": d.get("english_name"), "source": "static catalogue (observed in this game)"})
            if len(out) >= 8:
                return out, []
    return out, []


def explain_mechanic(ctx: CallContext) -> dict:
    kb = get_knowledge()
    topic = ctx.args["topic"]
    hits, closest = kb.find_mechanics(topic)
    terms = kb.find_terms(topic)
    catalog, degraded = _catalog_terms(ctx, topic)
    if not hits and not terms and not catalog:
        raise ToolError("not_found", f"no curated mechanic matches {topic!r}",
                        hint="Try one of the candidate ids, or read the resource roi://knowledge/mechanics.",
                        candidates=[{"id": c, "kind": "mechanic"} for c in closest])
    matches = [{**{k: m[k] for k in ("id", "title", "title_fr", "summary", "formula", "evidence", "verification", "caveats", "related")},
                "confidence": _conf(m), "resource_uri": f"{RESOURCES['mechanics']}/{m['id']}"} for m in hits]
    pitfalls = kb.pitfalls_for([m["id"] for m in hits])
    result = {"topic": topic, "matches": matches,
              "glossary": [{k: t[k] for k in ("en", "fr", "internal", "mcp_field", "definition", "verification", "note")} for t in terms],
              "catalog_terms": catalog,
              "pitfalls": [{k: p[k] for k in ("id", "title", "correction", "verification")} for p in pitfalls]}
    return _wrap(ctx, "explain_mechanic", result, [x["confidence"] for x in matches] or [_conf(t) for t in terms], degraded)


def how_to(ctx: CallContext) -> dict:
    kb = get_knowledge()
    action = ctx.args["action"]
    hits, closest = kb.find_how_to(action)
    if not hits:
        raise ToolError("not_found", f"no curated how-to matches {action!r}",
                        hint="Try one of the candidate ids, or read the resource roi://knowledge/how-to. "
                             "The repository records no UI steps for other in-game actions.",
                        candidates=[{"id": c, "kind": "how_to"} for c in closest])
    matches = []
    for h in hits:
        row = {k: h[k] for k in ("id", "title", "title_fr", "audience", "steps", "evidence", "verification", "caveats", "related_tools")}
        row["confidence"] = _conf(h)
        if h["audience"] == "player_in_game":
            row["mcp_can_do_it"] = False
        matches.append(row)
    return _wrap(ctx, "how_to", {"action": action, "matches": matches, "resource_uri": RESOURCES["how_to"]},
                 [m["confidence"] for m in matches], [])
