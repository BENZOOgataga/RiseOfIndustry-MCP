using System;
using System.Collections.Generic;
using ProjectAutomata;
using RoiMcp.Observer.Core;
using RoiMcp.Observer.Diagnostics;
using RoiMcp.Observer.Dto;
using UnityEngine;

namespace RoiMcp.Observer.ReadLayer
{
    /// <summary>Per-capture aggregates collected by the building index pass.</summary>
    internal sealed class OwnerAggregate
    {
        public readonly Dictionary<string, int> ByTag = new Dictionary<string, int>(StringComparer.Ordinal);
        public readonly Dictionary<string, int> ByType = new Dictionary<string, int>(StringComparer.Ordinal);
        public readonly Dictionary<string, double> PaidByRegion = new Dictionary<string, double>(StringComparer.Ordinal);
        public int Count;
        public double PaidTotal;
    }

    /// <summary>state.json sections (PRD 12.2), in capture order.</summary>
    internal static partial class StateCapture
    {
        internal static readonly Dictionary<int, OwnerAggregate> Owners = new Dictionary<int, OwnerAggregate>();
        internal static readonly List<Building> PlayerBuildings = new List<Building>(1024);
        internal static readonly List<Building> AiBuildings = new List<Building>(4096);

        /// <summary>Drops every game reference held between sections of one capture.</summary>
        internal static void ReleaseScratch()
        {
            PlayerBuildings.Clear();
            AiBuildings.Clear();
            Owners.Clear();
            PathCandidates.Clear();
            EventScratch.Clear();
            InventoryScratch.Clear();
        }

        public static List<ISection> Sections(WorldContext w)
        {
            return new List<ISection>
            {
                new Section("building_index", false, ctx => IndexSection(ctx, w)),
                new Section("session", false, ctx => SessionSection(ctx, w)),
                new Section("buildings_player", false, ctx => BuildingsSection(ctx, w, true)),
                new Section("buildings_ai", false, ctx => AiCompactSection(ctx, w)),
                new Section("buildings_ai_detail", true, ctx => BuildingsSection(ctx, w, false)),
                new Section("companies", false, ctx => CompaniesSection(ctx, w)),
                new Section("routes_player", false, ctx => RoutesSection(ctx, w, true)),
                new Section("routes_ai", true, ctx => RoutesSection(ctx, w, false)),
                new Section("requests_player", false, ctx => RequestsSection(ctx, w)),
                new Section("shops", false, ctx => ShopsSection(ctx, w)),
                new Section("cities", false, ctx => CitiesSection(ctx, w)),
                new Section("regions", false, ctx => RegionsSection(ctx, w)),
                new Section("market", false, ctx => MarketSection(ctx, w)),
                new Section("research", false, ctx => ResearchSection(ctx, w)),
                new Section("vehicles", false, ctx => VehiclesSection(ctx, w)),
                new Section("route_paths", true, ctx => RoutePathsSection(ctx, w)),
            };
        }

        private static StateData Data(SectionContext ctx)
        {
            return (StateData)ctx.Data;
        }

        internal static bool IsPlayer(IActor a)
        {
            var human = Player.humanPlayer;
            return a != null && human != null && ReferenceEquals(a, human);
        }

        internal static bool IsAiPlayer(IActor a)
        {
            return a is AiPlayer;
        }

        // ------------------------------------------------------------ building index

        /// <summary>
        /// Walks every building once: assigns keys (with collision suffixing), splits player/AI building lists
        /// for the later sections and aggregates per-owner counts. Decoration and settlement buildings are
        /// only counted.
        /// </summary>
        private static IEnumerator<bool> IndexSection(SectionContext ctx, WorldContext w)
        {
            var bm = ManagerBehaviour<BuildingManager>.instance;
            if (bm == null) throw new InvalidOperationException("BuildingManager unavailable");
            var all = new List<Building>(bm.buildingsList);
            Owners.Clear();
            PlayerBuildings.Clear();
            AiBuildings.Clear();
            w.IndexIds.Clear();
            w.Keys.BeginPass();
            var human = Player.humanPlayer;
            for (int i = 0; i < all.Count; i++)
            {
                var b = all[i];
                if (b == null)
                {
                    ctx.ItemsVanished++;
                    continue;
                }
                int id = b.GetInstanceID();
                w.Key(b);
                var owner = b.buildingOwner;
                bool isPlayer = owner != null && human != null && ReferenceEquals(owner, human);
                bool isAi = owner is AiPlayer;
                if (isPlayer || isAi)
                {
                    // GUIDs are only needed for player/AI buildings (export and collision suffix).
                    w.Keys.SetGuid(id, ReflectionTable.SaveGuid(b));
                    var tags = w.Tags(b);
                    if (!tags.Contains("Decoration"))
                    {
                        if (isPlayer) PlayerBuildings.Add(b);
                        else AiBuildings.Add(b);
                    }
                    int ownerId = owner.id;
                    OwnerAggregate agg;
                    if (!Owners.TryGetValue(ownerId, out agg))
                    {
                        agg = new OwnerAggregate();
                        Owners[ownerId] = agg;
                    }
                    agg.Count++;
                    double paid = b.paidToBuildAmount;
                    agg.PaidTotal += paid;
                    var prefab = b.prefab;
                    string type = prefab != null ? w.Name(prefab) : "unknown";
                    int n;
                    agg.ByType.TryGetValue(type, out n);
                    agg.ByType[type] = n + 1;
                    for (int t = 0; t < tags.Count; t++)
                    {
                        agg.ByTag.TryGetValue(tags[t], out n);
                        agg.ByTag[tags[t]] = n + 1;
                    }
                    var rid = w.RegionId(b.region);
                    if (rid != null)
                    {
                        double p;
                        agg.PaidByRegion.TryGetValue(rid, out p);
                        agg.PaidByRegion[rid] = p + paid;
                    }
                }
                w.Keys.Register(id);
                w.IndexIds.Add(id);
                ctx.Items++;
                if (ctx.ShouldYield) yield return true;
            }
            w.Keys.EndPass();
            foreach (var c in w.Keys.NewCollisions)
                Log.Warn("ids", "building key collision detected and suffixed: " + c);
        }

        // ------------------------------------------------------------ session

        private static IEnumerator<bool> SessionSection(SectionContext ctx, WorldContext w)
        {
            var tm = ManagerBehaviour<TimeManager>.instance;
            var speed = ManagerBehaviour<SpeedControls>.instance;
            if (tm == null || speed == null) throw new InvalidOperationException("time managers unavailable");
            var today = tm.today;
            var s = new SessionDto
            {
                year = today.Year,
                month = today.Month,
                day = today.Day,
                game_day = Baseline.DayCount(today.Year, today.Month, today.Day),
                game_date = Baseline.GameDate(today.Year, today.Month, today.Day),
                speed_level = speed.level,
                time_scale = Time.timeScale,
                paused = speed.level == -1 || Time.timeScale == 0f,
                speed_levels = new List<float>(),
                seconds_per_day = tm.secondsPerDay,
                mods = new List<ModDto>(),
                module = new ModuleDto(),
            };
            if (speed.speedLevels != null) s.speed_levels.AddRange(speed.speedLevels);

            var gp = ManagerBehaviour<GameParametersManager>.instance;
            if (gp != null)
            {
                var d = gp.difficulty;
                if (d != null)
                    s.difficulty = new DifficultyDto
                    {
                        name = null,
                        prices = d.prices,
                        upkeep = d.upkeep,
                        dispatch = d.dispatch,
                        loan = d.loan,
                        score_modifier = d.scoreModifier,
                        easy_chains = d.easyChains,
                        infinite_money = d.infiniteMoney,
                    };
                var wp = gp.world;
                if (wp != null) s.world = new WorldParamsDto { size = wp.size, ai_count = wp.aiCount, tech_tree = wp.techTree, seed = wp.seed };
            }

            var repo = PersistentManagerBehaviour<GameModuleRepository>.instance;
            var module = repo != null ? repo.currentModule : null;
            if (module != null)
            {
                s.module.id = module.id;
                s.module.name = module.displayName;
            }
            s.language = Localization.LanguageCode();
            ModList(s.mods);

            var human = Player.humanPlayer;
            s.player_actor_id = human != null ? human.id : -1;
            var active = Player.activeActor;
            s.active_actor_differs = human != null && active != null && !ReferenceEquals(active, human);
            var egm = ManagerBehaviour<EndGameManager>.instance;
            s.used_cheats = egm != null && egm.usedCheats;
            var ach = AchievementManager.instance;
            if (ach != null) s.achievements_enabled = ach.achievementsEnabled;
            s.logistic_requests_enabled = GameOptions.logisticRequestsEnabled;
            var ttm = ManagerBehaviour<TechTreeManager>.instance;
            s.tech_tree_enabled = ttm != null && ttm.isEnabled;
            var pm = ManagerBehaviour<PermitManager>.instance;
            if (pm != null) s.permit_management_enabled = pm.permitManagementEnabled;
            var market = ManagerBehaviour<GlobalMarket>.instance;
            if (market != null)
            {
                s.market_update_interval_days = ReflectionTable.MarketInterval(market);
                s.market_days_since_update = ReflectionTable.MarketDaysSinceUpdate(market);
            }
            ctx.Items = 1;
            Data(ctx).session = s;
            yield break;
        }

        internal static void ModList(List<ModDto> into)
        {
            var loader = CachedManagerBehaviour<ModLoader>.instance;
            if (loader == null || loader.loadedMods == null) return;
            var mods = loader.loadedMods;
            for (int i = 0; i < mods.Count && i < 64; i++)
            {
                var m = mods[i];
                if (m == null || m.description == null) continue;
                into.Add(new ModDto { name = m.description.name, version = m.description.version, version_string = m.description.versionString });
            }
        }

        // ------------------------------------------------------------ companies

        private static IEnumerator<bool> CompaniesSection(SectionContext ctx, WorldContext w)
        {
            var result = new List<CompanyDto>();
            var human = Player.humanPlayer;
            var players = new List<Player>();
            if (human != null) players.Add(human);
            var aim = ManagerBehaviour<AiPlayerManager>.instance;
            if (aim != null)
            {
                var ais = aim.aiPlayers;
                for (int i = 0; i < ais.count; i++) players.Add(ais[i]);
            }
            var money = ManagerBehaviour<MoneyManager>.instance;
            var pm = ManagerBehaviour<PermitManager>.instance;
            var permits = pm != null ? ReflectionTable.Permits(pm) : null;
            var fullPermit = pm != null ? pm.fullPermit : null;

            for (int i = 0; i < players.Count; i++)
            {
                try
                {
                    var p = players[i];
                    if (p == null)
                    {
                        ctx.ItemsVanished++;
                        continue;
                    }
                    result.Add(Company(p, ReferenceEquals(p, human), w, money, permits, fullPermit));
                    ctx.Items++;
                }
                catch (Exception e)
                {
                    ctx.ItemFailed("companies", e);
                }
                if (ctx.ShouldYield) yield return true;
            }
            Data(ctx).companies = result;
        }

        private static CompanyDto Company(Player p, bool isPlayer, WorldContext w, MoneyManager money,
            Dictionary<Region, Dictionary<PermitType, Permit>> permits, PermitType fullPermit)
        {
            var c = new CompanyDto
            {
                actor_id = p.id,
                name = p.actorName,
                kind = isPlayer ? "human" : "ai",
                is_player = isPlayer,
                color = ColorHex(p.color),
                building_counts_by_tag = new Dictionary<string, int>(StringComparer.Ordinal),
                building_counts_by_type = new Dictionary<string, int>(StringComparer.Ordinal),
            };
            var hq = p.hq;
            if (hq != null)
            {
                c.hq_building = w.Key(hq);
                c.hq_city_id = WorldContext.CityId(hq.settlement);
            }

            var agent = Guard.Has<IMoneyAgent>(p) ? p.money : null;
            var cash = new CashDto();
            if (agent != null)
            {
                cash.infinite = agent.infiniteMoney;
                double balance;
                if (money != null && money.balances.TryGetValue(agent, out balance))
                {
                    cash.registered = true;
                    if (!cash.infinite) cash.value = balance;
                }
            }
            c.cash = cash;

            var loans = Guard.Has<ILoansAgent>(p) ? p.loans : null;
            if (loans != null)
            {
                c.max_loans = loans.maxLoans;
                c.loans = new List<LoanDto>();
                var list = loans.loans;
                if (list != null)
                {
                    for (int i = 0; i < list.Count; i++)
                    {
                        var l = list[i];
                        if (l == null) continue;
                        var lender = l.grantingActor;
                        c.loans.Add(new LoanDto
                        {
                            type = l.type.ToString(),
                            title = l.title,
                            lender_actor_id = lender != null ? lender.id : (int?)null,
                            lender_name = lender != null ? lender.actorName : null,
                            principal = l.amount,
                            apr = l.apr,
                            duration_months = l.duration,
                            remaining_payments = l.remainingPayments,
                            amount_with_apr = l.amountWithApr,
                            early_repay_amount = l.amountToPay,
                            grace_months_left = ReflectionTable.LoanFreeMonths(l),
                            settlement_loan = lender is SettlementBase,
                        });
                    }
                }
            }

            var shares = Guard.Has<CompanySharesAgent>(p) ? p.Get<CompanySharesAgent>() : null;
            if (shares != null)
            {
                var sd = new SharesDto { bundle_owners = new List<int?>(), bundle_count = shares.bundleCount };
                var bundles = shares.bundles;
                int competitors = 0;
                for (int i = 0; i < bundles.count; i++)
                {
                    var b = bundles[i];
                    if (b == null) continue;
                    sd.bundle_size = b.size;
                    var owner = b.owner;
                    sd.bundle_owners.Add(owner != null ? owner.id : (int?)null);
                    if (owner != null && !ReferenceEquals(owner, p)) competitors++;
                }
                sd.owned_by_competitors = competitors;
                c.shares = sd;
            }

            var stats = Guard.Has<ActorStatisticsAgent>(p) ? p.Get<ActorStatisticsAgent>() : null;
            if (stats != null)
            {
                var st = new CompanyStatsDto
                {
                    cashflow = stats.cashflow.ToString(),
                    top_production = Products(stats.topProduction, w),
                    top_sales = Products(stats.topSales, w),
                    owned_permits = new List<string>(),
                    main_tech_tree = stats.mainTechTree != null ? w.Name(stats.mainTechTree) : null,
                };
                var owned = stats.ownedPermits;
                if (owned != null)
                    for (int i = 0; i < owned.Count; i++) st.owned_permits.Add(w.RegionId(owned[i]));
                c.stats = st;
            }

            OwnerAggregate agg;
            if (Owners.TryGetValue(p.id, out agg))
            {
                foreach (var kv in agg.ByTag) c.building_counts_by_tag[kv.Key] = kv.Value;
                foreach (var kv in agg.ByType) c.building_counts_by_type[kv.Key] = kv.Value;
                c.building_count = agg.Count;
                c.paid_to_build_total = agg.PaidTotal;
            }

            if (permits != null && fullPermit != null)
            {
                c.value_inputs = new List<RegionValueInputDto>();
                foreach (var kv in permits)
                {
                    var region = kv.Key;
                    if (region == null || kv.Value == null) continue;
                    Permit permit;
                    if (!kv.Value.TryGetValue(fullPermit, out permit) || permit == null) continue;
                    if (!ReferenceEquals(permit.owner, p)) continue;
                    var rid = w.RegionId(region);
                    double paid = 0;
                    if (agg != null) agg.PaidByRegion.TryGetValue(rid, out paid);
                    c.value_inputs.Add(new RegionValueInputDto
                    {
                        region_id = rid,
                        permit_cost = PermitCost(region, fullPermit),
                        paid_to_build_in_region = paid,
                    });
                }
            }

            var ai = p as AiPlayer;
            if (ai != null)
            {
                var a = new AiStateDto
                {
                    personality = ai.personality != null ? w.Name(ai.personality) : null,
                    owned_regions = new List<string>(),
                    has_initiative = ai.hasInitiative,
                };
                var regions = ai.ownedRegions;
                for (int i = 0; i < regions.count; i++) a.owned_regions.Add(w.RegionId(regions[i]));
                c.ai = a;
            }

            var contracts = Guard.Has<IContractsAgent>(p) ? p.contracts : null;
            if (contracts != null)
            {
                var cd = new ContractsAgentDto { max_contracts = contracts.maxContracts, can_accept = contracts.canAcceptContracts, active = new List<ContractDto>() };
                var active = contracts.activeContracts;
                for (int i = 0; i < active.count; i++)
                {
                    var ct = active[i];
                    if (ct != null) cd.active.Add(ContractRow(ct, w));
                }
                c.contracts = cd;
            }
            return c;
        }

        /// <summary>Replica of PermitManager.GetPermitCost (D-PERMIT-1), computed from fields only.</summary>
        internal static int PermitCost(Region region, PermitType type)
        {
            if (region == null || type == null) return 0;
            var top = type.topLevelParent;
            float costPerTile = top != null ? top.costPerTile : type.costPerTile;
            float num = region.tiles.count * costPerTile;
            num *= type.costModifier;
            if (region.settlement == null) num *= 0.75f;
            return (int)Math.Round((double)num);
        }

        internal static ContractDto ContractRow(Contract ct, WorldContext w)
        {
            var issuer = ct.issuer;
            var accepted = ct.acceptedActor;
            var target = ct.target;
            var row = new ContractDto
            {
                product = ct.product != null ? w.Name(ct.product) : null,
                amount = ct.amount,
                delivered = ct.delivered,
                reserved = ct.reserved,
                issuer_actor_id = issuer != null ? issuer.id : (int?)null,
                issuer_name = issuer != null ? issuer.actorName : null,
                target_building = target != null ? w.Key(target.building) : null,
                accepted_actor_id = accepted != null ? accepted.id : (int?)null,
                active = ct.active,
                completed = ct.completed,
            };
            var dc = ct as DeliveryContract;
            if (dc != null)
            {
                row.price = dc.price;
                var reward = dc.reward;
                if (reward != null) row.reward = (double)reward.money * Math.Sign(reward.sign == 0 ? 1 : reward.sign);
                var penalty = dc.penalty;
                if (penalty != null) row.penalty = (double)penalty.money * Math.Sign(penalty.sign == 0 ? 1 : penalty.sign);
                row.remaining_days = dc.remainingDays;
                row.duration_days = dc.duration;
                row.fulfilled = dc.fulfilled;
                row.failed = dc.failed;
            }
            return row;
        }

        private static List<ProductAmountFloatDto> Products(List<Product> list, WorldContext w)
        {
            var r = new List<ProductAmountFloatDto>();
            if (list == null) return r;
            for (int i = 0; i < list.Count; i++)
            {
                var p = list[i];
                if (p == null) continue;
                var def = p.definition;
                r.Add(new ProductAmountFloatDto { product = def != null ? w.Name(def) : null, amount = p.amount });
            }
            return r;
        }

        private static string ColorHex(Color c)
        {
            int r = Math.Max(0, Math.Min(255, (int)Math.Round(c.r * 255.0)));
            int g = Math.Max(0, Math.Min(255, (int)Math.Round(c.g * 255.0)));
            int b = Math.Max(0, Math.Min(255, (int)Math.Round(c.b * 255.0)));
            return "#" + r.ToString("X2") + g.ToString("X2") + b.ToString("X2");
        }
    }
}
