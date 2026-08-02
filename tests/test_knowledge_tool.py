import json
import unittest
from unittest.mock import MagicMock, call, patch

from langchain_core.documents import Document
from pymilvus import MilvusException

from app.tools.knowledge_tool import (
    _filter_mysql_slow_query_docs,
    _is_mysql_slow_query_request,
    retrieve_knowledge,
)
from app.services.vector_store_manager import MilvusSearchUnavailable, VectorStoreManager


class MysqlSlowQueryKnowledgeFilterTest(unittest.TestCase):
    def test_mysql_query_is_detected(self):
        self.assertTrue(
            _is_mysql_slow_query_request(
                "MySQL慢SQL排查 slow query EXPLAIN Performance Schema"
            )
        )
        self.assertFalse(_is_mysql_slow_query_request("SmartLifeHighCPUUsage jstack GC"))

    def test_only_canonical_relevant_mysql_chunks_are_retained(self):
        docs = [
            Document(
                page_content=(
                    "使用慢查询日志、SHOW FULL PROCESSLIST、Performance Schema "
                    "和 EXPLAIN 检查索引、全表扫描、filesort 与锁等待。"
                ),
                metadata={"_file_name": "MySQL 慢 SQL 排查.md"},
            ),
            Document(
                page_content="使用 jstack 定位 CPU 热点线程和 CPU 密集型任务。",
                metadata={"_file_name": "1.MySQL 慢 SQL 排查.md"},
            ),
            Document(
                page_content="使用 jstack 和 GC 指标定位 CPU 热点。",
                metadata={"_file_name": "1.CPU 使用率过高排查.md"},
            ),
            Document(
                page_content="MySQL 基础介绍。",
                metadata={"_file_name": "1.MySQL 慢 SQL 排查.md"},
            ),
        ]

        filtered = _filter_mysql_slow_query_docs(docs)

        self.assertEqual(len(filtered), 1)
        self.assertIn("SHOW FULL PROCESSLIST", filtered[0].page_content)


class MilvusSearchReliabilityTest(unittest.TestCase):
    def setUp(self):
        self.manager = VectorStoreManager.__new__(VectorStoreManager)
        self.manager.vector_store = MagicMock()

    @patch("app.services.vector_store_manager.milvus_manager")
    def test_search_checks_load_state_before_search(self, manager):
        expected = [Document(page_content="runbook")]
        self.manager.vector_store.similarity_search.return_value = expected

        result = self.manager.search_documents("cpu", search_kwargs={"k": 3})

        manager.ensure_collection_loaded.assert_called_once_with()
        self.assertEqual(result, expected)

    @patch("app.services.vector_store_manager.milvus_manager")
    def test_collection_not_loaded_reloads_and_retries_once(self, manager):
        expected = [Document(page_content="runbook")]
        self.manager.vector_store.similarity_search.side_effect = [
            MilvusException(message="failed to search: collection not loaded"),
            expected,
        ]

        result = self.manager.search_documents("cpu", search_kwargs={"k": 3})

        self.assertEqual(result, expected)
        self.assertEqual(
            manager.ensure_collection_loaded.call_args_list,
            [call(), call(force=True), call()],
        )
        self.assertEqual(self.manager.vector_store.similarity_search.call_count, 2)

    @patch("app.services.vector_store_manager.milvus_manager")
    def test_search_failure_after_retry_raises_structured_boundary_error(self, manager):
        self.manager.vector_store.similarity_search.side_effect = MilvusException(
            message="failed to search: collection not loaded"
        )

        with self.assertRaisesRegex(
            MilvusSearchUnavailable,
            "Milvus collection unavailable",
        ):
            self.manager.search_documents("cpu", search_kwargs={"k": 3})

        self.assertEqual(self.manager.vector_store.similarity_search.call_count, 2)

    @patch("app.tools.knowledge_tool.vector_store_manager.search_documents")
    def test_knowledge_tool_returns_structured_milvus_error(self, search):
        search.side_effect = MilvusSearchUnavailable("Milvus collection unavailable")

        content, docs = retrieve_knowledge.func("CPU Runbook")

        self.assertEqual(
            json.loads(content),
            {
                "success": False,
                "tool": "rag_search",
                "error": "Milvus collection unavailable",
            },
        )
        self.assertEqual(docs, [])


if __name__ == "__main__":
    unittest.main()
