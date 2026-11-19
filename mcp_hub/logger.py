"""McpHub 日志系统。

默认写入 ~/.mcp-hub/logs/mcp-hub-YYYY-MM-DD.log，按天轮转、保留 14 天。
绝对不写 stdout（stdio MCP 协议独占 stdout）；是否回显 stderr 可配置。

配置优先级（高 -> 低）：
  1. 显式调用 configure()（Hub 用 hub.json 的 defaults.logging 调用）
  2. 环境变量 MCPHUB_LOG_DIR / MCPHUB_LOG_LEVEL / MCPHUB_LOG_STDERR
  3. 默认值：~/.mcp-hub/logs、INFO、不回显 stderr
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

LOGGER_NAME = "mcp-hub"
DEFAULT_LOG_DIRNAME = Path(".mcp-hub") / "logs"
DEFAULT_LOG_FILE = "mcp-hub.log"
DEFAULT_LEVEL = "INFO"
DEFAULT_BACKUP_DAYS = 14

_logger: logging.Logger | None = None
_current: dict[str, Any] = {}


def default_log_dir() -> Path:
    env = os.environ.get("MCPHUB_LOG_DIR")
    return Path(env).expanduser() if env else Path.home() / DEFAULT_LOG_DIRNAME


def _resolve_level(value: str | int | None) -> int:
    if value is None:
        value = os.environ.get("MCPHUB_LOG_LEVEL", DEFAULT_LEVEL)
    if isinstance(value, int):
        return value
    return logging.getLevelName(str(value).upper())  # type: ignore[return-value]


def _stderr_enabled() -> bool:
    explicit = _current.get("stderr")
    if explicit is not None:
        return bool(explicit)
    return os.environ.get("MCPHUB_LOG_STDERR", "").strip().lower() in {"1", "true", "yes", "on"}


def configure(
    *,
    log_dir: str | Path | None = None,
    level: str | int | None = None,
    stderr: bool | None = None,
    backup_days: int | None = None,
    force: bool = False,
) -> logging.Logger:
    """配置并返回一个可供全局使用的 logger。重复调用默认幂等。"""
    global _logger, _current

    target_dir = Path(log_dir).expanduser() if log_dir else default_log_dir()
    resolved_level = _resolve_level(level)
    resolved_backup = int(backup_days or DEFAULT_BACKUP_DAYS)
    stderr_flag = _stderr_enabled() if stderr is None else bool(stderr)

    signature = {
        "dir": str(target_dir),
        "level": resolved_level,
        "backup": resolved_backup,
        "stderr": stderr_flag,
    }
    if _logger is not None and not force and signature == _current.get("signature"):
        return _logger

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(resolved_level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    target_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d %(levelname)-7s [%(process)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        file_handler: logging.Handler = TimedRotatingFileHandler(
            target_dir / DEFAULT_LOG_FILE,
            when="midnight",
            interval=1,
            backupCount=resolved_backup,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(resolved_level)
        logger.addHandler(file_handler)
    except Exception:
        file_handler = None  # type: ignore[assignment]

    if stderr_flag:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        stream_handler.setLevel(resolved_level)
        logger.addHandler(stream_handler)

    if not logger.handlers:  # 兜底：文件创建失败且无 stderr，避免日志丢失
        fallback = logging.NullHandler()
        logger.addHandler(fallback)

    _current = {"signature": signature, "dir": str(target_dir), "stderr": stderr_flag}
    _logger = logger
    return logger


def get_logger() -> logging.Logger:
    global _logger
    if _logger is None:
        return configure()
    return _logger


def log_dir() -> Path:
    return Path(_current.get("dir", str(default_log_dir())))


def debug(msg: str, *args: Any, **kwargs: Any) -> None:
    get_logger().debug(msg, *args, **kwargs)


def info(msg: str, *args: Any, **kwargs: Any) -> None:
    get_logger().info(msg, *args, **kwargs)


def warning(msg: str, *args: Any, **kwargs: Any) -> None:
    get_logger().warning(msg, *args, **kwargs)


def error(msg: str, *args: Any, **kwargs: Any) -> None:
    get_logger().error(msg, *args, **kwargs)


def exception(msg: str, *args: Any, **kwargs: Any) -> None:
    get_logger().exception(msg, *args, **kwargs)
