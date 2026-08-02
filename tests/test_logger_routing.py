import unittest

from app.utils.logger import _aiops_filter, _console_filter


class LoggerRoutingTest(unittest.TestCase):
    def test_console_keeps_key_nodes_and_errors(self):
        self.assertTrue(_console_filter({"level": type("Level", (), {"no": 20})(), "message": "workflow started"}))
        self.assertTrue(_console_filter({"level": type("Level", (), {"no": 40})(), "message": "failure"}))

    def test_console_hides_detailed_prometheus_result(self):
        self.assertFalse(_console_filter({"level": type("Level", (), {"no": 20})(), "message": "query_prometheus_metrics returned result: {...}"}))
        self.assertFalse(_console_filter({"level": type("Level", (), {"no": 20})(), "message": "[Planner] plan generated successfully"}))
        self.assertFalse(_console_filter({"level": type("Level", (), {"no": 20})(), "module": "replanner", "message": "RAG 命中服务启动失败 Runbook"}))
        self.assertFalse(_console_filter({"level": type("Level", (), {"no": 30})(), "message": "[Report] model gpt-5.6-sol failed attempt=1/3"}))
        self.assertTrue(_console_filter({"level": type("Level", (), {"no": 30})(), "message": "[Report] switching to qwen3.7-max"}))
        self.assertTrue(_console_filter({"level": type("Level", (), {"no": 30})(), "message": "[Report] llm timeout"}))
        self.assertTrue(_console_filter({"level": type("Level", (), {"no": 30})(), "message": "[Report] client disconnected"}))

    def test_aiops_file_requires_diagnosis_context(self):
        self.assertTrue(_aiops_filter({"extra": {"aiops": True}}))
        self.assertFalse(_aiops_filter({"extra": {"aiops": False}}))
