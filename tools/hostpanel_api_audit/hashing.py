from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .model import EVENT_HASH_DOMAIN, GENESIS_HASH_DOMAIN


def genesis_hash() -> bytes:
    return hashlib.sha256(GENESIS_HASH_DOMAIN).digest()


def event_document(fields: Mapping[str, Any]) -> bytes:
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


def event_hash(fields: Mapping[str, Any]) -> bytes:
    return hashlib.sha256(EVENT_HASH_DOMAIN + event_document(fields)).digest()
