"""SQLite-backed diagnosis history storage for AIOps runs."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from app.utils.timezone import now_shanghai_iso


class DiagnosisHistoryService:
    """Persist completed diagnosis sessions without introducing external dependencies."""

    def __init__(self, db_path: str = "data/diagnosis_history.sqlite3") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnosis_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    alert_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    report TEXT NOT NULL,
                    conclusion TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def save(
        self,
        *,
        session_id: str,
        alert: dict[str, Any],
        evidence: Any,
        report: str,
        conclusion: str,
    ) -> None:
        created_at = now_shanghai_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO diagnosis_history
                    (session_id, alert_json, created_at, evidence_json, report, conclusion)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    json.dumps(alert, ensure_ascii=False),
                    created_at,
                    json.dumps(evidence, ensure_ascii=False, default=str),
                    report,
                    conclusion,
                ),
            )
            conn.commit()
        logger.info("Diagnosis history saved: session_id={}, db={}", session_id, self.db_path)


diagnosis_history_service = DiagnosisHistoryService()
