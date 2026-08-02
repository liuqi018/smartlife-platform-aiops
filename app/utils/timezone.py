"""Canonical Asia/Shanghai business-time helpers."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def now_shanghai_iso() -> str:
    return now_shanghai().replace(tzinfo=None).isoformat(timespec="seconds")


def parse_business_time(value: str) -> datetime | None:
    """Parse RFC3339/ISO input and return an aware Asia/Shanghai datetime."""
    if not value or value.startswith("0001-"):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
    return parsed.astimezone(SHANGHAI_TZ)


def normalize_business_iso(value: str) -> str:
    """Return a stable, timezone-free Asia/Shanghai business timestamp."""
    parsed = parse_business_time(value)
    return parsed.replace(tzinfo=None).isoformat(timespec="seconds") if parsed else ""


def mysql_business_time(value: str) -> datetime | None:
    """Return a naive DATETIME value whose wall clock is Asia/Shanghai."""
    parsed = parse_business_time(value)
    return parsed.replace(tzinfo=None) if parsed else None
