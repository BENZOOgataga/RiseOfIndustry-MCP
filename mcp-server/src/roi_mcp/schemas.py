"""Loading of the snapshot JSON Schemas (the wire contract written by the observer)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

FAMILIES = ("heartbeat", "static", "state", "history")



def load_json_lenient(path: Path) -> Any:
    """Parse a schema file. Some hand-written schema files contain backslashes that are not valid
    JSON escapes (e.g. a regex '\\.' written as '\\.' with one backslash); those are re-escaped."""
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(fix_invalid_escapes(text))


def fix_invalid_escapes(text: str) -> str:
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt in '"\\/bfnrtu':
                out.append(ch + nxt)
                i += 2
                continue
            out.append("\\\\")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


class SchemaSet:
    def __init__(self, schema_dir: Path):
        self.schema_dir = Path(schema_dir)
        self._validators: dict[str, Draft202012Validator] = {}
        self._versions: dict[str, str | None] = {}
        self.errors: dict[str, str] = {}
        for fam in FAMILIES:
            path = self.schema_dir / f"{fam}.schema.json"
            try:
                schema = load_json_lenient(path)
                Draft202012Validator.check_schema(schema)
                self._validators[fam] = Draft202012Validator(schema)
                self._versions[fam] = schema.get("x-schema-version")
            except Exception as exc:  # missing or broken schema: files of that family cannot be validated
                self.errors[fam] = f"{type(exc).__name__}: {exc}"

    def available(self, family: str) -> bool:
        return family in self._validators

    def version(self, family: str) -> str | None:
        return self._versions.get(family)

    def validate(self, family: str, doc: Any) -> str | None:
        """Return None when valid, else a short description of the first error."""
        v = self._validators.get(family)
        if v is None:
            return f"schema_not_loaded: {self.errors.get(family, 'unknown')}"
        err = next(iter(v.iter_errors(doc)), None)
        if err is None:
            return None
        path = "/".join(str(p) for p in err.absolute_path)
        msg = err.message
        if len(msg) > 200:
            msg = msg[:200] + "..."
        return f"{path or '<root>'}: {msg}"


@lru_cache(maxsize=4)
def get_schema_set(schema_dir: str) -> SchemaSet:
    return SchemaSet(Path(schema_dir))
