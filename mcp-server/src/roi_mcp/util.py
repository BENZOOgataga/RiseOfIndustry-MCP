"""Small shared helpers: time parsing, text normalization, id helpers, JSON sizing."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

INT32_MAX = 2147483647

_ISO_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<h>\d{2}):(?P<m>\d{2})(?::(?P<s>\d{2})(?:\.(?P<f>\d+))?)?"
    r"(?P<tz>Z|z|[+-]\d{2}:?\d{2})?$"
)


def parse_utc(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp (as written by .NET or Python) into an aware UTC datetime.

    Accepts 'Z' or numeric offsets and any number of fractional digits. Returns None when the
    value is missing or unparseable; callers treat that as "unknown".
    """
    if not isinstance(value, str) or not value:
        return None
    m = _ISO_RE.match(value.strip())
    if not m:
        return None
    try:
        year, month, day = (int(p) for p in m.group("date").split("-"))
        frac = m.group("f") or "0"
        micro = int((frac + "000000")[:6])
        dt = datetime(year, month, day, int(m.group("h")), int(m.group("m")), int(m.group("s") or 0), micro)
    except ValueError:
        return None
    tz = m.group("tz")
    if tz and tz not in ("Z", "z"):
        # Pure datetime arithmetic: values such as .NET DateTime.MinValue/MaxValue with an offset fall outside
        # the platform's timestamp range, and an out-of-range result is "unknown", never an exception.
        try:
            sign = 1 if tz[0] == "+" else -1
            digits = tz[1:].replace(":", "")
            offset = timedelta(hours=int(digits[:2]), minutes=int(digits[2:4] or 0))
            return (dt - sign * offset).replace(tzinfo=timezone.utc)
        except (ValueError, OverflowError):
            return None
    return dt.replace(tzinfo=timezone.utc)


def format_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def normalize_name(text: Any) -> str:
    """Case-, accent- and whitespace-insensitive key used for exact name matching (PRD 13.6)."""
    if text is None:
        return ""
    s = unicodedata.normalize("NFKD", str(text))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.casefold()
    # Ligatures are letters, not accents, so NFKD keeps them: fold them for French names such as "Œufs".
    s = s.replace("œ", "oe").replace("æ", "ae")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def json_size(obj: Any) -> int:
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def args_digest(args: Any) -> str:
    """Short stable digest of tool arguments for the server log (never the full payload)."""
    try:
        text = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:  # pragma: no cover - defensive
        text = repr(args)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


_KEY_RE = re.compile(r"^(?P<prefab>.+)@(?P<x>-?\d+),(?P<y>-?\d+)(?:#(?P<suffix>.+))?$")


def parse_building_key(key: str | None) -> dict | None:
    """Split a raw building key '<prefab>@<x>,<y>[#suffix]' into parts (None if not that shape)."""
    if not key:
        return None
    m = _KEY_RE.match(key)
    if not m:
        return None
    return {"prefab": m.group("prefab"), "x": int(m.group("x")), "y": int(m.group("y")), "suffix": m.group("suffix")}


def encode_cursor(offset: int, token: str) -> str:
    raw = f"v1|{offset}|{token}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[int, str] | None:
    try:
        pad = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + pad).decode("utf-8")
        version, offset, token = raw.split("|", 2)
        if version != "v1":
            return None
        off = int(offset)
        if off < 0:
            return None
        return off, token
    except Exception:
        return None


def month_key(month: str | None) -> tuple[int, int] | None:
    """'Y78-05' -> (78, 5)."""
    if not isinstance(month, str):
        return None
    m = re.match(r"^Y(\d+)-(\d{1,2})$", month)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def first_not_none(*values: Any) -> Any:
    for v in values:
        if v is not None:
            return v
    return None


def uniq(seq: Iterable[Any]) -> list:
    seen = set()
    out = []
    for item in seq:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def round_num(value: Any, digits: int = 4) -> Any:
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        r = round(value, digits)
        if r == int(r) and abs(r) < 1e15:
            return int(r) if digits == 0 else r
        return r
    return value
