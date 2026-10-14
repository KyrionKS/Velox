"""Tdarr_Plugin_a9he_New_file_size_check.

Validates that the processed file is within the configured bounds relative to
the original file (``upper_bound`` / ``lower_bound``, in percent) and/or within
absolute bounds (``min_size_mb`` / ``max_size_mb``).

All sizes are MiB (1 MiB = 1,048,576 bytes).  A bound of 0 disables that check.
"""

from __future__ import annotations

from ..media import MediaInfo
from .base import PipelineContext, Plugin, PluginResult

MIB = 1024 * 1024


class FileSizeCheckPlugin(Plugin):
    key = "file_size_check"
    name = "New File Size Check"
    tdarr_id = "Tdarr_Plugin_a9he_New_file_size_check"
    description = (
        "Raises an error if the new file size is outside the configured relative "
        "or absolute bounds."
    )
    mergeable = False  # inspects the finished file, so pending remux must flush first

    def run(self, media: MediaInfo, ctx: PipelineContext) -> PluginResult:
        result = PluginResult(container=media.container)

        new_size = media.size
        old_size = ctx.original_media.size
        new_mb = new_size / MIB

        upper = float(getattr(self.config, "upper_bound", 110))
        lower = float(getattr(self.config, "lower_bound", 0))
        min_mb = float(getattr(self.config, "min_size_mb", 0))
        max_mb = float(getattr(self.config, "max_size_mb", 0))

        ratio = int((new_size / old_size) * 100) if old_size else 0
        size_text = (
            f"New file has size {new_mb:.3f} MiB ({ratio}% of the original"
            + (f" {old_size / MIB:.3f} MiB" if old_size else "")
            + ")"
        )

        if upper > 0 and old_size and new_size > (upper / 100) * old_size:
            result.error = (
                f"New file size not within limits. {size_text}. upperBound is {upper:g}%"
            )
        elif lower > 0 and old_size and new_size < (lower / 100) * old_size:
            result.error = (
                f"New file size not within limits. {size_text}. lowerBound is {lower:g}%"
            )
        elif max_mb > 0 and new_mb > max_mb:
            result.error = (
                f"New file size not within limits. {size_text}. max_size_mb is {max_mb:g} MiB"
            )
        elif min_mb > 0 and new_mb < min_mb:
            result.error = (
                f"New file size not within limits. {size_text}. min_size_mb is {min_mb:g} MiB"
            )
        else:
            result.note(size_text)
        return result
