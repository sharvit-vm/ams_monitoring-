import hashlib
import os
from dataclasses import dataclass
from typing import Any

from issuelayer.intake.redis_client import get_redis_client


DEFAULT_DELIVERY_TTL_SECONDS = int(os.getenv("INTAKE_DELIVERY_DEDUPE_TTL_SECONDS", "86400"))
DEFAULT_FINGERPRINT_TTL_SECONDS = int(os.getenv("INTAKE_FINGERPRINT_DEDUPE_TTL_SECONDS", "3600"))


@dataclass
class DedupeResult:
    duplicate: bool
    key: str = ""
    reason: str = ""
    redis_enabled: bool = False


class IntakeDedupe:
    """
    Redis-backed intake idempotency and short-window fingerprint dedupe.

    This is a guard cache, not durable source-of-truth storage. The file-backed
    queue still performs its own durable active-job dedupe.
    """

    def __init__(self, redis_client: Any = None):
        self.redis = redis_client if redis_client is not None else get_redis_client()

    @property
    def enabled(self) -> bool:
        return self.redis is not None

    def claim_delivery(
        self,
        source: str,
        delivery_id: str,
        ttl_seconds: int = DEFAULT_DELIVERY_TTL_SECONDS,
    ) -> DedupeResult:
        if not self.enabled or not delivery_id:
            return DedupeResult(duplicate=False, redis_enabled=self.enabled)

        key = f"intake:delivery:{source}:{delivery_id}"
        return self._set_once(key, ttl_seconds, "duplicate_delivery")

    def claim_payload_hash(
        self,
        source: str,
        payload_bytes: bytes,
        ttl_seconds: int = DEFAULT_DELIVERY_TTL_SECONDS,
    ) -> DedupeResult:
        if not self.enabled or not payload_bytes:
            return DedupeResult(duplicate=False, redis_enabled=self.enabled)

        digest = hashlib.sha256(payload_bytes).hexdigest()
        key = f"intake:payload:{source}:{digest}"
        return self._set_once(key, ttl_seconds, "duplicate_payload")

    def claim_fingerprint(
        self,
        source: str,
        fingerprint: str,
        ttl_seconds: int = DEFAULT_FINGERPRINT_TTL_SECONDS,
    ) -> DedupeResult:
        if not self.enabled or not fingerprint:
            return DedupeResult(duplicate=False, redis_enabled=self.enabled)

        key = f"intake:fingerprint:{source}:{fingerprint}"
        return self._set_once(key, ttl_seconds, "duplicate_fingerprint")

    def _set_once(self, key: str, ttl_seconds: int, reason: str) -> DedupeResult:
        try:
            claimed = self.redis.set(key, "1", nx=True, ex=ttl_seconds)
        except Exception as exc:
            print(f"[redis] dedupe unavailable for key={key}: {exc}")
            return DedupeResult(duplicate=False, key=key, reason="redis_unavailable", redis_enabled=True)

        return DedupeResult(
            duplicate=not bool(claimed),
            key=key,
            reason=reason if not claimed else "",
            redis_enabled=True,
        )
