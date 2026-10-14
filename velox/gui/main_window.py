"""The Velox main window."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QThread
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
    QVBoxLayout,
    QWidget,
)

from ..config import (
    VeloxConfig,
    load_user_config,
    save_config,
    user_config_path,
)
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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Velox")
        self.resize(1040, 780)
        self.config: VeloxConfig = load_user_config()
        self._thread: QThread | None = None
        self._worker: PipelineWorker | None = None
        self._files_total = 0
        self._files_done = 0
        self._current_file = ""

        self._build()
        self.settings.load_from_config(self.config)
        self._check_ffmpeg()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

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
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.file_list, 1)

        buttons = QHBoxLayout()
        add_files = QPushButton("Add files…")
        add_files.clicked.connect(self._add_files)
        add_folder = QPushButton("Add folder…")
        add_folder.clicked.connect(self._add_folder)
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self._remove_selected)
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self.file_list.clear())
        for button in (add_files, add_folder, remove, clear):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return page

    def _build_progress(self) -> QWidget:
        box = QGroupBox("Progress")
        layout = QVBoxLayout(box)

        self.current_label = QLabel("Current file: —")
        layout.addWidget(self.current_label)

        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("File:"))
        self.file_bar = QProgressBar()
        self.file_bar.setRange(0, 1000)
        self.file_bar.setValue(0)
        file_row.addWidget(self.file_bar, 1)
        layout.addLayout(file_row)

        overall_row = QHBoxLayout()
        overall_row.addWidget(QLabel("Overall:"))
        self.overall_bar = QProgressBar()
        self.overall_bar.setRange(0, 1)
        self.overall_bar.setValue(0)
        overall_row.addWidget(self.overall_bar, 1)
        layout.addLayout(overall_row)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setFixedHeight(150)
        layout.addWidget(self.log)
        return box

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
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
        if self._thread is not None:
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
        self.overall_bar.setRange(0, self._files_total)
        self.overall_bar.setValue(0)
        self._set_running(True)
        self._log(("Dry run — " if dry_run else "") + f"Starting {len(files)} file(s)…")

        self._thread = QThread(self)
        self._worker = PipelineWorker(self.config, [Path(f) for f in files], dry_run)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.run_started.connect(self._on_run_started)
        self._worker.file_started.connect(self._on_file_started)
        self._worker.step_started.connect(self._on_step_started)
        self._worker.note.connect(self._log)
        self._worker.pass_started.connect(lambda text: self._log(f"▶ {text}"))
        self._worker.progress.connect(self._on_progress)
        self._worker.pass_finished.connect(self._on_pass_finished)
        self._worker.file_finished.connect(self._on_file_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.run_finished.connect(self._on_run_finished)
        self._worker.run_finished.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.stop_button.setEnabled(False)
            self._log("Stopping after the current pass…")

    def _cleanup_thread(self) -> None:
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None

    def _set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.dry_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    # --------------------------------------------------------------- signals
    def _on_run_started(self, total: int) -> None:
        self.overall_bar.setRange(0, max(1, total))
        self.overall_bar.setValue(0)

    def _on_file_started(self, path: str, summary: str) -> None:
        self._current_file = Path(path).name
        self.current_label.setText(f"Current file: {self._current_file}")
        self.file_bar.setValue(0)
        self._log(f"— {self._current_file}: {summary}")

    def _on_step_started(self, name: str) -> None:
        self._log(f"[{name}]")

    def _on_progress(self, fraction: float) -> None:
        self.file_bar.setValue(int(max(0.0, min(1.0, fraction)) * 1000))

    def _on_pass_finished(self, event: PassEvent) -> None:
        self.results.add_pass(self._current_file or "?", event)
        self._log(f"   ✓ {event.describe()}")

    def _on_file_finished(self, report: RunReport) -> None:
        self.results.add_report(report)
        self._files_done += 1
        self.overall_bar.setValue(self._files_done)
        if report.cancelled:
            self._log(f"■ {report.source.name}: stopped")
        elif report.error:
            self._log(f"✗ {report.source.name}: {report.error}")
        elif report.skipped:
            self._log(f"⏭ {report.source.name}: skipped ({report.reason})")
        elif report.modified:
            saved = report.saved_bytes
            pct = report.saved_percent
            self._log(
                f"✓ {report.source.name}: {fmt_bytes(report.original_bytes)} → "
                f"{fmt_bytes(report.final_bytes)} (saved {fmt_bytes(saved)}, {pct:.1f}%)"
            )
        else:
            self._log(f"= {report.source.name}: nothing to change")

    def _on_failed(self, message: str) -> None:
        self._log(f"✗ {message}")

    def _on_run_finished(self, reports) -> None:
        self._set_running(False)
        failed = [r for r in reports if not r.ok and not r.cancelled]
        self._log(f"Done. {len(reports)} file(s), {len(failed)} failed.")
        self.results._update_summary()

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
        self.settings.ffmpeg_path.setText(tools.ffmpeg_path)
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
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
        event.accept()
