"""Unit tests for the ported plugin logic (no ffmpeg required)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from velox.config import VeloxConfig
from velox.media import MediaInfo
from velox.plugins import PipelineContext
from velox.plugins.clean_audio import CleanAudioPlugin
from velox.plugins.clean_subs import CleanSubsPlugin
from velox.plugins.file_size_check import FileSizeCheckPlugin
from velox.plugins.hevc_gpu import HevcGpuPlugin
from velox.plugins.reorder_all_streams import ReorderAllStreamsPlugin
from velox.plugins.reorder_streams import ReorderStreamsPlugin
from velox.plugins.standardise_audio import StandardiseAudioPlugin


# --------------------------------------------------------------------- helpers
def vstream(index: int, codec: str = "h264", **extra: Any) -> dict[str, Any]:
    stream = {"index": index, "codec_type": "video", "codec_name": codec}
    stream.update(extra)
    return stream


def astream(
    index: int,
    codec: str = "aac",
    lang: str | None = None,
    title: str | None = None,
    channels: int = 2,
    bitrate: int | None = None,
) -> dict[str, Any]:
    stream: dict[str, Any] = {
        "index": index,
        "codec_type": "audio",
        "codec_name": codec,
        "channels": channels,
    }
    tags: dict[str, str] = {}
    if lang is not None:
        tags["language"] = lang
    if title is not None:
        tags["title"] = title
    if tags:
        stream["tags"] = tags
    if bitrate is not None:
        stream["tags"] = {**tags, "BPS": str(bitrate)}
    return stream


def sstream(index: int, codec: str = "subrip", lang: str | None = None, title: str | None = None):
    stream: dict[str, Any] = {"index": index, "codec_type": "subtitle", "codec_name": codec}
    tags: dict[str, str] = {}
    if lang is not None:
        tags["language"] = lang
    if title is not None:
        tags["title"] = title
    if tags:
        stream["tags"] = tags
    return stream


def make_media(
    streams: list[dict[str, Any]],
    container: str = "mkv",
    size: int = 1_000_000,
    duration: float = 100.0,
) -> MediaInfo:
    probe = {"streams": streams, "format": {"duration": str(duration), "format_name": container}}
    return MediaInfo.from_probe(Path(f"movie.{container}"), probe, size)


class FakeFFmpeg:
    def __init__(self, available: set[str] | None = None) -> None:
        self.available = available or set()

    def has_encoder(self, encoder, input_args=None, video_filter=None) -> bool:  # noqa: ARG002
        return encoder in self.available


def make_ctx(media: MediaInfo, available: set[str] | None = None) -> PipelineContext:
    return PipelineContext(ffmpeg=FakeFFmpeg(available), original_media=media)  # type: ignore[arg-type]


# ------------------------------------------------------------ reorder_streams
def test_reorder_streams_moves_video_to_front():
    media = make_media([astream(0, lang="eng"), vstream(1)])
    plugin = ReorderStreamsPlugin(VeloxConfig().reorder_streams)
    result = plugin.run(media, make_ctx(media))
    assert result.process_file
    assert result.remux is not None
    assert result.remux.order == [1, 0]


def test_reorder_streams_noop_when_video_first():
    media = make_media([vstream(0), astream(1, lang="eng")])
    plugin = ReorderStreamsPlugin(VeloxConfig().reorder_streams)
    result = plugin.run(media, make_ctx(media))
    assert not result.process_file


# ---------------------------------------------------------------- clean_audio
def test_clean_audio_removes_unwanted_language():
    media = make_media([vstream(0), astream(1, lang="eng"), astream(2, lang="jpn")])
    plugin = CleanAudioPlugin(VeloxConfig().clean_audio)  # keep eng,und
    result = plugin.run(media, make_ctx(media))
    assert result.process_file
    assert result.remux is not None
    assert result.remux.drop == {2}  # remove the jpn track


def test_clean_audio_keeps_und_by_default():
    media = make_media([vstream(0), astream(1, lang="und")])
    plugin = CleanAudioPlugin(VeloxConfig().clean_audio)
    result = plugin.run(media, make_ctx(media))
    assert not result.process_file


def test_clean_audio_failsafe_never_removes_all():
    media = make_media([vstream(0), astream(1, lang="jpn"), astream(2, lang="fra")])
    plugin = CleanAudioPlugin(VeloxConfig().clean_audio)  # keeps eng,und
    result = plugin.run(media, make_ctx(media))
    assert not result.process_file
    assert any("Cancelling" in line for line in result.log)


def test_clean_audio_removes_commentary_when_enabled():
    config = VeloxConfig().clean_audio
    config.commentary = True
    media = make_media(
        [vstream(0), astream(1, lang="eng"), astream(2, lang="eng", title="Director Commentary")]
    )
    result = CleanAudioPlugin(config).run(media, make_ctx(media))
    assert result.process_file
    assert result.remux is not None
    assert result.remux.drop == {2}


def test_clean_audio_tags_untagged_language():
    config = VeloxConfig().clean_audio
    config.tag_language = "eng"
    media = make_media([vstream(0), astream(1, lang="und")])
    result = CleanAudioPlugin(config).run(media, make_ctx(media))
    assert result.process_file
    assert result.remux is not None
    assert result.remux.metadata == {1: [("language", "eng")]}


# ----------------------------------------------------------------- clean_subs
def test_clean_subs_removes_unwanted_language():
    media = make_media([vstream(0), sstream(1, lang="eng"), sstream(2, lang="deu")])
    result = CleanSubsPlugin(VeloxConfig().clean_subs).run(media, make_ctx(media))
    assert result.process_file
    assert result.remux is not None
    assert result.remux.drop == {2}


def test_clean_subs_removes_sdh_when_enabled():
    config = VeloxConfig().clean_subs
    config.commentary = True
    config.sdh = True
    media = make_media([vstream(0), sstream(1, lang="eng", title="English SDH")])
    result = CleanSubsPlugin(config).run(media, make_ctx(media))
    assert result.process_file
    assert result.remux is not None
    assert result.remux.drop == {1}


def test_clean_subs_keeps_sdh_when_disabled():
    config = VeloxConfig().clean_subs
    config.commentary = True
    config.sdh = False
    media = make_media([vstream(0), sstream(1, lang="eng", title="English SDH")])
    result = CleanSubsPlugin(config).run(media, make_ctx(media))
    assert not result.process_file


# --------------------------------------------------------- standardise_audio
def test_standardise_audio_transcodes_non_target_codec():
    media = make_media([vstream(0), astream(1, codec="ac3", bitrate=640_000)])
    result = StandardiseAudioPlugin(VeloxConfig().standardise_audio).run(media, make_ctx(media))
    assert result.process_file
    assert result.remux is not None
    assert result.remux.codecs == {1: "aac"}
    assert result.remux.bitrates == {1: "640k"}


def test_standardise_audio_noop_when_already_target():
    media = make_media([vstream(0), astream(1, codec="aac")])
    result = StandardiseAudioPlugin(VeloxConfig().standardise_audio).run(media, make_ctx(media))
    assert not result.process_file


def test_standardise_audio_caps_bitrate_on_transcode():
    config = VeloxConfig().standardise_audio
    config.audio_bitrate_kbps = 192
    media = make_media([vstream(0), astream(1, codec="ac3", bitrate=640_000)])
    result = StandardiseAudioPlugin(config).run(media, make_ctx(media))
    assert result.process_file
    assert result.remux is not None
    assert result.remux.bitrates == {1: "192k"}


def test_standardise_audio_caps_matching_codec_above_cap():
    config = VeloxConfig().standardise_audio
    config.audio_bitrate_kbps = 192
    media = make_media([vstream(0), astream(1, codec="aac", bitrate=640_000)])
    result = StandardiseAudioPlugin(config).run(media, make_ctx(media))
    assert result.process_file
    assert result.remux is not None
    assert result.remux.codecs == {1: "aac"}
    assert result.remux.bitrates == {1: "192k"}


# --------------------------------------------------------------- hevc_gpu
def test_hevc_gpu_skips_when_already_hevc_in_mkv():
    media = make_media([vstream(0, codec="hevc"), astream(1, codec="aac")])
    result = HevcGpuPlugin(VeloxConfig().hevc_gpu).run(
        media, make_ctx(media, available={"hevc_videotoolbox"})
    )
    assert not result.process_file


def test_hevc_gpu_selects_videotoolbox_on_mac():
    media = make_media([vstream(0, codec="h264", bit_rate="5000000"), astream(1)])
    result = HevcGpuPlugin(VeloxConfig().hevc_gpu).run(
        media, make_ctx(media, available={"hevc_videotoolbox"})
    )
    assert result.process_file
    assert "hevc_videotoolbox" in result.output_args
    # NVENC-only flags must not be present for videotoolbox.
    assert "-spatial_aq:v" not in result.output_args


def test_hevc_gpu_nvenc_gets_aq_flags():
    media = make_media([vstream(0, codec="h264", bit_rate="5000000"), astream(1)])
    config = VeloxConfig().hevc_gpu
    config.bframes_enabled = True
    result = HevcGpuPlugin(config).run(media, make_ctx(media, available={"hevc_nvenc"}))
    assert result.process_file
    assert "-spatial_aq:v" in result.output_args
    assert "-bf" in result.output_args


def test_hevc_gpu_cpu_fallback():
    media = make_media([vstream(0, codec="h264", bit_rate="5000000"), astream(1)])
    result = HevcGpuPlugin(VeloxConfig().hevc_gpu).run(media, make_ctx(media, available=set()))
    assert result.process_file
    assert "libx265" in result.output_args


def test_hevc_gpu_no_fallback_errors():
    config = VeloxConfig().hevc_gpu
    config.allow_cpu_fallback = False
    media = make_media([vstream(0, codec="h264", bit_rate="5000000"), astream(1)])
    result = HevcGpuPlugin(config).run(media, make_ctx(media, available=set()))
    assert result.error is not None


def test_hevc_gpu_bitrate_cutoff_skips():
    config = VeloxConfig().hevc_gpu
    config.bitrate_cutoff = 5000
    media = make_media([vstream(0, codec="h264", bit_rate="4000000"), astream(1)])
    result = HevcGpuPlugin(config).run(
        media, make_ctx(media, available={"hevc_videotoolbox"})
    )
    assert not result.process_file


def test_hevc_gpu_uses_configured_encoder():
    media = make_media([vstream(0, codec="h264", bit_rate="5000000"), astream(1)])
    config = VeloxConfig().hevc_gpu
    config.encoder = "hevc_nvenc"
    result = HevcGpuPlugin(config).run(
        media, make_ctx(media, available={"hevc_nvenc", "hevc_videotoolbox"})
    )
    assert result.process_file
    assert "hevc_nvenc" in result.output_args
    assert "hevc_videotoolbox" not in result.output_args


def test_hevc_gpu_configured_encoder_falls_back_when_missing():
    media = make_media([vstream(0, codec="h264", bit_rate="5000000"), astream(1)])
    config = VeloxConfig().hevc_gpu
    config.encoder = "hevc_videotoolbox"
    # videotoolbox unavailable, but nvenc is -> falls back to auto probing.
    result = HevcGpuPlugin(config).run(media, make_ctx(media, available={"hevc_nvenc"}))
    assert result.process_file
    assert "hevc_nvenc" in result.output_args
    assert any("not available" in line for line in result.log)


def test_hevc_gpu_auto_selection():
    media = make_media([vstream(0, codec="h264", bit_rate="5000000"), astream(1)])
    config = VeloxConfig().hevc_gpu
    config.encoder = "auto"
    result = HevcGpuPlugin(config).run(
        media, make_ctx(media, available={"hevc_videotoolbox"})
    )
    assert result.process_file
    assert "hevc_videotoolbox" in result.output_args


# ------------------------------------------------------- size-target tuning
def _movie(bit_rate: str = "8000000", width: int = 1920, height: int = 1080, audio_kbps=256_000):
    return make_media(
        [
            vstream(0, codec="h264", bit_rate=bit_rate, width=width, height=height),
            astream(1, bitrate=audio_kbps),
        ],
        duration=7200.0,  # 2 hours
    )


def _bitrate_of(result) -> str:
    return result.output_args[result.output_args.index("-b:v") + 1]


def test_hevc_gpu_tier_bitrate_for_1080p24():
    # 0.033 bpp * 1920 * 1080 * 24fps / 1000 ~= 1642 kbps
    result = HevcGpuPlugin(VeloxConfig().hevc_gpu).run(
        _movie(), make_ctx(_movie(), available={"hevc_videotoolbox"})
    )
    assert result.process_file
    kbps = int(_bitrate_of(result).rstrip("k"))
    assert 1600 <= kbps <= 1680


def test_hevc_gpu_tier_scales_with_resolution():
    hd = HevcGpuPlugin(VeloxConfig().hevc_gpu)
    high = hd.run(_movie(), make_ctx(_movie(), available={"hevc_videotoolbox"}))
    low_media = _movie(width=1280, height=720)
    low = hd.run(low_media, make_ctx(low_media, available={"hevc_videotoolbox"}))
    assert int(_bitrate_of(high).rstrip("k")) > int(_bitrate_of(low).rstrip("k"))


def test_hevc_gpu_tier_is_independent_of_duration():
    # Same resolution/fps -> same bitrate, regardless of how long the file is.
    plugin = HevcGpuPlugin(VeloxConfig().hevc_gpu)
    short = make_media([vstream(0, codec="h264", bit_rate="9000000", width=1920, height=1080), astream(1, bitrate=256_000)], duration=1500.0)
    long = make_media([vstream(0, codec="h264", bit_rate="9000000", width=1920, height=1080), astream(1, bitrate=256_000)], duration=7200.0)
    assert _bitrate_of(plugin.run(short, make_ctx(short, available={"hevc_videotoolbox"}))) == \
           _bitrate_of(plugin.run(long, make_ctx(long, available={"hevc_videotoolbox"})))


def test_hevc_gpu_size_mode_bitrate_math():
    # 1500 MiB over 2h, minus ~256k audio, minus 2% overhead -> ~1457 kbps.
    config = VeloxConfig().hevc_gpu
    config.bitrate_mode = "size"
    result = HevcGpuPlugin(config).run(_movie(), make_ctx(_movie(), available={"hevc_videotoolbox"}))
    assert result.process_file
    kbps = int(_bitrate_of(result).rstrip("k"))
    assert 1400 <= kbps <= 1500


def test_hevc_gpu_never_exceeds_source_bitrate():
    config = VeloxConfig().hevc_gpu
    config.bitrate_mode = "size"
    media = _movie(bit_rate="900000")
    result = HevcGpuPlugin(config).run(media, make_ctx(media, available={"hevc_videotoolbox"}))
    assert _bitrate_of(result) == "900k"


def test_hevc_gpu_no_scale_filter_for_4k():
    media = _movie(width=3840, height=2160)
    result = HevcGpuPlugin(VeloxConfig().hevc_gpu).run(
        media, make_ctx(media, available={"hevc_videotoolbox"})
    )
    # The resolution cap was removed: 4K stays 4K, no scaling filter.
    assert result.process_file
    assert "-vf" not in result.output_args


def test_hevc_gpu_tier_scales_to_4k():
    # 0.033 bpp * 3840 * 2160 * 24fps / 1000 ~= 6569 kbps (~4x of 1080p).
    media = _movie(width=3840, height=2160)
    result = HevcGpuPlugin(VeloxConfig().hevc_gpu).run(
        media, make_ctx(media, available={"hevc_videotoolbox"})
    )
    kbps = int(_bitrate_of(result).rstrip("k"))
    assert 6500 <= kbps <= 6650



def test_hevc_gpu_quality_mode_uses_qv(monkeypatch):
    import platform as platform_mod

    monkeypatch.setattr(platform_mod, "system", lambda: "Darwin")
    monkeypatch.setattr(platform_mod, "machine", lambda: "arm64")
    config = VeloxConfig().hevc_gpu
    config.bitrate_mode = "quality"
    config.quality = 60
    media = _movie()
    result = HevcGpuPlugin(config).run(
        media, make_ctx(media, available={"hevc_videotoolbox"})
    )
    assert result.process_file
    assert "-q:v" in result.output_args
    assert result.output_args[result.output_args.index("-q:v") + 1] == "60"
    assert "-b:v" not in result.output_args
    # resolution-aware ceiling
    assert "-maxrate" in result.output_args


def test_hevc_gpu_quality_falls_back_on_non_apple_silicon(monkeypatch):
    import platform as platform_mod

    monkeypatch.setattr(platform_mod, "system", lambda: "Linux")
    monkeypatch.setattr(platform_mod, "machine", lambda: "x86_64")
    config = VeloxConfig().hevc_gpu
    config.bitrate_mode = "quality"
    media = _movie()
    result = HevcGpuPlugin(config).run(
        media, make_ctx(media, available={"hevc_videotoolbox"})
    )
    assert result.process_file
    assert "-q:v" not in result.output_args
    assert "-b:v" in result.output_args


def test_hevc_gpu_hardware_decode_flag():
    media = _movie()
    result = HevcGpuPlugin(VeloxConfig().hevc_gpu).run(
        media, make_ctx(media, available={"hevc_videotoolbox"})
    )
    assert result.input_args[:2] == ["-hwaccel", "videotoolbox"]

    config = VeloxConfig().hevc_gpu
    config.hardware_decode = False
    off = HevcGpuPlugin(config).run(media, make_ctx(media, available={"hevc_videotoolbox"}))
    assert "-hwaccel" not in off.input_args


def test_hevc_gpu_main10_default():
    result = HevcGpuPlugin(VeloxConfig().hevc_gpu).run(
        _movie(), make_ctx(_movie(), available={"hevc_videotoolbox"})
    )
    assert "main10" in result.output_args
    assert "p010le" in result.output_args


def test_hevc_gpu_videotoolbox_spatial_aq():
    result = HevcGpuPlugin(VeloxConfig().hevc_gpu).run(
        _movie(), make_ctx(_movie(), available={"hevc_videotoolbox"})
    )
    assert "-spatial_aq" in result.output_args
    assert "-spatial_aq:v" not in result.output_args




# ------------------------------------------------------- reorder_all_streams
def test_reorder_all_streams_sorts_video_first():
    media = make_media([astream(0, lang="eng"), vstream(1), sstream(2, lang="eng")])
    result = ReorderAllStreamsPlugin(VeloxConfig().reorder_all_streams).run(
        media, make_ctx(media)
    )
    assert result.process_file
    assert result.remux is not None
    # video (originally index 1) should come first
    assert result.remux.order[0] == 1


def test_reorder_all_streams_noop_when_ordered():
    media = make_media([vstream(0), astream(1, lang="eng", channels=6), astream(2, lang="eng", channels=2)])
    result = ReorderAllStreamsPlugin(VeloxConfig().reorder_all_streams).run(
        media, make_ctx(media)
    )
    assert not result.process_file


# ------------------------------------------------------------- file_size_check
def test_file_size_check_passes_within_bounds():
    original = make_media([vstream(0)], size=1_000_000)
    new = make_media([vstream(0)], size=600_000)
    ctx = PipelineContext(ffmpeg=FakeFFmpeg(), original_media=original)  # type: ignore[arg-type]
    result = FileSizeCheckPlugin(VeloxConfig().file_size_check).run(new, ctx)
    assert result.error is None


def test_file_size_check_errors_when_too_small():
    original = make_media([vstream(0)], size=1_000_000)
    new = make_media([vstream(0)], size=100_000)
    config = VeloxConfig().file_size_check
    config.lower_bound = 40
    ctx = PipelineContext(ffmpeg=FakeFFmpeg(), original_media=original)  # type: ignore[arg-type]
    result = FileSizeCheckPlugin(config).run(new, ctx)
    assert result.error is not None
    assert "lowerBound" in result.error


def test_file_size_check_lower_bound_disabled_by_default():
    original = make_media([vstream(0)], size=20_000_000_000)
    new = make_media([vstream(0)], size=1_500_000_000)  # 7.5% of the original
    ctx = PipelineContext(ffmpeg=FakeFFmpeg(), original_media=original)  # type: ignore[arg-type]
    result = FileSizeCheckPlugin(VeloxConfig().file_size_check).run(new, ctx)
    assert result.error is None


def test_file_size_check_absolute_bounds():
    original = make_media([vstream(0)], size=20 * 1024 * 1024 * 1024)
    small = make_media([vstream(0)], size=800 * 1024 * 1024)
    config = VeloxConfig().file_size_check
    config.min_size_mb = 1000
    config.max_size_mb = 2000
    ctx = PipelineContext(ffmpeg=FakeFFmpeg(), original_media=original)  # type: ignore[arg-type]
    result = FileSizeCheckPlugin(config).run(small, ctx)
    assert result.error is not None
    assert "min_size_mb" in result.error

    ok = make_media([vstream(0)], size=1500 * 1024 * 1024)
    assert FileSizeCheckPlugin(config).run(ok, ctx).error is None


def test_file_size_check_errors_when_too_large():
    original = make_media([vstream(0)], size=1_000_000)
    new = make_media([vstream(0)], size=2_000_000)
    ctx = PipelineContext(ffmpeg=FakeFFmpeg(), original_media=original)  # type: ignore[arg-type]
    result = FileSizeCheckPlugin(VeloxConfig().file_size_check).run(new, ctx)
    assert result.error is not None
    assert "upperBound" in result.error
