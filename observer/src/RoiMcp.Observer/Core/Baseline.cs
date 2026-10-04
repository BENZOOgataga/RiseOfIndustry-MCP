using System;
using System.Collections.Generic;
using System.Globalization;
using RoiMcp.Observer.Dto;

namespace RoiMcp.Observer.Core
{
    /// <summary>Verified game baseline (PRD 3.1) and observer constants.</summary>
    public static class Baseline
    {
        public const string ObserverVersion = "1.0.0";
        public const string SchemaVersion = "1.0.0";

        public const int Major = 2;
        public const int Minor = 3;
        public const int Revision = 3;
        public const string Suffix = "b";
        public const string Build = "0507";
        public const string Commit = "76359e59644ebf55cafacf9a50b3d19800df22d7";
        public const int SavegameVersion = 2304;
        /// <summary>
        /// UI label correspondence of Max Send / Min Keep (U1): validated in-game by gate E2 (recorded in
        /// docs/VALIDATION-REPORT.md, session D).
        /// </summary>
        public const bool UiLabelValidated = true;

        public const string AssemblySha256 = "D62599EFD0CFCB9F343E7FF74AAC19533F572062B9CECA82E1D3911507D04803";

        public static GameInfoDto Expected()
        {
            return new GameInfoDto
            {
                version = Major + "." + Minor + "." + Revision,
                build = Build + Suffix,
                commit = Commit,
                savegame_version = SavegameVersion,
                assembly_sha256 = AssemblySha256,
                release = "Steam - Public - 2.3.3 : 0507b",
            };
        }

        public static string UtcNow()
        {
            return Utc(DateTime.UtcNow);
        }

        public static string Utc(DateTime t)
        {
            return t.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", CultureInfo.InvariantCulture);
        }

        /// <summary>Absolute game day (TimeManager._days + 1 == GameDate.CountDays()) to Y&lt;year&gt;-MM-DD.</summary>
        public static string GameDate(int year, int month, int day)
        {
            return "Y" + year.ToString(CultureInfo.InvariantCulture) + "-" +
                   month.ToString("00", CultureInfo.InvariantCulture) + "-" +
                   day.ToString("00", CultureInfo.InvariantCulture);
        }

        public static string GameMonth(int year, int month)
        {
            return "Y" + year.ToString(CultureInfo.InvariantCulture) + "-" + month.ToString("00", CultureInfo.InvariantCulture);
        }

        /// <summary>Inverse of GameDate.CountDays(): day count 1 = Y1-01-01.</summary>
        public static void FromDayCount(int count, out int year, out int month, out int day)
        {
            int zero = count - 1;
            if (zero < 0) zero = 0;
            year = zero / 360 + 1;
            month = (zero % 360) / 30 + 1;
            day = zero % 30 + 1;
        }

        public static int DayCount(int year, int month, int day)
        {
            return 360 * year + 30 * month + day - 390;
        }

        public static readonly IList<string> RequiredStateSections = new[]
        {
            "session", "companies", "buildings_player", "routes_player", "requests_player", "buildings_ai",
            "shops", "cities", "regions", "market", "research", "vehicles",
        };

        public static readonly IList<string> OptionalStateSections = new[]
        {
            "buildings_ai_detail", "routes_ai", "route_paths",
        };
    }
}
