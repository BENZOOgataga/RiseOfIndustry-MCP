using System;
using System.Collections.Generic;
using RoiMcp.Observer.Dto;

namespace RoiMcp.Observer.Capture
{
    /// <summary>Plain facts about one manual destination slot, read by the read layer.</summary>
    public sealed class RouteFacts
    {
        public string Origin;
        public string Destination;
        public string Endpoint;
        public string DestinationKind;
        public int DestinationOwnerActorId;
        public int? DestinationCityId;
        public bool DestinationDeadCity;
        public string Product;
        public string Source;
        public string TransportMode;
        public int SlotIndex;
        public bool Paused;
        public bool WaitForFullVehicle;
        public bool DormantAutoWarehouse;

        /// <summary>slot.autoMaxAccepted and the destination provides auto max (shop).</summary>
        public bool AutoMax;
        public int? AutoMaxValue;
        /// <summary>Destination-side stored max accepted (0 = unlimited); null when unreadable.</summary>
        public int? StoredMaxAccepted;
        public string StorageKind;

        public int MinKeep;
        public int? DistanceTiles;
        public float? DispatchCost;
        public string DispatchFormula;
        public int? VehicleCapacity;

        public int? OriginStock;
        public int? DestinationStock;
        public int? DestinationStored;
        public int? DestinationIncomingReserved;
        /// <summary>FreeSpace(product, ignorePut: false, ignorePull: true); null when not readable or not reviewed.</summary>
        public int? DestinationFreeSpace;
        public int? DestinationSlots;
        public bool DestinationAcceptsProduct = true;

        public bool HasContract;
        public int ContractAmount;
        public int ContractDelivered;
        public int ContractReserved;
        public bool WorldEventTargetsDestination;

        public InFlightDto InFlight;
        public string ValidationError;
        public bool HasError;
        public bool DestinationModuleMissing;
    }

    /// <summary>
    /// Deterministic route derivations computed by the observer (PRD 12.3): Max Send interpretation and the
    /// replica of ManualDestinationManager.GetRequestedAmount (ManualDestinationManager.cs:595-626), which the
    /// observer must not call because it reads maxAcceptedAtDestination (a GetSafe insert).
    /// </summary>
    public static class RouteMath
    {
        public const string ReplicaMethod = "replica:ManualDestinationManager.GetRequestedAmount";

        public static MaxSendDto MaxSend(RouteFacts f, bool uiLabelValidated)
        {
            int value;
            string mode;
            if (f.AutoMax)
            {
                // Shop demand unreadable (guard): never fall back to the stored manual value.
                value = f.AutoMaxValue ?? 0;
                mode = "auto_shop_demand";
            }
            else
            {
                value = f.StoredMaxAccepted ?? 0;
                mode = "manual";
            }
            var dto = new MaxSendDto
            {
                value = value,
                unlimited = value == 0,
                mode = mode,
                scope = "destination_product_shared",
                ui_label_validated = uiLabelValidated,
                storage_kind = f.StorageKind,
            };
            if (value > 0 && f.DestinationSlots.HasValue && f.DestinationFreeSpace.HasValue)
            {
                // Game: inDest = slots - FreeSpace(ignorePut:false, ignorePull:true) = stored + incoming reservations.
                int inDestination = f.DestinationSlots.Value - f.DestinationFreeSpace.Value;
                dto.headroom_now = Math.Max(value - inDestination, 0);
            }
            else if (value > 0 && f.DestinationStock.HasValue && f.DestinationIncomingReserved.HasValue)
            {
                dto.headroom_now = Math.Max(value - (f.DestinationStock.Value + f.DestinationIncomingReserved.Value), 0);
            }
            return dto;
        }

        public static MinKeepDto MinKeep(int minStoredAtSource, bool uiLabelValidated)
        {
            bool keepAll = minStoredAtSource >= int.MaxValue;
            return new MinKeepDto { value = minStoredAtSource, keep_all = keepAll, ui_label_validated = uiLabelValidated };
        }

        /// <summary>
        /// Replica of GetRequestedAmount from pure inputs:
        /// cap = vehicle capacity; available = max(originStock - minKeep, 0); free = destination free space;
        /// Max Send &gt; 0: free = min(free, max(maxSend - (slots - free), 0)); contract caps free and cap;
        /// world event: unevaluated (complete = false); amount = min(cap, available, free);
        /// waitForFullVehicle and amount &lt; cap: 0.
        /// </summary>
        public static DispatchAmountDto DispatchAmount(RouteFacts f, int maxSendValue)
        {
            var inputs = new DispatchInputsDto
            {
                vehicle_capacity = f.VehicleCapacity,
                origin_stock = f.OriginStock,
                min_keep = f.MinKeep,
                free_space = f.DestinationFreeSpace,
                max_send = maxSendValue,
                destination_slots = f.DestinationSlots,
                world_event_targets_destination = f.WorldEventTargetsDestination,
                wait_for_full_vehicle = f.WaitForFullVehicle,
            };
            var dto = new DispatchAmountDto { inputs = inputs, limited_by = new List<string>(), method = ReplicaMethod, complete = true };
            if (f.AutoMax && !f.AutoMaxValue.HasValue)
            {
                dto.complete = false;
                dto.value = null;
                dto.limited_by.Add("input_unavailable:auto_max_send");
                return dto;
            }

            if (!f.VehicleCapacity.HasValue || !f.OriginStock.HasValue || !f.DestinationFreeSpace.HasValue)
            {
                dto.complete = false;
                dto.value = null;
                if (!f.VehicleCapacity.HasValue) dto.limited_by.Add("input_unavailable:vehicle_capacity");
                if (!f.OriginStock.HasValue) dto.limited_by.Add("input_unavailable:origin_stock");
                if (!f.DestinationFreeSpace.HasValue) dto.limited_by.Add("input_unavailable:destination_free_space");
                return dto;
            }

            int cap = f.VehicleCapacity.Value;
            long availableL = Math.Max((long)f.OriginStock.Value - f.MinKeep, 0L);
            int available = (int)Math.Min(availableL, int.MaxValue);
            inputs.available = available;
            int free = f.DestinationFreeSpace.Value;
            string freeLimiter = "free_space";

            if (maxSendValue > 0 && f.DestinationSlots.HasValue)
            {
                int inDestination = f.DestinationSlots.Value - f.DestinationFreeSpace.Value;
                int room = Math.Max(maxSendValue - inDestination, 0);
                inputs.max_send_room = room;
                if (room < free)
                {
                    free = room;
                    freeLimiter = "max_send";
                }
            }

            string capLimiter = "cap";
            if (f.HasContract)
            {
                int contractRoom = Math.Max(0, f.ContractAmount - (f.ContractDelivered + f.ContractReserved));
                inputs.contract_room = contractRoom;
                if (contractRoom < free)
                {
                    free = contractRoom;
                    freeLimiter = "contract";
                }
                // Game: with a contract the vehicle capacity is also capped by the free room.
                if (free < cap)
                {
                    cap = free;
                    capLimiter = freeLimiter;
                }
            }

            int amount = Math.Min(cap, available);
            string limiter = cap <= available ? capLimiter : "available";
            if (free < amount)
            {
                amount = free;
                limiter = freeLimiter;
            }
            if (f.WaitForFullVehicle && amount < cap)
            {
                amount = 0;
                limiter = "wait_full";
            }
            dto.value = amount;
            dto.limited_by.Add(limiter);

            if (f.WorldEventTargetsDestination)
            {
                // NeededProduct implementations are not reviewed/allowlisted (PRD 10.4, U-WE).
                dto.complete = false;
                dto.limited_by.Add("world_event_unevaluated");
            }
            return dto;
        }

        public static RouteDto Build(RouteFacts f, bool uiLabelValidated, int occurrence)
        {
            var maxSend = MaxSend(f, uiLabelValidated);
            var dto = new RouteDto
            {
                route_key = f.Origin + "|" + f.Product + "|" + f.Destination + "|" + f.Source + "|" + occurrence,
                origin = f.Origin,
                destination = f.Destination,
                endpoint = f.Endpoint,
                destination_kind = f.DestinationKind,
                destination_owner_actor_id = f.DestinationOwnerActorId,
                destination_city_id = f.DestinationCityId,
                product = f.Product,
                source = f.Source,
                transport_mode = f.TransportMode,
                slot_index = f.SlotIndex,
                occurrence = occurrence,
                paused = f.Paused,
                wait_for_full_vehicle = f.WaitForFullVehicle,
                dormant_auto_warehouse = f.DormantAutoWarehouse,
                max_send = maxSend,
                min_keep = MinKeep(f.MinKeep, uiLabelValidated),
                distance_tiles = f.DistanceTiles.HasValue && f.DistanceTiles.Value > 0 ? f.DistanceTiles : null,
                path_status = f.DistanceTiles.HasValue && f.DistanceTiles.Value > 0 ? "cached" : "unavailable",
                dispatch_formula = f.DispatchFormula,
                vehicle_capacity = f.VehicleCapacity,
                dispatch_amount_now = DispatchAmount(f, maxSend.value),
                in_flight = f.InFlight ?? new InFlightDto(),
                destination_stock = f.DestinationStock,
                destination_incoming_reserved = f.DestinationIncomingReserved,
                destination_free_space = f.DestinationFreeSpace,
                destination_slots = f.DestinationSlots,
                origin_stock = f.OriginStock,
                validation_error = f.ValidationError,
                has_error = f.HasError,
                destination_accepts_product = f.DestinationAcceptsProduct,
                destination_dead_city = f.DestinationDeadCity,
                errors = new List<string>(),
            };
            // dispatch_cost 0 with no cached path means "no estimate" (PRD 12.3).
            dto.dispatch_cost = dto.path_status == "cached" && f.DispatchCost.HasValue && !float.IsNaN(f.DispatchCost.Value) && !float.IsInfinity(f.DispatchCost.Value)
                ? f.DispatchCost
                : null;
            if (dto.path_status == "unavailable") dto.errors.Add("no_path");
            if (f.DestinationModuleMissing) dto.errors.Add("destination_module_missing");
            if (f.AutoMax && !f.AutoMaxValue.HasValue)
            {
                dto.max_send.unlimited = false;
                dto.max_send.headroom_now = null;
                dto.errors.Add("auto_max_send_unavailable");
            }
            if (f.HasError) dto.errors.Add("validation_error:" + (f.ValidationError ?? "unknown"));
            if (f.DestinationDeadCity) dto.errors.Add("destination_dead_city");
            if (!f.DestinationAcceptsProduct) dto.errors.Add("product_not_accepted");
            return dto;
        }

        /// <summary>Assigns the occurrence index n among slots with an identical (origin, product, destination, source) tuple, in slot order.</summary>
        public sealed class OccurrenceCounter
        {
            private readonly Dictionary<string, int> _counts = new Dictionary<string, int>(StringComparer.Ordinal);

            public void Reset()
            {
                _counts.Clear();
            }

            public int Next(RouteFacts f)
            {
                string tuple = f.Origin + "|" + f.Product + "|" + f.Destination + "|" + f.Source;
                int n;
                _counts.TryGetValue(tuple, out n);
                _counts[tuple] = n + 1;
                return n;
            }
        }
    }
}
