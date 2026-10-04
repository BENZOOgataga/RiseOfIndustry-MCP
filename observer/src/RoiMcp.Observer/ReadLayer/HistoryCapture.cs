using System;
using System.Collections.Generic;
using ProjectAutomata;
using RoiMcp.Observer.Core;
using RoiMcp.Observer.Dto;

namespace RoiMcp.Observer.ReadLayer
{
    /// <summary>
    /// history.json (PRD 12.4): only history the game itself retains. Captured monthly, on READY entry and on
    /// a history refresh request. Month queries are month-aligned (day 1 .. day 30) as the game's own UI does.
    /// </summary>
    internal static class HistoryCapture
    {
        public const int LedgerYearsBack = 3;
        public const int ProductionMonths = 24;
        public const int ShopMonths = 12;
        public const int SeriesMonths = 24;

        public static List<ISection> Sections(WorldContext w)
        {
            return new List<ISection>
            {
                new Section("ledger_player", false, ctx => Ledger(ctx, w)),
                new Section("buildings_monthly_player", false, ctx => BuildingSeries(ctx, w)),
                new Section("production_monthly_player", false, ctx => ProductionSeries(ctx, w)),
                new Section("shops_monthly", false, ctx => ShopSeries(ctx, w)),
                new Section("player_product_stats", false, ctx => PlayerProductStats(ctx, w)),
                new Section("state_sales", false, ctx => StateSales(ctx, w)),
            };
        }

        private static HistoryData Data(SectionContext ctx)
        {
            return (HistoryData)ctx.Data;
        }

        private static GameDate Today()
        {
            var tm = ManagerBehaviour<TimeManager>.instance;
            if (tm == null) throw new InvalidOperationException("TimeManager unavailable");
            return tm.today;
        }

        /// <summary>Bounds of an exported window of the given length ending with the current month (clamped to Y1-01).</summary>
        private struct HistoryWindow
        {
            public int Months;
            public int FirstKey;
            public string First;
            public string Last;
            public bool HasOlder;     // a calendar month exists before First
            public GameDate OlderEnd; // last day before the window (valid when HasOlder)

            public static HistoryWindow Of(GameDate today, int months)
            {
                int fy, fm;
                int span = MonthWindow.Bounds(today.Year, today.Month, months, out fy, out fm);
                var w = new HistoryWindow
                {
                    FirstKey = fy * 12 + fm,
                    Months = span,
                    First = Baseline.GameMonth(fy, fm),
                    Last = Baseline.GameMonth(today.Year, today.Month),
                    HasOlder = fy > 1 || fm > 1,
                };
                if (w.HasOlder)
                {
                    int py = fy, pm = fm;
                    PrevMonth(ref py, ref pm);
                    w.OlderEnd = new GameDate(py, pm, 30);
                }
                return w;
            }

            public bool StartsAfter(int year, int month)
            {
                return year * 12 + month < FirstKey;
            }
        }

        private static readonly GameDate FirstDay = new GameDate(1, 1, 1);

        private static void PrevMonth(ref int y, ref int m)
        {
            m--;
            if (m < 1)
            {
                m = 12;
                y--;
            }
        }

        /// <summary>Collects the player's buildings (time-sliced: the building list includes every house).</summary>
        private static IEnumerator<bool> PlayerBuildings(SectionContext ctx, List<Building> into)
        {
            into.Clear();
            var human = Player.humanPlayer;
            var bm = ManagerBehaviour<BuildingManager>.instance;
            if (human == null || bm == null) yield break;
            var all = new List<Building>(bm.buildingsList);
            for (int i = 0; i < all.Count; i++)
            {
                var b = all[i];
                if (b != null && ReferenceEquals(b.buildingOwner, human)) into.Add(b);
                if ((i & 255) == 255 && ctx.ShouldYield) yield return true;
            }
        }

        // ------------------------------------------------------------ ledger

        private static IEnumerator<bool> Ledger(SectionContext ctx, WorldContext w)
        {
            var human = Player.humanPlayer;
            if (human == null) throw new InvalidOperationException("no human player");
            if (!Guard.Has<IMoneyAgent>(human)) throw new InvalidOperationException("money agent not cached");
            var agent = human.money;
            if (agent == null) throw new InvalidOperationException("no money agent");
            var cats = new List<MoneyBillCategory>();
            var ro = GameData.instance.GetAssetsRO(typeof(MoneyBillCategory));
            for (int i = 0; i < ro.count; i++)
            {
                var c = ro[i] as MoneyBillCategory;
                if (c != null) cats.Add(c);
            }
            var today = Today();
            var ledger = new LedgerDto
            {
                actor_id = human.id,
                current_month = Baseline.GameMonth(today.Year, today.Month),
                months = new List<LedgerMonthDto>(),
            };
            var ma = agent as MoneyAgent;
            if (ma != null) ledger.retention_years = ma.historicalDataRange;
            var mm = ManagerBehaviour<MoneyManager>.instance;
            double balance;
            ledger.balance_infinite = agent.infiniteMoney;
            if (mm != null && mm.balances.TryGetValue(agent, out balance)) ledger.balance_now = balance;

            int y = today.Year, m = today.Month;
            int monthsBack = 12 * LedgerYearsBack + today.Month;
            for (int k = 0; k < monthsBack && y >= 1; k++)
            {
                var from = new GameDate(y, m, 1);
                var to = new GameDate(y, m, 30);
                var row = new LedgerMonthDto
                {
                    month = Baseline.GameMonth(y, m),
                    current_month_to_date = y == today.Year && m == today.Month,
                    categories = new List<LedgerCategoryDto>(),
                };
                for (int c = 0; c < cats.Count; c++)
                {
                    double income = agent.GetIncome(from, to, cats[c]);
                    double expense = agent.GetExpenses(from, to, cats[c]);
                    if (income == 0 && expense == 0) continue;
                    row.categories.Add(new LedgerCategoryDto { category = w.Name(cats[c]), income = income, expense = expense });
                    row.income_total += income;
                    row.expense_total += expense;
                }
                ledger.months.Add(row);
                ctx.Items++;
                PrevMonth(ref y, ref m);
                if (ctx.ShouldYield) yield return true;
            }
            // Months are listed newest first; report the retained range.
            for (int i = ledger.months.Count - 1; i >= 0; i--)
            {
                if (ledger.months[i].categories.Count > 0)
                {
                    ledger.first_month_available = ledger.months[i].month;
                    break;
                }
            }
            ledger.last_month = ledger.current_month;
            ledger.window_months = ledger.months.Count;
            ledger.history_truncated = ledger.retention_years.HasValue ? ledger.retention_years.Value > LedgerYearsBack : (bool?)null;
            Data(ctx).ledger_player = ledger;
        }

        // ------------------------------------------------------------ building analysis series

        private static IEnumerator<bool> BuildingSeries(SectionContext ctx, WorldContext w)
        {
            var buildings = new List<Building>();
            var collect = PlayerBuildings(ctx, buildings);
            while (collect.MoveNext()) yield return true;
            var result = new List<BuildingSeriesDto>();
            var values = new List<BuildingAnalysis.TimeValue>(64);
            var defs = new List<AnalysisItemDefinition>(8);
            var today = Today();
            var window = new MonthWindow(SeriesMonths);
            var bounds = HistoryWindow.Of(today, SeriesMonths);
            for (int i = 0; i < buildings.Count; i++)
            {
                var b = buildings[i];
                if (b == null)
                {
                    ctx.ItemsVanished++;
                    continue;
                }
                var analysis = b.analysis;
                if (analysis == null) continue;
                defs.Clear();
                foreach (var d in analysis.GetAllAnalysisItemDefs())
                {
                    if (d != null) defs.Add(d);
                    if (defs.Count >= 16) break;
                }
                if (defs.Count == 0) continue;
                var row = new BuildingSeriesDto { building = w.Key(b), series = new List<AnalysisSeriesDto>() };
                bool vanished = false;
                for (int k = 0; k < defs.Count; k++)
                {
                    values.Clear();
                    analysis.GetValues(defs[k], values);
                    row.series.Add(MonthlySeries(w, defs[k], values, today, window, bounds));
                    // One series is the atomic unit: some series are daily and span the whole save (E3).
                    if (ctx.ShouldYield)
                    {
                        yield return true;
                        if (b == null)
                        {
                            vanished = true;
                            break;
                        }
                    }
                }
                values.Clear();
                if (vanished)
                {
                    ctx.ItemsVanished++;
                    continue;
                }
                result.Add(row);
                ctx.Items++;
                if (ctx.ShouldYield) yield return true;
            }
            values.TrimExcess();
            Data(ctx).buildings_monthly_player = result;
        }

        /// <summary>
        /// Aggregates one analysis series into the last <see cref="SeriesMonths"/> calendar months with the game's
        /// own rule (BuildingAnalysis.AccumulateValue: sum, or mean for AVERAGED items). The game keeps these
        /// series for the whole save, some with one value per day, so they are not exported raw (E3).
        /// </summary>
        private static AnalysisSeriesDto MonthlySeries(WorldContext w, AnalysisItemDefinition def, List<BuildingAnalysis.TimeValue> values,
            GameDate today, MonthWindow window, HistoryWindow bounds)
        {
            bool averaged = def.processingType == AnalysisItemOverallProcessingType.AVERAGED;
            var s = new AnalysisSeriesDto
            {
                item = w.Name(def),
                aggregation = averaged ? "average" : "sum",
                values_retained = values.Count,
                window_months = bounds.Months,
                window_first_month = bounds.First,
                window_last_month = bounds.Last,
                history_truncated = false,
                months = new List<MonthValueDto>(),
            };
            if (values.Count > 0)
            {
                // The game keeps the series in date order (TimeTree): the oldest retained value comes first.
                var first = values[0].date;
                s.first_month_available = Baseline.GameMonth(first.Year, first.Month);
                s.history_truncated = bounds.StartsAfter(first.Year, first.Month);
            }
            window.Reset(today.Year, today.Month);
            for (int i = 0; i < values.Count; i++)
            {
                var tv = values[i];
                var date = tv.date;
                window.Add(date.Year, date.Month, tv.value);
            }
            for (int k = window.Months - 1; k >= 0; k--)
            {
                if (!window.Has(k)) continue;
                int y, m;
                MonthWindow.Back(today.Year, today.Month, k, out y, out m);
                s.months.Add(new MonthValueDto { month = Baseline.GameMonth(y, m), value = window.Value(k, averaged) });
            }
            return s;
        }

        // ------------------------------------------------------------ produced / consumed per month

        private static IEnumerator<bool> ProductionSeries(SectionContext ctx, WorldContext w)
        {
            var buildings = new List<Building>();
            var collect = PlayerBuildings(ctx, buildings);
            while (collect.MoveNext()) yield return true;
            var result = new List<ProductionSeriesDto>();
            var today = Today();
            var bounds = HistoryWindow.Of(today, ProductionMonths);
            var products = new List<ProductDefinition>(8);
            for (int i = 0; i < buildings.Count; i++)
            {
                var b = buildings[i];
                if (b == null)
                {
                    ctx.ItemsVanished++;
                    continue;
                }
                var ru = b.recipeUser;
                if (ru == null) continue;
                var recipe = ru.currentRecipe;
                if (recipe == null) continue;
                products.Clear();
                Collect(recipe.result, products);
                Collect(recipe.ingredients, products);
                string key = w.Key(b);
                for (int p = 0; p < products.Count; p++)
                {
                    var def = products[p];
                    var row = new ProductionSeriesDto
                    {
                        building = key,
                        product = w.Name(def),
                        window_months = bounds.Months,
                        window_first_month = bounds.First,
                        window_last_month = bounds.Last,
                        history_truncated = false,
                        months = new List<ProducedMonthDto>(),
                    };
                    int y = today.Year, m = today.Month;
                    for (int k = 0; k < ProductionMonths && y >= 1; k++)
                    {
                        var from = new GameDate(y, m, 1);
                        var to = new GameDate(y, m, 30);
                        int produced = ru.GetProducedInRange(def, from, to);
                        int consumed = ru.GetConsumedInRange(def, from, to);
                        if (produced != 0 || consumed != 0)
                            row.months.Add(new ProducedMonthDto { month = Baseline.GameMonth(y, m), produced = produced, consumed = consumed });
                        PrevMonth(ref y, ref m);
                    }
                    // One range query before the window tells whether the game still holds older records (it prunes
                    // them at year end, so usually it does not).
                    if (bounds.HasOlder)
                        row.history_truncated = ru.GetProducedInRange(def, FirstDay, bounds.OlderEnd) != 0 ||
                                                ru.GetConsumedInRange(def, FirstDay, bounds.OlderEnd) != 0;
                    if (row.months.Count > 0 || row.history_truncated == true) result.Add(row);
                    if (ctx.ShouldYield)
                    {
                        yield return true;
                        if (b == null) break; // destroyed while yielded (PRD 8.5 revalidation)
                    }
                }
                ctx.Items++;
                if (ctx.ShouldYield) yield return true;
            }
            Data(ctx).production_monthly_player = result;
        }

        private static void Collect(ProductList list, List<ProductDefinition> into)
        {
            if (list == null || list.entries == null) return;
            for (int i = 0; i < list.entries.Count; i++)
            {
                var e = list.entries[i];
                var def = e != null ? e.definition : null;
                if (def != null && !into.Contains(def)) into.Add(def);
            }
        }

        // ------------------------------------------------------------ shops

        private static IEnumerator<bool> ShopSeries(SectionContext ctx, WorldContext w)
        {
            var sm = ManagerBehaviour<SettlementManager>.instance;
            if (sm == null) throw new InvalidOperationException("SettlementManager unavailable");
            var settlements = new List<SettlementBase>(sm.settlements);
            var today = Today();
            var bounds = HistoryWindow.Of(today, ShopMonths);
            var result = new List<ShopSeriesDto>();
            var demand = new List<ProductInfoCollection.SaleInfo>(16);
            var sold = new List<ProductDefinition>(16);
            for (int s = 0; s < settlements.Count; s++)
            {
                var st = settlements[s];
                if (st == null || st.buildings == null || st.buildings.shops == null) continue;
                var shops = new List<Shop>(st.buildings.shops);
                for (int i = 0; i < shops.Count; i++)
                {
                    var shop = shops[i];
                    if (shop == null || shop.building == null) continue;
                    sold.Clear();
                    if (shop.sold != null) sold.AddRange(shop.sold);
                    string key = w.Key(shop.building);
                    for (int p = 0; p < sold.Count; p++)
                    {
                        var def = sold[p];
                        if (def == null) continue;
                        var row = new ShopSeriesDto
                        {
                            shop = key,
                            product = w.Name(def),
                            window_months = bounds.Months,
                            window_first_month = bounds.First,
                            window_last_month = bounds.Last,
                            // One range query before the window: older sales the game still holds (it keeps ~2 years).
                            history_truncated = bounds.HasOlder && shop.GetSoldCount(def, FirstDay, bounds.OlderEnd) != 0,
                            months = new List<ShopMonthDto>(),
                        };
                        int y = today.Year, m = today.Month;
                        for (int k = 0; k < ShopMonths && y >= 1; k++)
                        {
                            var from = new GameDate(y, m, 1);
                            var to = new GameDate(y, m, 30);
                            int soldCount = shop.GetSoldCount(def, from, to);
                            demand.Clear();
                            shop.GetDemandRanged(def, from, to, demand);
                            int demandSum = 0;
                            for (int d = 0; d < demand.Count; d++) demandSum += demand[d].amount;
                            row.months.Add(new ShopMonthDto { month = Baseline.GameMonth(y, m), sold = soldCount, demand = demand.Count > 0 ? demandSum : (int?)null });
                            PrevMonth(ref y, ref m);
                        }
                        demand.Clear();
                        result.Add(row);
                        if (ctx.ShouldYield)
                        {
                            yield return true;
                            if (shop == null || shop.building == null) break; // destroyed while yielded
                        }
                    }
                    ctx.Items++;
                    if (ctx.ShouldYield) yield return true;
                }
            }
            Data(ctx).shops_monthly = result;
        }

        // ------------------------------------------------------------ player product statistics

        private static IEnumerator<bool> PlayerProductStats(SectionContext ctx, WorldContext w)
        {
            var tracker = ManagerBehaviour<ProductionStatsTracker>.instance;
            if (tracker == null) throw new InvalidOperationException("ProductionStatsTracker unavailable");
            var ro = GameData.instance.GetAssetsRO(typeof(ProductDefinition));
            var products = new List<ProductDefinition>(ro.count);
            for (int i = 0; i < ro.count; i++)
            {
                var p = ro[i] as ProductDefinition;
                if (p != null) products.Add(p);
            }
            var result = new List<PlayerProductStatsDto>();
            for (int i = 0; i < products.Count; i++)
            {
                var p = products[i];
                int produced = tracker.GetProducedAmount(p);
                int sold = tracker.GetSoldCount(p);
                int used = tracker.GetUsedCount(p);
                if (produced == 0 && sold == 0 && used == 0) continue;
                result.Add(new PlayerProductStatsDto
                {
                    product = w.Name(p),
                    produced = produced,
                    production_cost = tracker.GetProductionCost(p),
                    distribution_cost = tracker.GetDistributionCost(p),
                    total_cost = tracker.GetTotalCost(p),
                    sold = sold,
                    price_sold = tracker.GetPriceSold(p),
                    profit = tracker.GetProfit(p),
                    markup = tracker.GetMarkup(p),
                    used = used,
                });
                ctx.Items++;
                if (ctx.ShouldYield) yield return true;
            }
            Data(ctx).player_product_stats = result;
        }

        // ------------------------------------------------------------ State sales

        private static IEnumerator<bool> StateSales(SectionContext ctx, WorldContext w)
        {
            var state = ProjectAutomata.State.instance;
            if (state == null) throw new InvalidOperationException("State unavailable");
            var ro = GameData.instance.GetAssetsRO(typeof(ProductDefinition));
            var month = new GamePeriod(0, 0, 30);
            var twoMonths = new GamePeriod(0, 0, 60);
            var result = new List<StateSalesDto>();
            for (int i = 0; i < ro.count; i++)
            {
                var p = ro[i] as ProductDefinition;
                if (p == null) continue;
                int s30 = state.GetSoldCount(p, month);
                int s60 = state.GetSoldCount(p, twoMonths);
                if (s30 == 0 && s60 == 0) continue;
                result.Add(new StateSalesDto { product = w.Name(p), sold_last_30d = s30, sold_last_60d = s60 });
                ctx.Items++;
                if (ctx.ShouldYield) yield return true;
            }
            Data(ctx).state_sales = result;
        }
    }
}
