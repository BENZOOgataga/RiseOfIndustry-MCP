using Mono.Cecil;
using Mono.Cecil.Cil;

namespace RoiMcp.ReadOnlyGate;

internal sealed record Violation(string Rule, string Location, string Message)
{
    public override string ToString() => $"{Rule}  {Location}  {Message}";
}

internal sealed class Suggestion
{
    public required string Section { get; init; }

    public required AllowEntry Entry { get; init; }

    public SortedSet<string> ReferencedFrom { get; } = new(StringComparer.Ordinal);

    public MethodDefinition? GameMethod { get; init; }
}

/// <summary>Checks the observer assembly itself (PRD 10.1 - 10.3, G1 - G10, G-ALLOW, G-BCL, G-ASM).</summary>
internal sealed class ObserverAnalyzer
{
    private readonly GameEnvironment _env;
    private readonly GameIndex _index;
    private readonly Allowlist _allow;
    private readonly Denylist _deny;
    private readonly bool _allowDebugFaults;
    private readonly IlStack _stack = new();
    private readonly HashSet<Violation> _seen = new();
    private TypeDefinition? _modClass;
    private HashSet<TypeDefinition> _modClasses = new();

    public ObserverAnalyzer(GameEnvironment env, GameIndex index, Allowlist allow, Denylist deny, bool allowDebugFaults)
    {
        _env = env;
        _index = index;
        _allow = allow;
        _deny = deny;
        _allowDebugFaults = allowDebugFaults;
    }

    public List<Violation> Violations { get; } = new();

    public Dictionary<string, Suggestion> Suggestions { get; } = new(StringComparer.Ordinal);

    public HashSet<AllowEntry> UsedEntries { get; } = new();

    public GateStats Stats { get; } = new();

    public string? ModClassName => _modClass == null ? null : Names.Type(_modClass);

    private sealed class Ctx
    {
        public required TypeDefinition Type { get; init; }

        public required TypeDefinition Outer { get; init; }

        public MethodDefinition? Method { get; init; }

        public Instruction? Ins { get; set; }

        public string? Member { get; init; }

        public bool ReadLayer { get; init; }

        public bool Io { get; init; }

        public bool Publish { get; init; }

        public bool ModScope { get; init; }

        public bool ReflectionTable { get; init; }

        public string Location
        {
            get
            {
                if (Method != null)
                {
                    return Names.Location(Method) + (Ins != null ? $" IL_{Ins.Offset:x4}" : string.Empty);
                }

                return Member != null ? Names.Type(Type) + "::" + Member : Names.Type(Type);
            }
        }
    }

    private void Report(string rule, string location, string message)
    {
        var v = new Violation(rule, location, message);
        if (_seen.Add(v))
        {
            Violations.Add(v);
        }
    }

    private void Report(Ctx ctx, string rule, string message) => Report(rule, ctx.Location, message);

    public void Analyze(ModuleDefinition module)
    {
        CheckAssemblyLevel(module);
        var types = GameIndex.AllTypes(module.Types).Where(t => t.Name != "<Module>").ToList();
        CheckModClasses(types);

        foreach (var t in types)
        {
            Stats.Types++;
            CheckType(t);
        }
    }

    private void CheckAssemblyLevel(ModuleDefinition module)
    {
        foreach (var r in module.AssemblyReferences)
        {
            if (!Rules.AllowedAssemblyReferences.Contains(r.Name, StringComparer.Ordinal))
            {
                Report(Rules.Asm, "assembly", $"assembly reference '{r.Name}' is not allowed (allowed: {string.Join(", ", Rules.AllowedAssemblyReferences)})");
            }

            if (string.Equals(r.Name, "0Harmony", StringComparison.OrdinalIgnoreCase))
            {
                Report(Rules.Forbidden, "assembly", "reference to 0Harmony");
            }

            if (Rules.IsForbiddenIpcNamespace(r.Name))
            {
                Report(Rules.ProcessNet, "assembly", $"reference to network/IPC assembly '{r.Name}'");
            }
        }

        foreach (var mr in module.ModuleReferences)
        {
            Report(Rules.TypeRules, "assembly", $"native module reference '{mr.Name}' (P/Invoke)");
        }

        CheckAttributes(module.Assembly.CustomAttributes, "assembly");
        CheckAttributes(module.CustomAttributes, "module");
    }

    private void CheckAttributes(IEnumerable<CustomAttribute> attrs, string location)
    {
        foreach (var a in attrs)
        {
            var at = a.AttributeType;
            var name = Names.OpenType(at);
            if (name == "System.Security.UnverifiableCodeAttribute")
            {
                Report(Rules.TypeRules, location, "unsafe code (UnverifiableCodeAttribute)");
                continue;
            }

            if (name == "System.Security.Permissions.SecurityPermissionAttribute"
                && a.Properties.Any(p => p.Name == "SkipVerification" && p.Argument.Value is true))
            {
                Report(Rules.TypeRules, location, "unsafe code (SecurityPermission SkipVerification)");
                continue;
            }

            var origin = _env.OriginOf(at);
            if (origin == Origin.Game)
            {
                Report(Rules.TypeRules, location, $"game attribute applied: {name}");
            }
            else if (origin == Origin.Unity)
            {
                Report(Rules.TypeRules, location, $"Unity attribute applied: {name}");
            }
            else if (origin == Origin.Harmony)
            {
                Report(Rules.Forbidden, location, $"Harmony attribute applied: {name}");
            }
        }
    }

    private bool IsModSubclass(TypeDefinition t)
    {
        var bt = t.BaseType;
        var guard = 0;
        while (bt != null && guard++ < 64)
        {
            if (Names.OpenType(bt) == Rules.ModBaseType && _env.OriginOf(bt) == Origin.Game)
            {
                return true;
            }

            var d = Resolver.Type(bt);
            bt = d?.BaseType;
        }

        return false;
    }

    private void CheckModClasses(List<TypeDefinition> types)
    {
        var mods = types.Where(IsModSubclass).ToList();
        _modClasses = mods.ToHashSet();
        if (mods.Count == 0)
        {
            Report(Rules.TypeRules, "assembly", "no ProjectAutomata.Mod subclass found (exactly one non-abstract sealed subclass required)");
            return;
        }

        if (mods.Count > 1)
        {
            Report(Rules.TypeRules, "assembly", $"more than one ProjectAutomata.Mod subclass: {string.Join(", ", mods.Select(Names.Type))}");
        }

        foreach (var m in mods)
        {
            if (m.IsAbstract)
            {
                Report(Rules.TypeRules, Names.Type(m), "abstract ProjectAutomata.Mod subclass");
            }
            else if (!m.IsSealed)
            {
                Report(Rules.TypeRules, Names.Type(m), "ProjectAutomata.Mod subclass must be sealed");
            }

            if (m.BaseType == null || Names.OpenType(m.BaseType) != Rules.ModBaseType)
            {
                Report(Rules.TypeRules, Names.Type(m), "ProjectAutomata.Mod subclass must derive directly from ProjectAutomata.Mod");
            }
        }

        _modClass = mods.Count == 1 ? mods[0] : null;
    }

    private static TypeDefinition OuterOf(TypeDefinition t)
    {
        while (t.DeclaringType != null)
        {
            t = t.DeclaringType;
        }

        return t;
    }

    private static bool IsCompilerGenerated(TypeDefinition t) =>
        t.Name.StartsWith('<') || t.CustomAttributes.Any(a => a.AttributeType.FullName == "System.Runtime.CompilerServices.CompilerGeneratedAttribute");

    private Ctx MakeCtx(TypeDefinition t, MethodDefinition? m = null, string? member = null)
    {
        var outer = OuterOf(t);
        var ns = outer.Namespace;
        var modScope = false;
        if (_modClasses.Contains(outer))
        {
            modScope = true;
            for (var cur = t; !ReferenceEquals(cur, outer); cur = cur.DeclaringType)
            {
                if (!IsCompilerGenerated(cur))
                {
                    modScope = false;
                    break;
                }
            }
        }

        return new Ctx
        {
            Type = t,
            Outer = outer,
            Method = m,
            Member = member,
            ReadLayer = Rules.InNamespace(ns, Rules.ReadLayerNs),
            Io = Rules.InNamespace(ns, Rules.IoNs),
            Publish = Rules.InNamespace(ns, Rules.PublishNs),
            ModScope = modScope,
            ReflectionTable = Names.Type(outer) == Rules.ReflectionTableType,
        };
    }

    private void CheckType(TypeDefinition t)
    {
        var ctx = MakeCtx(t);
        if (Names.Type(t) == Rules.DebugFaultsType && !_allowDebugFaults)
        {
            Report(ctx, Rules.DebugFaults, "release build contains the RoiMcp.Observer.DebugFaults marker type");
        }

        if (t.BaseType != null)
        {
            var bo = _env.OriginOf(t.BaseType);
            var isMod = ReferenceEquals(t, _modClass) || IsModSubclass(t);
            if ((bo == Origin.Game || bo == Origin.Unity) && !isMod)
            {
                Report(ctx, Rules.TypeRules, $"derives from game/Unity type {Names.Type(t.BaseType)}");
            }
            else
            {
                CheckTypeUsage(ctx, t.BaseType, "base type");
            }
        }

        foreach (var i in t.Interfaces)
        {
            var io = _env.OriginOf(i.InterfaceType);
            if (io == Origin.Game || io == Origin.Unity)
            {
                Report(ctx, Rules.TypeRules, $"implements game/Unity interface {Names.Type(i.InterfaceType)}");
            }
            else
            {
                CheckTypeUsage(ctx, i.InterfaceType, "interface");
            }

            CheckAttributes(i.CustomAttributes, ctx.Location);
        }

        CheckAttributes(t.CustomAttributes, ctx.Location);
        CheckGenericParameters(ctx, t.GenericParameters);

        foreach (var f in t.Fields)
        {
            var fctx = MakeCtx(t, member: f.Name);
            CheckTypeUsage(fctx, f.FieldType, "field type");
            CheckAttributes(f.CustomAttributes, fctx.Location);
        }

        foreach (var p in t.Properties)
        {
            CheckAttributes(p.CustomAttributes, Names.Type(t) + "::" + p.Name);
        }

        foreach (var e in t.Events)
        {
            CheckAttributes(e.CustomAttributes, Names.Type(t) + "::" + e.Name);
            CheckTypeUsage(MakeCtx(t, member: e.Name), e.EventType, "event type");
        }

        foreach (var m in t.Methods)
        {
            Stats.Methods++;
            CheckMethod(t, m);
        }
    }

    private void CheckGenericParameters(Ctx ctx, IEnumerable<GenericParameter> gps)
    {
        foreach (var gp in gps)
        {
            CheckAttributes(gp.CustomAttributes, ctx.Location);
            foreach (var c in gp.Constraints)
            {
                CheckTypeUsage(ctx, c.ConstraintType, "generic constraint");
            }
        }
    }

    private void CheckMethod(TypeDefinition t, MethodDefinition m)
    {
        var ctx = MakeCtx(t, m);
        CheckAttributes(m.CustomAttributes, ctx.Location);
        CheckAttributes(m.MethodReturnType.CustomAttributes, ctx.Location);
        CheckGenericParameters(ctx, m.GenericParameters);

        if (m.IsPInvokeImpl || m.HasPInvokeInfo)
        {
            Report(ctx, Rules.TypeRules, "P/Invoke (DllImport) declaration");
        }

        if (m.IsInternalCall)
        {
            Report(ctx, Rules.TypeRules, "InternalCall method declaration");
        }

        CheckTypeUsage(ctx, m.ReturnType, "return type");
        foreach (var p in m.Parameters)
        {
            CheckTypeUsage(ctx, p.ParameterType, "parameter type");
            CheckAttributes(p.CustomAttributes, ctx.Location);
        }

        foreach (var o in m.Overrides)
        {
            var oo = _env.OriginOf(o.DeclaringType);
            if (oo == Origin.Game || oo == Origin.Unity)
            {
                Report(ctx, Rules.TypeRules, $"explicitly implements game/Unity member {Names.Method(o)}");
            }
        }

        if (!m.HasBody)
        {
            return;
        }

        foreach (var v in m.Body.Variables)
        {
            CheckTypeUsage(ctx, v.VariableType, "local variable");
        }

        foreach (var ins in m.Body.Instructions)
        {
            Stats.Instructions++;
            ctx.Ins = ins;
            switch (ins.OpCode.Code)
            {
                case Code.Localloc:
                case Code.Cpblk:
                case Code.Initblk:
                    Report(ctx, Rules.TypeRules, $"unsafe code ({ins.OpCode.Name})");
                    break;
                case Code.Calli:
                    Report(ctx, Rules.TypeRules, "unsafe code (calli through a function pointer)");
                    break;
            }

            switch (ins.Operand)
            {
                case MethodReference mr:
                    CheckMethodRef(ctx, mr, ins);
                    break;
                case FieldReference fr:
                    CheckFieldRef(ctx, fr, ins);
                    break;
                case TypeReference tr:
                    CheckTypeUsage(ctx, tr, $"{ins.OpCode.Name} operand");
                    break;
            }
        }

        ctx.Ins = null;
    }

    // ----------------------------------------------------------------- type usage

    /// <summary>Returns true if any violation was reported.</summary>
    private bool CheckTypeUsage(Ctx ctx, TypeReference t, string what, bool skipLayering = false)
    {
        var hit = false;
        switch (t)
        {
            case PointerType p:
                Report(ctx, Rules.TypeRules, $"unsafe code (pointer type {Names.Type(p)} in {what})");
                return CheckTypeUsage(ctx, p.ElementType, what, skipLayering) | true;
            case FunctionPointerType:
                Report(ctx, Rules.TypeRules, $"unsafe code (function pointer in {what})");
                return true;
            case GenericInstanceType g:
                hit |= CheckTypeUsage(ctx, g.ElementType, what, skipLayering);
                foreach (var a in g.GenericArguments)
                {
                    hit |= CheckTypeUsage(ctx, a, what);
                }

                return hit;
            case TypeSpecification spec:
                return CheckTypeUsage(ctx, spec.ElementType, what, skipLayering);
            case GenericParameter:
                return false;
        }

        var origin = _env.OriginOf(t);
        var open = Names.OpenType(t);
        var outer = Denylist.OuterTypesOf(open);
        switch (origin)
        {
            case Origin.Observer:
            case Origin.GenericParameter:
                return false;
            case Origin.Harmony:
                Report(ctx, Rules.Forbidden, $"Harmony type {open} used in {what}");
                return true;
            case Origin.Game:
            case Origin.Unity:
            {
                if (IsForbiddenG6(t, origin, out var why))
                {
                    Report(ctx, Rules.Forbidden, $"forbidden type {open} ({why}) used in {what}");
                    hit = true;
                }

                var deny = _deny.MatchType(Names.Type(t), open, outer);
                if (deny != null)
                {
                    Report(ctx, Rules.Denylist, $"denylisted type {open} used in {what} [{deny.Member}: {deny.Reason}]");
                    hit = true;
                }

                if (!skipLayering && !ctx.ReadLayer && !(ctx.ModScope && IsModAllowedType(open)))
                {
                    ReportLayering(ctx, $"{(origin == Origin.Game ? "game" : "Unity")} type {open} used in {what}");
                    hit = true;
                }

                return hit;
            }

            case Origin.Bcl:
            {
                var ns = Names.ElementOf(t).Namespace;
                if (Names.ElementOf(t).DeclaringType != null)
                {
                    ns = OuterNamespace(Names.ElementOf(t));
                }

                if (Rules.IsForbiddenIpcNamespace(ns))
                {
                    Report(ctx, Rules.ProcessNet, $"network/IPC type {open} used in {what}");
                    hit = true;
                }

                if (Rules.InNamespace(ns, "System.Reflection.Emit"))
                {
                    Report(ctx, Rules.Reflection, $"System.Reflection.Emit type {open} used in {what}");
                    hit = true;
                }

                if (open == "System.Diagnostics.ProcessStartInfo")
                {
                    Report(ctx, Rules.ProcessNet, $"process API type {open} used in {what}");
                    hit = true;
                }

                var deny = _deny.MatchType(Names.Type(t), open, outer);
                if (deny != null)
                {
                    Report(ctx, Rules.Denylist, $"denylisted type {open} used in {what} [{deny.Member}: {deny.Reason}]");
                    hit = true;
                }

                if (Resolver.Type(t) == null)
                {
                    Report(ctx, Rules.Bcl, $"BCL type {open} does not resolve in the game's Managed assemblies");
                    hit = true;
                }

                return hit;
            }

            case Origin.Newtonsoft:
            case Origin.Other:
            {
                var deny = _deny.MatchType(Names.Type(t), open, outer);
                if (deny != null)
                {
                    Report(ctx, Rules.Denylist, $"denylisted type {open} used in {what} [{deny.Member}: {deny.Reason}]");
                    return true;
                }

                return false;
            }
        }

        return hit;
    }

    private static string OuterNamespace(TypeReference t)
    {
        while (t.DeclaringType != null)
        {
            t = t.DeclaringType;
        }

        return t.Namespace;
    }

    private void ReportLayering(Ctx ctx, string what)
    {
        var where = ctx.Publish ? "Publish types must not reference game or Unity members (G9)" : "only RoiMcp.Observer.ReadLayer may reference game/Unity members";
        Report(ctx, Rules.Layering, $"{what}: {where}");
    }

    private static bool IsModAllowedType(string open) => Rules.ModBaseChain.Contains(open) || Rules.ModSceneTypes.Contains(open);

    private static bool IsModAllowedMember(string openDecl, string name)
    {
        if (Rules.ModBaseChain.Contains(openDecl) || openDecl == "UnityEngine.SceneManagement.Scene")
        {
            return true;
        }

        if ((openDecl == "UnityEngine.Events.UnityAction`1" || openDecl == "UnityEngine.Events.UnityAction`2") && name == ".ctor")
        {
            return true;
        }

        return openDecl == Rules.SceneManagerType && Rules.SceneManagerEventAdds.Contains(name);
    }

    private bool IsForbiddenG6(TypeReference t, Origin origin, out string why)
    {
        why = string.Empty;
        var e = Names.ElementOf(t);
        var ns = OuterNamespace(e);
        if (origin == Origin.Game && (ns == "DevConsole" || ns.StartsWith("DevConsole.", StringComparison.Ordinal)))
        {
            why = "DevConsole namespace";
            return true;
        }

        if (Rules.IsUiNamespace(ns))
        {
            why = "UI namespace";
            return true;
        }

        for (var cur = e; cur != null; cur = cur.DeclaringType)
        {
            var name = cur.Name;
            var tick = name.IndexOf('`');
            if (tick > 0)
            {
                name = name.Substring(0, tick);
            }

            if (origin == Origin.Game && Rules.ForbiddenGameTypeNames.Contains(name))
            {
                why = "save/console type";
                return true;
            }

            if (name.EndsWith("ViewModel", StringComparison.Ordinal))
            {
                why = "view model";
                return true;
            }
        }

        return false;
    }

    // ----------------------------------------------------------------- member references

    private bool CheckSignature(Ctx ctx, MethodReference r)
    {
        var hit = CheckTypeUsage(ctx, r.DeclaringType, "declaring type", skipLayering: true);
        if (r is GenericInstanceMethod gim)
        {
            foreach (var a in gim.GenericArguments)
            {
                hit |= CheckTypeUsage(ctx, a, "generic method argument");
            }
        }

        hit |= CheckTypeUsage(ctx, r.ReturnType, "referenced signature");
        foreach (var p in r.Parameters)
        {
            hit |= CheckTypeUsage(ctx, p.ParameterType, "referenced signature");
        }

        return hit;
    }

    private void CheckMethodRef(Ctx ctx, MethodReference r, Instruction ins)
    {
        Stats.MemberReferences++;
        var origin = _env.OriginOf(r.DeclaringType);
        if (origin == Origin.Observer)
        {
            // Signature types of internal calls are already checked at their definitions.
            return;
        }

        var hard = CheckSignature(ctx, r);
        var openDecl = Names.OpenType(r.DeclaringType);
        var name = r.Name;
        var member = Names.Method(r);
        var def = Resolver.Method(r);

        switch (origin)
        {
            case Origin.Game:
            case Origin.Unity:
            {
                if (origin == Origin.Game)
                {
                    Stats.GameReferences++;
                }
                else
                {
                    Stats.UnityReferences++;
                }

                if (ins.OpCode.Code == Code.Ldtoken)
                {
                    Report(ctx, Rules.Reflection, $"ldtoken of method handle {member}");
                    hard = true;
                }

                var g5 = origin == Origin.Unity && Rules.IsUnityMutator(openDecl, name);
                if (g5)
                {
                    Report(ctx, Rules.UnityMutator, $"Unity mutator {member}");
                    hard = true;
                }
                else if (Rules.IsMutatorName(name) && !(openDecl == Rules.SceneManagerType && Rules.SceneManagerEventAdds.Contains(name)))
                {
                    Report(ctx, Rules.MutatorName, $"mutator-named member {member}");
                    hard = true;
                }

                var deny = MatchDeny(r, def);
                if (deny != null)
                {
                    Report(ctx, Rules.Denylist, $"denylisted member {member} [{deny.Member}: {deny.Reason}]");
                    hard = true;
                }

                if (origin == Origin.Game && def != null && (def.IsVirtual || def.DeclaringType.IsInterface)
                    && ins.OpCode.Code is Code.Callvirt or Code.Ldvirtftn)
                {
                    foreach (var target in _index.DispatchTargets(def))
                    {
                        var td = MatchDeny(target, target);
                        if (td != null)
                        {
                            Report(ctx, Rules.Denylist, $"virtual call {member} can dispatch to denylisted {Names.MethodKey(target)} [{td.Member}: {td.Reason}]");
                            hard = true;
                        }
                    }
                }

                if (!ctx.ReadLayer && !(ctx.ModScope && IsModAllowedMember(openDecl, name)))
                {
                    ReportLayering(ctx, $"reference to {member}");
                    hard = true;
                }

                if (!hard)
                {
                    var kind = Names.MethodKind(r);
                    CheckAllowlisted(ctx, origin, member, kind, def);
                }

                break;
            }

            case Origin.Bcl:
                CheckBclMethod(ctx, r, def, openDecl, name, member);
                break;

            case Origin.Harmony:
                Report(ctx, Rules.Forbidden, $"Harmony member {member}");
                break;

            case Origin.Newtonsoft:
            case Origin.Other:
            {
                var deny = MatchDeny(r, def);
                if (deny != null)
                {
                    Report(ctx, Rules.Denylist, $"denylisted member {member} [{deny.Member}: {deny.Reason}]");
                }

                break;
            }
        }
    }

    private void CheckAllowlisted(Ctx ctx, Origin origin, string member, string kind, MethodDefinition? gameMethod)
    {
        var entry = origin == Origin.Game ? _allow.FindGame(member, kind) : _allow.FindUnity(member, kind);
        if (entry != null)
        {
            UsedEntries.Add(entry);
            return;
        }

        var section = origin == Origin.Game ? "game" : "unity";
        Report(ctx, Rules.Allow, $"{section} member {member} ({kind}) is not in the {section} allowlist");
        AddSuggestion(section, member, kind, null, ctx.Location, origin == Origin.Game ? gameMethod : null);
    }

    private void AddSuggestion(string section, string member, string kind, bool? isPrivate, string from, MethodDefinition? gameMethod)
    {
        var key = section + "|" + member + "|" + kind;
        if (!Suggestions.TryGetValue(key, out var s))
        {
            var entry = new AllowEntry
            {
                Member = member,
                Kind = kind,
                Private = isPrivate,
                Review = "TODO: summarize the decompiled body; confirm it has no side effects",
            };
            if (section == "game")
            {
                entry.DataMapId = "TODO";
                entry.Evidence = "TODO: File.cs:line";
            }
            else
            {
                entry.Source = "suggested by --suggest";
            }

            s = new Suggestion { Section = section, Entry = entry, GameMethod = gameMethod };
            Suggestions[key] = s;
        }

        s.ReferencedFrom.Add(from);
    }

    private DenyEntry? MatchDeny(MethodReference r, MethodDefinition? def)
    {
        var closed = Names.Type(r.DeclaringType);
        var open = Names.OpenType(r.DeclaringType);
        var outer = Denylist.OuterTypesOf(open);
        IEnumerable<ParameterDefinition> ps = def != null ? def.Parameters : r.Parameters;
        var nameAndParams = r.Name + "(" + string.Join(",", ps.Select(p => Names.Type(p.ParameterType))) + ")";
        return _deny.MatchMember(closed, open, outer, r.Name, nameAndParams);
    }

    private void CheckBclMethod(Ctx ctx, MethodReference r, MethodDefinition? def, string openDecl, string name, string member)
    {
        if (def == null)
        {
            Report(ctx, Rules.Bcl, $"BCL member {member} does not resolve in the game's Managed mscorlib/System/System.Core");
        }

        var deny = MatchDeny(r, def);
        if (deny != null)
        {
            Report(ctx, Rules.Denylist, $"denylisted member {member} [{deny.Member}: {deny.Reason}]");
        }

        CheckReflection(ctx, r, openDecl, name, member);

        // G8
        var ns = Names.ElementOf(r.DeclaringType).Namespace;
        if (openDecl == "System.Diagnostics.Process" && !(name == "GetCurrentProcess" && r.Parameters.Count == 0) && name != "get_Id")
        {
            Report(ctx, Rules.ProcessNet, $"process API {member}");
        }

        if (openDecl == "System.Threading.Thread" && name is "Abort" or "Suspend" or "Resume")
        {
            Report(ctx, Rules.ProcessNet, $"thread control API {member}");
        }

        if (Rules.IsForbiddenIpcNamespace(ns))
        {
            Report(ctx, Rules.ProcessNet, $"network/IPC API {member}");
        }

        // G7: raw memory access.
        if (openDecl is "System.Runtime.InteropServices.Marshal" or "System.Runtime.InteropServices.GCHandle")
        {
            Report(ctx, Rules.TypeRules, $"unsafe memory API {member}");
        }

        // G9: file APIs only in RoiMcp.Observer.Io.
        if (!ctx.Io)
        {
            if (Rules.FileApiTypes.Contains(openDecl) || Rules.InNamespace(ns, "System.IO.IsolatedStorage"))
            {
                Report(ctx, Rules.Layering, $"file API {member}: only RoiMcp.Observer.Io may use System.IO file APIs");
            }
            else if (Rules.PathStreamTypes.Contains(openDecl) && name == ".ctor" && r.Parameters.Count > 0
                     && r.Parameters[0].ParameterType.MetadataType == MetadataType.String)
            {
                Report(ctx, Rules.Layering, $"path-based stream constructor {member}: only RoiMcp.Observer.Io may open files");
            }
        }
    }

    private void CheckReflection(Ctx ctx, MethodReference r, string openDecl, string name, string member)
    {
        var ns = Names.ElementOf(r.DeclaringType).Namespace;
        string? banned = null;
        switch (openDecl)
        {
            case "System.Reflection.FieldInfo":
                if (name is "SetValue" or "SetValueDirect" or "GetValueDirect" or "GetFieldFromHandle")
                {
                    banned = member;
                }
                else if (name == "GetValue" && !ctx.ReflectionTable)
                {
                    banned = member + " outside " + Rules.ReflectionTableType;
                }

                break;
            case "System.Reflection.PropertyInfo":
                if (name is "SetValue" or "GetSetMethod" or "GetValue" or "GetGetMethod" or "GetAccessors")
                {
                    banned = member;
                }

                break;
            case "System.Reflection.MethodBase":
            case "System.Reflection.MethodInfo":
            case "System.Reflection.ConstructorInfo":
                if (name is "Invoke" or "CreateDelegate" or "GetMethodFromHandle")
                {
                    banned = member;
                }

                break;
            case "System.Reflection.EventInfo":
                if (name is "AddEventHandler" or "RemoveEventHandler" or "GetAddMethod" or "GetRemoveMethod" or "GetRaiseMethod")
                {
                    banned = member;
                }

                break;
            case "System.Type":
                if (name == "GetField")
                {
                    if (member != "System.Type::GetField(System.String,System.Reflection.BindingFlags)")
                    {
                        banned = member + " (only GetField(string, BindingFlags) with literal arguments is allowed)";
                    }
                    else if (!ctx.ReflectionTable)
                    {
                        banned = member + " outside " + Rules.ReflectionTableType;
                    }
                    else
                    {
                        VerifyGetFieldPattern(ctx, r);
                    }
                }
                else if (name is "InvokeMember" or "GetFields" or "GetMember" or "GetMembers" or "FindMembers" or "GetProperty"
                         or "GetProperties" or "GetMethod" or "GetMethods" or "GetConstructor" or "GetConstructors" or "GetEvent"
                         or "GetEvents" or "GetDefaultMembers" or "get_TypeInitializer" or "GetInterfaceMap" or "GetTypeFromProgID"
                         or "GetTypeFromCLSID" or "ReflectionOnlyGetType"
                         || (name == "GetType" && r.Parameters.Count > 0))
                {
                    banned = member;
                }

                break;
            case "System.Reflection.TypeInfo":
                if (name.StartsWith("GetDeclared", StringComparison.Ordinal) || name.StartsWith("get_Declared", StringComparison.Ordinal))
                {
                    banned = member;
                }

                break;
            case "System.Reflection.RuntimeReflectionExtensions":
            case "System.Activator":
            case "System.AppDomain":
            case "System.TypedReference":
            case "System.Runtime.Serialization.FormatterServices":
                banned = member;
                break;
            case "System.Delegate":
            case "System.MulticastDelegate":
                if (name is "CreateDelegate" or "DynamicInvoke")
                {
                    banned = member;
                }

                break;
            case "System.Reflection.Assembly":
                if (name.StartsWith("Load", StringComparison.Ordinal) || name.StartsWith("ReflectionOnlyLoad", StringComparison.Ordinal)
                    || name is "UnsafeLoadFrom" or "CreateInstance")
                {
                    banned = member;
                }

                break;
            case "System.Reflection.Module":
                if (name.StartsWith("Resolve", StringComparison.Ordinal) || name.StartsWith("GetField", StringComparison.Ordinal)
                    || name.StartsWith("GetMethod", StringComparison.Ordinal) || name == "FindTypes")
                {
                    banned = member;
                }

                break;
            case "System.Runtime.CompilerServices.RuntimeHelpers":
                if (name is "RunClassConstructor" or "RunModuleConstructor" or "PrepareMethod" or "PrepareDelegate")
                {
                    banned = member;
                }

                break;
        }

        if (banned == null && Rules.InNamespace(ns, "System.Reflection.Emit"))
        {
            banned = member;
        }

        if (banned == null && Rules.InNamespace(ns, "System.Linq.Expressions") && name == "Compile")
        {
            banned = member;
        }

        if (banned != null)
        {
            Report(ctx, Rules.Reflection, $"forbidden reflection: {banned}");
        }
    }

    /// <summary>
    /// G4: inside ReflectionTable, every GetField must be exactly
    /// ldtoken T; call Type::GetTypeFromHandle; ldstr "name"; ldc.i4 flags; callvirt Type::GetField(string, BindingFlags)
    /// with (T, name) allowlisted as a private field_read.
    /// </summary>
    private void VerifyGetFieldPattern(Ctx ctx, MethodReference r)
    {
        var m = ctx.Method!;
        var call = ctx.Ins!;
        var targets = _stack.BranchTargets(m);

        Instruction? Prev(Instruction i)
        {
            var p = i.Previous;
            while (p != null && p.OpCode.Code == Code.Nop)
            {
                p = p.Previous;
            }

            return p;
        }

        var flags = Prev(call);
        var str = flags == null ? null : Prev(flags);
        var gtfh = str == null ? null : Prev(str);
        var tok = gtfh == null ? null : Prev(gtfh);

        var ok = flags != null && TryGetInt(flags, out var flagValue)
                 && str != null && str.OpCode.Code == Code.Ldstr
                 && gtfh != null && gtfh.OpCode.Code == Code.Call && gtfh.Operand is MethodReference gm
                 && Names.Method(gm) == "System.Type::GetTypeFromHandle(System.RuntimeTypeHandle)"
                 && tok != null && tok.OpCode.Code == Code.Ldtoken && tok.Operand is TypeReference;

        if (!ok)
        {
            Report(ctx, Rules.Reflection, "Type.GetField must be called as typeof(GameType).GetField(\"literal\", BindingFlags) with a literal name");
            return;
        }

        // Nothing may jump into the middle of the pattern.
        for (var i = gtfh!; i != null && !ReferenceEquals(i, call.Next); i = i.Next)
        {
            if (targets.Contains(i))
            {
                Report(ctx, Rules.Reflection, "Type.GetField pattern is a branch target (non-literal argument possible)");
                return;
            }
        }

        TryGetInt(flags!, out var bf);
        const int allowedFlags = 4 | 8 | 16 | 32 | 2 | 64; // Instance | Static | Public | NonPublic | DeclaredOnly | FlattenHierarchy
        if ((bf & ~allowedFlags) != 0)
        {
            Report(ctx, Rules.Reflection, $"Type.GetField BindingFlags 0x{bf:x} contains non-lookup flags");
            return;
        }

        var type = (TypeReference)tok!.Operand;
        var fieldName = (string)str!.Operand;
        var closed = Names.Type(type);
        var open = Names.OpenType(type);
        if (_env.OriginOf(type) != Origin.Game)
        {
            Report(ctx, Rules.Reflection, $"reflection target {closed} is not a game type");
            return;
        }

        var td = Resolver.Type(type);
        if (td == null || td.Fields.All(f => f.Name != fieldName))
        {
            Report(ctx, Rules.Reflection, $"field {open}::{fieldName} does not exist on the declaring type");
            return;
        }

        var deny = _deny.MatchMemberString(open + "::" + fieldName);
        if (deny != null)
        {
            Report(ctx, Rules.Denylist, $"denylisted field {open}::{fieldName} [{deny.Member}: {deny.Reason}]");
            return;
        }

        var entry = _allow.FindPrivateField(closed, open, fieldName);
        if (entry == null)
        {
            Report(ctx, Rules.Reflection, $"GetField literal {open}::{fieldName} is not an allowlisted private field_read");
            AddSuggestion("game", open + "::" + fieldName, "field_read", true, ctx.Location, null);
            return;
        }

        Stats.ReflectionLookups++;
        UsedEntries.Add(entry);
    }

    private static bool TryGetInt(Instruction ins, out int value)
    {
        value = 0;
        switch (ins.OpCode.Code)
        {
            case Code.Ldc_I4_M1: value = -1; return true;
            case Code.Ldc_I4_0: value = 0; return true;
            case Code.Ldc_I4_1: value = 1; return true;
            case Code.Ldc_I4_2: value = 2; return true;
            case Code.Ldc_I4_3: value = 3; return true;
            case Code.Ldc_I4_4: value = 4; return true;
            case Code.Ldc_I4_5: value = 5; return true;
            case Code.Ldc_I4_6: value = 6; return true;
            case Code.Ldc_I4_7: value = 7; return true;
            case Code.Ldc_I4_8: value = 8; return true;
            case Code.Ldc_I4_S: value = (sbyte)ins.Operand; return true;
            case Code.Ldc_I4: value = (int)ins.Operand; return true;
            default: return false;
        }
    }

    private void CheckFieldRef(Ctx ctx, FieldReference f, Instruction ins)
    {
        Stats.MemberReferences++;
        var origin = _env.OriginOf(f.DeclaringType);
        if (origin == Origin.Observer)
        {
            return;
        }

        var hard = CheckTypeUsage(ctx, f.DeclaringType, "declaring type", skipLayering: true);
        hard |= CheckTypeUsage(ctx, f.FieldType, "referenced field type");
        var member = Names.Field(f);
        var open = Names.OpenType(f.DeclaringType);
        var code = ins.OpCode.Code;

        if (origin is Origin.Game or Origin.Unity)
        {
            if (origin == Origin.Game)
            {
                Stats.GameReferences++;
            }
            else
            {
                Stats.UnityReferences++;
            }

            if (code is Code.Stfld or Code.Stsfld or Code.Ldflda or Code.Ldsflda)
            {
                Report(ctx, Rules.FieldWrite, $"{ins.OpCode.Name} on game/Unity field {member}");
                hard = true;
            }

            if (code == Code.Ldtoken)
            {
                Report(ctx, Rules.Reflection, $"ldtoken of field handle {member}");
                hard = true;
            }

            var deny = _deny.MatchMember(Names.Type(f.DeclaringType), open, Denylist.OuterTypesOf(open), f.Name, f.Name);
            if (deny != null)
            {
                Report(ctx, Rules.Denylist, $"denylisted field {member} [{deny.Member}: {deny.Reason}]");
                hard = true;
            }

            if (!ctx.ReadLayer && !(ctx.ModScope && IsModAllowedMember(open, f.Name)))
            {
                ReportLayering(ctx, $"reference to {member}");
                hard = true;
            }

            if (!hard)
            {
                CheckAllowlisted(ctx, origin, member, "field_read", null);
            }

            return;
        }

        if (origin == Origin.Bcl && Resolver.Field(f) == null)
        {
            Report(ctx, Rules.Bcl, $"BCL field {member} does not resolve in the game's Managed assemblies");
        }

        if (origin == Origin.Harmony)
        {
            Report(ctx, Rules.Forbidden, $"Harmony member {member}");
        }

        var d = _deny.MatchMember(Names.Type(f.DeclaringType), open, Denylist.OuterTypesOf(open), f.Name, f.Name);
        if (d != null)
        {
            Report(ctx, Rules.Denylist, $"denylisted field {member} [{d.Member}: {d.Reason}]");
        }
    }
}

internal sealed class GateStats
{
    public int Types { get; set; }

    public int Methods { get; set; }

    public int Instructions { get; set; }

    public int MemberReferences { get; set; }

    public int GameReferences { get; set; }

    public int UnityReferences { get; set; }

    public int ReflectionLookups { get; set; }

    public int TransitiveRoots { get; set; }

    public int TransitiveMethodsScanned { get; set; }
}
