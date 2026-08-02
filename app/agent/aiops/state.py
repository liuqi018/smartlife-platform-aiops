"""
通用 Plan-Execute-Replan 状态定义
基于 LangGraph 官方教程实现
"""

from typing import Annotated, List, Tuple, TypedDict, Union
import operator

from pydantic import BaseModel, Field


class Plan(BaseModel):
    """Structured Planner output accepted by the AIOps state schema."""

    steps: List[str] = Field(
        default_factory=list,
        description="完成任务所需的有序步骤。",
    )


PlanStateValue = Union[List[str], Plan]


class PlanExecuteState(TypedDict):
    """Plan-Execute-Replan 状态"""
    
    # 用户输入（任务描述）
    input: str
    
    # 执行计划（步骤列表）
    # Planner 正常写入 List[str]；schema 同时允许结构化 LLM 返回的 Plan，
    # 避免 Pydantic 将实际 Plan 对象按 None/不兼容类型序列化。
    plan: PlanStateValue
    
    # 已执行的步骤历史
    # 使用 operator.add 实现追加式更新（而非覆盖）
    past_steps: Annotated[List[Tuple[str, str]], operator.add]
    
    # 最终响应/报告
    response: str
