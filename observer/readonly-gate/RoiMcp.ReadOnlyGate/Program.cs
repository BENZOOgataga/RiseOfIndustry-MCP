using System.Text.Json;
using System.Text.Json.Nodes;

namespace RoiMcp.ReadOnlyGate;

public static class Program
{
    private const string Usage =
        "Usage: RoiMcp.ReadOnlyGate --assembly <RoiMcpObserver.dll> --allowlist <allowlist.json> --denylist <denylist.json>\n" +
        "                           [--game-dir <ROI install dir>] [--report <out.json>] [--suggest] [--allow-debug-faults]\n" +
        "  --game-dir            default: %ROI_GAME_DIR%, else " + GameEnvironment.DefaultGameDir + "\n" +
        "  --suggest             also emit allowlist skeletons for every unlisted game/Unity reference\n" +
        "  --allow-debug-faults  ONLY for the DEBUG_FAULTS validation build; never for release builds\n" +
        "Exit codes: 0 pass, 1 violations, 2 usage/configuration error.";

    public static int Main(string[] args) => Run(args, Console.Out, Console.Error);

    internal static int Run(string[] args, TextWriter stdout, TextWriter stderr)
    {
        if (args.Contains("--check-denylist"))
        {
            return CheckDenylist(args, stdout, stderr);
        }

        GateOptions options;
        try
        {
            options = Parse(args);
        }
        catch (GateConfigException e)
        {
            stderr.WriteLine("error: " + e.Message);
            stderr.WriteLine(Usage);
            return 2;
        }

        GateResult result;
        try
        {
            result = Gate.Run(options);
        }
        catch (GateConfigException e)
        {
            stderr.WriteLine("error: " + e.Message);
            return 2;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or BadImageFormatException)
        {
            stderr.WriteLine("error: " + e.Message);
            return 2;
        }

        Print(result, options, stdout);

        if (options.ReportPath != null)
        {
            try
            {
                var dir = Path.GetDirectoryName(Path.GetFullPath(options.ReportPath));
                if (!string.IsNullOrEmpty(dir))
                {
                    Directory.CreateDirectory(dir);
                }

                File.WriteAllText(options.ReportPath, BuildReport(result, options).ToJsonString(ConfigJson.Options));
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                stderr.WriteLine("error: cannot write report: " + e.Message);
                return 2;
            }
        }

        return result.ExitCode;
    }

    private static int CheckDenylist(string[] args, TextWriter stdout, TextWriter stderr)
    {
        string? deny = null, gameDir = null;
        for (var i = 0; i < args.Length; i++)
        {
            if (args[i] == "--denylist" && i + 1 < args.Length)
            {
                deny = args[++i];
            }
            else if (args[i] == "--game-dir" && i + 1 < args.Length)
            {
                gameDir = args[++i];
            }
        }

        if (deny == null)
        {
            stderr.WriteLine("error: --check-denylist requires --denylist");
            return 2;
        }

        try
        {
            var denylist = Denylist.Load(deny);
            using var env = new GameEnvironment(GameEnvironment.ResolveGameDir(gameDir));
            var hash = GameEnvironment.Sha256OfFile(Path.Combine(env.ManagedDir, "Assembly-CSharp.dll"));
            if (!GameEnvironment.HashMatches(hash))
            {
                stdout.WriteLine($"{Rules.Hash}  {env.ManagedDir}  Assembly-CSharp.dll SHA-256 {hash} is not the baseline");
                return 1;
            }

            env.LoadGameAssemblies();
            var unmatched = 0;
            foreach (var (entry, matches) in DenylistCheck.Run(env, denylist))
            {
                if (matches == 0)
                {
                    unmatched++;
                    stdout.WriteLine($"UNMATCHED  {entry.Member}");
                }
            }

            stdout.WriteLine(unmatched == 0 ? $"OK: all {denylist.Entries.Count} denylist entries match the baseline game assemblies" : $"FAIL: {unmatched} unmatched entr(ies)");
            return unmatched == 0 ? 0 : 1;
        }
        catch (Exception e) when (e is GateConfigException or IOException or UnauthorizedAccessException)
        {
            stderr.WriteLine("error: " + e.Message);
            return 2;
        }
    }

    internal static GateOptions Parse(string[] args)
    {
        string? assembly = null, gameDir = null, allow = null, deny = null, report = null;
        bool suggest = false, debugFaults = false;
        for (var i = 0; i < args.Length; i++)
        {
            string Next()
            {
                if (i + 1 >= args.Length)
                {
                    throw new GateConfigException($"missing value for {args[i]}");
                }

                return args[++i];
            }

            switch (args[i])
            {
                case "--assembly": assembly = Next(); break;
                case "--game-dir": gameDir = Next(); break;
                case "--allowlist": allow = Next(); break;
                case "--denylist": deny = Next(); break;
                case "--report": report = Next(); break;
                case "--suggest": suggest = true; break;
                case "--allow-debug-faults": debugFaults = true; break;
                case "-h":
                case "--help":
                    throw new GateConfigException("help requested");
                default:
                    throw new GateConfigException($"unknown argument '{args[i]}'");
            }
        }

        if (assembly == null || allow == null || deny == null)
        {
            throw new GateConfigException("--assembly, --allowlist and --denylist are required");
        }

        return new GateOptions
        {
            AssemblyPath = assembly,
            GameDir = GameEnvironment.ResolveGameDir(gameDir),
            AllowlistPath = allow,
            DenylistPath = deny,
            ReportPath = report,
            Suggest = suggest,
            AllowDebugFaults = debugFaults,
        };
    }

    private static void Print(GateResult r, GateOptions o, TextWriter w)
    {
        w.WriteLine($"RoiMcp read-only gate: {o.AssemblyPath}");
        if (o.AllowDebugFaults)
        {
            w.WriteLine("WARNING  --allow-debug-faults is set: this build must NEVER be released");
        }

        foreach (var v in r.Violations.OrderBy(v => v.Rule, StringComparer.Ordinal).ThenBy(v => v.Location, StringComparer.Ordinal))
        {
            w.WriteLine(v.ToString());
        }

        foreach (var warn in r.Warnings)
        {
            w.WriteLine("WARN  " + warn);
        }

        if (o.Suggest && r.Suggestions.Count > 0)
        {
            w.WriteLine();
            w.WriteLine("Suggested allowlist entries (review every field before adding; TODO placeholders are rejected):");
            var obj = new JsonObject
            {
                ["game"] = JsonSerializer.SerializeToNode(r.Suggestions.Where(s => s.Section == "game").Select(s => s.Entry).ToList(), ConfigJson.Options),
                ["unity"] = JsonSerializer.SerializeToNode(r.Suggestions.Where(s => s.Section == "unity").Select(s => s.Entry).ToList(), ConfigJson.Options),
            };
            w.WriteLine(obj.ToJsonString(ConfigJson.Options));
        }

        var s = r.Stats;
        w.WriteLine(
            $"types={s.Types} methods={s.Methods} instructions={s.Instructions} member_refs={s.MemberReferences} game_refs={s.GameReferences} " +
            $"unity_refs={s.UnityReferences} reflection_lookups={s.ReflectionLookups} transitive_roots={s.TransitiveRoots} transitive_methods={s.TransitiveMethodsScanned}");
        w.WriteLine(r.Violations.Count == 0 ? "PASS" : $"FAIL ({r.Violations.Count} violation(s): {string.Join(", ", r.RuleIds.OrderBy(x => x, StringComparer.Ordinal))})");
    }

    internal static JsonObject BuildReport(GateResult r, GateOptions o)
    {
        var report = new JsonObject
        {
            ["schema"] = "roi-mcp/readonly-gate-report",
            ["schema_version"] = 1,
            ["result"] = r.Violations.Count == 0 ? "pass" : "fail",
            ["assembly"] = Path.GetFullPath(o.AssemblyPath),
            ["assembly_sha256"] = r.AssemblySha256,
            ["game_dir"] = o.GameDir,
            ["game_assembly_csharp_sha256"] = r.GameAssemblySha256,
            ["expected_assembly_csharp_sha256"] = GameEnvironment.ExpectedAssemblyCSharpSha256,
            ["allow_debug_faults"] = o.AllowDebugFaults,
            ["mod_class"] = r.ModClass,
            ["violations"] = new JsonArray(r.Violations.Select(v => (JsonNode)new JsonObject
            {
                ["rule"] = v.Rule,
                ["location"] = v.Location,
                ["message"] = v.Message,
            }).ToArray()),
            ["warnings"] = new JsonArray(r.Warnings.Select(w => (JsonNode)JsonValue.Create(w)!).ToArray()),
            ["transitive"] = JsonSerializer.SerializeToNode(r.Transitive, ConfigJson.Options),
            ["stats"] = JsonSerializer.SerializeToNode(r.Stats, ConfigJson.Options),
        };
        if (o.Suggest)
        {
            report["suggestions"] = new JsonArray(r.Suggestions.Select(s => (JsonNode)new JsonObject
            {
                ["section"] = s.Section,
                ["entry"] = JsonSerializer.SerializeToNode(s.Entry, ConfigJson.Options),
                ["referenced_from"] = new JsonArray(s.ReferencedFrom.Select(x => (JsonNode)JsonValue.Create(x)!).ToArray()),
            }).ToArray());
        }

        return report;
    }
}
