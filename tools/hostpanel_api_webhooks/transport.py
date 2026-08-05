from __future__ import annotations

import dataclasses
import ipaddress
import re
from collections.abc import Mapping

from .validation import ValidationError, positive_int


@dataclasses.dataclass(frozen=True, slots=True)
class DeliveryRequest:
    url: str
    host: str
    port: int
    path: str
    headers: tuple[tuple[str, str], ...]
    body: bytes
    connect_timeout_seconds: int
    read_timeout_seconds: int
    allow_redirects: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.url, str) or not isinstance(self.host, str) or self.port != 443 or not isinstance(self.path, str) or not self.path.startswith("/"):
            raise ValidationError("delivery request target is invalid")
        if not isinstance(self.headers, tuple) or len(self.headers) > 20:
            raise ValidationError("delivery request headers are invalid")
        seen: set[str] = set()
        for item in self.headers:
            if not isinstance(item, tuple) or len(item) != 2 or not all(isinstance(value, str) for value in item):
                raise ValidationError("delivery request headers are invalid")
            name, value = item
            lowered = name.lower()
            if lowered in seen or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", name) or any(ord(ch) < 32 or ord(ch) == 127 for ch in value) or len(value) > 512:
                raise ValidationError("delivery request headers are invalid")
            seen.add(lowered)
        if not isinstance(self.body, bytes) or len(self.body) > 64 << 10:
            raise ValidationError("delivery request body is invalid")
        positive_int(self.connect_timeout_seconds, "connect timeout", 60)
        positive_int(self.read_timeout_seconds, "read timeout", 300)
        if self.allow_redirects is not False:
            raise ValidationError("webhook redirects are forbidden")


@dataclasses.dataclass(frozen=True, slots=True)
class DeliveryResponse:
    status: int
    headers: Mapping[str, str]
    peer_ip: str
    tls_verified: bool
    resolved_ips: tuple[str, ...]
    redirected: bool = False
    body_bytes_read: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.status, int) or isinstance(self.status, bool) or not 100 <= self.status <= 599:
            raise ValidationError("delivery status is invalid")
        ipaddress.ip_address(self.peer_ip)
        if not isinstance(self.resolved_ips, tuple) or not self.resolved_ips or len(self.resolved_ips) > 16:
            raise ValidationError("delivery DNS result is invalid")
        for value in self.resolved_ips:
            ipaddress.ip_address(value)
        if self.peer_ip not in self.resolved_ips:
            raise ValidationError("delivery peer is outside the resolved target")
        if not isinstance(self.headers, Mapping) or len(self.headers) > 100:
            raise ValidationError("delivery response headers are invalid")
        for name, value in self.headers.items():
            if not isinstance(name, str) or not isinstance(value, str) or len(name) > 128 or len(value) > 2048 or "\r" in value or "\n" in value:
                raise ValidationError("delivery response headers are invalid")
        if not isinstance(self.tls_verified, bool) or not isinstance(self.redirected, bool):
            raise ValidationError("delivery transport flags are invalid")
        if not isinstance(self.body_bytes_read, int) or isinstance(self.body_bytes_read, bool) or not 0 <= self.body_bytes_read <= 4096:
            raise ValidationError("delivery response body accounting is invalid")


@dataclasses.dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    status: str
    event_id: str | None = None
    destination_id: str | None = None
    http_status: int | None = None
    retry_at: int | None = None

    def __post_init__(self) -> None:
        if self.status not in {"idle", "delivered", "retry_wait", "failed", "lease_lost"}:
            raise ValidationError("delivery outcome is invalid")
        if self.status == "idle" and any(value is not None for value in (self.event_id, self.destination_id, self.http_status, self.retry_at)):
            raise ValidationError("idle outcome cannot identify a delivery")
