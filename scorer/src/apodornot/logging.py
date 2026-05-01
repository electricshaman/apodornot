"""Lightweight logging setup. Call configure_logging() once from CLI / entrypoints."""

from __future__ import annotations

import logging
import os


def configure_logging(level: int | str | None = None) -> None:
    """Configure root logging. Honors APODORNOT_LOG_LEVEL if level is None."""
    if level is None:
        level = os.environ.get("APODORNOT_LOG_LEVEL", "INFO")
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
        datefmt="%H:%M:%S",
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
