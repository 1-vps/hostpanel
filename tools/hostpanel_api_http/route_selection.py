from __future__ import annotations

from collections.abc import Mapping

from .model import ApiProblem, ConfigurationError, RateLimitDecision, RateLimitPolicy
from .route import Route, _RegisteredRoute


class RouteSelectionMixin:
    def _select_route(
        self, path: str, method: str
    ) -> tuple[_RegisteredRoute, Mapping[str, str]]:
        path_matches = []
        for registered in self._routes:
            match = registered.regex.fullmatch(path)
            if match is not None:
                path_matches.append((registered, match))
        if not path_matches:
            raise ApiProblem(404, "not_found", "The requested API route was not found.")
        requested = "GET" if method == "HEAD" else method
        for registered, match in path_matches:
            if registered.route.method == requested:
                return registered, dict(match.groupdict())
        allowed = {registered.route.method for registered, _ in path_matches}
        if "GET" in allowed:
            allowed.add("HEAD")
        raise ApiProblem(
            405,
            "method_not_allowed",
            "The request method is not allowed for this route.",
            headers={"Allow": ", ".join(sorted(allowed))},
        )

    def _consume_rate_limit(
        self,
        *,
        identity: str,
        route: Route,
        policy: RateLimitPolicy,
        now: int,
    ) -> RateLimitDecision:
        if self.rate_limiter is None:
            raise ConfigurationError(
                f"route {route.operation_id} requires a configured rate limiter"
            )
        return self.rate_limiter.consume(
            identity=identity,
            operation_id=route.operation_id,
            policy=policy,
            now=now,
        )
