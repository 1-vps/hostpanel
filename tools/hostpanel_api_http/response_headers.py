from __future__ import annotations

from collections.abc import Mapping

from .model import ConfigurationError, RateLimitDecision, header_name, header_value

_CRITICAL_RESPONSE_HEADERS = {
    "cache-control",
    "content-length",
    "content-type",
    "x-content-type-options",
    "x-request-id",
}


class ResponseHeadersMixin:
    @staticmethod
    def _base_headers(request_id: str) -> dict[str, str]:
        return {
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Request-Id": request_id,
        }

    @staticmethod
    def _merge_custom_headers(
        target: dict[str, str], custom: Mapping[str, str]
    ) -> None:
        if not isinstance(custom, Mapping):
            raise ConfigurationError("response headers must be a mapping")
        for raw_name, raw_value in custom.items():
            name = header_name(raw_name)
            value = header_value(raw_value)
            lowered = name.lower()
            if lowered in _CRITICAL_RESPONSE_HEADERS or lowered == "set-cookie":
                raise ConfigurationError(
                    f"route handler cannot override response header: {name}"
                )
            target[name] = value

    @staticmethod
    def _merge_rate_headers(
        target: dict[str, str],
        decisions: list[RateLimitDecision] | tuple[RateLimitDecision, ...],
    ) -> None:
        if not decisions:
            return
        limiting = min(decisions, key=lambda item: (item.remaining, item.reset_at))
        target["RateLimit-Limit"] = str(limiting.limit)
        target["RateLimit-Remaining"] = str(limiting.remaining)
        target["RateLimit-Reset"] = str(limiting.reset_at)
        if not limiting.allowed:
            target["Retry-After"] = str(limiting.retry_after)
