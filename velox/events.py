"""Lightweight progress/event layer shared by the CLI and the GUI.

The pipeline emits these events instead of the GUI having to parse log lines.
``ProgressObserver`` is a no-op base class; subclass it and override what you
need.  All callbacks run in the pipeline's thread (the GUI worker), so GUI
implementations must hand off to their UI thread via signals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PassEvent:
    """One ffmpeg run (either a merged remux or a transcode)."""

    steps: list[str]
    kind: str  # "remux" | "transcode"
    input_bytes: int
    output_bytes: int
    command: list[str] = field(default_factory=list)

    @property
    def saved_bytes(self) -> int:
        return self.input_bytes - self.output_bytes

    @property
    def label(self) -> str:
        return " + ".join(self.steps)

    def describe(self) -> str:
        saved = self.saved_bytes
        sign = "−" if saved >= 0 else "+"
        return f"{self.label}: {fmt_bytes(self.input_bytes)} → {fmt_bytes(self.output_bytes)} ({sign}{fmt_bytes(abs(saved))})"


def fmt_bytes(num: int) -> str:
    """Human readable size (decimal units, like Finder)."""
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1000 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1000.0
    return f"{value:.2f} TB"


class ProgressObserver:
    """No-op observer base class."""

    def on_run_started(self, files: list[Path]) -> None: ...

    def on_file_started(self, path: Path, summary: str) -> None: ...

    def on_step_started(self, key: str, name: str) -> None: ...

    def on_step_note(self, message: str) -> None: ...

    def on_pass_started(self, description: str) -> None: ...

    def on_progress(self, fraction: float) -> None: ...

    def on_pass_finished(self, event: PassEvent) -> None: ...

    def on_file_finished(self, report: Any) -> None: ...

    def on_run_finished(self, results: list[Any]) -> None: ...

    def on_error(self, message: str) -> None: ...
