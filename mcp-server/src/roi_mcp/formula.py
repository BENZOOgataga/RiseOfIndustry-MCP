"""Safe arithmetic evaluator for game formula texts (static.json `formulas[]`).

Supports numbers, variables, + - * / ^ (right-associative power), unary +/-, parentheses and a fixed
set of functions. It is a hand-written tokenizer + recursive-descent parser; Python eval/exec are
never used. Unknown variables or functions raise FormulaError.
"""

from __future__ import annotations

import math
import re
from typing import Callable, Mapping


class FormulaError(ValueError):
    pass


def _round_half_even(x: float) -> float:
    # .NET Math.Round / Unity Mathf.Round default: midpoint rounds to the even integer.
    return float(round(x))


def _sign(x: float) -> float:
    return 1.0 if x > 0 else (-1.0 if x < 0 else 0.0)


FUNCTIONS: dict[str, tuple[Callable[..., float], int, int]] = {
    # name: (callable, min_args, max_args)
    "min": (lambda *a: float(min(a)), 1, 64),
    "max": (lambda *a: float(max(a)), 1, 64),
    "abs": (lambda a: float(abs(a)), 1, 1),
    "ceil": (lambda a: float(math.ceil(a)), 1, 1),
    "floor": (lambda a: float(math.floor(a)), 1, 1),
    "round": (lambda a: _round_half_even(a), 1, 1),
    "sqrt": (lambda a: math.sqrt(a), 1, 1),
    "sign": (lambda a: _sign(a), 1, 1),
}

_TOKEN_RE = re.compile(
    r"\s*(?:(?P<num>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)|(?P<name>[A-Za-z_][A-Za-z0-9_]*)|(?P<op>[-+*/^(),]))"
)

MAX_FORMULA_LENGTH = 2000
MAX_DEPTH = 64


def tokenize(text: str) -> list[tuple[str, str]]:
    if not isinstance(text, str):
        raise FormulaError("formula text must be a string")
    if len(text) > MAX_FORMULA_LENGTH:
        raise FormulaError("formula text too long")
    pos = 0
    tokens: list[tuple[str, str]] = []
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        m = _TOKEN_RE.match(text, pos)
        if not m or m.end() == pos:
            raise FormulaError(f"unexpected character at {pos}: {text[pos:pos + 10]!r}")
        pos = m.end()
        if m.group("num") is not None:
            tokens.append(("num", m.group("num")))
        elif m.group("name") is not None:
            tokens.append(("name", m.group("name")))
        else:
            tokens.append(("op", m.group("op")))
    tokens.append(("end", ""))
    return tokens


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], variables: Mapping[str, float]):
        self.tokens = tokens
        self.i = 0
        self.vars = variables
        self.depth = 0

    def peek(self) -> tuple[str, str]:
        return self.tokens[self.i]

    def take(self) -> tuple[str, str]:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def expect(self, kind: str, value: str | None = None) -> tuple[str, str]:
        tok = self.take()
        if tok[0] != kind or (value is not None and tok[1] != value):
            raise FormulaError(f"expected {value or kind}, got {tok[1] or tok[0]!r}")
        return tok

    def enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise FormulaError("formula nested too deeply")

    def leave(self) -> None:
        self.depth -= 1

    # expr := term (('+'|'-') term)*
    def expr(self) -> float:
        self.enter()
        value = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            rhs = self.term()
            value = value + rhs if op == "+" else value - rhs
        self.leave()
        return value

    # term := unary (('*'|'/') unary)*
    def term(self) -> float:
        value = self.unary()
        while self.peek() in (("op", "*"), ("op", "/")):
            op = self.take()[1]
            rhs = self.unary()
            if op == "*":
                value = value * rhs
            else:
                if rhs == 0:
                    raise FormulaError("division by zero")
                value = value / rhs
        return value

    # unary := ('+'|'-') unary | power
    def unary(self) -> float:
        if self.peek() == ("op", "-"):
            self.take()
            self.enter()
            v = -self.unary()
            self.leave()
            return v
        if self.peek() == ("op", "+"):
            self.take()
            self.enter()
            v = self.unary()
            self.leave()
            return v
        return self.power()

    # power := primary ('^' unary)?   (right-associative)
    def power(self) -> float:
        base = self.primary()
        if self.peek() == ("op", "^"):
            self.take()
            self.enter()
            exponent = self.unary()
            self.leave()
            try:
                result = math.pow(base, exponent)
            except (OverflowError, ValueError) as exc:
                raise FormulaError(f"invalid power: {exc}") from None
            return result
        return base

    def primary(self) -> float:
        kind, value = self.take()
        if kind == "num":
            return float(value)
        if kind == "op" and value == "(":
            v = self.expr()
            self.expect("op", ")")
            return v
        if kind == "name":
            if self.peek() == ("op", "("):
                self.take()
                fn = FUNCTIONS.get(value.lower())
                if fn is None:
                    raise FormulaError(f"unknown function {value!r}")
                args: list[float] = []
                if self.peek() != ("op", ")"):
                    args.append(self.expr())
                    while self.peek() == ("op", ","):
                        self.take()
                        args.append(self.expr())
                self.expect("op", ")")
                func, lo, hi = fn
                if not (lo <= len(args) <= hi):
                    raise FormulaError(f"function {value} takes {lo}..{hi} arguments, got {len(args)}")
                try:
                    return float(func(*args))
                except (ValueError, OverflowError) as exc:
                    raise FormulaError(f"{value}: {exc}") from None
            if value not in self.vars:
                raise FormulaError(f"unknown variable {value!r}")
            v = self.vars[value]
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise FormulaError(f"variable {value!r} is not a number")
            return float(v)
        raise FormulaError(f"unexpected token {value or kind!r}")


def evaluate(text: str, variables: Mapping[str, float] | None = None) -> float:
    """Evaluate a formula text with the given variables. Raises FormulaError on any problem."""
    tokens = tokenize(text)
    parser = _Parser(tokens, variables or {})
    value = parser.expr()
    if parser.peek()[0] != "end":
        raise FormulaError(f"unexpected trailing token {parser.peek()[1]!r}")
    if value != value or value in (float("inf"), float("-inf")):
        raise FormulaError("non-finite result")
    return value


def variables_of(text: str) -> list[str]:
    """Names used as variables (not function names) in a formula text."""
    tokens = tokenize(text)
    out = []
    for i, (kind, value) in enumerate(tokens):
        if kind == "name" and tokens[i + 1] != ("op", "(") and value not in out:
            out.append(value)
    return out
