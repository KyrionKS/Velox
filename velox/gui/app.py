"""Velox desktop app (PySide6)."""

from __future__ import annotations

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    import sys

    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Velox")
    app.setApplicationDisplayName("Velox")
    app.setOrganizationName("Velox")

    window = MainWindow()
    window.show()
    return app.exec()
