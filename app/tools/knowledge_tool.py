"""知识检索工具 - 从向量数据库中检索相关信息"""

from typing import List, Tuple

from langchain_core.documents import Document
from langchain_core.tools import tool
from loguru import logger

from app.config import config
from app.core.fault_mapping_loader import match_fault_mapping
from app.services.vector_store_manager import vector_store_manager


MYSQL_SLOW_QUERY_FILES = {"MySQL 慢 SQL 排查.md", "1.MySQL 慢 SQL 排查.md"}
MYSQL_SLOW_QUERY_TERMS = (
    "慢查询日志",
    "show full processlist",
    "performance schema",
    "performance_schema",
    "explain",
    "索引",
    "全表扫描",
    "filesort",
    "锁等待",
)
MYSQL_SLOW_QUERY_EXCLUDED_TERMS = (
    "jstack",
    "cpu热点线程",
    "cpu 热点线程",
    "cpu密集型任务",
    "cpu 密集型任务",
)


def _is_mysql_slow_query_request(query: str) -> bool:
    lower = str(query).lower()
    return "mysql" in lower and any(
        marker in lower for marker in ("慢sql", "慢 sql", "slow query", "mysqlslowquery")
    )


def _filter_mysql_slow_query_docs(docs: List[Document]) -> List[Document]:
    """Keep only relevant chunks from the canonical MySQL slow-SQL Runbook."""
    filtered: list[Document] = []
    for doc in docs:
        file_name = str(doc.metadata.get("_file_name") or "")
        source = str(doc.metadata.get("_source") or "").replace("\\", "/")
        if file_name not in MYSQL_SLOW_QUERY_FILES and not any(
            source.endswith(f"/{name}") for name in MYSQL_SLOW_QUERY_FILES
        ):
            continue
        content_lower = doc.page_content.lower()
        if any(term in content_lower for term in MYSQL_SLOW_QUERY_EXCLUDED_TERMS):
            continue
        if not any(term in content_lower for term in MYSQL_SLOW_QUERY_TERMS):
            continue
        filtered.append(doc)
    return filtered


def _filter_runbook_allowlist(
    docs: List[Document],
    allowlist: list[str],
) -> List[Document]:
    allowed = {name.casefold() for name in allowlist}
    return [
        doc for doc in docs
        if (
            str(doc.metadata.get("_file_name") or "").casefold() in allowed
            or str(doc.metadata.get("_source") or "").replace("\\", "/").rsplit("/", 1)[-1].casefold()
            in allowed
        )
    ]


@tool(response_format="content_and_artifact")
def retrieve_knowledge(query: str) -> Tuple[str, List[Document]]:
    """从知识库中检索相关信息来回答问题
    
    当用户的问题涉及专业知识、文档内容或需要参考资料时，使用此工具。
    
    Args:
        query: 用户的问题或查询
        
    Returns:
        Tuple[str, List[Document]]: (格式化的上下文文本, 原始文档列表)
    """
    try:
        logger.info(f"知识检索工具被调用: query='{query}'")
        
        # 从向量存储中检索相关文档
        vector_store = vector_store_manager.get_vector_store()
        mysql_slow_query = _is_mysql_slow_query_request(query)
        mapping = match_fault_mapping(query)
        allowlist = list((mapping or {}).get("runbook_allowlist") or [])
        candidate_k = (
            max(config.rag_top_k * 5, 20)
            if mysql_slow_query or allowlist
            else config.rag_top_k
        )
        search_kwargs: dict = {"k": candidate_k}
        if allowlist:
            escaped = [name.replace('"', '\\"') for name in allowlist]
            values = ", ".join(f'"{name}"' for name in escaped)
            search_kwargs["expr"] = f'metadata["_file_name"] in [{values}]'
        retriever = vector_store.as_retriever(search_kwargs=search_kwargs)
        
        docs = retriever.invoke(query)
        if mysql_slow_query:
            docs = _filter_mysql_slow_query_docs(docs)[: config.rag_top_k]
            logger.info(
                "MySQL slow-query RAG source filter applied: candidates={}, accepted={}",
                candidate_k,
                len(docs),
            )
        if allowlist:
            allowlisted_docs = _filter_runbook_allowlist(docs, allowlist)
            if allowlisted_docs:
                docs = allowlisted_docs[: config.rag_top_k]
                logger.info(
                    "YAML Runbook allowlist applied: alert={}, accepted={}",
                    mapping.get("alert_name"),
                    len(docs),
                )
            else:
                category = str((mapping or {}).get("category") or "")
                if category in {"service_availability", "dependency_availability"}:
                    docs = []
                    logger.warning(
                        "Availability Runbook allowlist matched no candidates; rejecting unrelated results: alert={}",
                        mapping.get("alert_name"),
                    )
                else:
                    logger.warning(
                        "YAML Runbook allowlist matched no candidates; preserving legacy results: alert={}",
                        mapping.get("alert_name"),
                    )
        
        if not docs:
            logger.warning("未检索到相关文档")
            return "没有找到相关信息。", []
        
        # 格式化文档为上下文
        context = format_docs(docs)
        
        logger.info(f"检索到 {len(docs)} 个相关文档")
        return context, docs
        
    except Exception as e:
        logger.error(f"知识检索工具调用失败: {e}")
        return f"检索知识时发生错误: {str(e)}", []


def format_docs(docs: List[Document]) -> str:
    """
    格式化文档列表为上下文文本
    
    Args:
        docs: 文档列表
        
    Returns:
        str: 格式化的上下文文本
    """
    formatted_parts = []
    
    for i, doc in enumerate(docs, 1):
        # 提取元数据
        metadata = doc.metadata
        source = metadata.get("_file_name", "未知来源")
        
        # 提取标题信息 (如果有)
        headers = []
        for key in ["h1", "h2", "h3"]:
            if key in metadata and metadata[key]:
                headers.append(metadata[key])
        
        header_str = " > ".join(headers) if headers else ""
        
        # 构建格式化文本
        formatted = f"【参考资料 {i}】"
        if header_str:
            formatted += f"\n标题: {header_str}"
        formatted += f"\n来源: {source}"
        formatted += f"\n内容:\n{doc.page_content}\n"
        
        formatted_parts.append(formatted)
    
    return "\n".join(formatted_parts)
