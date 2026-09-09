"""Idempotency-Key handling.

A key alone is not enough: a client that reuses a key with a different body is
a bug on their side, and replaying the old job would hide it. The stored
fingerprint of the request body is compared, and a mismatch is a 409.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IdempotencyConflict(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(f"Idempotency-Key {key} was used with a different request body")
        self.key = key
