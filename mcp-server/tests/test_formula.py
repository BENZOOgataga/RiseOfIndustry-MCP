"""Safe formula evaluator (used by D-ROUTE-2)."""

import ast
import inspect
import math

import pytest

from roi_mcp.formula import FormulaError, evaluate, variables_of

STATIC_FORMULAS = {
    "ManualDestinationDispatchCost": ("(250 + distance * 10) * difficulty * actor", {"distance": 22, "difficulty": 1, "actor": 1}, 470),
    "TrainTerminalDispatchCost": ("(2250 + distance * 25) * difficulty * actor", {"distance": 40, "difficulty": 1.25, "actor": 1}, 4062.5),
    "Research Time": ("(60 + (tier ^ 3) * 60) / efficiency", {"tier": 2, "efficiency": 1.0}, 540),
    "Settlement Distance Restriction": ("max(abs(x0 - x1), abs(y0 - y1))", {"x0": 1, "x1": 10, "y0": 5, "y1": -7}, 12),
    "GlobalMarket": ("2 * sign(demand - sold - stored) * sqrt(abs(demand - sold - stored))",
                     {"demand": 10, "sold": 30, "stored": 5}, -10.0),
}


@pytest.mark.parametrize("name", sorted(STATIC_FORMULAS))
def test_game_formulas(name):
    text, variables, expected = STATIC_FORMULAS[name]
    assert evaluate(text, variables) == pytest.approx(expected)


def test_operators_and_precedence():
    assert evaluate("1 + 2 * 3") == 7
    assert evaluate("(1 + 2) * 3") == 9
    assert evaluate("2 ^ 3 ^ 2") == 512  # right associative
    assert evaluate("-2 ^ 2") == -4
    assert evaluate("10 / 4") == 2.5
    assert evaluate("ceil(0.05 * ceil(120000 / 50000))") == 1
    assert evaluate("floor(2.7) + round(2.5) + round(3.5)") == 2 + 2 + 4  # half-to-even like .NET
    assert evaluate("min(3, 1, 2) + max(1, 5)") == 6
    assert evaluate("1.5e2") == 150
    assert math.isclose(evaluate("sqrt(2)"), math.sqrt(2))


@pytest.mark.parametrize("text", [
    "__import__('os')", "a.b", "1 +", "(1", "1)", "x", "foo(1)", "1 / 0", "max()", "abs(1, 2)", "1 2", "[1]", "'s'",
    "sqrt(-1)", "10 ^ 400", "", "open(1)", "getattr(1, 2)",
])
def test_rejects_unsafe_or_invalid(text):
    with pytest.raises(FormulaError):
        evaluate(text, {"distance": 1})


def test_rejects_non_numeric_variables():
    with pytest.raises(FormulaError):
        evaluate("x + 1", {"x": "1"})
    with pytest.raises(FormulaError):
        evaluate("x + 1", {"x": True})


def test_depth_and_length_limits():
    with pytest.raises(FormulaError):
        evaluate("(" * 200 + "1" + ")" * 200)
    with pytest.raises(FormulaError):
        evaluate("1+" * 2000 + "1")


def test_variables_of():
    assert variables_of("(250 + distance * 10) * difficulty * actor") == ["distance", "difficulty", "actor"]
    assert variables_of("max(abs(x0 - x1), 1)") == ["x0", "x1"]


def test_evaluator_uses_no_dynamic_code_execution():
    import roi_mcp.formula as f

    tree = ast.parse(inspect.getsource(f))
    forbidden = {"ev" + "al", "ex" + "ec", "comp" + "ile", "__imp" + "ort__"}
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not (called & forbidden)
