using System.Collections.Generic;
using RoiMcp.Observer.Core;
using RoiMcp.Observer.Diagnostics;
using RoiMcp.Observer.Dto;
using Xunit;

namespace RoiMcp.Observer.Tests
{
    /// <summary>Drives the main-thread runtime with a fake game reader (no Unity types involved).</summary>
    [Collection("Log")]
    public class ObserverRuntimeTests
    {
        private sealed class FakeReader : IGameReader
        {
            public string Scene = "MainMenu";
            public bool WorldReady;
            public double Time;
            public long Frame;
            public int Day = 100;
            public int StateCaptures;
            public int SleepInSessionMs;
            public int StateSectionMs;
            public int HistorySectionMs;

            public int GameDay() { return Day; }
            public long FrameCount() { return Frame; }
            public string ActiveSceneName() { return Scene; }
            public double UnscaledTime() { return Time; }
            public double FrameMs = 16;
            public double UnscaledDeltaMs() { return FrameMs; }

            public WorldProbe Probe(bool inGameScene)
            {
                return new WorldProbe
                {
                    InGameScene = inGameScene,
                    WorldExists = inGameScene,
                    WorldReady = inGameScene && WorldReady,
                    DateRead = inGameScene && WorldReady,
                    GameDay = Day,
                };
            }

            public bool ReadSession(SessionStatus into)
            {
                if (SleepInSessionMs > 0) System.Threading.Thread.Sleep(SleepInSessionMs);
                into.GameDay = Day;
                into.Year = 1;
                into.Month = 1;
                into.Day = 1;
                return true;
            }

            public void ReadSessionSlow(SessionStatus into) { into.ModuleId = "module"; }

            public VersionFacts ReadVersionFacts()
            {
                return new VersionFacts
                {
                    Found = true, Major = Baseline.Major, Minor = Baseline.Minor, Revision = Baseline.Revision,
                    Suffix = Baseline.Suffix, Build = Baseline.Build, CommitHash = Baseline.Commit, SavegameVersion = Baseline.SavegameVersion,
                };
            }

            public string AssemblyPath() { return "fake"; }
            public RuntimeConstantsDto ReadRuntimeConstants() { return new RuntimeConstantsDto(); }

            public ReflectionSelfCheckDto RunReflectionSelfCheck(Dictionary<string, List<string>> disabled)
            {
                return new ReflectionSelfCheckDto { problems = new List<ReflectionEntryDto>() };
            }

            public void BeginWorldSession(string worldSession) { }
            public void DropReferences() { }
            public void ReleaseCaptureScratch() { }
            public int IdCollisions { get { return 0; } }
            public int MaxDaysDeltaPerFrame { get; set; }
            public List<ISection> StaticSections() { return new List<ISection>(); }

            public List<ISection> StateSections()
            {
                StateCaptures++;
                return StateSectionMs > 0 ? new List<ISection> { new SlowSection("slow_state", StateSectionMs) } : new List<ISection>();
            }

            public List<ISection> HistorySections()
            {
                return HistorySectionMs > 0 ? new List<ISection> { new SlowSection("slow_history", HistorySectionMs) } : new List<ISection>();
            }
        }

        /// <summary>One atomic item that takes the given time (no yield), to exceed the capture ceiling.</summary>
        private sealed class SlowSection : ISection
        {
            private readonly int _ms;

            public SlowSection(string name, int ms)
            {
                Name = name;
                _ms = ms;
            }

            public string Name { get; private set; }
            public bool Optional { get { return false; } }

            public IEnumerator<bool> Run(SectionContext ctx)
            {
                System.Threading.Thread.Sleep(_ms);
                ctx.Items++;
                yield break;
            }
        }

        private static void Frames(ObserverRuntime rt, FakeReader r, int n)
        {
            for (int i = 0; i < n; i++)
            {
                r.Frame++;
                r.Time += 0.02;
                rt.Tick();
            }
        }

        [Theory]
        [InlineData("Game")]
        [InlineData("game")]
        public void GameSceneIsDetectedCaseInsensitively(string scene)
        {
            var hub = new Hub();
            hub.SetAssemblyHash(Baseline.AssemblySha256);
            var r = new FakeReader { Scene = scene, WorldReady = true };
            var rt = new ObserverRuntime(r, hub);
            Frames(rt, r, 5);
            Assert.Equal(ObserverState.Ready, rt.State);
            Assert.Equal("verified", hub.ReadStatus().Compatibility);
            Assert.True(r.StateCaptures > 0);
        }

        [Fact]
        public void DaysDeltaIsPublishedLiveAfterReadyEntry()
        {
            var hub = new Hub();
            hub.SetAssemblyHash(Baseline.AssemblySha256);
            var r = new FakeReader { Scene = "Game", WorldReady = true };
            var rt = new ObserverRuntime(r, hub);
            Frames(rt, r, 5);
            var before = hub.ReadStatus().RuntimeConstants;
            Assert.Equal(0, before.max_days_delta_per_frame);
            r.MaxDaysDeltaPerFrame = 1;
            r.Day++;
            Frames(rt, r, 2);
            Assert.Equal(1, hub.ReadStatus().RuntimeConstants.max_days_delta_per_frame);
            Assert.Equal(0, before.max_days_delta_per_frame); // published instance not mutated
        }

        [Fact]
        public void SlowTickIsLoggedWithPhaseBreakdownAndRateLimited()
        {
            var hub = new Hub();
            hub.SetAssemblyHash(Baseline.AssemblySha256);
            var r = new FakeReader { Scene = "Game", WorldReady = true };
            var rt = new ObserverRuntime(r, hub);
            Frames(rt, r, 3);
            var sink = new List<string>();
            while (Log.Drain(sink, 10000) > 0) sink.Clear();

            r.SleepInSessionMs = 30;
            Frames(rt, r, 3);
            r.SleepInSessionMs = 0;
            Log.Drain(sink, 10000);

            var slow = sink.FindAll(l => l.Contains("slow observer tick"));
            Assert.Single(slow); // three slow ticks, one line per 10 s
            Assert.Contains("GC during tick:", slow[0]);
            Assert.Contains("phases ms:", slow[0]);
            Assert.Contains(" session ", slow[0]);
        }

        private static List<string> CaptureLogFor(int stateMs, int historyMs, int frames)
        {
            var hub = new Hub();
            hub.SetAssemblyHash(Baseline.AssemblySha256);
            var r = new FakeReader { Scene = "Game", WorldReady = true, StateSectionMs = stateMs, HistorySectionMs = historyMs };
            var rt = new ObserverRuntime(r, hub);
            var sink = new List<string>();
            while (Log.Drain(sink, 10000) > 0) sink.Clear();
            Frames(rt, r, frames);
            Log.Drain(sink, 10000);
            return sink.FindAll(l => l.Contains(" capture took "));
        }

        [Fact]
        public void SlowHistoryCaptureIsNotReportedAsBackingOff()
        {
            var lines = CaptureLogFor(0, 60, 40);
            var history = lines.FindAll(l => l.Contains("history capture took"));
            Assert.Single(history);
            Assert.Contains("ceiling applies to state captures only, so no back-off", history[0]);
            Assert.DoesNotContain(lines, l => l.Contains("backing off"));
        }

        [Fact]
        public void SlowStateCaptureReportsTheRaisedInterval()
        {
            var lines = CaptureLogFor(60, 0, 40);
            var state = lines.FindAll(l => l.Contains("state capture took"));
            Assert.NotEmpty(state);
            // A ~60 ms capture: adaptive interval max(5 s, 100 x main-thread ms) ~ 6 s, doubled after the breach.
            var m = System.Text.RegularExpressions.Regex.Match(state[0],
                @"state capture took \d+(\.\d)? ms main-thread in \d+ slice\(s\), max slice \d+(\.\d)? ms \(ceiling 50 ms\): next state capture interval raised to (?<s>\d+(\.\d)?) s");
            Assert.True(m.Success, state[0]);
            double raised = double.Parse(m.Groups["s"].Value, System.Globalization.CultureInfo.InvariantCulture);
            Assert.InRange(raised, 12.0, 20.0);
        }

        [Fact]
        public void SceneUnloadPublishesLoadingWithoutWorldAtOnce()
        {
            var hub = new Hub();
            hub.SetAssemblyHash(Baseline.AssemblySha256);
            var r = new FakeReader { Scene = "Game", WorldReady = true };
            var rt = new ObserverRuntime(r, hub);
            Frames(rt, r, 5);
            string first = hub.ReadStatus().WorldSession;
            Assert.NotNull(first);

            rt.OnSceneChanged("sceneUnloaded"); // no tick follows while the game loads
            var during = hub.ReadStatus();
            Assert.Equal(ObserverState.Loading, during.State);
            Assert.Null(during.WorldSession);

            Frames(rt, r, 5); // game -> game reload finished
            var after = hub.ReadStatus();
            Assert.Equal(ObserverState.Ready, after.State);
            Assert.NotNull(after.WorldSession);
            Assert.NotEqual(first, after.WorldSession);
        }

        [Fact]
        public void LongFrameIsLoggedWithGcAttribution()
        {
            var hub = new Hub();
            hub.SetAssemblyHash(Baseline.AssemblySha256);
            var r = new FakeReader { Scene = "Game", WorldReady = true };
            var rt = new ObserverRuntime(r, hub);
            Frames(rt, r, 3);
            var sink = new List<string>();
            while (Log.Drain(sink, 10000) > 0) sink.Clear();
            r.FrameMs = 6500; // a 6.5 s freeze between two observer ticks
            Frames(rt, r, 1);
            r.FrameMs = 16;
            Frames(rt, r, 2);
            Log.Drain(sink, 10000);
            var lines = sink.FindAll(l => l.Contains("long frame"));
            Assert.Single(lines);
            Assert.Contains("long frame 6500 ms", lines[0]);
            Assert.Contains("garbage collections since the previous observer tick:", lines[0]);
        }

        [Fact]
        public void MenuSceneMakesNoCaptures()
        {
            var hub = new Hub();
            hub.SetAssemblyHash(Baseline.AssemblySha256);
            var r = new FakeReader { Scene = "MainMenu" };
            var rt = new ObserverRuntime(r, hub);
            Frames(rt, r, 10);
            Assert.Equal(ObserverState.Menu, rt.State);
            Assert.Equal(0, r.StateCaptures);
        }

        [Fact]
        public void WrongAssemblyHashMeansUnsupportedBuildAndNoCapture()
        {
            var hub = new Hub();
            hub.SetAssemblyHash("00");
            var r = new FakeReader { Scene = "Game", WorldReady = true };
            var rt = new ObserverRuntime(r, hub);
            Frames(rt, r, 10);
            Assert.Equal(ObserverState.UnsupportedBuild, rt.State);
            Assert.Equal("unsupported_build", hub.ReadStatus().Compatibility);
            Assert.Equal(0, r.StateCaptures);
        }

        [Fact]
        public void KillSwitchDisablesWithoutReads()
        {
            var hub = new Hub();
            hub.SetAssemblyHash(Baseline.AssemblySha256);
            hub.SetKillSwitch(true);
            var r = new FakeReader { Scene = "Game", WorldReady = true };
            var rt = new ObserverRuntime(r, hub);
            Frames(rt, r, 10);
            Assert.Equal(ObserverState.Disabled, rt.State);
            Assert.Equal(0, r.StateCaptures);
        }
    }
}
