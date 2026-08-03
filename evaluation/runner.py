from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import statistics
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import httpx
import pymysql
import yaml

from app.config import config as app_config


FAULT_ALERT_NAMES = {
    "cpu_high": ("SmartLifeHighCPUUsage",),
    "jvm_oom": ("SmartLifeJvmMemoryHighUsage",),
    "mysql_slow_query": ("SmartLifeMysqlSlowQueryHigh",),
    "smartlife_service_down": ("SmartLifeServiceDown",),
    "service_down": ("SmartLifeServiceDown",),
    "redis_unavailable": ("RedisUnavailable",),
    "mysql_unavailable": ("MysqlUnavailable",),
}


@dataclass
class TrialResult:
    fault: str
    repetition: int
    alert_name: str
    status: str = "failed"
    alert_id: int | None = None
    session_id: str = ""
    fingerprint: str = ""
    diagnosis_seconds: float | None = None
    identification_correct: bool = False
    root_cause_match: bool = False
    evidence_consistent: bool = False
    diagnosis_success: bool = False
    resolved: bool = False
    error: str = ""


class EvaluationRunner:
    def __init__(self, settings: dict[str, Any], *, dry_run: bool = False) -> None:
        self.settings = settings
        self.options = settings["evaluation"]
        self.faults = settings["faults"]
        self.dry_run = dry_run
        self.client = httpx.Client(timeout=30, trust_env=False)

    def _connect(self):
        return pymysql.connect(
            host=app_config.aiops_mysql_host,
            port=app_config.aiops_mysql_port,
            user=app_config.aiops_mysql_user,
            password=app_config.aiops_mysql_password,
            database=app_config.aiops_mysql_database,
            charset="utf8mb4",
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def preflight(self) -> None:
        for key, fault in self.faults.items():
            fault_type = str(fault.get("type") or "").lower()
            if fault_type not in {"http", "docker", "process"}:
                raise ValueError(f"Unsupported fault type for {key}: {fault_type or '<empty>'}")
            if fault_type == "http":
                for action in ("start", "stop"):
                    spec = fault.get(action) or {}
                    if not spec.get("url") or not spec.get("method"):
                        raise ValueError(f"HTTP fault {key} requires {action}.method and {action}.url")
            elif fault_type == "docker":
                if not self._command(fault, "start") or not self._command(fault, "stop"):
                    raise ValueError(f"Docker fault {key} requires stop.command and start.command")
            # Process commands are intentionally optional. The trial is marked
            # not_configured instead of preventing all other faults from running.
        if self.dry_run:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        aiops = str(self.options["aiops_base_url"]).rstrip("/")
        response = self.client.get(f"{aiops}/api/dashboard/summary")
        response.raise_for_status()

    @staticmethod
    def _command(fault: dict[str, Any], action: str) -> str:
        nested = fault.get(action) or {}
        return str(nested.get("command") or fault.get(f"{action}_command") or "").strip()

    def _http(self, spec: dict[str, Any]) -> None:
        method = str(spec["method"]).upper()
        url = str(spec["url"])
        if url.startswith("/"):
            url = f"{str(self.options['smartlife_base_url']).rstrip('/')}{url}"
        print(f"[HTTP]\n{method} {url}", flush=True)
        response = self.client.request(method, url)
        response.raise_for_status()

    @staticmethod
    def _run_command(command: str, label: str) -> None:
        print(f"[{label}]\n{command}", flush=True)
        args = shlex.split(command, posix=os.name != "nt")
        subprocess.run(args, check=True, timeout=120)

    def _configured(self, fault: dict[str, Any]) -> bool:
        if str(fault.get("type")).lower() != "process":
            return True
        return bool(self._command(fault, "stop") and self._command(fault, "start"))

    def execute_start(self, fault: dict[str, Any]) -> None:
        """Start the fault. Docker/process faults are induced by their stop action."""
        fault_type = str(fault["type"]).lower()
        if fault_type == "http":
            self._http(fault["start"])
        elif fault_type == "docker":
            self._run_command(self._command(fault, "stop"), "Docker")
        else:
            self._run_command(self._command(fault, "stop"), "Process")

    def execute_stop(self, fault: dict[str, Any]) -> None:
        """Stop the fault and restore the affected service."""
        fault_type = str(fault["type"]).lower()
        if fault_type == "http":
            self._http(fault["stop"])
        elif fault_type == "docker":
            self._run_command(self._command(fault, "start"), "Docker")
        else:
            self._run_command(self._command(fault, "start"), "Process")

    def _wait(self, description: str, timeout: float, query: Callable[[], Any]) -> Any:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = query()
            if value:
                return value
            time.sleep(float(self.options.get("poll_interval_seconds", 5)))
        raise TimeoutError(f"Timed out waiting for {description} after {timeout}s")

    @staticmethod
    def _alert_names(key: str, fault: dict[str, Any]) -> tuple[str, ...]:
        names = [*FAULT_ALERT_NAMES.get(key, ()), str(fault.get("alert_name") or "")]
        return tuple(dict.fromkeys(name for name in names if name))

    def _latest_alert_lifecycle(
        self, alert_names: tuple[str, ...], after_id: int
    ) -> dict[str, Any] | None:
        """Return a target lifecycle created after fault injection.

        ``alert_event`` stores one mutable row per lifecycle: the firing webhook
        inserts the row and the resolved webhook later updates that same row.
        A short-lived fault may therefore already be ``resolved`` before the
        runner's first poll.  ``id > after_id`` proves that the lifecycle was
        created after this trial's baseline, so filtering on its current status
        would incorrectly discard a valid firing event.
        """
        placeholders = ", ".join(["%s"] * len(alert_names))
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT id, fingerprint, alert_name, status, start_time, created_time
                   FROM alert_event
                   WHERE alert_name IN ({placeholders}) AND id>%s
                   ORDER BY id DESC LIMIT 1""",
                (*alert_names, after_id),
            )
            return cursor.fetchone()

    def _report(self, alert_id: int) -> dict[str, Any] | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT id, alert_id, session_id, evidence, root_cause, report, created_time
                   FROM diagnosis_report WHERE alert_id=%s ORDER BY id DESC LIMIT 1""",
                (alert_id,),
            )
            return cursor.fetchone()

    def _resolved(self, alert_id: int) -> bool:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT status, end_time FROM alert_event WHERE id=%s", (alert_id,))
            row = cursor.fetchone()
            return bool(row and row["status"] == "resolved" and row["end_time"] is not None)

    def _max_alert_id(self) -> int:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COALESCE(MAX(id), 0) AS id FROM alert_event")
            return int(cursor.fetchone()["id"])

    @staticmethod
    def _contains(text: str, terms: list[str]) -> bool:
        lower = text.lower()
        return any(term.lower() in lower for term in terms)

    def _score(self, trial: TrialResult, fault: dict[str, Any], report: dict[str, Any]) -> None:
        report_text = str(report.get("report") or "")
        root_cause = str(report.get("root_cause") or "")
        evidence = str(report.get("evidence") or "")
        terms = list(fault.get("expected_keywords") or [])
        trial.identification_correct = (
            trial.alert_name.lower() in report_text.lower()
            or self._contains(report_text, terms)
        )
        trial.root_cause_match = self._contains(
            f"{root_cause}\n{report_text}", terms
        )
        trial.evidence_consistent = self._contains(
            f"{evidence}\n{report_text}", terms
        )
        trial.diagnosis_success = bool(report_text.strip())

    def run_trial(self, key: str, fault: dict[str, Any], repetition: int) -> TrialResult:
        trial = TrialResult(key, repetition, fault["alert_name"])
        alert_names = self._alert_names(key, fault)
        print(f"[Evaluation]\nStarting fault: {key}", flush=True)
        baseline = self._max_alert_id()
        started = time.monotonic()
        recovery_required = False
        try:
            if self.dry_run:
                trial.status = "dry_run"
                return trial
            if not self._configured(fault):
                trial.status = "not_configured"
                trial.error = "Process stop/start command is not configured"
                return trial
            self.execute_start(fault)
            recovery_required = True
            event = self._wait(
                f"{'/'.join(alert_names)} firing",
                float(self.options["alert_timeout"]),
                lambda: self._latest_alert_lifecycle(alert_names, baseline),
            )
            trial.alert_name = str(event["alert_name"])
            print(f"[Alert]\n{trial.alert_name} firing detected", flush=True)
            trial.alert_id = int(event["id"])
            trial.fingerprint = str(event["fingerprint"])
            report = self._wait(
                f"diagnosis report for alert {trial.alert_id}",
                float(self.options.get("diagnosis_timeout", self.options["alert_timeout"] * 5)),
                lambda: self._report(trial.alert_id or 0),
            )
            print("[Report]\nDiagnosis completed", flush=True)
            trial.session_id = str(report.get("session_id") or "")
            trial.diagnosis_seconds = round(time.monotonic() - started, 3)
            self._score(trial, fault, report)
            trial.status = "completed"
        except Exception as exc:
            trial.error = f"{type(exc).__name__}: {exc}"
        finally:
            if recovery_required:
                try:
                    self.execute_stop(fault)
                    print(f"[Recovery]\n{key} restored", flush=True)
                    if trial.alert_id is not None:
                        self._wait(
                            f"alert {trial.alert_id} resolved",
                            float(self.options["recovery_timeout"]),
                            lambda: self._resolved(trial.alert_id or 0),
                        )
                        trial.resolved = True
                except Exception as exc:
                    suffix = f"recovery failed: {type(exc).__name__}: {exc}"
                    trial.error = f"{trial.error}; {suffix}" if trial.error else suffix
            if not self.dry_run and recovery_required:
                time.sleep(float(self.options.get("settle_seconds", 0)))
        return trial

    def run(self) -> dict[str, Any]:
        self.preflight()
        results = []
        for repetition in range(1, int(self.options["repeat"]) + 1):
            for key, fault in self.faults.items():
                results.append(self.run_trial(key, fault, repetition))
        return build_summary(results)


def build_summary(results: list[TrialResult]) -> dict[str, Any]:
    completed = [item for item in results if item.diagnosis_success]
    durations = [item.diagnosis_seconds for item in completed if item.diagnosis_seconds is not None]
    per_fault: dict[str, Any] = {}
    for fault in sorted({item.fault for item in results}):
        rows = [item for item in results if item.fault == fault]
        success_count = sum(item.diagnosis_success for item in rows)
        per_fault[fault] = {
            "tests": len(rows),
            "successes": success_count,
            "accuracy": round(sum(item.identification_correct for item in rows) / len(rows), 4),
            "success_rate": round(success_count / len(rows), 4),
            "stability": round(success_count / len(rows), 4),
        }
    total = len(results) or 1
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "summary": {
            "total_tests": len(results),
            "successful_tests": len(completed),
            "fault_identification_accuracy": round(sum(r.identification_correct for r in results) / total, 4),
            "root_cause_match_rate": round(sum(r.root_cause_match for r in results) / total, 4),
            "evidence_consistency": round(sum(r.evidence_consistent for r in results) / total, 4),
            "diagnosis_success_rate": round(len(completed) / total, 4),
            "average_diagnosis_time_seconds": round(statistics.mean(durations), 3) if durations else None,
            "resolved_rate": round(sum(r.resolved for r in results) / total, 4),
        },
        "per_fault": per_fault,
        "trials": [asdict(item) for item in results],
    }


def write_results(result: dict[str, Any], json_path: Path, csv_path: Path) -> None:
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = result["trials"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(TrialResult.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run automated AIOps fault evaluations")
    parser.add_argument("--config", default="evaluation/evaluation_config.yaml")
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    config_path = Path(args.config)
    settings = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if args.repetitions is not None:
        settings["evaluation"]["repeat"] = args.repetitions
    runner = EvaluationRunner(settings, dry_run=args.dry_run)
    result = runner.run()
    write_results(
        result,
        Path(settings["evaluation"].get("output_json", "evaluation_result.json")),
        Path(settings["evaluation"].get("output_csv", "evaluation_result.csv")),
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0 if result["summary"]["successful_tests"] == result["summary"]["total_tests"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
