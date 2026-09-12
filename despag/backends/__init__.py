"""wq#1811: despag P1-6 — per-harness backend env selection.

PHASE0_WIRING_SPEC.md RECOMMENDATION step 6 + D.2. A "backend" here only builds the
environment a `claude -p` child needs for a given model; it does not spawn or manage
that child (PHASE0_WIRING_SPEC.md step 2's runner does, once it lands). Keeping env
selection separate from process management is what lets the litellm-mechanical harness
run through the SAME runner instead of a forked one.
"""
from __future__ import annotations


class ProviderError(Exception):
    """The upstream model provider (here: the LiteLLM gateway) refused or failed —
    errors.md §3.2 `provider_error`, a *harness_error*, reported inside a `failed`
    response body (HTTP 200), never as an HTTP error envelope. Raised by a backend
    call; `despag.tiers.run_tier` catches exactly this and turns it into a failed
    Response for the SAME harness it was raised under — it never retries a different
    harness (D.2 budget guard)."""

    def __init__(self, message: str, *, status: int | None = None):
        self.status = status
        super().__init__(message)
