using System;
using System.Collections.Generic;
using System.Reflection;
using ProjectAutomata;
using RoiMcp.Observer.Dto;

namespace RoiMcp.Observer.ReadLayer
{
    /// <summary>
    /// The single place where private game fields are read (PRD 9.4). Every lookup is an inline
    /// typeof(T).GetField("literal", flags) so the IL gate can verify each (type, field) pair against the
    /// allowlist (rule G4). Only FieldInfo.GetValue is used; values are read with TryGetValue on the
    /// returned dictionaries. Resolved once on READY entry.
    /// </summary>
    internal static class ReflectionTable
    {
        private sealed class Entry
        {
            public string Name;
            public FieldInfo Field;
            public Type Expected;
            public string[] Sections;
            public bool Ok;
        }

        private static Entry _guidObjMappings;
        private static Entry _permits;
        private static Entry _maxAcceptedMap;
        private static Entry _storage;
        private static Entry _singleMaxAccepted;
        private static Entry _shopDelivered;
        private static Entry _shopDaysToPriceUpdate;
        private static Entry _landOrigin;
        private static Entry _landDestination;
        private static Entry _landProduct;
        private static Entry _landAmount;
        private static Entry _airOrigin;
        private static Entry _airDestination;
        private static Entry _airProduct;
        private static Entry _airAmount;
        private static Entry _loanFreeMonths;
        private static Entry _pricingInfo;
        private static Entry _marketInterval;
        private static Entry _marketDaysSince;
        private static Entry _upkeepAccrued;
        private static Entry _upkeepDaysUp;
        private static Entry _harvesterResources;
        private static Entry _productionFrames;
        private static Entry _framesSpentProducing;
        private static Entry _consumedProducts;
        private static Entry _unlockStates;
        private static Entry _buildingPrices;
        private static Entry _researchProgress;
        private static Entry _actorComponentMap;

        private static List<Entry> _all = new List<Entry>();

        private static Entry Make(string name, FieldInfo f, Type expected, params string[] sections)
        {
#if DEBUG_FAULTS
            if (DebugFaults.IsActive("reflection_missing:" + name)) f = null;
#endif
            var e = new Entry { Name = name, Field = f, Expected = expected, Sections = sections };
            e.Ok = f != null && expected.IsAssignableFrom(f.FieldType);
            _all.Add(e);
            return e;
        }

        /// <summary>Resolves every entry. An empty sections list means the dependent fields degrade to null.</summary>
        public static ReflectionSelfCheckDto Resolve(Dictionary<string, List<string>> disabledSectionsByEntry)
        {
            _all = new List<Entry>();
            _guidObjMappings = Make("GuidMapper.objMappings",
                typeof(GuidMapper).GetField("objMappings", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(Dictionary<object, Guid>));
            _permits = Make("PermitManager._permits",
                typeof(PermitManager).GetField("_permits", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(Dictionary<Region, Dictionary<PermitType, Permit>>), "regions");
            _maxAcceptedMap = Make("ProductSpecificProductStorage._maxAcceptedMap",
                typeof(ProductSpecificProductStorage).GetField("_maxAcceptedMap", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(MaxAcceptedDictionary), "routes_player", "routes_ai");
            _storage = Make("ProductSpecificProductStorage._storage",
                typeof(ProductSpecificProductStorage).GetField("_storage", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(StorageSavegameDictionary), "routes_player", "routes_ai");
            _singleMaxAccepted = Make("SingleProductStorage._maxAccepted",
                typeof(SingleProductStorage).GetField("_maxAccepted", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(int), "routes_player", "routes_ai");
            _shopDelivered = Make("Shop._deliveredByActors",
                typeof(Shop).GetField("_deliveredByActors", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(Shop.DeliveredByActors), "shops");
            _shopDaysToPriceUpdate = Make("Shop._daysToNextPricesUpdate",
                typeof(Shop).GetField("_daysToNextPricesUpdate", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(int));
            _landOrigin = Make("TransportJob<LandTransportJob>._origin",
                typeof(TransportJob<LandTransportJob>).GetField("_origin", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(BuildingLogistics));
            _landDestination = Make("TransportJob<LandTransportJob>._destination",
                typeof(TransportJob<LandTransportJob>).GetField("_destination", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(BuildingLogistics));
            _landProduct = Make("TransportJob<LandTransportJob>._product",
                typeof(TransportJob<LandTransportJob>).GetField("_product", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(ProductDefinition));
            _landAmount = Make("TransportJob<LandTransportJob>._productAmount",
                typeof(TransportJob<LandTransportJob>).GetField("_productAmount", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(int));
            _airOrigin = Make("TransportJob<AirTransportJob>._origin",
                typeof(TransportJob<AirTransportJob>).GetField("_origin", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(BuildingLogistics));
            _airDestination = Make("TransportJob<AirTransportJob>._destination",
                typeof(TransportJob<AirTransportJob>).GetField("_destination", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(BuildingLogistics));
            _airProduct = Make("TransportJob<AirTransportJob>._product",
                typeof(TransportJob<AirTransportJob>).GetField("_product", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(ProductDefinition));
            _airAmount = Make("TransportJob<AirTransportJob>._productAmount",
                typeof(TransportJob<AirTransportJob>).GetField("_productAmount", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(int));
            _loanFreeMonths = Make("Loan.freeMonthsLeft",
                typeof(Loan).GetField("freeMonthsLeft", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(int));
            _pricingInfo = Make("GlobalMarket._pricingInfoByProduct",
                typeof(GlobalMarket).GetField("_pricingInfoByProduct", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(Dictionary<ProductDefinition, ProductPricingInfo>), "market");
            _marketInterval = Make("GlobalMarket._updateIntervalInDays",
                typeof(GlobalMarket).GetField("_updateIntervalInDays", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(int));
            _marketDaysSince = Make("GlobalMarket._daysSinceLastUpdate",
                typeof(GlobalMarket).GetField("_daysSinceLastUpdate", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(int));
            _upkeepAccrued = Make("Upkeep.upkeep",
                typeof(Upkeep).GetField("upkeep", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(float));
            _upkeepDaysUp = Make("Upkeep.daysUp",
                typeof(Upkeep).GetField("daysUp", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(int));
            _harvesterResources = Make("Harvester._resources",
                typeof(Harvester).GetField("_resources", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(List<ResourceNode>));
            _productionFrames = Make("RecipeUser.productionFrames",
                typeof(RecipeUser).GetField("productionFrames", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(float));
            _framesSpentProducing = Make("RecipeUser.framesSpentProducing",
                typeof(RecipeUser).GetField("framesSpentProducing", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(float));
            _consumedProducts = Make("SettlementGrowth._consumedProducts",
                typeof(SettlementGrowth).GetField("_consumedProducts", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(int));
            _unlockStates = Make("TechTreeAgent._unlockStates",
                typeof(TechTreeAgent).GetField("_unlockStates", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(Dictionary<TechTreeUnlock, bool>), "research");
            // No dependent section: when missing, research.player.building_costs is null (degrades, never disables).
            _buildingPrices = Make("TechTreeAgent._buildingPrices",
                typeof(TechTreeAgent).GetField("_buildingPrices", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(Dictionary<Building, double>));
            _researchProgress = Make("TechTreeAgentResearchState._researchProgress",
                typeof(TechTreeAgentResearchState).GetField("_researchProgress", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(TechTreeAgentResearchState.ResearchProgress));

            _actorComponentMap = Make("Actor._componentMap",
                typeof(Actor).GetField("_componentMap", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public),
                typeof(Dictionary<Type, IActorComponent>), "companies", "research");

            var dto = new ReflectionSelfCheckDto { total = _all.Count, problems = new List<ReflectionEntryDto>() };
            foreach (var e in _all)
            {
                if (e.Ok)
                {
                    dto.resolved++;
                    continue;
                }
                bool missing = e.Field == null;
                if (missing) dto.missing++;
                else dto.type_mismatch++;
                dto.problems.Add(new ReflectionEntryDto
                {
                    entry = e.Name,
                    problem = missing ? "missing" : "type_mismatch",
                    actual_type = missing ? null : e.Field.FieldType.FullName,
                    dependent_sections = new List<string>(e.Sections),
                });
                disabledSectionsByEntry[e.Name] = new List<string>(e.Sections);
            }
            return dto;
        }

        private static object Get(Entry e, object instance)
        {
            if (e == null || !e.Ok || instance == null) return null;
            return e.Field.GetValue(instance);
        }

        // ------------------------------------------------------------ typed accessors (TryGetValue only)

        public static string SaveGuid(object building)
        {
            var map = Get(_guidObjMappings, GuidMapper.instance) as Dictionary<object, Guid>;
            Guid g;
            if (map != null && map.TryGetValue(building, out g)) return g.ToString();
            return null;
        }

        public static bool GuidAvailable { get { return _guidObjMappings != null && _guidObjMappings.Ok; } }

        public static Dictionary<Region, Dictionary<PermitType, Permit>> Permits(PermitManager pm)
        {
            return Get(_permits, pm) as Dictionary<Region, Dictionary<PermitType, Permit>>;
        }

        /// <summary>Max accepted for a product on a ProductSpecificProductStorage; missing key = 0 (unlimited).</summary>
        public static int? MaxAccepted(ProductSpecificProductStorage storage, int productAssetId)
        {
            var map = Get(_maxAcceptedMap, storage) as MaxAcceptedDictionary;
            if (map == null) return null;
            int v;
            return map.dictionary.TryGetValue(productAssetId, out v) ? v : 0;
        }

        public static int? SingleMaxAccepted(SingleProductStorage storage)
        {
            var v = Get(_singleMaxAccepted, storage);
            return v is int ? (int)v : (int?)null;
        }

        /// <summary>StorageData [pull, put, store] for a product, or null when unreadable. Missing key = all zero.</summary>
        public static bool StorageCounts(ProductSpecificProductStorage storage, int productAssetId, out int pulls, out int puts, out int stored)
        {
            pulls = puts = stored = 0;
            var map = Get(_storage, storage) as StorageSavegameDictionary;
            if (map == null) return false;
            StorageData d;
            if (!map.dictionary.TryGetValue(productAssetId, out d) || d == null) return true;
            var arr = d.data;
            if (arr == null || arr.Length < 3) return true;
            pulls = arr[0];
            puts = arr[1];
            stored = arr[2];
            return true;
        }

        public static bool StorageReadable { get { return _storage != null && _storage.Ok; } }

        /// <summary>Units of a product delivered into a shop by an actor (read-only, no insert).</summary>
        public static int? ShopDelivered(Shop shop, IActor actor, ProductDefinition product)
        {
            var map = Get(_shopDelivered, shop) as Shop.DeliveredByActors;
            if (map == null) return null;
            Dictionary<ProductDefinition, int> perProduct;
            if (!map.dictionary.TryGetValue(actor, out perProduct) || perProduct == null) return 0;
            int v;
            return perProduct.TryGetValue(product, out v) ? v : 0;
        }

        public static int? ShopDaysToPriceUpdate(Shop shop)
        {
            var v = Get(_shopDaysToPriceUpdate, shop);
            return v is int ? (int)v : (int?)null;
        }

        public static bool TransportJobFields(object job, out BuildingLogistics origin, out BuildingLogistics destination,
            out ProductDefinition product, out int amount)
        {
            origin = null;
            destination = null;
            product = null;
            amount = 0;
            if (job is TransportJob<LandTransportJob>)
            {
                origin = Get(_landOrigin, job) as BuildingLogistics;
                destination = Get(_landDestination, job) as BuildingLogistics;
                product = Get(_landProduct, job) as ProductDefinition;
                var a = Get(_landAmount, job);
                amount = a is int ? (int)a : 0;
                return true;
            }
            if (job is TransportJob<AirTransportJob>)
            {
                origin = Get(_airOrigin, job) as BuildingLogistics;
                destination = Get(_airDestination, job) as BuildingLogistics;
                product = Get(_airProduct, job) as ProductDefinition;
                var a = Get(_airAmount, job);
                amount = a is int ? (int)a : 0;
                return true;
            }
            return false;
        }

        public static int? LoanFreeMonths(Loan loan)
        {
            var v = Get(_loanFreeMonths, loan);
            return v is int ? (int)v : (int?)null;
        }

        public static Dictionary<ProductDefinition, ProductPricingInfo> PricingInfo(GlobalMarket market)
        {
            return Get(_pricingInfo, market) as Dictionary<ProductDefinition, ProductPricingInfo>;
        }

        public static int? MarketInterval(GlobalMarket market)
        {
            var v = Get(_marketInterval, market);
            return v is int ? (int)v : (int?)null;
        }

        public static int? MarketDaysSinceUpdate(GlobalMarket market)
        {
            var v = Get(_marketDaysSince, market);
            return v is int ? (int)v : (int?)null;
        }

        public static float? UpkeepAccrued(Upkeep u)
        {
            var v = Get(_upkeepAccrued, u);
            return v is float ? (float)v : (float?)null;
        }

        public static int? UpkeepDaysUp(Upkeep u)
        {
            var v = Get(_upkeepDaysUp, u);
            return v is int ? (int)v : (int?)null;
        }

        public static List<ResourceNode> HarvesterResources(Harvester h)
        {
            return Get(_harvesterResources, h) as List<ResourceNode>;
        }

        public static bool ProductionFrames(RecipeUser ru, out float frames, out float spent)
        {
            var a = Get(_productionFrames, ru);
            var b = Get(_framesSpentProducing, ru);
            frames = a is float ? (float)a : 0f;
            spent = b is float ? (float)b : 0f;
            return a is float && b is float;
        }

        public static int? ConsumedProducts(SettlementGrowth g)
        {
            var v = Get(_consumedProducts, g);
            return v is int ? (int)v : (int?)null;
        }

        public static Dictionary<TechTreeUnlock, bool> UnlockStates(TechTreeAgent agent)
        {
            return Get(_unlockStates, agent) as Dictionary<TechTreeUnlock, bool>;
        }

        /// <summary>
        /// The agent's building price table: TechTreeAgent.GetBuildingCost returns this value (base cost when the
        /// building is absent). Read directly because the method logs an error for every unknown building.
        /// </summary>
        public static Dictionary<Building, double> BuildingPrices(TechTreeAgent agent)
        {
            return Get(_buildingPrices, agent) as Dictionary<Building, double>;
        }

        /// <summary>
        /// True when Actor.Get(type) is a pure cache hit for this actor. Actor.Awake pre-fills the component map
        /// with every component type and interface, so this holds in practice; the observer checks it before
        /// any call that reaches Actor.Get so that the lazy insert path (_componentMap[type] = ...) is never taken.
        /// </summary>
        public static bool ActorComponentCached(IActor actor, Type componentType)
        {
            var a = actor as Actor;
            if (a == null) return false;
            var map = Get(_actorComponentMap, a) as Dictionary<Type, IActorComponent>;
            return map != null && map.ContainsKey(componentType);
        }

        public static Dictionary<TechTreeUnlock, float> ResearchProgressMap(TechTreeAgentResearchState research)
        {
            var map = Get(_researchProgress, research) as TechTreeAgentResearchState.ResearchProgress;
            return map != null ? map.dictionary : null;
        }
    }
}
