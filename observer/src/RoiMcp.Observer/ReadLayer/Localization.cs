using System;
using System.Collections.Generic;
using ProjectAutomata;

namespace RoiMcp.Observer.ReadLayer
{
    /// <summary>
    /// English names from the en-US LanguageData assets. Key format (LocalizationUtility.CleanUpKey):
    /// "{ConcreteTypeName}.{assetName}.{field}" with spaces removed, lower-cased. The game localizes definition
    /// fields in place, so display names are in the UI language; English names come from this table only.
    /// </summary>
    internal sealed class Localization
    {
        private readonly Dictionary<string, string> _english = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

        public bool Available { get { return _english.Count > 0; } }

        public static string LanguageCode()
        {
            var i18n = I18n.instance;
            if (i18n == null) return null;
            var lang = i18n.currentLanguage;
            return lang != null ? lang.keycode : null;
        }

        /// <summary>
        /// Builds the en-US table (once per static export; lower priority first, higher overrides). Time-sliced:
        /// the table holds thousands of keys, so the iterator yields whenever the frame budget is used.
        /// </summary>
        public static IEnumerator<bool> BuildEnglish(Localization into, RoiMcp.Observer.Core.SectionContext ctx)
        {
            var assets = GameData.instance.GetAssetsRO(typeof(LanguageData));
            var english = new List<LanguageData>();
            for (int i = 0; i < assets.count; i++)
            {
                var d = assets[i] as LanguageData;
                if (d == null || d.language == null) continue;
                if (string.Equals(d.language.keycode, "en-US", StringComparison.Ordinal)) english.Add(d);
            }
            english.Sort(ComparePriority);
            for (int i = 0; i < english.Count; i++)
            {
                if (english[i] == null) continue;
                var keys = english[i].keys;
                if (keys == null) continue;
                for (int k = 0; k < keys.Length; k++)
                {
                    var entry = keys[k];
                    if (entry.key != null) into._english[entry.key] = entry.value;
                    if ((k & 127) == 127 && ctx.ShouldYield) yield return true;
                }
            }
        }

        private static int ComparePriority(LanguageData a, LanguageData b)
        {
            int pa = a.priority;
            int pb = b.priority;
            return pa.CompareTo(pb);
        }

        public static string Key(string typeName, string assetName, string field)
        {
            return (typeName + "." + assetName + "." + field).Replace(" ", string.Empty).ToLowerInvariant();
        }

        public string English(UnityEngine.Object asset, string assetName, string field)
        {
            if (asset == null || _english.Count == 0) return null;
            string v;
            return _english.TryGetValue(Key(asset.GetType().Name, assetName, field), out v) ? v : null;
        }
    }
}
