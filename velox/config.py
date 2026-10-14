"""Typed configuration for Velox.

The configuration is intentionally a set of dataclasses so that the future
PySide6 GUI can introspect it and build settings pages automatically.

It can be loaded from / saved to TOML.  Unknown keys are ignored (with a
warning) so a config written for a newer version never crashes the run.
"""

from __future__ import annotations

import copy
import logging
import os
import sys
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - only on very old Pythons
    tomllib = None  # type: ignore[assignment]

log = logging.getLogger("velox")

# Plugin keys, in the default pipeline order requested by the user.
DEFAULT_ORDER = [
    "reorder_streams",
    "clean_audio",
    "clean_subs",
    "standardise_audio",
    "hevc_gpu",
    "reorder_all_streams",
    "file_size_check",
]

# Tdarr plugin ids, kept for traceability (docs / migration).
TDARR_IDS = {
    "reorder_streams": "Tdarr_Plugin_lmg1_Reorder_Streams",
    "clean_audio": "Tdarr_Plugin_MC93_Migz3CleanAudio",
    "clean_subs": "Tdarr_Plugin_MC93_Migz4CleanSubs",
    "standardise_audio": "Tdarr_Plugin_00td_action_standardise_audio_stream_codecs",
    "hevc_gpu": "Tdarr_Plugin_AC_HEVC_GPU",
    "reorder_all_streams": "Tdarr_Plugin_00td_action_re_order_all_streams_v2",
    "file_size_check": "Tdarr_Plugin_a9he_New_file_size_check",
}


@dataclass
class GeneralConfig:
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    # Keep the original file (a ".velox-orig" backup) instead of deleting it.
    # False (default) frees the space: after a successful run only the new file
    # remains, and any leftover backup for that file is cleaned up.
    keep_original: bool = False
    # If set, finished files are written here and the original is never touched.
    output_dir: str | None = None
    # Scratch directory for the temporary files written during processing.
    #   "source" (default) -> a hidden folder next to each source file, i.e. on
    #                         the same volume (e.g. your NAS). Large temp files
    #                         never touch the Mac's SSD and the final rename is
    #                         free (no copy).
    #   "/path/to/scratch" -> use this directory instead. Supports the
    #                         placeholders {source_dir} and {source_name}.
    temp_dir: str = "source"
    # Also process files that are not detected as video (default: skip).
    process_non_video: bool = False


@dataclass
class PluginConfigBase:
    enabled: bool = True


@dataclass
class ReorderStreamsConfig(PluginConfigBase):
    """Tdarr_Plugin_lmg1_Reorder_Streams - move the video stream to the front."""


@dataclass
class CleanAudioConfig(PluginConfigBase):
    """Tdarr_Plugin_MC93_Migz3CleanAudio - keep only chosen audio languages."""

    language: str = "eng,und"
    commentary: bool = False
    tag_language: str = ""
    tag_title: bool = False


@dataclass
class CleanSubsConfig(PluginConfigBase):
    """Tdarr_Plugin_MC93_Migz4CleanSubs - keep only chosen subtitle languages."""

    language: str = "eng"
    commentary: bool = False
    sdh: bool = True
    tag_language: str = ""


@dataclass
class StandardiseAudioConfig(PluginConfigBase):
    """Tdarr_Plugin_00td_action_standardise_audio_stream_codecs."""

    audio_codec: str = "aac"
    # Cap the audio bitrate (kbps) so audio cannot eat the video size budget.
    # 0 = keep the source bitrate (original behaviour). Tracks already in the
    # target codec are only re-encoded when they exceed this cap.
    audio_bitrate_kbps: int = 0


@dataclass
class HevcGpuConfig(PluginConfigBase):
    """Tdarr_Plugin_AC_HEVC_GPU - video transcode (GPU if available)."""

    target_codec: str = "hevc"
    encoder: str = "hevc_videotoolbox"

    # How the video bitrate is chosen:
    #   "tier"    -> resolution & framerate aware (bits_per_pixel * W * H * fps).
    #                One setting for everything: a 2h movie becomes ~1.5 GB, a
    #                45min episode ~0.6 GB, with consistent quality. (default)
    #   "size"    -> fixed output size per file (target_size_mb). Good when you
    #                know exactly how big a movie should be.
    #   "quality" -> constant quality (Apple Silicon VideoToolbox -q:v, NVENC
    #                -cq, x265 -crf) with a resolution-aware bitrate ceiling.
    #                Best quality per byte; size is not guaranteed.
    bitrate_mode: str = "tier"

    # --- tier mode ---------------------------------------------------------
    # Bits per pixel per frame. 0.03 = small, 0.04-0.05 = more quality.
    # 0.033 targets roughly 1.5 GB for a 1080p/24 feature film.
    bits_per_pixel: float = 0.033

    # --- size mode ---------------------------------------------------------
    # Desired output size in MiB.
    target_size_mb: float = 1500.0
    # Reserve a little space for container/muxing overhead (percent).
    size_overhead_percent: float = 2.0
    # Assumed bitrate (kbps) for audio streams whose bitrate cannot be detected.
    fallback_audio_kbps: int = 256

    # --- quality mode ------------------------------------------------------
    quality: int = 55

    # Clamp the computed video bitrate (kbps). The upper clamp leaves room for
    # 4K/8K so high resolutions are not artificially limited.
    min_video_kbps: int = 300
    max_video_kbps: int = 40000

    # Apple Silicon: let VideoToolbox decode the source too (faster).
    hardware_decode: bool = True

    try_use_gpu: bool = True
    # Velox extension: fall back to CPU (libx265/libx264) when no GPU encoder
    # works.  The Tdarr original aborts in that case.
    allow_cpu_fallback: bool = True
    cpu_preset: str = "medium"
    container: str = "mkv"
    bitrate_cutoff: int = 0
    # 10-bit output (Main10) compresses better and reduces banding at low
    # bitrates.  Fully supported on Apple Silicon; disable for old players.
    enable_10bit: bool = True
    bframes_enabled: bool = False
    bframes_value: int = 5
    # VideoToolbox/NVENC spatial adaptive quantization (better detail at low
    # bitrates; ignored automatically when unsupported).
    spatial_aq: bool = True
    force_conform: bool = False
    exclude_gpus: str = ""
    allowed_codecs: str = "vp9,av1"


@dataclass
class ReorderAllStreamsConfig(PluginConfigBase):
    """Tdarr_Plugin_00td_action_re_order_all_streams_v2."""

    process_order: str = "codecs,channels,languages,streamTypes"
    languages: str = ""
    channels: str = "7.1,5.1,2,1"
    codecs: str = ""
    stream_types: str = "video,audio,subtitle"


@dataclass
class FileSizeCheckConfig(PluginConfigBase):
    """Tdarr_Plugin_a9he_New_file_size_check."""

    # Relative bounds (percent of the ORIGINAL file). lower_bound = 0 disables
    # the lower check, which is what you want when shrinking large sources to a
    # small target (e.g. 20 GB -> 1.5 GB is only ~7%).
    upper_bound: float = 110
    lower_bound: float = 0
    # Absolute bounds in MiB. 0 disables. Use these to guarantee a size window,
    # e.g. min_size_mb = 1000, max_size_mb = 2000 for "1-2 GB per movie".
    min_size_mb: float = 0
    max_size_mb: float = 0


@dataclass
class VeloxConfig:
    general: GeneralConfig = field(default_factory=GeneralConfig)
    order: list[str] = field(default_factory=lambda: list(DEFAULT_ORDER))

    reorder_streams: ReorderStreamsConfig = field(default_factory=ReorderStreamsConfig)
    clean_audio: CleanAudioConfig = field(default_factory=CleanAudioConfig)
    clean_subs: CleanSubsConfig = field(default_factory=CleanSubsConfig)
    standardise_audio: StandardiseAudioConfig = field(default_factory=StandardiseAudioConfig)
    hevc_gpu: HevcGpuConfig = field(default_factory=HevcGpuConfig)
    reorder_all_streams: ReorderAllStreamsConfig = field(
        default_factory=ReorderAllStreamsConfig
    )
    file_size_check: FileSizeCheckConfig = field(default_factory=FileSizeCheckConfig)

    # ---------------------------------------------------------------- helpers
    def plugin(self, key: str) -> PluginConfigBase:
        value = getattr(self, key)
        assert isinstance(value, PluginConfigBase)
        return value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def copy(self) -> "VeloxConfig":
        return copy.deepcopy(self)


def _coerce(value: Any, target_type: Any) -> Any:
    """Best-effort coercion of a TOML value into the dataclass field type."""
    if value is None:
        return None
    origin = get_origin(target_type)
    if origin is not None:
        args = [a for a in get_args(target_type) if a is not type(None)]
        target_type = args[0] if args else str
    if target_type is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if target_type is int and not isinstance(value, bool):
        return int(value)
    if target_type is float:
        return float(value)
    if target_type is str:
        return str(value)
    return value


def _apply_section(instance: Any, data: dict[str, Any], section_name: str) -> None:
    valid = {f.name: f for f in fields(instance)}
    hints = get_type_hints(type(instance))
    for key, raw in data.items():
        if key not in valid:
            log.warning("Ignoring unknown config key %r in [%s]", key, section_name)
            continue
        setattr(instance, key, _coerce(raw, hints.get(key, Any)))


def _toml_to_dict(path: Path) -> dict[str, Any]:
    if tomllib is None:  # pragma: no cover
        raise RuntimeError("Reading TOML requires Python 3.11+")
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_config(path: str | Path | None = None) -> VeloxConfig:
    """Load config from *path*, falling back to defaults."""
    config = VeloxConfig()
    if path is None:
        return config

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    data = _toml_to_dict(path)

    if "general" in data:
        _apply_section(config.general, data["general"], "general")

    pipeline = data.get("pipeline", {})
    if isinstance(pipeline, dict) and "order" in pipeline:
        order = pipeline["order"]
        if isinstance(order, list):
            config.order = [str(item) for item in order]

    plugins = data.get("plugins", {})
    if isinstance(plugins, dict):
        for key, section in plugins.items():
            if not isinstance(section, dict):
                continue
            if not hasattr(config, key) or key == "general":
                log.warning("Ignoring unknown plugin section [plugins.%s]", key)
                continue
            _apply_section(getattr(config, key), section, f"plugins.{key}")

    return config


# --------------------------------------------------------------------- writer
def _format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (list, tuple)):
        inner = ", ".join(_format_toml_value(item) for item in value)
        return f"[{inner}]"
    if value is None:
        return '""'
    return str(value)


def user_config_path() -> Path:
    """Where the GUI stores its settings (per user)."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Velox"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "velox"
    return base / "config.toml"


def load_user_config() -> VeloxConfig:
    """Load the per-user config, or defaults when none exists yet."""
    path = user_config_path()
    if path.exists():
        return load_config(path)
    return VeloxConfig()


def save_config(config: VeloxConfig, path: str | Path) -> None:
    """Write *config* to a TOML file (used by the GUI / ``--init-config``)."""
    lines: list[str] = []
    lines.append("[general]")
    for f in fields(config.general):
        lines.append(f"{f.name} = {_format_toml_value(getattr(config.general, f.name))}")
    lines.append("")
    lines.append("[pipeline]")
    lines.append(f"order = {_format_toml_value(config.order)}")
    for key in DEFAULT_ORDER:
        plugin = config.plugin(key)
        lines.append("")
        lines.append(f"[plugins.{key}]")
        for f in fields(plugin):
            lines.append(f"{f.name} = {_format_toml_value(getattr(plugin, f.name))}")
    lines.append("")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")


__all__ = [
    "VeloxConfig",
    "GeneralConfig",
    "PluginConfigBase",
    "DEFAULT_ORDER",
    "TDARR_IDS",
    "load_config",
    "save_config",
    "user_config_path",
    "load_user_config",
    "is_dataclass",
]
