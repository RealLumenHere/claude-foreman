"""wq#4508 -- warm-band LLM summaries for oversized tool results.

Limpet's meta-server already mandates "size-capped and summarized" results
before they reach an agent's context
(``design/wq3806_limpet_architecture.md:155-158``: "This mirrors the fleet's
own 2000-char discipline") and the agent-chat UI already renders large
results as a bounded preview plus an evidence path + hash, never the full
blob (``docs/agent-chat-spec.md:144-147``). Neither piece has an
implementation yet (``ops/wq3808-limpet-mcp-meta-server-a1/meta_server.py``
has no capping at all). This module is that implementation's decision core:
given a tool result, decide which band it lands in and what the agent's
context should hold for it. Full spec: ``docs/agent-processor-spec.md`` §6.3.

Hot band (<= ``HOT_THRESHOLD`` chars): the text goes straight into context.

Warm band (over threshold): the full text is written to cold disk under the
workspace's evidence tree and never reaches context; a 3-5 line LLM summary
takes its place. Limpet's agent core and its LiteLLM client are still
design-only (``design/wq3806_limpet_architecture.md`` §6; see the identical
reasoning in ``model_router.py`` and ``critic.py``), so this module does not
place that summarization call itself -- that would be speculatively wiring a
model client that does not exist yet. Instead:

- ``summary_model()`` resolves WHICH model must answer the call -- the fast
  profile the ticket requires, never the reasoning model -- through the same
  house routing decision ``model_router.py`` already owns.
- ``store_result()`` takes the summary text as a parameter once the caller
  (the agent core, once it exists) has placed that call, the same posture as
  ``critic.py``'s ``critic_verdict`` parameter.
"""
from __future__ import annotations

import dataclasses
import hashlib
import os

import workspace_report as wr
from model_router import resolve_profile

HOT_THRESHOLD = 2000
RESULTS_RELDIR = os.path.join("evidence", "results")

# The requirement profile a warm-band summary call must route through
# (model_router.REQUIREMENT_PROFILES) -- "speed-critical", never "long-context"
# or any reasoning-heavy profile. The ticket's "fast model, not the reasoning
# model" constraint is enforced here, once, instead of at every call site.
SUMMARY_PROFILE = "speed-critical"


class ResultStoreError(ValueError):
    """A warm-band result stored without the summary it requires."""


@dataclasses.dataclass(frozen=True)
class StoredResult:
    band: str  # "hot" or "warm"
    context_text: str  # what the agent's context holds
    cold_path: str | None  # None for hot -- nothing was written to disk
    content_hash: str


def band_for(result_text: str, *, threshold: int = HOT_THRESHOLD) -> str:
    """"hot" or "warm" by length alone -- the one deterministic input both
    the caller (to decide whether it even needs a summary call) and this
    module's own ``store_result`` must agree on."""
    return "hot" if len(result_text) <= threshold else "warm"


def summary_model(*, profile_models: dict, resident_model: str, override: str | None = None) -> str:
    """The model a warm-band summary call must use: the fast profile, resolved
    through model_router's existing routing decision rather than a new ad hoc
    choice."""
    return resolve_profile(SUMMARY_PROFILE, profile_models=profile_models,
                            resident_model=resident_model, override=override)


def cold_path_for(workspace: str, tool: str, content_hash: str) -> str:
    return os.path.join(workspace, RESULTS_RELDIR, f"{tool}-{content_hash}.txt")


def _write_cold(workspace: str, tool: str, masked_text: str) -> tuple[str, str]:
    content_hash = hashlib.sha256(masked_text.encode("utf-8")).hexdigest()
    path = cold_path_for(workspace, tool, content_hash)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(masked_text)
    return path, content_hash


def store_result(workspace: str, tool: str, result_text: str, *,
                  summary: str | None = None, threshold: int = HOT_THRESHOLD) -> StoredResult:
    """Classify ``result_text`` and produce what the agent's context should
    hold for it.

    Hot band: the (masked) text itself, no disk round-trip.

    Warm band: the full (masked) text is written to cold disk and
    ``context_text`` becomes ``summary`` -- which the caller must already
    have (its own LLM call, via ``summary_model()`` for which model). This
    fails closed (wq#2630 posture, same as ``model_router.RoutingError``):
    a warm-band result with no summary yet raises rather than silently
    truncating or inlining the raw text, both of which are the exact
    behaviors this module exists to replace.
    """
    band = band_for(result_text, threshold=threshold)
    masked = wr.mask_text(result_text)
    if band == "hot":
        content_hash = hashlib.sha256(masked.encode("utf-8")).hexdigest()
        return StoredResult(band="hot", context_text=masked, cold_path=None,
                             content_hash=content_hash)

    if not summary or not summary.strip():
        raise ResultStoreError(
            f"{tool}: a {len(result_text)}-char result is over the "
            f"{threshold}-char threshold and needs a warm-band summary before "
            "it can be stored -- resolve a model with summary_model() and "
            "place that LLM call first"
        )

    cold_path, content_hash = _write_cold(workspace, tool, masked)
    return StoredResult(band="warm", context_text=summary.strip(), cold_path=cold_path,
                         content_hash=content_hash)
