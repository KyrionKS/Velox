"""Velox desktop app (PySide6)."""

from __future__ import annotations

import faulthandler
import sys
from pathlib import Path

from .. import __version__

__all__ = ["main"]

# Deliberately minimal: the native macOS style draws the controls (progress
# bars, sidebar selection, buttons) with the system appearance, accent colour
# and Liquid Glass. We only add a hairline divider and a muted secondary label.
_STYLE = """
#divider {
    background: palette(mid);
}
#statusStrip {
    border-top: 1px solid palette(mid);
}
#secondary {
    color: gray;
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

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Velox")
    app.setApplicationDisplayName("Velox")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Velox")
    app.setStyleSheet(_STYLE)

    icon_path = Path(__file__).resolve().parents[2] / "assets" / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    window.show()
    return app.exec()
