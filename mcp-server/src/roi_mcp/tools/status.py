"""get_game_status and search (PRD 14.1)."""

from __future__ import annotations

from .. import config as cfg
from ..app import COMMON_SUFFIX, CallContext, ToolSpec
from ..index import make_id
from ..util import parse_utc
from .common import provenance

LIMITATIONS = [
    "No market price history: the game does not retain it (price_history.available=false).",
    "No daily money ledger and no per-route history: the game keeps monthly aggregates only.",
    "Vehicle ids are session-scoped pooled objects; vehicles are reported mainly in aggregate.",
    "AI companies have infinite money; their cash is reported as infinite.",
    "Max Send is one shared cap per destination and product (validated in-game, gate E2); State destinations show no Max Send control and are reported as unlimited.",
    "The name of the loaded save is not available (save_name.value=null).",
    "AI building detail and AI routes are optional observer sections, off by default.",
    "Route path geometry is optional (include_route_paths), off by default.",
    "Only the verified game build is supported (Steam build 9064059, 2.3.3 0507b); other builds report unsupported_build.",
    "dispatch_amount_now is incomplete (complete=false) when a dynamic world event targets the destination.",
    "AI product goals are unavailable (brain-state reads not reviewed).",
]


def get_game_status(ctx: CallContext) -> dict:
    lv = ctx.lv
    app = ctx.app
    hbd = lv.hb_data
    hb = lv.heartbeat
    fams = hbd.get("families") or {}
    for fam in ("static", "state", "history"):
        app.store.load(fam)
    last_captures = {}
    for fam in ("static", "state", "history"):
        f = fams.get(fam) or {}
        st = app.store.families[fam]
        last_captures[fam] = {
            "seq": f.get("seq"), "last_published_utc": f.get("last_published_utc"),
            "last_verified_utc": f.get("last_verified_utc"), "size_bytes": f.get("size_bytes"),
            "loaded_by_server": {"seq": st.current.seq if st.current else None,
                                 "world_session": st.current.world_session if st.current else None,
                                 "invalid": st.invalid, "schema_mismatch": st.mismatch},
        }
    rsc = hbd.get("reflection_self_check")
    game = {
        "running": lv.running,
        "pid": lv.pid,
        "process_start_utc": lv.process_start.isoformat() if lv.process_start else None,
        "state": lv.game_state,
        "observer_state": hbd.get("state"),
        "paused": hbd.get("paused") if hb else None,
        "speed_level": hbd.get("speed_level"),
        "time_scale": hbd.get("time_scale"),
        "game_date": hbd.get("game_date"),
        "world_session": lv.world_session,
        "module": {"id": hbd.get("module_id"), "name": hbd.get("module_name")} if hb else None,
        "language": hbd.get("language"),
        "compatibility": "unsupported_build" if lv.unsupported_build else (hbd.get("compatibility") or lv.compatibility),
        "detected_game": hbd.get("detected_game") if hb else None,
        "expected_game": hbd.get("expected_game") if hb else None,
        "save_name": hbd.get("save_name"),
        "active_actor_differs": hbd.get("active_actor_differs"),
    }
    observer = None
    if hb is not None:
        observer = {
            "version": hbd.get("observer_version") or hb.doc.get("observer_version"),
            "heartbeat_written_utc": hbd.get("written_utc"),
            "heartbeat_age_s": round(lv.heartbeat_age_s, 1) if lv.heartbeat_age_s is not None else None,
            "main_thread_last_tick_utc": hbd.get("main_thread_last_tick_utc"),
            "last_captures": last_captures,
            "last_capture": hbd.get("last_capture"),
            "effective_interval_s": hbd.get("effective_interval_s"),
            "degraded": hbd.get("degraded"),
            "optional_sections_suppressed": hbd.get("optional_sections_suppressed"),
            "disabled_sections": hbd.get("disabled_sections"),
            "reflection_self_check": ({k: rsc.get(k) for k in ("total", "resolved", "type_mismatch", "missing")}
                                      if isinstance(rsc, dict) else None),
            "errors_last_hour": hbd.get("errors_last_hour"),
            "publish_failures": hbd.get("publish_failures"),
            "id_collisions": hbd.get("id_collisions"),
            "kill_switch": hbd.get("kill_switch"),
            "config_warnings": hbd.get("config_warnings"),
            "refresh_seen": hbd.get("refresh_seen"),
            "refresh_served": hbd.get("refresh_served"),
            "frame_stats": hbd.get("frame_stats"),
        }
    else:
        hbst = app.store.families["heartbeat"]
        ctx.add_unavailable("observer", "heartbeat_invalid" if hbst.invalid else (
            "heartbeat_schema_mismatch" if hbst.mismatch else "no_heartbeat_file"))
    server = {
        "version": cfg.SERVER_VERSION,
        "schema_versions": {f: app.schemas.version(f) for f in ("heartbeat", "static", "state", "history")},
        "supported_schema_major": cfg.SUPPORTED_SCHEMA_MAJOR,
        "exchange_dir": str(app.config.exchange_dir),
        "exchange_dir_exists": app.config.exchange_dir.is_dir(),
        "refresh_wait_s": app.refresh.wait_s,
        "heartbeat_schema_mismatch": app.store.families["heartbeat"].mismatch,
        "schema_load_errors": app.schemas.errors or None,
    }
    if lv.game_state != "ready":
        hint = lv.hint()
    else:
        hint = None
    return {"game": game, "observer": observer, "server": server, "limitations": LIMITATIONS, "hint": hint,
            "provenance": provenance(observed=["game", "observer"], persistence={"game": "RUNTIME", "observer": "RUNTIME"})}


SEARCH_KINDS = ["building", "building_type", "product", "recipe", "city", "region", "company", "tech", "shop"]


def search(ctx: CallContext) -> dict:
    ctx.static(required=False)
    state = ctx.snapshot("state", required=False)
    ix = ctx.state_index() if state is not None else None
    kinds = ctx.args.get("kinds") or None
    kind_set = None
    if kinds:
        kind_set = set()
        for k in kinds:
            kind_set.update(("building", "shop") if k == "building" else (k,))
    owner_ids = None
    owner = ctx.args.get("owner")
    if owner is not None:
        if ix is None:
            ctx.add_unavailable("owner_filter", "no current state snapshot")
            owner_ids = set()
        else:
            from .common import owner_filter
            owner_ids = owner_filter(ctx, ix, owner, default="all")
    rows = ctx.resolver().search(ctx.args["query"], kind_set, owner_ids)
    out = []
    for r in rows:
        e = r["entity"]
        out.append({
            "id": e.id, "kind": e.kind, "display_name": e.display_name, "english_name": e.english_name,
            "owner": ix.actor_ref(e.owner_actor_id) if ix and e.kind not in ("company",) and e.owner_actor_id is not None else None,
            "city": ix.city_ref(e.city_id) if ix and e.kind in ("building", "shop", "region") else None,
            "coordinates": {"x": e.x, "y": e.y} if e.x is not None else None,
            "match_kind": r["match_kind"], "score": r["score"],
        })
    if state is None:
        ctx.add_unavailable("live_entities", "no current state snapshot: only static catalogue names were searched")
    data = ctx.paginate("results", out)
    data["provenance"] = provenance(observed=["results (buildings, shops, companies, cities, regions)"],
                                    definition=["results (products, recipes, building types, techs)"],
                                    derived=[("match_kind/score", "server name index (PRD 13.6)")])
    return data


def specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="get_game_status",
            description=("Liveness, lifecycle state, data freshness, game build compatibility (detected vs expected) and "
                         "observer diagnostics. Always answers, also when the game is not running. Use it first when other "
                         "tools return lifecycle errors." + COMMON_SUFFIX),
            scope="none", kind="status", params={}, handler=get_game_status),
        ToolSpec(
            name="search",
            description=("Resolve names (display names as shown in game, English names, asset names; case- and "
                         "accent-insensitive) to ids such as building:<prefab>@<x>,<y>, product:<asset>, city:<id>. The only "
                         "tool that does fuzzy matching; other tools need an exact id or name." + COMMON_SUFFIX),
            scope="state", kind="static_live", list_tool=True, fields_param=False,
            params={"query": {"type": "string", "maxLength": 200},
                    "kinds": {"type": "array", "items": {"type": "string", "enum": SEARCH_KINDS}, "maxItems": 9},
                    "owner": {"type": "string", "description": "player, ai, all or a company id/name"}},
            required=("query",), handler=search),
    ]
