"""Resilient Valkey read-through cache with epoch-based namespace invalidation.

Adapted from edilio-vivas (backend/core/cache.py & backend/core/redis.py).
PostgreSQL remains the source of truth; Valkey failures fail open so DB-backed
reads never surface as 5xx errors.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from redis.asyncio import ConnectionPool, Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError, TimeoutError

logger = logging.getLogger("penpot_mcp.cache")

_SAFE_NAMESPACE = re.compile(r"^[a-z0-9_]{1,40}$")


def _require_namespace(namespace: str) -> str:
    if not _SAFE_NAMESPACE.fullmatch(namespace):
        raise ValueError(f"invalid cache namespace: {namespace!r}")
    return namespace


def namespace_epoch_key(namespace: str) -> str:
    """Return the TTL-less epoch counter key (protected by volatile-lfu)."""
    return f"cache:namespace:{_require_namespace(namespace)}:epoch"


class ValkeyCache:
    """Async Valkey 8.1 client wrapper with O(1) epoch invalidation."""

    def __init__(self, url: str = "", default_ttl: int = 120) -> None:
        self.url = url.strip()
        self.default_ttl = max(5, int(default_ttl))
        self._pool: ConnectionPool | None = None
        self._client: Redis | None = None
        self.hits = 0
        self.misses = 0
        self.errors = 0

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    async def connect(self) -> None:
        """Initialize the Valkey connection pool (fail-open if unreachable)."""
        if not self.url:
            return
        try:
            self._pool = ConnectionPool.from_url(
                self.url,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5,
                health_check_interval=30,
                retry=Retry(ExponentialBackoff(base=0.05, cap=0.5), 3),
                retry_on_error=[ConnectionError, TimeoutError],
            )
            self._client = Redis(connection_pool=self._pool)
            await self._client.ping()
            logger.info("Connected to Valkey cache: %s", self.url)
        except Exception as exc:  # noqa: BLE001
            self.errors += 1
            logger.warning("Valkey unavailable (failing open to PostgreSQL): %s", exc)
            self._client = None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        if self._pool is not None:
            await self._pool.disconnect()
            self._pool = None

    async def namespace_epoch(self, namespace: str) -> int:
        """Read current namespace epoch; unavailable cache behaves as epoch 0."""
        ns = _require_namespace(namespace)
        if self._client is None:
            return 0
        try:
            raw = await self._client.get(namespace_epoch_key(ns))
            if raw is None:
                return 0
            val = int(raw)
            return val if val >= 0 else 0
        except Exception:  # noqa: BLE001
            self.errors += 1
            return 0

    async def namespaced_key(self, namespace: str, suffix: str) -> str:
        """Build an epoch-scoped cache key without scanning keys."""
        ns = _require_namespace(namespace)
        safe_suffix = hashlib.sha256(suffix.encode("utf-8")).hexdigest()[:24]
        epoch = await self.namespace_epoch(ns)
        return f"cache:{ns}:{epoch}:{safe_suffix}"

    async def bump_namespace(self, namespace: str) -> int:
        """Invalidate an entire namespace in O(1) without KEYS/SCAN."""
        ns = _require_namespace(namespace)
        if self._client is None:
            return 0
        try:
            return int(await self._client.incr(namespace_epoch_key(ns)))
        except Exception as exc:  # noqa: BLE001
            self.errors += 1
            logger.warning("Valkey bump_namespace(%s) failed: %s", ns, exc)
            return 0

    async def get_json(self, namespace: str, suffix: str) -> Any | None:
        """Read cached JSON payload or return None on miss/error."""
        if self._client is None:
            return None
        try:
            key = await self.namespaced_key(namespace, suffix)
            raw = await self._client.get(key)
            if raw is None:
                self.misses += 1
                return None
            self.hits += 1
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            self.errors += 1
            return None

    async def set_json(
        self,
        namespace: str,
        suffix: str,
        payload: Any,
        ttl_seconds: int | None = None,
    ) -> bool:
        """Best-effort cache write with TTL; PostgreSQL remains source of truth."""
        if self._client is None:
            return False
        try:
            key = await self.namespaced_key(namespace, suffix)
            ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
            await self._client.set(key, json.dumps(payload, default=str), ex=max(1, int(ttl)))
            return True
        except Exception:  # noqa: BLE001
            self.errors += 1
            return False

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "connected": self._client is not None,
            "hits": self.hits,
            "misses": self.misses,
            "errors": self.errors,
            "default_ttl": self.default_ttl,
        }
