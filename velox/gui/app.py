"""Velox desktop app (PySide6)."""

from __future__ import annotations

import faulthandler
import sys

__all__ = ["main"]

# Keep the native look, just add a bit of spacing and use the system accent
# colour for progress. palette(...) keeps it correct in light and dark mode.
_STYLE = """
QGroupBox {
    font-weight: 600;
    border: 1px solid palette(mid);
    border-radius: 8px;
    margin-top: 10px;
    padding: 10px 8px 8px 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
}
QPushButton {
    padding: 5px 12px;
    border-radius: 6px;
}
QPushButton:default {
    font-weight: 600;
}
QProgressBar {
    border: 1px solid palette(mid);
    border-radius: 7px;
    text-align: center;
    min-height: 16px;
}
QProgressBar::chunk {
    background-color: palette(highlight);
    border-radius: 6px;
}
QPlainTextEdit, QListWidget, QTableWidget {
    border: 1px solid palette(mid);
    border-radius: 6px;
}
QTabWidget::pane {
    border: 1px solid palette(mid);
    border-radius: 8px;
}
"""


def main(argv: list[str] | None = None) -> int:
    # Print a Python traceback if the process ever crashes (SIGSEGV), so a
    # native crash can be diagnosed instead of just "segmentation fault".
    faulthandler.enable()

    def _excepthook(exc_type, exc_value, exc_tb):
        import traceback

        traceback.print_exception(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Velox")
    app.setApplicationDisplayName("Velox")
    app.setOrganizationName("Velox")
    app.setStyleSheet(_STYLE)

    window = MainWindow()
    window.show()
    return app.exec()
