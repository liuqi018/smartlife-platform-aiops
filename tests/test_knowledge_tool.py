import unittest

from langchain_core.documents import Document

from app.tools.knowledge_tool import (
    _filter_mysql_slow_query_docs,
    _is_mysql_slow_query_request,
)


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


if __name__ == "__main__":
    unittest.main()
