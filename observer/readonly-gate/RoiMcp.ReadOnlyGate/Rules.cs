namespace RoiMcp.ReadOnlyGate;

/// <summary>Rule ids and the fixed, code-level rule tables (PRD 10.2 / 10.3).</summary>
internal static class Rules
{
    public const string Hash = "G0-HASH";
    public const string FieldWrite = "G1";
    public const string MutatorName = "G2";
    public const string Denylist = "G3";
    public const string Reflection = "G4";
    public const string UnityMutator = "G5";
    public const string Forbidden = "G6";
    public const string TypeRules = "G7";
    public const string ProcessNet = "G8";
    public const string Layering = "G9";
    public const string DebugFaults = "G10";
    public const string Allow = "G-ALLOW";
    public const string Bcl = "G-BCL";
    public const string Asm = "G-ASM";
    public const string Trans = "G-TRANS";
    public const string TransColl = "G-TRANS-COLL";

    public const string ObserverRoot = "RoiMcp.Observer";
    public const string ReadLayerNs = "RoiMcp.Observer.ReadLayer";
    public const string IoNs = "RoiMcp.Observer.Io";
    public const string PublishNs = "RoiMcp.Observer.Publish";
    public const string ReflectionTableType = "RoiMcp.Observer.ReadLayer.ReflectionTable";
    public const string DebugFaultsType = "RoiMcp.Observer.DebugFaults";
    public const string ModBaseType = "ProjectAutomata.Mod";

    public static readonly string[] AllowedAssemblyReferences =
    {
        "mscorlib", "System", "System.Core", "UnityEngine", "UnityEngine.CoreModule",
        "Assembly-CSharp", "Assembly-CSharp-firstpass", "Newtonsoft.Json",
    };

    /// <summary>G2 name prefixes (literal, case-sensitive) and exact names.</summary>
    public static readonly string[] MutatorPrefixes =
    {
        "set_", "add_", "remove_", "Set", "Add", "Remove", "Clear", "Toggle", "Increment", "Decrement",
        "Unlock", "Research", "Purchase", "Sell", "Repay", "Validate", "Cancel", "Destroy", "Execute",
        "Pay", "Register", "Deregister", "Enqueue",
    };

    public static readonly string[] MutatorExactNames = { "Kill" };

    /// <summary>The only Unity event subscriptions allowed (PRD 9.5).</summary>
    public static readonly string[] SceneManagerEventAdds = { "add_sceneLoaded", "add_sceneUnloaded", "add_activeSceneChanged" };

    public const string SceneManagerType = "UnityEngine.SceneManagement.SceneManager";

    /// <summary>The Mod entry class' base chain (types whose members it may reference, subject to the Unity/game allowlists).</summary>
    public static readonly HashSet<string> ModBaseChain = new(StringComparer.Ordinal)
    {
        "ProjectAutomata.Mod", "UnityEngine.MonoBehaviour", "UnityEngine.Behaviour", "UnityEngine.Component", "UnityEngine.Object",
    };

    /// <summary>Additional Unity types the Mod entry class may use for its SceneManager handlers.</summary>
    public static readonly HashSet<string> ModSceneTypes = new(StringComparer.Ordinal)
    {
        "UnityEngine.SceneManagement.Scene", "UnityEngine.SceneManagement.LoadSceneMode", SceneManagerType,
        "UnityEngine.Events.UnityAction`1", "UnityEngine.Events.UnityAction`2",
    };

    public static bool IsMutatorName(string name)
    {
        if (MutatorExactNames.Contains(name, StringComparer.Ordinal))
        {
            return true;
        }

        foreach (var p in MutatorPrefixes)
        {
            if (name.StartsWith(p, StringComparison.Ordinal))
            {
                return true;
            }
        }

        return false;
    }

    /// <summary>G5: Unity mutators. Returns true if (open declaring type, member name) is forbidden.</summary>
    public static bool IsUnityMutator(string openType, string name)
    {
        switch (openType)
        {
            case "UnityEngine.Object":
                return name.StartsWith("Instantiate", StringComparison.Ordinal) || name is "Destroy" or "DestroyImmediate" or "DontDestroyOnLoad" or "DestroyObject";
            case "UnityEngine.GameObject":
                return name.StartsWith("AddComponent", StringComparison.Ordinal) || name == "SetActive" || name == "SetActiveRecursively"
                       || name.StartsWith("SendMessage", StringComparison.Ordinal) || name == "BroadcastMessage";
            case "UnityEngine.Component":
                return name.StartsWith("SendMessage", StringComparison.Ordinal) || name == "BroadcastMessage";
            case "UnityEngine.Time":
                return name.StartsWith("set_", StringComparison.Ordinal);
            case "UnityEngine.PlayerPrefs":
            case "UnityEngine.Input":
                return true;
            case SceneManagerType:
                return name.StartsWith("Load", StringComparison.Ordinal) || name.StartsWith("Unload", StringComparison.Ordinal);
            case "UnityEngine.Application":
                return name is "Quit" or "OpenURL";
            default:
                return false;
        }
    }

    public static bool InNamespace(string ns, string root) =>
        ns == root || ns.StartsWith(root + ".", StringComparison.Ordinal);

    public static bool IsUiNamespace(string ns) =>
        ns == "UI" || ns.StartsWith("UI.", StringComparison.Ordinal) || ns.EndsWith(".UI", StringComparison.Ordinal)
        || ns.Contains(".UI.", StringComparison.Ordinal);

    public static readonly HashSet<string> ForbiddenGameTypeNames = new(StringComparer.Ordinal)
    {
        "PAConsole", "SavegameManager", "SavegameStorage", "QuicksaveManager", "AutosaveManager",
    };

    /// <summary>G8 namespaces (type or member usage).</summary>
    public static bool IsForbiddenIpcNamespace(string ns) =>
        InNamespace(ns, "System.Net") || InNamespace(ns, "System.IO.Pipes") || InNamespace(ns, "System.IO.MemoryMappedFiles");

    /// <summary>G9: file-system API types usable only in RoiMcp.Observer.Io.</summary>
    public static readonly HashSet<string> FileApiTypes = new(StringComparer.Ordinal)
    {
        "System.IO.File", "System.IO.FileInfo", "System.IO.FileStream", "System.IO.Directory", "System.IO.DirectoryInfo",
        "System.IO.FileSystemInfo", "System.IO.FileSystemWatcher", "System.IO.DriveInfo",
    };

    /// <summary>G9: stream types whose constructors open files when given a path.</summary>
    public static readonly HashSet<string> PathStreamTypes = new(StringComparer.Ordinal)
    {
        "System.IO.StreamWriter", "System.IO.StreamReader",
    };
}
