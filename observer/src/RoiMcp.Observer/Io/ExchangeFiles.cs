using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

namespace RoiMcp.Observer.Io
{
    /// <summary>
    /// The only file-system access of the observer (gate rule G9). Every write targets the exchange
    /// directory; the only file read outside it is Assembly-CSharp.dll for the version hash.
    /// </summary>
    public sealed class ExchangeFiles
    {
        public const string Heartbeat = "heartbeat.json";
        public const string Static = "static.json";
        public const string State = "state.json";
        public const string History = "history.json";
        public const string RefreshRequest = "refresh-request.json";
        public const string Config = "observer.config.json";
        public const string KillSwitch = "observer.disabled";
        public const string LogName = "observer.log";
        public const string LogOldName = "observer.log.1";
        public const long LogRotateBytes = 1024 * 1024;

        private static readonly int[] RetryDelaysMs = { 100, 200, 400 };
        private readonly string _tempSuffix;

        public string Dir { get; private set; }

        public ExchangeFiles(string dir, int pid)
        {
            Dir = dir;
            _tempSuffix = ".tmp-" + pid;
        }

        /// <summary>%LOCALAPPDATA%\RoiMcp, or ROI_MCP_EXCHANGE_DIR when set (PRD 11.1).</summary>
        /// <summary>
        /// Returns null when no absolute directory can be determined: a relative path would resolve against the
        /// game's working directory (its install folder), where the observer must never write (PRD 11.1, 18).
        /// </summary>
        public static string ResolveDir()
        {
            var overrideDir = Environment.GetEnvironmentVariable("ROI_MCP_EXCHANGE_DIR");
            string dir = !string.IsNullOrEmpty(overrideDir)
                ? overrideDir
                : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RoiMcp");
            if (string.IsNullOrEmpty(dir) || !Path.IsPathRooted(dir)) return null;
            return Path.GetFullPath(dir);
        }

        public static string CurrentDirectory()
        {
            return Environment.CurrentDirectory;
        }

        public bool EnsureDir()
        {
            try
            {
                Directory.CreateDirectory(Dir);
                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }

        /// <summary>Deletes leftover temp files of this observer's naming pattern (PRD 11.3).</summary>
        public int CleanupTemps()
        {
            int n = 0;
            try
            {
                foreach (var f in Directory.GetFiles(Dir, "*.json.tmp-*"))
                {
                    try
                    {
                        File.Delete(f);
                        n++;
                    }
                    catch (Exception)
                    {
                        // another process may hold it; try again next start
                    }
                }
            }
            catch (Exception)
            {
                // directory missing or unreadable
            }
            return n;
        }

        /// <summary>
        /// Atomic publication: write temp, flush to disk, replace target; retry 3 times on IOException.
        /// On failure the temp file is deleted and false is returned. A partial file is never visible.
        /// </summary>
        public bool PublishAtomic(string name, byte[] buffer, int length, out string error)
        {
            error = null;
            string target = Path.Combine(Dir, name);
            string temp = target + _tempSuffix;
            for (int attempt = 0; attempt <= RetryDelaysMs.Length; attempt++)
            {
                try
                {
#if DEBUG_FAULTS
                    if (DebugFaults.IsActive("exchange_unwritable"))
                        throw new UnauthorizedAccessException("DEBUG_FAULTS injected fault: exchange directory not writable");
#endif
                    using (var fs = new FileStream(temp, FileMode.Create, FileAccess.Write, FileShare.None, 64 * 1024, FileOptions.None))
                    {
                        fs.Write(buffer, 0, length);
                        fs.Flush(true);
                    }
                    if (File.Exists(target)) File.Replace(temp, target, null);
                    else File.Move(temp, target);
                    return true;
                }
                catch (Exception e)
                {
                    error = e.GetType().Name + ": " + e.Message;
                    if (!(e is IOException) && !(e is UnauthorizedAccessException)) break;
                    if (attempt < RetryDelaysMs.Length) Thread.Sleep(RetryDelaysMs[attempt]);
                }
            }
            TryDelete(temp);
            return false;
        }

        private static void TryDelete(string path)
        {
            try
            {
                if (File.Exists(path)) File.Delete(path);
            }
            catch (Exception)
            {
                // best effort
            }
        }

        public bool Exists(string name)
        {
            try
            {
                return File.Exists(Path.Combine(Dir, name));
            }
            catch (Exception)
            {
                return false;
            }
        }

        /// <summary>Last write time (UTC ticks) or -1 when missing.</summary>
        public long MtimeTicks(string name)
        {
            try
            {
                var fi = new FileInfo(Path.Combine(Dir, name));
                return fi.Exists ? fi.LastWriteTimeUtc.Ticks ^ fi.Length : -1;
            }
            catch (Exception)
            {
                return -1;
            }
        }

        /// <summary>Reads a small text file shared for write/delete. Returns null when missing or larger than maxBytes.</summary>
        public string ReadSmallText(string name, int maxBytes, out long length)
        {
            length = -1;
            try
            {
                string path = Path.Combine(Dir, name);
                using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                {
                    length = fs.Length;
                    if (length > maxBytes) return null;
                    var bytes = new byte[length];
                    int read = 0;
                    while (read < length)
                    {
                        int r = fs.Read(bytes, read, (int)length - read);
                        if (r <= 0) break;
                        read += r;
                    }
                    return Encoding.UTF8.GetString(bytes, 0, read);
                }
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>Appends lines to observer.log, rotating to observer.log.1 at 1 MB (max 2 files).</summary>
        public void AppendLog(List<string> lines)
        {
            if (lines.Count == 0) return;
            string path = Path.Combine(Dir, LogName);
            try
            {
                var fi = new FileInfo(path);
                if (fi.Exists && fi.Length >= LogRotateBytes)
                {
                    string old = Path.Combine(Dir, LogOldName);
                    if (File.Exists(old)) File.Delete(old);
                    File.Move(path, old);
                }
                using (var fs = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete))
                using (var w = new StreamWriter(fs, new UTF8Encoding(false)))
                {
                    for (int i = 0; i < lines.Count; i++) w.WriteLine(lines[i]);
                }
            }
            catch (Exception)
            {
                // logging must never fault the observer
            }
        }

        /// <summary>SHA-256 (upper-case hex) of a file, read-only. Null on error.</summary>
        public static string Sha256OfFile(string path)
        {
            try
            {
                using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var sha = SHA256.Create())
                {
                    return Hex(sha.ComputeHash(fs));
                }
            }
            catch (Exception)
            {
                return null;
            }
        }

        public static string Hex(byte[] bytes)
        {
            var sb = new StringBuilder(bytes.Length * 2);
            for (int i = 0; i < bytes.Length; i++) sb.Append(bytes[i].ToString("X2"));
            return sb.ToString();
        }
    }
}
