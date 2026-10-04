using System;
using System.Diagnostics;
using ProjectAutomata;
using RoiMcp.Observer.Core;
using RoiMcp.Observer.Io;
using RoiMcp.Observer.Publish;
using RoiMcp.Observer.ReadLayer;
using UnityEngine.SceneManagement;

namespace RoiMcp.Observer
{
    /// <summary>
    /// The only in-process entry point (PRD 8.1). Loaded by the game's official mod loader as a component of
    /// the persistent ModLoader GameObject. Load hooks only create the exchange directory, start the
    /// background thread and log one line; they never throw (an exception would disable the mod and halt
    /// boot). All game reads happen in LateUpdate through the runtime and the read layer.
    /// </summary>
    public sealed class ObserverMod : Mod
    {
        private static bool _started;
        private ObserverRuntime _runtime;
        private bool _failed;

        public override void OnModWasLoaded()
        {
            try
            {
                StartObserver();
            }
            catch (Exception)
            {
                _failed = true;
            }
        }

        public override void OnAllModsLoaded()
        {
        }

        private void StartObserver()
        {
            if (_started) return;
            _started = true;
            var dir = ExchangeFiles.ResolveDir();
            if (dir == null)
            {
                // No absolute exchange directory: stay inert rather than write relative to the game folder.
                _failed = true;
                return;
            }
            int pid = Process.GetCurrentProcess().Id;
            var files = new ExchangeFiles(dir, pid);
            if (!files.EnsureDir())
            {
                _failed = true;
                return;
            }
            var hub = new Hub();
            var worker = new BackgroundWorker(hub, files, pid);
            worker.Start();
            _runtime = new ObserverRuntime(new GameReader(), hub);
            SceneManager.sceneLoaded += OnSceneLoaded;
            SceneManager.sceneUnloaded += OnSceneUnloaded;
            SceneManager.activeSceneChanged += OnActiveSceneChanged;
            UnityLog.Loaded(Baseline.ObserverVersion, files.Dir);
        }

        private void LateUpdate()
        {
            if (_failed || _runtime == null) return;
            try
            {
                _runtime.Tick();
            }
            catch (Exception)
            {
                // ObserverRuntime.Tick already isolates every fault; this is the last line of defence.
                _failed = true;
                UnityLog.FatalSelfDisable();
            }
        }

        private void OnSceneLoaded(Scene scene, LoadSceneMode mode)
        {
            try
            {
                if (_runtime != null && mode == LoadSceneMode.Single) _runtime.OnSceneChanged("sceneLoaded");
            }
            catch (Exception)
            {
                // never propagate into Unity
            }
        }

        private void OnSceneUnloaded(Scene scene)
        {
            try
            {
                if (_runtime != null) _runtime.OnSceneChanged("sceneUnloaded");
            }
            catch (Exception)
            {
                // never propagate into Unity
            }
        }

        private void OnActiveSceneChanged(Scene from, Scene to)
        {
            try
            {
                if (_runtime != null) _runtime.OnSceneChanged("activeSceneChanged");
            }
            catch (Exception)
            {
                // never propagate into Unity
            }
        }
    }
}
