"""AlertManager alert context models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.utils.timezone import normalize_business_iso


def _canonical_alert_name(value: Any) -> str:
    return str(value or "UnknownAlert")


class AlertContext(BaseModel):
    """Normalized alert context used as the AIOps diagnosis input."""

    alert_name: str = Field(..., description="Alert name")
    severity: str = Field(default="unknown", description="Alert severity")
    service: str = Field(default="unknown", description="Affected service")
    instance: str = Field(default="unknown", description="Affected instance")
    description: str = Field(default="", description="Alert description")
    start_time: str = Field(default="", description="Alert start time")
    end_time: str = Field(default="", description="Alert end time")
    raw_alert: dict[str, Any] = Field(default_factory=dict, description="Raw AlertManager alert")
    fingerprint: str = Field(default="", description="AlertManager alert fingerprint")
    status: str = Field(default="firing", description="Alert lifecycle status")


def parse_alertmanager_payload(payload: dict[str, Any]) -> list[AlertContext]:
    """Parse AlertManager webhook payload into normalized alert contexts."""
    alerts = payload.get("alerts")
    if not isinstance(alerts, list):
        return []

    contexts: list[AlertContext] = []
    for alert in alerts:
        if not isinstance(alert, dict):
            continue

        labels = alert.get("labels") or {}
        annotations = alert.get("annotations") or {}
        if not isinstance(labels, dict):
            labels = {}
        if not isinstance(annotations, dict):
            annotations = {}

        labels = dict(labels)
        raw_alert_name = labels.get("alertname") or alert.get("alertname") or "UnknownAlert"
        alert_name = _canonical_alert_name(raw_alert_name)
        labels["alertname"] = alert_name
        severity = str(labels.get("severity") or "unknown")
        service = str(
            labels.get("service")
            or labels.get("application")
            or labels.get("app")
            or labels.get("job")
            or "smartlife"
        )
        if service.lower() != "smartlife":
            continue
        instance = str(labels.get("instance") or "unknown")
        description = str(
            annotations.get("description")
            or annotations.get("summary")
            or alert.get("description")
            or ""
        )

        fingerprint = str(alert.get("fingerprint") or "")
        normalized_alert = dict(alert)
        normalized_alert["labels"] = labels

        contexts.append(
            AlertContext(
                alert_name=alert_name,
                severity=severity,
                service=service,
                instance=instance,
                description=description,
                start_time=normalize_business_iso(str(alert.get("startsAt") or "")),
                end_time=normalize_business_iso(str(alert.get("endsAt") or "")),
                raw_alert=normalized_alert,
                fingerprint=fingerprint,
                status=str(alert.get("status") or payload.get("status") or "firing").lower(),
            )
        )

    return contexts
