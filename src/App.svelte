<script lang="ts">
  import { onMount } from "svelte";
  import { listen } from "@tauri-apps/api/event";
  import { getCurrentWebview } from "@tauri-apps/api/webview";
  import { open, message } from "@tauri-apps/plugin-dialog";
  import { Activity, LayoutGrid, Minus, Settings2, Square, X, Zap } from "lucide-svelte";
  import { getCurrentWindow } from "@tauri-apps/api/window";

  import ActivityView from "./components/Activity.svelte";
  import Queue from "./components/Queue.svelte";
  import RunPanel from "./components/RunPanel.svelte";
  import SettingsSheet from "./components/SettingsSheet.svelte";
  import StatusBar from "./components/StatusBar.svelte";
  import { configPath as getConfigPath, detectEncoders, getConfig, saveConfig, scanFolder } from "./lib/api";
  import { demoConfig, demoEncoders } from "./lib/demo";
  import { estimateOutput } from "./lib/estimate";
  import { baseName } from "./lib/format";
  import { run } from "./lib/run.svelte";
  import { FILE_FILTERS, type Config, type EncoderInfo, type FileItem } from "./lib/types";

  type View = "library" | "activity";

  const params = new URLSearchParams(window.location.search);
  let view = $state<View>(params.get("view") === "activity" ? "activity" : "library");
  let settingsOpen = $state(params.has("settings"));
  let config = $state<Config | null>(null);
  let configFile = $state("");
  let encoders = $state<EncoderInfo | null>(null);
  let encoderError = $state<string | null>(null);
  let toast = $state<string | null>(null);
  let toastTimer: number | null = null;
  let isDemo = $state(false);
  let platform = $state("other");

  const VIDEO_EXTENSIONS = new Set(FILE_FILTERS[0].extensions);
  const estimate = $derived(config ? estimateOutput(run.files, config) : null);

  onMount(() => {
    platform = /Mac/i.test(navigator.userAgent) ? "macos" : "other";
    document.documentElement.dataset.platform = platform;

    let unlistenDrop: (() => void) | undefined;
    let unlistenMenu: (() => void) | undefined;

    isDemo = import.meta.env.DEV && !("__TAURI_INTERNALS__" in window);

    (async () => {
      if (isDemo) {
        config = demoConfig;
        configFile = "~/Library/Application Support/Velox/config.toml";
        encoders = demoEncoders;
        if (params.has("demo")) {
          if (params.get("demo") === "activity") run.loadDemoActivity();
          else run.loadDemo();
        }
        return;
      }

      await run.init();
      unlistenMenu = await listen<string>("velox://menu", (event) => {
        switch (event.payload) {
          case "settings":
            settingsOpen = true;
            break;
          case "add_files":
            addFiles();
            break;
          case "add_folder":
            addFolder();
            break;
          case "start":
            start(false);
            break;
          case "stop":
            run.stop();
            break;
        }
      });
      unlistenDrop = await getCurrentWebview().onDragDropEvent((event) => {
        if (event.payload.type !== "drop") return;
        const paths = event.payload.paths.filter((path) =>
          VIDEO_EXTENSIONS.has(path.split(".").pop()?.toLowerCase() ?? ""),
        );
        if (paths.length > 0) {
          addPaths(paths);
          view = "library";
        }
      });

      try {
        config = await getConfig();
        configFile = await getConfigPath();
      } catch (error) {
        showToast(String(error));
      }
      try {
        encoders = await detectEncoders();
        encoderError = null;
      } catch (error) {
        encoderError = String(error);
      }
    })();

    window.addEventListener("keydown", handleKeydown);
    return () => {
      unlistenDrop?.();
      unlistenMenu?.();
      window.removeEventListener("keydown", handleKeydown);
    };
  });

  function showToast(text: string) {
    toast = text;
    if (toastTimer !== null) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => {
      toast = null;
      toastTimer = null;
    }, 3500);
  }

  function handleKeydown(event: KeyboardEvent) {
    const meta = event.metaKey || event.ctrlKey;
    if (!meta) return;
    const target = event.target;
    const typing =
      target instanceof HTMLInputElement ||
      target instanceof HTMLTextAreaElement ||
      target instanceof HTMLSelectElement;

    switch (event.key.toLowerCase()) {
      case ",":
        event.preventDefault();
        settingsOpen = true;
        break;
      case "1":
        event.preventDefault();
        view = "library";
        break;
      case "2":
        event.preventDefault();
        view = "activity";
        break;
      case "o":
        if (typing) return;
        event.preventDefault();
        if (event.shiftKey) addFolder();
        else addFiles();
        break;
      case "r":
        if (typing || run.running) return;
        event.preventDefault();
        start(false);
        break;
    }
  }

  function addPaths(paths: string[]) {
    const items: FileItem[] = paths.map((path) => ({ path, name: baseName(path), size: 0 }));
    run.addFiles(items);
    view = "library";
  }

  async function addFiles() {
    if (run.running) return;
    try {
      const selected = await open({ multiple: true, filters: FILE_FILTERS });
      if (!selected) return;
      addPaths(Array.isArray(selected) ? selected : [selected]);
    } catch (error) {
      showToast(String(error));
    }
  }

  async function addFolder() {
    if (run.running) return;
    try {
      const folder = await open({ directory: true });
      if (!folder || Array.isArray(folder)) return;
      const items = await scanFolder(folder, true);
      if (items.length === 0) {
        await message("No video files found in that folder.", { title: "Velox" });
        return;
      }
      run.addFiles(items);
      view = "library";
    } catch (error) {
      showToast(String(error));
    }
  }

  async function start(dryRun: boolean) {
    if (!config || run.running || run.files.length === 0) return;
    try {
      await saveConfig(config);
      configFile = await getConfigPath();
    } catch (error) {
      showToast(`Could not save settings: ${String(error)}`);
      return;
    }
    settingsOpen = false;
    view = "library";
    await run.start(dryRun);
  }

  async function saveSettings() {
    if (!config) return;
    try {
      await saveConfig(config);
      configFile = await getConfigPath();
      showToast("Settings saved.");
    } catch (error) {
      showToast(`Could not save settings: ${String(error)}`);
    }
  }

  async function reloadSettings() {
    try {
      config = await getConfig();
      showToast("Settings reloaded.");
    } catch (error) {
      showToast(String(error));
    }
  }
</script>

<div class="app">
  <header class="app-header" data-tauri-drag-region>
    <div class="brand">
      <span class="mark"><Zap size={15} strokeWidth={2.4} /></span>
      <div class="brand-text">
        <span class="wordmark">Velox</span>
        <span class="tagline">Video optimizer</span>
      </div>
    </div>

    <nav class="view-switch">
      <button class:active={view === "library"} onclick={() => (view = "library")}>
        <LayoutGrid size={14} /> Library
        {#if run.files.length > 0}
          <span class="count-pill">{run.files.length}</span>
        {/if}
      </button>
      <button class:active={view === "activity"} onclick={() => (view = "activity")}>
        <Activity size={14} /> Activity
        {#if run.running}
          <span class="live-dot"></span>
        {/if}
      </button>
    </nav>

    <div class="header-actions">
      <button class="icon-button" onclick={() => (settingsOpen = true)} title="Settings (⌘,)">
        <Settings2 size={17} />
      </button>
      {#if !isDemo && platform !== "macos"}
        <div class="window-controls">
          <button onclick={() => getCurrentWindow().minimize()} title="Minimize"><Minus size={14} /></button>
          <button onclick={() => getCurrentWindow().toggleMaximize()} title="Maximize"><Square size={11} /></button>
          <button class="close" onclick={() => getCurrentWindow().close()} title="Close"><X size={14} /></button>
        </div>
      {/if}
    </div>
  </header>

  {#if config}
    <main class="main">
      {#if view === "library"}
        <div class="workspace">
          <Queue {config} onAddFiles={addFiles} onAddFolder={addFolder} />
          <RunPanel
            bind:config
            {estimate}
            onStart={start}
            onSettings={() => (settingsOpen = true)}
          />
        </div>
      {:else}
        <ActivityView />
      {/if}
    </main>

    <StatusBar onOpenActivity={() => (view = "activity")} />

    {#if settingsOpen}
      <SettingsSheet
        bind:config
        {encoders}
        {encoderError}
        configPath={configFile}
        onSave={saveSettings}
        onReload={reloadSettings}
        onClose={() => (settingsOpen = false)}
      />
    {/if}
  {:else}
    <main class="loading">
      <span class="spinner"></span>
      <p>Loading Velox…</p>
    </main>
  {/if}

  {#if toast}
    <div class="toast" role="status">{toast}</div>
  {/if}
</div>

<style>
  .app {
    display: flex;
    flex-direction: column;
    height: 100vh;
    overflow: hidden;
    background:
      radial-gradient(120% 90% at 8% -10%, var(--glow-a) 0%, transparent 55%),
      radial-gradient(90% 80% at 100% 0%, var(--glow-b) 0%, transparent 50%),
      var(--bg);
  }

  .app-header {
    display: flex;
    align-items: center;
    gap: 20px;
    height: 58px;
    min-height: 58px;
    padding: 0 18px;
    border-bottom: 1px solid var(--border);
    background: var(--header);
    backdrop-filter: blur(28px) saturate(1.2);
    user-select: none;
  }

  :global(html[data-platform="macos"]) .app-header {
    padding-left: 86px;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 190px;
  }

  .mark {
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    border-radius: 9px;
    background: var(--accent-gradient);
    color: #fff;
    box-shadow: 0 4px 14px var(--accent-glow);
  }

  .brand-text {
    display: flex;
    flex-direction: column;
    line-height: 1.1;
  }

  .wordmark {
    font-size: 14.5px;
    font-weight: 700;
    letter-spacing: -0.02em;
  }

  .tagline {
    margin-top: 2px;
    color: var(--muted);
    font-size: 9.5px;
    font-weight: 550;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .view-switch {
    display: flex;
    gap: 3px;
    padding: 3px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--segmented-bg);
  }

  .view-switch button {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 27px;
    padding: 0 11px;
    border: 0;
    border-radius: 7px;
    background: transparent;
    color: var(--muted);
    font: inherit;
    font-size: 12px;
    cursor: default;
    transition: background 120ms ease, color 120ms ease;
  }

  .view-switch button:hover {
    color: var(--text);
  }

  .view-switch button.active {
    background: var(--segmented-active);
    color: var(--text);
    box-shadow: var(--segmented-shadow);
    font-weight: 600;
  }

  .count-pill {
    min-width: 17px;
    padding: 1px 5px;
    border-radius: 8px;
    background: var(--badge);
    color: var(--muted);
    font-size: 9.5px;
    text-align: center;
  }

  .live-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 0 3px var(--green-soft);
    animation: pulse 1.5s ease-in-out infinite;
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-left: auto;
  }

  .icon-button {
    display: grid;
    place-items: center;
    width: 31px;
    height: 31px;
    border: 1px solid transparent;
    border-radius: 8px;
    background: transparent;
    color: var(--muted);
    cursor: default;
  }

  .icon-button:hover {
    border-color: var(--border);
    background: var(--control);
    color: var(--text);
  }

  .window-controls {
    display: flex;
    gap: 1px;
    margin-left: 6px;
    padding-left: 8px;
    border-left: 1px solid var(--border);
  }

  .window-controls button {
    display: grid;
    place-items: center;
    width: 28px;
    height: 24px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: var(--muted);
    cursor: default;
  }

  .window-controls button:hover {
    background: var(--control);
    color: var(--text);
  }

  .window-controls button.close:hover {
    background: #e81123;
    color: #fff;
  }

  .main {
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }

  .workspace {
    display: flex;
    height: 100%;
    min-height: 0;
  }

  .loading {
    display: flex;
    flex: 1;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
    color: var(--muted);
    font-size: 12.5px;
  }

  .spinner {
    width: 22px;
    height: 22px;
    border: 2px solid var(--border-strong);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  .toast {
    position: fixed;
    left: 50%;
    bottom: 70px;
    transform: translateX(-50%);
    max-width: 70vw;
    padding: 9px 16px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--toast);
    color: var(--toast-text);
    font-size: 12px;
    box-shadow: var(--shadow);
    z-index: 60;
  }

  @keyframes pulse {
    50% {
      opacity: 0.4;
    }
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  @media (max-width: 940px) {
    .brand {
      min-width: 0;
    }

    .tagline {
      display: none;
    }
  }
</style>
