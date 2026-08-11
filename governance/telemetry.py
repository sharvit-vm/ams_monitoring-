from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


_SENSITIVE_KEYS = {"authorization", "token", "secret", "password", "api_key", "apikey"}


def emit_governance_event(event_name: str, **fields: Any) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event_name,
        **{key: _redact_value(key, value) for key, value in fields.items()},
    }
    print(f"[governance] {json.dumps(payload, default=str, sort_keys=True)}")


def _redact_value(key: str, value: Any) -> Any:
    if any(marker in key.lower() for marker in _SENSITIVE_KEYS):
        return "***REDACTED***"
    if isinstance(value, dict):
        return {child_key: _redact_value(child_key, child_value) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_redact_value(key, item) for item in value]
    return value
