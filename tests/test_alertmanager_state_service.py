import unittest

from app.models.alert import parse_alertmanager_payload
from app.services.alertmanager_state_service import AlertManagerStateService


class AlertManagerStateServiceTest(unittest.TestCase):
    def test_match_requires_fingerprint_name_and_starts_at(self):
        def context(fingerprint: str, starts_at: str):
            return parse_alertmanager_payload({
                "status": "firing",
                "alerts": [{
                    "status": "firing",
                    "fingerprint": fingerprint,
                    "labels": {"alertname": "SmartLifeServiceDown", "job": "smartlife"},
                    "startsAt": starts_at,
                    "endsAt": "0001-01-01T00:00:00Z",
                }],
            })[0]

        candidate = context("fp", "2026-07-25T00:00:00Z")
        self.assertTrue(AlertManagerStateService.contains([candidate], candidate))
        self.assertFalse(AlertManagerStateService.contains(
            [context("fp", "2026-07-25T01:00:00Z")], candidate
        ))
