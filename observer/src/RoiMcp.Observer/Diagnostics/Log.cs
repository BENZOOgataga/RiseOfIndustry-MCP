using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace RoiMcp.Observer.Diagnostics
{
    public enum LogLevel
    {
        Error = 0,
        Warn = 1,
        Info = 2,
        Debug = 3,
    }

    /// <summary>
    /// Bounded in-memory log queue (PRD 19). Producers on any thread enqueue; the background thread drains
    /// it into observer.log. Overflow drops entries and counts them. Errors are de-duplicated by signature
    /// (at most one line per minute per signature plus a suppressed count). Never logs snapshot contents.
    /// </summary>
    public static class Log
    {
        public const int Capacity = 1000;

        private static readonly object Sync = new object();
        private static readonly Queue<string> Queue = new Queue<string>();
        private static readonly Dictionary<string, ErrorSignature> Signatures = new Dictionary<string, ErrorSignature>(StringComparer.Ordinal);
        private static readonly Queue<DateTime> ErrorTimes = new Queue<DateTime>();
        private static int _dropped;
        private static LogLevel _level = LogLevel.Info;

        private sealed class ErrorSignature
        {
            public DateTime LastLogged;
            public int Suppressed;
        }

        public static int Dropped { get { lock (Sync) return _dropped; } }

        public static void SetLevel(string level)
        {
            LogLevel l;
            switch (level)
            {
                case "error": l = LogLevel.Error; break;
                case "warn": l = LogLevel.Warn; break;
                case "debug": l = LogLevel.Debug; break;
                default: l = LogLevel.Info; break;
            }
            lock (Sync) _level = l;
        }

        public static void Info(string component, string message) { Write(LogLevel.Info, component, message); }
        public static void Warn(string component, string message) { Write(LogLevel.Warn, component, message); }
        public static void Debug(string component, string message) { Write(LogLevel.Debug, component, message); }

        /// <summary>Error with de-duplication by signature.</summary>
        public static void Error(string component, string signature, string message)
        {
            var now = DateTime.UtcNow;
            lock (Sync)
            {
                ErrorTimes.Enqueue(now);
                while (ErrorTimes.Count > 0 && (now - ErrorTimes.Peek()).TotalHours > 1) ErrorTimes.Dequeue();
                if (ErrorTimes.Count > 10000) ErrorTimes.Dequeue();
                ErrorSignature sig;
                if (!Signatures.TryGetValue(signature, out sig))
                {
                    if (Signatures.Count > 500) Signatures.Clear();
                    sig = new ErrorSignature { LastLogged = DateTime.MinValue };
                    Signatures[signature] = sig;
                }
                if ((now - sig.LastLogged).TotalSeconds < 60)
                {
                    sig.Suppressed++;
                    return;
                }
                string suffix = sig.Suppressed > 0 ? " (suppressed " + sig.Suppressed + " similar)" : "";
                sig.Suppressed = 0;
                sig.LastLogged = now;
                EnqueueLocked(Format(now, LogLevel.Error, component, "[" + signature + "] " + message + suffix));
            }
        }

        public static int ErrorsLastHour()
        {
            var now = DateTime.UtcNow;
            lock (Sync)
            {
                while (ErrorTimes.Count > 0 && (now - ErrorTimes.Peek()).TotalHours > 1) ErrorTimes.Dequeue();
                return ErrorTimes.Count;
            }
        }

        public static void Write(LogLevel level, string component, string message)
        {
            lock (Sync)
            {
                if (level > _level) return;
                EnqueueLocked(Format(DateTime.UtcNow, level, component, message));
            }
        }

        private static void EnqueueLocked(string line)
        {
            if (Queue.Count >= Capacity)
            {
                _dropped++;
                return;
            }
            Queue.Enqueue(line);
        }

        /// <summary>Drains up to max lines (background thread).</summary>
        public static int Drain(List<string> into, int max)
        {
            lock (Sync)
            {
                int n = 0;
                while (Queue.Count > 0 && n < max)
                {
                    into.Add(Queue.Dequeue());
                    n++;
                }
                return n;
            }
        }

        private static string Format(DateTime utc, LogLevel level, string component, string message)
        {
            var sb = new StringBuilder(64 + (message != null ? message.Length : 0));
            sb.Append(utc.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", CultureInfo.InvariantCulture));
            sb.Append(' ');
            sb.Append(LevelName(level));
            sb.Append(' ');
            sb.Append(component);
            sb.Append(' ');
            if (message != null) sb.Append(message.Replace('\r', ' ').Replace('\n', ' '));
            return sb.ToString();
        }

        private static string LevelName(LogLevel l)
        {
            switch (l)
            {
                case LogLevel.Error: return "ERROR";
                case LogLevel.Warn: return "WARN";
                case LogLevel.Debug: return "DEBUG";
                default: return "INFO";
            }
        }

        /// <summary>Stable short signature for an exception: location + type + first stack frame.</summary>
        public static string Signature(string where, Exception e)
        {
            if (e == null) return where;
            string frame = "";
            var st = e.StackTrace;
            if (!string.IsNullOrEmpty(st))
            {
                int nl = st.IndexOf('\n');
                frame = (nl > 0 ? st.Substring(0, nl) : st).Trim();
                if (frame.Length > 120) frame = frame.Substring(0, 120);
            }
            return where + ":" + e.GetType().Name + (frame.Length > 0 ? "@" + frame : "");
        }
    }
}
