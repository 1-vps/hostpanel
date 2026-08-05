from __future__ import annotations

import ipaddress
import re
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from .model import ValidationError

_HOST_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z")
_RESERVED_SUFFIXES = (".localhost", ".local", ".internal", ".invalid", ".test", ".example")


def normalize_webhook_url(value: str) -> tuple[str, str, int, str]:
    if not isinstance(value, str) or len(value) > 2048 or any(ord(ch) < 33 or ord(ch) == 127 for ch in value):
        raise ValidationError("webhook URL is invalid")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ValidationError("webhook URL is invalid") from exc
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValidationError("webhook URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None or parsed.fragment or parsed.query:
        raise ValidationError("webhook URL contains a forbidden component")
    try:
        host = parsed.hostname
        port = parsed.port or 443
    except ValueError as exc:
        raise ValidationError("webhook URL is invalid") from exc
    if host is None or port != 443:
        raise ValidationError("webhook URL must use the default HTTPS port")
    host = host.rstrip(".").lower()
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValidationError("webhook hostname is invalid") from exc
    if ascii_host != host or _HOST_RE.fullmatch(ascii_host) is None:
        raise ValidationError("webhook hostname is invalid")
    if ascii_host == "localhost" or ascii_host.endswith(_RESERVED_SUFFIXES):
        raise ValidationError("webhook hostname is not public")
    try:
        ipaddress.ip_address(ascii_host)
    except ValueError:
        pass
    else:
        raise ValidationError("webhook URL cannot use an IP literal")
    path = parsed.path or "/"
    try:
        decoded = unquote(path, errors="strict")
    except UnicodeError as exc:
        raise ValidationError("webhook path is invalid") from exc
    if not decoded.startswith("/") or "\\" in decoded or any(segment in {".", ".."} for segment in decoded.split("/")):
        raise ValidationError("webhook path is invalid")
    normalized_path = quote(decoded, safe="/-._~!$&'()*+,;=:@")
    normalized = urlunsplit(("https", ascii_host, normalized_path, "", ""))
    if normalized != value:
        raise ValidationError("webhook URL is not canonical")
    return normalized, ascii_host, 443, normalized_path


def validate_peer_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValidationError("webhook peer address is invalid") from exc
    if not address.is_global:
        raise ValidationError("webhook peer address is not public")
    return address.compressed


def validate_resolved_addresses(values: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not values or len(values) > 16:
        raise ValidationError("webhook DNS result is invalid")
    normalized = tuple(validate_peer_ip(value) for value in values)
    if len(set(normalized)) != len(normalized):
        raise ValidationError("webhook DNS result contains duplicates")
    return normalized
