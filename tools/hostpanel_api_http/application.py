from __future__ import annotations

from collections.abc import Callable

from .dispatch import DispatchMixin
from .model import AccessController, ConfigurationError, RateLimiter, clean_text
from .responses import ResponseMixin
from .path import compile_path
from .route import Route, _RegisteredRoute

ErrorReporter = Callable[[str, Exception], None]


class ApiApplication(DispatchMixin, ResponseMixin):
    def __init__(
        self,
        *,
        access_controller: AccessController | None = None,
        rate_limiter: RateLimiter | None = None,
        error_reporter: ErrorReporter | None = None,
        title: str = "HostPanel API",
        version: str = "1.0.0",
    ) -> None:
        if error_reporter is not None and not callable(error_reporter):
            raise TypeError("error reporter must be callable")
        self.access_controller = access_controller
        self.rate_limiter = rate_limiter
        self.error_reporter = error_reporter
        self.title = clean_text(title, "OpenAPI title", maximum=120)
        self.version = clean_text(version, "OpenAPI version", maximum=40)
        self._routes: list[_RegisteredRoute] = []
        self._operations: set[str] = set()
        self._shapes: set[tuple[str, str]] = set()
        self._frozen = False
        self.register(
            Route(
                method="GET",
                path="/api/v1/health",
                operation_id="getApiHealth",
                summary="Read API transport health",
                tags=("System",),
                auth_required=False,
                handler=self._health,
                response_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["api_version", "status"],
                    "properties": {
                        "api_version": {"type": "string", "const": "v1"},
                        "status": {"type": "string", "const": "ok"},
                    },
                },
            )
        )
        self.register(
            Route(
                method="GET",
                path="/api/v1/openapi.json",
                operation_id="getOpenApiContract",
                summary="Read the generated OpenAPI contract",
                tags=("System",),
                auth_required=False,
                handler=self._openapi,
                response_schema={"type": "object"},
            )
        )

    @property
    def routes(self) -> tuple[Route, ...]:
        return tuple(item.route for item in self._routes)

    def register(self, route: Route) -> None:
        if self._frozen:
            raise ConfigurationError("route table is frozen")
        if not isinstance(route, Route):
            raise TypeError("route must be Route")
        regex, parameters, shape = compile_path(route.path)
        shape_key = (route.method, shape)
        if route.operation_id in self._operations:
            raise ConfigurationError("route operation IDs must be unique")
        if shape_key in self._shapes:
            raise ConfigurationError("route method and path shape must be unique")
        self._operations.add(route.operation_id)
        self._shapes.add(shape_key)
        self._routes.append(_RegisteredRoute(route, regex, parameters, shape))

    def freeze(self) -> None:
        self._routes.sort(key=lambda item: (item.route.path, item.route.method))
        self._frozen = True

