import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.runnables import RunnableLambda

from app.agent.aiops.executor import _execute_batch
from app.agent.aiops.planner import _alert_metric_plan
from app.agent.aiops.replanner import _enforce_availability_report, _generate_response
from app.models.alert import parse_alertmanager_payload
from app.services.aiops_service import aiops_service


FIXTURE = Path(__file__).parent / "fixtures" / "alertmanager_smartlife_service_down.json"


class SmartLifeServiceDownClosureTest(unittest.IsolatedAsyncioTestCase):
    async def test_existing_agent_closes_service_down_diagnosis(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        alert = parse_alertmanager_payload(payload)[0]
        task = aiops_service.build_alert_diagnosis_task(alert)
        plan = _alert_metric_plan(task)
        plan_text = "\n".join(plan)

        self.assertIn('up{job="smartlife"}', plan_text)
        self.assertIn("服务启动失败", plan_text)
        self.assertNotIn("process_cpu_usage", plan_text)

        instant_result = json.dumps(
            {
                "success": True,
                "tool": "query_prometheus_metrics",
                "query": 'up{job="smartlife"}',
                "count": 1,
                "results": [
                    {
                        "metric_name": "up",
                        "promql": 'up{job="smartlife"}',
                        "labels": {"job": "smartlife", "instance": alert.instance},
                        "value": 0,
                        "display_value": "0",
                        "unit": "boolean",
                        "timestamp": "2026-07-24T01:01:00+08:00",
                        "description": "Prometheus target availability",
                    }
                ],
            },
            ensure_ascii=False,
        )
        alerts_result = json.dumps(
            {
                "success": True,
                "alerts": [{"name": "SmartLifeServiceDown", "state": "firing"}],
                "total": 1,
            },
            ensure_ascii=False,
        )
        rag_result = "来源: Linux 服务启动失败排查.md\n检查进程、端口、配置和启动日志。"

        metric_tool = SimpleNamespace(ainvoke=AsyncMock(return_value=instant_result))
        alerts_tool = SimpleNamespace(ainvoke=AsyncMock(return_value=alerts_result))
        rag_tool = SimpleNamespace(ainvoke=AsyncMock(return_value=rag_result))
        with patch("app.agent.aiops.executor.query_prometheus_metrics", metric_tool), patch(
            "app.agent.aiops.executor.query_prometheus_alerts", alerts_tool
        ), patch("app.agent.aiops.executor.retrieve_knowledge", rag_tool):
            past_steps = await _execute_batch(plan[:-1], task)

        evidence_text = "\n".join(str(result) for _, result in past_steps)
        self.assertIn('up{job=\\"smartlife\\"}', evidence_text)
        self.assertIn("SmartLifeServiceDown", evidence_text)
        self.assertIn("Linux 服务启动失败排查", evidence_text)

        async def report_llm(_prompt):
            return """# 告警摘要
- 告警名称：SmartLifeServiceDown
- 服务：smartlife
- 当前状态：Prometheus target down

# 关键证据
| 指标 | 当前值 | 判断 |
|---|---:|---|
| up{job=\"smartlife\"} | 0 | 服务不可抓取 |

# 根因分析
- 已确认：Prometheus 无法抓取目标。
- 可能原因：进程退出、端口或启动配置异常。
- 排除原因：缺少服务日志，暂不能排除依赖故障。

# 处理建议
1. 检查进程和监听端口。
2. 检查启动日志与配置。"""

        state = {"input": task, "plan": [], "past_steps": past_steps, "response": ""}
        report = (await _generate_response(state, RunnableLambda(report_llm)))["response"]
        self.assertIn("# 告警摘要", report)
        self.assertIn("SmartLifeServiceDown", report)
        self.assertIn('up{job="smartlife"}', report)
        self.assertLessEqual(len(report), 1500)

        polluted_report = """# 故障诊断报告

## 1. 故障摘要
- 服务不可用。
## 2. 影响分析
- Redis 或 MySQL 可能异常。
## 3. 自动诊断过程
- 缺少 JVM 热点线程，需要 Profiler 分析。
## 4. 证据链分析
| 来源 | 工具 | 指标 | 值 | 判断 |
|---|---|---|---|---|
| Prometheus | query_prometheus_metrics | up | 0 | 异常 |
## 5. 根因分析
### 已确认事实
- 证据不足。
### 高概率原因
- 缺少线程 CPU 时间占比。
### 可信度
- **低**
### 证据限制
- 缺少完整 Spring Boot Actuator。
## 6. 修复建议
### 立即处理
- 使用 jstack。
### 长期优化
- 使用 Profiler。
"""
        service_report = _enforce_availability_report(polluted_report, task, past_steps)
        self.assertIn("Prometheus 检测到 smartlife 服务不可抓取", service_report)
        self.assertIn('up{job="smartlife"}', service_report)
        self.assertIn("API 请求可能失败", service_report)
        self.assertIn("上游服务调用可能受到影响", service_report)
        self.assertIn("8081 端口未监听", service_report)
        self.assertIn("服务不可用判断可信度", service_report)
        self.assertIn("具体根因定位可信度", service_report)
        self.assertIn("Linux 服务启动失败排查.md", service_report)
        for forbidden in ("Redis", "MySQL", "JVM", "CPU", "线程", "Profiler", "jstack", "Actuator"):
            self.assertNotIn(forbidden, service_report)
