using System.Text.Json.Serialization;
using Mono.Cecil;

namespace RoiMcp.ReadOnlyGate;

internal sealed class GateOptions
{
    public required string AssemblyPath { get; init; }

    public required string GameDir { get; init; }

    public required string AllowlistPath { get; init; }

    public required string DenylistPath { get; init; }

    public string? ReportPath { get; init; }

    public bool Suggest { get; init; }

    public bool AllowDebugFaults { get; init; }
}

internal sealed class AckResult
{
    [JsonPropertyName("id")]
    public required string Id { get; init; }

    [JsonPropertyName("kind")]
    public required string Kind { get; init; }

    [JsonPropertyName("target")]
    public required string Target { get; init; }

    [JsonPropertyName("container")]
    public required string Container { get; init; }

    [JsonPropertyName("via")]
    public required IReadOnlyList<string> Via { get; init; }

    [JsonPropertyName("ackable")]
    public bool Ackable { get; init; }

    [JsonPropertyName("acknowledged")]
    public bool Acknowledged { get; init; }

    [JsonPropertyName("justification")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Justification { get; init; }

    [JsonPropertyName("category")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Category { get; init; }
}

internal sealed class RootReport
{
    [JsonPropertyName("member")]
    public required string Member { get; init; }

    [JsonPropertyName("kind")]
    public required string Kind { get; init; }

    [JsonPropertyName("methods_scanned")]
    public int MethodsScanned { get; init; }

    [JsonPropertyName("capped")]
    public bool Capped { get; init; }

    [JsonPropertyName("findings")]
    public List<AckResult> Findings { get; } = new();

    [JsonPropertyName("unity_boundary_calls")]
    public List<string> UnityBoundaryCalls { get; init; } = new();
}

internal sealed class GateResult
{
    public List<Violation> Violations { get; } = new();

    public List<string> Warnings { get; } = new();

    public List<RootReport> Transitive { get; } = new();

    public List<Suggestion> Suggestions { get; } = new();

    public Dictionary<string, List<RootReport>> SuggestionFindings { get; } = new(StringComparer.Ordinal);

    public GateStats Stats { get; set; } = new();

    public string? AssemblySha256 { get; set; }

    public string? GameAssemblySha256 { get; set; }

    public string? ModClass { get; set; }

    public int ExitCode => Violations.Count == 0 ? 0 : 1;

    public IEnumerable<string> RuleIds => Violations.Select(v => v.Rule).Distinct();
}

internal static class Gate
{
    public static GateResult Run(GateOptions o)
    {
        if (!File.Exists(o.AssemblyPath))
        {
            throw new GateConfigException($"Observer assembly not found: {o.AssemblyPath}");
        }

        var managed = GameEnvironment.ManagedDirFor(o.GameDir);
        var acs = Path.Combine(managed, "Assembly-CSharp.dll");
        if (!File.Exists(acs))
        {
            throw new GateConfigException($"Game assemblies not found: {acs} (set --game-dir or ROI_GAME_DIR)");
        }

        var allow = Allowlist.Load(o.AllowlistPath);
        var deny = Denylist.Load(o.DenylistPath);
        var result = new GateResult();

        // G0: baseline hash. No override exists.
        var hash = GameEnvironment.Sha256OfFile(acs);
        result.GameAssemblySha256 = hash;
        if (!GameEnvironment.HashMatches(hash))
        {
            result.Violations.Add(new Violation(Rules.Hash, acs,
                $"Assembly-CSharp.dll SHA-256 {hash} != baseline {GameEnvironment.ExpectedAssemblyCSharpSha256} (PRD 3.1); no override exists"));
            return result;
        }

        result.AssemblySha256 = GameEnvironment.Sha256OfFile(o.AssemblyPath);

        using var env = new GameEnvironment(o.GameDir);
        env.LoadGameAssemblies();
        var observer = GameEnvironment.ReadAssemblyFile(o.AssemblyPath, env);
        env.ObserverModule = observer.MainModule;

        var index = new GameIndex(env);
        var analyzer = new ObserverAnalyzer(env, index, allow, deny, o.AllowDebugFaults);
        analyzer.Analyze(observer.MainModule);
        result.Violations.AddRange(analyzer.Violations);
        result.ModClass = analyzer.ModClassName;

        // G3 self-check: no allowlist entry may be denylisted.
        for (var i = 0; i < allow.Game.Count; i++)
        {
            var d = deny.MatchMemberString(allow.Game[i].Member);
            if (d != null)
            {
                result.Violations.Add(new Violation(Rules.Denylist, $"allowlist game[{i}]",
                    $"allowlist entry {allow.Game[i].Member} is denylisted [{d.Member}: {d.Reason}]"));
            }
        }

        for (var i = 0; i < allow.Unity.Count; i++)
        {
            var d = deny.MatchMemberString(allow.Unity[i].Member);
            if (d != null)
            {
                result.Violations.Add(new Violation(Rules.Denylist, $"allowlist unity[{i}]",
                    $"allowlist entry {allow.Unity[i].Member} is denylisted [{d.Member}: {d.Reason}]"));
            }
        }

        // Transitive scan of every allowlisted game method / getter (PRD 10.4).
        var scanner = new TransitiveScanner(env, index, deny);
        for (var i = 0; i < allow.Game.Count; i++)
        {
            var entry = allow.Game[i];
            Names.TryParseMember(entry.Member, out var pm);
            if (entry.Kind == "field_read")
            {
                if (index.FindField(pm.Key) == null)
                {
                    result.Violations.Add(new Violation(Rules.Allow, $"allowlist game[{i}]",
                        $"field_read entry {entry.Member} does not resolve to a field in the game assemblies"));
                }

                continue;
            }

            var defs = index.FindMethods(pm.Key);
            if (defs.Count == 0)
            {
                result.Violations.Add(new Violation(Rules.Allow, $"allowlist game[{i}]",
                    $"{entry.Kind} entry {entry.Member} does not resolve to a method in the game assemblies"));
                continue;
            }

            foreach (var def in defs)
            {
                if ((entry.Kind == "getter") != def.IsGetter)
                {
                    result.Violations.Add(new Violation(Rules.Allow, $"allowlist game[{i}]",
                        $"entry {entry.Member} has kind {entry.Kind} but the member is {(def.IsGetter ? "a getter" : "not a getter")}"));
                    continue;
                }

                var scan = scanner.Scan(entry.Member, def, index.TypeArgumentsOf(pm.Type), MethodArgumentsOf(index, pm));
                result.Stats.TransitiveRoots++;
                result.Stats.TransitiveMethodsScanned += scan.MethodsScanned;
                var root = EvaluateAcks(entry, scan, result, $"allowlist game[{i}]", allow);
                result.Transitive.Add(root);
            }
        }

        // Unity entries must resolve against UnityEngine.CoreModule (stale entries fail).
        var unityCore = env.Load("UnityEngine.CoreModule");
        if (unityCore != null)
        {
            var unityIndex = new GameIndex(env, new[] { unityCore.MainModule });
            for (var i = 0; i < allow.Unity.Count; i++)
            {
                var entry = allow.Unity[i];
                Names.TryParseMember(entry.Member, out var pm);
                var resolves = entry.Kind == "field_read" ? unityIndex.FindField(pm.Key) != null : unityIndex.FindMethods(pm.Key).Count > 0;
                if (!resolves)
                {
                    result.Violations.Add(new Violation(Rules.Allow, $"allowlist unity[{i}]",
                        $"{entry.Kind} entry {entry.Member} does not resolve in UnityEngine.CoreModule"));
                }
            }
        }

        foreach (var e in allow.Game)
        {
            if (!analyzer.UsedEntries.Contains(e))
            {
                result.Warnings.Add($"game allowlist entry not referenced by the observer: {e.Member} ({e.Kind})");
            }
        }

        var unusedUnity = allow.Unity.Count(e => !analyzer.UsedEntries.Contains(e));
        if (unusedUnity > 0)
        {
            result.Warnings.Add($"{unusedUnity} of {allow.Unity.Count} unity allowlist entries are not referenced by the observer");
        }

        // Suggestions (skeletons + transitive findings for unlisted game methods).
        if (o.Suggest)
        {
            foreach (var s in analyzer.Suggestions.Values.OrderBy(s => s.Section, StringComparer.Ordinal).ThenBy(s => s.Entry.Member, StringComparer.Ordinal))
            {
                if (s.GameMethod != null && s.GameMethod.HasBody && index.IsGameMethod(s.GameMethod))
                {
                    Names.TryParseMember(s.Entry.Member, out var spm);
                    var scan = scanner.Scan(s.Entry.Member, s.GameMethod, index.TypeArgumentsOf(spm.Type), MethodArgumentsOf(index, spm));
                    s.Entry.TransitiveFindingsAck = scan.Findings
                        .Select(f => new AckEntry
                        {
                            Finding = f.Id,
                            Justification = f.Ackable ? "TODO: justify" : "TODO: write into a collection; acknowledgeable only with a matching collection_write_categories category",
                            Category = f.Ackable ? null : "TODO",
                        })
                        .ToList();
                }
                else if (s.Section == "game" && s.Entry.Kind != "field_read")
                {
                    s.Entry.TransitiveFindingsAck = new List<AckEntry>();
                }

                result.Suggestions.Add(s);
            }
        }

        result.Stats = Merge(analyzer.Stats, result.Stats);
        return result;
    }

    private static TypeReference?[]? MethodArgumentsOf(GameIndex index, ParsedMember pm)
    {
        if (pm.Name == pm.BareName)
        {
            return null;
        }

        // "Name<A,B>" -> resolve A, B the same way as type arguments.
        return index.TypeArgumentsOf(pm.Name);
    }

    private static GateStats Merge(GateStats a, GateStats b)
    {
        a.TransitiveRoots = b.TransitiveRoots;
        a.TransitiveMethodsScanned = b.TransitiveMethodsScanned;
        return a;
    }

    /// <summary>True when a categorized acknowledgement legitimately covers a collection-write finding.</summary>
    private static bool CategoryCovers(Allowlist allow, AckEntry? ack, TransitiveFinding f)
    {
        if (ack?.Category == null || !allow.CollectionWriteCategories.TryGetValue(ack.Category, out var cat))
        {
            return false;
        }

        if (cat.ContainerPatterns != null && cat.ContainerPatterns.Any(p => Glob.IsMatch(p, f.Container)))
        {
            return true;
        }

        return cat.ViaPatterns != null && f.Via.Any(v => cat.ViaPatterns.Any(p => Glob.IsMatch(p, v)));
    }

    private static RootReport EvaluateAcks(AllowEntry entry, TransitiveResult scan, GateResult result, string location, Allowlist allow)
    {
        var root = new RootReport
        {
            Member = entry.Member,
            Kind = entry.Kind,
            MethodsScanned = scan.MethodsScanned,
            Capped = scan.Capped,
            UnityBoundaryCalls = scan.UnityBoundaryCalls.ToList(),
        };
        var acks = entry.TransitiveFindingsAck ?? new List<AckEntry>();
        var usedAcks = new HashSet<AckEntry>();
        foreach (var f in scan.Findings)
        {
            var ack = acks.FirstOrDefault(a => Glob.IsMatch(a.Finding, f.Id) || Glob.IsMatch(a.Finding, f.ShortId));
            if (ack != null)
            {
                usedAcks.Add(ack);
            }

            bool categorized = !f.Ackable && CategoryCovers(allow, ack, f);
            if (!f.Ackable && !categorized)
            {
                result.Violations.Add(new Violation(Rules.TransColl, location,
                    $"{f.Id} (write into a game collection; cannot be acknowledged{(ack != null ? " without a matching collection_write_categories entry, acknowledgement ignored" : string.Empty)})"));
            }
            else if (ack == null)
            {
                result.Violations.Add(new Violation(Rules.Trans, location, $"unacknowledged transitive finding: {f.Id}"));
            }

            root.Findings.Add(new AckResult
            {
                Id = f.Id,
                Kind = f.Kind,
                Target = f.Target,
                Container = f.Container,
                Via = f.Via,
                Ackable = f.Ackable,
                Acknowledged = ack != null && (f.Ackable || categorized),
                Justification = ack?.Justification,
                Category = categorized ? ack?.Category : null,
            });
        }

        foreach (var a in acks.Where(a => !usedAcks.Contains(a)))
        {
            result.Warnings.Add($"acknowledgement matches no current finding: {entry.Member}: {a.Finding}");
        }

        return root;
    }
}
