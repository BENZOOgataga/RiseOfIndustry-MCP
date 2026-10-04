# Logistics: destinations, dispatch, requests, vehicles

> **Errata from the asset dump (2026-10-03, CONFIRMED, see `notes/static-dump.md`):**
> - Dispatch cost formula assets are now known: `ManualDestinationDispatchCost` = (250 + distance × 10) × difficulty × actor; depot/terminal variants in `notes/static-dump.md` §2.

Target: original **Rise of Industry** (Steam App 671440, Unity 2018.4.11 Mono, build 9064059). Not RoI 2.
Method: static reading of ILSpy output only. No game process, install, or save folder was touched.

All paths below are relative to
`research/_local/decompiled/Assembly-CSharp/ProjectAutomata/` unless prefixed otherwise.
Line numbers refer to that decompiled output.

Confidence tags:
- **CONFIRMED**: read directly in the source.
- **HIGH**: strongly implied by the source, but one link is in prefab/asset data we do not have.
- **INFERRED**: a reasonable reading, not proven.
- **UNKNOWN**: not determinable from the code.

Storage tags:
- **static**: prefab/GameData asset configuration.
- **save**: `[SavegameSerialized]`, so it persists in the save.
- **runtime**: rebuilt at load, not saved.

---

## 0. TL;DR for the example

> Gas Well 2 -> gas -> Hardware Store (Ales) max_send 8 min_keep 1; Petrochemical Plant 5 max_send 3 min_keep 0

| Example field | Source of truth | Notes |
|---|---|---|
| "Gas Well 2" | `building.buildingName` of the origin (`ManualDestinationManager.building`) | The name is user-renamable and not unique. Use a stable key (section 7). |
| destination list | `building.manualDestinations.slots` (`List<ManualDestinationSlot>`) | Only slots with `hasValidDestinationAndProduct` are real routes. |
| product "gas" | `slot.product` (`ProductDefinition`; `.productName` is display, `.name` is the stable asset key) | |
| destination "Hardware Store" | `slot.destination` (`BuildingLogistics`), `.userName` / `.building.buildingName` | |
| "(Ales)" | `slot.destination.settlement.settlementName` (`BuildingBehaviour.settlement`, which is `building.settlement`) | Null for buildings outside a town. |
| **max_send 8** | `slot.maxAcceptedAtDestination`, which reads **the destination's `productStorage.GetMaxAccepted` for the product**, or Shop demand when `autoMaxAccepted` is set | This is a cap on the **destination's stock**, not a per-trip amount. It is **stored on the destination building and shared** by every origin that ships that product there. 0 means unlimited (∞). |
| **min_keep 1** | `slot.minStoredAtSource` (`int`, saved private `_minStoredAtSource`) | 0..99. `int.MaxValue` means "keep all" (∞). |

The example's "max_send" is the UI "max amount" counter. In code it is `ManualDestinationSlotViewModel.maxAmount`, which reads `slot.maxAcceptedAtDestination`. The actual on-screen label text is inside a UI prefab that we do not have, so the label-to-member link is **HIGH**. The member mapping itself is **CONFIRMED**.

---

## 1. Max Send / Min Keep: UI to model trace

### 1.1 View model: `ManualDestinationSlotViewModel.cs`

The UI uses MVVM bindings to properties. The bindings and labels live in prefabs, not in code.

| VM member (line) | Reads | Confidence |
|---|---|---|
| `maxAmount` (63) | `slot.maxAcceptedAtDestination` (0 when there is no slot) | CONFIRMED |
| `autoMaxAmount` (65) | `slot.autoMaxAccepted` (when a slot exists) | CONFIRMED |
| `showMaxAmountToggle` (67-77) | true only if the destination has a `ShopWarehouseClient` component, so the "auto" toggle appears only for shop destinations | CONFIRMED |
| `minAmount` (79) | `slot.minStoredAtSource` (0 when there is no slot) | CONFIRMED |
| `maxAmountIsInfinite` (81-91) | `slot.destinationAcceptsInfiniteAmount` (that is, max is 0) | CONFIRMED |
| `minAmountIsInfinite` (93-103) | `slot.sourceKeepsAll` (that is, min is at least `int.MaxValue`) | CONFIRMED |
| `canSetMaxAccepted` (105-115) | true when `autoMaxAmount` is off and the destination storage is not an `InfiniteStorage` | CONFIRMED |
| `paused` (121), `waitTillVehicleFull` (125), `canWaitTillVehicleFull` (123) | slot fields | CONFIRMED |
| `distance` (35), `dispatchCost` (37), `dispatchCostString` (39) | `slot.distance`, `slot.dispatchCost` | CONFIRMED |
| `destinationName` (31) | `slot.destination.userName`, which is `building.buildingName` | CONFIRMED |
| `settlementName` (43) | `slot.destination.settlement.settlementName` | CONFIRMED |
| `productsInStorage` (166-176) | `min(99, origin storage count of product)` | CONFIRMED |

**The Max setters write the destination building's storage, not the slot:**
- `SetMaxAmount` (`ManualDestinationSlotViewModel.cs:253-259`) parses the text as an integer. If parsing succeeds, it calls
  `SetMaxAccepted` on the destination's `productStorage` for the slot's product with that value. If there is no slot or
  destination, nothing happens.
- `AddToMaxAmount` (`:308-320`) computes new value = current `GetMaxAccepted` for the product + delta, and compares it with the
  storage's slot count for that product (`GetSlots`). Below 0, it wraps to the slot count. Above the slot count, it wraps to 0 (∞).
  Otherwise it stores the new value as is.

**The Min setters write the slot:**
- The text setter (`:261-267`) stores `int.MaxValue` ("keep all") in `slot.minStoredAtSource` when the parsed value is above 99,
  otherwise the parsed value.
- `AddToMinAmount` (`:322-343`) clamps to 0..99; stepping past 99 or below 0 jumps to `int.MaxValue` ("keep all").

### 1.2 Model: `ManualDestinationSlot.cs`

- `maxAcceptedAtDestination` (`ManualDestinationSlot.cs:159-173`), evaluated in this order:
  1. 0 when the slot has no destination or no product.
  2. When `autoMaxAccepted` is on and the destination provides an auto-max provider (`destinationAutoMaxAcceptedProvider`), that
     provider's `GetAutoMaxAccepted` for the product and the origin's owner.
  3. Otherwise, the destination `productStorage.GetMaxAccepted` for the product.
- `minStoredAtSource` (`:175-185`) is backed by `_minStoredAtSource`. Its setter clamps negative values to 0.
- `destinationAcceptsInfiniteAmount` (`:187`) is true when `maxAcceptedAtDestination` is 0.
- `sourceKeepsAll` (`:189`) is true when `minStoredAtSource` is at least `int.MaxValue`.

- `IAutoMaxAcceptedProvider` (`IAutoMaxAcceptedProvider.cs:3-6`) is implemented only by `Shop` (`Shop.cs:11`). `Shop.GetAutoMaxAccepted` returns the shop's `GetDemand` for that product and actor (`Shop.cs:642-645`), which is round(base demand for the product (`_demand`) × `actorDemandModifier` × the settlement's `GetDemandMultiplier`) (`Shop.cs:234-246`). When auto is on, Max therefore equals the shop's current demand for that product. **CONFIRMED**
- Per-destination storage of the Max value **(CONFIRMED)**:
  - `ProductSpecificProductStorage._maxAcceptedMap` (`MaxAcceptedDictionary`, keyed at runtime by `ProductDefinition.AssetId` = `GetInstanceID()`, saved by asset `name`; `ProductSpecificProductStorage.cs:17,59-77`, `MaxAcceptedDictionary.cs`). `SetMaxAccepted` clamps to `slots` and maps negative values to `slots`.
  - `SingleProductStorage._maxAccepted` (`SingleProductStorage.cs:16,73-75,152-155`). One value regardless of product.
  - `InfiniteStorage._maxAccepted` (`InfiniteStorage.cs:23,158-178`), with defaults `_defaultMaxAcceptedTo/From`.
  - `ModuleSharedProductStorage` delegates to the module owner's storage (`ModuleSharedProductStorage.cs:165-178`).
- `ManualDestinationManager.OverrideMaxAcceptedAtDestination` (`ManualDestinationManager.cs:82-93`): when a slot's destination or product changes and the current max is 0, it seeds a default from the origin's `TryGetDefaultMaxAcceptedFrom` or the destination's `TryGetDefaultMaxAcceptedTo`. Only `InfiniteStorage` can return true here.

### 1.3 What actually gets sent per trip

The amount per trip is derived, never stored. It is computed in `ManualDestinationManager.GetRequestedAmount` (`ManualDestinationManager.cs:595-626`). **CONFIRMED**

Inputs and steps (behaviour summarised from the decompiled source; not reproduced here):
1. **cap** = vehicle capacity for the slot's network and product (`fleet.GetVehicleCapacity`, which uses a tech override or the
   vehicle prefab's storage slot count).
2. **available** = max(origin stock of the product − `minStoredAtSource`, 0).
3. **free** = the destination storage's free space for the product (`FreeSpace`, with put reservations not ignored and pull
   reservations ignored, for the origin owner).
4. If Max (`maxAcceptedAtDestination`) is above 0: in-destination = destination slot count for the product − free (that is, stored
   plus incoming reservations), and free = min(free, max(Max − in-destination, 0)).
5. If there is a contract with the destination owner for the product: free = min(free, contract amount − (delivered + reserved)),
   and cap = min(cap, free).
6. If an active dynamic world event targets the destination: free = min(free, the event's needed amount of the product
   (`NeededProductAmount`)).
7. **amount** = min(cap, available, free).
8. If "fill vehicles" (`waitTillVehicleFull`) is on and amount is below cap, amount becomes 0 (no dispatch).

Interpretation:
- "Max" is a ceiling on destination stock, including in-flight deliveries.
- "Min/Keep" is a floor on origin stock.
- A single request can exceed one vehicle's capacity only through contracts. In practice amount ≤ cap, and the dispatcher splits a request into ceil(amount/cap) vehicle jobs (`TransportJobVehicleDispatcher.cs:8-19`).

So the example's "max_send 8 to Hardware Store" means "keep at most 8 gas in the Hardware Store, counting incoming deliveries". Another gas producer with a slot to the same store **shares the same value 8**.

---

## 2. Enumerating destination slots per building

### 2.1 Entry points

| Concept | Path | Member / type | Storage | Confidence |
|---|---|---|---|---|
| All buildings | `ManagerBehaviour<BuildingManager>.instance.buildingsList` (`BuildingManager.cs:27`) | `List<Building>` | runtime | CONFIRMED |
| Per-actor buildings | `IActor.buildings` (`IActor.cs:19`) | `IActorBuildingCollection` | runtime | CONFIRMED |
| Destination manager | `Building.manualDestinations` (`Building.cs:173`) | `ManualDestinationManager` (LazyComponentRef, i.e. cached GetComponent) | component | CONFIRMED |
| Slots | `ManualDestinationManager.slots` (`ManualDestinationManager.cs:76`) | `List<ManualDestinationSlot>` (runtime list; saved via `_savegameSlots` in `OnSavegameSerialize`, line 341-349) | save | CONFIRMED |
| Slot count | `slotCount` (44-62): `_slotCount` (default 3, prefab) or `int.MaxValue` if `infiniteSlots` | int | static/runtime | CONFIRMED |
| `infiniteSlots` (64-74) | true if `_infiniteSlots` is set or the building owner's `infiniteManualDestinations` is true (`Actor.cs:20,73`) | bool | | CONFIRMED |
| Warehouse variant | `WarehouseManualDestinationManager : ManualDestinationManager` (`WarehouseManualDestinationManager.cs:8`) adds "sources" (station/airfield modules) | | | CONFIRMED |

In infinite-slot mode, empty slots are pruned and one empty slot is always inserted at index 0 (`UpdateSlots` 165-186, `OnSlotDestinationChanged` 503-514). Slot indices therefore shift whenever the user adds or removes a destination. **Do not use the slot index as an identity.**

### 2.2 Per-slot fields (`ManualDestinationSlot.cs`)

| Field | Line | Type | Save | Read safety | Conf. |
|---|---|---|---|---|---|
| `destinationManager` | 12 | `ManualDestinationManager` | save | public field, safe | CONFIRMED |
| `origin` | 42 | `BuildingLogistics` (the destination manager's `logistics`) | derived | safe | CONFIRMED |
| `destination` / `_destination` | 23, 58-82 | `BuildingLogistics` | save | getter safe; **setter fires events** | CONFIRMED |
| `source` / `_source` | 26, 84-99 | `ManualDestinationSource` (null = building's own default fleet) | save | getter safe; **setter fires events** | CONFIRMED |
| `product` / `_product` | 29, 130-145 | `ProductDefinition` | save | getter safe; **setter fires events** | CONFIRMED |
| `paused` | 20 | bool (public field) | save | read safe | CONFIRMED |
| `_waitTillVehicleFull` / `waitTillVehicleFull` | 32, 195-205 | bool ("fill vehicles") | save | safe | CONFIRMED |
| `_minStoredAtSource` / `minStoredAtSource` | 35, 175-185 | int (Min/Keep) | save | safe | CONFIRMED |
| `_autoMaxAccepted` / `autoMaxAccepted` | 38, 46-56 | bool | save | safe | CONFIRMED |
| `maxAcceptedAtDestination` | 159-173 | int (Max) | derived (destination storage) | see section 5 (possible dict insert) | CONFIRMED |
| `activeRequests` | 15 | `List<TransportRequestHandle>` | save | safe to read; never mutate | CONFIRMED |
| `validationError` / `hasError` | 17, 191 | `TransportError` | runtime | safe | CONFIRMED |
| `distance` | 101-116 | int | derived | cheap cache lookup; 0 if no cached path | CONFIRMED |
| `dispatchCost` | 118-128 | float | derived | formula eval; main thread only | CONFIRMED |
| `productsInStorage` | 44 | int (origin storage count) | derived | safe | CONFIRMED |
| `hasValidDestinationAndProduct` | 147-157 | bool | derived | safe | CONFIRMED |

### 2.3 Transport mode per slot

- `GetTransportForSlot` (a `BuildingTransportManager`) and `GetNetworkForSlot` (a `NetworkManager`) (`ManualDestinationManager.cs:260-276`)
  return the slot source's `transport` / `network` when the slot has a source, otherwise the manager's own `transport` / `_network`.
- The manager's `_network` is the Road network when its `networkName` is empty, otherwise the network looked up by that name through
  `ConnectivityNetworkManager.GetNetwork` (line 806).
- `ManualDestinationSource.network` is the world network named by its fleet's vehicle prefab `networkName` (`ManualDestinationSource.cs:25-28`).

- Mode = the `networkName` of the network returned by `GetNetworkForSlot`. Known names in code: `"Road"` (`ConnectivityNetworks.cs:15`), `"Rail"` (`ConnectivityNetworks.cs:27`, `TrainVehicle.cs:76`), `"Water"` (`WaterNetworkManager.cs:107`). The air network is an `AirNetworkManager`, but its `networkName` is set in prefab data, so its exact string is **UNKNOWN** (probably "Air"). **CONFIRMED** for Road and Rail.
- `GetNetworkForSlot` and `GetTransportForSlot` are **public** and side-effect free, so they are safe to call.
- For warehouse sources (train station or airfield modules), the effective endpoint is the matching module at the destination: `GetDestinationForSlot` (public, 255-258), which calls `GetDestinationForSource` (236-253). This returns `null` if the destination has no matching module. **CONFIRMED**
- Ship transport: `WaterNetworkManager` exists, but no ship vehicle or dispatcher classes were found. **INFERRED**: there is no ship transport in base RoI.
- The "depot" UI (`ManualSlotDepotItemViewModel`) is the source selector: "default" (own trucks) versus each `ManualDestinationSource` module prefab.

### 2.4 Distance and dispatch cost (details in section 5)

- `distance` = the network's `CalculateDistance` applied to the cached path. Road and Rail return the path's `Count` (tile count; `NetworkManager.cs:29-32`). Air returns the straight-line tile distance between the first and second path nodes (`AirNetworkManager.cs:38-41`).
- `dispatchCost` = the payment handler's `GetVehicleDispatchCost` for the origin, destination, vehicle prefab and distance, with a product amount of 1 (`TransportManagerUser.cs:143-164`). For `DefaultTransportRequestPaymentHandlerBehaviour`, this evaluates its `formula` with four variables: distance, productAmount, actor (the `VehicleDispatchCostModifier` for the network and region) and difficulty (`difficultyParams.dispatch`) (`DefaultTransportRequestPaymentHandlerBehaviour.cs:40-49`, `TransportRequestPaymentHandlerBehaviour.cs:98-102`). The formula text comes from GameData, so the actual equation is **UNKNOWN** from code. The cost is **per vehicle dispatch**, not per unit.

### 2.5 Dispatch scheduling (FYI)

- `TransportManagerUser.CustomUpdate` (`TransportManagerUser.cs:121-127`) runs on a `RandomAwait interval`. When the building has the `UserEnabled` flag and is not in auto-warehouse mode (`CanMakeTransportRequests`, `ManualDestinationManager.cs:778-785`), it calls `MakeTransportRequests` (787-798).
- `ManualDestinationScheduler.TryGetNextSlot` (`ManualDestinationScheduler.cs:46-64`) round-robins across slots and per product. It skips paused slots, slots without a destination, and slots whose product the origin has none of. It waits while the last slot's request has not started.
- `TransportPriorityResolver.IsTransportBlocked`, asked for the product and the MANUAL type, can block manual dispatch in favour of higher-priority modes (`TransportPriorityResolver.cs:45-56`; types MANUAL/AUTO/LOGISTIC; order from `TransportPriorityOverride.value`, default `AWH_LN_MAN`).
- Saved scheduler state: `_lastDispatchedSlotIndex`, `_productToSlotIndex` (`ManualDestinationScheduler.cs:26-33`).

### 2.6 Auto-warehouse mode

When `BuildingLogistics.options` has `LogisticsUserOptions.AUTO_WH` set (`LogisticsUserOptions.cs`, `BuildingLogistics.cs:62-81`), manual slots are inactive. The manager is disabled and its paths are removed (`ManualDestinationManager.cs:658-699`). The building instead pushes to `IWarehouseClient.warehouse` (the manual target when one is set, otherwise the closest warehouse).

The UI shows predictions (`DestinationsPanelViewModel.autoWhDispatches` 143-178), built from `IWarehousePushPredictor.GetPredictedPush()`. For recipe buildings, that is one `PredictedRecurringTransport` per recipe output, carrying the product definition, the amount, the warehouse, and a `daysInterval` equal to the recipe's `gameDays` (`RecipeWarehouseClient.cs:62-85`).

**An observer must check whether `logistics.options` includes the `AUTO_WH` flag before reporting slots as active.** **CONFIRMED**

---

## 3. Logistic Requests vs manual destinations

| Aspect | Manual destinations | Logistic requests ("Logistics/Requests" panel) |
|---|---|---|
| Owner | Any building with `ManualDestinationManager` (push from origin) | Buildings with a `LogisticNetworkEndpoint` (warehouse-type `MultiModuleOwner` hubs), as a **pull** request |
| Data | `ManualDestinationSlot` (plain class, saved) | `LogisticRequest : MonoBehaviour` pooled entity (`LogisticRequest.cs:9`, `[SavegameEntityObject]`) |
| Resolver | Per-building scheduler | Actor-level `LogisticNetworkAgent` (`LogisticNetworkAgent.cs:8`) with `RequestResolver` (`SmartRequestResolver` / `SimpleRequestResolver`, using `GreedyTransportSolver` / `SmartTransportSolver`) choosing among providers through "graphs" (depot module prefabs such as truck depot, train station, airfield: `NetworkGraph`) |
| Settings | min keep / max at destination / fill / pause / source | `requestedAmount` (`_amount`; `int.MaxValue` = infinite, fill to capacity), `priority`, `active`, `useFullVehicles`, `allowedGraphs` (set of depot prefabs), `nameOverride` |
| Tracking | none per slot (global per-product only, see section 6) | `LogisticRequestExpensesTracker` (this month / last month) |

Enumeration:
- Per endpoint: `building.GetComponent<LogisticNetworkEndpoint>().requests` (`ReadOnlyList<LogisticRequest>`, `LogisticNetworkEndpoint.cs:18`; saved `_requests` line 13).
- Per actor: `actor.logisticNetwork` (`IActor.cs:35`, `ILogisticNetworkAgent`). `LogisticNetworkAgent._requests` is saved (line 19). The public `GetRequests(preAlloc)` (185-190) allocates from `ListPool` if `preAlloc` is null; pass your own list.
- `LogisticRequest` read members (`LogisticRequest.cs`): `endpoint` (62), `product` (68), `requestedAmount` (85), `infinite` (97), `remainingAmount` (111), `amountBeingMoved` (113-124; sums `LogisticActionTicketHandle.amount`), `fulfilled` (126), `priority` (130), `active` (142), `useFullVehicles` (154), `allowedGraphs` (166), `possibleGraphs` (168), `expenses` (170), `nameOverride` (172). Avoid `requestName` (184), which goes through localization.
- An endpoint also has `isPullDisabled` (`LogisticNetworkEndpoint.cs:20`, saved) and `connectedUsers` (`List<LogisticNetworkUser>`, the depot modules).
- `LogisticNetworkUser` (depot module): `logisticFleet` / `endpointFleet` (`JITVehicleFleet`), `logisticNetwork` / `endpointNetwork` (`NetworkManager`), `vehicleCapacity` (`LogisticNetworkUser.cs:10-26`).
- Global toggle: `GameOptions.logisticRequestsEnabled` (`GameOptions.cs:173`). If false, the player's requests are ignored (`LogisticNetworkAgent.cs:163-166`).

Confidence: **CONFIRMED** for structure. The panel's visual labels are UNKNOWN (prefab).

---

## 4. Vehicles

### 4.1 Classes

| Class | File | Role |
|---|---|---|
| `Vehicle : MonoBehaviour` | `Vehicle.cs:10` | Base for trucks and planes. `[SavegameEntityObject]`. **Not ECS.** Pooled GameObjects. |
| `TrainVehicle : Vehicle` | `TrainVehicle.cs` | Forces `networkName` to "Rail". Wagon count = floor(cargo count × `wagonsPerProduct`). |
| Movers | `TruckMover.cs`, `TrainMover.cs`, `AircraftMover.cs` (`IMover`) | Movement |
| `JITVehicleFleet : BuildingBehaviour` | `JITVehicleFleet.cs:10` | Per-building fleet. `_vehicles` (saved HashSet of GUID refs), `vehiclePrefab`, `maximumVehicleAmount` (100), `infiniteAmount`, `activeVehicleCount`, `inactiveVehicleCount`, `GetVehicleCapacity(product[, actor])` |
| `VehicleDispatcher` (abstract) | `VehicleDispatcher.cs:9` | `fleet`, `maxDispatchedVehiclesDaily`, saved `_tickets`, `_dispatchQueue`, `_dispatchedVehicleCountThisMonth`, `_dispatchTimer`; `pendingJobCount`, `availableVehicles` |
| `LandTransportJobVehicleDispatcher`, `AirTransportJobVehicleDispatcher` | `TransportJobVehicleDispatcher<T>` | Create `LandTransportJob` / `AirTransportJob` |
| `TransportJob<T> : JobPoolJob<T> : Job` | `TransportJob.cs:8` | Saved `_origin`, `_destination` (`BuildingLogistics`), `_product`, `_productAmount`, `_originToDestinationPath`, `_destinationToOriginPath`, `_operation` (`TransportOperation` DELIVER_TO / PICKUP_FROM), put/pull reservations. All **protected** (reflection needed). |
| Other jobs | `GoHomeJob`, `HarvesterJob`, `AnonymousJob` | |
| `VehicleDispatchTicket` | `VehicleDispatchTicket.cs:7` | `state` (`VehicleDispatchTicketState`: INVALID, NEW, DOING, DONE, CANCELED), `dispatcher`, `dispatchedVehicles`, `jobsQueued` |
| `VehicleQueue` | `VehicleQueue.cs:10` | Parking/queue at buildings. `capacity` 8. `IsQueueFull()` blocks dispatch. |

Trains use the land job and dispatcher (no train-specific job class exists). **INFERRED** from the absence of one plus `TrainVehicle.networkName` being "Rail".

### 4.2 Where active vehicles live

- `ManagerBehaviour<VehicleManager>.instance.vehicles` (`HashSet<Vehicle>`, `VehicleManager.cs:29`) holds every active vehicle. Inactive vehicles sit in a private per-owner, per-prefab pool and are `SetActive(false)`. `activeVehicles` (27) is the count. **CONFIRMED**
- Per building: `JITVehicleFleet._vehicles` (private; `activeVehicleCount` is public).
- Enumerating `VehicleManager.vehicles` with foreach is fine on the main thread. Copy it, because the set mutates during `Update` (`VehicleManager.cs:106-127`).

### 4.3 Per-vehicle reads

| Member | Line | Notes |
|---|---|---|
| `id` (ushort) | `Vehicle.cs:17` | Saved. Assigned from `VehicleManager.vehicleIdCounter` on every pool pull (`VehicleManager.cs:48,91-99`), so **it changes each trip** (the vehicle is re-pulled per job). Wraps at 65535. |
| `vehicleName` | 12 | Prefab string |
| `networkName`, `networkManager` | 47, 117-127 | Road / Rail / air name |
| `fleet` | 77-87 | Owning `JITVehicleFleet`. `fleet.building` is the dispatching building. |
| `activeJob` | 89 | `Job`. Cast to `TransportJob<…>` for origin, destination, product, amount (protected fields). `job.tooltipData.origin` / `.destination` are public `GameObject`s (`VehicleTooltipData.cs`). |
| `productStorage` | 129-139 | Cargo. Enumerate `Product{definition, amount}`. |
| `IsGoingHome()` | 310-317 | Pure |
| `GetOwner()` | 278 | `IActor` |
| `queue` | 25 | Vehicles in a queue are updated by the queue, not by the manager |

Costs: dispatch cost is paid once per vehicle when it is dispatched (`BuildingTransportManager.OnVehicleDispatched` 447-460, then `TransportRequest.PayVehicleDispatch`, then `DefaultTransportRequestPaymentHandlerBehaviour.PayVehicleDispatch` 22-29, which sends money and adds to `_paidForTheMonth`). Vehicles have no per-vehicle upkeep in this code path. **CONFIRMED**

### 4.4 ECS

`Assembly-CSharp/ECS*` contains only `ECSCleaningManager`, `ECSEntityManager`, `DestroyEntityComponent`, and `DestroyEntitySystem`. None of them reference `Vehicle`. **Vehicles are not ECS entities.** **CONFIRMED**

---

## 5. Cost of `distance` / `dispatchCost`, and what not to call

### 5.1 `ManualDestinationSlot.distance` (line 101-116): cheap, no pathfinding

The chain is `GetNetworkForSlot`, then `BuildingTransportManager.TryGetPath`, then `PathCache.TryGetPath` (`PathCache.cs:53-61`). That is a `Dictionary<int,CachedPath>` lookup keyed by the `GetInstanceID()` hash of origin, destination, and network, followed by `CachedPath.TryGetPath` (`CachedPath.cs:86-90`), which returns the `_path.tiles` list if the path is ready and was found. Then `CalculateDistance` is `path.Count`.

There is no pathfinding and no allocation. It returns 0 if the path is not cached or not found. **CONFIRMED**

Caveat: `CachedPath` paths are produced on worker threads (`PathfinderOptions.MULTITHREADED`, `_pathLock`). `wasFound` / `tiles` are read without the lock, and paths are returned to `Pathfinding.pathPool` on recalculation. **Read on the Unity main thread only.**

### 5.2 `ManualDestinationSlot.dispatchCost` (118-128): cheap, main thread only

The chain is `GetPredictedVehicleDispatchCost`, then `GetPredictedVehicleDispatchCostCommon` (cache lookup, dispatcher dictionary lookup), then `formula.Evaluate`. `Formula.Evaluate` takes a dictionary from a **static, non-thread-safe `ObjectPool`** (`Formula.cs:11,32-36`). There is no pathfinding. It returns 0 if there is no path. **CONFIRMED**

### 5.3 Members that trigger pathfinding (do NOT call)

- `BuildingTransportManager.AddPath(...)` (169-172), which calls `PathCache.AddPath` / `AddPathOneWay`, then `UpdateCachedPath` and `vehiclePathfinder.FindPath` (`PathCache.cs:86-114`, `CachedPath.cs:106-129`).
- `PathCache.UpdateCachedPath` / `UpdatePaths`, `CachedPath.UpdatePath` (can call `PathRebuildManager.Rebuild`).
- `TransportManagerUser.CommonValidation(...)` (protected, 82-109). It calls `AddPath` on failure.

### 5.4 Members with side effects disguised as reads

- `ProductSpecificProductStorage.GetMaxAccepted(product)` (`ProductSpecificProductStorage.cs:59-68`) uses `Utils.GetSafe` with a default of 0, which **inserts an entry with value 0 into the saved `_maxAcceptedMap` if the key is absent** (`Utils.cs:269-278`). The value 0 is semantically the default (∞), so this is benign for gameplay, and the game itself calls it constantly. It is still a dictionary mutation. Call it only on the main thread, or read `_maxAcceptedMap` via `TryGetValue` (reflection) and treat a missing key as 0. This means `slot.maxAcceptedAtDestination` inherits the issue. **CONFIRMED**
- `GuidMapper.instance.GetGUIDForObject(obj)` (`GuidMapper.cs:38-47`) **creates and registers a new GUID** if the object has none. Use reflection on the private `objMappings` with `TryGetValue` instead (section 7).
- `ITransportRequestValidatorBehaviour.ValidateRequest` / `TransportRequestHandle.Validate()` (`TransportRequest.cs:86-89`). For manual destinations this runs `ValidateRequestForSlot` (`ManualDestinationManager.cs:351-394`), which removes errors, cleans up requests, may call `AddPath`, and adds errors. For `ManualDestinationManager.ValidateRequest` (892-899) it **throws** if the request is unknown. Never call.
- `DestinationsPanelViewModel.autoWhDispatches` getter (143-178) mutates the VM's internal lists. It is a UI object; recompute from `IWarehousePushPredictor` instead.
- `VehicleManager.NextVehicleIndex()` increments the saved counter.
- `LogisticRequest.requestName` hits localization (harmless but unnecessary).
- `ListPool`-returning helpers (`GetRequests(null)`, `GetSourcePrefabs(null)`, `GetTransportErrors(null)`, `GetPredictedPush(null)`) pull from static non-thread-safe pools. Always pass your own list, and call only on the main thread.

### 5.5 Allocation notes

`ReadOnlyList<T>` is a struct (`ReadOnlyList.cs:6`), so getters like `VehicleDispatcher.tickets` / `LogisticNetworkEndpoint.requests` do not heap-allocate. `LazyComponentRef<T>` (struct) caches `GetComponent`.

---

## 6. Logistics history / expense tracking

| Tracker | Location | Granularity | Storage | Conf. |
|---|---|---|---|---|
| Per-product distribution (dispatch) cost time series | `ProductionStatsTracker._distributionCostRecord : Dictionary<ProductDefinition, AggregatedCostTimeTree>` (`ProductionStatsTracker.cs:39`). Fed by `BuildingTransportManager.paidForDispatch` (`:574, 638-641`) for **player-owned** buildings only (`:555`). | actor x product x month | save (`_savegameDistributionCostRecord`) | CONFIRMED |
| Per-building monthly dispatch cost | `DefaultTransportRequestPaymentHandlerBehaviour._paidForTheMonth` (saved). Logged to `BuildingAnalysis` under `analysisItem` at month end (`DefaultTransportRequestPaymentHandlerBehaviour.cs:16,51-58`). | building x month | save | CONFIRMED |
| Per-dispatcher monthly vehicle count | `VehicleDispatcher._dispatchedVehicleCountThisMonth`, logged to `BuildingAnalysis` under `dispatchCountAnalysisItemDef` (`VehicleDispatcher.cs:26-27, 192-199`) | building x month | save | CONFIRMED |
| Read history | `BuildingAnalysis.GetValues(itemDef, from, to)` / `GetOverallValue(itemDef, …)` (`BuildingAnalysis.cs:144-186`) | | save | CONFIRMED |
| Logistic request expenses | `LogisticRequest.expenses` (`LogisticRequestExpensesTracker`: `expensesThisMonth`, `expensesLastMonth`; `LogisticRequestExpensesTracker.cs`) | request x month (2 values) | save | CONFIRMED |
| Per-slot history | **none**. Manual slots keep no history. Per-slot cost can only be estimated as `dispatchCost` × trips. | | | CONFIRMED (absence) |
| Money bill category | `DefaultTransportRequestPaymentHandlerBehaviour.billCategory` (`MoneyBillCategory`), from `EasyMoneySend` | via MoneyManager ledger | | HIGH (ledger not traced here) |

The `AnalysisItemDefinition` asset names for these items are prefab/GameData, so they are **UNKNOWN** from code.

---

## 7. Identity

| Object | Stable key options | Notes | Conf. |
|---|---|---|---|
| `Building` | (a) **`prefab.name` + `tile`**. `tile` is `NonSerialized` but recomputed from saved `constructorParams` (x, y) at load (`Building.cs:276-300`), and two buildings cannot share an origin tile (`BuildingManager.GetBuilding(tileIndex)`). Stable across sessions. (b) Savegame GUID via `GuidMapper` (private `objMappings`, a dictionary from object to `Guid`). Restored from the save on load (`SavegameManager.cs:319,344`), lazily created for new objects (`GetGUIDForObject`), and **reset on each load** (`SavegameManager.cs:15`). Stable after the building has been saved once; read with reflection `TryGetValue`, never `GetGUIDForObject`. (c) `GetInstanceID()`: session-only. | `buildingName` is user-editable (`SetBuildingName`, `Building.cs:376-385`) and duplicates are possible. Do not use it as a key. | CONFIRMED |
| `BuildingLogistics` | Same as its `building`. | Component on the building GameObject (or on a module for warehouse sources). | CONFIRMED |
| `ManualDestinationSlot` | **No id.** Use (origin building key, product.name, destination building key, source prefab name). Slot index is unstable (section 2.1). Duplicate slots with the same (product, destination) are possible; `TryGetSlot` returns the first match. | | CONFIRMED |
| `ProductDefinition` | `.name` (asset name). The save uses `asset.name` (`MaxAcceptedDictionary.cs`, `ManualDestinationScheduler.cs:14`). `AssetId` = `GetInstanceID()` is session-only (`ProductDefinition.cs:51,102`). | `productName` is display text | CONFIRMED |
| `Vehicle` | `id` (ushort) is reassigned on every pool pull, so it is **not stable per physical vehicle**. GUID via `GuidMapper` as above. Use per-trip identity (vehicle object plus job origin/destination). | | CONFIRMED |
| `TransportRequestHandle` | `id` (ulong) from `SequentialPooledEntityManager`. Valid while pooled. | | HIGH |
| `LogisticRequest` | No id. Endpoint building key plus product, or the GUID. | | CONFIRMED |

---

## 8. Setters and methods with side effects (observer must avoid)

**`ManualDestinationSlot`**
- `destination` setter: fires `destinationChanged`, then `OnSlotDestinationChanged` (`ManualDestinationManager.cs:485-516`). That cancels requests, removes or adds paths (pathfinding), seeds the default max, inserts or removes slots, and resets `autoMaxAccepted`. It also (un)subscribes `onAcceptedUpdated`.
- `source` setter: fires `sourceChanged`, which cancels requests and changes paths (538-562).
- `product` setter: fires `productChanged`, which may clear the destination and seed the max (518-528).
- `Clear()`, `WriteMySettingsTo()`.
- Writing `minStoredAtSource`, `autoMaxAccepted`, `waitTillVehicleFull` or `paused` is a plain write with no events, but it changes game state.

**`ManualDestinationManager`**
- `slotCount` setter, `UpdateSlots`, `UpdateSlotsNewOnly`, `ClearSlot`, `ClearAllSlots`, `CancelRequest`, `CancelSlotRequests`, `AddSource`, `RemoveSource`, `GetOrAddSlotWithoutDestination` (may add a slot), `WriteMySettingsTo`, `ValidateRequest`, `OnSavegameSerialize` (rewrites `_savegameSlots`).

**`ManualDestinationSlotViewModel`**
- All `Toggle*`, `Increment*`, `Decrement*`, `Set*`, `Clear`, and `LookAtDestination` (moves the camera).

**Storage**
- `IProductStorage.SetMaxAccepted`, `ClearMaxAccepted`, `Reserve`, `Put`, `Pull`. `GetMaxAccepted` inserts a key (section 5.4).

**`BuildingLogistics`**
- The `options` setter fires `optionsChanging` and `optionsChanged` (removes or adds paths, clears errors, enables or disables the manager, sets `_explicitManualMode`).
- `UpdateLogistics(...)` fires `onAcceptedUpdated` / `onOutgoingUpdated`, which may clear slots.

**`BuildingTransportManager`**
- `EnqueueRequest`, `CancelRequest`, `CancelRequestsWithDestination`, `CancelAllRequests`, `AddPath`, `RemovePath`, `OnSavegameSerialize`.

**`TransportRequest` / `TransportRequestHandle`**
- `Validate`, `PayVehicleDispatch` (sends money), `Complete`, `Fail`, `NotifyDeliveriesCompleted`, and all property setters on the handle.

**Fleet / dispatcher / vehicle**
- `JITVehicleFleet.PullVehicle`, `ReturnVehicle`, `RegisterVehicle`, `DeregisterVehicle`.
- `VehicleDispatcher.DeliverTo`, `PickUpFrom`, `OnTicketCanceled`.
- `VehicleDispatchTicket.state` setter, `Cancel`.
- `Vehicle.ExecuteJob`, `CancelJob`, `ChangeOwner` (event), `OverrideMaxSpeed`, `SetId`, `ToggleRenderers`.
- `VehicleManager.CreateFromPool`, `ReturnToPool`, `Register`, `Prewarm`, `NextVehicleIndex`.

**Logistic network**
- `LogisticRequest` setters: `product` (event, then `UpdateProductRequests`), `requestedAmount`, `priority`, `active`, `useFullVehicles`, `nameOverride`. Methods: `AllowGraph`, `DisallowGraph`, `UpdateGraphs`, `Cancel`, `AddTicket`, `Clear`.
- `LogisticNetworkEndpoint.MakeRequest`, `CancelRequest`, `SetPullStatus`.
- `LogisticNetworkAgent.AddRequest`, `RemoveRequest`, `Increase/DecreaseRequestPriority`.

**Warehouse clients**
- `IWarehouseClient.warehouse` setter, `cycles` setter, `AcquireProduct`.
- `Warehouse.SetAwhPullMode`, `UpdateOperation`, `UpdateAllOperations`.

**Misc**
- `GuidMapper.GetGUIDForObject` / `Write` (creates GUIDs).
- `AutoSendAll` (`AutoSendAll.cs`) is a separate component: every `intervalInDays` (default 5) it sends **all** stored products to the warehouse through `SendToWarehouseOperation.Send`. It is read-only for us; note only that it exists (saved `_daysSinceLastSend`).

---

## 9. Reconstruction recipe (pseudocode, observer-side, main thread, reflection-friendly)

```
for b in BuildingManager.instance.buildingsList:
  mdm = b.manualDestinations; if !mdm: continue
  autoWh = (b.logistics.options & AUTO_WH) != 0          # if set, slots are dormant
  for slot in mdm.slots where slot.destination && slot.product:
     dest  = mdm.GetDestinationForSlot(slot)             # module-resolved endpoint (public, pure)
     net   = mdm.GetNetworkForSlot(slot).networkName     # Road/Rail/Air
     max   = slot.autoMaxAccepted ? <Shop demand> : read destination storage maxAccepted (TryGetValue, missing=0) ; 0 => ∞
     min   = slot.minStoredAtSource ; int.MaxValue => keep all
     emit { origin: key(b), originName: b.buildingName, product: slot.product.name,
            destination: key(slot.destination.building), destName: slot.destination.userName,
            town: slot.destination.settlement?.settlementName, mode: net,
            max, min, paused: slot.paused, fillVehicles: slot.waitTillVehicleFull,
            autoMax: slot.autoMaxAccepted, distance: slot.distance, dispatchCost: slot.dispatchCost,
            inFlightRequests: slot.activeRequests.Count, error: slot.hasError, autoWhMode: autoWh }
```

---

## 10. Unknowns / open items

1. **On-screen label text** for Max / Min (for example "Max", "Keep", "Send") and the exact prefab bindings. UI prefabs and localization tables are not in the decompiled code. The mapping from VM to model is CONFIRMED. The mapping from label to VM is HIGH.
2. The **dispatch cost formula** text (`DefaultTransportRequestPaymentHandlerBehaviour.formula`) and the `difficulty.dispatch` values are GameData. The inputs are known: `distance`, `productAmount` (=1 for prediction), `actor` modifier, `difficulty`.
3. **Air network `networkName`** string and the exact rail and air fleet prefabs. These are prefab data.
4. **Ships**: a water network exists, but no ship vehicle or dispatcher was found (INFERRED absent).
5. Whether `Building.tile` uniqueness holds for **module** buildings (warehouse modules are separate `Building`s). This is probable but not verified. Use the GUID or InstanceID when tile collides.
6. The MoneyManager ledger by `billCategory` was not traced (see the economy notes).
