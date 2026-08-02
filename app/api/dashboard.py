"""Read-only APIs for the AIOps visualization console."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.services.alert_history_service import alert_history_service
from app.tools.query_metrics_alerts import (
    _format_prometheus_metric_result,
    query_prometheus_metrics_api,
)

router = APIRouter()


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


@router.get("/dashboard/summary")
async def dashboard_summary():
    return JSONResponse(
        {"data": alert_history_service.dashboard_summary()},
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


@router.get("/alerts/history")
async def alert_history(
    status: str = Query(default="", pattern="^(|firing|resolved)$"),
    limit: int = Query(default=100, ge=1, le=500),
):
    return JSONResponse(
        {"data": _json_safe(alert_history_service.list_alerts(status=status, limit=limit))},
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


@router.get("/alerts/history/{alert_id}")
async def alert_detail(alert_id: int):
    result = alert_history_service.get_alert_detail(alert_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"data": _json_safe(result)}


@router.get("/diagnosis/reports")
async def diagnosis_reports(limit: int = Query(default=100, ge=1, le=500)):
    return {"data": _json_safe(alert_history_service.list_diagnosis_reports(limit))}


@router.get("/diagnosis/reports/{report_id}")
async def diagnosis_report_detail(report_id: int):
    report = alert_history_service.get_diagnosis_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Diagnosis report not found")
    return {"data": _json_safe(report)}


def _trace_steps(report: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = report.get("evidence") or []
    steps = [{
        "stage": "Alert Received",
        "status": "success",
        "duration": None,
        "message": (
            f"{report.get('alert_name')} received for service "
            f"{report.get('service')}."
        ),
    }, {
        "stage": "Context Analysis",
        "status": "success",
        "duration": None,
        "message": (
            f"Service={report.get('service')}, severity={report.get('severity')}, "
            f"start_time={report.get('start_time')}."
        ),
    }]
    for item in evidence:
        if isinstance(item, (list, tuple)) and item:
            task = str(item[0])
            result = str(item[1]) if len(item) > 1 else ""
        else:
            task, result = str(item), ""
        lower = f"{task} {result}".lower()
        if "prometheus" in lower or "metric" in lower or "promql" in lower:
            stage = "Prometheus Query"
        elif "retrieve_knowledge" in lower or "runbook" in lower or ".md" in lower:
            stage = "RAG Retrieval"
        else:
            stage = "Evidence Analysis"
        failed = any(token in lower for token in ("执行失败", "error", "failed"))
        steps.append({
            "stage": stage,
            "status": "error" if failed else "success",
            "duration": None,
            "message": result.replace("\n", " ")[:300] or task[:300],
        })
    steps.append({
        "stage": "Root Cause Analysis",
        "status": "success",
        "duration": None,
        "message": str(report.get("root_cause") or "")[:300],
    })
    steps.append({
        "stage": "Diagnosis Report",
        "status": "success",
        "duration": None,
        "message": "The evidence-backed diagnosis report was generated and persisted.",
    })
    return steps


@router.get("/diagnosis/traces")
async def diagnosis_traces(limit: int = Query(default=50, ge=1, le=200)):
    reports = alert_history_service.list_diagnosis_reports(limit)
    return {"data": _json_safe(reports)}


@router.get("/diagnosis/traces/{report_id}")
async def diagnosis_trace_detail(report_id: int):
    report = alert_history_service.get_diagnosis_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Execution trace not found")
    return {
        "data": _json_safe({
            "id": report["id"],
            "session_id": report.get("session_id"),
            "alert_name": report.get("alert_name"),
            "service": report.get("service"),
            "status": "Completed",
            "created_at": report.get("created_at"),
            "steps": _trace_steps(report),
        })
    }


async def _current_metrics():
    queries = [
        ("CPU", "process_cpu_usage", "process_cpu_usage"),
        (
            "JVM", "heap_usage",
            'sum(jvm_memory_used_bytes{area="heap"}) / sum(jvm_memory_max_bytes{area="heap"})',
        ),
        (
            "MySQL", "slow_query_duration",
            "fault_mysql_slow_query_last_duration_milliseconds",
        ),
        (
            "HTTP", "latency",
            "rate(http_server_requests_seconds_sum[5m]) / "
            "rate(http_server_requests_seconds_count[5m])",
        ),
    ]
    output = []
    for category, name, promql in queries:
        body, error = await run_in_threadpool(query_prometheus_metrics_api, promql)
        formatted = _format_prometheus_metric_result(promql, body) if body else {}
        results = formatted.get("results", [])
        for item in results:
            try:
                numeric = float(item.get("value"))
            except (TypeError, ValueError):
                continue
            if category in {"CPU", "JVM"}:
                item["display_value"] = f"{numeric * 100:.2f}%"
                item["unit"] = "percent"
            elif category == "MySQL":
                item["display_value"] = f"{numeric:.2f} ms"
                item["unit"] = "milliseconds"
            elif category == "HTTP":
                item["display_value"] = f"{numeric * 1000:.2f} ms"
                item["unit"] = "milliseconds"
        output.append({
            "category": category,
            "name": name,
            "promql": promql,
            "results": results,
            "error": error,
        })
    return {"data": output}


@router.get("/observability/metrics")
async def observability_metrics():
    return await _current_metrics()


@router.get("/metrics/current")
async def current_metrics():
    return await _current_metrics()


@router.get("/services/health")
async def services_health():
    active = [
        alert for alert in alert_history_service.list_alerts(status="firing", limit=500)
        if str(alert.get("status", "")).lower() == "firing"
    ]
    definitions = ["SmartLife Service", "MySQL", "Redis", "JVM"]
    alert_services = {
        "smartlifeservicedown": "SmartLife Service",
        "mysqlunavailable": "MySQL",
        "smartlifemysqlslowqueryhigh": "MySQL",
        "redisunavailable": "Redis",
    }
    services = []
    for service_name in definitions:
        related = [
            alert for alert in active
            if alert_services.get(str(alert.get("alertname", "")).lower()) == service_name
            or (
                service_name == "JVM"
                and any(
                    token in str(alert.get("alertname", "")).lower()
                    for token in ("jvm", "heap", "memory")
                )
            )
        ]
        names = {str(alert.get("alertname", "")).lower() for alert in related}
        down_alert = {
            "SmartLife Service": "smartlifeservicedown",
            "MySQL": "mysqlunavailable",
            "Redis": "redisunavailable",
        }.get(service_name)
        status = "Down" if down_alert in names else "Degraded" if related else "Healthy"
        services.append({
            "service_name": service_name,
            "status": status,
            "active_alert_count": len(related),
        })
    return {"data": services}
