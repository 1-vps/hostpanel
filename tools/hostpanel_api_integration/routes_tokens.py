from __future__ import annotations

from .routes_token_accounts import TokenAccountRouteHandlers
from .routes_token_credentials import TokenCredentialRouteHandlers
from .routes_token_registration import register_token_routes


class TokenRouteHandlers(TokenAccountRouteHandlers, TokenCredentialRouteHandlers):
    pass


__all__ = ("TokenRouteHandlers", "register_token_routes")
