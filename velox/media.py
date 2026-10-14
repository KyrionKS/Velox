"""A thin, read-only wrapper around ffprobe JSON output."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VIDEO = "video"
AUDIO = "audio"
SUBTITLE = "subtitle"
DATA = "data"
ATTACHMENT = "attachment"

# Channels -> short display label used by the re-order plugin.
CHANNEL_LABELS = {8: "7.1", 6: "5.1", 2: "2", 1: "1"}

# Picture codecs that sometimes show up as a "video" stream but are not real video.
IMAGE_CODECS = {"mjpeg", "png", "bmp", "gif", "webp"}


def stream_type(stream: dict[str, Any]) -> str:
    return str(stream.get("codec_type") or "").lower()


def stream_language(stream: dict[str, Any]) -> str:
    tags = stream.get("tags") or {}
    return str(tags.get("language") or "").lower()


def stream_title(stream: dict[str, Any]) -> str:
    tags = stream.get("tags") or {}
    return str(tags.get("title") or "")


def stream_bitrate_kbps(stream: dict[str, Any]) -> int | None:
    """Best-effort bitrate (kbps) for a single stream."""
    tags = stream.get("tags") or {}
    for key in ("BPS", "BPS-eng", "bps"):
        raw = tags.get(key)
        if raw:
            try:
                return int(float(raw) / 1000)
            except (TypeError, ValueError):
                pass
    raw = stream.get("bit_rate")
    if raw:
        try:
            return int(float(raw) / 1000)
        except (TypeError, ValueError):
            pass
    return None


@dataclass
class MediaInfo:
    """Probed information about a single media file."""

    path: Path
    probe: dict[str, Any]
    size: int

    @classmethod
    def from_probe(cls, path: str | Path, probe: dict[str, Any], size: int) -> "MediaInfo":
        return cls(Path(path), probe, size)

    # ------------------------------------------------------------- basic info
    @property
    def streams(self) -> list[dict[str, Any]]:
        return list(self.probe.get("streams") or [])

    @property
    def format(self) -> dict[str, Any]:
        return self.probe.get("format") or {}

    @property
    def container(self) -> str:
        """Container as the bare file extension (e.g. ``mkv``)."""
        return self.path.suffix.lower().lstrip(".")

    @property
    def extension(self) -> str:
        return self.path.suffix.lower().lstrip(".")

    @property
    def is_video(self) -> bool:
        return any(stream_type(s) == VIDEO for s in self.streams)

    @property
    def file_medium(self) -> str:
        return "video" if self.is_video else "other"

    @property
    def duration(self) -> float:
        raw = self.format.get("duration")
        try:
            value = float(raw)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
        fallback = self.video_stream
        if fallback is not None:
            try:
                return float(fallback.get("duration") or 0.0)
            except (TypeError, ValueError):
                return 0.0
        return 0.0

    @property
    def bitrate_kbps(self) -> int | None:
        raw = self.format.get("bit_rate")
        if raw:
            try:
                return int(float(raw) / 1000)
            except (TypeError, ValueError):
                pass
        return None

    # --------------------------------------------------------------- streams
    def streams_of(self, kind: str) -> list[dict[str, Any]]:
        return [s for s in self.streams if stream_type(s) == kind]

    @property
    def video_stream(self) -> dict[str, Any] | None:
        for s in self.streams:
            if stream_type(s) == VIDEO and str(s.get("codec_name") or "").lower() not in IMAGE_CODECS:
                return s
        return None

    @property
    def first_stream_type(self) -> str:
        if not self.streams:
            return ""
        return stream_type(self.streams[0])

    def summary(self) -> str:
        bits = [
            f"container={self.container or '?'}",
            f"size={self.size / 1_000_000:.1f}MB",
            f"duration={self.duration:.1f}s",
        ]
        counts: dict[str, int] = {}
        for s in self.streams:
            counts[stream_type(s)] = counts.get(stream_type(s), 0) + 1
        if counts:
            bits.append(
                "streams=" + ",".join(f"{k}:{v}" for k, v in counts.items() if k)
            )
        return " ".join(bits)

    def stream_table(self) -> str:
        """Human readable table used by ``velox --info``."""
        header = f"{'idx':>4} {'type':<9} {'codec':<12} {'ch':>3} {'lang':<5} title"
        lines = [header, "-" * len(header)]
        for s in self.streams:
            lines.append(
                f"{s.get('index', '?'):>4} "
                f"{stream_type(s):<9} "
                f"{str(s.get('codec_name') or ''):<12} "
                f"{str(s.get('channels') or ''):>3} "
                f"{stream_language(s) or '-':<5} "
                f"{stream_title(s)}"
            )
        return "\n".join(lines)

    def as_debug_json(self) -> str:
        return json.dumps(self.probe, indent=2, ensure_ascii=False)
