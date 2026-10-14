"""Velox - clean up and transcode video files with ffmpeg.

Velox analyses a file with ``ffprobe`` and runs a configurable pipeline of
steps (remux, audio/subtitle cleanup, audio standardisation, video encoding,
size check) using ``ffmpeg``, with a CLI and a PySide6 desktop app.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
