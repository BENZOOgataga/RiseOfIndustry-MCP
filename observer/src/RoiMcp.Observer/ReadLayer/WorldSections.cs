using System;
using System.Collections.Generic;
using ProjectAutomata;
using RoiMcp.Observer.Core;
using RoiMcp.Observer.Dto;

namespace RoiMcp.Observer.ReadLayer
{
    internal static partial class StateCapture
    {
        private static List<SettlementBase> Settlements()
        {
            var sm = ManagerBehaviour<SettlementManager>.instance;
            if (sm == null) throw new InvalidOperationException("SettlementManager unavailable");
            return new List<SettlementBase>(sm.settlements);
        }

        // ------------------------------------------------------------ shops

        private static IEnumerator<bool> ShopsSection(SectionContext ctx, WorldContext w)
        {
            var settlements = Settlements();
            var human = Player.humanPlayer;
            bool humanMods = Guard.Modifiers(human);
            var market = ManagerBehaviour<GlobalMarket>.instance;
            var pricing = market != null ? ReflectionTable.PricingInfo(market) : null;
            var result = new List<ShopDto>();
            var shops = new List<Shop>(16);
            var sold = new List<ProductDefinition>(16);
            var period = new GamePeriod(0, 0, 30);
            for (int s = 0; s < settlements.Count; s++)
            {
                var settlement = settlements[s];
                if (settlement == null)
                {
                    ctx.ItemsVanished++;
                    continue;
                }
                var coll = settlement.buildings;
                if (coll == null || coll.shops == null) continue;
                shops.Clear();
                shops.AddRange(coll.shops);
                for (int i = 0; i < shops.Count; i++)
                {
                    try
                    {
                        var shop = shops[i];
                        if (shop == null) continue;
                        var b = shop.building;
                        if (b == null) continue;
                        var row = new ShopDto
                        {
                            building = w.Key(b),
                            display_name = b.buildingName,
                            prefab = b.prefab != null ? w.Name(b.prefab) : null,
                            city_id = settlement.id,
                            owner_actor_id = WorldContext.ActorId(b.buildingOwner),
                            is_dead = shop.isDead,
                            days_to_next_price_update = ReflectionTable.ShopDaysToPriceUpdate(shop),
                            products = new List<ShopProductDto>(),
                        };
                        var storage = shop.storage;
                        var pss = storage as ProductSpecificProductStorage;
                        sold.Clear();
                        if (shop.sold != null) sold.AddRange(shop.sold);
                        for (int p = 0; p < sold.Count; p++)
                        {
                            var def = sold[p];
                            if (def == null) continue;
                            var pr = new ShopProductDto
                            {
                                product = w.Name(def),
                                stock = storage != null ? storage.Count(def) : 0,
                                slots = pss != null ? pss.slots : (int?)null,
                                demand_raw = shop.GetDemand(def, null),
                                demand_for_player = humanMods ? shop.GetDemand(def, human) : 0,
                                sold_last_30d = shop.GetSoldCount(def, period),
                            };
                            if (humanMods)
                            {
                                pr.player_delivered_stock = ReflectionTable.ShopDelivered(shop, human, def);
                                // GetPrice reads GlobalMarket.GetPricingInfo, which logs an error for products
                                // outside the market key set: only call it for products the market knows.
                                ProductPricingInfo info;
                                if (pricing != null && pricing.TryGetValue(def, out info) && info != null)
                                {
                                    pr.price_for_player = Finite(shop.GetPrice(def, human));
                                    float shopMod = shop.GetShopModifier(def, human);
                                    pr.shop_modifier = Finite(shopMod);
                                    pr.market_modifier = info.modifier;
                                    pr.price_modifier_pct = Finite((1f + info.modifier + shopMod) * 100f);
                                }
                            }
                            row.products.Add(pr);
                        }
                        result.Add(row);
                        ctx.Items++;
                    }
                    catch (Exception e)
                    {
                        ctx.ItemFailed("shops", e);
                    }
                    if (ctx.ShouldYield) yield return true;
                }
            }
            Data(ctx).shops = result;
        }

        // ------------------------------------------------------------ cities

        private static IEnumerator<bool> CitiesSection(SectionContext ctx, WorldContext w)
        {
            var settlements = Settlements();
            var pm = ManagerBehaviour<PermitManager>.instance;
            var permits = pm != null ? ReflectionTable.Permits(pm) : null;
            var fullPermit = pm != null ? pm.fullPermit : null;
            var result = new List<CityDto>(settlements.Count);
            for (int s = 0; s < settlements.Count; s++)
            {
                try
                {
                    var st = settlements[s];
                    if (st == null)
                    {
                        ctx.ItemsVanished++;
                        continue;
                    }
                    var region = st.region;
                    var tier = st.tier;
                    var type = st.type;
                    var row = new CityDto
                    {
                        city_id = st.id,
                        name = st.settlementName,
                        region_id = w.RegionId(region),
                        type = type != null ? w.Name(type) : null,
                        tier = tier != null ? w.Name(tier) : null,
                        tier_id = tier != null ? tier.tierId : (int?)null,
                        next_tier = tier != null && tier.nextTier != null ? w.Name(tier.nextTier) : null,
                        population = st.population,
                        dead = st.isDead,
                        shops = new List<string>(),
                    };
                    if (region != null)
                    {
                        int cx, cy;
                        WorldContext.Coords(region.center, out cx, out cy);
                        row.center_x = cx;
                        row.center_y = cy;
                    }
                    var config = st.config;
                    if (config != null && config.growth != null) row.consumption_interval_days = config.growth.consumeProductsInterval;

                    var growth = st.growth as SettlementGrowth;
                    if (growth != null && tier != null)
                    {
                        int limit = growth.populationLimit;
                        bool limitReached = st.population >= limit;
                        row.population_limit = limit;
                        row.population_limit_reached = limitReached;
                        var g = new CityGrowthDto
                        {
                            growing = growth.IsGrowing(),
                            prospering = growth.IsProspering(),
                            consumed_products = ReflectionTable.ConsumedProducts(growth),
                        };
                        if (config != null && config.growth != null)
                        {
                            g.growth_threshold = config.growth.growthThreshold;
                            g.prosperity_threshold = config.growth.prosperityThreshold;
                        }
                        // IsWaitingForSponsor calls PermitManager.IsPermitTaken (GetSafe insert): replicated here.
                        bool canAdvance = !st.isDead && tier.nextTier != null;
                        bool permitTaken = PermitOwner(permits, region, fullPermit) != null;
                        g.waiting_for_sponsor = limitReached && canAdvance && !permitTaken && permits != null;
                        if (permits == null) g.waiting_for_sponsor = null;
                        g.state = GrowthState(g, limitReached);
                        row.growth = g;
                    }

                    var adv = st.advancement;
                    if (adv != null)
                    {
                        var a = new AdvancementDto
                        {
                            can_advance = adv.canAdvance,
                            can_accept = adv.canAccept,
                            is_advancing = adv.isAdvancing,
                            contracts = new List<AdvancementContractDto>(),
                        };
                        var list = adv.contracts;
                        if (list != null)
                        {
                            for (int i = 0; i < list.Count; i++)
                            {
                                var c = list[i];
                                if (c == null) continue;
                                a.contracts.Add(new AdvancementContractDto
                                {
                                    product = c.product != null ? w.Name(c.product) : null,
                                    amount = c.amount,
                                    delivered = c.delivered,
                                    payout = PayoutSafe(c),
                                });
                            }
                        }
                        row.advancement = a;
                    }

                    var contracts = st.contracts;
                    if (contracts != null)
                    {
                        var current = contracts.currentContract;
                        if (current != null) row.contract_offer = ContractRow(current, w);
                    }

                    var coll = st.buildings;
                    if (coll != null)
                    {
                        if (coll.houses != null) row.house_count = coll.houses.Count;
                        if (coll.shops != null)
                            for (int i = 0; i < coll.shops.Count; i++)
                            {
                                var shop = coll.shops[i];
                                if (shop != null && shop.building != null) row.shops.Add(w.Key(shop.building));
                            }
                    }
                    result.Add(row);
                    ctx.Items++;
                }
                catch (Exception e)
                {
                    ctx.ItemFailed("cities", e);
                }
                if (ctx.ShouldYield) yield return true;
            }
            Data(ctx).cities = result;
        }

        /// <summary>
        /// AdvancementContract.payOutSum reads GlobalMarket.GetPricingInfo, which logs an error for products
        /// outside the market: only evaluated for products present in the market's own key set.
        /// </summary>
        private static double PayoutSafe(AdvancementContract c)
        {
            var market = ManagerBehaviour<GlobalMarket>.instance;
            var pricing = market != null ? ReflectionTable.PricingInfo(market) : null;
            ProductPricingInfo info;
            if (pricing == null || c.product == null || !pricing.TryGetValue(c.product, out info) || info == null) return 0;
            return c.payOutSum;
        }

        /// <summary>D-GROW-1: SettlementUiViewModel order.</summary>
        private static string GrowthState(CityGrowthDto g, bool limitReached)
        {
            if (g.waiting_for_sponsor == true) return "WaitingForSponsor";
            if (limitReached) return "Bloated";
            if (g.prospering == true) return "Prospering";
            if (g.growing == true) return "Growing";
            return "Stagnating";
        }

        internal static IActor PermitOwner(Dictionary<Region, Dictionary<PermitType, Permit>> permits, Region region, PermitType type)
        {
            if (permits == null || region == null || type == null) return null;
            Dictionary<PermitType, Permit> byType;
            if (!permits.TryGetValue(region, out byType) || byType == null) return null;
            Permit permit;
            if (!byType.TryGetValue(type, out permit) || permit == null) return null;
            return permit.owner;
        }

        // ------------------------------------------------------------ regions

        private static IEnumerator<bool> RegionsSection(SectionContext ctx, WorldContext w)
        {
            var rm = ManagerBehaviour<RegionManager>.instance;
            if (rm == null) throw new InvalidOperationException("RegionManager unavailable");
            var regions = new List<Region>(rm.regions);
            var pm = ManagerBehaviour<PermitManager>.instance;
            var permits = pm != null ? ReflectionTable.Permits(pm) : null;
            if (permits == null) throw new InvalidOperationException("permit table unavailable");
            var fullPermit = pm.fullPermit;
            var result = new List<RegionDto>(regions.Count);
            for (int i = 0; i < regions.Count; i++)
            {
                try
                {
                    var r = regions[i];
                    if (r == null)
                    {
                        ctx.ItemsVanished++;
                        continue;
                    }
                    int cx, cy;
                    WorldContext.Coords(r.center, out cx, out cy);
                    var row = new RegionDto
                    {
                        region_id = w.RegionId(r),
                        name = r.regionName,
                        center_x = cx,
                        center_y = cy,
                        tile_count = r.tiles.count,
                        city_id = WorldContext.CityId(r.settlement),
                        resources = new List<RegionResourceDto>(),
                        resource_sites = new List<ResourceSiteDto>(),
                        permit_cost = fullPermit != null ? PermitCost(r, fullPermit) : (int?)null,
                        permit_auction_cooldown = r.permitAuctionCooldown,
                        permit_purchase_cooldown = r.permitPurchaseCooldown,
                    };
                    var res = r.availableResources;
                    if (res != null)
                    {
                        foreach (var kv in res)
                        {
                            if (kv.Key == null) continue;
                            bool water = kv.Value == int.MaxValue;
                            row.resources.Add(new RegionResourceDto { product = w.Name(kv.Key), tiles = water ? (int?)null : kv.Value, water_unlimited = water });
                        }
                    }
                    var sites = r.resourceSites;
                    if (sites != null)
                    {
                        for (int k = 0; k < sites.Count && k < 64; k++)
                        {
                            var site = sites[k];
                            if (site == null) continue;
                            int sx, sy;
                            WorldContext.Coords(site.center, out sx, out sy);
                            var resNode = site.resource;
                            row.resource_sites.Add(new ResourceSiteDto
                            {
                                product = resNode != null && resNode.resourceProduct != null ? w.Name(resNode.resourceProduct) : null,
                                center_x = sx,
                                center_y = sy,
                                radius = site.radius != null ? site.radius.max : 0,
                                amount = site.resourceAmount,
                                nodes = site.nodes != null ? site.nodes.Count : 0,
                            });
                        }
                    }
                    Dictionary<PermitType, Permit> byType;
                    Permit permit;
                    if (fullPermit != null && permits.TryGetValue(r, out byType) && byType != null && byType.TryGetValue(fullPermit, out permit) && permit != null)
                    {
                        var owner = permit.owner;
                        row.permit = new PermitDto
                        {
                            owner_actor_id = owner != null ? owner.id : (int?)null,
                            amount_paid = permit.amountPaid,
                            type = permit.type != null ? w.Name(permit.type) : null,
                        };
                    }
                    result.Add(row);
                    ctx.Items++;
                }
                catch (Exception e)
                {
                    ctx.ItemFailed("regions", e);
                }
                if (ctx.ShouldYield) yield return true;
            }
            Data(ctx).regions = result;
        }
    }
}
