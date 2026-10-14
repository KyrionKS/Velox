"""Tdarr_Plugin_AC_HEVC_GPU - GPU video transcode.

Ported from arabcoders' ``Tdarr_Plugin_AC_HEVC_GPU`` (v1.42) and tuned for
"best quality at the smallest size" on Apple Silicon.

Key adaptations over the Tdarr original:

* **Bitrate modes**:
  - ``tier`` (default): resolution & framerate aware
    (``bits_per_pixel * W * H * fps``) — one setting that scales correctly for
    both movies and series/episodes.
  - ``size``: fixed output size per file.
  - ``quality``: constant quality (Apple Silicon VideoToolbox ``-q:v``) with a
    resolution-aware ceiling.
* **No downscaling**: the source resolution is kept (up to 4K/8K); the tier
  bitrate scales with the pixel count, so a 4K source gets ~4x the bitrate of
  1080p automatically.
* **Hardware decode** (VideoToolbox) on Apple Silicon.
* **Encoder probing** picks the first working GPU encoder in the original order
  (nvenc -> amf -> qsv -> videotoolbox -> vaapi); the default configuration pins
  ``hevc_videotoolbox`` for Apple Silicon.
* **Encoder-aware flags**: VideoToolbox on Apple Silicon supports constant
  quality (``-q:v``) and spatial AQ, but not the NVENC-only ``-rc-lookahead``.
* Optional CPU fallback (``allow_cpu_fallback``) instead of hard-failing.
"""

from __future__ import annotations

import logging
import platform
import subprocess
from typing import Any

from ..media import AUDIO, IMAGE_CODECS, MediaInfo, stream_bitrate_kbps, stream_type
from .base import PipelineContext, Plugin, PluginResult

log = logging.getLogger("velox")

# Encoders tried in this order (same as the original plugin).
GPU_ENCODERS: list[dict[str, Any]] = [
    {"encoder": "hevc_nvenc"},
    {"encoder": "hevc_amf"},
    {"encoder": "hevc_qsv"},
    {"encoder": "hevc_videotoolbox"},
    {"encoder": "h264_nvenc"},
    {"encoder": "h264_amf"},
    {"encoder": "h264_qsv"},
    {"encoder": "h264_videotoolbox"},
    {
        "encoder": "hevc_vaapi",
        "input_args": [
            "-hwaccel", "vaapi",
            "-hwaccel_device", "/dev/dri/renderD128",
            "-hwaccel_output_format", "vaapi",
        ],
        "video_filter": "format=nv12,hwupload",
    },
]

NVENC_ENCODERS = {"hevc_nvenc", "h264_nvenc"}

BITRATE_MODES = {"tier", "size", "quality"}

# Apple Silicon is the home platform: VideoToolbox constant quality (-q:v),
# hardware decode and Main10 are all available.
VIDEOTOOLBOX_ENCODERS = {"hevc_videotoolbox", "h264_videotoolbox"}

# Stream codecs dropped when force_conform is enabled.
CONFORM_SPEC: dict[str, list[str]] = {
    "mkv": ["eia_608", "timed_id3", "dvb_teletext", "bin_data"],
    "mp4": ["hdmv_pgs_subtitle", "eia_608", "subrip", "timed_id3"],
}


def _is_10bit(stream: dict[str, Any] | None) -> bool:
    if not stream:
        return False
    profile = str(stream.get("profile") or "").lower()
    return (
        profile in {"high 10", "main 10"}
        or str(stream.get("bits_per_raw_sample") or "") == "10"
        or str(stream.get("pix_fmt") or "") == "yuv420p10le"
    )


class HevcGpuPlugin(Plugin):
    key = "hevc_gpu"
    name = "Transcode a video file (HEVC GPU)"
    tdarr_id = "Tdarr_Plugin_AC_HEVC_GPU"
    description = "Transcodes a video file to the target codec using a GPU encoder when possible."
    mergeable = False  # a transcode always needs its own pass

    # ------------------------------------------------------------------ entry
    def run(self, media: MediaInfo, ctx: PipelineContext) -> PluginResult:
        target_codec = str(getattr(self.config, "target_codec", "hevc") or "hevc").lower()
        configured_container = str(getattr(self.config, "container", "mkv") or "mkv").lower()
        container = media.container if configured_container == "original" else configured_container
        result = PluginResult(container=container)

        encoder = self._select_encoder(target_codec, ctx, result)
        if encoder is None:
            result.error = (
                "☒ No usable hardware encoder was found and CPU fallback is disabled. "
                "Enable general CPU fallback or install a GPU/videotoolbox-capable ffmpeg."
            )
            return result

        if not media.is_video:
            result.note("☒ File is not a video.")
            return result

        mode = str(getattr(self.config, "bitrate_mode", "tier") or "tier").lower()
        if mode not in BITRATE_MODES:
            result.note(f"☒ Unknown bitrate_mode {mode!r}, falling back to tier.")
            mode = "tier"

        # Constant quality (VideoToolbox -q:v) is only available on Apple
        # Silicon; fall back to tier mode on anything else.
        if mode == "quality" and not self._constant_quality_supported(encoder["encoder"], result):
            mode = "tier"

        duration = media.duration
        current_bitrate, source = self._current_bitrate(media, result)
        if current_bitrate:
            result.note(f"☑ Current video bitrate ≈ {current_bitrate}kbps ({source})")

        # -------------------------------------------------- target bitrate
        target_bitrate: int | None = None
        minimum_bitrate = maximum_bitrate = 0

        if mode == "quality":
            quality = int(getattr(self.config, "quality", 55))
            result.note(f"☑ Quality mode: constant quality {quality}")
        else:
            if mode == "size":
                if duration <= 0:
                    result.note("☒ Could not determine duration for the size target. Skipping.")
                    return result
                target_bitrate = self._size_target_bitrate(media, duration, result)
            else:  # tier
                target_bitrate = self._tier_bitrate(media, result)

            # Never encode at a higher bitrate than the source: it would only
            # make the file bigger without a quality benefit.
            if current_bitrate and target_bitrate and target_bitrate > current_bitrate:
                result.note(
                    f"ℹ Computed target {target_bitrate}k is above the source "
                    f"{current_bitrate}k, capping to the source bitrate."
                )
                target_bitrate = current_bitrate

            if not target_bitrate or target_bitrate <= 0:
                result.note("☒ Target bitrate could not be calculated. Skipping this plugin.")
                return result

            cutoff = int(getattr(self.config, "bitrate_cutoff", 0) or 0)
            if cutoff and cutoff >= target_bitrate:
                result.note(
                    f"☑ File target bitrate {target_bitrate}k is below the set cutoff of "
                    f"{cutoff}k. Skipping this plugin."
                )
                return result

            minimum_bitrate = round(target_bitrate * 0.75)
            maximum_bitrate = round(target_bitrate * 1.25)
            result.note("Bitrate settings:")
            result.note(f"  target  = {target_bitrate}kbps")
            result.note(f"  minimum = {minimum_bitrate}kbps")
            result.note(f"  maximum = {maximum_bitrate}kbps")

        # --------------------------------------------------------- extra args
        extra: list[str] = []

        if bool(getattr(self.config, "force_conform", False)) and container in CONFORM_SPEC:
            if container == "mkv":
                extra += ["-map", "-0:d"]
            for stream in media.streams:
                codec = str(stream.get("codec_name") or "").lower()
                if codec in CONFORM_SPEC[container]:
                    extra += ["-map", f"-0:{stream.get('index')}"]

        tenbit = bool(getattr(self.config, "enable_10bit", False)) or _is_10bit(media.video_stream)
        if tenbit and target_codec == "hevc":
            extra += ["-profile:v", "main10"]
            if encoder["encoder"] != "hevc_vaapi":
                extra += ["-pix_fmt", "p010le"]
            reason = (
                "user enabled in config"
                if getattr(self.config, "enable_10bit", False)
                else "source is 10-bit"
            )
            result.note(f"10-bit encode enabled (Main10, {reason}).")

        if bool(getattr(self.config, "bframes_enabled", False)) and encoder["encoder"] in NVENC_ENCODERS:
            extra += ["-bf", str(int(getattr(self.config, "bframes_value", 5)))]

        # ------------------------------------------------------ stream handling
        sub_idx = 0
        already_target = False
        allowed = [
            c.strip().lower()
            for c in str(getattr(self.config, "allowed_codecs", "") or "").split(",")
            if c.strip()
        ]
        for stream in media.streams:
            ctype = stream_type(stream)
            cname = str(stream.get("codec_name") or "").lower()

            if ctype == "subtitle":
                if container == "mkv" and cname == "mov_text":
                    extra += [f"-c:s:{sub_idx}", "srt"]
                    result.note("☒ Converting mov_text subtitles to srt (mkv target).")
                elif container == "mp4" and cname == "subrip":
                    extra += [f"-c:s:{sub_idx}", "mov_text"]
                    result.note("☒ Converting subrip subtitles to mov_text (mp4 target).")
                sub_idx += 1
                continue

            if ctype == "data":
                if container == "mkv":
                    extra += ["-map", f"-0:{stream.get('index')}"]
                    result.note(
                        f"☒ Removing data stream {stream.get('index')} (mkv does not support it)."
                    )
                continue

            if ctype != "video":
                continue

            if cname in IMAGE_CODECS:
                extra += ["-map", f"-0:{stream.get('index')}"]
                result.note(
                    f"☑ Removing {cname}:{stream.get('index')} as it interferes with encoding."
                )
                continue

            if cname == target_codec or cname in allowed:
                already_target = True

        if already_target:
            if media.container != container:
                result.note(
                    f"☒ File is {target_codec} but not in a {container} container. Remuxing."
                )
                result.process_file = True
                result.output_args = ["-map", "0", "-c", "copy", *extra]
                result.requeue = True
                return result
            result.note(f"☑ File is already {target_codec} and in a {container} container.")
            return result

        # ----------------------------------------------------------- transcode
        result.note(f"☒ File is not in {target_codec}. Transcoding with {encoder['encoder']}.")

        input_args: list[str] = list(encoder.get("input_args", []))
        if (
            encoder["encoder"] in VIDEOTOOLBOX_ENCODERS
            and bool(getattr(self.config, "hardware_decode", True))
        ):
            # Let the media engine decode the source as well (Apple Silicon).
            # ffmpeg falls back to software decode automatically for codecs
            # VideoToolbox cannot handle.
            input_args = ["-hwaccel", "videotoolbox", *input_args]
            result.note("Hardware (VideoToolbox) decoding enabled.")
        if container in ("ts", "avi"):
            input_args += ["-fflags", "+genpts"]

        output_args: list[str] = ["-map", "0", "-c", "copy", "-c:v", encoder["encoder"]]
        if encoder.get("output_args"):
            output_args += list(encoder["output_args"])
        if encoder["encoder"].startswith("hevc") and container == "mp4":
            output_args += ["-tag:v", "hvc1"]

        output_args += self._video_encode_args(
            encoder["encoder"],
            mode,
            target_bitrate,
            minimum_bitrate,
            maximum_bitrate,
            self._quality_ceiling(media),
        )

        # Hardware-upload filter chain (vaapi only; no downscaling).
        if encoder.get("video_filter"):
            output_args += ["-vf", encoder["video_filter"]]

        output_args += ["-max_muxing_queue_size", "9999"]
        output_args += extra

        result.input_args = input_args
        result.output_args = output_args
        result.process_file = True
        result.requeue = True
        return result

    # -------------------------------------------------------------- bitrate
    def _tier_bitrate(self, media: MediaInfo, result: PluginResult) -> int:
        """Resolution & framerate aware bitrate (bits per pixel per frame).

        This is the "one setting for everything" mode: the bitrate follows the
        output resolution and framerate, so a long movie naturally gets a bigger
        file than a short episode while the quality stays consistent.
        """
        width, height = self._video_dimensions(media)
        fps = self._fps(media)
        bpp = float(getattr(self.config, "bits_per_pixel", 0.033))
        video_kbps = round(bpp * width * height * fps / 1000)
        clamped = self._clamp(video_kbps)
        result.note(
            f"ℹ Tier mode: {bpp:g} bpp × {width}x{height}@{fps:g}fps → {video_kbps}k"
            + (f" (clamped to {clamped}k)" if clamped != video_kbps else "")
        )
        return clamped

    def _size_target_bitrate(
        self, media: MediaInfo, duration: float, result: PluginResult
    ) -> int:
        target_mb = float(getattr(self.config, "target_size_mb", 1500.0))
        overhead = float(getattr(self.config, "size_overhead_percent", 2.0))
        total_kbps = (target_mb * 1024 * 1024 * 8) / duration / 1000
        usable_kbps = total_kbps * (1 - overhead / 100.0)
        audio_kbps = self._audio_budget_kbps(media)
        video_kbps = round(usable_kbps - audio_kbps)
        clamped = self._clamp(video_kbps)
        result.note(
            f"ℹ Size mode: {target_mb:.0f} MiB over {duration:.0f}s → {total_kbps:.0f}k total, "
            f"audio budget {audio_kbps}k → video {video_kbps}k"
            + (f" (clamped to {clamped}k)" if clamped != video_kbps else "")
        )
        return clamped

    def _quality_ceiling(self, media: MediaInfo) -> int:
        """Upper bound used by constant-quality mode (2x the tier bitrate)."""
        width, height = self._video_dimensions(media)
        fps = self._fps(media)
        bpp = float(getattr(self.config, "bits_per_pixel", 0.033))
        return self._clamp(round(bpp * 2.0 * width * height * fps / 1000))

    def _clamp(self, kbps: int) -> int:
        minimum = int(getattr(self.config, "min_video_kbps", 300))
        maximum = int(getattr(self.config, "max_video_kbps", 12000))
        return max(minimum, min(maximum, kbps))

    def _audio_budget_kbps(self, media: MediaInfo) -> int:
        fallback = int(getattr(self.config, "fallback_audio_kbps", 256))
        total = 0
        for stream in media.streams_of(AUDIO):
            total += stream_bitrate_kbps(stream) or fallback
        return total

    def _current_bitrate(self, media: MediaInfo, result: PluginResult) -> tuple[int | None, str]:
        video = media.video_stream
        if video is not None:
            bitrate = stream_bitrate_kbps(video)
            if bitrate:
                return bitrate, "ffprobe stream tags"
        if media.bitrate_kbps:
            return media.bitrate_kbps, "container bitrate"
        return None, "none"

    # ----------------------------------------------------------- dimensions
    def _video_dimensions(self, media: MediaInfo) -> tuple[int, int]:
        """Source resolution (no downscaling).

        Falls back to 1920x1080 when the size is unknown, so tier mode always
        has a usable value.
        """
        video = media.video_stream
        if video is not None:
            try:
                width = int(video.get("width") or 0)
                height = int(video.get("height") or 0)
                if width > 0 and height > 0:
                    return width, height
            except (TypeError, ValueError):
                pass
        return 1920, 1080

    def _fps(self, media: MediaInfo) -> float:
        video = media.video_stream
        if video is not None:
            for key in ("avg_frame_rate", "r_frame_rate"):
                raw = str(video.get(key) or "")
                if not raw or raw in {"0/0", "0"}:
                    continue
                try:
                    num, _, den = raw.partition("/")
                    fps = float(num) / (float(den) if den else 1.0)
                    if fps > 0:
                        return fps
                except (TypeError, ValueError, ZeroDivisionError):
                    continue
        return 24.0

    def _constant_quality_supported(self, encoder: str, result: PluginResult) -> bool:
        """VideoToolbox constant quality (-q:v) only works on Apple Silicon."""
        if encoder not in VIDEOTOOLBOX_ENCODERS:
            return True
        if platform.system() == "Darwin" and platform.machine() == "arm64":
            return True
        result.note(
            "☒ Constant quality (-q:v) on VideoToolbox requires Apple Silicon, "
            "using tier mode instead."
        )
        return False

    # -------------------------------------------------------------- encoder
    def _select_encoder(
        self, target_codec: str, ctx: PipelineContext, result: PluginResult
    ) -> dict[str, Any] | None:
        configured = str(getattr(self.config, "encoder", "auto") or "auto").strip()

        # 1) An explicitly configured encoder wins (e.g. hevc_videotoolbox on
        #    Apple Silicon).  If it is not usable we fall back below.
        if configured and configured.lower() != "auto":
            selected = self._probe_encoder(configured, ctx, result)
            if selected is not None:
                if not selected["encoder"].startswith(target_codec):
                    result.note(
                        f"ℹ Configured encoder {selected['encoder']} does not match the "
                        f"target codec {target_codec}, using it anyway."
                    )
                result.note(f"☑ Using configured encoder: {selected['encoder']}")
                return selected
            result.note(
                f"☒ Configured encoder {configured} is not available, "
                "falling back to automatic detection."
            )

        # 2) Automatic GPU probing in the original plugin's order.
        if bool(getattr(self.config, "try_use_gpu", True)):
            candidates = [e for e in GPU_ENCODERS if target_codec in e["encoder"]]
            for candidate in candidates:
                selected = self._probe_encoder(candidate["encoder"], ctx, result)
                if selected is not None:
                    result.note(f"☑ Using hardware encoder: {selected['encoder']}")
                    return selected
            result.note("☒ No hardware encoder available.")

        # 3) CPU fallback (Velox extension).
        if bool(getattr(self.config, "allow_cpu_fallback", True)):
            cpu = "libx265" if target_codec == "hevc" else "libx264"
            result.note(f"ℹ Falling back to CPU encoder: {cpu}")
            return {"encoder": cpu, "cpu": True}
        return None

    def _probe_encoder(
        self, name: str, ctx: PipelineContext, result: PluginResult
    ) -> dict[str, Any] | None:
        """Return an encoder spec if *name* is usable, otherwise ``None``."""
        spec = next((e for e in GPU_ENCODERS if e["encoder"] == name), None)
        input_args = spec.get("input_args") if spec else None
        video_filter = spec.get("video_filter") if spec else None
        if not ctx.ffmpeg.has_encoder(name, input_args, video_filter):
            return None
        selected: dict[str, Any] = dict(spec) if spec else {"encoder": name}
        if selected["encoder"] in NVENC_ENCODERS:
            self._apply_best_nvenc(selected, result)
        return selected

    def _apply_best_nvenc(self, encoder: dict[str, Any], result: PluginResult) -> None:
        exclude_raw = str(getattr(self.config, "exclude_gpus", "") or "")
        try:
            exclude = {int(x) for x in exclude_raw.split(",") if x.strip()}
        except ValueError:
            exclude = set()

        try:
            names_out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                check=False,
            )
            if names_out.returncode != 0:
                return
            names = [n for n in names_out.stdout.strip().splitlines() if n.strip()]
        except OSError:
            return

        best_index = -1
        best_util = 100000
        for index in range(len(names)):
            if index in exclude:
                result.note(f"ℹ GPU {index}: {names[index]} is in the exclusion list, skipping.")
                continue
            try:
                util_out = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=utilization.gpu",
                        "--format=csv,noheader,nounits",
                        "-i",
                        str(index),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                util = int(util_out.stdout.strip())
            except (OSError, ValueError):
                continue
            result.note(f"ℹ GPU {index}: utilization {util}%")
            if util < best_util:
                best_index, best_util = index, util

        if best_index >= 0:
            encoder["input_args"] = ["-hwaccel_device", str(best_index)]
            encoder["output_args"] = ["-gpu", str(best_index)]

    # -------------------------------------------------------------- quality
    def _video_encode_args(
        self,
        encoder: str,
        mode: str,
        target: int | None,
        minimum: int,
        maximum: int,
        ceiling: int = 0,
    ) -> list[str]:
        if mode == "quality":
            args = self._quality_mode_args(encoder, ceiling)
        else:
            args = self._bitrate_mode_args(encoder, target or 0, minimum, maximum)

        # Adaptive quantization for the encoders that support it.
        if "nvenc" in encoder:
            args += ["-spatial_aq:v", "1", "-rc-lookahead:v", "32"]
        elif "videotoolbox" in encoder and bool(getattr(self.config, "spatial_aq", True)):
            # Supported on Apple Silicon; ignored automatically on devices that
            # do not provide it.
            args += ["-spatial_aq", "1"]
        return args

    def _bitrate_mode_args(
        self, encoder: str, target: int, minimum: int, maximum: int
    ) -> list[str]:
        if "videotoolbox" in encoder or "qsv" in encoder or "vaapi" in encoder:
            return ["-b:v", f"{target}k", "-maxrate", f"{maximum}k", "-bufsize", f"{target * 2}k"]
        if "amf" in encoder:
            return [
                "-b:v", f"{target}k",
                "-minrate", f"{minimum}k",
                "-maxrate", f"{maximum}k",
                "-bufsize", f"{target * 2}k",
            ]
        if "nvenc" in encoder:
            return [
                "-cq:v", "19",
                "-b:v", f"{target}k",
                "-minrate", f"{minimum}k",
                "-maxrate", f"{maximum}k",
                "-bufsize", f"{target * 2}k",
            ]
        # CPU fallback (libx264/libx265): ABR.
        preset = str(getattr(self.config, "cpu_preset", "medium") or "medium")
        return [
            "-preset", preset,
            "-b:v", f"{target}k",
            "-maxrate", f"{maximum}k",
            "-bufsize", f"{target * 2}k",
        ]

    def _quality_mode_args(self, encoder: str, ceiling: int = 0) -> list[str]:
        quality = int(getattr(self.config, "quality", 55))
        cap_args = ["-maxrate", f"{ceiling}k", "-bufsize", f"{ceiling * 2}k"] if ceiling > 0 else []
        if "videotoolbox" in encoder:
            # Constant quality is only available on Apple Silicon.
            return ["-q:v", str(quality), *cap_args]
        if "nvenc" in encoder:
            return ["-cq:v", str(quality), *cap_args]
        if "qsv" in encoder:
            return ["-global_quality", str(quality), *cap_args]
        if "vaapi" in encoder:
            return ["-qp", str(quality), *cap_args]
        if "amf" in encoder:
            return ["-rc", "cqp", "-qp_i", str(quality), "-qp_p", str(quality), *cap_args]
        preset = str(getattr(self.config, "cpu_preset", "medium") or "medium")
        return ["-preset", preset, "-crf", str(quality), *cap_args]
