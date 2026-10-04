using System;
using RoiMcp.Observer.Dto;

namespace RoiMcp.Observer.Core
{
    /// <summary>
    /// Rolling frame-time statistics over the last 60 s of unscaled time (PRD 11.5, E3). Percentiles come from
    /// fixed-width histograms (0.05 ms buckets) instead of sorting, so a summary costs O(samples) with no
    /// allocation besides the DTO and does not create its own frame-time spike.
    /// </summary>
    public sealed class FrameStats
    {
        private const int Capacity = 8192;
        private const double BucketMs = 0.05;
        private const int Buckets = 4000; // 200 ms; larger values land in the last bucket

        private readonly float[] _frameMs = new float[Capacity];
        private readonly float[] _observerMs = new float[Capacity];
        private readonly double[] _time = new double[Capacity];
        private readonly bool[] _gc = new bool[Capacity];
        private readonly int[] _frameHist = new int[Buckets];
        private readonly int[] _observerHist = new int[Buckets];
        private readonly float[] _scratch = new float[Capacity];
        private int _head;
        private int _count;

        /// <param name="gcDuringObserver">A garbage collection ran while the observer's tick was on the stack, so
        /// the tick's time includes a stop-the-world pause that may have been triggered by any thread.</param>
        public void Add(double unscaledTime, double frameMs, double observerMs, bool gcDuringObserver = false)
        {
            _frameMs[_head] = (float)frameMs;
            _observerMs[_head] = (float)observerMs;
            _gc[_head] = gcDuringObserver;
            _time[_head] = unscaledTime;
            _head = (_head + 1) % Capacity;
            if (_count < Capacity) _count++;
        }

        public void Clear()
        {
            _head = 0;
            _count = 0;
        }

        private static int Bucket(float ms)
        {
            if (!(ms > 0)) return 0;
            int b = (int)(ms / BucketMs);
            return b >= Buckets ? Buckets - 1 : b;
        }

        public FrameStatsDto Summarize(double now, double windowS)
        {
            Array.Clear(_frameHist, 0, Buckets);
            Array.Clear(_observerHist, 0, Buckets);
            int n = 0;
            int over50 = 0;
            double sum = 0;
            float obsMax = 0;
            float obsMaxNoGc = 0;
            int gcTicks = 0;
            for (int i = 0; i < _count; i++)
            {
                int idx = (_head - 1 - i + Capacity) % Capacity;
                if (now - _time[idx] > windowS) break;
                float f = _frameMs[idx];
                float o = _observerMs[idx];
                _frameHist[Bucket(f)]++;
                _observerHist[Bucket(o)]++;
                sum += f;
                if (f > 50f) over50++;
                if (o > obsMax) obsMax = o;
                if (_gc[idx]) gcTicks++;
                else if (o > obsMaxNoGc) obsMaxNoGc = o;
                n++;
            }
            var dto = new FrameStatsDto
            {
                window_s = windowS,
                frames = n,
                frames_over_50ms = over50,
                observer_ms_max = Round(obsMax),
                observer_ms_max_without_gc = Round(obsMaxNoGc),
                observer_ticks_with_gc = gcTicks,
            };
            if (n == 0) return dto;
            dto.frame_ms_p50 = Percentile(_frameHist, _frameMs, n, now, windowS, 0.50);
            dto.frame_ms_p95 = Percentile(_frameHist, _frameMs, n, now, windowS, 0.95);
            dto.frame_ms_p99 = Percentile(_frameHist, _frameMs, n, now, windowS, 0.99);
            dto.frame_ms_avg = Round(sum / n);
            dto.observer_ms_p99 = Percentile(_observerHist, _observerMs, n, now, windowS, 0.99);
            return dto;
        }

        /// <summary>
        /// Exact nearest-rank percentile: the histogram locates the bucket, then only that bucket's samples are
        /// sorted (a handful, not the whole window).
        /// </summary>
        private double Percentile(int[] hist, float[] values, int n, double now, double windowS, double p)
        {
            int rank = (int)Math.Ceiling(p * n);
            if (rank < 1) rank = 1;
            int cum = 0;
            int bucket = Buckets - 1;
            for (int b = 0; b < Buckets; b++)
            {
                if (cum + hist[b] >= rank)
                {
                    bucket = b;
                    break;
                }
                cum += hist[b];
            }
            int m = 0;
            for (int i = 0; i < n; i++)
            {
                int idx = (_head - 1 - i + Capacity) % Capacity;
                if (Bucket(values[idx]) == bucket) _scratch[m++] = values[idx];
            }
            if (m == 0) return Round((bucket + 1) * BucketMs);
            Array.Sort(_scratch, 0, m);
            int k = rank - cum - 1;
            if (k < 0) k = 0;
            if (k >= m) k = m - 1;
            return Round(_scratch[k]);
        }

        private static double Round(double v)
        {
            return Math.Round(v, 3);
        }
    }
}
