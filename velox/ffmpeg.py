"""Thin wrappers around ``ffmpeg`` and ``ffprobe``."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Sequence

from .errors import CancelledError, FFmpegError, ToolNotFoundError
from .media import MediaInfo

log = logging.getLogger("velox")

# Common install locations, checked when PATH is not set (e.g. an app launched
# from the Finder gets a minimal environment on macOS).
_FALLBACK_DIRS = (
    "/opt/homebrew/bin",  # Apple Silicon Homebrew
    "/usr/local/bin",     # Intel Homebrew
    "/opt/local/bin",     # MacPorts
    "/usr/bin",
)


def _parse_progress(line: str) -> tuple[str, str] | None:
    if "=" not in line:
        return None
    key, _, value = line.strip().partition("=")
    key = key.strip()
    if not key:
        return None
    return key, value.strip()


def _parse_timecode(value: str) -> float:
    """Parse ``HH:MM:SS.micro`` into seconds (0.0 on failure)."""
    try:
        hours, minutes, seconds = value.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (ValueError, AttributeError):
        return 0.0


class FFmpegTools:
    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        logger: logging.Logger | None = None,
    ) -> None:
        self.log = logger or log
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self._encoder_cache: dict[str, bool] = {}
        self._encoders: set[str] | None = None

    # ------------------------------------------------------------- discovery
    @staticmethod
    def _locate(tool: str) -> str | None:
        found = shutil.which(tool)
        if found:
            return found
        if Path(tool).is_absolute() and Path(tool).exists():
            return tool
        for directory in _FALLBACK_DIRS:
            candidate = Path(directory) / tool
            if candidate.exists():
                return str(candidate)
        return None

    def resolve(self) -> None:
        """Fail fast with a clear message when the binaries are missing."""
        for label, tool in (("ffmpeg", self.ffmpeg_path), ("ffprobe", self.ffprobe_path)):
            found = self._locate(tool)
            if found is None:
                raise ToolNotFoundError(
                    f"{label} not found (looked for {tool!r}). "
                    "Install it (macOS: `brew install ffmpeg`) or set "
                    f"general.{label}_path in your config."
                )
            if label == "ffmpeg":
                self.ffmpeg_path = found
            else:
                self.ffprobe_path = found

    def versions(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for label, tool in (("ffmpeg", self.ffmpeg_path), ("ffprobe", self.ffprobe_path)):
            try:
                out = subprocess.run(
                    [tool, "-version"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                result[label] = out.stdout.splitlines()[0] if out.stdout else "?"
            except OSError as exc:  # pragma: no cover
                result[label] = f"error: {exc}"
        return result

    # ---------------------------------------------------------------- probing
    def probe(self, path: str | Path) -> MediaInfo:
        path = Path(path)
        command = [
            self.ffprobe_path,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise FFmpegError(
                f"ffprobe failed for {path.name}: {result.stderr.strip()}", command
            )
        try:
            data: dict[str, Any] = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise FFmpegError(f"Could not parse ffprobe output for {path.name}: {exc}") from exc
        return MediaInfo.from_probe(path, data, path.stat().st_size)

    # -------------------------------------------------------------- executing
    def run(
        self,
        args: Sequence[str],
        *,
        description: str = "",
        dry_run: bool = False,
        duration: float = 0.0,
        progress: Callable[[float], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> list[str]:
        command = [self.ffmpeg_path, *args]
        if description:
            self.log.info("  ▶ %s", description)
        self.log.debug("  $ %s", " ".join(command))
        if dry_run:
            return command
        if progress is None and cancel is None:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                tail = "\n".join(result.stderr.strip().splitlines()[-12:])
                raise FFmpegError(
                    f"ffmpeg failed (exit {result.returncode}).\n{tail}", command
                )
            return command
        return self._run_streaming(command, duration, progress, cancel)

    def _run_streaming(
        self,
        command: Sequence[str],
        duration: float,
        progress: Callable[[float], None] | None,
        cancel: Callable[[], bool] | None,
    ) -> list[str]:
        """Run ffmpeg while streaming ``-progress`` output and honouring cancel."""
        full = [command[0], "-progress", "pipe:1", "-nostats", *command[1:]]
        proc = subprocess.Popen(
            full, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1
        )
        stderr_lines: list[str] = []

        def drain() -> None:
            if proc.stderr is not None:
                for line in proc.stderr:
                    stderr_lines.append(line)

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()

        cancelled = False
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if cancel is not None and cancel():
                    cancelled = True
                    proc.terminate()
                    break
                parsed = _parse_progress(line)
                if parsed is None or progress is None:
                    continue
                key, value = parsed
                seconds: float | None = None
                if key == "out_time_us" and value not in ("", "N/A"):
                    try:
                        seconds = float(value) / 1_000_000
                    except ValueError:
                        seconds = None
                elif key == "out_time":
                    seconds = _parse_timecode(value)
                if seconds is not None and duration > 0:
                    progress(max(0.0, min(1.0, seconds / duration)))
        finally:
            if cancelled:
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    proc.kill()
                    proc.wait()
            else:
                proc.wait()
            reader.join(timeout=2)

        if cancelled:
            raise CancelledError("Processing stopped by user")
        if proc.returncode != 0:
            tail = "\n".join("".join(stderr_lines).strip().splitlines()[-12:])
            raise FFmpegError(
                f"ffmpeg failed (exit {proc.returncode}).\n{tail}", list(full)
            )
        if progress is not None:
            progress(1.0)
        return list(command)

    # --------------------------------------------------------------- encoders
    def encoders(self) -> set[str]:
        if self._encoders is None:
            self._encoders = set()
            try:
                out = subprocess.run(
                    [self.ffmpeg_path, "-hide_banner", "-encoders"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                for line in out.stdout.splitlines():
                    parts = line.split()
                    # listings look like: " V....D hevc_videotoolbox ..."
                    if len(parts) >= 2 and parts[0][:1] in {"V", "A", "S"} and parts[1].endswith(
                        ("_nvenc", "_qsv", "_amf", "_videotoolbox", "_vaapi", "_v4l2m2m")
                    ):
                        self._encoders.add(parts[1])
                    elif len(parts) >= 2 and parts[0].startswith("V"):
                        self._encoders.add(parts[1])
            except OSError:  # pragma: no cover
                pass
        return self._encoders

    def has_encoder(
        self,
        encoder: str,
        input_args: Sequence[str] | None = None,
        video_filter: str | None = None,
    ) -> bool:
        """Probe the encoder by encoding 1 second of black to null."""
        if encoder in self._encoder_cache:
            return self._encoder_cache[encoder]

        command = [self.ffmpeg_path, "-hide_banner", "-loglevel", "error"]
        if input_args:
            command += list(input_args)
        command += [
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=256x256:d=1:r=30",
        ]
        if video_filter:
            command += ["-vf", video_filter]
        command += ["-c:v", encoder, "-frames:v", "1", "-f", "null", "-"]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            ok = result.returncode == 0
        except OSError:  # pragma: no cover
            ok = False
        self._encoder_cache[encoder] = ok
        return ok
