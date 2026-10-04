"""Response modes and language for advisor responses (phase-2 addendum sections 3 and 5).

Applied after a handler built its standard `data`, so every advisor tool gets the same behaviour:
- summary: result lists cut to 5 entries (counts kept), evidence kept on the first 3 entries of each list,
  observed/calculation_inputs emptied, basis reduced, Quantity `source` strings dropped; confidence, assumptions,
  degraded inputs, contradictions (first 5) and unavailable are always kept.
- standard: unchanged.
- full: unchanged here (tools add their own extra detail when `detail == "full"`).
"""

from __future__ import annotations

from typing import Any

from .localize import catalog_is_french, localize

SUMMARY_LIST = 5
SUMMARY_EVIDENCE = 3
DETAILS = ("summary", "standard", "full")
LANGUAGES = ("en", "fr", "both")

PARAMS = {
    "detail": {"type": "string", "enum": list(DETAILS), "default": "standard",
               "description": "summary = smallest answer that keeps the result, top items, critical evidence, confidence "
                              "and warnings; standard = default; full = with extra detail where the tool has some."},
    "language": {"type": "string", "enum": list(LANGUAGES), "default": "en",
                 "description": "Names and advisor texts: en, fr (game names from the catalogue in the game's language; "
                                "never machine-translated) or both. Ids, units and numbers never change."},
}


def _strip_sources(o: Any) -> None:
    stack = [o]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            if "kind" in x and "unit" in x:
                x.pop("source", None)
            stack.extend(v for v in x.values() if isinstance(v, (dict, list)))
        elif isinstance(x, list):
            stack.extend(v for v in x if isinstance(v, (dict, list)))


def _summarize(o: Any, keep: frozenset = frozenset()) -> Any:
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if k in keep:
                out[k] = v
            elif isinstance(v, list) and len(v) > SUMMARY_LIST and all(isinstance(i, dict) for i in v):
                out[k] = _summarize(v[:SUMMARY_LIST])
                out[f"{k}_omitted"] = len(v) - SUMMARY_LIST
            else:
                out[k] = _summarize(v)
        return out
    if isinstance(o, list):
        items = [_summarize(i) for i in o]
        for i, item in enumerate(items):
            if i >= SUMMARY_EVIDENCE and isinstance(item, dict) and isinstance(item.get("evidence"), dict):
                item["evidence"] = {"omitted_in_summary": True}
        return items
    return o


def present(data: dict, detail: str, language: str, static_language: Any) -> dict:
    data["detail"] = detail
    data["language"] = language
    keep = frozenset(data.pop("_summary_keep", ()) or ())   # lists a tool already reduced coherently (e.g. a graph)
    if detail == "summary":
        data["result"] = _summarize(data.get("result"), keep)
        data["observed"] = {}
        data["calculation_inputs"] = {}
        b = data.get("basis")
        if isinstance(b, dict):
            data["basis"] = {"snapshots": [{k: s.get(k) for k in ("family", "seq", "game_date", "age_s", "stale")}
                                           for s in b.get("snapshots") or []]}
        if len(data.get("contradictions") or []) > SUMMARY_LIST:
            data["contradictions_total"] = data.get("contradictions_total") or len(data["contradictions"])
            data["contradictions"] = data["contradictions"][:SUMMARY_LIST]
        _strip_sources(data["result"])
    missing = localize(data.get("result"), language, catalog_is_french(static_language))
    if language != "en":
        data["text_translation"] = {"untranslated_texts": missing,
                                    "note": "texts marked <field>_language: en have no authored French version"}
    return data
