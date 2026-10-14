"""GUI smoke tests (offscreen).  Skipped when PySide6 is not installed."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from velox.config import VeloxConfig  # noqa: E402
from velox.events import PassEvent  # noqa: E402
from velox.gui.results import ResultsPanel  # noqa: E402
from velox.gui.settings import SettingsPanel  # noqa: E402


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
