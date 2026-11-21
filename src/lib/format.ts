export function fmtBytes(num: number): string {
  let value = Math.abs(num);
  const units = ["B", "KB", "MB", "GB", "TB"];
  for (const unit of units) {
    if (value < 1000 || unit === "TB") {
      if (unit === "B") return `${Math.round(value)} ${unit}`;
      return `${value.toFixed(2)} ${unit}`;
    }
    value /= 1000;
  }
  return `${value.toFixed(2)} TB`;
}

export function fmtSaved(saved: number): string {
  const sign = saved >= 0 ? "\u2212" : "+";
  return `${sign}${fmtBytes(Math.abs(saved))}`;
}

export function fmtDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }
  return `${minutes}:${String(secs).padStart(2, "0")}`;
}

export function baseName(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

export function dirName(path: string): string {
  const index = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  return index > 0 ? path.slice(0, index) : path;
}
