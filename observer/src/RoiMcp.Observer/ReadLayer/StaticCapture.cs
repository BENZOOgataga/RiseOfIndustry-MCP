using System;
using System.Collections.Generic;
using ProjectAutomata;
using RoiMcp.Observer.Core;
using RoiMcp.Observer.Dto;

namespace RoiMcp.Observer.ReadLayer
{
    /// <summary>
    /// static.json (PRD 12.1): definitions read from the live GameData registry, which reflects the current
    /// game module and content mods. Only GetAssetsRO for a fixed list of known types is used (PRD 9.3).
    /// </summary>
    internal static class StaticCapture
    {
        private static Localization _loc;

        public static List<ISection> Sections(WorldContext w)
        {
            return new List<ISection>
            {
                new Section("header", false, ctx => Header(ctx, w)),
                new Section("products", false, ctx => Products(ctx, w)),
                new Section("recipes", false, ctx => Recipes(ctx, w)),
                new Section("building_types", false, ctx => BuildingTypes(ctx, w)),
                new Section("technology", false, ctx => Technology(ctx, w)),
                new Section("economy_definitions", false, ctx => Economy(ctx, w)),
            };
        }

        private static StaticData Data(SectionContext ctx)
        {
            return (StaticData)ctx.Data;
        }

        private static List<T> Assets<T>(Type t) where T : UnityEngine.Object
        {
            var ro = GameData.instance.GetAssetsRO(t);
            var list = new List<T>(ro.count);
            for (int i = 0; i < ro.count; i++)
            {
                var a = ro[i] as T;
                if (a != null) list.Add(a);
            }
            return list;
        }

        private static string En(UnityEngine.Object asset, string name, string field)
        {
            return _loc != null ? _loc.English(asset, name, field) : null;
        }

        private static IEnumerator<bool> Header(SectionContext ctx, WorldContext w)
        {
            var d = Data(ctx);
            var repo = PersistentManagerBehaviour<GameModuleRepository>.instance;
            var module = repo != null ? repo.currentModule : null;
            d.module = new ModuleDto { id = module != null ? module.id : null, name = module != null ? module.displayName : null };
            d.language = Localization.LanguageCode();
            d.mods = new List<ModDto>();
            StateCapture.ModList(d.mods);
            var loc = new Localization();
            var build = Localization.BuildEnglish(loc, ctx);
            while (build.MoveNext()) yield return true;
            _loc = loc;
            d.english_names_available = _loc.Available;
            if (!_loc.Available) ctx.Warn("english_name_unavailable", "no en-US LanguageData entries found");
            ctx.Items = 1;
        }

        private static IEnumerator<bool> Products(SectionContext ctx, WorldContext w)
        {
            var products = Assets<ProductDefinition>(typeof(ProductDefinition));
            var result = new List<ProductDefDto>(products.Count);
            for (int i = 0; i < products.Count; i++)
            {
                var p = products[i];
                string name = w.Name(p);
                var cat = p.category;
                var row = new ProductDefDto
                {
                    name = name,
                    display_name = p.productName,
                    english_name = En(p, name, "productName"),
                    category = cat != null ? w.Name(cat) : null,
                    category_group = cat != null ? cat.categoryGroupName : null,
                    tags = new List<string>(),
                    price_formula = p.price != null ? w.Name(p.price) : null,
                    demand_modifier = p.demandModifier,
                    end_game = p.endGameProduct,
                    disable_contracts = p.disableContracts,
                };
                if (p.tags != null)
                    for (int t = 0; t < p.tags.Length; t++)
                        if (p.tags[t] != null) row.tags.Add(w.Name(p.tags[t]));
                result.Add(row);
                ctx.Items++;
                if (ctx.ShouldYield) yield return true;
            }
            var cats = Assets<ProductCategory>(typeof(ProductCategory));
            var catRows = new List<CategoryDefDto>(cats.Count);
            for (int i = 0; i < cats.Count; i++)
            {
                var c = cats[i];
                string name = w.Name(c);
                catRows.Add(new CategoryDefDto
                {
                    name = name,
                    display_name = c.categoryName,
                    english_name = En(c, name, "categoryName"),
                    parent = c.parentCategory != null ? w.Name(c.parentCategory) : null,
                    price_multiplier = c.priceMultiplier,
                    growth_multiplier = c.growthMultiplier,
                });
            }
            Data(ctx).product_categories = catRows;
            Data(ctx).products = result;
        }

        private static List<ProductAmountDto> Amounts(ProductList list, WorldContext w)
        {
            var r = new List<ProductAmountDto>();
            if (list == null || list.entries == null) return r;
            for (int i = 0; i < list.entries.Count; i++)
            {
                var e = list.entries[i];
                if (e == null) continue;
                var def = e.definition;
                r.Add(new ProductAmountDto { product = def != null ? w.Name(def) : null, amount = e.amount });
            }
            return r;
        }

        private static IEnumerator<bool> Recipes(SectionContext ctx, WorldContext w)
        {
            var recipes = Assets<Recipe>(typeof(Recipe));
            var db = RecipeDatabase.instance;
            var result = new List<RecipeDefDto>(recipes.Count);
            for (int i = 0; i < recipes.Count; i++)
            {
                var r = recipes[i];
                string name = w.Name(r);
                var row = new RecipeDefDto
                {
                    name = name,
                    display_name = r.Title,
                    english_name = En(r, name, "Title"),
                    ingredients = Amounts(r.ingredients, w),
                    results = Amounts(r.result, w),
                    game_days = r.gameDays,
                    game_days_for_price = r.gameDaysForPriceCalculation,
                    required_modules = new List<string>(),
                    tier = r.tier,
                    building_types = new List<string>(),
                    used_by_water_harvester = r.usedByWaterResourceHarvester,
                };
                if (r.requiredModules != null)
                    for (int m = 0; m < r.requiredModules.Length; m++)
                        if (r.requiredModules[m] != null) row.required_modules.Add(w.Name(r.requiredModules[m]));
                if (db != null)
                {
                    var origins = db.GetOriginsOfRecipe(r);
                    if (origins.notNull)
                        for (int o = 0; o < origins.count; o++)
                            if (origins[o] != null) row.building_types.Add(w.Name(origins[o]));
                }
                result.Add(row);
                ctx.Items++;
                if (ctx.ShouldYield) yield return true;
            }
            Data(ctx).recipes = result;
        }

        private static IEnumerator<bool> BuildingTypes(SectionContext ctx, WorldContext w)
        {
            var prefabs = Assets<Building>(typeof(Building));
            var result = new List<BuildingTypeDefDto>(prefabs.Count);
            for (int i = 0; i < prefabs.Count; i++)
            {
                try
                {
                    result.Add(BuildingType(prefabs[i], w));
                    ctx.Items++;
                }
                catch (Exception e)
                {
                    ctx.ItemFailed("building_types", e);
                }
                if (ctx.ShouldYield) yield return true;
            }
            Data(ctx).building_types = result;
        }

        private static BuildingTypeDefDto BuildingType(Building b, WorldContext w)
        {
            string name = w.Name(b);
            var row = new BuildingTypeDefDto
            {
                name = name,
                display_name = b.buildingName,
                english_name = En(b, name, "buildingName"),
                base_cost = b.baseCost,
                tags = new List<string>(w.Tags(b)),
                category = b.category != null ? w.Name(b.category) : null,
                recipes = new List<string>(),
                is_module = b.GetComponent<Module>() != null,
            };
            var ru = b.GetComponent<RecipeUser>();
            if (ru != null)
            {
                row.production_speed = ru.productionSpeed;
                if (ru.availableRecipes != null)
                    for (int r = 0; r < ru.availableRecipes.Length; r++)
                        if (ru.availableRecipes[r] != null) row.recipes.Add(w.Name(ru.availableRecipes[r]));
                var hub = ru as GathererHub;
                if (hub != null) row.delivered_to_hub = hub.productionIsDeliveredToHub;
            }
            var pss = b.GetComponent<ProductSpecificProductStorage>();
            if (pss != null)
            {
                row.storage_slots = pss.slots;
                row.storage_kind = "product_specific";
            }
            else if (b.GetComponent<InfiniteStorage>() != null) row.storage_kind = "infinite";
            var mo = b.GetComponent<ModuleOwner>();
            if (mo != null)
            {
                row.max_module_count = mo.maxModuleCount;
                row.module_radius = mo.radius;
                row.module_prefab = mo.modulePrefab != null ? w.Name(mo.modulePrefab) : null;
            }
            var up = b.GetComponent<Upkeep>();
            if (up != null)
            {
                row.upkeep_cost_percentage = up.buildingCostPercentage;
                row.min_upkeep = up.minUpkeep;
            }
            var eff = b.GetComponent<BuildingEfficiency>();
            if (eff != null)
            {
                row.efficiency_output = eff.basicEfficiencyModifierValues != null ? new List<float>(eff.basicEfficiencyModifierValues) : null;
                row.efficiency_upkeep = eff.upkeepModifierValues != null ? new List<float>(eff.upkeepModifierValues) : null;
                row.initial_efficiency_index = eff.initialEfficiencyIndex;
            }
            var fleet = b.GetComponent<JITVehicleFleet>();
            if (fleet != null)
            {
                row.fleet_vehicle_prefab = fleet.vehiclePrefab != null ? w.Name(fleet.vehiclePrefab) : null;
                row.fleet_max_vehicles = fleet.maximumVehicleAmount;
            }
            var mdm = b.GetComponent<ManualDestinationManager>();
            if (mdm != null)
            {
                // slotCount is not read here: on a prefab its getter dereferences the (absent) owning company.
                var handler = mdm.paymentHandler as DefaultTransportRequestPaymentHandlerBehaviour;
                if (handler != null && handler.formula != null) row.dispatch_formula = w.Name(handler.formula);
            }
            var namer = b.GetComponent<BuildingNameGenerator>();
            if (namer != null) row.name_format = namer.nameFormat;
            var shop = b.GetComponent<Shop>();
            if (shop != null)
            {
                var sd = new ShopDefDto { max_products = shop.maxProducts, demand_modifier = shop.demandModifier, sold_tags = new List<string>() };
                if (shop.soldTags != null)
                    for (int t = 0; t < shop.soldTags.Length; t++)
                        if (shop.soldTags[t] != null) sd.sold_tags.Add(w.Name(shop.soldTags[t]));
                row.shop = sd;
            }
            return row;
        }

        private static IEnumerator<bool> Technology(SectionContext ctx, WorldContext w)
        {
            // Built into a scratch object and assigned only at the end (no partial data on failure).
            var d = new StaticData();
            var trees = Assets<TechTree>(typeof(TechTree));
            d.tech_trees = new List<TechTreeDefDto>(trees.Count);
            for (int i = 0; i < trees.Count; i++)
            {
                var t = trees[i];
                string name = w.Name(t);
                d.tech_trees.Add(new TechTreeDefDto
                {
                    name = name,
                    display_name = t.techTreeName,
                    english_name = En(t, name, "techTreeName"),
                    category = t.category != null ? w.Name(t.category) : null,
                    ui_order = t.uiOrder,
                    tier_count = t.tierCount,
                });
            }
            var cats = Assets<TechTreeCategory>(typeof(TechTreeCategory));
            d.tech_categories = new List<TechCategoryDefDto>(cats.Count);
            for (int i = 0; i < cats.Count; i++)
            {
                var c = cats[i];
                string name = w.Name(c);
                var row = new TechCategoryDefDto { name = name, display_name = c.categoryName, english_name = En(c, name, "categoryName"), trees = new List<string>() };
                var ct = c.trees;
                for (int k = 0; k < ct.count; k++)
                    if (ct[k] != null) row.trees.Add(w.Name(ct[k]));
                d.tech_categories.Add(row);
            }
            var unlocks = Assets<TechTreeUnlock>(typeof(TechTreeUnlock));
            var rows = new List<TechUnlockDefDto>(unlocks.Count);
            for (int i = 0; i < unlocks.Count; i++)
            {
                var u = unlocks[i];
                string name = w.Name(u);
                var row = new TechUnlockDefDto
                {
                    name = name,
                    kind = "other",
                    display_name = u.unlockName,
                    english_name = En(u, name, "unlockName"),
                    tier = u.tier,
                    required = DefNames(u.requiredUnlocks, w),
                    included = DefNames(u.includedUnlocks, w),
                    teaser = u.isTeaser,
                    unlocked_by_default = u.unlockedByDefault,
                    placements = new List<TechPlacementDto>(),
                    research_cost_formula = u.researchCost != null ? w.Name(u.researchCost) : null,
                    research_time_formula = u.researchTime != null ? w.Name(u.researchTime) : null,
                    buildings = new List<string>(),
                    recipes = new List<string>(),
                };
                var placements = u.trees;
                if (placements != null)
                {
                    for (int k = 0; k < placements.Count; k++)
                    {
                        var pl = placements[k];
                        if (pl.tree != null) row.placements.Add(new TechPlacementDto { tree = w.Name(pl.tree), column = pl.column });
                    }
                }
                var bu = u as TechTreeBuildingUnlock;
                var ru = u as TechTreeRecipeUnlock;
                var pu = u as TechTreeBuildingPriceUnlock;
                if (bu != null)
                {
                    row.kind = "building";
                    if (bu.building != null) row.buildings.Add(w.Name(bu.building));
                    row.building_category = bu.category != null ? w.Name(bu.category) : null;
                }
                else if (ru != null)
                {
                    row.kind = "recipe";
                    if (ru.recipes != null)
                        for (int k = 0; k < ru.recipes.Length; k++)
                            if (ru.recipes[k] != null) row.recipes.Add(w.Name(ru.recipes[k]));
                }
                else if (pu != null)
                {
                    row.kind = "building_price";
                    if (pu.buildings != null)
                        for (int k = 0; k < pu.buildings.Length; k++)
                            if (pu.buildings[k] != null) row.buildings.Add(w.Name(pu.buildings[k]));
                    row.price_percentage = pu.percentage;
                }
                else if (u is TechTreeGenericUnlock) row.kind = "generic";
                rows.Add(row);
                ctx.Items++;
                if (ctx.ShouldYield) yield return true;
            }
            d.tech_unlocks = rows;

            var ttm = ManagerBehaviour<TechTreeManager>.instance;
            var config = ttm != null ? ttm.config : null;
            if (config != null)
            {
                d.tech_config = new TechConfigDto
                {
                    max_enqueued_unlocks = config.maxEnqueuedUnlocks,
                    efficiency_values = config.efficiencyValues != null ? new List<float>(config.efficiencyValues) : new List<float>(),
                    efficiency_unlocks = new List<string>(),
                };
                if (config.efficiencyUnlocks != null)
                    for (int k = 0; k < config.efficiencyUnlocks.Count; k++)
                        d.tech_config.efficiency_unlocks.Add(config.efficiencyUnlocks[k] != null ? w.Name(config.efficiencyUnlocks[k]) : null);
            }
            var target = Data(ctx);
            target.tech_trees = d.tech_trees;
            target.tech_categories = d.tech_categories;
            target.tech_unlocks = d.tech_unlocks;
            target.tech_config = d.tech_config;
        }

        private static List<string> DefNames(TechTreeUnlock[] arr, WorldContext w)
        {
            var r = new List<string>();
            if (arr == null) return r;
            for (int i = 0; i < arr.Length; i++)
                if (arr[i] != null) r.Add(w.Name(arr[i]));
            return r;
        }

        private static IEnumerator<bool> Economy(SectionContext ctx, WorldContext w)
        {
            var d = new StaticData();
            var formulas = Assets<Formula>(typeof(Formula));
            d.formulas = new List<FormulaDefDto>(formulas.Count);
            for (int i = 0; i < formulas.Count; i++)
                d.formulas.Add(new FormulaDefDto { name = w.Name(formulas[i]), text = formulas[i].formula });

            var bills = Assets<MoneyBillCategory>(typeof(MoneyBillCategory));
            d.bill_categories = new List<NamedDefDto>(bills.Count);
            for (int i = 0; i < bills.Count; i++)
            {
                string name = w.Name(bills[i]);
                d.bill_categories.Add(new NamedDefDto { name = name, display_name = bills[i].categoryName, english_name = En(bills[i], name, "categoryName") });
            }

            var overview = Assets<MoneyOverviewCategory>(typeof(MoneyOverviewCategory));
            d.overview_categories = new List<OverviewCategoryDefDto>(overview.Count);
            for (int i = 0; i < overview.Count; i++)
            {
                var o = overview[i];
                string name = w.Name(o);
                var row = new OverviewCategoryDefDto
                {
                    name = name,
                    display_name = o.displayName,
                    english_name = En(o, name, "displayName"),
                    type = o.type == MoneyOverviewCategory.Type.ONE_TIME ? "ONE_TIME" : o.type == MoneyOverviewCategory.Type.REOCCURRING ? "REOCCURRING" : "OTHER",
                    ui_order = o.uiOrder,
                    bill_categories = new List<string>(),
                };
                if (o.billCategories != null)
                    for (int k = 0; k < o.billCategories.Count; k++)
                        if (o.billCategories[k] != null) row.bill_categories.Add(w.Name(o.billCategories[k]));
                d.overview_categories.Add(row);
            }

            var tiers = Assets<SettlementTier>(typeof(SettlementTier));
            d.settlement_tiers = new List<SettlementTierDefDto>(tiers.Count);
            for (int i = 0; i < tiers.Count; i++)
            {
                var t = tiers[i];
                string name = w.Name(t);
                var threshold = t.threshold;
                d.settlement_tiers.Add(new SettlementTierDefDto
                {
                    name = name,
                    display_name = t.tierName,
                    english_name = En(t, name, "tierName"),
                    tier_id = t.tierId,
                    threshold_min = threshold != null ? threshold.min : 0,
                    threshold_max = threshold != null ? threshold.max : 0,
                    placed_shops_count = t.placedShopsCount,
                    efficiency = t.efficiency,
                    next_tier = t.nextTier != null ? w.Name(t.nextTier) : null,
                });
            }

            var types = Assets<SettlementType>(typeof(SettlementType));
            d.settlement_types = new List<NamedDefDto>(types.Count);
            for (int i = 0; i < types.Count; i++)
            {
                string name = w.Name(types[i]);
                d.settlement_types.Add(new NamedDefDto { name = name, display_name = types[i].settlementTypeName, english_name = En(types[i], name, "settlementTypeName") });
            }

            var permits = Assets<PermitType>(typeof(PermitType));
            d.permit_types = new List<PermitTypeDefDto>(permits.Count);
            for (int i = 0; i < permits.Count; i++)
            {
                var p = permits[i];
                string name = w.Name(p);
                d.permit_types.Add(new PermitTypeDefDto
                {
                    name = name,
                    display_name = p.displayName,
                    english_name = En(p, name, "displayName"),
                    cost_per_tile = p.costPerTile,
                    cost_modifier = p.costModifier,
                    parent = p.parent != null ? w.Name(p.parent) : null,
                    top_level = p.topLevelParent != null ? w.Name(p.topLevelParent) : null,
                });
            }

            var loans = Assets<LoanInfo>(typeof(LoanInfo));
            d.loan_infos = new List<LoanInfoDefDto>(loans.Count);
            for (int i = 0; i < loans.Count; i++)
            {
                var l = loans[i];
                var loanType = l.type;
                d.loan_infos.Add(new LoanInfoDefDto
                {
                    name = w.Name(l),
                    type = loanType.ToString(),
                    title = l.title,
                    amount = l.amount,
                    apr = l.apr,
                    duration_months = l.duration,
                    grace_months = l.firstPaymentAfter,
                });
            }
            ctx.Items = formulas.Count + bills.Count + overview.Count + tiers.Count + types.Count + permits.Count + loans.Count;
            _loc = null;
            var target = Data(ctx);
            target.formulas = d.formulas;
            target.bill_categories = d.bill_categories;
            target.overview_categories = d.overview_categories;
            target.settlement_tiers = d.settlement_tiers;
            target.settlement_types = d.settlement_types;
            target.permit_types = d.permit_types;
            target.loan_infos = d.loan_infos;
            yield break;
        }
    }
}
