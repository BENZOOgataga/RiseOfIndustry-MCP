using System.Collections.Generic;
using RoiMcp.Observer.Core;
using RoiMcp.Observer.Dto;

namespace RoiMcp.Observer.Publish
{
    /// <summary>
    /// Size caps (PRD 11.3) and the documented drop order applied when a file would exceed its cap.
    /// Sections are dropped whole (status "skipped", reason "size_cap"); a truncated file is never written.
    /// </summary>
    public static class SizeCapPolicy
    {
        public const int HeartbeatCap = 16 * 1024;
        public const int StaticCap = 4 * 1024 * 1024;
        public const int StateCap = 5 * 1024 * 1024;
        public const int HistoryCap = 5 * 1024 * 1024;

        /// <summary>Drop order for state.json: optional sections first, then the largest AI-side data.</summary>
        public static readonly string[] StateDropOrder = { "route_paths", "routes_ai", "buildings_ai_detail", "buildings_ai" };

        /// <summary>Drop order for history.json.</summary>
        public static readonly string[] HistoryDropOrder = { "shops_monthly", "production_monthly_player", "buildings_monthly_player" };

        public static int Cap(CaptureFamily f)
        {
            switch (f)
            {
                case CaptureFamily.State: return StateCap;
                case CaptureFamily.History: return HistoryCap;
                default: return StaticCap;
            }
        }

        public static string[] DropOrder(CaptureFamily f)
        {
            switch (f)
            {
                case CaptureFamily.State: return StateDropOrder;
                case CaptureFamily.History: return HistoryDropOrder;
                default: return new string[0];
            }
        }

        /// <summary>Nulls one section of the data object. Returns false when nothing was dropped.</summary>
        public static bool Drop(object data, string section)
        {
            var s = data as StateData;
            if (s != null)
            {
                switch (section)
                {
                    case "route_paths": if (s.route_paths == null) return false; s.route_paths = null; return true;
                    case "routes_ai": if (s.routes_ai == null) return false; s.routes_ai = null; return true;
                    case "buildings_ai_detail": if (s.buildings_ai_detail == null) return false; s.buildings_ai_detail = null; return true;
                    case "buildings_ai": if (s.buildings_ai == null) return false; s.buildings_ai = null; return true;
                }
                return false;
            }
            var h = data as HistoryData;
            if (h != null)
            {
                switch (section)
                {
                    case "shops_monthly": if (h.shops_monthly == null) return false; h.shops_monthly = null; return true;
                    case "production_monthly_player": if (h.production_monthly_player == null) return false; h.production_monthly_player = null; return true;
                    case "buildings_monthly_player": if (h.buildings_monthly_player == null) return false; h.buildings_monthly_player = null; return true;
                }
            }
            return false;
        }

        public static void MarkDropped(Dictionary<string, SectionStatusDto> sections, List<WarningDto> warnings, string section, long size)
        {
            SectionStatusDto st;
            if (sections != null && sections.TryGetValue(section, out st))
            {
                st.status = "skipped";
                st.reason = "size_cap";
            }
            warnings.Add(new WarningDto { code = "section_dropped_size_cap", detail = section + " (file was " + size + " bytes)" });
        }
    }
}
