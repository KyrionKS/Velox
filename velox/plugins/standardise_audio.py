"""Tdarr_Plugin_00td_action_standardise_audio_stream_codecs.

Transcodes every audio stream that is not already in the target codec.  Bitrate
and channel count are kept as close to the source as possible.
"""

from __future__ import annotations

from ..media import AUDIO, MediaInfo, stream_bitrate_kbps, stream_type
from .base import PipelineContext, Plugin, PluginResult, RemuxPlan

# Codecs that do not accept a ``-b:a`` bitrate option.
LOSSLESS_CODECS = {"flac", "truehd", "mlp", "pcm_s16le", "pcm_s24le", "pcm_s32le", "alac"}


class StandardiseAudioPlugin(Plugin):
    key = "standardise_audio"
    name = "Standardise Audio Stream Codecs"
    tdarr_id = "Tdarr_Plugin_00td_action_standardise_audio_stream_codecs"
    description = (
        "Transcodes all audio tracks that are not in the specified codec into "
        "that codec, keeping the original bitrate and channel count."
    )

    def run(self, media: MediaInfo, ctx: PipelineContext) -> PluginResult:
        result = PluginResult(container=media.container)

        if not media.is_video:
            result.note("☒ File is not video")
            return result

        target = str(getattr(self.config, "audio_codec", "aac") or "").lower().strip()
        if not target:
            result.note("☒ No audio codec configured, skipping this plugin.")
            return result

        cap = int(getattr(self.config, "audio_bitrate_kbps", 0) or 0)

        codecs: dict[int, str] = {}
        bitrates: dict[int, str] = {}

        for stream in media.streams:
            if stream_type(stream) != AUDIO:
                continue

            index = int(stream["index"])
            codec = str(stream.get("codec_name") or "").lower()
            source_bitrate = stream_bitrate_kbps(stream)
            needs_codec = bool(codec) and codec != target
            # With a cap configured, also re-encode matching tracks that are
            # heavier than the cap so audio cannot eat the size budget.
            needs_cap = bool(cap) and bool(source_bitrate) and source_bitrate > cap

            if needs_codec or needs_cap:
                codecs[index] = target
                target_bitrate = cap if cap else source_bitrate
                if target_bitrate and target not in LOSSLESS_CODECS:
                    bitrates[index] = f"{target_bitrate}k"

                if needs_codec:
                    reason = f"is {codec}, transcoding to {target}"
                else:
                    reason = f"is {source_bitrate}k {codec}, capping to {target_bitrate}k"
                result.note(f"☒ Audio stream {index} {reason}")

        if codecs:
            result.process_file = True
            result.remux = RemuxPlan(codecs=codecs, bitrates=bitrates)
            result.requeue = True
        else:
            result.note(f"☑ All audio streams already use {target}.")
        return result
