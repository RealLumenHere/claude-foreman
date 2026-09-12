"""wq#1809: canonical `stream-json` frames -> UHP `response.*` events, one translator
for every backend (PHASE0_WIRING_SPEC.md C.1 / RECOMMENDATION step 3). Mined from
`HR/gateway/app.py:5025 class _RespTranslator` (+ its `_blocks_from_canonical` helper
at `HR/gateway/app.py:4977`) and cut down to despag's object shapes
(`despag/uhp/models.py`) and Core scope — the event vocabulary, gapless
`sequence_number`, and "stream + non-stream share one assembly" design (streaming.md
§6, S-08) are preserved; HR's multi-tenant metadata (`connection`, `served_model`,
file/annotation citations — Extended/Full, files.md) is not despag P1's concern.

One `RespTranslator` per response. `feed()` turns one already-normalized canonical
frame (`despag.runner.canonical.normalize` output) into zero or more SSE events while
also appending finished items to `self.output`, so a caller that never streams
(blocking, polled GET) can replay every frame through `feed()` and then call
`complete()`/`fail()` once to read the identical final `response` object a live SSE
subscriber would have seen in its terminal event — same code, same result, by
construction rather than by keeping two assemblies in sync.
"""
from __future__ import annotations

import json
import uuid

from despag.runner import canonical


def _rid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


# ── canonical (claude stream-json) -> ordered (kind, payload) blocks ────────────────
def blocks_from_canonical(frame: dict) -> list[tuple[str, object]]:
    """One canonical frame -> its ordered (kind, payload) content blocks. `kind` is
    one of text/reasoning/tool_use/tool_result/result; anything else (system/init,
    a bare user prompt echo, ...) carries no translatable content and yields nothing.
    """
    t = frame.get("type")
    out: list[tuple[str, object]] = []
    if t == "assistant":
        content = (frame.get("message") or {}).get("content")
        for c in content if isinstance(content, list) else []:
            if not isinstance(c, dict):
                continue
            ct = c.get("type")
            if ct == "text" and c.get("text"):
                out.append(("text", c["text"]))
            elif ct == "thinking" and c.get("thinking"):
                out.append(("reasoning", c["thinking"]))
            elif ct == "tool_use":
                out.append(("tool_use", {"id": c.get("id") or _rid("call"),
                                          "name": c.get("name") or "tool",
                                          "input": c.get("input") if c.get("input") is not None else {}}))
    elif t == "user":
        # A bare string content is a prompt echo, never tool output — only a content
        # LIST's tool_result blocks translate (mirrors the mined reference's guard:
        # iterating a string here crashed the cross-replica live tail on its first line).
        content = (frame.get("message") or {}).get("content")
        for c in content if isinstance(content, list) else []:
            if isinstance(c, dict) and c.get("type") == "tool_result":
                body = c.get("content")
                if isinstance(body, list):
                    body = "\n".join((x.get("text", "") if isinstance(x, dict) else str(x)) for x in body)
                out.append(("tool_result", {"call_id": c.get("tool_use_id") or "",
                                             "output": str(body if body is not None else ""),
                                             "is_error": bool(c.get("is_error"))}))
    elif t == "result":
        out.append(("result", {"text": frame.get("result") or "", "usage": frame.get("usage")}))
    return out


class RespTranslator:
    """Feed it canonical frames in arrival order via `feed()`. `sequence_number` is
    gapless from 0 across the whole response (streaming.md §3/S-04); each output item
    gets its own increasing `output_index` (§3 rule 3: `output_item.added` precedes
    every event about it, `output_item.done` follows them all)."""

    def __init__(self, resp_id: str, model: str, previous_response_id: str | None,
                 store: bool, created_at: int, *, session_id: str | None,
                 ignored_fields: list[str] | None = None):
        self.resp_id = resp_id
        self.model = model
        self.previous_response_id = previous_response_id
        self.store = store
        self.created_at = created_at
        self.session_id = session_id
        self.ignored_fields = list(ignored_fields or [])
        self.seq = 0
        self.out_index = -1
        self.output: list[dict] = []
        self.usage: dict | None = None
        self.error: dict | None = None
        self.incomplete_reason: str | None = None
        self.cur: dict | None = None
        self.any_text = False

    def _ev(self, type_: str, **fields) -> dict:
        ev = {"type": type_, "sequence_number": self.seq, **fields}
        self.seq += 1
        return ev

    def _response_obj(self, status: str) -> dict:
        metadata: dict = {}
        if self.session_id:
            metadata["session_id"] = self.session_id
        if self.ignored_fields:
            metadata["ignored_fields"] = self.ignored_fields
        incomplete_details = ({"reason": self.incomplete_reason}
                               if status == "incomplete" and self.incomplete_reason else None)
        return {
            "id": self.resp_id, "object": "response", "created_at": self.created_at,
            "status": status, "error": self.error, "incomplete_details": incomplete_details,
            "previous_response_id": self.previous_response_id, "model": self.model,
            "output": self.output, "store": self.store, "usage": self.usage,
            "metadata": metadata,
        }

    def start(self) -> list[dict]:
        """`response.created` (seq 0) then `response.in_progress` — lifecycle.md/
        streaming.md §3 rule 1: `response.created` MUST be the first event."""
        snapshot = {**self._response_obj("in_progress"), "output": []}
        return [self._ev("response.created", response=snapshot),
                self._ev("response.in_progress", response=snapshot)]

    def _close_cur(self) -> list[dict]:
        if not self.cur:
            return []
        evs: list[dict] = []
        c = self.cur
        if c["kind"] == "reasoning":
            part = {"type": "summary_text", "text": c["text"]}
            item = {"id": c["id"], "type": "reasoning", "summary": [part] if c["text"] else [],
                     "status": "completed"}
            evs.append(self._ev("response.reasoning_summary_part.done", item_id=c["id"],
                                 output_index=c["oi"], summary_index=0, part=part))
            evs.append(self._ev("response.output_item.done", output_index=c["oi"], item=item))
            self.output.append(item)
        elif c["kind"] == "message":
            part = {"type": "output_text", "text": c["text"]}
            item = {"id": c["id"], "type": "message", "status": "completed", "role": "assistant",
                     "content": [part]}
            evs.append(self._ev("response.output_text.done", item_id=c["id"], output_index=c["oi"],
                                 content_index=0, text=c["text"]))
            evs.append(self._ev("response.content_part.done", item_id=c["id"], output_index=c["oi"],
                                 content_index=0, part=part))
            evs.append(self._ev("response.output_item.done", output_index=c["oi"], item=item))
            self.output.append(item)
        self.cur = None
        return evs

    def _open_reasoning(self) -> list[dict]:
        self.out_index += 1
        rid = _rid("rs")
        self.cur = {"kind": "reasoning", "id": rid, "oi": self.out_index, "text": ""}
        item = {"id": rid, "type": "reasoning", "summary": [], "status": "in_progress"}
        return [self._ev("response.output_item.added", output_index=self.out_index, item=item),
                self._ev("response.reasoning_summary_part.added", item_id=rid, output_index=self.out_index,
                          summary_index=0, part={"type": "summary_text", "text": ""})]

    def _open_message(self) -> list[dict]:
        self.out_index += 1
        mid = _rid("msg")
        self.cur = {"kind": "message", "id": mid, "oi": self.out_index, "text": ""}
        item = {"id": mid, "type": "message", "status": "in_progress", "role": "assistant", "content": []}
        return [self._ev("response.output_item.added", output_index=self.out_index, item=item),
                self._ev("response.content_part.added", item_id=mid, output_index=self.out_index,
                          content_index=0, part={"type": "output_text", "text": ""})]

    def _handle(self, kind: str, payload) -> list[dict]:
        evs: list[dict] = []
        if kind == "reasoning":
            if not self.cur or self.cur["kind"] != "reasoning":
                evs += self._close_cur(); evs += self._open_reasoning()
            self.cur["text"] += payload
            evs.append(self._ev("response.reasoning_summary_text.delta", item_id=self.cur["id"],
                                 output_index=self.cur["oi"], summary_index=0, delta=payload))
        elif kind == "text":
            if not self.cur or self.cur["kind"] != "message":
                evs += self._close_cur(); evs += self._open_message()
            self.cur["text"] += payload
            self.any_text = True
            evs.append(self._ev("response.output_text.delta", item_id=self.cur["id"],
                                 output_index=self.cur["oi"], content_index=0, delta=payload))
        elif kind == "tool_use":
            evs += self._close_cur()
            self.out_index += 1
            fid = _rid("fc")
            inp = payload["input"]
            args = inp if isinstance(inp, str) else json.dumps(inp, default=str)
            shell = {"id": fid, "type": "function_call", "call_id": payload["id"],
                     "name": payload["name"], "arguments": "", "status": "in_progress"}
            done = {**shell, "arguments": args, "status": "completed"}
            evs.append(self._ev("response.output_item.added", output_index=self.out_index, item=shell))
            evs.append(self._ev("response.function_call_arguments.delta", item_id=fid,
                                 output_index=self.out_index, delta=args))
            evs.append(self._ev("response.function_call_arguments.done", item_id=fid,
                                 output_index=self.out_index, arguments=args))
            evs.append(self._ev("response.output_item.done", output_index=self.out_index, item=done))
            self.output.append(done)
        elif kind == "tool_result":
            evs += self._close_cur()
            self.out_index += 1
            oid = _rid("fco")
            item = {"id": oid, "type": "function_call_output", "call_id": payload["call_id"],
                     "output": payload["output"], "status": "completed"}
            evs.append(self._ev("response.output_item.added", output_index=self.out_index, item=item))
            evs.append(self._ev("response.output_item.done", output_index=self.out_index, item=item))
            self.output.append(item)
        elif kind == "result":
            u = payload.get("usage")
            if u:
                norm = canonical._norm_token_usage(u)
                self.usage = {**norm, "total_tokens": norm.get("input_tokens", 0) + norm.get("output_tokens", 0)}
            # The CLI's batch mode carries the whole answer in the result frame's own
            # `result` text rather than as assistant deltas when no text ever streamed
            # (e.g. a turn whose only output is its final line) — still one text block,
            # chunked as a single fragment (streaming.md §2.3 tolerates any chunking).
            if not self.any_text and payload.get("text"):
                evs += self._handle("text", payload["text"])
        return evs

    def feed(self, frame: dict) -> list[dict]:
        evs: list[dict] = []
        for kind, payload in blocks_from_canonical(frame):
            evs += self._handle(kind, payload)
        return evs

    def complete(self, status: str, *, error: dict | None = None,
                 incomplete_reason: str | None = None) -> list[dict]:
        """Close whatever item is open and emit the one terminal event (streaming.md
        §4): `response.completed|incomplete|failed`, never `response.cancelled` — a
        cancelled task terminates with `response.failed` carrying `status:"cancelled"`
        in the response object; the status field, not the event name, is authoritative.
        """
        self.error = error
        self.incomplete_reason = incomplete_reason
        evs = self._close_cur()
        ev_type = {"completed": "response.completed", "incomplete": "response.incomplete",
                   "failed": "response.failed", "cancelled": "response.failed"}[status]
        evs.append(self._ev(ev_type, response=self._response_obj(status)))
        return evs

    def fail(self, message: str) -> list[dict]:
        """A turn that could not be completed: a non-fatal `error` event (streaming.md
        §2.6) immediately followed by the terminal `response.failed` — an `error` event
        MUST be followed by a terminal event, so the two are always emitted together."""
        self.error = {"type": "harness_error", "code": "harness_error", "message": message}
        evs = self._close_cur()
        evs.append(self._ev("error", code="harness_error", message=message, param=None))
        evs.append(self._ev("response.failed", response=self._response_obj("failed")))
        return evs
