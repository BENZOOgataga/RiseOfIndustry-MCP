using System.Security.Cryptography;
using Mono.Cecil;

namespace RoiMcp.ReadOnlyGate;

internal enum Origin
{
    Observer,
    Game,
    Unity,
    Bcl,
    Newtonsoft,
    Harmony,
    Other,
    GenericParameter,
}

/// <summary>
/// Read-only view of the game's Managed folder. Every file is read into memory through a stream
/// opened with FileAccess.Read; nothing in the game directory is ever opened for writing.
/// </summary>
internal sealed class GameEnvironment : IAssemblyResolver
{
    public const string ExpectedAssemblyCSharpSha256 = "D62599EFD0CFCB9F343E7FF74AAC19533F572062B9CECA82E1D3911507D04803";

    public const string DefaultGameDir = @"C:\Program Files (x86)\Steam\steamapps\common\RiseOfIndustry";

    public static readonly string[] GameAssemblyNames = { "Assembly-CSharp", "Assembly-CSharp-firstpass" };

    public static readonly string[] BclAssemblyNames = { "mscorlib", "System", "System.Core" };

    private readonly Dictionary<string, AssemblyDefinition?> _cache = new(StringComparer.OrdinalIgnoreCase);

    public GameEnvironment(string gameDir)
    {
        GameDir = gameDir;
        ManagedDir = ManagedDirFor(gameDir);
    }

    public string GameDir { get; }

    public string ManagedDir { get; }

    public AssemblyDefinition? AssemblyCSharp { get; private set; }

    public AssemblyDefinition? Firstpass { get; private set; }

    public IReadOnlyList<ModuleDefinition> GameModules { get; private set; } = Array.Empty<ModuleDefinition>();

    public ModuleDefinition? ObserverModule { get; set; }

    public static string ManagedDirFor(string gameDir) => Path.Combine(gameDir, "Rise of Industry_Data", "Managed");

    public static string ResolveGameDir(string? explicitDir)
    {
        if (!string.IsNullOrWhiteSpace(explicitDir))
        {
            return explicitDir!;
        }

        var env = Environment.GetEnvironmentVariable("ROI_GAME_DIR");
        return string.IsNullOrWhiteSpace(env) ? DefaultGameDir : env!;
    }

    public static string Sha256OfFile(string path)
    {
        using var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        using var sha = SHA256.Create();
        return Convert.ToHexString(sha.ComputeHash(fs));
    }

    public static bool HashMatches(string actualHex) =>
        string.Equals(actualHex, ExpectedAssemblyCSharpSha256, StringComparison.OrdinalIgnoreCase);

    /// <summary>Loads the two game assemblies (read-only, in memory).</summary>
    public void LoadGameAssemblies()
    {
        AssemblyCSharp = Load("Assembly-CSharp") ?? throw new GateConfigException($"Cannot load Assembly-CSharp.dll from {ManagedDir}");
        Firstpass = Load("Assembly-CSharp-firstpass");
        var modules = new List<ModuleDefinition> { AssemblyCSharp.MainModule };
        if (Firstpass != null)
        {
            modules.Add(Firstpass.MainModule);
        }

        GameModules = modules;
    }

    public static AssemblyDefinition ReadAssemblyFile(string path, IAssemblyResolver resolver)
    {
        var bytes = ReadAllBytesShared(path);
        var parameters = new ReaderParameters
        {
            ReadWrite = false,
            ReadSymbols = false,
            InMemory = true,
            AssemblyResolver = resolver,
            ReadingMode = ReadingMode.Deferred,
        };
        return AssemblyDefinition.ReadAssembly(new MemoryStream(bytes, writable: false), parameters);
    }

    private static byte[] ReadAllBytesShared(string path)
    {
        using var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        using var ms = new MemoryStream();
        fs.CopyTo(ms);
        return ms.ToArray();
    }

    public AssemblyDefinition? Load(string simpleName)
    {
        if (_cache.TryGetValue(simpleName, out var cached))
        {
            return cached;
        }

        var path = Path.Combine(ManagedDir, simpleName + ".dll");
        AssemblyDefinition? asm = null;
        if (File.Exists(path))
        {
            asm = ReadAssemblyFile(path, this);
        }

        _cache[simpleName] = asm;
        return asm;
    }

    public AssemblyDefinition Resolve(AssemblyNameReference name) => Resolve(name, new ReaderParameters());

    public AssemblyDefinition Resolve(AssemblyNameReference name, ReaderParameters parameters)
    {
        if (ObserverModule != null && string.Equals(name.Name, ObserverModule.Assembly.Name.Name, StringComparison.OrdinalIgnoreCase))
        {
            return ObserverModule.Assembly;
        }

        return Load(name.Name) ?? throw new AssemblyResolutionException(name);
    }

    public void Dispose()
    {
        foreach (var a in _cache.Values)
        {
            a?.Dispose();
        }

        _cache.Clear();
    }

    public static string AssemblyNameOf(TypeReference t)
    {
        t = Names.ElementOf(t);
        var def = Resolver.Type(t);
        if (def != null)
        {
            return def.Module.Assembly.Name.Name;
        }

        return t.Scope switch
        {
            AssemblyNameReference a => a.Name,
            ModuleDefinition m => m.Assembly.Name.Name,
            ModuleReference mr => mr.Name,
            _ => string.Empty,
        };
    }

    public Origin OriginOf(TypeReference t)
    {
        t = Names.ElementOf(t);
        if (t is GenericParameter)
        {
            return Origin.GenericParameter;
        }

        if (ObserverModule != null)
        {
            if (t.Scope is ModuleDefinition sm && ReferenceEquals(sm, ObserverModule))
            {
                return Origin.Observer;
            }
        }

        var def = Resolver.Type(t);
        if (def != null && ObserverModule != null && ReferenceEquals(def.Module, ObserverModule))
        {
            return Origin.Observer;
        }

        return OriginOfAssembly(def != null ? def.Module.Assembly.Name.Name : AssemblyNameOf(t));
    }

    public static Origin OriginOfAssembly(string name)
    {
        if (GameAssemblyNames.Contains(name, StringComparer.OrdinalIgnoreCase))
        {
            return Origin.Game;
        }

        if (name.StartsWith("UnityEngine", StringComparison.OrdinalIgnoreCase))
        {
            return Origin.Unity;
        }

        if (BclAssemblyNames.Contains(name, StringComparer.OrdinalIgnoreCase))
        {
            return Origin.Bcl;
        }

        if (string.Equals(name, "Newtonsoft.Json", StringComparison.OrdinalIgnoreCase))
        {
            return Origin.Newtonsoft;
        }

        if (string.Equals(name, "0Harmony", StringComparison.OrdinalIgnoreCase))
        {
            return Origin.Harmony;
        }

        return Origin.Other;
    }

    public bool IsGameModule(ModuleDefinition m) => GameModules.Any(g => ReferenceEquals(g, m));
}

internal sealed class GateConfigException : Exception
{
    public GateConfigException(string message) : base(message)
    {
    }
}
