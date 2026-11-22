<script lang="ts">
  import {
    AlertCircle,
    CheckCircle2,
    Circle,
    Eye,
    FileVideo,
    FolderPlus,
    Loader2,
    Plus,
    SkipForward,
    Square,
    Trash2,
  } from "lucide-svelte";
  import { baseName, fmtBytes, fmtDuration } from "../lib/format";
  import { run } from "../lib/run.svelte";
  import type { Config, FileRow } from "../lib/types";

  let {
    config,
    onAddFiles,
    onAddFolder,
  }: { config: Config; onAddFiles: () => void; onAddFolder: () => void } = $props();

  function resolution(file: FileRow): string {
    const info = file.info;
    if (!info || !info.width || !info.height) return "";
    return `${info.width}×${info.height}`;
  }

  function codecLabel(codec: string): string {
    switch (codec.toLowerCase()) {
      case "h264":
        return "H.264";
      case "hevc":
        return "HEVC";
      case "av1":
        return "AV1";
      case "vp9":
        return "VP9";
      case "mpeg2video":
        return "MPEG-2";
      case "mpeg4":
        return "MPEG-4";
      case "vc1":
        return "VC-1";
      default:
        return codec ? codec.toUpperCase() : "";
    }
  }

  function languages(values: string[]): string {
    return values.filter((value) => value !== "und").join(", ");
  }

  function audioLabel(file: FileRow): string {
    const info = file.info;
    if (!info || info.audio_count === 0) return "No audio";
    const langs = languages(info.audio_languages);
    return `${info.audio_count} audio${langs ? ` · ${langs}` : ""}`;
  }

  function subtitleLabel(file: FileRow): string {
    const info = file.info;
    if (!info || info.subtitle_count === 0) return "No subtitles";
    const langs = languages(info.subtitle_languages);
    return `${info.subtitle_count} subs${langs ? ` · ${langs}` : ""}`;
  }

  function statusLine(file: FileRow): string {
    if (file.analyzing) return "Reading streams…";
    if (file.status === "running") return file.step;
    if (file.status === "done") return "Optimized";
    if (file.status === "would_change") return "Would change (preview)";
    if (file.status === "error") return file.step;
    if (file.status === "stopped") return "Stopped";
    if (file.status === "skipped") return "Skipped — not a video";
    if (file.status === "unchanged") return "Already the target format";
    if (file.info?.error) return file.info.error;
    return "Ready";
  }

  function resultText(file: FileRow): string {
    if (file.status === "done") return file.result;
    if (file.status === "would_change") return "preview";
    if (file.status === "error") return "Failed";
    if (file.status === "stopped") return "Stopped";
    if (file.status === "skipped") return "Skipped";
    if (file.status === "unchanged") return "Unchanged";
    return "";
  }
</script>

<section class="queue">
  <header class="queue-head">
    <div>
      <h1>Queue</h1>
      <p>
        {#if run.files.length === 0}
          No files yet
        {:else}
          {run.files.length} file{run.files.length === 1 ? "" : "s"}
          {#if run.sourceBytes > 0}· {fmtBytes(run.sourceBytes)}{/if}
          {#if run.analyzingCount > 0}· analysing {run.analyzingCount}…{/if}
        {/if}
      </p>
    </div>
    <div class="head-actions">
      <button class="ghost-button" onclick={onAddFiles} disabled={run.running}>
        <Plus size={14} /> Add files
      </button>
      <button class="ghost-button" onclick={onAddFolder} disabled={run.running}>
        <FolderPlus size={14} /> Folder
      </button>
      {#if run.files.length > 0}
        <button class="icon-button" title="Clear queue" disabled={run.running} onclick={() => run.clear()}>
          <Trash2 size={15} />
        </button>
      {/if}
    </div>
  </header>

  {#if run.files.length === 0}
    <div class="drop-zone">
      <div class="orb"></div>
      <div class="drop-icon"><FileVideo size={30} /></div>
      <h2>Drop videos to begin</h2>
      <p>Velox cleans up tracks, standardises audio and re-encodes to HEVC — in one pass where possible.</p>
      <div class="drop-actions">
        <button class="primary-button" onclick={onAddFiles}><Plus size={15} /> Choose files</button>
        <button class="ghost-button" onclick={onAddFolder}><FolderPlus size={15} /> Choose folder</button>
      </div>
      <ol class="steps">
        <li><span>1</span> Add files</li>
        <li><span>2</span> Pick a quality</li>
        <li><span>3</span> Run</li>
      </ol>
    </div>
  {:else}
    <div class="file-list">
      {#each run.files as file (file.path)}
        <article class:running={file.status === "running"} class:done={file.status === "done"} class:would_change={file.status === "would_change"} class:error={file.status === "error"} class="file-card">
          <div class="state">
            {#if file.status === "running"}
              <Loader2 size={17} class="spin" />
            {:else if file.status === "done"}
              <CheckCircle2 size={17} />
            {:else if file.status === "error"}
              <AlertCircle size={17} />
            {:else if file.status === "would_change"}
              <Eye size={16} />
            {:else if file.status === "stopped"}
              <Square size={13} />
            {:else if file.status === "skipped"}
              <SkipForward size={16} />
            {:else}
              <Circle size={15} />
            {/if}
          </div>

          <div class="file-body">
            <div class="file-top">
              <span class="name" title={file.path}>{file.name}</span>
              <span class="size">{file.size > 0 ? fmtBytes(file.size) : ""}</span>
            </div>

            <div class="chips">
              {#if file.info}
                {#if file.info.container}
                  <span class="chip">{file.info.container.toUpperCase()}</span>
                {/if}
                {#if resolution(file)}
                  <span class="chip">{resolution(file)}</span>
                {/if}
                {#if file.info.duration > 0}
                  <span class="chip">{fmtDuration(file.info.duration)}</span>
                {/if}
                {#if file.info.video_codec}
                  {#if !config.plugins.hevc_gpu.enabled}
                    <span class="chip muted">{codecLabel(file.info.video_codec)} · encode off</span>
                  {:else if file.info.video_codec === config.plugins.hevc_gpu.target_codec}
                    <span class="chip muted">{codecLabel(file.info.video_codec)} · already target</span>
                  {:else}
                    <span class="chip accent">
                      {codecLabel(file.info.video_codec)}
                      <span class="arrow">→</span>
                      {codecLabel(config.plugins.hevc_gpu.target_codec)}
                    </span>
                  {/if}
                {/if}
                <span class="chip muted">{audioLabel(file)}</span>
                <span class="chip muted">{subtitleLabel(file)}</span>
              {:else if file.analyzing}
                <span class="chip muted">Reading streams…</span>
              {/if}
            </div>

            {#if file.status === "running"}
              <div class="progress-row">
                <div class="bar">
                  <div style={`width:${Math.max(3, Math.round(file.progress * 100))}%`}></div>
                </div>
                <span class="percent">{Math.round(file.progress * 100)}%</span>
              </div>
            {/if}

            <div class="status-line" class:error-text={file.status === "error" || file.info?.error}>
              {statusLine(file)}
            </div>
          </div>

          <div class="right">
            {#if resultText(file)}
              <span class="result" class:positive={file.status === "done"} class:negative={file.status === "error"}>
                {resultText(file)}
              </span>
            {/if}
            <button
              class="remove"
              title="Remove"
              disabled={run.running}
              onclick={() => run.remove(file.path)}
            >
              <Trash2 size={14} />
            </button>
          </div>
        </article>
      {/each}
    </div>
  {/if}
</section>

<style>
  .queue {
    display: flex;
    flex: 1;
    flex-direction: column;
    min-width: 0;
    min-height: 0;
    padding: 22px 24px 18px;
  }

  .queue-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 16px;
  }

  h1 {
    margin: 0;
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.03em;
  }

  .queue-head p {
    margin: 3px 0 0;
    color: var(--muted);
    font-size: 12px;
  }

  .head-actions {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .ghost-button,
  .primary-button {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 31px;
    padding: 0 11px;
    border: 1px solid var(--border);
    border-radius: 9px;
    background: var(--control);
    color: var(--text);
    font: inherit;
    font-size: 12px;
    cursor: default;
    transition: background 120ms ease, border-color 120ms ease;
  }

  .ghost-button:hover:not(:disabled) {
    background: var(--control-hover);
    border-color: var(--border-strong);
  }

  .ghost-button:disabled,
  .primary-button:disabled {
    opacity: 0.45;
  }

  .primary-button {
    border-color: transparent;
    background: var(--accent-gradient);
    color: #fff;
    box-shadow: 0 4px 16px var(--accent-glow);
  }

  .primary-button:hover:not(:disabled) {
    filter: brightness(1.08);
  }

  .icon-button {
    display: grid;
    place-items: center;
    width: 31px;
    height: 31px;
    border: 1px solid transparent;
    border-radius: 9px;
    background: transparent;
    color: var(--muted);
    cursor: default;
  }

  .icon-button:hover:not(:disabled) {
    border-color: var(--border);
    background: var(--control);
    color: var(--text);
  }

  .drop-zone {
    position: relative;
    display: flex;
    flex: 1;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 8px;
    overflow: hidden;
    border: 1.5px dashed var(--border-strong);
    border-radius: 18px;
    background: var(--drop-bg);
    color: var(--muted);
    text-align: center;
  }

  .orb {
    position: absolute;
    top: -120px;
    left: 50%;
    width: 420px;
    height: 260px;
    transform: translateX(-50%);
    border-radius: 50%;
    background: radial-gradient(closest-side, var(--accent-glow), transparent 70%);
    opacity: 0.7;
    pointer-events: none;
  }

  .drop-icon {
    z-index: 1;
    display: grid;
    place-items: center;
    width: 64px;
    height: 64px;
    margin-bottom: 4px;
    border: 1px solid var(--border);
    border-radius: 18px;
    background: var(--panel);
    color: var(--accent);
    box-shadow: var(--shadow-soft);
  }

  .drop-zone h2 {
    z-index: 1;
    margin: 0;
    color: var(--text);
    font-size: 18px;
    letter-spacing: -0.02em;
  }

  .drop-zone p {
    z-index: 1;
    max-width: 430px;
    margin: 0;
    font-size: 12.5px;
    line-height: 1.55;
  }

  .drop-actions {
    z-index: 1;
    display: flex;
    gap: 8px;
    margin-top: 12px;
  }

  .steps {
    z-index: 1;
    display: flex;
    gap: 22px;
    margin: 26px 0 0;
    padding: 0;
    list-style: none;
    font-size: 11px;
  }

  .steps li {
    display: flex;
    align-items: center;
    gap: 7px;
  }

  .steps span {
    display: grid;
    place-items: center;
    width: 19px;
    height: 19px;
    border: 1px solid var(--border-strong);
    border-radius: 50%;
    color: var(--muted);
    font-size: 10px;
  }

  .file-list {
    display: flex;
    flex: 1;
    flex-direction: column;
    gap: 7px;
    min-height: 0;
    overflow: auto;
    padding-right: 3px;
  }

  .file-card {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 11px 12px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--panel);
    transition: border-color 160ms ease, background 160ms ease, box-shadow 160ms ease;
  }

  .file-card:hover {
    border-color: var(--border-strong);
  }

  .file-card.running {
    border-color: var(--accent);
    background: var(--panel);
    box-shadow: 0 0 0 3px var(--accent-soft), 0 6px 20px rgba(0, 0, 0, 0.08);
  }

  .state {
    display: grid;
    flex: 0 0 26px;
    place-items: center;
    height: 26px;
    color: var(--muted);
  }

  .running .state {
    color: var(--accent);
  }

  .done .state {
    color: var(--green);
  }

  .would_change .state {
    color: var(--accent-text);
  }

  .error .state {
    color: var(--red);
  }

  .file-body {
    min-width: 0;
    flex: 1;
  }

  .file-top {
    display: flex;
    align-items: baseline;
    gap: 10px;
  }

  .name {
    overflow: hidden;
    font-size: 12.5px;
    font-weight: 600;
    letter-spacing: -0.01em;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .size {
    margin-left: auto;
    color: var(--muted);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
    margin-top: 5px;
  }

  .chip {
    padding: 1.5px 6px;
    border: 1px solid var(--border-soft);
    border-radius: 5px;
    background: var(--chip-bg);
    color: var(--text);
    font-size: 9.5px;
    font-weight: 550;
    letter-spacing: 0.02em;
  }

  .chip.muted {
    border-color: transparent;
    background: transparent;
    color: var(--muted);
    font-weight: 450;
  }

  .chip.accent {
    border-color: transparent;
    background: var(--accent-soft);
    color: var(--accent-text);
  }

  .arrow {
    margin: 0 2px;
    opacity: 0.6;
  }

  .progress-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 7px;
  }

  .bar {
    overflow: hidden;
    flex: 1;
    height: 4px;
    border-radius: 3px;
    background: var(--bar-bg);
  }

  .bar > div {
    height: 100%;
    border-radius: 3px;
    background: var(--accent-gradient);
    transition: width 200ms ease;
  }

  .percent {
    width: 34px;
    color: var(--muted);
    font-size: 10px;
    font-variant-numeric: tabular-nums;
    text-align: right;
  }

  .status-line {
    overflow: hidden;
    margin-top: 5px;
    color: var(--muted);
    font-size: 10.5px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .status-line.error-text {
    color: var(--red);
  }

  .right {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .result {
    font-size: 11.5px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .result.positive {
    color: var(--green);
  }

  .result.negative {
    color: var(--red);
  }

  .remove {
    display: grid;
    place-items: center;
    width: 25px;
    height: 25px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: var(--muted);
    cursor: default;
    opacity: 0;
  }

  .file-card:hover .remove {
    opacity: 1;
  }

  .remove:hover:not(:disabled) {
    background: var(--control-hover);
    color: var(--red);
  }

  :global(.spin) {
    animation: spin 1s linear infinite;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>
