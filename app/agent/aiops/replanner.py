"""
Replanner 节点：重新规划或生成最终响应
基于 LangGraph 官方教程实现
"""

from textwrap import dedent
import asyncio
import json
import re
import time
from typing import Any, Dict, List
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from loguru import logger

from app.config import config
from app.core.llm_factory import llm_factory
from app.core.fault_mapping_loader import match_fault_mapping
from app.tools import DEFAULT_LOCAL_AGENT_TOOLS
from app.agent.mcp_client import format_exception_chain, get_mcp_client_with_retry, load_mcp_tools_safe
from .state import PlanExecuteState
from .utils import format_tools_description

logger = logger.bind(stage="report")


class Response(BaseModel):
    """最终响应的格式"""
    response: str = Field(description="对用户的最终响应")


class Act(BaseModel):
    """重新规划的输出格式"""
    action: str = Field(
        description="""下一步的行动，必须是以下三种之一：
        - 'continue': 当前计划合理，继续执行下一个步骤
        - 'replan': 当前计划需要调整，提供新的步骤列表
        - 'respond': 计划已完成且信息充足，生成最终响应"""
    )
    # action 为 'replan' 时，新的步骤列表（会替换当前剩余计划）
    new_steps: List[str] = Field(
        default_factory=list,
        description="新的步骤列表（如果 action 是 'replan'，这些步骤会替换剩余计划）"
    )


# Replanner 提示词
replanner_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            dedent("""
                作为一个重新规划专家，你需要根据已执行的步骤决定下一步行动。

                可用工具列表（用于制定计划时参考）：

                {tools_description}

                注意：你的职责是制定或调整计划，实际的工具调用由 Executor 负责执行。

                你有三个选择（按优先级排序）：

                **1. 'respond' - 信息充足，立即生成最终响应** 【最高优先级】
                   - 使用场景：当前信息已经足够回答用户问题
                   - 决策标准：
                     * 已执行步骤 >= 3 且获取了关键信息
                     * 或者已执行步骤 >= 4（无论结果如何）
                     * 或者当前信息完全满足任务需求
                   - ⚠️ 不要等到"完美"才响应，"足够好"就应该立即 respond

                **2. 'continue' - 当前计划合理，继续执行** 【次优先级】
                   - 使用场景：剩余计划合理且必要
                   - 决策标准：剩余步骤确实能提供关键信息
                   - ⚠️ 如果剩余步骤不是"必需"的，应选择 respond

                **3. 'replan' - 当前计划有严重问题** 【最低优先级，谨慎使用】
                   - 使用场景：原计划明显错误或遗漏关键步骤
                   - ⚠️ **严格限制**：
                     * 新步骤数量必须 <= 当前剩余步骤数
                     * 优先简化计划，不要添加不必要的步骤
                     * 总步骤数已执行 >= 4 次时，禁止 replan，只能 respond

                评估标准：
                - 当前信息是否已经足够解决用户问题？【最关键】
                - 已执行步骤是否成功获取了核心信息？
                - 剩余步骤是否真的"必需"？
                - 已执行步骤数是否过多（>= 4）？如果是，立即 respond

                **决策优先级口诀：** 
                "优先结束 > 保持不变 > 调整计划"
                "信息足够就响应，不要追求完美"
            """).strip(),
        ),
        ("placeholder", "{messages}"),
    ]
)

# 最终响应生成提示词
response_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            dedent("""
                你是一名资深 SRE 和 AIOps 故障诊断专家。

                你的任务不是简单总结告警，而是基于已有证据完成故障分析。

                输出要求：

                1. 必须严格使用已有证据。
                2. 禁止编造不存在的指标、日志、线程、异常信息。
                3. 对根因只能给出“证据支持的判断”，不要输出无依据结论。
                4. 如果证据不足，需要明确说明缺失信息。
                5. 将Prometheus指标、AlertManager状态、RAG Runbook作为诊断依据。

                分析原则：

                - 指标异常 ≠ 根因，需要结合多个证据判断。
                - 当前恢复状态和历史异常状态需要区分。
                - 对可能原因按照可信度排序。
                - 优先输出可以验证的下一步操作。

                输出中文 Markdown 格式。
            """).strip(),
        ),
        ("placeholder", "{messages}"),
    ]
)


def _is_report_only_plan(plan: list[str]) -> bool:
    return bool(plan) and all(("报告" in step or "Markdown" in step) for step in plan)


def _has_required_report_evidence(past_steps: list[tuple]) -> bool:
    evidence_text = "\n".join(f"{step}\n{result}" for step, result in past_steps)
    has_monitoring = (
        "query_prometheus_metrics" in evidence_text
        or "query_prometheus_range" in evidence_text
        or "query_prometheus_alerts" in evidence_text
    )
    return has_monitoring and _has_rag_evidence(past_steps)


def _has_rag_evidence(past_steps: list[tuple]) -> bool:
    evidence_text = "\n".join(f"{step}\n{result}" for step, result in past_steps)
    return "retrieve_knowledge" in evidence_text or "Runbook" in evidence_text or "source" in evidence_text.lower()


def _rag_query_for_input(input_text: str) -> str:
    mapping = match_fault_mapping(input_text)
    if mapping and mapping.get("rag_query"):
        return str(mapping["rag_query"])
    lower = input_text.lower()
    if any(keyword in lower for keyword in ("jvmoutofmemory", "outofmemory", "oom", "heap", "memory")):
        return "JVM OutOfMemory Java heap memory GC OOM Runbook"
    if any(keyword in lower for keyword in ("mysqlslowquery", "mysql", "slow query", "slowquery")):
        return (
            "MySQL慢SQL排查 slow query EXPLAIN index full table scan "
            "Performance Schema"
        )
    return "SmartLifeHighCPUUsage Java process CPU high JVM GC HTTP Runbook"


async def replanner(state: PlanExecuteState) -> Dict[str, Any]:
    """
    重新规划节点：决定是继续、调整计划还是生成最终响应

    三种决策：
    1. continue - 继续执行当前计划
    2. replan - 调整计划（替换剩余步骤）
    3. respond - 生成最终响应
    """
    replan_start = time.perf_counter()
    logger.info("=== Replanner：重新规划 ===")

    input_text = state.get("input", "")
    plan = state.get("plan", [])
    past_steps = state.get("past_steps", [])

    logger.info(f"剩余计划步骤: {len(plan)}")
    logger.info(f"已执行步骤: {len(past_steps)}")

    if _is_report_only_plan(plan) and not _has_rag_evidence(past_steps):
        logger.info("Replanner inserts missing retrieve_knowledge before final report")
        return {
            "plan": [
                f'Use retrieve_knowledge to search matching Runbook. query="{_rag_query_for_input(input_text)}".',
                *plan,
            ]
        }

    if not plan or (_is_report_only_plan(plan) and _has_required_report_evidence(past_steps)):
        logger.info(
            "Replanner fast path: generate report directly, remaining_steps={}, elapsed_before_report={:.2f}s",
            len(plan),
            time.perf_counter() - replan_start,
        )
        llm = llm_factory.create_chat_model(
            model=config.autodl_model or config.rag_model,
            temperature=0
        )
        return await _generate_response(state)

    # ⚠️ 强制限制：如果已执行步骤过多，直接生成响应
    MAX_STEPS = 5
    if len(past_steps) >= MAX_STEPS:
        logger.warning(f"已执行 {len(past_steps)} 个步骤，超过最大限制 {MAX_STEPS}，强制生成最终响应")
        llm = llm_factory.create_chat_model(
            model=config.autodl_model or config.rag_model,
            temperature=0
        )
        return await _generate_response(state)

    # 获取可用工具列表
    try:
        # 获取本地工具
        local_tools = list(DEFAULT_LOCAL_AGENT_TOOLS)

        # 获取 MCP 工具
        mcp_client = await get_mcp_client_with_retry()
        mcp_tools, mcp_error = await load_mcp_tools_safe(mcp_client)
        if mcp_error:
            logger.debug("MCP unavailable; Replanner uses local tools only")
            mcp_tools = []

        # 合并所有工具
        all_tools = local_tools + mcp_tools
        logger.info(f"可用工具数量: 本地 {len(local_tools)} + MCP {len(mcp_tools)}")

        # 格式化工具描述
        tools_description = format_tools_description(all_tools)
    except BaseException as e:
        logger.warning("获取工具列表失败，展开异常链:\n{}", format_exception_chain(e))
        tools_description = "无法获取工具列表"

    # 创建 LLM
    llm = llm_factory.create_chat_model(
        model=config.autodl_model or config.rag_model,
        temperature=0
    )

    # 格式化已执行的步骤
    steps_summary = "\n".join([
        f"步骤: {step}\n结果: {result[:300]}..."
        for step, result in past_steps
    ])

    # 如果还有剩余计划，进行决策
    if plan:
        logger.info("还有剩余计划，评估下一步行动")

        replanner_chain = replanner_prompt | llm.with_structured_output(Act)

        try:
            messages = [
                ("user", f"原始任务: {input_text}"),
                ("user", f"已执行的步骤:\n{steps_summary}"),
                ("user", f"剩余计划: {', '.join(plan)}"),
                ("user", f"⚠️ 重要提示：已执行 {len(past_steps)} 个步骤，请优先考虑是否信息已足够生成响应（respond）")
            ]

            act = await replanner_chain.ainvoke({
                "messages": messages,
                "tools_description": tools_description
            })
            logger.info("Replanner LLM elapsed={:.2f}s", time.perf_counter() - replan_start)

            # 处理返回结果
            if isinstance(act, Act):
                action = act.action
                new_steps = act.new_steps
            else:
                # 如果返回的是字典
                action = act.get("action", "continue")  # type: ignore
                new_steps = act.get("new_steps", [])  # type: ignore

            logger.info(f"Replanner 决策: {action}")

            if action == "respond":
                logger.info("决定生成最终响应")
                return await _generate_response(state)

            elif action == "replan":
                # ⚠️ 强制限制：新步骤数不能超过当前剩余步骤数
                if len(new_steps) > len(plan):
                    logger.warning(
                        f"新步骤数 {len(new_steps)} > 剩余步骤数 {len(plan)}，"
                        f"强制截断为 {len(plan)} 个步骤"
                    )
                    new_steps = new_steps[:len(plan)]
                
                # ⚠️ 二次检查：如果已执行步骤 >= 4，禁止 replan
                if len(past_steps) >= 4:
                    logger.warning(f"已执行 {len(past_steps)} 个步骤，禁止重新规划，强制生成响应")
                    return await _generate_response(state)
                
                logger.info(f"决定调整计划，新步骤数量: {len(new_steps)}")
                if new_steps:
                    # 替换剩余计划
                    return {"plan": new_steps}
                else:
                    logger.warning("replan 但未提供新步骤，继续执行原计划")
                    return {}

            else:  # action == "continue"
                logger.info("决定继续执行当前计划")
                return {}  # 不修改状态，继续执行

        except BaseException as e:
            logger.error("重新规划失败，展开异常链:\n{}", format_exception_chain(e))
            return {}

    else:
        # 没有剩余计划，生成最终响应
        logger.info("计划已执行完毕，生成最终响应")
        return await _generate_response(state)


def _truncate_text(text: str, max_chars: int) -> str:
    """Trim long context while preserving the beginning and the tail."""
    if len(text) <= max_chars:
        return text
    head_size = max_chars // 2
    tail_size = max_chars - head_size
    return text[:head_size] + "\n...[truncated]...\n" + text[-tail_size:]


def _estimate_tokens(text: str) -> int:
    """Approximate token count for logging when tokenizer is unavailable."""
    return max(1, len(text) // 4)


def _format_report_steps(past_steps: list, max_result_chars: int = 900) -> str:
    """Format compact evidence for final report generation."""
    if not past_steps:
        return "No executed steps."

    formatted = []
    for index, (step, result) in enumerate(past_steps, 1):
        compact_step = _truncate_text(str(step), 350)
        compact_result = _truncate_text(str(result), max_result_chars)
        formatted.append(
            f"### Step {index}\n"
            f"**Action:** {compact_step}\n\n"
            f"**Evidence:**\n{compact_result}"
        )
    return "\n\n".join(formatted)


def _format_report_step_summary(past_steps: list) -> str:
    """Format only execution status to avoid duplicating large tool payloads in report prompts."""
    if not past_steps:
        return "No executed steps."

    lines: list[str] = []
    for index, (step, result) in enumerate(past_steps, 1):
        result_text = str(result)
        status = "success"
        if '"success": false' in result_text.lower() or "error" in result_text.lower() or "失败" in result_text:
            status = "check_required"
        lines.append(f"{index}. {status}: {_truncate_text(str(step), 220)}")
    return "\n".join(lines)


def _extract_alert_summary(input_text: str) -> dict[str, str]:
    """Extract normalized alert fields from the task text without depending on an LLM."""
    import re

    fields = {
        "alertname": "unknown",
        "severity": "unknown",
        "service": "unknown",
        "instance": "unknown",
        "start_time": "unknown",
    }
    for name in fields:
        match = re.search(rf"(?im)^\s*-?\s*{name}\s*:\s*(.+?)\s*$", input_text)
        if match:
            fields[name] = match.group(1).strip()
    return fields


def _extract_tool_name(step: Any, result: Any) -> str:
    """Best-effort tool name extraction for deterministic fallback reports."""
    import re

    parsed = _safe_json_loads(result)
    if isinstance(parsed, dict) and parsed.get("tool"):
        return str(parsed["tool"])

    text = f"{step}\n{result}"
    known_tools = (
        "query_prometheus_alerts",
        "query_prometheus_metrics",
        "query_prometheus_range",
        "retrieve_knowledge",
        "get_current_time",
    )
    for tool_name in known_tools:
        if tool_name in text:
            return tool_name
    match = re.search(r"\b([a-z][a-z0-9_]{2,})\s*\(", str(step), re.IGNORECASE)
    return match.group(1) if match else _truncate_text(str(step), 80)


def _format_fallback_tool_results(past_steps: list[tuple], max_result_chars: int = 500) -> str:
    """List every executed tool with an evidence-preserving result summary."""
    if not past_steps:
        return "- 未记录已执行工具。"

    lines: list[str] = []
    for index, (step, result) in enumerate(past_steps, 1):
        tool_name = _extract_tool_name(step, result)
        result_text = _truncate_text(str(result).strip() or "无返回内容", max_result_chars)
        lines.append(f"{index}. **{tool_name}**\n   - 步骤：{_truncate_text(str(step), 180)}\n   - 结果摘要：{result_text}")
    return "\n".join(lines)


def _build_fallback_report(
    input_text: str,
    past_steps: list[tuple],
    failure_reason: str,
    prometheus_metric_evidence: str,
    trend_analysis: str,
    rag_evidence: str,
    missing_information: str,
) -> str:
    """Build a generic report directly from state when report LLM generation fails."""
    alert = _extract_alert_summary(input_text)
    evidence_summary = _truncate_text(prometheus_metric_evidence, 950)
    trend_summary = _truncate_text(trend_analysis, 450)
    missing_summary = _truncate_text(missing_information, 450)
    return f"""# 故障摘要
- **告警名称**：{alert['alertname']}
- **服务**：{alert['service']}
- **当前状态**：Report LLM 生成失败，已基于现有证据生成降级报告

# 关键证据
{evidence_summary}

历史趋势：{trend_summary}

# 根因分析
- **已确认原因**：现有证据不足以确认唯一根因。
- **可能原因**：需结合上方指标趋势及 Runbook 建议继续验证。
- **排除原因**：无充分证据可排除具体原因。
- **缺失证据**：{missing_summary}

# 处理建议
- 优先核对当前实时指标与最近 10 分钟历史趋势是否一致。
- 对服务下线类告警，继续检查 Spring Boot health、启动日志及进程状态；工具未接入时由值班人员手工确认。
- 按已命中的 Runbook 执行验证步骤，并补齐缺失证据后再确认根因。
- 当前信息不足时，按证据限制继续补充验证。
"""


def _compact_text(text: str, max_chars: int) -> str:
    """Compress evidence at a safe boundary without adding truncation markers."""
    import re

    non_table_lines: list[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and all(set(cell) <= {"-", ":"} for cell in cells):
                continue
            if cells:
                non_table_lines.append("；".join(cells))
            continue
        non_table_lines.append(line)
    normalized = re.sub(r"\s+", " ", " ".join(non_table_lines)).strip()
    if len(normalized) <= max_chars:
        return normalized
    candidate = normalized[:max_chars]
    boundary = max(candidate.rfind("。"), candidate.rfind("；"), candidate.rfind("，"), candidate.rfind(" "))
    if boundary >= max_chars // 2:
        candidate = candidate[:boundary]
    return candidate.rstrip("，；、:： ")


def _is_ignored_jvm_thread(name: str, frames: list[Any] | None = None) -> bool:
    """Filter non-business threads only from report presentation."""
    normalized = str(name or "").strip().lower()
    exact_names = {
        "reference handler",
        "finalizer",
        "signal dispatcher",
        "attach listener",
        "notification thread",
        "destroyjavavm",
    }
    infrastructure_name = (
        normalized in exact_names
        or normalized.startswith("jdwp")
        or normalized.startswith("rmi ")
        or "jndi-dns" in normalized
        or "dns-address-change" in normalized
        or normalized.startswith("catalina-utility")
        or "containerbackgroundprocessor" in normalized
        or "container-lifecycle" in normalized
    )
    if infrastructure_name:
        return True

    stack_text = "\n".join(str(frame) for frame in (frames or [])).lower()
    business_stack_keywords = (
        "controller",
        "service",
        "repository",
        "com.smartlife",
        "com.example",
        ".app.",
        "/app/",
        "consumecpu",
    )
    return frames is not None and not any(
        keyword in stack_text for keyword in business_stack_keywords
    )


def _fixed_evidence_limitations() -> str:
    """Evidence limitations that must always remain complete in the final report."""
    return "\n".join([
        "- 当前未接入应用日志查询能力，无法结合异常日志进一步定位。",
        "- 当前未接入完整 Spring Boot Actuator 数据，无法准确评估业务接口影响。",
        "- 缺少线程 CPU 时间占比数据，无法量化单个线程 CPU 贡献。",
    ])


def _report_context(input_text: str) -> dict[str, str]:
    mapping = match_fault_mapping(input_text) or {}
    return {
        "alert_name": str(mapping.get("alert_name") or _extract_alert_summary(input_text)["alertname"]),
        "category": str(mapping.get("category") or ""),
        "report_policy": str(mapping.get("report_policy") or "legacy"),
    }


def _is_availability_context(input_text: str) -> bool:
    context = _report_context(input_text)
    return context["report_policy"] == "service_availability" or context["category"] in {
        "service_availability",
        "dependency_availability",
    }


def _availability_evidence_limitations(input_text: str) -> str:
    context = _report_context(input_text)
    if context["category"] == "dependency_availability":
        component = "Redis" if context["alert_name"] == "RedisUnavailable" else "MySQL"
        alert_summary = _extract_alert_summary(input_text)
        return "\n".join([
            f"- 当前未接入{component}组件日志，无法确认实例停止、认证失败或资源耗尽中的具体原因。",
            f"- 当前未执行{component}实例直连检查，端口、认证和网络状态仍需按 Runbook 验证。",
            "- 上述限制仅影响具体原因定位，不否定 probe_success=0 已确认的健康检查失败。",
        ])
    return "\n".join([
        "- 当前未接入应用启动日志，无法确认进程退出或启动失败的具体异常。",
        "- 当前未执行服务端口直连检查，监听端口和网络可达性仍需按 Runbook 验证。",
        "- 上述限制仅影响具体原因定位，不否定 up=0 已确认的服务不可抓取状态。",
    ])


def _mysql_slow_query_evidence_limitations() -> str:
    return "\n".join([
        "- 当前未获取具体SQL，无法定位具体业务查询语句。",
        "- 当前未获取慢查询日志，无法核对SQL参数、扫描行数和锁等待详情。",
        "- 当前未获取EXPLAIN执行计划，无法确认全表扫描、filesort或具体索引缺失位置。",
        "- 上述限制仅影响SQL和代码位置的进一步定位，不否定已确认的慢查询异常。",
    ])


def _ensure_fixed_evidence_limitations(report: str, input_text: str = "") -> str:
    """Replace the evidence-limit section atomically so no sentence is cut."""
    import re

    heading = "### 证据限制"
    if "smartlifemysqlslowqueryhigh" in input_text.lower():
        limitations = _mysql_slow_query_evidence_limitations()
    elif _is_availability_context(input_text):
        limitations = _availability_evidence_limitations(input_text)
    else:
        limitations = _fixed_evidence_limitations()
    replacement = f"{heading}\n\n{limitations}\n\n"
    if heading not in report:
        return report
    return re.sub(
        r"(?ms)^### 证据限制\s*.*?(?=^##\s|\Z)",
        replacement,
        report,
        count=1,
    ).rstrip()


def _build_bounded_fallback_report(
    *,
    input_text: str,
    past_steps: list[tuple],
    evidence_chain: str,
    thread_dump_evidence: str,
    missing_information: str,
    rag_evidence: str,
    max_chars: int,
) -> str:
    """Rebuild a complete compact report; never slice an assembled Markdown document."""
    alert = _extract_alert_summary(input_text)
    tools = [_extract_tool_name(step, result) for step, result in past_steps]
    has_thread_evidence = (
        bool(thread_dump_evidence)
        and "No successful JVM thread evidence" not in thread_dump_evidence
        and "未发现明确业务热点线程" not in thread_dump_evidence
    )
    thread_fact = _compact_text(thread_dump_evidence, 260) if has_thread_evidence else ""
    lower_thread_fact = thread_fact.lower()
    input_lower = input_text.lower()
    oom = _analyze_jvm_oom_evidence(past_steps)
    mysql_slow = _analyze_mysql_slow_query_evidence(past_steps)
    availability = _analyze_availability_evidence(input_text, past_steps)
    if (
        "cpu持续超过阈值" in evidence_chain.lower()
        or "cpu异常仍存在" in evidence_chain.lower()
    ):
        current_status = "当前异常持续中"
    elif "cpu当前未超过高使用率阈值" in evidence_chain.lower():
        current_status = "指标已恢复，等待 AlertManager 生命周期确认"
    elif "status: resolved" in input_lower or "status：resolved" in input_lower:
        current_status = "指标已恢复，AlertManager 生命周期已确认"
    elif has_thread_evidence and "runnable" in lower_thread_fact:
        current_status = "异常持续中"
    else:
        current_status = "尚未收到恢复证据，等待 AlertManager 状态确认"
    metric_recovered = current_status.startswith("指标已恢复")
    if _is_availability_context(input_text) and availability["abnormal"]:
        current_status = "当前异常持续中"
        alert_name = availability["alert_name"]
        if availability["category"] == "dependency_availability":
            component = "Redis" if alert_name == "RedisUnavailable" else "MySQL"
            conclusion = (
                f"Prometheus 检测到 {availability['promql'] or 'probe_success'}=0，"
                f"已确认 {component} 健康检查失败，smartlife 当前无法正常访问{component}依赖。"
            )
            if component == "Redis":
                probable_cause = "Redis实例停止、Redis端口不可访问、Redis认证配置错误或网络连接异常。"
                immediate_actions = (
                    "- 检查 Redis 实例进程与运行状态，并验证服务端口可达性。\n"
                    "- 核对 smartlife 的 Redis 地址、端口、密码和网络策略。"
                )
                long_term_actions = "- 完善 Redis 进程、端口、认证失败和连接异常监控，并按 Runbook 建立恢复演练。"
            else:
                probable_cause = "MySQL实例停止、MySQL连接配置错误、数据库端口不可访问或连接资源耗尽。"
                immediate_actions = (
                    "- 检查 MySQL 实例进程、端口和连接资源使用情况。\n"
                    "- 核对 smartlife 的数据库地址、端口、账号、密码和网络策略。"
                )
                long_term_actions = "- 完善 MySQL 进程、端口、连接数和认证失败监控，并按 Runbook 建立恢复演练。"
            confirmed_fact = (
                f"- Prometheus 返回 `{availability['promql'] or 'probe_success'}=0`。\n"
                f"- 已确认 {component} 健康检查失败，smartlife 无法正常访问{component}依赖。"
            )
        else:
            conclusion = (
                f"Prometheus 检测到 {availability['promql'] or 'up'}=0，"
                "已确认 SmartLife 服务当前不可抓取，服务存活或端口可达性异常。"
            )
            probable_cause = "应用进程停止、服务启动失败、监听端口未开放或服务网络连接异常。"
            confidence = "中"
            immediate_actions = (
                "- 检查 SmartLife 进程和启动状态，并验证服务监听端口。\n"
                "- 查看应用启动日志，核对配置、端口占用和依赖初始化结果。"
            )
            long_term_actions = "- 完善进程、端口和启动失败监控，并按服务不可用 Runbook 建立自动恢复与演练。"
            confirmed_fact = (
                f"- Prometheus 返回 `{availability['promql'] or 'up'}=0`。\n"
                "- 已确认 SmartLife 服务当前不可抓取；具体是进程停止还是端口异常仍需验证。"
            )
        confidence = "中"
    elif mysql_slow["strong_slow_query_cause"] and "smartlifemysqlslowqueryhigh" in input_lower:
        current_status = "当前异常持续中"
        execution_fact = (
            "执行次数持续增长"
            if mysql_slow["executions_growing"]
            else (
                f"累计执行次数为 {mysql_slow['executions']:.0f}"
                if mysql_slow["executions"] is not None
                else "执行次数趋势尚未取得"
            )
        )
        conclusion = (
            f"检测到慢查询故障注入处于开启状态、{execution_fact}，且最近一次查询耗时"
            f"为 {mysql_slow['duration_ms']:.3f} ms。高耗时SQL持续执行正在导致MySQL查询性能下降。"
        )
        probable_cause = "检测到持续慢查询故障注入，高耗时SQL持续执行导致MySQL查询性能下降。"
        confidence = "高"
        immediate_actions = (
            "- 停止 MySQL 慢查询故障注入，确认 executions 不再增长且查询耗时回落。\n"
            "- 获取具体 SQL 和慢查询日志，检查当前受影响请求。"
        )
        long_term_actions = (
            "- 使用 Performance Schema 和 EXPLAIN 分析全表扫描、filesort 与索引缺失。\n"
            "- 优化索引和 SQL，并完善慢查询次数、耗时趋势告警。"
        )
        confirmed_fact = _format_mysql_slow_query_evidence(past_steps)
    elif oom["strong_retention_cause"] and "smartlifejvmmemoryhighusage" in input_lower:
        current_status = "当前异常持续中"
        retention_fact = (
            "且 retained bytes 持续增长"
            if oom["retained_growing"]
            else f"且 retained bytes 已达到 {_format_bytes(oom['retained'])}"
        )
        conclusion = (
            f"JVM Heap 使用率为 {oom['usage']:.1f}%，已超过 90%；OOM 注入处于开启状态，"
            f"{retention_fact}。当前异常大概率由对象保留导致 JVM 堆内存压力升高，引发 OOM 风险。"
        )
        probable_cause = (
            "结合 JVM Heap 使用率超过90%、fault_oom_injection_active=1 以及存在 "
            "fault_oom_retained_bytes 保留量，判断对象持续保留是本次 Heap 压力和 OOM 风险的高概率原因。"
        )
        confidence = "高"
        immediate_actions = (
            "- 停止 OOM 故障注入或对象保留任务，确认 retained bytes 与 Heap Used 是否回落。\n"
            "- 在处置前获取 heap dump，使用 MAT 检查 retained heap 和主要引用链。"
        )
        long_term_actions = (
            "- 为故障注入增加自动过期、容量上限和清理机制。\n"
            "- 完善对象保留量告警，并结合实际容量评估 -Xmx。"
        )
        confirmed_fact = _format_jvm_memory_evidence(past_steps)
    elif has_thread_evidence and "runnable" in lower_thread_fact and (
        "consumecpu" in lower_thread_fact or "fault-cpu-simulator" in lower_thread_fact
    ):
        conclusion = (
            f"检测到 Java 进程{'曾出现' if metric_recovered else ''} CPU 持续升高。结合 JVM 线程分析发现 "
            "fault-cpu-simulator 线程持续处于 RUNNABLE 状态，调用栈集中于 "
            "FaultTestController.consumeCpu 方法。当前证据表明该计算逻辑是"
            f"{'本次 CPU 异常' if metric_recovered else ' CPU 升高'}的高概率原因。"
        )
        probable_cause = "fault-cpu-simulator 线程持续执行 consumeCpu 计算逻辑，导致进程 CPU 使用率升高。"
        confidence = "高"
        immediate_actions = (
            "- 停止异常 CPU 计算任务。\n"
            "- 检查 `consumeCpu` 方法的调用来源。\n"
            "- 如果 CPU 持续影响业务，执行实例恢复操作。"
        )
        long_term_actions = (
            "- 避免业务线程执行长时间 CPU 密集计算。\n"
            "- 增加线程池隔离和任务超时控制。\n"
            "- 增加线程 CPU 占用监控。"
        )
        confirmed_fact = thread_dump_evidence
    elif has_thread_evidence and "runnable" in lower_thread_fact:
        conclusion = "已定位 RUNNABLE 线程及代码调用栈，该执行路径是资源异常的高概率原因。"
        probable_cause = "RUNNABLE 线程持续执行关键栈中的业务方法，可能造成 CPU 或请求处理资源持续占用。"
        confidence = "中"
        immediate_actions = "- 核对热点线程对应的业务任务，必要时停止异常任务或重启受影响实例。"
        long_term_actions = "- 为该执行路径增加线程隔离、超时控制和线程 CPU 监控。"
        confirmed_fact = thread_dump_evidence
    else:
        conclusion = "现有证据不足以确认唯一根因，需要补充线程、日志或指标交叉验证。"
        probable_cause = "当前没有足够的代码级证据，暂不指定唯一高概率原因。"
        confidence = "低"
        immediate_actions = "- 核对实例实时状态、关键指标和错误日志；若服务不可用，按应急预案恢复。"
        long_term_actions = "- 补齐缺失的指标、日志和线程证据，依据 Runbook 完成复盘与监控完善。"
        confirmed_fact = "- 仅确认上述工具返回的指标与状态，不将 Runbook 建议视为事实。"
    process = [
        "Step1：解析告警上下文。",
        "Step2：查询 Prometheus 指标与趋势。" if any("prometheus" in tool for tool in tools) else "Step2：未获得 Prometheus 查询结果。",
        (
            "Step3：核对服务或依赖组件状态与缺失证据。"
            if _is_availability_context(input_text)
            else (
                "Step3：分析 JVM、日志或线程证据。"
                if any("jvm" in str(step).lower() or "thread" in str(step).lower() for step, _ in past_steps)
                else "Step3：未获得有效 JVM、日志或线程证据。"
            )
        ),
        "Step4：结合 RAG Runbook。" if "retrieve_knowledge" in tools else "Step4：未获得 RAG Runbook 证据。",
        "Step5：汇总证据并形成诊断结论。",
    ]
    evidence_rows = [
        line for line in evidence_chain.splitlines()
        if line.strip().startswith("|")
    ]
    evidence_table = "\n".join(evidence_rows[:6])
    if not evidence_table:
        evidence_table = (
            "| 来源 | 工具 | 指标 | 值 | 判断 |\n"
            "|---|---|---|---|---|\n"
            "| 当前诊断 | 未获得 | 未获得 | 未获得 | 证据不足 |"
        )
    missing = (
        _availability_evidence_limitations(input_text)
        if _is_availability_context(input_text)
        else _fixed_evidence_limitations()
    )
    if _is_availability_context(input_text):
        evidence_detail = "- **可用性证据**：Prometheus 可用性指标用于确认服务或依赖健康检查状态。"
    elif "smartlifemysqlslowqueryhigh" in input_lower:
        evidence_detail = f"- **MySQL慢查询证据**：{_format_mysql_slow_query_evidence(past_steps)}"
    else:
        evidence_detail = f"- **线程证据**：{thread_fact or thread_dump_evidence or '未获得有效 JVM 线程证据。'}"
    if "smartlifemysqlslowqueryhigh" in input_lower:
        missing = _mysql_slow_query_evidence_limitations()
    missing_summary = _compact_text(missing, 180)
    report = f"""# 故障诊断报告

## 1. 故障摘要

- **告警名称**：{alert['alertname']}
- **服务**：{alert['service']}
- **实例**：{alert['instance']}
- **当前状态**：{current_status}
- **诊断结论**：{conclusion}

## 2. 影响分析

- **可能影响**：告警对应服务可能存在性能下降或可用性风险。
- **无法确认**：{missing_summary}

## 3. 自动诊断过程

{chr(10).join(f'- {item}' for item in process)}

## 4. 证据链分析

{evidence_table}

{evidence_detail}
{rag_evidence}

## 5. 根因分析

### 已确认事实

{confirmed_fact}

### 高概率原因

- {probable_cause}

### 可信度

- **{confidence}**

### 证据限制

{missing}

## 6. 修复建议

### 立即处理

{immediate_actions}

### 长期优化

{long_term_actions}
"""
    if len(report) <= max_chars:
        return report

    # Rebuild once more with minimum wording; the assembled Markdown is never sliced.
    return f"""# 故障诊断报告

## 1. 故障摘要
- **告警名称**：{alert['alertname']}
- **服务**：{alert['service']}
- **实例**：{alert['instance']}
- **当前状态**：{current_status}
- **诊断结论**：{conclusion}

## 2. 影响分析
- 可能存在性能或可用性风险；实际影响待确认。

## 3. 自动诊断过程
- Step1：告警解析。
- Step2：指标查询。
- Step3：{'服务或依赖状态核对。' if _is_availability_context(input_text) else 'JVM、日志与线程分析。'}
- Step4：Runbook 检索。
- Step5：生成保守结论。

## 4. 证据链分析
{evidence_table}

## 5. 根因分析
### 已确认事实
{confirmed_fact}
### 高概率原因
- {probable_cause}
### 可信度
- **{confidence}**
### 证据限制
{missing}

## 6. 修复建议
### 立即处理
{immediate_actions}
### 长期优化
{long_term_actions}
"""


def _safe_json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _iter_prometheus_metric_payloads(payload: Any) -> list[dict[str, Any]]:
    """Recursively collect Prometheus metric query payloads from executor results."""
    collected: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        query = payload.get("query") or payload.get("promql")
        results = payload.get("results")
        if query and ("success" in payload or isinstance(results, list)):
            collected.append(payload)
        for value in payload.values():
            collected.extend(_iter_prometheus_metric_payloads(value))
    elif isinstance(payload, list):
        for item in payload:
            collected.extend(_iter_prometheus_metric_payloads(item))
    return collected


def _analyze_availability_evidence(
    input_text: str,
    past_steps: list[tuple],
) -> dict[str, Any]:
    """Extract current up/probe_success evidence for availability reports."""
    context = _report_context(input_text)
    expected_metric = (
        "probe_success"
        if context["category"] == "dependency_availability"
        else "up"
    )
    current_value: float | None = None
    promql = ""
    labels: dict[str, Any] = {}
    for _step, result in past_steps:
        parsed = _safe_json_loads(result)
        if parsed is None:
            continue
        for payload in _iter_prometheus_metric_payloads(parsed):
            for item in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
                if not isinstance(item, dict) or "summary" in item:
                    continue
                metric_text = str(
                    item.get("metric_name")
                    or item.get("metric")
                    or item.get("promql")
                    or payload.get("query")
                    or ""
                ).lower()
                if expected_metric not in metric_text:
                    continue
                try:
                    current_value = float(item.get("value"))
                except (TypeError, ValueError):
                    continue
                promql = str(item.get("promql") or payload.get("query") or expected_metric)
                labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
    return {
        **context,
        "metric": expected_metric,
        "promql": promql,
        "labels": labels,
        "value": current_value,
        "abnormal": current_value == 0,
        "available": current_value is not None and current_value >= 1,
    }


def _format_prometheus_metric_evidence(past_steps: list[tuple]) -> str:
    """Build a non-truncated Markdown table for every Prometheus metric/range call."""
    metric_payloads: list[dict[str, Any]] = []
    for step, result in past_steps:
        result_text = str(result)
        if (
            "query_prometheus_metrics" not in str(step)
            and "query_prometheus_metrics" not in result_text
            and "query_prometheus_range" not in str(step)
            and "query_prometheus_range" not in result_text
        ):
            continue
        parsed = _safe_json_loads(result)
        if parsed is None:
            continue
        metric_payloads.extend(_iter_prometheus_metric_payloads(parsed))

    if not metric_payloads:
        return "No query_prometheus_metrics/query_prometheus_range evidence found in past_steps."

    rows = [
        "| source | metric | promql | labels | normalized_instance | value/trend | unit | timestamp/window | description |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    seen_metric_items: set[str] = set()
    notes: list[str] = []

    for payload in metric_payloads:
        query = payload.get("query") or payload.get("promql") or ""
        source = payload.get("tool") or ("query_prometheus_range" if "start" in payload and "end" in payload else "query_prometheus_metrics")
        if payload.get("success") is False:
            notes.append(f"- {source} `{query}` failed: {payload.get('error') or payload.get('message') or 'unknown'}")
            continue

        results = payload.get("results", [])
        if not isinstance(results, list) or not results:
            notes.append(f"- {source} `{query}` returned no series, result_type={payload.get('result_type', 'unknown')}.")
            continue

        for item in results:
            if not isinstance(item, dict):
                continue
            metric = item.get("metric_name") or item.get("metric") or query
            labels = item.get("labels") or {}
            normalized_labels = item.get("normalized_labels") or {}
            labels_text = json.dumps(labels, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            normalized_instance = normalized_labels.get("normalized_instance", labels.get("instance", ""))

            if "summary" in item or "values" in item:
                summary = item.get("summary") or {}
                value_text = (
                    f"points={summary.get('points', 0)}, "
                    f"first={summary.get('first_value', 'n/a')}, "
                    f"last={summary.get('last_display_value', summary.get('last_value', 'n/a'))}, "
                    f"min={summary.get('min_display_value', summary.get('min_value', 'n/a'))}, "
                    f"max={summary.get('max_display_value', summary.get('max_value', 'n/a'))}"
                )
                timestamp_text = f"{payload.get('start', '')} ~ {payload.get('end', '')}, step={payload.get('step', '')}"
                unit = summary.get("unit") or item.get("unit", "raw")
                description = summary.get("description") or item.get("description", "")
                identity_value = value_text
            else:
                value_text = str(item.get("display_value", item.get("value")))
                timestamp_text = str(item.get("timestamp"))
                unit = item.get("unit", "raw")
                description = item.get("description", "")
                identity_value = str(item.get("value"))

            identity = json.dumps(
                {
                    "source": source,
                    "metric": metric,
                    "promql": item.get("promql") or query,
                    "labels": labels,
                    "value": identity_value,
                    "timestamp": timestamp_text,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            if identity in seen_metric_items:
                continue
            seen_metric_items.add(identity)

            safe_description = str(description).replace("|", "/")
            rows.append(
                "| "
                + " | ".join(
                    [
                        str(source),
                        str(metric),
                        f"`{item.get('promql') or query}`",
                        f"`{labels_text}`",
                        str(normalized_instance),
                        str(value_text),
                        str(unit),
                        str(timestamp_text),
                        safe_description,
                    ]
                )
                + " |"
            )

    if len(rows) == 2 and notes:
        return "\n".join(notes)
    if notes:
        rows.extend(["", "Additional notes:", *notes])
    return "\n".join(rows)


def _format_bytes(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1024**3:
        return f"{value / 1024**3:.3f} GB"
    if value >= 1024**2:
        return f"{value / 1024**2:.3f} MB"
    return f"{value:.0f} bytes"


def _extract_numeric_value(item: dict[str, Any]) -> float | None:
    summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
    raw_value = summary.get("last_value") if summary else item.get("value")
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return None


def _analyze_jvm_oom_evidence(past_steps: list[tuple]) -> dict[str, Any]:
    """Extract the strong SmartLifeJvmMemoryHighUsage cause signal from metric evidence."""
    used: float | None = None
    maximum: float | None = None
    injection: float | None = None
    retained: float | None = None
    retained_growing = False

    for _step, result in past_steps:
        parsed = _safe_json_loads(result)
        if parsed is None:
            continue
        for payload in _iter_prometheus_metric_payloads(parsed):
            for item in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
                if not isinstance(item, dict):
                    continue
                metric_name = str(item.get("metric_name") or item.get("metric") or payload.get("query") or "")
                metric_text = f"{metric_name} {item.get('promql') or payload.get('query') or ''}".lower()
                value = _extract_numeric_value(item)
                if value is None:
                    continue
                values = item.get("values") if isinstance(item.get("values"), list) else []
                sequence: list[float] = []
                for point in values:
                    raw = point.get("value") if isinstance(point, dict) else None
                    try:
                        sequence.append(float(raw))
                    except (TypeError, ValueError):
                        continue
                if "fault_oom_injection_active" in metric_text:
                    injection = value
                elif "fault_oom_retained_bytes" in metric_text:
                    retained = value
                    if len(sequence) >= 2:
                        retained_growing = sequence[-1] > sequence[0] and any(
                            sequence[index] > sequence[index - 1]
                            for index in range(1, len(sequence))
                        )
                elif "jvm_memory_used_bytes" in metric_text and value >= 0:
                    used = value
                elif "jvm_memory_max_bytes" in metric_text and value > 0:
                    maximum = value

    usage = (used / maximum * 100) if used is not None and maximum else None
    return {
        "used": used,
        "maximum": maximum,
        "usage": usage,
        "injection": injection,
        "retained": retained,
        "retained_growing": retained_growing,
        "strong_retention_cause": bool(
            usage is not None
            and usage > 90
            and injection is not None
            and injection >= 1
            and retained is not None
        ),
    }


def _analyze_mysql_slow_query_evidence(past_steps: list[tuple]) -> dict[str, Any]:
    """Extract strong SmartLifeMysqlSlowQueryHigh evidence from instant and range results."""
    active: float | None = None
    executions: float | None = None
    duration_ms: float | None = None
    executions_growing = False

    for _step, result in past_steps:
        parsed = _safe_json_loads(result)
        if parsed is None:
            continue
        for payload in _iter_prometheus_metric_payloads(parsed):
            for item in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
                if not isinstance(item, dict):
                    continue
                metric_name = str(item.get("metric_name") or item.get("metric") or payload.get("query") or "")
                metric_text = f"{metric_name} {item.get('promql') or payload.get('query') or ''}".lower()
                value = _extract_numeric_value(item)
                if value is None:
                    continue
                values = item.get("values") if isinstance(item.get("values"), list) else []
                sequence: list[float] = []
                for point in values:
                    raw = point.get("value") if isinstance(point, dict) else None
                    try:
                        sequence.append(float(raw))
                    except (TypeError, ValueError):
                        continue

                if "fault_mysql_slow_query_active" in metric_text:
                    active = value
                elif "fault_mysql_slow_query_executions" in metric_text:
                    executions = value
                    if len(sequence) >= 2:
                        executions_growing = sequence[-1] > sequence[0] and any(
                            sequence[index] > sequence[index - 1]
                            for index in range(1, len(sequence))
                        )
                elif "fault_mysql_slow_query_last_duration_milliseconds" in metric_text:
                    duration_ms = value

    return {
        "active": active,
        "executions": executions,
        "duration_ms": duration_ms,
        "executions_growing": executions_growing,
        "duration_high": duration_ms is not None and duration_ms >= 1000,
        "strong_slow_query_cause": bool(
            active is not None
            and active >= 1
            and duration_ms is not None
            and duration_ms > 1000
        ),
    }


def _format_mysql_slow_query_evidence(past_steps: list[tuple]) -> str:
    evidence = _analyze_mysql_slow_query_evidence(past_steps)
    active_text = (
        "开启" if evidence["active"] is not None and evidence["active"] >= 1
        else ("关闭" if evidence["active"] is not None else "n/a")
    )
    duration_text = (
        f"{evidence['duration_ms']:.3f} ms"
        if evidence["duration_ms"] is not None
        else "n/a"
    )
    return "\n".join(
        [
            f"- 慢查询故障注入状态：{active_text}",
            f"- 慢查询累计执行次数：{evidence['executions'] if evidence['executions'] is not None else 'n/a'}",
            f"- executions趋势：{'持续增长' if evidence['executions_growing'] else '未确认持续增长'}",
            f"- 最近一次慢查询耗时：{duration_text}",
            (
                "- 判断：检测到持续慢查询故障注入，高耗时SQL持续执行导致MySQL查询性能下降"
                if evidence["strong_slow_query_cause"]
                else "- 判断：现有指标尚未同时满足持续慢查询强证据条件"
            ),
        ]
    )


def _is_heap_memory_item(item: dict[str, Any], payload: dict[str, Any], metric_name: str) -> bool:
    labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
    promql = str(item.get("promql") or payload.get("query") or payload.get("promql") or "").lower()
    metric_text = f"{metric_name} {promql}".lower()
    if "jvm_memory_" not in metric_text:
        return False
    area = str(labels.get("area", "")).lower()
    return area in ("", "heap") or 'area="heap"' in promql or "area='heap'" in promql


def _format_jvm_memory_evidence(past_steps: list[tuple]) -> str:
    """Summarize JVM Heap used/max and usage ratio from Prometheus evidence."""
    instant_used: list[float] = []
    instant_max: list[float] = []
    fallback_used: list[float] = []
    fallback_max: list[float] = []
    oom_injection_active: float | None = None
    oom_retained_bytes: float | None = None

    for _step, result in past_steps:
        parsed = _safe_json_loads(result)
        if parsed is None:
            continue
        for payload in _iter_prometheus_metric_payloads(parsed):
            for item in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
                if not isinstance(item, dict):
                    continue
                metric_name = str(item.get("metric_name") or item.get("metric") or payload.get("query") or "")
                metric_text = f"{metric_name} {item.get('promql') or payload.get('query') or ''}".lower()
                value = _extract_numeric_value(item)
                if value is not None and value >= 0 and "fault_oom_injection_active" in metric_text:
                    oom_injection_active = value
                    continue
                if value is not None and value >= 0 and "fault_oom_retained_bytes" in metric_text:
                    oom_retained_bytes = value
                    continue
                if not _is_heap_memory_item(item, payload, metric_name):
                    continue
                if value is None or value < 0:
                    continue
                is_range = "summary" in item or "start" in payload
                target_used = fallback_used if is_range else instant_used
                target_max = fallback_max if is_range else instant_max
                if "jvm_memory_used_bytes" in metric_text:
                    target_used.append(value)
                elif "jvm_memory_max_bytes" in metric_text and value > 0:
                    target_max.append(value)

    used_values = instant_used or fallback_used
    max_values = instant_max or fallback_max
    heap_used = sum(used_values) if used_values else None
    heap_max = sum(max_values) if max_values else None
    usage = (heap_used / heap_max * 100) if heap_used is not None and heap_max else None
    usage_text = f"{usage:.2f}%" if usage is not None else "n/a"
    oom_analysis = _analyze_jvm_oom_evidence(past_steps)

    risk = "证据不足"
    if usage is not None:
        if usage >= 90:
            risk = "OOM风险高，Heap使用率超过90%"
        elif usage >= 75:
            risk = "Heap压力偏高，需要继续观察"
        else:
            risk = "当前Heap使用率未超过高风险阈值"

    injection_text = (
        "开启（存在人为 OOM 压力注入）"
        if oom_injection_active is not None and oom_injection_active >= 1
        else ("关闭" if oom_injection_active is not None else "n/a")
    )
    if oom_injection_active is not None and oom_injection_active >= 1:
        if oom_retained_bytes is not None and oom_retained_bytes > 0:
            risk = "OOM 注入已开启且存在对象保留，是 Heap 高使用率的高概率原因"
        else:
            risk = "OOM 注入已开启，是 Heap 压力的高概率原因"

    return "\n".join(
        [
            f"- Heap Used：{_format_bytes(heap_used)}",
            f"- Heap Max：{_format_bytes(heap_max)}",
            f"- Heap使用率：{usage_text}",
            f"- OOM注入状态：{injection_text}",
            f"- OOM对象保留量：{_format_bytes(oom_retained_bytes)}",
            f"- OOM对象保留趋势：{'持续增长' if oom_analysis['retained_growing'] else '未确认持续增长'}",
            f"- 判断：{risk}",
        ]
    )


def _collect_range_series(past_steps: list[tuple]) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    for step, result in past_steps:
        result_text = str(result)
        if "query_prometheus_range" not in str(step) and "query_prometheus_range" not in result_text:
            continue
        parsed = _safe_json_loads(result)
        if parsed is None:
            continue
        for payload in _iter_prometheus_metric_payloads(parsed):
            source = payload.get("tool") or ("query_prometheus_range" if "start" in payload and "end" in payload else "")
            if source != "query_prometheus_range" and "start" not in payload:
                continue
            for item in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
                if not isinstance(item, dict):
                    continue
                if "summary" not in item and "values" not in item:
                    continue
                series.append({"payload": payload, "item": item})
    return series


def _format_trend_analysis(past_steps: list[tuple]) -> str:
    range_series = _collect_range_series(past_steps)
    if not range_series:
        return "\u672a\u83b7\u53d6\u5230 query_prometheus_range \u8d8b\u52bf\u6570\u636e\u3002"

    lines = [
        "## \u8d8b\u52bf\u5206\u6790",
        "",
        "| \u6307\u6807 | labels | \u65f6\u95f4\u8303\u56f4 | \u6570\u636e\u70b9 | \u6700\u5927\u503c | \u6700\u5c0f\u503c | \u5f53\u524d\u503c | \u5224\u65ad |",
        "|---|---|---|---:|---|---|---|---|",
    ]
    details: list[str] = []
    for entry in range_series:
        payload = entry["payload"]
        item = entry["item"]
        summary = item.get("summary") or {}
        labels = item.get("labels") or {}
        labels_text = json.dumps(labels, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        metric = item.get("metric_name") or item.get("metric") or item.get("promql") or payload.get("query", "")
        points = summary.get("points", 0)
        max_value = summary.get("max_value")
        min_value = summary.get("min_value")
        last_value = summary.get("last_value")
        max_display = summary.get("max_display_value", max_value)
        min_display = summary.get("min_display_value", min_value)
        current_display = summary.get("last_display_value", last_value)
        window = f"{payload.get('start', '')} ~ {payload.get('end', '')}, step={payload.get('step', '')}"

        judgment = "\u8d8b\u52bf\u6b63\u5e38\u6216\u8bc1\u636e\u4e0d\u8db3"
        try:
            max_number = float(max_value)
            last_number = float(last_value)
            min_number = float(min_value)
            metric_text = str(metric).lower() + str(item.get("promql", "")).lower()
            if (
                metric_text == "up"
                or "up{job=" in metric_text
                or "probe_success" in metric_text
            ):
                values = item.get("values") or []
                numeric_sequence = [float(point["value"]) for point in values if isinstance(point, dict) and point.get("value") is not None]
                had_up_before_down = any(
                    numeric_sequence[index - 1] >= 1 and numeric_sequence[index] == 0
                    for index in range(1, len(numeric_sequence))
                )
                recovered_after_down = any(value == 0 for value in numeric_sequence) and last_number >= 1
                if had_up_before_down and recovered_after_down:
                    judgment = f"告警期间存在 {metric} 从 1 变 0，当前已恢复为 1"
                elif min_number == 0 and recovered_after_down:
                    judgment = f"告警窗口内存在 {metric}=0，当前已恢复为 1"
                elif last_number == 0:
                    judgment = f"当前 {metric}=0，故障尚未恢复"
                elif min_number >= 1:
                    judgment = f"窗口内未捕获 {metric}=0，当前 {metric}=1；历史证据不足"
            elif "cpu" in metric_text and "usage" in metric_text:
                if max_number >= 0.8 and last_number < 0.5:
                    judgment = "\u5386\u53f2\u7a97\u53e3\u5b58\u5728CPU\u5cf0\u503c\uff0c\u5f53\u524d\u53ef\u80fd\u5df2\u6062\u590d"
                elif last_number >= 0.8 and max_number >= 0.8:
                    judgment = "CPU\u5f02\u5e38\u4ecd\u5b58\u5728"
                elif max_number < 0.8:
                    judgment = "\u672a\u53d1\u73b0CPU\u5f02\u5e38\u8d8b\u52bf\uff0c\u53ef\u80fd\u4e3a\u8bef\u62a5\u6216\u544a\u8b66\u5ef6\u8fdf"
            elif "jvm_memory_used_bytes" in metric_text or ("memory" in metric_text and "heap" in metric_text):
                values = item.get("values") or []
                numeric_sequence = [
                    float(point["value"])
                    for point in values
                    if isinstance(point, dict) and point.get("value") is not None
                ]
                largest_jump = 0.0
                if len(numeric_sequence) >= 2:
                    largest_jump = max(
                        numeric_sequence[index] - numeric_sequence[index - 1]
                        for index in range(1, len(numeric_sequence))
                    )
                if max_number > min_number and last_number < max_number * 0.6:
                    judgment = "JVM Heap历史存在高位，当前已恢复或明显回落"
                elif last_number > min_number and last_number >= max_number * 0.8 and last_number >= min_number * 1.2:
                    judgment = "JVM Heap持续增长，当前仍处高位，存在OOM风险"
                elif largest_jump > 0 and max_number > 0 and largest_jump >= max_number * 0.3:
                    judgment = "JVM Heap突然升高，需排查大对象分配或突发流量"
                elif max_number > min_number:
                    judgment = "JVM Heap存在波动，需结合Heap Max计算使用率"
            else:
                if max_number > min_number and last_number >= max_number * 0.8:
                    judgment = "\u5f53\u524d\u63a5\u8fd1\u7a97\u53e3\u9ad8\u4f4d\uff0c\u9700\u8981\u7ee7\u7eed\u6392\u67e5"
                elif max_number > min_number and last_number < max_number * 0.5:
                    judgment = "\u5386\u53f2\u5b58\u5728\u5cf0\u503c\uff0c\u5f53\u524d\u5df2\u6709\u56de\u843d"
        except (TypeError, ValueError):
            pass

        lines.append(
            f"| {metric} | `{labels_text}` | {window} | {points} | {max_display} | {min_display} | {current_display} | {judgment} |"
        )
        details.append(f"- {metric}: {judgment}")

    lines.extend(["", "\u5206\u6790\uff1a", *details])
    return "\n".join(lines)


def _interpret_metric(metric: str, value_text: str, description: str) -> tuple[str, str]:
    import re

    metric_lower = metric.lower()
    numbers = re.findall(r"-?\d+(?:\.\d+)?", value_text.replace(",", ""))
    number = float(numbers[0]) if numbers else None
    if "probe_success" in metric_lower:
        if number is not None and number == 0:
            return "健康探测失败，确认依赖当前不可用", "可用性异常"
        if number is not None and number >= 1:
            return "健康探测成功，依赖当前可用", "恢复证据"
        return "健康探测结果无法明确判断可用性", "可用性证据"
    if "cpu" in metric_lower and "usage" in metric_lower:
        if number is not None and "%" not in value_text and 0 <= number <= 1:
            number *= 100
        if number is not None and number >= 80:
            return "CPU持续超过阈值，确认存在CPU压力", "CPU异常"
        return "CPU当前未超过高使用率阈值", "CPU证据"
    if "gc" in metric_lower:
        if number is not None and number <= 0.1:
            return "GC活动较低，当前无明显频繁GC证据，不支持GC导致CPU升高", "GC排除证据"
        if number is not None and number >= 1:
            return "GC活动明显偏高，频繁GC可能与CPU升高相关", "GC关联证据"
        return "GC活动未达到明显异常水平，暂不支持GC是CPU升高主因", "GC辅助证据"
    if "thread" in metric_lower:
        return "线程指标用于判断线程增长或阻塞风险", "线程证据"
    if "http" in metric_lower or "request" in metric_lower:
        return "请求指标用于判断流量变化与资源压力的关联", "流量证据"
    if "memory" in metric_lower or "heap" in metric_lower:
        return "JVM内存指标用于判断内存压力和OOM风险", "内存证据"
    return description or "该指标作为当前诊断的监控证据", "监控证据"


def _format_evidence_chain(past_steps: list[tuple]) -> str:
    import re

    rows = [
        "| 来源 | 工具 | 指标 | 值 | 判断 |",
        "|---|---|---|---|---|",
    ]
    added = False
    seen_metrics: set[str] = set()
    seen_rag = False
    thread_groups: dict[tuple[str, str, str], list[str]] = {}

    for step, result in past_steps:
        parsed = _safe_json_loads(result)
        if parsed is None:
            if "retrieve_knowledge" in str(step) and not seen_rag:
                rows.append(
                    "| RAG | retrieve_knowledge | Runbook | 已命中 | 提供候选原因与处置建议，不能作为事实证据 |"
                )
                added = True
                seen_rag = True
            continue

        if isinstance(parsed, dict) and parsed.get("success") is False:
            tool = parsed.get("tool") or "unknown"
            error = parsed.get("tool_error") or parsed.get("error") or "unknown error"
            rows.append(
                f"| 工具错误 | {tool} | 无 | `{_truncate_text(str(error), 120)}` | 本项证据不可用 |"
            )
            added = True
            continue

        for payload in _iter_prometheus_metric_payloads(parsed):
            tool = payload.get("tool") or ("query_prometheus_range" if "start" in payload and "end" in payload else "query_prometheus_metrics")
            for item in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
                if not isinstance(item, dict):
                    continue
                metric = str(item.get("metric_name") or item.get("metric") or item.get("promql") or payload.get("query", "unknown"))
                metric_identity = metric.lower()
                if metric_identity in seen_metrics:
                    continue
                seen_metrics.add(metric_identity)
                description = str(item.get("description") or (item.get("summary") or {}).get("description") or "")
                if "summary" in item:
                    summary = item.get("summary") or {}
                    value = (
                        f"latest={summary.get('last_display_value', summary.get('last_value', 'n/a'))}, "
                        f"max={summary.get('max_display_value', summary.get('max_value', 'n/a'))}, "
                        f"min={summary.get('min_display_value', summary.get('min_value', 'n/a'))}, "
                        f"points={summary.get('points', 0)}"
                    )
                else:
                    value = str(item.get("display_value", item.get("value", "n/a")))
                interpretation, _impact = _interpret_metric(metric, value, description)
                rows.append(f"| Prometheus | {tool} | {metric} | {value} | {interpretation} |")
                added = True

        result_text = str(result)
        if not seen_rag and (
            "retrieve_knowledge" in str(step) or "Runbook" in result_text or "参考资料" in result_text
        ):
            rows.append(
                f"| RAG | retrieve_knowledge | Runbook | 已命中 | {_truncate_text(result_text, 120)} |"
            )
            added = True
            seen_rag = True

        if "collect_jvm_thread_dump" in str(step) and isinstance(parsed, dict):
            for thread in parsed.get("threads", []) if isinstance(parsed.get("threads"), list) else []:
                if not isinstance(thread, dict):
                    continue
                name = thread.get("name") or thread.get("threadName") or "unknown"
                status = thread.get("state") or thread.get("threadState") or "unknown"
                stack = thread.get("stacktrace") or thread.get("stackTrace") or []
                frames = stack if isinstance(stack, list) else []
                if _is_ignored_jvm_thread(str(name), frames):
                    continue
                key_frame = str(stack[0]) if isinstance(stack, list) and stack else "无调用栈"
                pattern = re.sub(r"\d+$", "*", str(name))
                identity = (pattern, str(status).upper(), key_frame)
                thread_groups.setdefault(identity, []).append(str(name))

    for (_pattern, status, key_frame), names in thread_groups.items():
        display_name = names[0] if len(names) == 1 else f"{names[0]} ~ {names[-1]}（{len(names)}个）"
        judgment = (
            f"发现持续运行线程，调用栈定位到 {_truncate_text(key_frame, 100)}"
            if status == "RUNNABLE"
            else f"线程状态为 {status}，需结合调用栈判断影响"
        )
        rows.append(f"| JVM线程分析 | thread_analysis | {display_name} | {status} | {judgment} |")
        added = True

    return "\n".join(rows) if added else "No structured evidence was collected."


def _format_missing_information(past_steps: list[tuple], input_text: str = "") -> str:
    mysql_context = "smartlifemysqlslowqueryhigh" in input_text.lower()
    availability_context = _is_availability_context(input_text)
    if mysql_context:
        missing = _mysql_slow_query_evidence_limitations().splitlines()
    elif availability_context:
        missing = _availability_evidence_limitations(input_text).splitlines()
    else:
        missing = _fixed_evidence_limitations().splitlines()
    has_valid_jvm_thread_info = False
    for step, result in past_steps:
        if "collect_jvm_thread_dump" not in str(step):
            continue
        parsed = _safe_json_loads(result)
        if (
            isinstance(parsed, dict)
            and parsed.get("success") is True
            and isinstance(parsed.get("threads"), list)
            and any(
                isinstance(thread, dict)
                and not _is_ignored_jvm_thread(
                    str(thread.get("name") or thread.get("threadName") or ""),
                    (
                        thread.get("stacktrace", thread.get("stackTrace", []))
                        if isinstance(
                            thread.get("stacktrace", thread.get("stackTrace", [])),
                            list,
                        )
                        else []
                    ),
                )
                for thread in parsed["threads"]
            )
        ):
            has_valid_jvm_thread_info = True
            break

    if not mysql_context and not availability_context and not has_valid_jvm_thread_info:
        missing.append("- 当前诊断流程未获取有效JVM线程信息，无法进一步定位线程级根因。")

    for step, result in past_steps:
        parsed = _safe_json_loads(result)
        if isinstance(parsed, dict) and parsed.get("success") is False:
            tool = parsed.get("tool") or str(step)
            error = parsed.get("tool_error") or parsed.get("error") or "unknown error"
            missing.append(f"- `{tool}` failed: {_truncate_text(str(error), 200)}")
    return "\n".join(missing)


def _ensure_trend_section(final_response: str, trend_analysis: str) -> str:
    if not trend_analysis or "\u672a\u83b7\u53d6\u5230 query_prometheus_range" in trend_analysis:
        return final_response
    if "## \u8d8b\u52bf\u5206\u6790" in final_response:
        return final_response
    return final_response.rstrip() + "\n\n" + trend_analysis


def _has_real_prometheus_metric_evidence(evidence: str) -> bool:
    return "query_prometheus_" in evidence and "| source | metric | promql | labels |" in evidence


def _ensure_prometheus_metric_section(final_response: str, evidence: str) -> str:
    """Guarantee that final Markdown contains the non-truncated metric evidence."""
    if not _has_real_prometheus_metric_evidence(evidence):
        return final_response
    if (
        ("\u5df2\u83b7\u53d6\u8bc1\u636e" in final_response or "\u6307\u6807\u8bc1\u636e\u94fe" in final_response)
        and "process_cpu_usage" in final_response
        and "\u622a\u65ad" not in final_response
    ):
        return final_response

    metric_section = f"""## 2. \u5df2\u83b7\u53d6\u8bc1\u636e

{evidence}

"""
    logger.info("Injecting non-truncated Prometheus metric evidence into final report")
    return metric_section + final_response


def _is_jvm_oom_context(input_text: str, past_steps: list[tuple]) -> bool:
    lower = input_text.lower()
    if any(keyword in lower for keyword in ("smartlifejvmmemoryhighusage", "jvmoutofmemory", "outofmemory", "oom", "heap")):
        return True
    evidence_text = "\n".join(f"{step}\n{result}" for step, result in past_steps).lower()
    return "jvm_memory_used_bytes" in evidence_text or "jvm_memory_max_bytes" in evidence_text


def _ensure_jvm_oom_sections(
    final_response: str,
    input_text: str,
    past_steps: list[tuple],
    jvm_memory_evidence: str,
    trend_analysis: str,
) -> str:
    if not _is_jvm_oom_context(input_text, past_steps):
        return final_response

    sections: list[str] = []
    if "## JVM内存证据" not in final_response:
        sections.append(f"## JVM内存证据\n{jvm_memory_evidence}")
    if "## 趋势分析" not in final_response:
        trend_text = trend_analysis if "未获取到 query_prometheus_range" not in trend_analysis else "- 未获取到 JVM Heap 历史趋势数据。"
        sections.append(f"## 趋势分析\n{trend_text}")
    if "## 根因分析" not in final_response:
        sections.append(
            "## 根因分析\n"
            f"{jvm_memory_evidence}\n"
            "- 若 OOM注入状态为开启且对象保留量大于 0，可将“故障注入持续保留对象”判定为高概率直接原因。\n"
            "- 内存泄漏：如果 Heap Used 持续增长且未回落，优先检查长生命周期集合、缓存、静态引用和监听器引用。\n"
            "- 大对象分配：如果 Heap 曲线突然升高，优先排查批量查询、一次性加载、文件/图片处理等大对象分配路径。\n"
            "- 堆空间不足：如果业务负载正常但 Heap使用率长期接近上限，评估 -Xmx 是否低于实际容量需求。\n"
            "- GC压力：结合 GC pause/count 指标判断是否存在频繁 GC、回收效率下降或晋升失败风险。"
        )
    if "## 处理建议" not in final_response:
        sections.append(
            "## 处理建议\n"
            "- 获取 heap dump，并保留触发时间点前后的 JVM 参数和实例信息。\n"
            "- 使用 MAT 分析 Dominator Tree、Leak Suspects 和 retained heap。\n"
            "- 检查高占用对象的引用链，定位缓存、集合、ThreadLocal、连接/会话等未释放引用。\n"
            "- 根据证据调整 -Xmx，同时避免只扩容掩盖内存泄漏。"
        )

    if not sections:
        return final_response
    return final_response.rstrip() + "\n\n" + "\n\n".join(sections)


def _enforce_strong_jvm_oom_root_cause(
    report: str,
    input_text: str,
    past_steps: list[tuple],
) -> str:
    """Prevent generic evidence limitations from overriding strong OOM evidence."""
    if "smartlifejvmmemoryhighusage" not in input_text.lower():
        return report
    analysis = _analyze_jvm_oom_evidence(past_steps)
    if not analysis["strong_retention_cause"]:
        return report

    retention_trend_fact = (
        "且历史趋势持续增长"
        if analysis["retained_growing"]
        else "；当前未取得其增长趋势，但不影响确认对象保留量存在"
    )
    root_cause = f"""## 5. 根因分析

### 已确认事实

- JVM Heap 使用率为 {analysis['usage']:.1f}%，超过 90% 高风险阈值。
- `fault_oom_injection_active=1`，OOM 压力注入处于开启状态。
- `fault_oom_retained_bytes` 当前为 {_format_bytes(analysis['retained'])}{retention_trend_fact}。

### 高概率原因

- 结合 JVM Heap 使用率超过90%、`fault_oom_injection_active=1` 以及存在 retained bytes 保留量，判断当前异常大概率由持续对象保留导致 JVM 堆内存压力升高，引发 OOM 风险。

### 可信度

- **高**

### 证据限制

{_fixed_evidence_limitations()}
- 当前未获取 Heap Dump，无法进一步定位被保留的具体对象类型、引用链或代码位置；该限制不否定已确认的 Heap 压力与持续对象保留证据。

"""
    if re.search(r"(?m)^## 5\. 根因分析\s*$", report):
        return re.sub(
            r"(?ms)^## 5\. 根因分析\s*.*?(?=^## 6\.|\Z)",
            root_cause,
            report,
            count=1,
        ).rstrip()
    return report.rstrip() + "\n\n" + root_cause.rstrip()


def _enforce_strong_mysql_slow_query_root_cause(
    report: str,
    input_text: str,
    past_steps: list[tuple],
) -> str:
    """Override generic low-confidence output when slow-query metrics are strong."""
    if "smartlifemysqlslowqueryhigh" not in input_text.lower():
        return report
    analysis = _analyze_mysql_slow_query_evidence(past_steps)
    if not analysis["strong_slow_query_cause"]:
        return report

    execution_fact = (
        f"`fault_mysql_slow_query_executions` 当前为 {analysis['executions']:.0f}，且最近10分钟持续增长。"
        if analysis["executions_growing"]
        else (
            f"`fault_mysql_slow_query_executions` 当前为 {analysis['executions']:.0f}；未取得持续增长趋势，该趋势不是本次强证据判定的必要条件。"
            if analysis["executions"] is not None
            else "`fault_mysql_slow_query_executions` 当前未取得；不影响 active 与高耗时指标形成的强证据。"
        )
    )
    root_cause = f"""## 5. 根因分析

### 已确认事实

- `fault_mysql_slow_query_active=1`，慢查询故障注入处于开启状态。
- {execution_fact}
- 最近一次慢查询耗时为 {analysis['duration_ms']:.3f} ms，属于高耗时查询。

### 高概率原因

- 检测到持续慢查询故障注入，高耗时SQL持续执行导致MySQL查询性能下降。

### 可信度

- **高**

### 证据限制

- 当前未获取具体SQL，无法定位具体业务查询语句。
- 当前未获取慢查询日志，无法核对SQL参数、扫描行数和锁等待详情。
- 当前未获取EXPLAIN执行计划，无法确认全表扫描、filesort或具体索引缺失位置。
- 上述限制仅影响SQL和代码位置的进一步定位，不否定Prometheus指标已经确认的持续慢查询异常。

"""
    if re.search(r"(?m)^## 5\. 根因分析\s*$", report):
        return re.sub(
            r"(?ms)^## 5\. 根因分析\s*.*?(?=^## 6\.|\Z)",
            root_cause,
            report,
            count=1,
        ).rstrip()
    return report.rstrip() + "\n\n" + root_cause.rstrip()


def _sanitize_mysql_slow_query_report(report: str, input_text: str) -> str:
    """Remove unrelated CPU/JVM troubleshooting lines from MySQL reports."""
    if "smartlifemysqlslowqueryhigh" not in input_text.lower():
        return report
    forbidden = (
        "未发现明确业务热点线程",
        "jstack",
        "cpu热点线程",
        "cpu 热点线程",
        "cpu线程分析",
        "cpu 线程分析",
        "jvm线程",
        "profiler",
    )
    kept: list[str] = []
    for line in report.splitlines():
        lower = line.lower()
        if any(term in lower for term in forbidden):
            continue
        if re.search(r"(?i)(?:检查|分析|排查)\s*gc\b", line):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _replace_report_section(report: str, number: int, title: str, content: str) -> str:
    heading = f"## {number}. {title}"
    replacement = f"{heading}\n\n{content.strip()}\n\n"
    if not re.search(rf"(?m)^## {number}\. {re.escape(title)}\s*$", report):
        return report
    return re.sub(
        rf"(?ms)^## {number}\. {re.escape(title)}\s*.*?(?=^## {number + 1}\.|\Z)",
        replacement,
        report,
        count=1,
    ).rstrip()


def _availability_runbook_section(input_text: str, past_steps: list[tuple]) -> str:
    mapping = match_fault_mapping(input_text) or {}
    allowlist = list(mapping.get("runbook_allowlist") or [])
    rag_results = "\n".join(
        str(result)
        for step, result in past_steps
        if "retrieve_knowledge" in str(step)
    )
    matched = [name for name in allowlist if name.casefold() in rag_results.casefold()]
    source_text = "、".join(matched) if matched else "白名单内 Runbook 未命中"
    alert_name = str(mapping.get("alert_name") or "")
    if alert_name == "RedisUnavailable":
        directions = (
            "- 检查 Redis 实例进程与运行状态。\n"
            "- 验证 Redis 端口、网络连通性和防火墙策略。\n"
            "- 核对 Redis 地址、端口、密码和认证配置。"
        )
    elif alert_name == "MysqlUnavailable":
        directions = (
            "- 检查 MySQL 实例进程与运行状态。\n"
            "- 验证数据库端口、网络连通性和防火墙策略。\n"
            "- 核对连接配置，并检查连接数和资源是否耗尽。"
        )
    else:
        directions = (
            "- 检查 SmartLife 应用进程和 Spring Boot 启动日志。\n"
            "- 验证 8081 端口监听状态和网络可达性。\n"
            "- 核对启动配置、端口占用和依赖初始化结果。"
        )
    return f"""### RAG Runbook

- **检索范围**：{source_text}
- **排查方向**：
{directions}"""


def _enforce_availability_report(
    report: str,
    input_text: str,
    past_steps: list[tuple],
) -> str:
    """Keep availability reports domain-specific and preserve metric-backed facts."""
    if not _is_availability_context(input_text):
        return report
    context = _analyze_availability_evidence(input_text, past_steps)
    forbidden = (
        "线程 cpu 时间占比",
        "线程cpu时间占比",
        "cpu线程",
        "cpu 线程",
        "热点线程",
        "jvm线程",
        "jvm 线程",
        "profiler",
        "jstack",
        "gc 分析",
        "gc排查",
        "完整 spring boot actuator",
    )
    kept: list[str] = []
    for line in report.splitlines():
        lower = line.lower()
        if any(term in lower for term in forbidden):
            continue
        if context["category"] == "service_availability" and (
            "redis" in lower or "mysql" in lower
        ):
            continue
        kept.append(line)
    report = "\n".join(kept).strip()
    if not context["abnormal"]:
        return report

    if context["category"] == "dependency_availability":
        component = "Redis" if context["alert_name"] == "RedisUnavailable" else "MySQL"
        alert_summary = _extract_alert_summary(input_text)
        if component == "Redis":
            causes = (
                "1. Redis 实例停止。\n"
                "2. Redis 端口不可访问。\n"
                "3. Redis 认证配置错误。\n"
                "4. 网络连接异常。"
            )
            impact = (
                "- Redis 缓存访问失败。\n"
                "- 依赖 Redis 的接口可能报错、超时或降级。"
            )
        else:
            causes = (
                "1. MySQL 实例停止。\n"
                "2. 数据库端口不可访问。\n"
                "3. 数据库连接配置错误。\n"
                "4. 数据库连接资源耗尽。"
            )
            impact = (
                "- 数据查询可能失败。\n"
                "- 事务执行可能异常。\n"
                "- 数据库依赖当前不可用。"
            )
        facts = (
            f"- Prometheus 检测到 `{context['promql'] or 'probe_success'}=0`。\n"
            f"- {component} 健康检查失败，smartlife 当前无法正常访问{component}依赖。"
        )
        summary = (
            f"- **告警名称**：{context['alert_name']}\n"
            f"- **服务**：{alert_summary['service']}\n"
            f"- **实例**：{alert_summary['instance']}\n"
            "- **当前状态**：当前异常持续中\n"
            f"- **诊断结论**：Prometheus 检测到 {component} 健康检查失败，"
            f"smartlife 当前无法正常访问{component}依赖。"
        )
        root_confidence = (
            "- **依赖不可用判断可信度**：高（Prometheus 健康探测指标为 0）。\n"
            "- **具体根因定位可信度**：中（尚缺少组件日志和直连验证）。"
        )
    else:
        alert_summary = _extract_alert_summary(input_text)
        causes = (
            "1. 应用进程退出。\n"
            "2. Spring Boot 启动失败。\n"
            "3. 8081 端口未监听。\n"
            "4. 服务网络连接异常。"
        )
        facts = (
            f"- Prometheus 检测到 `{context['promql'] or 'up'}=0`。\n"
            "- SmartLife 服务当前不可抓取，服务存活或端口可达性异常。"
        )
        summary = (
            "- **告警名称**：SmartLifeServiceDown\n"
            f"- **服务**：{alert_summary['service']}\n"
            f"- **实例**：{alert_summary['instance']}\n"
            "- **当前状态**：当前异常持续中\n"
            "- **诊断结论**：Prometheus 检测到 smartlife 服务不可抓取。"
        )
        impact = (
            "- smartlife 服务可能无法访问。\n"
            "- API 请求可能失败或超时。\n"
            "- 上游服务调用可能受到影响。"
        )
        root_confidence = (
            "- **服务不可用判断可信度**：高（Prometheus up 指标为 0）。\n"
            "- **具体根因定位可信度**：中（尚缺少启动日志和端口直连验证）。"
        )
    root_cause = f"""## 5. 根因分析

### 已确认事实

{facts}

### 高概率原因

{causes}

### 可信度

{root_confidence}

### 证据限制

{_availability_evidence_limitations(input_text)}

"""
    report = _replace_report_section(report, 1, "故障摘要", summary)
    report = _replace_report_section(report, 2, "影响分析", impact)
    runbook_section = _availability_runbook_section(input_text, past_steps)
    if re.search(r"(?m)^### RAG Runbook\s*$", report):
        report = re.sub(
            r"(?ms)^### RAG Runbook\s*.*?(?=^## 5\.|\Z)",
            runbook_section + "\n\n",
            report,
            count=1,
        ).rstrip()
    elif re.search(r"(?m)^## 5\. 根因分析\s*$", report):
        report = re.sub(
            r"(?m)^## 5\. 根因分析\s*$",
            runbook_section + "\n\n## 5. 根因分析",
            report,
            count=1,
        )
    if re.search(r"(?m)^## 5\. 根因分析\s*$", report):
        return re.sub(
            r"(?ms)^## 5\. 根因分析\s*.*?(?=^## 6\.|\Z)",
            root_cause,
            report,
            count=1,
        ).rstrip()
    return report.rstrip() + "\n\n" + root_cause.rstrip()


def _ensure_mysql_slow_query_rag_section(report: str, input_text: str) -> str:
    """Guarantee domain-correct Runbook directions in SmartLifeMysqlSlowQueryHigh reports."""
    if "smartlifemysqlslowqueryhigh" not in input_text.lower():
        return report
    section = """### RAG Runbook

- **命中文档**：1.MySQL 慢 SQL 排查.md
- **提供排查方向**：
  - 检查慢查询日志，并使用 SHOW FULL PROCESSLIST 定位当前长时间执行的 SQL。
  - 使用 Performance Schema 汇总执行次数、累计耗时、扫描行数和锁等待。
  - 对目标 SQL 执行 EXPLAIN，完成索引分析并检查全表扫描、filesort 和临时表。
  - 结合锁等待、事务阻塞和索引设计制定 SQL 优化方案。

"""
    if re.search(r"(?m)^### RAG Runbook\s*$", report):
        return re.sub(
            r"(?ms)^### RAG Runbook\s*.*?(?=^## 5\.|\Z)",
            section,
            report,
            count=1,
        ).rstrip()
    if re.search(r"(?m)^## 5\. 根因分析\s*$", report):
        return re.sub(
            r"(?m)^## 5\. 根因分析\s*$",
            section + "## 5. 根因分析",
            report,
            count=1,
        )
    return report.rstrip() + "\n\n" + section.rstrip()


def _findall_unique(pattern: str, text: str) -> list[str]:
    import re

    seen: list[str] = []
    for match in re.findall(pattern, text):
        if match not in seen:
            seen.append(match)
    return seen


def _format_rag_evidence(past_steps: list[tuple], max_chars: int = 3000) -> str:
    """Extract explicit RAG evidence from retrieve_knowledge output."""
    sections: list[str] = []
    for step, result in past_steps:
        if "retrieve_knowledge" not in str(step) and "\u53c2\u8003\u8d44\u6599" not in str(result):
            continue
        text = str(result)
        sources = [source.strip() for source in _findall_unique("\u6765\u6e90:\\s*([^\\n\\r]+)", text)]
        if not sources:
            sources = [source.strip() for source in _findall_unique(r"_file_name['\"]?\s*[:=]\s*['\"]([^'\"]+)", text)]
        source_text = "\u3001".join(sources) if sources else "\u672a\u89e3\u6790\u5230\u6587\u4ef6\u540d"
        combined = f"{step}\n{text}".lower()
        if (
            "mysqlslowquery" in combined
            or "mysql慢sql" in combined
            or "mysql 慢 sql" in combined
            or "1.mysql 慢 sql 排查" in combined
        ):
            directions = [
                "检查慢查询日志，并使用 SHOW FULL PROCESSLIST 定位当前长时间执行的 SQL。",
                "使用 Performance Schema 汇总执行次数、累计耗时、扫描行数和锁等待。",
                "对目标 SQL 执行 EXPLAIN，检查索引使用、全表扫描、Using filesort 和临时表。",
                "结合锁等待、事务阻塞和索引设计制定 SQL 优化方案。",
            ]
        elif "cpu" in combined:
            directions = [
                "获取 CPU 热点线程。",
                "使用 jstack 将热点线程映射到业务调用栈。",
                "检查 GC、定时任务和 CPU 密集型计算任务。",
                "必要时使用 Profiler 进一步分析。",
            ]
        else:
            directions = [
                "按 Runbook 核对关键指标和实例状态。",
                "结合日志、线程或调用链补充验证。",
                "完成恢复操作后持续观察指标。",
            ]
        sections.append(
            "### RAG Runbook\n\n"
            f"- **命中文档**：{source_text}\n"
            "- **提供排查方向**：\n"
            + "\n".join(f"  - {direction}" for direction in directions)
        )
    result = "\n\n".join(sections) if sections else "### RAG Runbook\n\n- 未命中可用 Runbook。"
    return _truncate_text(result, max_chars)


def _format_jvm_thread_dump_evidence(past_steps: list[tuple]) -> str:
    """Extract a compact prioritized thread summary from JVM thread dump results."""
    import re

    ranked_threads: list[tuple[tuple[int, int], str, str, list[str]]] = []
    max_thread_groups = 4
    max_stack_frames = 2
    priority_name_keywords = ("cpu", "worker", "executor", "task", "pool")
    business_stack_keywords = (".app.", "/app/", "controller", "service")
    must_keep_keywords = ("consumecpu", "faulttestcontroller", "fault-cpu-simulator")
    filtered_runnable_count = 0

    def thread_priority(thread: dict[str, Any], frames: list[Any], index: int) -> tuple[int, int]:
        name = str(thread.get("name") or thread.get("threadName") or "").lower()
        state = str(thread.get("state", "")).upper()
        stack_text = "\n".join(str(frame) for frame in frames).lower()
        combined_text = f"{name}\n{stack_text}"

        if any(keyword in combined_text for keyword in must_keep_keywords):
            return 0, index
        if (
            any(keyword in name for keyword in priority_name_keywords)
            and any(keyword in stack_text for keyword in business_stack_keywords)
        ):
            return 1, index
        if state == "RUNNABLE":
            return 2, index
        return 3, index

    for step, result in past_steps:
        if "collect_jvm_thread_dump" not in str(step):
            continue

        parsed = _safe_json_loads(result)
        if not isinstance(parsed, dict) or parsed.get("success") is not True:
            continue

        threads = parsed.get("threads")
        if not isinstance(threads, list):
            continue

        for index, thread in enumerate(threads):
            if not isinstance(thread, dict):
                continue

            name = str(thread.get("name") or thread.get("threadName") or "unknown")
            state = str(thread.get("state") or thread.get("threadState") or "unknown").upper()
            stack_trace = thread.get("stacktrace", thread.get("stackTrace", []))
            frames = stack_trace if isinstance(stack_trace, list) else []
            if _is_ignored_jvm_thread(name, frames):
                if state == "RUNNABLE":
                    filtered_runnable_count += 1
                continue
            ranked_threads.append((
                thread_priority(thread, frames, index),
                name,
                state,
                [_truncate_text(str(frame), 240) for frame in frames[:max_stack_frames]],
            ))

    if not ranked_threads:
        return "未发现明确业务热点线程，需要结合日志和 Profiler 进一步分析。"
    ranked_threads.sort(key=lambda item: item[0])
    grouped: dict[tuple[str, str, str], list[str]] = {}
    group_frames: dict[tuple[str, str, str], list[str]] = {}
    for _priority, name, state, frames in ranked_threads:
        key_frame = frames[0] if frames else "无调用栈"
        name_pattern = re.sub(r"\d+$", "*", name)
        key = (name_pattern, state, key_frame)
        grouped.setdefault(key, []).append(name)
        group_frames.setdefault(key, frames)

    thread_sections: list[str] = []
    for key, names in list(grouped.items())[:max_thread_groups]:
        _pattern, state, _key_frame = key
        frames = group_frames[key]
        if len(names) > 1:
            name_text = f"{names[0]} ~ {names[-1]}"
            count_text = f"发现 {len(names)} 个同类 CPU 热点线程"
        else:
            name_text = names[0]
            count_text = "发现 1 个热点线程"
        stack_lines = "\n".join(f"  - `{frame}`" for frame in frames[:max_stack_frames])
        if not stack_lines:
            stack_lines = "  - 未获得调用栈。"
        section = (
            f"- **线程聚合**：{count_text}\n"
            f"- **线程名称**：{name_text}\n"
            f"- **线程状态**：{state}\n"
            f"- **调用栈均定位到**：\n{stack_lines}"
        )
        if len(names) > 1 and any("consumecpu" in frame.lower() for frame in frames):
            section += "\n- **证据判断**：多个线程同时执行 CPU 密集计算逻辑，是 CPU 升高的重要证据。"
        thread_sections.append(section)
    if filtered_runnable_count:
        thread_sections.append(
            "- **其他线程**：发现其他 RUNNABLE 线程，但未发现明确业务热点调用栈，不作为根因依据。"
        )
    return "\n\n".join(thread_sections)


def _llm_response_text(response: Any) -> str:
    """Extract text from string and block-based LangChain/provider responses."""
    if response is None:
        return ""
    output_text = getattr(response, "text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if isinstance(block, dict):
                value = block.get("text") or block.get("content") or block.get("output_text")
                if isinstance(value, str):
                    parts.append(value)
        return "\n".join(part.strip() for part in parts if part.strip()).strip()
    return str(content).strip() if content is not None else ""


async def _generate_response(state: PlanExecuteState, llm: Any | None = None) -> Dict[str, Any]:
    """Generate the final Markdown diagnosis report."""
    report_start = time.perf_counter()
    logger.info("Report node started")

    input_text = str(state.get("input", ""))
    past_steps = state.get("past_steps", [])
    fault_mapping = match_fault_mapping(input_text)
    report_policy = str((fault_mapping or {}).get("report_policy") or "legacy")
    logger.info(
        "Report policy selected: policy={}, source={}",
        report_policy,
        "fault_mapping.yaml" if fault_mapping else "legacy_python",
    )

    compact_input = _truncate_text(input_text, 1200)
    execution_summary = _format_report_step_summary(past_steps)
    compact_execution_summary = _truncate_text(execution_summary, 1800)
    prometheus_metric_evidence = _format_prometheus_metric_evidence(past_steps)
    trend_analysis = _format_trend_analysis(past_steps)
    evidence_chain = _format_evidence_chain(past_steps)
    mysql_slow_query_context = "smartlifemysqlslowqueryhigh" in input_text.lower()
    availability_context = _is_availability_context(input_text)
    missing_information = _format_missing_information(past_steps, input_text)
    rag_evidence = _format_rag_evidence(past_steps, max_chars=600)
    thread_dump_evidence = (
        ""
        if mysql_slow_query_context or availability_context
        else _format_jvm_thread_dump_evidence(past_steps)
    )
    jvm_memory_evidence = _format_jvm_memory_evidence(past_steps)
    mysql_slow_query_evidence = _format_mysql_slow_query_evidence(past_steps)
    logger.info(
        "Report received Prometheus metric evidence: chars={}, content={}",
        len(prometheus_metric_evidence),
        prometheus_metric_evidence[:3000],
    )
    logger.info(
        "Report received trend analysis: chars={}, content={}",
        len(trend_analysis),
        trend_analysis[:3000],
    )
    logger.info(
        "Report received RAG evidence: chars={}, content={}",
        len(rag_evidence),
        rag_evidence[:3000],
    )
    logger.info(
        "Report received structured evidence chain: chars={}, content={}",
        len(evidence_chain),
        evidence_chain[:3000],
    )

    availability_instruction = ""
    if availability_context:
        context = _report_context(input_text)
        if context["category"] == "dependency_availability":
            availability_instruction = """
    当前是外部依赖可用性故障。报告必须围绕 probe_success、依赖组件状态、连接失败的
    高概率原因和 Runbook 建议；probe_success=0 是已经确认的健康检查失败事实，缺少组件日志
    只限制具体原因定位，不能否定该事实。禁止输出 JVM、CPU、线程、jstack、Profiler、GC 或
    Spring Boot Actuator 专项分析。自动诊断 Step3 写依赖组件状态核对。
    """
        else:
            availability_instruction = """
    当前是 SmartLife 服务可用性故障。报告必须围绕 up 指标、服务存活、端口可达性、启动失败
    原因和恢复建议；up=0 是已经确认的服务不可抓取事实，缺少启动日志只限制具体原因定位。
    禁止输出 Redis/MySQL 依赖推断以及 JVM、CPU、线程、jstack、Profiler 或 GC 专项分析。
    自动诊断 Step3 写服务状态与端口证据核对。
    """

    report_instruction = f"""
    请生成企业级 AIOps 故障诊断报告，目标长度 1500～{config.aiops_report_max_chars} 字，
    绝对不得超过 {config.aiops_report_max_chars} 字。接近上限时压缩措辞、减少次要证据，
    但必须完整结束所有章节，禁止把句子或 Markdown 结构写到一半。

    只允许以下 Markdown 结构，章节各出现一次：

    # 故障诊断报告

    ## 1. 故障摘要
    必须包含告警名称、服务、实例、当前状态和一句话诊断结论。
    当前状态必须结合监控证据判断：Prometheus 指标仍超过阈值且没有恢复证据时，
    写“当前异常持续中”；指标已恢复但尚未收到 resolved 时，写
    “指标已恢复，等待 AlertManager 生命周期确认”。指标状态与告警生命周期状态必须分开表达，
    不得仅因缺少日志等旁证写“待确认”。

    ## 2. 影响分析
    简洁说明可能影响，以及当前无法确认的信息。

    ## 3. 自动诊断过程
    使用简短的 Step1～Step5 列表，依次说明：告警解析、Prometheus 指标查询、
    JVM/日志/线程分析、RAG Runbook、诊断结论。只描述做了什么及是否取得证据，
    不复制工具参数或原始返回。

    ## 4. 证据链分析
    仅保留决定结论的指标、线程、日志和 RAG 文档证据；去重并区分观测事实与
    Runbook 建议。使用且只使用一个 Markdown 表格，列固定为：
    来源 | 工具 | 指标 | 值 | 判断。不要输出 Prometheus JSON，不复制完整日志。
    同一指标只允许出现一次；判断必须是面向运维人员的中文结论，禁止复述工具描述。
    相同名称模式且调用栈一致的线程必须聚合为一行。

    在证据表后增加“### RAG Runbook”，列出命中文档和 2～4 条实际排查方向，
    不能只写“已完成 Runbook 检索”。

    ## 5. 根因分析
    必须依次包含三级标题：### 已确认事实、### 高概率原因、### 可信度、### 证据限制。
    可信度只能是高/中/低。信息不足时明确写明，禁止编造或把建议描述为已确认事实。
    {availability_instruction}
    存在 JVM 线程证据时，引用线程名、状态和一条关键调用栈并给出代码级判断。
    JVM 内部线程（Reference Handler、Finalizer、Signal Dispatcher、Attach Listener、
    JDWP*、Notification Thread、DestroyJavaVM）不得作为业务热点证据展示或参与根因判断。
    如果过滤后没有业务线程，明确写“未发现明确业务热点线程，需要结合日志和 Profiler 进一步分析”。
    相同线程名模式、相同状态和相同调用栈必须聚合，只展示线程数量、名称范围、状态和公共调用栈。
    当线程名、RUNNABLE 状态和业务方法栈能够相互印证时，应输出高概率原因；
    将尚未取得的 CPU 时间占比、调用触发源等写入证据限制，不得因此否定已有强证据。
    对 CPU 告警，应明确区分：CPU 指标、线程状态和调用栈属于已确认事实；
    “该计算逻辑导致 CPU 升高”属于有证据支持的高概率推断。
    当前 CPU 已恢复时，结论必须使用“曾出现 CPU 持续升高”和“本次 CPU 异常的高概率原因”，
    禁止继续描述为当前仍在持续升高。
    JVM 内存告警需要在本节区分内存泄漏、大对象分配、堆空间不足和 GC 压力。
    对 SmartLifeJvmMemoryHighUsage，必须基于 Heap Used/Max 使用率、fault_oom_injection_active、
    fault_oom_retained_bytes 和 GC 指标联合判断。当 Heap 使用率 > 90%、
    fault_oom_injection_active=1 且存在 retained bytes 指标时，必须将
    “持续对象保留造成 JVM Heap 压力升高并引发 OOM 风险”列为明确的高概率原因，
    可信度必须为“高”。缺少线程、日志、Profiler 或 Heap Dump 只能限制具体对象类型和
    代码位置的定位，不得否定上述高概率根因或将可信度降为低。
    对 SmartLifeMysqlSlowQueryHigh，必须基于 fault_mysql_slow_query_active、executions 和
    last_duration_milliseconds 联合判断。当 active=1 且 duration>1000ms 时，无论
    executions 趋势是否取得，都必须输出“检测到持续慢查询故障注入，高耗时SQL持续执行导致
    MySQL查询性能下降”，可信度为高。缺少具体SQL、慢查询日志和EXPLAIN只能作为进一步
    定位限制，不得输出“缺少代码级证据，无法判断”。
    SmartLifeMysqlSlowQueryHigh 报告禁止输出 JVM/CPU 专项排查内容，包括业务热点线程、
    jstack、GC、CPU线程分析和Profiler；这些内容不得出现在摘要、证据链、根因、限制或建议中。

    ## 6. 修复建议
    必须包含三级标题：### 立即处理、### 长期优化。
    JVM 内存告警的建议需覆盖 heap dump、MAT、对象引用和 -Xmx。
    建议必须针对已识别原因；定位到 consumeCpu 时，立即处理应覆盖停止异常计算任务、
    检查调用来源和必要时执行实例恢复，长期优化应覆盖线程池隔离、任务执行时间与
    资源限制，以及线程级 CPU 监控。

    Markdown 约束：
    - 输出必须是完整、合法 Markdown。
    - 禁止重复章节；除“证据链分析”的五列表格外禁止其他表格；禁止截断提示。
    - 禁止输出模型、重试、压缩、降级或长度控制等内部实现信息。
    - “### 证据限制”必须完整保留当前故障策略提供的限制项，不得混入其他故障类型的限制。
    - 禁止前言、后记、免责声明和代码围栏。
    - 优先保留诊断结论、关键证据、证据限制和可执行建议；压缩背景与重复内容。
    """
    messages = [
        ("user", f"告警上下文：\n{_truncate_text(compact_input, 900)}"),
        ("user", f"结构化证据：\n{_truncate_text(evidence_chain, 3500)}"),
        ("user", f"JVM内存证据：\n{'' if availability_context else jvm_memory_evidence}"),
        ("user", f"MySQL慢查询证据：\n{mysql_slow_query_evidence}"),
        ("user", f"JVM线程证据：\n{thread_dump_evidence}"),
        ("user", f"历史趋势：\n{_truncate_text(trend_analysis, 1800)}"),
        ("user", f"缺失证据：\n{_truncate_text(missing_information, 900)}"),
        ("user", f"Runbook 摘要（仅供建议，不要原文照抄）：\n{rag_evidence}"),
        ("user", f"Agent 执行步骤摘要：\n{compact_execution_summary}"),
        ("user", report_instruction),
    ]

    prompt_text = "\n\n".join(content for _, content in messages)
    approx_input_tokens = _estimate_tokens(prompt_text)
    logger.info(
        "Report input stats: input_chars={}, metric_evidence_chars={}, trend_chars={}, evidence_chain_chars={}, rag_evidence_chars={}, execution_summary_chars={}, total_chars={}, approx_tokens={}, steps={}, timeout={}s",
        len(compact_input),
        len(prometheus_metric_evidence),
        len(trend_analysis),
        len(evidence_chain),
        len(rag_evidence),
        len(compact_execution_summary),
        len(prompt_text),
        approx_input_tokens,
        len(past_steps),
        config.aiops_report_timeout,
    )
    if approx_input_tokens > 3000:
        logger.warning("Report prompt is still large: approx_tokens={}", approx_input_tokens)

    if llm is not None:
        model_candidates = [("injected-test-model", lambda: llm)]
    else:
        model_candidates = [
            (
                config.aiops_report_primary_model,
                lambda: llm_factory.create_chat_model(
                    model=config.aiops_report_primary_model,
                    temperature=0,
                    streaming=False,
                    max_tokens=config.aiops_report_max_tokens,
                    timeout=config.aiops_report_timeout,
                    max_retries=1,
                ),
            ),
            (
                config.aiops_report_secondary_model,
                lambda: llm_factory.create_chat_model(
                    model=config.aiops_report_secondary_model,
                    temperature=0,
                    base_url=llm_factory.DASHSCOPE_BASE_URL,
                    api_key=config.dashscope_api_key,
                    streaming=False,
                    max_tokens=config.aiops_report_max_tokens,
                    timeout=config.aiops_report_timeout,
                    max_retries=1,
                ),
            ),
        ]
    max_attempts = config.aiops_report_max_attempts
    failure_reason = "未知 Report LLM 错误"
    report_cancelled = False
    for model_index, (model_name, create_report_llm) in enumerate(model_candidates):
        try:
            report_llm = create_report_llm()
        except Exception as e:
            failure_reason = f"Report 模型初始化失败（{model_name}: {type(e).__name__}: {_truncate_text(str(e), 200)}）"
            logger.warning("[Report] model {} initialization failed: {}", model_name, format_exception_chain(e))
            if model_index + 1 < len(model_candidates):
                logger.warning("[Report] switching to {}", model_candidates[model_index + 1][0])
            continue
        response_chain = response_prompt | report_llm
        for attempt in range(1, max_attempts + 1):
            try:
                request_start = time.perf_counter()
                logger.info(
                    "Report LLM request sent: model={}, attempt={}/{}, elapsed_from_report_start={:.2f}s",
                    model_name,
                    attempt,
                    max_attempts,
                    request_start - report_start,
                )
                response_obj = await asyncio.wait_for(
                    response_chain.ainvoke({"messages": messages}),
                    timeout=config.aiops_report_timeout,
                )

                response_end = time.perf_counter()
                final_response = _llm_response_text(response_obj)
                if not final_response:
                    raise ValueError("Report LLM returned an empty response")
                final_response = _ensure_jvm_oom_sections(
                    final_response,
                    input_text,
                    past_steps,
                    jvm_memory_evidence,
                    trend_analysis,
                )
                final_response = _ensure_fixed_evidence_limitations(final_response, input_text)
                if len(final_response) > config.aiops_report_max_chars:
                    logger.info(
                        "[Report] output exceeds limit; requesting semantic compression: chars={}, limit={}",
                        len(final_response),
                        config.aiops_report_max_chars,
                    )
                    compression_messages = [
                        (
                            "user",
                            f"""请将下面的 AIOps 报告压缩到 {config.aiops_report_max_chars} 字以内。
必须保留原报告中的事实，不得新增推断。严格保留且只保留六个编号章节及其必要三级标题。
删除重复、背景和次要证据；保留“证据链分析”的五列表格，其他位置使用项目列表。
禁止截断提示和任何模型、重试、压缩、降级信息，确保句子和 Markdown 完整。
“证据限制”必须保留当前故障策略提供的限制项，不得混入其他故障类型内容。

原报告：
{final_response}""",
                        )
                    ]
                    compressed_obj = await asyncio.wait_for(
                        response_chain.ainvoke({"messages": compression_messages}),
                        timeout=config.aiops_report_timeout,
                    )
                    compressed = _llm_response_text(compressed_obj)
                    compressed = _ensure_fixed_evidence_limitations(compressed, input_text)
                    if (
                        compressed
                        and "### 证据限制" in compressed
                        and len(compressed) <= config.aiops_report_max_chars
                    ):
                        final_response = compressed
                    else:
                        logger.warning(
                            "[Report] semantic compression did not meet limit; rebuilding compact report"
                        )
                        final_response = _build_bounded_fallback_report(
                            input_text=input_text,
                            past_steps=past_steps,
                            evidence_chain=evidence_chain,
                            thread_dump_evidence=thread_dump_evidence,
                            missing_information=missing_information,
                            rag_evidence=rag_evidence,
                            max_chars=config.aiops_report_max_chars,
                        )
                final_response = _enforce_strong_jvm_oom_root_cause(
                    final_response,
                    input_text,
                    past_steps,
                )
                final_response = _enforce_strong_mysql_slow_query_root_cause(
                    final_response,
                    input_text,
                    past_steps,
                )
                final_response = _ensure_mysql_slow_query_rag_section(
                    final_response,
                    input_text,
                )
                final_response = _sanitize_mysql_slow_query_report(
                    final_response,
                    input_text,
                )
                final_response = _enforce_availability_report(
                    final_response,
                    input_text,
                    past_steps,
                )
                model_role = "primary" if model_index == 0 else "secondary"
                logger.info(
                    "[Report] {} model {} success; attempt={}, llm_elapsed={:.2f}s, total_elapsed={:.2f}s, output_chars={}",
                    model_role,
                    model_name,
                    attempt,
                    response_end - request_start,
                    response_end - report_start,
                    len(final_response),
                )
                return {"response": final_response}
            except asyncio.CancelledError as e:
                # Cancellation normally means the SSE client/proxy disconnected.
                failure_reason = "Report 阶段任务被取消（通常由 SSE 客户端或上游代理断开触发）"
                logger.warning("Report generation cancelled; returning fallback:\n{}", format_exception_chain(e))
                report_cancelled = True
                break
            except (asyncio.TimeoutError, TimeoutError) as e:
                failure_reason = f"Report 阶段 LLM 请求超过 {config.aiops_report_timeout:g} 秒超时限制"
                logger.warning(
                    "[Report] llm timeout; model={}, attempt={}/{}: {}",
                    model_name,
                    attempt,
                    max_attempts,
                    format_exception_chain(e),
                )
            except Exception as e:
                failure_reason = f"Report 阶段 LLM 请求异常（{type(e).__name__}: {_truncate_text(str(e), 240)}）"
                logger.warning("[Report] model {} failed attempt={}/{}: {}", model_name, attempt, max_attempts, format_exception_chain(e))

            if attempt < max_attempts:
                await asyncio.sleep(min(attempt, 2))

        logger.warning("[Report] primary model {} failed" if model_index == 0 else "[Report] {} failed", model_name)
        if report_cancelled:
            break
        if model_index + 1 < len(model_candidates):
            logger.warning("[Report] switching to {}", model_candidates[model_index + 1][0])

    logger.error("Report LLM exhausted retries; returning evidence-based fallback: {}", failure_reason)

    logger.warning("[Report] fallback activated")
    fallback_response = _build_bounded_fallback_report(
        input_text=input_text,
        past_steps=past_steps,
        evidence_chain=evidence_chain,
        thread_dump_evidence=thread_dump_evidence,
        missing_information=missing_information,
        rag_evidence=rag_evidence,
        max_chars=config.aiops_report_max_chars,
    )
    fallback_response = _enforce_strong_jvm_oom_root_cause(
        fallback_response,
        input_text,
        past_steps,
    )
    fallback_response = _enforce_strong_mysql_slow_query_root_cause(
        fallback_response,
        input_text,
        past_steps,
    )
    fallback_response = _ensure_mysql_slow_query_rag_section(
        fallback_response,
        input_text,
    )
    fallback_response = _sanitize_mysql_slow_query_report(
        fallback_response,
        input_text,
    )
    fallback_response = _enforce_availability_report(
        fallback_response,
        input_text,
        past_steps,
    )
    logger.warning(
        "[Report] fallback report generated from evidence chain: chars={}, reason={}",
        len(fallback_response),
        failure_reason,
    )
    return {"response": fallback_response}


def _format_simple_steps(past_steps: list) -> str:
    """Format step list for fallback responses."""
    if not past_steps:
        return "No executed steps."

    formatted = []
    for i, (step, result) in enumerate(past_steps, 1):
        result_preview = _truncate_text(str(result), 300)
        formatted.append(f"{i}. **{step}**\n   {result_preview}\n")

    return "\n".join(formatted)
