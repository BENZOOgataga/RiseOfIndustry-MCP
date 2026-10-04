using Xunit;

namespace RoiMcp.ReadOnlyGate.Tests;

/// <summary>Tests that need no game installation.</summary>
public sealed class UnitTests
{
    [Theory]
    [InlineData("ProjectAutomata.Utils::GetSafe*", "ProjectAutomata.Utils::GetSafe(x)", true)]
    [InlineData("*ViewModel", "UI.MVVM.FooViewModel", true)]
    [InlineData("*ViewModel", "UI.MVVM.FooViewModelX", false)]
    [InlineData("DevConsole.*", "DevConsole.Console", true)]
    [InlineData("a?c", "abc", true)]
    [InlineData("a.c", "abc", false)]
    [InlineData("A(B)", "A(B)", true)]
    public void Glob_matching(string pattern, string value, bool expected)
    {
        Assert.Equal(expected, Glob.IsMatch(pattern, value));
    }

    [Fact]
    public void Parses_method_member_string()
    {
        Assert.True(Names.TryParseMember(
            "ProjectAutomata.ManagerBehaviour`1<ProjectAutomata.World>::get_instance()", out var pm));
        Assert.Equal("ProjectAutomata.ManagerBehaviour`1<ProjectAutomata.World>", pm.Type);
        Assert.Equal("ProjectAutomata.ManagerBehaviour`1", pm.OpenType);
        Assert.Equal("get_instance", pm.BareName);
        Assert.Equal(string.Empty, pm.Parameters);
        Assert.Equal("ProjectAutomata.ManagerBehaviour`1::get_instance()", pm.Key);
    }

    [Fact]
    public void Parses_generic_method_and_field_member_strings()
    {
        Assert.True(Names.TryParseMember("UnityEngine.Component::GetComponent<ProjectAutomata.Building>()", out var gm));
        Assert.Equal("GetComponent", gm.BareName);
        Assert.Equal("UnityEngine.Component::GetComponent()", gm.Key);

        Assert.True(Names.TryParseMember("ProjectAutomata.ManualDestinationSlot::_minStoredAtSource", out var f));
        Assert.False(f.IsMethod);
        Assert.Equal("ProjectAutomata.ManualDestinationSlot::_minStoredAtSource", f.Key);

        Assert.False(Names.TryParseMember("ProjectAutomata.Building", out _));
    }

    [Fact]
    public void Deny_patterns_match_members_and_types()
    {
        var member = DenyPattern.Parse("ProjectAutomata.Utils::GetSafe");
        Assert.True(member.MatchesMember("ProjectAutomata.Utils", "ProjectAutomata.Utils", Array.Empty<string>(), "GetSafe", "GetSafe(a,b)"));
        Assert.False(member.MatchesMember("ProjectAutomata.Utils", "ProjectAutomata.Utils", Array.Empty<string>(), "GetSafeX", "GetSafeX()"));

        var overload = DenyPattern.Parse("ProjectAutomata.Shop::GetSoldCount(ProjectAutomata.IActor,ProjectAutomata.GamePeriod)");
        Assert.True(overload.MatchesMember("ProjectAutomata.Shop", "ProjectAutomata.Shop", Array.Empty<string>(), "GetSoldCount", "GetSoldCount(ProjectAutomata.IActor,ProjectAutomata.GamePeriod)"));
        Assert.False(overload.MatchesMember("ProjectAutomata.Shop", "ProjectAutomata.Shop", Array.Empty<string>(), "GetSoldCount", "GetSoldCount(ProjectAutomata.GamePeriod)"));

        var type = DenyPattern.Parse("*ViewModel");
        Assert.True(type.IsTypePattern);
        Assert.True(type.MatchesType("A.FooViewModel/Nested", "A.FooViewModel/Nested", Denylist.OuterTypesOf("A.FooViewModel/Nested")));
    }

    [Theory]
    [InlineData("set_value", true)]
    [InlineData("add_onDayStart", true)]
    [InlineData("Kill", true)]
    [InlineData("Killer", false)]
    [InlineData("SetActive", true)]
    [InlineData("get_value", false)]
    [InlineData("Count", false)]
    public void Mutator_names(string name, bool expected)
    {
        Assert.Equal(expected, Rules.IsMutatorName(name));
    }

    [Fact]
    public void Hash_check_rejects_non_baseline_file()
    {
        var path = Path.GetTempFileName();
        try
        {
            File.WriteAllBytes(path, new byte[] { 1, 2, 3 });
            Assert.False(GameEnvironment.HashMatches(GameEnvironment.Sha256OfFile(path)));
            Assert.True(GameEnvironment.HashMatches(GameEnvironment.ExpectedAssemblyCSharpSha256.ToLowerInvariant()));
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Fact]
    public void Gate_reports_G0_HASH_for_a_non_baseline_game_dir()
    {
        // A fake game directory with a dummy (non-game) Assembly-CSharp.dll; no game files are copied.
        var root = Path.Combine(Path.GetTempPath(), "roimcp-gate-tests", Guid.NewGuid().ToString("N"));
        var managed = GameEnvironment.ManagedDirFor(root);
        Directory.CreateDirectory(managed);
        try
        {
            File.WriteAllBytes(Path.Combine(managed, "Assembly-CSharp.dll"), new byte[] { 0x4d, 0x5a, 0, 0 });
            var observer = Path.Combine(root, "RoiMcpObserver.dll");
            File.WriteAllBytes(observer, new byte[] { 0x4d, 0x5a });
            var r = Gate.Run(new GateOptions
            {
                AssemblyPath = observer,
                GameDir = root,
                AllowlistPath = TestEnv.AllowlistPath,
                DenylistPath = TestEnv.DenylistPath,
            });
            Assert.Equal(1, r.ExitCode);
            Assert.Equal(new[] { Rules.Hash }, r.RuleIds.ToArray());
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Fact]
    public void Cli_usage_errors_exit_2()
    {
        var o = new StringWriter();
        var e = new StringWriter();
        Assert.Equal(2, Program.Run(Array.Empty<string>(), o, e));
        Assert.Equal(2, Program.Run(new[] { "--bogus" }, o, e));
        Assert.Equal(2, Program.Run(new[] { "--assembly", "missing.dll", "--allowlist", "missing.json", "--denylist", "missing.json" }, o, e));
    }

    [Fact]
    public void Shipped_config_files_are_valid()
    {
        var allow = Allowlist.Load(TestEnv.AllowlistPath);
        Assert.NotEmpty(allow.Game);
        Assert.NotEmpty(allow.Unity);
        var deny = Denylist.Load(TestEnv.DenylistPath);
        Assert.NotEmpty(deny.Entries);
        foreach (var e in allow.Game)
        {
            Assert.Null(deny.MatchMemberString(e.Member));
        }

        // Categorized acknowledgements must name a defined category.
        Assert.Equal(new[] { "guarded_actor_component_cache", "scratch_pool", "unreachable_lazy_init" },
            allow.CollectionWriteCategories.Keys.OrderBy(k => k, StringComparer.Ordinal).ToArray());
        foreach (var e in allow.Game)
        {
            foreach (var a in e.TransitiveFindingsAck ?? new List<AckEntry>())
            {
                if (a.Category != null)
                {
                    Assert.True(allow.CollectionWriteCategories.ContainsKey(a.Category), e.Member + ": unknown category " + a.Category);
                }
            }
        }
        foreach (var e in allow.Unity)
        {
            Assert.Null(deny.MatchMemberString(e.Member));
        }
    }

    [Fact]
    public void Allowlist_rejects_wildcards_and_todo_placeholders()
    {
        var bad = new AllowlistFile
        {
            Schema = Allowlist.SchemaName,
            SchemaVersion = 1,
            Unity = new List<AllowEntry>(),
            Game = new List<AllowEntry> { new() { Member = "ProjectAutomata.World::get_*()", Kind = "getter", Evidence = "x", Review = "x" } },
        };
        Assert.Throws<GateConfigException>(() => Allowlist.FromFile(bad, "test"));

        bad.Game[0] = new AllowEntry { Member = "ProjectAutomata.World::get_seed()", Kind = "getter", Evidence = "x", Review = "TODO" };
        Assert.Throws<GateConfigException>(() => Allowlist.FromFile(bad, "test"));

        bad.Game[0] = new AllowEntry { Member = "ProjectAutomata.World::get_seed()", Kind = "getter", Evidence = "World.cs:1", Review = "auto-property" };
        Assert.Single(Allowlist.FromFile(bad, "test").Game);
    }
}

/// <summary>Configuration tests that need the game assemblies.</summary>
public sealed class ConfigTests
{
    [GameFact]
    public void Every_denylist_entry_matches_the_baseline_game()
    {
        var deny = Denylist.Load(TestEnv.DenylistPath);
        using var env = new GameEnvironment(TestEnv.GameDir);
        env.LoadGameAssemblies();
        var unmatched = DenylistCheck.Run(env, deny).Where(x => x.Matches == 0).Select(x => x.Entry.Member).ToList();
        Assert.True(unmatched.Count == 0, "unmatched denylist entries: " + string.Join(", ", unmatched));
    }

    [GameFact]
    public void Installed_game_is_the_baseline()
    {
        var hash = GameEnvironment.Sha256OfFile(Path.Combine(TestEnv.ManagedDir, "Assembly-CSharp.dll"));
        Assert.True(GameEnvironment.HashMatches(hash), $"installed Assembly-CSharp.dll {hash} is not the PRD 3.1 baseline");
    }

    [GameFact]
    public void Shipped_unity_allowlist_resolves_and_base_fixture_passes()
    {
        using var f = Fixture.Compile("// base fixture only");
        var r = f.RunGate();
        GateAssert.Passes(r);
        Assert.DoesNotContain(r.Violations, v => v.Location.StartsWith("allowlist unity", StringComparison.Ordinal));
    }

    [GameFact]
    public void Cli_end_to_end_report_and_exit_codes()
    {
        using var f = Fixture.Compile("namespace RoiMcp.Observer.ReadLayer { public static class P { public static int S() { return ProjectAutomata.World.seed; } } }");
        var allow = f.WriteAllowlist(Array.Empty<AllowEntry>());
        var report = Path.Combine(f.Dir, "report.json");
        var o = new StringWriter();
        var code = Program.Run(new[]
        {
            "--assembly", f.DllPath, "--game-dir", TestEnv.GameDir, "--allowlist", allow,
            "--denylist", TestEnv.DenylistPath, "--report", report, "--suggest",
        }, o, new StringWriter());
        Assert.Equal(1, code);
        var text = o.ToString();
        Assert.Contains("G-ALLOW  RoiMcp.Observer.ReadLayer.P::S IL_", text);
        Assert.Contains("\"member\": \"ProjectAutomata.World::get_seed()\"", text);
        Assert.True(File.Exists(report));
        Assert.Contains("\"result\": \"fail\"", File.ReadAllText(report));
    }
}
