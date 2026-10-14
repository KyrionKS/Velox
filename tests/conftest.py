"""Shared pytest fixtures."""

from __future__ import annotations

import os

import pytest

# Qt must run headless in CI / this environment. Set before Qt is imported.
# "minimal" is used instead of "offscreen": both work, but offscreen
# occasionally reports a glibc heap message during Qt teardown.
os.environ.setdefault("QT_QPA_PLATFORM", "minimal")


@pytest.fixture(scope="session")
def qapp():
    """A single QApplication for the whole test session (skip if PySide6 absent)."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
