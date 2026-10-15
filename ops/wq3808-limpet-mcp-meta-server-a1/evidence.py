"""wq#3964 LIMPET meta-server: per-tool-call evidence metadata.

Every tools/call the meta-server answers carries a `_meta.evidence` block
saying which tool ran, which child/plugin (and at what git sha) served it,
what it was asked (as a hash), when, whether it worked, what coverage the
child claimed, and whether the output is target-derived and so untrusted.

The block's identity fields follow REA's Evidence records (an `ev_<sha256>`
evidence_id plus the `operation` name), so a workspace's evidence log stays
portable into an REA-style `export_evidence_bundle`
(design/wq3806_limpet_architecture.md:238-240).
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

EVIDENCE_LOG_RELPATH = os.path.join("evidence", "tool-calls.jsonl")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def args_hash(arguments) -> str:
    return hashlib.sha256(_canonical(arguments).encode("utf-8")).hexdigest()


def _child_meta(result) -> dict:
    meta = result.get("_meta") if isinstance(result, dict) else None
    return meta if isinstance(meta, dict) else {}


def _child_coverage(result):
    """A coverage/limitations note the child put on its own result, if any.

    REA-style servers put it at `_meta.coverage`; a child that is itself a
    Limpet meta-server nests it under `_meta.evidence.coverage`.
    """
    meta = _child_meta(result)
    if meta.get("coverage") is not None:
        return meta["coverage"]
    nested = meta.get("evidence")
    if isinstance(nested, dict):
        return nested.get("coverage")
    return None


def _child_says_untrusted(result) -> bool:
    provenance = _child_meta(result).get("provenance")
    return isinstance(provenance, dict) and bool(provenance.get("untrusted_content"))


def build_evidence(
    tool: str,
    source: dict,
    arguments,
    started_at: str,
    finished_at: str,
    result=None,
    error: str | None = None,
) -> dict:
    """The `_meta.evidence` block for one tools/call.

    `source` is the ToolRegistry's provenance for the tool: kind, server,
    child tool name, the manifest entry's git_sha and untrusted_output flag.
    `error` set means the call never produced a result (child error, timeout,
    bad arguments); the block is still built so a failed call is evidence too.
    """
    exit_code = result.get("exit_code") if isinstance(result, dict) else None
    if error is not None:
        ok = False
    elif isinstance(result, dict) and result.get("isError"):
        ok = False
    else:
        ok = exit_code in (None, 0)
    block = {
        "operation": tool,
        "tool": tool,
        "source": {
            "kind": source["kind"],
            "server": source["server"],
            "tool": source["tool"],
            "git_sha": source.get("git_sha"),
        },
        "args_sha256": args_hash(arguments),
        "started_at": started_at,
        "finished_at": finished_at,
        "ok": ok,
        "exit_code": exit_code,
        "error": error,
        "coverage": _child_coverage(result),
        "untrusted_content": bool(source.get("untrusted_output", True))
        or _child_says_untrusted(result),
    }
    block["evidence_id"] = "ev_" + hashlib.sha256(_canonical(block).encode("utf-8")).hexdigest()
    return block


def attach_to_result(result, block: dict):
    """Return `result` with `block` at `_meta.evidence`, keeping the child's own _meta."""
    if not isinstance(result, dict):
        result = {"value": result}
    return {**result, "_meta": {**_child_meta(result), "evidence": block}}


class EvidenceLog:
    """Append-only JSONL log under a bound workspace's evidence/ directory."""

    def __init__(self, workspace: str):
        self.path = os.path.join(workspace, EVIDENCE_LOG_RELPATH)

    def append(self, block: dict) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        # One write() per record on an O_APPEND fd: concurrent appenders can't
        # interleave inside a line.
        line = (_canonical(block) + "\n").encode("utf-8")
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
