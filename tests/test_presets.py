"""Quality preset mapping tests (no Qt needed)."""

from __future__ import annotations

from velox.gui.presets import QUALITY_PRESETS, bpp_for_preset, preset_for_bpp


def test_preset_for_bpp_matches_known_values():
    assert preset_for_bpp(QUALITY_PRESETS["small"]) == "small"
    assert preset_for_bpp(QUALITY_PRESETS["balanced"]) == "balanced"
    assert preset_for_bpp(QUALITY_PRESETS["high"]) == "high"


def test_preset_for_bpp_falls_back_to_custom():
    assert preset_for_bpp(0.011) == "custom"


def test_bpp_for_preset():
    assert bpp_for_preset("small") == QUALITY_PRESETS["small"]
    assert bpp_for_preset("does-not-exist", fallback=0.123) == 0.123
