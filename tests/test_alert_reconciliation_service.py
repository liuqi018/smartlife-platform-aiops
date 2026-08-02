import unittest
import asyncio
from unittest.mock import AsyncMock, Mock, patch

from app.models.alert import parse_alertmanager_payload
from app.services.alert_reconciliation_service import AlertReconciliationService
from app.services.alert_state_manager import InMemoryAlertStateManager


def alert_context(fingerprint: str = "fp", starts_at: str = "2026-07-25T10:00:00Z"):
    return parse_alertmanager_payload({
        "status": "firing",
        "alerts": [{
            "status": "firing",
            "fingerprint": fingerprint,
            "labels": {"alertname": "DiskFull", "job": "smartlife"},
            "startsAt": starts_at,
            "endsAt": "0001-01-01T00:00:00Z",
        }],
    })[0]


class FakeAlertManager:
    def __init__(self, active=None, error=None):
        self.active = active or []
        self.error = error

    async def get_active_alerts(self):
        if self.error:
            raise self.error
        return self.active


class AlertReconciliationServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = alert_context()
        self.manager = InMemoryAlertStateManager()
        self.manager.save_alert_state("fp", {
            "fingerprint": "fp",
            "alert_name": "DiskFull",
            "service": "test",
            "status": "firing",
            "startsAt": self.context.start_time,
            "alert_id": 7,
        })
        self.history = Mock()
        self.history.find_firing_alert_id.return_value = None
        self.history.restore_active_alert_event.return_value = 9
        self.history.list_firing_lifecycles.return_value = []

    async def test_active_alert_is_not_reconciled(self):
        service = AlertReconciliationService(
            self.manager, self.history, FakeAlertManager([self.context]), 60
        )
        self.assertEqual(await service.reconcile_once(), 0)
        self.history.resolve_alert_event.assert_not_called()
        self.assertIsNotNone(self.manager.get_alert_state("fp"))

    async def test_missing_alert_updates_mysql_then_removes_redis(self):
        self.history.resolve_alert_event.return_value = True
        service = AlertReconciliationService(
            self.manager, self.history, FakeAlertManager(), 60
        )
        self.assertEqual(await service.reconcile_once(), 1)
        self.history.resolve_alert_event.assert_called_once()
        self.assertIsNone(self.manager.get_alert_state("fp"))

    async def test_mysql_failure_preserves_redis(self):
        self.history.resolve_alert_event.return_value = False
        service = AlertReconciliationService(
            self.manager, self.history, FakeAlertManager(), 60
        )
        self.assertEqual(await service.reconcile_once(), 0)
        self.assertIsNotNone(self.manager.get_alert_state("fp"))

    async def test_alertmanager_failure_does_not_reconcile(self):
        service = AlertReconciliationService(
            self.manager, self.history, FakeAlertManager(error=RuntimeError("unavailable")), 60
        )
        with self.assertRaises(RuntimeError):
            await service.reconcile_once()
        self.history.resolve_alert_event.assert_not_called()
        self.assertIsNotNone(self.manager.get_alert_state("fp"))

    async def test_non_firing_redis_state_is_not_reconciled(self):
        self.manager.update_alert_status("fp", "resolved")
        service = AlertReconciliationService(
            self.manager, self.history, FakeAlertManager(), 60
        )
        self.assertEqual(await service.reconcile_once(), 0)
        self.history.resolve_alert_event.assert_not_called()
        self.assertIsNotNone(self.manager.get_alert_state("fp"))

    async def test_mysql_race_preserves_current_redis_state(self):
        def start_new_lifecycle(*_args):
            self.manager.save_alert_state("fp", {
                "fingerprint": "fp",
                "status": "firing",
                "startsAt": "2026-07-25T11:00:00Z",
                "alert_id": 8,
            })
            return True

        self.history.resolve_alert_event.side_effect = start_new_lifecycle
        service = AlertReconciliationService(
            self.manager, self.history, FakeAlertManager(), 60
        )
        self.assertEqual(await service.reconcile_once(), 0)
        self.assertEqual(
            self.manager.get_alert_state("fp")["startsAt"],
            "2026-07-25T19:00:00",
        )

    async def test_startup_recovery_creates_missing_lifecycle_and_diagnoses(self):
        manager = InMemoryAlertStateManager()
        self.history.restore_active_alert_event.return_value = 9
        diagnosed = AsyncMock()
        service = AlertReconciliationService(
            manager,
            self.history,
            FakeAlertManager([self.context]),
            60,
            recovered_diagnosis=diagnosed,
        )

        self.assertEqual(await service.reconcile_once(), 0)
        await asyncio.gather(*tuple(service._diagnosis_tasks))

        state = manager.get_alert_state("fp")
        self.assertIsNotNone(state)
        self.assertEqual(state["status"], "firing")
        self.assertEqual(state["alert_id"], 9)
        self.assertEqual(state["diagnosis_status"], "completed")
        self.assertTrue(state["recovered_from_alertmanager"])
        self.history.restore_active_alert_event.assert_called_once()
        diagnosed.assert_awaited_once()

    async def test_mysql_firing_restores_redis_without_duplicate_diagnosis(self):
        manager = InMemoryAlertStateManager()
        self.history.find_firing_alert_id.return_value = 12
        diagnosed = AsyncMock()
        service = AlertReconciliationService(
            manager,
            self.history,
            FakeAlertManager([self.context]),
            60,
            recovered_diagnosis=diagnosed,
        )

        await service.reconcile_once()

        state = manager.get_alert_state("fp")
        self.assertEqual(state["alert_id"], 12)
        self.assertEqual(state["diagnosis_status"], "completed")
        self.history.restore_active_alert_event.assert_not_called()
        diagnosed.assert_not_awaited()

    async def test_recovered_lifecycle_remains_idempotent_on_next_cycle(self):
        manager = InMemoryAlertStateManager()
        self.history.restore_active_alert_event.return_value = 13
        diagnosed = AsyncMock()
        service = AlertReconciliationService(
            manager,
            self.history,
            FakeAlertManager([self.context]),
            60,
            recovered_diagnosis=diagnosed,
        )

        await service.reconcile_once()
        await asyncio.gather(*tuple(service._diagnosis_tasks))
        await service.reconcile_once()

        self.history.restore_active_alert_event.assert_called_once()
        diagnosed.assert_awaited_once()

    async def test_recovered_diagnosis_consumes_generator_to_natural_completion(self):
        finalized = asyncio.Event()

        async def diagnosis_stream(*_args):
            try:
                yield {"type": "complete", "response": "report"}
            finally:
                finalized.set()

        service = AlertReconciliationService(
            self.manager,
            self.history,
            FakeAlertManager(),
            60,
        )
        with patch(
            "app.services.aiops_service.aiops_service.diagnose_alert",
            new=diagnosis_stream,
        ):
            await service._diagnose_recovered(self.context, "recovery-session")

        self.assertTrue(finalized.is_set())
        self.assertEqual(
            self.manager.get_alert_state("fp")["diagnosis_status"],
            "completed",
        )

    async def test_mysql_orphan_without_redis_or_alertmanager_is_resolved(self):
        manager = InMemoryAlertStateManager()
        self.history.list_firing_lifecycles.return_value = [{
            "alert_id": 21,
            "fingerprint": "orphan-fp",
            "alert_name": "SmartLifeMysqlSlowQueryHigh",
            "service": "smartlife",
            "startsAt": "2026-07-31T22:20:09",
        }]
        self.history.resolve_alert_event.return_value = True
        service = AlertReconciliationService(
            manager, self.history, FakeAlertManager(), 60
        )

        self.assertEqual(await service.reconcile_once(), 1)
        self.history.resolve_alert_event.assert_called_once_with(
            21, "orphan-fp", "2026-07-31T22:20:09", unittest.mock.ANY
        )

    async def test_mysql_snapshot_active_in_alertmanager_is_preserved(self):
        manager = InMemoryAlertStateManager()
        self.history.list_firing_lifecycles.return_value = [{
            "alert_id": 21, "fingerprint": "fp", "startsAt": "2026-07-25 18:00:00",
        }]
        service = AlertReconciliationService(
            manager, self.history, FakeAlertManager([self.context]), 60
        )

        await service.reconcile_once()
        self.history.resolve_alert_event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
