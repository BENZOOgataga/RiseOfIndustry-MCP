"""Tool error type and the closed vocabularies of error and warning codes (PRD 13.3)."""

from __future__ import annotations

ERROR_CODES = (
    "game_not_running",
    "observer_not_detected",
    "observer_unresponsive",
    "at_main_menu",
    "loading",
    "observer_disabled",
    "observer_faulted",
    "unsupported_build",
    "snapshot_unavailable",
    "section_unavailable",
    "schema_mismatch",
    "not_found",
    "ambiguous",
    "stale_reference",
    "invalid_argument",
    "internal_error",
)

WARNING_CODES = (
    "stale",
    "refresh_timeout",
    "fresh_not_applicable",
    "snapshot_invalid_using_previous",
    "static_mismatch",
    "inconsistent_snapshot",
    "english_name_unavailable",
    "ui_label_unvalidated",
    "section_degraded",
    "active_actor_differs",
    "catalog_from_previous_session",
    "game_unresponsive",
    "truncated",
)


class ToolError(Exception):
    def __init__(self, code: str, message: str, hint: str | None = None, candidates: list | None = None,
                 details: dict | None = None):
        super().__init__(message)
        assert code in ERROR_CODES, code
        self.code = code
        self.message = message
        self.hint = hint
        self.candidates = candidates
        self.details = details

    def to_dict(self) -> dict:
        out = {"code": self.code, "message": self.message, "hint": self.hint}
        if self.candidates is not None:
            out["candidates"] = self.candidates
        if self.details is not None:
            out["details"] = self.details
        return out
