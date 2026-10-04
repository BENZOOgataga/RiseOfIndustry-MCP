using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using Xunit;

namespace RoiMcp.Observer.Tests
{
    public class SchemaTests
    {
        [Fact]
        public void SchemasAreUpToDate()
        {
            var dir = Path.Combine(SchemaGenerator.RepoRoot(), "schemas");
            var update = Environment.GetEnvironmentVariable("ROI_UPDATE_SCHEMAS") == "1";
            foreach (var kv in SchemaGenerator.GenerateAll())
            {
                var path = Path.Combine(dir, kv.Key);
                var text = SchemaGenerator.Serialize(kv.Value);
                if (update)
                {
                    Directory.CreateDirectory(dir);
                    File.WriteAllText(path, text);
                    continue;
                }
                Assert.True(File.Exists(path), "missing " + path + " (run tests with ROI_UPDATE_SCHEMAS=1)");
                Assert.True(File.ReadAllText(path).Replace("\r\n", "\n") == text,
                    kv.Key + " is out of date with the DTOs (run tests with ROI_UPDATE_SCHEMAS=1)");
            }
        }

        [Fact]
        public void EveryPropertyMapsToDataMapIdOrIsEnvelope()
        {
            // T-4: every schema property carries x-data-map-id, inherits one from its enclosing object,
            // or belongs to an envelope object.
            foreach (var kv in SchemaGenerator.GenerateAll())
            {
                var defs = (JObject)kv.Value["$defs"];
                foreach (var def in defs)
                {
                    var o = (JObject)def.Value;
                    if (o["x-envelope"] != null) continue;
                    if (o["x-data-map-id"] != null) continue;
                    foreach (var p in (JObject)o["properties"])
                        Assert.True(p.Value["x-data-map-id"] != null, kv.Key + ": " + def.Key + "." + p.Key + " has no x-data-map-id");
                }
            }
        }

        [Fact]
        public void DataMapIdsExistInResearchDataMap()
        {
            var dm = JObject.Parse(File.ReadAllText(Path.Combine(SchemaGenerator.RepoRoot(), "research", "data-map.json")));
            var ids = dm["categories"].SelectMany(c => c["entries"]).Select(e => (string)e["id"]).ToList();
            foreach (var kv in SchemaGenerator.GenerateAll())
                foreach (var t in kv.Value.SelectTokens("$..x-data-map-id"))
                    Assert.Contains((string)t, ids);
        }
    }
}
