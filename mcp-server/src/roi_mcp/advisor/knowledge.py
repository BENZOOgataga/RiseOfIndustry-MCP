"""Curated knowledge base (PRD addendum section 7): loading, validation, lookup and MCP resources.

The JSON files are part of the server package and independent of the game state. They are validated against
knowledge.schema.json when first loaded; an invalid file is a server bug and raises at load.
"""

from __future__ import annotations

import difflib
import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

from ..util import normalize_name

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"
FILES = {"mechanics": "mechanics.json", "glossary": "glossary.json", "pitfalls": "pitfalls.json", "how_to": "how_to.json"}
URI_PREFIX = "roi://knowledge/"
RESOURCES = {"mechanics": "roi://knowledge/mechanics", "glossary": "roi://knowledge/glossary",
             "pitfalls": "roi://knowledge/pitfalls", "how_to": "roi://knowledge/how-to"}
MIME = "application/json"


class KnowledgeBase:
    def __init__(self, directory: Path = KNOWLEDGE_DIR):
        self.directory = Path(directory)
        schema = json.loads((self.directory / "knowledge.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        self.docs: dict[str, dict] = {}
        for key, name in FILES.items():
            doc = json.loads((self.directory / name).read_text(encoding="utf-8"))
            errs = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
            if errs:
                raise ValueError(f"{name}: invalid knowledge file: {errs[0].message[:200]}")
            self.docs[key] = doc
        self.mechanics = {m["id"]: m for m in self.docs["mechanics"]["mechanics"]}
        self.terms = list(self.docs["glossary"]["terms"])
        self.pitfalls = {p["id"]: p for p in self.docs["pitfalls"]["pitfalls"]}
        self.how_to = {h["id"]: h for h in self.docs["how_to"]["entries"]}
        self.applies_to = self.docs["mechanics"]["applies_to"]

    # ------------------------------------------------------------- lookup
    @staticmethod
    def _score(query: str, texts: list[str]) -> float:
        q = normalize_name(query.replace("-", " ").replace("_", " "))
        if not q:
            return 0.0
        qtok = set(q.split())
        best = 0.0
        for t in texts:
            n = normalize_name(t.replace("-", " ").replace("_", " "))
            if not n:
                continue
            if n == q:
                return 1.0
            if q in n or n in q:
                best = max(best, 0.85 if len(q) >= 3 else 0.5)
                continue
            ntok = set(n.split())
            overlap = len(qtok & ntok) / max(len(qtok | ntok), 1)
            ratio = difflib.SequenceMatcher(None, q, n).ratio()
            best = max(best, 0.8 * overlap + 0.2 * ratio if overlap else 0.6 * ratio)
        return round(best, 4)

    def _rank(self, query: str, entries: dict, fields) -> list[tuple[float, str]]:
        rows = []
        for eid, e in entries.items():
            texts = [eid] + [e.get(f) for f in fields if isinstance(e.get(f), str)]
            for f in fields:
                if isinstance(e.get(f), list):
                    texts.extend(x for x in e[f] if isinstance(x, str))
            rows.append((self._score(query, [t for t in texts if t]), eid))
        rows.sort(key=lambda r: (-r[0], r[1]))
        return rows

    def find_mechanics(self, topic: str, limit: int = 3, threshold: float = 0.5) -> tuple[list[dict], list[str]]:
        ranked = self._rank(topic, self.mechanics, ("title", "title_fr", "aliases"))
        hits = [self.mechanics[i] for s, i in ranked if s >= threshold][:limit]
        return hits, [i for _, i in ranked[:5]]

    def find_how_to(self, action: str, limit: int = 3, threshold: float = 0.5) -> tuple[list[dict], list[str]]:
        ranked = self._rank(action, self.how_to, ("title", "title_fr", "aliases"))
        hits = [self.how_to[i] for s, i in ranked if s >= threshold][:limit]
        return hits, [i for _, i in ranked[:5]]

    def find_terms(self, query: str, threshold: float = 0.6) -> list[dict]:
        out = []
        for t in self.terms:
            texts = [t["en"], t.get("fr") or "", t.get("internal") or "", t.get("mcp_field") or ""]
            if self._score(query, [x for x in texts if x]) >= threshold:
                out.append(t)
        return out[:5]

    def pitfalls_for(self, mechanic_ids: list[str]) -> list[dict]:
        ids = set(mechanic_ids)
        return [p for p in self.pitfalls.values() if ids & set(p.get("related") or [])][:5]

    # ------------------------------------------------------------- resources
    def resource_list(self) -> list[dict]:
        out = [
            {"uri": RESOURCES["mechanics"], "name": "mechanics", "title": "Rise of Industry mechanics (curated, with evidence)",
             "description": f"{len(self.mechanics)} verified or classified game mechanics with formulas, evidence and verification status."},
            {"uri": RESOURCES["glossary"], "name": "glossary", "title": "Glossary (French/English game terms)",
             "description": "Game terms with internal names, MCP fields and evidence; French terms only where recorded."},
            {"uri": RESOURCES["pitfalls"], "name": "pitfalls", "title": "Known pitfalls",
             "description": "Common misunderstandings (Max Send, demand units, code defaults, ...) and their corrections."},
            {"uri": RESOURCES["how_to"], "name": "how-to", "title": "How-to entries",
             "description": "How to do things in the game UI (the MCP cannot) or with this MCP."},
        ]
        for mid, m in self.mechanics.items():
            out.append({"uri": f"{RESOURCES['mechanics']}/{mid}", "name": f"mechanic-{mid}", "title": m["title"],
                        "description": m["summary"][:200]})
        return out

    def read_resource(self, uri: str) -> str | None:
        if uri == RESOURCES["mechanics"]:
            return json.dumps(self.docs["mechanics"], ensure_ascii=False)
        if uri == RESOURCES["glossary"]:
            return json.dumps(self.docs["glossary"], ensure_ascii=False)
        if uri == RESOURCES["pitfalls"]:
            return json.dumps(self.docs["pitfalls"], ensure_ascii=False)
        if uri == RESOURCES["how_to"]:
            return json.dumps(self.docs["how_to"], ensure_ascii=False)
        prefix = RESOURCES["mechanics"] + "/"
        if uri.startswith(prefix):
            m = self.mechanics.get(uri[len(prefix):])
            if m is not None:
                return json.dumps({"applies_to": self.applies_to, "mechanic": m}, ensure_ascii=False)
        return None


@lru_cache(maxsize=1)
def get_knowledge() -> KnowledgeBase:
    return KnowledgeBase()
