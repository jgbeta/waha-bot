from __future__ import annotations

import logging

from app.config import Settings


def setup_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def format_error(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {str(exc)[:300]}"
