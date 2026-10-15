"""The Velox pipeline engine.

Runs the configured plugins in order against a file.  Consecutive stream-copy
(remux) plugins are **merged into a single ffmpeg pass** so the file is read and
written as few times as possible — important for large files on a NAS.  Plugins
that need their own pass (the video transcode) flush the pending remux first.

After every pass the file is re-probed so the next plugin sees fresh stream
information (each step sees the result of the previous one).

All work happens in a scratch directory next to the source file and the result
is only moved into place once the whole pipeline succeeded, so the original is
never left in a half-processed state.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .config import DEFAULT_ORDER, VeloxConfig
from .errors import CancelledError, PluginError, VeloxError
from .events import PassEvent, ProgressObserver
from .ffmpeg import FFmpegTools
from .logging_setup import get_logger
from .media import MediaInfo, stream_type
from .plugins import PLUGIN_MAP, PipelineContext, Plugin, PluginResult, RemuxPlan
from .report import write_report

TYPE_SPEC = {"video": "v", "audio": "a", "subtitle": "s", "data": "d", "attachment": "t"}


def _size_of(path: Path) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


@dataclass
class PluginOutcome:
    plugin: Plugin
    result: PluginResult
    command: list[str] | None = None
    executed: bool = False
    output_path: Path | None = None


@dataclass
class RunReport:
    source: Path
    final_path: Path | None = None
    modified: bool = False
    skipped: bool = False
    cancelled: bool = False
    reason: str = ""
    error: str | None = None
    original_bytes: int = 0
    final_bytes: int = 0
    elapsed: float = 0.0
    original_media: MediaInfo | None = None
    final_media: MediaInfo | None = None
    report_path: Path | None = None
    kept_original: bool = False
    passes: list[PassEvent] = field(default_factory=list)
    outcomes: list[PluginOutcome] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and not self.cancelled

    @property
    def saved_bytes(self) -> int:
        if not self.modified or self.final_bytes <= 0:
            return 0
        return self.original_bytes - self.final_bytes

    @property
    def saved_percent(self) -> float:
        if not self.original_bytes:
            return 0.0
        return (self.saved_bytes / self.original_bytes) * 100.0


class Pipeline:
    def __init__(self, config: VeloxConfig, logger: logging.Logger | None = None) -> None:
        self.config = config
        self.log = logger or get_logger()
        self.ffmpeg = FFmpegTools(config.general.ffmpeg_path, config.general.ffprobe_path, self.log)
        self.ffmpeg.resolve()
        self.plugins = self._build_plugins()

    # ------------------------------------------------------------- building
    def _build_plugins(self) -> list[Plugin]:
        order = self.config.order or list(DEFAULT_ORDER)
        plugins: list[Plugin] = []
        for key in order:
            cls = PLUGIN_MAP.get(key)
            if cls is None:
                self.log.warning("Unknown plugin key %r in pipeline order, skipping", key)
                continue
            plugins.append(cls(self.config.plugin(key)))
        return plugins

    @property
    def enabled_plugins(self) -> list[Plugin]:
        return [p for p in self.plugins if p.enabled]

    # -------------------------------------------------------------- probing
    def probe(self, path: str | Path) -> MediaInfo:
        return self.ffmpeg.probe(path)

    # -------------------------------------------------------------- running
    def process(
        self,
        path: str | Path,
        *,
        dry_run: bool = False,
        observer: ProgressObserver | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> RunReport:
        source = Path(path)
        report = RunReport(source=source)
        observer = observer or ProgressObserver()
        started = time.monotonic()

        try:
            original = self.ffmpeg.probe(source)
        except VeloxError as exc:
            report.error = str(exc)
            observer.on_error(str(exc))
            observer.on_file_finished(report)
            return report

        report.original_bytes = original.size
        report.original_media = original

        if not original.is_video and not self.config.general.process_non_video:
            report.skipped = True
            report.reason = "not a video file"
            report.final_path = source
            report.final_bytes = original.size
            observer.on_file_finished(report)
            return report

        self.log.info("Processing %s (%s)", source.name, original.summary())
        observer.on_file_started(source, original.summary())

        ctx = PipelineContext(
            ffmpeg=self.ffmpeg,
            original_media=original,
            dry_run=dry_run,
        )

        current = source
        modified = False
        would_change = False
        tmpdir: Path | None = None

        try:
            if not dry_run:
                tmpdir = self._scratch_dir(source)
                self.log.info("Scratch directory: %s", tmpdir)
                if not self._same_filesystem(tmpdir, source.parent):
                    self.log.warning(
                        "Scratch directory is on a different volume than the source; "
                        "the finished file will be copied instead of renamed."
                    )

            pending: RemuxPlan | None = None
            group_outcomes: list[PluginOutcome] = []
            virtual: MediaInfo | None = None
            pass_index = 0
            probe_cache: dict[Path, MediaInfo] = {}

            def probe(path: Path) -> MediaInfo:
                if path not in probe_cache:
                    probe_cache[path] = self.ffmpeg.probe(path)
                return probe_cache[path]

            def flush() -> None:
                nonlocal pending, group_outcomes, virtual
                nonlocal current, modified, pass_index, would_change
                plan, outcomes = pending, group_outcomes
                pending, group_outcomes = None, []
                if plan is None or plan.is_empty():
                    return
                base = probe(current)
                args = self._build_remux_args(plan, base)
                if not args:
                    return
                if cancel is not None and cancel():
                    raise CancelledError("Processing stopped by user")
                output_path = (
                    Path("<output>")
                    if dry_run
                    else (tmpdir or source.parent)
                    / f"{pass_index:02d}_{source.stem}.{base.container}"
                )
                command = [
                    self.ffmpeg.ffmpeg_path,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(current),
                    *args,
                    str(output_path),
                ]
                steps = [outcome.plugin.name for outcome in outcomes]
                observer.on_pass_started(" + ".join(steps))
                self.log.info("   → ffmpeg (merged remux): %s", " ".join(command))
                for outcome in outcomes:
                    outcome.command = command
                if dry_run:
                    virtual = self._apply_plan_to_media(base, plan)
                    would_change = True
                    return
                input_bytes = _size_of(current)
                self.ffmpeg.run(
                    command[1:],
                    duration=base.duration,
                    progress=observer.on_progress,
                    cancel=cancel,
                )
                output_bytes = _size_of(output_path)
                for outcome in outcomes:
                    outcome.executed = True
                    outcome.output_path = output_path
                event = PassEvent(
                    steps=steps,
                    kind="remux",
                    input_bytes=input_bytes,
                    output_bytes=output_bytes,
                    command=command,
                    plan=plan,
                )
                report.passes.append(event)
                observer.on_pass_finished(event)
                current = output_path
                modified = True
                pass_index += 1
                probe_cache.clear()

            for plugin in self.plugins:
                if cancel is not None and cancel():
                    raise CancelledError("Processing stopped by user")
                if not plugin.enabled:
                    self.log.debug("Plugin %s disabled, skipping", plugin.key)
                    continue

                # Plugins that need the real file (transcode, size check) must
                # see the result of the pending stream-copy pass first.
                if not plugin.mergeable:
                    flush()

                if virtual is not None:
                    media = virtual
                else:
                    media = probe(current)
                    if pending is not None and pending.drop:
                        media = self._filter_media(media, pending.drop)

                observer.on_step_started(plugin.key, plugin.name)
                result = plugin.run(media, ctx)
                outcome = PluginOutcome(plugin=plugin, result=result)
                report.outcomes.append(outcome)

                self.log.info("[%s]", plugin.name)
                for line in result.log:
                    self.log.info("   %s", line)
                    observer.on_step_note(line)

                if result.error:
                    raise PluginError(result.error, plugin.key)

                if not result.process_file:
                    continue

                # Structured stream-copy operation: merge into the current pass.
                if result.remux is not None:
                    if pending is None:
                        pending = RemuxPlan()
                    pending.merge(result.remux)
                    group_outcomes.append(outcome)
                    continue

                # Non-mergeable operation (e.g. transcode): finish the pending
                # stream-copy pass first so it sees the cleaned file.
                flush()

                base = probe(current)
                out_container = result.container or base.container
                output_path = (
                    Path("<output>")
                    if dry_run
                    else (tmpdir or source.parent)
                    / f"{pass_index:02d}_{source.stem}.{out_container}"
                )
                command = self._build_command(result, current, output_path)
                outcome.command = command

                observer.on_pass_started(plugin.name)
                self.log.info("   → ffmpeg: %s", " ".join(command))

                if dry_run:
                    would_change = True
                    continue

                input_bytes = _size_of(current)
                self.ffmpeg.run(
                    command[1:],
                    duration=base.duration,
                    progress=observer.on_progress,
                    cancel=cancel,
                )
                output_bytes = _size_of(output_path)
                outcome.executed = True
                outcome.output_path = output_path
                event = PassEvent(
                    steps=[plugin.name],
                    kind="transcode" if plugin.key == "hevc_gpu" else plugin.key,
                    input_bytes=input_bytes,
                    output_bytes=output_bytes,
                    command=command,
                )
                report.passes.append(event)
                observer.on_pass_finished(event)
                current = output_path
                modified = True
                pass_index += 1
                probe_cache.clear()

            flush()

            report.modified = modified

            if modified and not dry_run:
                if self.config.general.verify_output:
                    problem = self._verify_output(
                        current, original, self._expected_video_codec(report), observer
                    )
                    if problem:
                        raise PluginError(f"Output verification failed: {problem}", "verify")
                report.final_path = self._commit(current, source)
                report.kept_original = self.config.general.keep_original
                if not self.config.general.keep_original:
                    self._remove_backup(source)
                self.log.info("✓ Wrote %s", report.final_path)
            else:
                report.final_path = source
                if not modified and not would_change:
                    self.log.info("= Nothing to change")
                elif dry_run and would_change:
                    self.log.info("(dry-run) no files were written")

            if report.final_path is not None:
                report.final_bytes = _size_of(report.final_path)
                if modified:
                    try:
                        report.final_media = self.ffmpeg.probe(report.final_path)
                    except VeloxError:
                        report.final_media = None
            if modified:
                self._maybe_write_report(report)

        except CancelledError as exc:
            report.cancelled = True
            report.error = str(exc)
            self.log.warning("■ %s: %s", source.name, exc)
            self._maybe_write_report(report)
        except (VeloxError, OSError) as exc:
            report.error = str(exc)
            self.log.error("✗ %s: %s", source.name, exc)
            observer.on_error(str(exc))
            self._maybe_write_report(report)
        finally:
            if tmpdir is not None:
                shutil.rmtree(tmpdir, ignore_errors=True)

        report.elapsed = time.monotonic() - started
        observer.on_file_finished(report)
        return report

    # ------------------------------------------------------------- commands
    def _build_command(
        self, result: PluginResult, input_path: Path, output_path: Path
    ) -> list[str]:
        args = [
            self.ffmpeg.ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            *result.input_args,
            "-i",
            str(input_path),
            *result.output_args,
            str(output_path),
        ]
        return args

    # --------------------------------------------------------- remux merging
    @staticmethod
    def _final_order(plan: RemuxPlan, media: MediaInfo) -> list[int]:
        input_indices = [int(s["index"]) for s in media.streams]
        if plan.order is not None:
            return [i for i in plan.order if i in input_indices and i not in plan.drop]
        return [i for i in input_indices if i not in plan.drop]

    @classmethod
    def _build_remux_args(cls, plan: RemuxPlan, media: MediaInfo) -> list[str]:
        """Turn a merged :class:`RemuxPlan` into a single ffmpeg argument list."""
        input_indices = [int(s["index"]) for s in media.streams]
        final = cls._final_order(plan, media)
        if not final:
            return []
        unchanged = (
            not plan.drop
            and not plan.codecs
            and not plan.bitrates
            and not plan.metadata
            and not plan.dispositions
            and (plan.order is None or final == input_indices)
        )
        if unchanged:
            return []

        by_index = {int(s["index"]): s for s in media.streams}
        args: list[str] = []
        for index in final:
            args += ["-map", f"0:{index}"]
        args += ["-c", "copy"]

        # Output position of each kept stream within its own type.
        counter: dict[str, int] = {}
        out_index: dict[int, int] = {}
        for index in final:
            kind = stream_type(by_index.get(index, {}))
            out_index[index] = counter.get(kind, 0)
            counter[kind] = counter.get(kind, 0) + 1

        for index, codec in plan.codecs.items():
            if index in out_index:
                spec = TYPE_SPEC.get(stream_type(by_index[index]), "a")
                args += [f"-c:{spec}:{out_index[index]}", codec]
        for index, bitrate in plan.bitrates.items():
            if index in out_index:
                spec = TYPE_SPEC.get(stream_type(by_index[index]), "a")
                args += [f"-b:{spec}:{out_index[index]}", bitrate]
        for index, items in plan.metadata.items():
            if index not in out_index:
                continue
            spec = TYPE_SPEC.get(stream_type(by_index[index]), "a")
            for key, value in items:
                args += [f"-metadata:s:{spec}:{out_index[index]}", f"{key}={value}"]

        for index, value in plan.dispositions.items():
            if index not in out_index:
                continue
            spec = TYPE_SPEC.get(stream_type(by_index[index]), "a")
            args += [f"-disposition:{spec}:{out_index[index]}", value]

        args += ["-max_muxing_queue_size", "9999"]
        return args

    @staticmethod
    def _filter_media(media: MediaInfo, drop: set[int]) -> MediaInfo:
        """A view of *media* with *drop* streams removed (indices preserved)."""
        probe = dict(media.probe)
        probe["streams"] = [s for s in media.streams if int(s["index"]) not in drop]
        return MediaInfo.from_probe(media.path, probe, media.size)

    @classmethod
    def _apply_plan_to_media(cls, media: MediaInfo, plan: RemuxPlan) -> MediaInfo:
        """Approximate the post-remux media state (used for dry-run previews)."""
        by_index = {int(s["index"]): s for s in media.streams}
        streams = []
        for index in cls._final_order(plan, media):
            stream = dict(by_index[index])
            if index in plan.codecs:
                stream["codec_name"] = plan.codecs[index]
            tags = dict(stream.get("tags") or {})
            for key, value in plan.metadata.get(index, []):
                tags[key] = value
            if tags:
                stream["tags"] = tags
            streams.append(stream)
        probe = dict(media.probe)
        probe["streams"] = streams
        return MediaInfo.from_probe(media.path, probe, media.size)

    # ------------------------------------------------------------ reports
    def _maybe_write_report(self, report: RunReport) -> None:
        """Schreibt einen Bericht neben das Ergebnis (falls aktiviert)."""
        if not self.config.general.write_report:
            return
        if not (report.modified or report.error or report.cancelled):
            return
        try:
            report.report_path = write_report(report, self.config)
            self.log.info("Report: %s", report.report_path)
        except OSError as exc:  # pragma: no cover - best effort
            self.log.warning("Could not write report: %s", exc)

    # --------------------------------------------------------- verification
    def _verify_output(
        self,
        path: Path,
        original: MediaInfo,
        expected_codec: str | None,
        observer: ProgressObserver,
    ) -> str | None:
        """Integrity-check the processed file before it replaces the original."""
        observer.on_step_note("Verifying output…")
        self.log.info("Verifying output…")
        try:
            media = self.ffmpeg.probe(path)
        except VeloxError as exc:
            return str(exc)

        if not media.is_video:
            return "no video stream in the output"

        if original.duration > 0 and media.duration > 0:
            tolerance = max(1.0, original.duration * 0.005)
            if abs(media.duration - original.duration) > tolerance:
                return (
                    f"duration mismatch ({media.duration:.1f}s vs original "
                    f"{original.duration:.1f}s)"
                )

        if expected_codec:
            stream = media.video_stream or {}
            codec = str(stream.get("codec_name") or "").lower()
            if codec and codec != expected_codec.lower():
                return f"video codec is {codec}, expected {expected_codec}"

        if self.config.general.verify_full_decode:
            problem = self._decode_check(path)
            if problem:
                return problem

        self.log.info("✓ Output verified")
        return None

    def _decode_check(self, path: Path) -> str | None:
        command = [
            self.ffmpeg.ffmpeg_path,
            "-v",
            "error",
            "-xerror",
            "-i",
            str(path),
            "-f",
            "null",
            "-",
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0 or result.stderr.strip():
            tail = "\n".join(result.stderr.strip().splitlines()[-5:])
            return f"decoding reported errors: {tail}"
        return None

    def _expected_video_codec(self, report: RunReport) -> str | None:
        for outcome in report.outcomes:
            if outcome.plugin.key == "hevc_gpu" and outcome.executed:
                codec = str(getattr(self.config.hevc_gpu, "target_codec", "") or "")
                return codec or None
        return None

    # ------------------------------------------------------------ scratch dir
    def _scratch_dir(self, source: Path) -> Path:
        """Create and return a scratch directory for *source*.

        By default (``temp_dir = "source"``) this is a hidden folder next to the
        source file, so temporary data stays on the same volume (e.g. the NAS)
        and the final move is a free rename instead of a copy.
        """
        configured = str(self.config.general.temp_dir or "").strip()
        if configured.lower() in {"", "source", "auto", "same"}:
            parent = source.parent
        else:
            parent = Path(
                configured.format(source_dir=str(source.parent), source_name=source.name)
            ).expanduser()
        parent.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=".velox-", dir=str(parent)))

    @staticmethod
    def _same_filesystem(a: Path, b: Path) -> bool:
        try:
            return os.stat(a).st_dev == os.stat(b).st_dev
        except OSError:
            return False

    @staticmethod
    def _move(src: Path, dst: Path) -> None:
        """Rename when possible, otherwise copy+delete (cross-filesystem)."""
        try:
            os.replace(src, dst)
        except OSError:
            shutil.move(str(src), str(dst))

    def _remove_backup(self, source: Path) -> None:
        """Delete a leftover ".velox-orig" backup (we only want the new file)."""
        backup = source.with_name(f"{source.stem}.velox-orig{source.suffix}")
        try:
            backup.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best effort
            pass

    # --------------------------------------------------------------- commit
    def _commit(self, current: Path, source: Path) -> Path:
        general = self.config.general
        final_container = current.suffix.lower().lstrip(".")
        source_container = source.suffix.lower().lstrip(".")

        # 1) Explicit output directory: never touch the original.
        if general.output_dir:
            out_dir = Path(general.output_dir).expanduser()
            out_dir.mkdir(parents=True, exist_ok=True)
            target = out_dir / f"{source.stem}.{final_container}"
            if target.exists():
                target.unlink()
            shutil.move(str(current), str(target))
            return target

        # 2) Same container: replace in place (with optional backup).
        if final_container == source_container:
            if general.keep_original:
                backup = source.with_name(f"{source.stem}.velox-orig{source.suffix}")
                shutil.copy2(source, backup)
            self._move(current, source)
            return source

        # 3) Container changed: write a sibling file.
        target = source.with_suffix(f".{final_container}")
        if target.exists():
            target.unlink()
        self._move(current, target)
        if not general.keep_original:
            source.unlink(missing_ok=True)
        return target
