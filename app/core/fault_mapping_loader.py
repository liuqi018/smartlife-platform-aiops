"""Fail-safe loader for alert-to-diagnosis strategy configuration."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from loguru import logger


FAULT_MAPPING_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "config" / "fault_mapping.yaml"
)
ALLOWED_FIELDS = {
    "alert_name",
    "category",
    "metrics",
    "range_query",
    "rag_query",
    "runbook_allowlist",
    "report_policy",
    "missing_evidence",
}
LIST_FIELDS = {"metrics", "range_query", "runbook_allowlist", "missing_evidence"}


def _normalize_mapping(name: str, value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        logger.warning("Ignoring invalid fault mapping {}: expected object", name)
        return None
    mapping = {key: deepcopy(item) for key, item in value.items() if key in ALLOWED_FIELDS}
    mapping["alert_name"] = str(mapping.get("alert_name") or name)
    if mapping["alert_name"].lower() != name.lower():
        logger.warning("Ignoring fault mapping {}: alert_name mismatch", name)
        return None
    for field in LIST_FIELDS:
        item = mapping.get(field, [])
        if not isinstance(item, list) or not all(isinstance(entry, str) for entry in item):
            logger.warning("Ignoring fault mapping {}: {} must be a string list", name, field)
            return None
        mapping[field] = item
    for field in ("category", "rag_query", "report_policy"):
        mapping[field] = str(mapping.get(field) or "")
    return mapping


@lru_cache(maxsize=1)
def load_fault_mappings() -> dict[str, dict[str, Any]]:
    """Load validated mappings; return an empty mapping on every failure."""
    try:
        raw = yaml.safe_load(FAULT_MAPPING_PATH.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError("fault mapping root must be an object")
        mappings: dict[str, dict[str, Any]] = {}
        for name, value in raw.items():
            normalized = _normalize_mapping(str(name), value)
            if normalized:
                mappings[normalized["alert_name"].lower()] = normalized
        return mappings
    except Exception as exc:
        logger.warning(
            "Fault mapping unavailable; legacy Python strategy remains active: {}", exc
        )
        return {}


def reload_fault_mappings() -> dict[str, dict[str, Any]]:
    load_fault_mappings.cache_clear()
    return load_fault_mappings()


def get_fault_mapping(alert_name: str) -> dict[str, Any] | None:
    mapping = load_fault_mappings().get(str(alert_name).strip().lower())
    return deepcopy(mapping) if mapping else None


def match_fault_mapping(text: str) -> dict[str, Any] | None:
    lower = str(text).lower()
    exact = get_fault_mapping(str(text).strip())
    if exact:
        return exact
    for mapping in load_fault_mappings().values():
        if mapping["alert_name"].lower() in lower:
            return deepcopy(mapping)
        if mapping.get("rag_query") and mapping["rag_query"].lower() in lower:
            return deepcopy(mapping)
    return None
