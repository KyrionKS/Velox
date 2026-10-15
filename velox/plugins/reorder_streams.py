"""Move Video First - put the video stream at the front of the file."""

from __future__ import annotations

from ..media import VIDEO, MediaInfo, stream_type
from .base import PipelineContext, Plugin, PluginResult, RemuxPlan

# Type order used by the original ``-map 0:v? -map 0:a? ...`` preset.
TYPE_ORDER = ("video", "audio", "subtitle", "data", "attachment")


def type_order(media: MediaInfo) -> list[int]:
    """Absolute input indices ordered video, audio, subtitle, data, attachment.

    Any stream type not in the list is appended at the end so nothing is lost.
    """
    ordered: list[int] = []
    for kind in TYPE_ORDER:
        ordered += [int(s["index"]) for s in media.streams if stream_type(s) == kind]
    seen = set(ordered)
    ordered += [int(s["index"]) for s in media.streams if int(s["index"]) not in seen]
    return ordered


class ReorderStreamsPlugin(Plugin):
    key = "reorder_streams"
    name = "Move Video First"
    description = (
        "Moves the video stream to the front so downstream tools recognise the codec correctly."
    )

    def run(self, media: MediaInfo, ctx: PipelineContext) -> PluginResult:
        result = PluginResult(container=media.container)

        if not media.is_video:
            result.note("☒ File is not video")
            return result

        order = type_order(media)

        # Only act when the video stream is not first (matches the original).
        if media.first_stream_type != VIDEO:
            result.note("☒ Video is not in the first stream")
            result.process_file = True
            result.remux = RemuxPlan(order=order)
            result.requeue = True
            return result

        result.note("☑ File has video in first stream")
        result.note("☑ File meets conditions!")
        return result
