using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using RoiMcp.Observer.Core;
using RoiMcp.Observer.Dto;
using RoiMcp.Observer.Io;
using RoiMcp.Observer.Publish;
using Xunit;

namespace RoiMcp.Observer.Tests
{
    /// <summary>T-2: atomic publication, temp cleanup, envelope/hash, size-cap policy.</summary>
    public class PublisherTests
    {
        private const int Pid = 4242;

        private static byte[] Filled(int size, byte value)
        {
            var b = new byte[size];
            for (int i = 0; i < size; i++) b[i] = value;
            return b;
        }

        private static string[] Names(string dir)
        {
            return Directory.GetFiles(dir).Select(Path.GetFileName).OrderBy(n => n, StringComparer.Ordinal).ToArray();
        }

        // ---- ExchangeFiles.PublishAtomic

        [Fact]
        public void PublishAtomic_CreatesAndReplaces_NoTempRemains()
        {
            using (var t = new TempDir())
            {
                var files = new ExchangeFiles(t.Dir, Pid);
                string error;
                var v1 = Encoding.UTF8.GetBytes("{\"v\":1}");
                Assert.True(files.PublishAtomic("state.json", v1, v1.Length, out error), error);
                Assert.Null(error);
                Assert.Equal(v1, File.ReadAllBytes(t.PathOf("state.json")));
                Assert.Equal(new[] { "state.json" }, Names(t.Dir));

                // Buffer longer than the published length: only `length` bytes are written.
                var v2 = new byte[64];
                var payload = Encoding.UTF8.GetBytes("{\"v\":2}");
                Array.Copy(payload, v2, payload.Length);
                Assert.True(files.PublishAtomic("state.json", v2, payload.Length, out error), error);
                Assert.Equal(payload, File.ReadAllBytes(t.PathOf("state.json")));
                Assert.Equal(new[] { "state.json" }, Names(t.Dir));
            }
        }

        [Fact]
        public void PublishAtomic_TargetLocked_WritesViaPidTempThenFailsCleanly()
        {
            using (var t = new TempDir())
            {
                var files = new ExchangeFiles(t.Dir, Pid);
                string error;
                var original = Encoding.UTF8.GetBytes("{\"original\":true}");
                Assert.True(files.PublishAtomic("state.json", original, original.Length, out error));

                var big = Filled(8 * 1024 * 1024, (byte)'x');
                string tempPath = t.PathOf("state.json.tmp-" + Pid);
                bool sawTemp = false;
                bool result;
                using (new FileStream(t.PathOf("state.json"), FileMode.Open, FileAccess.Read, FileShare.None))
                {
                    var task = Task.Run(() =>
                    {
                        string e;
                        bool ok = files.PublishAtomic("state.json", big, big.Length, out e);
                        return Tuple.Create(ok, e);
                    });
                    // Retries take 100 + 200 + 400 ms; the temp file named <name>.tmp-<pid> exists meanwhile.
                    while (!task.IsCompleted)
                    {
                        if (File.Exists(tempPath)) sawTemp = true;
                        foreach (var n in Names(t.Dir))
                            Assert.True(n == "state.json" || n == "state.json.tmp-" + Pid, "unexpected file " + n);
                        Thread.Sleep(5);
                    }
                    result = task.Result.Item1;
                    Assert.NotNull(task.Result.Item2);
                }
                Assert.False(result);
                Assert.True(sawTemp, "temp file state.json.tmp-" + Pid + " was never observed");
                Assert.False(File.Exists(tempPath), "temp file must be deleted after a failed publication");
                Assert.Equal(original, File.ReadAllBytes(t.PathOf("state.json")));

                // Once the lock is gone, publication works again.
                Assert.True(files.PublishAtomic("state.json", big, big.Length, out error), error);
                Assert.Equal(big.Length, new FileInfo(t.PathOf("state.json")).Length);
                Assert.Equal(new[] { "state.json" }, Names(t.Dir));
            }
        }

        [Fact]
        public void PublishAtomic_TempHeldExclusively_FailsAndTargetUnchanged()
        {
            using (var t = new TempDir())
            {
                var files = new ExchangeFiles(t.Dir, Pid);
                string error;
                var original = Encoding.UTF8.GetBytes("{\"original\":true}");
                Assert.True(files.PublishAtomic("history.json", original, original.Length, out error));
                var big = Filled(4 * 1024 * 1024, (byte)'y');
                using (new FileStream(t.PathOf("history.json.tmp-" + Pid), FileMode.Create, FileAccess.Write, FileShare.None))
                {
                    Assert.False(files.PublishAtomic("history.json", big, big.Length, out error));
                    Assert.NotNull(error);
                }
                Assert.Equal(original, File.ReadAllBytes(t.PathOf("history.json")));
            }
        }

        [Fact]
        public void PublishAtomic_ConcurrentReaderNeverSeesAPartialFile()
        {
            using (var t = new TempDir())
            {
                var files = new ExchangeFiles(t.Dir, Pid);
                const int size = 4 * 1024 * 1024;
                string error;
                Assert.True(files.PublishAtomic("state.json", Filled(size, 1), size, out error), error);

                int goodReads = 0;
                string violation = null;
                var stop = new ManualResetEventSlim(false);
                var reader = new Thread(() =>
                {
                    while (!stop.IsSet)
                    {
                        var bytes = FileUtil.ReadBytes(t.PathOf("state.json"));
                        if (bytes == null) continue; // transiently locked or between rename steps
                        if (bytes.Length != size)
                        {
                            violation = "length " + bytes.Length;
                            return;
                        }
                        byte first = bytes[0];
                        for (int i = 1; i < bytes.Length; i++)
                        {
                            if (bytes[i] != first)
                            {
                                violation = "mixed content at " + i;
                                return;
                            }
                        }
                        Interlocked.Increment(ref goodReads);
                    }
                }) { IsBackground = true };
                reader.Start();

                int published = 0;
                for (int k = 2; k < 14; k++)
                    if (files.PublishAtomic("state.json", Filled(size, (byte)k), size, out error)) published++;
                stop.Set();
                reader.Join(10000);

                Assert.Null(violation);
                Assert.True(published > 0);
                Assert.True(goodReads > 0);
                Assert.DoesNotContain(Names(t.Dir), n => n.Contains(".tmp-"));
            }
        }

        [Fact]
        public void CleanupTemps_DeletesOnlyJsonTempFiles()
        {
            using (var t = new TempDir())
            {
                var temps = new[] { "state.json.tmp-123", "heartbeat.json.tmp-4242", "static.json.tmp-1", "history.json.tmp-99999" };
                var keep = new[] { "state.json", "observer.log", "observer.log.1", "observer.config.json", "refresh-request.json",
                                   "observer.disabled", "notes.txt.tmp-1", "state.json.bak", "state.json.tmp" };
                foreach (var n in temps.Concat(keep)) File.WriteAllText(t.PathOf(n), "x");
                var files = new ExchangeFiles(t.Dir, Pid);
                Assert.Equal(temps.Length, files.CleanupTemps());
                Assert.Equal(keep.OrderBy(n => n, StringComparer.Ordinal).ToArray(), Names(t.Dir));
            }
        }

        [Fact]
        public void CleanupTemps_MissingDirectoryIsHarmless()
        {
            var files = new ExchangeFiles(Path.Combine(Path.GetTempPath(), "roimcp-missing-" + Guid.NewGuid().ToString("N")), Pid);
            Assert.Equal(0, files.CleanupTemps());
        }

        [Fact]
        public void ReadSmallText_RespectsTheLimit()
        {
            using (var t = new TempDir())
            {
                var files = new ExchangeFiles(t.Dir, Pid);
                File.WriteAllText(t.PathOf("refresh-request.json"), new string('a', 2000));
                long len;
                Assert.Null(files.ReadSmallText("refresh-request.json", 1024, out len));
                Assert.Equal(2000, len);
                File.WriteAllText(t.PathOf("refresh-request.json"), "{}");
                Assert.Equal("{}", files.ReadSmallText("refresh-request.json", 1024, out len));
                Assert.Null(files.ReadSmallText("missing.json", 1024, out len));
                Assert.Equal(-1, files.MtimeTicks("missing.json"));
            }
        }

        // ---- SnapshotWriter

        private static EnvelopeDto Envelope(string hash)
        {
            return new EnvelopeDto
            {
                schema = "roi-mcp/state",
                schema_version = Baseline.SchemaVersion,
                observer_version = Baseline.ObserverVersion,
                compatibility = "verified",
                game = Baseline.Expected(),
                pid = Pid,
                world_session = "ws-1",
                seq = 7,
                content_hash = hash,
                written_utc = "2026-01-01T00:00:00.000Z",
                captured = new CapturedDto { utc_start = "a", utc_end = "b", game_day_start = 1, game_day_end = 1, game_date = "Y1-01-01", frames = new long[] { 1, 2 }, consistent = true },
                static_ref = new StaticRefDto { seq = 3, content_hash = "abc" },
                sections = new Dictionary<string, SectionStatusDto> { { "buildings_player", new SectionStatusDto { status = "ok", game_day = 1 } } },
                warnings = new List<WarningDto> { new WarningDto { code = "w", detail = "d" } },
            };
        }

        private static string Sha256Lower(byte[] bytes, int offset, int count)
        {
            using (var sha = SHA256.Create())
            {
                var h = sha.ComputeHash(bytes, offset, count);
                return string.Concat(h.Select(b => b.ToString("x2")));
            }
        }

        private static byte[] Written(SnapshotWriter w)
        {
            var b = new byte[w.Length];
            Array.Copy(w.Buffer, b, w.Length);
            return b;
        }

        [Fact]
        public void SnapshotWriter_EnvelopeOrderAndContentHash()
        {
            var data = new Dictionary<string, object>
            {
                { "display_name", "USINE PÉTROCHIMIQUE 5" },
                { "values", new[] { 1, 2, 3 } },
                { "nested", new Dictionary<string, object> { { "x", 1.5 }, { "y", null } } },
            };
            var w = new SnapshotWriter();
            string hash = w.SerializeData(data);
            w.WriteFile(Envelope(hash));
            var bytes = Written(w);
            var text = new UTF8Encoding(false, true).GetString(bytes);

            var o = FileUtil.ParseJson(text);
            var expectedOrder = new[] { "schema", "schema_version", "observer_version", "compatibility", "game", "pid", "world_session", "seq",
                                        "content_hash", "written_utc", "captured", "static_ref", "sections", "warnings", "data" };
            Assert.Equal(expectedOrder, o.Properties().Select(p => p.Name).ToArray());
            Assert.Equal(hash, (string)o["content_hash"]);
            Assert.Equal(64, hash.Length);
            Assert.Equal(hash.ToLowerInvariant(), hash);
            Assert.Equal("USINE PÉTROCHIMIQUE 5", (string)o["data"]["display_name"]);
            Assert.Equal(Pid, (int)o["pid"]);
            Assert.Equal(7L, (long)o["seq"]);

            // content_hash is the SHA-256 of exactly the bytes of the serialized `data` value in the file.
            var marker = Encoding.UTF8.GetBytes(",\"data\":");
            int at = IndexOf(bytes, marker);
            Assert.True(at > 0);
            int start = at + marker.Length;
            Assert.Equal((byte)'}', bytes[bytes.Length - 1]);
            int count = bytes.Length - 1 - start;
            Assert.Equal(w.DataLength, count);
            Assert.Equal(hash, Sha256Lower(bytes, start, count));
        }

        private static int IndexOf(byte[] haystack, byte[] needle)
        {
            for (int i = haystack.Length - needle.Length; i >= 0; i--)
            {
                bool ok = true;
                for (int j = 0; j < needle.Length; j++)
                    if (haystack[i + j] != needle[j]) { ok = false; break; }
                if (ok) return i;
            }
            return -1;
        }

        [Fact]
        public void SnapshotWriter_SameDataSameHash_DifferentDataDifferentHash()
        {
            var w = new SnapshotWriter();
            var h1 = w.SerializeData(new Dictionary<string, object> { { "a", 1 } });
            var h2 = w.SerializeData(new Dictionary<string, object> { { "a", 1 } });
            var h3 = w.SerializeData(new Dictionary<string, object> { { "a", 2 } });
            Assert.Equal(h1, h2);
            Assert.NotEqual(h1, h3);
            Assert.Equal(Sha256Lower(Encoding.UTF8.GetBytes("{\"a\":2}"), 0, 7), h3);
        }

        [Fact]
        public void SnapshotWriter_ReusedBuffersDoNotLeakPreviousContent()
        {
            var w = new SnapshotWriter();
            var big = new Dictionary<string, object> { { "big", new string('z', 100000) } };
            w.SerializeData(big);
            w.WriteFile(Envelope("h"));
            var h = w.SerializeData(new Dictionary<string, object> { { "a", 1 } });
            w.WriteFile(Envelope(h));
            var o = FileUtil.ParseJson(Encoding.UTF8.GetString(Written(w)));
            Assert.Equal(1, (int)o["data"]["a"]);
            Assert.Null(o["data"]["big"]);
            Assert.True(w.Length < 2000);
        }

        [Fact]
        public void SnapshotWriter_NonFiniteFloatsAreNeverWrittenAsInvalidJson()
        {
            var data = new Dictionary<string, object>
            {
                { "nan", double.NaN },
                { "pinf", double.PositiveInfinity },
                { "ninf", double.NegativeInfinity },
                { "fnan", float.NaN },
                { "frame_stats", new FrameStatsDto { window_s = double.NaN, frame_ms_p50 = double.PositiveInfinity, observer_ms_max = double.NegativeInfinity } },
                { "nullable", (double?)double.NaN },
            };
            var w = new SnapshotWriter();
            var hash = w.SerializeData(data);
            var env = Envelope(hash);
            env.captured.main_thread_ms = double.NaN;
            env.captured.max_slice_ms = double.PositiveInfinity;
            env.sections["buildings_player"].main_thread_ms = double.NegativeInfinity;
            w.WriteFile(env);
            var text = Encoding.UTF8.GetString(Written(w));
            Assert.DoesNotContain("NaN", text);
            Assert.DoesNotContain("Infinity", text);
            var o = FileUtil.ParseJson(text);
            Assert.NotNull(o["data"]["frame_stats"]);
        }

        [Fact]
        public void SnapshotWriter_WriteSmall_HasNullContentHash()
        {
            var w = new SnapshotWriter();
            var env = Envelope("will be cleared");
            env.schema = "roi-mcp/heartbeat";
            w.WriteSmall(env, new HeartbeatData { state = "menu" });
            var o = FileUtil.ParseJson(Encoding.UTF8.GetString(Written(w)));
            Assert.Equal(JTokenType.Null, o["content_hash"].Type);
            Assert.Equal("menu", (string)o["data"]["state"]);
        }

        // ---- SizeCapPolicy

        [Fact]
        public void SizeCaps()
        {
            Assert.Equal(16 * 1024, SizeCapPolicy.HeartbeatCap);
            Assert.Equal(4 * 1024 * 1024, SizeCapPolicy.Cap(CaptureFamily.Static));
            Assert.Equal(5 * 1024 * 1024, SizeCapPolicy.Cap(CaptureFamily.State));
            Assert.Equal(5 * 1024 * 1024, SizeCapPolicy.Cap(CaptureFamily.History));
        }

        [Fact]
        public void SizeCapDropOrder()
        {
            Assert.Equal(new[] { "route_paths", "routes_ai", "buildings_ai_detail", "buildings_ai" }, SizeCapPolicy.DropOrder(CaptureFamily.State));
            Assert.Equal(new[] { "shops_monthly", "production_monthly_player", "buildings_monthly_player" }, SizeCapPolicy.DropOrder(CaptureFamily.History));
            Assert.Empty(SizeCapPolicy.DropOrder(CaptureFamily.Static));
        }

        [Fact]
        public void Drop_NullsStateSectionsOnce()
        {
            var s = new StateData
            {
                route_paths = new List<RoutePathDto>(),
                routes_ai = new List<RouteDto>(),
                buildings_ai_detail = new List<BuildingDto>(),
                buildings_ai = new List<BuildingCompactDto>(),
                routes_player = new List<RouteDto>(),
            };
            foreach (var name in SizeCapPolicy.StateDropOrder)
            {
                Assert.True(SizeCapPolicy.Drop(s, name), name);
                Assert.False(SizeCapPolicy.Drop(s, name), name);
            }
            Assert.Null(s.route_paths);
            Assert.Null(s.routes_ai);
            Assert.Null(s.buildings_ai_detail);
            Assert.Null(s.buildings_ai);
            Assert.False(SizeCapPolicy.Drop(s, "routes_player"));
            Assert.NotNull(s.routes_player);
        }

        [Fact]
        public void Drop_NullsHistorySectionsOnce()
        {
            var h = new HistoryData
            {
                shops_monthly = new List<ShopSeriesDto>(),
                production_monthly_player = new List<ProductionSeriesDto>(),
                buildings_monthly_player = new List<BuildingSeriesDto>(),
                ledger_player = new LedgerDto(),
            };
            foreach (var name in SizeCapPolicy.HistoryDropOrder)
            {
                Assert.True(SizeCapPolicy.Drop(h, name), name);
                Assert.False(SizeCapPolicy.Drop(h, name), name);
            }
            Assert.False(SizeCapPolicy.Drop(h, "ledger_player"));
            Assert.NotNull(h.ledger_player);
            Assert.False(SizeCapPolicy.Drop(new StaticData(), "route_paths"));
        }

        [Fact]
        public void MarkDropped_SetsSkippedSizeCapAndWarns()
        {
            var sections = new Dictionary<string, SectionStatusDto> { { "route_paths", new SectionStatusDto { status = "ok" } } };
            var warnings = new List<WarningDto>();
            SizeCapPolicy.MarkDropped(sections, warnings, "route_paths", 6000000);
            Assert.Equal("skipped", sections["route_paths"].status);
            Assert.Equal("size_cap", sections["route_paths"].reason);
            Assert.Single(warnings);
            Assert.Equal("section_dropped_size_cap", warnings[0].code);
            Assert.Contains("route_paths", warnings[0].detail);

            // Section absent from the status map: still warned about.
            SizeCapPolicy.MarkDropped(sections, warnings, "routes_ai", 1);
            Assert.Equal(2, warnings.Count);
            SizeCapPolicy.MarkDropped(null, warnings, "routes_ai", 1);
            Assert.Equal(3, warnings.Count);
        }
    }
}
