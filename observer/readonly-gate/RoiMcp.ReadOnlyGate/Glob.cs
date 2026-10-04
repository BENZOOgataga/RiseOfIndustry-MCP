using System.Collections.Concurrent;
using System.Text;
using System.Text.RegularExpressions;

namespace RoiMcp.ReadOnlyGate;

/// <summary>
/// Case-sensitive glob matching. '*' matches any run of characters (including '.', '/', ':'),
/// '?' matches exactly one character. Every other character is literal.
/// </summary>
internal static class Glob
{
    private static readonly ConcurrentDictionary<string, Regex> Cache = new();

    public static bool HasWildcard(string pattern) => pattern.IndexOf('*') >= 0 || pattern.IndexOf('?') >= 0;

    public static bool IsMatch(string pattern, string value)
    {
        if (!HasWildcard(pattern))
        {
            return string.Equals(pattern, value, StringComparison.Ordinal);
        }

        var regex = Cache.GetOrAdd(pattern, Build);
        return regex.IsMatch(value);
    }

    private static Regex Build(string pattern)
    {
        var sb = new StringBuilder("^");
        foreach (var c in pattern)
        {
            switch (c)
            {
                case '*':
                    sb.Append(".*");
                    break;
                case '?':
                    sb.Append('.');
                    break;
                default:
                    sb.Append(Regex.Escape(c.ToString()));
                    break;
            }
        }

        sb.Append('$');
        return new Regex(sb.ToString(), RegexOptions.CultureInvariant | RegexOptions.Singleline);
    }
}
