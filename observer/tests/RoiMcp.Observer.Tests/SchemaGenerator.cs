using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using RoiMcp.Observer.Dto;

namespace RoiMcp.Observer.Tests
{
    /// <summary>
    /// Generates the JSON Schemas (2020-12) for the observer-written files from the DTO classes, so the wire
    /// format cannot drift from the code. The committed files in schemas/ are the contract; the test
    /// SchemasAreUpToDate fails when they differ (set ROI_UPDATE_SCHEMAS=1 to rewrite them).
    /// </summary>
    public static class SchemaGenerator
    {
        public const string SchemaVersion = "1.0.0";

        public static Dictionary<string, JObject> GenerateAll()
        {
            return new Dictionary<string, JObject>
            {
                { "heartbeat.schema.json", FileSchema("heartbeat", typeof(HeartbeatData), false) },
                { "static.schema.json", FileSchema("static", typeof(StaticData), true) },
                { "state.schema.json", FileSchema("state", typeof(StateData), true) },
                { "history.schema.json", FileSchema("history", typeof(HistoryData), true) },
            };
        }

        private static JObject FileSchema(string family, Type dataType, bool snapshot)
        {
            var defs = new SortedDictionary<string, JObject>(StringComparer.Ordinal);
            var envelope = ObjectSchema(typeof(EnvelopeDto), defs);
            var props = (JObject)envelope["properties"];
            props["schema"] = new JObject { ["const"] = "roi-mcp/" + family };
            props["schema_version"] = new JObject { ["type"] = "string", ["pattern"] = "^1\\.[0-9]+\\.[0-9]+$" };
            if (snapshot)
            {
                props["compatibility"] = new JObject { ["const"] = "verified" };
                props["world_session"] = new JObject { ["type"] = "string" };
                props["captured"] = Ref(typeof(CapturedDto), defs);
                props["sections"] = new JObject
                {
                    ["type"] = "object",
                    ["additionalProperties"] = Ref(typeof(SectionStatusDto), defs),
                };
                props["content_hash"] = new JObject { ["type"] = "string" };
            }
            if (family == "state" || family == "history")
            {
                props["static_ref"] = Ref(typeof(StaticRefDto), defs);
            }
            props["data"] = Ref(dataType, defs);
            ((JArray)envelope["required"]).Add("data");

            var root = new JObject
            {
                ["$schema"] = "https://json-schema.org/draft/2020-12/schema",
                ["$id"] = "https://roi-mcp.local/schemas/" + family + ".schema.json",
                ["title"] = "Rise of Industry MCP " + family + ".json",
                ["x-schema-version"] = SchemaVersion,
                ["description"] = "Generated from observer/src/RoiMcp.Observer/Dto by observer/tests/RoiMcp.Observer.Tests/SchemaGenerator.cs. Do not edit by hand.",
            };
            foreach (var p in envelope) root[p.Key] = p.Value;
            var defsObj = new JObject();
            foreach (var kv in defs) defsObj[kv.Key] = kv.Value;
            root["$defs"] = defsObj;
            return root;
        }

        private static JObject Ref(Type t, SortedDictionary<string, JObject> defs)
        {
            if (!defs.ContainsKey(t.Name))
            {
                defs[t.Name] = null; // reserve against recursion
                defs[t.Name] = ObjectSchema(t, defs);
            }
            return new JObject { ["$ref"] = "#/$defs/" + t.Name };
        }

        private static JObject ObjectSchema(Type t, SortedDictionary<string, JObject> defs)
        {
            var o = new JObject { ["type"] = "object", ["additionalProperties"] = false };
            var doc = t.GetCustomAttribute<DocAttribute>();
            if (doc != null) o["description"] = doc.Text;
            var dm = t.GetCustomAttribute<DataMapAttribute>();
            if (dm != null) o["x-data-map-id"] = dm.Id;
            var src = t.GetCustomAttribute<SourceAttribute>();
            if (src != null) o["x-source"] = src.Value;
            if (t.GetCustomAttribute<EnvelopeAttribute>() != null) o["x-envelope"] = true;

            var props = new JObject();
            var required = new JArray();
            foreach (var f in t.GetFields(BindingFlags.Public | BindingFlags.Instance).OrderBy(f => f.MetadataToken))
            {
                var nullable = f.GetCustomAttribute<NullableAttribute>() != null;
                var ps = TypeSchema(f.FieldType, defs, nullable);
                if (f.GetCustomAttribute<NullableItemsAttribute>() != null)
                {
                    var ft = f.FieldType;
                    var elem = ft.IsArray ? ft.GetElementType() : ft.GetGenericArguments()[0];
                    ps["items"] = TypeSchema(elem, defs, true);
                }
                var fdoc = f.GetCustomAttribute<DocAttribute>();
                if (fdoc != null) ps["description"] = fdoc.Text;
                var fdm = f.GetCustomAttribute<DataMapAttribute>();
                if (fdm != null) ps["x-data-map-id"] = fdm.Id;
                var fsrc = f.GetCustomAttribute<SourceAttribute>();
                if (fsrc != null) ps["x-source"] = fsrc.Value;
                var en = f.GetCustomAttribute<EnumValuesAttribute>();
                if (en != null)
                {
                    var arr = new JArray(en.Values);
                    if (nullable) arr.Add(JValue.CreateNull());
                    ps["enum"] = arr;
                }
                props[JsonName(f)] = ps;
                required.Add(JsonName(f));
            }
            o["properties"] = props;
            o["required"] = required;
            return o;
        }

        private static string JsonName(FieldInfo f)
        {
            var a = f.GetCustomAttribute<JsonPropertyAttribute>();
            return a != null && a.PropertyName != null ? a.PropertyName : f.Name;
        }

        private static JObject TypeSchema(Type t, SortedDictionary<string, JObject> defs, bool nullable)
        {
            var under = Nullable.GetUnderlyingType(t);
            if (under != null) { t = under; nullable = true; }

            JObject s;
            if (t == typeof(string)) s = Simple("string", nullable);
            else if (t == typeof(int) || t == typeof(long) || t == typeof(short) || t == typeof(ushort)) s = Simple("integer", nullable);
            else if (t == typeof(float) || t == typeof(double)) s = Simple("number", nullable);
            else if (t == typeof(bool)) s = Simple("boolean", nullable);
            else if (t.IsArray)
            {
                s = Simple("array", nullable);
                s["items"] = TypeSchema(t.GetElementType(), defs, false);
            }
            else if (t.IsGenericType && t.GetGenericTypeDefinition() == typeof(List<>))
            {
                s = Simple("array", nullable);
                var elem = t.GetGenericArguments()[0];
                s["items"] = TypeSchema(elem, defs, Nullable.GetUnderlyingType(elem) != null);
            }
            else if (t.IsGenericType && t.GetGenericTypeDefinition() == typeof(Dictionary<,>))
            {
                s = Simple("object", nullable);
                s["additionalProperties"] = TypeSchema(t.GetGenericArguments()[1], defs, false);
            }
            else if (t.IsClass)
            {
                var r = Ref(t, defs);
                if (!nullable) return r;
                return new JObject { ["anyOf"] = new JArray(r, new JObject { ["type"] = "null" }) };
            }
            else throw new InvalidOperationException("Unsupported DTO field type " + t);
            return s;
        }

        private static JObject Simple(string type, bool nullable)
        {
            return nullable
                ? new JObject { ["type"] = new JArray(type, "null") }
                : new JObject { ["type"] = type };
        }

        public static string Serialize(JObject o)
        {
            return o.ToString(Formatting.Indented).Replace("\r\n", "\n") + "\n";
        }

        public static string RepoRoot()
        {
            var dir = new DirectoryInfo(AppDomain.CurrentDomain.BaseDirectory);
            while (dir != null && !File.Exists(Path.Combine(dir.FullName, "PRD.md"))) dir = dir.Parent;
            if (dir == null) throw new InvalidOperationException("repository root not found");
            return dir.FullName;
        }
    }
}
