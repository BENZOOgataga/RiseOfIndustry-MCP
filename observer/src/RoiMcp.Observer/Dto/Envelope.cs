using System.Collections.Generic;

// DTO fields use the exact snake_case wire names on purpose: the JSON produced by the observer is the
// contract consumed by the MCP server (see schemas/ and docs/SNAPSHOT-FORMAT.md).
#pragma warning disable IDE1006

namespace RoiMcp.Observer.Dto
{
    [Envelope, Doc("Game build facts. In envelopes these are the detected values of the running game.")]
    public sealed class GameInfoDto
    {
        [Doc("major.minor.revision")] public string version;
        [Doc("build + suffix, e.g. 0507b")] public string build;
        public string commit;
        public int savegame_version;
        [Nullable, Doc("SHA-256 (upper-case hex) of the loaded Assembly-CSharp.dll; null until computed")] public string assembly_sha256;
        [Nullable] public string release;
    }

    [Envelope]
    public sealed class CapturedDto
    {
        public string utc_start;
        public string utc_end;
        public int game_day_start;
        public int game_day_end;
        [Doc("Y<year>-<MM>-<DD>, 30-day months")] public string game_date;
        [Doc("[first frame, last frame]")] public long[] frames;
        public double main_thread_ms;
        public int slices;
        public double max_slice_ms;
        [Doc("true when every section was read on the same game day")] public bool consistent;
    }

    [Envelope]
    public sealed class StaticRefDto
    {
        public long seq;
        public string content_hash;
    }

    [Envelope]
    public sealed class SectionStatusDto
    {
        [EnumValues("ok", "disabled", "failed", "skipped", "over_budget")] public string status;
        [Nullable] public string reason;
        public int game_day;
        public long frame_start;
        public long frame_end;
        public int items;
        public int items_vanished;
        public double main_thread_ms;
    }

    [Envelope]
    public sealed class WarningDto
    {
        public string code;
        [Nullable] public string detail;
    }

    [Envelope, Doc("Common envelope of heartbeat/static/state/history files (PRD 11.2).")]
    public sealed class EnvelopeDto
    {
        [EnumValues("roi-mcp/heartbeat", "roi-mcp/static", "roi-mcp/state", "roi-mcp/history")] public string schema;
        public string schema_version;
        public string observer_version;
        [EnumValues("verified", "unsupported_build", "pending"),
         Doc("static/state/history: always verified. heartbeat: pending until the version gate has run at the first READY of this process")]
        public string compatibility;
        [Doc("detected game facts; heartbeat before the first READY: null")] [Nullable] public GameInfoDto game;
        public int pid;
        [Nullable] public string world_session;
        public long seq;
        [Nullable] public string content_hash;
        public string written_utc;
        [Nullable] public CapturedDto captured;
        [Nullable] public StaticRefDto static_ref;
        [Nullable] public Dictionary<string, SectionStatusDto> sections;
        public List<WarningDto> warnings;
    }
}
