"""The V1.1 live semantic-check script works on synthetic snapshots (it is never run against the live game here)."""

from __future__ import annotations

import importlib.util

from conftest import REPO


def _load():
    spec = importlib.util.spec_from_file_location("v11_semantic_check", REPO / "scripts" / "validation" / "v11_semantic_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_semantic_check_on_fixture(h):
    mod = _load()
    before = {p.name: p.stat().st_mtime_ns for p in h.exchange.iterdir()}
    report = mod.run(h.app)
    hard = dict(report["hard_checks"])
    # The synthetic fixture's gas wells do not share one upkeep formula (0/2/3 modules -> 3000/4500/5000), so the
    # measured company modifier is refused there and GasWell differs by design; every other pair must match.
    nb = report["measurements"]["new_building_comparisons"]
    assert nb["n"] >= 3 and [m["building_type"] for m in nb["mismatches"]] in ([], ["GasWell"]), nb
    hard.pop("new_building_economics_match_player_buildings")
    assert all(hard.values()), report["hard_checks"]
    m = report["measurements"]
    assert m["dispatch_replica_mismatches"] == []
    assert m["cycle_observed_over_static_model"]["n"] > 0 and m["cycle_observed_over_static_model"]["median"] == 1
    assert m["research_days_game_over_formula"]["median"] == 1
    assert m["catalogue_language"] == "French" and m["products_with_distinct_display_name"] > 0
    assert {p.name: p.stat().st_mtime_ns for p in h.exchange.iterdir()} == before          # read-only


def test_semantic_check_reports_lifecycle_error(h):
    h.procs.procs = []
    assert _load().run(h.app)["passed"] is False
