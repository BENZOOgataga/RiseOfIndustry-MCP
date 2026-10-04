using System;

namespace RoiMcp.Observer.Core
{
    /// <summary>
    /// Capture scheduling (PRD 8.4): intervals in unscaled real time, adaptive back-off, single flight,
    /// refresh-request coalescing per scope. Pure logic, driven by the main thread.
    /// </summary>
    public sealed class Scheduler
    {
        public const double BackoffCapS = 120;
        public const int BreachesBeforeSuppression = 3;

        private static readonly CaptureFamily[] AllFamilies = { CaptureFamily.State, CaptureFamily.History, CaptureFamily.Static };

        private ObserverConfig _config = ObserverConfig.Default();

        private bool _staticPending;
        private bool _statePending;
        private bool _historyPending;

        private double _lastStateStart = double.NegativeInfinity;
        private int _lastStateDay = int.MinValue;
        private double _lastAnyRefreshCapture = double.NegativeInfinity;
        private int _lastHistoryMonthKey = int.MinValue;
        private string _lastModuleId;

        private int _backoffFactor = 1;
        private int _consecutiveBreaches;
        private DateTime _lastBreachUtc = DateTime.MinValue;

        /// <summary>Latest nonce seen per scope that has not yet been assigned to a capture.</summary>
        private readonly RefreshNonces _seen = new RefreshNonces();

        /// <summary>Highest nonce per scope already covered by a started capture.</summary>
        private readonly RefreshNonces _covered = new RefreshNonces();

        public bool OptionalSuppressed { get; private set; }
        public double LastCaptureMainThreadMs { get; private set; }
        public bool Degraded { get; private set; }

        public void SetConfig(ObserverConfig c)
        {
            _config = c ?? ObserverConfig.Default();
        }

        public double EffectiveIntervalS(bool paused)
        {
            double configured = paused ? _config.PausedIntervalS : _config.RunningIntervalS;
            double adaptive = 100.0 * LastCaptureMainThreadMs / 1000.0;
            double v = Math.Max(configured, adaptive);
            // After a ceiling breach the next effective interval doubles (and keeps doubling on further
            // breaches), capped at 120 s but never below the un-backed-off interval (PRD 8.4).
            if (_backoffFactor > 1) v = Math.Max(v, Math.Min(BackoffCapS, v * _backoffFactor));
            return v;
        }

        /// <summary>Resets per-world-session state and queues the ready-entry captures (static, state, history).</summary>
        public void OnEnteredReady(int gameDay, string moduleId)
        {
            _staticPending = true;
            _statePending = true;
            _historyPending = true;
            _lastStateStart = double.NegativeInfinity;
            _lastStateDay = int.MinValue;
            _lastHistoryMonthKey = MonthKey(gameDay);
            _lastModuleId = moduleId;
            _backoffFactor = 1;
            _consecutiveBreaches = 0;
            OptionalSuppressed = false;
            Degraded = false;
        }

        public void OnLeftReady()
        {
            _staticPending = false;
            _statePending = false;
            _historyPending = false;
        }

        /// <summary>Records nonces read from refresh-request.json (any state).</summary>
        public void ObserveNonces(RefreshNonces n)
        {
            if (n == null) return;
            foreach (CaptureFamily f in AllFamilies)
            {
                var v = n.Get(f);
                if (v.HasValue && (!_seen.Get(f).HasValue || v.Value > _seen.Get(f).Value)) _seen.Set(f, v);
            }
        }

        public RefreshNonces Seen { get { return _seen.Clone(); } }

        /// <summary>
        /// Nonces that arrive while unsupported_build, disabled or faulted are never served (PRD 11.6):
        /// mark them covered without serving them.
        /// </summary>
        public void DiscardPendingNonces()
        {
            foreach (CaptureFamily f in AllFamilies)
                if (RefreshPending(f)) _covered.Set(f, _seen.Get(f));
        }

        private bool RefreshPending(CaptureFamily f)
        {
            var s = _seen.Get(f);
            var c = _covered.Get(f);
            return s.HasValue && (!c.HasValue || s.Value > c.Value);
        }

        /// <summary>
        /// Chooses the next capture to start, or null. Ready-entry captures go first (static, state, history);
        /// refresh-driven captures are served state first, then history, then static.
        /// </summary>
        public CaptureFamily? Next(double now, bool paused, int gameDay, string moduleId)
        {
            if (_lastModuleId != moduleId)
            {
                _lastModuleId = moduleId;
                _staticPending = true;
            }
            if (MonthKey(gameDay) != _lastHistoryMonthKey) _historyPending = true;

            if (_staticPending) return CaptureFamily.Static;
            if (_statePending) return CaptureFamily.State;
            if (_historyPending) return CaptureFamily.History;

            bool gapOk = now - _lastAnyRefreshCapture >= _config.MinGapS;
            // Refresh-driven state captures respect min_gap_s and, while backing off, the backed-off interval
            // (PRD 11.6, 13.2a: a fresh call may then time out).
            double refreshGap = _backoffFactor > 1 ? EffectiveIntervalS(paused) : _config.MinGapS;
            if (gapOk && RefreshPending(CaptureFamily.State) && now - _lastStateStart >= refreshGap) return CaptureFamily.State;
            if (gapOk && RefreshPending(CaptureFamily.History)) return CaptureFamily.History;
            if (gapOk && RefreshPending(CaptureFamily.Static)) return CaptureFamily.Static;

            double interval = EffectiveIntervalS(paused);
            if (now - _lastStateStart >= interval)
            {
                if (paused) return CaptureFamily.State;
                if (gameDay != _lastStateDay) return CaptureFamily.State;
            }
            return null;
        }

        /// <summary>Marks a capture as started; returns the nonce it will serve when published (null if none).</summary>
        public long? OnCaptureStarted(CaptureFamily f, double now, int gameDay, bool readyEntry)
        {
            long? serve = null;
            var seen = _seen.Get(f);
            if (seen.HasValue)
            {
                var covered = _covered.Get(f);
                if (!covered.HasValue || seen.Value > covered.Value)
                {
                    _covered.Set(f, seen);
                    serve = seen;
                }
            }
            switch (f)
            {
                case CaptureFamily.Static: _staticPending = false; break;
                case CaptureFamily.State:
                    _statePending = false;
                    _lastStateStart = now;
                    _lastStateDay = gameDay;
                    break;
                case CaptureFamily.History:
                    _historyPending = false;
                    _lastHistoryMonthKey = MonthKey(gameDay);
                    break;
            }
            if (serve.HasValue) _lastAnyRefreshCapture = now;
            return serve;
        }

        /// <summary>
        /// Back-off bookkeeping after a capture finished (PRD 8.4). capture_ceiling_ms is defined per state
        /// capture (PERF-2): static and history captures do not count against it.
        /// </summary>
        public void OnCaptureFinished(CaptureFamily f, double mainThreadMs, DateTime utcNow)
        {
            if (f != CaptureFamily.State) return;
            LastCaptureMainThreadMs = mainThreadMs;
            if (mainThreadMs > _config.CaptureCeilingMs)
            {
                _consecutiveBreaches++;
                _lastBreachUtc = utcNow;
                if (_backoffFactor < 64) _backoffFactor *= 2;
                Degraded = true;
                if (_consecutiveBreaches >= BreachesBeforeSuppression) OptionalSuppressed = true;
            }
            else
            {
                _consecutiveBreaches = 0;
                _backoffFactor = 1;
                if ((utcNow - _lastBreachUtc).TotalMinutes > 1) Degraded = OptionalSuppressed;
            }
        }

        public int ConsecutiveBreaches { get { return _consecutiveBreaches; } }

        public static int MonthKey(int gameDay)
        {
            if (gameDay == int.MinValue) return int.MinValue;
            int y, m, d;
            Baseline.FromDayCount(gameDay, out y, out m, out d);
            return y * 12 + m;
        }
    }
}
