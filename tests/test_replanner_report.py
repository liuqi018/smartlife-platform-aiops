import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.runnables import RunnableLambda

from app.agent.aiops.replanner import (
    _analyze_mysql_slow_query_evidence,
    _analyze_jvm_oom_evidence,
    _enforce_strong_mysql_slow_query_root_cause,
    _enforce_strong_jvm_oom_root_cause,
    _format_evidence_chain,
    _format_rag_evidence,
    _format_jvm_thread_dump_evidence,
    _generate_response,
    _llm_response_text,
)
from app.config import config


def _strong_oom_steps() -> list[tuple]:
    metrics = [
        ("jvm_memory_used_bytes", 3.664 * 1024**3),
        ("jvm_memory_max_bytes", 3.945 * 1024**3),
        ("fault_oom_injection_active", 1),
    ]
    steps = [
        (
            f"query_prometheus_metrics {name}",
            json.dumps({
                "tool": "query_prometheus_metrics",
                "success": True,
                "query": name,
                "results": [{
                    "metric_name": name,
                    "promql": name,
                    "value": value,
                }],
            }),
        )
        for name, value in metrics
    ]
    steps.append((
        "query_prometheus_range fault_oom_retained_bytes",
        json.dumps({
            "tool": "query_prometheus_range",
            "query": "fault_oom_retained_bytes",
            "start": "2026-07-29T10:00:00+08:00",
            "end": "2026-07-29T10:10:00+08:00",
            "results": [{
                "metric_name": "fault_oom_retained_bytes",
                "promql": "fault_oom_retained_bytes",
                "values": [
                    {"timestamp": 1, "value": 1.0 * 1024**3},
                    {"timestamp": 2, "value": 2.0 * 1024**3},
                    {"timestamp": 3, "value": 2.898 * 1024**3},
                ],
                "summary": {"last_value": 2.898 * 1024**3},
            }],
        }),
    ))
    return steps


class JvmOomRootCauseTest(unittest.TestCase):
    def test_strong_retention_evidence_overrides_low_confidence_conclusion(self):
        steps = _strong_oom_steps()
        analysis = _analyze_jvm_oom_evidence(steps)
        self.assertTrue(analysis["strong_retention_cause"])
        report = """# 故障诊断报告

## 5. 根因分析
### 已确认事实
- Heap 高。
### 高概率原因
- 当前没有足够的代码级证据，暂不指定唯一高概率原因。
### 可信度
- **低**
### 证据限制
- 缺少日志。

## 6. 修复建议
- 获取 Heap Dump。
"""
        corrected = _enforce_strong_jvm_oom_root_cause(
            report,
            "- alertname: SmartLifeJvmMemoryHighUsage",
            steps,
        )
        self.assertIn("持续对象保留", corrected)
        self.assertIn("**高**", corrected)
        self.assertIn("当前未获取 Heap Dump", corrected)
        self.assertNotIn("暂不指定唯一高概率原因", corrected)
        self.assertNotIn("**低**", corrected)


class MysqlSlowQueryRootCauseTest(unittest.TestCase):
    def test_mysql_runbook_with_cpu_word_uses_mysql_directions(self):
        rag = _format_rag_evidence([
            (
                'Use retrieve_knowledge. query="MySQL慢SQL排查 slow query EXPLAIN".',
                "来源: 1.MySQL 慢 SQL 排查.md\n"
                "慢 SQL 可能导致数据库 CPU 升高。使用 SHOW FULL PROCESSLIST、"
                "Performance Schema 和 EXPLAIN 检查索引与全表扫描。",
            )
        ])
        self.assertIn("慢查询日志", rag)
        self.assertIn("SHOW FULL PROCESSLIST", rag)
        self.assertIn("Performance Schema", rag)
        self.assertIn("EXPLAIN", rag)
        self.assertIn("全表扫描", rag)
        self.assertIn("Using filesort", rag)
        self.assertIn("锁等待", rag)
        self.assertNotIn("jstack", rag)
        self.assertNotIn("CPU 热点线程", rag)

    def test_active_and_high_duration_are_strong_without_execution_trend(self):
        steps = [
            (
                "query_prometheus_metrics mysql slow query",
                json.dumps({
                    "tool": "query_prometheus_metrics",
                    "success": True,
                    "mode": "parallel",
                    "results": [
                        {
                            "success": True,
                            "query": "fault_mysql_slow_query_active",
                            "results": [{
                                "metric_name": "fault_mysql_slow_query_active",
                                "promql": "fault_mysql_slow_query_active",
                                "value": 1,
                            }],
                        },
                        {
                            "success": True,
                            "query": "fault_mysql_slow_query_executions",
                            "results": [{
                                "metric_name": "fault_mysql_slow_query_executions",
                                "promql": "fault_mysql_slow_query_executions",
                                "value": 9,
                            }],
                        },
                        {
                            "success": True,
                            "query": "fault_mysql_slow_query_last_duration_milliseconds",
                            "results": [{
                                "metric_name": "fault_mysql_slow_query_last_duration_milliseconds",
                                "promql": "fault_mysql_slow_query_last_duration_milliseconds",
                                "value": 3016,
                            }],
                        },
                    ],
                }),
            ),
        ]
        analysis = _analyze_mysql_slow_query_evidence(steps)
        self.assertTrue(analysis["strong_slow_query_cause"])
        self.assertFalse(analysis["executions_growing"])

        corrected = _enforce_strong_mysql_slow_query_root_cause(
            "## 5. 根因分析\n\n### 高概率原因\n- 当前没有足够代码级证据。\n\n### 可信度\n- **低**",
            "- alertname: SmartLifeMysqlSlowQueryHigh",
            steps,
        )
        self.assertIn("高耗时SQL持续执行导致MySQL查询性能下降", corrected)
        self.assertIn("**高**", corrected)
        self.assertIn("当前为 9", corrected)
        self.assertNotIn("**低**", corrected)


class MysqlSlowQueryCompleteReportTest(unittest.IsolatedAsyncioTestCase):
    async def test_complete_report_contains_only_mysql_directions(self):
        async def cpu_contaminated_llm(_prompt):
            return """# 故障诊断报告

## 1. 故障摘要
- SmartLifeMysqlSlowQueryHigh

## 2. 影响分析
- MySQL查询变慢。

## 3. 自动诊断过程
- 已完成。

## 4. 证据链分析
- 使用 jstack 检查 CPU热点线程。
- 检查 GC，并使用 Profiler进一步分析。

## 5. 根因分析
### 已确认事实
- 指标异常。
### 高概率原因
- 当前没有足够代码级证据。
### 可信度
- **低**
### 证据限制
- 未发现明确业务热点线程。

## 6. 修复建议
- 继续排查。
"""

        state = {
            "input": "- alertname: SmartLifeMysqlSlowQueryHigh\n- service: smartlife",
            "plan": [],
            "response": "",
            "past_steps": [
                (
                    "query_prometheus_metrics MySQL",
                    json.dumps({
                        "tool": "query_prometheus_metrics",
                        "success": True,
                        "mode": "parallel",
                        "results": [
                            {"success": True, "query": "fault_mysql_slow_query_active", "results": [{
                                "metric_name": "fault_mysql_slow_query_active", "value": 1
                            }]},
                            {"success": True, "query": "fault_mysql_slow_query_executions", "results": [{
                                "metric_name": "fault_mysql_slow_query_executions", "value": 8
                            }]},
                            {"success": True, "query": "fault_mysql_slow_query_last_duration_milliseconds", "results": [{
                                "metric_name": "fault_mysql_slow_query_last_duration_milliseconds", "value": 3004
                            }]},
                        ],
                    }),
                ),
                (
                    'Use retrieve_knowledge query="MySQL慢SQL排查 slow query EXPLAIN"',
                    "来源: 1.MySQL 慢 SQL 排查.md\n"
                    "慢查询日志 SHOW FULL PROCESSLIST Performance Schema EXPLAIN "
                    "索引分析 全表扫描 filesort 锁等待",
                ),
            ],
        }

        report = (
            await _generate_response(state, RunnableLambda(cpu_contaminated_llm))
        )["response"]

        self.assertIn("检测到持续慢查询故障注入，高耗时SQL持续执行导致MySQL查询性能下降", report)
        self.assertIn("**高**", report)
        for expected in (
            "慢查询日志",
            "SHOW FULL PROCESSLIST",
            "Performance Schema",
            "EXPLAIN",
            "索引",
            "全表扫描",
            "filesort",
            "锁等待",
        ):
            self.assertIn(expected, report)
        for forbidden in (
            "未发现明确业务热点线程",
            "jstack",
            "检查 GC",
            "CPU热点线程",
            "Profiler",
        ):
            self.assertNotIn(forbidden, report)

    def test_strong_mysql_metrics_override_generic_low_confidence(self):
        steps = [
            (
                "query_prometheus_metrics mysql slow query",
                json.dumps({
                    "tool": "query_prometheus_metrics",
                    "success": True,
                    "mode": "parallel",
                    "results": [
                        {
                            "success": True,
                            "query": "fault_mysql_slow_query_active",
                            "results": [{
                                "metric_name": "fault_mysql_slow_query_active",
                                "promql": "fault_mysql_slow_query_active",
                                "value": 1,
                            }],
                        },
                        {
                            "success": True,
                            "query": "fault_mysql_slow_query_executions",
                            "results": [{
                                "metric_name": "fault_mysql_slow_query_executions",
                                "promql": "fault_mysql_slow_query_executions",
                                "value": 12,
                            }],
                        },
                        {
                            "success": True,
                            "query": "fault_mysql_slow_query_last_duration_milliseconds",
                            "results": [{
                                "metric_name": "fault_mysql_slow_query_last_duration_milliseconds",
                                "promql": "fault_mysql_slow_query_last_duration_milliseconds",
                                "value": 3500,
                            }],
                        },
                    ],
                }),
            ),
            (
                "query_prometheus_range fault_mysql_slow_query_executions",
                json.dumps({
                    "tool": "query_prometheus_range",
                    "query": "fault_mysql_slow_query_executions",
                    "start": "2026-07-29T10:00:00+08:00",
                    "end": "2026-07-29T10:10:00+08:00",
                    "results": [{
                        "metric_name": "fault_mysql_slow_query_executions",
                        "promql": "fault_mysql_slow_query_executions",
                        "values": [
                            {"timestamp": 1, "value": 3},
                            {"timestamp": 2, "value": 8},
                            {"timestamp": 3, "value": 12},
                        ],
                        "summary": {"last_value": 12},
                    }],
                }),
            ),
        ]
        analysis = _analyze_mysql_slow_query_evidence(steps)
        self.assertTrue(analysis["strong_slow_query_cause"])

        report = """# 故障诊断报告

## 5. 根因分析
### 已确认事实
- 指标异常。
### 高概率原因
- 缺少代码级证据，无法判断。
### 可信度
- **低**

## 6. 修复建议
- 继续排查。
"""
        corrected = _enforce_strong_mysql_slow_query_root_cause(
            report,
            "- alertname: SmartLifeMysqlSlowQueryHigh",
            steps,
        )
        self.assertIn("MySQL查询性能下降", corrected)
        self.assertIn("**高**", corrected)
        self.assertIn("当前未获取具体SQL", corrected)
        self.assertIn("当前未获取慢查询日志", corrected)
        self.assertIn("当前未获取EXPLAIN执行计划", corrected)
        self.assertNotIn("缺少代码级证据，无法判断", corrected)
        self.assertNotIn("**低**", corrected)


class JvmOomNoTrendTest(unittest.TestCase):
    def test_retained_metric_exists_without_trend_is_still_strong_evidence(self):
        steps = _strong_oom_steps()[:-1]
        steps.append((
            "query_prometheus_metrics fault_oom_retained_bytes",
            json.dumps({
                "tool": "query_prometheus_metrics",
                "success": True,
                "query": "fault_oom_retained_bytes",
                "results": [{
                    "metric_name": "fault_oom_retained_bytes",
                    "promql": "fault_oom_retained_bytes",
                    "value": 2.898 * 1024**3,
                }],
            }),
        ))
        analysis = _analyze_jvm_oom_evidence(steps)
        self.assertTrue(analysis["strong_retention_cause"])
        self.assertFalse(analysis["retained_growing"])

        corrected = _enforce_strong_jvm_oom_root_cause(
            "## 5. 根因分析\n\n### 高概率原因\n- 证据不足。\n\n### 可信度\n- **低**",
            "- alertname: SmartLifeJvmMemoryHighUsage",
            steps,
        )
        self.assertIn("存在 retained bytes 保留量", corrected)
        self.assertIn("**高**", corrected)
        self.assertNotIn("**低**", corrected)


def _report_state() -> dict:
    metric_result = {
        "tool": "query_prometheus_metrics",
        "success": True,
        "query": "process_cpu_usage",
        "results": [
            {
                "metric_name": "process_cpu_usage",
                "labels": {"instance": "smartlife:8081"},
                "value": 0.91,
                "display_value": "91%",
                "unit": "percent",
                "timestamp": "2026-07-23T10:00:00+08:00",
            }
        ],
    }
    return {
        "input": """## 告警上下文
- alertname: SmartLifeHighCPUUsage
- severity: warning
- service: smartlife
- instance: smartlife:8081
- start_time: 2026-07-23T10:00:00+08:00
""",
        "plan": [],
        "past_steps": [
            ("Use query_prometheus_metrics(promql='process_cpu_usage')", metric_result),
            ("Use retrieve_knowledge(query='SmartLifeHighCPUUsage Runbook')", "来源: CPU Runbook.md\n检查高 CPU 线程。"),
        ],
        "response": "",
    }


class ReportFallbackTest(unittest.IsolatedAsyncioTestCase):
    def test_llm_response_text_supports_content_blocks(self):
        response = SimpleNamespace(
            content=[
                {"type": "text", "text": "# 故障诊断报告"},
                {"type": "text", "text": "\n报告内容。"},
            ]
        )

        self.assertEqual(_llm_response_text(response), "# 故障诊断报告\n报告内容。")

    async def test_jvm_thread_dump_evidence_enters_report_prompt(self):
        captured_prompt = ""

        async def capture_llm(prompt):
            nonlocal captured_prompt
            captured_prompt = "\n".join(message.content for message in prompt.to_messages())
            return "# 故障诊断报告\n\n已引用 JVM 线程证据。"

        state = _report_state()
        state["past_steps"].append(
            (
                "Use collect_jvm_thread_dump to inspect CPU hotspots",
                json.dumps(
                    {
                        "success": True,
                        "threads": [
                            {
                                "name": "http-nio-8081-exec-7",
                                "state": "RUNNABLE",
                                "threadId": 47,
                                "stacktrace": [
                                    "at com.example.OrderService.calculate(OrderService.java:81)",
                                    "at com.example.OrderController.create(OrderController.java:42)",
                                    "at java.lang.Thread.run(Thread.java:840)",
                                    "at ignored.Frame.four(Frame.java:4)",
                                    "at ignored.Frame.five(Frame.java:5)",
                                    "at ignored.Frame.six(Frame.java:6)",
                                ],
                            },
                            {"name": "waiting-thread", "state": "WAITING", "stacktrace": ["ignored"]},
                        ],
                    }
                ),
            )
        )

        await _generate_response(state, RunnableLambda(capture_llm))

        self.assertIn("JVM线程证据", captured_prompt)
        self.assertIn("http-nio-8081-exec-7", captured_prompt)
        self.assertIn("**线程状态**：RUNNABLE", captured_prompt)
        self.assertIn("OrderService.calculate", captured_prompt)
        self.assertNotIn("ignored.Frame.six", captured_prompt)

    async def test_jvm_thread_dump_prefers_fault_cpu_simulator_over_ordinary_runnable_threads(self):
        captured_prompt = ""

        async def capture_llm(prompt):
            nonlocal captured_prompt
            captured_prompt = "\n".join(message.content for message in prompt.to_messages())
            return "# 故障诊断报告\n\n已引用 JVM 线程证据。"

        ordinary_threads = [
            {
                "name": "Reference Handler" if index == 0 else f"RMI TCP Accept-{index}",
                "state": "RUNNABLE",
                "threadId": index,
                "stacktrace": [
                    "at java.lang.ref.Reference.waitForReferencePendingList(Native Method)",
                    "at java.lang.Thread.run(Thread.java:840)",
                ],
            }
            for index in range(8)
        ]
        state = _report_state()
        state["past_steps"].append(
            (
                "Use collect_jvm_thread_dump to inspect CPU hotspots",
                json.dumps(
                    {
                        "success": True,
                        "threads": ordinary_threads
                        + [
                            {
                                "name": "fault-cpu-simulator-0",
                                "state": "RUNNABLE",
                                "threadId": 99,
                                "stacktrace": [
                                    "at com.smartlife.controller.FaultTestController.consumeCpu(FaultTestController.java:41)",
                                    "at com.smartlife.service.FaultService.trigger(FaultService.java:22)",
                                ],
                            }
                        ],
                    }
                ),
            )
        )

        await _generate_response(state, RunnableLambda(capture_llm))

        self.assertIn("fault-cpu-simulator-0", captured_prompt)
        self.assertIn("FaultTestController.consumeCpu", captured_prompt)

    async def test_jvm_oom_report_includes_memory_evidence_and_actions(self):
        async def minimal_llm(_prompt):
            return "# 故障诊断报告\n\n模型输出未包含 OOM 专用章节。"

        state = _report_state()
        state["input"] = """## 告警上下文
- alertname: SmartLifeJvmMemoryHighUsage
- severity: warning
- service: smartlife
- instance: smartlife:8081
- start_time: 2026-07-27T10:00:00+08:00
"""
        state["past_steps"] = [
            (
                "Use query_prometheus_metrics for JVM Heap",
                json.dumps(
                    {
                        "success": True,
                        "tool": "query_prometheus_metrics",
                        "mode": "parallel",
                        "results": [
                            {
                                "success": True,
                                "query": "jvm_memory_used_bytes",
                                "results": [
                                    {
                                        "metric_name": "jvm_memory_used_bytes",
                                        "promql": "jvm_memory_used_bytes",
                                        "labels": {"area": "heap", "id": "G1 Old Gen"},
                                        "value": "966367642",
                                        "display_value": "921.600 MB",
                                        "unit": "bytes",
                                    }
                                ],
                            },
                            {
                                "success": True,
                                "query": "jvm_memory_max_bytes",
                                "results": [
                                    {
                                        "metric_name": "jvm_memory_max_bytes",
                                        "promql": "jvm_memory_max_bytes",
                                        "labels": {"area": "heap", "id": "G1 Old Gen"},
                                        "value": "1073741824",
                                        "display_value": "1.000 GB",
                                        "unit": "bytes",
                                    }
                                ],
                            },
                        ],
                    }
                ),
            ),
            (
                "Use query_prometheus_range. query='jvm_memory_used_bytes{area=\"heap\"}'",
                json.dumps(
                    {
                        "success": True,
                        "tool": "query_prometheus_range",
                        "query": 'jvm_memory_used_bytes{area="heap"}',
                        "start": "2026-07-27T09:50:00+08:00",
                        "end": "2026-07-27T10:00:00+08:00",
                        "step": "30s",
                        "results": [
                            {
                                "metric_name": "jvm_memory_used_bytes",
                                "promql": 'jvm_memory_used_bytes{area="heap"}',
                                "labels": {"area": "heap", "id": "G1 Old Gen"},
                                "values": [
                                    {"timestamp": 1, "value": "536870912"},
                                    {"timestamp": 2, "value": "751619276"},
                                    {"timestamp": 3, "value": "966367642"},
                                ],
                                "summary": {
                                    "points": 3,
                                    "first_value": 536870912,
                                    "last_value": 966367642,
                                    "min_value": 536870912,
                                    "max_value": 966367642,
                                    "last_display_value": "921.600 MB",
                                    "max_display_value": "921.600 MB",
                                    "min_display_value": "512.000 MB",
                                    "unit": "bytes",
                                },
                            }
                        ],
                    }
                ),
            ),
            ("Use retrieve_knowledge", "来源: JVM OOM Runbook.md\n获取heap dump并使用MAT分析。"),
        ]

        result = await _generate_response(state, RunnableLambda(minimal_llm))
        report = result["response"]

        self.assertIn("## JVM内存证据", report)
        self.assertIn("Heap Used", report)
        self.assertIn("Heap Max", report)
        self.assertIn("Heap使用率", report)
        self.assertIn("持续增长", report)
        self.assertIn("内存泄漏", report)
        self.assertIn("大对象分配", report)
        self.assertIn("堆空间不足", report)
        self.assertIn("GC压力", report)
        self.assertIn("heap dump", report)
        self.assertIn("MAT", report)
        self.assertIn("-Xmx", report)

    async def test_generate_response_returns_fallback_on_timeout(self):
        async def slow_llm(_prompt):
            await asyncio.sleep(1)

        original_timeout = config.aiops_report_timeout
        config.aiops_report_timeout = 0.01
        try:
            with patch("app.agent.aiops.replanner.asyncio.sleep", new=AsyncMock()):
                result = await _generate_response(_report_state(), RunnableLambda(slow_llm))
        finally:
            config.aiops_report_timeout = original_timeout

        report = result["response"]
        self.assertIn("## 1. 故障摘要", report)
        self.assertIn("SmartLifeHighCPUUsage", report)
        self.assertIn("process_cpu_usage", report)
        self.assertIn("query_prometheus_metrics", report)
        self.assertIn("91%", report)
        self.assertNotIn("超时限制", report)
        self.assertIn("| 来源 | 工具 | 指标 | 值 | 判断 |", report)
        self.assertLessEqual(len(report), config.aiops_report_max_chars + 30)

    async def test_generate_response_returns_fallback_on_cancellation(self):
        async def cancelled_llm(_prompt):
            raise asyncio.CancelledError()

        result = await _generate_response(_report_state(), RunnableLambda(cancelled_llm))

        report = result["response"]
        self.assertIn("SmartLifeHighCPUUsage", report)
        self.assertNotIn("任务被取消", report)
        self.assertIn("## 6. 修复建议", report)

    async def test_generate_response_returns_fallback_on_llm_request_error(self):
        async def failed_llm(_prompt):
            raise OSError("TLS read failed")

        with patch("app.agent.aiops.replanner.asyncio.sleep", new=AsyncMock()):
            result = await _generate_response(_report_state(), RunnableLambda(failed_llm))

        report = result["response"]
        self.assertIn("SmartLifeHighCPUUsage", report)
        self.assertNotIn("OSError: TLS read failed", report)
        self.assertIn("## 1. 故障摘要", report)
        self.assertIn("## 4. 证据链分析", report)
        self.assertIn("## 5. 根因分析", report)
        self.assertIn("## 6. 修复建议", report)

    async def test_generate_response_retries_twice_then_succeeds(self):
        attempts = 0

        async def flaky_llm(_prompt):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise OSError("temporary TLS read failed")
            return "# AIOps 告警诊断报告\n\n第三次调用成功。"

        with patch("app.agent.aiops.replanner.asyncio.sleep", new=AsyncMock()):
            result = await _generate_response(_report_state(), RunnableLambda(flaky_llm))

        self.assertEqual(attempts, 3)
        self.assertIn("第三次调用成功", result["response"])

    async def test_generate_response_switches_to_secondary_model(self):
        async def primary_failure(_prompt):
            raise OSError("primary unavailable")

        async def secondary_success(_prompt):
            return "# 故障摘要\n\n- 当前状态：已获得证据\n\n# 关键证据\n\n| 指标 | 当前值 | 判断 |\n|---|---|---|\n| CPU | 91% | 高 |\n\n# 根因分析\n\n- 可能原因：待验证\n\n# 处理建议\n\n1. 检查线程。"

        models = [RunnableLambda(primary_failure), RunnableLambda(secondary_success)]
        with patch(
            "app.agent.aiops.replanner.llm_factory.create_chat_model",
            side_effect=models,
        ), patch("app.agent.aiops.replanner.asyncio.sleep", new=AsyncMock()):
            result = await _generate_response(_report_state())

        self.assertIn("当前状态：已获得证据", result["response"])
        self.assertLessEqual(len(result["response"]), config.aiops_report_max_chars + 30)

    async def test_oversized_report_is_semantically_compressed(self):
        calls = 0
        compact_report = """# 故障诊断报告

## 1. 故障摘要
- 告警名称：SmartLifeHighCPUUsage
- 服务：smartlife
- 实例：8081
- 当前状态：仍异常
- 诊断结论：CPU 使用率异常。

## 2. 影响分析
- 服务响应可能变慢；用户影响范围待确认。

## 3. 自动诊断过程
- Step1：告警解析。
- Step2：查询指标。
- Step3：分析线程。
- Step4：检索 Runbook。
- Step5：生成结论。

## 4. 证据链分析
- CPU 为 91%。

## 5. 根因分析
### 已确认事实
- CPU 指标异常。
### 高概率原因
- 高 CPU 线程待确认。
### 可信度
- 中
### 证据限制
- 缺少完整线程栈。

## 6. 修复建议
### 立即处理
- 限流并检查热点线程。
### 长期优化
- 增加线程监控。
"""

        async def oversized_then_compact(_prompt):
            nonlocal calls
            calls += 1
            return ("重复内容。" * 1000) if calls == 1 else compact_report

        result = await _generate_response(
            _report_state(), RunnableLambda(oversized_then_compact)
        )

        self.assertEqual(calls, 2)
        self.assertIn("CPU 使用率异常", result["response"])
        self.assertIn("当前未接入完整 Spring Boot Actuator 数据", result["response"])
        self.assertLessEqual(len(result["response"]), config.aiops_report_max_chars)
        self.assertNotIn("截断", result["response"])

    async def test_failed_compression_rebuilds_complete_markdown(self):
        async def always_oversized(_prompt):
            return "未压缩内容。" * 1000

        original_limit = config.aiops_report_max_chars
        config.aiops_report_max_chars = 1000
        try:
            result = await _generate_response(
                _report_state(), RunnableLambda(always_oversized)
            )
        finally:
            config.aiops_report_max_chars = original_limit

        report = result["response"]
        self.assertLessEqual(len(report), 1000)
        self.assertNotIn("截断", report)
        for section in range(1, 7):
            self.assertEqual(report.count(f"## {section}."), 1)
        self.assertIn("### 立即处理", report)
        self.assertIn("### 长期优化", report)

    async def test_fallback_uses_strong_thread_evidence_for_probable_cause(self):
        async def failed_llm(_prompt):
            raise OSError("internal failure")

        state = _report_state()
        state["past_steps"].append((
            "Use collect_jvm_thread_dump to inspect CPU hotspots",
            json.dumps({
                "success": True,
                "threads": [{
                    "name": f"fault-cpu-simulator-{index}",
                    "state": "RUNNABLE",
                    "threadId": 99 + index,
                    "stacktrace": [
                        "at com.smartlife.controller.fault.FaultTestController.consumeCpu(FaultTestController.java:217)"
                    ],
                } for index in range(30)],
            }),
        ))

        with patch("app.agent.aiops.replanner.asyncio.sleep", new=AsyncMock()):
            report = (
                await _generate_response(state, RunnableLambda(failed_llm))
            )["response"]

        self.assertIn("fault-cpu-simulator-0", report)
        self.assertIn("fault-cpu-simulator-29", report)
        self.assertIn("发现 30 个同类 CPU 热点线程", report)
        self.assertIn("RUNNABLE", report)
        self.assertIn("consumeCpu", report)
        self.assertIn("CPU 使用率升高", report)
        self.assertIn("### 证据限制", report)
        self.assertIn("**高**", report)
        self.assertIn("停止异常 CPU 计算任务", report)
        self.assertIn("线程池隔离", report)
        self.assertIn("执行实例恢复操作", report)
        self.assertNotIn("internal failure", report)

    async def test_fallback_status_uses_active_cpu_evidence(self):
        async def failed_llm(_prompt):
            raise OSError("internal failure")

        with patch("app.agent.aiops.replanner.asyncio.sleep", new=AsyncMock()):
            report = (
                await _generate_response(_report_state(), RunnableLambda(failed_llm))
            )["response"]

        self.assertIn(
            "**当前状态**：当前异常持续中",
            report,
        )
        self.assertIn("CPU持续超过阈值，确认存在CPU压力", report)
        self.assertIn("当前未接入应用日志查询能力", report)
        self.assertIn("缺少线程 CPU 时间占比数据", report)
        self.assertIn("### RAG Runbook", report)
        self.assertIn("获取 CPU 热点线程", report)
        self.assertNotIn("Application logs are not connected", report)

    async def test_internal_jvm_threads_are_filtered_from_report(self):
        async def failed_llm(_prompt):
            raise OSError("internal failure")

        state = _report_state()
        state["past_steps"].append((
            "Use collect_jvm_thread_dump to inspect CPU hotspots",
            json.dumps({
                "success": True,
                "threads": [
                    {"name": name, "state": "RUNNABLE", "stacktrace": ["JVM internal"]}
                    for name in [
                        "Reference Handler",
                        "Finalizer",
                        "Signal Dispatcher",
                        "Attach Listener",
                        "JDWP Transport Listener",
                        "JDWP Command Reader",
                        "Notification Thread",
                        "DestroyJavaVM",
                    ]
                ],
            }),
        ))
        with patch("app.agent.aiops.replanner.asyncio.sleep", new=AsyncMock()):
            report = (
                await _generate_response(state, RunnableLambda(failed_llm))
            )["response"]

        self.assertIn("未发现明确业务热点线程", report)
        self.assertNotIn("Reference Handler", report)
        self.assertNotIn("JDWP Transport Listener", report)

    async def test_fallback_distinguishes_recovered_metric_from_alert_lifecycle(self):
        async def failed_llm(_prompt):
            raise OSError("internal failure")

        state = _report_state()
        metric = state["past_steps"][0][1]["results"][0]
        metric["value"] = 0.30
        metric["display_value"] = "30%"
        state["past_steps"].append((
            "Use collect_jvm_thread_dump to inspect CPU hotspots",
            json.dumps({
                "success": True,
                "threads": [{
                    "name": "fault-cpu-simulator-0",
                    "state": "RUNNABLE",
                    "stacktrace": [
                        "at com.smartlife.controller.fault.FaultTestController.consumeCpu(FaultTestController.java:217)"
                    ],
                }],
            }),
        ))
        with patch("app.agent.aiops.replanner.asyncio.sleep", new=AsyncMock()):
            report = (
                await _generate_response(state, RunnableLambda(failed_llm))
            )["response"]

        self.assertIn(
            "**当前状态**：指标已恢复，等待 AlertManager 生命周期确认",
            report,
        )
        self.assertIn("检测到 Java 进程曾出现 CPU 持续升高", report)
        self.assertIn("本次 CPU 异常的高概率原因", report)

    def test_evidence_table_is_human_readable_and_deduplicates_metrics(self):
        state = _report_state()
        state["past_steps"].append(state["past_steps"][0])

        evidence = _format_evidence_chain(state["past_steps"])

        self.assertIn("| 来源 | 工具 | 指标 | 值 | 判断 |", evidence)
        self.assertIn("CPU持续超过阈值，确认存在CPU压力", evidence)
        self.assertEqual(evidence.count("process_cpu_usage"), 1)

    def test_gc_evidence_explains_low_activity(self):
        past_steps = [(
            "Use query_prometheus_metrics for GC",
            {
                "tool": "query_prometheus_metrics",
                "success": True,
                "query": "rate(jvm_gc_pause_seconds_count[5m])",
                "results": [{
                    "metric_name": "jvm_gc_pause_seconds_count",
                    "value": 0.02,
                    "display_value": "0.02 events/s",
                }],
            },
        )]

        evidence = _format_evidence_chain(past_steps)

        self.assertIn(
            "GC活动较低，当前无明显频繁GC证据，不支持GC导致CPU升高",
            evidence,
        )

    def test_report_thread_formatter_excludes_framework_threads(self):
        result = json.dumps({
            "success": True,
            "threads": [
                {
                    "name": "fault-cpu-simulator-0",
                    "state": "RUNNABLE",
                    "stacktrace": [
                        "at com.smartlife.controller.fault.FaultTestController.consumeCpu(FaultTestController.java:217)"
                    ],
                },
                {
                    "name": "RMI TCP Accept-0",
                    "state": "RUNNABLE",
                    "stacktrace": ["at sun.rmi.transport.tcp.TCPTransport$AcceptLoop.executeAcceptLoop"],
                },
                {
                    "name": "Catalina-utility-1",
                    "state": "RUNNABLE",
                    "stacktrace": ["at org.apache.catalina.core.ContainerBase.threadStart"],
                },
                {
                    "name": "Jndi-Dns-address-change-listener",
                    "state": "RUNNABLE",
                    "stacktrace": ["at sun.net.dns.ResolverConfigurationImpl.notifyAddrChange0"],
                },
            ],
        })

        evidence = _format_jvm_thread_dump_evidence([
            ("Use collect_jvm_thread_dump to inspect CPU hotspots", result)
        ])

        self.assertIn("fault-cpu-simulator-0", evidence)
        self.assertNotIn("RMI TCP Accept-0", evidence)
        self.assertNotIn("Catalina-utility-1", evidence)
        self.assertNotIn("Jndi-Dns-address-change-listener", evidence)
        self.assertIn("不作为根因依据", evidence)
