"""Alert lifecycle state abstraction with Redis and in-memory implementations."""

from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from app.config import config
from app.utils.timezone import normalize_business_iso


def _canonical_starts_at(starts_at: str) -> str:
    """Canonical lifecycle time used in state and all newly-created claim keys."""
    return normalize_business_iso(str(starts_at or "")) or str(starts_at or "")


class AlertStateManager(ABC):
    @abstractmethod
    def claim_lifecycle(
        self, fingerprint: str, starts_at: str, claimed_at: str
    ) -> tuple[bool, dict[str, Any] | None]: ...

    @abstractmethod
    def save_alert_state(self, fingerprint: str, state: dict[str, Any]) -> None: ...

    @abstractmethod
    def get_alert_state(self, fingerprint: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def update_alert_status(self, fingerprint: str, status: str, **fields: Any) -> dict[str, Any]: ...

    @abstractmethod
    def update_current_status(
        self, fingerprint: str, starts_at: str, status: str, **fields: Any
    ) -> tuple[bool, dict[str, Any] | None]: ...

    @abstractmethod
    def mark_repeat_if_active(self, fingerprint: str, starts_at: str, last_seen_at: str) -> tuple[bool, dict[str, Any] | None]: ...

    @abstractmethod
    def resolve_current(self, fingerprint: str, starts_at: str) -> tuple[bool, dict[str, Any] | None]: ...

    @abstractmethod
    def count_active_alerts(self) -> int: ...

    @abstractmethod
    def list_active_states(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def count_aiops_keys(self) -> int: ...

    @property
    @abstractmethod
    def database(self) -> int: ...


class InMemoryAlertStateManager(AlertStateManager):
    def __init__(self) -> None:
        self._states: dict[str, dict[str, Any]] = {}
        self._claims: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = threading.RLock()

    def claim_lifecycle(
        self, fingerprint: str, starts_at: str, claimed_at: str
    ) -> tuple[bool, dict[str, Any] | None]:
        with self._lock:
            starts_at = _canonical_starts_at(starts_at)
            claim_key = (fingerprint, starts_at)
            current = self._states.get(fingerprint)
            if current and _canonical_starts_at(current.get("startsAt", "")) == starts_at:
                current["last_seen_at"] = claimed_at
                current["repeat_count"] = int(current.get("repeat_count", 0)) + 1
                return False, dict(current)
            if claim_key in self._claims:
                return False, dict(self._claims[claim_key])
            claim = {
                "fingerprint": fingerprint,
                "startsAt": starts_at,
                "status": "firing",
                "diagnosis_status": "claimed",
                "claimed_at": claimed_at,
            }
            self._claims[claim_key] = claim
            return True, dict(claim)

    def save_alert_state(self, fingerprint: str, state: dict[str, Any]) -> None:
        with self._lock:
            normalized = dict(state)
            if normalized.get("startsAt"):
                normalized["startsAt"] = _canonical_starts_at(normalized["startsAt"])
            self._states[fingerprint] = normalized

    def get_alert_state(self, fingerprint: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._states.get(fingerprint)
            return dict(state) if state else None

    def update_alert_status(self, fingerprint: str, status: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            state = self._states.setdefault(fingerprint, {"fingerprint": fingerprint})
            state.update(fields)
            state["status"] = status
            return dict(state)

    def update_current_status(
        self, fingerprint: str, starts_at: str, status: str, **fields: Any
    ) -> tuple[bool, dict[str, Any] | None]:
        with self._lock:
            state = self._states.get(fingerprint)
            if not state or state.get("startsAt") != starts_at:
                return False, dict(state) if state else None
            state.update(fields)
            state["status"] = status
            return True, dict(state)

    def mark_repeat_if_active(self, fingerprint: str, starts_at: str, last_seen_at: str) -> tuple[bool, dict[str, Any] | None]:
        with self._lock:
            state = self._states.get(fingerprint)
            if (
                not state
                or state.get("startsAt") != starts_at
                or state.get("status") != "firing"
                or state.get("diagnosis_status") not in ("running", "completed")
            ):
                return False, dict(state) if state else None
            state["last_seen_at"] = last_seen_at
            state["repeat_count"] = int(state.get("repeat_count", 0)) + 1
            return True, dict(state)

    def resolve_current(self, fingerprint: str, starts_at: str) -> tuple[bool, dict[str, Any] | None]:
        with self._lock:
            state = self._states.get(fingerprint)
            state_starts_at = str((state or {}).get("startsAt") or "")
            self._claims.pop((fingerprint, _canonical_starts_at(starts_at)), None)
            if (
                not state
                or _canonical_starts_at(state_starts_at) != _canonical_starts_at(starts_at)
            ):
                return False, dict(state) if state else None
            resolved_state = dict(state)
            del self._states[fingerprint]
            return True, resolved_state

    def count_active_alerts(self) -> int:
        with self._lock:
            return len(self._states)

    def list_active_states(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(state) for state in self._states.values()]

    def count_aiops_keys(self) -> int:
        return self.count_active_alerts()

    @property
    def database(self) -> int:
        return 1


class ProtectedAIOpsRedisPipeline:
    def __init__(self, pipeline: Any, validate_keys: Any) -> None:
        self._pipeline = pipeline
        self._validate_keys = validate_keys

    def __enter__(self):
        self._pipeline.__enter__()
        return self

    def __exit__(self, *args: Any):
        return self._pipeline.__exit__(*args)

    def delete(self, *keys: Any):
        self._validate_keys(keys)
        self._pipeline.delete(*keys)
        return self

    def unlink(self, *keys: Any):
        self._validate_keys(keys)
        self._pipeline.unlink(*keys)
        return self

    def flushdb(self, *_args: Any, **_kwargs: Any) -> None:
        raise PermissionError("FLUSHDB is forbidden for AIOps Redis")

    def flushall(self, *_args: Any, **_kwargs: Any) -> None:
        raise PermissionError("FLUSHALL is forbidden for AIOps Redis")

    def __getattr__(self, name: str) -> Any:
        if name.lower() in {"flushdb", "flushall"}:
            raise PermissionError(f"{name.upper()} is forbidden for AIOps Redis")
        return getattr(self._pipeline, name)


class ProtectedAIOpsRedis:
    """Redis proxy preventing database-wide or cross-namespace deletion."""

    def __init__(self, client: Any, namespace: str = "aiops") -> None:
        self._client = client
        self._namespace_prefix = f"{namespace.rstrip(':')}:"

    def _validate_keys(self, keys: tuple[Any, ...]) -> None:
        invalid = [str(key) for key in keys if not str(key).startswith(self._namespace_prefix)]
        if invalid:
            raise PermissionError(f"Redis deletion outside AIOps namespace is forbidden: {invalid}")

    def delete(self, *keys: Any) -> int:
        self._validate_keys(keys)
        return self._client.delete(*keys)

    def unlink(self, *keys: Any) -> int:
        self._validate_keys(keys)
        return self._client.unlink(*keys)

    def flushdb(self, *_args: Any, **_kwargs: Any) -> None:
        raise PermissionError("FLUSHDB is forbidden for AIOps Redis")

    def flushall(self, *_args: Any, **_kwargs: Any) -> None:
        raise PermissionError("FLUSHALL is forbidden for AIOps Redis")

    def pipeline(self, *args: Any, **kwargs: Any) -> ProtectedAIOpsRedisPipeline:
        return ProtectedAIOpsRedisPipeline(
            self._client.pipeline(*args, **kwargs),
            self._validate_keys,
        )

    def __getattr__(self, name: str) -> Any:
        if name.lower() in {"flushdb", "flushall"}:
            raise PermissionError(f"{name.upper()} is forbidden for AIOps Redis")
        return getattr(self._client, name)


class RedisAlertStateManager(AlertStateManager):
    def __init__(self, redis_url: str, prefix: str, namespace: str = "aiops") -> None:
        import redis

        parsed = urlparse(redis_url)
        database = int(parsed.path.lstrip("/") or "0")
        if database != 1:
            raise ValueError(f"AIOps Redis must use database 1, configured database={database}")
        if not prefix.startswith(f"{namespace}:"):
            raise ValueError("AIOps Redis prefix must use the configured namespace")
        raw_client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        raw_client.ping()
        self.client = ProtectedAIOpsRedis(raw_client, namespace)
        self.namespace = namespace.rstrip(":")
        self._database = database
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 6379
        self.prefix = prefix.rstrip(":")

    def _key(self, fingerprint: str) -> str:
        return f"{self.prefix}:{fingerprint}"

    def _claim_key(self, fingerprint: str, starts_at: str) -> str:
        return f"{self.namespace}:claim:alert:{fingerprint}:{_canonical_starts_at(starts_at)}"

    def claim_lifecycle(
        self, fingerprint: str, starts_at: str, claimed_at: str
    ) -> tuple[bool, dict[str, Any] | None]:
        """Atomically claim one AlertManager lifecycle with Redis SET NX."""
        starts_at = _canonical_starts_at(starts_at)
        state = self.get_alert_state(fingerprint)
        if state and _canonical_starts_at(state.get("startsAt", "")) == starts_at:
            return False, state

        claim = {
            "fingerprint": fingerprint,
            "startsAt": starts_at,
            "status": "firing",
            "diagnosis_status": "claimed",
            "claimed_at": claimed_at,
        }
        claim_key = self._claim_key(fingerprint, starts_at)
        claimed = self.client.set(
            claim_key,
            json.dumps(claim, ensure_ascii=False, default=str),
            nx=True,
            ex=config.alert_claim_ttl_seconds,
        )
        if claimed:
            return True, claim

        state = self.get_alert_state(fingerprint)
        if state and state.get("startsAt") == starts_at:
            return False, state
        value = self.client.get(claim_key)
        return False, json.loads(value) if value else None

    def save_alert_state(self, fingerprint: str, state: dict[str, Any]) -> None:
        normalized = dict(state)
        if normalized.get("startsAt"):
            normalized["startsAt"] = _canonical_starts_at(normalized["startsAt"])
        self.client.set(
            self._key(fingerprint),
            json.dumps(normalized, ensure_ascii=False, default=str),
        )

    def get_alert_state(self, fingerprint: str) -> dict[str, Any] | None:
        value = self.client.get(self._key(fingerprint))
        return json.loads(value) if value else None

    def update_alert_status(self, fingerprint: str, status: str, **fields: Any) -> dict[str, Any]:
        state = self.get_alert_state(fingerprint) or {"fingerprint": fingerprint}
        state.update(fields)
        state["status"] = status
        self.save_alert_state(fingerprint, state)
        return state

    def update_current_status(
        self, fingerprint: str, starts_at: str, status: str, **fields: Any
    ) -> tuple[bool, dict[str, Any] | None]:
        import redis

        key = self._key(fingerprint)
        while True:
            try:
                with self.client.pipeline() as pipe:
                    pipe.watch(key)
                    value = pipe.get(key)
                    state = json.loads(value) if value else None
                    if not state or state.get("startsAt") != starts_at:
                        pipe.unwatch()
                        return False, state
                    state.update(fields)
                    state["status"] = status
                    pipe.multi()
                    pipe.set(key, json.dumps(state, ensure_ascii=False, default=str))
                    pipe.execute()
                    return True, state
            except redis.WatchError:
                continue

    def mark_repeat_if_active(self, fingerprint: str, starts_at: str, last_seen_at: str) -> tuple[bool, dict[str, Any] | None]:
        import redis

        key = self._key(fingerprint)
        while True:
            try:
                with self.client.pipeline() as pipe:
                    pipe.watch(key)
                    value = pipe.get(key)
                    state = json.loads(value) if value else None
                    if (
                        not state
                        or state.get("startsAt") != starts_at
                        or state.get("status") != "firing"
                        or state.get("diagnosis_status") not in ("running", "completed")
                    ):
                        pipe.unwatch()
                        return False, state
                    state["last_seen_at"] = last_seen_at
                    state["repeat_count"] = int(state.get("repeat_count", 0)) + 1
                    pipe.multi()
                    pipe.set(key, json.dumps(state, ensure_ascii=False, default=str))
                    pipe.execute()
                    return True, state
            except redis.WatchError:
                continue

    def resolve_current(self, fingerprint: str, starts_at: str) -> tuple[bool, dict[str, Any] | None]:
        import redis

        key = self._key(fingerprint)
        while True:
            try:
                with self.client.pipeline() as pipe:
                    pipe.watch(key)
                    value = pipe.get(key)
                    state = json.loads(value) if value else None
                    state_starts_at = str((state or {}).get("startsAt") or "")
                    claim_key = self._claim_key(fingerprint, starts_at)
                    if (
                        not state
                        or _canonical_starts_at(state_starts_at)
                        != _canonical_starts_at(starts_at)
                    ):
                        pipe.unwatch()
                        self.client.delete(claim_key)
                        return False, state
                    pipe.multi()
                    pipe.delete(key)
                    pipe.delete(claim_key)
                    pipe.execute()
                    return True, state
            except redis.WatchError:
                continue

    def count_active_alerts(self) -> int:
        return sum(1 for _ in self.client.scan_iter(match=f"{self.prefix}:*"))

    def list_active_states(self) -> list[dict[str, Any]]:
        states: list[dict[str, Any]] = []
        for key in self.client.scan_iter(match=f"{self.prefix}:*"):
            value = self.client.get(key)
            if not value:
                continue
            try:
                state = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                logger.warning("Ignoring invalid current alert state during reconciliation: key={}", key)
                continue
            if isinstance(state, dict):
                states.append(state)
        return states

    def count_aiops_keys(self) -> int:
        return sum(1 for _ in self.client.scan_iter(match=f"{self.namespace}:*"))

    @property
    def database(self) -> int:
        return self._database


def create_alert_state_manager() -> AlertStateManager:
    try:
        manager = RedisAlertStateManager(
            config.aiops_redis_url,
            config.aiops_redis_prefix,
            config.aiops_redis_namespace,
        )
        logger.info(
            "AIOps Redis connected: host={} port={} database={} namespace={}:* prefix={}",
            manager.host,
            manager.port,
            manager.database,
            config.aiops_redis_namespace,
            config.aiops_redis_prefix,
        )
        return manager
    except Exception as exc:
        if not config.aiops_storage_fallback:
            raise
        logger.warning("Redis unavailable; alert state uses in-memory fallback: {}", exc)
        return InMemoryAlertStateManager()


alert_state_manager = create_alert_state_manager()
