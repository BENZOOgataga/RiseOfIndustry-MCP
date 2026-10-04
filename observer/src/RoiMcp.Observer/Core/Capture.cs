using System;
using System.Collections.Generic;
using System.Diagnostics;
using RoiMcp.Observer.Diagnostics;
using RoiMcp.Observer.Dto;

namespace RoiMcp.Observer.Core
{
    /// <summary>
    /// One snapshot section. Run() is an iterator executed on the main thread in budgeted slices: it yields
    /// whenever ctx.ShouldYield is true. A section must assign its result into the data object only as its
    /// very last step, so a failed section never leaves partial data (PRD 8.6).
    /// </summary>
    public interface ISection
    {
        string Name { get; }
        bool Optional { get; }
        IEnumerator<bool> Run(SectionContext ctx);
    }

    public sealed class SectionContext
    {
        private readonly Stopwatch _frameWatch;
        private double _frameBudgetMs;

        public SectionContext(Stopwatch frameWatch)
        {
            _frameWatch = frameWatch;
        }

        public object Data;
        public CaptureOptions Options;
        public int Items;
        public int ItemsVanished;
        public List<WarningDto> Warnings;

        internal void SetFrameBudget(double ms) { _frameBudgetMs = ms; }

        /// <summary>True when the current frame slice has used its budget.</summary>
        public bool ShouldYield { get { return _frameWatch.Elapsed.TotalMilliseconds >= _frameBudgetMs; } }

        public void Warn(string code, string detail)
        {
            if (Warnings.Count < 100) Warnings.Add(new WarningDto { code = code, detail = detail });
        }

        /// <summary>Items skipped because their own read threw (PRD 8.6: every item handler is isolated).</summary>
        public int ItemsFailed;

        /// <summary>Records one failed item: the item is skipped, the section continues.</summary>
        public void ItemFailed(string section, Exception e)
        {
            ItemsFailed++;
            Diagnostics.Log.Error("capture", Diagnostics.Log.Signature("item." + section, e), "item skipped in " + section + ": " + e.GetType().Name + ": " + e.Message);
            if (ItemsFailed <= 5) Warn("item_failed", section + ": " + e.GetType().Name);
        }
    }

    public sealed class CaptureOptions
    {
        public bool IncludeAiBuildingDetail;
        public bool IncludeAiRoutes;
        public bool IncludeRoutePaths;
        public bool OptionalSuppressed;
        public bool UiLabelValidated;
        public string WorldSession;
    }

    /// <summary>Tracks section failures across captures within one world session (PRD 8.6).</summary>
    public sealed class SectionHealth
    {
        public const int FailuresBeforeDisable = 3;

        private readonly Dictionary<string, int> _consecutiveFailures = new Dictionary<string, int>(StringComparer.Ordinal);
        private readonly Dictionary<string, DisabledSectionDto> _disabled = new Dictionary<string, DisabledSectionDto>(StringComparer.Ordinal);

        public void Reset()
        {
            _consecutiveFailures.Clear();
            _disabled.Clear();
        }

        public bool IsDisabled(string section)
        {
            return _disabled.ContainsKey(section);
        }

        public DisabledSectionDto Get(string section)
        {
            DisabledSectionDto d;
            return _disabled.TryGetValue(section, out d) ? d : null;
        }

        public void DisableForReflection(string section, string entry)
        {
            if (!_disabled.ContainsKey(section))
                _disabled[section] = new DisabledSectionDto { section = section, reason = "reflection_missing", error_signature = entry };
        }

        public void RecordSuccess(string section)
        {
            _consecutiveFailures[section] = 0;
        }

        /// <summary>Returns true when this failure disabled the section.</summary>
        public bool RecordFailure(string section, string signature)
        {
            int n;
            _consecutiveFailures.TryGetValue(section, out n);
            n++;
            _consecutiveFailures[section] = n;
            if (n >= FailuresBeforeDisable && !_disabled.ContainsKey(section))
            {
                _disabled[section] = new DisabledSectionDto { section = section, reason = "failed_repeatedly", error_signature = signature };
                return true;
            }
            return false;
        }

        public List<DisabledSectionDto> Snapshot()
        {
            return new List<DisabledSectionDto>(_disabled.Values);
        }
    }

    /// <summary>Supplies the current absolute game day and frame count to the runner.</summary>
    public interface ICaptureClock
    {
        int GameDay();
        long FrameCount();
    }

    /// <summary>
    /// Runs the sections of one capture (state, static or history) across frames within the per-frame budget.
    /// Every MoveNext is isolated with try/catch; exceptions never propagate into Unity.
    /// </summary>
    public sealed class CaptureRunner
    {
        private readonly List<ISection> _sections;
        private readonly SectionHealth _health;
        private readonly ICaptureClock _clock;
        private readonly SectionContext _ctx;
        private readonly Stopwatch _sectionWatch = new Stopwatch();
        private readonly double _ceilingMs;

        private int _index = -1;
        private IEnumerator<bool> _current;
        private SectionStatusDto _currentStatus;
        private bool _started;

        public CaptureFamily Family { get; private set; }
        public object Data { get; private set; }
        public Dictionary<string, SectionStatusDto> Sections { get; private set; }
        public List<WarningDto> Warnings { get; private set; }
        public bool Done { get; private set; }
        public double MainThreadMs { get; private set; }
        public int Slices { get; private set; }
        public double MaxSliceMs { get; private set; }
        public int GameDayStart { get; private set; }
        public int GameDayEnd { get; private set; }
        public long FrameStart { get; private set; }
        public long FrameEnd { get; private set; }
        public DateTime UtcStart { get; private set; }
        public DateTime UtcEnd { get; private set; }
        public long AllocStart { get; private set; }
        public int GcStart { get; private set; }
        public long? ServesNonce;
        public bool ReadyEntry;

        public CaptureRunner(CaptureFamily family, object data, List<ISection> sections, SectionHealth health,
            ICaptureClock clock, CaptureOptions options, Stopwatch frameWatch, double ceilingMs)
        {
            Family = family;
            Data = data;
            _sections = sections;
            _health = health;
            _clock = clock;
            _ceilingMs = ceilingMs;
            Sections = new Dictionary<string, SectionStatusDto>(StringComparer.Ordinal);
            Warnings = new List<WarningDto>();
            _ctx = new SectionContext(frameWatch) { Data = data, Options = options, Warnings = Warnings };
        }

        public bool Consistent
        {
            get
            {
                foreach (var s in Sections.Values)
                    if (s.status == "ok" && s.game_day != GameDayStart) return false;
                return GameDayStart == GameDayEnd;
            }
        }

        /// <summary>
        /// Runs until the frame budget (measured by the shared frame stopwatch) is used or the capture is done.
        /// </summary>
        public void Step(double frameBudgetMs)
        {
            if (Done) return;
            var slice = Stopwatch.StartNew();
            _ctx.SetFrameBudget(frameBudgetMs);
            if (!_started)
            {
                _started = true;
                UtcStart = DateTime.UtcNow;
                GameDayStart = SafeDay();
                FrameStart = SafeFrame();
                AllocStart = GC.GetTotalMemory(false);
                GcStart = GC.CollectionCount(0);
            }
            try
            {
                while (!_ctx.ShouldYield)
                {
                    if (_current == null)
                    {
                        if (!StartNextSection()) break;
                        continue;
                    }
                    bool more;
                    _sectionWatch.Start();
                    try
                    {
                        FaultPoint.Hit("section:" + _sections[_index].Name);
                        more = _current.MoveNext();
                    }
                    catch (Exception e)
                    {
                        _sectionWatch.Stop();
                        FailCurrent(e);
                        continue;
                    }
                    _sectionWatch.Stop();
                    if (!more) CompleteCurrent();
                }
            }
            finally
            {
                slice.Stop();
                double ms = slice.Elapsed.TotalMilliseconds;
                MainThreadMs += ms;
                Slices++;
                if (ms > MaxSliceMs) MaxSliceMs = ms;
                if (Done)
                {
                    UtcEnd = DateTime.UtcNow;
                    GameDayEnd = SafeDay();
                    FrameEnd = SafeFrame();
                }
            }
        }

        /// <summary>Abandons the capture (world unloaded): no data is published.</summary>
        public void Abandon()
        {
            try
            {
                if (_current != null) _current.Dispose();
            }
            catch (Exception)
            {
                // Disposal of an iterator over a destroyed world must never escape.
            }
            _current = null;
            Done = true;
        }

        private bool StartNextSection()
        {
            _index++;
            if (_index >= _sections.Count)
            {
                Done = true;
                return false;
            }
            var section = _sections[_index];
            var status = new SectionStatusDto { status = "ok", game_day = SafeDay(), frame_start = SafeFrame() };
            Sections[section.Name] = status;
            if (_health.IsDisabled(section.Name))
            {
                var d = _health.Get(section.Name);
                status.status = "disabled";
                status.reason = d.reason + (d.error_signature != null ? ": " + d.error_signature : "");
                return true;
            }
            if (section.Optional && !IsOptionalEnabled(section.Name))
            {
                status.status = "skipped";
                status.reason = _ctx.Options.OptionalSuppressed ? "optional_suppressed_after_ceiling_breaches" : "optional_off";
                return true;
            }
            if (section.Optional && MainThreadMs > _ceilingMs)
            {
                status.status = "over_budget";
                status.reason = "capture_ceiling_ms reached before optional section";
                return true;
            }
            _ctx.Items = 0;
            _ctx.ItemsVanished = 0;
            _sectionWatch.Reset();
            _currentStatus = status;
            try
            {
                _current = section.Run(_ctx);
            }
            catch (Exception e)
            {
                FailCurrent(e);
            }
            return true;
        }

        private bool IsOptionalEnabled(string name)
        {
            var o = _ctx.Options;
            if (o.OptionalSuppressed) return false;
            switch (name)
            {
                case "buildings_ai_detail": return o.IncludeAiBuildingDetail;
                case "routes_ai": return o.IncludeAiRoutes;
                case "route_paths": return o.IncludeRoutePaths;
                default: return true;
            }
        }

        private void CompleteCurrent()
        {
            var name = _sections[_index].Name;
            _currentStatus.items = _ctx.Items;
            _currentStatus.items_vanished = _ctx.ItemsVanished;
            _currentStatus.frame_end = SafeFrame();
            _currentStatus.main_thread_ms = Math.Round(_sectionWatch.Elapsed.TotalMilliseconds, 3);
            int endDay = SafeDay();
            if (endDay != _currentStatus.game_day)
            {
                // A day ticked during this section: record the end day, consistency is lost.
                _currentStatus.game_day = endDay;
                GameDayEnd = endDay;
            }
            _health.RecordSuccess(name);
            DisposeCurrent();
        }

        private void FailCurrent(Exception e)
        {
            var name = _sections[_index].Name;
            var sig = Log.Signature("section." + name, e);
            _currentStatus.status = "failed";
            _currentStatus.reason = e.GetType().Name;
            _currentStatus.frame_end = SafeFrame();
            _currentStatus.main_thread_ms = Math.Round(_sectionWatch.Elapsed.TotalMilliseconds, 3);
            Log.Error("capture", sig, "section " + name + " failed: " + e.GetType().Name + ": " + e.Message);
            if (_health.RecordFailure(name, sig))
                Log.Warn("capture", "section " + name + " disabled until the next world session after repeated failures");
            DisposeCurrent();
        }

        private void DisposeCurrent()
        {
            try
            {
                if (_current != null) _current.Dispose();
            }
            catch (Exception)
            {
                // ignore
            }
            _current = null;
            _currentStatus = null;
        }

        private int SafeDay()
        {
            try
            {
                return _clock.GameDay();
            }
            catch (Exception)
            {
                return 0;
            }
        }

        private long SafeFrame()
        {
            try
            {
                return _clock.FrameCount();
            }
            catch (Exception)
            {
                return 0;
            }
        }
    }
}
