import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.runnables import RunnableLambda

from app.agent.aiops.executor import (
    _execute_deterministic_task,
    _execute_metric_queries,
    _extract_alert_window,
    _extract_promqls,
)
from app.agent.aiops.planner import (
    _alert_metric_plan,
    _extract_plan_json,
    _planner_response_metadata,
    _safe_default_plan,
    planner,
    planner_prompt,
)
from app.agent.aiops.replanner import _format_trend_analysis
from app.config import config
from app.tools.query_metrics_alerts import _infer_metric_info


class DiagnosticPlanTest(unittest.TestCase):
    def test_planner_recovers_fenced_or_surrounded_json(self):
        fenced = _extract_plan_json(
            'prefix\n```json\n{"steps":["query_prometheus_metrics", "retrieve_knowledge", "report"]}\n```'
        )
        self.assertIsNotNone(fenced)
        self.assertEqual(fenced.steps[0], "query_prometheus_metrics")

    def test_truncated_planner_json_uses_service_specific_default(self):
        self.assertIsNone(_extract_plan_json('{"'))
        plan = _safe_default_plan("- alertname: SmartLifeServiceDown")
        text = "\n".join(plan)
        self.assertIn('up{job="smartlife"}', text)
        self.assertIn("query_prometheus_range", text)
        self.assertIn("retrieve_knowledge", text)
        self.assertNotIn("process_cpu_usage", text)

    def test_planner_response_metadata_reads_finish_reason_and_tokens(self):
        response = SimpleNamespace(
            response_metadata={"finish_reason": "stop"},
            usage_metadata={"output_tokens": 37},
        )
        self.assertEqual(_planner_response_metadata(response), ("stop", 37))

    def test_mysql_slow_query_metric_units(self):
        active = _infer_metric_info(
            "fault_mysql_slow_query_active", "fault_mysql_slow_query_active", 1
        )
        executions = _infer_metric_info(
            "fault_mysql_slow_query_executions",
            "fault_mysql_slow_query_executions",
            12,
        )
        duration = _infer_metric_info(
            "fault_mysql_slow_query_last_duration_milliseconds",
            "fault_mysql_slow_query_last_duration_milliseconds",
            3500,
        )
        self.assertEqual((active["unit"], active["display_value"]), ("state", "active"))
        self.assertEqual(executions["unit"], "executions")
        self.assertEqual(duration["unit"], "ms")
        self.assertIn("3500.000 ms", duration["display_value"])

    def test_range_query_preserves_escaped_promql_quotes(self):
        task = 'Use query_prometheus_range. query="up{job=\\"smartlife\\"}", step="30s".'
        self.assertEqual(_extract_promqls(task, include_query_param=True), ['up{job="smartlife"}'])

    def test_range_query_preserves_plain_nested_promql(self):
        task = "Use query_prometheus_range. query='up{job=\"smartlife\"}', step='30s'."
        self.assertEqual(_extract_promqls(task, include_query_param=True), ['up{job="smartlife"}'])

    def test_smartlife_alert_window_uses_starts_at_minus_five_minutes(self):
        start, end = _extract_alert_window(
            "- alertname: SmartLifeServiceDown\n- start_time: 2026-07-24T01:00:00+08:00"
        )
        self.assertEqual(start, "2026-07-24T00:55:00+08:00")
        self.assertTrue(end.endswith("+08:00"))

    def test_smartlife_up_trend_marks_recovery(self):
        payload = json.dumps({
            "tool": "query_prometheus_range",
            "query": 'up{job="smartlife"}',
            "start": "2026-07-24T00:55:00+08:00",
            "end": "2026-07-24T01:10:00+08:00",
            "step": "30s",
            "results": [{
                "metric_name": "up",
                "promql": 'up{job="smartlife"}',
                "labels": {"job": "smartlife", "instance": "host.docker.internal:8081"},
                "values": [
                    {"timestamp": 1, "value": "1"},
                    {"timestamp": 2, "value": "0"},
                    {"timestamp": 3, "value": "1"},
                ],
                "summary": {"points": 3, "first_value": 1, "last_value": 1, "min_value": 0, "max_value": 1},
            }],
        })
        trend = _format_trend_analysis([("query_prometheus_range", payload)])
        self.assertIn("告警期间存在 up 从 1 变 0，当前已恢复为 1", trend)

    def test_planner_prompt_escapes_promql_label_selector(self):
        self.assertEqual(
            set(planner_prompt.input_variables),
            {"experience_context", "tools_description"},
        )
        self.assertNotIn('job="smartlife"', planner_prompt.input_variables)
        messages = planner_prompt.format_messages(
            tools_description="tools",
            experience_context="",
            messages=[("user", "- alertname: SmartLifeServiceDown")],
        )
        rendered = "\n".join(str(message.content) for message in messages)
        self.assertIn('up{job="smartlife"}', rendered)

    def test_high_cpu_plan_contains_cpu_jvm_gc_http_and_cpu_runbook(self):
        plan = _alert_metric_plan("- alertname: SmartLifeHighCPUUsage\n- description: CPU 使用率过高")
        text = "\n".join(plan)

        self.assertIn("process_cpu_usage", text)
        self.assertIn("jvm_threads_live_threads", text)
        self.assertIn("jvm_gc_pause_seconds_count", text)
        self.assertIn("http_server_requests_seconds_count", text)
        self.assertIn("Java process CPU high", text)

    def test_jvm_memory_high_usage_plan_contains_heap_metrics_trend_and_oom_runbook(self):
        plan = _alert_metric_plan("- alertname: SmartLifeJvmMemoryHighUsage\n- description: JVM Heap 使用率过高")
        text = "\n".join(plan)

        self.assertIn("jvm_memory_used_bytes", text)
        self.assertIn("jvm_memory_max_bytes", text)
        self.assertIn('sum(jvm_memory_used_bytes{job="smartlife",area="heap"})', text)
        self.assertIn('sum(jvm_memory_max_bytes{job="smartlife",area="heap"})', text)
        self.assertIn("fault_oom_injection_active", text)
        self.assertIn("fault_oom_retained_bytes", text)
        self.assertIn("query_prometheus_range", text)
        self.assertIn("JVM OutOfMemory", text)
        self.assertNotIn("process_cpu_usage", text)
        self.assertNotIn("collect_jvm_thread_dump", text)
        self.assertNotIn("promql='jvm_memory_used_bytes'", text)
        self.assertNotIn("promql='jvm_memory_max_bytes'", text)

    def test_mysql_slow_query_high_uses_exact_specialized_template(self):
        plan = _alert_metric_plan(
            "- alertname: SmartLifeMysqlSlowQueryHigh\n- description: injected slow SQL"
        )
        text = "\n".join(plan)

        self.assertIn("fault_mysql_slow_query_active", text)
        self.assertIn("fault_mysql_slow_query_executions", text)
        self.assertIn("fault_mysql_slow_query_last_duration_milliseconds", text)
        self.assertIn("query_prometheus_range", text)
        self.assertIn("minutes=10", text)
        self.assertIn("MySQL慢SQL排查", text)
        self.assertIn("Performance Schema", text)
        self.assertNotIn("process_cpu_usage", text)
        self.assertNotIn("jvm_memory_used_bytes", text)
        self.assertNotIn("collect_jvm_thread_dump", text)

        range_step = next(step for step in plan if "query_prometheus_range" in step)
        self.assertEqual(
            _extract_promqls(range_step, include_query_param=True),
            [
                "fault_mysql_slow_query_executions",
                "fault_mysql_slow_query_last_duration_milliseconds",
            ],
        )

    def test_aggregate_heap_promql_is_not_split_into_pool_query(self):
        task = (
            "Use query_prometheus_metrics. "
            "promql='sum(jvm_memory_used_bytes{job=\"smartlife\",area=\"heap\"})'."
        )
        self.assertEqual(
            _extract_promqls(task),
            ['sum(jvm_memory_used_bytes{job="smartlife",area="heap"})'],
        )

    def test_service_down_plan_does_not_contain_high_cpu_metrics(self):
        plan = _alert_metric_plan("- alertname: SmartLifeServiceDown\n- description: smartlife 服务不可用")
        text = "\n".join(plan)

        self.assertIn('up{job="smartlife"}', text)
        self.assertIn("Prometheus target/down alert state", text)
        self.assertIn("Spring Boot health", text)
        self.assertIn("服务日志", text)
        self.assertIn("服务启动失败", text)
        self.assertNotIn("process_cpu_usage", text)
        self.assertNotIn("jvm_threads_live_threads", text)

    def test_unknown_alert_uses_generic_description_fallback(self):
        plan = _alert_metric_plan("- alertname: CustomUnknownAlert\n- description: 自定义依赖异常")
        text = "\n".join(plan)

        self.assertIn('promql="up"', text)
        self.assertIn("通用故障排查", text)

    def test_jvm_memory_promqls_are_extracted_from_planner_step(self):
        task = (
            "Use query_prometheus_metrics. "
            "promql='jvm_memory_used_bytes', promql='jvm_memory_max_bytes'."
        )
        self.assertEqual(
            _extract_promqls(task),
            ["jvm_memory_used_bytes", "jvm_memory_max_bytes"],
        )


class PrometheusRangeFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def test_planner_structured_adapter_failure_recovers_raw_content(self):
        class RawRetryLLM(RunnableLambda):
            def __init__(self):
                super().__init__(
                    lambda _prompt: SimpleNamespace(
                        content='{"steps":["query_prometheus_metrics promql=\'probe_success{job=\\"smartlife-health-mysql\\"}\'", "retrieve_knowledge MySQL 服务不可用", "Generate final report"]}',
                        response_metadata={"finish_reason": "stop"},
                        usage_metadata={"output_tokens": 42},
                    )
                )

            def with_structured_output(self, _schema, *, include_raw=False):
                return RunnableLambda(
                    lambda _prompt: (_ for _ in ()).throw(
                        ValueError(
                            "Structured Output response does not have a 'parsed' field nor a 'refusal' field"
                        )
                    )
                )

        with patch(
            "app.agent.aiops.planner.get_mcp_client_with_retry",
            new=AsyncMock(return_value=object()),
        ), patch(
            "app.agent.aiops.planner.load_mcp_tools_safe",
            new=AsyncMock(return_value=([], "unavailable")),
        ), patch(
            "app.agent.aiops.planner.llm_factory.create_chat_model",
            return_value=RawRetryLLM(),
        ):
            result = await planner({"input": "- alertname: MysqlUnavailable"})

        text = "\n".join(result["plan"])
        self.assertIn('probe_success{job="smartlife-health-mysql"}', text)
        self.assertIn("MySQL 服务不可用", text)
        self.assertNotIn("process_cpu_usage", text)

    async def test_planner_empty_raw_retry_uses_redis_specific_fallback(self):
        class EmptyRawRetryLLM(RunnableLambda):
            def __init__(self):
                super().__init__(
                    lambda _prompt: SimpleNamespace(
                        content="",
                        response_metadata={"finish_reason": "stop"},
                        usage_metadata={"output_tokens": 0},
                    )
                )

            def with_structured_output(self, _schema, *, include_raw=False):
                return RunnableLambda(
                    lambda _prompt: (_ for _ in ()).throw(
                        ValueError("missing parsed/refusal")
                    )
                )

        with patch(
            "app.agent.aiops.planner.get_mcp_client_with_retry",
            new=AsyncMock(return_value=object()),
        ), patch(
            "app.agent.aiops.planner.load_mcp_tools_safe",
            new=AsyncMock(return_value=([], "unavailable")),
        ), patch(
            "app.agent.aiops.planner.llm_factory.create_chat_model",
            return_value=EmptyRawRetryLLM(),
        ):
            result = await planner({"input": "- alertname: RedisUnavailable"})

        text = "\n".join(result["plan"])
        self.assertIn('probe_success{job="smartlife-health-redis"}', text)
        self.assertIn("Redis 服务不可用", text)
        self.assertNotIn("process_cpu_usage", text)

    async def test_planner_incomplete_structured_json_continues_with_alert_plan(self):
        class IncompletePlannerLLM:
            structured_calls = 0

            def with_structured_output(self, _schema, *, include_raw=False):
                self.include_raw = include_raw
                def incomplete_response(_prompt):
                    self.structured_calls += 1
                    return {
                        "raw": SimpleNamespace(content='{"'),
                        "parsed": None,
                        "parsing_error": ValueError("EOF while parsing a string"),
                    }

                return RunnableLambda(incomplete_response)

        fake_llm = IncompletePlannerLLM()
        with patch(
            "app.agent.aiops.planner.get_mcp_client_with_retry",
            new=AsyncMock(return_value=object()),
        ), patch(
            "app.agent.aiops.planner.load_mcp_tools_safe",
            new=AsyncMock(return_value=([], "unavailable")),
        ), patch(
            "app.agent.aiops.planner.llm_factory.create_chat_model",
            return_value=fake_llm,
        ) as create_model:
            result = await planner({"input": "- alertname: SmartLifeServiceDown"})

        self.assertTrue(fake_llm.include_raw)
        self.assertEqual(fake_llm.structured_calls, config.aiops_planner_max_attempts)
        create_model.assert_called_once()
        self.assertEqual(
            create_model.call_args.kwargs["max_tokens"],
            config.aiops_planner_max_tokens,
        )
        self.assertEqual(
            create_model.call_args.kwargs["timeout"],
            config.aiops_planner_timeout,
        )
        self.assertEqual(create_model.call_args.kwargs["max_retries"], 1)
        self.assertFalse(create_model.call_args.kwargs["streaming"])
        text = "\n".join(result["plan"])
        self.assertIn('up{job="smartlife"}', text)
        self.assertIn("query_prometheus_range", text)
        self.assertIn("retrieve_knowledge", text)
        self.assertNotIn("process_cpu_usage", text)

    async def test_mysql_plan_executes_only_mysql_specialized_metrics(self):
        plan = _alert_metric_plan("- alertname: SmartLifeMysqlSlowQueryHigh")
        instant_step = next(step for step in plan if "query_prometheus_metrics" in step)
        range_step = next(step for step in plan if "query_prometheus_range" in step)
        instant_invoke = AsyncMock(
            return_value=json.dumps({"success": True, "results": [{"value": 1}]})
        )
        range_invoke = AsyncMock(return_value=json.dumps({"success": True, "results": []}))

        with patch(
            "app.agent.aiops.executor.query_prometheus_metrics",
            SimpleNamespace(ainvoke=instant_invoke),
        ), patch(
            "app.agent.aiops.executor.query_prometheus_range",
            SimpleNamespace(ainvoke=range_invoke),
        ):
            await _execute_deterministic_task(instant_step, "- alertname: SmartLifeMysqlSlowQueryHigh")
            await _execute_deterministic_task(range_step, "- alertname: SmartLifeMysqlSlowQueryHigh")

        instant_queries = {
            call.args[0]["promql"] for call in instant_invoke.await_args_list
        }
        range_queries = {
            call.args[0]["promql"] for call in range_invoke.await_args_list
        }
        self.assertEqual(instant_queries, {
            "fault_mysql_slow_query_active",
            "fault_mysql_slow_query_executions",
            "fault_mysql_slow_query_last_duration_milliseconds",
        })
        self.assertEqual(range_queries, {
            "fault_mysql_slow_query_executions",
            "fault_mysql_slow_query_last_duration_milliseconds",
        })
        combined = " ".join(instant_queries | range_queries).lower()
        self.assertNotIn("cpu", combined)
        self.assertNotIn("jvm", combined)
        self.assertNotIn("gc", combined)

    async def test_planner_step_reaches_range_tool_with_complete_label_matcher(self):
        range_invoke = AsyncMock(return_value=json.dumps({"success": True, "results": []}))
        range_tool = SimpleNamespace(ainvoke=range_invoke)
        task = next(
            step for step in _alert_metric_plan("- alertname: SmartLifeServiceDown")
            if "query_prometheus_range" in step
        )

        with patch("app.agent.aiops.executor.query_prometheus_range", range_tool):
            await _execute_deterministic_task(
                task,
                "- alertname: SmartLifeServiceDown\n- start_time: 2026-07-24T10:00:00Z",
            )

        sent = range_invoke.await_args.args[0]
        self.assertEqual(sent["promql"], 'up{job="smartlife"}')

    async def test_cpu_label_matcher_reaches_range_tool_unchanged(self):
        range_invoke = AsyncMock(return_value=json.dumps({"success": True, "results": []}))
        range_tool = SimpleNamespace(ainvoke=range_invoke)
        with patch("app.agent.aiops.executor.query_prometheus_range", range_tool):
            await _execute_deterministic_task(
                "Use query_prometheus_range. query='process_cpu_usage{job=\"smartlife\"}', minutes=10.",
                "- alertname: SmartLifeHighCPUUsage",
            )
        self.assertEqual(
            range_invoke.await_args.args[0]["promql"],
            'process_cpu_usage{job="smartlife"}',
        )

    async def test_planner_thread_dump_step_reaches_executor_tool(self):
        plan = _alert_metric_plan("- alertname: SmartLifeHighCPUUsage")
        thread_dump_step = next(step for step in plan if "collect_jvm_thread_dump" in step)
        thread_dump_result = {
            "success": True,
            "threads": [
                {"name": "worker-1", "state": "RUNNABLE", "stacktrace": ["at example.Worker.run"]}
            ],
        }
        thread_dump_invoke = AsyncMock(return_value=thread_dump_result)
        thread_dump_tool = SimpleNamespace(ainvoke=thread_dump_invoke)

        with patch("app.agent.aiops.executor.collect_jvm_thread_dump", thread_dump_tool):
            executed_step, result = await _execute_deterministic_task(
                thread_dump_step,
                "- alertname: SmartLifeHighCPUUsage",
            )

        self.assertEqual(executed_step, thread_dump_step)
        thread_dump_invoke.assert_awaited_once_with({})
        self.assertEqual(json.loads(result), thread_dump_result)

    async def test_empty_instant_query_triggers_range_fallback(self):
        instant = json.dumps(
            {"success": True, "query": "process_cpu_usage", "count": 0, "results": []}
        )
        historical = json.dumps(
            {
                "success": True,
                "tool": "query_prometheus_range",
                "query": "process_cpu_usage",
                "results": [{"summary": {"first_value": 0.02, "last_value": 0.88, "points": 20}}],
            }
        )
        instant_invoke = AsyncMock(return_value=instant)
        range_invoke = AsyncMock(return_value=historical)
        instant_tool = SimpleNamespace(ainvoke=instant_invoke)
        range_tool = SimpleNamespace(ainvoke=range_invoke)

        with patch("app.agent.aiops.executor.query_prometheus_metrics", instant_tool), patch(
            "app.agent.aiops.executor.query_prometheus_range", range_tool
        ):
            result = json.loads(await _execute_metric_queries(["process_cpu_usage"]))

        self.assertEqual(result["evidence_type"], "current_instant")
        self.assertEqual(result["results"][0]["count"], 0)
        self.assertEqual(len(result["historical_fallback"]), 1)
        trend = _format_trend_analysis(
            [("query_prometheus_metrics process_cpu_usage", json.dumps(result))]
        )
        self.assertIn("趋势分析", trend)
        self.assertIn("process_cpu_usage", trend)
        range_invoke.assert_awaited_once_with(
            {"promql": "process_cpu_usage", "minutes": 10, "step": "30s"}
        )
