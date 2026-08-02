import asyncio
import unittest

from app.api.sse_utils import _BACKGROUND_DIAGNOSIS_TASKS, detached_sse_stream


class DetachedSSETest(unittest.IsolatedAsyncioTestCase):
    async def test_client_close_does_not_cancel_background_source(self):
        completed = asyncio.Event()

        async def source():
            yield {"type": "started"}
            await asyncio.sleep(0)
            completed.set()
            yield {"type": "complete"}

        stream = detached_sse_stream(source())
        self.assertEqual((await anext(stream))["type"], "started")
        await stream.aclose()
        await asyncio.wait_for(completed.wait(), timeout=1)
        await asyncio.gather(*list(_BACKGROUND_DIAGNOSIS_TASKS), return_exceptions=True)
