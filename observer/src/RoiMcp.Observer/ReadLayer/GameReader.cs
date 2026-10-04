using System;
using System.Collections.Generic;
using ProjectAutomata;
using RoiMcp.Observer.Core;
using RoiMcp.Observer.Dto;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace RoiMcp.Observer.ReadLayer
{
    /// <summary>
    /// Read-layer entry point: lifecycle probes, version facts, session status and the capture sections.
    /// Every member access to Assembly-CSharp / UnityEngine of the observer happens in this namespace and is
    /// checked by the IL gate against the allowlist. Main thread only.
    /// </summary>
    public sealed class GameReader : IGameReader
    {
        private readonly WorldContext _world = new WorldContext();
        private int _lastProbeDay = int.MinValue;
        private int _maxDaysDeltaPerFrame;

        public int IdCollisions { get { return _world.Keys.Collisions; } }
        public int MaxDaysDeltaPerFrame { get { return _maxDaysDeltaPerFrame; } }

        // ------------------------------------------------------------ clock

        public string ActiveSceneName()
        {
            return SceneManager.GetActiveScene().name;
        }

        public double UnscaledTime()
        {
            return Time.unscaledTime;
        }

        public double UnscaledDeltaMs()
        {
            return Time.unscaledDeltaTime * 1000.0;
        }

        public long FrameCount()
        {
            return Time.frameCount;
        }

        public int GameDay()
        {
            var tm = ManagerBehaviour<TimeManager>.instance;
            if (tm == null) return 0;
            var d = tm.today;
            return Baseline.DayCount(d.Year, d.Month, d.Day);
        }

        // ------------------------------------------------------------ lifecycle

        public WorldProbe Probe(bool inGameScene)
        {
            var p = new WorldProbe { InGameScene = inGameScene };
            if (!inGameScene) return p;
            var world = ManagerBehaviour<World>.instance;
            p.WorldExists = world != null;
            if (!p.WorldExists) return p;
            p.WorldReady = world.isWorldReady;
            var loading = WorldLoadingScreen._instance;
            p.LoadingScreenActive = loading != null && loading.gameObject.activeSelf;
            if (!p.WorldReady || p.LoadingScreenActive) return p;
            var tm = ManagerBehaviour<TimeManager>.instance;
            if (tm == null) return p;
            var d = tm.today;
            if (d.Year < 1 || d.Month < 1 || d.Month > 12 || d.Day < 1 || d.Day > 30) return p;
            p.DateRead = true;
            p.GameDay = Baseline.DayCount(d.Year, d.Month, d.Day);
            if (_lastProbeDay != int.MinValue)
            {
                int delta = p.GameDay - _lastProbeDay;
                if (delta > _maxDaysDeltaPerFrame && delta < 1000) _maxDaysDeltaPerFrame = delta;
            }
            _lastProbeDay = p.GameDay;
            return p;
        }

        public bool ReadSession(SessionStatus into)
        {
            var tm = ManagerBehaviour<TimeManager>.instance;
            var speed = ManagerBehaviour<SpeedControls>.instance;
            if (tm == null || speed == null) return false;
            var d = tm.today;
            into.Year = d.Year;
            into.Month = d.Month;
            into.Day = d.Day;
            into.GameDay = Baseline.DayCount(d.Year, d.Month, d.Day);
            into.SpeedLevel = speed.level;
            into.TimeScale = Time.timeScale;
            into.Paused = speed.level == -1 || Time.timeScale == 0f;
            return true;
        }

        /// <summary>Module, language and active-actor flag (read on READY entry and at most once per second).</summary>
        public void ReadSessionSlow(SessionStatus into)
        {
            var repo = PersistentManagerBehaviour<GameModuleRepository>.instance;
            var module = repo != null ? repo.currentModule : null;
            into.ModuleId = module != null ? module.id : null;
            into.ModuleName = module != null ? module.displayName : null;
            into.Language = Localization.LanguageCode();
            var human = Player.humanPlayer;
            var active = Player.activeActor;
            into.ActiveActorDiffers = human != null && active != null && !ReferenceEquals(active, human);
        }

        public VersionFacts ReadVersionFacts()
        {
            var v = GameVersion.Get();
            if (v == null) return new VersionFacts { Found = false };
            return new VersionFacts
            {
                Found = true,
                Major = v.major,
                Minor = v.minor,
                Revision = v.revision,
                Suffix = v.suffix,
                Build = v.build,
                CommitHash = v.commitHash,
                SavegameVersion = v.savegameVersion,
                Release = null,
            };
        }

        public string AssemblyPath()
        {
            return typeof(GameVersion).Assembly.Location;
        }

        public RuntimeConstantsDto ReadRuntimeConstants()
        {
            var dto = new RuntimeConstantsDto
            {
                is_debug_build = Debug.isDebugBuild,
                application_version = Application.version,
                max_days_delta_per_frame = _maxDaysDeltaPerFrame,
                world_size = World.Size,
                network_names = new List<string>(),
            };
            var tm = ManagerBehaviour<TimeManager>.instance;
            if (tm != null) dto.seconds_per_day = tm.secondsPerDay;
            var speed = ManagerBehaviour<SpeedControls>.instance;
            if (speed != null && speed.speedLevels != null) dto.speed_levels = (float[])speed.speedLevels.Clone();
            var ach = AchievementManager.instance;
            if (ach != null) dto.disable_with_mods = ach.disableWithMods;
            var world = ManagerBehaviour<World>.instance;
            if (world != null)
            {
                var networks = World.Networks;
                if (networks != null)
                {
                    foreach (var n in networks)
                    {
                        if (n != null && dto.network_names.Count < 32) dto.network_names.Add(n.networkName);
                    }
                }
            }
            return dto;
        }

        public ReflectionSelfCheckDto RunReflectionSelfCheck(Dictionary<string, List<string>> disabledSectionsByEntry)
        {
            return ReflectionTable.Resolve(disabledSectionsByEntry);
        }

        // ------------------------------------------------------------ world session

        public void BeginWorldSession(string worldSession)
        {
            _world.Begin(worldSession);
            _lastProbeDay = int.MinValue;
        }

        public void ReleaseCaptureScratch()
        {
            StateCapture.ReleaseScratch();
        }

        public void DropReferences()
        {
            StateCapture.ReleaseScratch();
            _world.Clear();
            _lastProbeDay = int.MinValue;
        }

        public List<ISection> StaticSections()
        {
            return StaticCapture.Sections(_world);
        }

        public List<ISection> StateSections()
        {
            return StateCapture.Sections(_world);
        }

        public List<ISection> HistorySections()
        {
            return HistoryCapture.Sections(_world);
        }
    }
}
