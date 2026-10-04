using System;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using RoiMcp.Observer.Core;
using Xunit;

namespace RoiMcp.Observer.Tests
{
    public class ObserverConfigTests
    {
        private static void AssertDefaults(ObserverConfig c)
        {
            Assert.True(c.Enabled);
            Assert.Equal(5, c.RunningIntervalS);
            Assert.Equal(15, c.PausedIntervalS);
            Assert.Equal(1, c.MinGapS);
            Assert.Equal(2.0, c.FrameBudgetMs);
            Assert.Equal(50, c.CaptureCeilingMs);
            Assert.False(c.IncludeAiBuildingDetail);
            Assert.False(c.IncludeAiRoutes);
            Assert.False(c.IncludeRoutePaths);
            Assert.Equal("info", c.LogLevel);
        }

        /// <summary>Every setting except the warning list, for comparing two configurations.</summary>
        private static JObject Settings(ObserverConfig c)
        {
            var o = JObject.FromObject(c);
            o.Remove("Warnings");
            return o;
        }

        [Fact]
        public void Defaults()
        {
            AssertDefaults(ObserverConfig.Default());
            AssertDefaults(ObserverConfig.Parse(null));
            var c = ObserverConfig.Parse("{}");
            AssertDefaults(c);
            Assert.Empty(c.Warnings);
        }

        [Fact]
        public void ValidValuesAreRead()
        {
            var c = ObserverConfig.Parse("{\"enabled\": true, \"running_interval_s\": 10, \"paused_interval_s\": 30, \"min_gap_s\": 2, " +
                                         "\"frame_budget_ms\": 1.5, \"capture_ceiling_ms\": 80, \"include_ai_building_detail\": true, " +
                                         "\"include_ai_routes\": true, \"include_route_paths\": true, \"log_level\": \"debug\"}");
            Assert.Empty(c.Warnings);
            Assert.Equal(10, c.RunningIntervalS);
            Assert.Equal(30, c.PausedIntervalS);
            Assert.Equal(2, c.MinGapS);
            Assert.Equal(1.5, c.FrameBudgetMs);
            Assert.Equal(80, c.CaptureCeilingMs);
            Assert.True(c.IncludeAiBuildingDetail);
            Assert.True(c.IncludeAiRoutes);
            Assert.True(c.IncludeRoutePaths);
            Assert.Equal("debug", c.LogLevel);
        }

        [Theory]
        [InlineData("running_interval_s", 1, 2, "2")]
        [InlineData("running_interval_s", 301, 300, "300")]
        [InlineData("paused_interval_s", 4, 5, "5")]
        [InlineData("paused_interval_s", 601, 600, "600")]
        [InlineData("min_gap_s", 0, 1, "1")]
        [InlineData("min_gap_s", 61, 60, "60")]
        [InlineData("frame_budget_ms", 0.1, 0.5, "0.5")]
        [InlineData("frame_budget_ms", 6, 5.0, "5")]
        [InlineData("capture_ceiling_ms", 5, 10, "10")]
        [InlineData("capture_ceiling_ms", 500, 200, "200")]
        [InlineData("running_interval_s", -100, 2, "2")]
        public void OutOfRangeValuesAreClampedWithAWarning(string key, double value, double expected, string expectedText)
        {
            var c = ObserverConfig.Parse("{\"" + key + "\": " + value.ToString(System.Globalization.CultureInfo.InvariantCulture) + "}");
            Assert.Equal(expected, Read(c, key));
            Assert.Equal(new[] { "clamped:" + key + "=" + expectedText }, c.Warnings.ToArray());
        }

        [Theory]
        [InlineData("running_interval_s", 2)]
        [InlineData("running_interval_s", 300)]
        [InlineData("paused_interval_s", 5)]
        [InlineData("paused_interval_s", 600)]
        [InlineData("min_gap_s", 1)]
        [InlineData("min_gap_s", 60)]
        [InlineData("frame_budget_ms", 0.5)]
        [InlineData("frame_budget_ms", 5)]
        [InlineData("capture_ceiling_ms", 10)]
        [InlineData("capture_ceiling_ms", 200)]
        public void RangeBoundsAreAccepted(string key, double value)
        {
            var c = ObserverConfig.Parse("{\"" + key + "\": " + value.ToString(System.Globalization.CultureInfo.InvariantCulture) + "}");
            Assert.Equal(value, Read(c, key));
            Assert.Empty(c.Warnings);
        }

        private static double Read(ObserverConfig c, string key)
        {
            switch (key)
            {
                case "running_interval_s": return c.RunningIntervalS;
                case "paused_interval_s": return c.PausedIntervalS;
                case "min_gap_s": return c.MinGapS;
                case "frame_budget_ms": return c.FrameBudgetMs;
                case "capture_ceiling_ms": return c.CaptureCeilingMs;
            }
            throw new ArgumentException(key);
        }

        [Theory]
        [InlineData("running_interval_s", "\"10\"")]
        [InlineData("paused_interval_s", "true")]
        [InlineData("min_gap_s", "null")]
        [InlineData("frame_budget_ms", "[1]")]
        [InlineData("capture_ceiling_ms", "{}")]
        [InlineData("enabled", "\"false\"")]
        [InlineData("enabled", "0")]
        [InlineData("include_ai_building_detail", "1")]
        [InlineData("include_ai_routes", "\"yes\"")]
        [InlineData("include_route_paths", "null")]
        [InlineData("log_level", "3")]
        [InlineData("log_level", "\"verbose\"")]
        public void InvalidTypesWarnAndKeepTheDefault(string key, string json)
        {
            var c = ObserverConfig.Parse("{\"" + key + "\": " + json + "}");
            AssertDefaults(c);
            Assert.Equal(new[] { "invalid_value:" + key }, c.Warnings.ToArray());
        }

        [Fact]
        public void UnknownKeysAreIgnoredWithAWarning()
        {
            var c = ObserverConfig.Parse("{\"foo\": 1, \"running_interval_s\": 9, \"bar_baz\": {\"x\": true}}");
            Assert.Equal(9, c.RunningIntervalS);
            Assert.Contains("unknown_key_ignored:foo", c.Warnings);
            Assert.Contains("unknown_key_ignored:bar_baz", c.Warnings);
            Assert.Equal(2, c.Warnings.Count);
        }

        [Fact]
        public void AllowUnverifiedBuild_IsIgnoredAndHasNoEffect()
        {
            var c = ObserverConfig.Parse("{\"allow_unverified_build\": true}");
            Assert.Equal(new[] { "unknown_key_ignored:allow_unverified_build" }, c.Warnings.ToArray());
            Assert.True(JToken.DeepEquals(Settings(ObserverConfig.Default()), Settings(c)));

            // Other plausible bypass keys behave the same way.
            foreach (var key in new[] { "compatibility_override", "skip_version_gate", "allow_unsupported_build", "expected_assembly_sha256", "debug_faults" })
            {
                var o = ObserverConfig.Parse("{\"" + key + "\": true}");
                Assert.Equal(new[] { "unknown_key_ignored:" + key }, o.Warnings.ToArray());
                Assert.True(JToken.DeepEquals(Settings(ObserverConfig.Default()), Settings(o)), key);
            }
        }

        [Fact]
        public void AllowUnverifiedBuild_DoesNotChangeTheVersionGate()
        {
            // The gate takes no configuration at all, so a non-baseline build stays incompatible.
            ObserverConfig.Parse("{\"allow_unverified_build\": true}");
            var facts = VersionGateTests.BaselineFacts();
            facts.Revision = 4;
            Assert.False(VersionGate.Evaluate(facts, Baseline.AssemblySha256).Compatible);
        }

        [Fact]
        public void ConfigHasNoMemberThatCouldBypassTheVersionGate()
        {
            var forbidden = new[] { "unverified", "compat", "override", "bypass", "gate", "baseline", "version", "sha", "hash", "unsupported" };
            var members = typeof(ObserverConfig).GetMembers(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static)
                .Where(m => m is FieldInfo || m is PropertyInfo)
                .Select(m => m.Name.ToLowerInvariant())
                .ToList();
            Assert.NotEmpty(members);
            foreach (var name in members)
                foreach (var f in forbidden)
                    Assert.False(name.Contains(f), "ObserverConfig member '" + name + "' mentions '" + f + "'");
        }

        [Theory]
        [InlineData("{ not json")]
        [InlineData("{\"running_interval_s\": }")]
        [InlineData("{\"a\": 1")]
        public void MalformedJson_YieldsDefaultsAndAWarning(string text)
        {
            var c = ObserverConfig.Parse(text);
            AssertDefaults(c);
            Assert.Equal(new[] { "config_malformed_ignored" }, c.Warnings.ToArray());
        }

        [Theory]
        [InlineData("[1, 2]")]
        [InlineData("5")]
        [InlineData("\"x\"")]
        public void NonObjectRoot_YieldsDefaultsAndAWarning(string text)
        {
            var c = ObserverConfig.Parse(text);
            AssertDefaults(c);
            Assert.Equal(new[] { "config_not_an_object_ignored" }, c.Warnings.ToArray());
        }

        [Fact]
        public void TooLarge_YieldsDefaultsAndAWarning()
        {
            var c = ObserverConfig.Parse("{\"running_interval_s\": 9}" + new string(' ', ObserverConfig.MaxConfigBytes));
            AssertDefaults(c);
            Assert.Equal(new[] { "config_too_large_ignored" }, c.Warnings.ToArray());
        }

        [Fact]
        public void EnabledFalse()
        {
            var c = ObserverConfig.Parse("{\"enabled\": false}");
            Assert.False(c.Enabled);
            Assert.Empty(c.Warnings);
        }

        [Fact]
        public void Clone_IsIndependent()
        {
            var c = ObserverConfig.Parse("{\"foo\": 1}");
            var d = c.Clone();
            d.Warnings.Add("x");
            d.RunningIntervalS = 99;
            Assert.Single(c.Warnings);
            Assert.Equal(5, c.RunningIntervalS);
        }
    }
}
