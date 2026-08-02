"""Prometheus 告警查询工具

通过 Prometheus HTTP API `GET /api/v1/alerts` 拉取当前规则产生的告警列表
（含 pending / firing 等状态）。每条告警由「完整 labels」唯一标识，与 Prometheus
文档一致；不得仅用 `alertname` 去重，否则多实例同名规则会被错误合并。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from langchain_core.tools import tool
from loguru import logger

from app.config import config

# Prometheus Alerts API 相对 base URL 的路径（与 Query API 的 /api/v1/query 并列）
ALERTS_API_PATH = "/api/v1/alerts"
QUERY_API_PATH = "/api/v1/query"
QUERY_RANGE_API_PATH = "/api/v1/query_range"

# 常见 label：在简化输出中带出，便于扫一眼定位服务/实例/级别（不存在则省略）
COMMON_LABEL_KEYS = ("alertname", "severity", "instance", "job", "namespace", "pod")


def normalize_instance(instance: str | None) -> str:
    """Normalize equivalent local/Docker host instance labels for evidence correlation."""
    if not instance:
        return ""
    value = str(instance).strip()
    replacements = {
        "localhost": "host.docker.internal",
        "127.0.0.1": "host.docker.internal",
    }
    if ":" in value:
        host, port = value.rsplit(":", 1)
        return f"{replacements.get(host, host)}:{port}"
    return replacements.get(value, value)


def _infer_metric_info(metric_name: str, promql: str, value: Any) -> dict[str, Any]:
    """Infer unit and display value without applying invalid percentage conversions."""
    name = metric_name or promql
    query = promql or name
    lower = f"{name} {query}".lower()

    unit = "raw"
    description = "Prometheus metric value"
    display_value = value

    try:
        number = float(value)
    except (TypeError, ValueError):
        number = None

    if "probe_success" in lower:
        unit = "boolean"
        description = "Blackbox dependency health probe result"
        if number is not None:
            display_value = "1 (available)" if number >= 1 else "0 (unavailable)"
    elif "cpu" in lower and "usage" in lower:
        unit = "percent"
        description = "CPU usage ratio reported by Micrometer/Prometheus"
        display_value = f"{number * 100:.4f}%" if number is not None else value
    elif "http_server_requests" in lower and ("rate(" in lower or "_count" in lower):
        unit = "requests/s"
        description = "HTTP request throughput over the PromQL rate window"
        display_value = f"{number:.4f} requests/s" if number is not None else value
    elif "jvm_threads" in lower:
        unit = "threads"
        description = "JVM live thread count"
        display_value = f"{number:.0f} threads" if number is not None else value
    elif "gc" in lower and ("rate(" in lower or "_count" in lower):
        unit = "events/s"
        description = "JVM GC event rate over the PromQL rate window"
        display_value = f"{number:.4f} events/s" if number is not None else value
    elif "fault_mysql_slow_query_active" in lower:
        unit = "state"
        description = "MySQL slow-query fault injection state"
        if number is not None:
            display_value = "active" if number >= 1 else "inactive"
    elif "fault_mysql_slow_query_executions" in lower:
        unit = "executions"
        description = "Cumulative MySQL slow-query fault executions"
        display_value = f"{number:.0f} executions" if number is not None else value
    elif "fault_mysql_slow_query_last_duration_milliseconds" in lower:
        unit = "ms"
        description = "Duration of the latest injected MySQL slow query"
        display_value = f"{number:.3f} ms" if number is not None else value
    elif "memory" in lower or "bytes" in lower:
        unit = "bytes"
        description = "Memory size in bytes"
        if number is not None:
            if number >= 1024**3:
                display_value = f"{number / 1024**3:.3f} GB"
            elif number >= 1024**2:
                display_value = f"{number / 1024**2:.3f} MB"
            else:
                display_value = f"{number:.0f} bytes"

    return {
        "unit": unit,
        "description": description,
        "display_value": display_value,
    }


def _parse_active_at(active_at_str: str) -> datetime | None:
    """将 Prometheus 返回的 activeAt（RFC3339 或带 Z 后缀）解析为 UTC 时间。"""
    if not active_at_str:
        return None
    try:
        s = active_at_str.replace("Z", "+00:00", 1)
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _labels_identity(labels: dict[str, Any]) -> str:
    """告警唯一键：完整 labels 的 JSON（键排序），用于去重或合并重复项。"""
    return json.dumps(labels, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def calculate_duration(active_at_str: str) -> str:
    """根据 activeAt 计算相对当前 UTC 的已持续时长（人类可读短文本）。"""
    active_at = _parse_active_at(active_at_str)
    if active_at is None:
        return "unknown"
    now = datetime.now(timezone.utc)
    delta = now - active_at
    total_seconds = max(0, int(delta.total_seconds()))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours > 0:
        return f"{hours}h{minutes}m{seconds}s"
    if minutes > 0:
        return f"{minutes}m{seconds}s"
    return f"{seconds}s"


def query_prometheus_alerts_api() -> tuple[dict[str, Any], str | None]:
    """请求 `GET {prometheus_base_url}/api/v1/alerts`。

    返回 (JSON 体, 错误信息)。成功时第二项为 None；HTTP 或 JSON 解析失败时第一项为空 dict。
    """
    base_url = config.prometheus_base_url.rstrip("/")
    api_url = f"{base_url}{ALERTS_API_PATH}"
    logger.info("Querying Prometheus alerts: {}", api_url)
    try:
        with httpx.Client(timeout=config.prometheus_request_timeout) as client:
            resp = client.get(api_url)
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPError as e:
        return {}, f"failed to query Prometheus alerts: {e}"
    except json.JSONDecodeError as e:
        return {}, f"failed to parse response: {e}"
    return body, None


def query_prometheus_metrics_api(promql: str, time: str | None = None) -> tuple[dict[str, Any], str | None]:
    """请求 `GET {prometheus_base_url}/api/v1/query` 执行 PromQL 即时查询。"""
    base_url = config.prometheus_base_url.rstrip("/")
    api_url = f"{base_url}{QUERY_API_PATH}"
    params: dict[str, str] = {"query": promql}
    if time:
        params["time"] = time

    logger.info("Querying Prometheus metrics: url={}, query={}", api_url, promql)
    try:
        with httpx.Client(timeout=config.prometheus_request_timeout) as client:
            resp = client.get(api_url, params=params)
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPError as e:
        return {}, f"failed to query Prometheus metrics: {e}"
    except json.JSONDecodeError as e:
        return {}, f"failed to parse response: {e}"
    return body, None


def query_prometheus_range_api(
    promql: str,
    start: str,
    end: str,
    step: str = "30s",
) -> tuple[dict[str, Any], str | None]:
    """Request Prometheus GET /api/v1/query_range for a time-window trend."""
    base_url = config.prometheus_base_url.rstrip("/")
    api_url = f"{base_url}{QUERY_RANGE_API_PATH}"
    params: dict[str, str] = {"query": promql, "start": start, "end": end, "step": step}
    logger.info("Querying Prometheus range metrics: url={}, params={}", api_url, params)
    try:
        with httpx.Client(timeout=config.prometheus_request_timeout) as client:
            resp = client.get(api_url, params=params)
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPError as e:
        return {}, f"failed to query Prometheus range metrics: {e}"
    except json.JSONDecodeError as e:
        return {}, f"failed to parse response: {e}"
    return body, None


def _prometheus_time(value: str | None) -> str:
    """Return an RFC3339 timestamp accepted by Prometheus."""
    if value:
        return value
    return datetime.now(timezone.utc).isoformat()


def _default_range_window(minutes: int = 10) -> tuple[str, str]:
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(minutes=minutes)
    return start_dt.isoformat(), end_dt.isoformat()


def _format_metric_item(promql: str, metric_name: str, labels: dict[str, Any], timestamp: Any, value: Any, result_type: str) -> dict[str, Any]:
    metric_info = _infer_metric_info(metric_name, promql, value)
    normalized_labels = dict(labels)
    if "instance" in normalized_labels:
        normalized_labels["normalized_instance"] = normalize_instance(normalized_labels.get("instance"))
    return {
        "metric_name": metric_name,
        "metric": metric_name,
        "promql": promql,
        "result_type": result_type,
        "value": value,
        "display_value": metric_info["display_value"],
        "labels": labels,
        "normalized_labels": normalized_labels,
        "timestamp": timestamp,
        "unit": metric_info["unit"],
        "description": metric_info["description"],
    }


def _format_prometheus_metric_result(promql: str, result: dict[str, Any]) -> dict[str, Any]:
    """将 Prometheus query API 返回值归一化为 Agent 易读的结构化证据。"""
    data = result.get("data") or {}
    result_type = data.get("resultType", "")
    raw_results = data.get("result") or []
    normalized_results: list[dict[str, Any]] = []

    if result_type == "scalar" and isinstance(raw_results, list) and len(raw_results) >= 2:
        timestamp, value = raw_results[0], raw_results[1]
        normalized_results.append(
            _format_metric_item(promql, promql, {}, timestamp, value, result_type)
        )
    elif isinstance(raw_results, list):
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            metric_labels = item.get("metric") or {}
            if not isinstance(metric_labels, dict):
                metric_labels = {}
            value_pair = item.get("value") or []
            timestamp = value_pair[0] if isinstance(value_pair, list) and len(value_pair) >= 1 else None
            value = value_pair[1] if isinstance(value_pair, list) and len(value_pair) >= 2 else None
            metric_name = metric_labels.get("__name__") or promql
            normalized_results.append(
                _format_metric_item(
                    promql,
                    metric_name,
                    {k: v for k, v in metric_labels.items() if k != "__name__"},
                    timestamp,
                    value,
                    result_type,
                )
            )

    return {
        "success": True,
        "query": promql,
        "promql": promql,
        "result_type": result_type,
        "count": len(normalized_results),
        "results": normalized_results,
    }


def _pick_common_labels(labels: dict[str, Any]) -> dict[str, Any]:
    """从 labels 中提取常用维度，减少 Agent 阅读整表 labels 的成本。"""
    out: dict[str, Any] = {}
    for k in COMMON_LABEL_KEYS:
        if k == "alertname":
            continue
        v = labels.get(k)
        if v is not None and v != "":
            out[k] = v
    return out


def _simplify_alerts(result: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """将 Prometheus `data.alerts` 转为简化列表，并按 activeAt 从新到旧排序。

    返回 (simplified_alerts, state_counts)。
    """
    data = result.get("data") or {}
    alerts = data.get("alerts") or []
    if not isinstance(alerts, list):
        return [], {}

    simplified: list[dict[str, Any]] = []
    # 若上游偶发重复推送完全相同 labels 的条目，只保留一条（按 labels 身份去重）
    seen_identity: set[str] = set()
    state_counts: dict[str, int] = {}

    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        labels = alert.get("labels") or {}
        annotations = alert.get("annotations") or {}
        if not isinstance(labels, dict):
            labels = {}
        if not isinstance(annotations, dict):
            annotations = {}

        identity = _labels_identity(labels)
        if identity in seen_identity:
            continue
        seen_identity.add(identity)

        state = str(alert.get("state", "") or "")
        state_counts[state] = state_counts.get(state, 0) + 1

        active_at = str(alert.get("activeAt", "") or "")
        alert_name = str(labels.get("alertname", "") or "")

        simplified.append(
            {
                "alert_name": alert_name,
                "labels": labels,
                "common_labels": _pick_common_labels(labels),
                "description": str(annotations.get("description", "") or ""),
                "summary": str(annotations.get("summary", "") or ""),
                "state": state,
                "active_at": active_at,
                "duration": calculate_duration(active_at),
            }
        )

    # 「最新」：按 activeAt 降序；无法解析的时间排在最后，便于人工扫列表
    def sort_key(item: dict[str, Any]) -> tuple[int, float]:
        dt = _parse_active_at(str(item.get("active_at", "")))
        if dt is None:
            return (1, 0.0)
        # (0, -timestamp) 保证新在前；用负的 timestamp 避免 tuple 比较问题
        return (0, -dt.timestamp())

    simplified.sort(key=sort_key)
    return simplified, state_counts


def _format_prometheus_range_result(promql: str, result: dict[str, Any]) -> dict[str, Any]:
    """Normalize Prometheus query_range matrix results while preserving labels per series."""
    data = result.get("data") or {}
    result_type = data.get("resultType", "")
    raw_results = data.get("result") or []
    normalized_results: list[dict[str, Any]] = []

    if isinstance(raw_results, list):
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            metric_labels = item.get("metric") or {}
            if not isinstance(metric_labels, dict):
                metric_labels = {}
            labels = {k: v for k, v in metric_labels.items() if k != "__name__"}
            metric_name = metric_labels.get("__name__") or promql
            values = item.get("values") or []
            normalized_values: list[dict[str, Any]] = []
            numeric_values: list[float] = []
            for pair in values:
                if not isinstance(pair, list) or len(pair) < 2:
                    continue
                timestamp, raw_value = pair[0], pair[1]
                metric_info = _infer_metric_info(metric_name, promql, raw_value)
                normalized_values.append(
                    {
                        "timestamp": timestamp,
                        "value": raw_value,
                        "display_value": metric_info["display_value"],
                    }
                )
                try:
                    numeric_values.append(float(raw_value))
                except (TypeError, ValueError):
                    pass

            summary: dict[str, Any] = {"points": len(normalized_values)}
            if numeric_values:
                first_value = numeric_values[0]
                last_value = numeric_values[-1]
                max_value = max(numeric_values)
                min_value = min(numeric_values)
                metric_info = _infer_metric_info(metric_name, promql, last_value)
                summary.update(
                    {
                        "first_value": first_value,
                        "last_value": last_value,
                        "min_value": min_value,
                        "max_value": max_value,
                        "last_display_value": metric_info["display_value"],
                        "max_display_value": _infer_metric_info(metric_name, promql, max_value)["display_value"],
                        "min_display_value": _infer_metric_info(metric_name, promql, min_value)["display_value"],
                        "unit": metric_info["unit"],
                        "description": metric_info["description"],
                    }
                )

            normalized_labels = dict(labels)
            if "instance" in normalized_labels:
                normalized_labels["normalized_instance"] = normalize_instance(normalized_labels.get("instance"))

            metric_info = _infer_metric_info(metric_name, promql, normalized_values[-1]["value"] if normalized_values else None)
            normalized_results.append(
                {
                    "metric_name": metric_name,
                    "metric": metric_name,
                    "promql": promql,
                    "result_type": result_type,
                    "labels": labels,
                    "normalized_labels": normalized_labels,
                    "values": normalized_values,
                    "summary": summary,
                    "unit": metric_info["unit"],
                    "description": metric_info["description"],
                }
            )

    return {
        "success": True,
        "query": promql,
        "promql": promql,
        "result_type": result_type,
        "count": len(normalized_results),
        "results": normalized_results,
    }


@tool
def query_prometheus_alerts() -> str:
    """查询 Prometheus 服务端当前活动告警（HTTP GET /api/v1/alerts）。

    适用场景：用户关心「有没有告警」「哪些规则在 firing/pending」「最近触发了什么告警」
    「排查监控告警」「和 Prometheus 告警规则相关的现状」等运维/可观测性问题；无需用户
    提供参数，直接调用即可拉取服务端已聚合的告警列表。

    行为说明：向配置项 `prometheus_base_url` 指向的 Prometheus 拉取告警；结果按激活时间
    从新到旧排序；每条包含 alert 名称、labels、常见维度摘要、描述/摘要注解、状态与
    持续时长等。返回 JSON 字符串，含 success、alerts、state_counts 等字段。

    注意：这是 Prometheus 内置告警 API，不是执行 PromQL 指标查询，也不是 Alertmanager
    的通知/静默接口；若需查指标曲线请用 MCP 或其它指标工具。

    Returns:
        str: JSON 字符串。成功时含告警列表与状态统计；失败时含 success=false 与 error。
    """
    result, err = query_prometheus_alerts_api()
    if err:
        out = {
            "success": False,
            "error": err,
            "message": "Failed to query Prometheus alerts",
        }
        return json.dumps(out, ensure_ascii=False, indent=2)

    if result.get("status") != "success":
        err_msg = result.get("error") or result.get("errorType") or "Prometheus returned non-success status"
        out = {
            "success": False,
            "error": str(err_msg),
            "message": "Failed to query Prometheus alerts",
        }
        return json.dumps(out, ensure_ascii=False, indent=2)

    simplified, state_counts = _simplify_alerts(result)
    out = {
        "success": True,
        "alerts": simplified,
        "state_counts": state_counts,
        "total": len(simplified),
        "message": f"已获取 {len(simplified)} 条告警（按 activeAt 从新到旧），状态分布: {state_counts}",
    }
    logger.info("Prometheus alerts query completed: {} alerts, states={}", len(simplified), state_counts)
    return json.dumps(out, ensure_ascii=False, indent=2)


@tool
def query_prometheus_metrics(promql: str, time: str | None = None) -> str:
    """执行 Prometheus PromQL 即时指标查询（HTTP GET /api/v1/query）。

    适用场景：需要获取真实监控指标证据，例如 CPU 使用率、JVM 线程数、GC 次数/耗时、
    HTTP 请求量、响应时间、错误率等。调用方必须传入具体 PromQL 查询语句。

    常用示例：
    - process_cpu_usage
    - system_cpu_usage
    - jvm_memory_used_bytes
    - jvm_memory_max_bytes
    - jvm_memory_used_bytes{area="heap"}
    - jvm_memory_max_bytes{area="heap"}
    - sum(jvm_memory_used_bytes{job="smartlife",area="heap"})
    - sum(jvm_memory_max_bytes{job="smartlife",area="heap"})
    - fault_oom_injection_active
    - fault_oom_retained_bytes
    - fault_mysql_slow_query_active
    - fault_mysql_slow_query_executions
    - fault_mysql_slow_query_last_duration_milliseconds
    - jvm_threads_live_threads
    - rate(jvm_gc_pause_seconds_count[5m])
    - rate(jvm_gc_pause_seconds_sum[5m])
    - rate(http_server_requests_seconds_count[5m])
    - histogram_quantile(0.95, sum(rate(http_server_requests_seconds_bucket[5m])) by (le))

    Args:
        promql: PromQL 查询语句。
        time: 可选查询时间，RFC3339 或 Unix timestamp；不传则查询 Prometheus 当前时间点。

    Returns:
        str: JSON 字符串，包含 query、result_type、count、results；每个 result 含 metric、
        labels、timestamp、value。失败时 success=false 并返回 error。
    """
    cleaned_promql = (promql or "").strip()
    if not cleaned_promql:
        return json.dumps(
            {
                "success": False,
                "error": "promql is required",
                "message": "PromQL query must not be empty",
            },
            ensure_ascii=False,
            indent=2,
        )

    result, err = query_prometheus_metrics_api(cleaned_promql, time)
    if err:
        out = {
            "success": False,
            "query": cleaned_promql,
            "error": err,
            "message": "Failed to query Prometheus metrics",
        }
        return json.dumps(out, ensure_ascii=False, indent=2)

    if result.get("status") != "success":
        err_msg = result.get("error") or result.get("errorType") or "Prometheus returned non-success status"
        out = {
            "success": False,
            "query": cleaned_promql,
            "error": str(err_msg),
            "message": "Failed to query Prometheus metrics",
        }
        return json.dumps(out, ensure_ascii=False, indent=2)

    out = _format_prometheus_metric_result(cleaned_promql, result)
    logger.info(
        "Prometheus metrics query completed: query={}, result_type={}, count={}",
        cleaned_promql,
        out.get("result_type"),
        out.get("count"),
    )
    logger.info(
        "query_prometheus_metrics returned result: query={}, content={}",
        cleaned_promql,
        json.dumps(out, ensure_ascii=False)[:4000],
    )
    return json.dumps(out, ensure_ascii=False, indent=2)

@tool
def query_prometheus_range(
    promql: str,
    start: str | None = None,
    end: str | None = None,
    step: str = "30s",
    minutes: int = 10,
) -> str:
    """Execute Prometheus query_range for trend evidence around an alert window.

    Args:
        promql: PromQL query.
        start: Optional RFC3339/Unix start time. Defaults to now-minutes.
        end: Optional RFC3339/Unix end time. Defaults to now.
        step: Query resolution, for example 30s or 1m.
        minutes: Default lookback window when start/end are omitted.
    """
    cleaned_promql = (promql or "").strip()
    if not cleaned_promql:
        return json.dumps({"success": False, "error": "promql is required"}, ensure_ascii=False, indent=2)

    if not start or not end:
        start, end = _default_range_window(minutes)
    start = _prometheus_time(start)
    end = _prometheus_time(end)

    result, err = query_prometheus_range_api(cleaned_promql, start, end, step)
    if err:
        return json.dumps(
            {
                "success": False,
                "query": cleaned_promql,
                "promql": cleaned_promql,
                "start": start,
                "end": end,
                "step": step,
                "error": err,
                "message": "Failed to query Prometheus range metrics",
            },
            ensure_ascii=False,
            indent=2,
        )

    if result.get("status") != "success":
        err_msg = result.get("error") or result.get("errorType") or "Prometheus returned non-success status"
        return json.dumps(
            {
                "success": False,
                "query": cleaned_promql,
                "promql": cleaned_promql,
                "start": start,
                "end": end,
                "step": step,
                "error": str(err_msg),
                "message": "Failed to query Prometheus range metrics",
            },
            ensure_ascii=False,
            indent=2,
        )

    out = _format_prometheus_range_result(cleaned_promql, result)
    out.update({"start": start, "end": end, "step": step})
    logger.info(
        "Prometheus range query completed: query={}, start={}, end={}, step={}, result_type={}, count={}",
        cleaned_promql,
        start,
        end,
        step,
        out.get("result_type"),
        out.get("count"),
    )
    logger.info("query_prometheus_range returned result: query={}, content={}", cleaned_promql, json.dumps(out, ensure_ascii=False)[:4000])
    return json.dumps(out, ensure_ascii=False, indent=2)
