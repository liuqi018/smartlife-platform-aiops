"""Central logging with application, lifecycle, diagnosis, and debug scopes.

Files:
- logs/app.log: complete application runtime and infrastructure messages.
- logs/aiops_YYYY-MM-DD.log: compact global AIOps lifecycle and warnings.
- logs/diagnosis/{session_id}.log: readable end-to-end diagnosis execution.
- logs/debug/{session_id}.log: prompts, full tool payloads, RAG, and debug detail.
"""

import logging
import sys
import threading
from pathlib import Path

from loguru import logger

from app.config import config
from app.utils.timezone import SHANGHAI_TZ


LOG_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "session={extra[session_id]} alert={extra[alert_name]} service={extra[service]} "
    "stage={extra[stage]} | {module}.{function}:{line} | {message}"
)

CONSOLE_KEYWORDS = (
    "workflow started",
    "workflow completed",
    "[Report] primary model",
    "[Report] secondary model",
    "[Report] switching to",
    "[Report] llm timeout",
    "[Report] client disconnected",
    "[Report] fallback activated",
    "[Report] fallback report generated",
)

LIFECYCLE_KEYWORDS = (
    "alert_received",
    "alert_created",
    "alert_repeated",
    "alert_resolved",
    "diagnosis_started",
    "diagnosis_completed",
    "Redis database=",
    "Received AlertManager webhook",
    "Alert resolved:",
    "workflow started",
    "workflow completed",
    "plan generated successfully",
    "Executor batch finished",
    "Report node started",
    "[Report] primary model",
    "[Report] secondary model",
    "[Report] switching to",
    "[Report] llm timeout",
    "[Report] fallback",
    "Diagnosis history saved",
    "Alert state storage connected",
    "AIOps history storage connected",
)

DETAIL_MARKERS = (
    "prompt", "messages=", "content=", "result_preview=", "returned result:",
    "collected query_", "metric evidence:", "trend analysis:", "RAG evidence:",
    "structured evidence chain:", "用户输入:",
)


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


def _console_filter(record: dict) -> bool:
    if record["level"].no >= logging.ERROR:
        return True
    message = record["message"]
    if record.get("module") == "main" and any(
        marker in message for marker in ("启动中", "正在关闭", "关闭")
    ):
        return True
    if message.startswith("[Report] primary model ") and " success;" in message:
        return True
    if message.startswith("[Report] secondary model ") and " success;" in message:
        return True
    return any(keyword in message for keyword in CONSOLE_KEYWORDS)


def _aiops_filter(record: dict) -> bool:
    return bool(record["extra"].get("aiops"))


def _aiops_lifecycle_filter(record: dict) -> bool:
    if not _aiops_filter(record):
        return False
    if record["level"].no >= logging.WARNING:
        return True
    return any(marker in record["message"] for marker in LIFECYCLE_KEYWORDS)


def _diagnosis_filter(record: dict) -> bool:
    if not _aiops_filter(record) or record["extra"].get("session_id") in (None, "-"):
        return False
    message = record["message"].lower()
    return record["level"].no >= logging.WARNING or not any(marker.lower() in message for marker in DETAIL_MARKERS)


_diagnosis_write_lock = threading.Lock()


def _diagnosis_file_sink(message) -> None:
    session_id = str(message.record["extra"].get("session_id") or "unknown")
    safe_session_id = "".join(char if char.isalnum() or char in "-_." else "_" for char in session_id)
    path = Path("logs/diagnosis") / f"{safe_session_id}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _diagnosis_write_lock, path.open("a", encoding="utf-8") as stream:
        stream.write(str(message))


def _debug_file_sink(message) -> None:
    session_id = str(message.record["extra"].get("session_id") or "unknown")
    safe_session_id = "".join(char if char.isalnum() or char in "-_." else "_" for char in session_id)
    path = Path("logs/debug") / f"{safe_session_id}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _diagnosis_write_lock, path.open("a", encoding="utf-8") as stream:
        stream.write(str(message))


def setup_logger() -> None:
    _configure_utf8_stdio()
    logger.remove()
    logger.configure(
        extra={
            "aiops": False,
            "session_id": "-",
            "alert_name": "-",
            "service": "-",
            "stage": "app",
        },
        patcher=lambda record: record.update(time=record["time"].astimezone(SHANGHAI_TZ)),
    )

    # Console: lifecycle, key AIOps milestones, and errors only.
    logger.add(
        sys.stdout,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
            "session={extra[session_id]} stage={extra[stage]} | <level>{message}</level>"
        ),
        level="INFO",
        filter=_console_filter,
        colorize=True,
        backtrace=True,
        diagnose=config.debug,
    )

    # Application runtime: all application and infrastructure INFO+ records.
    logger.add(
        "logs/app.log",
        rotation="00:00",
        retention="14 days",
        compression="zip",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=config.debug,
        level="INFO",
        format=LOG_FORMAT,
    )

    # Global AIOps lifecycle: compact milestones only, one dated file per day.
    logger.add(
        "logs/aiops_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="30 days",
        compression="zip",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=config.debug,
        level="INFO",
        filter=_aiops_lifecycle_filter,
        format=LOG_FORMAT,
    )

    # Session diagnosis: readable execution without full prompts/tool payloads.
    logger.add(
        _diagnosis_file_sink,
        enqueue=True,
        backtrace=True,
        diagnose=config.debug,
        level="INFO",
        filter=_diagnosis_filter,
        format=LOG_FORMAT,
    )

    # Session debug: complete detail for troubleshooting and evidence inspection.
    logger.add(
        _debug_file_sink,
        enqueue=True,
        backtrace=True,
        diagnose=config.debug,
        level="DEBUG",
        filter=lambda record: _aiops_filter(record) and record["extra"].get("session_id") not in (None, "-"),
        format=LOG_FORMAT,
    )


setup_logger()
