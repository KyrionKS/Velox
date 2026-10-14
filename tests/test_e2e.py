"""End-to-end test that exercises real ffmpeg (skipped if ffmpeg is missing).

Set ``VELOX_TEST_FFMPEG`` to point at an ffmpeg binary; otherwise ffmpeg/ffprobe
are looked up in ``PATH``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from velox.config import VeloxConfig
from velox.pipeline import Pipeline


def _find_tool(name: str) -> str | None:
    override = os.environ.get(f"VELOX_TEST_{name.upper()}")
    if override:
        return override
    return shutil.which(name)


FFMPEG = _find_tool("ffmpeg")
FFPROBE = _find_tool("ffprobe")

pytestmark = pytest.mark.skipif(
    not FFMPEG or not FFPROBE, reason="ffmpeg/ffprobe not available"
)


def _make_messy_sample(path: Path) -> None:
    """Like ``_make_sample`` but audio comes first (triggers stream reordering)."""
    subtitle = path.with_suffix(".srt")
    subtitle.write_text("1\n00:00:00,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
    command = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=660:duration=2",
        "-i", str(subtitle),
        # audio first, then video, then the second audio, then subs
        "-map", "1:a", "-map", "0:v", "-map", "2:a", "-map", "3:s",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-c:s", "srt",
        "-metadata:s:a:0", "language=eng",
        "-metadata:s:a:1", "language=jpn",
        "-metadata:s:s:0", "language=eng",
        str(path),
    ]
    subprocess.run(command, check=True, capture_output=True)
    subtitle.unlink()


def _pass_count(report) -> int:
    return len({o.output_path for o in report.outcomes if o.executed})


def _make_sample(path: Path) -> None:
    """Create a tiny mkv: h264 video + eng/jpn audio + eng subtitle."""
    subtitle = path.with_suffix(".srt")
    subtitle.write_text("1\n00:00:00,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
    command = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=660:duration=2",
        "-i", str(subtitle),
        "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-c:s", "srt",
        "-metadata:s:a:0", "language=eng",
        "-metadata:s:a:1", "language=jpn",
        "-metadata:s:s:0", "language=eng",
        str(path),
    ]
    subprocess.run(command, check=True, capture_output=True)
    subtitle.unlink()


def _make_pipeline(config: VeloxConfig) -> Pipeline:
    config.general.ffmpeg_path = FFMPEG  # type: ignore[assignment]
    config.general.ffprobe_path = FFPROBE  # type: ignore[assignment]
    config.general.keep_original = True
    return Pipeline(config)


def _stream_languages(media) -> list[str]:
    from velox.media import AUDIO, stream_language

    return [stream_language(s) for s in media.streams if s.get("codec_type") == AUDIO]


def test_end_to_end_clean_and_reorder(tmp_path: Path):
    source = tmp_path / "sample.mkv"
    _make_sample(source)

    config = VeloxConfig()
    # Disable the transcode to keep the test fast; exercise the remux plugins.
    config.hevc_gpu.enabled = False
    config.standardise_audio.enabled = False
    pipeline = _make_pipeline(config)

    report = pipeline.process(source)
    assert report.ok, report.error
    assert report.modified

    media = pipeline.probe(report.final_path)
    # Only the English audio track remains (jpn removed).
    assert _stream_languages(media) == ["eng"]
    # Video is first.
    assert media.streams[0]["codec_type"] == "video"
    # Backup created because keep_original defaults to true.
    assert (tmp_path / "sample.velox-orig.mkv").exists()


def test_end_to_end_cpu_transcode(tmp_path: Path):
    source = tmp_path / "sample.mkv"
    _make_sample(source)

    config = VeloxConfig()
    config.clean_audio.enabled = False
    config.clean_subs.enabled = False
    config.standardise_audio.enabled = False
    config.reorder_all_streams.enabled = False
    config.file_size_check.enabled = False
    config.hevc_gpu.try_use_gpu = False  # force CPU path (libx265)
    config.hevc_gpu.allow_cpu_fallback = True
    config.hevc_gpu.target_bitrate_multiplier = 1.0
    pipeline = _make_pipeline(config)

    report = pipeline.process(source)
    assert report.ok, report.error
    media = pipeline.probe(report.final_path)
    assert media.video_stream is not None
    assert media.video_stream["codec_name"] == "hevc"


def test_end_to_end_file_size_check_error(tmp_path: Path):
    source = tmp_path / "sample.mkv"
    _make_sample(source)

    config = VeloxConfig()
    config.hevc_gpu.enabled = False
    config.standardise_audio.enabled = False
    # Impossible lower bound -> the final file is smaller than 200%? set lower high.
    config.file_size_check.lower_bound = 99.9
    config.file_size_check.upper_bound = 100.1
    pipeline = _make_pipeline(config)

    report = pipeline.process(source)
    # Removing one audio track shrinks the file -> below the lower bound.
    assert not report.ok
    assert "lowerBound" in (report.error or "")


def test_dry_run_does_not_modify(tmp_path: Path):
    source = tmp_path / "sample.mkv"
    _make_sample(source)
    original_bytes = source.read_bytes()

    config = VeloxConfig()
    pipeline = _make_pipeline(config)

    report = pipeline.process(source, dry_run=True)
    assert report.ok
    assert source.read_bytes() == original_bytes
    assert not (tmp_path / "sample.velox-orig.mkv").exists()


# ----------------------------------------------------------- scratch / NAS
def test_scratch_dir_defaults_next_to_source(tmp_path: Path):
    pipeline = _make_pipeline(VeloxConfig())
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"x")
    scratch = pipeline._scratch_dir(source)
    try:
        # Same directory (i.e. same volume/NAS) as the source file.
        assert scratch.parent == tmp_path
        assert scratch.name.startswith(".velox-")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_scratch_dir_honours_configured_temp_dir(tmp_path: Path):
    config = VeloxConfig()
    config.general.temp_dir = str(tmp_path / "scratch")
    pipeline = _make_pipeline(config)
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"x")
    scratch = pipeline._scratch_dir(source)
    try:
        assert scratch.parent == tmp_path / "scratch"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_scratch_dir_supports_placeholder(tmp_path: Path):
    config = VeloxConfig()
    config.general.temp_dir = str(tmp_path / "{source_name}.scratch")
    pipeline = _make_pipeline(config)
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"x")
    scratch = pipeline._scratch_dir(source)
    try:
        assert scratch.parent == tmp_path / "movie.mkv.scratch"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_move_falls_back_when_cross_device(tmp_path: Path, monkeypatch):
    import velox.pipeline as pipeline_mod

    src = tmp_path / "a.bin"
    src.write_bytes(b"hello")
    dst = tmp_path / "b.bin"

    def cross_device(*args, **kwargs):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(pipeline_mod.os, "replace", cross_device)
    Pipeline._move(src, dst)
    assert dst.read_bytes() == b"hello"
    assert not src.exists()


def test_no_scratch_files_left_behind(tmp_path: Path):
    source = tmp_path / "sample.mkv"
    _make_sample(source)
    config = VeloxConfig()
    config.hevc_gpu.enabled = False
    config.standardise_audio.enabled = False
    pipeline = _make_pipeline(config)

    report = pipeline.process(source)
    assert report.ok
    assert not list(tmp_path.glob(".velox-*"))


def test_remux_plugins_merge_into_a_single_pass(tmp_path: Path):
    # audio-first file with an unwanted audio track: reorder + clean_audio (+
    # reorder_all_streams) must all fold into ONE ffmpeg pass.
    source = tmp_path / "messy.mkv"
    _make_messy_sample(source)

    config = VeloxConfig()
    config.hevc_gpu.enabled = False      # transcode would force its own pass
    config.standardise_audio.enabled = False
    pipeline = _make_pipeline(config)

    report = pipeline.process(source)
    assert report.ok, report.error
    assert _pass_count(report) == 1

    from velox.media import AUDIO, stream_language

    media = pipeline.probe(report.final_path)
    assert media.streams[0]["codec_type"] == "video"
    assert [stream_language(x) for x in media.streams if x.get("codec_type") == AUDIO] == ["eng"]


def test_transcode_forces_a_second_pass(tmp_path: Path):
    source = tmp_path / "messy.mkv"
    _make_messy_sample(source)

    config = VeloxConfig()
    config.standardise_audio.enabled = False
    config.reorder_all_streams.enabled = False
    config.file_size_check.enabled = False
    config.hevc_gpu.try_use_gpu = False
    config.hevc_gpu.target_codec = "hevc"
    pipeline = _make_pipeline(config)

    report = pipeline.process(source)
    assert report.ok, report.error
    # one merged remux pass + one transcode pass
    assert _pass_count(report) == 2


def test_observer_receives_pass_sizes_and_progress(tmp_path: Path):
    from velox.events import ProgressObserver

    source = tmp_path / "messy.mkv"
    _make_messy_sample(source)

    config = VeloxConfig()
    config.hevc_gpu.enabled = False
    config.standardise_audio.enabled = False
    pipeline = _make_pipeline(config)

    passes = []
    fractions = []

    class Obs(ProgressObserver):
        def on_pass_finished(self, event):
            passes.append(event)

        def on_progress(self, fraction):
            fractions.append(fraction)

    report = pipeline.process(source, observer=Obs())
    assert report.ok, report.error
    assert passes, "expected at least one pass event"
    assert passes[0].input_bytes > 0
    assert passes[0].output_bytes > 0
    assert passes[0].output_bytes < passes[0].input_bytes  # jpn audio removed
    assert report.saved_bytes > 0
    # progress reached 100%
    assert fractions and max(fractions) == 1.0


def test_cancel_stops_before_processing(tmp_path: Path):
    source = tmp_path / "sample.mkv"
    _make_sample(source)
    original = source.read_bytes()

    pipeline = _make_pipeline(VeloxConfig())
    report = pipeline.process(source, cancel=lambda: True)

    assert report.cancelled
    assert not report.ok
    assert source.read_bytes() == original


def test_report_has_pass_list(tmp_path: Path):
    source = tmp_path / "messy.mkv"
    _make_messy_sample(source)
    config = VeloxConfig()
    config.hevc_gpu.enabled = False
    config.standardise_audio.enabled = False
    pipeline = _make_pipeline(config)

    report = pipeline.process(source)
    assert report.ok
    assert len(report.passes) == 1
    assert report.passes[0].kind == "remux"
    assert report.original_bytes > report.final_bytes


def test_gui_worker_emits_signals(tmp_path: Path, qapp):
    pytest.importorskip("PySide6")

    from velox.gui.worker import PipelineWorker

    source = tmp_path / "messy.mkv"
    _make_messy_sample(source)
    config = VeloxConfig()
    config.general.ffmpeg_path = FFMPEG  # type: ignore[assignment]
    config.general.ffprobe_path = FFPROBE  # type: ignore[assignment]
    config.hevc_gpu.enabled = False
    config.standardise_audio.enabled = False

    worker = PipelineWorker(config, [source], False)
    passes: list = []
    reports: list = []
    worker.pass_finished.connect(passes.append)
    worker.run_finished.connect(reports.append)
    worker.run()

    assert passes, "worker should emit pass events"
    assert reports and reports[0] and reports[0][0].ok




