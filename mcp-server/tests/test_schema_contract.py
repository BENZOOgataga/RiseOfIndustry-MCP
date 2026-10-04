"""T-4: snapshot fixtures validate against schemas/; the server rejects invalid files and major
mismatches; committed artefacts equal their generators."""

import copy
import json

import pytest

import build_fixtures as bf
from conftest import SCHEMA_DIR, TOOL_SCHEMA_DIR
from roi_mcp import response_schemas
from roi_mcp.schemas import SchemaSet, load_json_lenient
from roi_mcp.tools import ALL_TOOL_NAMES, TOOL_NAMES

FAMILIES = ("heartbeat", "static", "state", "history")


@pytest.fixture(scope="module")
def schemas():
    s = SchemaSet(SCHEMA_DIR)
    assert not s.errors, s.errors
    return s


@pytest.mark.parametrize("family", FAMILIES)
def test_committed_sample_fixtures_validate(schemas, family):
    doc = json.loads((bf.SAMPLE_DIR / f"{family}.json").read_text(encoding="utf-8"))
    assert schemas.validate(family, doc) is None


@pytest.mark.parametrize("family", FAMILIES)
def test_committed_samples_equal_builder_output(family):
    committed = json.loads((bf.SAMPLE_DIR / f"{family}.json").read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(bf.build_world()[family])), \
        "re-run: uv run --directory mcp-server python tests/fixtures/build_fixtures.py"


def test_builder_variants_validate(schemas):
    for state in ("menu", "loading", "disabled", "faulted", "starting"):
        assert schemas.validate("heartbeat", bf.build_heartbeat(state=state)) is None
    assert schemas.validate("heartbeat", bf.build_heartbeat(state="unsupported_build", compatibility="unsupported_build")) is None
    pending = bf.build_heartbeat(state="menu", compatibility="pending")
    assert pending["game"] is None and pending["compatibility"] == "pending"
    assert schemas.validate("heartbeat", pending) is None


def test_invalid_documents_are_rejected(schemas):
    world = bf.build_world()
    bad = copy.deepcopy(world["state"])
    bad["data"]["routes_player"][0]["max_send"]["scope"] = "per_route"
    assert schemas.validate("state", bad) is not None
    bad2 = copy.deepcopy(world["static"])
    bad2["compatibility"] = "unsupported_build"
    assert schemas.validate("static", bad2) is not None
    bad3 = copy.deepcopy(world["heartbeat"])
    del bad3["data"]["families"]
    assert schemas.validate("heartbeat", bad3) is not None


def test_server_rejects_invalid_and_mismatched_files(h):
    # invalid state with no previous snapshot -> snapshot_unavailable
    h.write_raw("state", json.dumps({**h.world["state"], "data": {"session": "nope"}}))
    r = h.call("list_routes")
    assert not r["ok"] and r["error"]["code"] == "snapshot_unavailable"
    # major mismatch -> schema_mismatch (both versions in the message)
    doc = copy.deepcopy(h.world["state"])
    doc["schema_version"] = "2.0.0"
    h.write_raw("state", json.dumps(doc))
    r = h.call("list_routes")
    assert r["error"]["code"] == "schema_mismatch"
    assert "2.0.0" in r["error"]["message"] and r["error"]["details"]["file_version"] == "2.0.0"
    # minor version bump of the same major is accepted
    doc["schema_version"] = "1.4.0"
    doc["seq"] = 99
    h.write_raw("state", json.dumps(doc))
    assert h.call("list_routes")["ok"]


def test_static_with_unverified_compatibility_is_rejected(h):
    doc = copy.deepcopy(h.world["static"])
    doc["compatibility"] = "unsupported_build"
    h.write_raw("static", json.dumps(doc))
    r = h.call("list_products")
    assert r["error"]["code"] == "snapshot_unavailable"


def test_refresh_request_schema_matches_writer(h):
    schema = load_json_lenient(SCHEMA_DIR / "refresh-request.schema.json")
    from jsonschema import Draft202012Validator
    h.app.refresh.writer.write({"state": 1730000000123, "history": None, "static": None})
    doc = json.loads((h.exchange / "refresh-request.json").read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(doc)) == []
    assert (h.exchange / "refresh-request.json").stat().st_size < 1024


def test_tool_response_schema_files_are_generated():
    # V1.1: the 29 V1 files (unchanged) and the advisor files are all generated.
    for name in ALL_TOOL_NAMES:
        p = TOOL_SCHEMA_DIR / f"{name}.schema.json"
        assert p.is_file(), f"missing {p}"
        assert p.read_text(encoding="utf-8") == response_schemas.render(name), \
            "re-run: uv run --directory mcp-server python -m roi_mcp.response_schemas"
    extra = {p.name for p in TOOL_SCHEMA_DIR.glob("*.schema.json")} - {f"{n}.schema.json" for n in ALL_TOOL_NAMES}
    assert not extra


def test_tool_response_schemas_reject_garbage():
    from jsonschema import Draft202012Validator
    v = Draft202012Validator(response_schemas.tool_schema("list_routes"))
    assert not v.is_valid({"ok": True})
    assert not v.is_valid({"ok": False, "error": {"code": "made_up", "message": "x", "hint": None}, "meta": {}})
