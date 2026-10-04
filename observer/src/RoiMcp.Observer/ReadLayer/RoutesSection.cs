using System;
using System.Collections.Generic;
using ProjectAutomata;
using RoiMcp.Observer.Capture;
using RoiMcp.Observer.Core;
using RoiMcp.Observer.Dto;

namespace RoiMcp.Observer.ReadLayer
{
    internal static partial class StateCapture
    {
        /// <summary>Slots captured by the last routes_player pass, kept only until route_paths ran in the same capture.</summary>
        private static readonly List<KeyValuePair<string, ManualDestinationSlot>> PathCandidates = new List<KeyValuePair<string, ManualDestinationSlot>>();

        private static IEnumerator<bool> RoutesSection(SectionContext ctx, WorldContext w, bool player)
        {
            var source = new List<Building>(player ? PlayerBuildings : AiBuildings);
            var result = new List<RouteDto>();
            var occurrences = new RouteMath.OccurrenceCounter();
            bool validated = ctx.Options.UiLabelValidated;
            if (player) PathCandidates.Clear();
            var slots = new List<ManualDestinationSlot>(16);
            for (int i = 0; i < source.Count; i++)
            {
                var b = source[i];
                if (b == null)
                {
                    ctx.ItemsVanished++;
                    continue;
                }
                var mdm = b.manualDestinations;
                if (mdm == null || mdm.slots == null) continue;
                slots.Clear();
                slots.AddRange(mdm.slots);
                bool dormant = false;
                var logistics = b.logistics;
                if (logistics != null) dormant = (logistics.options & LogisticsUserOptions.AUTO_WH) != 0;
                for (int s = 0; s < slots.Count; s++)
                {
                    var slot = slots[s];
                    if (slot == null) continue;
                    var destination = slot.destination;
                    var product = slot.product;
                    if (destination == null || product == null) continue;
                    RouteDto row;
                    try
                    {
                        var facts = RouteFacts(b, mdm, slot, s, dormant, w);
                        row = RouteMath.Build(facts, validated, occurrences.Next(facts));
                    }
                    catch (Exception e)
                    {
                        ctx.ItemFailed(player ? "routes_player" : "routes_ai", e);
                        continue;
                    }
                    result.Add(row);
                    if (player && PathCandidates.Count < 2000) PathCandidates.Add(new KeyValuePair<string, ManualDestinationSlot>(row.route_key, slot));
                    ctx.Items++;
                }
                if (ctx.ShouldYield) yield return true;
            }
            if (player) Data(ctx).routes_player = result;
            else Data(ctx).routes_ai = result;
        }

        private static RouteFacts RouteFacts(Building origin, ManualDestinationManager mdm, ManualDestinationSlot slot, int index, bool dormant, WorldContext w)
        {
            var destination = slot.destination;
            var product = slot.product;
            var destBuilding = destination.building;
            var owner = origin.buildingOwner;
            var f = new RouteFacts
            {
                Origin = w.Key(origin),
                Destination = w.Key(destBuilding),
                DestinationKind = w.DestinationKind(destBuilding),
                Product = w.Name(product),
                SlotIndex = index,
                Paused = slot.paused,
                WaitForFullVehicle = slot.waitTillVehicleFull,
                DormantAutoWarehouse = dormant,
                MinKeep = slot.minStoredAtSource,
            };
            var destOwner = destination.owner;
            f.DestinationOwnerActorId = WorldContext.ActorId(destOwner);
            if (destBuilding != null)
            {
                var settlement = destBuilding.settlement;
                f.DestinationCityId = WorldContext.CityId(settlement);
                f.DestinationDeadCity = settlement != null && settlement.isDead;
            }

            var src = slot.source;
            f.Source = src != null && src.building != null && src.building.prefab != null ? w.Name(src.building.prefab) : "own";

            var endpoint = mdm.GetDestinationForSlot(slot);
            f.Endpoint = endpoint != null ? w.Key(endpoint) : null;
            f.DestinationModuleMissing = endpoint == null;

            var network = mdm.GetNetworkForSlot(slot);
            f.TransportMode = network != null ? network.networkName : null;

            // Distance and dispatch cost: cache lookup + formula evaluation, main thread (PRD 12.3). Both getters
            // resolve the module endpoint and network again; without them they would throw, so skip.
            if (endpoint != null && network != null)
            {
                f.DistanceTiles = slot.distance;
                f.DispatchCost = slot.dispatchCost;
            }
            var handler = src != null ? src.paymentHandler : mdm.paymentHandler as DefaultTransportRequestPaymentHandlerBehaviour;
            if (handler != null && handler.formula != null) f.DispatchFormula = w.Name(handler.formula);

            var transport = mdm.GetTransportForSlot(slot);
            if (transport != null && network != null) f.VehicleCapacity = transport.GetVehicleCapacityForNetwork(network, product);

            // Origin stock as the game reads it (slot.origin.productStorage.Count).
            var originLogistics = slot.origin;
            var originStorage = originLogistics != null ? originLogistics.productStorage : null;
            if (originStorage != null) f.OriginStock = originStorage.Count(product);

            // Max Send: stored on the destination, read without the GetSafe insert.
            var autoProvider = destBuilding != null ? destBuilding.GetComponent<Shop>() : null;
            if (slot.autoMaxAccepted && autoProvider != null)
            {
                f.AutoMax = true;
                if (Guard.Modifiers(owner)) f.AutoMaxValue = autoProvider.GetDemand(product, owner);
            }
            string kind;
            f.StoredMaxAccepted = StoredMaxAccepted(destination.productStorage, product, out kind);
            f.StorageKind = kind;

            // Destination stock and free space as GetRequestedAmount reads them (slot.destination.storage).
            var destStorage = destination.storage;
            if (destStorage != null)
            {
                f.DestinationStock = destStorage.Count(product);
                f.DestinationSlots = destStorage.GetSlots(product);
                var pss = destStorage as ProductSpecificProductStorage;
                if (pss != null)
                {
                    int pulls, puts, stored;
                    if (ReflectionTable.StorageCounts(pss, product.AssetId, out pulls, out puts, out stored))
                    {
                        f.DestinationStored = stored;
                        f.DestinationIncomingReserved = puts;
                    }
                }
                // FreeSpace is reviewed pure only for ProductSpecificProductStorage and InfiniteStorage (PRD 12.3.3).
                var t = destStorage.GetType();
                if (t == typeof(ProductSpecificProductStorage))
                    f.DestinationFreeSpace = pss.FreeSpace(product, false, true, owner);
                else if (t == typeof(InfiniteStorage))
                    f.DestinationFreeSpace = ((InfiniteStorage)destStorage).FreeSpace(product, false, true, owner);
            }
            var accepted = destination.acceptedProducts;
            f.DestinationAcceptsProduct = destination.canAcceptAndProvideAll || (accepted != null && accepted.Contains(product));

            // Contract cap (ContractsAgent.TryGetContract: dictionary lookup, reviewed pure).
            var contracts = Guard.Has<IContractsAgent>(owner) ? owner.contracts : null;
            Contract contract;
            if (contracts != null && contracts.TryGetContract(product, destOwner, out contract) && contract != null)
            {
                f.HasContract = true;
                f.ContractAmount = contract.amount;
                f.ContractDelivered = contract.delivered;
                f.ContractReserved = contract.reserved;
            }
            f.WorldEventTargetsDestination = WorldEventTargets(owner, destination);

            // In-flight requests: only valid handles are read (invalid handles log errors in the game).
            var inflight = new InFlightDto();
            var requests = slot.activeRequests;
            if (requests != null)
            {
                for (int r = 0; r < requests.Count; r++)
                {
                    var h = requests[r];
                    inflight.requests_total++;
                    if (!h.IsValidHandle)
                    {
                        inflight.invalid_handles++;
                        continue;
                    }
                    var status = h.status;
                    int amount = h.productAmount;
                    if (status == TransportRequestStatus.NEW)
                    {
                        inflight.requests_new++;
                        inflight.units_requested += amount;
                    }
                    else if (status == TransportRequestStatus.STARTED)
                    {
                        inflight.requests_started++;
                        inflight.units_started += amount;
                    }
                    else inflight.requests_other++;
                }
            }
            f.InFlight = inflight;

            var error = slot.validationError;
            f.HasError = TransportError.IsError(error);
            if (f.HasError)
            {
                var errorType = error.type;
                f.ValidationError = errorType.ToString();
            }
            return f;
        }

        /// <summary>Destination-side max accepted for a product; 0 = unlimited; null = unreadable.</summary>
        private static int? StoredMaxAccepted(IProductStorage storage, ProductDefinition product, out string kind)
        {
            kind = "unknown";
            for (int depth = 0; depth < 3 && storage != null; depth++)
            {
                var pss = storage as ProductSpecificProductStorage;
                if (pss != null)
                {
                    kind = "product_specific";
                    return ReflectionTable.MaxAccepted(pss, product.AssetId);
                }
                var single = storage as SingleProductStorage;
                if (single != null)
                {
                    kind = "single_product";
                    return ReflectionTable.SingleMaxAccepted(single);
                }
                var infinite = storage as InfiniteStorage;
                if (infinite != null)
                {
                    kind = "infinite";
                    var map = infinite._maxAccepted;
                    if (map == null) return 0;
                    int v;
                    return map.dictionary.TryGetValue(product.AssetId, out v) ? v : 0;
                }
                var shared = storage as ModuleSharedProductStorage;
                if (shared != null)
                {
                    // ModuleSharedProductStorage delegates to the module owner's storage.
                    kind = "module_shared";
                    var module = shared.building != null ? shared.building.module : null;
                    var ownerBuilding = module != null && module.moduleOwner != null ? module.moduleOwner.building : null;
                    storage = ownerBuilding != null ? ownerBuilding.storage : null;
                    continue;
                }
                return null;
            }
            return null;
        }

        private static readonly List<DynamicWorldEvent> EventScratch = new List<DynamicWorldEvent>();

        /// <summary>
        /// True when an active product-target dynamic world event of the origin owner targets this destination.
        /// FilterProduct is not reviewed, so any product-target event on the destination counts (PRD 12.3.3).
        /// </summary>
        private static bool WorldEventTargets(IActor owner, BuildingLogistics destination)
        {
            if (owner == null || !Guard.Has<IWorldEventAgent>(owner)) return false;
            var events = owner.worldEvents;
            if (events == null) return false;
            EventScratch.Clear();
            foreach (var e in events.GetActiveDynamicEvents())
            {
                if (e != null) EventScratch.Add(e);
                if (EventScratch.Count > 64) break;
            }
            bool hit = false;
            for (int i = 0; i < EventScratch.Count && !hit; i++)
            {
                var e = EventScratch[i];
                if (e.completed) continue;
                if (e.GetObjective<ProductTargetDynamicEventObjective>() == null) continue;
                var target = e.targetBuilding;
                if (target != null && ReferenceEquals(target.logistics, destination)) hit = true;
            }
            EventScratch.Clear();
            return hit;
        }

        // ------------------------------------------------------------ warehouse requests

        private static IEnumerator<bool> RequestsSection(SectionContext ctx, WorldContext w)
        {
            var source = new List<Building>(PlayerBuildings);
            var result = new List<WarehouseRequestDto>();
            var counts = new Dictionary<string, int>(StringComparer.Ordinal);
            for (int i = 0; i < source.Count; i++)
            {
                var b = source[i];
                if (b == null)
                {
                    ctx.ItemsVanished++;
                    continue;
                }
                var endpoint = b.GetComponent<LogisticNetworkEndpoint>();
                if (endpoint == null) continue;
                var requests = endpoint.requests;
                string endpointKey = w.Key(b);
                for (int r = 0; r < requests.count; r++)
                {
                    var req = requests[r];
                    if (req == null) continue;
                    var product = req.product;
                    string productName = product != null ? w.Name(product) : null;
                    string tuple = endpointKey + "|" + (productName ?? "none");
                    int n;
                    counts.TryGetValue(tuple, out n);
                    counts[tuple] = n + 1;
                    var row = new WarehouseRequestDto
                    {
                        request_key = tuple + "|" + n,
                        endpoint = endpointKey,
                        product = productName,
                        occurrence = n,
                        fill = req.infinite,
                        requested_amount = req.infinite ? (int?)null : req.requestedAmount,
                        remaining = req.remainingAmount,
                        amount_being_moved = req.amountBeingMoved,
                        priority = req.priority,
                        active = req.active,
                        use_full_vehicles = req.useFullVehicles,
                        fulfilled = req.fulfilled,
                        allowed_graphs = new List<string>(),
                        endpoint_pull_disabled = endpoint.isPullDisabled,
                    };
                    var graphs = req.allowedGraphs;
                    if (graphs != null)
                        foreach (var g in graphs)
                            if (g != null && row.allowed_graphs.Count < 16) row.allowed_graphs.Add(w.Name(g.prefab != null ? g.prefab : g));
                    var exp = req.expenses;
                    if (exp != null)
                    {
                        row.expenses_this_month = exp.expensesThisMonth;
                        row.expenses_last_month = exp.expensesLastMonth;
                    }
                    result.Add(row);
                    ctx.Items++;
                }
                if (ctx.ShouldYield) yield return true;
            }
            Data(ctx).requests_player = result;
        }

        // ------------------------------------------------------------ route paths (optional)

        private static IEnumerator<bool> RoutePathsSection(SectionContext ctx, WorldContext w)
        {
            var result = new List<RoutePathDto>();
            var candidates = new List<KeyValuePair<string, ManualDestinationSlot>>(PathCandidates);
            for (int i = 0; i < candidates.Count; i++)
            {
                var slot = candidates[i].Value;
                if (slot == null || slot.destinationManager == null || slot.destination == null)
                {
                    ctx.ItemsVanished++;
                    continue;
                }
                var mdm = slot.destinationManager;
                var transport = mdm.GetTransportForSlot(slot);
                var network = mdm.GetNetworkForSlot(slot);
                var endpoint = mdm.GetDestinationForSlot(slot);
                List<int> path;
                if (transport == null || network == null || endpoint == null || !transport.TryGetPath(endpoint, network, out path) || path == null)
                    continue;
                // Pooled list: copy within this frame and never keep it (PRD 12.3.4).
                var row = new RoutePathDto { route_key = candidates[i].Key, original_points = path.Count, points = Decimate(path) };
                result.Add(row);
                ctx.Items++;
                if (ctx.ShouldYield) yield return true;
            }
            PathCandidates.Clear();
            Data(ctx).route_paths = result;
        }

        /// <summary>Keeps the end points and every turning point; at most 256 points.</summary>
        private static List<int[]> Decimate(List<int> tiles)
        {
            var points = new List<int[]>();
            int prevDx = int.MinValue, prevDy = int.MinValue;
            int px = 0, py = 0;
            for (int i = 0; i < tiles.Count; i++)
            {
                int x, y;
                WorldContext.Coords(tiles[i], out x, out y);
                if (i == 0)
                {
                    points.Add(new[] { x, y });
                }
                else
                {
                    int dx = Math.Sign(x - px), dy = Math.Sign(y - py);
                    if (i > 1 && (dx != prevDx || dy != prevDy)) points.Add(new[] { px, py });
                    prevDx = dx;
                    prevDy = dy;
                }
                px = x;
                py = y;
            }
            if (tiles.Count > 1) points.Add(new[] { px, py });
            if (points.Count <= 256) return points;
            var reduced = new List<int[]>(256);
            double step = (points.Count - 1) / 255.0;
            for (int i = 0; i < 256; i++) reduced.Add(points[(int)Math.Round(i * step)]);
            return reduced;
        }
    }
}
