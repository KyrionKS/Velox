"""Plugin registry.

The order here is the default pipeline order and can be overridden with
``[pipeline] order = [...]`` in the config.
"""

from __future__ import annotations

from .base import PipelineContext, Plugin, PluginResult, RemuxPlan
from .clean_audio import CleanAudioPlugin
from .clean_subs import CleanSubsPlugin
from .file_size_check import FileSizeCheckPlugin
from .hevc_gpu import HevcGpuPlugin
from .reorder_all_streams import ReorderAllStreamsPlugin
from .reorder_streams import ReorderStreamsPlugin
from .standardise_audio import StandardiseAudioPlugin

PLUGIN_CLASSES: list[type[Plugin]] = [
    ReorderStreamsPlugin,
    CleanAudioPlugin,
    CleanSubsPlugin,
    StandardiseAudioPlugin,
    HevcGpuPlugin,
    ReorderAllStreamsPlugin,
    FileSizeCheckPlugin,
]

PLUGIN_MAP: dict[str, type[Plugin]] = {cls.key: cls for cls in PLUGIN_CLASSES}

__all__ = [
    "PLUGIN_CLASSES",
    "PLUGIN_MAP",
    "CleanAudioPlugin",
    "CleanSubsPlugin",
    "FileSizeCheckPlugin",
    "HevcGpuPlugin",
    "PipelineContext",
    "Plugin",
    "PluginResult",
    "RemuxPlan",
    "ReorderAllStreamsPlugin",
    "ReorderStreamsPlugin",
    "StandardiseAudioPlugin",
]
