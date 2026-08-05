from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from hostpanel_api_http import (
    ApiApplication,
    ApiProblem,
    AuthorizationTarget,
    CallbackAccessController,
    ConfigurationError,
    CursorCodec,
    JsonResponse,
    PrincipalContext,
    RateLimitPolicy,
    Request,
    Response,
    Route,
    SqliteRateLimiter,
    WsgiAdapter,
    generate_openapi,
    parse_page_query,
)

NOW = 1_800_000_000
TOKEN = "hp_test_" + "A" * 40


def body(response):
    return json.loads(response.body.decode("utf-8")) if response.body else None


def headers(response):
    return {name.lower(): value for name, value in response.headers}


def target_resolver(principal, request, path_parameters, query_parameters):
    return AuthorizationTarget(
        "tenant-a",
        "site",
        path_parameters.get("site_id", "example.com"),
    )


class AccessRecorder:
    def __init__(self):
        self.events = []
        self.fail_auth = False
        self.fail_authorize = False

    def authenticate(self, raw_token, source_ip, now):
        self.events.append(("authenticate", raw_token, source_ip, now))
        if self.fail_auth:
            raise RuntimeError("secret authentication detail")
        return PrincipalContext("service-a", "service_account", "token-a")

    def authorize(self, principal, required_scope, target, source_ip, now):
        self.events.append(
            ("authorize", principal.principal_id, required_scope, target, source_ip, now)
        )
        if self.fail_authorize:
            raise RuntimeError("secret authorization detail")

    def controller(self):
        return CallbackAccessController(
            authenticate=self.authenticate,
            authorize_target=self.authorize,
        )


def protected_app(*, recorder=None, rate_limiter=None, route_options=None, handler=None):
    recorder = recorder or AccessRecorder()
    application = ApiApplication(
        access_controller=recorder.controller(),
        rate_limiter=rate_limiter,
    )
    options = {
        "method": "GET",
        "path": "/api/v1/sites/{site_id}",
        "operation_id": "getSite",
        "summary": "Read one site",
        "tags": ("Sites",),
        "required_scope": "sites:read",
        "resolve_target": target_resolver,
        "handler": handler
        or (
            lambda context: {
                "principal_id": context.principal.principal_id,
                "site_id": context.path_parameters["site_id"],
                "tenant_id": context.target.tenant_id,
            }
        ),
        "response_schema": {"type": "object"},
    }
    if route_options:
        options.update(route_options)
    application.register(Route(**options))
    return application, recorder
