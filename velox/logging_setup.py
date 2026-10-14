"""Small logging helper so the CLI and the future GUI share one style."""

from __future__ import annotations

import logging
import sys

LOGGER_NAME = "velox"

_LEVEL_STYLES = {
    logging.DEBUG: "\033[38;5;244m",  # grey
    logging.INFO: "\033[0m",  # default
    logging.WARNING: "\033[33m",  # yellow
    logging.ERROR: "\033[31m",  # red
    logging.CRITICAL: "\033[1;31m",  # bold red
}
_RESET = "\033[0m"


class _ColorFormatter(logging.Formatter):
    def __init__(self, use_color: bool) -> None:
        super().__init__("%(message)s")
        self.use_color = use_color and sys.stderr.isatty()

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if not self.use_color:
            return message
        color = _LEVEL_STYLES.get(record.levelno, "")
        return f"{color}{message}{_RESET}"


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def setup_logging(verbosity: int = 0, use_color: bool = True) -> logging.Logger:
    """Configure and return the Velox logger.

    verbosity: 0 -> INFO, 1 -> DEBUG, negative -> quieter (WARNING).
    """
    if verbosity >= 1:
        level = logging.DEBUG
    elif verbosity < 0:
        level = logging.WARNING
    else:
        level = logging.INFO

    logger = get_logger()
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_ColorFormatter(use_color))
    logger.addHandler(handler)
    return logger
