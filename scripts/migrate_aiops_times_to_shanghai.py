"""One-time, idempotent migration of historical AIOps UTC test rows to Asia/Shanghai.

Dry-run by default. Pass --apply and a snapshot boundary via --max-alert-id.
Rows are updated in place (+8 hours); nothing is deleted.
"""

from __future__ import annotations

import argparse
from app.config import config
from app.utils.timezone import now_shanghai


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-alert-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    import pymysql

    connection = pymysql.connect(
        host=config.aiops_mysql_host,
        port=config.aiops_mysql_port,
        user=config.aiops_mysql_user,
        password=config.aiops_mysql_password,
        database=config.aiops_mysql_database,
        charset="utf8mb4",
        autocommit=False,
    )
    marker = f"utc_to_shanghai_alert_id_le_{args.max_alert_id}"
    try:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema=%s AND table_name='aiops_time_migration'""",
                (config.aiops_mysql_database,),
            )
            migration_table_exists = bool(cursor.fetchone()[0])
            already_applied = False
            if migration_table_exists:
                cursor.execute("SELECT COUNT(*) FROM aiops_time_migration WHERE migration_key=%s", (marker,))
                already_applied = bool(cursor.fetchone()[0])
            if already_applied:
                print(f"Already applied: {marker}")
                connection.rollback()
                return
            cursor.execute("SELECT COUNT(*) FROM alert_event WHERE id <= %s", (args.max_alert_id,))
            alert_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM diagnosis_report WHERE alert_id <= %s", (args.max_alert_id,))
            report_count = cursor.fetchone()[0]
            print(f"Would migrate alert_event={alert_count}, diagnosis_report={report_count}, boundary={args.max_alert_id}")
            if not args.apply:
                connection.rollback()
                print("Dry-run only; rerun with --apply to commit.")
                return
            cursor.execute("""CREATE TABLE IF NOT EXISTS aiops_time_migration (
                migration_key VARCHAR(128) PRIMARY KEY,
                applied_at DATETIME(6) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cursor.execute("""UPDATE alert_event SET
                start_time=IF(start_time IS NULL, NULL, DATE_ADD(start_time, INTERVAL 8 HOUR)),
                end_time=IF(end_time IS NULL, NULL, DATE_ADD(end_time, INTERVAL 8 HOUR)),
                created_time=DATE_ADD(created_time, INTERVAL 8 HOUR)
                WHERE id <= %s""", (args.max_alert_id,))
            cursor.execute("""UPDATE diagnosis_report SET
                created_time=DATE_ADD(created_time, INTERVAL 8 HOUR)
                WHERE alert_id <= %s""", (args.max_alert_id,))
            cursor.execute(
                "INSERT INTO aiops_time_migration (migration_key, applied_at) VALUES (%s,%s)",
                (marker, now_shanghai().replace(tzinfo=None)),
            )
        connection.commit()
        print(f"Applied: {marker}")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
