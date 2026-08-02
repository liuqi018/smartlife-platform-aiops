"""MySQL persistence for alert events and diagnosis reports."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from loguru import logger

from app.config import config
from app.utils.timezone import mysql_business_time, now_shanghai


class AlertHistoryService:
    def __init__(self) -> None:
        self.available = False
        self._driver = None

    def initialize(self) -> bool:
        """Connect and initialize the dedicated AIOps schema during startup."""
        try:
            import pymysql

            self._driver = pymysql
            bootstrap = self._connect(database=None)
            with bootstrap.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{config.aiops_mysql_database}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            bootstrap.close()
            self._init_schema()
            self.available = True
            with self._connect(config.aiops_mysql_database) as connection, connection.cursor() as cursor:
                cursor.execute("SELECT @@hostname")
                server_hostname = cursor.fetchone()[0]
            logger.info(
                "AIOps MySQL initialized: host={} port={} database={} server={}",
                config.aiops_mysql_host,
                config.aiops_mysql_port,
                config.aiops_mysql_database,
                server_hostname,
            )
            return True
        except Exception as exc:
            self.available = False
            if not config.aiops_storage_fallback:
                raise
            logger.warning("MySQL AIOps history unavailable; diagnosis history will not be persisted: {}", exc)
            return False

    def _connect(self, database: str | None = None):
        connection = self._driver.connect(
            host=config.aiops_mysql_host,
            port=config.aiops_mysql_port,
            user=config.aiops_mysql_user,
            password=config.aiops_mysql_password,
            database=database,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=3,
        )
        with connection.cursor() as cursor:
            cursor.execute("SET time_zone = '+08:00'")
        return connection

    def _init_schema(self) -> None:
        with self._connect(config.aiops_mysql_database) as conn, conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alert_event (
                    id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    fingerprint VARCHAR(128) NOT NULL,
                    alert_name VARCHAR(255) NOT NULL,
                    service VARCHAR(255) NOT NULL,
                    instance VARCHAR(255) NOT NULL DEFAULT 'unknown',
                    severity VARCHAR(64) NOT NULL,
                    status VARCHAR(32) NOT NULL,
                    start_time DATETIME(6) NULL,
                    end_time DATETIME(6) NULL,
                    created_time DATETIME(6) NOT NULL,
                    INDEX idx_alert_fingerprint (fingerprint),
                    INDEX idx_alert_created (created_time),
                    UNIQUE KEY uq_alert_lifecycle (fingerprint, start_time)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cursor.execute(
                """SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema=%s AND table_name='alert_event' AND column_name='instance'""",
                (config.aiops_mysql_database,),
            )
            if not cursor.fetchone()[0]:
                cursor.execute(
                    "ALTER TABLE alert_event ADD COLUMN instance VARCHAR(255) "
                    "NOT NULL DEFAULT 'unknown' AFTER service"
                )
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS diagnosis_report (
                    id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    alert_id BIGINT NOT NULL,
                    session_id VARCHAR(255) NULL,
                    evidence LONGTEXT NOT NULL,
                    root_cause TEXT NOT NULL,
                    suggestion TEXT NOT NULL,
                    report LONGTEXT NOT NULL,
                    created_time DATETIME(6) NOT NULL,
                    INDEX idx_report_alert (alert_id),
                    INDEX idx_report_session (session_id),
                    CONSTRAINT fk_report_alert FOREIGN KEY (alert_id) REFERENCES alert_event(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cursor.execute(
                """SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema=%s AND table_name='diagnosis_report' AND column_name='session_id'""",
                (config.aiops_mysql_database,),
            )
            if not cursor.fetchone()[0]:
                cursor.execute(
                    "ALTER TABLE diagnosis_report ADD COLUMN session_id VARCHAR(255) NULL AFTER alert_id"
                )
            cursor.execute(
                """SELECT COUNT(*) FROM information_schema.statistics
                WHERE table_schema=%s AND table_name='diagnosis_report' AND index_name='idx_report_session'""",
                (config.aiops_mysql_database,),
            )
            if not cursor.fetchone()[0]:
                cursor.execute("CREATE INDEX idx_report_session ON diagnosis_report(session_id)")
            cursor.execute(
                """SELECT COUNT(*) FROM information_schema.statistics
                WHERE table_schema=%s AND table_name='alert_event'
                AND index_name='uq_alert_lifecycle' AND non_unique=0""",
                (config.aiops_mysql_database,),
            )
            if not cursor.fetchone()[0]:
                # Preserve the resolved state and every diagnosis report before
                # consolidating historical duplicate lifecycle rows.
                cursor.execute(
                    """UPDATE alert_event keeper
                    JOIN (
                        SELECT fingerprint, start_time, MIN(id) AS keep_id,
                               MAX(status='resolved') AS has_resolved,
                               MAX(end_time) AS latest_end_time
                        FROM alert_event
                        WHERE start_time IS NOT NULL
                        GROUP BY fingerprint, start_time
                        HAVING COUNT(*) > 1
                    ) duplicate_group ON keeper.id=duplicate_group.keep_id
                    SET keeper.status=IF(duplicate_group.has_resolved, 'resolved', keeper.status),
                        keeper.end_time=COALESCE(duplicate_group.latest_end_time, keeper.end_time)"""
                )
                cursor.execute(
                    """UPDATE diagnosis_report report
                    JOIN alert_event duplicate_event ON report.alert_id=duplicate_event.id
                    JOIN (
                        SELECT fingerprint, start_time, MIN(id) AS keep_id
                        FROM alert_event
                        WHERE start_time IS NOT NULL
                        GROUP BY fingerprint, start_time
                        HAVING COUNT(*) > 1
                    ) duplicate_group
                      ON duplicate_event.fingerprint=duplicate_group.fingerprint
                     AND duplicate_event.start_time=duplicate_group.start_time
                    SET report.alert_id=duplicate_group.keep_id
                    WHERE duplicate_event.id<>duplicate_group.keep_id"""
                )
                cursor.execute(
                    """DELETE duplicate_event FROM alert_event duplicate_event
                    JOIN alert_event keeper
                      ON duplicate_event.fingerprint=keeper.fingerprint
                     AND duplicate_event.start_time=keeper.start_time
                     AND duplicate_event.id>keeper.id"""
                )
                cursor.execute(
                    "CREATE UNIQUE INDEX uq_alert_lifecycle "
                    "ON alert_event(fingerprint, start_time)"
                )

    @staticmethod
    def _time(value: str) -> datetime | None:
        return mysql_business_time(value)

    def save_alert_event(self, alert: dict[str, Any]) -> int | None:
        if not self.available:
            return None
        with self._connect(config.aiops_mysql_database) as conn, conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO alert_event
                (fingerprint, alert_name, service, instance, severity, status, start_time, end_time, created_time)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)""",
                (
                    alert.get("fingerprint", ""), alert.get("alert_name", "UnknownAlert"),
                    alert.get("service", "unknown"), alert.get("instance", "unknown"),
                    alert.get("severity", "unknown"),
                    alert.get("status", "firing"), self._time(str(alert.get("start_time", ""))),
                    self._time(str(alert.get("end_time", ""))), now_shanghai().replace(tzinfo=None),
                ),
            )
            return int(cursor.lastrowid)

    def find_firing_alert_id(self, fingerprint: str, start_time: str) -> int | None:
        """Return the persisted firing lifecycle id used by startup recovery."""
        if not self.available:
            return None
        with self._connect(config.aiops_mysql_database) as conn, conn.cursor() as cursor:
            cursor.execute(
                """SELECT id FROM alert_event
                WHERE fingerprint=%s AND start_time=%s AND status='firing'
                LIMIT 1""",
                (fingerprint, self._time(start_time)),
            )
            row = cursor.fetchone()
            return int(row[0]) if row else None

    def list_firing_lifecycles(self, limit: int = 1000) -> list[dict[str, Any]]:
        """Return persisted firing identities for reconciliation snapshots."""
        if not self.available:
            return []
        with self._connect(config.aiops_mysql_database) as conn, conn.cursor(
            self._driver.cursors.DictCursor
        ) as cursor:
            cursor.execute(
                """SELECT id AS alert_id, fingerprint, alert_name, service,
                          start_time AS startsAt
                   FROM alert_event WHERE status='firing'
                   ORDER BY id LIMIT %s""",
                (limit,),
            )
            return list(cursor.fetchall())

    def restore_active_alert_event(self, alert: dict[str, Any]) -> int | None:
        """Upsert AlertManager-authoritative active state during recovery."""
        if not self.available:
            return None
        with self._connect(config.aiops_mysql_database) as conn, conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO alert_event
                (fingerprint, alert_name, service, instance, severity, status, start_time, end_time, created_time)
                VALUES (%s,%s,%s,%s,%s,'firing',%s,NULL,%s)
                ON DUPLICATE KEY UPDATE
                    id=LAST_INSERT_ID(id), status='firing', end_time=NULL""",
                (
                    alert.get("fingerprint", ""),
                    alert.get("alert_name", "UnknownAlert"),
                    alert.get("service", "unknown"),
                    alert.get("instance", "unknown"),
                    alert.get("severity", "unknown"),
                    self._time(str(alert.get("start_time", ""))),
                    now_shanghai().replace(tzinfo=None),
                ),
            )
            return int(cursor.lastrowid)

    def save_diagnosis_report(
        self,
        alert_id: int | None,
        session_id: str,
        evidence: Any,
        report: str,
    ) -> None:
        if not self.available or alert_id is None:
            return
        with self._connect(config.aiops_mysql_database) as conn, conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO diagnosis_report
                (alert_id, session_id, evidence, root_cause, suggestion, report, created_time)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (alert_id, session_id, json.dumps(evidence, ensure_ascii=False, default=str), report[:1000], report[-1000:], report,
                 now_shanghai().replace(tzinfo=None)),
            )

    def resolve_alert_event(
        self,
        alert_id: int | None,
        fingerprint: str,
        start_time: str,
        end_time: str,
    ) -> bool:
        """Close the existing firing event instead of creating Redis history."""
        if not self.available:
            return False
        with self._connect(config.aiops_mysql_database) as conn, conn.cursor() as cursor:
            if alert_id is None:
                cursor.execute(
                    """UPDATE alert_event SET status=%s, end_time=%s
                    WHERE fingerprint=%s AND start_time=%s AND status=%s""",
                    ("resolved", self._time(end_time), fingerprint, self._time(start_time), "firing"),
                )
            else:
                cursor.execute(
                    """UPDATE alert_event SET status=%s, end_time=%s
                    WHERE id=%s AND fingerprint=%s AND status=%s""",
                    (
                        "resolved", self._time(end_time), alert_id, fingerprint, "firing",
                    ),
                )
                # A stale/missing Redis alert_id must not leave the matching
                # persisted lifecycle firing. Fall back to its stable identity.
                if cursor.rowcount != 1:
                    cursor.execute(
                        """UPDATE alert_event SET status=%s, end_time=%s
                        WHERE fingerprint=%s AND start_time=%s AND status=%s""",
                        (
                            "resolved", self._time(end_time), fingerprint,
                            self._time(start_time), "firing",
                        ),
                    )
            if cursor.rowcount == 1:
                return True

            # A reconciliation cycle may have closed the same lifecycle before
            # the resolved webhook arrives. Treat that as idempotent success.
            if alert_id is not None:
                cursor.execute(
                    """SELECT 1 FROM alert_event
                    WHERE id=%s AND fingerprint=%s AND status='resolved' LIMIT 1""",
                    (alert_id, fingerprint),
                )
            else:
                cursor.execute(
                    """SELECT 1 FROM alert_event
                    WHERE fingerprint=%s AND start_time=%s AND status='resolved' LIMIT 1""",
                    (fingerprint, self._time(start_time)),
                )
            return cursor.fetchone() is not None

    def dashboard_summary(self) -> dict[str, int]:
        """Return the compact counters needed by the operations dashboard."""
        if not self.available:
            return {"total": 0, "firing": 0, "resolved": 0, "diagnoses_today": 0}
        with self._connect(config.aiops_mysql_database) as conn, conn.cursor() as cursor:
            cursor.execute(
                """SELECT COUNT(*),
                          SUM(status='firing'),
                          SUM(status='resolved')
                   FROM alert_event"""
            )
            total, firing, resolved = cursor.fetchone()
            cursor.execute(
                "SELECT COUNT(*) FROM diagnosis_report WHERE DATE(created_time)=CURRENT_DATE()"
            )
            diagnoses_today = cursor.fetchone()[0]
        return {
            "total": int(total or 0),
            "firing": int(firing or 0),
            "resolved": int(resolved or 0),
            "diagnoses_today": int(diagnoses_today or 0),
        }

    def list_alerts(self, *, status: str = "", limit: int = 100) -> list[dict[str, Any]]:
        """List alert lifecycles with their latest diagnosis status."""
        if not self.available:
            return []
        where = "WHERE event.status=%s" if status in {"firing", "resolved"} else ""
        params: tuple[Any, ...] = (status, limit) if where else (limit,)
        with self._connect(config.aiops_mysql_database) as conn, conn.cursor(
            self._driver.cursors.DictCursor
        ) as cursor:
            cursor.execute(
                f"""SELECT event.id, event.fingerprint, event.alert_name AS alertname,
                           event.severity, event.service, event.instance,
                           event.status, event.start_time AS startsAt,
                           event.end_time AS endsAt, event.created_time,
                           report.session_id,
                           CASE WHEN report.id IS NULL THEN 'pending' ELSE 'completed' END diagnosis_status
                    FROM alert_event event
                    LEFT JOIN diagnosis_report report ON report.id=(
                        SELECT report2.id FROM diagnosis_report report2
                        WHERE report2.alert_id=event.id ORDER BY report2.id DESC LIMIT 1
                    )
                    {where}
                    ORDER BY event.created_time DESC LIMIT %s""",
                params,
            )
            return list(cursor.fetchall())

    def get_alert_detail(self, alert_id: int) -> dict[str, Any] | None:
        """Return one alert lifecycle and its latest persisted diagnosis."""
        if not self.available:
            return None
        with self._connect(config.aiops_mysql_database) as conn, conn.cursor(
            self._driver.cursors.DictCursor
        ) as cursor:
            cursor.execute(
                """SELECT event.id, event.fingerprint, event.alert_name AS alertname,
                          event.severity, event.service, event.instance,
                          event.status, event.start_time AS startsAt,
                          event.end_time AS endsAt, event.created_time,
                          report.session_id, report.evidence, report.report,
                          report.root_cause, report.suggestion,
                          report.created_time AS diagnosis_created_at
                   FROM alert_event event
                   LEFT JOIN diagnosis_report report ON report.id=(
                       SELECT report2.id FROM diagnosis_report report2
                       WHERE report2.alert_id=event.id ORDER BY report2.id DESC LIMIT 1
                   )
                   WHERE event.id=%s""",
                (alert_id,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        try:
            row["evidence"] = json.loads(row.get("evidence") or "[]")
        except (TypeError, json.JSONDecodeError):
            row["evidence"] = []
        row["diagnosis_status"] = "completed" if row.get("report") else "pending"
        return row

    def list_diagnosis_reports(self, limit: int = 100) -> list[dict[str, Any]]:
        """List persisted diagnosis reports joined with their incident context."""
        if not self.available:
            return []
        with self._connect(config.aiops_mysql_database) as conn, conn.cursor(
            self._driver.cursors.DictCursor
        ) as cursor:
            cursor.execute(
                """SELECT report.id, report.alert_id, report.session_id,
                          event.alert_name AS alert_name, event.service, event.severity,
                          event.status AS alert_status, event.status AS status,
                          'completed' AS diagnosis_status, report.root_cause,
                          report.report, report.created_time
                   FROM diagnosis_report report
                   JOIN alert_event event ON event.id=report.alert_id
                   ORDER BY report.created_time DESC LIMIT %s""",
                (limit,),
            )
            rows = list(cursor.fetchall())
        import re
        for row in rows:
            match = re.search(
                r"(?:confidence|置信度)[^\d]{0,20}(\d{1,3})\s*%",
                str(row.get("report") or ""),
                re.IGNORECASE,
            )
            row["confidence"] = int(match.group(1)) if match else None
            row["created_at"] = row.pop("created_time", None)
        return rows

    def get_diagnosis_report(self, report_id: int) -> dict[str, Any] | None:
        """Return a complete persisted report and its LangGraph evidence."""
        if not self.available:
            return None
        with self._connect(config.aiops_mysql_database) as conn, conn.cursor(
            self._driver.cursors.DictCursor
        ) as cursor:
            cursor.execute(
                """SELECT report.id, report.alert_id, report.session_id,
                          event.alert_name AS alert_name, event.service, event.severity,
                          event.status, event.status AS alert_status,
                          'completed' AS diagnosis_status,
                          event.start_time, event.end_time,
                          report.evidence, report.root_cause, report.suggestion,
                          report.report AS report_content,
                          report.created_time AS created_at
                   FROM diagnosis_report report
                   JOIN alert_event event ON event.id=report.alert_id
                   WHERE report.id=%s""",
                (report_id,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        try:
            row["evidence"] = json.loads(row.get("evidence") or "[]")
        except (TypeError, json.JSONDecodeError):
            row["evidence"] = []
        report_text = str(row.get("report_content") or "")
        import re
        match = re.search(
            r"(?:confidence|置信度)[^\d]{0,20}(\d{1,3})\s*%",
            report_text,
            re.IGNORECASE,
        )
        row["confidence"] = int(match.group(1)) if match else None
        return row


alert_history_service = AlertHistoryService()
