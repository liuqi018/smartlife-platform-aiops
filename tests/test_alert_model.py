import unittest

from app.models.alert import parse_alertmanager_payload


class AlertNameNormalizationTest(unittest.TestCase):
    def test_smartlife_alert_name_and_fingerprint_are_preserved(self):
        context = parse_alertmanager_payload({
            "status": "firing",
            "alerts": [{
                "status": "firing",
                "fingerprint": "smartlife-alertmanager-fingerprint",
                "labels": {
                    "alertname": "SmartLifeHighCPUUsage",
                    "job": "smartlife",
                    "severity": "warning",
                },
                "startsAt": "2026-07-24T10:00:00Z",
            }],
        })[0]

        self.assertEqual(context.alert_name, "SmartLifeHighCPUUsage")
        self.assertEqual(context.fingerprint, "smartlife-alertmanager-fingerprint")
        self.assertEqual(
            context.raw_alert["labels"]["alertname"],
            "SmartLifeHighCPUUsage",
        )

    def test_non_smartlife_service_is_ignored(self):
        contexts = parse_alertmanager_payload({
            "status": "firing",
            "alerts": [{
                "status": "firing",
                "fingerprint": "unrelated-service",
                "labels": {
                    "alertname": "UnrelatedServiceDown",
                    "service": "unrelated",
                    "severity": "critical",
                },
                "startsAt": "2026-07-24T10:00:00Z",
            }],
        })

        self.assertEqual(contexts, [])
