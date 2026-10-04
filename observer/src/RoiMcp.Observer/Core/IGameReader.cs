using System.Collections.Generic;
using RoiMcp.Observer.Dto;

namespace RoiMcp.Observer.Core
{
    /// <summary>Main-thread status values read every frame while ready.</summary>
    public sealed class SessionStatus
    {
        public int Year;
        public int Month;
        public int Day;
        public int GameDay;
        public int SpeedLevel;
        public float TimeScale;
        public bool Paused;
        public string ModuleId;
        public string ModuleName;
        public string Language;
        public bool ActiveActorDiffers;
    }

    /// <summary>
    /// Everything the observer core needs from the game, implemented by the read layer (the only code
    /// allowed to touch game or Unity members, gate rule G9). Every method runs on the Unity main thread.
    /// </summary>
    public interface IGameReader : ICaptureClock
    {
        string ActiveSceneName();
        double UnscaledTime();
        double UnscaledDeltaMs();
        WorldProbe Probe(bool inGameScene);
        bool ReadSession(SessionStatus into);

        /// <summary>Module, language and active-actor flag; called on READY entry and about once per second.</summary>
        void ReadSessionSlow(SessionStatus into);
        VersionFacts ReadVersionFacts();
        string AssemblyPath();
        RuntimeConstantsDto ReadRuntimeConstants();

        /// <summary>Resolves the reflection table. Returns, per missing entry, the dependent section names.</summary>
        ReflectionSelfCheckDto RunReflectionSelfCheck(Dictionary<string, List<string>> disabledSectionsByEntry);

        void BeginWorldSession(string worldSession);
        void DropReferences();

        /// <summary>Releases per-capture scratch lists holding game references (after each capture).</summary>
        void ReleaseCaptureScratch();
        int IdCollisions { get; }

        /// <summary>Largest game-day change seen between two consecutive frames (E1: expect at most 1 at 10x).</summary>
        int MaxDaysDeltaPerFrame { get; }

        List<ISection> StaticSections();
        List<ISection> StateSections();
        List<ISection> HistorySections();
    }
}
