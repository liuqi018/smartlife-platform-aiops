"""Helpers that keep finite background workflows alive after SSE disconnects."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger


_BACKGROUND_DIAGNOSIS_TASKS: set[asyncio.Task[None]] = set()
_STREAM_END = object()


async def detached_sse_stream(source: AsyncIterator[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    """Forward source events without coupling source cancellation to the SSE client."""
    queue: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue()

    async def produce() -> None:
        try:
            async for event in source:
                await queue.put(event)
        except Exception as exc:
            logger.error("Background AIOps diagnosis failed after SSE start: {}", exc, exc_info=True)
        finally:
            await queue.put(_STREAM_END)

    producer = asyncio.create_task(produce(), name="aiops-background-diagnosis")
    _BACKGROUND_DIAGNOSIS_TASKS.add(producer)
    producer.add_done_callback(_BACKGROUND_DIAGNOSIS_TASKS.discard)

    try:
        while True:
            item = await queue.get()
            if item is _STREAM_END:
                break
            yield item  # type: ignore[misc]
    except asyncio.CancelledError:
        logger.warning("[Report] client disconnected; background diagnosis continues")
        return
