using System.Collections.Generic;

#pragma warning disable IDE1006

namespace RoiMcp.Observer.Dto
{
    [DataMap("static.registry"), Source("STATIC"), Doc("static.json data: definitions read from the live GameData registry (PRD 12.1).")]
    public sealed class StaticData
    {
        // Every field below is produced by one static section (header, products, recipes, building_types,
        // technology, economy_definitions) and is null when that section failed; see "sections" in the envelope.
        [DataMap("game.module"), Nullable] public ModuleDto module;
        [DataMap("game.language"), Nullable] public string language;
        [Doc("true when en-US names were resolved through LanguageData")] public bool english_names_available;
        [Nullable] public List<ProductDefDto> products;
        [Nullable] public List<CategoryDefDto> product_categories;
        [Nullable] public List<RecipeDefDto> recipes;
        [Nullable] public List<BuildingTypeDefDto> building_types;
        [Nullable] public List<TechTreeDefDto> tech_trees;
        [Nullable] public List<TechCategoryDefDto> tech_categories;
        [Nullable] public List<TechUnlockDefDto> tech_unlocks;
        [DataMap("tech.manager"), Nullable] public TechConfigDto tech_config;
        [Nullable] public List<FormulaDefDto> formulas;
        [DataMap("company.bill_categories"), Nullable] public List<NamedDefDto> bill_categories;
        [DataMap("company.bill_categories"), Nullable] public List<OverviewCategoryDefDto> overview_categories;
        [DataMap("city.type_tier"), Nullable] public List<SettlementTierDefDto> settlement_tiers;
        [DataMap("city.type_tier"), Nullable] public List<NamedDefDto> settlement_types;
        [DataMap("region.permits"), Nullable] public List<PermitTypeDefDto> permit_types;
        [DataMap("company.loans"), Nullable] public List<LoanInfoDefDto> loan_infos;
        [DataMap("game.mods"), Nullable] public List<ModDto> mods;
    }

    [DataMap("game.module"), Source("RUNTIME")]
    public sealed class ModuleDto
    {
        [Nullable] public string id;
        [Nullable] public string name;
    }

    [DataMap("game.mods"), Source("RUNTIME")]
    public sealed class ModDto
    {
        public string name;
        public int version;
        [Nullable] public string version_string;
    }

    [DataMap("static.other_definitions"), Source("STATIC")]
    public sealed class NamedDefDto
    {
        public string name;
        [Nullable] public string display_name;
        [Nullable] public string english_name;
    }

    [DataMap("static.products"), Source("STATIC")]
    public sealed class ProductDefDto
    {
        [Doc("asset name (stable id)")] public string name;
        [Nullable, Doc("localized display name as shown in game")] public string display_name;
        [Nullable] public string english_name;
        [Nullable] public string category;
        [Nullable] public string category_group;
        public List<string> tags;
        [Nullable] public string price_formula;
        public float demand_modifier;
        public bool end_game;
        public bool disable_contracts;
    }

    [DataMap("static.product_categories"), Source("STATIC")]
    public sealed class CategoryDefDto
    {
        public string name;
        [Nullable] public string display_name;
        [Nullable] public string english_name;
        [Nullable] public string parent;
        [Nullable] public float? price_multiplier;
        [Nullable] public float? growth_multiplier;
    }

    [DataMap("production.io"), Source("STATIC")]
    public sealed class ProductAmountDto
    {
        public string product;
        public int amount;
    }

    [DataMap("static.recipes"), Source("STATIC")]
    public sealed class RecipeDefDto
    {
        public string name;
        [Nullable] public string display_name;
        [Nullable] public string english_name;
        public List<ProductAmountDto> ingredients;
        public List<ProductAmountDto> results;
        [DataMap("production.cycle"), Doc("effective game days (respects easy chains)")] public float game_days;
        [DataMap("production.cycle")] public float game_days_for_price;
        [Doc("module building prefabs required (harvester/field)")] public List<string> required_modules;
        public int tier;
        [DataMap("static.compatible_buildings")] public List<string> building_types;
        public bool used_by_water_harvester;
    }

    [DataMap("static.building_types"), Source("STATIC")]
    public sealed class BuildingTypeDefDto
    {
        public string name;
        [Nullable] public string display_name;
        [Nullable] public string english_name;
        public float base_cost;
        public List<string> tags;
        [Nullable] public string category;
        public List<string> recipes;
        [Nullable] public float? production_speed;
        [Nullable] public int? storage_slots;
        [Nullable] public string storage_kind;
        [Nullable] public int? max_module_count;
        [Nullable] public float? module_radius;
        [Nullable] public string module_prefab;
        [Nullable] public bool? delivered_to_hub;
        [Nullable] public float? upkeep_cost_percentage;
        [Nullable] public float? min_upkeep;
        [Nullable] public List<float> efficiency_output;
        [Nullable] public List<float> efficiency_upkeep;
        [Nullable] public int? initial_efficiency_index;
        [Nullable] public string fleet_vehicle_prefab;
        [Nullable] public int? fleet_max_vehicles;
        [Nullable, Doc("Always null in this version: the game's slot-count getter needs the owning company, which a prefab does not have. Live counts are in state buildings (manual_slot_count)")]
        public int? manual_destination_slots;

        [Nullable, Doc("Always null in this version (depends on the owning company at runtime)")]
        public bool? manual_destination_infinite;
        [Nullable] public string name_format;
        [Nullable] public ShopDefDto shop;
        public bool is_module;
        [Nullable] public string dispatch_formula;
    }

    [DataMap("shop.type"), Source("STATIC")]
    public sealed class ShopDefDto
    {
        public int max_products;
        public List<string> sold_tags;
        public float demand_modifier;
    }

    [DataMap("tech.tree"), Source("STATIC")]
    public sealed class TechTreeDefDto
    {
        public string name;
        [Nullable] public string display_name;
        [Nullable] public string english_name;
        [Nullable] public string category;
        public int ui_order;
        public int tier_count;
    }

    [DataMap("tech.tree"), Source("STATIC")]
    public sealed class TechCategoryDefDto
    {
        public string name;
        [Nullable] public string display_name;
        [Nullable] public string english_name;
        public List<string> trees;
    }

    [DataMap("tech.tree"), Source("STATIC")]
    public sealed class TechPlacementDto
    {
        public string tree;
        public int column;
    }

    [DataMap("tech.unlock_effects"), Source("STATIC")]
    public sealed class TechUnlockDefDto
    {
        public string name;
        [EnumValues("building", "recipe", "building_price", "generic", "other")] public string kind;
        [Nullable] public string display_name;
        [Nullable] public string english_name;
        public int tier;
        public List<string> required;
        public List<string> included;
        public bool teaser;
        public bool unlocked_by_default;
        public List<TechPlacementDto> placements;
        [Nullable] public string research_cost_formula;
        [Nullable] public string research_time_formula;
        public List<string> buildings;
        [Nullable] public string building_category;
        public List<string> recipes;
        [Nullable] public float? price_percentage;
    }

    [DataMap("tech.manager"), Source("STATIC")]
    public sealed class TechConfigDto
    {
        public int max_enqueued_unlocks;
        public List<float> efficiency_values;
        [NullableItems, Doc("Research unlock required for each efficiency level, aligned with efficiency_values; null where the level needs no unlock")]
        public List<string> efficiency_unlocks;
    }

    [DataMap("static.formulas"), Source("STATIC")]
    public sealed class FormulaDefDto
    {
        public string name;
        [Nullable] public string text;
    }

    [DataMap("company.bill_categories"), Source("STATIC")]
    public sealed class OverviewCategoryDefDto
    {
        public string name;
        [Nullable] public string display_name;
        [Nullable] public string english_name;
        [EnumValues("ONE_TIME", "REOCCURRING", "OTHER")] public string type;
        public int ui_order;
        public List<string> bill_categories;
    }

    [DataMap("city.type_tier"), Source("STATIC")]
    public sealed class SettlementTierDefDto
    {
        public string name;
        [Nullable] public string display_name;
        [Nullable] public string english_name;
        public int tier_id;
        public int threshold_min;
        public int threshold_max;
        public int placed_shops_count;
        public float efficiency;
        [Nullable] public string next_tier;
    }

    [DataMap("region.permits"), Source("STATIC")]
    public sealed class PermitTypeDefDto
    {
        public string name;
        [Nullable] public string display_name;
        [Nullable] public string english_name;
        public float cost_per_tile;
        public float cost_modifier;
        [Nullable] public string parent;
        [Nullable] public string top_level;
    }

    [DataMap("company.loans"), Source("STATIC")]
    public sealed class LoanInfoDefDto
    {
        public string name;
        [Nullable] public string type;
        [Nullable] public string title;
        public float amount;
        public float apr;
        public int duration_months;
        public int grace_months;
    }
}
