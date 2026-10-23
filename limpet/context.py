"""wq#4508 -- assemble agent context entries from result_store.py outputs.

One ``ContextEntry`` per tool call, derived from a ``result_store.StoredResult``:
hot band inlines the full (masked) text; warm band inlines the LLM summary plus
the cold-disk path and content hash, so the agent (or a human via the
agent-chat UI's "open in workspace" affordance,
``docs/agent-chat-spec.md:144-147``) can retrieve the full output on demand --
the UI never inlines the full blob either, same discipline here. Full spec:
``docs/agent-processor-spec.md`` §6.3.
"""
from __future__ import annotations

import dataclasses

from result_store import StoredResult


@dataclasses.dataclass(frozen=True)
class ContextEntry:
    tool: str
    text: str
    evidence_path: str | None
    content_hash: str


def entry_for(tool: str, stored: StoredResult) -> ContextEntry:
    """What the agent's context holds for one tool result."""
    if stored.band == "hot":
        return ContextEntry(tool=tool, text=stored.context_text, evidence_path=None,
                             content_hash=stored.content_hash)
    return ContextEntry(
        tool=tool,
        text=f"{stored.context_text}\n[full output: {stored.cold_path} sha256={stored.content_hash}]",
        evidence_path=stored.cold_path,
        content_hash=stored.content_hash,
    )
