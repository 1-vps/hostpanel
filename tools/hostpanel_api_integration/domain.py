from __future__ import annotations

from hostpanel_api_http.model import ApiProblem


def map_domain_error(exc: Exception, *, missing_is_not_found: bool = False) -> ApiProblem:
    name = exc.__class__.__name__
    if name in {"ValidationError"}:
        return ApiProblem(400, "invalid_request", "The request violates the operation contract.")
    if name in {"ConflictError"}:
        return ApiProblem(409, "conflict", "The request conflicts with current state.")
    if name in {"StateError"}:
        if missing_is_not_found and "does not exist" in str(exc).lower():
            return ApiProblem(404, "not_found", "The requested resource was not found.")
        return ApiProblem(409, "invalid_state", "The resource is not in a valid state for this operation.")
    return ApiProblem(500, "internal_error", "The request could not be completed.")
