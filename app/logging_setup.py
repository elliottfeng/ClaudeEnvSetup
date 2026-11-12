"""日志配置：轮转文件 + 可选 UI 队列。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from queue import Queue

from . import constants
from .models import UiEvent

LOGGER_NAME = "claude_env"


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def setup_logging(debug: bool = False, log_path: str | Path | None = None) -> logging.Logger:
    logger = get_logger()
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False
    if getattr(logger, "_claude_env_configured", False):
        return logger

    target = Path(log_path) if log_path else constants.log_dir() / "app.log"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            target, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(file_handler)
    except OSError:
        pass

    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(console)

    logger._claude_env_configured = True  # type: ignore[attr-defined]
    return logger


class QueueLogHandler(logging.Handler):
    """把日志推送到 UI 事件队列。"""

    def __init__(self, queue: Queue[UiEvent]):
        super().__init__()
        self.queue = queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put(
                UiEvent(
                    "log",
                    {
                        "level": record.levelname,
                        "message": self.format(record),
                    },
                )
            )
        except Exception:  # pragma: no cover - 日志处理器不能抛异常
            pass


def attach_queue_handler(queue: Queue[UiEvent]) -> QueueLogHandler:
    handler = QueueLogHandler(queue)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    get_logger().addHandler(handler)
    return handler
