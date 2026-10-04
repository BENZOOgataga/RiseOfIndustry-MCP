using System;
using System.Collections.Generic;
using System.Globalization;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace RoiMcp.Observer.Core
{
    /// <summary>
    /// observer.config.json (PRD 8.7). Out-of-range values are clamped, unknown keys ignored, both with a
    /// warning. Deliberately no key influences the compatibility gate (PRD 3.2).
    /// </summary>
    public sealed class ObserverConfig
    {
        public bool Enabled = true;
        public double RunningIntervalS = 5;
        public double PausedIntervalS = 15;
        public double MinGapS = 1;
        public double FrameBudgetMs = 2.0;
        public double CaptureCeilingMs = 50;
        public bool IncludeAiBuildingDetail;
        public bool IncludeAiRoutes;
        public bool IncludeRoutePaths;
        public string LogLevel = "info";
        public List<string> Warnings = new List<string>();
        public Dictionary<string, bool> DebugFaults = new Dictionary<string, bool>(StringComparer.Ordinal);

        public const int MaxConfigBytes = 64 * 1024;

        public static ObserverConfig Default()
        {
            return new ObserverConfig();
        }

        public ObserverConfig Clone()
        {
            var c = (ObserverConfig)MemberwiseClone();
            c.Warnings = new List<string>(Warnings);
            c.DebugFaults = new Dictionary<string, bool>(DebugFaults, StringComparer.Ordinal);
            return c;
        }

        /// <summary>Parses config text. Never throws; malformed content yields defaults plus a warning.</summary>
        public static ObserverConfig Parse(string text)
        {
            var c = new ObserverConfig();
            if (text == null) return c;
            if (text.Length > MaxConfigBytes)
            {
                c.Warnings.Add("config_too_large_ignored");
                return c;
            }
            JObject o;
            try
            {
                var token = JToken.Parse(text);
                o = token as JObject;
                if (o == null)
                {
                    c.Warnings.Add("config_not_an_object_ignored");
                    return c;
                }
            }
            catch (JsonException)
            {
                c.Warnings.Add("config_malformed_ignored");
                return c;
            }

            foreach (var p in o.Properties())
            {
                switch (p.Name)
                {
                    case "enabled": c.Enabled = ReadBool(p, c.Enabled, c.Warnings); break;
                    case "running_interval_s": c.RunningIntervalS = ReadNumber(p, c.RunningIntervalS, 2, 300, c.Warnings); break;
                    case "paused_interval_s": c.PausedIntervalS = ReadNumber(p, c.PausedIntervalS, 5, 600, c.Warnings); break;
                    case "min_gap_s": c.MinGapS = ReadNumber(p, c.MinGapS, 1, 60, c.Warnings); break;
                    case "frame_budget_ms": c.FrameBudgetMs = ReadNumber(p, c.FrameBudgetMs, 0.5, 5.0, c.Warnings); break;
                    case "capture_ceiling_ms": c.CaptureCeilingMs = ReadNumber(p, c.CaptureCeilingMs, 10, 200, c.Warnings); break;
                    case "include_ai_building_detail": c.IncludeAiBuildingDetail = ReadBool(p, c.IncludeAiBuildingDetail, c.Warnings); break;
                    case "include_ai_routes": c.IncludeAiRoutes = ReadBool(p, c.IncludeAiRoutes, c.Warnings); break;
                    case "include_route_paths": c.IncludeRoutePaths = ReadBool(p, c.IncludeRoutePaths, c.Warnings); break;
                    case "log_level":
                        var s = p.Value.Type == JTokenType.String ? (string)p.Value : null;
                        if (s == "error" || s == "warn" || s == "info" || s == "debug") c.LogLevel = s;
                        else c.Warnings.Add("invalid_value:log_level");
                        break;
#if DEBUG_FAULTS
                    case "debug_faults":
                        var faults = p.Value as JObject;
                        if (faults != null)
                            foreach (var f in faults.Properties())
                                if (f.Value.Type == JTokenType.Boolean) c.DebugFaults[f.Name] = (bool)f.Value;
                        break;
#endif
                    default:
                        c.Warnings.Add("unknown_key_ignored:" + Truncate(p.Name, 64));
                        break;
                }
            }
            return c;
        }

        private static string Truncate(string s, int n)
        {
            return s.Length <= n ? s : s.Substring(0, n);
        }

        private static bool ReadBool(JProperty p, bool fallback, List<string> warnings)
        {
            if (p.Value.Type == JTokenType.Boolean) return (bool)p.Value;
            warnings.Add("invalid_value:" + p.Name);
            return fallback;
        }

        private static double ReadNumber(JProperty p, double fallback, double min, double max, List<string> warnings)
        {
            if (p.Value.Type != JTokenType.Integer && p.Value.Type != JTokenType.Float)
            {
                warnings.Add("invalid_value:" + p.Name);
                return fallback;
            }
            double v = (double)p.Value;
            if (double.IsNaN(v) || double.IsInfinity(v))
            {
                warnings.Add("invalid_value:" + p.Name);
                return fallback;
            }
            if (v < min)
            {
                warnings.Add("clamped:" + p.Name + "=" + min.ToString(CultureInfo.InvariantCulture));
                return min;
            }
            if (v > max)
            {
                warnings.Add("clamped:" + p.Name + "=" + max.ToString(CultureInfo.InvariantCulture));
                return max;
            }
            return v;
        }
    }
}
