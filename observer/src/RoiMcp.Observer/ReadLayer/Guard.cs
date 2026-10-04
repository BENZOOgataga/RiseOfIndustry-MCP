using ProjectAutomata;

namespace RoiMcp.Observer.ReadLayer
{
    /// <summary>
    /// Preconditions checked before calls whose transitive bodies contain a lazy cache insert
    /// (Actor.Get: _componentMap[type] = component). When the component type is already cached the call is a
    /// pure lookup; otherwise the observer skips the value instead of calling. See docs/READ-ONLY-GATE.md,
    /// category guarded_actor_component_cache.
    /// </summary>
    internal static class Guard
    {
        public static bool Has<T>(IActor actor)
        {
            return actor != null && ReflectionTable.ActorComponentCached(actor, typeof(T));
        }

        /// <summary>Actor.modifiers (IActorModifiers) is cached: game price/demand/speed getters can be called.</summary>
        public static bool Modifiers(IActor actor)
        {
            return Has<IActorModifiers>(actor);
        }
    }
}
