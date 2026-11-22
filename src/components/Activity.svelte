<script lang="ts">
  import { Activity, CheckCircle2, Clock, FileText, FileVideo, Gauge, Trash2, Zap } from "lucide-svelte";
  import { baseName, fmtBytes, fmtDuration, fmtSaved } from "../lib/format";
  import { run } from "../lib/run.svelte";

  let { onOpenReport }: { onOpenReport: (path: string) => void } = $props();

  let logEl = $state<HTMLPreElement | undefined>();

  const saved = $derived(run.totalOriginal - run.totalFinal);
  const percent = $derived(
    run.totalOriginal > 0 ? (saved / run.totalOriginal) * 100 : Math.round(run.overall * 100),
  );
  const failed = $derived(run.reports.filter((report) => report.error && !report.cancelled).length);

  $effect(() => {
    run.logLines.length;
    if (logEl) logEl.scrollTop = logEl.scrollHeight;
  });
</script>

<section class="activity">
  <header class="activity-head">
    <div>
      <h1>Activity</h1>
      <p>Live progress, ffmpeg passes and the space you saved.</p>
    </div>
    {#if run.logLines.length > 0}
      <button class="ghost-button" onclick={() => run.clearLog()}>
        <Trash2 size={14} /> Clear log
      </button>
    {/if}
  </header>

  {#if run.previewMode}
    <div class="preview-banner">
      <span class="preview-dot"></span>
      Preview only — nothing was written. Planned passes are shown with “—” as output size.
    </div>
  {/if}

  <div class="layout">
    <div class="left">
      {#if run.totalOriginal > 0 || run.running || run.reports.length > 0}
        <div class="summary-card" class:running={run.running}>
          <div class="summary-top">
            <div>
              <span class="eyebrow">{run.running ? "In progress" : "Last run"}</span>
              <div class="big-number">
                {#if run.totalOriginal > 0}
                  {fmtBytes(Math.abs(saved))}
                {:else}
                  {run.doneCount}/{run.files.length}
                {/if}
              </div>
              <span class="big-label">
                {#if run.totalOriginal > 0}
                  {percent.toFixed(1)}% smaller · {fmtBytes(run.totalOriginal)} → {fmtBytes(run.totalFinal)}
                {:else if run.previewMode}
                  {run.files.length} file{run.files.length === 1 ? "" : "s"} checked — no files written
                {:else}
                  files finished
                {/if}
              </span>
            </div>
            <div class="ring" style={`--p:${Math.max(0, Math.min(100, percent))}`}>
              <span>{Math.round(percent)}%</span>
            </div>
          </div>

          <div class="stats">
            <div class="stat">
              <FileVideo size={14} />
              <span>{run.files.length} files</span>
            </div>
            <div class="stat">
              <Zap size={14} />
              <span>{run.passes.length} passes</span>
            </div>
            <div class="stat">
              <Clock size={14} />
              <span>{run.elapsed}</span>
            </div>
            <div class="stat">
              <CheckCircle2 size={14} />
              <span>
                {run.previewMode
                  ? `${run.wouldChangeCount} would change`
                  : `${run.successCount} done`}{run.failedCount > 0 ? ` · ${run.failedCount} failed` : ""}
              </span>
            </div>
          </div>
        </div>
      {/if}

      {#if run.passes.length > 0}
        <section class="panel">
          <h2>Passes</h2>
          <table>
            <thead>
              <tr>
                <th>File</th>
                <th>Step</th>
                <th class="num">In</th>
                <th class="num">Out</th>
                <th class="num">Saved</th>
              </tr>
            </thead>
            <tbody>
              {#each run.passes as pass, index (index)}
                <tr>
                  <td class="file" title={pass.file}>{pass.file}</td>
                  <td>
                    {pass.label}
                    <span class="kind">{pass.planned ? "planned" : pass.kind}</span>
                  </td>
                  <td class="num">{fmtBytes(pass.input)}</td>
                  <td class="num">{pass.planned ? "—" : fmtBytes(pass.output)}</td>
                  <td class="num saved" class:loss={!pass.planned && pass.saved < 0}>
                    {pass.planned ? "—" : fmtSaved(pass.saved)}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </section>
      {/if}

      {#if run.reports.some((report) => report.report_path)}
        <section class="panel">
          <h2>Reports</h2>
          <div class="reports">
            {#each run.reports.filter((report) => report.report_path) as report}
              <button class="report-row" onclick={() => onOpenReport(report.report_path!)}>
                <FileText size={14} />
                <span class="report-name">{baseName(report.final_path ?? report.source)}</span>
                <span class="report-kind">
                  {report.cancelled ? "cancelled" : report.error ? "error" : "processed"}
                </span>
              </button>
            {/each}
          </div>
        </section>
      {/if}

      {#if run.passes.length === 0 && !run.running && run.reports.length === 0}
        <div class="empty">
          <div class="empty-icon"><Activity size={28} /></div>
          <h2>No activity yet</h2>
          <p>Add files in the Library and start a run. Progress and results appear here.</p>
        </div>
      {/if}
    </div>

    <section class="panel log-panel">
      <div class="panel-head">
        <h2>Log</h2>
        <span class="log-status">
          {#if run.running}
            <span class="live"></span> live
          {:else}
            {run.logLines.length} lines
          {/if}
        </span>
      </div>
      {#if run.logLines.length === 0}
        <div class="log-empty">The ffmpeg command lines and step notes appear here.</div>
      {:else}
        <pre bind:this={logEl}>{run.logLines.join("\n")}</pre>
      {/if}
    </section>
  </div>
</section>

<style>
  .activity {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    padding: 22px 24px 18px;
  }

  .activity-head {
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

  .activity-head p {
    margin: 3px 0 0;
    color: var(--muted);
    font-size: 12px;
  }

  .ghost-button {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 30px;
    padding: 0 10px;
    border: 1px solid var(--border);
    border-radius: 9px;
    background: var(--control);
    color: var(--text);
    font: inherit;
    font-size: 11.5px;
    cursor: default;
  }

  .ghost-button:hover {
    background: var(--control-hover);
  }

  .preview-banner {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 12px;
    padding: 9px 12px;
    border: 1px solid var(--accent);
    border-radius: 9px;
    background: var(--accent-soft);
    color: var(--accent-text);
    font-size: 11.5px;
  }

  .preview-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--accent);
  }

  .layout {
    display: grid;
    grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
    gap: 14px;
    flex: 1;
    min-height: 0;
  }

  .left {
    display: flex;
    flex-direction: column;
    gap: 14px;
    min-height: 0;
    overflow: auto;
  }

  .summary-card {
    padding: 18px;
    border: 1px solid var(--border);
    border-radius: 16px;
    background: var(--panel);
    box-shadow: var(--shadow-soft);
  }

  .summary-card.running {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-soft), var(--shadow-soft);
  }

  .summary-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 18px;
  }

  .eyebrow {
    color: var(--muted);
    font-size: 9.5px;
    font-weight: 650;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .big-number {
    margin-top: 3px;
    font-size: 32px;
    font-weight: 750;
    letter-spacing: -0.035em;
    font-variant-numeric: tabular-nums;
    background: var(--accent-gradient);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
  }

  .big-label {
    display: block;
    margin-top: 1px;
    color: var(--muted);
    font-size: 11px;
  }

  .ring {
    --value: calc(var(--p) * 3.6deg);
    display: grid;
    flex: 0 0 62px;
    place-items: center;
    width: 62px;
    height: 62px;
    border-radius: 50%;
    background: conic-gradient(var(--accent) var(--value), var(--bar-bg) 0);
  }

  .ring::before {
    content: "";
    grid-area: 1 / 1;
    width: 48px;
    height: 48px;
    border-radius: 50%;
    background: var(--panel);
  }

  .ring span {
    z-index: 1;
    font-size: 11px;
    font-weight: 650;
    font-variant-numeric: tabular-nums;
  }

  .stats {
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    margin-top: 14px;
    padding-top: 12px;
    border-top: 1px solid var(--border-soft);
  }

  .stat {
    display: flex;
    align-items: center;
    gap: 5px;
    color: var(--muted);
    font-size: 10.5px;
  }

  .panel {
    min-height: 0;
    padding: 14px 16px;
    border: 1px solid var(--border);
    border-radius: 14px;
    background: var(--panel);
  }

  .panel h2 {
    margin: 0 0 10px;
    font-size: 11px;
    font-weight: 650;
    color: var(--muted);
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  table {
    width: 100%;
    border-collapse: collapse;
  }

  th {
    padding: 5px 8px;
    border-bottom: 1px solid var(--border-soft);
    color: var(--muted);
    font-size: 9.5px;
    font-weight: 550;
    text-align: left;
  }

  td {
    padding: 6px 8px;
    border-bottom: 1px solid var(--border-soft);
    font-size: 10.5px;
  }

  tr:last-child td {
    border-bottom: 0;
  }

  .file {
    max-width: 130px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .saved {
    color: var(--green);
    font-weight: 600;
  }

  .saved.loss {
    color: var(--red);
  }

  .kind {
    margin-left: 5px;
    color: var(--muted);
    font-size: 8.5px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }

  .reports {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .report-row {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    padding: 6px 8px;
    border: 0;
    border-radius: 7px;
    background: transparent;
    color: var(--text);
    font: inherit;
    font-size: 11px;
    text-align: left;
    cursor: default;
  }

  .report-row:hover {
    background: var(--control-hover);
  }

  .report-name {
    overflow: hidden;
    flex: 1;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .report-kind {
    color: var(--muted);
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .empty {
    display: flex;
    flex: 1;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 7px;
    min-height: 240px;
    border: 1.5px dashed var(--border-strong);
    border-radius: 16px;
    color: var(--muted);
    text-align: center;
  }

  .empty-icon {
    display: grid;
    place-items: center;
    width: 54px;
    height: 54px;
    border-radius: 15px;
    background: var(--control);
    color: var(--accent);
  }

  .empty h2 {
    margin: 0;
    color: var(--text);
    font-size: 14px;
  }

  .empty p {
    max-width: 300px;
    margin: 0;
    font-size: 11.5px;
    line-height: 1.5;
  }

  .log-panel {
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .panel-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .panel-head h2 {
    margin-bottom: 0;
  }

  .log-status {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    color: var(--muted);
    font-size: 9.5px;
  }

  .live {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--green);
    animation: pulse 1.5s ease-in-out infinite;
  }

  .log-empty {
    display: grid;
    flex: 1;
    place-items: center;
    padding: 20px;
    color: var(--muted);
    font-size: 11px;
    text-align: center;
  }

  pre {
    flex: 1;
    min-height: 0;
    margin: 10px 0 0;
    overflow: auto;
    padding: 10px;
    border: 1px solid var(--border-soft);
    border-radius: 9px;
    background: var(--log-bg);
    color: var(--muted);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 10px;
    line-height: 1.55;
    white-space: pre-wrap;
  }

  @keyframes pulse {
    50% {
      opacity: 0.35;
    }
  }

  @media (max-width: 980px) {
    .layout {
      grid-template-columns: 1fr;
      overflow: auto;
    }

    .log-panel {
      min-height: 280px;
    }
  }
</style>
