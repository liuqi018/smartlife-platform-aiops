import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agent.aiops.executor import _execute_batch
from app.agent.aiops.planner import _alert_metric_plan
from app.agent.aiops.replanner import (
    _enforce_availability_report,
    _format_evidence_chain,
    _format_trend_analysis,
)
from app.models.alert import parse_alertmanager_payload
from app.services.aiops_service import aiops_service


FIXTURES = Path(__file__).parent / "fixtures"


class DependencyUnavailableClosureTest(unittest.IsolatedAsyncioTestCase):
    CASES = (
        (
            "alertmanager_redis_unavailable.json",
            "RedisUnavailable",
            'probe_success{job="smartlife-health-redis"}',
            "Redis 服务不可用排查",
            "09:55:00+08:00",
        ),
        (
            "alertmanager_mysql_unavailable.json",
            "MysqlUnavailable",
            'probe_success{job="smartlife-health-mysql"}',
            "MySQL 服务不可用排查",
            "10:00:00+08:00",
        ),
    )

    async def test_dependency_alerts_use_configured_availability_closure(self):
        for fixture_name, alert_name, promql, runbook_name, expected_start in self.CASES:
            with self.subTest(alert_name=alert_name):
                payload = json.loads(
                    (FIXTURES / fixture_name).read_text(encoding="utf-8")
                )
                alert = parse_alertmanager_payload(payload)[0]
                task = aiops_service.build_alert_diagnosis_task(alert)
                plan = _alert_metric_plan(task)
                plan_text = "\n".join(plan)

                self.assertEqual(alert.alert_name, alert_name)
                self.assertIn(promql, plan_text)
                self.assertIn("startsAt minus 5 minutes", plan_text)
                self.assertIn(runbook_name.split()[0], plan_text)
                self.assertNotIn("process_cpu_usage", plan_text)
                self.assertNotIn("jvm_memory_used_bytes", plan_text)
                self.assertNotIn("fault_mysql_slow_query_", plan_text)
                self.assertNotIn("collect_jvm_thread_dump", plan_text)

                instant = json.dumps(
                    {
                        "success": True,
                        "tool": "query_prometheus_metrics",
                        "query": promql,
                        "results": [
                            {
                                "metric_name": "probe_success",
                                "promql": promql,
                                "labels": {"job": promql.split('"')[1]},
                                "value": 0,
                                "display_value": "0 (unavailable)",
                                "description": "Blackbox dependency health probe result",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
                trend = json.dumps(
                    {
                        "success": True,
                        "tool": "query_prometheus_range",
                        "query": promql,
                        "start": "2026-07-31T09:55:00+08:00",
                        "end": "2026-07-31T10:10:00+08:00",
                        "step": "30s",
                        "results": [
                            {
                                "metric_name": "probe_success",
                                "promql": promql,
                                "labels": {"job": promql.split('"')[1]},
                                "values": [
                                    {"timestamp": 1, "value": "1"},
                                    {"timestamp": 2, "value": "0"},
                                ],
                                "summary": {
                                    "points": 2,
                                    "first_value": 1,
                                    "last_value": 0,
                                    "min_value": 0,
                                    "max_value": 1,
                                },
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
                active_alerts = json.dumps(
                    {
                        "success": True,
                        "alerts": [{"name": alert_name, "state": "firing"}],
                    },
                    ensure_ascii=False,
                )
                runbook = f"来源: {runbook_name}.md\n检查连接、进程、端口和认证配置。"

                metric_invoke = AsyncMock(return_value=instant)
                range_invoke = AsyncMock(return_value=trend)
                alerts_invoke = AsyncMock(return_value=active_alerts)
                rag_invoke = AsyncMock(return_value=runbook)
                with patch(
                    "app.agent.aiops.executor.query_prometheus_metrics",
                    SimpleNamespace(ainvoke=metric_invoke),
                ), patch(
                    "app.agent.aiops.executor.query_prometheus_range",
                    SimpleNamespace(ainvoke=range_invoke),
                ), patch(
                    "app.agent.aiops.executor.query_prometheus_alerts",
                    SimpleNamespace(ainvoke=alerts_invoke),
                ), patch(
                    "app.agent.aiops.executor.retrieve_knowledge",
                    SimpleNamespace(ainvoke=rag_invoke),
                ):
                    past_steps = await _execute_batch(plan[:-1], task)

                metric_invoke.assert_awaited_once_with({"promql": promql})
                range_args = range_invoke.await_args.args[0]
                self.assertEqual(range_args["promql"], promql)
                self.assertTrue(range_args["start"].endswith(expected_start))
                self.assertIn("end", range_args)
                rag_query = rag_invoke.await_args.args[0]["query"]
                self.assertIn(alert_name, rag_query)

                evidence = _format_evidence_chain(past_steps)
                trend_text = _format_trend_analysis(past_steps)
                self.assertIn("健康探测失败，确认依赖当前不可用", evidence)
                self.assertIn("故障尚未恢复", trend_text)
                self.assertIn(runbook_name, "\n".join(str(x) for x in past_steps))

                generic_report = """# 故障诊断报告

## 1. 故障摘要
- 当前异常。

## 2. 影响分析
- 待确认。

## 3. 自动诊断过程
- 缺少 JVM 热点线程，需要 Profiler 分析。

## 4. 证据链分析
| 来源 | 工具 | 指标 | 值 | 判断 |
|---|---|---|---|---|
| Prometheus | query_prometheus_metrics | probe_success | 0 | 异常 |

## 5. 根因分析
### 已确认事实
- 现有证据不足以确认唯一根因。
### 高概率原因
- 缺少线程 CPU 时间占比。
### 可信度
- **低**
### 证据限制
- 当前未接入完整 Spring Boot Actuator 数据。

## 6. 修复建议
### 立即处理
- 继续分析 JVM 线程。
### 长期优化
- 使用 Profiler。
"""
                report = _enforce_availability_report(generic_report, task, past_steps)
                self.assertIn("# 故障诊断报告", report)
                self.assertIn("## 5. 根因分析", report)
                self.assertIn("健康检查失败", report)
                self.assertIn("无法正常访问", report)
                self.assertIn("probe_success", report)
                self.assertIn("不可用判断可信度", report)
                self.assertIn("具体根因定位可信度", report)
                self.assertIn(runbook_name, report)
                if alert_name == "RedisUnavailable":
                    self.assertIn("缓存访问失败", report)
                    self.assertIn("Redis 认证配置错误", report)
                else:
                    self.assertIn("数据查询可能失败", report)
                    self.assertIn("数据库连接资源耗尽", report)
                self.assertNotIn("现有证据不足以确认唯一根因", report)
                for forbidden in ("JVM", "CPU", "线程", "Profiler", "Actuator", "GC 分析"):
                    self.assertNotIn(forbidden, report)
