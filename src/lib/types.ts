export interface GeneralConfig {
  ffmpeg_path: string;
  ffprobe_path: string;
  keep_original: boolean;
  output_dir: string;
  temp_dir: string;
  concurrency: number;
  verify_output: boolean;
  verify_full_decode: boolean;
  write_report: boolean;
  process_non_video: boolean;
}

export interface ReorderStreamsConfig {
  enabled: boolean;
}

export interface CleanAudioConfig {
  enabled: boolean;
  language: string;
  commentary: boolean;
  tag_language: string;
  tag_title: boolean;
  default_audio: string;
}

export interface CleanSubsConfig {
  enabled: boolean;
  language: string;
  commentary: boolean;
  sdh: boolean;
  tag_language: string;
}

export interface StandardiseAudioConfig {
  enabled: boolean;
  audio_codec: string;
  audio_bitrate_kbps: number;
  audio_bitrate_min_per_channel_kbps: number;
  aac_encoder: string;
  reencode_target_codec: boolean;
}

export interface HevcGpuConfig {
  enabled: boolean;
  target_codec: string;
  encoder: string;
  bitrate_mode: string;
  bits_per_pixel: number;
  target_size_mb: number;
  size_overhead_percent: number;
  fallback_audio_kbps: number;
  quality: number;
  min_video_kbps: number;
  max_video_kbps: number;
  hardware_decode: boolean;
  try_use_gpu: boolean;
  allow_cpu_fallback: boolean;
  cpu_preset: string;
  container: string;
  bitrate_cutoff: number;
  enable_10bit: boolean;
  bframes_enabled: boolean;
  bframes_value: number;
  spatial_aq: boolean;
  force_conform: boolean;
  exclude_gpus: string;
  allowed_codecs: string;
}

export interface ReorderAllStreamsConfig {
  enabled: boolean;
  process_order: string;
  languages: string;
  channels: string;
  codecs: string;
  stream_types: string;
}

export interface FileSizeCheckConfig {
  enabled: boolean;
  upper_bound: number;
  lower_bound: number;
  min_size_mb: number;
  max_size_mb: number;
}

export interface PluginsConfig {
  reorder_streams: ReorderStreamsConfig;
  clean_audio: CleanAudioConfig;
  clean_subs: CleanSubsConfig;
  standardise_audio: StandardiseAudioConfig;
  hevc_gpu: HevcGpuConfig;
  reorder_all_streams: ReorderAllStreamsConfig;
  file_size_check: FileSizeCheckConfig;
}

export interface Config {
  general: GeneralConfig;
  pipeline: { order: string[] };
  plugins: PluginsConfig;
}

export interface PassEvent {
  steps: string[];
  kind: string;
  input_bytes: number;
  output_bytes: number;
  command: string[];
}

export interface RunReport {
  source: string;
  final_path: string | null;
  modified: boolean;
  skipped: boolean;
  cancelled: boolean;
  reason: string;
  error: string | null;
  original_bytes: number;
  final_bytes: number;
  elapsed: number;
  report_path: string | null;
  output_dir: string | null;
  kept_original: boolean;
  passes: PassEvent[];
}

export type PipelineEvent =
  | { type: "run_started"; total: number }
  | { type: "file_started"; path: string; summary: string }
  | { type: "step_started"; path: string; key: string; name: string }
  | { type: "step_note"; path: string; message: string }
  | { type: "pass_started"; path: string; description: string }
  | { type: "progress"; path: string; fraction: number }
  | { type: "pass_finished"; path: string; pass: PassEvent }
  | { type: "file_finished"; report: RunReport }
  | { type: "run_finished"; reports: RunReport[] }
  | { type: "log"; message: string }
  | { type: "error"; path: string | null; message: string };

export interface EncoderInfo {
  ffmpeg: string;
  ffprobe: string;
  video: string[];
  audio: string[];
}

export interface FileItem {
  path: string;
  name: string;
  size: number;
}

export interface FileInfo {
  path: string;
  name: string;
  size: number;
  container: string;
  duration: number;
  is_video: boolean;
  video_codec: string;
  width: number;
  height: number;
  fps: number;
  video_kbps: number | null;
  audio_count: number;
  subtitle_count: number;
  audio_kbps: number;
  audio_languages: string[];
  subtitle_languages: string[];
  error: string | null;
}

export type FileStatus =
  | "queued"
  | "running"
  | "done"
  | "error"
  | "stopped"
  | "skipped"
  | "unchanged";

export interface FileRow {
  path: string;
  name: string;
  size: number;
  info?: FileInfo;
  analyzing: boolean;
  progress: number;
  step: string;
  stepLabel: string;
  result: string;
  status: FileStatus;
  passStartedAt: number | null;
}

export interface PassRow {
  file: string;
  label: string;
  kind: string;
  input: number;
  output: number;
  saved: number;
}

export const FILE_FILTERS = [
  {
    name: "Video files",
    extensions: [
      "mkv",
      "mp4",
      "m4v",
      "avi",
      "mov",
      "ts",
      "m2ts",
      "mts",
      "webm",
      "wmv",
      "flv",
      "mpg",
      "mpeg",
      "vob",
    ],
  },
];

export const PLUGIN_LABELS: Record<string, string> = {
  reorder_streams: "Move Video First",
  clean_audio: "Clean Audio Tracks",
  clean_subs: "Clean Subtitle Tracks",
  standardise_audio: "Standardise Audio Codec",
  hevc_gpu: "Encode Video",
  reorder_all_streams: "Order Streams",
  file_size_check: "Check File Size",
};

export const QUALITY_PRESETS: Record<string, number> = {
  small: 0.028,
  balanced: 0.033,
  high: 0.045,
};

export function presetForBpp(value: number, tolerance = 0.0005): string {
  for (const [name, bpp] of Object.entries(QUALITY_PRESETS)) {
    if (Math.abs(bpp - value) <= tolerance) return name;
  }
  return "custom";
}
