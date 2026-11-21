import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { probeFiles, startRun, stopRun } from "./api";
import { baseName, fmtBytes, fmtSaved } from "./format";
import type { FileInfo, FileItem, FileRow, PassRow, PipelineEvent, RunReport } from "./types";

function newRow(item: FileItem): FileRow {
  return {
    path: item.path,
    name: item.name || baseName(item.path),
    size: item.size,
    analyzing: false,
    progress: 0,
    step: "Queued",
    stepLabel: "Queued",
    result: "",
    status: "queued",
    passStartedAt: null,
  };
}

class RunStore {
  files = $state<FileRow[]>([]);
  running = $state(false);
  overall = $state(0);
  status = $state("Idle");
  logLines = $state<string[]>([]);
  logVisible = $state(false);
  passes = $state<PassRow[]>([]);
  reports = $state<RunReport[]>([]);
  totalOriginal = $state(0);
  totalFinal = $state(0);
  startedAt = $state<number | null>(null);
  now = $state(Date.now());
  error = $state<string | null>(null);
  finishedSeconds = $state(0);

  private unlisten: UnlistenFn[] = [];
  private timer: number | null = null;
  private runHadError = false;

  async init(): Promise<void> {
    if (this.unlisten.length > 0) return;
    this.unlisten.push(
      await listen<PipelineEvent>("velox://event", (event) => {
        this.handle(event.payload);
      }),
    );
  }

  get elapsed(): string {
    const seconds = this.startedAt
      ? Math.max(0, (this.now - this.startedAt) / 1000)
      : this.finishedSeconds;
    const total = Math.floor(seconds);
    const minutes = Math.floor(total / 60);
    return `${minutes}:${String(total % 60).padStart(2, "0")}`;
  }

  get sourceBytes(): number {
    return this.files.reduce((total, file) => total + (file.info?.size ?? file.size), 0);
  }

  get analyzingCount(): number {
    return this.files.filter((file) => file.analyzing).length;
  }

  get videoCount(): number {
    return this.files.filter((file) => file.info?.is_video !== false).length;
  }

  get doneCount(): number {
    return this.files.filter((file) => file.status !== "queued" && file.status !== "running")
      .length;
  }

  get successCount(): number {
    return this.files.filter((file) => file.status === "done").length;
  }

  get failedCount(): number {
    return this.files.filter((file) => file.status === "error").length;
  }

  addFiles(items: FileItem[]): void {
    const existing = new Set(this.files.map((file) => file.path));
    for (const item of items) {
      if (!existing.has(item.path)) {
        this.files.push(newRow(item));
        existing.add(item.path);
      }
    }
    this.error = null;
    void this.analyze();
  }

  async analyze(): Promise<void> {
    const pending = this.files.filter((file) => !file.info && !file.analyzing);
    if (pending.length === 0) return;
    for (const file of pending) file.analyzing = true;
    try {
      const infos = await probeFiles(pending.map((file) => file.path));
      const byPath = new Map(infos.map((info) => [info.path, info]));
      for (const file of this.files) {
        if (!file.analyzing) continue;
        const info = byPath.get(file.path);
        if (info) {
          file.info = info;
          file.size = info.size;
          file.name = info.name;
        }
        file.analyzing = false;
      }
    } catch (error) {
      for (const file of pending) file.analyzing = false;
      this.error = `Could not analyse files: ${String(error)}`;
    }
  }

  remove(path: string): void {
    if (this.running) return;
    this.files = this.files.filter((file) => file.path !== path);
  }

  clear(): void {
    if (this.running) return;
    this.files = [];
    this.passes = [];
    this.reports = [];
    this.totalOriginal = 0;
    this.totalFinal = 0;
    this.overall = 0;
    this.status = "Idle";
    this.logLines = [];
    this.error = null;
  }

  toggleLog(): void {
    this.logVisible = !this.logVisible;
  }

  clearLog(): void {
    this.logLines = [];
  }

  log(message: string): void {
    this.logLines.push(message);
    if (this.logLines.length > 5000) {
      this.logLines.splice(0, this.logLines.length - 5000);
    }
  }

  loadDemo(): void {
    const samples: FileInfo[] = [
      {
        path: "/Volumes/Media/Movies/Blade Runner 2049 (2017)/Blade.Runner.2049.2017.2160p.mkv",
        name: "Blade.Runner.2049.2017.2160p.mkv",
        size: 48_200_000_000,
        container: "mkv",
        duration: 9780,
        is_video: true,
        video_codec: "hevc",
        width: 3840,
        height: 2160,
        fps: 23.976,
        video_kbps: 36_000,
        audio_count: 3,
        subtitle_count: 4,
        audio_kbps: 3200,
        audio_languages: ["eng", "deu"],
        subtitle_languages: ["eng", "deu"],
        error: null,
      },
      {
        path: "/Volumes/Media/Series/Andor/S01/Andor.S01E04.1080p.mkv",
        name: "Andor.S01E04.1080p.mkv",
        size: 6_400_000_000,
        container: "mkv",
        duration: 3120,
        is_video: true,
        video_codec: "h264",
        width: 1920,
        height: 1080,
        fps: 23.976,
        video_kbps: 14_500,
        audio_count: 2,
        subtitle_count: 2,
        audio_kbps: 960,
        audio_languages: ["eng"],
        subtitle_languages: ["eng", "deu"],
        error: null,
      },
      {
        path: "/Volumes/Media/Clips/Drone Footage.mov",
        name: "Drone Footage.mov",
        size: 2_100_000_000,
        container: "mov",
        duration: 420,
        is_video: true,
        video_codec: "prores",
        width: 3840,
        height: 2160,
        fps: 30,
        video_kbps: 38_000,
        audio_count: 1,
        subtitle_count: 0,
        audio_kbps: 256,
        audio_languages: ["und"],
        subtitle_languages: [],
        error: null,
      },
    ];
    this.files = samples.map((info) => ({
      ...newRow({ path: info.path, name: info.name, size: info.size }),
      info,
    }));
  }

  loadDemoActivity(): void {
    this.loadDemo();
    const rows = this.files;
    rows[0].status = "done";
    rows[0].step = "Done";
    rows[0].result = "−18.4 GB";
    rows[0].progress = 1;
    rows[1].status = "done";
    rows[1].step = "Done";
    rows[1].result = "−2.7 GB";
    rows[1].progress = 1;
    rows[2].status = "error";
    rows[2].step = "Output verification failed";
    rows[2].result = "Failed";
    this.passes = [
      { file: rows[0].name, label: "Clean Audio Tracks + Order Streams", kind: "remux", input: 48_200_000_000, output: 47_900_000_000, saved: 300_000_000 },
      { file: rows[0].name, label: "Encode Video", kind: "transcode", input: 47_900_000_000, output: 29_500_000_000, saved: 18_400_000_000 },
      { file: rows[1].name, label: "Clean Subtitle Tracks + Standardise Audio Codec", kind: "remux", input: 6_400_000_000, output: 6_100_000_000, saved: 300_000_000 },
      { file: rows[1].name, label: "Encode Video", kind: "transcode", input: 6_100_000_000, output: 3_400_000_000, saved: 2_700_000_000 },
    ];
    this.totalOriginal = 48_200_000_000 + 6_400_000_000;
    this.totalFinal = 29_500_000_000 + 3_400_000_000;
    this.overall = 1;
    this.status = "Finished.";
    this.finishedSeconds = 514;
    this.logLines = [
      "Velox 0.1.0 - pipeline: Move Video First → Clean Audio Tracks → Clean Subtitle Tracks → Standardise Audio Codec → Encode Video → Order Streams → Check File Size",
      "Processing Blade.Runner.2049.2017.2160p.mkv (container=mkv size=48200.0MB duration=9780.0s streams=video:1,audio:3,subtitle:4)",
      "[Move Video First]",
      "   File has video in first stream",
      "[Clean Audio Tracks]",
      "   Audio stream 2 has unwanted language tag deu, removing.",
      "[Standardise Audio Codec]",
      "   Audio stream 1 is eac3, transcoding to aac (6 ch, 640k)",
      "   -> ffmpeg (merged remux): /opt/homebrew/bin/ffmpeg -y -hide_banner -loglevel error -i source.mkv -map 0:0 -map 0:1 -map 0:3 -c copy -c:a:0 aac -ac:a:0 6 -b:a:0 640k -max_muxing_queue_size 9999 out.mkv",
      "[Encode Video]",
      "   Using configured encoder: hevc_videotoolbox",
      "   Current video bitrate ~36000kbps (ffprobe stream tags)",
      "   Quality mode: constant quality 50 (VideoToolbox -q:v)",
      "   10-bit encode enabled (Main10, user enabled in config).",
      "   File is not in hevc. Transcoding with hevc_videotoolbox.",
    ];
  }

  async start(dryRun = false): Promise<void> {
    if (this.running || this.files.length === 0) return;
    this.running = true;
    this.error = null;
    this.runHadError = false;
    this.passes = [];
    this.reports = [];
    this.totalOriginal = 0;
    this.totalFinal = 0;
    this.overall = 0;
    this.status = dryRun ? "Starting dry run…" : "Starting…";
    this.startedAt = Date.now();
    this.now = Date.now();
    this.finishedSeconds = 0;
    this.logLines = [];
    for (const file of this.files) {
      file.progress = 0;
      file.step = "Queued";
      file.stepLabel = "Queued";
      file.result = "";
      file.status = "queued";
      file.passStartedAt = null;
    }
    this.startTimer();
    try {
      await startRun(
        this.files.map((file) => file.path),
        dryRun,
      );
    } catch (error) {
      this.running = false;
      this.status = "Could not start";
      this.error = String(error);
      this.log(`✗ ${String(error)}`);
      this.stopTimer();
    }
  }

  async stop(): Promise<void> {
    if (!this.running) return;
    try {
      await stopRun();
      this.log("Stopping after the current pass…");
      this.status = "Stopping…";
    } catch (error) {
      this.log(`✗ ${String(error)}`);
    }
  }

  private startTimer(): void {
    this.stopTimer();
    this.timer = window.setInterval(() => {
      this.now = Date.now();
    }, 1000);
  }

  private stopTimer(): void {
    if (this.timer !== null) {
      window.clearInterval(this.timer);
      this.timer = null;
    }
  }

  private updateOverall(): void {
    if (this.files.length === 0) {
      this.overall = 0;
      return;
    }
    const sum = this.files.reduce((total, file) => total + Math.max(0, Math.min(1, file.progress)), 0);
    this.overall = sum / this.files.length;
  }

  private find(path: string): FileRow | undefined {
    return this.files.find((file) => file.path === path);
  }

  private handle(event: PipelineEvent): void {
    switch (event.type) {
      case "run_started": {
        this.status = this.status.startsWith("Starting dry") ? "Dry run…" : "Processing…";
        this.log(`Starting ${event.total} file(s)…`);
        break;
      }
      case "file_started": {
        const file = this.find(event.path);
        if (file) {
          file.status = "running";
          file.step = "Analysing streams…";
          file.stepLabel = "Analysing streams…";
          file.progress = 0;
          file.passStartedAt = null;
        }
        this.log(`— ${baseName(event.path)}: ${event.summary}`);
        this.updateOverall();
        break;
      }
      case "step_started": {
        const file = this.find(event.path);
        if (file) {
          file.step = event.name;
          file.stepLabel = event.name;
          file.passStartedAt = null;
        }
        this.updateStatusLine();
        break;
      }
      case "step_note": {
        this.log(`   ${event.message}`);
        break;
      }
      case "pass_started": {
        const file = this.find(event.path);
        if (file) {
          file.step = event.description;
          file.stepLabel = event.description;
          file.passStartedAt = Date.now();
          file.progress = 0;
        }
        this.log(`▶ ${baseName(event.path)}: ${event.description}`);
        this.updateStatusLine();
        break;
      }
      case "progress": {
        const file = this.find(event.path);
        if (!file) break;
        file.progress = Math.max(0, Math.min(1, event.fraction));
        const percent = Math.round(file.progress * 100);
        if (file.passStartedAt && file.progress > 0.02) {
          const elapsed = (Date.now() - file.passStartedAt) / 1000;
          const remaining = (elapsed * (1 - file.progress)) / file.progress;
          file.step = `${file.stepLabel}  ·  ${percent}%  ·  ~${Math.round(remaining)}s left`;
        } else {
          file.step = `${file.stepLabel}  ·  ${percent}%`;
        }
        this.updateOverall();
        this.updateStatusLine();
        break;
      }
      case "pass_finished": {
        const file = baseName(event.path);
        const saved = event.pass.input_bytes - event.pass.output_bytes;
        this.passes.push({
          file,
          label: event.pass.steps.join(" + "),
          kind: event.pass.kind,
          input: event.pass.input_bytes,
          output: event.pass.output_bytes,
          saved,
        });
        this.log(
          `   ✓ ${file}: ${event.pass.steps.join(" + ")}: ${fmtBytes(event.pass.input_bytes)} → ${fmtBytes(event.pass.output_bytes)} (${fmtSaved(saved)})`,
        );
        break;
      }
      case "file_finished": {
        this.finishFile(event.report);
        break;
      }
      case "run_finished": {
        this.running = false;
        this.stopTimer();
        this.overall = this.files.length > 0 && this.files.every((file) => file.status !== "error")
          ? 1
          : this.overall;
        const failed = event.reports.filter((report) => report.error && !report.cancelled).length;
        this.status = this.runHadError
          ? "Finished with an error — see log."
          : failed > 0
            ? `Finished with ${failed} error(s).`
            : "Finished.";
        this.log(`Done. ${event.reports.length} file(s), ${failed} failed.`);
        if (this.startedAt) this.finishedSeconds = (Date.now() - this.startedAt) / 1000;
        this.startedAt = null;
        this.updateOverallSummary();
        break;
      }
      case "log": {
        this.log(event.message);
        break;
      }
      case "error": {
        this.runHadError = true;
        this.error = event.message;
        this.log(`✗ ${event.message}`);
        break;
      }
    }
  }

  private finishFile(report: RunReport): void {
    const file = this.find(report.source);
    if (file) {
      file.progress = report.error || report.cancelled ? file.progress : 1;
      if (report.cancelled) {
        file.status = "stopped";
        file.step = "Stopped";
        file.result = "stopped";
      } else if (report.error) {
        file.status = "error";
        file.step = report.error;
        file.result = "error";
      } else if (report.skipped) {
        file.status = "skipped";
        file.step = "Skipped";
        file.result = "skipped";
      } else if (report.modified) {
        file.status = "done";
        file.step = "Done";
        file.result = fmtSaved(report.original_bytes - report.final_bytes);
      } else {
        file.status = "unchanged";
        file.step = "Nothing to change";
        file.result = "unchanged";
      }
      file.passStartedAt = null;
    }
    if (report.modified) {
      this.totalOriginal += report.original_bytes;
      this.totalFinal += report.final_bytes;
    }
    this.reports.push(report);
    this.updateOverall();
    this.updateOverallSummary();
    this.updateStatusLine();
  }

  private updateOverallSummary(): void {
    if (this.totalOriginal <= 0) return;
    const saved = this.totalOriginal - this.totalFinal;
    const percent = (saved / this.totalOriginal) * 100;
    this.log(
      `Total: ${fmtBytes(this.totalOriginal)} → ${fmtBytes(this.totalFinal)} (saved ${fmtBytes(Math.abs(saved))}, ${percent.toFixed(1)}%)`,
    );
  }

  private updateStatusLine(): void {
    const active = this.files.filter((file) => file.status === "running");
    if (active.length === 1) {
      this.status = `${active[0].name} · ${active[0].step}`;
    } else if (active.length > 1) {
      this.status = `Processing ${active.length} files…`;
    }
  }
}

export const run = new RunStore();
