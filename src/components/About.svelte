<script lang="ts">
  import { ExternalLink, Settings2, X, Zap } from "lucide-svelte";
  import type { AppInfo } from "../lib/types";

  interface Props {
    info: AppInfo | null;
    onClose: () => void;
    onOpenPath: (path: string) => void;
  }

  let { info, onClose, onOpenPath }: Props = $props();

  function handleKeydown(event: KeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    }
  }

  function shortVersion(value: string | null): string {
    if (!value) return "not found";
    return value.length > 80 ? `${value.slice(0, 77)}…` : value;
  }
</script>

<svelte:window onkeydown={handleKeydown} />

<div class="backdrop" role="presentation" onclick={onClose}></div>

<div class="about" role="dialog" aria-label="About Velox">
  <button class="close" onclick={onClose} title="Close (Esc)"><X size={16} /></button>

  <div class="hero">
    <span class="mark"><Zap size={22} strokeWidth={2.4} /></span>
    <div>
      <h1>Velox</h1>
      <p>Video optimizer</p>
    </div>
  </div>

  <p class="tagline">
    Remux, clean and transcode video files with ffmpeg. Built with Rust, Tauri 2 and Svelte 5.
  </p>

  {#if info}
    <div class="details">
      <div class="row"><span>Version</span><strong>{info.version}</strong></div>
      <div class="row"><span>Tauri</span><strong>{info.tauri_version}</strong></div>
      <div class="row"><span>Platform</span><strong>{info.os} · {info.arch}</strong></div>
      <div class="row">
        <span>ffmpeg</span>
        <div class="tool">
          <strong title={info.ffmpeg_path ?? ""}>{shortVersion(info.ffmpeg_version)}</strong>
          <small title={info.ffmpeg_path ?? ""}>{info.ffmpeg_path ?? "not found"}</small>
        </div>
      </div>
      <div class="row">
        <span>ffprobe</span>
        <div class="tool">
          <strong title={info.ffprobe_path ?? ""}>{shortVersion(info.ffprobe_version)}</strong>
          <small title={info.ffprobe_path ?? ""}>{info.ffprobe_path ?? "not found"}</small>
        </div>
      </div>
    </div>
  {:else}
    <div class="loading">Loading system information…</div>
  {/if}

  <div class="actions">
    {#if info}
      <button class="button" onclick={() => onOpenPath(info.config_path)}>
        <Settings2 size={14} /> Open config
      </button>
    {/if}
    <button class="button" onclick={() => onOpenPath("https://github.com/KyrionKS/Velox")}>
      <ExternalLink size={14} /> Project page
    </button>
    <button class="button primary" onclick={onClose}><X size={14} /> Close</button>
  </div>
</div>

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    z-index: 55;
    background: var(--backdrop);
    backdrop-filter: blur(4px);
  }

  .about {
    position: fixed;
    top: 50%;
    left: 50%;
    z-index: 56;
    width: min(470px, calc(100vw - 40px));
    padding: 22px;
    transform: translate(-50%, -50%);
    border: 1px solid var(--border);
    border-radius: 18px;
    background: var(--bg-elevated);
    box-shadow: var(--shadow);
  }

  .close {
    position: absolute;
    top: 12px;
    right: 12px;
    display: grid;
    place-items: center;
    width: 28px;
    height: 28px;
    border: 1px solid transparent;
    border-radius: 8px;
    background: transparent;
    color: var(--muted);
    cursor: default;
  }

  .close:hover {
    border-color: var(--border);
    background: var(--control);
    color: var(--text);
  }

  .hero {
    display: flex;
    align-items: center;
    gap: 13px;
  }

  .mark {
    display: grid;
    place-items: center;
    width: 46px;
    height: 46px;
    border-radius: 13px;
    background: var(--accent-gradient);
    color: #fff;
    box-shadow: 0 6px 20px var(--accent-glow);
  }

  h1 {
    margin: 0;
    font-size: 23px;
    letter-spacing: -0.03em;
  }

  .hero p {
    margin: 1px 0 0;
    color: var(--muted);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  .tagline {
    margin: 14px 0 16px;
    color: var(--muted);
    font-size: 11.5px;
    line-height: 1.55;
  }

  .details {
    display: flex;
    flex-direction: column;
    gap: 1px;
    overflow: hidden;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--border);
  }

  .row {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 8px 11px;
    background: var(--panel);
    font-size: 11px;
  }

  .row > span {
    width: 74px;
    flex: 0 0 74px;
    padding-top: 1px;
    color: var(--muted);
  }

  .row strong {
    font-size: 10.5px;
    font-weight: 550;
    word-break: break-word;
  }

  .tool {
    display: flex;
    min-width: 0;
    flex-direction: column;
    gap: 1px;
  }

  .tool small {
    overflow: hidden;
    color: var(--muted);
    font-size: 9.5px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .loading {
    padding: 18px;
    border: 1px solid var(--border);
    border-radius: 10px;
    color: var(--muted);
    font-size: 11px;
    text-align: center;
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 7px;
    margin-top: 16px;
  }

  .button {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 30px;
    padding: 0 10px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--control);
    color: var(--text);
    font: inherit;
    font-size: 11px;
    cursor: default;
  }

  .button:hover {
    background: var(--control-hover);
  }

  .button.primary {
    border-color: transparent;
    background: var(--accent-gradient);
    color: #fff;
  }
</style>
