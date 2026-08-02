"""Executor node: execute steps in the Plan-Execute-Replan workflow."""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import ToolNode
from loguru import logger

from app.agent.aiops.state import PlanExecuteState
from app.agent.mcp_client import (
    format_exception_chain,
    get_mcp_client_with_retry,
    load_mcp_tools_safe,
)
from app.config import config
from app.core.fault_mapping_loader import match_fault_mapping
from app.core.llm_factory import llm_factory
from app.tools import (
    DEFAULT_LOCAL_AGENT_TOOLS,
    collect_jvm_thread_dump,
    query_prometheus_alerts,
    query_prometheus_metrics,
    query_prometheus_range,
    retrieve_knowledge,
)
from app.utils.timezone import now_shanghai

logger = logger.bind(stage="executor")

HIGH_CPU_PROMQLS = (
    "process_cpu_usage",
    "jvm_threads_live_threads",
    "rate(jvm_gc_pause_seconds_count[5m])",
    "rate(http_server_requests_seconds_count[5m])",
)

KNOWN_PROMQLS = (
    "process_cpu_usage",
    "system_cpu_usage",
    "jvm_memory_used_bytes",
    "jvm_memory_max_bytes",
    'sum(jvm_memory_used_bytes{job="smartlife",area="heap"})',
    'sum(jvm_memory_max_bytes{job="smartlife",area="heap"})',
    "fault_oom_injection_active",
    "fault_oom_retained_bytes",
    "fault_mysql_slow_query_active",
    "fault_mysql_slow_query_executions",
    "fault_mysql_slow_query_last_duration_milliseconds",
    'jvm_memory_used_bytes{area="heap"}',
    'jvm_memory_max_bytes{area="heap"}',
    "jvm_threads_live_threads",
    "rate(jvm_gc_pause_seconds_count[5m])",
    "rate(jvm_gc_pause_seconds_sum[5m])",
    "rate(http_server_requests_seconds_count[5m])",
    "histogram_quantile(0.95, sum(rate(http_server_requests_seconds_bucket[5m])) by (le))",
)


def _is_context_step(task: str) -> bool:
    return not _is_deterministic_tool_step(task) and not _is_report_step(task)


def _is_report_step(task: str) -> bool:
    if any(
        keyword in task
        for keyword in (
            "query_prometheus_metrics",
            "query_prometheus_range",
            "query_prometheus_alerts",
            "retrieve_knowledge",
            "get_current_time",
            "collect_jvm_thread_dump",
        )
    ):
        return False
    lowered = task.lower()
    return "markdown" in lowered or "report" in lowered or "final" in lowered


def _is_deterministic_tool_step(task: str) -> bool:
    return any(
        keyword in task
        for keyword in (
            "query_prometheus_metrics",
            "query_prometheus_range",
            "query_prometheus_alerts",
            "retrieve_knowledge",
            "get_current_time",
            "collect_jvm_thread_dump",
        )
    )


def _take_batch(plan: list[str]) -> tuple[list[str], list[str]]:
    """Take consecutive deterministic steps and leave report/replan work for later."""
    batch: list[str] = []
    index = 0
    while index < len(plan):
        task = plan[index]
        if _is_report_step(task):
            break
        if _is_context_step(task) or _is_deterministic_tool_step(task):
            batch.append(task)
            index += 1
            continue
    return batch, plan[index:]


def _extract_retrieve_query(task: str, default_query: str) -> str:
    match = re.search(r'query\s*=\s*("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')', task)
    if match:
        return _decode_quoted_parameter(match.group(1))
    return default_query


def _decode_quoted_parameter(token: str) -> str:
    """Decode a Planner parameter without truncating escaped PromQL quotes."""
    if token.startswith('"'):
        try:
            return str(json.loads(token))
        except json.JSONDecodeError:
            return token[1:-1].replace('\\"', '"').replace('\\\\', '\\')
    if token.startswith("'"):
        return token[1:-1].replace("\\'", "'").replace('\\"', '"').replace('\\\\', '\\')
    return token


def _extract_promqls(task: str, include_query_param: bool = False) -> list[str]:
    """Extract every PromQL expression explicitly mentioned in a planner step."""
    found: list[str] = []
    parameter_names = ["promql"]
    if include_query_param:
        parameter_names.append("query")

    # A planner step is human-readable text, not guaranteed to be valid JSON.
    # Capture a complete label selector by its balanced closing brace before
    # applying generic quoted-string parsing. This preserves inner matcher
    # quotes in forms such as query="up{job="smartlife"}".
    braced_pattern = r'(?i)\b([a-z_:][a-z0-9_:]*\{[^}\r\n]*\})'
    for match in re.finditer(braced_pattern, task):
        candidate = match.group(1).strip().replace('\\"', '"').replace("\\'", "'")
        if candidate and candidate not in found:
            found.append(candidate)

    quoted_pattern = rf'(?i)\b(?:{"|".join(parameter_names)})\s*=\s*("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')'
    for match in re.finditer(quoted_pattern, task):
        candidate = _decode_quoted_parameter(match.group(1)).strip().strip("` ?,?")
        if candidate and candidate not in found and not any(
            complete.startswith(candidate) for complete in found
        ):
            found.append(candidate)

    for pattern in [r"PromQL\s*[:?]\s*`([^`\n]+)`", r"PromQL\s*[:?]\s*([^?\n]+)"]:
        for match in re.finditer(pattern, task, flags=re.IGNORECASE):
            candidate = match.group(1).strip().strip("` ?,?")
            if candidate and candidate not in found:
                found.append(candidate)

    for candidate in KNOWN_PROMQLS:
        if (
            candidate in task
            and candidate not in found
            and not any(item.startswith(f"{candidate}{{") for item in found)
        ):
            found.append(candidate)

    # Do not execute an inner selector separately when it is part of an
    # explicitly quoted aggregate such as sum(metric{...}).
    return [
        candidate
        for candidate in found
        if not any(candidate != other and candidate in other for other in found)
    ]


def _extract_minutes(task: str, default_minutes: int = 10) -> int:
    match = re.search(r"(\d+)\s*(?:minutes|minute|mins|min|分钟)", task, flags=re.IGNORECASE)
    if not match:
        return default_minutes
    try:
        return max(1, min(120, int(match.group(1))))
    except ValueError:
        return default_minutes



def _extract_step(task: str, default_step: str = "30s") -> str:
    match = re.search(r'step\s*=\s*"([^"]+)"', task, flags=re.IGNORECASE)
    if not match:
        match = re.search(r"step\s*=\s*'([^']+)'", task, flags=re.IGNORECASE)
    if not match:
        return default_step
    value = match.group(1).strip()
    return value or default_step


def _extract_alert_window(input_text: str) -> tuple[str, str] | None:
    """Return startsAt-5m..now for an AlertManager-backed diagnosis."""
    match = re.search(r"(?im)^\s*-\s*start_time\s*:\s*([^\r\n]+)", input_text)
    if not match:
        return None
    raw_start = match.group(1).strip()
    try:
        alert_start = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
        if alert_start.tzinfo is None:
            # AlertContext stores business time without an offset. Treat that
            # normalized value as Asia/Shanghai rather than UTC.
            alert_start = alert_start.replace(tzinfo=timezone(timedelta(hours=8)))
    except ValueError:
        logger.warning("Unable to parse AlertManager startsAt for range query: {}", raw_start)
        return None
    return (
        (alert_start - timedelta(minutes=5)).isoformat(),
        now_shanghai().isoformat(),
    )


def _uses_incident_window(input_text: str) -> bool:
    mapping = match_fault_mapping(input_text)
    return bool(
        mapping
        and (
            mapping.get("report_policy") == "service_availability"
            or mapping.get("category") in {"service_availability", "dependency_availability"}
        )
    )


def _format_json(data: Any) -> str:
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False, indent=2)


def _parse_tool_payload(tool_name: str, item: Any) -> Any:
    if not isinstance(item, str):
        return item
    try:
        return json.loads(item)
    except json.JSONDecodeError:
        logger.warning("Tool returned non-JSON payload: tool={}, chars={}", tool_name, len(item))
        return {
            "success": False,
            "tool": tool_name,
            "tool_error": "tool returned non-JSON payload",
            "raw": item[:2000],
        }


def _instant_query_has_data(payload: Any) -> bool:
    """Return whether one instant-query payload contains at least one sample."""
    return isinstance(payload, dict) and payload.get("success") is not False and bool(payload.get("results"))


async def _timed_tool_call(tool_name: str, awaitable: Any) -> Any:
    start = time.perf_counter()
    logger.info("Tool call started: {}", tool_name)
    try:
        result = await awaitable
        elapsed = time.perf_counter() - start
        logger.info("Tool call finished: tool={}, elapsed={:.2f}s", tool_name, elapsed)
        return result
    except BaseException as exc:
        elapsed = time.perf_counter() - start
        logger.error("Tool call failed: tool={}, elapsed={:.2f}s\n{}", tool_name, elapsed, format_exception_chain(exc))
        return json.dumps(
            {
                "success": False,
                "tool": tool_name,
                "tool_error": format_exception_chain(exc),
            },
            ensure_ascii=False,
            indent=2,
        )


async def _execute_metric_queries(promqls: Iterable[str]) -> str:
    unique_promqls = []
    for promql in promqls:
        cleaned = str(promql).strip()
        if cleaned and cleaned not in unique_promqls:
            unique_promqls.append(cleaned)

    logger.info("Executor preparing query_prometheus_metrics calls: params={}", unique_promqls)
    tasks = [
        _timed_tool_call(
            f"query_prometheus_metrics[{promql}]",
            query_prometheus_metrics.ainvoke({"promql": promql}),
        )
        for promql in unique_promqls
    ]
    results = await asyncio.gather(*tasks)
    parsed_results = []
    empty_promqls: list[str] = []
    for promql, item in zip(unique_promqls, results):
        parsed = _parse_tool_payload("query_prometheus_metrics", item)
        parsed_results.append(parsed)
        if not _instant_query_has_data(parsed):
            empty_promqls.append(promql)

    historical_fallback: list[Any] = []
    if empty_promqls:
        logger.info(
            "Instant Prometheus query returned no data; running 10-minute range fallback: {}",
            empty_promqls,
        )
        range_tasks = [
            _timed_tool_call(
                f"query_prometheus_range_fallback[{promql}]",
                query_prometheus_range.ainvoke({"promql": promql, "minutes": 10, "step": "30s"}),
            )
            for promql in empty_promqls
        ]
        range_results = await asyncio.gather(*range_tasks)
        for item in range_results:
            parsed_range = _parse_tool_payload("query_prometheus_range", item)
            if isinstance(parsed_range, dict):
                parsed_range.setdefault("tool", "query_prometheus_range")
                parsed_range.setdefault("evidence_type", "historical_range_fallback")
            historical_fallback.append(parsed_range)
    payload = {
        "success": True,
        "tool": "query_prometheus_metrics",
        "mode": "parallel",
        "queries": unique_promqls,
        "evidence_type": "current_instant",
        "results": parsed_results,
        "historical_fallback": historical_fallback,
    }
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    logger.info("Executor collected query_prometheus_metrics result: chars={}, content={}", len(serialized), serialized[:8000])
    return serialized


async def _execute_range_queries(
    promqls: Iterable[str],
    minutes: int = 10,
    step: str = "30s",
    window: tuple[str, str] | None = None,
) -> str:
    unique_promqls = []
    for promql in promqls:
        cleaned = str(promql).strip()
        if cleaned and cleaned not in unique_promqls:
            unique_promqls.append(cleaned)

    range_args = {"minutes": minutes, "step": step}
    if window:
        range_args.update({"start": window[0], "end": window[1]})
    logger.info("Executor preparing query_prometheus_range calls: params={}, range_args={}", unique_promqls, range_args)
    tasks = [
        _timed_tool_call(
            f"query_prometheus_range[{promql}]",
            query_prometheus_range.ainvoke({"promql": promql, **range_args}),
        )
        for promql in unique_promqls
    ]
    results = await asyncio.gather(*tasks)
    parsed_results = []
    for item in results:
        parsed_results.append(_parse_tool_payload("query_prometheus_range", item))
    payload = {
        "success": True,
        "tool": "query_prometheus_range",
        "mode": "parallel",
        "minutes": minutes,
        "step": step,
        "start": window[0] if window else None,
        "end": window[1] if window else None,
        "queries": unique_promqls,
        "results": parsed_results,
    }
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    logger.info("Executor collected query_prometheus_range result: chars={}, content={}", len(serialized), serialized[:8000])
    return serialized


async def _execute_deterministic_task(task: str, input_text: str) -> tuple[str, str]:
    if _is_context_step(task):
        return task, "已读取当前 AlertManager 告警上下文，包括 alertname、severity、service、instance 和 startsAt 等字段。"

    if "query_prometheus_range" in task or "query_range" in task:
        promqls = _extract_promqls(task, include_query_param=True)
        if not promqls:
            promqls = ["process_cpu_usage"]
        incident_window = _extract_alert_window(input_text) if _uses_incident_window(input_text) else None
        result = await _execute_range_queries(
            promqls, _extract_minutes(task), _extract_step(task), incident_window
        )
        return task, _format_json(result)

    if "query_prometheus_metrics" in task:
        promqls = _extract_promqls(task)
        if not promqls and ("SmartLifeHighCPUUsage" in input_text or "SmartLifeHighCPUUsage" in task):
            promqls = list(HIGH_CPU_PROMQLS)
        if not promqls:
            promqls = ["process_cpu_usage"]
        result = await _execute_metric_queries(promqls)
        return task, _format_json(result)

    if "query_prometheus_alerts" in task:
        logger.info("Executor executing tool=query_prometheus_alerts params={}", {})
        result = await _timed_tool_call("query_prometheus_alerts", query_prometheus_alerts.ainvoke({}))
        return task, _format_json(result)

    if "retrieve_knowledge" in task:
        query = _extract_retrieve_query(task, input_text)
        logger.info("Executor executing tool=retrieve_knowledge params={}", {"query": query})
        result = await _timed_tool_call(
            "retrieve_knowledge",
            retrieve_knowledge.ainvoke({"query": query}),
        )
        return task, _format_json(result)

    if "collect_jvm_thread_dump" in task:
        logger.info("Executor executing tool=collect_jvm_thread_dump params={}", {})
        result = await _timed_tool_call(
            "collect_jvm_thread_dump",
            collect_jvm_thread_dump.ainvoke({}),
        )
        return task, _format_json(result)

    return await _execute_llm_task(task)


async def _execute_batch(batch: Iterable[str], input_text: str) -> list[tuple[str, str]]:
    batch_list = list(batch)
    if not batch_list:
        return []

    results: list[tuple[str, str]] = []
    context_tasks = [task for task in batch_list if _is_context_step(task)]
    tool_tasks = [task for task in batch_list if task not in context_tasks]

    for task in context_tasks:
        results.append(await _execute_deterministic_task(task, input_text))

    if tool_tasks:
        tool_results = await asyncio.gather(
            *[_execute_deterministic_task(task, input_text) for task in tool_tasks],
            return_exceptions=True,
        )
        for task, item in zip(tool_tasks, tool_results):
            if isinstance(item, BaseException):
                expanded_error = format_exception_chain(item)
                logger.error("Executor tool task failed but diagnosis continues: task={}\n{}", task, expanded_error)
                results.append(
                    (
                        task,
                        json.dumps(
                            {
                                "success": False,
                                "tool": "executor",
                                "tool_error": expanded_error,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                )
            else:
                results.append(item)

    return results


async def _execute_llm_task(task: str) -> tuple[str, str]:
    local_tools = list(DEFAULT_LOCAL_AGENT_TOOLS)
    logger.info(
        "Executor local tools: {}",
        ", ".join(getattr(tool, "name", str(tool)) for tool in local_tools),
    )

    mcp_client = await get_mcp_client_with_retry()
    mcp_tools, mcp_error = await load_mcp_tools_safe(mcp_client)
    if mcp_error:
        logger.debug("MCP unavailable; Executor uses local tools only")
        mcp_tools = []

    all_tools = local_tools + mcp_tools
    llm = llm_factory.create_chat_model(
        model=config.autodl_model or config.rag_model,
        temperature=0,
    )
    llm_with_tools = llm.bind_tools(all_tools)
    tool_node = ToolNode(all_tools)

    messages = [
        SystemMessage(
            content=(
                "You are the Executor in an AIOps diagnosis flow. Execute only the current step. "
                "Use available tools when needed. Do not invent metrics, logs, health status, or execution results."
            )
        ),
        HumanMessage(content=f"Execute this task: {task}"),
    ]

    llm_start = time.perf_counter()
    llm_response = await llm_with_tools.ainvoke(messages)
    logger.info("Executor LLM decision elapsed={:.2f}s", time.perf_counter() - llm_start)

    tool_calls = getattr(llm_response, "tool_calls", None) or []
    if tool_calls:
        logger.info("Executor detected {} tool calls", len(tool_calls))
        messages.append(llm_response)
        tool_start = time.perf_counter()
        tool_messages = await tool_node.ainvoke({"messages": messages})
        logger.info("Executor ToolNode elapsed={:.2f}s", time.perf_counter() - tool_start)
        returned_messages = tool_messages.get("messages", [])
        messages.extend(returned_messages)
        final_start = time.perf_counter()
        final_response = await llm_with_tools.ainvoke(messages)
        logger.info("Executor LLM summarize elapsed={:.2f}s", time.perf_counter() - final_start)
        result = final_response.content if hasattr(final_response, "content") else str(final_response)
    else:
        result = llm_response.content if hasattr(llm_response, "content") else str(llm_response)

    return task, result


async def executor(state: PlanExecuteState) -> Dict[str, Any]:
    """Execute deterministic steps in batches; fall back to LLM execution when needed."""
    node_start = time.perf_counter()
    logger.info("=== Executor: execute step batch ===")

    plan = state.get("plan", [])
    input_text = state.get("input", "")
    if not plan:
        logger.info("Executor skipped: empty plan")
        return {}

    batch, remaining_plan = _take_batch(plan)
    logger.info("Executor batch size={}, remaining_after_batch={}", len(batch), len(remaining_plan))
    for index, task in enumerate(batch, 1):
        logger.info("Executor batch task {}: {}", index, task)

    try:
        past_steps = await _execute_batch(batch, input_text)
        elapsed = time.perf_counter() - node_start
        logger.info("Executor batch finished: executed_steps={}, elapsed={:.2f}s", len(past_steps), elapsed)
        for index, (step, result) in enumerate(past_steps, 1):
            logger.info(
                "Executor writing LangGraph state past_steps[{}]: step={}, result_chars={}, result_preview={}",
                index,
                step,
                len(str(result)),
                str(result)[:3000],
            )
        return {
            "plan": remaining_plan,
            "past_steps": past_steps,
        }

    except BaseException as e:
        expanded_error = format_exception_chain(e)
        logger.error("Executor batch failed:\n{}", expanded_error, exc_info=True)
        failed_task = batch[0] if batch else plan[0]
        return {
            "plan": remaining_plan if batch else plan[1:],
            "past_steps": [(failed_task, f"执行失败: {expanded_error}")],
        }
