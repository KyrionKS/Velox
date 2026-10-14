"""Background worker that runs the pipeline off the GUI thread.

Files are processed concurrently (``concurrency``, default 2).  Each file gets
its own :class:`ProgressObserver` that tags every event with the file path, so
the GUI can show one row per file.  A plain Python thread pool is used and Qt
signals are emitted from those threads; the GUI connections use
``Qt.QueuedConnection`` so widget updates always happen on the main thread.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from ..config import VeloxConfig
from ..errors import VeloxError
from ..events import PassEvent, ProgressObserver
from ..pipeline import Pipeline, RunReport


class _FileObserver(ProgressObserver):
    """Pipeline callbacks for one file, forwarded as Qt signals with its path."""

    def __init__(self, worker: "PipelineWorker", source: Path) -> None:
        self.worker = worker
        self.source = str(source)

    def on_file_started(self, path: Path, summary: str) -> None:
        self.worker.file_started.emit(str(path), summary)

    def on_step_started(self, key: str, name: str) -> None:
        self.worker.file_step.emit(self.source, name)

    def on_step_note(self, message: str) -> None:
        self.worker.note.emit(message)

    def on_pass_started(self, description: str) -> None:
        self.worker.file_pass.emit(self.source, description)

    def on_progress(self, fraction: float) -> None:
        self.worker.file_progress.emit(self.source, float(fraction))

    def on_pass_finished(self, event: PassEvent) -> None:
        self.worker.pass_finished.emit(self.source, event)

    def on_file_finished(self, report: RunReport) -> None:
        self.worker.file_finished.emit(report)

    def on_error(self, message: str) -> None:
        self.worker.failed.emit(message)


class PipelineWorker(QObject):
    run_started = Signal(int)
    file_started = Signal(str, str)
    file_step = Signal(str, str)
    file_pass = Signal(str, str)
    file_progress = Signal(str, float)
    pass_finished = Signal(str, object)  # path, PassEvent
    file_finished = Signal(object)
    run_finished = Signal(object)  # list[RunReport]
    note = Signal(str)
    failed = Signal(str)

    def __init__(self, config: VeloxConfig, files: list[Path], dry_run: bool) -> None:
        super().__init__()
        self.config = config
        self.files = [Path(f) for f in files]
        self.dry_run = dry_run
        self.concurrency = max(1, int(getattr(config.general, "concurrency", 1) or 1))
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- control
    def start(self) -> None:
        self._thread = threading.Thread(target=self.run, name="velox-worker", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # ------------------------------------------------------------------- work
    def run(self) -> None:
        """Run the pipeline.  Safe to call directly (e.g. in tests)."""
        reports: list[RunReport] = []
        logger = logging.getLogger("velox.gui")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False

        try:
            try:
                pipeline = Pipeline(self.config, logger)
            except VeloxError as exc:
                self.failed.emit(str(exc))
                return

            self.run_started.emit(len(self.files))

            def task(path: Path) -> RunReport:
                observer = _FileObserver(self, path)
                return pipeline.process(
                    path,
                    dry_run=self.dry_run,
                    observer=observer,
                    cancel=self._cancel.is_set,
                )

            workers = min(self.concurrency, max(1, len(self.files)))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="velox") as pool:
                reports = list(pool.map(task, self.files))
        except Exception as exc:  # pragma: no cover - defensive
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.run_finished.emit(reports)
