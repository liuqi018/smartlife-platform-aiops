import json
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api import alert
from app.models.alert import parse_alertmanager_payload
from app.services.alert_state_manager import InMemoryAlertStateManager


def payload(status: str, starts_at: str = "2026-07-24T09:54:02Z") -> dict:
    return {
        "status": status,
        "alerts": [{
            "status": status,
            "fingerprint": "test-fingerprint",
            "labels": {
                "alertname": "SmartLifeServiceDown", "service": "smartlife",
                "severity": "critical", "instance": "localhost:8081",
            },
            "annotations": {"description": "target down"},
            "startsAt": starts_at,
            "endsAt": "2026-07-24T10:20:02Z" if status == "resolved" else "0001-01-01T00:00:00Z",
        }],
    }


def cpu_payload(alert_name: str, fingerprint: str) -> dict:
    result = payload("firing")
    item = result["alerts"][0]
    item["fingerprint"] = fingerprint
    item["labels"]["alertname"] = alert_name
    return result


class AlertLifecycleTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(alert.router, prefix="/api")
        self.client = TestClient(app)
        self.manager = InMemoryAlertStateManager()
        self.active_fetch = patch.object(
            alert.alertmanager_state_service, "get_active_alerts", new=AsyncMock(return_value=[])
        )
        self.active_match = patch.object(
            alert.alertmanager_state_service, "contains", return_value=True
        )
        self.active_fetch.start()
        self.active_match_mock = self.active_match.start()
        self.addCleanup(self.active_fetch.stop)
        self.addCleanup(self.active_match.stop)

    def test_resolved_updates_state_without_starting_diagnosis_or_session(self):
        diagnosis = AsyncMock()
        self.manager.save_alert_state("test-fingerprint", {
            "fingerprint": "test-fingerprint",
            "status": "firing",
            "startsAt": "2026-07-24T17:54:02+08:00",
            "session_id": "existing-session",
            "diagnosis_status": "completed",
            "alert_id": 7,
        })
        with patch.object(alert, "alert_state_manager", self.manager), patch.object(
            alert.alert_history_service, "resolve_alert_event"
        ) as resolve_event, patch.object(
            alert.alert_history_service, "save_alert_event", return_value=2
        ) as save_event, patch.object(alert.aiops_service, "diagnose_alert", diagnosis), patch.object(
            alert, "_build_session_id", side_effect=AssertionError("resolved must not create session")
        ):
            response = self.client.post("/api/alerts/webhook", json=payload("resolved"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["diagnosis_started"], 0)
        self.assertIsNone(self.manager.get_alert_state("test-fingerprint"))
        resolve_event.assert_called_once_with(
            7, "test-fingerprint", "2026-07-24T17:54:02", "2026-07-24T18:20:02"
        )
        save_event.assert_not_called()
        diagnosis.assert_not_called()

    def test_resolved_keeps_current_state_when_mysql_update_fails(self):
        self.manager.save_alert_state("test-fingerprint", {
            "fingerprint": "test-fingerprint",
            "status": "firing",
            "startsAt": "2026-07-24T17:54:02+08:00",
            "alert_id": 7,
        })
        with patch.object(alert, "alert_state_manager", self.manager), patch.object(
            alert.alert_history_service, "resolve_alert_event", return_value=False
        ):
            response = self.client.post("/api/alerts/webhook", json=payload("resolved"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.manager.get_alert_state("test-fingerprint")["status"], "firing")

    def test_firing_after_resolved_starts_a_new_lifecycle(self):
        async def diagnosis(**_kwargs):
            yield {"type": "complete", "response": "report"}

        self.manager.save_alert_state("test-fingerprint", {
            "fingerprint": "test-fingerprint", "status": "firing",
            "startsAt": "2026-07-24T17:54:02+08:00", "alert_id": 7,
            "diagnosis_status": "completed", "session_id": "old-session",
        })
        with patch.object(alert, "alert_state_manager", self.manager), patch.object(
            alert.alert_history_service, "resolve_alert_event", return_value=True
        ), patch.object(
            alert.alert_history_service, "save_alert_event", return_value=8
        ) as save_event, patch.object(
            alert.aiops_service, "diagnose_alert", diagnosis
        ), patch.object(alert, "_build_session_id", return_value="new-session"):
            self.client.post("/api/alerts/webhook", json=payload("resolved"))
            response = self.client.post(
                "/api/alerts/webhook",
                json=payload("firing", "2026-07-24T11:00:00Z"),
            )
            list(response.iter_lines())

        self.assertEqual(save_event.call_count, 1)
        state = self.manager.get_alert_state("test-fingerprint")
        self.assertEqual(state["startsAt"], "2026-07-24T19:00:00")
        self.assertEqual(state["alert_id"], 8)

    def test_firing_creates_session_and_state(self):
        async def diagnosis(**_kwargs):
            yield {"type": "complete", "response": "report"}

        with patch.object(alert, "alert_state_manager", self.manager), patch.object(
            alert.alert_history_service, "save_alert_event", return_value=7
        ), patch.object(alert.aiops_service, "diagnose_alert", diagnosis), patch.object(
            alert, "_build_session_id", return_value="alert_smartlife_SmartLifeServiceDown_test"
        ):
            response = self.client.post("/api/alerts/webhook", json=payload("firing"))
            list(response.iter_lines())

        state = self.manager.get_alert_state("test-fingerprint")
        self.assertEqual(state["session_id"], "alert_smartlife_SmartLifeServiceDown_test")
        self.assertEqual(state["diagnosis_status"], "completed")
        self.assertEqual(state["alert_id"], 7)
        self.assertEqual(state["repeat_count"], 0)

    def test_resolved_during_diagnosis_is_not_recreated_on_completion(self):
        async def diagnosis(**kwargs):
            alert_context = kwargs["alert_context"]
            alert._resolve_lifecycle(
                alert_context,
                "2026-07-24T18:20:02+08:00",
                create_if_missing=False,
            )
            yield {"type": "complete", "response": "report"}

        with patch.object(alert, "alert_state_manager", self.manager), patch.object(
            alert.alert_history_service, "save_alert_event", return_value=7
        ), patch.object(
            alert.alert_history_service, "resolve_alert_event", return_value=True
        ) as resolve_event, patch.object(
            alert.aiops_service, "diagnose_alert", diagnosis
        ), patch.object(alert, "_build_session_id", return_value="alert_smartlife_SmartLifeServiceDown_test"):
            response = self.client.post("/api/alerts/webhook", json=payload("firing"))
            list(response.iter_lines())

        self.assertIsNone(self.manager.get_alert_state("test-fingerprint"))
        resolve_event.assert_called_once_with(
            7, "test-fingerprint", "2026-07-24T17:54:02", "2026-07-24T18:20:02"
        )

    def test_repeated_firing_skips_second_diagnosis_and_session(self):
        calls = []

        async def diagnosis(**_kwargs):
            calls.append(_kwargs)
            yield {"type": "complete", "response": "report"}

        session_ids = ["alert_smartlife_SmartLifeServiceDown_first"]

        def build_session(_context):
            if not session_ids:
                raise AssertionError("repeat firing must not create another session")
            return session_ids.pop()

        with patch.object(alert, "alert_state_manager", self.manager), patch.object(
            alert.alert_history_service, "save_alert_event", return_value=7
        ), patch.object(alert.aiops_service, "diagnose_alert", diagnosis), patch.object(
            alert, "_build_session_id", side_effect=build_session
        ):
            first = self.client.post("/api/alerts/webhook", json=payload("firing"))
            list(first.iter_lines())
            second = self.client.post("/api/alerts/webhook", json=payload("firing"))

        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["diagnosis_started"], 0)
        self.assertEqual(second.json()["repeated"], 1)
        self.assertEqual(len(calls), 1)
        state = self.manager.get_alert_state("test-fingerprint")
        self.assertEqual(state["status"], "firing")
        self.assertEqual(state["diagnosis_status"], "completed")
        self.assertEqual(state["repeat_count"], 1)
        self.assertIn("last_seen_at", state)

    def test_duplicate_is_rejected_before_first_sse_stream_starts(self):
        request_body = json.dumps(payload("firing")).encode()

        def webhook_request():
            sent = False

            async def receive():
                nonlocal sent
                if sent:
                    return {"type": "http.disconnect"}
                sent = True
                return {"type": "http.request", "body": request_body, "more_body": False}

            return Request({"type": "http", "method": "POST", "headers": []}, receive)

        with patch.object(alert, "alert_state_manager", self.manager), patch.object(
            alert.alert_history_service, "save_alert_event", return_value=7
        ) as save_event, patch.object(
            alert.aiops_service, "diagnose_alert", new=AsyncMock()
        ) as diagnosis, patch.object(alert, "_build_session_id") as build_session:
            first = asyncio.run(alert._diagnose_alerts_stream(webhook_request()))
            second = asyncio.run(alert._diagnose_alerts_stream(webhook_request()))

        self.assertEqual(first.__class__.__name__, "EventSourceResponse")
        self.assertEqual(second.status_code, 200)
        second_payload = json.loads(second.body)
        self.assertEqual(second_payload["diagnosis_started"], 0)
        self.assertEqual(second_payload["repeated"], 1)
        self.assertEqual(second_payload["existing_alerts"][0]["diagnosis_status"], "claimed")
        save_event.assert_not_called()
        build_session.assert_not_called()
        diagnosis.assert_not_called()

    def test_smartlife_cpu_alert_persists_one_lifecycle(self):
        calls = []

        async def diagnosis(**kwargs):
            calls.append(kwargs)
            yield {"type": "complete", "response": "report"}

        smartlife_payload = cpu_payload(
            "SmartLifeHighCPUUsage",
            "smartlife-fingerprint",
        )

        with patch.object(alert, "alert_state_manager", self.manager), patch.object(
            alert.alert_history_service, "save_alert_event", return_value=7
        ) as save_event, patch.object(
            alert.aiops_service, "diagnose_alert", diagnosis
        ), patch.object(
            alert,
            "_build_session_id",
            return_value="alert_smartlife_SmartLifeHighCPUUsage_test",
        ):
            first = self.client.post("/api/alerts/webhook", json=smartlife_payload)
            list(first.iter_lines())
            second = self.client.post("/api/alerts/webhook", json=smartlife_payload)

        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["repeated"], 1)
        self.assertEqual(len(calls), 1)
        save_event.assert_called_once()
        self.assertEqual(save_event.call_args.args[0]["alert_name"], "SmartLifeHighCPUUsage")
        self.assertEqual(self.manager.count_active_alerts(), 1)
        state = self.manager.get_alert_state("smartlife-fingerprint")
        self.assertIsNotNone(state)
        self.assertEqual(state["alert_name"], "SmartLifeHighCPUUsage")

    def test_historical_firing_missing_from_alertmanager_skips_diagnosis(self):
        self.active_match_mock.return_value = False
        diagnosis = AsyncMock()
        with patch.object(alert, "alert_state_manager", self.manager), patch.object(
            alert.alert_history_service, "save_alert_event"
        ) as save_event, patch.object(alert.aiops_service, "diagnose_alert", diagnosis), patch.object(
            alert, "_build_session_id", side_effect=AssertionError("historical retry must not create session")
        ):
            response = self.client.post("/api/alerts/webhook", json=payload("firing"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["historical_retries"], 1)
        self.assertEqual(response.json()["diagnosis_started"], 0)
        self.assertIsNone(self.manager.get_alert_state("test-fingerprint"))
        save_event.assert_not_called()
        diagnosis.assert_not_called()

    def test_firing_with_end_time_is_closed_without_diagnosis(self):
        ended = payload("firing")
        ended["alerts"][0]["endsAt"] = "2026-07-24T10:20:02Z"
        diagnosis = AsyncMock()
        with patch.object(alert, "alert_state_manager", self.manager), patch.object(
            alert.alert_history_service, "save_alert_event", return_value=9
        ) as save_event, patch.object(
            alert.alert_history_service, "resolve_alert_event", return_value=True
        ) as resolve_event, patch.object(alert.aiops_service, "diagnose_alert", diagnosis):
            response = self.client.post("/api/alerts/webhook", json=ended)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ended_firing"], 1)
        self.assertEqual(response.json()["diagnosis_started"], 0)
        save_event.assert_called_once()
        resolve_event.assert_called_once()
        diagnosis.assert_not_called()

    def test_new_starts_at_creates_new_session_for_same_fingerprint(self):
        calls = []

        async def diagnosis(**kwargs):
            calls.append(kwargs["session_id"])
            yield {"type": "complete", "response": "report"}

        sessions = iter(["first-session", "second-session"])
        with patch.object(alert, "alert_state_manager", self.manager), patch.object(
            alert.alert_history_service, "save_alert_event", return_value=7
        ), patch.object(alert.aiops_service, "diagnose_alert", diagnosis), patch.object(
            alert, "_build_session_id", side_effect=lambda _context: next(sessions)
        ):
            list(self.client.post("/api/alerts/webhook", json=payload("firing")).iter_lines())
            list(self.client.post(
                "/api/alerts/webhook",
                json=payload("firing", "2026-07-24T11:00:00Z"),
            ).iter_lines())

        self.assertEqual(calls, ["first-session", "second-session"])
        state = self.manager.get_alert_state("test-fingerprint")
        self.assertEqual(state["startsAt"], "2026-07-24T19:00:00")
        self.assertEqual(state["session_id"], "second-session")
        self.assertEqual(state["repeat_count"], 0)

    def test_old_resolved_does_not_overwrite_new_firing_cycle(self):
        self.manager.save_alert_state("test-fingerprint", {
            "fingerprint": "test-fingerprint",
            "status": "firing",
            "startsAt": "2026-07-24T19:00:00+08:00",
            "session_id": "new-session",
            "diagnosis_status": "completed",
        })
        diagnosis = AsyncMock()
        with patch.object(alert, "alert_state_manager", self.manager), patch.object(
            alert.alert_history_service, "resolve_alert_event"
        ) as resolve_event, patch.object(alert.aiops_service, "diagnose_alert", diagnosis):
            response = self.client.post("/api/alerts/webhook", json=payload("resolved"))

        self.assertEqual(response.status_code, 200)
        state = self.manager.get_alert_state("test-fingerprint")
        self.assertEqual(state["status"], "firing")
        self.assertEqual(state["startsAt"], "2026-07-24T19:00:00")
        resolve_event.assert_called_once_with(
            None,
            "test-fingerprint",
            "2026-07-24T17:54:02",
            "2026-07-24T18:20:02",
        )
        diagnosis.assert_not_called()


if __name__ == "__main__":
    unittest.main()
