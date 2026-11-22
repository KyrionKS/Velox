import { beforeEach, describe, expect, it } from "vitest";
import { demoConfig } from "./demo";
import { estimateOutput } from "./estimate";
import type { Config, FileInfo, FileRow } from "./types";

const MIB = 1024 * 1024;

function makeConfig(): Config {
  return structuredClone(demoConfig);
}

function makeInfo(overrides: Partial<FileInfo> = {}): FileInfo {
  return {
    path: "/media/film.mkv",
    name: "film.mkv",
    size: 4_000_000_000,
    container: "mkv",
    duration: 3600,
    is_video: true,
    video_codec: "h264",
    width: 1920,
    height: 1080,
    fps: 24,
    video_kbps: 8000,
    audio_count: 1,
    subtitle_count: 0,
    audio_kbps: 256,
    audio_languages: ["eng"],
    subtitle_languages: [],
    already_processed: false,
    report_path: null,
    error: null,
    ...overrides,
  };
}

function makeRow(info: FileInfo | undefined, path = "/media/film.mkv"): FileRow {
  return {
    path,
    name: path.split("/").pop() ?? path,
    size: info?.size ?? 0,
    info,
    analyzing: false,
    progress: 0,
    step: "Queued",
    stepLabel: "Queued",
    result: "",
    status: "queued",
    reportPath: null,
    passStartedAt: null,
  };
}

describe("estimateOutput", () => {
  let config: Config;

  beforeEach(() => {
    config = makeConfig();
  });

  it("returns null in quality mode", () => {
    config.plugins.hevc_gpu.bitrate_mode = "quality";
    expect(estimateOutput([makeRow(makeInfo())], config)).toBeNull();
  });

  it("estimates a positive size in tier mode", () => {
    config.plugins.hevc_gpu.bitrate_mode = "tier";
    const estimate = estimateOutput([makeRow(makeInfo())], config);
    expect(estimate).not.toBeNull();
    expect(estimate?.bytes).toBeGreaterThan(0);
    expect(estimate?.counted).toBe(1);
    expect(estimate?.total).toBe(1);
  });

  it("counts only video files in tier mode", () => {
    config.plugins.hevc_gpu.bitrate_mode = "tier";
    const video = makeRow(makeInfo({ path: "/media/a.mkv" }), "/media/a.mkv");
    const audio = makeRow(
      makeInfo({ path: "/media/b.mka", is_video: false }),
      "/media/b.mka",
    );
    const estimate = estimateOutput([video, audio], config);
    expect(estimate?.counted).toBe(1);
    expect(estimate?.total).toBe(1);
  });

  it("uses the target size per video in size mode", () => {
    config.plugins.hevc_gpu.bitrate_mode = "size";
    config.plugins.hevc_gpu.target_size_mb = 1500;
    const estimate = estimateOutput(
      [
        makeRow(makeInfo({ path: "/media/a.mkv" }), "/media/a.mkv"),
        makeRow(makeInfo({ path: "/media/b.mkv" }), "/media/b.mkv"),
      ],
      config,
    );
    expect(estimate?.bytes).toBe(2 * 1500 * MIB);
    expect(estimate?.counted).toBe(2);
  });

  it("ignores non-video files in size mode", () => {
    config.plugins.hevc_gpu.bitrate_mode = "size";
    config.plugins.hevc_gpu.target_size_mb = 1500;
    const estimate = estimateOutput(
      [
        makeRow(makeInfo({ path: "/media/a.mkv" }), "/media/a.mkv"),
        makeRow(
          makeInfo({ path: "/media/b.srt", is_video: false }),
          "/media/b.srt",
        ),
      ],
      config,
    );
    expect(estimate?.bytes).toBe(1500 * MIB);
    expect(estimate?.counted).toBe(1);
  });

  it("excludes already processed files when skipping is enabled", () => {
    config.plugins.hevc_gpu.bitrate_mode = "size";
    config.general.skip_processed = true;
    const processed = makeRow(makeInfo({ already_processed: true }));
    expect(estimateOutput([processed], config)).toBeNull();

    config.general.skip_processed = false;
    expect(estimateOutput([processed], config)).not.toBeNull();
  });

  it("returns null without analysed file info", () => {
    config.plugins.hevc_gpu.bitrate_mode = "tier";
    expect(estimateOutput([makeRow(undefined)], config)).toBeNull();
  });

  it("returns null for an empty queue", () => {
    config.plugins.hevc_gpu.bitrate_mode = "size";
    expect(estimateOutput([], config)).toBeNull();
  });
});
