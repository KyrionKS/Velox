"""The Velox main window (macOS-oriented layout).

* a toolbar holds the actions (Add files/folder, Start, Dry run, Stop);
* a source-list **sidebar** switches between Files / Settings / Results;
* the Files page is a table with **one row per file** (progress, current step,
  result) so several files can be processed at the same time;
* a slim status strip at the bottom shows the overall progress and a
  collapsible Details log.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QRectF, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPalette,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
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

SIDEBAR_WIDTH = 190
COL_FILE, COL_PROGRESS, COL_STEP, COL_RESULT = range(4)


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
        self.setMinimumSize(860, 580)
        self.resize(1080, 740)
        self.config: VeloxConfig = load_user_config()
        self._worker: PipelineWorker | None = None
        self._files_total = 0
        self._files_done = 0
        self._run_started_at: float | None = None
        self._log_visible = False
        self._qsettings = QSettings("Velox", "Velox")

        # per-file state, keyed by the full path
        self._rows: dict[str, int] = {}
        self._bars: dict[str, QProgressBar] = {}
        self._step: dict[str, str] = {}
        self._progress: dict[str, float] = {}
        self._status_text: dict[str, str] = {}
        self._pass_started: dict[str, float | None] = {}
        self._active: set[str] = set()

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._update_elapsed)

        self._build()
        self.settings.load_from_config(self.config)
        self._restore_geometry()
        self._check_ffmpeg()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        self._build_actions()
        self._build_toolbar()
        self._build_menus()

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())
        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedWidth(1)
        divider.setFrameShape(QFrame.Shape.NoFrame)
        body.addWidget(divider)
        body.addWidget(self._build_content(), 1)
        layout.addLayout(body, 1)

        layout.addWidget(self._build_status_strip())

    def _toolbar_icon(self, shape: str) -> QIcon:
        """Draw a play/stop glyph in the current text colour, so it stays
        visible on both light and dark toolbars."""
        color = self.palette().color(QPalette.ColorRole.WindowText)
        icon = QIcon()
        for size in (16, 32, 64):
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            if shape == "play":
                path = QPainterPath()
                path.moveTo(size * 0.26, size * 0.15)
                path.lineTo(size * 0.84, size * 0.50)
                path.lineTo(size * 0.26, size * 0.85)
                path.closeSubpath()
                painter.drawPath(path)
            elif shape == "stop":
                margin = size * 0.26
                radius = size * 0.13
                painter.drawRoundedRect(
                    QRectF(margin, margin, size - 2 * margin, size - 2 * margin),
                    radius,
                    radius,
                )
            painter.end()
            icon.addPixmap(pixmap)
        return icon

    def _build_actions(self) -> None:
        style = self.style()
        self.act_add_files = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton), "Add Files…", self
        )
        self.act_add_files.setShortcut(QKeySequence.StandardKey.Open)
        self.act_add_files.triggered.connect(self._add_files)

        self.act_add_folder = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon), "Add Folder…", self
        )
        self.act_add_folder.setShortcut(QKeySequence("Ctrl+Shift+O"))
        self.act_add_folder.triggered.connect(self._add_folder)

        self.act_start = QAction(self._toolbar_icon("play"), "Start", self)
        self.act_start.setToolTip("Start processing")
        self.act_start.setShortcut(QKeySequence("Ctrl+R"))
        self.act_start.triggered.connect(lambda: self._start(dry_run=False))

        self.act_stop = QAction(self._toolbar_icon("stop"), "Stop", self)
        self.act_stop.setToolTip("Stop after the current pass")
        self.act_stop.setEnabled(False)
        self.act_stop.triggered.connect(self._stop)

    def _build_toolbar(self) -> None:
        bar = QToolBar("Main")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(bar)
        bar.addAction(self.act_add_files)
        bar.addAction(self.act_add_folder)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)
        bar.addAction(self.act_start)
        bar.addAction(self.act_stop)
        # Merge the toolbar with the title bar (native on macOS).
        self.setUnifiedTitleAndToolBarOnMac(True)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self.act_add_files)
        file_menu.addAction(self.act_add_folder)

        # Native macOS app-menu items (About / Settings… / Quit).
        self.act_settings = QAction("Settings…", self)
        self.act_settings.setMenuRole(QAction.MenuRole.PreferencesRole)
        self.act_settings.setShortcuts(
            [QKeySequence.StandardKey.Preferences, QKeySequence("Ctrl+,")]
        )
        self.act_settings.triggered.connect(lambda: self.nav.setCurrentRow(1))
        self.addAction(self.act_settings)

        self.act_about = QAction("About Velox", self)
        self.act_about.setMenuRole(QAction.MenuRole.AboutRole)
        self.act_about.triggered.connect(self._show_about)
        self.addAction(self.act_about)

        quit_action = QAction("Quit Velox", self)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "Velox",
            f"<b>Velox {__version__}</b><br><br>"
            "Video cleanup and transcoding for Apple Silicon.<br><br>"
            f"ffmpeg: <code>{self.config.general.ffmpeg_path}</code>",
        )

    def _build_sidebar(self) -> QWidget:
        self.nav = QListWidget()
        self.nav.setObjectName("sidebar")
        self.nav.setFixedWidth(SIDEBAR_WIDTH)
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.setSpacing(1)
        self.nav.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        style = self.style()
        entries = [
            (QStyle.StandardPixmap.SP_DirIcon, "Files"),
            (QStyle.StandardPixmap.SP_FileDialogDetailedView, "Settings"),
            (QStyle.StandardPixmap.SP_FileDialogInfoView, "Results"),
        ]
        for pixmap, label in entries:
            item = QListWidgetItem(style.standardIcon(pixmap), label)
            item.setSizeHint(QSize(0, 30))
            self.nav.addItem(item)
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self._on_nav_changed)
        return self.nav

    def _build_content(self) -> QWidget:
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_files_page())
        self.settings = SettingsPanel()
        self.settings.load_requested.connect(self._load_settings)
        self.settings.save_requested.connect(self._save_settings)
        self.stack.addWidget(self.settings)
        self.results = ResultsPanel()
        self.stack.addWidget(self.results)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        return self.stack

    def _build_files_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        self.file_table = QTableWidget(0, 4)
        self.file_table.setHorizontalHeaderLabels(["File", "Progress", "Step", "Result"])
        self.file_table.verticalHeader().setVisible(False)
        self.file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.file_table.setShowGrid(False)
        self.file_table.setAlternatingRowColors(False)
        header = self.file_table.horizontalHeader()
        header.setSectionResizeMode(COL_FILE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_PROGRESS, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_STEP, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_RESULT, QHeaderView.ResizeMode.ResizeToContents)
        self.file_table.setColumnWidth(COL_PROGRESS, 150)
        layout.addWidget(self.file_table, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        for label, slot in (
            ("Add Files…", self._add_files),
            ("Add Folder…", self._add_folder),
            ("Remove", self._remove_selected),
            ("Clear", self._clear_files),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return page

    def _build_status_strip(self) -> QWidget:
        strip = QFrame()
        strip.setObjectName("statusStrip")
        layout = QVBoxLayout(strip)
        layout.setContentsMargins(16, 8, 16, 10)
        layout.setSpacing(5)

        header = QHBoxLayout()
        self.status_label = QLabel("Idle")
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        font = self.status_label.font()
        font.setBold(True)
        self.status_label.setFont(font)
        self.count_label = QLabel("")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.count_label.setObjectName("secondary")
        header.addWidget(self.status_label, 1)
        header.addWidget(self.count_label)
        layout.addLayout(header)

        self.overall_bar = QProgressBar()
        self.overall_bar.setRange(0, 1)
        self.overall_bar.setValue(0)
        self.overall_bar.setTextVisible(False)
        layout.addWidget(self.overall_bar)

        self.details_button = QPushButton("Show log")
        self.details_button.setFlat(True)
        self.details_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.details_button.clicked.connect(self._toggle_details)
        details_row = QHBoxLayout()
        details_row.addWidget(self.details_button)
        details_row.addStretch(1)
        layout.addLayout(details_row)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setFixedHeight(130)
        self.log.setVisible(False)
        layout.addWidget(self.log)
        return strip

    def _on_nav_changed(self, row: int) -> None:
        self.stack.setCurrentIndex(row)

    def _toggle_details(self) -> None:
        self._log_visible = not self._log_visible
        self.log.setVisible(self._log_visible)
        self.details_button.setText("Hide log" if self._log_visible else "Show log")

    # ------------------------------------------------------------ file table
    def _add_paths(self, paths: list[str]) -> None:
        added = 0
        for raw in paths:
            path = str(Path(raw))
            if path in self._rows:
                continue
            self._insert_row(path)
            added += 1
        if added:
            self._log(f"Added {added} file(s).")
            if self.nav.currentRow() != 0:
                self.nav.setCurrentRow(0)

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

    def _insert_row(self, path: str) -> None:
        row = self.file_table.rowCount()
        self.file_table.insertRow(row)

        name = QTableWidgetItem(Path(path).name)
        name.setToolTip(path)
        self.file_table.setItem(row, COL_FILE, name)

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        self.file_table.setCellWidget(row, COL_PROGRESS, bar)

        self.file_table.setItem(row, COL_STEP, QTableWidgetItem("Queued"))
        self.file_table.setItem(row, COL_RESULT, QTableWidgetItem(""))

        self._rows[path] = row
        self._bars[path] = bar
        self._step[path] = ""
        self._pass_started[path] = None

    def _rebuild_rows(self) -> None:
        mapping: dict[str, int] = {}
        for row in range(self.file_table.rowCount()):
            item = self.file_table.item(row, COL_FILE)
            if item is None:
                continue
            path = item.toolTip()
            mapping[path] = row
        self._rows = mapping

    def _remove_selected(self) -> None:
        rows = sorted({index.row() for index in self.file_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.file_table.removeRow(row)
        self._rebuild_rows()

    def _clear_files(self) -> None:
        self.file_table.setRowCount(0)
        self._rows.clear()
        self._bars.clear()
        self._step.clear()
        self._pass_started.clear()

    def selected_files(self) -> list[str]:
        files = []
        for row in range(self.file_table.rowCount()):
            item = self.file_table.item(row, COL_FILE)
            if item is not None:
                files.append(item.toolTip())
        return files

    def _row_of(self, path: str) -> int | None:
        return self._rows.get(path)

    def _set_step(self, path: str, text: str) -> None:
        row = self._row_of(path)
        if row is None:
            return
        item = self.file_table.item(row, COL_STEP)
        if item is not None:
            item.setText(text)

    def _set_result(self, path: str, text: str, color: str | None = None) -> None:
        row = self._row_of(path)
        if row is None:
            return
        item = self.file_table.item(row, COL_RESULT)
        if item is None:
            return
        item.setText(text)
        if color:
            item.setForeground(QColor(color))

    def _bar_of(self, path: str) -> QProgressBar | None:
        return self._bars.get(path)

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

        # reset the per-file rows
        for path in self._rows:
            self._step[path] = ""
            self._pass_started[path] = None
            self._set_step(path, "Queued")
            self._set_result(path, "")
            bar = self._bar_of(path)
            if bar is not None:
                bar.setRange(0, 100)
                bar.setValue(0)

        self.results.clear()
        self.log.clear()
        self._files_total = len(files)
        self._files_done = 0
        self._progress = {path: 0.0 for path in files}
        self._status_text = {}
        self._active = set()
        self._run_started_at = time.monotonic()
        self.overall_bar.setRange(0, 1000)
        self.overall_bar.setValue(0)
        self.status_label.setText("Starting…")
        self.count_label.setText(f"0 of {self._files_total}")
        self._set_running(True)
        self._elapsed_timer.start()
        concurrency = max(1, int(getattr(self.config.general, "concurrency", 1) or 1))
        self._log(
            ("Dry run — " if dry_run else "")
            + f"Starting {len(files)} file(s), {concurrency} at a time…"
        )

        worker = PipelineWorker(self.config, [Path(f) for f in files], dry_run)
        queued = Qt.ConnectionType.QueuedConnection
        worker.run_started.connect(self._on_run_started, queued)
        worker.file_started.connect(self._on_file_started, queued)
        worker.file_step.connect(self._on_file_step, queued)
        worker.file_pass.connect(self._on_file_pass, queued)
        worker.file_progress.connect(self._on_file_progress, queued)
        worker.pass_finished.connect(self._on_pass_finished, queued)
        worker.note.connect(self._log, queued)
        worker.file_finished.connect(self._on_file_finished, queued)
        worker.failed.connect(self._on_failed, queued)
        worker.run_finished.connect(self._on_run_finished, queued)

        self._worker = worker
        worker.start()

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.act_stop.setEnabled(False)
            self._log("Stopping after the current pass…")

    def _release_worker(self) -> None:
        self._worker = None

    def _set_running(self, running: bool) -> None:
        self.act_start.setEnabled(not running)
        self.act_stop.setEnabled(running)

    def _overall_fraction(self) -> float:
        if not self._files_total:
            return 0.0
        if self._progress:
            done = sum(self._progress.values())
        else:
            done = float(self._files_done)
        return max(0.0, min(1.0, done / self._files_total))

    def _set_overall(self) -> None:
        if self.overall_bar.maximum() != 1000:
            self.overall_bar.setRange(0, 1000)
        self.overall_bar.setValue(int(self._overall_fraction() * 1000))

    def _update_elapsed(self) -> None:
        if self._run_started_at is None:
            return
        elapsed = _fmt_duration(time.monotonic() - self._run_started_at)
        pct = int(self._overall_fraction() * 100)
        self.count_label.setText(
            f"{self._files_done} of {self._files_total}  ·  {pct}%  ·  {elapsed}"
        )

    # --------------------------------------------------------------- signals
    def _on_run_started(self, total: int) -> None:
        self.overall_bar.setRange(0, 1000)
        self.overall_bar.setValue(0)
        self._update_elapsed()

    def _on_file_started(self, path: str, summary: str) -> None:
        self._step[path] = "Analysing streams…"
        self._pass_started[path] = None
        self._progress[path] = 0.0
        self._active.add(path)
        self._set_overall()
        self._set_step(path, "Analysing streams…")
        self._status_text[path] = f"{Path(path).name} — analysing…"
        self._update_status_line()
        bar = self._bar_of(path)
        if bar is not None:
            bar.setRange(0, 0)
        self._log(f"— {Path(path).name}: {summary}")

    def _on_file_step(self, path: str, text: str) -> None:
        self._step[path] = text
        self._pass_started[path] = None
        self._set_step(path, text)
        self._status_text[path] = f"{Path(path).name} · {text}"
        self._update_status_line()
        bar = self._bar_of(path)
        if bar is not None:
            bar.setRange(0, 0)

    def _on_file_pass(self, path: str, text: str) -> None:
        self._step[path] = text
        self._pass_started[path] = time.monotonic()
        self._set_step(path, text)
        self._status_text[path] = f"{Path(path).name} · {text}"
        self._update_status_line()
        bar = self._bar_of(path)
        if bar is not None:
            bar.setRange(0, 100)
            bar.setValue(0)
        self._log(f"▶ {Path(path).name}: {text}")

    def _on_file_progress(self, path: str, fraction: float) -> None:
        fraction = max(0.0, min(1.0, fraction))
        self._progress[path] = fraction
        self._set_overall()
        bar = self._bar_of(path)
        if bar is not None:
            if bar.maximum() == 0:
                bar.setRange(0, 100)
            bar.setValue(int(fraction * 100))
        step = self._step.get(path, "")
        started = self._pass_started.get(path)
        if started is not None and fraction > 0.02:
            elapsed = time.monotonic() - started
            remaining = elapsed * (1.0 - fraction) / fraction
            text = f"{step}  ·  {int(fraction * 100)}%  ·  ~{_fmt_duration(remaining)} left"
        else:
            text = f"{step}  ·  {int(fraction * 100)}%"
        self._set_step(path, text)
        self._status_text[path] = f"{Path(path).name} · {text}"
        self._update_status_line()

    def _update_status_line(self) -> None:
        """Show one file's detail, or an aggregate when several run at once."""
        active = list(self._active)
        if len(active) == 1:
            path = active[0]
            self.status_label.setText(
                self._status_text.get(path) or Path(path).name
            )
        elif len(active) > 1:
            self.status_label.setText(f"Processing {len(active)} files…")

    def _on_pass_finished(self, path: str, event: PassEvent) -> None:
        self.results.add_pass(Path(path).name, event)
        self._log(f"   ✓ {Path(path).name}: {event.describe()}")

    def _on_file_finished(self, report: RunReport) -> None:
        path = str(report.source)
        self.results.add_report(report)
        self._files_done += 1
        self._progress[path] = 1.0
        self._active.discard(path)
        self._set_overall()
        self._update_elapsed()

        bar = self._bar_of(path)
        if bar is not None:
            bar.setRange(0, 100)
            bar.setValue(100 if report.ok else 0)

        if report.cancelled:
            self._set_step(path, "Stopped")
            self._set_result(path, "stopped", "#c62828")
        elif report.error:
            self._set_step(path, report.error)
            self._set_result(path, "error", "#c62828")
            self._log(f"✗ {Path(path).name}: {report.error}")
        elif report.skipped:
            self._set_step(path, "Skipped")
            self._set_result(path, "skipped")
            self._log(f"⏭ {Path(path).name}: skipped ({report.reason})")
        elif report.modified:
            self._set_step(path, "Done")
            self._set_result(path, f"−{fmt_bytes(report.saved_bytes)}", "#2e7d32")
            self._log(
                f"✓ {Path(path).name}: {fmt_bytes(report.original_bytes)} → "
                f"{fmt_bytes(report.final_bytes)} (saved {fmt_bytes(report.saved_bytes)}, "
                f"{report.saved_percent:.1f}%)"
            )
        else:
            self._set_step(path, "Nothing to change")
            self._set_result(path, "unchanged")
            self._log(f"= {Path(path).name}: nothing to change")
        self._update_status_line()

    def _on_failed(self, message: str) -> None:
        self._log(f"✗ {message}")

    def _on_run_finished(self, reports) -> None:
        self._set_running(False)
        self._elapsed_timer.stop()
        self._run_started_at = None
        failed = [r for r in reports if not r.ok and not r.cancelled]
        self.status_label.setText(
            "Finished." if not failed else f"Finished with {len(failed)} error(s)."
        )
        self.count_label.setText(f"{self._files_done} of {self._files_total}")
        self.overall_bar.setRange(0, 1000)
        self.overall_bar.setValue(1000)
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

    def _restore_geometry(self) -> None:
        geometry = self._qsettings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        # Redraw the toolbar icons when the system appearance (light/dark) changes.
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "act_start"):
            self.act_start.setIcon(self._toolbar_icon("play"))
            self.act_stop.setIcon(self._toolbar_icon("stop"))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._qsettings.setValue("window/geometry", self.saveGeometry())
        worker = self._worker
        if worker is not None:
            worker.cancel()
            worker.join(10000)
        event.accept()
