import unittest
from unittest.mock import MagicMock, patch

from app.services.alert_history_service import AlertHistoryService


class _Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_value


class AlertHistoryServiceTest(unittest.TestCase):
    def test_save_alert_event_returns_existing_lifecycle_id_on_duplicate(self):
        service = AlertHistoryService()
        service.available = True
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.lastrowid = 42
        service._connect = MagicMock(return_value=_Connection(cursor))
        alert = {
            "fingerprint": "fp-1",
            "alert_name": "SmartLifeServiceDown",
            "service": "smartlife",
            "severity": "critical",
            "status": "firing",
            "start_time": "2026-07-27T10:00:00+08:00",
            "end_time": "",
        }

        alert_id = service.save_alert_event(alert)

        sql, values = cursor.execute.call_args.args
        self.assertIn("ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)", sql)
        self.assertEqual(values[0], "fp-1")
        self.assertEqual(alert_id, 42)

    def test_schema_declares_fingerprint_start_time_unique_lifecycle(self):
        source = __import__(
            "inspect"
        ).getsource(AlertHistoryService._init_schema)

        self.assertIn("UNIQUE KEY uq_alert_lifecycle (fingerprint, start_time)", source)
        self.assertIn("CREATE UNIQUE INDEX uq_alert_lifecycle", source)
        self.assertIn("UPDATE diagnosis_report report", source)

    def test_save_report_persists_session_id_in_mysql(self):
        service = AlertHistoryService()
        service.available = True
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        service._connect = MagicMock(return_value=_Connection(cursor))

        persisted = service.save_diagnosis_report(
            7, "alert-session-123", [("step", "evidence")], "报告"
        )

        sql, values = cursor.execute.call_args.args
        self.assertIn("session_id", sql)
        self.assertEqual(values[0], 7)
        self.assertEqual(values[1], "alert-session-123")
        self.assertEqual(values[5], "报告")
        self.assertTrue(persisted)

    def test_restore_active_event_reopens_existing_lifecycle(self):
        service = AlertHistoryService()
        service.available = True
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.lastrowid = 21
        service._connect = MagicMock(return_value=_Connection(cursor))

        alert_id = service.restore_active_alert_event({
            "fingerprint": "fp-active",
            "alert_name": "SmartLifeJvmMemoryHighUsage",
            "service": "smartlife",
            "severity": "warning",
            "start_time": "2026-07-29T10:00:00+08:00",
        })

        sql, _values = cursor.execute.call_args.args
        self.assertIn("status='firing'", sql)
        self.assertIn("end_time=NULL", sql)
        self.assertEqual(alert_id, 21)

    def test_resolve_by_alert_id_does_not_require_duplicate_time_match(self):
        service = AlertHistoryService()
        service.available = True
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.rowcount = 1
        service._connect = MagicMock(return_value=_Connection(cursor))

        updated = service.resolve_alert_event(
            21, "fp-active", "2026-07-29T10:00:00+08:00", "2026-07-29T10:05:00+08:00"
        )

        sql, values = cursor.execute.call_args.args
        self.assertIn("WHERE id=%s AND fingerprint=%s AND status=%s", sql)
        self.assertNotIn("start_time=%s", sql)
        self.assertEqual(values[2:], (21, "fp-active", "firing"))
        self.assertTrue(updated)

    def test_report_list_exposes_alert_lifecycle_status_separately(self):
        source = __import__("inspect").getsource(AlertHistoryService.list_diagnosis_reports)

        self.assertIn("event.status AS alert_status", source)
        self.assertIn("event.status AS status", source)
        self.assertIn("'completed' AS diagnosis_status", source)
