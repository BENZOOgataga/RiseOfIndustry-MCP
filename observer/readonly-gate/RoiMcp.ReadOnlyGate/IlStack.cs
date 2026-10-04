using Mono.Cecil;
using Mono.Cecil.Cil;

namespace RoiMcp.ReadOnlyGate;

/// <summary>
/// Minimal backward evaluation-stack analysis over straight-line IL. Used to find which instruction
/// produced a given stack slot (e.g. the target object of a stfld or the receiver of a call).
/// Returns null whenever control flow could merge (branch targets, exception handlers), so callers
/// must treat null as "unknown" and stay conservative.
/// </summary>
internal sealed class IlStack
{
    private readonly Dictionary<MethodDefinition, HashSet<Instruction>> _targets = new();

    public HashSet<Instruction> BranchTargets(MethodDefinition m)
    {
        if (_targets.TryGetValue(m, out var set))
        {
            return set;
        }

        set = new HashSet<Instruction>();
        foreach (var ins in m.Body.Instructions)
        {
            switch (ins.Operand)
            {
                case Instruction t:
                    set.Add(t);
                    break;
                case Instruction[] ts:
                    foreach (var t in ts)
                    {
                        set.Add(t);
                    }

                    break;
            }
        }

        foreach (var h in m.Body.ExceptionHandlers)
        {
            if (h.TryStart != null) set.Add(h.TryStart);
            if (h.TryEnd != null) set.Add(h.TryEnd);
            if (h.HandlerStart != null) set.Add(h.HandlerStart);
            if (h.HandlerEnd != null) set.Add(h.HandlerEnd);
            if (h.FilterStart != null) set.Add(h.FilterStart);
        }

        _targets[m] = set;
        return set;
    }

    /// <summary>
    /// Instruction that pushed the value found at <paramref name="slotFromTop"/> (0 = top of stack)
    /// immediately before <paramref name="at"/> executes, or null if it cannot be determined.
    /// </summary>
    public Instruction? FindProducer(MethodDefinition m, Instruction at, int slotFromTop) => Find(m, at, slotFromTop, 0);

    private const int MaxMergeDepth = 8;

    private Instruction? Find(MethodDefinition m, Instruction at, int slotFromTop, int depth)
    {
        if (depth > MaxMergeDepth)
        {
            return null;
        }

        var targets = BranchTargets(m);
        if (targets.Contains(at))
        {
            return FromPredecessors(m, at, slotFromTop, depth + 1);
        }

        var need = slotFromTop;
        var ins = at.Previous;
        var guard = 0;
        while (ins != null && guard++ < 1024)
        {
            if (EndsFlow(ins))
            {
                return null;
            }

            if (!TryStackEffect(m, ins, out var pop, out var push))
            {
                return null;
            }

            if (ins.OpCode.Code == Code.Dup)
            {
                if (need <= 1)
                {
                    need = 0;
                }
                else
                {
                    need -= 1;
                }
            }
            else
            {
                if (need < push)
                {
                    return ins;
                }

                need = need - push + pop;
            }

            if (targets.Contains(ins))
            {
                // Control can also reach 'ins' from elsewhere: all paths must agree on the producer.
                return FromPredecessors(m, ins, need, depth + 1);
            }

            ins = ins.Previous;
        }

        return null;
    }

    /// <summary>
    /// Producer of a slot in the stack just before <paramref name="target"/>, a join point. Every
    /// predecessor (fall-through and branches) must yield the same producing instruction.
    /// </summary>
    private Instruction? FromPredecessors(MethodDefinition m, Instruction target, int slot, int depth)
    {
        if (depth > MaxMergeDepth)
        {
            return null;
        }

        foreach (var h in m.Body.ExceptionHandlers)
        {
            if (ReferenceEquals(h.HandlerStart, target) || ReferenceEquals(h.FilterStart, target) || ReferenceEquals(h.TryStart, target))
            {
                return null;
            }
        }

        var preds = new List<Instruction>();
        if (target.Previous != null && !EndsFlow(target.Previous))
        {
            preds.Add(target.Previous);
        }

        foreach (var ins in m.Body.Instructions)
        {
            if (ReferenceEquals(ins.Operand, target) || (ins.Operand is Instruction[] arr && arr.Contains(target)))
            {
                preds.Add(ins);
            }
        }

        if (preds.Count == 0)
        {
            return null;
        }

        Instruction? result = null;
        foreach (var p in preds)
        {
            var r = AfterInstruction(m, p, slot, depth);
            if (r == null || (result != null && !ReferenceEquals(r, result)))
            {
                return null;
            }

            result = r;
        }

        return result;
    }

    /// <summary>Producer of a slot in the stack right after <paramref name="p"/> executes.</summary>
    private Instruction? AfterInstruction(MethodDefinition m, Instruction p, int slot, int depth)
    {
        if (!TryStackEffect(m, p, out var pop, out var push))
        {
            return null;
        }

        if (p.OpCode.Code == Code.Dup)
        {
            return Find(m, p, slot <= 1 ? 0 : slot - 1, depth);
        }

        if (slot < push)
        {
            return p;
        }

        return Find(m, p, slot - push + pop, depth);
    }

    public static bool IsThisLoad(MethodDefinition m, Instruction ins)
    {
        if (!m.HasThis)
        {
            return false;
        }

        switch (ins.OpCode.Code)
        {
            case Code.Ldarg_0:
                return true;
            case Code.Ldarg:
            case Code.Ldarg_S:
                return ReferenceEquals(ins.Operand, m.Body.ThisParameter);
            default:
                return false;
        }
    }

    /// <summary>True if the method ever overwrites argument 0 (so ldarg.0 may no longer be "this").</summary>
    public static bool StoresToThis(MethodDefinition m)
    {
        if (!m.HasThis)
        {
            return false;
        }

        foreach (var ins in m.Body.Instructions)
        {
            if ((ins.OpCode.Code == Code.Starg || ins.OpCode.Code == Code.Starg_S) && ReferenceEquals(ins.Operand, m.Body.ThisParameter))
            {
                return true;
            }

            if ((ins.OpCode.Code == Code.Ldarga || ins.OpCode.Code == Code.Ldarga_S) && ReferenceEquals(ins.Operand, m.Body.ThisParameter))
            {
                return true;
            }
        }

        return false;
    }

    private static bool EndsFlow(Instruction ins)
    {
        switch (ins.OpCode.FlowControl)
        {
            case FlowControl.Branch:
            case FlowControl.Return:
            case FlowControl.Throw:
                return true;
            default:
                return ins.OpCode.Code == Code.Jmp || ins.OpCode.Code == Code.Endfinally || ins.OpCode.Code == Code.Endfilter;
        }
    }

    public static bool TryStackEffect(MethodDefinition m, Instruction ins, out int pop, out int push)
    {
        pop = 0;
        push = 0;
        var op = ins.OpCode;
        switch (op.StackBehaviourPop)
        {
            case StackBehaviour.Pop0: pop = 0; break;
            case StackBehaviour.Pop1:
            case StackBehaviour.Popi:
            case StackBehaviour.Popref:
                pop = 1; break;
            case StackBehaviour.Pop1_pop1:
            case StackBehaviour.Popi_pop1:
            case StackBehaviour.Popi_popi:
            case StackBehaviour.Popi_popi8:
            case StackBehaviour.Popi_popr4:
            case StackBehaviour.Popi_popr8:
            case StackBehaviour.Popref_pop1:
            case StackBehaviour.Popref_popi:
                pop = 2; break;
            case StackBehaviour.Popi_popi_popi:
            case StackBehaviour.Popref_popi_popi:
            case StackBehaviour.Popref_popi_popi8:
            case StackBehaviour.Popref_popi_popr4:
            case StackBehaviour.Popref_popi_popr8:
            case StackBehaviour.Popref_popi_popref:
                pop = 3; break;
            case StackBehaviour.PopAll:
                return false;
            case StackBehaviour.Varpop:
                if (op.Code == Code.Ret)
                {
                    pop = m.ReturnType.MetadataType == MetadataType.Void ? 0 : 1;
                }
                else if (ins.Operand is MethodReference mr)
                {
                    pop = mr.Parameters.Count + (mr.HasThis && op.Code != Code.Newobj ? 1 : 0);
                }
                else if (ins.Operand is CallSite cs)
                {
                    pop = cs.Parameters.Count + (cs.HasThis ? 1 : 0) + 1;
                }
                else
                {
                    return false;
                }

                break;
            default:
                return false;
        }

        switch (op.StackBehaviourPush)
        {
            case StackBehaviour.Push0: push = 0; break;
            case StackBehaviour.Push1:
            case StackBehaviour.Pushi:
            case StackBehaviour.Pushi8:
            case StackBehaviour.Pushr4:
            case StackBehaviour.Pushr8:
            case StackBehaviour.Pushref:
                push = 1; break;
            case StackBehaviour.Push1_push1: push = 2; break;
            case StackBehaviour.Varpush:
                if (op.Code == Code.Newobj)
                {
                    push = 1;
                }
                else if (ins.Operand is MethodReference mr)
                {
                    push = mr.ReturnType.MetadataType == MetadataType.Void ? 0 : 1;
                }
                else if (ins.Operand is CallSite cs)
                {
                    push = cs.ReturnType.MetadataType == MetadataType.Void ? 0 : 1;
                }
                else
                {
                    return false;
                }

                break;
            default:
                return false;
        }

        return true;
    }
}
