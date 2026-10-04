using RoiMcp.Observer.Core;
using Xunit;

namespace RoiMcp.Observer.Tests
{
    public class LifecycleTests
    {
        private static WorldProbe MenuProbe()
        {
            return new WorldProbe { InGameScene = false };
        }

        private static WorldProbe LoadingProbe()
        {
            return new WorldProbe { InGameScene = true, WorldExists = true, WorldReady = false, LoadingScreenActive = true };
        }

        private static WorldProbe ReadyProbe(bool dateRead = true)
        {
            return new WorldProbe { InGameScene = true, WorldExists = true, WorldReady = true, LoadingScreenActive = false, DateRead = dateRead, GameDay = 100 };
        }

        private static Lifecycle InReady()
        {
            var l = new Lifecycle();
            l.Step(MenuProbe(), false);
            l.Step(LoadingProbe(), false);
            l.Step(ReadyProbe(), false);
            Assert.Equal(LifecycleEvent.EnteredReady, l.Step(ReadyProbe(), false));
            Assert.Equal(ObserverState.Ready, l.State);
            return l;
        }

        [Fact]
        public void StartsInStarting()
        {
            Assert.Equal(ObserverState.Starting, new Lifecycle().State);
        }

        [Fact]
        public void MenuToLoadingToReady_NeedsDateOnTwoConsecutiveFrames()
        {
            var l = new Lifecycle();
            Assert.Equal(LifecycleEvent.None, l.Step(MenuProbe(), false));
            Assert.Equal(ObserverState.Menu, l.State);
            Assert.Equal(LifecycleEvent.None, l.Step(LoadingProbe(), false));
            Assert.Equal(ObserverState.Loading, l.State);
            Assert.Equal(LifecycleEvent.None, l.Step(ReadyProbe(), false));
            Assert.Equal(ObserverState.Loading, l.State); // only one frame with a date
            Assert.Equal(LifecycleEvent.EnteredReady, l.Step(ReadyProbe(), false));
            Assert.Equal(ObserverState.Ready, l.State);
            Assert.Equal(LifecycleEvent.None, l.Step(ReadyProbe(), false));
        }

        [Fact]
        public void DateFramesMustBeConsecutive()
        {
            var l = new Lifecycle();
            l.Step(LoadingProbe(), false);
            l.Step(ReadyProbe(true), false);
            l.Step(ReadyProbe(false), false);
            Assert.Equal(LifecycleEvent.None, l.Step(ReadyProbe(true), false));
            Assert.Equal(ObserverState.Loading, l.State);
            Assert.Equal(LifecycleEvent.EnteredReady, l.Step(ReadyProbe(true), false));
        }

        [Fact]
        public void LoadingScreenOrMissingWorldKeepsLoading()
        {
            var l = new Lifecycle();
            var p = ReadyProbe();
            p.LoadingScreenActive = true;
            for (int i = 0; i < 3; i++) l.Step(p, false);
            Assert.Equal(ObserverState.Loading, l.State);

            var noWorld = ReadyProbe();
            noWorld.WorldExists = false;
            for (int i = 0; i < 3; i++) l.Step(noWorld, false);
            Assert.Equal(ObserverState.Loading, l.State);

            var notReady = ReadyProbe();
            notReady.WorldReady = false;
            for (int i = 0; i < 3; i++) l.Step(notReady, false);
            Assert.Equal(ObserverState.Loading, l.State);
        }

        [Fact]
        public void LeftReady_FiresWhenLeavingToMenu()
        {
            var l = InReady();
            Assert.Equal(LifecycleEvent.LeftReady, l.Step(MenuProbe(), false));
            Assert.Equal(ObserverState.Menu, l.State);
            Assert.Equal(LifecycleEvent.None, l.Step(MenuProbe(), false));
        }

        [Fact]
        public void LeftReady_FiresWhenLeavingToLoading()
        {
            var l = InReady();
            Assert.Equal(LifecycleEvent.LeftReady, l.Step(LoadingProbe(), false));
            Assert.Equal(ObserverState.Loading, l.State);
        }

        [Fact]
        public void Disabled_OverridesEveryProbe()
        {
            var l = new Lifecycle();
            Assert.Equal(LifecycleEvent.None, l.Step(ReadyProbe(), true));
            Assert.Equal(ObserverState.Disabled, l.State);
            l.Step(ReadyProbe(), true);
            l.Step(ReadyProbe(), true);
            Assert.Equal(ObserverState.Disabled, l.State);

            var r = InReady();
            Assert.Equal(LifecycleEvent.LeftReady, r.Step(ReadyProbe(), true));
            Assert.Equal(ObserverState.Disabled, r.State);

            // Re-enabled: ready again only after two consecutive date frames.
            Assert.Equal(LifecycleEvent.None, r.Step(ReadyProbe(), false));
            Assert.Equal(ObserverState.Loading, r.State);
            Assert.Equal(LifecycleEvent.EnteredReady, r.Step(ReadyProbe(), false));
        }

        [Fact]
        public void GameToGameReload_ForcesReadyLoadingReady()
        {
            var l = InReady();
            l.NotifySceneChanged();
            // Even if the probe still looks ready, the scene change forces a pass through loading.
            Assert.Equal(LifecycleEvent.LeftReady, l.Step(ReadyProbe(), false));
            Assert.Equal(ObserverState.Loading, l.State);
            Assert.Equal(LifecycleEvent.None, l.Step(ReadyProbe(), false));
            Assert.Equal(ObserverState.Loading, l.State);
            Assert.Equal(LifecycleEvent.EnteredReady, l.Step(ReadyProbe(), false));
            Assert.Equal(ObserverState.Ready, l.State);
        }

        [Fact]
        public void GameToGameReload_WithRealLoadingFrames()
        {
            var l = InReady();
            l.NotifySceneChanged();
            Assert.Equal(LifecycleEvent.LeftReady, l.Step(LoadingProbe(), false));
            l.Step(LoadingProbe(), false);
            l.Step(ReadyProbe(), false);
            Assert.Equal(LifecycleEvent.EnteredReady, l.Step(ReadyProbe(), false));
        }

        [Fact]
        public void ReadyDoesNotFlapOnASingleMissedDateRead()
        {
            var l = InReady();
            Assert.Equal(LifecycleEvent.None, l.Step(ReadyProbe(false), false));
            Assert.Equal(ObserverState.Ready, l.State);
        }

        [Fact]
        public void MarkUnsupported_IsTerminal()
        {
            var l = InReady();
            l.MarkUnsupported();
            Assert.Equal(ObserverState.UnsupportedBuild, l.State);
            for (int i = 0; i < 5; i++)
            {
                Assert.Equal(LifecycleEvent.None, l.Step(ReadyProbe(), false));
                Assert.Equal(ObserverState.UnsupportedBuild, l.State);
            }
            Assert.Equal(LifecycleEvent.None, l.Step(MenuProbe(), false));
            Assert.Equal(LifecycleEvent.None, l.Step(ReadyProbe(), true));
            l.NotifySceneChanged();
            Assert.Equal(LifecycleEvent.None, l.Step(ReadyProbe(), false));
            Assert.Equal(ObserverState.UnsupportedBuild, l.State);
        }

        [Fact]
        public void MarkFaulted_IsTerminal()
        {
            var l = InReady();
            l.MarkFaulted();
            Assert.Equal(LifecycleEvent.None, l.Step(ReadyProbe(), false));
            Assert.Equal(ObserverState.Faulted, l.State);
        }

        [Fact]
        public void WireNames()
        {
            Assert.Equal("starting", ObserverStateNames.Wire(ObserverState.Starting));
            Assert.Equal("menu", ObserverStateNames.Wire(ObserverState.Menu));
            Assert.Equal("loading", ObserverStateNames.Wire(ObserverState.Loading));
            Assert.Equal("ready", ObserverStateNames.Wire(ObserverState.Ready));
            Assert.Equal("unsupported_build", ObserverStateNames.Wire(ObserverState.UnsupportedBuild));
            Assert.Equal("disabled", ObserverStateNames.Wire(ObserverState.Disabled));
            Assert.Equal("faulted", ObserverStateNames.Wire(ObserverState.Faulted));
        }
    }
}
