using System.Text.Json;
using System.Text.Json.Serialization;

namespace RoiMcp.ReadOnlyGate;

internal sealed class AckEntry
{
    [JsonPropertyName("finding")]
    public string Finding { get; set; } = string.Empty;

    [JsonPropertyName("justification")]
    public string Justification { get; set; } = string.Empty;

    /// <summary>
    /// Required to acknowledge a write into a game collection: names one of the allowlist's
    /// collection_write_categories, whose patterns must match the write's containing method or call path.
    /// </summary>
    [JsonPropertyName("category")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Category { get; set; }
}

/// <summary>
/// A narrowly scoped class of transitive writes into collections that may be acknowledged (PRD 10.4
/// interpretation, see docs/READ-ONLY-GATE.md): pool/scratch bookkeeping and lazy-initialization paths that
/// the observer guarantees unreachable. Writes into game state collections never match a category.
/// </summary>
internal sealed class CollectionWriteCategory
{
    [JsonPropertyName("description")]
    public string Description { get; set; } = string.Empty;

    /// <summary>Globs over the method containing the collection write.</summary>
    [JsonPropertyName("container_patterns")]
    public List<string>? ContainerPatterns { get; set; }

    /// <summary>Globs over methods on the call path from the allowlisted member to the write.</summary>
    [JsonPropertyName("via_patterns")]
    public List<string>? ViaPatterns { get; set; }

    /// <summary>The runtime guarantee the observer relies on (documentation; reviewed by humans).</summary>
    [JsonPropertyName("observer_guarantee")]
    public string ObserverGuarantee { get; set; } = string.Empty;
}

internal sealed class AllowEntry
{
    [JsonPropertyName("member")]
    public string Member { get; set; } = string.Empty;

    [JsonPropertyName("kind")]
    public string Kind { get; set; } = string.Empty;

    [JsonPropertyName("private")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public bool? Private { get; set; }

    [JsonPropertyName("data_map_id")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? DataMapId { get; set; }

    [JsonPropertyName("evidence")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Evidence { get; set; }

    [JsonPropertyName("review")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Review { get; set; }

    [JsonPropertyName("source")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Source { get; set; }

    [JsonPropertyName("transitive_findings_ack")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public List<AckEntry>? TransitiveFindingsAck { get; set; }

    [JsonIgnore]
    public bool IsPrivate => Private == true;
}

internal sealed class AllowlistFile
{
    [JsonPropertyName("schema")]
    public string? Schema { get; set; }

    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; }

    [JsonPropertyName("description")]
    public string? Description { get; set; }

    [JsonPropertyName("unity")]
    public List<AllowEntry>? Unity { get; set; }

    [JsonPropertyName("game")]
    public List<AllowEntry>? Game { get; set; }

    [JsonPropertyName("collection_write_categories")]
    public Dictionary<string, CollectionWriteCategory>? CollectionWriteCategories { get; set; }
}

internal sealed class DenyEntry
{
    [JsonPropertyName("member")]
    public string Member { get; set; } = string.Empty;

    [JsonPropertyName("reason")]
    public string Reason { get; set; } = string.Empty;

    [JsonPropertyName("source")]
    public string Source { get; set; } = string.Empty;
}

internal sealed class DenylistFile
{
    [JsonPropertyName("schema")]
    public string? Schema { get; set; }

    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; }

    [JsonPropertyName("description")]
    public string? Description { get; set; }

    [JsonPropertyName("entries")]
    public List<DenyEntry>? Entries { get; set; }
}

internal static class ConfigJson
{
    public static readonly JsonSerializerOptions Options = new()
    {
        ReadCommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = false,
        WriteIndented = true,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };
}

internal sealed class Allowlist
{
    public const string SchemaName = "roi-mcp/readonly-gate-allowlist";

    private static readonly string[] Kinds = { "getter", "method", "field_read" };

    private readonly Dictionary<(string Member, string Kind), AllowEntry> _game = new();
    private readonly Dictionary<(string Member, string Kind), AllowEntry> _unity = new();

    public IReadOnlyList<AllowEntry> Game { get; private set; } = Array.Empty<AllowEntry>();

    public IReadOnlyList<AllowEntry> Unity { get; private set; } = Array.Empty<AllowEntry>();

    public IReadOnlyDictionary<string, CollectionWriteCategory> CollectionWriteCategories { get; private set; } =
        new Dictionary<string, CollectionWriteCategory>();

    public static Allowlist Load(string path)
    {
        if (!File.Exists(path))
        {
            throw new GateConfigException($"Allowlist not found: {path}");
        }

        AllowlistFile? file;
        try
        {
            file = JsonSerializer.Deserialize<AllowlistFile>(File.ReadAllText(path), ConfigJson.Options);
        }
        catch (JsonException e)
        {
            throw new GateConfigException($"Allowlist is not valid JSON ({path}): {e.Message}");
        }

        if (file == null)
        {
            throw new GateConfigException($"Allowlist is empty: {path}");
        }

        return FromFile(file, path);
    }

    public static Allowlist FromFile(AllowlistFile file, string origin)
    {
        if (file.Schema != SchemaName)
        {
            throw new GateConfigException($"Allowlist {origin}: \"schema\" must be \"{SchemaName}\"");
        }

        if (file.SchemaVersion != 1)
        {
            throw new GateConfigException($"Allowlist {origin}: unsupported schema_version {file.SchemaVersion}");
        }

        if (file.Game == null || file.Unity == null)
        {
            throw new GateConfigException($"Allowlist {origin}: both \"game\" and \"unity\" arrays are required");
        }

        var categories = file.CollectionWriteCategories ?? new Dictionary<string, CollectionWriteCategory>();
        foreach (var kv in categories)
        {
            var c = kv.Value;
            if (string.IsNullOrWhiteSpace(c.Description) || string.IsNullOrWhiteSpace(c.ObserverGuarantee) ||
                ((c.ContainerPatterns == null || c.ContainerPatterns.Count == 0) && (c.ViaPatterns == null || c.ViaPatterns.Count == 0)))
            {
                throw new GateConfigException($"Allowlist {origin}: collection_write_categories.{kv.Key} needs description, observer_guarantee and patterns");
            }
        }

        var a = new Allowlist { Game = file.Game, Unity = file.Unity, CollectionWriteCategories = categories };
        foreach (var e in file.Unity)
        {
            Validate(e, origin, "unity", requireGovernance: false);
            if (!a._unity.TryAdd((e.Member, e.Kind), e))
            {
                throw new GateConfigException($"Allowlist {origin}: duplicate unity entry {e.Member} ({e.Kind})");
            }
        }

        foreach (var e in file.Game)
        {
            Validate(e, origin, "game", requireGovernance: true);
            if (!a._game.TryAdd((e.Member, e.Kind), e))
            {
                throw new GateConfigException($"Allowlist {origin}: duplicate game entry {e.Member} ({e.Kind})");
            }
        }

        return a;
    }

    private static void Validate(AllowEntry e, string origin, string section, bool requireGovernance)
    {
        var where = $"Allowlist {origin} [{section}] \"{e.Member}\"";
        if (string.IsNullOrWhiteSpace(e.Member))
        {
            throw new GateConfigException($"Allowlist {origin} [{section}]: entry without \"member\"");
        }

        if (Glob.HasWildcard(e.Member))
        {
            throw new GateConfigException($"{where}: wildcards are not allowed in allowlist entries");
        }

        if (!Names.TryParseMember(e.Member, out var parsed))
        {
            throw new GateConfigException($"{where}: not a canonical member string (Type::member)");
        }

        if (!Kinds.Contains(e.Kind))
        {
            throw new GateConfigException($"{where}: kind must be getter|method|field_read");
        }

        if (e.Kind == "field_read" && parsed.IsMethod)
        {
            throw new GateConfigException($"{where}: field_read entries must not have a parameter list");
        }

        if (e.Kind != "field_read" && !parsed.IsMethod)
        {
            throw new GateConfigException($"{where}: {e.Kind} entries need a parameter list, e.g. \"()\"");
        }

        if (e.Private != null && e.Kind != "field_read")
        {
            throw new GateConfigException($"{where}: \"private\" is only valid for field_read entries");
        }

        if (e.Kind == "getter" && !parsed.BareName.StartsWith("get_", StringComparison.Ordinal))
        {
            throw new GateConfigException($"{where}: getter entries must name the accessor (get_X)");
        }

        if (requireGovernance)
        {
            if (string.IsNullOrWhiteSpace(e.Evidence) || string.IsNullOrWhiteSpace(e.Review))
            {
                throw new GateConfigException($"{where}: game entries require \"evidence\" and \"review\" (PRD 10.4)");
            }

            if (ContainsTodo(e.Review) || ContainsTodo(e.Evidence) || ContainsTodo(e.DataMapId))
            {
                throw new GateConfigException($"{where}: unreviewed placeholder (TODO) left in entry");
            }
        }

        if (e.TransitiveFindingsAck != null)
        {
            if (e.Kind == "field_read")
            {
                throw new GateConfigException($"{where}: field_read entries have no transitive findings to acknowledge");
            }

            foreach (var ack in e.TransitiveFindingsAck)
            {
                if (string.IsNullOrWhiteSpace(ack.Finding) || string.IsNullOrWhiteSpace(ack.Justification))
                {
                    throw new GateConfigException($"{where}: every transitive_findings_ack needs \"finding\" and \"justification\"");
                }

                if (ContainsTodo(ack.Justification))
                {
                    throw new GateConfigException($"{where}: unreviewed placeholder (TODO) in acknowledgement justification");
                }

                if (ack.Category != null && ContainsTodo(ack.Category))
                {
                    throw new GateConfigException($"{where}: unreviewed placeholder (TODO) in acknowledgement category");
                }
            }
        }
    }

    private static bool ContainsTodo(string? s) => s != null && s.Contains("TODO", StringComparison.Ordinal);

    public AllowEntry? FindGame(string member, string kind) => _game.TryGetValue((member, kind), out var e) ? e : null;

    public AllowEntry? FindUnity(string member, string kind) => _unity.TryGetValue((member, kind), out var e) ? e : null;

    /// <summary>
    /// Finds a private field_read entry for a reflection lookup. Accepts both the closed (as written in
    /// ldtoken) and the open generic declaring type name.
    /// </summary>
    public AllowEntry? FindPrivateField(string closedType, string openType, string fieldName)
    {
        foreach (var t in new[] { closedType, openType }.Distinct())
        {
            if (_game.TryGetValue((t + "::" + fieldName, "field_read"), out var e) && e.IsPrivate)
            {
                return e;
            }
        }

        return null;
    }
}

internal sealed class Denylist
{
    public const string SchemaName = "roi-mcp/readonly-gate-denylist";

    private readonly List<(DenyEntry Entry, DenyPattern Pattern)> _patterns = new();

    public IReadOnlyList<DenyEntry> Entries => _patterns.Select(p => p.Entry).ToList();

    public static Denylist Load(string path)
    {
        if (!File.Exists(path))
        {
            throw new GateConfigException($"Denylist not found: {path}");
        }

        DenylistFile? file;
        try
        {
            file = JsonSerializer.Deserialize<DenylistFile>(File.ReadAllText(path), ConfigJson.Options);
        }
        catch (JsonException e)
        {
            throw new GateConfigException($"Denylist is not valid JSON ({path}): {e.Message}");
        }

        if (file?.Entries == null || file.Schema != SchemaName || file.SchemaVersion != 1)
        {
            throw new GateConfigException($"Denylist {path}: expected schema \"{SchemaName}\", schema_version 1 and an \"entries\" array");
        }

        var d = new Denylist();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var e in file.Entries)
        {
            if (string.IsNullOrWhiteSpace(e.Member) || string.IsNullOrWhiteSpace(e.Reason) || string.IsNullOrWhiteSpace(e.Source))
            {
                throw new GateConfigException($"Denylist {path}: every entry needs member, reason and source (\"{e.Member}\")");
            }

            if (!seen.Add(e.Member))
            {
                throw new GateConfigException($"Denylist {path}: duplicate entry {e.Member}");
            }

            d._patterns.Add((e, DenyPattern.Parse(e.Member)));
        }

        return d;
    }

    public DenyEntry? MatchMember(string declaringTypeClosed, string declaringTypeOpen, IReadOnlyList<string> outerTypes, string bareName, string nameAndParams)
    {
        foreach (var (entry, p) in _patterns)
        {
            if (p.MatchesMember(declaringTypeClosed, declaringTypeOpen, outerTypes, bareName, nameAndParams))
            {
                return entry;
            }
        }

        return null;
    }

    public DenyEntry? MatchType(string typeClosed, string typeOpen, IReadOnlyList<string> outerTypes)
    {
        foreach (var (entry, p) in _patterns)
        {
            if (p.IsTypePattern && p.MatchesType(typeClosed, typeOpen, outerTypes))
            {
                return entry;
            }
        }

        return null;
    }

    /// <summary>Matches a canonical member string (used for the allowlist self-check).</summary>
    public DenyEntry? MatchMemberString(string member)
    {
        if (!Names.TryParseMember(member, out var pm))
        {
            return null;
        }

        var outer = OuterTypesOf(pm.OpenType);
        var nameAndParams = pm.IsMethod ? pm.BareName + "(" + pm.Parameters + ")" : pm.BareName;
        return MatchMember(pm.Type, pm.OpenType, outer, pm.BareName, nameAndParams);
    }

    public static IReadOnlyList<string> OuterTypesOf(string openType)
    {
        var list = new List<string>();
        var idx = openType.LastIndexOf('/');
        while (idx > 0)
        {
            openType = openType.Substring(0, idx);
            list.Add(openType);
            idx = openType.LastIndexOf('/');
        }

        return list;
    }
}

/// <summary>
/// Denylist pattern. Two forms:
///   "TypeGlob::MemberGlob"            member pattern; MemberGlob without '(' matches the member name
///                                      (all overloads), with '(' it matches "name(params)".
///   "TypeGlob"                         type pattern; matches every member of, and every reference to,
///                                      a matching type (or a type nested in it).
/// Type globs are matched against the open type name and the closed (instantiated) name.
/// </summary>
internal sealed class DenyPattern
{
    private DenyPattern(string typeGlob, string? memberGlob)
    {
        TypeGlob = typeGlob;
        MemberGlob = memberGlob;
    }

    public string TypeGlob { get; }

    public string? MemberGlob { get; }

    public bool IsTypePattern => MemberGlob == null;

    public static DenyPattern Parse(string s)
    {
        var idx = s.IndexOf("::", StringComparison.Ordinal);
        return idx < 0 ? new DenyPattern(s, null) : new DenyPattern(s.Substring(0, idx), s.Substring(idx + 2));
    }

    public bool MatchesType(string closed, string open, IReadOnlyList<string> outerTypes)
    {
        if (Glob.IsMatch(TypeGlob, open) || Glob.IsMatch(TypeGlob, closed))
        {
            return true;
        }

        return outerTypes.Any(o => Glob.IsMatch(TypeGlob, o));
    }

    public bool MatchesMember(string closed, string open, IReadOnlyList<string> outerTypes, string bareName, string nameAndParams)
    {
        if (IsTypePattern)
        {
            return MatchesType(closed, open, outerTypes);
        }

        if (!(Glob.IsMatch(TypeGlob, open) || Glob.IsMatch(TypeGlob, closed)))
        {
            return false;
        }

        return MemberGlob!.Contains('(') ? Glob.IsMatch(MemberGlob, nameAndParams) : Glob.IsMatch(MemberGlob, bareName);
    }
}
