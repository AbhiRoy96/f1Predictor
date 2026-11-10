"""Logging utilities for the F1 Race Intelligence pipeline."""

import logging
import sys


def setup_logger(name: str = "f1_intelligence", level: int = logging.INFO) -> logging.Logger:
    """Set up and return a configured console logger."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(level)
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger
