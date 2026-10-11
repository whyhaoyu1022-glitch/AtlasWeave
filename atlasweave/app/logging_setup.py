"""Local rotating logs; never upload user content."""

import logging
import platform
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR

from atlasweave import __version__


def configure_logging(directory: Path) -> logging.Logger:
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("atlasweave")
    # Reinitialization must not duplicate output or leak open file handles.
    for old in list(logger.handlers):
        old.close()
        logger.removeHandler(old)
    handler = RotatingFileHandler(
        directory / "atlasweave.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=4,  # One current file plus four archives: five files total.
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info(
        "Application starting | AtlasWeave=%s Python=%s PyQt=%s Qt=%s OS=%s",
        __version__,
        platform.python_version(),
        PYQT_VERSION_STR,
        QT_VERSION_STR,
        platform.platform(),
    )
    return logger
