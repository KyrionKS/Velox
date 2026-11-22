import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/svelte";
import About from "./About.svelte";
import { demoAppInfo } from "../lib/demo";

describe("About", () => {
  it("shows the app, tool versions and config button", () => {
    render(About, {
      info: demoAppInfo,
      error: null,
      onClose: vi.fn(),
      onOpenPath: vi.fn(),
      onRetry: vi.fn(),
    });

    expect(screen.getByRole("heading", { name: "Velox" })).toBeInTheDocument();
    expect(screen.getByText("Tauri")).toBeInTheDocument();
    expect(screen.getByText("ffmpeg")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Open config/ })).toBeInTheDocument();
  });

  it("opens the config and project page through the callback", () => {
    const onOpenPath = vi.fn();
    render(About, {
      info: demoAppInfo,
      error: null,
      onClose: vi.fn(),
      onOpenPath,
      onRetry: vi.fn(),
    });

    screen.getByRole("button", { name: /Open config/ }).click();
    expect(onOpenPath).toHaveBeenCalledWith(demoAppInfo.config_path);

    screen.getByRole("button", { name: /Project page/ }).click();
    expect(onOpenPath).toHaveBeenCalledWith("https://github.com/KyrionKS/Velox");
  });
});
