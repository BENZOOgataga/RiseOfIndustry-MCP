using System;
using System.Collections.Generic;
using System.Linq;
using RoiMcp.Observer.Diagnostics;
using Xunit;

namespace RoiMcp.Observer.Tests
{
    /// <summary>
    /// Log is static. Each test drains the queue first and uses unique markers/signatures, so it does not
    /// depend on lines or signatures left by other tests. Test parallelization is disabled for the assembly.
    /// </summary>
    [Collection("Log")]
    public class LogTests
    {
        private static void DrainAll()
        {
            var sink = new List<string>();
            while (Log.Drain(sink, 10000) > 0) sink.Clear();
        }

        [Fact]
        public void BoundedQueue_DropsBeyondCapacityAndCountsDrops()
        {
            Log.SetLevel("info");
            DrainAll();
            int droppedBefore = Log.Dropped;
            string marker = Guid.NewGuid().ToString("N");
            for (int i = 0; i < Log.Capacity + 50; i++) Log.Info("test", marker + " " + i);
            Assert.Equal(droppedBefore + 50, Log.Dropped);

            var lines = new List<string>();
            Log.Drain(lines, 100000);
            Assert.Equal(Log.Capacity, lines.Count);
            Assert.EndsWith(marker + " 0", lines[0]);
            Assert.EndsWith(marker + " " + (Log.Capacity - 1), lines[lines.Count - 1]);

            // Space is available again after draining.
            Log.Info("test", marker + " again");
            Assert.Equal(droppedBefore + 50, Log.Dropped);
            DrainAll();
        }

        [Fact]
        public void Drain_ReturnsLinesInOrderAndRespectsMax()
        {
            Log.SetLevel("info");
            DrainAll();
            string marker = Guid.NewGuid().ToString("N");
            for (int i = 0; i < 10; i++) Log.Info("comp", marker + " #" + i);

            var first = new List<string>();
            Assert.Equal(4, Log.Drain(first, 4));
            var rest = new List<string>();
            Assert.Equal(6, Log.Drain(rest, 100));
            Assert.Equal(0, Log.Drain(new List<string>(), 100));

            var all = first.Concat(rest).ToList();
            for (int i = 0; i < 10; i++)
            {
                Assert.EndsWith(marker + " #" + i, all[i]);
                Assert.Contains(" INFO comp ", all[i]);
            }
        }

        [Fact]
        public void Error_DeduplicatedBySignatureWithinSixtySeconds()
        {
            Log.SetLevel("info");
            DrainAll();
            string sig = "test.sig." + Guid.NewGuid().ToString("N");
            int errorsBefore = Log.ErrorsLastHour();
            Log.Error("comp", sig, "first");
            Log.Error("comp", sig, "second");
            Log.Error("comp", sig, "third");
            string other = "test.sig." + Guid.NewGuid().ToString("N");
            Log.Error("comp", other, "different signature");

            var lines = new List<string>();
            Log.Drain(lines, 1000);
            var mine = lines.Where(l => l.Contains(sig)).ToList();
            Assert.Single(mine);
            Assert.Contains("first", mine[0]);
            Assert.Contains(" ERROR comp [" + sig + "] ", mine[0]);
            Assert.Single(lines.Where(l => l.Contains(other)));
            // Suppressed errors are still counted.
            Assert.True(Log.ErrorsLastHour() >= errorsBefore + 4);
        }

        [Fact]
        public void Level_FiltersLowerSeverities()
        {
            DrainAll();
            string marker = Guid.NewGuid().ToString("N");
            try
            {
                Log.SetLevel("warn");
                Log.Info("c", marker + " info");
                Log.Debug("c", marker + " debug");
                Log.Warn("c", marker + " warn");
                var lines = new List<string>();
                Log.Drain(lines, 100);
                Assert.Single(lines.Where(l => l.Contains(marker)));
                Assert.Contains(" WARN c ", lines.Single(l => l.Contains(marker)));
            }
            finally
            {
                Log.SetLevel("info");
            }
        }

        [Fact]
        public void Lines_AreSingleLine()
        {
            Log.SetLevel("info");
            DrainAll();
            Log.Info("c", "a\r\nb\nc");
            var lines = new List<string>();
            Log.Drain(lines, 10);
            Assert.Single(lines);
            Assert.DoesNotContain("\n", lines[0]);
            Assert.DoesNotContain("\r", lines[0]);
        }

        [Fact]
        public void Signature_IncludesLocationAndExceptionType()
        {
            Assert.Equal("where", Log.Signature("where", null));
            Exception caught = null;
            try
            {
                throw new InvalidOperationException("x");
            }
            catch (Exception e)
            {
                caught = e;
            }
            var sig = Log.Signature("section.buildings", caught);
            Assert.StartsWith("section.buildings:InvalidOperationException", sig);
            Assert.Equal(sig, Log.Signature("section.buildings", caught));
        }
    }
}
