"""Plugin base classes.

A Velox step analyses a file and returns a :class:`PluginResult` describing
what should happen to it (usually a stream-copy plan or an ffmpeg command).

The pipeline runner turns that into one concrete ``ffmpeg`` invocation and,
after every modification, re-probes the file so the next step sees fresh
stream information.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..config import PluginConfigBase

if TYPE_CHECKING:  # pragma: no cover
    from ..ffmpeg import FFmpegTools
    from ..media import MediaInfo


@dataclass
class RemuxPlan:
    """A stream-copy operation expressed with *absolute input* stream indices.

    Remux-only plugins produce one of these instead of raw ffmpeg arguments.
    Consecutive plans are merged by the pipeline into a single ffmpeg pass,
    which avoids reading/writing the (possibly network) file once per plugin.

    All indices refer to the stream's ``index`` in the input file, which stays
    stable no matter how many streams are dropped or reordered.
    """

    # absolute input indices of streams to remove
    drop: set[int] = field(default_factory=set)
    # explicit output order (absolute input indices); None = keep input order
    order: list[int] | None = None
    # per-stream codec override, e.g. {3: "aac"}
    codecs: dict[int, str] = field(default_factory=dict)
    # per-stream bitrate, e.g. {3: "192k"}
    bitrates: dict[int, str] = field(default_factory=dict)
    # per-stream metadata, e.g. {3: [("language", "eng")]}
    metadata: dict[int, list[tuple[str, str]]] = field(default_factory=dict)
    # per-stream disposition, e.g. {3: "default"} or {4: "0"} to clear
    dispositions: dict[int, str] = field(default_factory=dict)

    def merge(self, other: "RemuxPlan") -> None:
        self.drop |= set(other.drop)
        if other.order is not None:
            self.order = list(other.order)
        self.codecs.update(other.codecs)
        self.bitrates.update(other.bitrates)
        for index, items in other.metadata.items():
            self.metadata.setdefault(index, []).extend(items)
        self.dispositions.update(other.dispositions)

    def is_empty(self) -> bool:
        return not (
            self.drop
            or self.order is not None
            or self.codecs
            or self.bitrates
            or self.metadata
            or self.dispositions
        )


@dataclass
class PluginResult:
    """What a plugin decided to do with a file."""

    process_file: bool = False
    # args placed before ``-i`` (e.g. hwaccel)
    input_args: list[str] = field(default_factory=list)
    # args placed after ``-i`` (maps, codecs, metadata, ...); used for
    # non-mergeable operations such as transcoding
    output_args: list[str] = field(default_factory=list)
    # structured stream-copy operation (merged with neighbouring plugins)
    remux: RemuxPlan | None = None
    # output container, defaults to the input container
    container: str | None = None
    # whether the pipeline should re-evaluate the file afterwards
    requeue: bool = False
    # human readable log lines
    log: list[str] = field(default_factory=list)
    # set to abort processing with an error
    error: str | None = None

    def note(self, message: str) -> None:
        self.log.append(message)


@dataclass
class PipelineContext:
    """Shared state handed to every plugin."""

    ffmpeg: "FFmpegTools"
    original_media: "MediaInfo"
    dry_run: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class Plugin:
    """Base class for all Velox plugins."""

    key: str = ""
    name: str = ""
    stage: str = "Pre-processing"
    description: str = ""
    # Whether this plugin's file change can be batched with neighbouring
    # stream-copy plugins into one ffmpeg pass.  Plugins that need their own
    # pass (transcode) or that inspect the finished file (size check) set this
    # to False, which makes the pipeline flush the pending remux first.
    mergeable: bool = True

    def __init__(self, config: PluginConfigBase) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.config, "enabled", True))

    def run(self, media: "MediaInfo", ctx: PipelineContext) -> PluginResult:  # noqa: ARG002
        raise NotImplementedError
