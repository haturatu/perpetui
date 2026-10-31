from __future__ import annotations

import logging
import sys


def configure_logging(debug: bool = False) -> logging.Logger:
    logger = logging.getLogger("perpetui")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if debug else logging.WARNING)
    logger.propagate = False
    stdout = logging.StreamHandler(sys.stdout)
    stdout.addFilter(lambda record: record.levelno < logging.WARNING)
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setLevel(logging.WARNING)
    for handler in (stdout, stderr):
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger
