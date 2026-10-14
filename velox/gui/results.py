"""Results table: one row per ffmpeg pass with the space it saved."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..events import PassEvent, fmt_bytes


class ResultsPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["File", "Step", "In", "Out", "Saved"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (2, 3, 4):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        self.summary = QLabel("No results yet.")
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.summary)

        self._original = 0
        self._final = 0

    def clear(self) -> None:
        self.table.setRowCount(0)
        self._original = 0
        self._final = 0
        self.summary.setText("No results yet.")

    def add_pass(self, filename: str, event: PassEvent) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        saved = event.saved_bytes
        sign = "−" if saved >= 0 else "+"
        cells = [
            filename,
            event.label,
            fmt_bytes(event.input_bytes),
            fmt_bytes(event.output_bytes),
            f"{sign}{fmt_bytes(abs(saved))}",
        ]
        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            if col == 4:
                item.setForeground(QColor("#2e7d32" if saved >= 0 else "#c62828"))
            self.table.setItem(row, col, item)
        self.table.scrollToBottom()

    def add_report(self, report) -> None:  # report: pipeline.RunReport
        if report.modified and report.original_bytes:
            self._original += report.original_bytes
            self._final += report.final_bytes
        self._update_summary()

    def _update_summary(self) -> None:
        if not self._original:
            self.summary.setText("No results yet.")
            return
        saved = self._original - self._final
        pct = saved / self._original * 100 if self._original else 0.0
        self.summary.setText(
            f"Total: {fmt_bytes(self._original)} → {fmt_bytes(self._final)} "
            f"(saved {fmt_bytes(saved)}, {pct:.1f}%)"
        )
