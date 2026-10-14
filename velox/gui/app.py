"""Velox desktop app (PySide6)."""

from __future__ import annotations

import faulthandler
import sys

__all__ = ["main"]

# Rely on the native macOS style for the controls themselves (it already
# follows the current system appearance and Liquid Glass). We only add the few
# things the native style does not: a source-list sidebar, a slim status strip
# and an accent-coloured progress bar.
_STYLE = """
#sidebar {
    outline: 0;
    padding: 6px 8px;
}
#sidebar::item {
    border-radius: 6px;
    padding: 5px 8px;
}
#sidebar::item:selected {
    background: palette(highlight);
    color: palette(highlighted-text);
}
#statusStrip {
    border-top: 1px solid palette(mid);
}
#secondary {
    color: gray;
}
QProgressBar {
    border: 1px solid palette(mid);
    border-radius: 6px;
    text-align: center;
    min-height: 14px;
}
QProgressBar::chunk {
    background-color: palette(highlight);
    border-radius: 5px;
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
