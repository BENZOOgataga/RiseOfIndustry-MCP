using System;
using System.Collections.Generic;
using RoiMcp.Observer.Dto;

namespace RoiMcp.Observer.Core
{
    /// <summary>Raw version facts read from the GameVersion asset (no interpretation).</summary>
    public sealed class VersionFacts
    {
        public int Major;
        public int Minor;
        public int Revision;
        public string Suffix;
        public string Build;
        public string CommitHash;
        public int SavegameVersion;
        public string Release;
        public bool Found;
    }

    public sealed class VersionGateResult
    {
        public bool Compatible;
        public List<string> Mismatches = new List<string>();
        public GameInfoDto Detected;
    }

    /// <summary>
    /// Compatibility gate (PRD 3.2). Any difference from the baseline means unsupported_build for the rest of
    /// the process lifetime. There is intentionally no override input.
    /// </summary>
    public static class VersionGate
    {
        public static GameInfoDto Describe(VersionFacts f, string sha256)
        {
            if (f == null || !f.Found)
                return new GameInfoDto { version = null, build = null, commit = null, savegame_version = 0, assembly_sha256 = sha256 };
            return new GameInfoDto
            {
                version = f.Major + "." + f.Minor + "." + f.Revision,
                build = (f.Build ?? "") + (f.Suffix ?? ""),
                commit = f.CommitHash,
                savegame_version = f.SavegameVersion,
                assembly_sha256 = sha256,
                release = f.Release,
            };
        }

        public static VersionGateResult Evaluate(VersionFacts f, string sha256)
        {
            var r = new VersionGateResult { Detected = Describe(f, sha256) };
            if (f == null || !f.Found)
            {
                r.Mismatches.Add("game_version_unreadable");
            }
            else
            {
                if (f.Major != Baseline.Major || f.Minor != Baseline.Minor || f.Revision != Baseline.Revision)
                    r.Mismatches.Add("version");
                if (!string.Equals(f.Suffix, Baseline.Suffix, StringComparison.Ordinal) ||
                    !string.Equals(f.Build, Baseline.Build, StringComparison.Ordinal))
                    r.Mismatches.Add("build");
                if (!string.Equals(f.CommitHash, Baseline.Commit, StringComparison.OrdinalIgnoreCase))
                    r.Mismatches.Add("commit");
                if (f.SavegameVersion != Baseline.SavegameVersion)
                    r.Mismatches.Add("savegame_version");
            }
            if (sha256 == null || !string.Equals(sha256, Baseline.AssemblySha256, StringComparison.OrdinalIgnoreCase))
                r.Mismatches.Add("assembly_sha256");
            r.Compatible = r.Mismatches.Count == 0;
            return r;
        }
    }
}
