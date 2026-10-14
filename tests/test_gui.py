"""GUI smoke tests (offscreen).  Skipped when PySide6 is not installed."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "minimal")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402  (ensures the platform plugin loads)

from velox.config import VeloxConfig  # noqa: E402
from velox.events import PassEvent  # noqa: E402
from velox.gui.results import ResultsPanel  # noqa: E402
from velox.gui.settings import SettingsPanel  # noqa: E402

FFMPEG = os.environ.get("VELOX_TEST_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("VELOX_TEST_FFPROBE") or shutil.which("ffprobe")


def test_settings_roundtrip(qapp):
    panel = SettingsPanel()
    config = VeloxConfig()
    config.hevc_gpu.bits_per_pixel = 0.045  # -> "high" preset
    config.hevc_gpu.bitrate_mode = "size"
    config.clean_audio.language = "deu"
    config.standardise_audio.audio_bitrate_kbps = 128
    config.clean_subs.sdh = False
    config.hevc_gpu.encoder = "libx265"
    config.clean_audio.default_audio = "eng"

    panel.load_from_config(config)
    result = panel.apply_to(VeloxConfig())

    assert result.hevc_gpu.bits_per_pixel == pytest.approx(0.045)
    assert result.hevc_gpu.bitrate_mode == "size"
    assert result.clean_audio.language == "deu"
    assert result.standardise_audio.audio_bitrate_kbps == 128
    assert result.clean_subs.sdh is False
    assert result.hevc_gpu.encoder == "libx265"
    assert result.clean_audio.default_audio == "eng"


def test_settings_default_audio_presets(qapp):
    from velox.gui.settings import DEFAULT_AUDIO_PRESETS

    panel = SettingsPanel()
    for label, value in DEFAULT_AUDIO_PRESETS.items():
        config = VeloxConfig()
        config.clean_audio.default_audio = value
        panel.load_from_config(config)
        assert panel.default_audio.currentText() == label
        assert panel.apply_to(VeloxConfig()).clean_audio.default_audio == value



def test_settings_commentary_applies_to_both(qapp):
    panel = SettingsPanel()
    panel.commentary.setChecked(True)
    result = panel.apply_to(VeloxConfig())
    assert result.clean_audio.commentary is True
    assert result.clean_subs.commentary is True


def test_results_panel_collects_rows(qapp):
    panel = ResultsPanel()
    panel.add_pass("movie.mkv", PassEvent(steps=["Clean Audio"], kind="remux",
                                          input_bytes=1000, output_bytes=600))
    panel.add_pass("movie.mkv", PassEvent(steps=["Transcode"], kind="transcode",
                                          input_bytes=600, output_bytes=300))
    assert panel.table.rowCount() == 2
    assert panel.table.item(0, 4).text() == "−400 B"


def _make_sample(path: Path) -> None:
    subprocess.run(
        [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=660:duration=1",
            # audio first (forces a reorder) + an unwanted jpn track
            "-map", "1:a", "-map", "0:v", "-map", "2:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-metadata:s:a:0", "language=eng",
            "-metadata:s:a:1", "language=jpn",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="ffmpeg not available")
def test_mainwindow_start_runs_worker_and_finishes(qapp, tmp_path, monkeypatch):
    """Exercises the real Start path: worker thread + queued signals + event loop."""
    import velox.gui.main_window as mw
    from velox.gui.main_window import MainWindow

    sample = tmp_path / "sample.mkv"
    _make_sample(sample)

    config = VeloxConfig()
    config.general.ffmpeg_path = FFMPEG
    config.general.ffprobe_path = FFPROBE
    config.hevc_gpu.enabled = False
    config.standardise_audio.enabled = False

    monkeypatch.setattr(mw, "load_user_config", lambda: config)
    monkeypatch.setattr(mw, "user_config_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(MainWindow, "_check_ffmpeg", lambda self: None)

    window = MainWindow()
    window._add_paths([str(sample)])
    window._start(dry_run=False)

    deadline = time.time() + 60
    while window._worker is not None and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.05)

    assert window._worker is None, "worker did not finish"
    assert window.results.table.rowCount() >= 1
    assert sample.exists()


def test_status_strip_reflects_progress(qapp, tmp_path, monkeypatch):
    """The bottom bar must move while files are still being processed."""
    import velox.gui.main_window as mw
    from velox.gui.main_window import MainWindow
    from velox.pipeline import RunReport

    config = VeloxConfig()
    monkeypatch.setattr(mw, "load_user_config", lambda: config)
    monkeypatch.setattr(mw, "user_config_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(MainWindow, "_check_ffmpeg", lambda self: None)

    window = MainWindow()
    a, b = "/tmp/A.mkv", "/tmp/B.mkv"
    window._add_paths([a, b])
    window._files_total = 2
    window._progress = {a: 0.0, b: 0.0}
    window._run_started_at = time.monotonic()
    window.overall_bar.setRange(0, 1000)
    window.status_label.setText("Starting…")

    window._on_file_started(a, "summary")
    assert "A.mkv" in window.status_label.text()

    window._on_file_pass(a, "Transcoding video (HEVC)")
    window._on_file_progress(a, 0.5)
    assert window.overall_bar.value() == 250  # half of one of two files
    assert "50%" in window.status_label.text()

    report = RunReport(source=Path(a), final_path=Path(a), original_bytes=100, final_bytes=100)
    window._on_file_finished(report)
    assert window.overall_bar.value() == 500
    assert "50%" in window.count_label.text()


def test_status_line_aggregates_parallel_files(qapp, tmp_path, monkeypatch):
    """With several files at once the status line aggregates instead of flickering."""
    import velox.gui.main_window as mw
    from velox.gui.main_window import MainWindow
    from velox.pipeline import RunReport

    config = VeloxConfig()
    monkeypatch.setattr(mw, "load_user_config", lambda: config)
    monkeypatch.setattr(mw, "user_config_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(MainWindow, "_check_ffmpeg", lambda self: None)

    window = MainWindow()
    a, b = "/tmp/A.mkv", "/tmp/B.mkv"
    window._add_paths([a, b])

    # one active file -> its own detail
    window._on_file_started(a, "summary")
    window._on_file_pass(a, "Encode Video")
    assert "A.mkv" in window.status_label.text()

    # a second file starts -> aggregate (no flicker between the two)
    window._on_file_started(b, "summary")
    window._on_file_pass(b, "Clean Audio Tracks")
    window._on_file_progress(a, 0.5)
    assert window.status_label.text() == "Processing 2 files…"

    # one finishes -> back to the remaining file's detail
    window._on_file_finished(
        RunReport(source=Path(b), final_path=Path(b), original_bytes=1, final_bytes=1)
    )
    assert "A.mkv" in window.status_label.text()

