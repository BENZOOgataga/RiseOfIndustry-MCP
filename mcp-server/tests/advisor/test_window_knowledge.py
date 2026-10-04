"""what_changed (in-memory window digests + history), knowledge base integrity, response sizes and safety."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import pytest

import build_fixtures as bf
from advisor.helpers import VALID_CALLS, WHAT_IF_CALLS, check_advisor_response
from conftest import REPO, Harness
from roi_mcp import config as cfg
from roi_mcp.advisor import window
from roi_mcp.advisor.knowledge import KnowledgeBase, get_knowledge
from roi_mcp.tools import ADVISOR_TOOL_NAMES
from roi_mcp.util import json_size


def publish_state(h, mutate, seq):
    doc = copy.deepcopy(h.world["state"])
    mutate(doc["data"])
    doc["seq"] = seq
    doc["content_hash"] = bf.content_hash(doc["data"])
    doc["data"]["session"]["game_date"] = f"Y5-03-{12 + seq - 10:02d}"
    doc["captured"]["game_date"] = doc["data"]["session"]["game_date"]
    h.write_raw("state", json.dumps(doc))
    hb = copy.deepcopy(h.world["heartbeat"])
    hb["data"]["families"]["state"] = bf.family_status(doc)
    h.write_raw("heartbeat", json.dumps(hb))
    return doc


def change_everything(d):
    d["companies"][0]["cash"]["value"] = 2_000_000.0
    d["buildings_player"] = [b for b in d["buildings_player"] if b["key"] != bf.B_TD]
    for b in d["buildings_player"]:
        if b["key"] == bf.B_PC2:
            b["flags"]["user_enabled"] = False
        if b["key"] == bf.B_PF1:
            b["inventory"][2]["count"] = 20
    for r in d["routes_player"]:
        if r["origin"] == bf.B_GW1:
            r["min_keep"]["value"] = 5
    d["research"]["player"]["unlocked"].append("AdvancedPaints")
    d["market"]["prices"][0]["price"] = 120.0


# ------------------------------------------------------------------ what_changed

def test_short_term_changes_between_two_loaded_snapshots(h):
    first = h.call("what_changed", {"horizon": "short_term"})
    assert first["data"]["result"]["short_term"]["available"] is False                  # one snapshot only
    publish_state(h, change_everything, 11)
    r = h.call("what_changed", {"horizon": "short_term", "limit": 50})
    check_advisor_response("what_changed", r)
    st = r["data"]["result"]["short_term"]
    assert st["available"] and st["from"]["seq"] == 10 and st["to"]["seq"] == 11
    kinds = {(c["kind"], c["subject"], c.get("field")) for c in st["changes"]}
    assert ("building_removed", f"building:{bf.B_TD}", None) in kinds
    assert ("building_changed", f"building:{bf.B_PC2}", "user_enabled") in kinds
    assert ("research_unlocked", "tech:AdvancedPaints", None) in kinds
    assert ("route_changed", f"route:{bf.B_GW1}|Gas|{bf.B_PC1}|own|0", "min_keep") in kinds
    cash = next(c for c in st["changes"] if c.get("field") == "cash")
    assert cash["delta"]["value"] == -500000 and cash["before"]["value"] == 2500000 and cash["delta"]["kind"] == "derived"
    # entity changes come before numeric ones
    first_numeric = next(i for i, c in enumerate(st["changes"]) if "delta" in c)
    assert all("delta" not in c for c in st["changes"][:first_numeric])
    # since_seq selects the baseline; an unknown seq is not_found with the available seqs
    assert h.call("what_changed", {"since_seq": 10})["data"]["result"]["short_term"]["from"]["seq"] == 10
    nf = h.call("what_changed", {"since_seq": 3})
    assert nf["error"]["code"] == "not_found" and "[10, 11]" in nf["error"]["hint"]


def test_window_cleared_on_world_session_change(h):
    h.call("what_changed")
    publish_state(h, change_everything, 11)
    h.call("what_changed")
    assert len(h.app.window.entries) == 2
    w2 = bf.build_world(world_session=bf.WORLD_SESSION_2)
    w2["state"]["seq"] = 12
    w2["heartbeat"] = bf.build_heartbeat(world_session=bf.WORLD_SESSION_2, static_doc=w2["static"], state_doc=w2["state"],
                                         history_doc=w2["history"])
    h.write_world(w2)
    r = h.call("what_changed", {"horizon": "short_term"})
    assert r["data"]["result"]["short_term"]["available"] is False
    assert r["data"]["result"]["short_term"]["window"]["seqs"] == [12]


def test_monthly_changes_from_history(h):
    r = h.call("what_changed", {"horizon": "monthly"})
    m = r["data"]["result"]["monthly"]
    assert m["available"] and m["from_month"] == "Y5-01" and m["to_month"] == "Y5-02"   # in-progress Y5-03 never compared
    assert m["changes"][0]["kind"] == "ledger_net" and m["changes"][0]["delta"]["value"] == 519000
    shop = [c for c in m["changes"] if c["kind"] == "shop_sales"]
    assert shop and shop[0]["delta"]["value"] == 1                                      # 23 -> 24 between Y5-01 and Y5-02
    assert r["data"]["result"]["short_term"] == {"available": False}


def test_monthly_unavailable_without_history(h):
    (h.exchange / "history.json").unlink()
    r = h.call("what_changed", {"horizon": "monthly"})
    assert r["data"]["result"]["monthly"]["available"] is False


def test_digest_and_diff_units():
    d1 = window.digest(bf.state_data())
    assert window.diff(d1, d1) == []
    d2 = copy.deepcopy(d1)
    d2["cash"] = None
    rows = window.diff(d1, d2)
    assert rows[0]["field"] == "cash" and "delta" not in rows[0]
    assert window.digest({})["buildings"] is None                 # missing section: unknown, not empty
    assert window.diff(window.digest({}), d1) == [r for r in window.diff(window.digest({}), d1)]


def test_window_memory_is_bounded(h):
    for seq in range(11, 40):
        publish_state(h, lambda d: None, seq)
        h.call("what_changed", {"horizon": "short_term"})
    assert len(h.app.window.entries) <= cfg.WINDOW_MAX_SNAPSHOTS


# ------------------------------------------------------------------ knowledge base

def test_knowledge_files_validate_and_evidence_exists():
    kb = KnowledgeBase()
    assert len(kb.mechanics) >= 40 and kb.terms and kb.pitfalls and kb.how_to
    seen = set()
    for collection in (kb.mechanics.values(), kb.terms, kb.pitfalls.values(), kb.how_to.values()):
        for e in collection:
            for ev in e["evidence"]:
                assert (REPO / ev["file"]).is_file(), ev["file"]
                seen.add(ev["file"])
            expected = {"CONFIRMED_IN_GAME": "high", "CONFIRMED_SOURCE": "high", "HIGH_CONFIDENCE": "medium"}.get(e["verification"], "low")
            assert e["confidence"] == expected
    for m in kb.mechanics.values():
        assert set(m["related"]) <= set(kb.mechanics), m["id"]
    for p in kb.pitfalls.values():
        assert set(p["related"]) <= set(kb.mechanics), p["id"]
    for h_ in kb.how_to.values():
        from roi_mcp.tools import ALL_TOOL_NAMES
        assert set(h_["related_tools"]) <= set(ALL_TOOL_NAMES), h_["id"]
    assert not any("rise of industry 2" in json.dumps(d).lower() for d in kb.docs.values())


def test_knowledge_file_with_missing_evidence_is_rejected(tmp_path):
    src = Path(get_knowledge().directory)
    for f in src.iterdir():
        (tmp_path / f.name).write_bytes(f.read_bytes())
    doc = json.loads((tmp_path / "mechanics.json").read_text(encoding="utf-8"))
    doc["mechanics"][0]["evidence"] = []
    (tmp_path / "mechanics.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError):
        KnowledgeBase(tmp_path)


@pytest.mark.parametrize("topic,expected", [("max send", "max-send"), ("Max_Send", "max-send"), ("min keep", "min-keep"),
                                            ("wages", "building-efficiency"), ("upkeep", "building-upkeep"),
                                            ("demand per month", "shop-demand-unit"), ("loan", "loans"),
                                            ("price history", "no-price-history"), ("AUTO_WH", "auto-warehouse"),
                                            ("dispatch-cost", "dispatch-cost")])
def test_explain_mechanic_lookup(h, topic, expected):
    r = h.call("explain_mechanic", {"topic": topic})
    assert r["ok"] and r["data"]["result"]["matches"][0]["id"] == expected, [m["id"] for m in r["data"]["result"]["matches"]]
    m = r["data"]["result"]["matches"][0]
    assert m["evidence"] and m["verification"] and m["resource_uri"].endswith(expected)
    check_advisor_response("explain_mechanic", r)


def test_explain_mechanic_glossary_and_catalogue_names(h):
    r = h.call("explain_mechanic", {"topic": "Usine pétrochimique"})
    res = r["data"]["result"]
    assert any(t["en"] == "Petrochemical Plant" for t in res["glossary"])
    ids = {c["id"] for c in res["catalog_terms"]}
    assert "building_type:PetrochemicalFactory" in ids                        # live catalogue: display + English name
    r = h.call("explain_mechanic", {"topic": "peinture"})
    assert any(c["english_name"] == "Paint" for c in r["data"]["result"]["catalog_terms"])
    assert r["meta"]["source"] == "static_catalog"


def test_explain_mechanic_pitfalls_and_unverified_factor(h):
    r = h.call("explain_mechanic", {"topic": "max send"})
    assert {p["id"] for p in r["data"]["result"]["pitfalls"]} >= {"max-send-not-per-trip", "max-send-shared"}
    r = h.call("explain_mechanic", {"topic": "demand unit"})
    m = r["data"]["result"]["matches"][0]
    assert m["verification"] == "HIGH_CONFIDENCE" and m["confidence"] == {"level": "medium", "factors": ["mechanic_unverified"]}


def test_how_to_lookup(h):
    r = h.call("how_to", {"action": "set max send"})
    m = r["data"]["result"]["matches"][0]
    assert m["id"] == "change-max-send" and m["audience"] == "player_in_game" and m["mcp_can_do_it"] is False
    r = h.call("how_to", {"action": "best shop"})
    assert r["data"]["result"]["matches"][0]["audience"] == "mcp_client"


# ------------------------------------------------------------------ sizes on the large synthetic fixture

@pytest.fixture(scope="module")
def large_world():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "perf"))
    import build_large_fixture as blf
    return blf.build_large()


def test_all_advisor_tools_respect_size_cap_on_large_fixture(exchange, clock, procs, large_world):
    hh = Harness(exchange, clock, procs)
    hh.write_world(large_world)
    calls = dict(VALID_CALLS)
    st = large_world["state"]["data"]
    paint = next(b["key"] for b in st["buildings_player"] if b.get("recipe") == "Paints")
    shops = [s["building"] for s in st["shops"] if any(p["product"] == "Paint" for p in s["products"])]
    keys = [f"building:{b['key']}" for b in st["buildings_player"]]
    calls.update({"review_routes": {"limit": 50}, "find_opportunities": {"limit": 50, "include_unviable": True},
                  "get_profitability": {"limit": 25}, "what_changed": {"limit": 50},
                  "plan_chain": {"product": "Paint", "target_per_month": 1e5, "existing_capacity": "none"},
                  "suggest_research": {"include_reachable": True, "max_chain": 6, "limit": 50},
                  "route_calculator": {"origin": f"building:{paint}", "destination": f"building:{shops[-1]}", "product": "Paint"},
                  "compare_options": {"kind": "shop_destinations", "origin": f"building:{paint}", "product": "Paint",
                                      "options": [f"building:{x}" for x in shops[:8]]},
                  "spatial_analysis": {"mode": "hub", "locations": keys[:30], "candidates": keys[:20]},
                  "get_chain_graph": {"product": "Paint", "depth": 8, "format": "mermaid"}})
    sizes = {}
    for name in ADVISOR_TOOL_NAMES:
        for detail in ("full", "standard", "summary"):
            t0 = time.perf_counter()
            r = hh.call(name, {**calls[name], "detail": detail, "language": "both"})
            ms = round((time.perf_counter() - t0) * 1000)
            assert r["ok"], (name, r.get("error"))
            check_advisor_response(name, r)
            # tools bound their own output: the generic truncation backstop must not be needed
            assert not any(w["code"] == "truncated" for w in r["meta"]["warnings"]), (name, detail)
            if name == "get_chain_graph":
                ids = {n["id"] for n in r["data"]["result"]["nodes"]}
                assert all(e["from"] in ids and e["to"] in ids for e in r["data"]["result"]["edges"])
            sizes[f"{name}:{detail}"] = (json_size(r), ms)
    for name in ADVISOR_TOOL_NAMES:
        assert sizes[f"{name}:summary"][0] <= sizes[f"{name}:standard"][0] <= sizes[f"{name}:full"][0] + 50, name
    for change in WHAT_IF_CALLS:
        r = hh.call("what_if", {"change": change})
        assert json_size(r) <= cfg.RESPONSE_SIZE_CAP_BYTES
    # maximum accepted inputs
    worst = {"spatial_analysis": {"mode": "matrix", "locations": keys[:12], "detail": "full", "language": "both"},
             "research_path": {"target": "Polymers", "detail": "full", "language": "both"},
             "get_chain_graph": {"product": "Paint", "depth": 8, "format": "mermaid", "detail": "full", "language": "both"},
             "loan_calculator": {"principal": 1e12, "apr": 10, "duration_months": 1200, "existing": True, "detail": "full",
                                 "language": "both"}}
    for name, args in worst.items():
        r = hh.call(name, args)
        assert r["ok"] and not any(w["code"] == "truncated" for w in r["meta"]["warnings"]), name
        check_advisor_response(name, r)
        sizes[f"{name}:worst"] = (json_size(r), 0)
    print("advisor sizes on large fixture (bytes, ms):", sizes)


# ------------------------------------------------------------------ safety

def test_advisor_never_writes_anything_but_the_refresh_request(exchange, clock, procs):
    hh = Harness(exchange, clock, procs)
    hh.write_world(bf.build_world())
    before = {p.name: p.stat().st_mtime_ns for p in exchange.iterdir()}
    for name in ADVISOR_TOOL_NAMES:
        hh.call(name, VALID_CALLS[name])
    for change in WHAT_IF_CALLS:
        hh.call("what_if", {"change": change})
    after = {p.name: p.stat().st_mtime_ns for p in exchange.iterdir()}
    assert after == before                                            # no fresh -> no file written at all


def test_advisor_parameters_have_no_write_semantics(h):
    verbs = {"set", "write", "build", "demolish", "buy", "sell", "repay", "research", "dispatch", "command", "exec",
             "update", "delete", "toggle", "pay", "bid", "unlock", "enqueue", "cancel", "save", "load", "apply"}
    for name in ADVISOR_TOOL_NAMES:
        for p in h.app.specs[name].input_schema()["properties"]:
            # what_if.change is a hypothetical evaluated on copies (addendum 6.7); it is never applied
            assert p.split("_")[0] not in verbs, (name, p)
    import re
    net = re.compile(r"^\s*(import|from)\s+(socket|urllib|requests|http|httpx|aiohttp|subprocess|ssl)", re.M)
    for f in (REPO / "mcp-server" / "src" / "roi_mcp" / "advisor").glob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert not net.search(text), f.name                          # no network or process access
        for banned in ("open(", ".write_text", ".write_bytes", ".unlink(", ".mkdir(", "os.remove", "shutil"):
            assert banned not in text, (f.name, banned)               # the advisor writes no file


@pytest.mark.parametrize("name,key,args", [("review_routes", "findings", {"limit": 50}),
                                           ("find_opportunities", "opportunities", {"limit": 50, "include_unviable": True}),
                                           ("suggest_research", "suggestions", {"limit": 50, "include_reachable": True, "max_chain": 6})])
def test_size_bounded_pages_lose_no_rows(exchange, clock, procs, large_world, name, key, args):
    hh = Harness(exchange, clock, procs)
    hh.write_world(large_world)
    seen, cur, pages = [], None, 0
    while True:
        r = hh.call(name, {**args, "language": "both", **({"cursor": cur} if cur else {})})
        assert r["ok"] and not any(w["code"] == "truncated" for w in r["meta"]["warnings"])
        check_advisor_response(name, r)
        seen += [json.dumps({k: x for k, x in row.items() if k != "rank"}, sort_keys=True) for row in r["data"]["result"][key]]
        pages += 1
        cur = r["page"]["next_cursor"]
        if not cur:
            break
    assert len(seen) == r["page"]["total"] and len(set(seen)) == len(seen), (name, len(seen), r["page"]["total"], pages)
