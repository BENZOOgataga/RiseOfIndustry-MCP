using UnityEngine;

namespace RoiMcp.Observer.ReadLayer
{
    /// <summary>
    /// The observer writes at most two lines to the Unity log (PRD 19): one on load and one on a fatal
    /// self-disable. Everything else goes to the observer's own log file.
    /// </summary>
    public static class UnityLog
    {
        public static void Loaded(string version, string exchangeDir)
        {
            Debug.Log("RoiMcpObserver " + version + " loaded (read-only observer). Exchange directory: " + exchangeDir);
        }

        public static void FatalSelfDisable()
        {
            Debug.LogWarning("RoiMcpObserver disabled itself after an unrecoverable error. The game is unaffected.");
        }
    }
}
