using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading;
using Newtonsoft.Json.Linq;
using RoiMcp.Observer.Core;
using RoiMcp.Observer.Io;
using RoiMcp.Observer.Publish;
using Xunit;

// Diagnostics.Log is process-wide static state and BackgroundWorker drains it; running test classes
// sequentially keeps the Log tests deterministic.
[assembly: CollectionBehavior(DisableTestParallelization = true)]

namespace RoiMcp.Observer.Tests
{
    /// <summary>A private temporary directory (never the real exchange directory).</summary>
    internal sealed class TempDir : IDisposable
    {
        public string Dir { get; private set; }

        public TempDir()
        {
            Dir = Path.Combine(Path.GetTempPath(), "roimcp-observer-tests-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(Dir);
        }

        public string PathOf(string name)
        {
            return Path.Combine(Dir, name);
        }

        public void Dispose()
        {
            for (int i = 0; i < 10; i++)
            {
                try
                {
                    if (Directory.Exists(Dir)) Directory.Delete(Dir, true);
                    return;
                }
                catch (Exception)
                {
                    Thread.Sleep(100);
                }
            }
        }
    }

    internal static class Wait
    {
        /// <summary>Polls a condition until it holds or the timeout elapses. Exceptions count as "not yet".</summary>
        public static bool Until(Func<bool> condition, int timeoutMs = 5000)
        {
            var sw = Stopwatch.StartNew();
            while (sw.ElapsedMilliseconds < timeoutMs)
            {
                if (Try(condition)) return true;
                Thread.Sleep(25);
            }
            return Try(condition);
        }

        private static bool Try(Func<bool> condition)
        {
            try
            {
                return condition();
            }
            catch (Exception)
            {
                return false;
            }
        }
    }

    internal static class FileUtil
    {
        /// <summary>Reads a file shared with a concurrent writer; null when missing or locked.</summary>
        public static byte[] ReadBytes(string path)
        {
            try
            {
                using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                {
                    var ms = new MemoryStream();
                    fs.CopyTo(ms);
                    return ms.ToArray();
                }
            }
            catch (IOException)
            {
                return null;
            }
            catch (UnauthorizedAccessException)
            {
                return null;
            }
        }

        public static JObject ReadJson(string path)
        {
            var bytes = ReadBytes(path);
            if (bytes == null) return null;
            try
            {
                return ParseJson(new UTF8Encoding(false).GetString(bytes));
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>Parses JSON keeping date-like strings as strings (exact wire text).</summary>
        public static JObject ParseJson(string text)
        {
            using (var reader = new Newtonsoft.Json.JsonTextReader(new StringReader(text)) { DateParseHandling = Newtonsoft.Json.DateParseHandling.None })
            {
                return JObject.Load(reader);
            }
        }
    }

    /// <summary>Hub + ExchangeFiles + BackgroundWorker over a private temp exchange directory.</summary>
    internal sealed class WorkerHarness : IDisposable
    {
        public const int Pid = 4242;

        public readonly TempDir Temp = new TempDir();
        public readonly Hub Hub = new Hub();
        public readonly ExchangeFiles Files;
        public readonly BackgroundWorker Worker;

        public WorkerHarness()
        {
            Files = new ExchangeFiles(Temp.Dir, Pid);
            Worker = new BackgroundWorker(Hub, Files, Pid);
        }

        public string Dir { get { return Temp.Dir; } }

        public string PathOf(string name)
        {
            return Temp.PathOf(name);
        }

        public void Start()
        {
            Worker.Start();
        }

        public JObject Heartbeat()
        {
            return FileUtil.ReadJson(PathOf(ExchangeFiles.Heartbeat));
        }

        /// <summary>Waits for a heartbeat satisfying the predicate and returns it (null on timeout).</summary>
        public JObject WaitHeartbeat(Func<JObject, bool> predicate, int timeoutMs = 5000)
        {
            JObject found = null;
            Wait.Until(() =>
            {
                var hb = Heartbeat();
                if (hb != null && predicate(hb))
                {
                    found = hb;
                    return true;
                }
                return false;
            }, timeoutMs);
            return found;
        }

        public void Dispose()
        {
            // The production thread runs for the process lifetime; stop it in tests so it no longer drains
            // the static log queue or touches the deleted directory.
            try
            {
                var field = typeof(BackgroundWorker).GetField("_thread", BindingFlags.NonPublic | BindingFlags.Instance);
                var thread = field == null ? null : field.GetValue(Worker) as Thread;
                if (thread != null && thread.IsAlive)
                {
                    thread.Abort();
                    thread.Join(5000);
                }
            }
            catch (Exception)
            {
                // best effort
            }
            Temp.Dispose();
        }
    }

    internal sealed class FakeClock : ICaptureClock
    {
        public int Day;
        public long Frame;

        public int GameDay()
        {
            return Day;
        }

        public long FrameCount()
        {
            return Frame;
        }
    }

    internal sealed class FakeSection : ISection
    {
        private readonly Func<SectionContext, IEnumerator<bool>> _body;

        public FakeSection(string name, Func<SectionContext, IEnumerator<bool>> body, bool optional = false)
        {
            Name = name;
            _body = body;
            Optional = optional;
        }

        public string Name { get; private set; }
        public bool Optional { get; private set; }
        public int RunCalls;

        public IEnumerator<bool> Run(SectionContext ctx)
        {
            RunCalls++;
            return _body(ctx);
        }
    }

    internal sealed class TestData
    {
        public readonly Dictionary<string, object> Values = new Dictionary<string, object>(StringComparer.Ordinal);
        public readonly List<string> Order = new List<string>();
    }
}
