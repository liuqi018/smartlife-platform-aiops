import unittest
from unittest.mock import Mock, patch

from evaluation.runner import EvaluationRunner, TrialResult, build_summary


class EvaluationSummaryTest(unittest.TestCase):
    def test_builds_required_metrics_and_stability(self):
        results = [
            TrialResult("cpu_high", 1, "SmartLifeHighCPUUsage", diagnosis_seconds=10,
                        identification_correct=True, root_cause_match=True,
                        evidence_consistent=True, diagnosis_success=True, resolved=True),
            TrialResult("cpu_high", 2, "SmartLifeHighCPUUsage", diagnosis_seconds=20,
                        identification_correct=False, diagnosis_success=False),
        ]
        output = build_summary(results)
        self.assertEqual(output["summary"]["total_tests"], 2)
        self.assertEqual(output["summary"]["average_diagnosis_time_seconds"], 10)
        self.assertEqual(output["per_fault"]["cpu_high"]["stability"], 0.5)

    def runner(self):
        return EvaluationRunner({
            "evaluation": {
                "repeat": 1, "alert_timeout": 1, "recovery_timeout": 1,
                "aiops_base_url": "http://aiops", "smartlife_base_url": "http://smartlife",
            },
            "faults": {},
        })

    def test_http_executor_respects_method_and_relative_url(self):
        runner = self.runner()
        runner.client.request = Mock(return_value=Mock(raise_for_status=Mock()))
        runner.execute_start({
            "type": "http", "start": {"method": "GET", "url": "/test/fault/cpu"}
        })
        runner.client.request.assert_called_once_with("GET", "http://smartlife/test/fault/cpu")

    @patch("evaluation.runner.subprocess.run")
    def test_docker_stop_injects_and_start_recovers(self, run):
        runner = self.runner()
        fault = {
            "type": "docker",
            "stop": {"command": "docker stop smartlife-redis"},
            "start": {"command": "docker start smartlife-redis"},
        }
        runner.execute_start(fault)
        runner.execute_stop(fault)
        self.assertEqual(run.call_args_list[0].args[0], ["docker", "stop", "smartlife-redis"])
        self.assertEqual(run.call_args_list[1].args[0], ["docker", "start", "smartlife-redis"])

    def test_empty_process_commands_are_not_configured(self):
        runner = self.runner()
        self.assertFalse(runner._configured({
            "type": "process", "stop": {"command": ""}, "start": {"command": ""}
        }))

    @patch("evaluation.runner.subprocess.run")
    def test_process_flat_command_keys_are_supported(self, run):
        runner = self.runner()
        fault = {"type": "process", "stop_command": "svc stop", "start_command": "svc start"}
        runner.execute_start(fault)
        runner.execute_stop(fault)
        self.assertEqual(run.call_count, 2)

    def test_all_six_faults_include_the_database_alert_name(self):
        expected = {
            "cpu_high": "SmartLifeHighCPUUsage",
            "jvm_oom": "SmartLifeJvmMemoryHighUsage",
            "mysql_slow_query": "SmartLifeMysqlSlowQueryHigh",
            "smartlife_service_down": "SmartLifeServiceDown",
            "redis_unavailable": "RedisUnavailable",
            "mysql_unavailable": "MysqlUnavailable",
        }
        runner = self.runner()
        for key, alert_name in expected.items():
            with self.subTest(key=key):
                names = runner._alert_names(key, {"alert_name": "configured-alias"})
                self.assertIn(alert_name, names)

    def test_jvm_report_is_polled_by_captured_alert_id(self):
        runner = self.runner()
        connection = Mock()
        cursor = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        cursor_context = Mock()
        cursor_context.__enter__ = Mock(return_value=cursor)
        cursor_context.__exit__ = Mock(return_value=False)
        connection.cursor.return_value = cursor_context
        cursor.fetchone.return_value = {"alert_id": 48, "report": "JVM OOM heap"}
        runner._connect = Mock(return_value=connection)

        report = runner._report(48)

        sql, values = cursor.execute.call_args.args
        self.assertIn("WHERE alert_id=%s", sql)
        self.assertEqual(values, (48,))
        self.assertEqual(report["alert_id"], 48)


if __name__ == "__main__":
    unittest.main()
