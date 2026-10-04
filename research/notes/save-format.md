# Rise of Industry (original, App 671440): save-file format

Scope: the original Rise of Industry (Unity 2018.4.11, Mono, build 9064059, save header `savegameBuild` "0507b", `saveFormatVersion` 2304). This does not cover Rise of Industry 2.
Inputs: the decompiled `Assembly-CSharp` (`research/_local/decompiled/...`) and three save **copies** in `research/_local/saves/`. Nothing was read from `%APPDATA%\RiseOfIndustry` or the game install, and the running game was not touched. SHA256 of all three copies was re-checked after analysis and still matches `HASHES.txt`.

Labels: **CONFIRMED** means verified in code and in the bytes of the copies. **HIGH CONFIDENCE** means strong code evidence or partial byte evidence. **INFERRED** means reasoned but not directly verified. **UNKNOWN** means not determined.

---

## 1. Save/load code path (decompiled source)

| Concern | Where | Finding |
|---|---|---|
| File location | `ProjectAutomata/SavegameStorage.cs` | `%APPDATA%/RiseOfIndustry/<name>.sav`. The name goes through `Utils.ReplaceIllegalNameCharacters`. The file is written with `File.WriteAllBytes` on a ThreadPool thread. The write is **not atomic** (no temp file and rename). **CONFIRMED (code)** |
| Top-level writer | `ProjectAutomata/Savegame.cs` `Serialize(Savegame, SavegameHeader)` | An `LZ4Stream` in compress mode over a memory stream wraps: `int32 headerLen`, then the BinaryFormatter `SavegameHeader`, then `SerializeRaw`. **CONFIRMED** |
| Header | `ProjectAutomata/SavegameHeader.cs` | `[Serializable]` class serialized with **plain `BinaryFormatter`**. Fields: `name`, `timestamp` (int64), `saveFormatVersion`, `savegameBuild`, `mods[] {name, version, versionString}`, `[OptionalField] module`. The load menu reads only this part (`SavegameStorage.GetAllSavegames`). **There is no thumbnail and no company name in the header.** **CONFIRMED** |
| Timestamp epoch | `Utils.GetTimestamp()` | Total seconds elapsed between `DateTime.MinValue` plus 1970 years and the current UTC time. The epoch is therefore **1971-01-01 UTC**, not Unix 1970. Example: `sample-a` gives 1759500654, which is 2026-10-03T14:10:54Z and matches the copy's mtime. **CONFIRMED** |
| Body | `Savegame.SerializeRaw` | Fields are written in this order: `SavegameMetadata.createdSavegameBuild`, `SavegameWorldData` (randomState, sizeX, sizeY, worldName, height `byte[]`, biomes `byte[]`, water `uint[]`, blocked `uint[]`, then the resourceNodes table written raw), camera (Vector3, Quaternion, float, `CameraModeType`), `achievementsEnabled`, **`Dictionary<Type, ManagerSaveData>`**, `savegameVersion`, `WorldParameters`, and then `module` as a **raw** `BinaryWriter.Write(string)` with no tag, only when version ≥ 200. **CONFIRMED** (parsing ends with 0 trailing bytes on all three copies) |
| Serializer | `ProjectAutomata/Serializer.cs` | This is a **custom tagged binary format, not a single BinaryFormatter graph.** Each value is written as `bool isNull`, then `string AssemblyQualifiedName`, then the payload. If a custom `[SerializerMethod]` is registered for the type, the payload is written by that method. Otherwise it is `int32 len` followed by a **BinaryFormatter (MS-NRBF) blob**. Registered types are: Int32, String, Single, Double, Boolean, Guid, Vector2/3, Quaternion, GameDate (d,m,y int32), SavegamePrefabIdentifier (name, type), ObjectSaveData / ManagerSaveData / EntitySaveData, BlockReason (u32), Biome (u8), `byte[]`, the `T[]` and `List<T>` of each, plus 5 specific `Dictionary<GameDate,…>` shapes. Arrays and lists are written as `int32 n` followed by n×(`bool isNull` + element). Strings are .NET 7-bit-length-prefixed UTF-8. **CONFIRMED** |
| Surrogates | n/a | **No `ISerializationSurrogate` or `SurrogateSelector`.** The BinaryFormatter blobs are vanilla NRBF. **CONFIRMED (code search)** |
| Reflection model | `SaveSystemReflection.cs`, `SavegameManager.cs` | `[SavegameManagerObject]` managers, `[SavegameEntityObject]` entities (created through a `[SavegameEntityConstructor]` static plus a `[SavegameConstructorParams] object[]`), `[SavegameEntityComponent]` components and `[SavegameSpecialSerialized]` nested objects. Only fields marked `[SavegameSerialized]` are saved. **CONFIRMED** |
| ObjectSaveData | `ObjectSaveData.cs` | Written as `string objectType AQN`, `int32 nFields`, then n×(`string fieldName`, Serializer value). `ManagerSaveData` adds `int32 nEntities` plus the entities. `EntitySaveData` adds a 16-byte GUID, its components, and `constructorParams` (Serializer values). `EntityComponentSaveData` adds a GUID. **The format is self-describing by field name**, so field order does not matter. **CONFIRMED** |
| References | `ObjectSaveData.ReadFields/WriteFields*` | A reference to another entity or component (a MonoBehaviour) is stored as a **`System.Guid`** from `GuidMapper`. A reference to a game-data asset (`[SavegameGameDataObject]`, e.g. ProductDefinition, Recipe, Building prefab, SettlementTier, TechTreeUnlock, PermitType) is stored as a **`SavegamePrefabIdentifier`** holding the asset's `UnityEngine.Object.name` and its type's AQN, which is resolved on load through `GameData.GetAsset` by type and name. **CONFIRMED** |
| Opaque blobs | `ISavegameSerializable` (e.g. `SavegameDictionary`, `SavegameHashSet`, `MoneyBill`, `MoneyTransfer`, `ResearchQueue`, `WaypointPath.Waypoint`, `VehiclePath.PathSegment`) | These are stored as a BinaryFormatter `ObjectSaveData+FixedSerializationData {fieldType, byte[] data, elementCount}`. Each class's own `Serialize(BinaryWriter)` defines the layout of the `data` bytes. Products inside them are referenced by asset **name** strings and actors by GUID bytes. **CONFIRMED** |
| Versioning | `GameVersion.GetSavegameVersion()`, `SavegameUpdater*.cs` (110…2299), `SavegameUpdaterSystem` | An int `savegameVersion` (2304) appears both in the header and at the end of the body. Migration runs per objectType on the ObjectSaveData dictionaries. **CONFIRMED (code)** |
| Compression lib | `research/_local/managed/LZ4.dll` | **lz4net by Milosz Krajewski, ".NET2" safe build** ("Copyright (c) 2015, Milosz Krajewski", types `LZ4Stream`, `ChunkFlags`, `Safe32/64LZ4Service`). **CONFIRMED (strings)** |

### LZ4Stream framing (lz4net)
The stream is a sequence of chunks, each laid out as `varint flags | varint originalLength | [varint compressedLength if flags&1] | data`. Here `varint` is LEB128 (7 bits per byte, little-endian). flags: `1` = Compressed, `2` = HighCompression, `>>2` = passes (must be ≤ 1). `data` is a **raw LZ4 block**, so standard `lz4.block.decompress(data, uncompressed_size=originalLength)` decodes it. Chunks are 1 MiB uncompressed (`80 80 40` = 0x100000). This is **CONFIRMED** on all three copies: 23, 23 and 52 chunks with 0 trailing bytes.

Decoding the start of the given header bytes: `01` (compressed), `80 80 40` (1 048 576), `F1 D7 06` (109 553 compressed bytes), then an LZ4 token `D0`. The literals are `4C 02 00 00` (headerLen = 588), followed by `00 01 00 00 00 FF FF FF FF 01 …`, which is the NRBF SerializedStreamHeader.

---

## 2. Research tooling (static, no game code executed)

All tools are in `research/tools/save-inspect/` and start with the required "RESEARCH TOOLING ONLY" header. `.venv/` is excluded by a local `.gitignore`.

* `roi_save.py`: does the lz4net de-framing, mirrors the `ProjectAutomata.Serializer` format, and contains a **hand-written MS-NRBF parser** (record types 0–17; List`1, Dictionary`2 and enums are simplified). It also has decoders for 9 `FixedSerializationData` payloads. Commands are `decompress`, `header` and `summary`.
* `schema_dump.py`: lists every objectType (manager, entity, component and nested type) with its field names, value kinds and one sample value.
* `evidence.py`: extracts the gameplay facts listed in section 3.

Reproduce from `research/`:
```bash
cd research/tools/save-inspect && python -m venv .venv && ./.venv/Scripts/python -m pip install lz4   # lz4 4.4.5
cd ../..
P=tools/save-inspect/.venv/Scripts/python; export PYTHONIOENCODING=utf-8
$P tools/save-inspect/roi_save.py header     "_local/saves/sample-a.sav"
$P tools/save-inspect/roi_save.py decompress "_local/saves/sample-a.sav" _local/save-analysis/sample_a.decompressed.bin
$P tools/save-inspect/roi_save.py summary    "_local/saves/sample-a.sav" --json _local/save-analysis/sample_a.json
$P tools/save-inspect/schema_dump.py         "_local/saves/sample-a.sav" _local/save-analysis/sample_a.schema.txt
$P tools/save-inspect/evidence.py            "_local/saves/sample-a.sav" > _local/save-analysis/evidence_sample_a.txt
```
Derived outputs are in `research/_local/save-analysis/` (gitignored): `*.json` (full tree with long primitive arrays truncated), `*.summary.txt`, `sample_a.schema.txt` (818 lines) and `evidence_*.txt`.

Results on the copies (**CONFIRMED**):

| Save | .sav size | decompressed | LZ4 chunks | NRBF blobs (bytes) | unparsed blobs | Python full parse | header only |
|---|---|---|---|---|---|---|---|
| sample-a | 2.82 MB | 23.8 MB | 23 | 7 780 (7.6 MB) | 0 | ~1.6–1.75 s | ~3 ms |
| Autosave 2 | 2.78 MB | 23.5 MB | 23 | 7 590 (7.5 MB) | 0 | ~1.55 s | – |
| Autosave 1 | 9.58 MB | 54.5 MB | 52 | 18 875 (18.4 MB) | 0 | ~3.2 s | ~3.5 ms |

LZ4 decompression itself takes 0.05–0.16 s. Python object building accounts for the rest of the time. All 238 decoded `FixedSerializationData` payloads in `sample-a` consumed exactly their byte length.

Top-level managers found: 45 types. They include BuildingManager (2146 Building entities), VehicleManager (91 Vehicle), ActorManager (State, HumanPlayer, Settlement×7, plus AiPlayer in Autosave 1), RegionManager (7 Region), ConnectivityNetworkManager (Road/Rail networks), MoneyManager, TimeManager, GlobalMarket, PermitManager, TechTreeManager, TransportRequestManager, VehicleDispatchTicketManager, StorageReservationManager, AiPlayerManager, ProductionStatsTracker, SeasonManager, ComputePollutionStrategy, CampaignController, GameParametersManager and others. The full list is in `*.summary.txt`.

---

## 3. What persists (evidence from `sample-a.sav` unless noted)

| Topic | Persists? | Where / evidence |
|---|---|---|
| **Game date** | **CONFIRMED** | `TimeManager._days=27893`, `_months=929`. Following `TimeManager`: day = (`_days` mod 30) + 1, month = (`_months` mod 12) + 1, year = floor(`_months` / 12) + 1, which gives **Y78 Jun 24** (UI shows "Jun 24, Y78"). The calendar uses 30-day months. Autosave 2 is Y75-05-03 and Autosave 1 is Y69-02-16. |
| **Building IDs / names** | **CONFIRMED** | `BuildingManager` entities have `EntitySaveData.guid` plus fields `buildingName` (displayed/localized, e.g. "USINE DE TEXTILE 1"), `region` (GUID), `settlement` (GUID), `buildingStateFlags` (enum), `paidToBuild`. `BuildingNameGenerator.usingCustomName/index`. **The GUIDs are stable across saves**: 2123 of 2146 building GUIDs in `sample-a` also appear in Autosave 2. |
| **Building type and position** | **CONFIRMED** | These come from `constructorParams`, which match the parameters of `Building.Create`: prefab (`Building`), x and y (int), rotation (`Direction`), owner (`IActor`) and context (`GameObject`). Example: `[prefab "TextileFactory", 45, 388, Direction 2, owner GUID 6d1dbc9e… (player), null]`. x and y are **tile coordinates**. There are 81 distinct prefabs. Most entities are settlement houses, and 141 buildings belong to the player. |
| **Recipe selected** | **CONFIRMED** | `Factory/Farm/GathererHub._currentRecipe` holds a SavegamePrefabIdentifier for `Recipe` (e.g. "Fibers", "Cotton", "OilDrill"). Also saved: `producedLastMonth`, `producedThisMonth`, `totalProduced`, `currentProgress`, `currentProducedAverage` (last 10 values), and `producedGoods` / `consumedGoods` daily history (`ProductInfoCollection`: parallel `UInt16[] days/amounts/productIds` plus a `products` "A\|B\|C" string). Harvester and Field modules link to their hub by GUID. |
| **Inventories** | **CONFIRMED** | `ProductSpecificProductStorage._storage` (decoded: `[{product:"Fibers", pull:0, put:2, store:4}, …]`) and `_maxAcceptedMap`. `SingleProductStorage`, `ReservableSingleProductStorage` and `AdvancedSingleProductStorage` each store `_product`, `_occupied`, `_maxAccepted`, `_reserved`. Shop storage uses the same component. `StorageReservationManager` lists 132 open reservations (type, storage GUID, product, amount, reserver). |
| **ManualDestinationSlot** | **CONFIRMED** | `ManualDestinationManager._savegameSlots` holds a list of `ManualDestinationSlot` with `destinationManager` (GUID), `activeRequests` (TransportRequestHandle ids), `paused`, `_destination` (GUID or null), `_source`, `_product` (prefab), `_waitTillVehicleFull`, `_minStoredAtSource`, `_autoMaxAccepted`. There are 249 slots in 41 buildings, 20 of them non-default (e.g. Cotton → `8431aae2…`, `_minStoredAtSource=4`). `ManualDestinationScheduler._lastDispatchedSlotIndex` is also saved. `BuildingLogistics` saves `_options` (LogisticsUserOptions), `_explicitManualMode`, `acceptedProducts` and `outgoingProducts`. |
| **Logistic / transport requests** | **CONFIRMED** | `TransportRequestManager._savegameEntities` (92 requests) holds `status`, `type`, `origin`/`destination` GUIDs, `distance`, `product`, `productAmount`, `paymentHandler`, `_networkName` "Road", plus `_idCounter` and `_savegameIds` (UInt64). `LogisticRequestManager` had 0 entities and `LogisticNetworkAgent._requests` was empty in these saves. `VehicleDispatchTicketManager` (93 tickets with jobs) and `LogisticTicketManager` are also saved. |
| **Vehicles** | **CONFIRMED** | `VehicleManager` entities (91, or 535 in Autosave 1) have ctor `[prefab "ChemicalsTruck", owner GUID]` and fields `id` (UInt16), `_fleet` (GUID), `_activeJob` (LandTransportJob: origin, destination, product, amount, full tile paths), `_save_position` and `_save_rotation`. `TruckMover` / `BoatMover` save their full movement state. Each building's `JITVehicleFleet._vehicles` is an opaque blob that has not been decoded. |
| **Company money** | **CONFIRMED** | `MoneyManager._balances` (decoded) maps actor GUID to a double. For example, the player "Altitude" has 310 375 187.23, the State has −10 000 000, and each settlement has its own balance. `MoneyAgent._infiniteMoney`. |
| **Loans** | **CONFIRMED** | `LoansAgent._loans` holds a list of `Loan {_savegameGrantingActor, _savegameInfo (LoanInfo prefab), _paymentsAmount, _amount, _apr, _duration, freeMonthsLeft, _eventData}`. It is empty in `sample-a`. Autosave 1 has a 2 000 000 "Fine" loan caused by a "Mine Collapse" event. |
| **Financial history** | **CONFIRMED, partial** | `MoneyAgent._savegameBills` (decoded MoneyBill: amount, date, category name, recipient and originator GUIDs) has 192 bills dated Y75-04-10…Y78-06-01. Categories include Upkeep, VehicleUpkeep, ProductTrade, BuildingConstruction, Loan Payments and FinesAndGrants. The game appears to keep a **rolling window** (each save covers about 3 years), **INFERRED**. Also saved: `ProductionStatsTracker` cost records (product, GameDate, value), `CampaignStatistics` (money per category or tier in shops and to the State, products moved, auctions won), `ActorStatisticsAgent._topSales/_topProduction`, and `Upkeep.upkeep/daysUp`. |
| **City / shop state** | **CONFIRMED** | Settlement entity: `_settlementName`, `_population`, `_tier`, `_type`, `_region`, `_config`, building list. `SettlementGrowth._desiredPopulation/_tierToPopulationLimit`. `SettlementAdvancement` state and contracts. Shop component: `sold`, `soldTags`, `_priceModifiers` (decoded, e.g. Leather 0.5, Cotton 0.4), `_deliveredByActors` (decoded, per actor id → product → count), `_demandFigures`, `_overallSales`, `_savegameSales*` (daily ProductInfoCollection), `_daysToNextPricesUpdate`. |
| **Prices** | **HIGH CONFIDENCE** | `GlobalMarket._serializedPricingInfo` has 151 entries of `{product, modifier, trend}`. Shop `_priceModifiers` holds per-shop modifiers. **Absolute base prices are not in the save**: they come from `ProductDefinition` assets referenced by name. A static game-data catalogue is needed to compute actual prices, **INFERRED**. |
| **Regions / permits** | **CONFIRMED** | Region entities: `_regionName`, `regionalBuilding`, `settlement`, `_resourceSites` (resource name, center tile, amount, initial amount, node tiles), cooldowns. Ctor params: prefab, name, GUID, center tile, borders. `PermitManager._savegamePermits` lists entries of `{actor int id, regionId Guid, permitType prefab "PermitFull", amountPaid}` plus `_permitManagementEnabled` and `_auctionedRegions`. |
| **Technology** | **CONFIRMED** | `TechTreeAgent._saveUnlockStates` (Dictionary<SavegamePrefabIdentifier,bool>) has 243 entries, 152 of them true. `TechTreeAgentResearchState`: `_queue` (decoded names), `_researchProgress` (decoded name→float), `_unlockPoints`, `_efficiencyIndex`, research cost. `TechTreeManager.isEnabled`. |
| **AI competitors** | **CONFIRMED** (Autosave 1) | An `AiPlayer` entity "Inertia Corporation" has ctor `[AiPlayer prefab, StandardPersonality, name, colour Vector3S, id 11]` and its own `_brainInstance`, MoneyAgent, TechTreeAgent and 478 buildings. `AiPlayerManager._aiPlayers` holds GUIDs and the AI runner scheduling state. The two later saves have no AI player (`aiCount=2` in WorldParameters, but none is alive). |
| World / map | **CONFIRMED** | Saved: 512×512 `height` and `biomes` bytes, `water` and `blocked` uint arrays, resource nodes, the Road/Rail `ConnectivityNetwork.layerSavegameData` (byte[][]), pollution arrays (1 048 576 UInt16 values and the same number of floats), seasons, `WorldParameters` (seed, size, regions, assistant…) and `DifficultyParameters`. |
| Thumbnail / company name in header | **CONFIRMED absent** | The header has only name, timestamp, version, build, mods and module. The company name is in the body (`HumanPlayer._actorName` = "Altitude"). |
| Remaining opaque blobs | **UNKNOWN content** (layout readable from source) | Not yet decoded: `_reservations` (ReservationsSavegameHashset), `JITVehicleFleet._vehicles`, `VehicleQueue.*`, `WaypointPath.waypoints`, `Settlement/StatePathfinder._importantTiles`, `HelpManager.dismissed`, `ResourceRespawnManager.*`, `ManualDestinationScheduler._productToSlotIndex`, `BuildingAnalysis.data`. Each one needs a small decoder written from that class's `Serialize`. |

Tile index encoding (`center=178508`, `tilePath` values) is probably `y*size + x` or `x*size + y`. **INFERRED, not verified**.

---

## 4. Feasibility of external parsing / MCP fallback

* **Correctness and coverage.** Without any game code, the whole body of all three copies parses to the last byte with 0 NRBF failures. Every gameplay object is reachable by field name. **CONFIRMED**
* **Speed.** In pure Python, the header takes about 3 ms and a full parse takes 1.6 s at 2.8 MB / 23.8 MB, or 3.2 s at 9.6 MB / 54.5 MB. About half of the cost is the 1M-element pollution arrays and the long tile paths, which can be skipped by length prefix. A C#/.NET port (BinaryReader plus a small NRBF reader, or the bundled `LZ4.dll`) would likely run at 0.1–0.3 s, **INFERRED**. Memory use is tens to hundreds of MB in Python, **INFERRED**.
* **Stability across versions.** The game is a finished title (build "0507b", save format 2304), so drift is low. ObjectSaveData is keyed by field name and AQN, which tolerates added or removed fields. NRBF is parsed structurally and is version-agnostic. **HIGH CONFIDENCE**. The main risks are:
  1. The `FixedSerializationData` payloads are positional per class and need per-type decoders.
  2. The parser hard-codes the set of `[SerializerMethod]`-registered types. If a mod or patch registers a new one, values would be misparsed. All other unknown types are skipped safely because their blobs are length-prefixed.
  3. Mods (here "Default Scenarios" and "Noms de villes Françaises") can add types or assets.
* **Interpretation needs static data.** Products, recipes, buildings, tiers and tech are saved **by asset name**. To get base prices, recipe inputs/outputs, display names and building footprints, an MCP needs a catalogue extracted from the game's assets. This is a separate task, **INFERRED**.
* **Joining with live data.** Entity and component GUIDs from `GuidMapper` are persisted and restored, and are stable across saves (2123 of 2146 overlap). A live mod/bridge that exposes `GuidMapper` GUIDs could therefore join with save data, **HIGH CONFIDENCE**.
* **Use as a fallback or offline source.** Feasibility is **good** for read-only snapshots, for example: what is built where, recipes, stocks, routes and manual slot settings, money, loans and bills, tech, regions and permits, city tiers and shop demand, market modifiers, and AI competitors. Caveats:
  - Data is only as fresh as the last manual save or autosave (here the autosaves are about 30 min apart).
  - There is no live state between saves.
  - Bills and charts cover only rolling windows.
  - Because `File.WriteAllBytes` is not atomic, an MCP must copy the file, check that its size and mtime are stable, and parse the copy.
  - It must never write `.sav` files: writing is out of scope, and the opaque blobs and GUID integrity make editing risky.
  - Listing saves can use the header-only path (ms per file). The header timestamp uses the **1971 epoch**.
