using System;
using System.Collections.Generic;
using ProjectAutomata;
using RoiMcp.Observer.Capture;
using RoiMcp.Observer.Core;

namespace RoiMcp.Observer.ReadLayer
{
    /// <summary>A section backed by an iterator method.</summary>
    internal sealed class Section : ISection
    {
        private readonly Func<SectionContext, IEnumerator<bool>> _run;

        public Section(string name, bool optional, Func<SectionContext, IEnumerator<bool>> run)
        {
            Name = name;
            Optional = optional;
            _run = run;
        }

        public string Name { get; private set; }
        public bool Optional { get; private set; }

        public IEnumerator<bool> Run(SectionContext ctx)
        {
            return _run(ctx);
        }
    }

    /// <summary>
    /// Per-world-session caches of the read layer: asset names, building keys and kinds, region ids.
    /// Cleared when the world unloads so no game object is retained across sessions.
    /// </summary>
    public sealed class WorldContext
    {
        private readonly Dictionary<int, string> _assetNames = new Dictionary<int, string>();
        private readonly Dictionary<int, string> _kinds = new Dictionary<int, string>();
        private readonly Dictionary<int, List<string>> _tagsByPrefab = new Dictionary<int, List<string>>();
        private readonly Dictionary<int, string> _regionIds = new Dictionary<int, string>();

        public readonly BuildingKeys Keys = new BuildingKeys();
        public readonly List<int> IndexIds = new List<int>(4096);
        public string WorldSession { get; private set; }

        public void Begin(string worldSession)
        {
            Clear();
            WorldSession = worldSession;
        }

        public void Clear()
        {
            _assetNames.Clear();
            _kinds.Clear();
            _tagsByPrefab.Clear();
            _regionIds.Clear();
            Keys.Clear();
            IndexIds.Clear();
            WorldSession = null;
        }

        /// <summary>Asset name (stable id) of a definition, cached by instance id (Object.name allocates).</summary>
        public string Name(UnityEngine.Object asset)
        {
            if (asset == null) return null;
            int id = asset.GetInstanceID();
            string n;
            if (_assetNames.TryGetValue(id, out n)) return n;
            n = asset.name;
            _assetNames[id] = n;
            return n;
        }

        public static void Coords(int tile, out int x, out int y)
        {
            int size = World.Size;
            if (size <= 0)
            {
                x = tile;
                y = 0;
                return;
            }
            x = tile % size;
            y = tile / size;
        }

        /// <summary>Building key; registers the base key on first sight.</summary>
        public string Key(Building b)
        {
            if (b == null) return null;
            int id = b.GetInstanceID();
            if (!Keys.HasBase(id))
            {
                int x, y;
                Coords(b.tile, out x, out y);
                var prefab = b.prefab;
                Keys.SetBase(id, prefab != null ? Name(prefab) : null, x, y);
            }
            return Keys.KeyOf(id);
        }

        public string Key(BuildingLogistics l)
        {
            if (l == null) return null;
            return Key(l.building);
        }

        public static int ActorId(IActor a)
        {
            return a != null ? a.id : -1;
        }

        public static int? CityId(SettlementBase s)
        {
            if (s == null) return null;
            return s.id;
        }

        public string RegionId(Region r)
        {
            if (r == null) return null;
            int id = r.GetInstanceID();
            string s;
            if (_regionIds.TryGetValue(id, out s)) return s;
            s = r.id.ToString();
            _regionIds[id] = s;
            return s;
        }

        /// <summary>Tag asset names of a building, cached per prefab.</summary>
        public List<string> Tags(Building b)
        {
            var prefab = b.prefab != null ? b.prefab : b;
            int id = prefab.GetInstanceID();
            List<string> tags;
            if (_tagsByPrefab.TryGetValue(id, out tags)) return tags;
            tags = new List<string>();
            var arr = prefab.tags;
            if (arr != null)
            {
                for (int i = 0; i < arr.Length; i++)
                {
                    if (arr[i] != null) tags.Add(Name(arr[i]));
                }
            }
            _tagsByPrefab[id] = tags;
            return tags;
        }

        /// <summary>Kind used by the MCP filters, cached per building.</summary>
        public string Kind(Building b)
        {
            int id = b.GetInstanceID();
            string k;
            if (_kinds.TryGetValue(id, out k)) return k;
            k = ComputeKind(b);
            _kinds[id] = k;
            return k;
        }

        private string ComputeKind(Building b)
        {
            if (b.GetComponent<Headquarters>() != null) return "hq";
            var harvester = b.harvester;
            if (harvester != null) return harvester is Field ? "field" : "harvester";
            var tags = Tags(b);
            if (tags.Contains("Shop")) return "shop";
            if (tags.Contains("Farm")) return "farm";
            if (tags.Contains("Gatherer")) return "gatherer";
            if (tags.Contains("Factory")) return "factory";
            if (b.GetComponent<Warehouse>() != null) return "warehouse";
            if (tags.Contains("Logistics")) return "depot";
            return "other";
        }

        /// <summary>Destination kind for a route (PRD 12.3).</summary>
        public string DestinationKind(Building b)
        {
            if (b == null) return "other";
            if (b.GetComponent<StateTradingHandler>() != null) return "state_trading";
            if (b.GetComponent<Wholesaler>() != null) return "wholesaler";
            if (b.GetComponent<ContractsTarget>() != null) return "contract_target";
            switch (Kind(b))
            {
                case "shop": return "shop";
                case "warehouse": return "warehouse";
                case "factory": return "factory";
                case "gatherer": return "gatherer";
                case "farm": return "farm";
                default: return "other";
            }
        }
    }
}
