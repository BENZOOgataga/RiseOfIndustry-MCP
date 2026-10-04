using Mono.Cecil;
using Mono.Cecil.Cil;

namespace RoiMcp.ReadOnlyGate;

internal sealed record TransitiveFinding(
    string Id,
    string ShortId,
    string Kind,
    string Target,
    string Container,
    IReadOnlyList<string> Via,
    bool Ackable);

internal sealed class TransitiveResult
{
    public required string Root { get; init; }

    public List<TransitiveFinding> Findings { get; } = new();

    public SortedSet<string> UnityBoundaryCalls { get; } = new(StringComparer.Ordinal);

    public int MethodsScanned { get; set; }

    public bool Capped { get; set; }
}

/// <summary>
/// PRD 10.4 transitive scan. Walks callee bodies inside Assembly-CSharp / firstpass from an
/// allowlisted game member to a fixed depth and reports potential mutations.
///
/// "this-local": the root's own instance (ldarg.0 in an instance root) and, recursively, the
/// instance of a callee invoked on a this-local receiver; also the fresh object of a newobj.
/// Writes to this-local objects are not reported. Every other stfld, every stsfld, every
/// denylisted callee (including virtual dispatch targets), UnityEngine.Random, World.deterministicRandom
/// and writes into System.Collections(.Generic) collections that are not method-local are reported.
/// </summary>
internal sealed class TransitiveScanner
{
    public const string KindStfld = "stfld";
    public const string KindStsfld = "stsfld";
    public const string KindDenylisted = "denylisted-call";
    public const string KindUnityRandom = "unity-random";
    public const string KindDeterministicRandom = "deterministic-random";
    public const string KindCollectionWrite = "collection-write";
    public const string KindCallerCollectionWrite = "caller-collection-write";
    public const string KindCapped = "walk-capped";

    private static readonly HashSet<string> CollectionMutators = new(StringComparer.Ordinal)
    {
        "Add", "AddRange", "Insert", "InsertRange", "set_Item", "Remove", "RemoveAt", "RemoveAll", "RemoveRange",
        "RemoveWhere", "Clear", "Enqueue", "Dequeue", "Push", "Pop", "TryAdd", "TryRemove", "UnionWith",
        "ExceptWith", "IntersectWith", "SymmetricExceptWith", "Sort", "Reverse", "AddFirst", "AddLast",
        "AddBefore", "AddAfter", "RemoveFirst", "RemoveLast", "TrimExcess", "CopyTo", "TryDequeue", "TryPop",
        "GetOrAdd", "AddOrUpdate", "TryUpdate",
    };

    private readonly GameEnvironment _env;
    private readonly GameIndex _index;
    private readonly Denylist _deny;
    private readonly IlStack _stack = new();

    public TransitiveScanner(GameEnvironment env, GameIndex index, Denylist deny)
    {
        _env = env;
        _index = index;
        _deny = deny;
    }

    public int MaxDepth { get; init; } = 4;

    public int FanOutCap { get; init; } = 32;

    public int MaxMethodsPerRoot { get; init; } = 4000;

    /// <summary>
    /// A method to scan. TypeArgs / MethodArgs bind the generic parameters of the method's declaring
    /// type / of the method itself (by position) when known, so virtual dispatch on a receiver of a
    /// generic parameter type (e.g. ManagerBehaviour&lt;World&gt;) can be narrowed to the bound type.
    /// </summary>
    private sealed record Node(
        MethodDefinition Method,
        bool ThisLocal,
        int Depth,
        IReadOnlyList<string> Via,
        TypeReference?[]? TypeArgs,
        TypeReference?[]? MethodArgs,
        bool[] CallerArgs)
    {
        public string ContextKey =>
            (TypeArgs == null ? string.Empty : string.Join(",", TypeArgs.Select(a => a == null ? "?" : Names.Type(a)))) + "|" +
            (MethodArgs == null ? string.Empty : string.Join(",", MethodArgs.Select(a => a == null ? "?" : Names.Type(a)))) + "|" +
            string.Concat(CallerArgs.Select(b => b ? '1' : '0'));
    }

    public TransitiveResult Scan(string rootMember, MethodDefinition root, TypeReference?[]? rootTypeArgs = null, TypeReference?[]? rootMethodArgs = null)
    {
        var result = new TransitiveResult { Root = rootMember };
        var seenIds = new HashSet<string>(StringComparer.Ordinal);
        var visited = new HashSet<(MethodDefinition, bool, string)>();
        var queue = new Queue<Node>();
        // Every argument of the root is supplied by the observer (caller-provided).
        var rootCallerArgs = Enumerable.Repeat(true, root.Parameters.Count).ToArray();
        queue.Enqueue(new Node(root, root.HasThis, 0, Array.Empty<string>(), rootTypeArgs, rootMethodArgs, rootCallerArgs));

        void Add(string kind, string target, MethodDefinition container, IReadOnlyList<string> via, bool ackable)
        {
            var containerName = Names.MethodKey(container);
            var shortId = $"{containerName} : {kind} {target}";
            var id = $"{rootMember} -> {shortId}";
            if (seenIds.Add(id))
            {
                result.Findings.Add(new TransitiveFinding(id, shortId, kind, target, containerName, via, ackable));
            }
        }

        while (queue.Count > 0)
        {
            var node = queue.Dequeue();
            if (!visited.Add((node.Method, node.ThisLocal, node.ContextKey)))
            {
                continue;
            }

            if (result.MethodsScanned >= MaxMethodsPerRoot)
            {
                if (!result.Capped)
                {
                    result.Capped = true;
                    Add(KindCapped, $"method budget {MaxMethodsPerRoot} exhausted", root, Array.Empty<string>(), true);
                }

                break;
            }

            result.MethodsScanned++;
            var m = node.Method;
            if (!m.HasBody)
            {
                continue;
            }

            var via = node.Via.Concat(new[] { Names.MethodKey(m) }).ToList();
            var thisStable = node.ThisLocal && !IlStack.StoresToThis(m);

            foreach (var ins in m.Body.Instructions)
            {
                switch (ins.OpCode.Code)
                {
                    case Code.Stfld:
                    {
                        var f = (FieldReference)ins.Operand;
                        CheckDeterministicRandom(f, m, via, Add);
                        var producer = _stack.FindProducer(m, ins, 1);
                        var isThis = thisStable && producer != null && IlStack.IsThisLoad(m, producer);
                        if (!isThis && !IsMethodLocalStorage(producer))
                        {
                            Add(KindStfld, Names.Field(f), m, via, true);
                        }

                        break;
                    }

                    case Code.Stsfld:
                    {
                        var f = (FieldReference)ins.Operand;
                        CheckDeterministicRandom(f, m, via, Add);
                        Add(KindStsfld, Names.Field(f), m, via, true);
                        break;
                    }

                    case Code.Ldfld:
                    case Code.Ldflda:
                    case Code.Ldsfld:
                    case Code.Ldsflda:
                    {
                        var f = (FieldReference)ins.Operand;
                        CheckDeterministicRandom(f, m, via, Add);
                        if (IsUnityRandom(f.DeclaringType))
                        {
                            Add(KindUnityRandom, Names.Field(f), m, via, true);
                        }

                        break;
                    }

                    case Code.Call:
                    case Code.Callvirt:
                    case Code.Newobj:
                    case Code.Ldftn:
                    case Code.Ldvirtftn:
                    {
                        var callee = (MethodReference)ins.Operand;
                        HandleCall(node, m, ins, callee, via, thisStable, queue, result, Add);
                        break;
                    }
                }
            }
        }

        return result;
    }

    private void HandleCall(
        Node node,
        MethodDefinition m,
        Instruction ins,
        MethodReference callee,
        IReadOnlyList<string> via,
        bool thisStable,
        Queue<Node> queue,
        TransitiveResult result,
        Action<string, string, MethodDefinition, IReadOnlyList<string>, bool> add)
    {
        var calleeName = Names.Method(callee);
        if (IsUnityRandom(callee.DeclaringType))
        {
            add(KindUnityRandom, calleeName, m, via, true);
            return;
        }

        var origin = _env.OriginOf(callee.DeclaringType);
        if (origin == Origin.Bcl && IsCollectionWrite(callee))
        {
            if (IsMethodLocalReceiver(m, ins, callee))
            {
                return;
            }

            // A collection received as an argument that traces back to the root's caller (the observer)
            // is the observer's own buffer: reportable but acknowledgeable. Anything else is a game collection.
            var receiver = callee.HasThis ? _stack.FindProducer(m, ins, callee.Parameters.Count) : null;
            var param = receiver == null ? null : ArgumentOf(m, receiver);
            if (param != null && param.Index < node.CallerArgs.Length && node.CallerArgs[param.Index] && !StoresToParameter(m, param))
            {
                add(KindCallerCollectionWrite, calleeName, m, via, true);
            }
            else
            {
                add(KindCollectionWrite, calleeName, m, via, false);
            }

            return;
        }

        if (origin == Origin.Unity)
        {
            result.UnityBoundaryCalls.Add(calleeName);
            return;
        }

        if (origin != Origin.Game)
        {
            return;
        }

        var def = Resolver.Method(callee);
        if (MatchesDeny(callee, def, out var denied))
        {
            add(KindDenylisted, $"{calleeName} [{denied}]", m, via, true);
            return;
        }

        if (def == null)
        {
            return;
        }

        // Receiver analysis for the this-local chain.
        bool calleeThisLocal;
        if (ins.OpCode.Code == Code.Newobj)
        {
            calleeThisLocal = true;
        }
        else if (ins.OpCode.Code is Code.Ldftn or Code.Ldvirtftn)
        {
            calleeThisLocal = false;
        }
        else if (def.HasThis)
        {
            var receiver = _stack.FindProducer(m, ins, callee.Parameters.Count);
            calleeThisLocal = thisStable && receiver != null && IlStack.IsThisLoad(m, receiver);
        }
        else
        {
            calleeThisLocal = false;
        }

        var nextDepth = node.Depth + 1;
        var nextVia = via;
        var calleeTypeArgs = callee.DeclaringType is GenericInstanceType git
            ? git.GenericArguments.Select(a => Substitute(a, node, null)).ToArray()
            : (ReferenceEquals(Resolver.Type(callee.DeclaringType), m.DeclaringType) ? node.TypeArgs : null);
        var calleeMethodArgs = callee is GenericInstanceMethod gim
            ? gim.GenericArguments.Select(a => Substitute(a, node, null)).ToArray()
            : null;
        var calleeCallerArgs = CallerArgsAt(node, m, ins, callee);

        var isDispatch = (ins.OpCode.Code is Code.Callvirt or Code.Ldvirtftn) && (def.IsVirtual || def.DeclaringType.IsInterface);
        if (isDispatch)
        {
            var bound = ReceiverBound(node, m, ins, callee);
            var targets = _index.DispatchTargets(def)
                .Where(t => bound == null || GameIndex.IsSameOrSubclass(t.DeclaringType, bound) || GameIndex.IsSameOrSubclass(bound, t.DeclaringType))
                .ToList();
            var count = 0;
            foreach (var t in targets)
            {
                if (MatchesDeny(t, t, out var deniedImpl))
                {
                    add(KindDenylisted, $"{Names.MethodKey(t)} [{deniedImpl}] via dispatch of {calleeName}", m, via, true);
                    continue;
                }

                if (count++ >= FanOutCap)
                {
                    add(KindCapped, $"dispatch fan-out of {calleeName} capped at {FanOutCap} of {targets.Count}", m, via, true);
                    break;
                }

                if (nextDepth <= MaxDepth && t.HasBody)
                {
                    var sameType = ReferenceEquals(t.DeclaringType, def.DeclaringType);
                    queue.Enqueue(new Node(t, calleeThisLocal, nextDepth, nextVia, sameType ? calleeTypeArgs : null, calleeMethodArgs, calleeCallerArgs));
                }
            }
        }

        if (nextDepth <= MaxDepth && def.HasBody && _index.IsGameMethod(def))
        {
            queue.Enqueue(new Node(def, calleeThisLocal, nextDepth, nextVia, calleeTypeArgs, calleeMethodArgs, calleeCallerArgs));
        }
    }

    /// <summary>
    /// True if a stfld target is storage owned by the executing method: the address of a local struct
    /// (ldloca), of a by-value struct argument (ldarga on a non-by-ref parameter), or a fresh object (newobj).
    /// </summary>
    private static bool IsMethodLocalStorage(Instruction? producer)
    {
        if (producer == null)
        {
            return false;
        }

        switch (producer.OpCode.Code)
        {
            case Code.Ldloca:
            case Code.Ldloca_S:
            case Code.Newobj:
                return true;
            case Code.Ldarga:
            case Code.Ldarga_S:
                return producer.Operand is ParameterDefinition p && p.ParameterType is not ByReferenceType && p.Index >= 0;
            default:
                return false;
        }
    }

    /// <summary>For each callee parameter: true if the argument is a caller-provided (observer) value passed through unchanged.</summary>
    private bool[] CallerArgsAt(Node node, MethodDefinition m, Instruction call, MethodReference callee)
    {
        var n = callee.Parameters.Count;
        var result = new bool[n];
        if (call.OpCode.Code is Code.Ldftn or Code.Ldvirtftn)
        {
            return result;
        }

        for (var i = 0; i < n; i++)
        {
            var producer = _stack.FindProducer(m, call, n - 1 - i);
            var param = producer == null ? null : ArgumentOf(m, producer);
            result[i] = param != null && param.Index < node.CallerArgs.Length && node.CallerArgs[param.Index] && !StoresToParameter(m, param);
        }

        return result;
    }

    /// <summary>The (non-this) parameter loaded by an ldarg instruction, or null.</summary>
    private static ParameterDefinition? ArgumentOf(MethodDefinition m, Instruction ins)
    {
        var offset = m.HasThis ? 1 : 0;
        int index;
        switch (ins.OpCode.Code)
        {
            case Code.Ldarg_0: index = 0; break;
            case Code.Ldarg_1: index = 1; break;
            case Code.Ldarg_2: index = 2; break;
            case Code.Ldarg_3: index = 3; break;
            case Code.Ldarg:
            case Code.Ldarg_S:
                return ins.Operand is ParameterDefinition pd && !ReferenceEquals(pd, m.Body.ThisParameter) ? pd : null;
            default:
                return null;
        }

        var pi = index - offset;
        return pi >= 0 && pi < m.Parameters.Count ? m.Parameters[pi] : null;
    }

    private static bool StoresToParameter(MethodDefinition m, ParameterDefinition p)
    {
        foreach (var ins in m.Body.Instructions)
        {
            if ((ins.OpCode.Code is Code.Starg or Code.Starg_S or Code.Ldarga or Code.Ldarga_S) && ReferenceEquals(ins.Operand, p))
            {
                return true;
            }
        }

        return false;
    }

    /// <summary>Replaces generic parameters by their bound types from the node context (null if unknown).</summary>
    private static TypeReference? Substitute(TypeReference t, Node node, TypeReference? memberDeclaringType)
    {
        if (t is GenericParameter gp)
        {
            if (gp.Type == GenericParameterType.Type)
            {
                // A parameter of the referenced member's declaring type instance (position N of A<X>) maps to X first.
                if (memberDeclaringType is GenericInstanceType mgit && gp.Position < mgit.GenericArguments.Count
                    && !ReferenceEquals(mgit.GenericArguments[gp.Position], gp))
                {
                    return Substitute(mgit.GenericArguments[gp.Position], node, null);
                }

                return node.TypeArgs != null && gp.Position < node.TypeArgs.Length ? node.TypeArgs[gp.Position] : null;
            }

            return node.MethodArgs != null && gp.Position < node.MethodArgs.Length ? node.MethodArgs[gp.Position] : null;
        }

        return t;
    }

    /// <summary>Static type of a virtual call's receiver, when it can be determined from straight-line IL.</summary>
    private TypeDefinition? ReceiverBound(Node node, MethodDefinition m, Instruction call, MethodReference callee)
    {
        var p = _stack.FindProducer(m, call, callee.Parameters.Count);
        if (p == null)
        {
            return null;
        }

        TypeReference? t = null;
        switch (p.OpCode.Code)
        {
            case Code.Box:
            case Code.Castclass:
            case Code.Isinst:
                t = Substitute((TypeReference)p.Operand, node, null);
                break;
            case Code.Ldfld:
            case Code.Ldsfld:
            {
                var f = (FieldReference)p.Operand;
                t = Substitute(f.FieldType, node, f.DeclaringType);
                break;
            }

            case Code.Newobj:
                t = ((MethodReference)p.Operand).DeclaringType;
                break;
            case Code.Call:
            case Code.Callvirt:
            {
                var mr = (MethodReference)p.Operand;
                t = Substitute(mr.ReturnType, node, mr.DeclaringType);
                break;
            }

            default:
                if (IlStack.IsThisLoad(m, p))
                {
                    return m.DeclaringType;
                }

                break;
        }

        if (t == null || t is GenericParameter)
        {
            return null;
        }

        var d = Resolver.Type(t);
        return d == null || d.IsInterface ? null : d;
    }

    private bool MatchesDeny(MethodReference callee, MethodDefinition? def, out string denied)
    {
        denied = string.Empty;
        var closed = Names.Type(callee.DeclaringType);
        var open = Names.OpenType(callee.DeclaringType);
        var outer = Denylist.OuterTypesOf(open);
        IEnumerable<ParameterDefinition> ps = def != null ? def.Parameters : callee.Parameters;
        var nameAndParams = callee.Name + "(" + string.Join(",", ps.Select(p => Names.Type(p.ParameterType))) + ")";
        var hit = _deny.MatchMember(closed, open, outer, callee.Name, nameAndParams);
        if (hit != null)
        {
            denied = hit.Member;
            return true;
        }

        return false;
    }

    private static void CheckDeterministicRandom(
        FieldReference f,
        MethodDefinition m,
        IReadOnlyList<string> via,
        Action<string, string, MethodDefinition, IReadOnlyList<string>, bool> add)
    {
        if (f.Name == "deterministicRandom" && Names.OpenType(f.DeclaringType) == "ProjectAutomata.World")
        {
            add(KindDeterministicRandom, Names.Field(f), m, via, true);
        }
    }

    private static bool IsUnityRandom(TypeReference t) => Names.OpenType(t) == "UnityEngine.Random";

    private static bool IsCollectionWrite(MethodReference callee)
    {
        var ns = Names.ElementOf(callee.DeclaringType).Namespace;
        if (ns != "System.Collections.Generic" && ns != "System.Collections" && ns != "System.Collections.Concurrent" && ns != "System.Collections.ObjectModel")
        {
            return false;
        }

        return CollectionMutators.Contains(callee.Name);
    }

    /// <summary>
    /// True if the receiver of a collection call is provably a collection created in this method
    /// (newobj directly, or a local whose every store comes straight from a newobj).
    /// </summary>
    private bool IsMethodLocalReceiver(MethodDefinition m, Instruction call, MethodReference callee)
    {
        if (!callee.HasThis)
        {
            return false;
        }

        var receiver = _stack.FindProducer(m, call, callee.Parameters.Count);
        if (receiver == null)
        {
            return false;
        }

        if (receiver.OpCode.Code == Code.Newobj)
        {
            return true;
        }

        var local = LocalOf(m, receiver);
        if (local == null)
        {
            return false;
        }

        var stores = 0;
        foreach (var ins in m.Body.Instructions)
        {
            if (!IsStoreTo(m, ins, local))
            {
                continue;
            }

            stores++;
            var value = _stack.FindProducer(m, ins, 0);
            if (value == null || value.OpCode.Code != Code.Newobj)
            {
                return false;
            }
        }

        if (stores == 0)
        {
            return false;
        }

        // The local's address must never escape (ldloca would allow aliasing writes).
        foreach (var ins in m.Body.Instructions)
        {
            if ((ins.OpCode.Code == Code.Ldloca || ins.OpCode.Code == Code.Ldloca_S) && ReferenceEquals(ins.Operand, local))
            {
                return false;
            }
        }

        return true;
    }

    private static VariableDefinition? LocalOf(MethodDefinition m, Instruction ins)
    {
        var vars = m.Body.Variables;
        return ins.OpCode.Code switch
        {
            Code.Ldloc_0 => vars.Count > 0 ? vars[0] : null,
            Code.Ldloc_1 => vars.Count > 1 ? vars[1] : null,
            Code.Ldloc_2 => vars.Count > 2 ? vars[2] : null,
            Code.Ldloc_3 => vars.Count > 3 ? vars[3] : null,
            Code.Ldloc or Code.Ldloc_S => ins.Operand as VariableDefinition,
            _ => null,
        };
    }

    private static bool IsStoreTo(MethodDefinition m, Instruction ins, VariableDefinition v)
    {
        var vars = m.Body.Variables;
        return ins.OpCode.Code switch
        {
            Code.Stloc_0 => vars.Count > 0 && ReferenceEquals(vars[0], v),
            Code.Stloc_1 => vars.Count > 1 && ReferenceEquals(vars[1], v),
            Code.Stloc_2 => vars.Count > 2 && ReferenceEquals(vars[2], v),
            Code.Stloc_3 => vars.Count > 3 && ReferenceEquals(vars[3], v),
            Code.Stloc or Code.Stloc_S => ReferenceEquals(ins.Operand, v),
            _ => false,
        };
    }
}
