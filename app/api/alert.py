"""AlertManager webhook API."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from sse_starlette.sse import EventSourceResponse

from app.api.sse_utils import detached_sse_stream
from app.models.alert import AlertContext, parse_alertmanager_payload
from app.services.aiops_service import aiops_service
from app.services.alert_history_service import alert_history_service
from app.services.alert_state_manager import alert_state_manager
from app.services.alertmanager_state_service import alertmanager_state_service
from app.utils.timezone import normalize_business_iso, now_shanghai, now_shanghai_iso

router = APIRouter()


def _log_alert_received(
    context: AlertContext,
    receive_time: str,
    *,
    alertmanager_active: bool | None,
    diagnosis_trigger: bool,
    skip_reason: str = "",
) -> None:
    logger.bind(
        aiops=True,
        alert_name=context.alert_name,
        service=context.service,
        stage="alert_received",
    ).info(
        "alert_received receive_time={} fingerprint={} alertname={} status={} startsAt={} endsAt={} "
        "alertmanager_active={} diagnosis_trigger={} skip_reason={}",
        receive_time,
        context.fingerprint,
        context.alert_name,
        context.status,
        context.start_time,
        context.end_time,
        alertmanager_active,
        diagnosis_trigger,
        skip_reason or "-",
    )


def _resolve_lifecycle(context: AlertContext, end_time: str, *, create_if_missing: bool) -> bool:
    state = alert_state_manager.get_alert_state(context.fingerprint)
    current_lifecycle = bool(
        state
        and normalize_business_iso(str(state.get("startsAt") or "")) == context.start_time
    )
    alert_id = (state or {}).get("alert_id")
    end_time = normalize_business_iso(end_time) or end_time
    if not current_lifecycle and create_if_missing:
        alert_id = alert_history_service.save_alert_event(context.model_copy(update={"status": "firing"}).model_dump())
    database_updated = alert_history_service.resolve_alert_event(
        alert_id, context.fingerprint, context.start_time, end_time
    )
    if not database_updated:
        logger.bind(aiops=True, stage="resolved").warning(
            "alert_resolve_mysql_not_applied fingerprint={} startsAt={} alert_id={}; current state retained for retry",
            context.fingerprint,
            context.start_time,
            alert_id,
        )
        return False

    # MySQL is the source used by Active Alerts. Remove the transient current
    # state only after the persisted firing lifecycle has been closed.
    removed, _ = alert_state_manager.resolve_current(
        context.fingerprint, context.start_time
    )
    return removed or database_updated


def _build_session_id(alert_context: AlertContext) -> str:
    """Build a stable-enough diagnosis session id for one webhook request."""
    timestamp = now_shanghai().strftime("%Y%m%d%H%M%S")
    alert_name = alert_context.alert_name.replace(" ", "_")
    service = alert_context.service.replace(" ", "_")
    return f"alert_{service}_{alert_name}_{timestamp}"


async def _read_payload(request: Request) -> dict[str, Any]:
    """Read and validate the AlertManager JSON payload."""
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {e}") from e

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="AlertManager payload must be a JSON object")
    return payload


async def _diagnose_alerts_stream(request: Request) -> EventSourceResponse | JSONResponse:
    """Parse AlertManager payload and stream diagnosis events."""
    payload = await _read_payload(request)
    alert_contexts = parse_alertmanager_payload(payload)

    if not alert_contexts:
        raise HTTPException(status_code=400, detail="No valid alerts found in AlertManager payload")

    receive_time = now_shanghai_iso()
    firing_alerts = [context for context in alert_contexts if context.status == "firing"]
    resolved_alerts = [context for context in alert_contexts if context.status == "resolved"]
    for context in resolved_alerts:
        state = alert_state_manager.get_alert_state(context.fingerprint)
        resolved = _resolve_lifecycle(context, context.end_time or receive_time, create_if_missing=False)
        _log_alert_received(
            context, receive_time, alertmanager_active=False,
            diagnosis_trigger=False, skip_reason="resolved",
        )
        logger.bind(
            aiops=True,
            session_id=(state or {}).get("session_id", "-"),
            alert_name=context.alert_name,
            service=context.service,
            stage="resolved",
        ).info(
            "alert_resolved fingerprint={} startsAt={} endsAt={} applied={}; diagnosis workflow skipped",
            context.fingerprint,
            context.start_time,
            context.end_time,
            resolved,
        )

    ended_firing = [context for context in firing_alerts if context.end_time]
    for context in ended_firing:
        _resolve_lifecycle(context, context.end_time, create_if_missing=True)
        _log_alert_received(
            context, receive_time, alertmanager_active=False,
            diagnosis_trigger=False, skip_reason="payload_ended",
        )

    candidates = [context for context in firing_alerts if not context.end_time]
    active_alerts: list[AlertContext] = []
    if candidates:
        try:
            active_alerts = await alertmanager_state_service.get_active_alerts()
        except (httpx.HTTPError, ValueError) as exc:
            logger.bind(aiops=True, stage="alert_validation").warning(
                "AlertManager current-state validation failed; webhook will be retried: {}", exc
            )
            raise HTTPException(status_code=503, detail="AlertManager state validation unavailable") from exc

    validated_firing: list[AlertContext] = []
    historical_retries = 0
    for context in candidates:
        is_active = alertmanager_state_service.contains(active_alerts, context)
        if is_active:
            validated_firing.append(context)
            continue
        historical_retries += 1
        _resolve_lifecycle(context, receive_time, create_if_missing=False)
        _log_alert_received(
            context, receive_time, alertmanager_active=False,
            diagnosis_trigger=False, skip_reason="historical_retry",
        )

    diagnosis_alerts: list[AlertContext] = []
    existing_alerts: list[dict[str, Any]] = []
    repeated_alerts = 0
    now = receive_time
    for context in validated_firing:
        claimed, state = alert_state_manager.claim_lifecycle(
            context.fingerprint, context.start_time, now
        )
        if not claimed:
            repeated_alerts += 1
            existing_alerts.append(state or {
                "fingerprint": context.fingerprint,
                "startsAt": context.start_time,
                "status": "firing",
            })
            logger.bind(
                aiops=True,
                session_id=(state or {}).get("session_id", "-"),
                alert_name=context.alert_name,
                service=context.service,
                stage="repeat_firing",
            ).info(
                "alert_repeated fingerprint={} repeat_count={} last_seen_at={}",
                context.fingerprint,
                (state or {}).get("repeat_count", 0),
                now,
            )
            _log_alert_received(
                context, receive_time, alertmanager_active=True,
                diagnosis_trigger=False, skip_reason="repeat_firing",
            )
        else:
            diagnosis_alerts.append(context)
            _log_alert_received(
                context, receive_time, alertmanager_active=True,
                diagnosis_trigger=True,
            )

    logger.info(
        "Received AlertManager webhook: firing={} resolved={} repeated={}",
        len(firing_alerts), len(resolved_alerts), repeated_alerts,
    )
    firing_alerts = diagnosis_alerts
    if not firing_alerts:
        return JSONResponse({
            "status": "accepted",
            "resolved": len(resolved_alerts),
            "repeated": repeated_alerts,
            "historical_retries": historical_retries,
            "ended_firing": len(ended_firing),
            "diagnosis_started": 0,
            "existing_alerts": existing_alerts,
        })

    async def diagnosis_events():
        try:
            yield {
                "event": "message",
                "data": json.dumps(
                    {
                        "type": "step_complete",
                        "stage": "alert_parsed",
                        "message": f"完成告警解析，共 {len(alert_contexts)} 条告警",
                        "content": f"完成告警解析，共 {len(alert_contexts)} 条告警",
                        "alerts": [context.model_dump() for context in firing_alerts],
                    },
                    ensure_ascii=False,
                ),
            }

            for index, alert_context in enumerate(firing_alerts, 1):
                session_id = _build_session_id(alert_context)
                alert_id = alert_history_service.save_alert_event(alert_context.model_dump())
                alert_state_manager.save_alert_state(
                    alert_context.fingerprint,
                    {
                        "fingerprint": alert_context.fingerprint,
                        "alert_name": alert_context.alert_name,
                        "service": alert_context.service,
                        "severity": alert_context.severity,
                        "status": "firing",
                        "session_id": session_id,
                        "startsAt": alert_context.start_time,
                        "endsAt": alert_context.end_time,
                        "diagnosis_status": "running",
                        "alert_id": alert_id,
                        "last_seen_at": now,
                        "repeat_count": 0,
                    },
                )
                bound_logger = logger.bind(
                    aiops=True,
                    session_id=session_id,
                    alert_name=alert_context.alert_name,
                    service=alert_context.service,
                )
                bound_logger.bind(stage="alert_created").info(
                    "alert_created fingerprint={} startsAt={} alert_id={}",
                    alert_context.fingerprint, alert_context.start_time, alert_id,
                )
                bound_logger.bind(stage="diagnosis_started").info(
                    "diagnosis_started fingerprint={} startsAt={}",
                    alert_context.fingerprint, alert_context.start_time,
                )
                yield {
                    "event": "message",
                    "data": json.dumps(
                        {
                            "type": "status",
                            "stage": "diagnosis_started",
                            "message": f"开始诊断告警 {index}/{len(alert_contexts)}: {alert_context.alert_name}",
                            "content": f"正在分析 {alert_context.alert_name} 异常",
                            "session_id": session_id,
                            "alert": alert_context.model_dump(),
                        },
                        ensure_ascii=False,
                    ),
                }

                async for event in aiops_service.diagnose_alert(
                    alert_context=alert_context,
                    session_id=session_id,
                    alert_id=alert_id,
                ):
                    if event.get("type") == "complete":
                        updated, _state = alert_state_manager.update_current_status(
                            alert_context.fingerprint,
                            alert_context.start_time,
                            "firing",
                            diagnosis_status="completed",
                        )
                        bound_logger.bind(stage="diagnosis_completed").info(
                            "diagnosis_completed fingerprint={} startsAt={} state_updated={}",
                            alert_context.fingerprint, alert_context.start_time,
                            updated,
                        )
                    yield {
                        "event": "message",
                        "data": json.dumps(event, ensure_ascii=False),
                    }

        except Exception as e:
            logger.error("AlertManager webhook diagnosis failed: {}", e, exc_info=True)
            yield {
                "event": "message",
                "data": json.dumps(
                    {
                        "type": "error",
                        "stage": "alert_diagnosis_error",
                        "message": f"告警诊断失败: {e}",
                    },
                    ensure_ascii=False,
                ),
            }

    return EventSourceResponse(
        detached_sse_stream(diagnosis_events()),
        ping=15,
        send_timeout=30,
    )


@router.post("/alerts/webhook")
async def alertmanager_webhook(request: Request):
    """Receive AlertManager webhook payload and stream AIOps diagnosis events."""
    return await _diagnose_alerts_stream(request)


@router.post("/alerts")
async def alertmanager_webhook_alias(request: Request):
    """Compatibility endpoint for an existing AlertManager webhook URL."""
    return await _diagnose_alerts_stream(request)
