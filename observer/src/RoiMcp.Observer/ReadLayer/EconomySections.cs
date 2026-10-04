using System;
using System.Collections.Generic;
using ProjectAutomata;
using RoiMcp.Observer.Core;
using RoiMcp.Observer.Dto;
using UnityEngine;

namespace RoiMcp.Observer.ReadLayer
{
    internal static partial class StateCapture
    {
        // ------------------------------------------------------------ market

        private static IEnumerator<bool> MarketSection(SectionContext ctx, WorldContext w)
        {
            var market = ManagerBehaviour<GlobalMarket>.instance;
            if (market == null) throw new InvalidOperationException("GlobalMarket unavailable");
            var pricing = ReflectionTable.PricingInfo(market);
            if (pricing == null) throw new InvalidOperationException("pricing table unavailable");
            var human = Player.humanPlayer;
            bool humanMods = Guard.Modifiers(human);
            var entries = new List<KeyValuePair<ProductDefinition, ProductPricingInfo>>(pricing);
            var dto = new MarketDto
            {
                prices = new List<MarketPriceDto>(entries.Count),
                state = new StateMarketDto { sold = new List<StateSoldDto>() },
                city_contract_offers = new List<ContractDto>(),
            };
            for (int i = 0; i < entries.Count; i++)
            {
                try
                {
                    var def = entries[i].Key;
                    var info = entries[i].Value;
                    if (def == null || info == null) continue;
                    var row = new MarketPriceDto
                    {
                        product = w.Name(def),
                        value = info.value,
                        price = info.price,
                        modifier = info.modifier,
                        trend = Trend(info.trend),
                    };
                    // GetFinalPrice is only called for products in the market's own key set (PRD 9.3).
                    if (humanMods) row.final_price_for_player = Finite(market.GetFinalPrice(def, human));
                    dto.prices.Add(row);
                    ctx.Items++;
                }
                catch (Exception e)
                {
                    ctx.ItemFailed("market", e);
                }
                if (ctx.ShouldYield) yield return true;
            }

            var state = ProjectAutomata.State.instance;
            if (state != null)
            {
                var handlers = state.tradingHandlers;
                dto.state.trading_handlers = handlers.count;
                var seen = new HashSet<int>();
                for (int h = 0; h < handlers.count; h++)
                {
                    var handler = handlers[h];
                    if (handler == null) continue;
                    if (handler.allowIncomingTrade) dto.state.incoming_trade_allowed = true;
                    dto.state.sale_markup = handler.saleMarkup;
                    var list = handler.initialProducts;
                    if (list == null || list.entries == null) continue;
                    for (int p = 0; p < list.entries.Count; p++)
                    {
                        var e = list.entries[p];
                        var def = e != null ? e.definition : null;
                        if (def == null || !seen.Add(def.GetInstanceID())) continue;
                        var row = new StateSoldDto { product = w.Name(def) };
                        ProductPricingInfo info;
                        if (humanMods && pricing.TryGetValue(def, out info) && info != null)
                            row.price_for_player = Finite(handler.GetSalePrice(def, human));
                        dto.state.sold.Add(row);
                    }
                }
            }

            var sm = ManagerBehaviour<SettlementManager>.instance;
            if (sm != null)
            {
                var settlements = new List<SettlementBase>(sm.settlements);
                for (int s = 0; s < settlements.Count; s++)
                {
                    var st = settlements[s];
                    if (st == null || st.contracts == null) continue;
                    var c = st.contracts.currentContract;
                    if (c != null) dto.city_contract_offers.Add(ContractRow(c, w));
                }
            }

            var am = ManagerBehaviour<AuctionsManager>.instance;
            if (am != null)
            {
                var auctions = new AuctionsDto { queue = new List<AuctionDto>() };
                var current = am.currentAuction;
                if (current != null) auctions.current = AuctionRow(current, w);
                var queue = am.auctionQueue;
                if (queue != null)
                {
                    foreach (var a in queue.ToArray())
                    {
                        if (a != null && auctions.queue.Count < 32) auctions.queue.Add(AuctionRow(a, w));
                    }
                }
                dto.auctions = auctions;
            }
            Data(ctx).market = dto;
        }

        private static string Trend(PriceTrend t)
        {
            switch (t)
            {
                case PriceTrend.STABLE: return "STABLE";
                case PriceTrend.GOING_UP: return "GOING_UP";
                case PriceTrend.GOING_DOWN: return "GOING_DOWN";
                default: return "OTHER";
            }
        }

        private static AuctionDto AuctionRow(Auction a, WorldContext w)
        {
            var def = a.definition;
            var row = new AuctionDto
            {
                definition = def != null ? w.Name(def) : null,
                title = def != null ? a.title : null,
                remaining_days = a.remainingDays,
                duration_days = a.duration,
                start_bid = a.startBid,
                bid_count = a.bids.count,
            };
            var top = a.highestBid;
            if (top != null)
            {
                row.highest_bid = top.amount;
                row.highest_bidder_actor_id = top.bidder != null ? top.bidder.id : (int?)null;
            }
            var reward = a.reward;
            if (reward is PermitAuctionReward)
            {
                row.reward_kind = "permit";
                row.region_id = w.RegionId(((PermitAuctionReward)reward).region);
            }
            else if (reward is ContractAuctionReward)
            {
                row.reward_kind = "contract";
                var c = ((ContractAuctionReward)reward).contract;
                if (c != null && c.product != null) row.contract_product = w.Name(c.product);
            }
            else if (reward is BankruptcyAuctionReward) row.reward_kind = "bankruptcy_assets";
            else if (reward is AssetsAuctionReward) row.reward_kind = "assets";
            else if (reward is PrAndMarketingAuctionReward) row.reward_kind = "pr_and_marketing";
            else row.reward_kind = reward != null ? "other" : null;
            return row;
        }

        // ------------------------------------------------------------ research

        private static IEnumerator<bool> ResearchSection(SectionContext ctx, WorldContext w)
        {
            var human = Player.humanPlayer;
            if (human == null) throw new InvalidOperationException("no human player");
            var dto = new ResearchDto { ai = new List<AiResearchDto>() };
            if (!Guard.Has<ITechTreeAgent>(human) || !Guard.Modifiers(human)) throw new InvalidOperationException("tech tree agent not cached");
            var agent = human.techTree as TechTreeAgent;
            if (agent == null) throw new InvalidOperationException("player tech tree agent unavailable");
            var research = agent.research;
            var unlockStates = ReflectionTable.UnlockStates(agent);
            if (unlockStates == null) throw new InvalidOperationException("unlock states unavailable");
            var states = new List<KeyValuePair<TechTreeUnlock, bool>>(unlockStates);

            var p = new PlayerResearchDto
            {
                queue = new List<string>(),
                progress = new List<UnlockProgressDto>(),
                unlocked = new List<string>(),
                costs = new List<UnlockCostDto>(),
            };
            var prices = ReflectionTable.BuildingPrices(agent);
            if (prices != null)
            {
                p.building_costs = new List<BuildingCostDto>(prices.Count);
                foreach (var kv in new List<KeyValuePair<Building, double>>(prices))
                    if (kv.Key != null) p.building_costs.Add(new BuildingCostDto { building_type = w.Name(kv.Key), cost = kv.Value });
                p.building_costs.Sort((a, b) => string.CompareOrdinal(a.building_type, b.building_type));
            }
            var unlockedSet = new HashSet<int>();
            for (int i = 0; i < states.Count; i++)
            {
                if (states[i].Key != null && states[i].Value)
                {
                    p.unlocked.Add(w.Name(states[i].Key));
                    unlockedSet.Add(states[i].Key.GetInstanceID());
                }
            }
            if (research != null)
            {
                var active = research.activeUnlock;
                p.active = active != null ? w.Name(active) : null;
                p.active_progress = research.researchProgress;
                p.remaining_days = research.remainingTime;
                p.remaining_cost = research.remainingCost;
                p.current_cost = research.currentResearchCost;
                p.efficiency_index = research.efficiencyIndex;
                p.efficiency = research.efficiency;
                p.unlock_points = research.unlockPoints;
                var queue = new List<TechTreeUnlock>(research.queue);
                for (int i = 0; i < queue.Count; i++)
                    if (queue[i] != null) p.queue.Add(w.Name(queue[i]));
                var progress = ReflectionTable.ResearchProgressMap(research);
                if (progress != null)
                    foreach (var kv in progress)
                        if (kv.Key != null) p.progress.Add(new UnlockProgressDto { unlock = w.Name(kv.Key), progress = kv.Value });

                // Costs for queued, active and available nodes only (formula evaluations, PRD 14.8).
                var costNodes = new List<TechTreeUnlock>(queue);
                for (int i = 0; i < states.Count; i++)
                {
                    var u = states[i].Key;
                    if (u == null || states[i].Value || u.isTeaser || costNodes.Contains(u)) continue;
                    if (AllRequiredUnlocked(u, unlockedSet)) costNodes.Add(u);
                }
                for (int i = 0; i < costNodes.Count; i++)
                {
                    var u = costNodes[i];
                    // Nodes without a cost formula make the game's cost methods throw (seen live in E1/E3).
                    if (u == null || u.researchCost == null) continue;
                    try
                    {
                        p.costs.Add(new UnlockCostDto
                        {
                            unlock = w.Name(u),
                            daily_cost = research.GetResearchDailyCost(u),
                            days = research.GetResearchTime(u),
                            total_cost_at_efficiency_1 = Finite(research.GetResearchCost(u)),
                        });
                        ctx.Items++;
                    }
                    catch (Exception e)
                    {
                        ctx.ItemFailed("research", e);
                    }
                    if (ctx.ShouldYield) yield return true;
                }
            }
            dto.player = p;

            var aim = ManagerBehaviour<AiPlayerManager>.instance;
            if (aim != null)
            {
                var ais = aim.aiPlayers;
                var list = new List<AiPlayer>();
                for (int i = 0; i < ais.count; i++) list.Add(ais[i]);
                for (int i = 0; i < list.Count; i++)
                {
                    var ai = list[i];
                    if (ai == null) continue;
                    var row = new AiResearchDto { actor_id = ai.id, unlocked = new List<string>() };
                    var aiAgent = Guard.Has<ITechTreeAgent>(ai) ? ai.techTree as TechTreeAgent : null;
                    var aiStates = aiAgent != null ? ReflectionTable.UnlockStates(aiAgent) : null;
                    if (aiStates != null)
                        foreach (var kv in aiStates)
                            if (kv.Key != null && kv.Value) row.unlocked.Add(w.Name(kv.Key));
                    row.unlocked_count = row.unlocked.Count;
                    dto.ai.Add(row);
                    if (ctx.ShouldYield) yield return true;
                }
            }
            Data(ctx).research = dto;
        }

        private static bool AllRequiredUnlocked(TechTreeUnlock u, HashSet<int> unlocked)
        {
            var req = u.requiredUnlocks;
            if (req == null) return true;
            for (int i = 0; i < req.Length; i++)
                if (req[i] != null && !unlocked.Contains(req[i].GetInstanceID())) return false;
            return true;
        }

        // ------------------------------------------------------------ vehicles

        private static IEnumerator<bool> VehiclesSection(SectionContext ctx, WorldContext w)
        {
            var vm = ManagerBehaviour<VehicleManager>.instance;
            if (vm == null) throw new InvalidOperationException("VehicleManager unavailable");
            var vehicles = new List<Vehicle>(vm.vehicles);
            var human = Player.humanPlayer;
            var groups = new Dictionary<string, VehicleGroupDto>(StringComparer.Ordinal);
            var dto = new VehiclesDto
            {
                groups = new List<VehicleGroupDto>(),
                fleets_player = new List<FleetRowDto>(),
                vehicles_player = new List<VehicleRowDto>(),
                identity = "session_pooled_object",
            };
            for (int i = 0; i < vehicles.Count; i++)
            {
                try
                {
                    var v = vehicles[i];
                    if (v == null)
                    {
                        ctx.ItemsVanished++;
                        continue;
                    }
                    // Only transform, job fields and cargo are read; never mover internals (ThreadPool, R-CRASH-6).
                    var owner = v.GetOwner();
                    int ownerId = WorldContext.ActorId(owner);
                    BuildingLogistics origin, destination;
                    ProductDefinition jobProduct;
                    int jobAmount;
                    bool hasJob = ReflectionTable.TransportJobFields(v.activeJob, out origin, out destination, out jobProduct, out jobAmount);
                    var storage = v.productStorage;
                    int cargo = storage != null ? storage.occupiedSlots : 0;
                    string product = hasJob && jobProduct != null ? w.Name(jobProduct) : null;
                    string mode = v.networkName;
                    string gk = ownerId + "|" + (mode ?? "") + "|" + (product ?? "");
                    VehicleGroupDto g;
                    if (!groups.TryGetValue(gk, out g))
                    {
                        g = new VehicleGroupDto { owner_actor_id = ownerId, transport_mode = mode, product = product };
                        groups[gk] = g;
                        dto.groups.Add(g);
                    }
                    g.vehicles++;
                    g.units_in_transit += cargo;
                    dto.total_active++;

                    if (owner != null && human != null && ReferenceEquals(owner, human))
                    {
                        var pos = v.transform.position;
                        int tx = (int)Math.Floor(pos.x), ty = (int)Math.Floor(pos.z);
                        var fleet = v.fleet;
                        var prefab = v.prefab;
                        dto.vehicles_player.Add(new VehicleRowDto
                        {
                            instance_id = v.GetInstanceID(),
                            trip_counter_id = v.id,
                            prefab = prefab != null ? w.Name(prefab) : v.vehicleName,
                            transport_mode = mode,
                            fleet_building = fleet != null ? w.Key(fleet.building) : null,
                            product = product,
                            amount = cargo,
                            job_origin = hasJob ? w.Key(origin) : null,
                            job_destination = hasJob ? w.Key(destination) : null,
                            job_product = product,
                            job_amount = hasJob ? jobAmount : (int?)null,
                            going_home = v.IsGoingHome(),
                            position_x = pos.x,
                            position_z = pos.z,
                            tile_x = tx,
                            tile_y = ty,
                        });
                    }
                    ctx.Items++;
                }
                catch (Exception e)
                {
                    ctx.ItemFailed("vehicles", e);
                }
                if (ctx.ShouldYield) yield return true;
            }

            var source = new List<Building>(PlayerBuildings);
            for (int i = 0; i < source.Count; i++)
            {
                var b = source[i];
                if (b == null) continue;
                var fleet = b.GetComponent<JITVehicleFleet>();
                if (fleet == null) continue;
                var vp = fleet.vehiclePrefab;
                dto.fleets_player.Add(new FleetRowDto
                {
                    building = w.Key(b),
                    vehicle_prefab = vp != null ? w.Name(vp) : null,
                    transport_mode = vp != null ? vp.networkName : null,
                    active = fleet.activeVehicleCount,
                    inactive = fleet.inactiveVehicleCount,
                    max = fleet.maximumVehicleAmount,
                    infinite = fleet.infiniteAmount,
                });
                if (ctx.ShouldYield) yield return true;
            }
            Data(ctx).vehicles = dto;
        }
    }
}
