import os
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=1)
def get_redis_client() -> Any:
    """
    Return a singleton Redis client when REDIS_URL is configured.

    Redis is optional for local development. Callers should treat None as
    "Redis disabled" and continue with the durable fallback path.
    """
    if os.getenv("INTAKE_REDIS_DEDUPE_ENABLED", "true").lower() not in ("1", "true", "yes"):
        return None

    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        return None

    try:
        import redis
    except ImportError:
        print("[redis] REDIS_URL is set but the redis package is not installed; dedupe disabled")
        return None

    try:
        return redis.Redis.from_url(redis_url, decode_responses=True)
    except Exception as exc:
        print(f"[redis] Invalid REDIS_URL; dedupe disabled: {exc}")
        return None
