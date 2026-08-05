from __future__ import annotations

import re
from collections.abc import Callable, Mapping

from .model import (
    AccessGrant,
    ApiProblem,
    AuthorizationTarget,
    PrincipalContext,
    Request,
    TargetResolver,
    scope,
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9._~-]{20,2048}\Z")


def parse_bearer_authorization(request: Request) -> str:
    value = request.single_header("Authorization")
    if value is None:
        raise ApiProblem(
            401,
            "authentication_required",
            "Bearer authentication is required.",
            headers={"WWW-Authenticate": 'Bearer realm="hostpanel-api"'},
        )
    if value != value.strip():
        raise ApiProblem(
            401,
            "invalid_token",
            "The bearer token is invalid.",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
    parts = value.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise ApiProblem(
            401,
            "invalid_token",
            "The bearer token is invalid.",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
    token = parts[1]
    if _TOKEN_RE.fullmatch(token) is None:
        raise ApiProblem(
            401,
            "invalid_token",
            "The bearer token is invalid.",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
    return token


class CallbackAccessController:
    """Two-phase adapter that authenticates before trusted target resolution."""

    def __init__(
        self,
        *,
        authenticate: Callable[[str, str, int], PrincipalContext],
        authorize_target: Callable[
            [PrincipalContext, str, AuthorizationTarget, str, int], None
        ],
    ) -> None:
        if not callable(authenticate) or not callable(authorize_target):
            raise TypeError("access-controller callbacks must be callable")
        self._authenticate = authenticate
        self._authorize_target = authorize_target

    def authorize(
        self,
        *,
        raw_token: str,
        required_scope: str,
        request: Request,
        path_parameters: Mapping[str, str],
        query_parameters: Mapping[str, tuple[str, ...]],
        resolve_target: TargetResolver,
        now: int,
    ) -> AccessGrant:
        required = scope(required_scope)
        try:
            principal = self._authenticate(raw_token, request.source_ip, now)
        except ApiProblem:
            raise
        except Exception as exc:
            raise ApiProblem(
                401,
                "invalid_token",
                "The bearer token is invalid.",
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            ) from exc
        if not isinstance(principal, PrincipalContext):
            raise TypeError("authentication callback must return PrincipalContext")
        try:
            target = resolve_target(
                principal,
                request,
                path_parameters,
                query_parameters,
            )
        except ApiProblem:
            raise
        except Exception as exc:
            raise ApiProblem(404, "not_found", "The requested resource was not found.") from exc
        if not isinstance(target, AuthorizationTarget):
            raise TypeError("target resolver must return AuthorizationTarget")
        try:
            self._authorize_target(
                principal,
                required,
                target,
                request.source_ip,
                now,
            )
        except ApiProblem:
            raise
        except Exception as exc:
            raise ApiProblem(403, "forbidden", "The requested operation is not permitted.") from exc
        return AccessGrant(principal, target)
