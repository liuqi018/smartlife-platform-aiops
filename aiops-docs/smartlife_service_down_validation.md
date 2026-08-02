# SmartLifeServiceDown 故障闭环验证

该场景复用现有 AlertManager → FastAPI → LangGraph → Prometheus → RAG → Report 链路，不增加工具、不修改核心 workflow。

## 触发方式

Prometheus 规则使用 `up{job="smartlife"} == 0`，持续 30 秒后触发。测试时停止一个被抓取的 smartlife 实例，例如 8081；不要停止 FastAPI、Prometheus、AlertManager、Milvus 或 RAG 服务。

## 预期诊断链路

1. AlertManager 发送 `alertname=SmartLifeServiceDown`。
2. Planner 生成 `up{job="smartlife"}`、活动告警、缺失 health/log 证据、服务启动失败 Runbook 和报告步骤。
3. Executor 使用现有 `query_prometheus_metrics`、`query_prometheus_alerts` 和 `retrieve_knowledge`。
4. RAG 命中 `Linux/6.Linux 服务启动失败排查.md` 或等价服务不可用文档。
5. Report 输出 `# 告警摘要`、`# 关键证据`、`# 根因分析`、`# 处理建议`。

## 手工验证

```powershell
Invoke-RestMethod http://localhost:9090/api/v1/query?query=up%7Bjob%3D%22smartlife%22%7D
Get-Content logs/aiops_$(Get-Date -Format yyyy-MM-dd).log -Wait

# 单次诊断过程
Get-Content logs/diagnosis/<session_id>.log -Wait

# Prompt、RAG 全文与工具详细返回
Get-Content logs/debug/<session_id>.log -Wait
```

也可将 `tests/fixtures/alertmanager_smartlife_service_down.json` POST 到 `/api/alerts/webhook`，用于验证 webhook 与 Agent 链路；该方式不会自行制造 Prometheus target down 数据。

恢复实例后确认告警 resolved，并检查诊断日志包含同一 `session_id`、`alert_name=SmartLifeServiceDown`、`service=smartlife` 及各阶段记录。
