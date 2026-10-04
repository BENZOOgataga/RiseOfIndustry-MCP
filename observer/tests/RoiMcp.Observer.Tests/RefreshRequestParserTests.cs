using System.Text;
using RoiMcp.Observer.Core;
using Xunit;

namespace RoiMcp.Observer.Tests
{
    public class RefreshRequestParserTests
    {
        private static RefreshNonces Parse(string text)
        {
            return RefreshRequestParser.Parse(text, text == null ? 0 : Encoding.UTF8.GetByteCount(text));
        }

        private static void AssertAllNull(RefreshNonces n)
        {
            Assert.NotNull(n);
            Assert.Null(n.State);
            Assert.Null(n.History);
            Assert.Null(n.Static);
        }

        [Fact]
        public void ValidFile()
        {
            var n = Parse("{\"schema\": \"roi-mcp/refresh-request\", \"schema_version\": \"1.0.0\", " +
                          "\"requests\": {\"state\": 1730000000123, \"history\": null, \"static\": 7}, \"requested_utc\": \"2026-01-01T00:00:00Z\"}");
            Assert.Equal(1730000000123L, n.State);
            Assert.Null(n.History);
            Assert.Equal(7L, n.Static);
        }

        [Fact]
        public void Oversize_YieldsAllNull()
        {
            var padding = new string(' ', 1100);
            var text = "{\"requests\": {\"state\": 1, \"history\": 2, \"static\": 3}}" + padding;
            AssertAllNull(Parse(text));
            // byteLength alone over the limit is enough.
            AssertAllNull(RefreshRequestParser.Parse("{\"requests\": {\"state\": 1}}", RefreshRequestParser.MaxBytes + 1));
        }

        [Fact]
        public void ExactlyOneKilobyte_IsAccepted()
        {
            var core = "{\"requests\": {\"state\": 1}}";
            var text = core + new string(' ', RefreshRequestParser.MaxBytes - core.Length);
            Assert.Equal(1024, Encoding.UTF8.GetByteCount(text));
            Assert.Equal(1L, Parse(text).State);
        }

        [Theory]
        [InlineData("")]
        [InlineData("{")]
        [InlineData("{\"requests\": {\"state\": 1,}")]
        [InlineData("not json at all")]
        public void Malformed_YieldsAllNull(string text)
        {
            AssertAllNull(Parse(text));
        }

        [Fact]
        public void NullText_YieldsAllNull()
        {
            AssertAllNull(RefreshRequestParser.Parse(null, -1));
        }

        [Theory]
        [InlineData("[1, 2, 3]")]
        [InlineData("42")]
        [InlineData("\"state\"")]
        [InlineData("null")]
        public void NonObjectRoot_YieldsAllNull(string text)
        {
            AssertAllNull(Parse(text));
        }

        [Theory]
        [InlineData("{}")]
        [InlineData("{\"state\": 5}")]
        [InlineData("{\"requests\": null}")]
        [InlineData("{\"requests\": [1, 2, 3]}")]
        [InlineData("{\"requests\": {}}")]
        public void MissingKeys_YieldsNull(string text)
        {
            AssertAllNull(Parse(text));
        }

        [Fact]
        public void MissingScope_OnlyThatScopeIsNull()
        {
            var n = Parse("{\"requests\": {\"history\": 9}}");
            Assert.Null(n.State);
            Assert.Equal(9L, n.History);
            Assert.Null(n.Static);
        }

        [Theory]
        [InlineData("\"123\"")]
        [InlineData("1.5")]
        [InlineData("2.0")]
        [InlineData("1e3")]
        [InlineData("true")]
        [InlineData("false")]
        [InlineData("-5")]
        [InlineData("0")]
        [InlineData("{}")]
        [InlineData("[1]")]
        [InlineData("null")]
        public void NonIntegerOrNonPositiveNonce_IsNull(string value)
        {
            var n = Parse("{\"requests\": {\"state\": " + value + ", \"history\": 11}}");
            Assert.Null(n.State);
            Assert.Equal(11L, n.History);
        }

        [Fact]
        public void UnknownExtraKeysAreIgnored()
        {
            var n = Parse("{\"requests\": {\"state\": 3, \"buildings\": 4, \"static\": 5}, \"command\": \"demolish\", \"path\": \"C:/x\"}");
            Assert.Equal(3L, n.State);
            Assert.Null(n.History);
            Assert.Equal(5L, n.Static);
        }

        [Fact]
        public void HugeIntegerBeyondLong_IsNull()
        {
            var n = Parse("{\"requests\": {\"state\": 99999999999999999999999999, \"history\": 9223372036854775808, \"static\": 9223372036854775807}}");
            Assert.Null(n.State);
            Assert.Null(n.History);
            Assert.Equal(long.MaxValue, n.Static);
        }

        [Fact]
        public void NoncesCloneGetSet()
        {
            var n = new RefreshNonces();
            n.Set(CaptureFamily.State, 1);
            n.Set(CaptureFamily.History, 2);
            n.Set(CaptureFamily.Static, 3);
            var c = n.Clone();
            n.Set(CaptureFamily.State, 9);
            Assert.Equal(1L, c.Get(CaptureFamily.State));
            Assert.Equal(2L, c.Get(CaptureFamily.History));
            Assert.Equal(3L, c.Get(CaptureFamily.Static));
        }
    }
}
