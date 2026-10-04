using Mono.Cecil;

namespace RoiMcp.ReadOnlyGate;

/// <summary>
/// Canonical member strings used by the allowlist, the denylist, findings and reports.
///
/// Type:   Cecil-style full name. Nested types use '/', generic instances use
///         "Name`N&lt;Arg1,Arg2&gt;" (no spaces), arrays "[]", by-ref "&amp;", pointers "*".
///         Generic parameters are written by their declared name (e.g. "T").
/// Method: "&lt;DeclaringType&gt;::&lt;Name&gt;[&lt;GenericMethodArgs&gt;](&lt;ParamType&gt;,&lt;ParamType&gt;)".
///         The declaring type is written as referenced (closed generic instance if instantiated).
///         Parameter types are taken from the resolved definition (open, e.g. "T").
///         Constructors use the name ".ctor", property getters "get_X".
/// Field:  "&lt;DeclaringType&gt;::&lt;name&gt;".
/// </summary>
internal static class Names
{
    public static string Type(TypeReference t)
    {
        switch (t)
        {
            case RequiredModifierType r:
                return Type(r.ElementType);
            case OptionalModifierType o:
                return Type(o.ElementType);
            case PinnedType p:
                return Type(p.ElementType);
            case SentinelType s:
                return Type(s.ElementType);
            case ByReferenceType b:
                return Type(b.ElementType) + "&";
            case PointerType p:
                return Type(p.ElementType) + "*";
            case ArrayType a:
                return Type(a.ElementType) + (a.Rank <= 1 ? "[]" : "[" + new string(',', a.Rank - 1) + "]");
            case GenericInstanceType g:
                return Type(g.ElementType) + "<" + string.Join(",", g.GenericArguments.Select(Type)) + ">";
            case GenericParameter gp:
                return gp.Name;
            case FunctionPointerType f:
                return "method " + f.FullName;
        }

        if (t.DeclaringType != null)
        {
            return Type(t.DeclaringType) + "/" + t.Name;
        }

        return string.IsNullOrEmpty(t.Namespace) ? t.Name : t.Namespace + "." + t.Name;
    }

    /// <summary>Type name with all generic instantiation arguments removed ("A`1&lt;B&gt;" -> "A`1").</summary>
    public static string OpenType(TypeReference t)
    {
        var e = ElementOf(t);
        return Type(e);
    }

    /// <summary>Strips modifiers, arrays, by-ref, pointers and generic instantiation.</summary>
    public static TypeReference ElementOf(TypeReference t)
    {
        while (true)
        {
            switch (t)
            {
                case TypeSpecification spec:
                    t = spec.ElementType;
                    continue;
                default:
                    return t;
            }
        }
    }

    public static string Method(MethodReference r)
    {
        var def = Resolver.Method(r);
        var name = r.Name;
        if (r is GenericInstanceMethod gim)
        {
            name += "<" + string.Join(",", gim.GenericArguments.Select(Type)) + ">";
        }

        IEnumerable<ParameterDefinition> ps = def != null
            ? def.Parameters
            : (r is GenericInstanceMethod g2 ? g2.ElementMethod.Parameters : r.Parameters);
        return Type(r.DeclaringType) + "::" + name + "(" + string.Join(",", ps.Select(p => Type(p.ParameterType))) + ")";
    }

    public static string Field(FieldReference r) => Type(r.DeclaringType) + "::" + r.Name;

    /// <summary>Definition key: open declaring type, no generic method arguments.</summary>
    public static string MethodKey(MethodDefinition d) =>
        Type(d.DeclaringType) + "::" + d.Name + "(" + string.Join(",", d.Parameters.Select(p => Type(p.ParameterType))) + ")";

    public static string FieldKey(FieldDefinition d) => Type(d.DeclaringType) + "::" + d.Name;

    public static string MethodKind(MethodReference r)
    {
        var def = Resolver.Method(r);
        if (def != null)
        {
            return def.IsGetter ? "getter" : "method";
        }

        return r.Name.StartsWith("get_", StringComparison.Ordinal) ? "getter" : "method";
    }

    /// <summary>Short location of a method for violation output: "Type::Method".</summary>
    public static string Location(MethodDefinition m) => Type(m.DeclaringType) + "::" + m.Name;

    /// <summary>Removes balanced "&lt;...&gt;" groups from a type string.</summary>
    public static string StripGenericArgs(string type)
    {
        var sb = new System.Text.StringBuilder(type.Length);
        var depth = 0;
        foreach (var c in type)
        {
            if (c == '<')
            {
                depth++;
                continue;
            }

            if (c == '>')
            {
                depth = Math.Max(0, depth - 1);
                continue;
            }

            if (depth == 0)
            {
                sb.Append(c);
            }
        }

        return sb.ToString();
    }

    /// <summary>
    /// Splits a canonical member string into its parts. Returns false if it is not a member string
    /// (no top-level "::").
    /// </summary>
    public static bool TryParseMember(string member, out ParsedMember parsed)
    {
        parsed = default;
        var depth = 0;
        var split = -1;
        for (var i = 0; i < member.Length - 1; i++)
        {
            var c = member[i];
            if (c == '<')
            {
                depth++;
            }
            else if (c == '>')
            {
                depth--;
            }
            else if (depth == 0 && c == ':' && member[i + 1] == ':')
            {
                split = i;
                break;
            }
        }

        if (split <= 0)
        {
            return false;
        }

        var type = member.Substring(0, split);
        var rest = member.Substring(split + 2);
        string name;
        string? parameters = null;
        var paren = rest.IndexOf('(');
        if (paren >= 0)
        {
            name = rest.Substring(0, paren);
            var close = rest.LastIndexOf(')');
            parameters = close > paren ? rest.Substring(paren + 1, close - paren - 1) : rest.Substring(paren + 1);
        }
        else
        {
            name = rest;
        }

        var bareName = name;
        if (bareName.Length > 0 && bareName[0] != '<' && bareName.EndsWith('>'))
        {
            var lt = bareName.IndexOf('<');
            if (lt > 0)
            {
                bareName = bareName.Substring(0, lt);
            }
        }

        parsed = new ParsedMember(type, StripGenericArgs(type), name, bareName, parameters);
        return true;
    }
}

internal readonly record struct ParsedMember(string Type, string OpenType, string Name, string BareName, string? Parameters)
{
    public bool IsMethod => Parameters != null;

    /// <summary>Definition key for index lookups (open type, bare name).</summary>
    public string Key => IsMethod ? OpenType + "::" + BareName + "(" + Parameters + ")" : OpenType + "::" + BareName;
}

/// <summary>Exception-safe Cecil resolution.</summary>
internal static class Resolver
{
    public static MethodDefinition? Method(MethodReference r)
    {
        try
        {
            return r.Resolve();
        }
        catch
        {
            return null;
        }
    }

    public static FieldDefinition? Field(FieldReference r)
    {
        try
        {
            return r.Resolve();
        }
        catch
        {
            return null;
        }
    }

    public static TypeDefinition? Type(TypeReference r)
    {
        try
        {
            return r.Resolve();
        }
        catch
        {
            return null;
        }
    }
}
