import { invoke } from "@tauri-apps/api/core";
import type { AppInfo, Config, EncoderInfo, FileInfo, FileItem } from "./types";

export function getConfig(): Promise<Config> {
  return invoke<Config>("get_config");
}

export function saveConfig(config: Config): Promise<void> {
  return invoke("save_config", { config });
}

export function configError(): Promise<string | null> {
  return invoke<string | null>("config_error");
}

export function reloadConfig(): Promise<Config> {
  return invoke<Config>("reload_config");
}

export function startLogStream(): Promise<void> {
  return invoke("start_log_stream");
}

export function openPath(path: string): Promise<void> {
  return invoke("open_path", { path });
}

export function appInfo(): Promise<AppInfo> {
  return invoke<AppInfo>("app_info");
}

export function configPath(): Promise<string> {
  return invoke<string>("config_path");
}

export function detectEncoders(): Promise<EncoderInfo> {
  return invoke<EncoderInfo>("detect_encoders");
}

export function scanFolder(path: string, recursive: boolean): Promise<FileItem[]> {
  return invoke<FileItem[]>("scan_folder", { path, recursive });
}

export function startRun(files: string[], dryRun: boolean): Promise<void> {
  return invoke("start_run", { files, dryRun });
}

export function stopRun(): Promise<void> {
  return invoke("stop_run");
}

export function probeFile(path: string): Promise<unknown> {
  return invoke("probe_file", { path });
}

export function probeFiles(paths: string[]): Promise<FileInfo[]> {
  return invoke<FileInfo[]>("probe_files", { paths });
}
