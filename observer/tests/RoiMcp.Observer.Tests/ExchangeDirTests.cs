using System;
using System.IO;
using RoiMcp.Observer.Io;
using Xunit;

namespace RoiMcp.Observer.Tests
{
    public class ExchangeDirTests
    {
        [Fact]
        public void RelativeOverrideIsRejected()
        {
            var old = Environment.GetEnvironmentVariable("ROI_MCP_EXCHANGE_DIR");
            try
            {
                Environment.SetEnvironmentVariable("ROI_MCP_EXCHANGE_DIR", "relative" + Path.DirectorySeparatorChar + "RoiMcp");
                Assert.Null(ExchangeFiles.ResolveDir());
                var abs = Path.Combine(Path.GetTempPath(), "RoiMcpTest");
                Environment.SetEnvironmentVariable("ROI_MCP_EXCHANGE_DIR", abs);
                Assert.Equal(Path.GetFullPath(abs), ExchangeFiles.ResolveDir());
                Environment.SetEnvironmentVariable("ROI_MCP_EXCHANGE_DIR", null);
                Assert.EndsWith("RoiMcp", ExchangeFiles.ResolveDir());
                Assert.True(Path.IsPathRooted(ExchangeFiles.ResolveDir()));
            }
            finally
            {
                Environment.SetEnvironmentVariable("ROI_MCP_EXCHANGE_DIR", old);
            }
        }
    }
}
