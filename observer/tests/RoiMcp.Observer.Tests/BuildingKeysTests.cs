using RoiMcp.Observer.Capture;
using Xunit;

namespace RoiMcp.Observer.Tests
{
    public class BuildingKeysTests
    {
        [Fact]
        public void BaseKeyFormat()
        {
            Assert.Equal("FarmWheat@12,34", BuildingKeys.BaseKey("FarmWheat", 12, 34));
            Assert.Equal("Shop@-3,0", BuildingKeys.BaseKey("Shop", -3, 0));
            Assert.Equal("unknown@1,2", BuildingKeys.BaseKey(null, 1, 2));
        }

        [Fact]
        public void BaseKeyUsesInvariantDigits()
        {
            var previous = System.Threading.Thread.CurrentThread.CurrentCulture;
            try
            {
                System.Threading.Thread.CurrentThread.CurrentCulture = new System.Globalization.CultureInfo("ar-SA");
                Assert.Equal("A@1000,-2", BuildingKeys.BaseKey("A", 1000, -2));
            }
            finally
            {
                System.Threading.Thread.CurrentThread.CurrentCulture = previous;
            }
        }

        private static BuildingKeys Pass(BuildingKeys k, params int[] ids)
        {
            k.BeginPass();
            foreach (var id in ids) k.Register(id);
            k.EndPass();
            return k;
        }

        [Fact]
        public void NoCollision_PlainKeys()
        {
            var k = new BuildingKeys();
            k.SetBase(1, "Farm", 1, 2);
            k.SetBase(2, "Mill", 1, 2);
            k.SetBase(3, "Farm", 2, 1);
            Pass(k, 1, 2, 3);
            Assert.Equal("Farm@1,2", k.KeyOf(1));
            Assert.Equal("Mill@1,2", k.KeyOf(2));
            Assert.Equal("Farm@2,1", k.KeyOf(3));
            Assert.Equal(0, k.Collisions);
            Assert.Empty(k.NewCollisions);
            Assert.True(k.HasBase(1));
            Assert.False(k.HasBase(99));
        }

        [Fact]
        public void SameInstanceRegisteredTwice_IsNotACollision()
        {
            var k = new BuildingKeys();
            k.SetBase(1, "Farm", 1, 2);
            k.BeginPass();
            k.Register(1);
            k.Register(1);
            k.EndPass();
            Assert.Equal("Farm@1,2", k.KeyOf(1));
            Assert.Equal(0, k.Collisions);
        }

        [Fact]
        public void Collision_GuidSuffixAndInstanceIdSuffix()
        {
            var k = new BuildingKeys();
            k.SetBase(101, "Warehouse", 5, 5);
            k.SetBase(202, "Warehouse", 5, 5);
            k.SetBase(303, "Farm", 0, 0);
            k.SetGuid(101, "0f8fad5b-d9cb-469f-a165-70867728950e");
            Pass(k, 101, 202, 303);
            Assert.Equal("Warehouse@5,5#0f8fad5b", k.KeyOf(101));
            Assert.Equal("Warehouse@5,5#i202", k.KeyOf(202));
            Assert.Equal("Farm@0,0", k.KeyOf(303));
            Assert.Equal(2, k.Collisions);
            Assert.Equal(new[] { "Warehouse@5,5" }, k.NewCollisions.ToArray());
        }

        [Fact]
        public void Collision_GuidSuffixIgnoresDashes()
        {
            var k = new BuildingKeys();
            k.SetBase(1, "W", 0, 0);
            k.SetBase(2, "W", 0, 0);
            k.SetGuid(1, "ab-cd-ef12-3456-7890");
            k.SetGuid(2, "ABCDEF0123456789");
            Pass(k, 1, 2);
            Assert.Equal("W@0,0#abcdef12", k.KeyOf(1));
            Assert.Equal("W@0,0#ABCDEF01", k.KeyOf(2));
        }

        [Fact]
        public void Collision_BothWithoutGuid()
        {
            var k = new BuildingKeys();
            k.SetBase(7, "W", 3, 4);
            k.SetBase(8, "W", 3, 4);
            k.SetBase(9, "W", 3, 4);
            k.SetGuid(9, null);
            Pass(k, 7, 8, 9);
            Assert.Equal("W@3,4#i7", k.KeyOf(7));
            Assert.Equal("W@3,4#i8", k.KeyOf(8));
            Assert.Equal("W@3,4#i9", k.KeyOf(9));
            Assert.Equal(3, k.Collisions);
            Assert.Single(k.NewCollisions);
        }

        [Fact]
        public void NewCollisions_ReportedOncePerBaseKeyAcrossPasses()
        {
            var k = new BuildingKeys();
            k.SetBase(1, "W", 0, 0);
            k.SetBase(2, "W", 0, 0);
            Pass(k, 1, 2);
            Assert.Equal(new[] { "W@0,0" }, k.NewCollisions.ToArray());

            Pass(k, 1, 2);
            Assert.Empty(k.NewCollisions);
            Assert.Equal(2, k.Collisions);

            k.SetBase(3, "X", 9, 9);
            k.SetBase(4, "X", 9, 9);
            Pass(k, 1, 2, 3, 4);
            Assert.Equal(new[] { "X@9,9" }, k.NewCollisions.ToArray());
            Assert.Equal(4, k.Collisions);

            // Collision resolved (one building gone): keys become plain again.
            Pass(k, 1, 3);
            Assert.Equal("W@0,0", k.KeyOf(1));
            Assert.Equal("X@9,9", k.KeyOf(3));
            Assert.Equal(0, k.Collisions);
            Assert.Empty(k.NewCollisions);
        }

        [Fact]
        public void KeyOf_FallsBackToBaseKeyForBuildingsNotInThePass()
        {
            var k = new BuildingKeys();
            k.SetBase(1, "W", 0, 0);
            k.SetBase(2, "W", 0, 0);
            Pass(k, 1, 2);
            k.SetBase(3, "Farm", 7, 7); // created after the pass
            Assert.Equal("Farm@7,7", k.KeyOf(3));
            Assert.Null(k.KeyOf(404));
        }

        [Fact]
        public void Clear_ForgetsEverything()
        {
            var k = new BuildingKeys();
            k.SetBase(1, "W", 0, 0);
            k.SetBase(2, "W", 0, 0);
            Pass(k, 1, 2);
            k.Clear();
            Assert.Null(k.KeyOf(1));
            Assert.Equal(0, k.Collisions);
            k.SetBase(1, "W", 0, 0);
            k.SetBase(2, "W", 0, 0);
            Pass(k, 1, 2);
            Assert.Equal(new[] { "W@0,0" }, k.NewCollisions.ToArray()); // a new world session logs again
        }
    }
}
