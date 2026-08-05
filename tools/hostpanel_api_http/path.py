from __future__ import annotations

import re

from .model import ConfigurationError

_PATH_PARAMETER_RE = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
_STATIC_SEGMENT_RE = re.compile(r"[A-Za-z0-9._~-]{1,128}\Z")


def compile_path(path: str) -> tuple[re.Pattern[str], tuple[str, ...], str]:
    if not isinstance(path, str) or not path.startswith("/api/v1/"):
        raise ConfigurationError("routes must be rooted under /api/v1/")
    if len(path.encode("ascii", errors="ignore")) != len(path) or len(path) > 2048:
        raise ConfigurationError("route path is invalid")
    if path.endswith("/") or "//" in path or "\\" in path or "%" in path:
        raise ConfigurationError("route path is invalid")
    parameter_names: list[str] = []
    regex_parts: list[str] = []
    shape_parts: list[str] = []
    for segment in path.split("/")[1:]:
        if segment.startswith("{") and segment.endswith("}"):
            name = segment[1:-1]
            if _PATH_PARAMETER_RE.fullmatch(name) is None or name in parameter_names:
                raise ConfigurationError("route path parameter is invalid")
            parameter_names.append(name)
            regex_parts.append(
                f"(?P<{name}>[A-Za-z0-9][A-Za-z0-9_.:@+~-]{{0,127}})"
            )
            shape_parts.append("{}")
        else:
            if _STATIC_SEGMENT_RE.fullmatch(segment) is None:
                raise ConfigurationError("route path segment is invalid")
            regex_parts.append(re.escape(segment))
            shape_parts.append(segment)
    regex = re.compile(r"/" + r"/".join(regex_parts) + r"\Z")
    shape = "/" + "/".join(shape_parts)
    return regex, tuple(parameter_names), shape
