using Mono.Cecil;

namespace RoiMcp.ReadOnlyGate;

/// <summary>
/// Indexes over the game assemblies: definition keys, and virtual/interface dispatch targets.
/// Override detection is a deliberate over-approximation (same name, same parameter count and
/// matching non-generic parameter types), which can only add callees to the transitive walk.
/// </summary>
internal sealed class GameIndex
{
    private readonly GameEnvironment _env;
    private readonly Dictionary<string, List<MethodDefinition>> _methodsByKey = new(StringComparer.Ordinal);
    private readonly Dictionary<string, FieldDefinition> _fieldsByKey = new(StringComparer.Ordinal);
    private readonly Dictionary<MethodDefinition, List<MethodDefinition>> _directOverrides = new();
    private readonly List<TypeDefinition> _allTypes = new();
    private readonly Dictionary<string, TypeDefinition> _typesByName = new(StringComparer.Ordinal);
    private bool _overridesBuilt;

    public GameIndex(GameEnvironment env, IEnumerable<ModuleDefinition>? modules = null)
    {
        _env = env;
        foreach (var module in modules ?? env.GameModules)
        {
            foreach (var t in AllTypes(module.Types))
            {
                _allTypes.Add(t);
                _typesByName.TryAdd(Names.Type(t), t);
                foreach (var m in t.Methods)
                {
                    var key = Names.MethodKey(m);
                    if (!_methodsByKey.TryGetValue(key, out var list))
                    {
                        _methodsByKey[key] = list = new List<MethodDefinition>(1);
                    }

                    list.Add(m);
                }

                foreach (var f in t.Fields)
                {
                    _fieldsByKey[Names.FieldKey(f)] = f;
                }
            }
        }
    }

    public IReadOnlyList<TypeDefinition> Types => _allTypes;

    public TypeDefinition? FindType(string fullName) => _typesByName.TryGetValue(fullName, out var t) ? t : null;

    /// <summary>
    /// Resolves the top-level generic arguments of a closed type string such as
    /// "ProjectAutomata.ManagerBehaviour`1&lt;ProjectAutomata.World&gt;" against the indexed types.
    /// Unresolvable arguments are returned as null (unknown).
    /// </summary>
    public TypeReference?[]? TypeArgumentsOf(string closedType)
    {
        var lt = closedType.IndexOf('<');
        if (lt < 0 || !closedType.EndsWith('>'))
        {
            return null;
        }

        var inner = closedType.Substring(lt + 1, closedType.Length - lt - 2);
        var args = new List<string>();
        var depth = 0;
        var start = 0;
        for (var i = 0; i < inner.Length; i++)
        {
            switch (inner[i])
            {
                case '<': depth++; break;
                case '>': depth--; break;
                case ',' when depth == 0:
                    args.Add(inner.Substring(start, i - start));
                    start = i + 1;
                    break;
            }
        }

        args.Add(inner.Substring(start));
        return args.Select(a => (TypeReference?)FindType(a)).ToArray();
    }

    /// <summary>True if <paramref name="t"/> is <paramref name="b"/> or derives from it (class chain only).</summary>
    public static bool IsSameOrSubclass(TypeDefinition t, TypeDefinition b)
    {
        var cur = t;
        var guard = 0;
        while (cur != null && guard++ < 64)
        {
            if (ReferenceEquals(cur, b))
            {
                return true;
            }

            cur = cur.BaseType == null ? null : Resolver.Type(cur.BaseType);
        }

        return false;
    }

    public static IEnumerable<TypeDefinition> AllTypes(IEnumerable<TypeDefinition> roots)
    {
        foreach (var t in roots)
        {
            yield return t;
            if (t.HasNestedTypes)
            {
                foreach (var n in AllTypes(t.NestedTypes))
                {
                    yield return n;
                }
            }
        }
    }

    public IReadOnlyList<MethodDefinition> FindMethods(string definitionKey) =>
        _methodsByKey.TryGetValue(definitionKey, out var l) ? l : Array.Empty<MethodDefinition>();

    public FieldDefinition? FindField(string definitionKey) =>
        _fieldsByKey.TryGetValue(definitionKey, out var f) ? f : null;

    public bool IsGameMethod(MethodDefinition m) => _env.IsGameModule(m.Module);

    /// <summary>All overrides / implementations (transitively) of a virtual or interface method, in game assemblies.</summary>
    public IReadOnlyList<MethodDefinition> DispatchTargets(MethodDefinition m)
    {
        EnsureOverrides();
        var result = new List<MethodDefinition>();
        var seen = new HashSet<MethodDefinition> { m };
        var queue = new Queue<MethodDefinition>();
        queue.Enqueue(m);
        while (queue.Count > 0)
        {
            var cur = queue.Dequeue();
            if (!_directOverrides.TryGetValue(cur, out var list))
            {
                continue;
            }

            foreach (var o in list)
            {
                if (seen.Add(o))
                {
                    result.Add(o);
                    queue.Enqueue(o);
                }
            }
        }

        return result;
    }

    private void AddOverride(MethodDefinition baseMethod, MethodDefinition impl)
    {
        if (!_directOverrides.TryGetValue(baseMethod, out var list))
        {
            _directOverrides[baseMethod] = list = new List<MethodDefinition>();
        }

        if (!list.Contains(impl))
        {
            list.Add(impl);
        }
    }

    private void EnsureOverrides()
    {
        if (_overridesBuilt)
        {
            return;
        }

        _overridesBuilt = true;
        foreach (var t in _allTypes)
        {
            if (t.IsInterface)
            {
                continue;
            }

            foreach (var m in t.Methods)
            {
                if (!m.IsVirtual)
                {
                    continue;
                }

                foreach (var o in m.Overrides)
                {
                    var od = Resolver.Method(o);
                    if (od != null)
                    {
                        AddOverride(od, m);
                    }
                }

                if (!m.IsNewSlot)
                {
                    var b = FindBaseVirtual(t, m);
                    if (b != null)
                    {
                        AddOverride(b, m);
                    }
                }
            }

            foreach (var iface in AllInterfaces(t))
            {
                foreach (var im in iface.Methods)
                {
                    var impl = FindImplementation(t, im);
                    if (impl != null && !ReferenceEquals(impl, im))
                    {
                        AddOverride(im, impl);
                    }
                }
            }
        }
    }

    private static MethodDefinition? FindBaseVirtual(TypeDefinition t, MethodDefinition m)
    {
        var bt = t.BaseType == null ? null : Resolver.Type(t.BaseType);
        var guard = 0;
        while (bt != null && guard++ < 64)
        {
            foreach (var cand in bt.Methods)
            {
                if (cand.IsVirtual && cand.Name == m.Name && ParamsMatch(cand, m))
                {
                    return cand;
                }
            }

            bt = bt.BaseType == null ? null : Resolver.Type(bt.BaseType);
        }

        return null;
    }

    private static MethodDefinition? FindImplementation(TypeDefinition t, MethodDefinition im)
    {
        var cur = t;
        var guard = 0;
        while (cur != null && guard++ < 64)
        {
            foreach (var m in cur.Methods)
            {
                foreach (var o in m.Overrides)
                {
                    if (ReferenceEquals(Resolver.Method(o), im))
                    {
                        return m;
                    }
                }
            }

            foreach (var m in cur.Methods)
            {
                if (m.IsVirtual && m.Name == im.Name && ParamsMatch(m, im))
                {
                    return m;
                }
            }

            cur = cur.BaseType == null ? null : Resolver.Type(cur.BaseType);
        }

        return null;
    }

    private static IEnumerable<TypeDefinition> AllInterfaces(TypeDefinition t)
    {
        var seen = new HashSet<TypeDefinition>();
        var stack = new Stack<TypeReference>();
        var cur = t;
        var guard = 0;
        while (cur != null && guard++ < 64)
        {
            foreach (var i in cur.Interfaces)
            {
                stack.Push(i.InterfaceType);
            }

            cur = cur.BaseType == null ? null : Resolver.Type(cur.BaseType);
        }

        while (stack.Count > 0)
        {
            var d = Resolver.Type(stack.Pop());
            if (d == null || !seen.Add(d))
            {
                continue;
            }

            yield return d;
            foreach (var i in d.Interfaces)
            {
                stack.Push(i.InterfaceType);
            }
        }
    }

    private static bool ParamsMatch(MethodDefinition a, MethodDefinition b)
    {
        if (a.Parameters.Count != b.Parameters.Count)
        {
            return false;
        }

        for (var i = 0; i < a.Parameters.Count; i++)
        {
            var pa = a.Parameters[i].ParameterType;
            var pb = b.Parameters[i].ParameterType;
            if (pa.ContainsGenericParameter || pb.ContainsGenericParameter)
            {
                continue;
            }

            if (Names.Type(pa) != Names.Type(pb))
            {
                return false;
            }
        }

        return true;
    }
}
