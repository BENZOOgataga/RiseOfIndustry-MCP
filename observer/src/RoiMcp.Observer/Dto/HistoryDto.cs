using System.Collections.Generic;

#pragma warning disable IDE1006

namespace RoiMcp.Observer.Dto
{
    [DataMap("company.ledger"), Source("SAVE"), Doc("history.json data: only history the game itself retains (PRD 12.4).")]
    public sealed class HistoryData
    {
        [Nullable] public LedgerDto ledger_player;
        [Nullable] public List<BuildingSeriesDto> buildings_monthly_player;
        [Nullable] public List<ProductionSeriesDto> production_monthly_player;
        [Nullable] public List<ShopSeriesDto> shops_monthly;
        [Nullable] public List<PlayerProductStatsDto> player_product_stats;
        [Nullable] public List<StateSalesDto> state_sales;
    }

    [DataMap("company.ledger"), Source("SAVE")]
    public sealed class LedgerDto
    {
        public int actor_id;
        [Doc("Y<year>-<MM> of the current, in-progress month")] public string current_month;
        public List<LedgerMonthDto> months;
        [Nullable] public string first_month_available;
        [Nullable] public string last_month;
        [Nullable] public int? retention_years;

        [Doc("Number of calendar months queried, counting the current month (the game's retention period plus the current year)")]
        public int window_months;

        [Nullable, Doc("True when the game's retention (retention_years) is longer than the exported window, so older ledger months may exist in the game; false when the window covers the retention period; null when the retention is unknown")]
        public bool? history_truncated;

        public double balance_now;
        public bool balance_infinite;
    }

    [DataMap("company.ledger"), Source("SAVE")]
    public sealed class LedgerMonthDto
    {
        public string month;
        public bool current_month_to_date;
        public double income_total;
        public double expense_total;
        public List<LedgerCategoryDto> categories;
    }

    [DataMap("company.revenue_breakdown"), Source("SAVE")]
    public sealed class LedgerCategoryDto
    {
        public string category;
        public double income;
        public double expense;
    }

    [DataMap("building.history"), Source("SAVE")]
    public sealed class BuildingSeriesDto
    {
        public string building;
        public List<AnalysisSeriesDto> series;
    }

    [DataMap("building.history"), Source("SAVE")]
    public sealed class AnalysisSeriesDto
    {
        public string item;

        [Doc("Size of the exported window in calendar months, counting the current month (V1 exports a bounded recent window, not all history the game retains)")]
        public int window_months;

        [Doc("Oldest calendar month inside the exported window (game month label)")]
        public string window_first_month;

        [Doc("Newest calendar month inside the exported window: the current, in-progress month")]
        public string window_last_month;

        [Nullable, Doc("True when the game still holds data older than window_first_month, so this series is a truncated view of game-retained history; false when the window covers everything the game holds; null when it could not be determined")]
        public bool? history_truncated;

        [EnumValues("sum", "average"), Doc("How the values of one month are combined, as the game does for this item (sum, or mean for averaged items such as percentages)")]
        public string aggregation;

        [Nullable, Doc("Oldest month the game still retains for this series (game month label), which can lie far before window_first_month; null when the series is empty")]
        public string first_month_available;

        [Doc("Number of raw values the game retains for this series (some series hold one value per day)")]
        public int values_retained;

        [Doc("Months of the exported window, oldest first; months without values are omitted")]
        public List<MonthValueDto> months;
    }

    [DataMap("building.history"), Source("SAVE")]
    public sealed class MonthValueDto
    {
        public string month;
        public double value;
    }

    [DataMap("production.history"), Source("SAVE")]
    public sealed class ProductionSeriesDto
    {
        public string building;
        public string product;

        [Doc("Size of the exported window in calendar months, counting the current month (V1 exports a bounded recent window, not all history the game retains)")]
        public int window_months;

        [Doc("Oldest calendar month inside the exported window (game month label)")]
        public string window_first_month;

        [Doc("Newest calendar month inside the exported window: the current, in-progress month")]
        public string window_last_month;

        [Nullable, Doc("True when the game still holds data older than window_first_month, so this series is a truncated view of game-retained history; false when the window covers everything the game holds; null when it could not be determined")]
        public bool? history_truncated;

        [Nullable, Doc("Oldest month the game still retains for this building and product; always null in V1 (not readable without scanning the game's records); use history_truncated")]
        public string first_month_available;

        [Doc("Months of the exported window with production or consumption, oldest first")]
        public List<ProducedMonthDto> months;
    }

    [DataMap("production.history"), Source("SAVE")]
    public sealed class ProducedMonthDto
    {
        public string month;
        public int produced;
        public int consumed;
    }

    [DataMap("shop.history"), Source("SAVE")]
    public sealed class ShopSeriesDto
    {
        public string shop;
        public string product;

        [Doc("Size of the exported window in calendar months, counting the current month (V1 exports a bounded recent window, not all history the game retains)")]
        public int window_months;

        [Doc("Oldest calendar month inside the exported window (game month label)")]
        public string window_first_month;

        [Doc("Newest calendar month inside the exported window: the current, in-progress month")]
        public string window_last_month;

        [Nullable, Doc("True when the game still holds data older than window_first_month, so this series is a truncated view of game-retained history; false when the window covers everything the game holds; null when it could not be determined")]
        public bool? history_truncated;

        [Nullable, Doc("Oldest month the game still retains for this shop and product; always null in V1 (not readable without scanning the game's records); use history_truncated")]
        public string first_month_available;

        [Doc("Months of the exported window, oldest first")]
        public List<ShopMonthDto> months;
    }

    [DataMap("shop.history"), Source("SAVE")]
    public sealed class ShopMonthDto
    {
        public string month;
        public int sold;
        [Nullable] public int? demand;
    }

    [DataMap("production.player_product_stats"), Source("SAVE")]
    public sealed class PlayerProductStatsDto
    {
        public string product;
        public double produced;
        public double production_cost;
        public double distribution_cost;
        public double total_cost;
        public double sold;
        public double price_sold;
        public double profit;
        public double markup;
        public double used;
    }

    [DataMap("market.state_history"), Source("SAVE")]
    public sealed class StateSalesDto
    {
        public string product;
        public int sold_last_30d;
        public int sold_last_60d;
    }
}
