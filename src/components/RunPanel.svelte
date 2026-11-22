<script lang="ts">
  import {
    ArrowRight,
    Eye,
    FolderOutput,
    Gauge,
    Play,
    SlidersHorizontal,
    Sparkles,
    Square,
    Subtitles,
    Video,
    Volume2,
  } from "lucide-svelte";
  import type { Estimate } from "../lib/estimate";
  import { fmtBytes } from "../lib/format";
  import { run } from "../lib/run.svelte";
  import { QUALITY_PRESETS, presetForBpp, type Config } from "../lib/types";

  interface Props {
    config: Config;
    estimate: Estimate | null;
    onStart: (dryRun: boolean) => void;
    onSettings: () => void;
  }

  let { config = $bindable(), estimate, onStart, onSettings }: Props = $props();

  type Preset = "auto" | "small" | "balanced" | "high" | "size";

  const presets: { id: Preset; label: string; hint: string }[] = [
    { id: "auto", label: "Auto", hint: "Best quality per byte" },
    { id: "small", label: "Compact", hint: "Smaller files" },
    { id: "balanced", label: "Balanced", hint: "Recommended" },
    { id: "high", label: "Archive", hint: "More detail" },
  ];

  const active = $derived.by((): Preset => {
    const mode = config.plugins.hevc_gpu.bitrate_mode;
    if (mode === "quality") return "auto";
    if (mode === "size") return "size";
    return presetForBpp(config.plugins.hevc_gpu.bits_per_pixel) as Preset;
  });

  const encodeEnabled = $derived(config.plugins.hevc_gpu.enabled);
  const sourceBytes = $derived(estimate?.source ?? run.sourceBytes);
  const processedCount = $derived(run.processedCount);
  const savedPercent = $derived(
    estimate && sourceBytes > 0 ? Math.max(0, 100 - (estimate.bytes / sourceBytes) * 100) : 0,
  );

  function choose(preset: Preset) {
    const hevc = config.plugins.hevc_gpu;
    if (preset === "auto") {
      hevc.bitrate_mode = "quality";
      return;
    }
    if (preset === "size") {
      hevc.bitrate_mode = "size";
      return;
    }
    hevc.bitrate_mode = "tier";
    hevc.bits_per_pixel = QUALITY_PRESETS[preset];
  }

  function modeHint(): string {
    const hevc = config.plugins.hevc_gpu;
    if (hevc.bitrate_mode === "quality") return `Constant quality ${hevc.quality} — adapts to the content`;
    if (hevc.bitrate_mode === "size") return `About ${hevc.target_size_mb.toFixed(0)} MiB per file`;
    return `${hevc.bits_per_pixel.toFixed(3)} bits per pixel — predictable size`;
  }

  function outputLabel(): string {
    if (config.general.output_dir) return "New folder";
    if (config.general.keep_original) return "Replace · keep backup";
    return "Replace originals";
  }
</script>

<aside class="inspector">
  <div class="panel">
    <header>
      <div class="eyebrow"><Sparkles size={13} /> Run</div>
      <h2>Choose a quality</h2>
      <p class="hint">One setting for the whole queue. Fine-tune anything in Settings.</p>
    </header>

    <div class="preset-grid">
      {#each presets as preset}
        <button
          class:active={active === preset.id}
          onclick={() => choose(preset.id)}
          disabled={run.running}
        >
          <span class="preset-label">{preset.label}</span>
          <span class="preset-hint">{preset.hint}</span>
        </button>
      {/each}
    </div>

    <div class="mode-hint">
      <Gauge size={13} />
      <span>{modeHint()}</span>
    </div>

    <div class="target-row" class:active={config.plugins.hevc_gpu.bitrate_mode === "size"}>
      <button class="target-toggle" onclick={() => choose("size")} disabled={run.running}>
        Size target
      </button>
      <input
        type="number"
        min="1"
        step="50"
        bind:value={config.plugins.hevc_gpu.target_size_mb}
        onfocus={() => choose("size")}
        disabled={run.running}
      />
      <span>MiB</span>
    </div>

    <div class="divider"></div>

    <div class="plan">
      <div class="plan-row">
        <Video size={14} />
        <span>Video</span>
        <strong>{encodeEnabled ? `${config.plugins.hevc_gpu.target_codec.toUpperCase()} · ${config.plugins.hevc_gpu.container}` : "Keep source (encoding off)"}</strong>
      </div>
      <div class="plan-row">
        <Volume2 size={14} />
        <span>Audio</span>
        <strong>{config.plugins.standardise_audio.audio_codec.toUpperCase()} · {config.plugins.clean_audio.language}</strong>
      </div>
      <div class="plan-row">
        <Subtitles size={14} />
        <span>Subtitles</span>
        <strong>{config.plugins.clean_subs.language || "Keep all"}</strong>
      </div>
      <div class="plan-row">
        <FolderOutput size={14} />
        <span>Output</span>
        <strong>{outputLabel()}</strong>
      </div>
    </div>

    <div class="estimate">
      {#if !encodeEnabled}
        <div class="estimate-head">
          <span class="estimate-label">Video encoding</span>
          <span class="estimate-value qualitative">Disabled</span>
        </div>
        <p class="estimate-note">
          “Encode Video” is disabled. You can still clean up tracks and remux.
          <button class="enable-link" onclick={() => (config.plugins.hevc_gpu.enabled = true)}>
            Enable encoding
          </button>
        </p>
      {:else if estimate && estimate.counted > 0}
        <div class="estimate-head">
          <span class="estimate-label">Estimated output</span>
          <span class="estimate-value">~{fmtBytes(estimate.bytes)}</span>
        </div>
        <div class="compare-bar">
          <div class="before" style={`width:${sourceBytes > 0 ? Math.min(100, (estimate.bytes / sourceBytes) * 100) : 0}%`}></div>
        </div>
        <div class="estimate-foot">
          <span>{fmtBytes(sourceBytes)} source</span>
          <span class="save">{savedPercent.toFixed(0)}% smaller</span>
        </div>
      {:else if config.plugins.hevc_gpu.bitrate_mode === "quality"}
        <div class="estimate-head">
          <span class="estimate-label">Output size</span>
          <span class="estimate-value qualitative">Adapts to content</span>
        </div>
        <p class="estimate-note">
          Constant quality keeps the best detail per byte. The final size depends on the footage.
        </p>
      {:else if run.analyzingCount > 0}
        <div class="estimate-head">
          <span class="estimate-label">Estimated output</span>
          <span class="estimate-value qualitative">Analysing…</span>
        </div>
        <div class="skeleton"></div>
      {:else if run.files.length > 0}
        <div class="estimate-head">
          <span class="estimate-label">Source</span>
          <span class="estimate-value">{fmtBytes(sourceBytes)}</span>
        </div>
        <p class="estimate-note">Add video files to see an output estimate.</p>
      {:else}
        <div class="estimate-head">
          <span class="estimate-label">Output estimate</span>
          <span class="estimate-value qualitative">—</span>
        </div>
        <p class="estimate-note">Add files to see how much space the run will save.</p>
      {/if}
    </div>

    <label class="skip-row">
      <input type="checkbox" bind:checked={config.general.skip_processed} />
      <span>Skip already processed files{#if processedCount > 0} ({processedCount}){/if}</span>
    </label>

    {#if run.running}
      <button class="stop-button" onclick={() => run.stop()}>
        <Square size={14} /> Stop after current pass
      </button>
    {:else}
      <button
        class="start-button"
        onclick={() => onStart(false)}
        disabled={run.files.length === 0}
      >
        <Play size={16} fill="currentColor" /> Start run
        <span class="shortcut">⌘R</span>
      </button>
    {/if}

    <div class="secondary-actions">
      <button class="link-button" onclick={() => onStart(true)} disabled={run.running || run.files.length === 0}>
        <Eye size={13} /> Preview only
      </button>
      <button class="link-button" onclick={onSettings}>
        <SlidersHorizontal size={13} /> Settings
      </button>
    </div>

    <div class="threads">
      {config.general.concurrency} file{config.general.concurrency === 1 ? "" : "s"} at a time
      <ArrowRight size={11} />
    </div>
  </div>
</aside>

<style>
  .inspector {
    width: 336px;
    min-width: 336px;
    overflow: auto;
    padding: 18px 18px 18px 0;
  }

  .panel {
    display: flex;
    flex-direction: column;
    min-height: 100%;
    padding: 18px;
    border: 1px solid var(--border);
    border-radius: 16px;
    background: var(--panel);
    box-shadow: var(--shadow-soft);
  }

  .eyebrow {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    color: var(--accent-text);
    font-size: 10px;
    font-weight: 650;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  h2 {
    margin: 6px 0 0;
    font-size: 17px;
    letter-spacing: -0.02em;
  }

  .hint {
    margin: 5px 0 0;
    color: var(--muted);
    font-size: 11.5px;
    line-height: 1.45;
  }

  .preset-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
    margin-top: 14px;
  }

  .preset-grid button {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
    padding: 9px 10px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--control);
    color: var(--text);
    font: inherit;
    text-align: left;
    cursor: default;
    transition: border-color 120ms ease, background 120ms ease, box-shadow 120ms ease;
  }

  .preset-grid button:hover:not(:disabled) {
    border-color: var(--border-strong);
    background: var(--control-hover);
  }

  .preset-grid button.active {
    border-color: var(--accent);
    background: var(--accent-soft);
    box-shadow: 0 0 0 3px var(--accent-soft);
  }

  .preset-grid button:disabled {
    opacity: 0.55;
  }

  .preset-label {
    font-size: 12px;
    font-weight: 650;
  }

  .preset-hint {
    color: var(--muted);
    font-size: 9.5px;
  }

  .mode-hint {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 8px;
    color: var(--muted);
    font-size: 10.5px;
  }

  .target-row {
    display: flex;
    align-items: center;
    gap: 7px;
    margin-top: 10px;
    padding: 5px 7px 5px 9px;
    border: 1px solid var(--border);
    border-radius: 9px;
    background: var(--control);
    color: var(--muted);
    font-size: 10.5px;
  }

  .target-row.active {
    border-color: var(--accent);
    background: var(--accent-soft);
  }

  .target-toggle {
    padding: 0;
    white-space: nowrap;
    border: 0;
    background: transparent;
    color: var(--text);
    font: inherit;
    font-size: 11px;
    font-weight: 550;
    cursor: default;
  }

  .target-row input {
    width: 100%;
    height: 24px;
    margin-left: auto;
    padding: 0 6px;
    border: 1px solid var(--border-strong);
    border-radius: 6px;
    background: var(--input);
    color: var(--text);
    font: inherit;
    font-size: 11px;
    text-align: right;
    outline: none;
  }

  .target-row input:focus {
    border-color: var(--accent);
  }

  .divider {
    height: 1px;
    margin: 14px 0;
    background: var(--border-soft);
  }

  .plan {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .plan-row {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--muted);
    font-size: 11px;
  }

  :global(.plan-row svg) {
    flex: 0 0 auto;
    opacity: 0.8;
  }

  .plan-row span {
    width: 64px;
  }

  .plan-row strong {
    overflow: hidden;
    margin-left: auto;
    color: var(--text);
    font-size: 10.5px;
    font-weight: 550;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .estimate {
    margin-top: 13px;
    padding: 11px 12px;
    border: 1px solid var(--border-soft);
    border-radius: 11px;
    background: var(--well);
  }

  .estimate-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
  }

  .estimate-label {
    color: var(--muted);
    font-size: 10px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  .estimate-value {
    font-size: 17px;
    font-weight: 700;
    letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums;
  }

  .estimate-value.qualitative {
    color: var(--muted);
    font-size: 12px;
    font-weight: 550;
    letter-spacing: 0;
  }

  .compare-bar {
    overflow: hidden;
    height: 5px;
    margin-top: 9px;
    border-radius: 3px;
    background: var(--bar-bg);
  }

  .compare-bar .before {
    height: 100%;
    border-radius: 3px;
    background: var(--accent-gradient);
    transition: width 260ms ease;
  }

  .estimate-foot {
    display: flex;
    justify-content: space-between;
    margin-top: 5px;
    color: var(--muted);
    font-size: 9.5px;
  }

  .estimate-foot .save {
    color: var(--green);
    font-weight: 600;
  }

  .enable-link {
    display: inline;
    padding: 0;
    border: 0;
    background: transparent;
    color: var(--accent-text);
    font: inherit;
    font-size: 10.5px;
    font-weight: 600;
    text-decoration: underline;
    cursor: default;
  }

  .estimate-note {
    margin: 5px 0 0;
    color: var(--muted);
    font-size: 10.5px;
    line-height: 1.45;
  }

  .skeleton {
    height: 5px;
    margin-top: 9px;
    border-radius: 3px;
    background: linear-gradient(90deg, var(--bar-bg) 0%, var(--control-hover) 50%, var(--bar-bg) 100%);
    background-size: 200% 100%;
    animation: shimmer 1.4s linear infinite;
  }

  .skip-row {
    display: flex;
    align-items: center;
    gap: 7px;
    margin-top: 11px;
    color: var(--muted);
    font-size: 10.5px;
    cursor: default;
  }

  .skip-row input {
    width: 13px;
    height: 13px;
    accent-color: var(--accent);
  }

  .start-button {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    height: 42px;
    margin-top: 14px;
    border: 0;
    border-radius: 11px;
    background: var(--accent-gradient);
    color: #fff;
    font: inherit;
    font-size: 13px;
    font-weight: 650;
    box-shadow: 0 6px 20px var(--accent-glow);
    cursor: default;
    transition: filter 120ms ease, transform 120ms ease;
  }

  .start-button:hover:not(:disabled) {
    filter: brightness(1.08);
  }

  .start-button:active:not(:disabled) {
    transform: translateY(1px);
  }

  .start-button:disabled {
    opacity: 0.4;
    box-shadow: none;
  }

  .shortcut {
    margin-left: 2px;
    padding: 1px 5px;
    border-radius: 5px;
    background: rgba(255, 255, 255, 0.18);
    font-size: 9.5px;
    font-weight: 500;
  }

  .stop-button {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    height: 42px;
    margin-top: 14px;
    border: 1px solid var(--border-strong);
    border-radius: 11px;
    background: var(--control);
    color: var(--text);
    font: inherit;
    font-size: 12.5px;
    font-weight: 600;
    cursor: default;
  }

  .stop-button:hover {
    border-color: var(--red);
    color: var(--red);
  }

  .secondary-actions {
    display: flex;
    justify-content: space-between;
    margin-top: 9px;
  }

  .link-button {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 2px 0;
    border: 0;
    background: transparent;
    color: var(--muted);
    font: inherit;
    font-size: 10.5px;
    cursor: default;
  }

  .link-button:hover:not(:disabled) {
    color: var(--text);
  }

  .link-button:disabled {
    opacity: 0.4;
  }

  .threads {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
    margin-top: auto;
    padding-top: 16px;
    color: var(--muted);
    font-size: 9.5px;
  }

  @keyframes shimmer {
    to {
      background-position: -200% 0;
    }
  }

  @media (max-width: 980px) {
    .inspector {
      width: 300px;
      min-width: 300px;
    }
  }
</style>
