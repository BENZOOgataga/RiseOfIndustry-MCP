using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace RoiMcp.Observer.Core
{
    /// <summary>Nonces from refresh-request.json (PRD 11.6). Only three integers are ever read.</summary>
    public sealed class RefreshNonces
    {
        public long? State;
        public long? History;
        public long? Static;

        public RefreshNonces Clone()
        {
            return (RefreshNonces)MemberwiseClone();
        }

        public long? Get(CaptureFamily f)
        {
            switch (f)
            {
                case CaptureFamily.State: return State;
                case CaptureFamily.History: return History;
                default: return Static;
            }
        }

        public void Set(CaptureFamily f, long? v)
        {
            switch (f)
            {
                case CaptureFamily.State: State = v; break;
                case CaptureFamily.History: History = v; break;
                default: Static = v; break;
            }
        }
    }

    public enum CaptureFamily
    {
        Static,
        State,
        History,
    }

    public static class RefreshRequestParser
    {
        public const int MaxBytes = 1024;

        /// <summary>
        /// Parses refresh-request.json. Files over 1 KB, malformed JSON, missing or non-integer values all
        /// yield null for the affected scope. No string from the file is used for anything.
        /// </summary>
        public static RefreshNonces Parse(string text, long byteLength)
        {
            var r = new RefreshNonces();
            if (text == null || byteLength > MaxBytes || text.Length > MaxBytes) return r;
            JObject root;
            try
            {
                root = JToken.Parse(text) as JObject;
            }
            catch (JsonException)
            {
                return r;
            }
            if (root == null) return r;
            var requests = root["requests"] as JObject;
            if (requests == null) return r;
            r.State = ReadNonce(requests["state"]);
            r.History = ReadNonce(requests["history"]);
            r.Static = ReadNonce(requests["static"]);
            return r;
        }

        private static long? ReadNonce(JToken t)
        {
            if (t == null || t.Type != JTokenType.Integer) return null;
            try
            {
                long v = (long)t;
                return v > 0 ? v : (long?)null;
            }
            catch (System.OverflowException)
            {
                return null;
            }
        }
    }
}
