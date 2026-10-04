"""Shared helpers for the advisor tests: valid calls, response checks and the Quantity contract walker."""

from __future__ import annotations

import json

from jsonschema import Draft202012Validator

import build_fixtures as bf
from conftest import TOOL_SCHEMA_DIR
from roi_mcp import config as cfg
from roi_mcp.advisor.contract import KINDS, UNITS
from roi_mcp.tools import ADVISOR_TOOL_NAMES
from roi_mcp.util import json_size

ROUTE_GW1 = f"route:{bf.B_GW1}|Gas|{bf.B_PC1}|own|0"
ROUTE_HW1 = f"route:{bf.B_PF1}|Paint|{bf.S_HW1}|own|0"

VALID_CALLS = {
    "get_overview": {},
    "diagnose_chain": {"product": "Paint"},
    "get_profitability": {},
    "find_opportunities": {"include_unviable": True},
    "review_routes": {},
    "plan_chain": {"product": "Paint", "target_per_month": 100},
    "what_if": {"change": {"type": "set_max_send", "route": ROUTE_GW1, "value": 20}},
    "what_changed": {},
    "explain_mechanic": {"topic": "max send"},
    "how_to": {"action": "change max send"},
    # phase 2
    "research_path": {"target": "Polymers"},
    "suggest_research": {"include_reachable": True},
    "loan_calculator": {"loan": "BankLoan", "existing": True},
    "route_calculator": {"origin": f"building:{bf.B_PF1}", "destination": f"building:{bf.S_HW2}", "product": "Paint"},
    "compare_options": {"kind": "recipes", "options": ["Paints", "PaintsAdvanced"]},
    "spatial_analysis": {"mode": "matrix", "locations": [f"building:{bf.B_PF1}", "city:10", "region:" + bf.R_GRE, f"building:{bf.S_GS}"]},
    "get_chain_graph": {"product": "Paint", "format": "mermaid"},
    "forecast": {"kind": "cash"},
}

WHAT_IF_CALLS = [
    {"type": "add_buildings", "recipe": "Paints", "count": 2},
    {"type": "add_buildings", "recipe": "Chemicals", "building_type": "PetrochemicalFactory"},
    {"type": "remove_building", "building": f"building:{bf.B_PC2}"},
    {"type": "set_efficiency", "building": f"building:{bf.B_PC1}", "index": 5},
    {"type": "change_recipe", "building": f"building:{bf.B_PF1}", "recipe": "PaintsAdvanced"},
    {"type": "set_max_send", "route": ROUTE_GW1, "value": 0},
    {"type": "set_min_keep", "route": ROUTE_GW1, "value": 5},
    {"type": "set_min_keep", "route": ROUTE_GW1, "keep_all": True},
    {"type": "take_loan", "loan": "BankLoan"},
]

VALIDATORS = {n: Draft202012Validator(json.loads((TOOL_SCHEMA_DIR / f"{n}.schema.json").read_text(encoding="utf-8")))
              for n in ADVISOR_TOOL_NAMES}

# Keys whose integer values are counts, ranks, sizes or identifiers (not measurements) — addendum 3.2.
PLAIN_KEYS = {"rank", "count", "depth", "buildings", "buildings_needed", "modules_per_building", "seq", "seqs", "entries",
              "routes", "routes_reviewed", "findings", "findings_total", "steps_total", "products_total", "changes_total",
              "queue_length", "unlocked_count", "products", "shops_with_demand", "index", "loans", "max_loans",
              "routes_sharing_cap", "routes_with_findings", "with_errors", "paused", "dormant_auto_wh", "keep_all",
              "contradictions_total", "occurrence", "from_index", "to_index", "max_attention", "limit",
              # phase 2: ordinals, identifiers and counts
              "tier", "remaining_count", "chain_length", "calibration_samples", "first_payment_month_offset",
              "last_payment_month_offset", "payment_number", "month_offset", "schedule_rows_omitted", "rows_omitted", "samples",
              "iterations", "compared_options", "nodes", "edges", "observed_edges", "catalogue_edges", "hypothetical_edges",
              "producers_omitted", "player_buildings_in_graph", "from_game_day", "to_game_day", "untranslated_texts",
              "player_buildings", "player_buildings_omitted", "building_limit_per_recipe", "producers_total"}
PLAIN_PARENTS = {"by_status", "issue_counts", "findings_by_kind", "findings_by_severity"}


def numeric_leaks(obj, path="", parent_key=None, in_evidence=False, in_quantity=False):
    """Paths of numbers that are neither a Quantity value, a plain count/rank/id, nor a snapshot value copied into an
    `evidence` object (addendum 3.2)."""
    out = []
    if isinstance(obj, dict):
        is_q = "kind" in obj and "unit" in obj and obj.get("kind") in KINDS
        for k, v in obj.items():
            # `evidence` holds snapshot values; `change` echoes the caller's what_if argument (addendum 3.2)
            out += numeric_leaks(v, f"{path}.{k}", k, in_evidence or k in ("evidence", "change"), is_q and k == "value")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += numeric_leaks(v, f"{path}[{i}]", parent_key, in_evidence, False)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if not (in_quantity or in_evidence or parent_key in PLAIN_KEYS or parent_key in PLAIN_PARENTS
                or (isinstance(parent_key, str) and parent_key.endswith("_omitted"))
                or path.split(".")[-2:-1] and path.split(".")[-2] in PLAIN_PARENTS):
            out.append(path)
    return out


def quantities(obj):
    """Every Quantity object in a response part."""
    if isinstance(obj, dict):
        if "kind" in obj and "unit" in obj and obj.get("kind") in KINDS:
            yield obj
        for v in obj.values():
            yield from quantities(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from quantities(v)


def check_advisor_response(name: str, resp: dict) -> None:
    errs = list(VALIDATORS[name].iter_errors(resp))
    if errs:
        from jsonschema.exceptions import best_match
        e = best_match(errs)
        raise AssertionError(f"{name}: {'/'.join(map(str, e.absolute_path))}: {e.message[:400]}")
    assert json_size(resp) <= cfg.RESPONSE_SIZE_CAP_BYTES, f"{name}: {json_size(resp)} bytes"
    if not resp["ok"]:
        return
    d = resp["data"]
    assert d["advisor"]["tool"] == name and d["advisor"]["read_only"] is True
    for part in ("result", "observed", "calculation_inputs"):
        leaks = numeric_leaks(d[part], part)
        assert not leaks, f"{name}: numbers outside Quantity objects: {leaks[:10]}"
    for q in quantities({k: d[k] for k in ("result", "observed", "calculation_inputs")}):
        assert q["unit"] in UNITS
        if q["kind"] == "estimate":
            assert q.get("method") and q.get("confidence"), (name, q)
        if q["kind"] == "derived":
            assert q.get("method"), (name, q)
        if q["kind"] in ("observed", "definition", "game_computed") and d.get("detail", "standard") != "summary":
            assert q.get("source"), (name, q)
    ids = [a["id"] for a in d["assumptions"]]
    assert len(ids) == len(set(ids))
