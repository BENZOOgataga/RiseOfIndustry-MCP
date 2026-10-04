using System;
using System.Linq;
using System.Reflection;
using RoiMcp.Observer.Core;
using Xunit;

namespace RoiMcp.Observer.Tests
{
    public class VersionGateTests
    {
        internal static VersionFacts BaselineFacts()
        {
            return new VersionFacts
            {
                Major = Baseline.Major,
                Minor = Baseline.Minor,
                Revision = Baseline.Revision,
                Suffix = Baseline.Suffix,
                Build = Baseline.Build,
                CommitHash = Baseline.Commit,
                SavegameVersion = Baseline.SavegameVersion,
                Release = "Steam - Public - 2.3.3 : 0507b",
                Found = true,
            };
        }

        [Fact]
        public void BaselineIsThePrdBaseline()
        {
            Assert.Equal(2, Baseline.Major);
            Assert.Equal(3, Baseline.Minor);
            Assert.Equal(3, Baseline.Revision);
            Assert.Equal("0507", Baseline.Build);
            Assert.Equal("b", Baseline.Suffix);
            Assert.Equal(2304, Baseline.SavegameVersion);
            Assert.StartsWith("76359e59", Baseline.Commit);
            Assert.StartsWith("D62599EF", Baseline.AssemblySha256);
        }

        [Fact]
        public void BaselineFactsAndHash_AreCompatible()
        {
            var r = VersionGate.Evaluate(BaselineFacts(), Baseline.AssemblySha256);
            Assert.True(r.Compatible);
            Assert.Empty(r.Mismatches);
            Assert.Equal("2.3.3", r.Detected.version);
            Assert.Equal("0507b", r.Detected.build);
            Assert.Equal(Baseline.AssemblySha256, r.Detected.assembly_sha256);
        }

        [Fact]
        public void HashComparisonIsCaseInsensitive()
        {
            Assert.True(VersionGate.Evaluate(BaselineFacts(), Baseline.AssemblySha256.ToLowerInvariant()).Compatible);
        }

        private static void AssertSingleMismatch(Action<VersionFacts> mutate, string tag)
        {
            var f = BaselineFacts();
            mutate(f);
            var r = VersionGate.Evaluate(f, Baseline.AssemblySha256);
            Assert.False(r.Compatible);
            Assert.Equal(new[] { tag }, r.Mismatches.ToArray());
        }

        [Fact] public void DifferentMajor() { AssertSingleMismatch(f => f.Major = 3, "version"); }
        [Fact] public void DifferentMinor() { AssertSingleMismatch(f => f.Minor = 2, "version"); }
        [Fact] public void DifferentRevision() { AssertSingleMismatch(f => f.Revision = 4, "version"); }
        [Fact] public void DifferentSuffix() { AssertSingleMismatch(f => f.Suffix = "c", "build"); }
        [Fact] public void NullSuffix() { AssertSingleMismatch(f => f.Suffix = null, "build"); }
        [Fact] public void DifferentBuild() { AssertSingleMismatch(f => f.Build = "0508", "build"); }
        [Fact] public void DifferentCommit() { AssertSingleMismatch(f => f.CommitHash = "0000000000000000000000000000000000000000", "commit"); }
        [Fact] public void NullCommit() { AssertSingleMismatch(f => f.CommitHash = null, "commit"); }
        [Fact] public void DifferentSavegameVersion() { AssertSingleMismatch(f => f.SavegameVersion = 2305, "savegame_version"); }

        [Fact]
        public void DifferentHash()
        {
            var r = VersionGate.Evaluate(BaselineFacts(), "0000000000000000000000000000000000000000000000000000000000000000");
            Assert.False(r.Compatible);
            Assert.Equal(new[] { "assembly_sha256" }, r.Mismatches.ToArray());
        }

        [Fact]
        public void NullHash()
        {
            var r = VersionGate.Evaluate(BaselineFacts(), null);
            Assert.False(r.Compatible);
            Assert.Equal(new[] { "assembly_sha256" }, r.Mismatches.ToArray());
        }

        [Fact]
        public void FactsNotFound()
        {
            var f = BaselineFacts();
            f.Found = false;
            var r = VersionGate.Evaluate(f, Baseline.AssemblySha256);
            Assert.False(r.Compatible);
            Assert.Equal(new[] { "game_version_unreadable" }, r.Mismatches.ToArray());
            Assert.Null(r.Detected.version);

            var n = VersionGate.Evaluate(null, Baseline.AssemblySha256);
            Assert.False(n.Compatible);
            Assert.Equal(new[] { "game_version_unreadable" }, n.Mismatches.ToArray());
        }

        [Fact]
        public void ReleaseStringIsNotPartOfTheGate()
        {
            var f = BaselineFacts();
            f.Release = "something else";
            Assert.True(VersionGate.Evaluate(f, Baseline.AssemblySha256).Compatible);
        }

        [Fact]
        public void GateHasNoOverrideInput()
        {
            var publicStatics = typeof(VersionGate).GetMethods(BindingFlags.Public | BindingFlags.Static);
            var evaluate = publicStatics.Where(m => m.Name == "Evaluate").ToList();
            Assert.Single(evaluate);
            var ps = evaluate[0].GetParameters();
            Assert.Equal(2, ps.Length);
            Assert.Equal(typeof(VersionFacts), ps[0].ParameterType);
            Assert.Equal(typeof(string), ps[1].ParameterType);
            Assert.Equal(typeof(VersionGateResult), evaluate[0].ReturnType);

            // No public member of the gate accepts a flag or a configuration, and the gate holds no state.
            foreach (var m in publicStatics)
                foreach (var p in m.GetParameters())
                {
                    Assert.NotEqual(typeof(bool), p.ParameterType);
                    Assert.NotEqual(typeof(ObserverConfig), p.ParameterType);
                }
            Assert.Empty(typeof(VersionGate).GetFields(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static));
            Assert.Empty(typeof(VersionGate).GetProperties(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static));

            // VersionFacts carries raw facts only.
            foreach (var field in typeof(VersionFacts).GetFields())
            {
                var name = field.Name.ToLowerInvariant();
                Assert.DoesNotContain("override", name);
                Assert.DoesNotContain("allow", name);
                Assert.DoesNotContain("unverified", name);
            }
        }
    }
}
