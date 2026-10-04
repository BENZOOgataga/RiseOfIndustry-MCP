using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using Newtonsoft.Json.Linq;
using RoiMcp.Observer.Core;
using RoiMcp.Observer.Dto;
using RoiMcp.Observer.Io;
using RoiMcp.Observer.Publish;
using Xunit;

namespace RoiMcp.Observer.Tests
{
    /// <summary>End-to-end publisher tests in a private temporary exchange directory (T-2).</summary>
    public class BackgroundWorkerTests
    {
        private static Publication StatePublication(object data, string worldSession = "ws-1", long? nonce = null)
        {
            return new Publication
            {
                Family = CaptureFamily.State,
                WorldSession = worldSession,
                Data = data,
                Captured = new CapturedDto
                {
                    utc_start = "2026-01-01T00:00:00.000Z",
                    utc_end = "2026-01-01T00:00:00.010Z",
                    game_day_start = 100,
                    game_day_end = 100,
                    game_date = "Y1-04-10",
                    frames = new long[] { 1, 2 },
                    main_thread_ms = 1.5,
                    slices = 1,
                    max_slice_ms = 1.5,
                    consistent = true,
                },
                Sections = new Dictionary<string, SectionStatusDto> { { "session", new SectionStatusDto { status = "ok", game_day = 100 } } },
                Warnings = new List<WarningDto>(),
                ServesNonce = nonce,
                Stats = new CaptureStatsDto { family = "state" },
            };
        }

        private static Dictionary<string, object> Data(int v)
        {
            return new Dictionary<string, object> { { "value", v }, { "label", "x" } };
        }

        private static long Seq(JObject hb, string family)
        {
            return (long)hb["data"]["families"][family]["seq"];
        }

        [Fact]
        public void Heartbeat_AppearsWithBasicFields()
        {
            using (var h = new WorkerHarness())
            {
                h.Start();
                var hb = h.WaitHeartbeat(o => o != null, 3000);
                Assert.NotNull(hb);
                Assert.Equal("roi-mcp/heartbeat", (string)hb["schema"]);
                Assert.Equal(Baseline.SchemaVersion, (string)hb["schema_version"]);
                Assert.Equal(WorkerHarness.Pid, (int)hb["pid"]);
                Assert.Equal(JTokenType.Null, hb["world_session"].Type);
                Assert.Equal("starting", (string)hb["data"]["state"]);
                Assert.Equal(0L, Seq(hb, "state"));
                Assert.Equal(0L, Seq(hb, "static"));
                Assert.Equal(0L, Seq(hb, "history"));
                Assert.Equal(h.Dir, (string)hb["data"]["exchange_dir"]);
                Assert.Equal("pending", (string)hb["data"]["compatibility"]);
                Assert.Equal(Baseline.Commit, (string)hb["data"]["expected_game"]["commit"]);
                Assert.False((bool)hb["data"]["kill_switch"]);
                Assert.True((bool)hb["data"]["config_enabled"]);

                // Heartbeat keeps being rewritten (~1 s period) with a growing seq.
                long first = (long)hb["seq"];
                Assert.NotNull(h.WaitHeartbeat(o => (long)o["seq"] > first, 3000));
                Assert.True(new FileInfo(h.PathOf(ExchangeFiles.Heartbeat)).Length <= SizeCapPolicy.HeartbeatCap);
            }
        }

        [Fact]
        public void Heartbeat_ReflectsMainThreadStatus()
        {
            using (var h = new WorkerHarness())
            {
                h.Hub.UpdateStatus(s =>
                {
                    s.State = ObserverState.Ready;
                    s.WorldSession = "ws-42";
                    s.Paused = true;
                    s.GameDay = 100;
                    s.GameDate = "Y1-04-10";
                    s.Compatibility = "verified";
                    s.RefreshSeen = new RefreshNonces { State = 5 };
                });
                h.Start();
                var hb = h.WaitHeartbeat(o => (string)o["data"]["state"] == "ready", 3000);
                Assert.NotNull(hb);
                Assert.Equal("ws-42", (string)hb["world_session"]);
                Assert.True((bool)hb["data"]["paused"]);
                Assert.Equal("Y1-04-10", (string)hb["data"]["game_date"]);
                Assert.Equal(5L, (long)hb["data"]["refresh_seen"]["state"]);
            }
        }

        [Fact]
        public void Publication_SkipsUnchangedAndIncrementsSeqOnChange()
        {
            using (var h = new WorkerHarness())
            {
                h.Start();
                h.Hub.Enqueue(StatePublication(Data(1)));
                JObject state = null;
                Assert.True(Wait.Until(() => (state = FileUtil.ReadJson(h.PathOf(ExchangeFiles.State))) != null));
                Assert.Equal(1L, (long)state["seq"]);
                Assert.Equal("roi-mcp/state", (string)state["schema"]);
                Assert.Equal("verified", (string)state["compatibility"]);
                Assert.Equal(WorkerHarness.Pid, (int)state["pid"]);
                Assert.Equal("ws-1", (string)state["world_session"]);
                Assert.Equal(1, (int)state["data"]["value"]);
                string hash1 = (string)state["content_hash"];

                var hb1 = h.WaitHeartbeat(o => Seq(o, "state") == 1);
                Assert.NotNull(hb1);
                string verified1 = (string)hb1["data"]["families"]["state"]["last_verified_utc"];
                string published1 = (string)hb1["data"]["families"]["state"]["last_published_utc"];
                Assert.Equal(hash1, (string)hb1["data"]["families"]["state"]["content_hash"]);
                long size1 = (long)hb1["data"]["families"]["state"]["size_bytes"];
                Assert.Equal(new FileInfo(h.PathOf(ExchangeFiles.State)).Length, size1);
                var mtime1 = File.GetLastWriteTimeUtc(h.PathOf(ExchangeFiles.State));

                Thread.Sleep(50);
                h.Hub.Enqueue(StatePublication(Data(1))); // identical content
                var hb2 = h.WaitHeartbeat(o => (string)o["data"]["families"]["state"]["last_verified_utc"] != verified1);
                Assert.NotNull(hb2);
                Assert.Equal(1L, Seq(hb2, "state"));
                Assert.Equal(published1, (string)hb2["data"]["families"]["state"]["last_published_utc"]);
                Assert.Equal(1L, (long)FileUtil.ReadJson(h.PathOf(ExchangeFiles.State))["seq"]);
                Assert.Equal(mtime1, File.GetLastWriteTimeUtc(h.PathOf(ExchangeFiles.State)));

                h.Hub.Enqueue(StatePublication(Data(2))); // different content
                Assert.True(Wait.Until(() => (long)FileUtil.ReadJson(h.PathOf(ExchangeFiles.State))["seq"] == 2));
                var s2 = FileUtil.ReadJson(h.PathOf(ExchangeFiles.State));
                Assert.Equal(2, (int)s2["data"]["value"]);
                Assert.NotEqual(hash1, (string)s2["content_hash"]);
                Assert.NotNull(h.WaitHeartbeat(o => Seq(o, "state") == 2));

                // Same content in a new world session is published again.
                h.Hub.Enqueue(StatePublication(Data(2), "ws-2"));
                Assert.True(Wait.Until(() => (long)FileUtil.ReadJson(h.PathOf(ExchangeFiles.State))["seq"] == 3));
                Assert.Equal("ws-2", (string)FileUtil.ReadJson(h.PathOf(ExchangeFiles.State))["world_session"]);

                Assert.DoesNotContain(Directory.GetFiles(h.Dir), f => f.Contains(".tmp-"));
            }
        }

        [Fact]
        public void Publication_StaticRefPointsAtTheStaticOfTheSameSession()
        {
            using (var h = new WorkerHarness())
            {
                h.Start();
                var st = StatePublication(Data(7));
                st.Family = CaptureFamily.Static;
                h.Hub.Enqueue(st);
                Assert.True(Wait.Until(() => FileUtil.ReadJson(h.PathOf(ExchangeFiles.Static)) != null));
                var stat = FileUtil.ReadJson(h.PathOf(ExchangeFiles.Static));
                Assert.Equal("roi-mcp/static", (string)stat["schema"]);
                Assert.Equal(JTokenType.Null, stat["static_ref"].Type);

                h.Hub.Enqueue(StatePublication(Data(1)));
                Assert.True(Wait.Until(() => FileUtil.ReadJson(h.PathOf(ExchangeFiles.State)) != null));
                var state = FileUtil.ReadJson(h.PathOf(ExchangeFiles.State));
                Assert.Equal((long)stat["seq"], (long)state["static_ref"]["seq"]);
                Assert.Equal((string)stat["content_hash"], (string)state["static_ref"]["content_hash"]);
            }
        }

        [Fact]
        public void RefreshServedNonce_AppearsInHeartbeat()
        {
            using (var h = new WorkerHarness())
            {
                h.Start();
                h.Hub.Enqueue(StatePublication(Data(1), nonce: 1730000000123));
                var hb = h.WaitHeartbeat(o => o["data"]["refresh_served"]["state"].Type == JTokenType.Integer);
                Assert.NotNull(hb);
                Assert.Equal(1730000000123L, (long)hb["data"]["refresh_served"]["state"]);
                Assert.Equal(JTokenType.Null, hb["data"]["refresh_served"]["history"].Type);

                // A skipped (unchanged) publication still serves its nonce.
                h.Hub.Enqueue(StatePublication(Data(1), nonce: 1730000000999));
                Assert.NotNull(h.WaitHeartbeat(o => (long?)o["data"]["refresh_served"]["state"] == 1730000000999));
            }
        }

        [Fact]
        public void RefreshRequestFile_IsReadIntoTheHub()
        {
            using (var h = new WorkerHarness())
            {
                h.Start();
                File.WriteAllText(h.PathOf(ExchangeFiles.RefreshRequest),
                    "{\"schema\":\"roi-mcp/refresh-request\",\"schema_version\":\"1.0.0\",\"requests\":{\"state\":5,\"history\":null,\"static\":6},\"requested_utc\":\"x\"}");
                Assert.True(Wait.Until(() => h.Hub.ReadControl().Nonces.State == 5));
                var n = h.Hub.ReadControl().Nonces;
                Assert.Null(n.History);
                Assert.Equal(6L, n.Static);
            }
        }

        [Fact]
        public void KillSwitchFile_SetsAndClearsTheHubFlag()
        {
            using (var h = new WorkerHarness())
            {
                h.Start();
                Assert.NotNull(h.WaitHeartbeat(o => o != null, 3000));
                Assert.False(h.Hub.ReadControl().KillSwitch);
                File.WriteAllText(h.PathOf(ExchangeFiles.KillSwitch), "");
                Assert.True(Wait.Until(() => h.Hub.ReadControl().KillSwitch, 3000));
                Assert.NotNull(h.WaitHeartbeat(o => (bool)o["data"]["kill_switch"], 3000));
                File.Delete(h.PathOf(ExchangeFiles.KillSwitch));
                Assert.True(Wait.Until(() => !h.Hub.ReadControl().KillSwitch, 3000));
            }
        }

        [Fact]
        public void ConfigFile_IsClampedAndWarningsReachTheHeartbeat()
        {
            using (var h = new WorkerHarness())
            {
                h.Start();
                Assert.True(Wait.Until(() => h.Hub.ReadControl().ConfigVersion >= 1, 3000));
                File.WriteAllText(h.PathOf(ExchangeFiles.Config), "{\"running_interval_s\": 1}");
                Assert.True(Wait.Until(() => h.Hub.ReadControl().Config.RunningIntervalS == 2, 5000));
                var cfg = h.Hub.ReadControl().Config;
                Assert.Contains("clamped:running_interval_s=2", cfg.Warnings);
                var hb = h.WaitHeartbeat(o => ((JArray)o["data"]["config_warnings"]).Any(w => (string)w == "clamped:running_interval_s=2"));
                Assert.NotNull(hb);

                File.WriteAllText(h.PathOf(ExchangeFiles.Config), "{\"enabled\": false, \"allow_unverified_build\": true}");
                Assert.True(Wait.Until(() => !h.Hub.ReadControl().Config.Enabled, 5000));
                var hb2 = h.WaitHeartbeat(o => !(bool)o["data"]["config_enabled"]);
                Assert.NotNull(hb2);
                Assert.Contains("unknown_key_ignored:allow_unverified_build", ((JArray)hb2["data"]["config_warnings"]).Select(w => (string)w));
            }
        }

        [Fact]
        public void StaleTempFiles_AreRemovedOnStart()
        {
            using (var h = new WorkerHarness())
            {
                File.WriteAllText(h.PathOf("state.json.tmp-1"), "partial");
                File.WriteAllText(h.PathOf("heartbeat.json.tmp-4242"), "partial");
                File.WriteAllText(h.PathOf("keep.txt"), "keep");
                h.Start();
                Assert.True(Wait.Until(() => !File.Exists(h.PathOf("state.json.tmp-1")) && !File.Exists(h.PathOf("heartbeat.json.tmp-4242"))));
                Assert.True(File.Exists(h.PathOf("keep.txt")));
            }
        }

        [Fact]
        public void SizeCap_DropsOptionalSectionsInOrder()
        {
            using (var h = new WorkerHarness())
            {
                var paths = new List<RoutePathDto>();
                for (int i = 0; i < 2500; i++)
                {
                    var pts = new List<int[]>();
                    for (int j = 0; j < 256; j++) pts.Add(new[] { 1000 + j, 2000 + j });
                    paths.Add(new RoutePathDto { route_key = "r" + i, original_points = 256, points = pts });
                }
                var data = new StateData
                {
                    route_paths = paths,
                    routes_ai = new List<RouteDto>(),
                    buildings_ai = new List<BuildingCompactDto>(),
                };
                var p = StatePublication(data);
                p.Sections["route_paths"] = new SectionStatusDto { status = "ok" };
                p.Sections["routes_ai"] = new SectionStatusDto { status = "ok" };
                h.Start();
                h.Hub.Enqueue(p);
                JObject state = null;
                Assert.True(Wait.Until(() => (state = FileUtil.ReadJson(h.PathOf(ExchangeFiles.State))) != null, 10000));
                Assert.True(new FileInfo(h.PathOf(ExchangeFiles.State)).Length <= SizeCapPolicy.StateCap);
                Assert.Equal(JTokenType.Null, state["data"]["route_paths"].Type);
                Assert.Equal(JTokenType.Array, state["data"]["routes_ai"].Type); // next in order, not needed
                Assert.Equal(JTokenType.Array, state["data"]["buildings_ai"].Type);
                Assert.Equal("skipped", (string)state["sections"]["route_paths"]["status"]);
                Assert.Equal("size_cap", (string)state["sections"]["route_paths"]["reason"]);
                Assert.Equal("ok", (string)state["sections"]["routes_ai"]["status"]);
                Assert.Contains(state["warnings"], w => (string)w["code"] == "section_dropped_size_cap" && ((string)w["detail"]).StartsWith("route_paths"));
            }
        }

        [Fact]
        public void SizeCap_StillOverAfterDrops_IsNotPublished()
        {
            using (var h = new WorkerHarness())
            {
                var big = new string('k', 10000);
                var routes = new List<RouteDto>();
                for (int i = 0; i < 600; i++) routes.Add(new RouteDto { route_key = big + i });
                var p = StatePublication(new StateData { routes_player = routes });
                h.Start();
                h.Hub.Enqueue(p);
                var hb = h.WaitHeartbeat(o => (int)o["data"]["publish_failures"] >= 1, 10000);
                Assert.NotNull(hb);
                Assert.Equal(0L, Seq(hb, "state"));
                Assert.False(File.Exists(h.PathOf(ExchangeFiles.State)));
                Assert.DoesNotContain(Directory.GetFiles(h.Dir), f => f.Contains(".tmp-"));
            }
        }
    }
}
