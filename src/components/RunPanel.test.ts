import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/svelte";
import RunPanel from "./RunPanel.svelte";
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

describe("RunPanel", () => {
  beforeEach(() => {
    resetRun();
  });

  it("shows the quality presets and skip option", () => {
    render(RunPanel, {
      config: structuredClone(demoConfig),
      estimate: null,
      onStart: vi.fn(),
      onSettings: vi.fn(),
    });

    expect(screen.getByText("Choose a quality")).toBeInTheDocument();
    for (const label of ["Auto", "Compact", "Balanced", "Archive"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(
      screen.getByRole("checkbox", { name: /Skip already processed files/ }),
    ).toBeInTheDocument();
  });

  it("toggles the skip-processed setting", async () => {
    const config = structuredClone(demoConfig);
    render(RunPanel, {
      config,
      estimate: null,
      onStart: vi.fn(),
      onSettings: vi.fn(),
    });

    const toggle = screen.getByRole("checkbox", {
      name: /Skip already processed files/,
    }) as HTMLInputElement;
    expect(toggle.checked).toBe(true);
    toggle.click();
    expect(config.general.skip_processed).toBe(false);
  });
});
