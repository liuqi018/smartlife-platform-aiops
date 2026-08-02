import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.documents import Document

from app.agent.aiops.planner import _alert_metric_plan, _select_diagnostic_template
from app.agent.aiops.replanner import _rag_query_for_input
from app.core import fault_mapping_loader
from app.tools.knowledge_tool import _filter_runbook_allowlist, retrieve_knowledge


class FaultMappingLoaderTest(unittest.TestCase):
    def tearDown(self):
        fault_mapping_loader.reload_fault_mappings()

    def test_loads_all_supported_faults(self):
        mappings = fault_mapping_loader.reload_fault_mappings()
        self.assertEqual(
            set(mappings),
            {
                "smartlifehighcpuusage",
                "smartlifejvmmemoryhighusage",
                "smartlifemysqlslowqueryhigh",
                "smartlifeservicedown",
                "redisunavailable",
                "mysqlunavailable",
            },
        )
        self.assertEqual(mappings["smartlifemysqlslowqueryhigh"]["report_policy"], "mysql")

    def test_missing_yaml_falls_back_to_legacy_planner(self):
        missing = Path(tempfile.gettempdir()) / "missing-fault-mapping.yaml"
        with patch.object(fault_mapping_loader, "FAULT_MAPPING_PATH", missing):
            fault_mapping_loader.load_fault_mappings.cache_clear()
            template = _select_diagnostic_template("- alertname: SmartLifeHighCPUUsage")
            self.assertEqual(template["name"], "SmartLifeHighCPUUsage")
            self.assertIn("process_cpu_usage", "\n".join(_alert_metric_plan(
                "- alertname: SmartLifeHighCPUUsage"
            )))

    def test_yaml_rag_query_overlays_legacy_template(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mapping_path = Path(temp_dir) / "fault_mapping.yaml"
            mapping_path.write_text(
                """
SmartLifeHighCPUUsage:
  alert_name: SmartLifeHighCPUUsage
  category: cpu
  metrics: [process_cpu_usage]
  range_query: [process_cpu_usage]
  rag_query: configured CPU runbook
  runbook_allowlist: []
  report_policy: cpu
  missing_evidence: []
""",
                encoding="utf-8",
            )
            with patch.object(fault_mapping_loader, "FAULT_MAPPING_PATH", mapping_path):
                fault_mapping_loader.load_fault_mappings.cache_clear()
                template = _select_diagnostic_template("- alertname: SmartLifeHighCPUUsage")
                self.assertEqual(template["rag_query"], "configured CPU runbook")

    def test_smartlife_service_down_uses_yaml_rag_query(self):
        query = _rag_query_for_input("- alertname: SmartLifeServiceDown")
        self.assertIn("SmartLifeServiceDown", query)
        self.assertIn("服务启动失败", query)
        self.assertNotIn("SmartLifeHighCPUUsage", query)

    def test_allowlist_matches_file_name_and_source(self):
        docs = [
            Document(page_content="cpu", metadata={"_file_name": "CPU 使用率过高排查.md"}),
            Document(page_content="mysql", metadata={"_source": "runbooks/MySQL/MySQL 慢 SQL 排查.md"}),
        ]
        result = _filter_runbook_allowlist(docs, ["MySQL 慢 SQL 排查.md"])
        self.assertEqual([doc.page_content for doc in result], ["mysql"])

    def test_availability_rag_rejects_non_allowlisted_documents(self):
        wrong_docs = [
            Document(
                page_content="JVM CPU GC troubleshooting",
                metadata={"_file_name": "Java 进程 CPU 过高排查.md"},
            )
        ]
        retriever = SimpleNamespace(invoke=lambda _query: wrong_docs)
        vector_store = SimpleNamespace(as_retriever=lambda **_kwargs: retriever)
        with patch(
            "app.tools.knowledge_tool.vector_store_manager.get_vector_store",
            return_value=vector_store,
        ):
            content, docs = retrieve_knowledge.func(
                "RedisUnavailable Redis 服务不可用 连接失败 Runbook"
            )

        self.assertEqual(content, "没有找到相关信息。")
        self.assertEqual(docs, [])


if __name__ == "__main__":
    unittest.main()
