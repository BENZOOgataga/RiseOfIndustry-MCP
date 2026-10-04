using System.Collections.Generic;

#pragma warning disable IDE1006

namespace RoiMcp.Observer.Dto
{
    [DataMap("game.scene_state"), Source("RUNTIME"),
     Doc("state.json data (PRD 12.2). Each section is null when its envelope section status is not ok.")]
    public sealed class StateData
    {
        [Nullable] public SessionDto session;
        [Nullable] public List<CompanyDto> companies;
        [Nullable] public List<BuildingDto> buildings_player;
        [Nullable] public List<RouteDto> routes_player;
        [Nullable] public List<WarehouseRequestDto> requests_player;
        [Nullable] public List<BuildingCompactDto> buildings_ai;
        [Nullable] public List<BuildingDto> buildings_ai_detail;
        [Nullable] public List<RouteDto> routes_ai;
        [Nullable] public List<ShopDto> shops;
        [Nullable] public List<CityDto> cities;
        [Nullable] public List<RegionDto> regions;
        [Nullable] public MarketDto market;
        [Nullable] public ResearchDto research;
        [Nullable] public VehiclesDto vehicles;
        [Nullable] public List<RoutePathDto> route_paths;
    }

    // ---------------------------------------------------------------- session

    [DataMap("game.date"), Source("RUNTIME")]
    public sealed class SessionDto
    {
        public string game_date;
        public int game_day;
        public int year;
        public int month;
        public int day;
        [DataMap("game.speed")] public int speed_level;
        [DataMap("game.speed")] public float time_scale;
        [DataMap("game.speed")] public bool paused;
        [DataMap("game.speed")] public List<float> speed_levels;
        [DataMap("game.seconds_per_day")] public float seconds_per_day;
        [DataMap("game.difficulty"), Nullable] public DifficultyDto difficulty;
        [DataMap("game.difficulty"), Nullable] public WorldParamsDto world;
        [DataMap("game.module")] public ModuleDto module;
        [DataMap("game.language"), Nullable] public string language;
        [DataMap("game.mods")] public List<ModDto> mods;
        [DataMap("company.player")] public int player_actor_id;
        [DataMap("company.player")] public bool active_actor_differs;
        [DataMap("game.cheats_achievements")] public bool used_cheats;
        [DataMap("game.cheats_achievements"), Nullable] public bool? achievements_enabled;
        [DataMap("logistics.logistic_requests"), Nullable] public bool? logistic_requests_enabled;
        [DataMap("tech.manager")] public bool tech_tree_enabled;
        [DataMap("region.permits"), Nullable] public bool? permit_management_enabled;
        [DataMap("market.global_prices"), Nullable] public int? market_update_interval_days;
        [DataMap("market.global_prices"), Nullable] public int? market_days_since_update;
    }

    [DataMap("game.difficulty"), Source("SAVE")]
    public sealed class DifficultyDto
    {
        [Nullable] public string name;
        public float prices;
        public float upkeep;
        public float dispatch;
        public float loan;
        public float score_modifier;
        public bool easy_chains;
        public bool infinite_money;
    }

    [DataMap("game.difficulty"), Source("SAVE")]
    public sealed class WorldParamsDto
    {
        public int size;
        public int ai_count;
        public bool tech_tree;
        [Nullable] public int? seed;
    }

    // ---------------------------------------------------------------- companies

    [DataMap("company.identity"), Source("SAVE")]
    public sealed class CompanyDto
    {
        public int actor_id;
        public string name;
        [EnumValues("human", "ai")] public string kind;
        public bool is_player;
        [Nullable, Doc("#RRGGBB")] public string color;
        [Nullable] public string hq_building;
        [Nullable] public int? hq_city_id;
        [DataMap("company.cash")] public CashDto cash;
        [DataMap("company.loans"), Nullable] public List<LoanDto> loans;
        [DataMap("company.loans"), Nullable] public int? max_loans;
        [DataMap("company.shares"), Nullable] public SharesDto shares;
        [DataMap("company.cashflow_label"), Nullable] public CompanyStatsDto stats;
        [DataMap("company.buildings")] public Dictionary<string, int> building_counts_by_tag;
        [DataMap("company.buildings")] public Dictionary<string, int> building_counts_by_type;
        [DataMap("company.buildings")] public int building_count;
        [DataMap("company.total_assets"), Doc("sum of paid_to_build over the company's buildings")] public double paid_to_build_total;
        [DataMap("company.value"), Nullable] public List<RegionValueInputDto> value_inputs;
        [DataMap("company.ai_state"), Nullable] public AiStateDto ai;
        [DataMap("market.contracts"), Nullable] public ContractsAgentDto contracts;
    }

    [DataMap("company.cash"), Source("SAVE")]
    public sealed class CashDto
    {
        [Nullable] public double? value;
        public bool infinite;
        public bool registered;
    }

    [DataMap("company.loans"), Source("SAVE")]
    public sealed class LoanDto
    {
        [Nullable] public string type;
        [Nullable] public string title;
        [Nullable] public int? lender_actor_id;
        [Nullable] public string lender_name;
        public double principal;
        public double apr;
        public int duration_months;
        public int remaining_payments;
        public double amount_with_apr;
        public double early_repay_amount;
        [Nullable] public int? grace_months_left;
        [Nullable] public bool? settlement_loan;
    }

    [DataMap("company.shares"), Source("SAVE")]
    public sealed class SharesDto
    {
        public int bundle_count;
        public float bundle_size;
        public List<int?> bundle_owners;
        public int owned_by_competitors;
    }

    [DataMap("company.cashflow_label"), Source("SAVE")]
    public sealed class CompanyStatsDto
    {
        [Nullable] public string cashflow;
        public List<ProductAmountFloatDto> top_production;
        public List<ProductAmountFloatDto> top_sales;
        public List<string> owned_permits;
        [Nullable] public string main_tech_tree;
    }

    [DataMap("company.cashflow_label"), Source("SAVE")]
    public sealed class ProductAmountFloatDto
    {
        public string product;
        public double amount;
    }

    [DataMap("company.value"), Source("DERIVED"), Doc("Inputs for the company value replica D-VAL-1, per region whose full permit the company owns.")]
    public sealed class RegionValueInputDto
    {
        public string region_id;
        public int permit_cost;
        public double paid_to_build_in_region;
    }

    [DataMap("company.ai_state"), Source("SAVE")]
    public sealed class AiStateDto
    {
        [Nullable] public string personality;
        public List<string> owned_regions;
        public bool has_initiative;
    }

    [DataMap("market.contracts"), Source("SAVE")]
    public sealed class ContractsAgentDto
    {
        public int max_contracts;
        public bool can_accept;
        public List<ContractDto> active;
    }

    [DataMap("market.contracts"), Source("SAVE")]
    public sealed class ContractDto
    {
        [Nullable] public string product;
        public int amount;
        public int delivered;
        public int reserved;
        [Nullable] public int? issuer_actor_id;
        [Nullable] public string issuer_name;
        [Nullable] public string target_building;
        [Nullable] public int? accepted_actor_id;
        public bool active;
        public bool completed;
        [Nullable] public float? price;
        [Nullable] public double? reward;
        [Nullable] public double? penalty;
        [Nullable] public int? remaining_days;
        [Nullable] public int? duration_days;
        [Nullable] public bool? fulfilled;
        [Nullable] public bool? failed;
    }

    // ---------------------------------------------------------------- buildings

    [DataMap("building.id"), Source("SAVE"), Doc("Full building detail (player buildings, and AI buildings when buildings_ai_detail is enabled).")]
    public sealed class BuildingDto
    {
        [Doc("<prefab>@<x>,<y>[#suffix]")] public string key;
        [Nullable] public string save_guid;
        [DataMap("building.type")] public string prefab;
        [DataMap("building.name")] public string display_name;
        [DataMap("building.owner")] public int owner_actor_id;
        [DataMap("building.position")] public int x;
        [DataMap("building.position")] public int y;
        [DataMap("building.position")] public int rotation;
        [DataMap("building.region"), Nullable] public string region_id;
        [DataMap("building.region"), Nullable] public int? city_id;
        [DataMap("building.type")] public List<string> tags;
        [DataMap("building.type"), EnumValues("factory", "gatherer", "farm", "harvester", "field", "warehouse", "depot", "shop", "hq", "other")] public string kind;
        [DataMap("building.active")] public BuildingFlagsDto flags;
        [DataMap("building.cost")] public double paid_to_build;
        [DataMap("building.efficiency"), Nullable] public EfficiencyDto efficiency;
        [DataMap("building.upkeep"), Nullable] public UpkeepDto upkeep;
        [DataMap("production.recipe"), Nullable] public string recipe;
        [DataMap("production.cycle"), Nullable, Doc("GetFinalProductionTime(), game days")] public float? cycle_days_effective;
        [DataMap("production.counters"), Nullable] public ProductionDto production;
        [DataMap("gatherer.hub"), Nullable] public ModulesDto modules;
        [DataMap("production.inventory")] public List<InventoryDto> inventory;
        [DataMap("building.config_logistics"), Nullable] public LogisticsConfigDto logistics;
        [DataMap("building.blocking")] public List<string> notifications;
        [DataMap("building.pollution"), Nullable] public float? pollution_at_tile;
        [DataMap("building.pollution"), Nullable] public bool? is_polluted;
        [DataMap("logistics.fleet"), Nullable] public FleetDto fleet;
        public bool is_module;
        [Nullable] public string module_owner;
        [DataMap("shop.type"), Nullable] public bool? is_shop;
    }

    [DataMap("building.active"), Source("SAVE")]
    public sealed class BuildingFlagsDto
    {
        public bool user_enabled;
        public bool requirements_met;
        public bool is_working;
    }

    [DataMap("building.efficiency"), Source("SAVE")]
    public sealed class EfficiencyDto
    {
        [EnumValues("building", "settlement")] public string kind;
        public int index;
        public float output_multiplier;
        public float upkeep_multiplier;
    }

    [DataMap("building.upkeep"), Source("SAVE")]
    public sealed class UpkeepDto
    {
        public float monthly_full;
        public float monthly_active;
        [Nullable] public float? accrued_this_month;
        [Nullable] public int? days_up;
    }

    [DataMap("production.counters"), Source("SAVE")]
    public sealed class ProductionDto
    {
        [DataMap("production.progress"), Nullable] public float? progress;
        public int produced_this_month;
        public int produced_last_month;
        public long total_produced;
        [Nullable] public float? average_10_months;
        [DataMap("building.production_efficiency")] public long production_frames;
        [DataMap("building.production_efficiency")] public long frames_spent_producing;
        [DataMap("building.production_efficiency"), Nullable] public float? final_speed;
    }

    [DataMap("gatherer.hub"), Source("SAVE")]
    public sealed class ModulesDto
    {
        public int count;
        public int max;
        [Nullable] public string module_prefab;
        [Nullable] public bool? delivered_to_hub;
        public List<ModuleDto2> items;
    }

    [DataMap("gatherer.rate"), Source("SAVE")]
    public sealed class ModuleDto2
    {
        public string key;
        public string prefab;
        public float progress;
        [Nullable] public float? efficiency;
        [Nullable] public float? min_guaranteed_speed;
        [Nullable] public int? max_resources;
        [Nullable] public float? speed_replica;
        [DataMap("gatherer.deposits"), Nullable] public string resource;
        [DataMap("gatherer.deposits"), Nullable] public int? nodes;
        [DataMap("gatherer.deposits"), Nullable] public int? nodes_depleted;
        [DataMap("gatherer.deposits"), Nullable] public long? deposit_remaining;
        public bool user_enabled;
        public bool requirements_met;
        public bool is_working;
    }

    [DataMap("production.inventory"), Source("SAVE")]
    public sealed class InventoryDto
    {
        public string product;
        [EnumValues("input", "output", "accepted", "other")] public string role;
        [Doc("stored minus pull reservations (IProductStorage.Count)")] public int count;
        [DataMap("production.capacity")] public int slots;
        [Nullable] public int? stored;
        [Nullable] public int? incoming_reserved;
        [Nullable] public int? outgoing_reserved;
        [DataMap("production.inbound_cap"), Nullable, Doc("Max Send stored on this building for this product; 0 = unlimited")] public int? inbound_cap;
    }

    [DataMap("building.config_logistics"), Source("SAVE")]
    public sealed class LogisticsConfigDto
    {
        public int options;
        [DataMap("logistics.auto_warehouse")] public bool auto_wh;
        [DataMap("logistics.auto_warehouse"), Nullable] public string warehouse;
        public List<string> accepted;
        public List<string> outgoing;
        [Nullable] public int? manual_slot_count;
    }

    [DataMap("logistics.fleet"), Source("RUNTIME")]
    public sealed class FleetDto
    {
        [Nullable] public string vehicle_prefab;
        public int active;
        public int inactive;
        public int max;
        public bool infinite;
    }

    [DataMap("building.enumerate"), Source("SAVE"), Doc("Compact AI building row.")]
    public sealed class BuildingCompactDto
    {
        public string key;
        public string prefab;
        public string display_name;
        public int owner_actor_id;
        public int x;
        public int y;
        [Nullable] public string region_id;
        [Nullable] public int? city_id;
        public string kind;
        public List<string> tags;
        public BuildingFlagsDto flags;
        [Nullable] public string recipe;
        [Nullable] public int? produced_last_month;
        [Nullable] public float? cycle_days_effective;
        [Nullable] public int? module_count;
        public bool is_module;
    }

    // ---------------------------------------------------------------- logistics

    [DataMap("logistics.slots"), Source("SAVE"), Doc("One configured manual destination slot (PRD 12.3).")]
    public sealed class RouteDto
    {
        [Doc("origin|product|destination|source|n (route id without the route: prefix)")] public string route_key;
        [DataMap("logistics.source_building")] public string origin;
        [DataMap("logistics.destination")] public string destination;
        [DataMap("logistics.destination"), Nullable] public string endpoint;
        [DataMap("logistics.destination"), EnumValues("shop", "warehouse", "factory", "gatherer", "farm", "state_trading", "wholesaler", "contract_target", "other")] public string destination_kind;
        [DataMap("logistics.destination")] public int destination_owner_actor_id;
        [DataMap("logistics.destination"), Nullable] public int? destination_city_id;
        [DataMap("logistics.product")] public string product;
        [DataMap("logistics.transport_mode")] public string source;
        [DataMap("logistics.transport_mode"), Nullable] public string transport_mode;
        public int slot_index;
        public int occurrence;
        [DataMap("logistics.slot_flags")] public bool paused;
        [DataMap("logistics.slot_flags")] public bool wait_for_full_vehicle;
        [DataMap("logistics.auto_warehouse")] public bool dormant_auto_warehouse;
        [DataMap("logistics.max_send")] public MaxSendDto max_send;
        [DataMap("logistics.min_keep")] public MinKeepDto min_keep;
        [DataMap("logistics.distance"), Nullable] public int? distance_tiles;
        [DataMap("logistics.distance"), EnumValues("cached", "unavailable")] public string path_status;
        [DataMap("logistics.dispatch_cost"), Nullable] public float? dispatch_cost;
        [DataMap("logistics.dispatch_cost"), Nullable] public string dispatch_formula;
        [DataMap("logistics.fleet"), Nullable] public int? vehicle_capacity;
        [DataMap("logistics.per_trip_amount")] public DispatchAmountDto dispatch_amount_now;
        [DataMap("logistics.in_flight")] public InFlightDto in_flight;
        [DataMap("production.inventory"), Nullable] public int? destination_stock;
        [DataMap("production.inventory"), Nullable] public int? destination_incoming_reserved;
        [DataMap("production.inventory"), Nullable] public int? destination_free_space;
        [DataMap("production.capacity"), Nullable] public int? destination_slots;
        [DataMap("production.inventory"), Nullable] public int? origin_stock;
        [DataMap("logistics.slot_flags"), Nullable] public string validation_error;
        [DataMap("logistics.slot_flags")] public bool has_error;
        [DataMap("logistics.slot_flags")] public bool destination_accepts_product;
        [DataMap("city.dead")] public bool destination_dead_city;
        public List<string> errors;
    }

    [DataMap("logistics.max_send"), Source("SAVE")]
    public sealed class MaxSendDto
    {
        public int value;
        public bool unlimited;
        [EnumValues("manual", "auto_shop_demand")] public string mode;
        [EnumValues("destination_product_shared")] public string scope;
        [Nullable, Doc("R: max(value - (stock + incoming_reserved), 0); null when unlimited")] public int? headroom_now;
        public bool ui_label_validated;
        [Nullable, EnumValues("product_specific", "single_product", "infinite", "module_shared", "unknown")] public string storage_kind;
    }

    [DataMap("logistics.min_keep"), Source("SAVE")]
    public sealed class MinKeepDto
    {
        public int value;
        public bool keep_all;
        public bool ui_label_validated;
    }

    [DataMap("logistics.per_trip_amount"), Source("DERIVED")]
    public sealed class DispatchAmountDto
    {
        [Nullable] public int? value;
        public bool complete;
        public List<string> limited_by;
        public DispatchInputsDto inputs;
        public string method;
    }

    [DataMap("logistics.per_trip_amount"), Source("DERIVED")]
    public sealed class DispatchInputsDto
    {
        [Nullable] public int? vehicle_capacity;
        [Nullable] public int? origin_stock;
        public int min_keep;
        [Nullable] public int? available;
        [Nullable] public int? free_space;
        [Nullable] public int? max_send;
        [Nullable] public int? destination_slots;
        [Nullable] public int? max_send_room;
        [Nullable] public int? contract_room;
        public bool world_event_targets_destination;
        public bool wait_for_full_vehicle;
    }

    [DataMap("logistics.in_flight"), Source("SAVE")]
    public sealed class InFlightDto
    {
        public int requests_total;
        public int requests_new;
        public int requests_started;
        public int requests_other;
        public int units_requested;
        public int units_started;
        public int invalid_handles;
    }

    [DataMap("logistics.logistic_requests"), Source("SAVE")]
    public sealed class WarehouseRequestDto
    {
        public string request_key;
        public string endpoint;
        [Nullable] public string product;
        public int occurrence;
        [Nullable] public int? requested_amount;
        public bool fill;
        public int remaining;
        public int amount_being_moved;
        public int priority;
        public bool active;
        public bool use_full_vehicles;
        public bool fulfilled;
        public List<string> allowed_graphs;
        public double expenses_this_month;
        public double expenses_last_month;
        public bool endpoint_pull_disabled;
    }

    [DataMap("logistics.routes"), Source("RUNTIME")]
    public sealed class RoutePathDto
    {
        public string route_key;
        public int original_points;
        [Doc("[[x,y],...] decimated to turning points, max 256")] public List<int[]> points;
    }

    // ---------------------------------------------------------------- vehicles

    [DataMap("logistics.vehicles"), Source("RUNTIME")]
    public sealed class VehiclesDto
    {
        public List<VehicleGroupDto> groups;
        public List<FleetRowDto> fleets_player;
        public List<VehicleRowDto> vehicles_player;
        public int total_active;
        [Doc("session_pooled_object: vehicle ids are only valid in this world session")] public string identity;
    }

    [DataMap("logistics.vehicles"), Source("RUNTIME")]
    public sealed class VehicleGroupDto
    {
        public int owner_actor_id;
        [Nullable] public string transport_mode;
        [Nullable] public string product;
        public int vehicles;
        public int units_in_transit;
    }

    [DataMap("logistics.fleet"), Source("RUNTIME")]
    public sealed class FleetRowDto
    {
        public string building;
        [Nullable] public string vehicle_prefab;
        [Nullable] public string transport_mode;
        public int active;
        public int inactive;
        public int max;
        public bool infinite;
    }

    [DataMap("logistics.vehicles"), Source("RUNTIME")]
    public sealed class VehicleRowDto
    {
        [Doc("GetInstanceID of the pooled vehicle object (world-session scoped)")] public int instance_id;
        [Doc("Vehicle.id: reassigned per trip, wraps at 65535; not an identity")] public int trip_counter_id;
        [Nullable] public string prefab;
        [Nullable] public string transport_mode;
        [Nullable] public string fleet_building;
        [Nullable] public string product;
        public int amount;
        [Nullable] public string job_origin;
        [Nullable] public string job_destination;
        [Nullable] public string job_product;
        [Nullable] public int? job_amount;
        public bool going_home;
        public float position_x;
        public float position_z;
        public int tile_x;
        public int tile_y;
    }

    // ---------------------------------------------------------------- shops / cities / regions

    [DataMap("shop.type"), Source("SAVE")]
    public sealed class ShopDto
    {
        public string building;
        public string display_name;
        public string prefab;
        [Nullable] public int? city_id;
        public int owner_actor_id;
        public bool is_dead;
        [Nullable] public int? days_to_next_price_update;
        public List<ShopProductDto> products;
    }

    [DataMap("shop.accepted"), Source("SAVE")]
    public sealed class ShopProductDto
    {
        public string product;
        [DataMap("shop.inventory")] public int stock;
        [DataMap("shop.inventory"), Nullable] public int? slots;
        [DataMap("shop.inventory"), Nullable] public int? player_delivered_stock;
        [DataMap("shop.demand"), Source("RUNTIME")] public int demand_raw;
        [DataMap("shop.demand"), Source("RUNTIME")] public int demand_for_player;
        [DataMap("shop.price"), Nullable] public float? price_for_player;
        [DataMap("shop.price_multiplier"), Nullable] public float? shop_modifier;
        [DataMap("shop.price_multiplier"), Nullable] public float? market_modifier;
        [DataMap("shop.price_multiplier"), Nullable] public float? price_modifier_pct;
        [DataMap("shop.history"), Nullable] public int? sold_last_30d;
    }

    [DataMap("city.identity"), Source("SAVE")]
    public sealed class CityDto
    {
        public int city_id;
        public string name;
        [Nullable] public string region_id;
        [DataMap("city.type_tier"), Nullable] public string type;
        [DataMap("city.type_tier"), Nullable] public string tier;
        [DataMap("city.type_tier"), Nullable] public int? tier_id;
        [DataMap("city.type_tier"), Nullable] public string next_tier;
        [DataMap("city.population")] public int population;
        [DataMap("city.population"), Nullable] public int? population_limit;
        [DataMap("city.population"), Nullable] public bool? population_limit_reached;
        [DataMap("city.growth_state"), Nullable] public CityGrowthDto growth;
        [DataMap("city.dead")] public bool dead;
        [DataMap("city.development"), Nullable] public AdvancementDto advancement;
        [DataMap("city.contract"), Nullable] public ContractDto contract_offer;
        [DataMap("shop.demand_interval"), Nullable] public int? consumption_interval_days;
        public int house_count;
        public List<string> shops;
        [Nullable] public int? center_x;
        [Nullable] public int? center_y;
    }

    [DataMap("city.growth_state"), Source("RUNTIME")]
    public sealed class CityGrowthDto
    {
        [Nullable] public bool? waiting_for_sponsor;
        [Nullable] public bool? prospering;
        [Nullable] public bool? growing;
        [Nullable] public int? consumed_products;
        [Nullable] public int? growth_threshold;
        [Nullable] public int? prosperity_threshold;
        [Nullable, EnumValues("WaitingForSponsor", "Bloated", "Prospering", "Growing", "Stagnating")] public string state;
    }

    [DataMap("city.development"), Source("SAVE")]
    public sealed class AdvancementDto
    {
        public bool can_advance;
        public bool can_accept;
        public bool is_advancing;
        public List<AdvancementContractDto> contracts;
    }

    [DataMap("city.development"), Source("SAVE")]
    public sealed class AdvancementContractDto
    {
        [Nullable] public string product;
        public int amount;
        public int delivered;
        public double payout;
    }

    [DataMap("region.identity"), Source("SAVE")]
    public sealed class RegionDto
    {
        public string region_id;
        public string name;
        public int center_x;
        public int center_y;
        public int tile_count;
        [Nullable] public int? city_id;
        [DataMap("region.resources")] public List<RegionResourceDto> resources;
        [DataMap("region.resources")] public List<ResourceSiteDto> resource_sites;
        [DataMap("region.permits"), Nullable] public PermitDto permit;
        [DataMap("region.permit_cost"), Source("DERIVED"), Nullable] public int? permit_cost;
        [DataMap("region.cooldowns")] public int permit_auction_cooldown;
        [DataMap("region.cooldowns")] public int permit_purchase_cooldown;
    }

    [DataMap("region.resources"), Source("SAVE")]
    public sealed class RegionResourceDto
    {
        public string product;
        [Nullable] public int? tiles;
        public bool water_unlimited;
    }

    [DataMap("region.resources"), Source("SAVE")]
    public sealed class ResourceSiteDto
    {
        [Nullable] public string product;
        public int center_x;
        public int center_y;
        public float radius;
        public long amount;
        public int nodes;
    }

    [DataMap("region.permits"), Source("SAVE")]
    public sealed class PermitDto
    {
        [Nullable] public int? owner_actor_id;
        public double amount_paid;
        [Nullable] public string type;
    }

    // ---------------------------------------------------------------- market

    [DataMap("market.global_prices"), Source("RUNTIME")]
    public sealed class MarketDto
    {
        public List<MarketPriceDto> prices;
        [DataMap("market.state_sells")] public StateMarketDto state;
        [DataMap("market.contracts")] public List<ContractDto> city_contract_offers;
        [DataMap("market.auctions"), Nullable] public AuctionsDto auctions;
    }

    [DataMap("market.global_prices"), Source("RUNTIME")]
    public sealed class MarketPriceDto
    {
        public string product;
        public float value;
        public float price;
        public float modifier;
        [EnumValues("STABLE", "GOING_UP", "GOING_DOWN", "OTHER")] public string trend;
        [Nullable] public float? final_price_for_player;
    }

    [DataMap("market.state_sells"), Source("RUNTIME")]
    public sealed class StateMarketDto
    {
        public List<StateSoldDto> sold;
        [DataMap("market.state_buys")] public bool incoming_trade_allowed;
        [Nullable] public float? sale_markup;
        public int trading_handlers;
    }

    [DataMap("market.state_sells"), Source("RUNTIME")]
    public sealed class StateSoldDto
    {
        public string product;
        [Nullable] public float? price_for_player;
    }

    [DataMap("market.auctions"), Source("SAVE")]
    public sealed class AuctionsDto
    {
        [Nullable] public AuctionDto current;
        public List<AuctionDto> queue;
    }

    [DataMap("market.auctions"), Source("SAVE")]
    public sealed class AuctionDto
    {
        [Nullable] public string definition;
        [Nullable] public string title;
        [Nullable] public string reward_kind;
        public int remaining_days;
        public int duration_days;
        public double start_bid;
        [Nullable] public double? highest_bid;
        [Nullable] public int? highest_bidder_actor_id;
        [Nullable] public double? next_bid;
        public int bid_count;
        [Nullable] public string region_id;
        [Nullable] public string contract_product;
    }

    // ---------------------------------------------------------------- research

    [DataMap("tech.research_state"), Source("SAVE")]
    public sealed class ResearchDto
    {
        public PlayerResearchDto player;
        public List<AiResearchDto> ai;
    }

    [DataMap("tech.research_state"), Source("SAVE")]
    public sealed class PlayerResearchDto
    {
        [Nullable] public string active;
        public List<string> queue;
        public float active_progress;
        public List<UnlockProgressDto> progress;
        public float remaining_days;
        public float remaining_cost;
        public float current_cost;
        public int efficiency_index;
        public float efficiency;
        public int unlock_points;
        [DataMap("tech.unlocked")] public List<string> unlocked;
        [DataMap("tech.costs")] public List<UnlockCostDto> costs;

        [Nullable, DataMap("building.cost"), Doc("Current build price per building type from the player's tech tree (the value TechTreeAgent.GetBuildingCost returns, before regional modifiers); null when the price table could not be read")]
        public List<BuildingCostDto> building_costs;
    }

    [DataMap("building.cost"), Source("RUNTIME")]
    public sealed class BuildingCostDto
    {
        [Doc("Building type asset name")] public string building_type;
        public double cost;
    }

    [DataMap("tech.research_state"), Source("SAVE")]
    public sealed class UnlockProgressDto
    {
        public string unlock;
        public float progress;
    }

    [DataMap("tech.costs"), Source("RUNTIME")]
    public sealed class UnlockCostDto
    {
        public string unlock;
        public float daily_cost;
        public float days;
        [Nullable] public float? total_cost_at_efficiency_1;
    }

    [DataMap("tech.competitors"), Source("SAVE")]
    public sealed class AiResearchDto
    {
        public int actor_id;
        public int unlocked_count;
        public List<string> unlocked;
    }
}
