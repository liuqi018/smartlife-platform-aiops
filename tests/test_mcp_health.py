import unittest
from unittest.mock import AsyncMock

from app.agent import mcp_client


class MCPHealthTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        mcp_client._mcp_health_checked = False
        mcp_client._mcp_cached_tools = []
        mcp_client._mcp_unavailable_reason = None

    async def test_unavailable_mcp_is_probed_only_once(self):
        client = AsyncMock()
        client.get_tools.side_effect = RuntimeError("502 Bad Gateway")

        first_tools, first_error = await mcp_client.load_mcp_tools_safe(client)
        second_tools, second_error = await mcp_client.load_mcp_tools_safe(client)

        self.assertEqual(first_tools, [])
        self.assertEqual(second_tools, [])
        self.assertIn("502 Bad Gateway", first_error)
        self.assertEqual(first_error, second_error)
        client.get_tools.assert_awaited_once()

    async def test_available_mcp_tools_are_cached(self):
        client = AsyncMock()
        client.get_tools.return_value = ["tool"]

        first_tools, first_error = await mcp_client.load_mcp_tools_safe(client)
        second_tools, second_error = await mcp_client.load_mcp_tools_safe(client)

        self.assertEqual(first_tools, ["tool"])
        self.assertEqual(second_tools, ["tool"])
        self.assertIsNone(first_error)
        self.assertIsNone(second_error)
        client.get_tools.assert_awaited_once()
