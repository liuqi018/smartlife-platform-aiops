"""配置管理模块

使用 Pydantic Settings 实现类型安全的配置管理
"""

from typing import Dict, Any
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用配置
    app_name: str = "SmartLife AIOps"
    app_version: str = "1.0.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 9900

    # DashScope 配置（当前主要用于 Embedding）
    dashscope_api_key: str = ""
    dashscope_model: str = "qwen-max"
    dashscope_embedding_model: str = "text-embedding-v4"

    # AutoDL OpenAI-compatible LLM 配置
    autodl_api_key: str = ""
    autodl_base_url: str = "https://www.autodl.art/api/v1"
    autodl_model: str = "gpt-5.5"

    # Legacy OpenAI-compatible 配置，仅保留兼容，不作为默认 LLM 鉴权来源
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = ""

    # Milvus 配置
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_timeout: int = 10000  # 毫秒

    # RAG 配置
    rag_top_k: int = 3
    rag_model: str = "qwen-max"  # 使用快速响应模型，不带扩展思考

    # 文档分块配置
    chunk_max_size: int = 800
    chunk_overlap: int = 100

    # MCP 服务配置（transport: stdio | sse | streamable-http）
    # 腾讯云托管 MCP 的 URL 通常含 /sse/，需使用 sse；本地 FastMCP 使用 streamable-http
    mcp_cls_transport: str = "streamable-http"
    mcp_cls_url: str = "http://localhost:8003/mcp"
    mcp_monitor_transport: str = "streamable-http"
    mcp_monitor_url: str = "http://localhost:8004/mcp"

    # Prometheus
    prometheus_base_url: str = "http://127.0.0.1:9090"
    prometheus_request_timeout: float = 10.0
    alertmanager_base_url: str = "http://127.0.0.1:9093"
    alertmanager_request_timeout: float = 5.0
    alert_reconciliation_enabled: bool = True
    alert_reconciliation_interval_seconds: float = 60.0
    alert_claim_ttl_seconds: int = 1800

    # Alert lifecycle storage. Redis uses an isolated key prefix; MySQL uses
    # a dedicated database and never writes to the smartlife schema.
    aiops_redis_url: str = "redis://:1234@127.0.0.1:6380/1"
    aiops_redis_namespace: str = "aiops"
    aiops_redis_prefix: str = "aiops:alert:current"
    aiops_redis_diagnosis_prefix: str = "aiops:diagnosis"
    aiops_redis_session_prefix: str = "aiops:session"
    aiops_redis_state_prefix: str = "aiops:state"
    aiops_mysql_host: str = "127.0.0.1"
    # Docker smartlife-mysql is published on a dedicated host port so it cannot be
    # confused with a developer workstation MySQL listening on 3306.
    aiops_mysql_port: int = 3307
    aiops_mysql_user: str = "root"
    aiops_mysql_password: str = "1234"
    aiops_mysql_database: str = "aiops"
    aiops_storage_fallback: bool = True

    # AIOps planner generation
    aiops_planner_timeout: float = 60.0
    aiops_planner_max_attempts: int = 2
    aiops_planner_max_tokens: int = 2000

    # AIOps report generation
    aiops_report_timeout: float = 120.0
    aiops_report_primary_model: str = "gpt-5.6-sol"
    aiops_report_secondary_model: str = "qwen3.7-max"
    aiops_report_max_attempts: int = 3
    aiops_report_max_chars: int = 2000
    aiops_report_max_tokens: int = 3200

    @property
    def mcp_servers(self) -> Dict[str, Dict[str, Any]]:
        """获取完整的 MCP 服务器配置"""
        return {
            "cls": {
                "transport": self.mcp_cls_transport,
                "url": self.mcp_cls_url,
            },
            "monitor": {
                "transport": self.mcp_monitor_transport,
                "url": self.mcp_monitor_url,
            }
        }


# 全局配置实例
config = Settings()
