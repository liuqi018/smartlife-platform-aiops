"""
通用 Plan-Execute-Replan 服务
基于 LangGraph 官方教程实现
"""

from typing import AsyncGenerator, Dict, Any
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from loguru import logger

from app.agent.aiops import PlanExecuteState, planner, executor, replanner
from app.models.alert import AlertContext
from app.services.alert_history_service import alert_history_service


# 节点名称常量
NODE_PLANNER = "planner"
NODE_EXECUTOR = "executor"
NODE_REPLANNER = "replanner"


class AIOpsService:
    """通用 Plan-Execute-Replan 服务"""

    def __init__(self):
        """初始化服务"""
        self.checkpointer = MemorySaver()
        self.graph = self._build_graph()
        logger.info("Plan-Execute-Replan Service 初始化完成")

    def _build_graph(self):
        """构建 Plan-Execute-Replan 工作流"""
        logger.info("构建工作流图...")

        # 创建状态图
        workflow = StateGraph(PlanExecuteState)

        # 添加节点
        workflow.add_node(NODE_PLANNER, planner)      # 制定计划
        workflow.add_node(NODE_EXECUTOR, executor)  # 执行步骤
        workflow.add_node(NODE_REPLANNER, replanner)  # 重新规划

        # 设置入口点
        workflow.set_entry_point(NODE_PLANNER)

        # 定义边
        workflow.add_edge(NODE_PLANNER, NODE_EXECUTOR)     # planner -> executor
        workflow.add_edge(NODE_EXECUTOR, NODE_REPLANNER)   # executor -> replanner

        # replanner 的条件边
        def should_continue(state: PlanExecuteState) -> str:
            """判断是否继续执行"""
            # 如果已经生成了最终响应，结束
            if state.get("response"):
                logger.info("已生成最终响应，结束流程")
                return END

            # 如果还有计划步骤，继续执行
            plan = state.get("plan", [])
            if plan:
                logger.info(f"继续执行，剩余 {len(plan)} 个步骤")
                return NODE_EXECUTOR

            # 计划为空但没有响应，返回 replanner 生成响应
            logger.info("计划执行完毕，生成最终响应")
            return END

        workflow.add_conditional_edges(
            NODE_REPLANNER,
            should_continue,
            {
                NODE_EXECUTOR: NODE_EXECUTOR,
                END: END
            }
        )

        # 编译工作流
        compiled_graph = workflow.compile(checkpointer=self.checkpointer)

        logger.info("工作流图构建完成")
        return compiled_graph

    async def execute(
        self,
        user_input: str,
        session_id: str = "default",
        alert_name: str = "-",
        service: str = "-",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        执行 Plan-Execute-Replan 流程

        Args:
            user_input: 用户的任务描述
            session_id: 会话ID

        Yields:
            Dict[str, Any]: 流式事件
        """
        with logger.contextualize(
            aiops=True,
            session_id=session_id,
            alert_name=alert_name,
            service=service,
            stage="workflow",
        ):
            logger.info("workflow started")
            try:
                # 初始化状态
                initial_state: PlanExecuteState = {
                    "input": user_input,
                    "plan": [],
                    "past_steps": [],
                    "response": "",
                }

                # 流式执行工作流
                config_dict = {
                    "configurable": {
                        "thread_id": session_id,
                    }
                }

                async for event in self.graph.astream(
                    input=initial_state,
                    config=config_dict,
                    stream_mode="updates",
                ):
                    for node_name, node_output in event.items():
                        logger.info(f"节点 '{node_name}' 输出事件")

                        if node_name == NODE_PLANNER:
                            yield self._format_planner_event(node_output)
                        elif node_name == NODE_EXECUTOR:
                            yield self._format_executor_event(node_output)
                        elif node_name == NODE_REPLANNER:
                            yield self._format_replanner_event(node_output)

                # 获取最终状态
                final_state = self.graph.get_state(config_dict)
                final_response = ""
                final_values = {}

                if final_state and final_state.values:
                    final_response = final_state.values.get("response", "")
                    final_values = final_state.values

                yield {
                    "type": "complete",
                    "stage": "complete",
                    "message": "任务执行完成",
                    "response": final_response,
                    "state": {
                        "input": final_values.get("input", ""),
                        "past_steps": final_values.get("past_steps", []),
                    },
                }

                logger.info("workflow completed")

            except Exception as e:
                logger.error(f"[会话 {session_id}] 任务执行失败: {e}", exc_info=True)
                yield {
                    "type": "error",
                    "stage": "error",
                    "message": f"任务执行出错: {str(e)}"
                }

    def build_alert_diagnosis_task(self, alert_context: AlertContext) -> str:
        """Build the diagnosis task text from a normalized AlertManager alert."""
        high_cpu_hint = """
## SmartLifeHighCPUUsage 专项要求
当告警名称为 SmartLifeHighCPUUsage 时，必须调用 query_prometheus_metrics 查询以下指标：
- promql="process_cpu_usage"

必须先获得真实 CPU 指标，再结合 JVM/GC/HTTP 指标进行关联分析，不得用其他指标替代 process_cpu_usage。
""" if alert_context.alert_name == "SmartLifeHighCPUUsage" else ""
        mysql_slow_query_hint = """
## SmartLifeMysqlSlowQueryHigh 专项要求
当告警名称为 SmartLifeMysqlSlowQueryHigh 时，必须查询以下指标及最近10分钟趋势：
- fault_mysql_slow_query_active
- fault_mysql_slow_query_executions
- fault_mysql_slow_query_last_duration_milliseconds

必须基于注入状态、执行次数增长和最近一次耗时判断慢查询异常。缺少具体SQL、
慢查询日志或EXPLAIN只能作为代码级定位限制，不得否定Prometheus强证据。
""" if alert_context.alert_name == "SmartLifeMysqlSlowQueryHigh" else ""

        return f"""
你是面向 Java 微服务的 Agentic AIOps 故障诊断助手。请基于以下真实 AlertManager 告警上下文，对黑马点评 smartlife 系统执行 LangGraph Plan-Execute-Replan 故障诊断。

## 告警上下文
- alertname: {alert_context.alert_name}
- severity: {alert_context.severity}
- service: {alert_context.service}
- instance: {alert_context.instance}
- start_time: {alert_context.start_time}
- end_time: {alert_context.end_time or "仍在 firing 或未提供"}
- description: {alert_context.description or "未提供"}

## 工具调用要求
1. 使用 get_current_time 获取当前时间，作为诊断时间基准。
2. 使用 query_prometheus_alerts 查询 Prometheus 当前活跃告警并核对告警状态。
3. 使用 query_prometheus_metrics 按明确 PromQL 查询相关 CPU、JVM、GC、HTTP 指标。
4. 使用 retrieve_knowledge 查询匹配的 RAG Runbook。
{high_cpu_hint}
{mysql_slow_query_hint}
## 诊断要求
1. 必须基于 Prometheus 返回的真实指标值和标签进行分析。
2. 区分已确认原因、已排除原因、候选原因和缺失证据。
3. 引用 RAG Runbook 的文件名和关键建议。
4. 生成 Markdown 报告，包含告警摘要、证据链、根因排序和处理建议。

## 限制
- 当前未接入 Spring Boot Actuator、应用日志（Loki/ELK）和 Redis/MySQL 深度诊断工具。
- 无法获取的证据必须明确标记为未获取，不得编造。
- 诊断计划控制在 3-5 步。
""".strip()


    async def diagnose_alert(
        self,
        alert_context: AlertContext,
        session_id: str = "default",
        alert_id: int | None = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Run the existing Plan-Execute-Replan flow from an alert context."""
        logger.info(
            "[会话 {}] 收到告警诊断任务: alert={}, severity={}, service={}, instance={}",
            session_id,
            alert_context.alert_name,
            alert_context.severity,
            alert_context.service,
            alert_context.instance,
        )

        yield {
            "type": "status",
            "stage": "alert_received",
            "message": "已收到 AlertManager 告警，开始生成诊断任务",
            "alert": alert_context.model_dump(),
        }

        aiops_task = self.build_alert_diagnosis_task(alert_context)

        async for event in self.execute(
            aiops_task,
            session_id,
            alert_name=alert_context.alert_name,
            service=alert_context.service,
        ):
            if event.get("type") == "complete":
                report = event.get("response", "")
                evidence = (event.get("state") or {}).get("past_steps", [])
                try:
                    report_persisted = alert_history_service.save_diagnosis_report(
                        alert_id,
                        session_id,
                        evidence,
                        report,
                        fingerprint=alert_context.fingerprint,
                    )
                except Exception as history_error:
                    report_persisted = False
                    logger.warning("Failed to save diagnosis history: {}", history_error)
                yield {
                    "type": "complete",
                    "stage": "diagnosis_complete",
                    "message": "diagnosis complete",
                    "diagnosis": {
                        "status": "completed",
                        "alert": alert_context.model_dump(),
                        "report": report,
                        "report_persisted": report_persisted,
                    },
                }
            else:
                yield event

    async def diagnose(
        self,
        session_id: str = "default"
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        AIOps 诊断接口（兼容旧接口）

        Args:
            session_id: 会话ID

        Yields:
            Dict[str, Any]: 诊断过程的流式事件
        """
        # 使用固定的 AIOps 任务描述
        from textwrap import dedent
        aiops_task = dedent("""诊断当前系统是否存在告警，如果存在告警请详细分析告警原因并生成诊断报告，诊断报告输出格式要求：
                ```
                # 告警分析报告

                ---

                ## 📋 活跃告警清单

                | 告警名称 | 级别 | 目标服务 | 首次触发时间 | 最新触发时间 | 状态 |
                |---------|------|----------|-------------|-------------|------|
                | [告警1名称] | [级别] | [服务名] | [时间] | [时间] | 活跃 |
                | [告警2名称] | [级别] | [服务名] | [时间] | [时间] | 活跃 |

                ---

                ## 🔍 告警根因分析1 - [告警名称]

                ### 告警详情
                - **告警级别**: [级别]
                - **受影响服务**: [服务名]
                - **持续时间**: [X分钟]

                ### 症状描述
                [根据监控指标描述症状]

                ### 日志证据
                [引用查询到的关键日志]

                ### 根因结论
                [基于证据得出的根本原因]

                ---

                ## 🛠️ 处理方案执行1 - [告警名称]

                ### 已执行的排查步骤
                1. [步骤1]
                2. [步骤2]

                ### 处理建议
                [给出具体的处理建议]

                ### 预期效果
                [说明预期的效果]

                ---

                ## 🔍 告警根因分析2 - [告警名称]
                [如果有第2个告警，重复上述格式]

                ---

                ## 📊 结论

                ### 整体评估
                [总结所有告警的整体情况]

                ### 关键发现
                - [发现1]
                - [发现2]

                ### 后续建议
                1. [建议1]
                2. [建议2]

                ### 风险评估
                [评估当前风险等级和影响范围]
                ```

                **重要提醒**：
                - 最终输出必须是纯 Markdown 文本，不要包含 JSON 结构
                - 所有内容必须基于工具查询的真实数据，严禁编造
                - 如果某个步骤失败，在结论中如实说明，不要跳过""")

        async for event in self.execute(aiops_task, session_id):
            # 转换事件格式以兼容旧的 API
            if event.get("type") == "complete":
                # 将 response 包装为 diagnosis 格式
                yield {
                    "type": "complete",
                    "stage": "diagnosis_complete",
                    "message": "诊断流程完成",
                    "diagnosis": {
                        "status": "completed",
                        "report": event.get("response", "")
                    }
                }
            else:
                yield event

    def _format_planner_event(self, state: Dict | None) -> Dict:
        """格式化 Planner 节点事件"""
        if not state:
            return {
                "type": "status",
                "stage": "planner",
                "message": "规划节点执行中"
            }

        plan = state.get("plan", [])

        return {
            "type": "plan",
            "stage": "plan_created",
            "message": f"执行计划已制定，共 {len(plan)} 个步骤",
            "plan": plan
        }

    def _format_executor_event(self, state: Dict | None) -> Dict:
        """格式化 Executor 节点事件"""
        if not state:
            return {
                "type": "status",
                "stage": "executor",
                "message": "执行节点运行中"
            }

        plan = state.get("plan", [])
        past_steps = state.get("past_steps", [])

        if past_steps:
            last_step, _ = past_steps[-1]
            return {
                "type": "step_complete",
                "stage": "step_executed",
                "message": f"步骤执行完成 ({len(past_steps)}/{len(past_steps) + len(plan)})",
                "current_step": last_step,
                "remaining_steps": len(plan)
            }
        else:
            return {
                "type": "status",
                "stage": "executor",
                "message": "开始执行步骤"
            }

    def _format_replanner_event(self, state: Dict | None) -> Dict:
        """格式化 Replanner 节点事件"""
        if not state:
            return {
                "type": "status",
                "stage": "replanner",
                "message": "评估节点运行中"
            }

        response = state.get("response", "")
        plan = state.get("plan", [])

        if response:
            # 已生成最终响应
            return {
                "type": "report",
                "stage": "final_report",
                "message": "最终报告已生成",
                "report": response
            }
        else:
            # 重新规划
            return {
                "type": "status",
                "stage": "replanner",
                "message": f"评估完成，{'继续执行剩余步骤' if plan else '准备生成最终响应'}",
                "remaining_steps": len(plan)
            }


# 全局单例
aiops_service = AIOpsService()
