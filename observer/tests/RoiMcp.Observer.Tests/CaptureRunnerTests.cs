using System;
using System.Collections.Generic;
using System.Diagnostics;
using RoiMcp.Observer.Core;
using Xunit;

namespace RoiMcp.Observer.Tests
{
    public class CaptureRunnerTests
    {
        private static CaptureOptions Options(bool aiDetail = false, bool aiRoutes = false, bool paths = false, bool suppressed = false)
        {
            return new CaptureOptions
            {
                IncludeAiBuildingDetail = aiDetail,
                IncludeAiRoutes = aiRoutes,
                IncludeRoutePaths = paths,
                OptionalSuppressed = suppressed,
                WorldSession = "ws",
            };
        }

        private static CaptureRunner Runner(TestData data, List<ISection> sections, SectionHealth health = null, FakeClock clock = null,
            CaptureOptions options = null, Stopwatch frame = null, double ceilingMs = 1000)
        {
            return new CaptureRunner(CaptureFamily.State, data, sections, health ?? new SectionHealth(), clock ?? new FakeClock { Day = 100, Frame = 1 },
                options ?? Options(), frame ?? new Stopwatch(), ceilingMs);
        }

        /// <summary>Runs to completion with an unlimited budget (a stopped, zero frame watch never asks to yield).</summary>
        private static void RunToEnd(CaptureRunner r)
        {
            for (int i = 0; i < 10000 && !r.Done; i++) r.Step(2.0);
            Assert.True(r.Done);
        }

        private static IEnumerator<bool> Assign(SectionContext ctx, string name, object value)
        {
            var d = (TestData)ctx.Data;
            d.Order.Add(name);
            ctx.Items = 1;
            yield return true;
            d.Values[name] = value; // assigned as the very last step
        }

        private static void Throw()
        {
            throw new InvalidOperationException("boom");
        }

        private static IEnumerator<bool> ThrowsImmediately(SectionContext ctx)
        {
            Throw();
            yield break;
        }

        private static IEnumerator<bool> ThrowsAfterPartialWork(SectionContext ctx, string name)
        {
            var partial = new List<int>();
            for (int i = 0; i < 3; i++)
            {
                partial.Add(i);
                ctx.Items++;
                yield return true;
            }
            Throw();
            ((TestData)ctx.Data).Values[name] = partial;
        }

        private static ISection Ok(string name, object value)
        {
            return new FakeSection(name, ctx => Assign(ctx, name, value));
        }

        [Fact]
        public void SectionsRunInOrderAndDataIsAssigned()
        {
            var data = new TestData();
            var r = Runner(data, new List<ISection> { Ok("a", 1), Ok("b", 2), Ok("c", 3) });
            RunToEnd(r);
            Assert.Equal(new[] { "a", "b", "c" }, data.Order.ToArray());
            Assert.Equal(1, data.Values["a"]);
            Assert.Equal(2, data.Values["b"]);
            Assert.Equal(3, data.Values["c"]);
            foreach (var n in new[] { "a", "b", "c" })
            {
                Assert.Equal("ok", r.Sections[n].status);
                Assert.Equal(1, r.Sections[n].items);
                Assert.Equal(100, r.Sections[n].game_day);
            }
            Assert.Same(data, r.Data);
            Assert.Equal(CaptureFamily.State, r.Family);
            Assert.True(r.Consistent);
            Assert.Equal(100, r.GameDayStart);
            Assert.Equal(100, r.GameDayEnd);
            Assert.True(r.Slices >= 1);
        }

        [Fact]
        public void FailingSection_IsFailedWithNoData_LaterSectionsStillRun()
        {
            var data = new TestData();
            var r = Runner(data, new List<ISection>
            {
                Ok("a", 1),
                new FakeSection("bad", ThrowsImmediately),
                new FakeSection("bad_late", ctx => ThrowsAfterPartialWork(ctx, "bad_late")),
                Ok("c", 3),
            });
            RunToEnd(r);
            Assert.Equal("ok", r.Sections["a"].status);
            Assert.Equal("failed", r.Sections["bad"].status);
            Assert.Equal("InvalidOperationException", r.Sections["bad"].reason);
            Assert.Equal("failed", r.Sections["bad_late"].status);
            Assert.Equal("ok", r.Sections["c"].status);
            Assert.False(data.Values.ContainsKey("bad"));
            Assert.False(data.Values.ContainsKey("bad_late"));
            Assert.Equal(3, data.Values["c"]);
        }

        [Fact]
        public void SectionWhoseRunThrows_IsFailed()
        {
            var data = new TestData();
            var r = Runner(data, new List<ISection>
            {
                new FakeSection("throws_in_run", ctx => { throw new NotSupportedException("x"); }),
                Ok("b", 2),
            });
            RunToEnd(r);
            Assert.Equal("failed", r.Sections["throws_in_run"].status);
            Assert.Equal("NotSupportedException", r.Sections["throws_in_run"].reason);
            Assert.Equal("ok", r.Sections["b"].status);
        }

        [Fact]
        public void ThreeConsecutiveFailures_DisableTheSectionForTheSession()
        {
            var health = new SectionHealth();
            var bad = new FakeSection("bad", ThrowsImmediately);
            for (int i = 0; i < 3; i++)
            {
                var r = Runner(new TestData(), new List<ISection> { bad, Ok("ok", 1) }, health);
                RunToEnd(r);
                Assert.Equal("failed", r.Sections["bad"].status);
            }
            Assert.True(health.IsDisabled("bad"));
            Assert.Equal(3, bad.RunCalls);

            var fourth = Runner(new TestData(), new List<ISection> { bad, Ok("ok", 1) }, health);
            RunToEnd(fourth);
            Assert.Equal("disabled", fourth.Sections["bad"].status);
            Assert.StartsWith("failed_repeatedly", fourth.Sections["bad"].reason);
            Assert.Equal(3, bad.RunCalls); // not run any more
            Assert.Equal("ok", fourth.Sections["ok"].status);

            var snap = health.Snapshot();
            Assert.Single(snap);
            Assert.Equal("bad", snap[0].section);
            Assert.NotNull(snap[0].error_signature);

            health.Reset(); // next world session
            Assert.False(health.IsDisabled("bad"));
        }

        [Fact]
        public void NonConsecutiveFailures_DoNotDisable()
        {
            var health = new SectionHealth();
            bool fail = true;
            var flaky = new FakeSection("flaky", ctx => fail ? ThrowsImmediately(ctx) : Assign(ctx, "flaky", 1));
            foreach (var f in new[] { true, true, false, true, true })
            {
                fail = f;
                RunToEnd(Runner(new TestData(), new List<ISection> { flaky }, health));
            }
            Assert.False(health.IsDisabled("flaky"));
            fail = true;
            RunToEnd(Runner(new TestData(), new List<ISection> { flaky }, health));
            Assert.True(health.IsDisabled("flaky"));
        }

        [Fact]
        public void SectionHealth_RecordFailureReportsDisableOnce()
        {
            var h = new SectionHealth();
            Assert.False(h.RecordFailure("s", "sig"));
            Assert.False(h.RecordFailure("s", "sig"));
            Assert.True(h.RecordFailure("s", "sig"));
            Assert.False(h.RecordFailure("s", "sig"));
            Assert.Equal("failed_repeatedly", h.Get("s").reason);
            Assert.Null(h.Get("other"));
        }

        [Fact]
        public void DisableForReflection_MarksDisabled()
        {
            var health = new SectionHealth();
            health.DisableForReflection("routes_player", "ManualDestinationSlot._minStoredAtSource");
            var section = new FakeSection("routes_player", ctx => Assign(ctx, "routes_player", 1));
            var data = new TestData();
            var r = Runner(data, new List<ISection> { section }, health);
            RunToEnd(r);
            Assert.Equal("disabled", r.Sections["routes_player"].status);
            Assert.Equal("reflection_missing: ManualDestinationSlot._minStoredAtSource", r.Sections["routes_player"].reason);
            Assert.Equal(0, section.RunCalls);
            Assert.False(data.Values.ContainsKey("routes_player"));
        }

        [Fact]
        public void OptionalSectionWithOptionOff_IsSkipped()
        {
            var data = new TestData();
            var sections = new List<ISection>
            {
                new FakeSection("buildings_ai_detail", ctx => Assign(ctx, "buildings_ai_detail", 1), true),
                new FakeSection("routes_ai", ctx => Assign(ctx, "routes_ai", 1), true),
                new FakeSection("route_paths", ctx => Assign(ctx, "route_paths", 1), true),
            };
            var r = Runner(data, sections, options: Options(aiDetail: false, aiRoutes: true, paths: false));
            RunToEnd(r);
            Assert.Equal("skipped", r.Sections["buildings_ai_detail"].status);
            Assert.Equal("optional_off", r.Sections["buildings_ai_detail"].reason);
            Assert.Equal("ok", r.Sections["routes_ai"].status);
            Assert.Equal("skipped", r.Sections["route_paths"].status);
            Assert.Equal(0, ((FakeSection)sections[0]).RunCalls);
            Assert.False(data.Values.ContainsKey("buildings_ai_detail"));
            Assert.True(data.Values.ContainsKey("routes_ai"));
        }

        [Fact]
        public void OptionalSuppressed_SkipsEvenEnabledOptionalSections()
        {
            var data = new TestData();
            var opt = new FakeSection("routes_ai", ctx => Assign(ctx, "routes_ai", 1), true);
            var r = Runner(data, new List<ISection> { opt, Ok("routes_player", 2) }, options: Options(aiDetail: true, aiRoutes: true, paths: true, suppressed: true));
            RunToEnd(r);
            Assert.Equal("skipped", r.Sections["routes_ai"].status);
            Assert.Equal("optional_suppressed_after_ceiling_breaches", r.Sections["routes_ai"].reason);
            Assert.Equal(0, opt.RunCalls);
            Assert.Equal("ok", r.Sections["routes_player"].status); // required sections still run
        }

        private static void Spin(double ms)
        {
            var sw = Stopwatch.StartNew();
            while (sw.Elapsed.TotalMilliseconds < ms) { }
        }

        private sealed class Progress
        {
            public int Items;
        }

        private static IEnumerator<bool> Work(SectionContext ctx, int items, double msPerItem, Progress progress)
        {
            for (int i = 0; i < items; i++)
            {
                Spin(msPerItem);
                progress.Items++;
                ctx.Items++;
                if (ctx.ShouldYield) yield return true;
            }
            ((TestData)ctx.Data).Values["work"] = items;
        }

        [Fact]
        public void Budget_TinyFrameBudgetSpreadsTheCaptureOverSeveralSteps()
        {
            var data = new TestData();
            var progress = new Progress();
            var frame = new Stopwatch();
            var r = Runner(data, new List<ISection> { new FakeSection("work", ctx => Work(ctx, 20, 1.0, progress)) }, frame: frame);
            var perStep = new List<int>();
            int steps = 0;
            while (!r.Done && steps < 1000)
            {
                frame.Restart(); // a new frame
                int before = progress.Items;
                r.Step(2.0);
                perStep.Add(progress.Items - before);
                steps++;
            }
            Assert.True(r.Done);
            Assert.Equal(20, progress.Items);
            Assert.Equal(20, data.Values["work"]);
            Assert.Equal(steps, r.Slices);
            Assert.True(r.Slices > 1);
            Assert.True(r.Slices >= 7, "slices: " + r.Slices);
            foreach (var n in perStep) Assert.True(n <= 3, "items processed in one slice: " + n);
            Assert.True(r.MaxSliceMs > 0);
            Assert.True(r.MainThreadMs >= 20);
            Assert.Equal(20, r.Sections["work"].items);
        }

        [Fact]
        public void Budget_FrameAlreadyOverBudgetDoesNoWork()
        {
            var data = new TestData();
            var progress = new Progress();
            var frame = Stopwatch.StartNew();
            Spin(3);
            var r = Runner(data, new List<ISection> { new FakeSection("work", ctx => Work(ctx, 5, 0, progress)) }, frame: frame);
            r.Step(1.0);
            Assert.Equal(0, progress.Items);
            Assert.False(r.Done);
        }

        [Fact]
        public void OptionalSectionAfterCeiling_IsOverBudget()
        {
            var data = new TestData();
            var progress = new Progress();
            var frame = new Stopwatch();
            var opt = new FakeSection("routes_ai", ctx => Assign(ctx, "routes_ai", 1), true);
            var r = Runner(data, new List<ISection> { new FakeSection("work", ctx => Work(ctx, 6, 1.0, progress)), opt },
                options: Options(aiRoutes: true), frame: frame, ceilingMs: 2.0);
            for (int i = 0; i < 1000 && !r.Done; i++)
            {
                frame.Restart();
                r.Step(1.5);
            }
            Assert.Equal("ok", r.Sections["work"].status);
            Assert.Equal("over_budget", r.Sections["routes_ai"].status);
            Assert.Equal(0, opt.RunCalls);
        }

        [Fact]
        public void Abandon_DisposesTheIteratorWithoutThrowing()
        {
            var data = new TestData();
            bool disposed = false;
            var frame = new Stopwatch();
            var section = new FakeSection("long", ctx => LongRunning(ctx, () => disposed = true));
            var r = Runner(data, new List<ISection> { section }, frame: frame);
            frame.Restart();
            r.Step(5); // long enough for the iterator to start even when the first call pays JIT costs
            Assert.False(r.Done);
            r.Abandon();
            Assert.True(r.Done);
            Assert.True(disposed);
            Assert.False(data.Values.ContainsKey("long"));
            r.Step(1000); // no-op after abandon
            r.Abandon();  // idempotent
        }

        private static IEnumerator<bool> LongRunning(SectionContext ctx, Action onDispose)
        {
            try
            {
                for (int i = 0; i < 3; i++)
                {
                    ctx.Items++;
                    yield return true;
                }
                while (true) yield return true; // never finishes on its own

            }
            finally
            {
                onDispose();
            }
        }

        [Fact]
        public void Abandon_SwallowsExceptionsFromDispose()
        {
            var frame = new Stopwatch();
            var section = new FakeSection("bad_dispose", ctx => ThrowingFinally(ctx));
            var r = Runner(new TestData(), new List<ISection> { section }, frame: frame);
            frame.Restart();
            r.Step(1);
            Assert.False(r.Done);
            r.Abandon();
            Assert.True(r.Done);
        }

        private static IEnumerator<bool> ThrowingFinally(SectionContext ctx)
        {
            try
            {
                while (true)
                {
                    Spin(2);
                    yield return true;
                }
            }
            finally
            {
                Throw();
            }
        }

        [Fact]
        public void DayChangeMidCapture_IsNotConsistent()
        {
            var clock = new FakeClock { Day = 100 };
            var data = new TestData();
            var r = Runner(data, new List<ISection>
            {
                Ok("a", 1),
                new FakeSection("b", ctx => DayTicks(ctx, clock)),
                Ok("c", 3),
            }, clock: clock);
            RunToEnd(r);
            Assert.False(r.Consistent);
            Assert.Equal(100, r.GameDayStart);
            Assert.Equal(101, r.GameDayEnd);
            Assert.Equal(100, r.Sections["a"].game_day);
            Assert.Equal(101, r.Sections["b"].game_day);
            Assert.Equal(101, r.Sections["c"].game_day);
        }

        private static IEnumerator<bool> DayTicks(SectionContext ctx, FakeClock clock)
        {
            yield return true;
            clock.Day = 101;
            clock.Frame++;
            ((TestData)ctx.Data).Values["b"] = 2;
        }

        [Fact]
        public void ClockFailure_DoesNotEscape()
        {
            var r = new CaptureRunner(CaptureFamily.History, new TestData(), new List<ISection> { Ok("a", 1) }, new SectionHealth(),
                new ThrowingClock(), Options(), new Stopwatch(), 1000);
            RunToEnd(r);
            Assert.Equal("ok", r.Sections["a"].status);
            Assert.Equal(0, r.GameDayStart);
        }

        private sealed class ThrowingClock : ICaptureClock
        {
            public int GameDay() { throw new InvalidOperationException("world gone"); }
            public long FrameCount() { throw new InvalidOperationException("world gone"); }
        }

        [Fact]
        public void FrameNumbersAreRecorded()
        {
            var clock = new FakeClock { Day = 5, Frame = 1000 };
            var r = Runner(new TestData(), new List<ISection> { Ok("a", 1) }, clock: clock);
            RunToEnd(r);
            Assert.Equal(1000, r.FrameStart);
            Assert.Equal(1000, r.FrameEnd);
            Assert.Equal(1000, r.Sections["a"].frame_start);
        }
    }
}
