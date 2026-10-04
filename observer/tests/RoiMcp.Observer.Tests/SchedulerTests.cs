using System;
using RoiMcp.Observer.Core;
using Xunit;

namespace RoiMcp.Observer.Tests
{
    public class SchedulerTests
    {
        // Day 100 = Y1-04-10, day 101 = Y1-04-11 (same month), day 121 = Y1-05-01.
        private const int Day = 100;
        private const string Module = "module-a";
        private static readonly DateTime Utc0 = new DateTime(2026, 1, 1, 0, 0, 0, DateTimeKind.Utc);

        /// <summary>A scheduler that has entered ready at t=0 and started its three ready-entry captures.</summary>
        private static Scheduler ReadyAndInitialCapturesDone(ObserverConfig config = null)
        {
            var s = new Scheduler();
            if (config != null) s.SetConfig(config);
            s.OnEnteredReady(Day, Module);
            for (int i = 0; i < 3; i++)
            {
                var f = s.Next(0, false, Day, Module);
                Assert.True(f.HasValue);
                s.OnCaptureStarted(f.Value, 0, Day, true);
                s.OnCaptureFinished(f.Value, 1, Utc0);
            }
            Assert.Null(s.Next(0, false, Day, Module));
            return s;
        }

        [Fact]
        public void ReadyEntry_QueuesStaticThenStateThenHistory()
        {
            var s = new Scheduler();
            s.OnEnteredReady(Day, Module);
            Assert.Equal(CaptureFamily.Static, s.Next(0, false, Day, Module));
            s.OnCaptureStarted(CaptureFamily.Static, 0, Day, true);
            Assert.Equal(CaptureFamily.State, s.Next(0, false, Day, Module));
            s.OnCaptureStarted(CaptureFamily.State, 0, Day, true);
            Assert.Equal(CaptureFamily.History, s.Next(0, false, Day, Module));
            s.OnCaptureStarted(CaptureFamily.History, 0, Day, true);
            Assert.Null(s.Next(0, false, Day, Module));
        }

        [Fact]
        public void Next_ReturnsNullWhenNothingIsDue()
        {
            var s = ReadyAndInitialCapturesDone();
            Assert.Null(s.Next(1, false, Day, Module));
            Assert.Null(s.Next(4.99, false, Day + 1, Module));
            Assert.Null(s.Next(14.99, true, Day, Module));
        }

        [Fact]
        public void Running_DefaultIntervalIsFiveSecondsAndRequiresADayChange()
        {
            var s = ReadyAndInitialCapturesDone();
            Assert.Equal(5, s.EffectiveIntervalS(false));
            Assert.Null(s.Next(4.9, false, Day + 1, Module));
            // Interval elapsed but the game day did not change: nothing to capture.
            Assert.Null(s.Next(5.0, false, Day, Module));
            Assert.Null(s.Next(60.0, false, Day, Module));
            Assert.Equal(CaptureFamily.State, s.Next(60.0, false, Day + 1, Module));
        }

        [Fact]
        public void Running_IntervalIsMeasuredInRealTimeNotGameDays()
        {
            // At high game speed many days pass per second; the gap is still 5 s of unscaled time.
            var s = ReadyAndInitialCapturesDone();
            Assert.Null(s.Next(1.0, false, Day + 3, Module));
            Assert.Null(s.Next(4.0, false, Day + 9, Module));
            Assert.Equal(CaptureFamily.State, s.Next(5.0, false, Day + 11, Module));
            s.OnCaptureStarted(CaptureFamily.State, 5.0, Day + 11, false);
            Assert.Null(s.Next(9.9, false, Day + 17, Module));
            Assert.Equal(CaptureFamily.State, s.Next(10.0, false, Day + 20, Module));
        }

        [Fact]
        public void Paused_IntervalIsFifteenSecondsWithoutDayChange()
        {
            var s = ReadyAndInitialCapturesDone();
            Assert.Equal(15, s.EffectiveIntervalS(true));
            Assert.Null(s.Next(14.9, true, Day, Module));
            Assert.Equal(CaptureFamily.State, s.Next(15.0, true, Day, Module));
            s.OnCaptureStarted(CaptureFamily.State, 15.0, Day, false);
            Assert.Null(s.Next(29.9, true, Day, Module));
            Assert.Equal(CaptureFamily.State, s.Next(30.0, true, Day, Module));
        }

        [Fact]
        public void ConfiguredIntervalsAreUsed()
        {
            var s = ReadyAndInitialCapturesDone(ObserverConfig.Parse("{\"running_interval_s\": 7, \"paused_interval_s\": 20}"));
            Assert.Equal(7, s.EffectiveIntervalS(false));
            Assert.Equal(20, s.EffectiveIntervalS(true));
            Assert.Null(s.Next(6.9, false, Day + 1, Module));
            Assert.Equal(CaptureFamily.State, s.Next(7.0, false, Day + 1, Module));
            Assert.Null(s.Next(19.9, true, Day, Module));
            Assert.Equal(CaptureFamily.State, s.Next(20.0, true, Day, Module));
        }

        [Fact]
        public void Refresh_StateRequestRespectsMinGap()
        {
            var s = ReadyAndInitialCapturesDone();
            s.ObserveNonces(new RefreshNonces { State = 10 });
            // Last state capture started at t=0; min_gap_s = 1.
            Assert.Null(s.Next(0.5, false, Day, Module));
            Assert.Equal(CaptureFamily.State, s.Next(1.0, false, Day, Module));
            Assert.Equal(10L, s.OnCaptureStarted(CaptureFamily.State, 1.0, Day, false));

            s.ObserveNonces(new RefreshNonces { State = 11 });
            Assert.Null(s.Next(1.5, false, Day, Module));
            Assert.Equal(CaptureFamily.State, s.Next(2.0, false, Day, Module));
            Assert.Equal(11L, s.OnCaptureStarted(CaptureFamily.State, 2.0, Day, false));
            Assert.Null(s.Next(2.5, false, Day, Module));
        }

        [Fact]
        public void Refresh_MinGapAppliesAcrossScopes()
        {
            var s = ReadyAndInitialCapturesDone(ObserverConfig.Parse("{\"min_gap_s\": 3}"));
            s.ObserveNonces(new RefreshNonces { History = 5 });
            Assert.Equal(CaptureFamily.History, s.Next(0.1, false, Day, Module));
            Assert.Equal(5L, s.OnCaptureStarted(CaptureFamily.History, 0.1, Day, false));

            s.ObserveNonces(new RefreshNonces { History = 5, Static = 6 });
            Assert.Null(s.Next(3.0, false, Day, Module));
            Assert.Equal(CaptureFamily.Static, s.Next(3.1, false, Day, Module));
        }

        [Fact]
        public void Refresh_ServesStateThenHistoryThenStatic()
        {
            var s = ReadyAndInitialCapturesDone();
            s.ObserveNonces(new RefreshNonces { State = 1, History = 2, Static = 3 });
            Assert.Equal(CaptureFamily.State, s.Next(1.0, false, Day, Module));
            Assert.Equal(1L, s.OnCaptureStarted(CaptureFamily.State, 1.0, Day, false));
            Assert.Equal(CaptureFamily.History, s.Next(2.0, false, Day, Module));
            Assert.Equal(2L, s.OnCaptureStarted(CaptureFamily.History, 2.0, Day, false));
            Assert.Equal(CaptureFamily.Static, s.Next(3.0, false, Day, Module));
            Assert.Equal(3L, s.OnCaptureStarted(CaptureFamily.Static, 3.0, Day, false));
            Assert.Null(s.Next(4.0, false, Day, Module));
        }

        [Fact]
        public void Nonces_CoalesceToLatestAndAreServedOnce()
        {
            var s = ReadyAndInitialCapturesDone();
            s.ObserveNonces(new RefreshNonces { State = 5 });
            s.ObserveNonces(new RefreshNonces { State = 7 });
            s.ObserveNonces(new RefreshNonces { State = 6 }); // older value: ignored
            Assert.Equal(7L, s.Seen.State);
            Assert.Equal(7L, s.OnCaptureStarted(CaptureFamily.State, 1.0, Day, false));
            Assert.Null(s.OnCaptureStarted(CaptureFamily.State, 2.0, Day, false));

            // Re-reading the same file must not serve it again.
            s.ObserveNonces(new RefreshNonces { State = 7 });
            Assert.Null(s.Next(10.0, false, Day, Module));
            Assert.Null(s.OnCaptureStarted(CaptureFamily.State, 10.0, Day, false));
        }

        [Fact]
        public void Nonces_ArePerScope()
        {
            var s = ReadyAndInitialCapturesDone();
            s.ObserveNonces(new RefreshNonces { State = 5, History = 9 });
            Assert.Equal(5L, s.OnCaptureStarted(CaptureFamily.State, 1.0, Day, false));
            Assert.Null(s.OnCaptureStarted(CaptureFamily.Static, 1.0, Day, false));
            Assert.Equal(9L, s.OnCaptureStarted(CaptureFamily.History, 1.0, Day, false));
        }

        [Fact]
        public void PeriodicCaptureServesAPendingNonce()
        {
            var s = ReadyAndInitialCapturesDone();
            s.ObserveNonces(new RefreshNonces { State = 42 });
            // Whatever starts the next state capture covers the pending request.
            Assert.Equal(42L, s.OnCaptureStarted(CaptureFamily.State, 5.0, Day + 1, false));
        }

        [Fact]
        public void NoncesSeenOutsideReady_AreServedByReadyEntryCaptures()
        {
            var s = new Scheduler();
            s.ObserveNonces(new RefreshNonces { State = 3, History = 4, Static = 5 });
            s.OnEnteredReady(Day, Module);
            Assert.Equal(CaptureFamily.Static, s.Next(0, false, Day, Module));
            Assert.Equal(5L, s.OnCaptureStarted(CaptureFamily.Static, 0, Day, true));
            Assert.Equal(CaptureFamily.State, s.Next(0, false, Day, Module));
            Assert.Equal(3L, s.OnCaptureStarted(CaptureFamily.State, 0, Day, true));
            Assert.Equal(CaptureFamily.History, s.Next(0, false, Day, Module));
            Assert.Equal(4L, s.OnCaptureStarted(CaptureFamily.History, 0, Day, true));
            // Nothing left to serve: no extra refresh-driven capture afterwards.
            Assert.Null(s.Next(1.5, false, Day, Module));
            Assert.Null(s.Next(3.0, false, Day, Module));
        }

        [Fact]
        public void NoncesSeenAcrossLeaveAndReEnter_AreServedOnNextReadyEntry()
        {
            var s = ReadyAndInitialCapturesDone();
            s.OnLeftReady();
            s.ObserveNonces(new RefreshNonces { State = 8 });
            s.OnEnteredReady(Day, Module);
            Assert.Equal(CaptureFamily.Static, s.Next(0, false, Day, Module));
            Assert.Null(s.OnCaptureStarted(CaptureFamily.Static, 0, Day, true));
            Assert.Equal(CaptureFamily.State, s.Next(0, false, Day, Module));
            Assert.Equal(8L, s.OnCaptureStarted(CaptureFamily.State, 0, Day, true));
        }

        [Fact]
        public void DiscardPendingNonces_MakesThemNeverServed()
        {
            var s = new Scheduler();
            s.ObserveNonces(new RefreshNonces { State = 3, History = 4, Static = 5 });
            s.DiscardPendingNonces(); // e.g. arrived while disabled / unsupported_build
            s.OnEnteredReady(Day, Module);
            for (int i = 0; i < 3; i++)
            {
                var f = s.Next(0, false, Day, Module);
                Assert.True(f.HasValue);
                Assert.Null(s.OnCaptureStarted(f.Value, 0, Day, true));
            }
            Assert.Null(s.Next(2.0, false, Day, Module));

            // A newer nonce after the discard is served normally.
            s.ObserveNonces(new RefreshNonces { State = 6 });
            Assert.Equal(CaptureFamily.State, s.Next(2.0, false, Day, Module));
            Assert.Equal(6L, s.OnCaptureStarted(CaptureFamily.State, 2.0, Day, false));
        }

        [Fact]
        public void LeftReady_ClearsPendingCaptures_ReEntryQueuesThemAgain()
        {
            var s = new Scheduler();
            s.OnEnteredReady(Day, Module);
            s.OnLeftReady();
            Assert.NotEqual(CaptureFamily.Static, s.Next(0, true, Day, Module));
            s.OnEnteredReady(Day, Module);
            Assert.Equal(CaptureFamily.Static, s.Next(0, false, Day, Module));
        }

        [Fact]
        public void Backoff_CeilingBreachDoublesIntervalAndSetsDegraded()
        {
            var s = ReadyAndInitialCapturesDone();
            Assert.False(s.Degraded);
            Assert.Equal(5, s.EffectiveIntervalS(false));

            // 60 ms capture: adaptive interval max(5, 6) = 6 s, doubled per consecutive breach.
            s.OnCaptureFinished(CaptureFamily.State, 60, Utc0);
            Assert.True(s.Degraded);
            Assert.Equal(1, s.ConsecutiveBreaches);
            Assert.Equal(12, s.EffectiveIntervalS(false));
            Assert.False(s.OptionalSuppressed);

            s.OnCaptureFinished(CaptureFamily.State, 60, Utc0);
            Assert.Equal(24, s.EffectiveIntervalS(false));
            Assert.False(s.OptionalSuppressed);

            s.OnCaptureFinished(CaptureFamily.State, 60, Utc0);
            Assert.Equal(48, s.EffectiveIntervalS(false));
            Assert.Equal(3, s.ConsecutiveBreaches);
            Assert.True(s.OptionalSuppressed);
        }

        [Fact]
        public void Backoff_IsCappedAt120Seconds()
        {
            var s = ReadyAndInitialCapturesDone();
            for (int i = 0; i < 10; i++) s.OnCaptureFinished(CaptureFamily.State, 60, Utc0);
            Assert.Equal(Scheduler.BackoffCapS, s.EffectiveIntervalS(false));
            Assert.Equal(120, s.EffectiveIntervalS(false));
        }

        [Fact]
        public void Backoff_DelaysThePeriodicCapture()
        {
            var s = ReadyAndInitialCapturesDone();
            s.OnCaptureFinished(CaptureFamily.State, 60, Utc0);
            Assert.Null(s.Next(5.0, false, Day + 1, Module));
            Assert.Null(s.Next(11.9, false, Day + 1, Module));
            Assert.Equal(CaptureFamily.State, s.Next(12.0, false, Day + 1, Module));
        }

        [Fact]
        public void Backoff_ResetsAfterACaptureUnderTheCeiling()
        {
            var s = ReadyAndInitialCapturesDone();
            s.OnCaptureFinished(CaptureFamily.State, 60, Utc0);
            s.OnCaptureFinished(CaptureFamily.State, 60, Utc0);
            s.OnCaptureFinished(CaptureFamily.State, 10, Utc0.AddSeconds(5));
            Assert.Equal(0, s.ConsecutiveBreaches);
            Assert.Equal(5, s.EffectiveIntervalS(false));
            // Degraded is cleared once no breach happened for over a minute.
            s.OnCaptureFinished(CaptureFamily.State, 10, Utc0.AddMinutes(2));
            Assert.False(s.Degraded);
        }

        [Fact]
        public void Backoff_ThreeBreachesMustBeConsecutive()
        {
            var s = ReadyAndInitialCapturesDone();
            s.OnCaptureFinished(CaptureFamily.State, 60, Utc0);
            s.OnCaptureFinished(CaptureFamily.State, 60, Utc0);
            s.OnCaptureFinished(CaptureFamily.State, 10, Utc0);
            s.OnCaptureFinished(CaptureFamily.State, 60, Utc0);
            s.OnCaptureFinished(CaptureFamily.State, 60, Utc0);
            Assert.False(s.OptionalSuppressed);
            s.OnCaptureFinished(CaptureFamily.State, 60, Utc0);
            Assert.True(s.OptionalSuppressed);
        }

        [Fact]
        public void OptionalSuppression_LastsUntilTheNextWorldSession()
        {
            var s = ReadyAndInitialCapturesDone();
            for (int i = 0; i < 3; i++) s.OnCaptureFinished(CaptureFamily.State, 60, Utc0);
            Assert.True(s.OptionalSuppressed);
            s.OnCaptureFinished(CaptureFamily.State, 10, Utc0.AddMinutes(5));
            Assert.True(s.OptionalSuppressed);
            s.OnLeftReady();
            s.OnEnteredReady(Day, Module);
            Assert.False(s.OptionalSuppressed);
            Assert.False(s.Degraded);
            Assert.Equal(5, s.EffectiveIntervalS(false));
        }

        [Fact]
        public void Adaptive_IntervalIsHundredTimesLastCaptureTime()
        {
            var s = ReadyAndInitialCapturesDone(ObserverConfig.Parse("{\"capture_ceiling_ms\": 200}"));
            s.OnCaptureFinished(CaptureFamily.State, 49, Utc0);
            Assert.Equal(5, s.EffectiveIntervalS(false)); // max(5, 4.9)
            s.OnCaptureFinished(CaptureFamily.State, 150, Utc0);
            Assert.False(s.Degraded);
            Assert.Equal(15, s.EffectiveIntervalS(false), 6); // max(5, 100 * 150 / 1000)
            s.OnCaptureFinished(CaptureFamily.State, 180, Utc0);
            Assert.Equal(18, s.EffectiveIntervalS(false), 6);
            Assert.Equal(18, s.EffectiveIntervalS(true), 6); // max(15, 18)
            Assert.Equal(180, s.LastCaptureMainThreadMs);
        }

        [Fact]
        public void Adaptive_OnlyStateCapturesSetLastCaptureTime()
        {
            var s = ReadyAndInitialCapturesDone(ObserverConfig.Parse("{\"capture_ceiling_ms\": 200}"));
            s.OnCaptureFinished(CaptureFamily.State, 20, Utc0);
            s.OnCaptureFinished(CaptureFamily.History, 190, Utc0);
            Assert.Equal(20, s.LastCaptureMainThreadMs);
            Assert.Equal(5, s.EffectiveIntervalS(false));
        }

        [Fact]
        public void ModuleIdChange_QueuesStaticCapture()
        {
            var s = ReadyAndInitialCapturesDone();
            Assert.Equal(CaptureFamily.Static, s.Next(0.1, false, Day, "module-b"));
            s.OnCaptureStarted(CaptureFamily.Static, 0.1, Day, false);
            Assert.Null(s.Next(0.2, false, Day, "module-b"));
        }

        [Fact]
        public void MonthChange_QueuesHistoryCapture()
        {
            var s = ReadyAndInitialCapturesDone();
            Assert.Null(s.Next(0.1, false, 120, Module)); // Y1-04-30, same month
            Assert.Equal(CaptureFamily.History, s.Next(0.2, false, 121, Module)); // Y1-05-01
            s.OnCaptureStarted(CaptureFamily.History, 0.2, 121, false);
            Assert.Null(s.Next(0.3, false, 125, Module));
        }

        [Fact]
        public void MonthKey_UsesThirtyDayMonths()
        {
            Assert.Equal(Scheduler.MonthKey(91), Scheduler.MonthKey(120));
            Assert.NotEqual(Scheduler.MonthKey(120), Scheduler.MonthKey(121));
            Assert.Equal(int.MinValue, Scheduler.MonthKey(int.MinValue));
        }

        // ---- Behaviour required by the PRD that the implementation does not follow (see Skip reasons).

        [Fact]
        public void Refresh_IsSubjectToBackoff()
        {
            var s = ReadyAndInitialCapturesDone();
            // 60 ms capture: effective = max(5, 100 * 60 / 1000 = 6) = 6 s; the breach doubles it to 12 s.
            s.OnCaptureFinished(CaptureFamily.State, 60, Utc0);
            Assert.Equal(12, s.EffectiveIntervalS(false));
            s.ObserveNonces(new RefreshNonces { State = 1 });
            Assert.Null(s.Next(2.0, false, Day, Module));
            Assert.Null(s.Next(11.9, false, Day, Module));
            Assert.Equal(CaptureFamily.State, s.Next(12.0, false, Day, Module));
        }

        [Fact]
        public void Backoff_OnlyStateCapturesCountAgainstTheCeiling()
        {
            var s = ReadyAndInitialCapturesDone();
            s.OnCaptureFinished(CaptureFamily.Static, 500, Utc0);
            s.OnCaptureFinished(CaptureFamily.History, 500, Utc0);
            s.OnCaptureFinished(CaptureFamily.Static, 500, Utc0);
            Assert.False(s.Degraded);
            Assert.Equal(0, s.ConsecutiveBreaches);
            Assert.False(s.OptionalSuppressed);
            Assert.Equal(5, s.EffectiveIntervalS(false));
        }

        [Fact]
        public void Backoff_WhilePaused_DoublesThePausedInterval()
        {
            var s = ReadyAndInitialCapturesDone();
            Assert.Equal(15, s.EffectiveIntervalS(true));
            s.OnCaptureFinished(CaptureFamily.State, 60, Utc0);
            Assert.Equal(30, s.EffectiveIntervalS(true));
        }
    }
}
