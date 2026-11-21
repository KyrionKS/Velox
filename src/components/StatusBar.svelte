<script lang="ts">
  import { Activity, Square } from "lucide-svelte";
  import { run } from "../lib/run.svelte";

  let { onOpenActivity }: { onOpenActivity: () => void } = $props();

  const percent = $derived(Math.round(run.overall * 100));
  const status = $derived(
    run.running
      ? run.status
      : run.files.length === 0
        ? "Ready — add files to begin"
        : run.status === "Idle"
          ? `${run.files.length} file${run.files.length === 1 ? "" : "s"} ready`
          : run.status,
  );
</script>

<footer class="status">
  <span class="dot" class:running={run.running} class:error={!!run.error}></span>
  <span class="status-text" title={status}>{status}</span>

  <div class="bar" role="progressbar" aria-valuenow={percent} aria-valuemin="0" aria-valuemax="100">
    <div style={`width:${percent}%`}></div>
  </div>

  <span class="count">
    {#if run.files.length > 0}
      {run.doneCount}/{run.files.length}
    {:else}
      0
    {/if}
  </span>
  <span class="percent">{percent}%</span>
  <span class="elapsed">{run.elapsed}</span>

  {#if run.running}
    <button class="stop" onclick={() => run.stop()} title="Stop after the current pass">
      <Square size={12} /> Stop
    </button>
  {/if}

  <button class="activity-link" onclick={onOpenActivity}>
    <Activity size={13} /> Activity
  </button>
</footer>

<style>
  .status {
    display: flex;
    align-items: center;
    gap: 10px;
    height: 42px;
    min-height: 42px;
    padding: 0 16px;
    border-top: 1px solid var(--border);
    background: var(--statusbar);
    backdrop-filter: blur(24px);
    font-size: 10.5px;
    user-select: none;
  }

  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--muted);
    opacity: 0.55;
  }

  .dot.running {
    background: var(--accent);
    opacity: 1;
    animation: pulse 1.4s ease-in-out infinite;
  }

  .dot.error {
    background: var(--red);
    opacity: 1;
  }

  .status-text {
    overflow: hidden;
    min-width: 120px;
    max-width: 38%;
    color: var(--text);
    font-weight: 500;
    text-overflow: ellipsis;
    white-space: nowrap;
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
    transition: width 220ms ease;
  }

  .count,
  .percent,
  .elapsed {
    color: var(--muted);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .count {
    min-width: 40px;
    text-align: right;
  }

  .percent {
    width: 34px;
    text-align: right;
  }

  .elapsed {
    width: 34px;
    text-align: right;
  }

  .stop {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    height: 24px;
    padding: 0 8px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: transparent;
    color: var(--muted);
    font: inherit;
    font-size: 10px;
    cursor: default;
  }

  .stop:hover {
    border-color: var(--red);
    color: var(--red);
  }

  .activity-link {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    height: 24px;
    padding: 0 7px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: var(--muted);
    font: inherit;
    font-size: 10px;
    cursor: default;
  }

  .activity-link:hover {
    background: var(--control);
    color: var(--text);
  }

  @keyframes pulse {
    50% {
      opacity: 0.35;
    }
  }
</style>
