using System;
using System.Collections.Generic;
using System.Globalization;

namespace RoiMcp.Observer.Capture
{
    /// <summary>
    /// Building keys (PRD 7.1, 7.2): "&lt;prefab&gt;@&lt;x&gt;,&lt;y&gt;". Uniqueness is asserted on every capture pass; colliding
    /// keys get "#&lt;first 8 hex chars of the save GUID&gt;" or "#i&lt;instanceId&gt;" when no GUID exists.
    /// Base keys are cached per world session by Unity instance id to avoid re-allocating strings.
    /// </summary>
    public sealed class BuildingKeys
    {
        private readonly Dictionary<int, string> _base = new Dictionary<int, string>();
        private readonly Dictionary<int, string> _final = new Dictionary<int, string>();
        private readonly Dictionary<string, int> _firstOwner = new Dictionary<string, int>(StringComparer.Ordinal);
        private readonly Dictionary<int, string> _guidOf = new Dictionary<int, string>();
        private readonly HashSet<string> _collidingBase = new HashSet<string>(StringComparer.Ordinal);
        private readonly HashSet<string> _loggedCollisions = new HashSet<string>(StringComparer.Ordinal);

        /// <summary>Number of building keys that needed a suffix in the last completed pass.</summary>
        public int Collisions { get; private set; }

        /// <summary>Collisions newly detected (for one-time logging).</summary>
        public List<string> NewCollisions = new List<string>();

        public static string BaseKey(string prefab, int x, int y)
        {
            return (prefab ?? "unknown") + "@" + x.ToString(CultureInfo.InvariantCulture) + "," + y.ToString(CultureInfo.InvariantCulture);
        }

        public void Clear()
        {
            _base.Clear();
            _final.Clear();
            _firstOwner.Clear();
            _guidOf.Clear();
            _collidingBase.Clear();
            _loggedCollisions.Clear();
            Collisions = 0;
        }

        public bool HasBase(int instanceId)
        {
            return _base.ContainsKey(instanceId);
        }

        public void SetBase(int instanceId, string prefab, int x, int y)
        {
            _base[instanceId] = BaseKey(prefab, x, y);
        }

        public void SetGuid(int instanceId, string guid)
        {
            if (guid != null) _guidOf[instanceId] = guid;
        }

        private int _passCollisions;

        public void BeginPass()
        {
            _firstOwner.Clear();
            _collidingBase.Clear();
            _final.Clear();
            NewCollisions.Clear();
            _passCollisions = 0;
        }

        /// <summary>Registers one building of the current pass.</summary>
        public void Register(int instanceId)
        {
            string b;
            if (!_base.TryGetValue(instanceId, out b)) return;
            int other;
            if (_firstOwner.TryGetValue(b, out other))
            {
                if (other != instanceId)
                {
                    // First collision on this base key counts both buildings, later ones one each.
                    _passCollisions += _collidingBase.Add(b) ? 2 : 1;
                    if (_loggedCollisions.Add(b)) NewCollisions.Add(b);
                }
            }
            else
            {
                _firstOwner[b] = instanceId;
            }
        }

        /// <summary>Ends the pass: publishes the collision count. Final keys are resolved lazily by KeyOf.</summary>
        public void EndPass()
        {
            Collisions = _passCollisions;
        }

        /// <summary>Final key of a building (falls back to the base key for buildings created after the pass).</summary>
        public string KeyOf(int instanceId)
        {
            string k;
            if (_final.TryGetValue(instanceId, out k)) return k;
            string b;
            if (!_base.TryGetValue(instanceId, out b)) return null;
            if (!_collidingBase.Contains(b)) return b;
            string g;
            k = b + (_guidOf.TryGetValue(instanceId, out g) && g.Length >= 8
                ? "#" + g.Replace("-", "").Substring(0, 8)
                : "#i" + instanceId.ToString(CultureInfo.InvariantCulture));
            _final[instanceId] = k;
            return k;
        }
    }
}
