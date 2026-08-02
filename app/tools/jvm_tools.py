"""JVM diagnostic tools backed by the Spring Boot Actuator API."""

from __future__ import annotations

from typing import Any

import httpx
from langchain_core.tools import tool
from loguru import logger


THREAD_DUMP_URL = "http://localhost:8081/actuator/threaddump"
INTERESTING_THREAD_STATES = {"RUNNABLE", "BLOCKED", "WAITING"}
REQUEST_TIMEOUT_SECONDS = 10.0


def _format_stack_frame(frame: Any) -> str:
    """Convert an Actuator stack-frame object into a compact Java-style line."""
    if not isinstance(frame, dict):
        return str(frame)

    class_name = str(frame.get("className") or "<unknown-class>")
    method_name = str(frame.get("methodName") or "<unknown-method>")
    file_name = frame.get("fileName")
    line_number = frame.get("lineNumber")

    if frame.get("nativeMethod"):
        location = "Native Method"
    elif file_name and line_number is not None:
        location = f"{file_name}:{line_number}"
    elif file_name:
        location = str(file_name)
    else:
        location = "Unknown Source"

    return f"at {class_name}.{method_name}({location})"


@tool
def collect_jvm_thread_dump() -> dict[str, Any]:
    """Collect a JVM thread dump from the smartlife Actuator endpoint.

    Returns only RUNNABLE, BLOCKED, and WAITING threads. Each returned thread
    contains its name, state, and at most the first ten stack-trace frames.
    Use this tool to diagnose high CPU, lock contention, deadlocks, or threads
    waiting for resources in the smartlife Java process.
    """
    try:
        response = httpx.get(THREAD_DUMP_URL, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        error = f"JVM thread dump request failed with HTTP status {status_code}"
        logger.warning("{}: {}", error, THREAD_DUMP_URL)
        return {"success": False, "error": error, "threads": []}
    except httpx.RequestError as exc:
        error = f"JVM thread dump request failed: {type(exc).__name__}: {exc}"
        logger.warning("{}", error)
        return {"success": False, "error": error, "threads": []}
    except Exception as exc:
        error = f"Unexpected JVM thread dump HTTP error: {type(exc).__name__}: {exc}"
        logger.exception("{}", error)
        return {"success": False, "error": error, "threads": []}

    try:
        payload = response.json()
        raw_threads = payload.get("threads") if isinstance(payload, dict) else None
        if not isinstance(raw_threads, list):
            return {
                "success": False,
                "error": "Invalid JVM thread dump response: 'threads' must be a list",
                "threads": [],
            }

        threads: list[dict[str, Any]] = []
        for thread in raw_threads:
            if not isinstance(thread, dict):
                continue

            state = str(thread.get("threadState") or "").upper()
            if state not in INTERESTING_THREAD_STATES:
                continue

            stack_trace = thread.get("stackTrace")
            frames = stack_trace if isinstance(stack_trace, list) else []
            threads.append(
                {
                    "name": str(thread.get("threadName") or ""),
                    "state": state,
                    "stacktrace": [_format_stack_frame(frame) for frame in frames[:10]],
                }
            )

        return {"success": True, "threads": threads}
    except Exception as exc:
        error = f"Failed to parse JVM thread dump response: {type(exc).__name__}: {exc}"
        logger.exception("{}", error)
        return {"success": False, "error": error, "threads": []}
