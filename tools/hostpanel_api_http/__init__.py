"""HostPanel versioned JSON API transport and contract primitives."""

from .auth import CallbackAccessController, parse_bearer_authorization
from .cursor import CursorCodec
from .jsonio import canonical_json_bytes, parse_json_object
from .model import (
    AccessGrant,
    ApiProblem,
    AuthorizationTarget,
    ConfigurationError,
    JsonResponse,
    PrincipalContext,
    RateLimitDecision,
    RateLimitPolicy,
    Request,
    RequestContext,
    Response,
)
from .openapi import generate_openapi
from .pagination import PageRequest, parse_page_query
from .rate_limit import SqliteRateLimiter
from .routing import ApiApplication, Route
from .wsgi import WsgiAdapter

__all__ = (
    "AccessGrant",
    "ApiApplication",
    "ApiProblem",
    "AuthorizationTarget",
    "CallbackAccessController",
    "ConfigurationError",
    "CursorCodec",
    "JsonResponse",
    "PageRequest",
    "PrincipalContext",
    "RateLimitDecision",
    "RateLimitPolicy",
    "Request",
    "RequestContext",
    "Response",
    "Route",
    "SqliteRateLimiter",
    "WsgiAdapter",
    "canonical_json_bytes",
    "generate_openapi",
    "parse_bearer_authorization",
    "parse_json_object",
    "parse_page_query",
)
