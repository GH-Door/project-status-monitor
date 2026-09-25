"""로그 설정 — 원인 추적·병목 분석용. 프로젝트 루트 logs/app.log에 회전 저장한다."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TypeVar

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5

_configured = False


def _configure_once() -> None:
    global _configured
    if _configured:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_DIR / "app.log", maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root = logging.getLogger("psm")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """모듈별 로거. "psm.xxx"로 통일해 logs/app.log에서 모듈 단위로 grep하기 쉽게 한다."""
    _configure_once()
    return logging.getLogger(f"psm.{name}")


T = TypeVar("T")


def log_timing(logger: logging.Logger, operation: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """실행 시간을 남겨 병목을 추적한다. 실패해도 걸린 시간과 스택트레이스를 남기고 다시 던진다."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.monotonic()
            try:
                result = func(*args, **kwargs)
            except Exception:
                logger.exception("%s 실패 (%.0fms)", operation, (time.monotonic() - start) * 1000)
                raise
            logger.info("%s 완료 (%.0fms)", operation, (time.monotonic() - start) * 1000)
            return result

        return wrapper

    return decorator
