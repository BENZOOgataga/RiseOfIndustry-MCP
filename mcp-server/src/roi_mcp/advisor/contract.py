"""Advisor contract (PRD addendum section 3): Quantity values, confidence records, and the Advice builder that
assembles observed facts, assumptions, calculation inputs, degraded inputs, contradictions and confidence into the
`data` of an advisor response."""

from __future__ import annotations

import math
from typing import Any, Iterable

CONTRACT_VERSION = "1.1.0"

KINDS = ("observed", "definition", "game_computed", "derived", "estimate", "parameter")
UNITS = ("money", "money/30d", "money/day", "money/unit", "units", "units/30d", "units/day", "days", "months", "tiles", "ratio",
         "count", "percent", "units/dispatch")
LEVELS = ("low", "medium", "high")
FACTORS = ("stale_data", "inconsistent_snapshot", "static_mismatch", "approximate_rate", "price_fallback",
           "mechanic_unverified", "contradictory_inputs", "partial_inputs")
DEGRADED_REASONS = ("section_unavailable", "family_unavailable", "stale", "history_world_session_mismatch",
                    "static_mismatch", "inconsistent_snapshot", "missing_value", "approximated", "contradictory")

# Addendum 4.3. Ids are stable; texts are returned with every response that relies on them.
ASSUMPTIONS = {
    "A-CONTINUOUS": "Producers run continuously at their theoretical rate (D-RATE-1/2); stoppages are not forecast.",
    "A-UPKEEP-ACTIVE": "Upkeep uses the observed active monthly upkeep (the amount the game accrues while the building works).",
    "A-INPUT-MARKET": "Inputs are valued at the current market price for the player (opportunity cost), not at the player's own production cost.",
    "A-INPUT-OWN": "Inputs the player produces are valued at the advisor's own unit-cost estimate; other inputs at the market price.",
    "A-NO-TRANSPORT-IN-UNITCOST": "Inbound transport cost is not part of the unit cost; outbound distribution is reported separately.",
    "A-FULL-VEHICLES": "Distribution cost per unit assumes full vehicles (dispatch cost / vehicle capacity).",
    "A-PRICE-SHOP": "Sales happen at current shop prices for the player (median over shops with demand for the player).",
    "A-NEW-DEFAULT-EFFICIENCY": "New buildings run at the building type's initial efficiency index.",
    "A-ACTOR-MODIFIER-1": "Company-specific (actor) production and upkeep modifiers are not exported. For a building type the player owns they are measured on the player's buildings of that type (D-ADV-TYPEMOD-1, e.g. oil buildings x0.75 output and x1.2 upkeep in the validation world, apparently recent); a measured modifier is the current one and may be temporary (e.g. an event); for other types they are assumed 1 and the estimate is flagged mechanic_unverified.",
    "A-MODULE-UPKEEP-PCT": "A module's upkeep percentage is not exported; the owner building's upkeep percentage is used (matched the observed upkeep of every module-owner type in the live validation world).",
    "A-FULL-DEPOSITS": "New gatherers get their maximum module count on undepleted resources at module speed 1.",
    "A-NO-REGIONAL-COST": "Build costs exclude regional cost modifiers applied at placement.",
    "A-SPARE-AVAILABLE": "Spare existing supply (theoretical supply minus internal need) can be redirected to the plan.",
    "A-NO-LOGISTICS": "Routes, vehicles and transport costs of a plan are not planned.",
    "A-DEMAND-PERSISTS": "Current shop demand persists; price and demand reactions to new supply are not modelled.",
    "A-DISPATCH-NOT-THROUGHPUT": "A dispatch amount is what the next dispatch would request now, not monthly throughput.",
    "A-EFFICIENCY-SCALES-RATE": "Production rate scales with the efficiency output multiplier; upkeep with the upkeep multiplier.",
    "A-STATIC-RECIPE-CYCLE": "Cycle time from the static recipe (game_days / production_speed / efficiency multiplier) when no building runs the recipe.",
    # phase 2
    "A-TREND-PERSISTS": "The recent observed trend (sample listed) continues unchanged.",
    "A-RESEARCH-FORMULA": "Research cost and time of nodes the game has not costed come from the catalogue formulas at the current efficiency, scaled by the calibration factor from costed nodes.",
    "A-STRAIGHT-LINE": "Distances are straight-line tile distances, not road or rail path lengths.",
    "A-LOAN-MODIFIER": "The loan payment modifier is 1 (the settlement loan modifier is not exported) unless given.",
    "A-SCORE-WEIGHTS": "Research score: demand and bottleneck value in money per 30 days, chain-fit weight 0.5, divided by research cost.",
}


def clean(x: Any, digits: int = 4) -> Any:
    """Round floats for output; NaN/Infinity become None (never emitted as numbers)."""
    if isinstance(x, bool) or x is None:
        return x
    if isinstance(x, float):
        if not math.isfinite(x):
            return None
        r = round(x, digits)
        if r == int(r) and abs(r) < 1e15:
            return int(r)
        return r
    return x


def num(v: Any) -> float | None:
    """A finite number as float, else None (bools are not numbers)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    return None


# ------------------------------------------------------------------ Quantity

def quantity(value: Any, unit: str, kind: str, *, method: str | None = None, source: str | None = None,
             confidence: dict | None = None, digits: int = 4) -> dict:
    assert kind in KINDS, kind
    assert unit in UNITS, unit
    out: dict[str, Any] = {"value": clean(num(value), digits) if value is not None else None, "unit": unit, "kind": kind}
    if method is not None:
        out["method"] = method
    if source is not None:
        out["source"] = source
    if confidence is not None:
        out["confidence"] = confidence
    return out


def observed(value: Any, unit: str, source: str) -> dict:
    return quantity(value, unit, "observed", source=source)


def definition(value: Any, unit: str, source: str) -> dict:
    return quantity(value, unit, "definition", source=source)


def game_computed(value: Any, unit: str, source: str) -> dict:
    return quantity(value, unit, "game_computed", source=source)


def derived(value: Any, unit: str, method: str, digits: int = 4) -> dict:
    """An exact, deterministic function of observed/definition values (no modelling assumption)."""
    return quantity(value, unit, "derived", method=method, digits=digits)


def estimate(value: Any, unit: str, method: str, confidence: dict, digits: int = 4) -> dict:
    return quantity(value, unit, "estimate", method=method, confidence=confidence, digits=digits)


def parameter(value: Any, unit: str, source: str = "argument") -> dict:
    return quantity(value, unit, "parameter", source=source)


# ------------------------------------------------------------------ confidence (D-ADV-CONF-1)

def confidence(start: str, factors: Iterable[str] = ()) -> dict:
    """Start level lowered one step per distinct factor, floor `low` (addendum 3.4)."""
    assert start in ("high", "medium"), start
    fs = []
    for f in factors:
        assert f in FACTORS, f
        if f not in fs:
            fs.append(f)
    idx = LEVELS.index(start) - len(fs)
    return {"level": LEVELS[max(idx, 0)], "factors": fs}


def merge_confidence(records: Iterable[dict | None]) -> dict:
    """Overall confidence: the lowest level, with the union of factors."""
    level = "high"
    fs: list[str] = []
    for r in records:
        if not r:
            continue
        if LEVELS.index(r["level"]) < LEVELS.index(level):
            level = r["level"]
        for f in r.get("factors") or []:
            if f not in fs:
                fs.append(f)
    return {"level": level, "factors": fs}


def lower(record: dict, *factors: str) -> dict:
    """Apply extra factors to an existing confidence record (each new distinct factor lowers one step)."""
    fs = list(record.get("factors") or [])
    level = LEVELS.index(record["level"])
    for f in factors:
        assert f in FACTORS, f
        if f not in fs:
            fs.append(f)
            level = max(level - 1, 0)
    return {"level": LEVELS[level], "factors": fs}


# ------------------------------------------------------------------ Advice builder

class Advice:
    """Collects the advisor parts of one response. `basis` supplies snapshot-level factors (stale, inconsistent,
    static mismatch) that lower every estimate, plus its degraded inputs."""

    def __init__(self, tool: str, basis: Any | None = None):
        self.tool = tool
        self.basis = basis
        self.observed: dict[str, Any] = {}
        self.assumption_ids: list[str] = []
        self.inputs: dict[str, Any] = {}
        self.degraded: list[dict] = []
        self.contradictions: list[dict] = []
        self.records: list[dict] = []
        self.methods: list[tuple[str, str]] = []
        if basis is not None:
            for d in basis.degraded:
                self.degrade(**d)

    # ---- collection
    def assume(self, *ids: str) -> None:
        for i in ids:
            assert i in ASSUMPTIONS, i
            if i not in self.assumption_ids:
                self.assumption_ids.append(i)

    def degrade(self, input: str, reason: str, effect: str) -> None:  # noqa: A002 - contract field name
        assert reason in DEGRADED_REASONS, reason
        row = {"input": input, "reason": reason, "effect": effect}
        if row not in self.degraded:
            self.degraded.append(row)

    def contradiction(self, row: dict) -> None:
        if row not in self.contradictions:
            self.contradictions.append(row)

    def method(self, field: str, method: str) -> None:
        if (field, method) not in self.methods:
            self.methods.append((field, method))

    def conf(self, start: str, factors: Iterable[str] = ()) -> dict:
        """Confidence of one result, including the snapshot-level factors of the basis."""
        fs = list(factors)
        if self.basis is not None:
            fs = list(self.basis.factors) + fs
        rec = confidence(start, fs)
        self.records.append(rec)
        return rec

    def track(self, rec: dict | None) -> dict | None:
        if rec:
            if self.basis is not None:
                rec = lower(rec, *self.basis.factors)
            self.records.append(rec)
        return rec

    # ---- output
    def overall(self) -> dict:
        if not self.records:
            return confidence("high", self.basis.factors if self.basis is not None else ())
        return merge_confidence(self.records)

    def finish(self, result: dict, provenance: dict, max_contradictions: int = 20) -> dict:
        contradictions = self.contradictions[:max_contradictions]
        data = {
            "advisor": {"contract_version": CONTRACT_VERSION, "tool": self.tool, "player_only": True, "read_only": True},
            "result": result,
            "observed": self.observed,
            "assumptions": [{"id": i, "text": ASSUMPTIONS[i]} for i in self.assumption_ids],
            "calculation_inputs": self.inputs,
            "confidence": self.overall(),
            "degraded_inputs": list(self.degraded),
            "contradictions": contradictions,
            "basis": self.basis.to_dict() if self.basis is not None else None,
            "provenance": provenance,
        }
        if len(self.contradictions) > max_contradictions:
            data["contradictions_total"] = len(self.contradictions)
        return data
