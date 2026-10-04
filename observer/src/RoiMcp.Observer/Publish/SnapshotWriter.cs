using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using RoiMcp.Observer.Dto;

namespace RoiMcp.Observer.Publish
{
    /// <summary>
    /// Serializes an envelope plus data into JSON bytes (background thread only). The data part is
    /// serialized first into a reused buffer so that content_hash (SHA-256 of the data JSON) can be written
    /// in the envelope before it. Buffers are reused across publications to avoid repeated large allocations.
    /// </summary>
    public sealed class SnapshotWriter
    {
        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false);
        private readonly JsonSerializer _serializer;
        private readonly MemoryStream _data = new MemoryStream(256 * 1024);
        private readonly MemoryStream _file = new MemoryStream(256 * 1024);

        public SnapshotWriter()
        {
            _serializer = JsonSerializer.Create(new JsonSerializerSettings
            {
                NullValueHandling = NullValueHandling.Include,
                FloatFormatHandling = FloatFormatHandling.DefaultValue,
                Formatting = Formatting.None,
                ReferenceLoopHandling = ReferenceLoopHandling.Error,
                DateParseHandling = DateParseHandling.None,
            });
        }

        public byte[] Buffer { get { return _file.GetBuffer(); } }
        public int Length { get { return (int)_file.Length; } }
        public int DataLength { get; private set; }

        /// <summary>Serializes data and returns its SHA-256 hex (lower-case).</summary>
        public string SerializeData(object data)
        {
            _data.SetLength(0);
            using (var sw = new StreamWriter(_data, Utf8, 64 * 1024, true))
            using (var w = new JsonTextWriter(sw) { CloseOutput = false })
            {
                _serializer.Serialize(w, data);
                w.Flush();
            }
            DataLength = (int)_data.Length;
            using (var sha = SHA256.Create())
            {
                var hash = sha.ComputeHash(_data.GetBuffer(), 0, DataLength);
                var sb = new StringBuilder(64);
                for (int i = 0; i < hash.Length; i++) sb.Append(hash[i].ToString("x2"));
                return sb.ToString();
            }
        }

        /// <summary>Writes envelope fields in canonical order followed by the already serialized data.</summary>
        public void WriteFile(EnvelopeDto env)
        {
            _file.SetLength(0);
            using (var sw = new StreamWriter(_file, Utf8, 64 * 1024, true))
            using (var w = new JsonTextWriter(sw) { CloseOutput = false, AutoCompleteOnClose = false })
            {
                w.WriteStartObject();
                Prop(w, "schema", env.schema);
                Prop(w, "schema_version", env.schema_version);
                Prop(w, "observer_version", env.observer_version);
                Prop(w, "compatibility", env.compatibility);
                w.WritePropertyName("game");
                _serializer.Serialize(w, env.game);
                w.WritePropertyName("pid");
                w.WriteValue(env.pid);
                Prop(w, "world_session", env.world_session);
                w.WritePropertyName("seq");
                w.WriteValue(env.seq);
                Prop(w, "content_hash", env.content_hash);
                Prop(w, "written_utc", env.written_utc);
                w.WritePropertyName("captured");
                _serializer.Serialize(w, env.captured);
                w.WritePropertyName("static_ref");
                _serializer.Serialize(w, env.static_ref);
                w.WritePropertyName("sections");
                _serializer.Serialize(w, env.sections);
                w.WritePropertyName("warnings");
                _serializer.Serialize(w, env.warnings ?? new List<WarningDto>());
                w.WritePropertyName("data");
                w.Flush();
            }
            _file.Write(_data.GetBuffer(), 0, DataLength);
            _file.WriteByte((byte)'}');
        }

        private static void Prop(JsonTextWriter w, string name, string value)
        {
            w.WritePropertyName(name);
            if (value == null) w.WriteNull();
            else w.WriteValue(value);
        }

        /// <summary>Serializes a small object (heartbeat) completely: envelope + data.</summary>
        public void WriteSmall(EnvelopeDto env, object data)
        {
            env.content_hash = null;
            SerializeData(data);
            WriteFile(env);
        }
    }
}
