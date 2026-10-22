"""Loguru setup + routing of stdlib logging (uvicorn) into loguru."""

from __future__ import annotations

import logging
import sys

from loguru import logger


class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        level: str | int
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(level: str) -> None:
    logger.remove()
    logger.add(sys.stderr, level=level.upper(), backtrace=False, diagnose=False)
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
