from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from .db import GatewayDB


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


@dataclass(frozen=True)
class HardeningSettings:
    postgres_pool_min_size: int = 0
    postgres_pool_max_size: int = 0
    postgres_pool_timeout_seconds: float = 10.0
    principal_rate_limit_per_minute: int = 0
    admin_rate_limit_per_minute: int = 0
    enrollment_rate_limit_per_minute: int = 0

    @classmethod
    def from_env(cls) -> "HardeningSettings":
        max_size = _bounded_int("OWG_GATEWAY_DB_POOL_MAX_SIZE", 0, 0, 100)
        min_size = _bounded_int("OWG_GATEWAY_DB_POOL_MIN_SIZE", 0, 0, 100)
        if max_size == 0:
            min_size = 0
        else:
            min_size = min(min_size, max_size)
        return cls(
            postgres_pool_min_size=min_size,
            postgres_pool_max_size=max_size,
            postgres_pool_timeout_seconds=_bounded_float(
                "OWG_GATEWAY_DB_POOL_TIMEOUT_SECONDS", 10.0, 1.0, 120.0
            ),
            principal_rate_limit_per_minute=_bounded_int(
                "OWG_GATEWAY_PRINCIPAL_RATE_LIMIT_PER_MINUTE", 0, 0, 100_000
            ),
            admin_rate_limit_per_minute=_bounded_int(
                "OWG_GATEWAY_ADMIN_RATE_LIMIT_PER_MINUTE", 0, 0, 100_000
            ),
            enrollment_rate_limit_per_minute=_bounded_int(
                "OWG_GATEWAY_ENROLLMENT_RATE_LIMIT_PER_MINUTE", 0, 0, 100_000
            ),
        )


class SlidingWindowRateLimiter:
    """Small bounded in-process overload guard.

    A zero limit disables a bucket. This intentionally does not pretend to be a
    distributed DDoS/WAF control: multi-replica deployments should also enforce
    organization-wide limits at their customer-controlled proxy/load balancer.
    """

    def __init__(self, *, window_seconds: float = 60.0, max_keys: int = 10_000) -> None:
        self.window_seconds = max(1.0, float(window_seconds))
        self.max_keys = max(100, min(int(max_keys), 100_000))
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, *, now: float | None = None) -> tuple[bool, int]:
        if int(limit) <= 0:
            return True, 0
        current = time.monotonic() if now is None else float(now)
        cutoff = current - self.window_seconds
        normalized = str(key)
        with self._lock:
            bucket = self._events.get(normalized)
            if bucket is None:
                if len(self._events) >= self.max_keys:
                    # Dicts preserve insertion order. Evicting the oldest key
                    # bounds memory even if unauthenticated callers rotate
                    # arbitrary bearer strings before the auth layer rejects them.
                    oldest = next(iter(self._events), None)
                    if oldest is not None:
                        self._events.pop(oldest, None)
                bucket = deque()
                self._events[normalized] = bucket
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= int(limit):
                retry_after = max(1, int(math.ceil(bucket[0] + self.window_seconds - current)))
                return False, retry_after
            bucket.append(current)
        return True, 0


def bearer_fingerprint(value: str | None) -> str:
    raw = str(value or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    if not raw:
        return "anonymous"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PooledGatewayDB(GatewayDB):
    """GatewayDB-compatible PostgreSQL pooling, disabled unless max_size > 0."""

    def __init__(
        self,
        database_url: str,
        *,
        pool_min_size: int = 0,
        pool_max_size: int = 0,
        pool_timeout_seconds: float = 10.0,
    ) -> None:
        super().__init__(database_url)
        max_size = max(0, min(int(pool_max_size), 100))
        min_size = max(0, min(int(pool_min_size), max_size if max_size else 0))
        self.pool_min_size = min_size
        self.pool_max_size = max_size
        self.pool_timeout_seconds = max(1.0, min(float(pool_timeout_seconds), 120.0))
        self._pool: Any | None = None
        self._pool_lock = threading.Lock()

    @property
    def pooling_enabled(self) -> bool:
        return bool(self.is_postgres and self.pool_max_size > 0)

    def _ensure_pool(self):
        if not self.pooling_enabled:
            return None
        if self._pool is not None:
            return self._pool
        with self._pool_lock:
            if self._pool is None:
                try:
                    from psycopg_pool import ConnectionPool
                except ImportError as exc:
                    raise RuntimeError(
                        "PostgreSQL connection pooling requires `pip install '.[gateway]'`"
                    ) from exc
                self._pool = ConnectionPool(
                    conninfo=self.database_url,
                    min_size=self.pool_min_size,
                    max_size=self.pool_max_size,
                    timeout=self.pool_timeout_seconds,
                    open=True,
                )
        return self._pool

    @contextmanager
    def connect(self):
        if not self.pooling_enabled:
            with super().connect() as conn:
                yield conn
            return

        pool = self._ensure_pool()
        with pool.connection(timeout=self.pool_timeout_seconds) as conn:
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def close(self) -> None:
        with self._pool_lock:
            pool = self._pool
            self._pool = None
        if pool is not None:
            pool.close()

    def list_token_metadata(
        self,
        *,
        organization_id: str,
        token_type: str | None = None,
        include_revoked: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = ["organization_id = ?"]
        params: list[Any] = [str(organization_id)]
        if token_type:
            clauses.append("token_type = ?")
            params.append(str(token_type))
        if not include_revoked:
            clauses.append("revoked_at IS NULL")
        params.append(max(1, min(int(limit), 1000)))
        sql = (
            "SELECT token_id, token_type, organization_id, actor_id, device_id, "
            "scopes_json, created_at, revoked_at FROM access_tokens WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC, token_id ASC LIMIT ?"
        )
        with self.connect() as conn:
            cur = self._execute(conn, sql, tuple(params))
            rows = cur.fetchall()
            columns = [d[0] for d in cur.description] if cur.description else None
        result: list[dict[str, Any]] = []
        for row in rows:
            data = self._row(row, columns)
            try:
                scopes = sorted({str(x) for x in json.loads(data.pop("scopes_json") or "[]")})
            except Exception:
                scopes = []
            data["scopes"] = scopes
            data["active"] = not bool(data.get("revoked_at"))
            result.append(data)
        return result

    def revoke_device_tokens(self, *, organization_id: str, device_id: str) -> int:
        from .db import _now

        with self.connect() as conn:
            cur = self._execute(
                conn,
                "UPDATE access_tokens SET revoked_at = ? WHERE organization_id = ? "
                "AND device_id = ? AND token_type = 'device' AND revoked_at IS NULL",
                (_now(), str(organization_id), str(device_id)),
            )
            return max(0, int(cur.rowcount or 0))
