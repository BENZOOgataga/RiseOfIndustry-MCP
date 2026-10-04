using System.Collections.Generic;

#pragma warning disable IDE1006

namespace RoiMcp.Observer.Dto
{
    [Envelope, Doc("heartbeat.json data (PRD 11.5). Lifecycle and diagnostics only, no world data.")]
    public sealed class HeartbeatData
    {
        [EnumValues("starting", "menu", "loading", "ready", "unsupported_build", "disabled", "faulted")] public string state;
        [Nullable] public string state_reason;
        public bool paused;
        [Nullable] public int? speed_level;
        [Nullable] public float? time_scale;
        [Nullable] public string game_date;
        [Nullable] public int? game_day;
        public string main_thread_last_tick_utc;
        public long frame_count;
        [Nullable] public string scene;
        [Nullable] public string world_session;
        [Nullable] public string module_id;
        [Nullable] public string module_name;
        [Nullable] public string language;
        [EnumValues("verified", "unsupported_build", "pending")] public string compatibility;
        [Nullable] public GameInfoDto detected_game;
        public GameInfoDto expected_game;
        [Nullable] public ReflectionSelfCheckDto reflection_self_check;
        public FamiliesDto families;
        [Nullable] public CaptureStatsDto last_capture;
        public double effective_interval_s;
        public bool degraded;
        public bool optional_sections_suppressed;
        public List<DisabledSectionDto> disabled_sections;
        public int id_collisions;
        public FrameStatsDto frame_stats;
        public RefreshNoncesDto refresh_seen;
        public RefreshNoncesDto refresh_served;
        public int publish_failures;
        public int errors_last_hour;
        public int log_dropped;
        public string observer_version;
        public string exchange_dir;
        [Nullable] public string cwd;
        public bool kill_switch;
        public bool config_enabled;
        public List<string> config_warnings;
        [Nullable] public bool? active_actor_differs;
        [Nullable] public SaveNameDto save_name;
        [Nullable] public RuntimeConstantsDto runtime_constants;
        public string written_utc;
    }

    [Envelope]
    public sealed class SaveNameDto
    {
        [Nullable] public string value;
        [Nullable] public string reason;
    }

    [Envelope, Doc("Runtime constants recorded for validation gate E1.")]
    public sealed class RuntimeConstantsDto
    {
        /// <summary>Copy with a new days delta (the published instance is never mutated).</summary>
        public RuntimeConstantsDto WithMaxDaysDelta(int value)
        {
            var c = (RuntimeConstantsDto)MemberwiseClone();
            c.max_days_delta_per_frame = value;
            return c;
        }

        [Nullable] public float? seconds_per_day;
        [Nullable] public float[] speed_levels;
        [Nullable] public bool? disable_with_mods;
        public bool is_debug_build;
        [Nullable] public string application_version;
        [Nullable] public int? max_days_delta_per_frame;
        [Nullable] public List<string> network_names;
        [Nullable] public int? world_size;
        [Nullable] public string game_version_string;
    }

    [Envelope]
    public sealed class ReflectionSelfCheckDto
    {
        public int total;
        public int resolved;
        public int type_mismatch;
        public int missing;
        public List<ReflectionEntryDto> problems;
    }

    [Envelope]
    public sealed class ReflectionEntryDto
    {
        public string entry;
        [EnumValues("missing", "type_mismatch")] public string problem;
        [Nullable] public string actual_type;
        public List<string> dependent_sections;
    }

    [Envelope]
    public sealed class FamilyStatusDto
    {
        public long seq;
        [Nullable] public string last_published_utc;
        [Nullable] public string last_verified_utc;
        public long size_bytes;
        [Nullable] public string content_hash;
        [Nullable] public string world_session;
    }

    [Envelope]
    public sealed class FamiliesDto
    {
        public FamilyStatusDto @static;
        public FamilyStatusDto state;
        public FamilyStatusDto history;
    }

    [Envelope]
    public sealed class CaptureStatsDto
    {
        [EnumValues("static", "state", "history")] public string family;
        public string utc;
        public double main_thread_ms;
        public int slices;
        public double max_slice_ms;
        public long alloc_bytes_approx;
        public int gc_count_delta;
        public long size_bytes;
        public double serialize_ms;
    }

    [Envelope]
    public sealed class DisabledSectionDto
    {
        public string section;
        [Nullable] public string error_signature;
        [EnumValues("failed_repeatedly", "reflection_missing", "over_budget", "optional_suppressed")] public string reason;
    }

    [Envelope]
    public sealed class FrameStatsDto
    {
        public double window_s;
        public int frames;
        [Nullable] public double? frame_ms_p50;
        [Nullable] public double? frame_ms_p95;
        [Nullable] public double? frame_ms_p99;
        [Nullable] public double? frame_ms_avg;
        public int frames_over_50ms;
        [Nullable] public double? observer_ms_p99;
        public double observer_ms_max;

        [Doc("Largest observer tick in the window that had no garbage collection running inside it")]
        public double observer_ms_max_without_gc;

        [Doc("Observer ticks in the window during which a garbage collection ran (its stop-the-world pause is included in observer_ms_*, whichever thread triggered it)")]
        public int observer_ticks_with_gc;
    }

    [Envelope]
    public sealed class RefreshNoncesDto
    {
        [Nullable] public long? state;
        [Nullable] public long? history;
        [Nullable] public long? @static;
    }
}
