"""Tests for pipeline commit/verification behaviour that need no ffmpeg."""

from __future__ import annotations

import logging
from pathlib import Path

from velox.config import VeloxConfig
from velox.events import ProgressObserver
from velox.media import MediaInfo
from velox.pipeline import Pipeline, RunReport
from velox.report import build_report


class _FakeFFmpeg:
    def __init__(self, media: MediaInfo | None = None) -> None:
        self.media = media

    def probe(self, path):
        assert self.media is not None
        return self.media


def _pipeline(config: VeloxConfig, media: MediaInfo | None = None) -> Pipeline:
    pipeline = Pipeline.__new__(Pipeline)
    pipeline.config = config
    pipeline.log = logging.getLogger("velox.test")
    pipeline.ffmpeg = _FakeFFmpeg(media)  # type: ignore[assignment]
    pipeline.plugins = []
    return pipeline


def _audio_media(path: Path) -> MediaInfo:
    return MediaInfo.from_probe(
        path,
        {
            "streams": [{"index": 0, "codec_type": "audio", "codec_name": "aac"}],
            "format": {"duration": "10.0"},
        },
        100,
    )


def test_output_dir_same_folder_keeps_original_and_uses_unique_name(tmp_path: Path):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"ORIGINAL")
    scratch = tmp_path / ".velox-test"
    scratch.mkdir()
    current = scratch / "00_movie.mkv"
    current.write_bytes(b"NEW")

    config = VeloxConfig()
    config.general.output_dir = str(tmp_path)
    config.general.keep_original = True

    result = _pipeline(config)._commit(current, source)

    assert result != source
    assert result.name == "movie (1).mkv"
    assert result.read_bytes() == b"NEW"
    assert source.read_bytes() == b"ORIGINAL"


def test_output_dir_does_not_overwrite_existing_target(tmp_path: Path):
    source_dir = tmp_path / "src"
    out_dir = tmp_path / "out"
    source_dir.mkdir()
    out_dir.mkdir()
    source = source_dir / "movie.mkv"
    source.write_bytes(b"ORIGINAL")
    existing = out_dir / "movie.mkv"
    existing.write_bytes(b"USER FILE")
    scratch = source_dir / ".velox-test"
    scratch.mkdir()
    current = scratch / "00_movie.mkv"
    current.write_bytes(b"NEW")

    config = VeloxConfig()
    config.general.output_dir = str(out_dir)

    result = _pipeline(config)._commit(current, source)

    assert existing.read_bytes() == b"USER FILE"
    assert result.name == "movie (1).mkv"
    assert result.read_bytes() == b"NEW"
    assert source.read_bytes() == b"ORIGINAL"


def test_container_change_does_not_delete_existing_sibling(tmp_path: Path):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"ORIGINAL MKV")
    sibling = tmp_path / "movie.mp4"
    sibling.write_bytes(b"EXISTING MP4")
    scratch = tmp_path / ".velox-test"
    scratch.mkdir()
    current = scratch / "00_movie.mp4"
    current.write_bytes(b"NEW MP4")

    config = VeloxConfig()
    config.general.keep_original = False

    result = _pipeline(config)._commit(current, source)

    assert sibling.read_bytes() == b"EXISTING MP4"
    assert result.name == "movie (1).mp4"
    assert result.read_bytes() == b"NEW MP4"
    assert not source.exists()


def test_verify_output_accepts_non_video_when_source_is_not_video(tmp_path: Path):
    media = _audio_media(tmp_path / "audio.m4a")
    config = VeloxConfig()
    pipeline = _pipeline(config, media)

    assert pipeline._verify_output(media.path, media, None, ProgressObserver()) is None


def test_report_uses_plus_when_file_got_bigger(tmp_path: Path):
    report = RunReport(
        source=tmp_path / "movie.mkv",
        final_path=tmp_path / "movie.mkv",
        modified=True,
        original_bytes=1000,
        final_bytes=1200,
    )

    text = build_report(report, VeloxConfig())

    assert "(+200 B" in text


def test_report_says_original_untouched_for_output_dir(tmp_path: Path):
    report = RunReport(
        source=tmp_path / "movie.mkv",
        final_path=tmp_path / "out" / "movie.mkv",
        modified=True,
        original_bytes=1000,
        final_bytes=1200,
        output_dir=str(tmp_path / "out"),
    )

    text = build_report(report, VeloxConfig())

    assert "Original unangetastet" in text
