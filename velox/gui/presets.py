"""Quality presets for the GUI (kept separate so they are easy to test)."""

from __future__ import annotations

# name -> bits per pixel (used by the "tier" bitrate mode)
QUALITY_PRESETS: dict[str, float] = {
    "small": 0.028,
    "balanced": 0.033,
    "high": 0.045,
}

PRESET_LABELS: list[tuple[str, str]] = [
    ("small", "Small file"),
    ("balanced", "Balanced (recommended)"),
    ("high", "High quality (bigger)"),
    ("custom", "Custom"),
]

DEFAULT_PRESET = "balanced"


def preset_for_bpp(bits_per_pixel: float, tolerance: float = 0.0005) -> str:
    """Return the preset name matching *bits_per_pixel* (else ``"custom"``)."""
    for name, value in QUALITY_PRESETS.items():
        if abs(value - bits_per_pixel) <= tolerance:
            return name
    return "custom"


def bpp_for_preset(name: str, fallback: float = 0.033) -> float:
    return QUALITY_PRESETS.get(name, fallback)
