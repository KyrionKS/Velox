"""Exception types used across Velox."""

from __future__ import annotations


class VeloxError(Exception):
    """Base class for all Velox errors."""


class ToolNotFoundError(VeloxError):
    """Raised when ffmpeg/ffprobe cannot be found or executed."""


class FFmpegError(VeloxError):
    """Raised when an ffmpeg/ffprobe invocation fails."""

    def __init__(self, message: str, command: list[str] | None = None) -> None:
        super().__init__(message)
        self.command = command or []


class PluginError(VeloxError):
    """Raised when a plugin cannot process a file (mirrors Tdarr plugin errors)."""

    def __init__(self, message: str, plugin_id: str | None = None) -> None:
        super().__init__(message)
        self.plugin_id = plugin_id


class CancelledError(VeloxError):
    """Raised when the user asked the pipeline to stop."""
