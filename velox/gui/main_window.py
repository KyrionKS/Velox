"""The Velox main window."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import VeloxConfig, load_user_config, save_config, user_config_path
from ..errors import VeloxError
from ..events import PassEvent, fmt_bytes
from ..ffmpeg import FFmpegTools
from ..pipeline import RunReport
from .results import ResultsPanel
from .settings import SettingsPanel
from .worker import PipelineWorker

VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts", ".mts",
    ".webm", ".wmv", ".flv", ".mpg", ".mpeg", ".vob",
}
FILE_FILTER = (
    "Video files (*.mkv *.mp4 *.m4v *.avi *.mov *.ts *.m2ts *.mts *.webm "
    "*.wmv *.flv *.mpg *.mpeg *.vob);;All files (*)"
)


def _fmt_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Velox")
        self.setMinimumSize(720, 520)
        self.resize(920, 640)
        self.config: VeloxConfig = load_user_config()
        self._worker: PipelineWorker | None = None
        self._files_total = 0
        self._files_done = 0
        self._current_file = ""
        self._step_text = ""
        self._pass_started_at: float | None = None
        self._run_started_at: float | None = None

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._update_elapsed)

        self._build()
        self.settings.load_from_config(self.config)
        self._check_ffmpeg()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_files_tab(), "Files")
        self.settings = SettingsPanel()
        self.tabs.addTab(self.settings, "Settings")
        self.results = ResultsPanel()
        self.tabs.addTab(self.results, "Results")
        layout.addWidget(self.tabs, 1)

        layout.addWidget(self._build_progress())
        layout.addLayout(self._build_buttons())

    def _build_files_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setAlternatingRowColors(True)
        layout.addWidget(self.file_list, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        for label, slot in (
            ("Add files…", self._add_files),
            ("Add folder…", self._add_folder),
            ("Remove", self._remove_selected),
            ("Clear", lambda: self.file_list.clear()),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return page

    def _build_progress(self) -> QWidget:
        box = QGroupBox("Progress")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(12, 6, 12, 8)
        layout.setSpacing(6)

        header = QHBoxLayout()
        self.current_label = QLabel("Idle")
        self.current_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        font = self.current_label.font()
        font.setBold(True)
        self.current_label.setFont(font)
        self.count_label = QLabel("")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self.current_label, 1)
        header.addWidget(self.count_label)
        layout.addLayout(header)

        self.file_bar = QProgressBar()
        self.file_bar.setRange(0, 100)
        self.file_bar.setValue(0)
        self.file_bar.setTextVisible(True)
        self.file_bar.setFormat("%p%")
        layout.addWidget(self.file_bar)

        self.step_label = QLabel("Add files and press Start.")
        self.step_label.setObjectName("stepLabel")
        layout.addWidget(self.step_label)

        overall = QHBoxLayout()
        overall.addWidget(QLabel("Overall"))
        self.overall_bar = QProgressBar()
        self.overall_bar.setRange(0, 1)
        self.overall_bar.setValue(0)
        self.overall_bar.setTextVisible(True)
        self.overall_bar.setFormat("%v / %m files")
        overall.addWidget(self.overall_bar, 1)
        layout.addLayout(overall)

        self.details_button = QToolButton()
        self.details_button.setText("Details")
        self.details_button.setCheckable(True)
        self.details_button.setArrowType(Qt.ArrowType.RightArrow)
        self.details_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.details_button.toggled.connect(self._toggle_details)
        layout.addWidget(self.details_button)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setFixedHeight(140)
        self.log.setVisible(False)
        layout.addWidget(self.log)
        return box

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        load = QPushButton("Load settings")
        load.clicked.connect(self._load_settings)
        save = QPushButton("Save settings")
        save.clicked.connect(self._save_settings)
        row.addWidget(load)
        row.addWidget(save)
        row.addStretch(1)

        self.dry_button = QPushButton("Dry run")
        self.dry_button.clicked.connect(lambda: self._start(dry_run=True))
        self.start_button = QPushButton("Start")
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(lambda: self._start(dry_run=False))
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop)
        row.addWidget(self.dry_button)
        row.addWidget(self.start_button)
        row.addWidget(self.stop_button)
        return row

    def _toggle_details(self, checked: bool) -> None:
        self.log.setVisible(checked)
        self.details_button.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )

    # ------------------------------------------------------------------ files
    def _add_paths(self, paths: list[str]) -> None:
        existing = {
            self.file_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.file_list.count())
        }
        added = 0
        for raw in paths:
            path = str(Path(raw))
            if path in existing:
                continue
            item = QListWidgetItem(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.file_list.addItem(item)
            existing.add(path)
            added += 1
        if added:
            self._log(f"Added {added} file(s).")

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Select video files", "", FILE_FILTER)
        if paths:
            self._add_paths(paths)

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select a folder (scanned recursively)")
        if not folder:
            return
        found = [
            str(p)
            for p in sorted(Path(folder).rglob("*"))
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
        ]
        if not found:
            QMessageBox.information(self, "Velox", "No video files found in that folder.")
            return
        self._add_paths(found)

    def _remove_selected(self) -> None:
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))

    def selected_files(self) -> list[str]:
        return [
            self.file_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.file_list.count())
        ]

    # ------------------------------------------------------------------- run
    def _start(self, *, dry_run: bool) -> None:
        if self._worker is not None:
            return
        files = self.selected_files()
        if not files:
            QMessageBox.warning(self, "Velox", "Please add at least one file first.")
            return

        self.config = self.settings.apply_to(self.config)
        try:
            save_config(self.config, user_config_path())
        except OSError as exc:  # pragma: no cover - best effort
            self._log(f"Could not save settings: {exc}")

        self.results.clear()
        self.log.clear()
        self._files_total = len(files)
        self._files_done = 0
        self._step_text = "Preparing…"
        self._pass_started_at = None
        self._run_started_at = time.monotonic()
        self.overall_bar.setRange(0, self._files_total)
        self.overall_bar.setValue(0)
        self.current_label.setText("Preparing…")
        self.file_bar.setValue(0)
        self.step_label.setText("Preparing…")
        self.count_label.setText(f"0 of {self._files_total}")
        self._set_running(True)
        self._elapsed_timer.start()
        self._log(("Dry run — " if dry_run else "") + f"Starting {len(files)} file(s)…")

        worker = PipelineWorker(self.config, [Path(f) for f in files], dry_run)
        queued = Qt.ConnectionType.QueuedConnection
        worker.run_started.connect(self._on_run_started, queued)
        worker.file_started.connect(self._on_file_started, queued)
        worker.step_started.connect(self._on_step_started, queued)
        worker.note.connect(self._log, queued)
        worker.pass_started.connect(self._on_pass_started, queued)
        worker.progress.connect(self._on_progress, queued)
        worker.pass_finished.connect(self._on_pass_finished, queued)
        worker.file_finished.connect(self._on_file_finished, queued)
        worker.failed.connect(self._on_failed, queued)
        worker.run_finished.connect(self._on_run_finished, queued)

        self._worker = worker
        worker.start()

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.stop_button.setEnabled(False)
            self._log("Stopping after the current pass…")

    def _release_worker(self) -> None:
        self._worker = None

    def _set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.dry_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def _update_elapsed(self) -> None:
        if self._run_started_at is None:
            return
        elapsed = _fmt_duration(time.monotonic() - self._run_started_at)
        self.count_label.setText(
            f"File {min(self._files_done + 1, self._files_total)} of "
            f"{self._files_total}  ·  {elapsed}"
        )

    # --------------------------------------------------------------- signals
    def _on_run_started(self, total: int) -> None:
        self.overall_bar.setRange(0, max(1, total))
        self.overall_bar.setValue(0)
        self._update_elapsed()

    def _on_file_started(self, path: str, summary: str) -> None:
        self._current_file = Path(path).name
        self.current_label.setText(self._current_file)
        self.file_bar.setValue(0)
        self._step_text = "Analysing streams…"
        self._pass_started_at = None
        self.step_label.setText(self._step_text)
        self._update_elapsed()
        self._log(f"— {self._current_file}: {summary}")

    def _on_step_started(self, name: str) -> None:
        self._log(f"[{name}]")

    def _on_pass_started(self, description: str) -> None:
        self._step_text = description
        self._pass_started_at = time.monotonic()
        self.file_bar.setValue(0)
        self.step_label.setText(description)
        self._log(f"▶ {description}")

    def _on_progress(self, fraction: float) -> None:
        fraction = max(0.0, min(1.0, fraction))
        self.file_bar.setValue(int(fraction * 100))
        if self._pass_started_at is not None and fraction > 0.02:
            elapsed = time.monotonic() - self._pass_started_at
            remaining = elapsed * (1.0 - fraction) / fraction
            self.step_label.setText(
                f"{self._step_text}  ·  {int(fraction * 100)}%  ·  "
                f"~{_fmt_duration(remaining)} left"
            )
        else:
            self.step_label.setText(f"{self._step_text}  ·  {int(fraction * 100)}%")

    def _on_pass_finished(self, event: PassEvent) -> None:
        self.results.add_pass(self._current_file or "?", event)
        self._log(f"   ✓ {event.describe()}")

    def _on_file_finished(self, report: RunReport) -> None:
        self.results.add_report(report)
        self._files_done += 1
        self.overall_bar.setValue(self._files_done)
        self._update_elapsed()
        if report.cancelled:
            self._log(f"■ {report.source.name}: stopped")
        elif report.error:
            self._log(f"✗ {report.source.name}: {report.error}")
        elif report.skipped:
            self._log(f"⏭ {report.source.name}: skipped ({report.reason})")
        elif report.modified:
            self._log(
                f"✓ {report.source.name}: {fmt_bytes(report.original_bytes)} → "
                f"{fmt_bytes(report.final_bytes)} (saved {fmt_bytes(report.saved_bytes)}, "
                f"{report.saved_percent:.1f}%)"
            )
        else:
            self._log(f"= {report.source.name}: nothing to change")

    def _on_failed(self, message: str) -> None:
        self._log(f"✗ {message}")

    def _on_run_finished(self, reports) -> None:
        self._set_running(False)
        self._elapsed_timer.stop()
        self._run_started_at = None
        self.file_bar.setValue(100)
        failed = [r for r in reports if not r.ok and not r.cancelled]
        self.step_label.setText("Finished." if not failed else f"Finished with {len(failed)} error(s).")
        self.count_label.setText(f"{self._files_done} of {self._files_total}")
        self._log(f"Done. {len(reports)} file(s), {len(failed)} failed.")
        self.results._update_summary()
        QTimer.singleShot(0, self._release_worker)

    # ------------------------------------------------------------- settings
    def _load_settings(self) -> None:
        self.config = load_user_config()
        self.settings.load_from_config(self.config)
        self._log("Settings loaded.")

    def _save_settings(self) -> None:
        self.config = self.settings.apply_to(self.config)
        save_config(self.config, user_config_path())
        self._log(f"Settings saved to {user_config_path()}")

    # ----------------------------------------------------------------- ffmpeg
    def _check_ffmpeg(self) -> None:
        tools = FFmpegTools(
            self.config.general.ffmpeg_path,
            self.config.general.ffprobe_path,
            logging.getLogger("velox.gui"),
        )
        try:
            tools.resolve()
        except VeloxError as exc:
            self._log(f"✗ {exc}")
            answer = QMessageBox.warning(
                self,
                "ffmpeg not found",
                f"{exc}\n\nLocate the ffmpeg binary now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._locate_ffmpeg()
            return
        self.config.general.ffmpeg_path = tools.ffmpeg_path
        self.config.general.ffprobe_path = tools.ffprobe_path
        self.settings.ffmpeg_path.setText(tools.ffmpeg_path)
        self.settings.ffprobe_path.setText(tools.ffprobe_path)
        self.statusBar().showMessage(f"ffmpeg: {tools.ffmpeg_path}")
        self._log(f"ffmpeg: {tools.ffmpeg_path}")

    def _locate_ffmpeg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Locate the ffmpeg binary")
        if not path:
            return
        self.config.general.ffmpeg_path = path
        probe = Path(path).with_name("ffprobe")
        if probe.exists():
            self.config.general.ffprobe_path = str(probe)
        self.settings.ffmpeg_path.setText(path)
        self.settings.ffprobe_path.setText(self.config.general.ffprobe_path)
        try:
            save_config(self.config, user_config_path())
        except OSError:
            pass
        self._check_ffmpeg()

    # ------------------------------------------------------------------- misc
    def _log(self, message: str) -> None:
        self.log.appendPlainText(message)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        worker = self._worker
        if worker is not None:
            worker.cancel()
            worker.join(5000)
        event.accept()
