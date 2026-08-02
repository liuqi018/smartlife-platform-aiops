"""
Planner 节点：制定执行计划
基于 LangGraph 官方教程实现
"""

from textwrap import dedent
import json
import re
import time
from typing import Dict, Any, List
from langchain_core.prompts import ChatPromptTemplate
from loguru import logger

from app.config import config
from app.core.llm_factory import llm_factory
from app.core.fault_mapping_loader import get_fault_mapping
from app.tools import DEFAULT_LOCAL_AGENT_TOOLS, retrieve_knowledge
from app.agent.mcp_client import format_exception_chain, get_mcp_client_with_retry, load_mcp_tools_safe
from .state import Plan, PlanExecuteState
from .utils import format_tools_description

logger = logger.bind(stage="planner")

SUPPORTED_PLAN_STEPS = [
    "确认告警上下文：整理告警名称、级别、服务、实例、开始时间和主症状；本步骤不需要工具。",
    '使用 query_prometheus_metrics 工具查询服务可用性。参数：promql="up"。',
    "使用 query_prometheus_alerts 工具查询 Prometheus 当前活动告警，确认是否存在同服务、同实例或依赖相关的并发告警。参数：无。",
    '使用 retrieve_knowledge 工具根据告警名称和描述检索相关 Runbook 或历史经验。参数：query="smartlife Java Spring Boot 通用告警排查 Runbook"。',
    "基于告警上下文、Prometheus 指标证据、Prometheus 告警证据和 RAG 检索结果生成 Markdown 诊断报告；区分已获取证据、RAG知识建议和未获取信息。",
]


def _constrain_plan_steps(plan_steps: List[str]) -> List[str]:
    """Constrain planner output to current tool capabilities and 3-5 steps."""
    unsupported_keywords = (
        "Actuator",
        "健康检查接口",
        "日志查询",
        "应用日志",
        "指标曲线",
        "Redis指标",
        "Redis 指标",
        "MySQL指标",
        "MySQL 指标",
        "spring_health",
        "spring_log",
        "jvm_metric",
        "redis_metric",
        "mysql_metric",
    )
    allowed_keywords = (
        "get_current_time",
        "query_prometheus_alerts",
        "query_prometheus_metrics",
        "query_prometheus_range",
        "retrieve_knowledge",
        "collect_jvm_thread_dump",
        "PromQL",
        "指标",
        "告警",
        "根因",
        "报告",
    )

    constrained: list[str] = []
    for step in plan_steps:
        if any(keyword in step for keyword in unsupported_keywords):
            continue
        if any(keyword in step for keyword in allowed_keywords):
            constrained.append(step)
        if len(constrained) >= 5:
            break

    if len(constrained) < 3:
        return SUPPORTED_PLAN_STEPS

    if not any("报告" in step for step in constrained):
        constrained.append(SUPPORTED_PLAN_STEPS[-1])

    return constrained[:5]


DIAGNOSTIC_TEMPLATES: list[dict[str, Any]] = [
    {
        "name": "SmartLifeHighCPUUsage",
        "keywords": ("smartlifehighcpuusage", "cpu"),
        "metrics": (
            ("process_cpu_usage", "SmartLifeHighCPUUsage CPU"),
            ("jvm_threads_live_threads", "JVM live threads"),
            ("rate(jvm_gc_pause_seconds_count[5m])", "JVM GC pause rate"),
            ("rate(http_server_requests_seconds_count[5m])", "HTTP request rate"),
        ),
        "rag_query": "SmartLifeHighCPUUsage smartlife Java process CPU high JVM GC HTTP Runbook",
    },
    {
        "name": "SmartLifeServiceDown",
        "keywords": ("smartlifeservicedown", "service down", "服务下线", "服务不可用", "无法访问"),
        "metrics": (("up{job=\"smartlife\"}", "Prometheus target/service availability"),),
        "rag_query": "smartlife Java Spring Boot 服务启动失败 服务不可用 Runbook",
        "missing_evidence": "Spring Boot health 和服务日志当前未接入工具，记录为缺失证据。",
    },
    {
        "name": "SmartLifeJvmMemoryHighUsage",
        "keywords": ("smartlifejvmmemoryhighusage", "jvmoutofmemory", "outofmemory", "oom", "heap", "memory"),
        "metrics": (
            ('sum(jvm_memory_used_bytes{job="smartlife",area="heap"})', "JVM Heap memory used"),
            ('sum(jvm_memory_max_bytes{job="smartlife",area="heap"})', "JVM Heap memory max"),
            ("fault_oom_injection_active", "OOM fault injection state"),
            ("fault_oom_retained_bytes", "OOM fault retained bytes"),
            ("rate(jvm_gc_pause_seconds_count[5m])", "JVM GC pause rate"),
            ("rate(jvm_gc_pause_seconds_sum[5m])", "JVM GC pause duration rate"),
        ),
        "rag_query": "SmartLifeJvmMemoryHighUsage JVM OutOfMemory Java heap memory GC OOM Runbook",
    },
    {
        "name": "SmartLifeMysqlSlowQueryHigh",
        "keywords": ("smartlifemysqlslowqueryhigh",),
        "metrics": (
            ("fault_mysql_slow_query_active", "MySQL slow-query fault injection state"),
            ("fault_mysql_slow_query_executions", "MySQL slow-query executions"),
            (
                "fault_mysql_slow_query_last_duration_milliseconds",
                "MySQL last slow-query duration",
            ),
        ),
        "rag_query": (
            "MySQL慢SQL排查 slow query EXPLAIN index full table scan "
            "Performance Schema"
        ),
    },
]


def _extract_alert_name(input_text: str) -> str:
    match = re.search(r"(?im)^\s*-?\s*(?:alertname|告警名称)\s*:\s*([^\r\n]+)", input_text)
    return match.group(1).strip() if match else ""


def _configured_template(
    legacy_template: dict[str, Any],
    mapping: dict[str, Any] | None,
) -> dict[str, Any]:
    """Overlay safe declarative fields while retaining the legacy template."""
    if not mapping:
        return legacy_template
    template = dict(legacy_template)
    legacy_labels = {promql: label for promql, label in legacy_template.get("metrics", ())}
    configured_metrics = mapping.get("metrics") or []
    if configured_metrics:
        template["metrics"] = tuple(
            (promql, legacy_labels.get(promql, promql)) for promql in configured_metrics
        )
    for field in ("rag_query", "category", "report_policy"):
        if mapping.get(field):
            template[field] = mapping[field]
    if mapping.get("range_query"):
        template["range_query"] = tuple(mapping["range_query"])
    if mapping.get("missing_evidence"):
        template["missing_evidence"] = " ".join(mapping["missing_evidence"])
    template["runbook_allowlist"] = tuple(mapping.get("runbook_allowlist") or ())
    return template


def _template_from_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    """Build a Planner template for a fault registered only in YAML."""
    return {
        "name": mapping["alert_name"],
        "keywords": (mapping["alert_name"].lower(),),
        "metrics": tuple((promql, promql) for promql in mapping.get("metrics") or ()),
        "range_query": tuple(mapping.get("range_query") or ()),
        "rag_query": mapping.get("rag_query") or mapping["alert_name"],
        "category": mapping.get("category") or "",
        "report_policy": mapping.get("report_policy") or "",
        "missing_evidence": " ".join(mapping.get("missing_evidence") or ()),
        "runbook_allowlist": tuple(mapping.get("runbook_allowlist") or ()),
    }


def _select_diagnostic_template(input_text: str) -> dict[str, Any] | None:
    """Prefer exact alertname matching, then fall back to description keywords."""
    extracted_alert_name = _extract_alert_name(input_text)
    alert_name = extracted_alert_name.lower()
    for template in DIAGNOSTIC_TEMPLATES:
        if alert_name == template["name"].lower():
            return _configured_template(template, get_fault_mapping(template["name"]))

    mapping = get_fault_mapping(extracted_alert_name)
    if mapping:
        return _template_from_mapping(mapping)

    lower = input_text.lower()
    for template in DIAGNOSTIC_TEMPLATES:
        if any(keyword in lower for keyword in template["keywords"]):
            return _configured_template(template, get_fault_mapping(template["name"]))
    return None


def _alert_metric_plan(input_text: str) -> list[str]:
    """Return deterministic tool steps for the selected alert template."""
    template = _select_diagnostic_template(input_text)
    if not template:
        return [
            "确认未知告警的名称、描述、服务、实例和时间范围；本步骤不需要工具。",
            'Use query_prometheus_metrics to query general service availability from the alert description. promql="up".',
            "Use query_prometheus_alerts to get related active Prometheus alerts and target-state evidence.",
            'Use retrieve_knowledge to search a general Runbook based on the alert description. query="smartlife Java Spring Boot 通用故障排查 Runbook".',
            "Generate final Markdown diagnosis report and explicitly list unsupported health/log evidence.",
        ]

    steps: list[str] = []
    metrics = list(template.get("metrics") or [])
    if metrics:
        current_promqls = ", ".join(f"promql='{promql}'" for promql, _ in metrics)
        steps.append(
            f'Use query_prometheus_metrics to get current {template["name"]} metric evidence. {current_promqls}.'
        )
        if template["name"] == "SmartLifeHighCPUUsage":
            range_promql = (template.get("range_query") or (metrics[0][0],))[0]
            steps.append(
                f'Use query_prometheus_range to get last 10 minutes {template["name"]} trend. query="{range_promql}", minutes=10, step="30s".'
            )
        elif template["name"] == "SmartLifeJvmMemoryHighUsage":
            range_promql = (
                template.get("range_query")
                or ('sum(jvm_memory_used_bytes{job="smartlife",area="heap"})',)
            )[0]
            steps.append(
                'Use query_prometheus_range to analyze JVM Heap growth trend for the last 10 minutes. '
                f'query="{range_promql.replace(chr(34), chr(92) + chr(34))}", minutes=10, step="30s".'
            )
        elif template["name"] == "SmartLifeMysqlSlowQueryHigh":
            range_queries = template.get("range_query") or (
                "fault_mysql_slow_query_executions",
                "fault_mysql_slow_query_last_duration_milliseconds",
            )
            steps.append(
                "Use query_prometheus_range to analyze MySQL slow-query activity for the last 10 minutes. "
                + ", ".join(f"query='{query}'" for query in range_queries)
                + ", "
                "minutes=10, step='30s'."
            )

    is_service_availability = template.get("report_policy") == "service_availability"
    if template["name"] == "SmartLifeHighCPUUsage":
        steps.append(
            "Use collect_jvm_thread_dump to get JVM thread names, states, and stack traces for CPU hotspot, blocking, or deadlock diagnosis."
        )
    elif is_service_availability:
        service_range_query = (
            template.get("range_query") or ('up{job="smartlife"}',)
        )[0]
        steps.append(
            f'Use query_prometheus_range to query the {template["name"]} incident window from AlertManager startsAt minus 5 minutes to now. '
            f'query="{service_range_query.replace(chr(34), chr(92) + chr(34))}", step="30s".'
        )
        if template["name"] == "SmartLifeServiceDown":
            steps.append(
                "Use query_prometheus_alerts to verify Prometheus target/down alert state for job smartlife."
            )
        else:
            steps.append(
                f'Use query_prometheus_alerts to verify the current {template["name"]} availability alert state.'
            )
        if template.get("missing_evidence"):
            steps.append(str(template["missing_evidence"]))
    else:
        steps.append("Use query_prometheus_alerts to get current active Prometheus alert evidence.")
    steps.append(f'Use retrieve_knowledge to search matching Runbook. query="{template["rag_query"]}".')
    if template["name"] == "SmartLifeHighCPUUsage":
        steps.append(
            "Generate final Markdown diagnosis report from current CPU metrics, CPU history trend, JVM thread dump, and CPU troubleshooting knowledge."
        )
    elif template["name"] == "SmartLifeJvmMemoryHighUsage":
        steps.append(
            "Generate final Markdown JVM OOM risk diagnosis report from Heap usage ratio, OOM injection state, retained bytes, GC metrics, Heap trend, and Runbook evidence."
        )
    elif template["name"] == "SmartLifeMysqlSlowQueryHigh":
        steps.append(
            "Generate final Markdown MySQL slow-query diagnosis report from fault injection state, "
            "execution growth, last duration, active alerts, and MySQL slow SQL Runbook evidence."
        )
    else:
        steps.append("Generate final Markdown diagnosis report from alert context, metrics, trend, active alerts, RAG evidence, and missing evidence; distinguish incident-window evidence, current state, and recovery status.")
    return steps


def _ensure_metric_range_steps(input_text: str, plan_steps: List[str]) -> List[str]:
    """Ensure selected alert templates include deterministic evidence and RAG steps."""
    required_steps = _alert_metric_plan(input_text)
    if not required_steps:
        return plan_steps

    protected_promqls: list[str] = []
    for step in required_steps:
        promql_match = re.search(r'(?:promql|query)="([^"]+)"', step)
        if promql_match:
            protected_promqls.append(promql_match.group(1))

    output: list[str] = required_steps.copy()
    for step in plan_steps:
        step_lower = step.lower()
        is_metric_tool_step = (
            "query_prometheus_metrics" in step_lower
            or "query_prometheus_range" in step_lower
            or "query_prometheus_alerts" in step_lower
            or "retrieve_knowledge" in step_lower
        )
        is_protected_promql = any(promql in step for promql in protected_promqls)
        if is_metric_tool_step and (
            is_protected_promql
            or "query_prometheus_alerts" in step_lower
            or "retrieve_knowledge" in step_lower
        ):
            continue
        output.append(step)

    deduped: list[str] = []
    seen: set[str] = set()
    for step in output:
        key = step.strip()
        if key and key not in seen:
            deduped.append(step)
            seen.add(key)
    return deduped[:5]


def _raw_planner_text(raw_response: Any) -> str:
    """Return loggable text from an AIMessage or provider response."""
    content = getattr(raw_response, "content", raw_response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or item))
            else:
                parts.append(str(item))
        text = "\n".join(parts)
    else:
        text = str(content or "")
    if text:
        return text
    tool_calls = getattr(raw_response, "tool_calls", None)
    if tool_calls:
        return json.dumps(tool_calls, ensure_ascii=False, default=str)
    additional = getattr(raw_response, "additional_kwargs", None)
    if additional:
        return json.dumps(additional, ensure_ascii=False, default=str)
    return ""


def _extract_plan_json(raw_text: str) -> Plan | None:
    """Recover a Plan from plain, fenced, or surrounding-text JSON."""
    text = str(raw_text or "").strip()
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.IGNORECASE | re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    candidates.append(text)
    decoder = json.JSONDecoder()
    for candidate in candidates:
        starts = [0] if candidate.startswith("{") else []
        starts.extend(index for index, char in enumerate(candidate) if char == "{" and index not in starts)
        for start in starts:
            try:
                value, _end = decoder.raw_decode(candidate[start:])
                return Plan.model_validate(value)
            except (json.JSONDecodeError, ValueError, TypeError):
                continue
    return None


def _safe_default_plan(input_text: str) -> list[str]:
    """Keep the alert-specific deterministic route when LLM planning fails."""
    deterministic = _alert_metric_plan(input_text)
    return deterministic or list(SUPPORTED_PLAN_STEPS)


def _planner_response_metadata(raw_response: Any) -> tuple[str, int | None]:
    response_metadata = getattr(raw_response, "response_metadata", None) or {}
    additional = getattr(raw_response, "additional_kwargs", None) or {}
    finish_reason = str(
        response_metadata.get("finish_reason")
        or additional.get("finish_reason")
        or "unknown"
    )
    usage = (
        getattr(raw_response, "usage_metadata", None)
        or response_metadata.get("token_usage")
        or response_metadata.get("usage")
        or {}
    )
    completion_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    try:
        completion_tokens = int(completion_tokens) if completion_tokens is not None else None
    except (TypeError, ValueError):
        completion_tokens = None
    return finish_reason, completion_tokens


def _log_planner_raw_response(model_name: str, phase: str, raw_response: Any) -> str:
    raw_text = _raw_planner_text(raw_response)
    finish_reason, completion_tokens = _planner_response_metadata(raw_response)
    logger.info(
        "Planner LLM response model_name={} phase={} finish_reason={} completion_tokens={} raw_chars={} raw_response={}",
        model_name,
        phase,
        finish_reason,
        completion_tokens,
        len(raw_text),
        raw_text[:8000] or "<empty>",
    )
    return raw_text


# Planner prompt
planner_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            dedent("""
                你是 AIOps 告警诊断 Planner。你的任务是基于当前已经注册的工具，生成 3-5 个可执行诊断步骤。

                可用工具列表：
                {tools_description}

                Additional available tool: query_prometheus_range can query Prometheus /api/v1/query_range.
                collect_jvm_thread_dump: 用于获取 Java 应用 JVM 线程状态、线程名称和调用栈信息，可用于 CPU高、线程阻塞、死锁等问题诊断。
                For SmartLifeHighCPUUsage, prefer querying process_cpu_usage trend over the last 10 minutes when trend evidence is useful.

                规划规则：
                - 只能生成当前工具支持的步骤：get_current_time、query_prometheus_alerts、query_prometheus_metrics、query_prometheus_range、retrieve_knowledge、collect_jvm_thread_dump。
                - 必须优先依据 alertname 选择诊断策略，不能把 SmartLifeHighCPUUsage 的指标套用到其他告警。
                - 如果 alertname=SmartLifeHighCPUUsage，第一优先步骤必须使用 query_prometheus_metrics 查询 process_cpu_usage。
                - SmartLifeHighCPUUsage 的必要工具参数：promql="process_cpu_usage"。
                - 如果 alertname=SmartLifeJvmMemoryHighUsage，必须查询 sum(jvm_memory_used_bytes{{job="smartlife",area="heap"}})、sum(jvm_memory_max_bytes{{job="smartlife",area="heap"}})、fault_oom_injection_active、fault_oom_retained_bytes 以及 jvm_gc 指标；禁止查询单个 G1 pool。
                - SmartLifeJvmMemoryHighUsage 必须使用 query_prometheus_range 查询 sum(jvm_memory_used_bytes{{job="smartlife",area="heap"}}) 的最近 10 分钟趋势。
                - SmartLifeJvmMemoryHighUsage 必须使用 retrieve_knowledge 查询 JVM OOM / Heap / GC Runbook，不要生成 collect_jvm_thread_dump 步骤。
                - 如果 alertname=SmartLifeMysqlSlowQueryHigh，必须查询 fault_mysql_slow_query_active、fault_mysql_slow_query_executions、fault_mysql_slow_query_last_duration_milliseconds 及后两项最近10分钟趋势，并检索 MySQL 慢 SQL / EXPLAIN / Performance Schema Runbook；不要生成 collect_jvm_thread_dump 步骤。
                - 如果 alertname=SmartLifeServiceDown，必须查询 up{{job="smartlife"}} 和 Prometheus 活跃告警；Spring Boot health 与服务日志未接入时必须标记为缺失证据。
                - 如需补充证据，才考虑 system_cpu_usage、jvm_threads_live_threads、rate(jvm_gc_pause_seconds_count[5m])、rate(http_server_requests_seconds_count[5m])。
                - query_prometheus_alerts 只用于查询当前活动告警，不用于获取指标值。
                - 不要生成 Spring Boot Actuator 健康检查、日志查询、Loki/ELK、Redis/MySQL 专项指标工具步骤。
                - 如果某类信息当前没有工具支持，只能在最终报告中标注“未获取信息”，不要作为执行步骤。
                - 总步骤控制在 3-5 步。
                - 输出必须严格符合 Plan JSON Schema：一个且仅一个 JSON 对象，格式为
                  {{"steps":["步骤1","步骤2","步骤3"]}}；不得输出 Markdown 代码围栏、前言或后记。

                未知告警应根据告警描述选择通用的服务可用性、相关活动告警和 Runbook 流程。

                经验上下文：
                {experience_context}
            """).strip(),
        ),
        ("placeholder", "{messages}"),
    ]
)


async def planner(state: PlanExecuteState) -> Dict[str, Any]:
    """
    规划节点：根据用户输入生成执行计划

    流程：
    1. 先查询内部文档，获取相关经验和最佳实践
    2. 基于经验文档和可用工具制定执行计划
    """
    planner_start = time.perf_counter()
    logger.info("=== Planner：制定执行计划 ===")

    input_text = state.get("input", "")
    logger.info(f"用户输入: {input_text}")

    try:
        # 规划阶段不预取 RAG，避免与执行计划中的 retrieve_knowledge 重复调用。
        logger.info("Planner skips pre-RAG lookup; RAG will run in Executor step.")
        experience_docs = ""

        # 步骤2: 获取可用工具列表
        # 获取本地工具
        local_tools = list(DEFAULT_LOCAL_AGENT_TOOLS)

        # 获取 MCP 工具
        mcp_client = await get_mcp_client_with_retry()
        mcp_tools, mcp_error = await load_mcp_tools_safe(mcp_client)
        if mcp_error:
            logger.debug("MCP unavailable; Planner uses local tools only")
            mcp_tools = []

        # 合并所有工具
        all_tools = local_tools + mcp_tools
        logger.info(f"可用工具数量: 本地 {len(local_tools)} + MCP {len(mcp_tools)}")

        # 格式化工具描述
        tools_description = format_tools_description(all_tools)

        # 步骤3: 格式化经验文档上下文
        if experience_docs:
            experience_context = dedent(f"""
                ## 相关经验文档

                以下是从知识库中检索到的相关经验和最佳实践，请参考这些经验制定执行计划：

                {experience_docs}

                ---
            """).strip()
        else:
            experience_context = ""

        # 步骤4: 创建 LLM 并生成计划
        model_name = config.autodl_model or config.rag_model
        llm = llm_factory.create_chat_model(
            model=model_name,
            temperature=0,
            streaming=False,
            max_tokens=config.aiops_planner_max_tokens,
            timeout=config.aiops_planner_timeout,
            max_retries=1,
        )

        planner_input = {
            "messages": [("user", input_text)],
            "tools_description": tools_description,
            "experience_context": experience_context
        }

        plan_result: Plan | dict[str, Any] | None = None
        raw_text = ""
        structured_chain = planner_prompt | llm.with_structured_output(
            Plan, include_raw=True
        )
        for attempt in range(1, max(1, config.aiops_planner_max_attempts) + 1):
            try:
                plan_envelope = await structured_chain.ainvoke(planner_input)
                raw_response = (
                    plan_envelope.get("raw")
                    if isinstance(plan_envelope, dict)
                    else plan_envelope
                )
                raw_text = _log_planner_raw_response(
                    str(model_name), f"structured_{attempt}", raw_response
                )
                if isinstance(plan_envelope, dict):
                    plan_result = plan_envelope.get("parsed")
                    parsing_error = plan_envelope.get("parsing_error")
                    if parsing_error:
                        logger.warning(
                            "Planner structured output parsing failed model_name={} attempt={}/{}; retrying before raw mode:\n{}",
                            model_name,
                            attempt,
                            config.aiops_planner_max_attempts,
                            format_exception_chain(parsing_error),
                        )
                elif isinstance(plan_envelope, (Plan, dict)):
                    plan_result = plan_envelope
                if plan_result is not None:
                    break
                if raw_text:
                    plan_result = _extract_plan_json(raw_text)
                    if plan_result is not None:
                        logger.warning("Planner recovered Plan from structured raw content")
                        break
            except Exception as exc:
                logger.warning(
                    "Planner structured output invocation failed model_name={} attempt={}/{}:\n{}",
                    model_name,
                    attempt,
                    config.aiops_planner_max_attempts,
                    format_exception_chain(exc),
                )

        # Recover JSON from the final structured raw content before raw mode.
        if plan_result is None and raw_text:
            plan_result = _extract_plan_json(raw_text)
            if plan_result is not None:
                logger.warning("Planner recovered Plan from structured raw content")

        # Some OpenAI-compatible providers reject/omit the structured envelope
        # itself. Invoke the same model without response_format to obtain content.
        if plan_result is None:
            try:
                raw_response = await (planner_prompt | llm).ainvoke(planner_input)
                raw_text = _log_planner_raw_response(
                    str(model_name), "raw_retry", raw_response
                )
                plan_result = _extract_plan_json(raw_text)
                if plan_result is not None:
                    logger.warning("Planner recovered Plan from raw retry content")
            except Exception as exc:
                logger.error(
                    "Planner raw content retry failed model_name={}; alert-specific fallback will be used:\n{}",
                    model_name,
                    format_exception_chain(exc),
                    exc_info=True,
                )

        # Layer 4: deterministic alert-specific plan. This path must never
        # terminate the workflow because of provider JSON behavior.
        if plan_result is None:
            logger.warning(
                "[Planner] all parsing layers failed model_name={}; alert-specific fallback activated",
                model_name,
            )
            plan_steps = _safe_default_plan(input_text)
        elif isinstance(plan_result, Plan):
            plan_steps = plan_result.steps
        elif isinstance(plan_result, dict):
            try:
                plan_steps = Plan.model_validate(plan_result).steps
            except (ValueError, TypeError) as exc:
                logger.error(
                    "Planner parsed payload failed Plan validation; alert-specific fallback activated:\n{}",
                    format_exception_chain(exc),
                )
                plan_steps = _safe_default_plan(input_text)
        else:
            logger.error(
                "Planner returned unsupported parsed type {}; alert-specific fallback activated",
                type(plan_result).__name__,
            )
            plan_steps = _safe_default_plan(input_text)

        raw_plan_count = len(plan_steps)
        plan_steps = _constrain_plan_steps(plan_steps)
        plan_steps = _ensure_metric_range_steps(input_text, plan_steps)
        if len(plan_steps) != raw_plan_count:
            logger.info("Planner 已压缩诊断计划步骤: {} -> {}", raw_plan_count, len(plan_steps))

        logger.info("Planner elapsed={:.2f}s", time.perf_counter() - planner_start)
        logger.info("[Planner] plan generated successfully")
        logger.info(f"计划已生成，共 {len(plan_steps)} 个步骤")
        for i, step in enumerate(plan_steps, 1):
            logger.info(f"  步骤{i}: {step}")

        return {"plan": plan_steps}

    except Exception as e:
        logger.info("Planner failed elapsed={:.2f}s", time.perf_counter() - planner_start)
        logger.error("生成计划失败，展开异常链:\n{}", format_exception_chain(e), exc_info=True)
        # Preserve the configured alert route even when the provider call itself fails.
        logger.warning("[Planner] alert-specific fallback plan activated")
        return {"plan": _safe_default_plan(input_text)}
