using RoiMcp.Observer.Core;
using Xunit;

namespace RoiMcp.Observer.Tests
{
    public class FrameStatsTests
    {
        [Fact]
        public void Empty_HasNoPercentiles()
        {
            var dto = new FrameStats().Summarize(10, 60);
            Assert.Equal(0, dto.frames);
            Assert.Null(dto.frame_ms_p50);
            Assert.Null(dto.frame_ms_p99);
            Assert.Equal(0, dto.frames_over_50ms);
            Assert.Equal(60, dto.window_s);
        }

        [Fact]
        public void Percentiles_ForAKnownDistribution()
        {
            // Frame times 1..100 ms, observer times 0.01..1.00 ms, inserted in shuffled order.
            var fs = new FrameStats();
            int[] order = new int[100];
            for (int i = 0; i < 100; i++) order[i] = (i * 37) % 100 + 1; // permutation of 1..100
            for (int i = 0; i < 100; i++) fs.Add(i * 0.1, order[i], order[i] / 100.0);
            var dto = fs.Summarize(10.0, 60);
            Assert.Equal(100, dto.frames);
            Assert.Equal(50, dto.frame_ms_p50);
            Assert.Equal(95, dto.frame_ms_p95);
            Assert.Equal(99, dto.frame_ms_p99);
            Assert.Equal(50.5, dto.frame_ms_avg);
            Assert.Equal(50, dto.frames_over_50ms); // 51..100
            Assert.Equal(0.99, dto.observer_ms_p99.Value, 3);
            Assert.Equal(1.0, dto.observer_ms_max, 3);
        }

        [Fact]
        public void Window_ExcludesOldSamples()
        {
            var fs = new FrameStats();
            for (int i = 0; i < 50; i++) fs.Add(i * 0.1, 500, 9); // old spikes at t < 5 s
            for (int i = 0; i < 10; i++) fs.Add(100 + i, 10, 1);  // recent
            var dto = fs.Summarize(110, 60);
            Assert.Equal(10, dto.frames);
            Assert.Equal(0, dto.frames_over_50ms);
            Assert.Equal(10, dto.frame_ms_p99);
            Assert.Equal(1, dto.observer_ms_max);
        }

        [Fact]
        public void FramesOver50ms_AreCounted()
        {
            var fs = new FrameStats();
            double[] ms = { 16, 50, 50.5, 120, 33, 51, 49.9 };
            for (int i = 0; i < ms.Length; i++) fs.Add(i, ms[i], 0);
            var dto = fs.Summarize(ms.Length, 60);
            Assert.Equal(3, dto.frames_over_50ms); // 50.5, 120, 51 (50 itself is not over)
            Assert.Equal(120, dto.frame_ms_p99);
        }

        [Fact]
        public void TicksWithGc_AreCountedAndExcludedFromTheCleanMax()
        {
            var fs = new FrameStats();
            fs.Add(1.0, 10, 0.2);
            fs.Add(1.1, 100, 92.4, true);
            fs.Add(1.2, 10, 0.3);
            fs.Add(1.3, 40, 30.0, true);
            var dto = fs.Summarize(1.3, 60);
            Assert.Equal(92.4, dto.observer_ms_max, 3);
            Assert.Equal(0.3, dto.observer_ms_max_without_gc, 3);
            Assert.Equal(2, dto.observer_ticks_with_gc);
        }

        [Fact]
        public void Clear_ResetsTheWindow()
        {
            var fs = new FrameStats();
            fs.Add(0, 100, 1);
            fs.Clear();
            Assert.Equal(0, fs.Summarize(1, 60).frames);
        }

        [Fact]
        public void RingBufferWrapsAround()
        {
            var fs = new FrameStats();
            for (int i = 0; i < 10000; i++) fs.Add(i * 0.001, i < 9000 ? 100 : 5, 0);
            var dto = fs.Summarize(10, 60);
            Assert.Equal(8192, dto.frames);
            Assert.Equal(7192, dto.frames_over_50ms);
        }
    }
}
