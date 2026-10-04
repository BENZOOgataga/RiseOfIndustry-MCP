using System;
using System.Collections.Generic;
using System.Diagnostics;

namespace RoiMcp.Observer.Core
{
    /// <summary>
    /// Fault injection points for validation gate E5. Calls are removed by the compiler unless the build
    /// defines DEBUG_FAULTS, so release builds contain no fault-injection code (checked by gate rule G10
    /// through the DebugFaults marker type).
    /// </summary>
    public static class FaultPoint
    {
        [Conditional("DEBUG_FAULTS")]
        public static void Hit(string name)
        {
#if DEBUG_FAULTS
            if (DebugFaults.IsActive(name)) throw new InvalidOperationException("DEBUG_FAULTS injected fault: " + name);
#endif
        }
    }
}

#if DEBUG_FAULTS
namespace RoiMcp.Observer
{
    /// <summary>Marker type: present only in DEBUG_FAULTS builds (never released).</summary>
    public static class DebugFaults
    {
        private static readonly object Sync = new object();
        private static Dictionary<string, bool> _active = new Dictionary<string, bool>(StringComparer.Ordinal);

        public static void Set(Dictionary<string, bool> faults)
        {
            lock (Sync) _active = new Dictionary<string, bool>(faults, StringComparer.Ordinal);
        }

        public static bool IsActive(string name)
        {
            lock (Sync)
            {
                bool v;
                return _active.TryGetValue(name, out v) && v;
            }
        }
    }
}
#endif
