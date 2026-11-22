<script lang="ts">
  import { FolderOpen, RefreshCw, Save, X } from "lucide-svelte";
  import { open } from "@tauri-apps/plugin-dialog";
  import { PLUGIN_LABELS, type Config, type EncoderInfo } from "../lib/types";

  interface Props {
    config: Config;
    encoders: EncoderInfo | null;
    encoderError: string | null;
    configPath: string;
    onSave: () => void;
    onReload: () => void;
    onClose: () => void;
  }

  let {
    config = $bindable(),
    encoders = null,
    encoderError = null,
    configPath = "",
    onSave,
    onReload,
    onClose,
  }: Props = $props();

  type Section = "video" | "audio" | "tracks" | "output" | "advanced";
  let section = $state<Section>("video");

  const sections: { id: Section; label: string }[] = [
    { id: "video", label: "Video" },
    { id: "audio", label: "Audio" },
    { id: "tracks", label: "Tracks" },
    { id: "output", label: "Output" },
    { id: "advanced", label: "Advanced" },
  ];

  const encoderOptions = [
    ["Apple VideoToolbox HEVC", "hevc_videotoolbox"],
    ["Apple VideoToolbox H.264", "h264_videotoolbox"],
    ["Automatic", "auto"],
    ["NVIDIA NVENC HEVC", "hevc_nvenc"],
    ["Intel Quick Sync HEVC", "hevc_qsv"],
    ["AMD AMF HEVC", "hevc_amf"],
    ["VAAPI HEVC (Linux)", "hevc_vaapi"],
    ["CPU — libx265", "libx265"],
    ["CPU — libx264", "libx264"],
  ];

  function pluginEnabled(key: string): boolean {
    return (config.plugins as unknown as Record<string, { enabled: boolean }>)[key].enabled;
  }

  function setPluginEnabled(key: string, value: boolean): void {
    (config.plugins as unknown as Record<string, { enabled: boolean }>)[key].enabled = value;
  }

  async function browseOutputDir() {
    const selected = await open({ directory: true });
    if (typeof selected === "string") config.general.output_dir = selected;
  }

  async function locateFfmpeg() {
    const selected = await open({ multiple: false });
    if (typeof selected !== "string") return;
    config.general.ffmpeg_path = selected;
    const separator = selected.includes("\\") ? "\\" : "/";
    const index = selected.lastIndexOf(separator);
    const dir = index >= 0 ? selected.slice(0, index) : "";
    const suffix = selected.toLowerCase().endsWith(".exe") ? ".exe" : "";
    config.general.ffprobe_path = dir ? `${dir}${separator}ffprobe${suffix}` : "ffprobe";
  }

  function handleKeydown(event: KeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    }
  }
</script>

<svelte:window onkeydown={handleKeydown} />

<div class="backdrop" role="presentation" onclick={onClose}></div>

<div class="sheet" role="dialog" aria-label="Settings">
  <header>
    <div>
      <h1>Settings</h1>
      <p title={configPath}>{configPath}</p>
    </div>
    <button class="close" onclick={onClose} title="Close (Esc)"><X size={17} /></button>
  </header>

  <div class="body">
    <nav>
      {#each sections as item}
        <button class:active={section === item.id} onclick={() => (section = item.id)}>{item.label}</button>
      {/each}
    </nav>

    <div class="content">
      {#if section === "video"}
        <div class="section">
          <h2>Encoding</h2>
          <div class="row">
            <label for="mode">Size mode</label>
            <div class="stack">
              <select id="mode" bind:value={config.plugins.hevc_gpu.bitrate_mode}>
                <option value="quality">Constant quality — adapts to the content</option>
                <option value="tier">Automatic — predictable size</option>
                <option value="size">Target size per file</option>
              </select>
              <span class="help">The three presets in the Run panel switch between these modes for you.</span>
            </div>
          </div>

          {#if config.plugins.hevc_gpu.bitrate_mode === "quality"}
            <div class="row">
              <label for="quality">Quality</label>
              <div class="stack">
                <div class="range-row">
                  <input id="quality" type="range" min="1" max="100" bind:value={config.plugins.hevc_gpu.quality} />
                  <span class="range-value">{config.plugins.hevc_gpu.quality}</span>
                </div>
                <span class="help">VideoToolbox 1–100, higher is better. 45–65 recommended; 55+ is excellent.</span>
              </div>
            </div>
          {:else if config.plugins.hevc_gpu.bitrate_mode === "tier"}
            <div class="row">
              <label for="bpp">Bits per pixel</label>
              <div class="stack">
                <input id="bpp" type="number" min="0.005" max="0.3" step="0.001" bind:value={config.plugins.hevc_gpu.bits_per_pixel} />
                <span class="help">0.028 compact · 0.033 balanced · 0.045 archive.</span>
              </div>
            </div>
          {:else}
            <div class="row">
              <label for="target-size">Target size</label>
              <div class="stack">
                <div class="unit-row">
                  <input id="target-size" type="number" min="1" bind:value={config.plugins.hevc_gpu.target_size_mb} />
                  <span>MiB</span>
                </div>
                <span class="help">Per file. Audio is subtracted from the video budget.</span>
              </div>
            </div>
          {/if}

          <div class="row">
            <label for="encoder">Encoder</label>
            <div class="stack">
              <select id="encoder" bind:value={config.plugins.hevc_gpu.encoder}>
                {#each encoderOptions as [label, value]}
                  <option {value}>{label}</option>
                {/each}
              </select>
              {#if encoders}
                <span class="help">
                  {encoders.video.length > 0 ? `Detected: ${encoders.video.slice(0, 6).join(", ")}` : "No hardware encoders detected."}
                </span>
              {:else if encoderError}
                <span class="help warn">{encoderError}</span>
              {/if}
            </div>
          </div>

          <div class="row">
            <label for="container">Container</label>
            <select id="container" bind:value={config.plugins.hevc_gpu.container}>
              <option value="mkv">Matroska (.mkv)</option>
              <option value="mp4">MP4 (.mp4)</option>
              <option value="original">Keep the original</option>
            </select>
          </div>

          <div class="row toggles-row">
            <span class="label">Options</span>
            <div class="toggles">
              <label class="switch">
                <input type="checkbox" bind:checked={config.plugins.hevc_gpu.enable_10bit} />
                <span class="track"></span>
                <span>10-bit output (Main10)</span>
              </label>
              <label class="switch">
                <input type="checkbox" bind:checked={config.plugins.hevc_gpu.spatial_aq} />
                <span class="track"></span>
                <span>Spatial adaptive quantization</span>
              </label>
              <label class="switch">
                <input type="checkbox" bind:checked={config.plugins.hevc_gpu.hardware_decode} />
                <span class="track"></span>
                <span>Hardware decoding (VideoToolbox)</span>
              </label>
            </div>
          </div>
        </div>
      {:else if section === "audio"}
        <div class="section">
          <h2>Audio encoding</h2>
          <div class="row">
            <label for="audio-codec">Codec</label>
            <select id="audio-codec" bind:value={config.plugins.standardise_audio.audio_codec}>
              {#each ["aac", "ac3", "eac3", "flac", "libmp3lame"] as codec}
                <option value={codec}>{codec.toUpperCase()}</option>
              {/each}
            </select>
          </div>
          <div class="row">
            <label for="audio-cap">Bitrate cap</label>
            <div class="stack">
              <div class="unit-row">
                <input id="audio-cap" type="number" min="0" max="1536" bind:value={config.plugins.standardise_audio.audio_bitrate_kbps} />
                <span>kbps</span>
              </div>
              <span class="help">0 keeps the source bitrate. A cap can only lose quality.</span>
            </div>
          </div>
          {#if config.plugins.standardise_audio.audio_bitrate_kbps > 0}
            <div class="row">
              <label for="audio-floor">Per channel floor</label>
              <div class="stack">
                <div class="unit-row">
                  <input id="audio-floor" type="number" min="0" max="512" bind:value={config.plugins.standardise_audio.audio_bitrate_min_per_channel_kbps} />
                  <span>kbps/ch</span>
                </div>
                <span class="help">Surround tracks get at least this much, so 5.1 is not crushed.</span>
              </div>
            </div>
          {/if}
          {#if config.plugins.standardise_audio.audio_codec === "aac"}
            <div class="row">
              <label for="aac">AAC encoder</label>
              <select id="aac" bind:value={config.plugins.standardise_audio.aac_encoder}>
                <option value="auto">Automatic (FDK when available)</option>
                <option value="aac">Native FFmpeg</option>
                <option value="libfdk_aac">libfdk_aac</option>
              </select>
            </div>
          {/if}
          <div class="row">
            <label for="default-audio">Default track</label>
            <div class="stack">
              <input id="default-audio" list="default-audio-options" bind:value={config.plugins.clean_audio.default_audio} />
              <datalist id="default-audio-options">
                <option value="">Leave unchanged</option>
                <option value="first">First kept track</option>
                <option value="best">Highest channels</option>
                <option value="eng">English</option>
                <option value="deu">German</option>
              </datalist>
              <span class="help">A preset, a language code, or empty to keep the flags as they are.</span>
            </div>
          </div>
          <div class="row toggles-row">
            <span class="label">Options</span>
            <div class="toggles">
              <label class="switch">
                <input type="checkbox" bind:checked={config.plugins.standardise_audio.reencode_target_codec} />
                <span class="track"></span>
                <span>Re-encode tracks already in the target codec</span>
              </label>
            </div>
          </div>
        </div>
      {:else if section === "tracks"}
        <div class="section">
          <h2>Track selection</h2>
          <div class="row">
            <label for="audio-langs">Keep audio</label>
            <div class="stack">
              <input id="audio-langs" bind:value={config.plugins.clean_audio.language} />
              <span class="help">ISO-639-2 codes, comma separated. “und” keeps untagged tracks.</span>
            </div>
          </div>
          <div class="row">
            <label for="sub-langs">Keep subtitles</label>
            <div class="stack">
              <input id="sub-langs" bind:value={config.plugins.clean_subs.language} />
              <span class="help">Comma separated language codes.</span>
            </div>
          </div>
          <div class="row">
            <label for="sub-tag">Tag untagged subtitles</label>
            <div class="stack">
              <input id="sub-tag" placeholder="e.g. eng" bind:value={config.plugins.clean_subs.tag_language} />
              <span class="help">Adds a language tag to subtitle tracks that have none.</span>
            </div>
          </div>
          <div class="row toggles-row">
            <span class="label">Remove</span>
            <div class="toggles">
              <label class="switch">
                <input type="checkbox" bind:checked={config.plugins.clean_audio.commentary} />
                <span class="track"></span>
                <span>Commentary / description audio</span>
              </label>
              <label class="switch">
                <input type="checkbox" bind:checked={config.plugins.clean_subs.commentary} />
                <span class="track"></span>
                <span>Commentary / description subtitles</span>
              </label>
              <label class="switch">
                <input type="checkbox" bind:checked={config.plugins.clean_subs.sdh} />
                <span class="track"></span>
                <span>SDH / hard-of-hearing subtitles</span>
              </label>
              <label class="switch">
                <input type="checkbox" bind:checked={config.plugins.clean_audio.tag_title} />
                <span class="track"></span>
                <span>Tag untitled audio with its channel layout</span>
              </label>
            </div>
          </div>
        </div>
      {:else if section === "output"}
        <div class="section">
          <h2>Result</h2>
          <div class="toggles block">
            <label class="switch">
              <input type="checkbox" bind:checked={config.general.keep_original} />
              <span class="track"></span>
              <span>Keep the original as a .velox-orig backup</span>
            </label>
            <label class="switch">
              <input type="checkbox" bind:checked={config.general.verify_output} />
              <span class="track"></span>
              <span>Verify the output before replacing the original</span>
            </label>
            <label class="switch" class:disabled={!config.general.verify_output}>
              <input type="checkbox" bind:checked={config.general.verify_full_decode} disabled={!config.general.verify_output} />
              <span class="track"></span>
              <span>Deep verification (full decode, slower)</span>
            </label>
            <label class="switch">
              <input type="checkbox" bind:checked={config.general.write_report} />
              <span class="track"></span>
              <span>Write a report next to each file</span>
            </label>
            <label class="switch">
              <input type="checkbox" bind:checked={config.general.skip_processed} />
              <span class="track"></span>
              <span>Skip files that already have a successful report</span>
            </label>
          </div>

          <div class="row">
            <label for="output-dir">Output folder</label>
            <div class="stack">
              <div class="inline">
                <input id="output-dir" placeholder="Same folder as the source" bind:value={config.general.output_dir} />
                <button class="small" onclick={browseOutputDir}><FolderOpen size={14} /></button>
              </div>
              <span class="help">Set a folder to leave the originals untouched.</span>
            </div>
          </div>
          <div class="row">
            <label for="temp-dir">Scratch folder</label>
            <div class="stack">
              <input id="temp-dir" bind:value={config.general.temp_dir} />
              <span class="help">“source” keeps temporary files next to each file, which is ideal for NAS storage.</span>
            </div>
          </div>
        </div>
      {:else}
        <div class="section">
          <h2>Limits</h2>
          <div class="row">
            <label for="target-codec">Target codec</label>
            <select id="target-codec" bind:value={config.plugins.hevc_gpu.target_codec}>
              <option value="hevc">HEVC (H.265)</option>
              <option value="h264">H.264</option>
            </select>
          </div>
          <div class="row">
            <label for="min-kbps">Minimum bitrate</label>
            <div class="unit-row">
              <input id="min-kbps" type="number" min="0" bind:value={config.plugins.hevc_gpu.min_video_kbps} />
              <span>kbps</span>
            </div>
          </div>
          <div class="row">
            <label for="max-kbps">Maximum bitrate</label>
            <div class="unit-row">
              <input id="max-kbps" type="number" min="0" bind:value={config.plugins.hevc_gpu.max_video_kbps} />
              <span>kbps</span>
            </div>
          </div>
          <div class="row">
            <label for="cutoff">Skip below</label>
            <div class="stack">
              <div class="unit-row">
                <input id="cutoff" type="number" min="0" bind:value={config.plugins.hevc_gpu.bitrate_cutoff} />
                <span>kbps</span>
              </div>
              <span class="help">Skip files whose bitrate is already below this. 0 disables.</span>
            </div>
          </div>

          <h2 class="spaced">Size check</h2>
          <div class="row">
            <label for="upper">Upper bound</label>
            <div class="unit-row">
              <input id="upper" type="number" min="0" bind:value={config.plugins.file_size_check.upper_bound} />
              <span>% of original</span>
            </div>
          </div>
          <div class="row">
            <label for="lower">Lower bound</label>
            <div class="unit-row">
              <input id="lower" type="number" min="0" bind:value={config.plugins.file_size_check.lower_bound} />
              <span>% of original</span>
            </div>
          </div>
          <div class="row">
            <label for="min-size">Absolute range</label>
            <div class="inline">
              <div class="unit-row">
                <input id="min-size" type="number" min="0" bind:value={config.plugins.file_size_check.min_size_mb} />
                <span>min MiB</span>
              </div>
              <div class="unit-row">
                <input type="number" min="0" bind:value={config.plugins.file_size_check.max_size_mb} />
                <span>max MiB</span>
              </div>
            </div>
          </div>

          <h2 class="spaced">Performance</h2>
          <div class="row">
            <label for="concurrency">Parallel files</label>
            <div class="stack">
              <input id="concurrency" type="number" min="1" max="8" bind:value={config.general.concurrency} />
              <span class="help">2 is a good default on Apple Silicon.</span>
            </div>
          </div>

          <h2 class="spaced">Pipeline steps</h2>
          <div class="toggles block">
            {#each Object.entries(PLUGIN_LABELS) as [key, label]}
              <label class="switch">
                <input
                  type="checkbox"
                  checked={pluginEnabled(key)}
                  onchange={(event) => setPluginEnabled(key, event.currentTarget.checked)}
                />
                <span class="track"></span>
                <span>{label}</span>
              </label>
            {/each}
          </div>

          <h2 class="spaced">Tools</h2>
          <div class="row">
            <label for="ffmpeg">ffmpeg</label>
            <div class="inline">
              <input id="ffmpeg" bind:value={config.general.ffmpeg_path} />
              <button class="small" onclick={locateFfmpeg}><FolderOpen size={14} /> Locate</button>
            </div>
          </div>
          <div class="row">
            <label for="ffprobe">ffprobe</label>
            <input id="ffprobe" bind:value={config.general.ffprobe_path} />
          </div>
          {#if encoderError}
            <p class="warn">{encoderError}</p>
          {/if}
        </div>
      {/if}
    </div>
  </div>

  <footer>
    <button class="button" onclick={onReload}><RefreshCw size={14} /> Reload</button>
    <button class="button primary" onclick={onSave}><Save size={14} /> Save</button>
    <button class="button" onclick={onClose}>Close</button>
  </footer>
</div>

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    z-index: 40;
    background: var(--backdrop);
    backdrop-filter: blur(3px);
  }

  .sheet {
    position: fixed;
    top: 0;
    right: 0;
    bottom: 0;
    z-index: 50;
    display: flex;
    flex-direction: column;
    width: min(760px, calc(100vw - 40px));
    border-left: 1px solid var(--border);
    background: var(--bg-elevated);
    box-shadow: -18px 0 60px rgba(0, 0, 0, 0.3);
    animation: slide-in 180ms ease-out;
  }

  header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    padding: 18px 20px 14px;
    border-bottom: 1px solid var(--border);
  }

  h1 {
    margin: 0;
    font-size: 20px;
    letter-spacing: -0.03em;
  }

  header p {
    overflow: hidden;
    max-width: 560px;
    margin: 3px 0 0;
    color: var(--muted);
    font-size: 10px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .close {
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
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

  .body {
    display: flex;
    flex: 1;
    min-height: 0;
  }

  nav {
    display: flex;
    flex-direction: column;
    gap: 2px;
    width: 144px;
    min-width: 144px;
    padding: 12px 9px;
    border-right: 1px solid var(--border);
  }

  nav button {
    height: 31px;
    padding: 0 9px;
    border: 0;
    border-radius: 7px;
    background: transparent;
    color: var(--muted);
    font: inherit;
    font-size: 12px;
    text-align: left;
    cursor: default;
  }

  nav button:hover {
    background: var(--control-hover);
    color: var(--text);
  }

  nav button.active {
    background: var(--sidebar-active);
    color: var(--text);
    font-weight: 600;
  }

  .content {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: 18px 22px 28px;
  }

  .section {
    display: flex;
    flex-direction: column;
    gap: 5px;
    max-width: 560px;
  }

  .section h2 {
    margin: 0 0 8px;
    font-size: 11px;
    font-weight: 650;
    color: var(--muted);
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  .section h2.spaced {
    margin-top: 18px;
  }

  .row {
    display: grid;
    grid-template-columns: 150px minmax(0, 1fr);
    align-items: start;
    gap: 14px;
    padding: 6px 0;
  }

  .row > label,
  .row > .label {
    padding-top: 6px;
    font-size: 12px;
  }

  .stack {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .help {
    color: var(--muted);
    font-size: 10.5px;
    line-height: 1.45;
  }

  .help.warn,
  .warn {
    color: var(--warn-text);
  }

  input,
  select {
    width: 100%;
    height: 30px;
    padding: 0 8px;
    border: 1px solid var(--border-strong);
    border-radius: 7px;
    background: var(--input);
    color: var(--text);
    font: inherit;
    font-size: 12px;
    outline: none;
  }

  input:focus,
  select:focus {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--focus);
  }

  input[type="range"] {
    height: 22px;
    padding: 0;
    border: 0;
    background: transparent;
    accent-color: var(--accent);
    box-shadow: none;
  }

  .range-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .range-value {
    width: 30px;
    color: var(--muted);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    text-align: right;
  }

  .unit-row {
    display: flex;
    align-items: center;
    gap: 7px;
  }

  .unit-row span {
    color: var(--muted);
    font-size: 10.5px;
    white-space: nowrap;
  }

  .unit-row input {
    max-width: 110px;
  }

  .inline {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .inline input {
    flex: 1;
  }

  .small {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    height: 30px;
    padding: 0 9px;
    border: 1px solid var(--border);
    border-radius: 7px;
    background: var(--control);
    color: var(--text);
    font: inherit;
    font-size: 11px;
    white-space: nowrap;
    cursor: default;
  }

  .small:hover {
    background: var(--control-hover);
  }

  .toggles-row {
    align-items: start;
  }

  .toggles {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .toggles.block {
    gap: 9px;
    padding: 2px 0 6px;
  }

  .switch {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 11.5px;
    cursor: default;
  }

  .switch input {
    position: absolute;
    width: 1px;
    height: 1px;
    opacity: 0;
  }

  .switch .track {
    position: relative;
    flex: 0 0 30px;
    width: 30px;
    height: 17px;
    border-radius: 9px;
    background: var(--switch-off);
    transition: background 140ms ease;
  }

  .switch .track::after {
    content: "";
    position: absolute;
    top: 2px;
    left: 2px;
    width: 13px;
    height: 13px;
    border-radius: 50%;
    background: #fff;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25);
    transition: transform 140ms ease;
  }

  .switch input:checked + .track {
    background: var(--accent);
  }

  .switch input:checked + .track::after {
    transform: translateX(13px);
  }

  .switch input:focus-visible + .track {
    box-shadow: 0 0 0 3px var(--focus);
  }

  .switch.disabled {
    opacity: 0.5;
  }

  footer {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 11px 20px;
    border-top: 1px solid var(--border);
    background: var(--panel);
  }

  footer .button:first-of-type {
    margin-left: auto;
  }

  .button {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 31px;
    padding: 0 11px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--control);
    color: var(--text);
    font: inherit;
    font-size: 12px;
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

  @keyframes slide-in {
    from {
      transform: translateX(24px);
      opacity: 0;
    }
  }

  @media (max-width: 720px) {
    nav {
      width: 120px;
      min-width: 120px;
    }

    .row {
      grid-template-columns: 120px minmax(0, 1fr);
    }
  }
</style>
