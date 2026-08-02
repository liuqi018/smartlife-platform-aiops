"""Periodic compensation for AlertManager resolved notifications lost on restart."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Awaitable, Callable

from loguru import logger

from app.config import config
from app.services.alert_history_service import AlertHistoryService, alert_history_service
from app.services.alert_state_manager import AlertStateManager, alert_state_manager
from app.services.alertmanager_state_service import AlertManagerStateService, alertmanager_state_service
from app.models.alert import AlertContext
from app.utils.timezone import normalize_business_iso, now_shanghai, now_shanghai_iso

RecoveredDiagnosis = Callable[[AlertContext, str], Awaitable[None]]


class AlertReconciliationService:
    def __init__(
        self,
        state_manager: AlertStateManager = alert_state_manager,
        history_service: AlertHistoryService = alert_history_service,
        alertmanager_service: AlertManagerStateService = alertmanager_state_service,
        interval_seconds: float | None = None,
        recovered_diagnosis: RecoveredDiagnosis | None = None,
    ) -> None:
        self.state_manager = state_manager
        self.history_service = history_service
        self.alertmanager_service = alertmanager_service
        self.interval_seconds = interval_seconds or config.alert_reconciliation_interval_seconds
        self.recovered_diagnosis = recovered_diagnosis
        self._task: asyncio.Task[None] | None = None
        self._diagnosis_tasks: set[asyncio.Task[None]] = set()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="alert-reconciliation")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._diagnosis_tasks:
            for task in tuple(self._diagnosis_tasks):
                task.cancel()
            await asyncio.gather(*tuple(self._diagnosis_tasks), return_exceptions=True)

    async def _run(self) -> None:
        # The first comparison occurs after one full interval, never during startup.
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.reconcile_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.bind(aiops=True, stage="alert_reconciliation").exception(
                    "alert_reconciliation_failed"
                )

    async def reconcile_once(self) -> int:
        logger.bind(aiops=True, stage="alert_reconciliation").info(
            "alert_reconciliation_started"
        )
        # Snapshot first: alerts created while the HTTP request is in flight are
        # deliberately deferred to the next cycle instead of being closed.
        current_states = await asyncio.to_thread(self.state_manager.list_active_states)
        mysql_firing = await asyncio.to_thread(self.history_service.list_firing_lifecycles)
        active_alerts = await self.alertmanager_service.get_active_alerts()
        active_identities = {
            (alert.fingerprint, normalize_business_iso(alert.start_time))
            for alert in active_alerts
        }
        redis_identities = {
            (
                str(state.get("fingerprint") or ""),
                normalize_business_iso(str(state.get("startsAt") or "")),
            )
            for state in current_states
            if state.get("status") == "firing"
        }
        await self._recover_missing_active_alerts(active_alerts, redis_identities)
        reconciled = 0
        for state in current_states:
            if state.get("status") != "firing":
                continue
            fingerprint = str(state.get("fingerprint") or "")
            starts_at = str(state.get("startsAt") or "")
            if not fingerprint or not starts_at:
                continue
            if (fingerprint, normalize_business_iso(starts_at)) in active_identities:
                continue

            end_time = now_shanghai_iso()
            updated = await asyncio.to_thread(
                self.history_service.resolve_alert_event,
                state.get("alert_id"),
                fingerprint,
                starts_at,
                end_time,
            )
            if not updated:
                logger.bind(aiops=True, stage="alert_reconciliation").warning(
                    "alert_reconciliation_skipped fingerprint={} startsAt={} reason=mysql_update_not_applied",
                    fingerprint,
                    starts_at,
                )
                continue

            removed, _ = await asyncio.to_thread(
                self.state_manager.resolve_current, fingerprint, starts_at
            )
            if not removed:
                logger.bind(aiops=True, stage="alert_reconciliation").warning(
                    "alert_reconciliation_redis_not_removed fingerprint={} startsAt={} reason=state_changed",
                    fingerprint,
                    starts_at,
                )
                continue
            reconciled += 1
            logger.bind(
                aiops=True,
                stage="alert_reconciliation",
                alert_name=str(state.get("alert_name") or "-"),
                service=str(state.get("service") or "-"),
            ).info(
                "alert_reconciled fingerprint={} startsAt={} end_time={} reconcile_reason=alertmanager_missing",
                fingerprint,
                starts_at,
                end_time,
            )
        for lifecycle in mysql_firing:
            fingerprint = str(lifecycle.get("fingerprint") or "")
            starts_at = str(lifecycle.get("startsAt") or "")
            identity = (fingerprint, normalize_business_iso(starts_at))
            if not fingerprint or not starts_at:
                continue
            if identity in active_identities or identity in redis_identities:
                continue
            end_time = now_shanghai_iso()
            updated = await asyncio.to_thread(
                self.history_service.resolve_alert_event,
                lifecycle.get("alert_id"), fingerprint, starts_at, end_time,
            )
            if not updated:
                continue
            reconciled += 1
            logger.bind(
                aiops=True,
                stage="alert_reconciliation",
                alert_name=str(lifecycle.get("alert_name") or "-"),
                service=str(lifecycle.get("service") or "-"),
            ).info(
                "alert_mysql_orphan_reconciled fingerprint={} startsAt={} end_time={} reconcile_reason=missing_from_alertmanager_and_redis",
                fingerprint, starts_at, end_time,
            )
        return reconciled

    async def _recover_missing_active_alerts(
        self,
        active_alerts: list[AlertContext],
        redis_identities: set[tuple[str, str]],
    ) -> int:
        """Restore AlertManager-active lifecycles absent from local Redis/MySQL."""
        recovered = 0
        for alert in active_alerts:
            identity = (alert.fingerprint, normalize_business_iso(alert.start_time))
            if identity in redis_identities:
                continue

            mysql_alert_id = await asyncio.to_thread(
                self.history_service.find_firing_alert_id,
                alert.fingerprint,
                alert.start_time,
            )
            session_id = self._recovery_session_id(alert)
            if mysql_alert_id is not None:
                # MySQL proves the firing webhook was persisted earlier. Restore
                # only the volatile current state; do not duplicate diagnosis.
                await asyncio.to_thread(
                    self.state_manager.save_alert_state,
                    alert.fingerprint,
                    self._state(alert, mysql_alert_id, session_id, "completed"),
                )
                redis_identities.add(identity)
                logger.bind(aiops=True, stage="alert_reconciliation").info(
                    "alert_redis_state_restored fingerprint={} startsAt={} alert_id={}",
                    alert.fingerprint,
                    alert.start_time,
                    mysql_alert_id,
                )
                continue

            claimed, _claim = await asyncio.to_thread(
                self.state_manager.claim_lifecycle,
                alert.fingerprint,
                alert.start_time,
                now_shanghai_iso(),
            )
            if not claimed:
                # Another worker or a concurrent webhook owns this lifecycle.
                continue

            alert_id = await asyncio.to_thread(
                self.history_service.restore_active_alert_event,
                alert.model_copy(update={"status": "firing"}).model_dump(),
            )
            await asyncio.to_thread(
                self.state_manager.save_alert_state,
                alert.fingerprint,
                self._state(alert, alert_id, session_id, "running"),
            )
            redis_identities.add(identity)
            recovered += 1
            logger.bind(
                aiops=True,
                stage="alert_reconciliation",
                alert_name=alert.alert_name,
                service=alert.service,
                session_id=session_id,
            ).info(
                "alert_recovered fingerprint={} startsAt={} alert_id={} diagnosis_trigger=true",
                alert.fingerprint,
                alert.start_time,
                alert_id,
            )
            task = asyncio.create_task(
                self._diagnose_recovered(alert, session_id),
                name=f"recovered-alert-diagnosis:{alert.fingerprint}",
            )
            self._diagnosis_tasks.add(task)
            task.add_done_callback(self._diagnosis_tasks.discard)
        return recovered

    @staticmethod
    def _recovery_session_id(alert: AlertContext) -> str:
        timestamp = now_shanghai().strftime("%Y%m%d%H%M%S")
        return (
            f"alert_recovery_{alert.service.replace(' ', '_')}_"
            f"{alert.alert_name.replace(' ', '_')}_{timestamp}"
        )

    @staticmethod
    def _state(
        alert: AlertContext,
        alert_id: int | None,
        session_id: str,
        diagnosis_status: str,
    ) -> dict:
        return {
            "fingerprint": alert.fingerprint,
            "alert_name": alert.alert_name,
            "service": alert.service,
            "severity": alert.severity,
            "status": "firing",
            "session_id": session_id,
            "startsAt": alert.start_time,
            "endsAt": alert.end_time,
            "diagnosis_status": diagnosis_status,
            "alert_id": alert_id,
            "last_seen_at": now_shanghai_iso(),
            "repeat_count": 0,
            "recovered_from_alertmanager": True,
        }

    async def _diagnose_recovered(self, alert: AlertContext, session_id: str) -> None:
        try:
            if self.recovered_diagnosis is not None:
                await self.recovered_diagnosis(alert, session_id)
            else:
                # Lazy import avoids coupling the lifecycle service to graph
                # initialization and reuses the existing diagnosis entry point.
                from app.services.aiops_service import aiops_service

                completed = False
                async for event in aiops_service.diagnose_alert(alert, session_id):
                    if event.get("type") == "complete":
                        completed = True
                # Consume the async generator to natural completion in this
                # task. Breaking on the complete event defers generator cleanup
                # to another Context and can break Loguru ContextVar reset.
                if not completed:
                    raise RuntimeError("recovered alert diagnosis ended without a complete event")
            await asyncio.to_thread(
                self.state_manager.update_current_status,
                alert.fingerprint,
                alert.start_time,
                "firing",
                diagnosis_status="completed",
            )
        except Exception:
            await asyncio.to_thread(
                self.state_manager.update_current_status,
                alert.fingerprint,
                alert.start_time,
                "firing",
                diagnosis_status="failed",
            )
            logger.bind(
                aiops=True,
                stage="alert_reconciliation",
                alert_name=alert.alert_name,
                service=alert.service,
                session_id=session_id,
            ).exception("recovered_alert_diagnosis_failed")


alert_reconciliation_service = AlertReconciliationService()
