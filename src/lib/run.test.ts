import { beforeEach, describe, expect, it } from "vitest";
import { fmtSaved } from "./format";
import { run } from "./run.svelte";
import type { RunReport } from "./types";

function resetRun(): void {
  run.running = false;
  run.files = [];
  run.passes = [];
  run.reports = [];
  run.totalOriginal = 0;
  run.totalFinal = 0;
  run.overall = 0;
  run.status = "Idle";
  run.logLines = [];
  run.logVisible = false;
  run.previewMode = false;
  run.error = null;
  run.startedAt = null;
  run.finishedSeconds = 0;
  run.now = Date.now();
}

function makeReport(source: string, overrides: Partial<RunReport> = {}): RunReport {
  return {
    source,
    final_path: null,
    modified: false,
    skipped: false,
    cancelled: false,
    reason: "",
    error: null,
    original_bytes: 0,
    final_bytes: 0,
    elapsed: 0,
    report_path: null,
    output_dir: null,
    kept_original: false,
    would_change: false,
    passes: [],
    ...overrides,
  };
}

describe("run store", () => {
  beforeEach(() => {
    resetRun();
  });

  it("loads the demo queue", () => {
    run.loadDemo();
    expect(run.files).toHaveLength(3);
    for (const file of run.files) {
      expect(file.info).toBeDefined();
    }
    expect(run.sourceBytes).toBeGreaterThan(0);
  });

  it("marks the demo file as already processed", () => {
    run.loadDemoProcessed();
    expect(run.files[0].status).toBe("processed");
    expect(run.files[0].reportPath).toBeTruthy();
  });

  it("sets up the demo preview with planned passes", () => {
    run.loadDemoPreview();
    expect(run.previewMode).toBe(true);
    expect(run.passes.length).toBeGreaterThan(0);
    expect(run.passes.every((pass) => pass.planned)).toBe(true);
  });

  it("sets up demo activity totals", () => {
    run.loadDemoActivity();
    expect(run.passes.length).toBeGreaterThan(0);
    expect(run.passes.reduce((total, pass) => total + pass.saved, 0)).toBeGreaterThan(0);
    expect(run.totalOriginal).toBeGreaterThan(0);
    expect(run.totalFinal).toBeGreaterThan(0);
    expect(run.status).toBe("Finished.");
  });

  it("handles the pipeline events for a modified file", () => {
    run.loadDemo();
    const path = run.files[0].path;

    run.handle({ type: "run_started", total: 2 });
    expect(run.status).toBe("Processing…");

    run.handle({ type: "file_started", path, summary: "video, 1 audio" });
    expect(run.files[0].status).toBe("running");

    run.handle({ type: "step_started", path, key: "clean_audio", name: "Clean Audio Tracks" });
    expect(run.files[0].step).toBe("Clean Audio Tracks");

    run.handle({ type: "progress", path, fraction: 0.5 });
    expect(run.files[0].progress).toBe(0.5);
    expect(run.overall).toBeGreaterThan(0);

    run.handle({
      type: "file_finished",
      report: makeReport(path, {
        modified: true,
        original_bytes: 4_000_000_000,
        final_bytes: 2_000_000_000,
      }),
    });

    expect(run.files[0].status).toBe("done");
    expect(run.files[0].progress).toBe(1);
    expect(run.files[0].result).toBe(fmtSaved(2_000_000_000));
    expect(run.totalOriginal).toBe(4_000_000_000);
    expect(run.overall).toBeGreaterThan(0);
  });

  it("records planned passes without savings", () => {
    run.loadDemo();
    const path = run.files[0].path;

    run.handle({
      type: "pass_finished",
      path,
      pass: {
        steps: ["Encode Video"],
        kind: "transcode",
        input_bytes: 4_000_000_000,
        output_bytes: 2_000_000_000,
        command: [],
        planned: true,
      },
    });

    expect(run.passes).toHaveLength(1);
    expect(run.passes[0].planned).toBe(true);
    expect(run.passes[0].saved).toBe(0);
    expect(run.logLines.some((line) => line.includes("planned"))).toBe(true);
  });

  it("marks dry-run changes as would-change", () => {
    run.loadDemo();
    const path = run.files[0].path;
    run.previewMode = true;

    run.handle({
      type: "file_finished",
      report: makeReport(path, { would_change: true }),
    });

    expect(run.files[0].status).toBe("would_change");
    expect(run.files[0].result).toBe("preview");
  });

  it("marks skipped already-processed files as processed", () => {
    run.loadDemo();
    const path = run.files[0].path;

    run.handle({
      type: "file_finished",
      report: makeReport(path, {
        skipped: true,
        reason: "already processed",
        report_path: "/media/reports/film-report.txt",
      }),
    });

    expect(run.files[0].status).toBe("processed");
    expect(run.files[0].step).toBe("Already processed");
    expect(run.files[0].reportPath).toBe("/media/reports/film-report.txt");
  });
});
