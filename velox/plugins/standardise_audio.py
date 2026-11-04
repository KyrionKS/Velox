"""Standardise Audio Codec.

Transcodes every audio stream that is not already in the target codec.  Bitrate
and channel count are kept as close to the source as possible.

Two details matter for quality:

* AAC needs an explicit channel count (``-ac``).  AC-3, E-AC-3 and DTS decode
  as ``5.1(side)``; without ``-ac 6`` FFmpeg then encodes a non-standard PCE
  channel layout (no LFE) that many players mis-handle.  We therefore always
  pass the source channel count (clamped to what the target codec supports).
* Re-encoding lossy -> lossy adds generation loss, so tracks that already use
  the target codec are left alone unless the user asks for it.
"""

from __future__ import annotations

from ..media import AUDIO, MediaInfo, stream_bitrate_kbps, stream_type
from .base import PipelineContext, Plugin, PluginResult, RemuxPlan

# Codecs that do not accept a ``-b:a`` bitrate option.
LOSSLESS_CODECS = {"flac", "truehd", "mlp", "pcm_s16le", "pcm_s24le", "pcm_s32le", "alac"}

# Maximum number of channels a codec can carry.  Sources with more channels
# are downmixed (e.g. 7.1 -> 5.1 for AC-3, anything -> stereo for MP3).
MAX_CHANNELS = {
    "aac": 8,
    "ac3": 6,
    "eac3": 6,
    "libmp3lame": 2,
    "mp3": 2,
}

# AAC encoders in order of preference (``auto`` picks the first available).
AAC_ENCODERS = ("libfdk_aac", "aac")


class StandardiseAudioPlugin(Plugin):
    key = "standardise_audio"
    name = "Standardise Audio Codec"
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
        min_per_channel = int(getattr(self.config, "audio_bitrate_min_per_channel_kbps", 128) or 0)
        reencode_target = bool(getattr(self.config, "reencode_target_codec", False))
        encoder, encoder_note = self._resolve_encoder(target, ctx)
        if encoder_note:
            result.note(encoder_note)
        encoder_label = encoder if target == "aac" else target

        codecs: dict[int, str] = {}
        channels: dict[int, int] = {}
        bitrates: dict[int, str] = {}

        for stream in media.streams:
            if stream_type(stream) != AUDIO:
                continue

            index = int(stream["index"])
            codec = str(stream.get("codec_name") or "").lower()
            source_bitrate = stream_bitrate_kbps(stream)
            source_channels = int(stream.get("channels") or 0)
            output_channels = self._clamp_channels(target, source_channels)

            # An AAC track without a channel layout is the broken 5.1 encoding
            # produced by older versions (PCE instead of standard 5.1).  Even
            # though the codec matches, it has to be re-encoded.
            broken_layout = (
                target == "aac"
                and codec == "aac"
                and source_channels > 2
                and not stream.get("channel_layout")
            )

            needs_codec = bool(codec) and (codec != target or broken_layout)
            effective_cap = self._effective_cap(cap, min_per_channel, source_channels)
            needs_cap = (
                bool(effective_cap)
                and bool(source_bitrate)
                and source_bitrate > effective_cap
                # Never re-encode a track that is already in the target codec
                # just to shave bits off it (lossy -> lossy generation loss),
                # unless the user explicitly asked for it.
                and (reencode_target or codec != target)
            )

            if not (needs_codec or needs_cap):
                continue

            codecs[index] = encoder if target == "aac" else target
            if output_channels > 0:
                channels[index] = output_channels

            target_bitrate = effective_cap or source_bitrate
            if target_bitrate and source_bitrate:
                target_bitrate = min(target_bitrate, source_bitrate)
            if target_bitrate and target not in LOSSLESS_CODECS:
                bitrates[index] = f"{target_bitrate}k"

            if output_channels and source_channels and output_channels < source_channels:
                result.note(
                    f"ℹ Audio stream {index}: downmixing {source_channels} -> "
                    f"{output_channels} channels for {target}."
                )

            dims = f"{output_channels} ch" if output_channels else "channels unknown"
            rate = f"{target_bitrate}k" if target_bitrate else "source rate"
            if broken_layout:
                reason = (
                    f"is {codec} with an invalid channel layout, re-encoding to "
                    f"{encoder_label} ({dims}, {rate})"
                )
            elif needs_codec:
                reason = f"is {codec}, transcoding to {encoder_label} ({dims}, {rate})"
            else:
                reason = f"is {source_bitrate}k {codec}, capping to {target_bitrate}k"
            result.note(f"☒ Audio stream {index} {reason}")

        if codecs:
            result.process_file = True
            result.remux = RemuxPlan(codecs=codecs, channels=channels, bitrates=bitrates)
            result.requeue = True
        else:
            result.note(f"☑ All audio streams already use {target}.")
        return result

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _effective_cap(cap: int, min_per_channel: int, channels: int) -> int:
        """Cap in kbps, raised to a sane minimum for surround tracks."""
        if cap <= 0:
            return 0
        floor = channels * min_per_channel if channels > 2 and min_per_channel > 0 else 0
        return max(cap, floor)

    @staticmethod
    def _clamp_channels(target: str, channels: int) -> int:
        if channels <= 0:
            return 0
        maximum = MAX_CHANNELS.get(target)
        if maximum is None:
            return channels
        return min(channels, maximum)

    def _resolve_encoder(self, target: str, ctx: PipelineContext) -> tuple[str, str | None]:
        """Return the concrete encoder name and an optional log note."""
        if target != "aac":
            return target, None

        preference = str(getattr(self.config, "aac_encoder", "auto") or "auto").lower().strip()
        probe = getattr(ctx.ffmpeg, "has_audio_encoder", None)
        if probe is None:  # very old/misconfigured FFmpegTools
            return "aac", None

        if preference in AAC_ENCODERS:
            if preference == "aac" or probe(preference):
                return preference, None
            return "aac", (
                f"ℹ AAC encoder {preference} is not available, using the native AAC encoder."
            )

        # "auto" (or an unknown value): best available encoder.
        for candidate in AAC_ENCODERS:
            if probe(candidate):
                if candidate != "aac":
                    return candidate, f"☑ Using AAC encoder {candidate}."
                return candidate, None
        return "aac", None
