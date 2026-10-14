"""Qt worker that runs the pipeline on a background thread."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from ..config import VeloxConfig
from ..errors import VeloxError
from ..events import PassEvent, ProgressObserver
from ..pipeline import Pipeline, RunReport


class _QtObserver(ProgressObserver):
    """Bridges pipeline callbacks to Qt signals (queued to the GUI thread)."""

    def __init__(self, worker: "PipelineWorker") -> None:
        self.worker = worker

    def on_run_started(self, files: list[Path]) -> None:
        self.worker.run_started.emit(len(files))

    def on_file_started(self, path: Path, summary: str) -> None:
        self.worker.file_started.emit(str(path), summary)

    def on_step_started(self, key: str, name: str) -> None:
        self.worker.step_started.emit(name)

    def on_step_note(self, message: str) -> None:
        self.worker.note.emit(message)

    def on_pass_started(self, description: str) -> None:
        self.worker.pass_started.emit(description)

    def on_progress(self, fraction: float) -> None:
        self.worker.progress.emit(float(fraction))

    def on_pass_finished(self, event: PassEvent) -> None:
        self.worker.pass_finished.emit(event)

    def on_file_finished(self, report: RunReport) -> None:
        self.worker.file_finished.emit(report)

    def on_error(self, message: str) -> None:
        self.worker.failed.emit(message)


class PipelineWorker(QObject):
    run_started = Signal(int)
    file_started = Signal(str, str)
    step_started = Signal(str)
    note = Signal(str)
    pass_started = Signal(str)
    progress = Signal(float)
    pass_finished = Signal(object)
    file_finished = Signal(object)
    run_finished = Signal(object)  # list[RunReport]
    failed = Signal(str)

    def __init__(self, config: VeloxConfig, files: list[Path], dry_run: bool) -> None:
        super().__init__()
        self.config = config
        self.files = [Path(f) for f in files]
        self.dry_run = dry_run
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @Slot()
    def run(self) -> None:
        reports: list[Any] = []
        logger = logging.getLogger("velox.gui")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False

        try:
            pipeline = Pipeline(self.config, logger)
        except VeloxError as exc:
            self.failed.emit(str(exc))
            self.run_finished.emit(reports)
            return

        observer = _QtObserver(self)
        observer.on_run_started(self.files)
        for file in self.files:
            if self._cancel.is_set():
                break
            reports.append(
                pipeline.process(
                    file,
                    dry_run=self.dry_run,
                    observer=observer,
                    cancel=self._cancel.is_set,
                )
            )
        observer.on_run_finished(reports)
        self.run_finished.emit(reports)
