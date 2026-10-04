using Xunit;

namespace RoiMcp.ReadOnlyGate.Tests;

/// <summary>PRD 10.5 / T-3: negative fixtures (each must fail with its rule id) and a positive fixture (must pass).</summary>
public sealed class FixtureTests
{
    private const string Usings = "using System; using System.IO; using System.Reflection; using System.Text;\n";

    private static GateResult Run(string source, IEnumerable<AllowEntry>? extraGame = null, bool includeBaseMod = true,
        IEnumerable<string>? extraRefs = null, bool allowDebugFaults = false, bool suggest = false, bool allowUnsafe = false)
    {
        using var f = Fixture.Compile(Usings + source, includeBaseMod, extraRefs, allowUnsafe);
        return f.RunGate(extraGame, allowDebugFaults, suggest);
    }

    private static string InReadLayer(string body) => @"
namespace RoiMcp.Observer.ReadLayer
{
    public static class Probe
    {
        public static object Run()
        {
            " + body + @"
        }
    }
}";

    // ------------------------------------------------------------------ positive

    internal const string PositiveSource = @"
using UnityEngine.SceneManagement;

namespace RoiMcp.Observer
{
    public sealed class ObserverMod : ProjectAutomata.Mod
    {
        public override void OnModWasLoaded()
        {
            try
            {
                SceneManager.sceneLoaded += OnSceneLoaded;
                SceneManager.sceneUnloaded += OnSceneUnloaded;
                SceneManager.activeSceneChanged += OnActiveSceneChanged;
                SceneManager.sceneLoaded += (s, m) => Lifecycle.OnScene(s.name);
            }
            catch (Exception)
            {
            }
        }

        public override void OnAllModsLoaded()
        {
            try { Lifecycle.Start(); } catch (Exception) { }
        }

        private void OnSceneLoaded(Scene scene, LoadSceneMode mode) { try { Lifecycle.OnScene(scene.name); } catch (Exception) { } }

        private void OnSceneUnloaded(Scene scene) { try { Lifecycle.OnScene(scene.name); } catch (Exception) { } }

        private void OnActiveSceneChanged(Scene from, Scene to) { try { Lifecycle.OnScene(to.name); } catch (Exception) { } }

        private void LateUpdate() { try { Lifecycle.Tick(); } catch (Exception) { } }
    }

    internal static class Lifecycle
    {
        private static string _scene;
        private static System.Diagnostics.Stopwatch _watch;
        private static int _pid;

        public static void Start()
        {
            _watch = System.Diagnostics.Stopwatch.StartNew();
            _pid = System.Diagnostics.Process.GetCurrentProcess().Id;
        }

        public static void OnScene(string name) { _scene = name; }

        public static void Tick()
        {
            var facts = ReadLayer.Reader.Read();
            var text = Publish.Publisher.Format(facts.Seed, facts.FieldsResolved, _scene, _pid);
            Io.FileSink.Write(Path.Combine(Path.GetTempPath(), ""roimcp-positive.json""), text);
        }
    }
}

namespace RoiMcp.Observer.ReadLayer
{
    public struct Facts
    {
        public int Seed;
        public bool FieldsResolved;
        public int Frame;
    }

    public static class Reader
    {
        public static Facts Read()
        {
            var f = new Facts();
            f.Seed = ProjectAutomata.World.seed;
            f.Frame = UnityEngine.Time.frameCount;
            ReflectionTable.Resolve();
            f.FieldsResolved = ReflectionTable.PermitsField != null && ReflectionTable.OriginField != null;
            return f;
        }

        public static object Permits(ProjectAutomata.PermitManager pm)
        {
            if (pm == null)
            {
                return null;
            }

            return ReflectionTable.GetPermits(pm);
        }
    }

    internal static class ReflectionTable
    {
        public static FieldInfo PermitsField;
        public static FieldInfo OriginField;

        public static void Resolve()
        {
            PermitsField = typeof(ProjectAutomata.PermitManager).GetField(""_permits"", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
            OriginField = typeof(ProjectAutomata.TransportJob<ProjectAutomata.LandTransportJob>).GetField(""_origin"", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        }

        public static object GetPermits(object manager)
        {
            return PermitsField.GetValue(manager);
        }
    }
}

namespace RoiMcp.Observer.Publish
{
    public static class Publisher
    {
        public static string Format(int seed, bool resolved, string scene, int pid)
        {
            var sb = new StringBuilder();
            sb.Append(""{\""seed\"":"").Append(seed).Append("",\""scene\"":\"""").Append(scene).Append(""\""}"");
            return sb.ToString();
        }
    }
}

namespace RoiMcp.Observer.Io
{
    public static class FileSink
    {
        public static void Write(string path, string text)
        {
            var tmp = path + "".tmp"";
            File.WriteAllText(tmp, text);
            if (File.Exists(path)) { File.Delete(path); }
            File.Move(tmp, path);
        }
    }
}
";

    internal static AllowEntry[] PositiveEntries => new[]
    {
        Entries.Game("ProjectAutomata.World::get_seed()", "getter"),
        Entries.Game("ProjectAutomata.PermitManager::_permits", "field_read", isPrivate: true),
        Entries.Game("ProjectAutomata.TransportJob`1::_origin", "field_read", isPrivate: true),
    };

    [GameFact]
    public void Positive_fixture_passes()
    {
        var r = Run(PositiveSource, PositiveEntries, includeBaseMod: false);
        GateAssert.Passes(r);
        Assert.Equal("RoiMcp.Observer.ObserverMod", r.ModClass);
        Assert.Equal(2, r.Stats.ReflectionLookups);
    }

    // ------------------------------------------------------------------ G2

    [GameFact]
    public void G2_setter_call_on_game_type()
    {
        var r = Run(InReadLayer("ProjectAutomata.Recipe recipe = null; recipe.tier = 3; return null;"));
        GateAssert.FailsWith(r, Rules.MutatorName);
    }

    [GameFact]
    public void G2_subscription_to_game_event()
    {
        var r = Run(InReadLayer(@"ProjectAutomata.TimeManager tm = null;
            tm.onDayStart += OnDay; return null;
        }
        private static void OnDay(ProjectAutomata.GameDate d) {"));
        // The delegate construction (game delegate type .ctor) is additionally unlisted.
        GateAssert.FailsWith(r, Rules.MutatorName, Rules.Allow);
    }

    // ------------------------------------------------------------------ G3

    [GameFact]
    public void G3_Utils_GetSafe()
    {
        var r = Run(InReadLayer(@"var d = new System.Collections.Generic.Dictionary<int, System.Collections.Generic.List<int>>();
            return ProjectAutomata.Utils.GetSafe(d, 1, new System.Collections.Generic.List<int>());"));
        GateAssert.FailsWith(r, Rules.Denylist);
    }

    [GameFact]
    public void G3_MoneyManager_GetBalance()
    {
        var r = Run(InReadLayer("ProjectAutomata.MoneyManager mm = null; return mm.GetBalance(null);"));
        GateAssert.FailsWith(r, Rules.Denylist);
    }

    [GameFact]
    public void G3_interface_call_that_dispatches_to_a_denylisted_implementation()
    {
        var r = Run(InReadLayer("ProjectAutomata.IProductStorage s = null; return s.GetMaxAccepted(null);"),
            new[] { Entries.Game("ProjectAutomata.IProductStorage::GetMaxAccepted(ProjectAutomata.ProductDefinition)", "method") });
        GateAssert.FailsWith(r, Rules.Denylist);
    }

    [GameFact]
    public void G3_denylisted_allowlist_entry()
    {
        var r = Run(InReadLayer("return null;"),
            new[] { Entries.Game("ProjectAutomata.MoneyManager::GetBalance(ProjectAutomata.IMoneyAgent)", "method") });
        // The transitive scan of the (denylisted) entry also reports its own findings.
        GateAssert.FailsWith(r, Rules.Denylist, Rules.Trans, Rules.TransColl);
        Assert.Contains(r.Violations, v => v.Rule == Rules.Denylist && v.Location.StartsWith("allowlist game[", StringComparison.Ordinal));
    }

    // ------------------------------------------------------------------ G4

    [GameFact]
    public void G4_FieldInfo_SetValue()
    {
        var r = Run(@"
namespace RoiMcp.Observer.ReadLayer
{
    internal static class ReflectionTable
    {
        public static void Poke(FieldInfo f, object target) { f.SetValue(target, null); }
    }
}");
        GateAssert.FailsWith(r, Rules.Reflection);
    }

    [GameFact]
    public void G4_GetField_with_non_literal_name()
    {
        var r = Run(@"
namespace RoiMcp.Observer.ReadLayer
{
    internal static class ReflectionTable
    {
        public static string Name = ""_permits"";
        public static FieldInfo F;
        public static void Resolve()
        {
            F = typeof(ProjectAutomata.PermitManager).GetField(Name, BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        }
    }
}", new[] { Entries.Game("ProjectAutomata.PermitManager::_permits", "field_read", isPrivate: true) });
        GateAssert.FailsWith(r, Rules.Reflection);
    }

    [GameFact]
    public void G4_GetField_literal_not_allowlisted()
    {
        var r = Run(@"
namespace RoiMcp.Observer.ReadLayer
{
    internal static class ReflectionTable
    {
        public static FieldInfo F;
        public static void Resolve()
        {
            F = typeof(ProjectAutomata.GlobalMarket).GetField(""_pricingInfoByProduct"", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        }
    }
}");
        GateAssert.FailsWith(r, Rules.Reflection);
    }

    [GameFact]
    public void G4_GetField_outside_ReflectionTable()
    {
        var r = Run(InReadLayer(
            @"return typeof(ProjectAutomata.PermitManager).GetField(""_permits"", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);"),
            new[] { Entries.Game("ProjectAutomata.PermitManager::_permits", "field_read", isPrivate: true) });
        GateAssert.FailsWith(r, Rules.Reflection);
    }

    [GameFact]
    public void G4_MethodBase_Invoke()
    {
        var r = Run(InReadLayer("MethodInfo m = null; return m.Invoke(null, null);"));
        GateAssert.FailsWith(r, Rules.Reflection);
    }

    // ------------------------------------------------------------------ G5

    [GameFact]
    public void G5_Object_Destroy()
    {
        var r = Run(InReadLayer("UnityEngine.Object.Destroy(null); return null;"));
        GateAssert.FailsWith(r, Rules.UnityMutator);
    }

    [GameFact]
    public void G5_PlayerPrefs()
    {
        var r = Run(InReadLayer("return UnityEngine.PlayerPrefs.GetInt(\"x\");"));
        GateAssert.FailsWith(r, Rules.UnityMutator);
    }

    // ------------------------------------------------------------------ G6 / G-ASM

    [GameFact]
    public void G6_Harmony_reference()
    {
        var r = Run(InReadLayer("return typeof(Harmony.HarmonyInstance);"), extraRefs: new[] { "0Harmony" });
        GateAssert.FailsWith(r, Rules.Forbidden, Rules.Asm);
        Assert.Contains(Rules.Asm, r.RuleIds);
    }

    [GameFact]
    public void G6_savegame_type_reference()
    {
        var r = Run(InReadLayer("return typeof(ProjectAutomata.SavegameManager);"));
        GateAssert.FailsWith(r, Rules.Forbidden, Rules.Denylist);
    }

    // ------------------------------------------------------------------ G7

    [GameFact]
    public void G7_implements_game_interface()
    {
        var r = Run(@"
namespace RoiMcp.Observer.ReadLayer
{
    public sealed class Listener : ProjectAutomata.IWorldReadyListener
    {
        public void OnWorldBecameReady(bool loaded) { }
    }
}");
        GateAssert.FailsWith(r, Rules.TypeRules);
    }

    [GameFact]
    public void G7_two_Mod_subclasses()
    {
        var r = Run(@"
namespace RoiMcp.Observer
{
    public sealed class SecondMod : ProjectAutomata.Mod { }
}");
        GateAssert.FailsWith(r, Rules.TypeRules);
    }

    [GameFact]
    public void G7_abstract_Mod_subclass()
    {
        var r = Run(@"
namespace RoiMcp.Observer
{
    public abstract class AbstractMod : ProjectAutomata.Mod { }
    public sealed class ObserverMod : AbstractMod { }
}", includeBaseMod: false);
        GateAssert.FailsWith(r, Rules.TypeRules);
        Assert.Contains(r.Violations, v => v.Message.Contains("abstract", StringComparison.Ordinal));
    }

    [GameFact]
    public void G7_non_sealed_Mod_subclass()
    {
        var r = Run(@"
namespace RoiMcp.Observer
{
    public class ObserverMod : ProjectAutomata.Mod { }
}", includeBaseMod: false);
        GateAssert.FailsWith(r, Rules.TypeRules);
    }

    [GameFact]
    public void G7_DllImport()
    {
        var r = Run(@"
namespace RoiMcp.Observer.Io
{
    public static class Native
    {
        [System.Runtime.InteropServices.DllImport(""kernel32.dll"")]
        public static extern uint GetTickCount();
    }
}");
        GateAssert.FailsWith(r, Rules.TypeRules);
    }

    [GameFact]
    public void G7_unsafe_code()
    {
        var r = Run(@"
namespace RoiMcp.Observer.Io
{
    public static unsafe class Raw
    {
        public static int Read(int* p) { return *p; }
    }
}", allowUnsafe: true);
        GateAssert.FailsWith(r, Rules.TypeRules);
    }

    // ------------------------------------------------------------------ G1

    [GameFact]
    public void G1_stfld_on_game_field()
    {
        var r = Run(InReadLayer("ProjectAutomata.TechTreeManager m = null; m.isEnabled = false; return null;"));
        GateAssert.FailsWith(r, Rules.FieldWrite);
    }

    // ------------------------------------------------------------------ G8

    [GameFact]
    public void G8_Process_Start()
    {
        var r = Run(@"
namespace RoiMcp.Observer.Io
{
    public static class Launcher
    {
        public static void Run() { System.Diagnostics.Process.Start(""notepad.exe""); }
    }
}");
        GateAssert.FailsWith(r, Rules.ProcessNet);
    }

    [GameFact]
    public void G8_System_Net_reference()
    {
        var r = Run(@"
namespace RoiMcp.Observer.Io
{
    public static class Net
    {
        public static object Make() { return new System.Net.WebClient(); }
    }
}");
        GateAssert.FailsWith(r, Rules.ProcessNet);
    }

    // ------------------------------------------------------------------ G9

    [GameFact]
    public void G9_game_reference_outside_ReadLayer()
    {
        var r = Run(@"
namespace RoiMcp.Observer.Capture
{
    public static class Capturer
    {
        public static int Seed() { return ProjectAutomata.World.seed; }
    }
}", new[] { Entries.Game("ProjectAutomata.World::get_seed()", "getter") });
        GateAssert.FailsWith(r, Rules.Layering);
    }

    [GameFact]
    public void G9_file_api_outside_Io()
    {
        var r = Run(@"
namespace RoiMcp.Observer.Capture
{
    public static class Capturer
    {
        public static bool Probe() { return File.Exists(""x""); }
    }
}");
        GateAssert.FailsWith(r, Rules.Layering);
    }

    [GameFact]
    public void G9_Publish_references_Unity()
    {
        var r = Run(@"
namespace RoiMcp.Observer.Publish
{
    public static class Publisher
    {
        public static int Frame() { return UnityEngine.Time.frameCount; }
    }
}");
        GateAssert.FailsWith(r, Rules.Layering);
        Assert.Contains(r.Violations, v => v.Message.Contains("Publish", StringComparison.Ordinal));
    }

    // ------------------------------------------------------------------ G10

    private const string DebugFaultsSource = @"
namespace RoiMcp.Observer
{
    internal static class DebugFaults
    {
        public static bool ThrowInCapture;
    }
}";

    [GameFact]
    public void G10_DebugFaults_marker_type()
    {
        GateAssert.FailsWith(Run(DebugFaultsSource), Rules.DebugFaults);
    }

    [GameFact]
    public void G10_DebugFaults_allowed_only_with_explicit_flag()
    {
        GateAssert.Passes(Run(DebugFaultsSource, allowDebugFaults: true));
    }

    // ------------------------------------------------------------------ default deny

    [GameFact]
    public void Unlisted_game_getter_is_denied()
    {
        var r = Run(InReadLayer("return ProjectAutomata.World.seed;"));
        GateAssert.FailsWith(r, Rules.Allow);
    }

    [GameFact]
    public void Unlisted_unity_member_is_denied()
    {
        var r = Run(InReadLayer("return UnityEngine.Time.deltaTime;"));
        GateAssert.FailsWith(r, Rules.Allow);
    }

    [GameFact]
    public void Suggest_emits_skeleton_for_unlisted_member()
    {
        var r = Run(InReadLayer("return ProjectAutomata.World.seed;"), suggest: true);
        var s = Assert.Single(r.Suggestions);
        Assert.Equal("game", s.Section);
        Assert.Equal("ProjectAutomata.World::get_seed()", s.Entry.Member);
        Assert.Equal("getter", s.Entry.Kind);
        Assert.NotNull(s.Entry.TransitiveFindingsAck);
    }

    // ------------------------------------------------------------------ transitive (PRD 10.4)

    private const string TransitiveMember = "ProjectAutomata.WorldStartupParameters::Retrieve()";

    private static readonly string TransitiveSource = InReadLayer("return ProjectAutomata.WorldStartupParameters.Retrieve();");

    [GameFact]
    public void Transitive_static_write_fails_without_ack()
    {
        var r = Run(TransitiveSource, new[] { Entries.Game(TransitiveMember, "method") });
        GateAssert.FailsWith(r, Rules.Trans);
        Assert.Contains(r.Violations, v => v.Message.Contains("stsfld ProjectAutomata.WorldStartupParameters::_stagedInstance", StringComparison.Ordinal));
    }

    [GameFact]
    public void Transitive_static_write_passes_with_ack()
    {
        var ack = new AckEntry
        {
            Finding = TransitiveMember + " -> " + TransitiveMember + " : stsfld ProjectAutomata.WorldStartupParameters::_stagedInstance",
            Justification = "test: consumes the staged startup parameters (fixture only, never allowlisted for real)",
        };
        var r = Run(TransitiveSource, new[] { Entries.Game(TransitiveMember, "method", null, ack) });
        GateAssert.Passes(r);
    }

    [GameFact]
    public void Transitive_ack_accepts_glob_pattern()
    {
        var ack = new AckEntry { Finding = "* : stsfld ProjectAutomata.WorldStartupParameters::*", Justification = "test glob" };
        var r = Run(TransitiveSource, new[] { Entries.Game(TransitiveMember, "method", null, ack) });
        GateAssert.Passes(r);
    }

    [GameFact]
    public void Transitive_collection_write_cannot_be_acknowledged()
    {
        // Actor.Get(Type) lazily inserts into the actor's own component dictionary: a write into a game
        // collection. A catch-all acknowledgement must not cover it.
        var ack = new AckEntry { Finding = "*", Justification = "attempt to acknowledge everything" };
        var r = Run(InReadLayer("return null;"), new[]
        {
            Entries.Game("ProjectAutomata.Actor::Get(System.Type)", "method", null, ack),
        });
        GateAssert.FailsWith(r, Rules.TransColl);
        Assert.Contains(r.Violations, v => v.Rule == Rules.TransColl && v.Message.Contains("acknowledgement ignored", StringComparison.Ordinal));
    }

    [GameFact]
    public void Transitive_collection_write_passes_with_matching_category()
    {
        // Actor.Get(Type) matches the shipped guarded_actor_component_cache category (container pattern).
        var ack = new AckEntry { Finding = "* : collection-write *", Justification = "test: guarded cache", Category = "guarded_actor_component_cache" };
        var r = Run(InReadLayer("return null;"), new[]
        {
            Entries.Game("ProjectAutomata.Actor::Get(System.Type)", "method", null, ack),
        });
        Assert.DoesNotContain(r.Violations, v => v.Rule == Rules.TransColl);
    }

    [GameFact]
    public void Transitive_collection_write_fails_with_non_matching_category()
    {
        // scratch_pool patterns do not match Actor.Get: the categorized acknowledgement is ignored.
        var ack = new AckEntry { Finding = "* : collection-write *", Justification = "test: wrong category", Category = "scratch_pool" };
        var r = Run(InReadLayer("return null;"), new[]
        {
            Entries.Game("ProjectAutomata.Actor::Get(System.Type)", "method", null, ack),
        });
        GateAssert.FailsWith(r, Rules.TransColl);
    }

    [GameFact]
    public void Transitive_collection_write_fails_with_unknown_category()
    {
        var ack = new AckEntry { Finding = "* : collection-write *", Justification = "test: undefined category", Category = "anything_goes" };
        var r = Run(InReadLayer("return null;"), new[]
        {
            Entries.Game("ProjectAutomata.Actor::Get(System.Type)", "method", null, ack),
        });
        GateAssert.FailsWith(r, Rules.TransColl);
    }

    [GameFact]
    public void Transitive_write_into_caller_provided_collection_is_acknowledgeable()
    {
        // GetSafe inserts into the dictionary passed by the caller: as a root, that is the observer's own
        // buffer, so the finding is acknowledgeable (GetSafe is still denylisted: G3).
        var ack = new AckEntry { Finding = "* : caller-collection-write *", Justification = "observer passes its own dictionary" };
        var r = Run(InReadLayer("return null;"), new[]
        {
            Entries.Game("ProjectAutomata.Utils::GetSafe(System.Collections.Generic.IDictionary`2<T1,T2>,T1,System.Func`1<T2>)", "method", null, ack),
        });
        GateAssert.FailsWith(r, Rules.Denylist);
    }

    [GameFact]
    public void Stale_allowlist_entry_fails()
    {
        var r = Run(InReadLayer("return null;"), new[] { Entries.Game("ProjectAutomata.World::get_doesNotExist()", "getter") });
        GateAssert.FailsWith(r, Rules.Allow);
    }
}
