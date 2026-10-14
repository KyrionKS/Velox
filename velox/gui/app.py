"""Velox desktop app (PySide6)."""

from __future__ import annotations

import faulthandler
import sys

__all__ = ["main"]


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

    window = MainWindow()
    window.show()
    return app.exec()
