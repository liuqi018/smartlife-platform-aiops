"""Read-only AlertManager state used to validate at-least-once webhooks."""

from __future__ import annotations

from typing import Any

import httpx

from app.config import config
from app.models.alert import AlertContext, parse_alertmanager_payload


class AlertManagerStateService:
    async def get_active_alerts(self) -> list[AlertContext]:
        url = f"{config.alertmanager_base_url.rstrip('/')}/api/v2/alerts"
        async with httpx.AsyncClient(timeout=config.alertmanager_request_timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("AlertManager /api/v2/alerts did not return a list")

        active: list[AlertContext] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            status = item.get("status") or {}
            if not isinstance(status, dict) or status.get("state") != "active":
                continue
            parsed = parse_alertmanager_payload({"status": "firing", "alerts": [item]})
            if parsed:
                active.append(parsed[0])
        return active

    @staticmethod
    def contains(active_alerts: list[AlertContext], candidate: AlertContext) -> bool:
        return any(
            alert.fingerprint == candidate.fingerprint
            and alert.alert_name == candidate.alert_name
            and alert.start_time == candidate.start_time
            for alert in active_alerts
        )


alertmanager_state_service = AlertManagerStateService()
