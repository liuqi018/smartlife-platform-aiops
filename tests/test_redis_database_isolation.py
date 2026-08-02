import unittest
from uuid import uuid4

from app.config import config


class RedisDatabaseIsolationTest(unittest.TestCase):
    def test_aiops_configuration_uses_database_one_and_namespaces(self):
        self.assertIn("127.0.0.1:6380", config.aiops_redis_url)
        self.assertTrue(config.aiops_redis_url.endswith("/1"))
        self.assertEqual(config.aiops_redis_namespace, "aiops")
        for prefix in (
            config.aiops_redis_prefix,
            config.aiops_redis_diagnosis_prefix,
            config.aiops_redis_session_prefix,
            config.aiops_redis_state_prefix,
        ):
            self.assertTrue(prefix.startswith("aiops:"))

    def test_smartlife_6379_and_aiops_6380_are_isolated(self):
        try:
            import redis
        except ImportError:
            self.skipTest("redis package unavailable")

        marker = uuid4().hex
        db0 = redis.Redis.from_url("redis://:1234@127.0.0.1:6379/0", decode_responses=True)
        db1 = redis.Redis.from_url(config.aiops_redis_url, decode_responses=True)
        business_key = f"smartlife:test:isolation:{marker}"
        aiops_key = f"aiops:state:test:isolation:{marker}"
        try:
            db0.set(business_key, "business")
            db1.set(aiops_key, "aiops")
            self.assertEqual(db0.get(business_key), "business")
            self.assertIsNone(db1.get(business_key))
            self.assertEqual(db1.get(aiops_key), "aiops")
            self.assertIsNone(db0.get(aiops_key))
        finally:
            db0.delete(business_key)
            db1.delete(aiops_key)
