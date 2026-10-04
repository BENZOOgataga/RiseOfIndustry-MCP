using System;
using System.Collections.Generic;
using ProjectAutomata;
using RoiMcp.Observer.Core;
using RoiMcp.Observer.Dto;

namespace RoiMcp.Observer.ReadLayer
{
    internal static partial class StateCapture
    {
        // ------------------------------------------------------------ full building detail

        private static IEnumerator<bool> BuildingsSection(SectionContext ctx, WorldContext w, bool player)
        {
            var source = new List<Building>(player ? PlayerBuildings : AiBuildings);
            var result = new List<BuildingDto>(source.Count);
            var pollution = ManagerBehaviour<PollutionManager>.instance;
            for (int i = 0; i < source.Count; i++)
            {
                var b = source[i];
                if (b == null)
                {
                    ctx.ItemsVanished++;
                    continue;
                }
                try
                {
                    result.Add(BuildingDetail(b, w, player, pollution));
                    ctx.Items++;
                }
                catch (Exception e)
                {
                    ctx.ItemFailed(player ? "buildings_player" : "buildings_ai_detail", e);
                }
                if (ctx.ShouldYield) yield return true;
            }
            if (player) Data(ctx).buildings_player = result;
            else Data(ctx).buildings_ai_detail = result;
        }

        private static BuildingFlagsDto Flags(Building b)
        {
            var f = b.buildingStateFlags;
            return new BuildingFlagsDto
            {
                user_enabled = (f & BuildingStateFlags.UserEnabled) != 0,
                requirements_met = (f & BuildingStateFlags.RequirementsMet) != 0,
                is_working = (f & BuildingStateFlags.IsWorking) != 0,
            };
        }

        private static BuildingDto BuildingDetail(Building b, WorldContext w, bool player, PollutionManager pollution)
        {
            int x, y;
            WorldContext.Coords(b.tile, out x, out y);
            var prefab = b.prefab;
            var owner = b.buildingOwner;
            var dto = new BuildingDto
            {
                key = w.Key(b),
                save_guid = ReflectionTable.SaveGuid(b),
                prefab = prefab != null ? w.Name(prefab) : null,
                display_name = b.buildingName,
                owner_actor_id = WorldContext.ActorId(owner),
                x = x,
                y = y,
                rotation = (int)b.rotation,
                region_id = w.RegionId(b.region),
                city_id = WorldContext.CityId(b.settlement),
                tags = w.Tags(b),
                kind = w.Kind(b),
                flags = Flags(b),
                paid_to_build = b.paidToBuildAmount,
                inventory = new List<InventoryDto>(),
                notifications = new List<string>(),
                is_module = b.module != null,
            };

            // efficiency ("wage" slider)
            bool ownerMods = Guard.Modifiers(owner);
            var eff = b.GetComponent<BuildingEfficiency>();
            if (eff != null && ownerMods)
            {
                dto.efficiency = new EfficiencyDto { kind = "building", index = eff.efficiencyIndex, output_multiplier = eff.efficiency, upkeep_multiplier = eff.upkeepModifier };
            }
            else
            {
                var seff = b.GetComponent<BuildingSettlementEfficiency>();
                if (seff != null) dto.efficiency = new EfficiencyDto { kind = "settlement", index = -1, output_multiplier = seff.efficiency, upkeep_multiplier = seff.upkeepModifier };
            }

            var upkeep = b.GetComponent<Upkeep>();
            if (upkeep != null)
            {
                dto.upkeep = new UpkeepDto
                {
                    monthly_full = upkeep.totalMonthlyUpkeep,
                    monthly_active = upkeep.totalActiveMonthlyUpkeep,
                    accrued_this_month = ReflectionTable.UpkeepAccrued(upkeep),
                    days_up = ReflectionTable.UpkeepDaysUp(upkeep),
                };
            }

            // production
            var ru = b.recipeUser;
            Recipe recipe = null;
            if (ru != null)
            {
                recipe = ru.currentRecipe;
                dto.recipe = recipe != null ? w.Name(recipe) : null;
                if (recipe != null && ownerMods) dto.cycle_days_effective = Finite(ru.GetFinalProductionTime());
                var prod = new ProductionDto
                {
                    produced_this_month = ru.producedThisMonth,
                    produced_last_month = ru.producedLastMonth,
                    total_produced = ru.totalProduced,
                    final_speed = ownerMods ? Finite(ru.GetFinalProductionSpeed()) : null,
                };
                var avg = ru.currentProducedAverage;
                if (avg != null) prod.average_10_months = Finite(avg.currentAverage);
                float frames, spent;
                if (ReflectionTable.ProductionFrames(ru, out frames, out spent))
                {
                    prod.production_frames = (long)frames;
                    prod.frames_spent_producing = (long)spent;
                }
                var factory = ru as Factory;
                if (factory != null) prod.progress = factory.currentProgress;
                dto.production = prod;
            }

            // modules (gatherers / farms)
            var mo = b.moduleOwner;
            if (mo != null)
            {
                var md = new ModulesDto
                {
                    count = mo.moduleCount,
                    max = mo.maxModuleCount,
                    module_prefab = mo.modulePrefab != null ? w.Name(mo.modulePrefab) : null,
                    items = new List<ModuleDto2>(),
                };
                var hub = ru as GathererHub;
                if (hub != null) md.delivered_to_hub = hub.productionIsDeliveredToHub;
                var mods = mo.modules;
                for (int i = 0; i < mods.Count; i++)
                {
                    var m = mods[i];
                    if (m == null) continue;
                    md.items.Add(ModuleRow(m, w));
                }
                if (md.items.Count > 0 && dto.production != null && !dto.production.progress.HasValue)
                    dto.production.progress = md.items[0].progress;
                dto.modules = md;
            }
            var asModule = b.module;
            if (asModule != null && asModule.moduleOwner != null) dto.module_owner = w.Key(asModule.moduleOwner.building);

            // inventory
            Inventory(b, recipe, w, dto.inventory);

            // logistics configuration
            var logistics = b.logistics;
            if (logistics != null)
            {
                var opts = logistics.options;
                var lc = new LogisticsConfigDto
                {
                    options = (int)opts,
                    auto_wh = (opts & LogisticsUserOptions.AUTO_WH) != 0,
                    accepted = Names(logistics.acceptedProducts, w),
                    outgoing = Names(logistics.outgoingProducts, w),
                };
                var client = logistics.warehouseClient;
                if (client != null)
                {
                    var wh = client.warehouse;
                    if (wh != null) lc.warehouse = w.Key(wh.building);
                }
                var mdm = b.manualDestinations;
                if (mdm != null) lc.manual_slot_count = mdm.slots != null ? mdm.slots.Count : 0;
                dto.logistics = lc;
            }

            // requirement notifications (player buildings only; the game debounces them)
            var rc = b.GetComponent<BuildingRequirementController>();
            if (rc != null && rc.notificationCount > 0)
            {
                foreach (var n in rc.notifications)
                {
                    if (n != null && n.specification != null) dto.notifications.Add(w.Name(n.specification));
                    if (dto.notifications.Count >= 16) break;
                }
            }

            // pollution
            if (pollution != null) dto.pollution_at_tile = Finite(pollution.GetPollution(b.tile));
            var polluted = b.GetComponent<ThresholdBuildingPollutionEffect>();
            if (polluted != null) dto.is_polluted = polluted.isPolluted;

            // fleet
            var fleet = b.GetComponent<JITVehicleFleet>();
            if (fleet != null)
            {
                dto.fleet = new FleetDto
                {
                    vehicle_prefab = fleet.vehiclePrefab != null ? w.Name(fleet.vehiclePrefab) : null,
                    active = fleet.activeVehicleCount,
                    inactive = fleet.inactiveVehicleCount,
                    max = fleet.maximumVehicleAmount,
                    infinite = fleet.infiniteAmount,
                };
            }
            dto.is_shop = b.GetComponent<Shop>() != null;
            return dto;
        }

        private static ModuleDto2 ModuleRow(Module m, WorldContext w)
        {
            var mb = m.building;
            var row = new ModuleDto2
            {
                key = mb != null ? w.Key(mb) : null,
                prefab = mb != null && mb.prefab != null ? w.Name(mb.prefab) : null,
                progress = m.currentProgress,
            };
            if (mb != null)
            {
                var f = Flags(mb);
                row.user_enabled = f.user_enabled;
                row.requirements_met = f.requirements_met;
                row.is_working = f.is_working;
            }
            var h = m as Harvester;
            if (h != null)
            {
                row.efficiency = h.efficiency;
                row.min_guaranteed_speed = h.minGuaranteedProductionSpeed;
                row.max_resources = h.maxResources;
                var nodes = ReflectionTable.HarvesterResources(h);
                if (nodes != null)
                {
                    int count = 0, depleted = 0;
                    long remaining = 0;
                    float speedSum = 0f;
                    string product = null;
                    for (int i = 0; i < nodes.Count; i++)
                    {
                        var node = nodes[i];
                        if (node == null) continue;
                        count++;
                        int amount = node.resourceAmount;
                        if (amount > 0)
                        {
                            remaining += amount;
                            speedSum += 1f;
                        }
                        else
                        {
                            depleted++;
                            if (node.canBeHarvestedIfDepleted) speedSum += node.productionSpeedModifierIfDepleted;
                        }
                        if (product == null && node.resourceProduct != null) product = w.Name(node.resourceProduct);
                    }
                    row.nodes = count;
                    row.nodes_depleted = depleted;
                    row.deposit_remaining = remaining;
                    row.resource = product;
                    // Replica of Harvester.GetProductionSpeed (Field overrides it with _efficiency).
                    if (h is Field || h.maxResources <= 0) row.speed_replica = h.efficiency;
                    else
                    {
                        float v = speedSum / h.maxResources * h.efficiency;
                        row.speed_replica = Math.Max(h.minGuaranteedProductionSpeed, Math.Min(1f, v));
                    }
                }
                else if (h is Field)
                {
                    row.speed_replica = h.efficiency;
                }
            }
            return row;
        }

        private static readonly List<ProductDefinition> InventoryScratch = new List<ProductDefinition>(32);

        private static void Inventory(Building b, Recipe recipe, WorldContext w, List<InventoryDto> into)
        {
            var storage = b.storage;
            if (storage == null) return;
            InventoryScratch.Clear();
            var inputs = new HashSet<int>();
            var outputs = new HashSet<int>();
            if (recipe != null)
            {
                AddEntries(recipe.ingredients, inputs);
                AddEntries(recipe.result, outputs);
            }
            var logistics = b.logistics;
            if (logistics != null)
            {
                AddList(logistics.acceptedProducts);
                AddList(logistics.outgoingProducts);
            }
            var pss = storage as ProductSpecificProductStorage;
            for (int i = 0; i < InventoryScratch.Count && i < 64; i++)
            {
                var def = InventoryScratch[i];
                int id = def.AssetId;
                var row = new InventoryDto
                {
                    product = w.Name(def),
                    role = inputs.Contains(id) ? "input" : outputs.Contains(id) ? "output" : "accepted",
                    count = storage.Count(def),
                    slots = storage.GetSlots(def),
                };
                if (pss != null)
                {
                    int pulls, puts, stored;
                    if (ReflectionTable.StorageCounts(pss, id, out pulls, out puts, out stored))
                    {
                        row.stored = stored;
                        row.incoming_reserved = puts;
                        row.outgoing_reserved = pulls;
                    }
                    row.inbound_cap = ReflectionTable.MaxAccepted(pss, id);
                }
                into.Add(row);
            }
            InventoryScratch.Clear();
        }

        private static void AddEntries(ProductList list, HashSet<int> ids)
        {
            if (list == null || list.entries == null) return;
            for (int i = 0; i < list.entries.Count; i++)
            {
                var e = list.entries[i];
                if (e == null) continue;
                var def = e.definition;
                if (def == null) continue;
                ids.Add(def.AssetId);
                AddUnique(def);
            }
        }

        private static void AddList(List<ProductDefinition> list)
        {
            if (list == null) return;
            for (int i = 0; i < list.Count; i++)
                if (list[i] != null) AddUnique(list[i]);
        }

        private static void AddUnique(ProductDefinition def)
        {
            for (int i = 0; i < InventoryScratch.Count; i++)
                if (ReferenceEquals(InventoryScratch[i], def)) return;
            InventoryScratch.Add(def);
        }

        internal static List<string> Names(List<ProductDefinition> list, WorldContext w)
        {
            var r = new List<string>();
            if (list == null) return r;
            for (int i = 0; i < list.Count && i < 256; i++)
                if (list[i] != null) r.Add(w.Name(list[i]));
            return r;
        }

        internal static float? Finite(float v)
        {
            if (float.IsNaN(v) || float.IsInfinity(v)) return null;
            return v;
        }

        // ------------------------------------------------------------ AI compact rows

        private static IEnumerator<bool> AiCompactSection(SectionContext ctx, WorldContext w)
        {
            var source = new List<Building>(AiBuildings);
            var result = new List<BuildingCompactDto>(source.Count);
            for (int i = 0; i < source.Count; i++)
            {
                try
                {
                    var b = source[i];
                    if (b == null)
                    {
                        ctx.ItemsVanished++;
                        continue;
                    }
                    int x, y;
                    WorldContext.Coords(b.tile, out x, out y);
                    var prefab = b.prefab;
                    var row = new BuildingCompactDto
                    {
                        key = w.Key(b),
                        prefab = prefab != null ? w.Name(prefab) : null,
                        display_name = b.buildingName,
                        owner_actor_id = WorldContext.ActorId(b.buildingOwner),
                        x = x,
                        y = y,
                        region_id = w.RegionId(b.region),
                        city_id = WorldContext.CityId(b.settlement),
                        kind = w.Kind(b),
                        tags = w.Tags(b),
                        flags = Flags(b),
                        is_module = b.module != null,
                    };
                    var ru = b.recipeUser;
                    if (ru != null)
                    {
                        var recipe = ru.currentRecipe;
                        row.recipe = recipe != null ? w.Name(recipe) : null;
                        row.produced_last_month = ru.producedLastMonth;
                        if (recipe != null && Guard.Modifiers(b.buildingOwner)) row.cycle_days_effective = Finite(ru.GetFinalProductionTime());
                    }
                    var mo = b.moduleOwner;
                    if (mo != null) row.module_count = mo.moduleCount;
                    result.Add(row);
                    ctx.Items++;
                }
                catch (Exception e)
                {
                    ctx.ItemFailed("buildings_ai", e);
                }
                if (ctx.ShouldYield) yield return true;
            }
            Data(ctx).buildings_ai = result;
        }
    }
}
