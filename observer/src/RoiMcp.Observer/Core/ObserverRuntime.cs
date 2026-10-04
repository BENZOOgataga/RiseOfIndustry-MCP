using System;
using System.Collections.Generic;
using System.Diagnostics;
using RoiMcp.Observer.Diagnostics;
using RoiMcp.Observer.Dto;

namespace RoiMcp.Observer.Core
{
    /// <summary>
    /// Main-thread orchestration, called once per frame from the mod's LateUpdate: lifecycle, version gate,
    /// reflection self-check, scheduling and time-sliced captures. Every exception is caught here; nothing is
    /// ever rethrown into Unity (PRD 8.6).
    /// </summary>
    public sealed class ObserverRuntime
    {
        private const int TickFaultLimit = 300;
        private const double SlowTickLogMs = 20.0;
        private const double LongFrameLogMs = 1000.0;
        private const int SlowTickLogIntervalMs = 10000;
        private static readonly string[] PhaseNames = { "control", "lifecycle", "session", "capture", "status", "per_second" };

        private readonly IGameReader _reader;
        private readonly Hub _hub;
        private readonly Lifecycle _lifecycle = new Lifecycle();
        private readonly Scheduler _scheduler = new Scheduler();
        private readonly SectionHealth _health = new SectionHealth();
        private readonly FrameStats _frameStats = new FrameStats();
        private readonly Stopwatch _frameWatch = new Stopwatch();
        private readonly ControlState _control = new ControlState();
        private readonly SessionStatus _session = new SessionStatus();

        private ObserverConfig _config = ObserverConfig.Default();
        private int _configVersion = -1;
        private CaptureRunner _capture;
        private string _worldSession;
        private bool _gatePassed;
        private bool _gateFailed;
        private bool _assemblyHashRequested;
        private double _lastObserverMs;
        private bool _lastTickGc;
        private int _gcAtLastTickEnd = -1;
        private readonly long[] _phaseEnd = new long[6];
        private int _nextSlowLogTick;
        private int _slowTicksSuppressed;
        private double _nextSecond;
        private int _consecutiveTickFaults;
        private string _sceneName;
        private bool _sceneDirty = true;
        private long _sceneCheckedFrame = long.MinValue;
        private int _lastStatusDay = int.MinValue;

        public ObserverRuntime(IGameReader reader, Hub hub)
        {
            _reader = reader;
            _hub = hub;
        }

        public ObserverState State { get { return _lifecycle.State; } }

        /// <summary>Scene event handler entry (main thread): abandon the world immediately.</summary>
        public void OnSceneChanged(string eventName)
        {
            try
            {
                FaultPoint.Hit("scene_handler");
                _sceneDirty = true;
                _lifecycle.NotifySceneChanged();
                AbandonWorld("scene event: " + eventName);
                // The next tick can be seconds away while the game loads; publish the state change now.
                if (_lifecycle.LeaveReadyForSceneChange())
                    _hub.UpdateStatus(s => { s.State = ObserverState.Loading; });
            }
            catch (Exception e)
            {
                Log.Error("lifecycle", Log.Signature("scene", e), e.Message);
            }
        }

        public void Tick()
        {
            for (int i = 0; i < _phaseEnd.Length; i++) _phaseEnd[i] = -1;
            int gcBefore = GC.CollectionCount(0);
            NoteLongFrame(gcBefore);
            _frameWatch.Reset();
            _frameWatch.Start();
            try
            {
                TickInner();
                _consecutiveTickFaults = 0;
            }
            catch (Exception e)
            {
                _consecutiveTickFaults++;
                Log.Error("runtime", Log.Signature("tick", e), e.GetType().Name + ": " + e.Message);
                if (_capture != null)
                {
                    _capture.Abandon();
                    _capture = null;
                }
                if (_consecutiveTickFaults >= TickFaultLimit && _lifecycle.State != ObserverState.Faulted)
                {
                    _lifecycle.MarkFaulted();
                    SafeDrop();
                    Log.Error("runtime", "runtime.faulted", "observer entered faulted state after repeated errors; no further game reads");
                    _hub.UpdateStatus(s => { s.State = ObserverState.Faulted; s.StateReason = "repeated_runtime_errors"; });
                }
            }
            finally
            {
                _frameWatch.Stop();
                _lastObserverMs = _frameWatch.Elapsed.TotalMilliseconds;
                // A collection that runs while this tick is on the stack stops the main thread and is timed as
                // observer work, whichever thread triggered it. Flag it so the cost is attributed correctly.
                _gcAtLastTickEnd = GC.CollectionCount(0);
                _lastTickGc = _gcAtLastTickEnd != gcBefore;
                if (_lastObserverMs >= SlowTickLogMs) LogSlowTick();
            }
        }

        /// <summary>
        /// Logs a frame of a second or more (a visible freeze) with what ran during it: garbage collections since
        /// the previous observer tick and the previous tick's own time. Lets a freeze be attributed to the game, a
        /// collection or the observer (E5). Costs one integer read per frame.
        /// </summary>
        private void NoteLongFrame(int gcNow)
        {
            double frameMs;
            try
            {
                frameMs = _reader.UnscaledDeltaMs();
            }
            catch (Exception)
            {
                return;
            }
            if (frameMs < LongFrameLogMs || _gcAtLastTickEnd < 0) return;
            int gcs = gcNow - _gcAtLastTickEnd;
            Log.Warn("runtime", "long frame " + Num(frameMs) + " ms (state " + _lifecycle.State + "); garbage collections since the previous observer tick: " +
                                gcs + "; previous observer tick " + Num(_lastObserverMs) + " ms" + (_lastTickGc ? " (GC inside)" : ""));
        }

        /// <summary>Marks the end of a tick phase (elapsed stopwatch ticks; no allocation).</summary>
        private void Phase(int index)
        {
            _phaseEnd[index] = _frameWatch.ElapsedTicks;
        }

        /// <summary>Rate-limited diagnosis of a slow tick: where the time went and whether a GC ran inside it.</summary>
        private void LogSlowTick()
        {
            int nowMs = Environment.TickCount;
            if (_nextSlowLogTick != 0 && unchecked(nowMs - _nextSlowLogTick) < 0)
            {
                _slowTicksSuppressed++;
                return;
            }
            _nextSlowLogTick = unchecked(nowMs + SlowTickLogIntervalMs);
            var sb = new System.Text.StringBuilder(160);
            sb.Append("slow observer tick ").Append(_lastObserverMs.ToString("0.0", System.Globalization.CultureInfo.InvariantCulture))
              .Append(" ms (state ").Append(_lifecycle.State).Append(", GC during tick: ").Append(_lastTickGc ? "yes" : "no").Append("); phases ms:");
            long prev = 0;
            double toMs = 1000.0 / Stopwatch.Frequency;
            for (int i = 0; i < _phaseEnd.Length; i++)
            {
                if (_phaseEnd[i] < 0) continue;
                sb.Append(' ').Append(PhaseNames[i]).Append(' ')
                  .Append(((_phaseEnd[i] - prev) * toMs).ToString("0.00", System.Globalization.CultureInfo.InvariantCulture));
                prev = _phaseEnd[i];
            }
            if (_slowTicksSuppressed > 0) sb.Append("; ").Append(_slowTicksSuppressed).Append(" earlier slow tick(s) not logged");
            _slowTicksSuppressed = 0;
            Log.Warn("runtime", sb.ToString());
        }

        private void TickInner()
        {
            FaultPoint.Hit("tick");
            double now = _reader.UnscaledTime();
            long frame = _reader.FrameCount();
            _frameStats.Add(now, _reader.UnscaledDeltaMs(), _lastObserverMs, _lastTickGc);
            _hub.ReadControl(_control);

            if (_control.ConfigVersion != _configVersion)
            {
                _configVersion = _control.ConfigVersion;
                _config = _control.Config ?? ObserverConfig.Default();
                _scheduler.SetConfig(_config);
            }
            if (_control.PublisherFaulted && _lifecycle.State != ObserverState.Faulted)
            {
                _lifecycle.MarkFaulted();
                SafeDrop();
                _hub.UpdateStatus(s => { s.StateReason = "publisher_faulted"; });
            }
            if (!_assemblyHashRequested)
            {
                _assemblyHashRequested = true;
                string path = null;
                try
                {
                    path = _reader.AssemblyPath();
                }
                catch (Exception e)
                {
                    Log.Error("version", Log.Signature("assembly_path", e), e.Message);
                }
                _hub.RequestAssemblyHash(path ?? "");
            }

            _scheduler.ObserveNonces(_control.Nonces);
            Phase(0);
            bool disabled = _control.KillSwitch || !_config.Enabled;
            var state = _lifecycle.State;
            if (state == ObserverState.Faulted || state == ObserverState.UnsupportedBuild)
            {
                _scheduler.DiscardPendingNonces();
                _hub.Tick(DateTime.UtcNow, frame, state);
                PerSecond(now);
                return;
            }

            // The active scene name is re-read after scene events and otherwise about once a second
            // (Scene.name allocates a string).
            if (_sceneDirty || frame - _sceneCheckedFrame >= 60 || frame < _sceneCheckedFrame)
            {
                _sceneName = _reader.ActiveSceneName();
                _sceneCheckedFrame = frame;
                _sceneDirty = false;
            }
            // Unity reports the game scene as "Game" (the game loads it as "game"): compare case-insensitively.
            bool inGame = string.Equals(_sceneName, "game", StringComparison.OrdinalIgnoreCase);
            var probe = disabled ? new WorldProbe { InGameScene = inGame } : _reader.Probe(inGame);
            var ev = _lifecycle.Step(probe, disabled);
            if (ev == LifecycleEvent.LeftReady) AbandonWorld("left ready");
            if (ev == LifecycleEvent.EnteredReady) EnterReady(probe.GameDay);

            state = _lifecycle.State;
            if (state == ObserverState.Disabled) _scheduler.DiscardPendingNonces();
            Phase(1);

            if (state == ObserverState.Ready && _gatePassed)
            {
                if (_reader.ReadSession(_session))
                {
                    if (_session.GameDay != _lastStatusDay)
                    {
                        _lastStatusDay = _session.GameDay;
                        string date = Baseline.GameDate(_session.Year, _session.Month, _session.Day);
                        int day = _session.GameDay;
                        int maxDelta = _reader.MaxDaysDeltaPerFrame;
                        _hub.UpdateStatus(s =>
                        {
                            s.GameDate = date;
                            s.GameDay = day;
                            // Constants are read on READY entry, usually while paused; keep the delta live.
                            var rc = s.RuntimeConstants;
                            if (rc != null && rc.max_days_delta_per_frame != maxDelta) s.RuntimeConstants = rc.WithMaxDaysDelta(maxDelta);
                        });
                    }
                    Phase(2);
                    RunCaptures(now);
                    Phase(3);
                }
            }
            _hub.Tick(DateTime.UtcNow, frame, _lifecycle.State);
            Phase(4);
            PerSecond(now);
            Phase(5);
        }

        private void PerSecond(double now)
        {
            if (now < _nextSecond) return;
            _nextSecond = now + 1.0;
            var stats = _frameStats.Summarize(now, 60.0);
            var st = _lifecycle.State;
            bool ready = st == ObserverState.Ready && _gatePassed;
            if (ready)
            {
                try
                {
                    _reader.ReadSessionSlow(_session);
                }
                catch (Exception e)
                {
                    Log.Error("runtime", Log.Signature("session_slow", e), e.Message);
                }
            }
            var seen = _scheduler.Seen;
            var disabled = _health.Snapshot();
            double interval = _scheduler.EffectiveIntervalS(_session.Paused);
            bool degraded = _scheduler.Degraded;
            bool suppressed = _scheduler.OptionalSuppressed;
            int collisions = _reader.IdCollisions;
            string scene = _sceneName;
            var session = _session;
            _hub.UpdateStatus(s =>
            {
                s.FrameStats = stats;
                s.RefreshSeen = seen;
                s.DisabledSections = disabled;
                s.EffectiveIntervalS = interval;
                s.Degraded = degraded;
                s.OptionalSuppressed = suppressed;
                s.IdCollisions = collisions;
                s.Scene = scene;
                if (ready)
                {
                    s.Paused = session.Paused;
                    s.SpeedLevel = session.SpeedLevel;
                    s.TimeScale = session.TimeScale;
                    s.ModuleId = session.ModuleId;
                    s.ModuleName = session.ModuleName;
                    s.Language = session.Language;
                    s.ActiveActorDiffers = session.ActiveActorDiffers;
                }
                else
                {
                    s.Paused = false;
                    s.SpeedLevel = null;
                    s.TimeScale = null;
                    s.GameDate = null;
                    s.GameDay = null;
                }
            });
        }

        private void EnterReady(int gameDay)
        {
            _ceilingNoted.Clear();
            _worldSession = Guid.NewGuid().ToString();
            _hub.DropPendingExcept(_worldSession);
            if (!_gatePassed && !_gateFailed)
            {
                if (!_control.AssemblyHashDone)
                {
                    // The hash is computed on the background thread at start-up; wait for it (stay "loading").
                    _lifecycle.NotifySceneChanged();
                    _worldSession = null;
                    _hub.UpdateStatus(s => { s.StateReason = "awaiting_assembly_hash"; });
                    return;
                }
                var facts = _reader.ReadVersionFacts();
                var gate = VersionGate.Evaluate(facts, _control.AssemblySha256);
                var detected = gate.Detected;
                if (!gate.Compatible)
                {
                    _gateFailed = true;
                    _lifecycle.MarkUnsupported();
                    _worldSession = null;
                    SafeDrop();
                    string mismatches = string.Join(", ", gate.Mismatches.ToArray());
                    Log.Warn("version", "unsupported game build (" + mismatches + "): heartbeat only, zero capture for this process");
                    _hub.UpdateStatus(s =>
                    {
                        s.State = ObserverState.UnsupportedBuild;
                        s.StateReason = "version_mismatch: " + mismatches;
                        s.Compatibility = "unsupported_build";
                        s.DetectedGame = detected;
                        s.WorldSession = null;
                    });
                    return;
                }
                _gatePassed = true;
                Log.Info("version", "game build verified: " + detected.version + " " + detected.build + " savegame " + detected.savegame_version);
                _hub.UpdateStatus(s => { s.Compatibility = "verified"; s.DetectedGame = detected; });
            }
            if (_gateFailed) return;

            _health.Reset();
            _reader.BeginWorldSession(_worldSession);
            var missing = new Dictionary<string, List<string>>(StringComparer.Ordinal);
            var selfCheck = _reader.RunReflectionSelfCheck(missing);
            foreach (var kv in missing)
                foreach (var section in kv.Value)
                    _health.DisableForReflection(section, kv.Key);
            Log.Info("reflection", "self-check: " + selfCheck.resolved + "/" + selfCheck.total + " resolved" +
                                   (selfCheck.problems.Count > 0 ? "; problems: " + Describe(selfCheck.problems) : ""));
            RuntimeConstantsDto constants = null;
            try
            {
                constants = _reader.ReadRuntimeConstants();
            }
            catch (Exception e)
            {
                Log.Error("runtime", Log.Signature("constants", e), e.Message);
            }
            _reader.ReadSessionSlow(_session);
            _scheduler.OnEnteredReady(gameDay, _session.ModuleId);
            _lastStatusDay = int.MinValue;
            string session = _worldSession;
            _hub.UpdateStatus(s =>
            {
                s.WorldSession = session;
                s.StateReason = null;
                s.ReflectionSelfCheck = selfCheck;
                s.RuntimeConstants = constants;
            });
            Log.Info("lifecycle", "ready: world session " + session);
        }

        private static string Describe(List<ReflectionEntryDto> problems)
        {
            var parts = new List<string>();
            foreach (var p in problems) parts.Add(p.entry + "=" + p.problem);
            return string.Join(", ", parts.ToArray());
        }

        private void AbandonWorld(string why)
        {
            if (_capture != null)
            {
                _capture.Abandon();
                _capture = null;
                Log.Info("lifecycle", "capture abandoned: " + why);
            }
            _scheduler.OnLeftReady();
            SafeDrop();
            if (_worldSession != null) Log.Info("lifecycle", "world session " + _worldSession + " ended: " + why);
            _worldSession = null;
            _lastStatusDay = int.MinValue;
            _hub.DropPendingExcept(null);
            _hub.UpdateStatus(s => { s.WorldSession = null; s.GameDate = null; s.GameDay = null; });
        }

        private void SafeDrop()
        {
            try
            {
                _reader.DropReferences();
            }
            catch (Exception e)
            {
                Log.Error("lifecycle", Log.Signature("drop", e), e.Message);
            }
        }

        private void RunCaptures(double now)
        {
            if (_capture == null)
            {
                var family = _scheduler.Next(now, _session.Paused, _session.GameDay, _session.ModuleId);
                if (!family.HasValue) return;
                StartCapture(family.Value, now);
                if (_capture == null) return;
            }
            double remaining = _config.FrameBudgetMs - _frameWatch.Elapsed.TotalMilliseconds;
            if (remaining <= 0) return;
            _capture.Step(_config.FrameBudgetMs);
            if (_capture.Done) FinishCapture();
        }

        private void StartCapture(CaptureFamily family, double now)
        {
            List<ISection> sections;
            object data;
            switch (family)
            {
                case CaptureFamily.Static:
                    sections = _reader.StaticSections();
                    data = new StaticData();
                    break;
                case CaptureFamily.History:
                    sections = _reader.HistorySections();
                    data = new HistoryData();
                    break;
                default:
                    sections = _reader.StateSections();
                    data = new StateData();
                    break;
            }
            var options = new CaptureOptions
            {
                IncludeAiBuildingDetail = _config.IncludeAiBuildingDetail,
                IncludeAiRoutes = _config.IncludeAiRoutes,
                IncludeRoutePaths = _config.IncludeRoutePaths,
                OptionalSuppressed = _scheduler.OptionalSuppressed,
                UiLabelValidated = Baseline.UiLabelValidated,
                WorldSession = _worldSession,
            };
            _capture = new CaptureRunner(family, data, sections, _health, _reader, options, _frameWatch, _config.CaptureCeilingMs);
            _capture.ServesNonce = _scheduler.OnCaptureStarted(family, now, _session.GameDay, false);
        }

        private void FinishCapture()
        {
            var c = _capture;
            _capture = null;
            _reader.ReleaseCaptureScratch();
            _scheduler.OnCaptureFinished(c.Family, c.MainThreadMs, DateTime.UtcNow);
            long allocDelta = GC.GetTotalMemory(false) - c.AllocStart;
            var stats = new CaptureStatsDto
            {
                family = Wire(c.Family),
                utc = Baseline.UtcNow(),
                main_thread_ms = Math.Round(c.MainThreadMs, 3),
                slices = c.Slices,
                max_slice_ms = Math.Round(c.MaxSliceMs, 3),
                alloc_bytes_approx = allocDelta,
                gc_count_delta = GC.CollectionCount(0) - c.GcStart,
            };
            int y, m, d;
            Baseline.FromDayCount(c.GameDayEnd, out y, out m, out d);
            var captured = new CapturedDto
            {
                utc_start = Baseline.Utc(c.UtcStart),
                utc_end = Baseline.Utc(c.UtcEnd),
                game_day_start = c.GameDayStart,
                game_day_end = c.GameDayEnd,
                game_date = Baseline.GameDate(y, m, d),
                frames = new[] { c.FrameStart, c.FrameEnd },
                main_thread_ms = stats.main_thread_ms,
                slices = c.Slices,
                max_slice_ms = stats.max_slice_ms,
                consistent = c.Consistent,
            };
            if (!captured.consistent) c.Warnings.Add(new WarningDto { code = "inconsistent_snapshot", detail = "a game day ticked during the capture" });
            _hub.Enqueue(new Publication
            {
                Family = c.Family,
                WorldSession = _worldSession,
                Data = c.Data,
                Captured = captured,
                Sections = c.Sections,
                Warnings = c.Warnings,
                ServesNonce = c.ServesNonce,
                Stats = stats,
            });
            _hub.UpdateStatus(s => { s.LastCapture = stats; });
            if (c.MainThreadMs > _config.CaptureCeilingMs) LogCeilingExceeded(c, stats);
            CaptureSummary(c);
        }

        private readonly HashSet<CaptureFamily> _ceilingNoted = new HashSet<CaptureFamily>();

        /// <summary>
        /// Describes what actually follows a capture above capture_ceiling_ms. The ceiling and its back-off apply to
        /// state captures only (PRD 8.4, PERF-2); static and history captures stay time-sliced and are not backed off.
        /// </summary>
        private void LogCeilingExceeded(CaptureRunner c, CaptureStatsDto stats)
        {
            string head = Wire(c.Family) + " capture took " + Num(c.MainThreadMs) + " ms main-thread in " + c.Slices +
                          " slice(s), max slice " + Num(stats.max_slice_ms) + " ms";
            if (c.Family == CaptureFamily.State)
            {
                string msg = head + " (ceiling " + Num(_config.CaptureCeilingMs) + " ms): next state capture interval raised to " +
                             Num(_scheduler.EffectiveIntervalS(_session.Paused)) + " s";
                if (_scheduler.OptionalSuppressed) msg += "; optional sections suppressed until the next world session";
                Log.Warn("capture", msg);
                return;
            }
            // Once per family per world session; later occurrences show in the per-minute capture summary.
            if (!_ceilingNoted.Add(c.Family)) return;
            Log.Info("capture", head + "; the " + Num(_config.CaptureCeilingMs) + " ms ceiling applies to state captures only, so no back-off" +
                                " (further occurrences this world session are only counted in the capture summary)");
        }

        private static string Num(double v)
        {
            return Math.Round(v, 1).ToString("0.#", System.Globalization.CultureInfo.InvariantCulture);
        }

        private int _summaryCount;
        private double _summaryMaxMs;
        private double _summarySumMs;
        private DateTime _summaryNext = DateTime.MinValue;

        /// <summary>Capture summary at most once per minute (PRD 19).</summary>
        private void CaptureSummary(CaptureRunner c)
        {
            _summaryCount++;
            _summarySumMs += c.MainThreadMs;
            if (c.MainThreadMs > _summaryMaxMs) _summaryMaxMs = c.MainThreadMs;
            var now = DateTime.UtcNow;
            if (now < _summaryNext) return;
            _summaryNext = now.AddMinutes(1);
            Log.Info("capture", "captures: " + _summaryCount + ", avg " + Num(_summarySumMs / Math.Max(1, _summaryCount)) +
                                " ms, max " + Num(_summaryMaxMs) + " ms main-thread");
            _summaryCount = 0;
            _summarySumMs = 0;
            _summaryMaxMs = 0;
        }

        private static string Wire(CaptureFamily f)
        {
            switch (f)
            {
                case CaptureFamily.State: return "state";
                case CaptureFamily.History: return "history";
                default: return "static";
            }
        }
    }
}
