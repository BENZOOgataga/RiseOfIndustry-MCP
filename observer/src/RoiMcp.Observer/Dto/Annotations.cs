using System;

namespace RoiMcp.Observer.Dto
{
    /// <summary>Links a DTO class or field to an entry id in research/data-map.json (emitted as x-data-map-id).</summary>
    [AttributeUsage(AttributeTargets.Class | AttributeTargets.Field, AllowMultiple = false)]
    public sealed class DataMapAttribute : Attribute
    {
        public DataMapAttribute(string id) { Id = id; }
        public string Id { get; private set; }
    }

    /// <summary>Data class of a value: STATIC, SAVE, RUNTIME or DERIVED (emitted as x-source).</summary>
    [AttributeUsage(AttributeTargets.Class | AttributeTargets.Field, AllowMultiple = false)]
    public sealed class SourceAttribute : Attribute
    {
        public SourceAttribute(string source) { Value = source; }
        public string Value { get; private set; }
    }

    /// <summary>Human description emitted into the JSON schema.</summary>
    [AttributeUsage(AttributeTargets.Class | AttributeTargets.Field, AllowMultiple = false)]
    public sealed class DocAttribute : Attribute
    {
        public DocAttribute(string text) { Text = text; }
        public string Text { get; private set; }
    }

    /// <summary>The field may be JSON null.</summary>
    [AttributeUsage(AttributeTargets.Field, AllowMultiple = false)]
    public sealed class NullableAttribute : Attribute
    {
    }

    /// <summary>The items of a list field may be JSON null (positional lists where null means "none").</summary>
    [AttributeUsage(AttributeTargets.Field, AllowMultiple = false)]
    public sealed class NullableItemsAttribute : Attribute
    {
    }

    /// <summary>Restricts a string field to a fixed set of values.</summary>
    [AttributeUsage(AttributeTargets.Field, AllowMultiple = false)]
    public sealed class EnumValuesAttribute : Attribute
    {
        public EnumValuesAttribute(params string[] values) { Values = values; }
        public string[] Values { get; private set; }
    }

    /// <summary>Envelope field: exempt from the data-map annotation rule.</summary>
    [AttributeUsage(AttributeTargets.Class | AttributeTargets.Field, AllowMultiple = false)]
    public sealed class EnvelopeAttribute : Attribute
    {
    }
}
