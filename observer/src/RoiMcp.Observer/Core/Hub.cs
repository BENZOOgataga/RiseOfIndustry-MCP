using System;
using System.Collections.Generic;
using RoiMcp.Observer.Dto;

namespace RoiMcp.Observer.Core
{
    /// <summary>A completed capture handed from the main thread to the background publisher.</summary>
    public sealed class Publication
    {
        public CaptureFamily Family;
        public string WorldSession;
        public object Data;
        public CapturedDto Captured;
        public Dictionary<string, SectionStatusDto> Sections;
        public List<WarningDto> Warnings;
        public long? ServesNonce;
        public CaptureStatsDto Stats;
    }

    /// <summary>
    /// Status values the main thread publishes for the heartbeat. Plain fields, copied under the hub lock.
    /// </summary>
    public sealed class MainStatus
    {
        public ObserverState State = ObserverState.Starting;
        public string StateReason;
        public bool Paused;
        public int? SpeedLevel;
        public float? TimeScale;
        public string GameDate;
        public int? GameDay;
        public DateTime LastTickUtc;
        public long FrameCount;
        public string Scene;
        public string WorldSession;
        public string ModuleId;
        public string ModuleName;
        public string Language;
        public string Compatibility = "pending";
        public GameInfoDto DetectedGame;
        public ReflectionSelfCheckDto ReflectionSelfCheck;
        public CaptureStatsDto LastCapture;
        public double EffectiveIntervalS;
        public bool Degraded;
        public bool OptionalSuppressed;
        public List<DisabledSectionDto> DisabledSections = new List<DisabledSectionDto>();
        public int IdCollisions;
        public FrameStatsDto FrameStats = new FrameStatsDto();
        public RefreshNonces RefreshSeen = new RefreshNonces();
        public bool? ActiveActorDiffers;
        public RuntimeConstantsDto RuntimeConstants;

        public MainStatus CopyShallow()
        {
            var c = (MainStatus)MemberwiseClone();
            c.DisabledSections = new List<DisabledSectionDto>(DisabledSections);
            c.RefreshSeen = RefreshSeen.Clone();
            return c;
        }
    }

    /// <summary>Values the background thread hands back to the main thread.</summary>
    public sealed class ControlState
    {
        public bool KillSwitch;
        public ObserverConfig Config = ObserverConfig.Default();
        public int ConfigVersion;
        public RefreshNonces Nonces = new RefreshNonces();
        public string AssemblySha256;
        public bool AssemblyHashDone;
        public bool PublisherFaulted;
    }

    /// <summary>
    /// The only shared state between the Unity main thread and the background publisher thread. All access
    /// is under one lock and copies plain values; no game or Unity object ever crosses this boundary.
    /// </summary>
    public sealed class Hub
    {
        private readonly object _sync = new object();
        private readonly MainStatus _status = new MainStatus();
        private readonly ControlState _control = new ControlState();
        private readonly List<Publication> _pending = new List<Publication>();
        private string _assemblyPath;

        // ---- main thread -> background

        public void UpdateStatus(Action<MainStatus> update)
        {
            lock (_sync)
            {
                update(_status);
            }
        }

        /// <summary>Per-frame fields; no allocation.</summary>
        public void Tick(DateTime utc, long frame, ObserverState state)
        {
            lock (_sync)
            {
                _status.LastTickUtc = utc;
                _status.FrameCount = frame;
                _status.State = state;
            }
        }

        public MainStatus ReadStatus()
        {
            lock (_sync)
            {
                return _status.CopyShallow();
            }
        }

        /// <summary>Queues a publication. At most one pending publication per family: a newer one replaces it.</summary>
        public void Enqueue(Publication p)
        {
            lock (_sync)
            {
                for (int i = 0; i < _pending.Count; i++)
                {
                    if (_pending[i].Family == p.Family)
                    {
                        _pending.RemoveAt(i);
                        break;
                    }
                }
                _pending.Add(p);
            }
        }

        /// <summary>Drops queued publications of other world sessions (world unloaded).</summary>
        public void DropPendingExcept(string worldSession)
        {
            lock (_sync)
            {
                _pending.RemoveAll(p => p.WorldSession != worldSession);
            }
        }

        public Publication TakeNext()
        {
            lock (_sync)
            {
                if (_pending.Count == 0) return null;
                var p = _pending[0];
                _pending.RemoveAt(0);
                return p;
            }
        }

        public void RequestAssemblyHash(string path)
        {
            lock (_sync)
            {
                if (_assemblyPath == null) _assemblyPath = path ?? "";
            }
        }

        public string TakeAssemblyPathRequest()
        {
            lock (_sync)
            {
                if (_control.AssemblyHashDone || _assemblyPath == null) return null;
                return _assemblyPath;
            }
        }

        // ---- background -> main thread

        public void SetAssemblyHash(string sha)
        {
            lock (_sync)
            {
                _control.AssemblySha256 = sha;
                _control.AssemblyHashDone = true;
            }
        }

        public void SetKillSwitch(bool on)
        {
            lock (_sync) _control.KillSwitch = on;
        }

        public void SetConfig(ObserverConfig c)
        {
            lock (_sync)
            {
                _control.Config = c;
                _control.ConfigVersion++;
            }
        }

        public void SetNonces(RefreshNonces n)
        {
            lock (_sync) _control.Nonces = n;
        }

        public void SetPublisherFaulted()
        {
            lock (_sync) _control.PublisherFaulted = true;
        }

        public ControlState ReadControl()
        {
            lock (_sync)
            {
                return new ControlState
                {
                    KillSwitch = _control.KillSwitch,
                    Config = _control.Config,
                    ConfigVersion = _control.ConfigVersion,
                    Nonces = _control.Nonces,
                    AssemblySha256 = _control.AssemblySha256,
                    AssemblyHashDone = _control.AssemblyHashDone,
                    PublisherFaulted = _control.PublisherFaulted,
                };
            }
        }

        /// <summary>Same as ReadControl but without allocation: copies into an existing instance.</summary>
        public void ReadControl(ControlState into)
        {
            lock (_sync)
            {
                into.KillSwitch = _control.KillSwitch;
                into.Config = _control.Config;
                into.ConfigVersion = _control.ConfigVersion;
                into.Nonces = _control.Nonces;
                into.AssemblySha256 = _control.AssemblySha256;
                into.AssemblyHashDone = _control.AssemblyHashDone;
                into.PublisherFaulted = _control.PublisherFaulted;
            }
        }
    }
}
