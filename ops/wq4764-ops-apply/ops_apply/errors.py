"""Refusal codes (design §3.4). Every check raises OpsApplyError with one of
these codes; nothing is staged or applied on a refusal."""

CODES = (
    "OPS_IDENTITY",
    "OPS_LEASE",
    "OPS_HOST",
    "OPS_NOT_ALLOWED",
    "OPS_PARAMS",
    "OPS_POLICY",
    "OPS_APPROVAL",
    "OPS_SIGNATURE",
    "OPS_ARGV_DENY",
    "OPS_DIGEST",
    "OPS_TOCTOU",
    "OPS_CAS",
)


class OpsApplyError(RuntimeError):
    """A fail-closed refusal. `code` is always one of CODES."""

    def __init__(self, code, message):
        if code not in CODES:
            raise ValueError(f"unknown refusal code: {code}")
        self.code = code
        super().__init__(f"{code}: {message}")
