namespace RoiMcp.Observer.Core
{
    public enum ObserverState
    {
        Starting,
        Menu,
        Loading,
        Ready,
        UnsupportedBuild,
        Disabled,
        Faulted,
    }

    public static class ObserverStateNames
    {
        public static string Wire(ObserverState s)
        {
            switch (s)
            {
                case ObserverState.Starting: return "starting";
                case ObserverState.Menu: return "menu";
                case ObserverState.Loading: return "loading";
                case ObserverState.Ready: return "ready";
                case ObserverState.UnsupportedBuild: return "unsupported_build";
                case ObserverState.Disabled: return "disabled";
                default: return "faulted";
            }
        }
    }

    /// <summary>One frame of lifecycle observations (PRD 8.2). Produced by the read layer.</summary>
    public struct WorldProbe
    {
        public bool InGameScene;
        public bool WorldExists;
        public bool WorldReady;
        public bool LoadingScreenActive;
        public bool DateRead;
        public int GameDay;
    }

    public enum LifecycleEvent
    {
        None,
        EnteredReady,
        LeftReady,
    }

    /// <summary>
    /// Lifecycle state machine. Pure logic: the caller feeds one probe per frame plus the disable/fault/
    /// unsupported conditions. Ready requires the date to be read on at least two consecutive frames.
    /// </summary>
    public sealed class Lifecycle
    {
        private int _consecutiveDateFrames;
        private bool _sceneChangedSinceReady;

        public ObserverState State { get; private set; }

        public Lifecycle()
        {
            State = ObserverState.Starting;
        }

        /// <summary>Called by the scene event handlers: abandons the current world immediately.</summary>
        public void NotifySceneChanged()
        {
            _sceneChangedSinceReady = true;
            _consecutiveDateFrames = 0;
        }

        /// <summary>
        /// A scene event while ready means the world is being torn down: report loading at once instead of a
        /// ready state without a world while the main thread is busy loading. Returns true when the state changed.
        /// </summary>
        public bool LeaveReadyForSceneChange()
        {
            if (State != ObserverState.Ready) return false;
            State = ObserverState.Loading;
            return true;
        }

        public void MarkUnsupported()
        {
            State = ObserverState.UnsupportedBuild;
        }

        public void MarkFaulted()
        {
            State = ObserverState.Faulted;
        }

        public LifecycleEvent Step(WorldProbe probe, bool disabled)
        {
            if (State == ObserverState.UnsupportedBuild || State == ObserverState.Faulted) return LifecycleEvent.None;

            var previous = State;
            ObserverState next;
            if (disabled)
            {
                next = ObserverState.Disabled;
                _consecutiveDateFrames = 0;
            }
            else if (!probe.InGameScene || !probe.WorldExists)
            {
                next = probe.InGameScene ? ObserverState.Loading : ObserverState.Menu;
                _consecutiveDateFrames = 0;
            }
            else if (!probe.WorldReady || probe.LoadingScreenActive)
            {
                next = ObserverState.Loading;
                _consecutiveDateFrames = 0;
            }
            else
            {
                _consecutiveDateFrames = probe.DateRead ? _consecutiveDateFrames + 1 : 0;
                bool readyNow = _consecutiveDateFrames >= 2;
                if (previous == ObserverState.Ready && _sceneChangedSinceReady)
                {
                    // game -> game reload observed through scene events: force a loading transition.
                    next = ObserverState.Loading;
                    _consecutiveDateFrames = 0;
                }
                else
                {
                    next = readyNow ? ObserverState.Ready : (previous == ObserverState.Ready ? ObserverState.Ready : ObserverState.Loading);
                }
            }
            _sceneChangedSinceReady = false;
            State = next;
            if (previous != ObserverState.Ready && next == ObserverState.Ready) return LifecycleEvent.EnteredReady;
            if (previous == ObserverState.Ready && next != ObserverState.Ready) return LifecycleEvent.LeftReady;
            return LifecycleEvent.None;
        }
    }
}
