import type { Config, FileRow } from "./types";

export interface Estimate {
  bytes: number;
  source: number;
  counted: number;
  total: number;
}

/**
 * Rough output-size estimate for the current settings.  VideoToolbox constant
 * quality cannot be predicted, so the caller should show a qualitative label
 * instead when this returns null.
 */
export function estimateOutput(files: FileRow[], config: Config): Estimate | null {
  const hevc = config.plugins.hevc_gpu;
  if (hevc.bitrate_mode === "quality") return null;

  let bytes = 0;
  let counted = 0;
  let source = 0;

  for (const file of files) {
    const info = file.info;
    if (!info || !info.is_video) continue;
    counted += 1;
    source += info.size;

    if (hevc.bitrate_mode === "size") {
      bytes += hevc.target_size_mb * 1024 * 1024;
      continue;
    }

    const width = info.width || 1920;
    const height = info.height || 1080;
    const fps = info.fps || 24;
    const duration = info.duration || 0;
    if (duration <= 0) continue;

    let videoKbps = (hevc.bits_per_pixel * width * height * fps) / 1000;
    const sourceKbps =
      info.video_kbps ??
      Math.max(0, (info.size * 8) / 1000 / duration - (info.audio_kbps || 0));
    if (sourceKbps > 0) videoKbps = Math.min(videoKbps, sourceKbps);
    videoKbps = Math.max(hevc.min_video_kbps, Math.min(hevc.max_video_kbps, videoKbps));

    const totalKbps = videoKbps + (info.audio_kbps || 256);
    bytes += ((totalKbps * 1000) / 8) * duration * (1 + hevc.size_overhead_percent / 100);
  }

  if (counted === 0) return null;
  return { bytes, source, counted, total: files.filter((file) => file.info?.is_video !== false).length };
}
