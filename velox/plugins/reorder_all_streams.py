"""Order Streams.

Re-orders streams by a configurable set of properties
(codecs, channels, languages, streamTypes).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..media import CHANNEL_LABELS, VIDEO, MediaInfo, stream_type
from .base import PipelineContext, Plugin, PluginResult, RemuxPlan
from .reorder_streams import type_order


def _channels_value(stream: dict[str, Any]) -> str:
    channels = stream.get("channels")
    if isinstance(channels, int) and channels in CHANNEL_LABELS:
        return CHANNEL_LABELS[channels]
    return ""


class ReorderAllStreamsPlugin(Plugin):
    key = "reorder_all_streams"
    name = "Order Streams"
    description = "Re-orders all streams based on codecs, channels, languages and stream types."

    def run(self, media: MediaInfo, ctx: PipelineContext) -> PluginResult:
        result = PluginResult(container=media.container)

        if not media.streams:
            result.error = (
                "ffprobe was unable to extract any stream info on this file. "
                "This may be a corrupt file or a permissions issue."
            )
            return result

        # --- mp4 special case (ffmpeg cannot reliably reorder mp4 streams).
        if media.container == "mp4" and media.is_video:
            if media.first_stream_type == VIDEO:
                result.note(
                    "File is mp4 and already has the video stream in the correct order! "
                    "Due to FFmpeg issues when reordering streams in mp4 files, other "
                    "stream ordering will be skipped"
                )
                return result
            result.process_file = True
            result.note("File is mp4 and contains video but video is not first stream, remuxing")
            result.remux = RemuxPlan(order=type_order(media))
            result.requeue = True
            return result

        # --- general reordering.
        streams = [dict(s) for s in media.streams]
        for index, stream in enumerate(streams):
            stream["typeIndex"] = int(stream["index"])
        original_order = [s["typeIndex"] for s in streams]

        languages = str(getattr(self.config, "languages", "") or "")
        codecs = str(getattr(self.config, "codecs", "") or "")
        channels = str(getattr(self.config, "channels", "") or "")
        stream_types = str(getattr(self.config, "stream_types", "") or "")
        process_order = str(getattr(self.config, "process_order", "") or "")

        sort_types: dict[str, tuple[str, Callable[[dict[str, Any]], str]]] = {
            "languages": (languages, lambda s: str((s.get("tags") or {}).get("language") or "")),
            "codecs": (codecs, lambda s: str(s.get("codec_name") or "")),
            "channels": (channels, _channels_value),
            "streamTypes": (stream_types, stream_type),
        }

        for sort_name in [p.strip() for p in process_order.split(",") if p.strip()]:
            entry = sort_types.get(sort_name)
            if not entry:
                continue
            inputs, get_value = entry
            if inputs:
                streams = _sort_streams(streams, inputs, get_value)

        new_order = [s["typeIndex"] for s in streams]
        if new_order != original_order:
            result.note("☒ Streams are not in the correct order!")
            result.process_file = True
            result.remux = RemuxPlan(order=new_order)
            result.requeue = True
        else:
            result.note("☑ Streams are in the correct order!")
        return result


def _sort_streams(
    streams: list[dict[str, Any]],
    inputs: str,
    get_value: Callable[[dict[str, Any]], str],
) -> list[dict[str, Any]]:
    """Port of the original JS ``sortStreams`` helper (stable, in place)."""
    items = [item.strip() for item in inputs.split(",") if item.strip()]
    items.reverse()
    for item in items:
        matched: list[dict[str, Any]] = []
        i = 0
        while i < len(streams):
            stream = streams[i]
            if str(get_value(stream)) == str(item):
                codec_long = str(stream.get("codec_long_name") or "")
                codec_name = str(stream.get("codec_name") or "")
                if codec_long and ("image" in codec_long or "png" in codec_name):
                    # ffmpeg bug: image streams are not moved.
                    i += 1
                else:
                    matched.append(stream)
                    streams.pop(i)
            else:
                i += 1
        streams[:] = matched + streams
    return streams
