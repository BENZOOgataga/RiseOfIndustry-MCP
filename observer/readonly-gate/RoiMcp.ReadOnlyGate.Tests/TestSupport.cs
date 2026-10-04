using System.Text.Json;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Xunit;

namespace RoiMcp.ReadOnlyGate.Tests;

internal static class TestEnv
{
    public static string GameDir => GameEnvironment.ResolveGameDir(null);

    public static string ManagedDir => GameEnvironment.ManagedDirFor(GameDir);

    public static bool Available => File.Exists(Path.Combine(ManagedDir, "Assembly-CSharp.dll"));

    public static string SkipReason =>
        $"Rise of Industry Managed folder not found at '{ManagedDir}'. Set ROI_GAME_DIR to run the gate fixture tests.";

    public static string ConfigDir => Path.Combine(AppContext.BaseDirectory, "gate-config");

    public static string DenylistPath => Path.Combine(ConfigDir, "denylist.json");

    public static string AllowlistPath => Path.Combine(ConfigDir, "allowlist.json");
}

/// <summary>Fact that is skipped (not failed) when the game is not installed.</summary>
public sealed class GameFactAttribute : FactAttribute
{
    public GameFactAttribute()
    {
        if (!TestEnv.Available)
        {
            Skip = TestEnv.SkipReason;
        }
    }
}

/// <summary>A compiled fixture assembly in a private temp directory (deleted on dispose).</summary>
internal sealed class Fixture : IDisposable
{
    /// <summary>Minimal valid entry class used by every negative fixture so it contains exactly one violation.</summary>
    public const string BaseMod = @"
namespace RoiMcp.Observer
{
    public sealed class ObserverMod : ProjectAutomata.Mod
    {
        public override void OnModWasLoaded()
        {
        }
    }
}
";

    private static readonly string[] DefaultReferences =
    {
        "mscorlib", "System", "System.Core", "UnityEngine", "UnityEngine.CoreModule",
        "Assembly-CSharp", "Assembly-CSharp-firstpass", "Newtonsoft.Json",
    };

    private Fixture(string dir, string dllPath)
    {
        Dir = dir;
        DllPath = dllPath;
    }

    public string Dir { get; }

    public string DllPath { get; }

    public static Fixture Compile(string source, bool includeBaseMod = true, IEnumerable<string>? extraReferences = null, bool allowUnsafe = false)
    {
        var dir = Path.Combine(Path.GetTempPath(), "roimcp-gate-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(dir);
        var sources = new List<SyntaxTree>
        {
            CSharpSyntaxTree.ParseText(source, new CSharpParseOptions(LanguageVersion.CSharp7_3)),
        };
        if (includeBaseMod)
        {
            sources.Add(CSharpSyntaxTree.ParseText(BaseMod, new CSharpParseOptions(LanguageVersion.CSharp7_3)));
        }

        // Compile against the game's own Managed assemblies only (old-style mscorlib); never .NET 8 assemblies.
        var refs = DefaultReferences.Concat(extraReferences ?? Array.Empty<string>())
            .Select(n => MetadataReference.CreateFromFile(Path.Combine(TestEnv.ManagedDir, n + ".dll")))
            .ToList();

        var compilation = CSharpCompilation.Create(
            "RoiMcpObserver",
            sources,
            refs,
            new CSharpCompilationOptions(
                OutputKind.DynamicallyLinkedLibrary,
                optimizationLevel: OptimizationLevel.Release,
                allowUnsafe: allowUnsafe,
                nullableContextOptions: NullableContextOptions.Disable));

        var dll = Path.Combine(dir, "RoiMcpObserver.dll");
        using (var fs = File.Create(dll))
        {
            var emit = compilation.Emit(fs);
            if (!emit.Success)
            {
                var errors = string.Join(Environment.NewLine, emit.Diagnostics.Where(d => d.Severity == DiagnosticSeverity.Error));
                throw new InvalidOperationException("Fixture failed to compile:" + Environment.NewLine + errors);
            }
        }

        return new Fixture(dir, dll);
    }

    public string WriteAllowlist(IEnumerable<AllowEntry> extraGame, IEnumerable<AllowEntry>? extraUnity = null, bool includeModCtor = true)
    {
        var real = JsonSerializer.Deserialize<AllowlistFile>(File.ReadAllText(TestEnv.AllowlistPath), ConfigJson.Options)!;
        var game = new List<AllowEntry>();
        if (includeModCtor)
        {
            game.Add(Entries.ModCtor);
        }

        game.AddRange(extraGame);
        var file = new AllowlistFile
        {
            Schema = Allowlist.SchemaName,
            SchemaVersion = 1,
            Unity = real.Unity!.Concat(extraUnity ?? Array.Empty<AllowEntry>()).ToList(),
            Game = game,
            CollectionWriteCategories = real.CollectionWriteCategories,
        };
        var path = Path.Combine(Dir, "allowlist.test.json");
        File.WriteAllText(path, JsonSerializer.Serialize(file, ConfigJson.Options));
        return path;
    }

    public GateResult RunGate(IEnumerable<AllowEntry>? extraGame = null, bool allowDebugFaults = false, bool suggest = false, bool includeModCtor = true)
    {
        var allow = WriteAllowlist(extraGame ?? Array.Empty<AllowEntry>(), includeModCtor: includeModCtor);
        return Gate.Run(new GateOptions
        {
            AssemblyPath = DllPath,
            GameDir = TestEnv.GameDir,
            AllowlistPath = allow,
            DenylistPath = TestEnv.DenylistPath,
            Suggest = suggest,
            AllowDebugFaults = allowDebugFaults,
        });
    }

    public void Dispose()
    {
        try
        {
            Directory.Delete(Dir, recursive: true);
        }
        catch (IOException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }
    }
}

internal static class Entries
{
    public static AllowEntry ModCtor => Game("ProjectAutomata.Mod::.ctor()", "method");

    public static AllowEntry Game(string member, string kind, bool? isPrivate = null, params AckEntry[] acks) => new()
    {
        Member = member,
        Kind = kind,
        Private = isPrivate,
        DataMapId = "test",
        Evidence = "test fixture",
        Review = "test fixture entry",
        TransitiveFindingsAck = kind == "field_read" ? null : acks.ToList(),
    };
}

internal static class GateAssert
{
    public static string Describe(GateResult r) =>
        string.Join(Environment.NewLine, r.Violations.Select(v => v.ToString()));

    public static void Passes(GateResult r)
    {
        Assert.True(r.ExitCode == 0, "expected PASS but got:" + Environment.NewLine + Describe(r));
    }

    /// <summary>Asserts the gate failed with <paramref name="expected"/> and with no rule outside expected + alsoAllowed.</summary>
    public static void FailsWith(GateResult r, string expected, params string[] alsoAllowed)
    {
        Assert.True(r.ExitCode == 1, $"expected FAIL with {expected} but the gate passed");
        var rules = r.RuleIds.ToHashSet();
        Assert.True(rules.Contains(expected), $"expected rule {expected}; got:" + Environment.NewLine + Describe(r));
        var unexpected = rules.Except(alsoAllowed.Append(expected)).ToList();
        Assert.True(unexpected.Count == 0, $"unexpected rule(s) {string.Join(", ", unexpected)}:" + Environment.NewLine + Describe(r));
    }
}
