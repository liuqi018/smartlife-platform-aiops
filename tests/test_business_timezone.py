import unittest
from datetime import datetime

from loguru import logger

from app.models.alert import parse_alertmanager_payload
from app.services.alert_history_service import AlertHistoryService
from app.services.alert_state_manager import InMemoryAlertStateManager
from app.utils.timezone import SHANGHAI_TZ, now_shanghai_iso


class BusinessTimezoneTest(unittest.TestCase):
    def test_alert_redis_mysql_and_log_clock_use_shanghai(self):
        context = parse_alertmanager_payload({
            "status": "firing",
            "alerts": [{
                "status": "firing",
                "fingerprint": "timezone-test",
                "labels": {"alertname": "SmartLifeServiceDown", "job": "smartlife"},
                "startsAt": "2026-07-24T14:45:17Z",
                "endsAt": "2026-07-24T14:50:17Z",
            }],
        })[0]
        now_iso = now_shanghai_iso()
        manager = InMemoryAlertStateManager()
        manager.save_alert_state(context.fingerprint, {
            "startsAt": context.start_time,
            "endsAt": context.end_time,
            "last_seen_at": now_iso,
        })
        redis_state = manager.get_alert_state(context.fingerprint)

        self.assertEqual(context.start_time, "2026-07-24T22:45:17")
        self.assertEqual(redis_state["endsAt"], "2026-07-24T22:50:17")
        self.assertNotIn("+08:00", redis_state["last_seen_at"])
        self.assertEqual(
            AlertHistoryService._time(context.start_time),
            datetime(2026, 7, 24, 22, 45, 17),
        )
        self.assertIsNone(datetime.fromisoformat(now_iso).utcoffset())

        captured = []
        sink_id = logger.add(lambda message: captured.append(message.record["time"]), enqueue=False)
        try:
            logger.info("timezone consistency probe")
        finally:
            logger.remove(sink_id)
        self.assertEqual(captured[-1].utcoffset().total_seconds(), 8 * 3600)
