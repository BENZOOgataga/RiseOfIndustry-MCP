namespace RoiMcp.ReadOnlyGate;

/// <summary>Verifies that every denylist entry matches at least one type or member of the baseline game assemblies.</summary>
internal static class DenylistCheck
{
    public static List<(DenyEntry Entry, int Matches)> Run(GameEnvironment env, Denylist deny)
    {
        var index = new GameIndex(env);
        var result = new List<(DenyEntry, int)>();
        foreach (var entry in deny.Entries)
        {
            var pattern = DenyPattern.Parse(entry.Member);
            var count = 0;
            foreach (var t in index.Types)
            {
                var name = Names.Type(t);
                var outer = Denylist.OuterTypesOf(name);
                if (pattern.IsTypePattern)
                {
                    if (pattern.MatchesType(name, name, outer))
                    {
                        count++;
                    }

                    continue;
                }

                foreach (var m in t.Methods)
                {
                    var nameAndParams = m.Name + "(" + string.Join(",", m.Parameters.Select(p => Names.Type(p.ParameterType))) + ")";
                    if (pattern.MatchesMember(name, name, outer, m.Name, nameAndParams))
                    {
                        count++;
                    }
                }

                foreach (var f in t.Fields)
                {
                    if (pattern.MatchesMember(name, name, outer, f.Name, f.Name))
                    {
                        count++;
                    }
                }
            }

            result.Add((entry, count));
        }

        return result;
    }
}
