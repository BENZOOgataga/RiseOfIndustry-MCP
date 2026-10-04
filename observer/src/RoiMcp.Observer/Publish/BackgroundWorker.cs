using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Threading;
using RoiMcp.Observer.Core;
using RoiMcp.Observer.Diagnostics;
using RoiMcp.Observer.Dto;
using RoiMcp.Observer.Io;

namespace RoiMcp.Observer.Publish
{
    /// <summary>
    /// The single background thread (PRD 8.3): writes the heartbeat every second, serializes and publishes
    /// completed snapshots, polls refresh-request.json, observer.config.json and the kill switch, and drains
    /// the log queue. It never touches a game or Unity object (gate rule G9 on this namespace).
    /// </summary>
    public sealed class BackgroundWorker
    {
        private const int LoopSleepMs = 50;
        private const int ConsecutiveFaultLimit = 200;

        private readonly Hub _hub;
        private readonly ExchangeFiles _files;
        private readonly int _pid;
        private readonly SnapshotWriter _writer = new SnapshotWriter();
        private readonly SnapshotWriter _heartbeatWriter = new SnapshotWriter();
        private readonly List<string> _logLines = new List<string>(256);
        private readonly Dictionary<CaptureFamily, FamilyStatusDto> _families = new Dictionary<CaptureFamily, FamilyStatusDto>();
        private readonly RefreshNonces _served = new RefreshNonces();
        private Thread _thread;
        private long _heartbeatSeq;
        private int _publishFailures;
        private long _configMtime = long.MinValue;
        private long _refreshMtime = long.MinValue;
        private List<string> _configWarnings = new List<string>();
        private bool _configEnabled = true;
        private bool _killSwitch;
        private string _cwd;

        public BackgroundWorker(Hub hub, ExchangeFiles files, int pid)
        {
            _hub = hub;
            _files = files;
            _pid = pid;
            _families[CaptureFamily.Static] = new FamilyStatusDto();
            _families[CaptureFamily.State] = new FamilyStatusDto();
            _families[CaptureFamily.History] = new FamilyStatusDto();
        }

        public void Start()
        {
            _thread = new Thread(Run) { IsBackground = true, Name = "RoiMcpObserver.Publisher", Priority = ThreadPriority.Normal };
            _thread.Start();
        }

        public bool IsAlive { get { return _thread != null && _thread.IsAlive; } }

        private void Run()
        {
            int consecutiveFaults = 0;
            try
            {
                _files.EnsureDir();
                int removed = _files.CleanupTemps();
                _cwd = SafeCwd();
                Log.Info("observer", "started observer " + Baseline.ObserverVersion + "; exchange dir " + _files.Dir +
                                     "; cwd " + _cwd + "; removed " + removed + " stale temp files");
            }
            catch (Exception e)
            {
                Log.Error("publisher", Log.Signature("publisher.start", e), e.Message);
            }

            var clock = Stopwatch.StartNew();
            double nextPoll = 0, nextHeartbeat = 0, nextLogDrain = 0;
            // The thread lives for the whole process; the game ends with Process.Kill (no shutdown hook).
            while (true)
            {
                try
                {
                    double now = clock.Elapsed.TotalSeconds;
                    HashRequest();
                    if (now >= nextPoll)
                    {
                        nextPoll = now + 1.0;
                        PollFiles();
                    }
                    var p = _hub.TakeNext();
                    if (p != null) PublishSnapshot(p);
                    if (now >= nextHeartbeat)
                    {
                        nextHeartbeat = now + 1.0;
                        WriteHeartbeat();
                    }
                    if (now >= nextLogDrain)
                    {
                        nextLogDrain = now + 0.25;
                        DrainLog();
                    }
                    consecutiveFaults = 0;
                }
                catch (Exception e)
                {
                    consecutiveFaults++;
                    Log.Error("publisher", Log.Signature("publisher.loop", e), e.GetType().Name + ": " + e.Message);
                    if (consecutiveFaults >= ConsecutiveFaultLimit)
                    {
                        _hub.SetPublisherFaulted();
                        Log.Error("publisher", "publisher.faulted", "publisher entered faulted state after repeated errors");
                        consecutiveFaults = 0;
                    }
                }
                Thread.Sleep(LoopSleepMs);
            }
        }

        private static string SafeCwd()
        {
            try
            {
                return ExchangeFiles.CurrentDirectory();
            }
            catch (Exception)
            {
                return null;
            }
        }

        private void HashRequest()
        {
            var path = _hub.TakeAssemblyPathRequest();
            if (path == null) return;
            var sha = path.Length == 0 ? null : ExchangeFiles.Sha256OfFile(path);
            _hub.SetAssemblyHash(sha);
            Log.Info("observer", "Assembly-CSharp.dll SHA-256 " + (sha ?? "unavailable"));
        }

        private void PollFiles()
        {
            bool kill = _files.Exists(ExchangeFiles.KillSwitch);
            if (kill != _killSwitch)
            {
                _killSwitch = kill;
                _hub.SetKillSwitch(kill);
                Log.Info("observer", kill ? "kill switch present: observer disabled" : "kill switch removed");
            }

            long cfgM = _files.MtimeTicks(ExchangeFiles.Config);
            if (cfgM != _configMtime)
            {
                _configMtime = cfgM;
                ObserverConfig cfg;
                if (cfgM == -1) cfg = ObserverConfig.Default();
                else
                {
                    long len;
                    var text = _files.ReadSmallText(ExchangeFiles.Config, ObserverConfig.MaxConfigBytes, out len);
                    cfg = text == null && len > ObserverConfig.MaxConfigBytes ? ObserverConfig.Parse(new string(' ', ObserverConfig.MaxConfigBytes + 1)) : ObserverConfig.Parse(text);
                }
#if DEBUG_FAULTS
                DebugFaults.Set(cfg.DebugFaults);
#endif
                Log.SetLevel(cfg.LogLevel);
                _configWarnings = new List<string>(cfg.Warnings);
                _configEnabled = cfg.Enabled;
                _hub.SetConfig(cfg);
                Log.Info("observer", "configuration " + (cfgM == -1 ? "defaults (no file)" : "loaded") +
                                     (cfg.Warnings.Count > 0 ? "; warnings: " + string.Join(", ", cfg.Warnings.ToArray()) : ""));
            }

            long rM = _files.MtimeTicks(ExchangeFiles.RefreshRequest);
            if (rM != _refreshMtime)
            {
                _refreshMtime = rM;
                if (rM != -1)
                {
                    long len;
                    var text = _files.ReadSmallText(ExchangeFiles.RefreshRequest, RefreshRequestParser.MaxBytes, out len);
                    _hub.SetNonces(RefreshRequestParser.Parse(text, len));
                }
            }
        }

        private void PublishSnapshot(Publication p)
        {
            FaultPoint.Hit("publisher");
            var family = _families[p.Family];
            string name;
            string schema;
            switch (p.Family)
            {
                case CaptureFamily.State: name = ExchangeFiles.State; schema = "roi-mcp/state"; break;
                case CaptureFamily.History: name = ExchangeFiles.History; schema = "roi-mcp/history"; break;
                default: name = ExchangeFiles.Static; schema = "roi-mcp/static"; break;
            }

            var sw = Stopwatch.StartNew();
            var warnings = p.Warnings ?? new List<WarningDto>();
            string hash = _writer.SerializeData(p.Data);
            var drops = SizeCapPolicy.DropOrder(p.Family);
            int cap = SizeCapPolicy.Cap(p.Family);
            int dropIndex = 0;
            // Envelope overhead is small; the cap applies to the whole file, checked after writing.
            var env = BuildEnvelope(p, schema, hash);
            _writer.WriteFile(env);
            while (_writer.Length > cap && dropIndex < drops.Length)
            {
                if (SizeCapPolicy.Drop(p.Data, drops[dropIndex]))
                {
                    SizeCapPolicy.MarkDropped(p.Sections, warnings, drops[dropIndex], _writer.Length);
                    hash = _writer.SerializeData(p.Data);
                    env = BuildEnvelope(p, schema, hash);
                    _writer.WriteFile(env);
                }
                dropIndex++;
            }
            if (_writer.Length > cap)
            {
                _publishFailures++;
                Log.Error("publisher", "size_cap." + p.Family, name + " exceeds its size cap even after dropping optional sections (" + _writer.Length + " bytes); not published");
                return;
            }

            string nowUtc = Baseline.UtcNow();
            if (hash == family.content_hash && p.WorldSession == family.world_session)
            {
                // Unchanged content: skip the write, only refresh the verification time (PRD 11.3, R-PERF-6).
                family.last_verified_utc = nowUtc;
                MarkServed(p);
                return;
            }

            family.seq++;
            env.seq = family.seq;
            env.written_utc = nowUtc;
            _writer.WriteFile(env);
            string error;
            if (!_files.PublishAtomic(name, _writer.Buffer, _writer.Length, out error))
            {
                family.seq--;
                _publishFailures++;
                Log.Error("publisher", "publish." + p.Family, "publication of " + name + " failed: " + error);
                return;
            }
            sw.Stop();
            family.last_published_utc = nowUtc;
            family.last_verified_utc = nowUtc;
            family.size_bytes = _writer.Length;
            family.content_hash = hash;
            family.world_session = p.WorldSession;
            if (p.Stats != null)
            {
                p.Stats.size_bytes = _writer.Length;
                p.Stats.serialize_ms = Math.Round(sw.Elapsed.TotalMilliseconds, 3);
            }
            MarkServed(p);
        }

        private void MarkServed(Publication p)
        {
            if (!p.ServesNonce.HasValue) return;
            var cur = _served.Get(p.Family);
            if (!cur.HasValue || p.ServesNonce.Value > cur.Value)
            {
                _served.Set(p.Family, p.ServesNonce);
                Log.Info("observer", "refresh request served: " + p.Family + " nonce " + p.ServesNonce.Value);
            }
        }

        private EnvelopeDto BuildEnvelope(Publication p, string schema, string hash)
        {
            var family = _families[p.Family];
            StaticRefDto staticRef = null;
            if (p.Family != CaptureFamily.Static)
            {
                var st = _families[CaptureFamily.Static];
                staticRef = st.world_session == p.WorldSession
                    ? new StaticRefDto { seq = st.seq, content_hash = st.content_hash }
                    : new StaticRefDto { seq = 0, content_hash = "" };
            }
            var status = _hub.ReadStatus();
            return new EnvelopeDto
            {
                schema = schema,
                schema_version = Baseline.SchemaVersion,
                observer_version = Baseline.ObserverVersion,
                compatibility = "verified",
                game = status.DetectedGame,
                pid = _pid,
                world_session = p.WorldSession,
                seq = family.seq,
                content_hash = hash,
                written_utc = Baseline.UtcNow(),
                captured = p.Captured,
                static_ref = staticRef,
                sections = p.Sections,
                warnings = p.Warnings ?? new List<WarningDto>(),
            };
        }

        private void WriteHeartbeat()
        {
            var s = _hub.ReadStatus();
            var data = new HeartbeatData
            {
                state = ObserverStateNames.Wire(s.State),
                state_reason = s.StateReason,
                paused = s.Paused,
                speed_level = s.SpeedLevel,
                time_scale = s.TimeScale,
                game_date = s.GameDate,
                game_day = s.GameDay,
                main_thread_last_tick_utc = s.LastTickUtc == default(DateTime) ? null : Baseline.Utc(s.LastTickUtc),
                frame_count = s.FrameCount,
                scene = s.Scene,
                world_session = s.WorldSession,
                module_id = s.ModuleId,
                module_name = s.ModuleName,
                language = s.Language,
                compatibility = s.Compatibility,
                detected_game = s.DetectedGame,
                expected_game = Baseline.Expected(),
                reflection_self_check = s.ReflectionSelfCheck,
                families = new FamiliesDto
                {
                    @static = Copy(_families[CaptureFamily.Static]),
                    state = Copy(_families[CaptureFamily.State]),
                    history = Copy(_families[CaptureFamily.History]),
                },
                last_capture = s.LastCapture,
                effective_interval_s = Math.Round(s.EffectiveIntervalS, 3),
                degraded = s.Degraded,
                optional_sections_suppressed = s.OptionalSuppressed,
                disabled_sections = s.DisabledSections,
                id_collisions = s.IdCollisions,
                frame_stats = s.FrameStats ?? new FrameStatsDto(),
                refresh_seen = Nonces(s.RefreshSeen),
                refresh_served = Nonces(_served),
                publish_failures = _publishFailures,
                errors_last_hour = Log.ErrorsLastHour(),
                log_dropped = Log.Dropped,
                observer_version = Baseline.ObserverVersion,
                exchange_dir = _files.Dir,
                cwd = _cwd,
                kill_switch = _killSwitch,
                config_enabled = _configEnabled,
                config_warnings = _configWarnings,
                active_actor_differs = s.ActiveActorDiffers,
                save_name = new SaveNameDto { value = null, reason = "no_runtime_member_identified (U7)" },
                runtime_constants = s.RuntimeConstants,
                written_utc = Baseline.UtcNow(),
            };
            _heartbeatSeq++;
            var env = new EnvelopeDto
            {
                schema = "roi-mcp/heartbeat",
                schema_version = Baseline.SchemaVersion,
                observer_version = Baseline.ObserverVersion,
                compatibility = s.Compatibility,
                game = s.DetectedGame,
                pid = _pid,
                world_session = s.State == ObserverState.Ready ? s.WorldSession : null,
                seq = _heartbeatSeq,
                content_hash = null,
                written_utc = data.written_utc,
                captured = null,
                static_ref = null,
                sections = null,
                warnings = new List<WarningDto>(),
            };
            _heartbeatWriter.WriteSmall(env, data);
            if (_heartbeatWriter.Length > SizeCapPolicy.HeartbeatCap)
            {
                // Trim the diagnostic lists rather than exceed the cap.
                data.reflection_self_check = null;
                data.disabled_sections = new List<DisabledSectionDto>();
                data.config_warnings = new List<string> { "truncated" };
                _heartbeatWriter.WriteSmall(env, data);
                if (_heartbeatWriter.Length > SizeCapPolicy.HeartbeatCap) return;
            }
            string error;
            if (!_files.PublishAtomic(ExchangeFiles.Heartbeat, _heartbeatWriter.Buffer, _heartbeatWriter.Length, out error))
            {
                _publishFailures++;
                Log.Error("publisher", "publish.heartbeat", "heartbeat write failed: " + error);
            }
        }

        private static FamilyStatusDto Copy(FamilyStatusDto f)
        {
            return new FamilyStatusDto
            {
                seq = f.seq,
                last_published_utc = f.last_published_utc,
                last_verified_utc = f.last_verified_utc,
                size_bytes = f.size_bytes,
                content_hash = f.content_hash,
                world_session = f.world_session,
            };
        }

        private static RefreshNoncesDto Nonces(RefreshNonces n)
        {
            return new RefreshNoncesDto { state = n.State, history = n.History, @static = n.Static };
        }

        private void DrainLog()
        {
            _logLines.Clear();
            Log.Drain(_logLines, 500);
            _files.AppendLog(_logLines);
        }
    }
}
