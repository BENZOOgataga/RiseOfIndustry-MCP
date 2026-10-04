using RoiMcp.Observer.Capture;
using Xunit;

namespace RoiMcp.Observer.Tests
{
    /// <summary>
    /// Hand-computed cases for the PRD 12.3.3 replica of ManualDestinationManager.GetRequestedAmount:
    /// cap = vehicle capacity; available = max(stock - min_keep, 0); free = FreeSpace;
    /// max_send &gt; 0: free = min(free, max(max_send - (slots - free), 0));
    /// contract: free = min(free, max(0, amount - (delivered + reserved))); cap = min(cap, free);
    /// amount = min(cap, available, free); wait_for_full_vehicle and amount &lt; cap: 0.
    /// </summary>
    public class RouteMathTests
    {
        private static RouteFacts Facts()
        {
            return new RouteFacts
            {
                Origin = "Farm@10,20",
                Product = "Wheat",
                Destination = "Mill@30,40",
                Endpoint = "Mill@30,40",
                DestinationKind = "factory",
                DestinationOwnerActorId = 1,
                Source = "own",
                TransportMode = "Road",
                SlotIndex = 0,
                StoredMaxAccepted = 0,
                StorageKind = "product_specific",
                MinKeep = 0,
                DistanceTiles = 12,
                DispatchCost = 370f,
                DispatchFormula = "ManualDestinationDispatchCost",
                VehicleCapacity = 10,
                OriginStock = 50,
                DestinationStock = 0,
                DestinationIncomingReserved = 0,
                DestinationFreeSpace = 100,
                DestinationSlots = 100,
            };
        }

        // ---- dispatch_amount_now

        [Fact]
        public void UnlimitedMaxSend_AvailableLimits()
        {
            var f = Facts();
            f.VehicleCapacity = 20;
            f.OriginStock = 15;
            f.MinKeep = 5; // available = 10
            var d = RouteMath.DispatchAmount(f, 0);
            Assert.Equal(10, d.value);
            Assert.True(d.complete);
            Assert.Equal(new[] { "available" }, d.limited_by.ToArray());
            Assert.Equal(10, d.inputs.available);
            Assert.Null(d.inputs.max_send_room);
            Assert.Equal(RouteMath.ReplicaMethod, d.method);
            Assert.Equal("replica:ManualDestinationManager.GetRequestedAmount", d.method);
        }

        [Fact]
        public void CapLimits()
        {
            var f = Facts(); // cap 10, available 50, free 100
            var d = RouteMath.DispatchAmount(f, 0);
            Assert.Equal(10, d.value);
            Assert.True(d.complete);
            Assert.Equal(new[] { "cap" }, d.limited_by.ToArray());
        }

        [Fact]
        public void FreeSpaceLimits()
        {
            var f = Facts();
            f.DestinationFreeSpace = 3;
            var d = RouteMath.DispatchAmount(f, 0);
            Assert.Equal(3, d.value);
            Assert.Equal(new[] { "free_space" }, d.limited_by.ToArray());
        }

        [Fact]
        public void MaxSendHeadroomLimits()
        {
            // slots 40, free 30 => 10 already in the destination (stored + incoming); max send 12 => room 2.
            var f = Facts();
            f.DestinationSlots = 40;
            f.DestinationFreeSpace = 30;
            f.StoredMaxAccepted = 12;
            var d = RouteMath.DispatchAmount(f, 12);
            Assert.Equal(2, d.value);
            Assert.True(d.complete);
            Assert.Equal(new[] { "max_send" }, d.limited_by.ToArray());
            Assert.Equal(2, d.inputs.max_send_room);
            Assert.Equal(12, d.inputs.max_send);
            Assert.Equal(40, d.inputs.destination_slots);
            Assert.Equal(30, d.inputs.free_space);

            var m = RouteMath.MaxSend(f, false);
            Assert.Equal(2, m.headroom_now);
        }

        [Fact]
        public void MaxSendAlreadyExceeded_GivesZero()
        {
            var f = Facts();
            f.DestinationSlots = 40;
            f.DestinationFreeSpace = 20; // 20 in destination
            var d = RouteMath.DispatchAmount(f, 12);
            Assert.Equal(0, d.value);
            Assert.Equal(0, d.inputs.max_send_room);
            Assert.Equal(new[] { "max_send" }, d.limited_by.ToArray());
        }

        [Fact]
        public void MaxSendRoomLargerThanFree_FreeSpaceLimits()
        {
            var f = Facts();
            f.DestinationSlots = 40;
            f.DestinationFreeSpace = 4; // 36 in destination, max send 100 => room 64 > free 4
            var d = RouteMath.DispatchAmount(f, 100);
            Assert.Equal(4, d.value);
            Assert.Equal(64, d.inputs.max_send_room);
            Assert.Equal(new[] { "free_space" }, d.limited_by.ToArray());
        }

        [Fact]
        public void ContractCapsFreeAndCap()
        {
            // contract room = 20 - (12 + 3) = 5 => free = min(100, 5) = 5; cap = min(10, 5) = 5; amount = 5.
            var f = Facts();
            f.HasContract = true;
            f.ContractAmount = 20;
            f.ContractDelivered = 12;
            f.ContractReserved = 3;
            var d = RouteMath.DispatchAmount(f, 0);
            Assert.Equal(5, d.value);
            Assert.True(d.complete);
            Assert.Equal(5, d.inputs.contract_room);
            Assert.Single(d.limited_by);
        }

        [Fact]
        public void ContractFullyDelivered_GivesZero()
        {
            var f = Facts();
            f.HasContract = true;
            f.ContractAmount = 20;
            f.ContractDelivered = 18;
            f.ContractReserved = 5; // over-reserved => room max(0, -3) = 0
            var d = RouteMath.DispatchAmount(f, 0);
            Assert.Equal(0, d.value);
            Assert.Equal(0, d.inputs.contract_room);
        }

        [Fact]
        public void ContractRoomLargerThanCap_CapLimits()
        {
            var f = Facts();
            f.HasContract = true;
            f.ContractAmount = 100;
            f.ContractDelivered = 10;
            f.ContractReserved = 0; // room 90 => cap stays 10
            var d = RouteMath.DispatchAmount(f, 0);
            Assert.Equal(10, d.value);
            Assert.Equal(90, d.inputs.contract_room);
            Assert.Equal(new[] { "cap" }, d.limited_by.ToArray());
        }

        [Fact]
        public void ContractReducedCap_DoesNotTriggerWaitForFullVehicle()
        {
            // The game lowers cap to the contract room, so a "full" vehicle is the reduced cap.
            var f = Facts();
            f.WaitForFullVehicle = true;
            f.HasContract = true;
            f.ContractAmount = 5;
            var d = RouteMath.DispatchAmount(f, 0);
            Assert.Equal(5, d.value);
            Assert.DoesNotContain("wait_full", d.limited_by);
        }

        [Fact]
        public void ContractLimits_ReportsContract()
        {
            var f = Facts();
            f.HasContract = true;
            f.ContractAmount = 20;
            f.ContractDelivered = 12;
            f.ContractReserved = 3;
            var d = RouteMath.DispatchAmount(f, 0);
            Assert.Equal(5, d.value);
            Assert.Equal(new[] { "contract" }, d.limited_by.ToArray());
        }

        [Fact]
        public void WaitForFullVehicle_BelowCap_GivesZero()
        {
            var f = Facts();
            f.WaitForFullVehicle = true;
            f.OriginStock = 4; // amount 4 < cap 10
            var d = RouteMath.DispatchAmount(f, 0);
            Assert.Equal(0, d.value);
            Assert.True(d.complete);
            Assert.Equal(new[] { "wait_full" }, d.limited_by.ToArray());
            Assert.True(d.inputs.wait_for_full_vehicle);
        }

        [Fact]
        public void WaitForFullVehicle_AtCap_Dispatches()
        {
            var f = Facts();
            f.WaitForFullVehicle = true;
            var d = RouteMath.DispatchAmount(f, 0);
            Assert.Equal(10, d.value);
            Assert.Equal(new[] { "cap" }, d.limited_by.ToArray());
        }

        [Fact]
        public void WorldEvent_IsUnevaluated()
        {
            var f = Facts();
            f.WorldEventTargetsDestination = true;
            var d = RouteMath.DispatchAmount(f, 0);
            Assert.False(d.complete);
            Assert.Contains("world_event_unevaluated", d.limited_by);
            Assert.True(d.inputs.world_event_targets_destination);
        }

        [Fact]
        public void MissingVehicleCapacity_ValueNullIncomplete()
        {
            var f = Facts();
            f.VehicleCapacity = null;
            var d = RouteMath.DispatchAmount(f, 0);
            Assert.Null(d.value);
            Assert.False(d.complete);
            Assert.Equal(new[] { "input_unavailable:vehicle_capacity" }, d.limited_by.ToArray());
        }

        [Fact]
        public void MissingOriginStock_ValueNullIncomplete()
        {
            var f = Facts();
            f.OriginStock = null;
            var d = RouteMath.DispatchAmount(f, 0);
            Assert.Null(d.value);
            Assert.False(d.complete);
            Assert.Equal(new[] { "input_unavailable:origin_stock" }, d.limited_by.ToArray());
        }

        [Fact]
        public void MissingFreeSpace_ValueNullIncomplete()
        {
            var f = Facts();
            f.DestinationFreeSpace = null;
            var d = RouteMath.DispatchAmount(f, 0);
            Assert.Null(d.value);
            Assert.False(d.complete);
            Assert.Equal(new[] { "input_unavailable:destination_free_space" }, d.limited_by.ToArray());
        }

        [Fact]
        public void AllInputsMissing_ListsEach()
        {
            var f = Facts();
            f.VehicleCapacity = null;
            f.OriginStock = null;
            f.DestinationFreeSpace = null;
            var d = RouteMath.DispatchAmount(f, 0);
            Assert.Null(d.value);
            Assert.Equal(3, d.limited_by.Count);
        }

        [Fact]
        public void MinKeepKeepAll_NothingAvailable()
        {
            var f = Facts();
            f.MinKeep = int.MaxValue;
            var d = RouteMath.DispatchAmount(f, 0);
            Assert.Equal(0, d.value);
            Assert.Equal(0, d.inputs.available);
            Assert.Equal(new[] { "available" }, d.limited_by.ToArray());
        }

        [Fact]
        public void MinKeepAboveStock_NothingAvailable()
        {
            var f = Facts();
            f.MinKeep = 60;
            var d = RouteMath.DispatchAmount(f, 0);
            Assert.Equal(0, d.value);
            Assert.Equal(0, d.inputs.available);
        }

        // ---- max_send

        [Fact]
        public void MaxSend_Manual()
        {
            var f = Facts();
            f.StoredMaxAccepted = 8;
            var m = RouteMath.MaxSend(f, false);
            Assert.Equal(8, m.value);
            Assert.False(m.unlimited);
            Assert.Equal("manual", m.mode);
            Assert.Equal("destination_product_shared", m.scope);
            Assert.False(m.ui_label_validated);
            Assert.Equal("product_specific", m.storage_kind);
        }

        [Fact]
        public void MaxSend_PrdExample_HeadroomFromStockPlusIncoming()
        {
            // PRD 12.3.1 example: value 8, stock + incoming reserved = 5 => headroom 3.
            var f = Facts();
            f.StoredMaxAccepted = 8;
            f.DestinationSlots = null;
            f.DestinationStock = 3;
            f.DestinationIncomingReserved = 2;
            var m = RouteMath.MaxSend(f, false);
            Assert.Equal(3, m.headroom_now);
        }

        [Fact]
        public void MaxSend_HeadroomNeverNegative()
        {
            var f = Facts();
            f.StoredMaxAccepted = 8;
            f.DestinationSlots = 100;
            f.DestinationFreeSpace = 80; // 20 in destination
            Assert.Equal(0, RouteMath.MaxSend(f, false).headroom_now);
        }

        [Fact]
        public void MaxSend_UnreadableStoredValueMeansUnlimited()
        {
            var f = Facts();
            f.StoredMaxAccepted = null;
            var m = RouteMath.MaxSend(f, false);
            Assert.Equal(0, m.value);
            Assert.True(m.unlimited);
        }

        [Fact]
        public void MaxSend_AutoShopDemand()
        {
            var f = Facts();
            f.AutoMax = true;
            f.AutoMaxValue = 12;
            f.StoredMaxAccepted = 99;
            f.DestinationSlots = 40;
            f.DestinationFreeSpace = 30;
            var m = RouteMath.MaxSend(f, true);
            Assert.Equal(12, m.value);
            Assert.Equal("auto_shop_demand", m.mode);
            Assert.False(m.unlimited);
            Assert.Equal(2, m.headroom_now);
            Assert.True(m.ui_label_validated);
        }

        [Fact]
        public void MaxSend_Unlimited_HeadroomNull()
        {
            var f = Facts();
            f.StoredMaxAccepted = 0;
            var m = RouteMath.MaxSend(f, false);
            Assert.True(m.unlimited);
            Assert.Null(m.headroom_now);
            Assert.Equal("manual", m.mode);

            var a = Facts();
            a.AutoMax = true;
            a.AutoMaxValue = 0;
            var am = RouteMath.MaxSend(a, false);
            Assert.True(am.unlimited);
            Assert.Null(am.headroom_now);
            Assert.Equal("auto_shop_demand", am.mode);
        }

        // ---- min_keep

        [Fact]
        public void MinKeep_KeepAll()
        {
            var k = RouteMath.MinKeep(int.MaxValue, false);
            Assert.True(k.keep_all);
            Assert.Equal(int.MaxValue, k.value);
            Assert.False(k.ui_label_validated);
        }

        [Fact]
        public void MinKeep_Regular()
        {
            var k = RouteMath.MinKeep(4, true);
            Assert.False(k.keep_all);
            Assert.Equal(4, k.value);
            Assert.True(k.ui_label_validated);
            Assert.False(RouteMath.MinKeep(99, false).keep_all);
            Assert.False(RouteMath.MinKeep(0, false).keep_all);
        }

        // ---- Build

        [Fact]
        public void Build_RouteKeyAndCachedPath()
        {
            var r = RouteMath.Build(Facts(), false, 0);
            Assert.Equal("Farm@10,20|Wheat|Mill@30,40|own|0", r.route_key);
            Assert.Equal(0, r.occurrence);
            Assert.Equal("cached", r.path_status);
            Assert.Equal(12, r.distance_tiles);
            Assert.Equal(370f, r.dispatch_cost);
            Assert.Empty(r.errors);
            Assert.NotNull(r.in_flight);
            Assert.Equal(10, r.dispatch_amount_now.value);
            Assert.Equal("manual", r.max_send.mode);
            Assert.False(r.min_keep.keep_all);

            var depot = Facts();
            depot.Source = "TruckDepot";
            Assert.Equal("Farm@10,20|Wheat|Mill@30,40|TruckDepot|3", RouteMath.Build(depot, false, 3).route_key);
        }

        [Fact]
        public void Build_ZeroDistance_PathUnavailable()
        {
            var f = Facts();
            f.DistanceTiles = 0;
            f.DispatchCost = 0f;
            var r = RouteMath.Build(f, false, 0);
            Assert.Null(r.distance_tiles);
            Assert.Equal("unavailable", r.path_status);
            Assert.Null(r.dispatch_cost);
            Assert.Equal(new[] { "no_path" }, r.errors.ToArray());
        }

        [Fact]
        public void Build_NullDistance_PathUnavailable()
        {
            var f = Facts();
            f.DistanceTiles = null;
            var r = RouteMath.Build(f, false, 0);
            Assert.Null(r.distance_tiles);
            Assert.Equal("unavailable", r.path_status);
            Assert.Null(r.dispatch_cost);
            Assert.Contains("no_path", r.errors);
        }

        [Fact]
        public void Build_NonFiniteDispatchCostIsNull()
        {
            var f = Facts();
            f.DispatchCost = float.NaN;
            Assert.Null(RouteMath.Build(f, false, 0).dispatch_cost);
            f.DispatchCost = float.PositiveInfinity;
            Assert.Null(RouteMath.Build(f, false, 0).dispatch_cost);
        }

        [Fact]
        public void Build_AllErrors()
        {
            var f = Facts();
            f.DistanceTiles = 0;
            f.DestinationModuleMissing = true;
            f.Endpoint = null;
            f.HasError = true;
            f.ValidationError = "NoRoadConnection";
            f.DestinationDeadCity = true;
            f.DestinationAcceptsProduct = false;
            var r = RouteMath.Build(f, false, 0);
            Assert.Equal(new[] { "no_path", "destination_module_missing", "validation_error:NoRoadConnection", "destination_dead_city", "product_not_accepted" },
                r.errors.ToArray());
            Assert.True(r.has_error);
            Assert.Equal("NoRoadConnection", r.validation_error);
            Assert.True(r.destination_dead_city);
            Assert.False(r.destination_accepts_product);
            Assert.Null(r.endpoint);
        }

        [Fact]
        public void Build_ValidationErrorWithoutType()
        {
            var f = Facts();
            f.HasError = true;
            Assert.Equal(new[] { "validation_error:unknown" }, RouteMath.Build(f, false, 0).errors.ToArray());
        }

        [Fact]
        public void Build_EachErrorAlone()
        {
            var a = Facts();
            a.DestinationModuleMissing = true;
            Assert.Equal(new[] { "destination_module_missing" }, RouteMath.Build(a, false, 0).errors.ToArray());
            var b = Facts();
            b.DestinationDeadCity = true;
            Assert.Equal(new[] { "destination_dead_city" }, RouteMath.Build(b, false, 0).errors.ToArray());
            var c = Facts();
            c.DestinationAcceptsProduct = false;
            Assert.Equal(new[] { "product_not_accepted" }, RouteMath.Build(c, false, 0).errors.ToArray());
        }

        [Fact]
        public void Build_DispatchAmountUsesTheMaxSendValue()
        {
            var f = Facts();
            f.AutoMax = true;
            f.AutoMaxValue = 12;
            f.DestinationSlots = 40;
            f.DestinationFreeSpace = 30;
            var r = RouteMath.Build(f, false, 0);
            Assert.Equal(12, r.max_send.value);
            Assert.Equal(2, r.dispatch_amount_now.value);
            Assert.Equal(new[] { "max_send" }, r.dispatch_amount_now.limited_by.ToArray());
        }

        // ---- occurrence numbering

        [Fact]
        public void OccurrenceCounter_NumbersDuplicateTuplesInSlotOrder()
        {
            var c = new RouteMath.OccurrenceCounter();
            var a = Facts();
            var b = Facts();
            b.Destination = "Shop@1,1";
            var depot = Facts();
            depot.Source = "TruckDepot";
            var otherProduct = Facts();
            otherProduct.Product = "Flour";

            Assert.Equal(0, c.Next(a));
            Assert.Equal(1, c.Next(a));
            Assert.Equal(0, c.Next(b));
            Assert.Equal(0, c.Next(depot));
            Assert.Equal(0, c.Next(otherProduct));
            Assert.Equal(2, c.Next(Facts()));
            Assert.Equal(1, c.Next(b));

            c.Reset();
            Assert.Equal(0, c.Next(a));
        }

        [Fact]
        public void OccurrenceCounter_RouteKeysOfDuplicatesAreDistinct()
        {
            var c = new RouteMath.OccurrenceCounter();
            var f = Facts();
            var k0 = RouteMath.Build(f, false, c.Next(f)).route_key;
            var k1 = RouteMath.Build(f, false, c.Next(f)).route_key;
            Assert.Equal("Farm@10,20|Wheat|Mill@30,40|own|0", k0);
            Assert.Equal("Farm@10,20|Wheat|Mill@30,40|own|1", k1);
        }
    }
}
