import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/svelte";
import Activity from "./Activity.svelte";
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

describe("Activity", () => {
  beforeEach(() => {
    resetRun();
  });

  it("shows passes, log and encode pass labels", () => {
    run.loadDemoActivity();

    render(Activity, { onOpenReport: vi.fn() });

    expect(screen.getByRole("heading", { name: "Passes" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Log" })).toBeInTheDocument();
    expect(screen.getAllByText(/Encode Video/).length).toBeGreaterThan(0);
  });

  it("marks planned passes and shows the preview banner", () => {
    run.loadDemoPreview();
    render(Activity, { onOpenReport: vi.fn() });

    expect(screen.getByText(/Preview only/)).toBeInTheDocument();
    expect(screen.getAllByText("planned").length).toBeGreaterThan(0);
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });
});
