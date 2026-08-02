import unittest

from app.services.alert_state_manager import (
    InMemoryAlertStateManager,
    ProtectedAIOpsRedis,
    RedisAlertStateManager,
)


class AlertStateKeyTest(unittest.TestCase):
    def test_current_key_namespace(self):
        manager = object.__new__(RedisAlertStateManager)
        manager.prefix = "aiops:alert:current"
        self.assertEqual(
            manager._key("abc123"),
            "aiops:alert:current:abc123",
        )

    def test_manager_rejects_database_zero(self):
        with self.assertRaisesRegex(ValueError, "database 1"):
            RedisAlertStateManager(
                "redis://:1234@127.0.0.1:6379/0",
                "aiops:alert:current",
                "aiops",
            )

    def test_in_memory_active_count_tracks_current_states(self):
        manager = InMemoryAlertStateManager()
        self.assertEqual(manager.count_active_alerts(), 0)
        manager.save_alert_state("one", {"status": "firing", "startsAt": "start"})
        self.assertEqual(manager.count_active_alerts(), 1)
        manager.resolve_current("one", "start")
        self.assertEqual(manager.count_active_alerts(), 0)

    def test_lifecycle_claim_is_atomic_for_fingerprint_and_start_time(self):
        manager = InMemoryAlertStateManager()

        first, first_state = manager.claim_lifecycle("fp", "start-1", "now-1")
        second, second_state = manager.claim_lifecycle("fp", "start-1", "now-2")
        next_cycle, _ = manager.claim_lifecycle("fp", "start-2", "now-3")

        self.assertTrue(first)
        self.assertEqual(first_state["diagnosis_status"], "claimed")
        self.assertFalse(second)
        self.assertEqual(second_state["startsAt"], "start-1")
        self.assertTrue(next_cycle)

    def test_existing_current_lifecycle_is_already_claimed(self):
        manager = InMemoryAlertStateManager()
        manager.save_alert_state("fp", {
            "fingerprint": "fp",
            "startsAt": "start-1",
            "status": "firing",
            "diagnosis_status": "completed",
        })

        claimed, state = manager.claim_lifecycle("fp", "start-1", "now")

        self.assertFalse(claimed)
        self.assertEqual(state["diagnosis_status"], "completed")
        self.assertEqual(state["repeat_count"], 1)

    def test_redis_claim_uses_set_nx_and_lifecycle_key(self):
        class FakeRedis:
            def __init__(self):
                self.values = {}

            def set(self, key, value, nx=False, ex=None):
                if nx and key in self.values:
                    return None
                self.values[key] = value
                self.ttl = ex
                return True

            def get(self, key):
                return self.values.get(key)

        manager = object.__new__(RedisAlertStateManager)
        manager.namespace = "aiops"
        manager.prefix = "aiops:alert:current"
        manager.client = FakeRedis()

        first, _ = manager.claim_lifecycle("fp", "2026-07-27T10:00:00+08:00", "now")
        second, state = manager.claim_lifecycle("fp", "2026-07-27T10:00:00+08:00", "later")

        expected_key = "aiops:claim:alert:fp:2026-07-27T10:00:00"
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertIn(expected_key, manager.client.values)
        self.assertEqual(manager.client.ttl, 1800)
        self.assertEqual(state["diagnosis_status"], "claimed")

    def test_resolved_offset_time_removes_utc_firing_claim(self):
        class FakePipeline:
            def __init__(self, client):
                self.client = client
                self.commands = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def watch(self, *_keys):
                return None

            def unwatch(self):
                return None

            def get(self, key):
                return self.client.get(key)

            def multi(self):
                return self

            def delete(self, *keys):
                self.commands.append(("delete", keys))
                return self

            def execute(self):
                for command, keys in self.commands:
                    if command == "delete":
                        self.client.delete(*keys)
                return []

        class FakeRedis:
            def __init__(self):
                self.values = {}

            def set(self, key, value, nx=False, ex=None):
                if nx and key in self.values:
                    return None
                self.values[key] = value
                return True

            def get(self, key):
                return self.values.get(key)

            def delete(self, *keys):
                deleted = 0
                for key in keys:
                    deleted += int(self.values.pop(key, None) is not None)
                return deleted

            def pipeline(self):
                return FakePipeline(self)

        manager = object.__new__(RedisAlertStateManager)
        manager.namespace = "aiops"
        manager.prefix = "aiops:alert:current"
        manager.client = FakeRedis()

        claimed, claim = manager.claim_lifecycle(
            "fp", "2026-07-27T08:00:00Z", "now"
        )
        claim_key = "aiops:claim:alert:fp:2026-07-27T16:00:00"
        self.assertTrue(claimed)
        self.assertEqual(claim["startsAt"], "2026-07-27T16:00:00")
        self.assertIn(claim_key, manager.client.values)

        manager.save_alert_state("fp", {
            "fingerprint": "fp",
            "startsAt": "2026-07-27T08:00:00Z",
            "status": "firing",
        })
        resolved, _ = manager.resolve_current(
            "fp", "2026-07-27T16:00:00+08:00"
        )

        self.assertTrue(resolved)
        self.assertNotIn(claim_key, manager.client.values)

    def test_resolved_removes_current_canonical_claim_without_state(self):
        class FakeRedis:
            def __init__(self):
                self.values = {
                    "aiops:claim:alert:fp:2026-07-27T16:00:00": "current"
                }

            def get(self, key):
                return self.values.get(key)

            def delete(self, *keys):
                for key in keys:
                    self.values.pop(key, None)

            class Pipeline:
                def __init__(self, client):
                    self.client = client

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def watch(self, *_keys):
                    return None

                def get(self, key):
                    return self.client.get(key)

                def unwatch(self):
                    return None

            def pipeline(self):
                return self.Pipeline(self)

        manager = object.__new__(RedisAlertStateManager)
        manager.namespace = "aiops"
        manager.prefix = "aiops:alert:current"
        manager.client = FakeRedis()

        resolved, state = manager.resolve_current(
            "fp", "2026-07-27T16:00:00+08:00"
        )

        self.assertFalse(resolved)
        self.assertIsNone(state)
        self.assertFalse(manager.client.values)

    def test_update_current_status_does_not_create_missing_state(self):
        manager = InMemoryAlertStateManager()

        updated, state = manager.update_current_status(
            "one", "start", "firing", diagnosis_status="completed"
        )

        self.assertFalse(updated)
        self.assertIsNone(state)
        self.assertIsNone(manager.get_alert_state("one"))

    def test_update_current_status_requires_matching_start_time(self):
        manager = InMemoryAlertStateManager()
        manager.save_alert_state("one", {"status": "firing", "startsAt": "new-start"})

        updated, state = manager.update_current_status(
            "one", "old-start", "firing", diagnosis_status="completed"
        )

        self.assertFalse(updated)
        self.assertEqual(state["startsAt"], "new-start")
        self.assertNotIn("diagnosis_status", manager.get_alert_state("one"))

    def test_protected_client_rejects_flush_and_non_aiops_delete(self):
        class FakeRedis:
            def delete(self, *keys):
                return len(keys)

            def unlink(self, *keys):
                return len(keys)

        client = ProtectedAIOpsRedis(FakeRedis())
        self.assertEqual(client.delete("aiops:state:test"), 1)
        self.assertEqual(client.unlink("aiops:session:test"), 1)
        with self.assertRaises(PermissionError):
            client.delete("smartlife:shop:1")
        with self.assertRaises(PermissionError):
            client.flushdb()
        with self.assertRaises(PermissionError):
            client.flushall()


if __name__ == "__main__":
    unittest.main()
