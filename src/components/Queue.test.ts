import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/svelte";
import Queue from "./Queue.svelte";
import { demoConfig } from "../lib/demo";
import { run } from "../lib/run.svelte";

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
  run.previewMode = false;
  run.error = null;
  run.startedAt = null;
}

function renderQueue(onOpenReport = vi.fn()) {
  return render(Queue, {
    config: demoConfig,
    onAddFiles: () => {},
    onAddFolder: () => {},
    onOpenReport,
  });
}

describe("Queue", () => {
  beforeEach(() => {
    resetRun();
  });

  it("shows an already processed file with an open report button", () => {
    run.loadDemoProcessed();
    renderQueue();

    expect(screen.getAllByText(/Already processed/).length).toBeGreaterThan(0);
    expect(screen.getAllByTitle("Open report")).toHaveLength(1);
  });

  it("opens the report of a processed file", async () => {
    run.loadDemoProcessed();
    const onOpenReport = vi.fn();
    renderQueue(onOpenReport);

    screen.getByTitle("Open report").click();

    expect(onOpenReport).toHaveBeenCalledWith(run.files[0].reportPath);
  });

  it("shows ready for newly loaded files", () => {
    run.loadDemo();
    renderQueue();

    expect(screen.getAllByText("Ready")).toHaveLength(3);
  });
});
