"""wq#1805: the single UHP error envelope (errors.md §1-3).

One shape for every non-2xx response: {"error": {type, code, message, param, detail}}.
`code` comes from the closed table below (errors.md §3) except for Lumen vendor
codes, which MUST be `lumen_`-prefixed (errors.md §1 rule: "namespace them with a
vendor prefix so a future version of this specification cannot collide with them").
"""
from __future__ import annotations

from fastapi.responses import JSONResponse

# type -> default HTTP status (errors.md §2). `harness_error` is 200: the harness ran
# and failed, so the transport succeeded and the failure is carried in a failed response
# (errors.md §2 "200 (in a failed response)", §3.3). Without this entry every §3.3
# execution code — harness_error, provider_error, timeout — raised KeyError (wq#1852).
TYPE_STATUS = {
    "invalid_request_error": 400,
    "authentication_error": 401,
    "permission_error": 403,
    "rate_limit_error": 429,
    "harness_error": 200,
    "server_error": 500,
}

# closed code table (errors.md §3.1-3.3), each mapped to its error `type`.
CODE_TYPES = {
    "unsupported_protocol_version": "invalid_request_error",
    "invalid_input": "invalid_request_error",
    "harness_not_found": "invalid_request_error",
    "response_not_found": "invalid_request_error",
    "session_not_found": "invalid_request_error",
    "file_not_found": "invalid_request_error",
    "session_expired": "invalid_request_error",
    "harness_mismatch": "invalid_request_error",
    "session_busy": "invalid_request_error",
    "file_too_large": "invalid_request_error",
    "model_unavailable": "invalid_request_error",
    "unsupported_base": "invalid_request_error",
    "missing_credential": "authentication_error",
    "invalid_credential": "authentication_error",
    "insufficient_scope": "permission_error",
    "rate_limited": "rate_limit_error",
    "quota_exhausted": "rate_limit_error",
    "harness_error": "harness_error",
    "harness_unavailable": "server_error",
    "provider_error": "harness_error",
    "timeout": "harness_error",
    "cancelled": "invalid_request_error",
    "preview_unavailable": "server_error",
    "preview_failed": "server_error",
}

# codes whose HTTP status is not the type's default (errors.md §3 "HTTP" column)
CODE_STATUS_OVERRIDE = {
    "unsupported_protocol_version": 400,
    "invalid_input": 400,
    "harness_not_found": 404,
    "response_not_found": 404,
    "session_not_found": 404,
    "file_not_found": 404,
    "session_expired": 404,
    "harness_mismatch": 409,
    "session_busy": 409,
    "file_too_large": 413,
    "model_unavailable": 422,
    "unsupported_base": 422,
    "harness_unavailable": 503,
    "preview_unavailable": 501,
    "preview_failed": 502,
}


def error_response(
    code: str,
    message: str,
    *,
    param: str | None = None,
    detail: dict | None = None,
    status: int | None = None,
) -> JSONResponse:
    """Build the one error envelope. `code` should come from CODE_TYPES for a
    protocol-defined failure, or be `lumen_`-prefixed for a Lumen-specific one."""
    if code in CODE_TYPES:
        err_type = CODE_TYPES[code]
        http_status = status or CODE_STATUS_OVERRIDE.get(code, TYPE_STATUS[err_type])
    else:
        if not code.startswith("lumen_"):
            raise ValueError(f"vendor error code {code!r} must be lumen_-prefixed (errors.md §1)")
        err_type = "server_error" if status is None else _type_for_status(status)
        http_status = status or 500
    body = {"error": {"type": err_type, "code": code, "message": message, "param": param, "detail": detail}}
    return JSONResponse(status_code=http_status, content=body)


def _type_for_status(status: int) -> str:
    for t, s in TYPE_STATUS.items():
        if s == status:
            return t
    return "server_error"
