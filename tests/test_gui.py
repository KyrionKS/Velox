"""GUI smoke tests (offscreen).  Skipped when PySide6 is not installed."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from velox.config import VeloxConfig  # noqa: E402
from velox.events import PassEvent  # noqa: E402
from velox.gui.results import ResultsPanel  # noqa: E402
from velox.gui.settings import SettingsPanel  # noqa: E402

FFMPEG = os.environ.get("VELOX_TEST_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("VELOX_TEST_FFPROBE") or shutil.which("ffprobe")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_settings_roundtrip(qapp):
    panel = SettingsPanel()
    config = VeloxConfig()
    config.hevc_gpu.bits_per_pixel = 0.045  # -> "high" preset
    config.hevc_gpu.bitrate_mode = "size"
    config.clean_audio.language = "deu"
    config.standardise_audio.audio_bitrate_kbps = 128
    config.clean_subs.sdh = False
    config.hevc_gpu.encoder = "libx265"

    panel.load_from_config(config)
    result = panel.apply_to(VeloxConfig())

    assert result.hevc_gpu.bits_per_pixel == pytest.approx(0.045)
    assert result.hevc_gpu.bitrate_mode == "size"
    assert result.clean_audio.language == "deu"
    assert result.standardise_audio.audio_bitrate_kbps == 128
    assert result.clean_subs.sdh is False
    assert result.hevc_gpu.encoder == "libx265"


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

