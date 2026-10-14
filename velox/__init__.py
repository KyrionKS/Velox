"""Velox - standalone Python port of a Tdarr classic plugin stack.

Velox analyses a video file with ``ffprobe`` and then runs a configurable
pipeline of plugins (ported from Tdarr classic plugins) that remux, clean and
optionally transcode the file using ``ffmpeg``.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
