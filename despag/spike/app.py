#!/usr/bin/env python3
"""wq#1803: UHP-shaped acceptance spike (PHASE0_WIRING_SPEC.md B.4/C.1/C.2). One
`claude -p` subprocess per turn, built from fworker's own env/cmd (imported, not
copied). Spike scope: no checkpointing, no idempotency store, no session listing.
"""
from __future__ import annotations
import asyncio
import contextlib
import json
import os
import signal
import sys
import time
import uuid
from pathlib import Path
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
sys.path.insert(0, "/home/lumen/foreman/harness")
from fworker import build_env, build_claude_cmd, SETTINGS_FILE, is_local_model  # noqa: E402

CLAUDE_BIN = "/home/lumen/.npm-global/bin/claude"  # fworker.py:2692's own --claude-bin default
SPIKE_DIR = Path(__file__).resolve().parent
HARNESSES = yaml.safe_load((SPIKE_DIR / "harnesses.yaml").read_text())["harnesses"]
HARNESS_BY_ID = {h["id"]: h for h in HARNESSES}
API_KEY = os.environ.get("UHP_SPIKE_API_KEY", "x")
UHP_VERSION = "2026-08-11"
SESSIONS_ROOT = Path(os.environ.get("UHP_SPIKE_SESSIONS_ROOT", "/tmp/uhp-spike-sessions"))
SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)
TERMINAL_STATUSES = {"completed", "failed", "incomplete", "cancelled"}
app = FastAPI()
RESPONSES: dict[str, dict] = {}
SESSIONS: dict[str, dict] = {}
def now_ms() -> int:
    return int(time.time() * 1000)
def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"
def error(status: int, type_: str, code: str, message: str, detail: dict | None = None) -> JSONResponse:
    err = {"type": type_, "code": code, "message": message}
    if detail is not None:
        err["detail"] = detail
    return JSONResponse(status_code=status, content={"error": err})
def check_auth(request: Request):
    if request.headers.get("authorization", "") != f"Bearer {API_KEY}":
        return error(401, "authentication_error", "invalid_credential", "missing or invalid bearer token")
    return None
def public_view(rec: dict) -> dict:
    return {"id": rec["id"], "object": "response", "created_at": rec["created_at"],
            "status": rec["status"], "model": rec["model"], "output": rec["output"],
            "usage": rec["usage"], "metadata": rec["metadata"],
            "previous_response_id": rec["previous_response_id"], "error": rec["error"]}
@app.middleware("http")
async def add_version_header(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["UHP-Version"] = UHP_VERSION
    return resp
@app.get("/v1/uhp")
async def discovery(request: Request):
    ver = request.headers.get("uhp-version")
    if ver and ver != UHP_VERSION:
        return error(400, "invalid_request_error", "unsupported_protocol_version",
                     f"unsupported UHP-Version {ver!r}", detail={"supported": [UHP_VERSION]})
    return {
        "object": "uhp.discovery", "protocol": "uhp", "versions": [UHP_VERSION],
        "default_version": UHP_VERSION, "conformance_class": "core",
        "capabilities": {"streaming": True, "sessions": True, "cancellation": True,
                          "files_input": False, "files_output": False, "session_listing": False,
                          "harness_management": False, "session_sharing": False, "idempotency": False},
        "implementation": {"name": "despag-uhp-spike", "version": "wq1803"},
    }
@app.get("/v1/harnesses")
async def list_harnesses(request: Request):
    return check_auth(request) or {"harnesses": HARNESSES}
@app.get("/v1/harnesses/{hid}")
async def get_harness(hid: str, request: Request):
    if (e := check_auth(request)):
        return e
    h = HARNESS_BY_ID.get(hid)
    return h if h else error(404, "invalid_request_error", "harness_not_found", f"no harness {hid!r}")
def _model_entry(model_id: str, backend: str) -> dict:
    return {"id": model_id, "label": model_id, "backend": backend, "available": True, "default": True}
@app.get("/v1/models")
async def list_models(request: Request):
    if (e := check_auth(request)):
        return e
    backends: dict[str, dict] = {}
    for h in HARNESSES:
        backend = "litellm" if is_local_model(h["defaultModel"]) else "claude"
        backends.setdefault(backend, {"default": h["defaultModel"], "models": []})
        backends[backend]["models"].append(_model_entry(h["defaultModel"], backend))
    return {"backends": backends}
@app.get("/v1/harnesses/{hid}/models")
async def harness_models(hid: str, request: Request):
    if (e := check_auth(request)):
        return e
    h = HARNESS_BY_ID.get(hid)
    if not h:
        return error(404, "invalid_request_error", "harness_not_found", f"no harness {hid!r}")
    backend = "litellm" if is_local_model(h["defaultModel"]) else "claude"
    return {"harness_id": hid, "backend": backend, "default": h["defaultModel"],
            "models": [_model_entry(h["defaultModel"], backend)]}
def _extract_text(frame: dict) -> str:
    content = (frame.get("message") or {}).get("content") or []
    return "".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
async def run_turn(rec: dict, queue: "asyncio.Queue | None") -> None:
    """Spawn one `claude -p` turn, translate frames into UHP events (C.1); C.2: its
    own process group (start_new_session=True) so cancel kills it whole."""
    seq = 0
    def emit(ev_type: str, **fields) -> None:
        nonlocal seq
        ev = {"type": ev_type, "sequence_number": seq, **fields}
        seq += 1
        if queue is not None:
            queue.put_nowait(ev)
    emit("response.created", response=public_view(rec))
    try:
        env = build_env("uhp-spike", rec["model"], None, rec["_cwd"], "uhp-spike")
        cmd = build_claude_cmd(rec["_input"], rec["model"], None, SETTINGS_FILE, [], claude_bin=CLAUDE_BIN)
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=rec["_cwd"], env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True)
    except Exception as e:  # noqa: BLE001 — a spawn failure is a harness_error, not a crash
        rec["status"] = "failed"
        rec["error"] = {"type": "server_error", "code": "harness_error", "message": str(e)}
        emit("response.failed", response=public_view(rec))
        if queue is not None:
            queue.put_nowait(None)
        rec["_done"].set()
        return
    rec["_proc"] = proc
    rec["_pgid"] = proc.pid  # start_new_session=True: the child's pid IS its pgid
    if rec["_cancel_requested"]:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(rec["_pgid"], signal.SIGKILL)
    emit("response.in_progress", response=public_view(rec))
    result_frame, msg_id, item_opened, parts = None, new_id("msg"), False, []
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        try:
            frame = json.loads(line.decode("utf-8", "replace").strip() or "{}")
        except json.JSONDecodeError:
            continue
        ftype = frame.get("type")
        if ftype == "assistant":
            text = _extract_text(frame)
            if text:
                if not item_opened:
                    emit("response.output_item.added", output_index=0,
                         item={"id": msg_id, "type": "message", "status": "in_progress",
                               "role": "assistant", "content": []})
                    emit("response.content_part.added", item_id=msg_id, output_index=0,
                         content_index=0, part={"type": "output_text", "text": ""})
                    item_opened = True
                parts.append(text)
                emit("response.output_text.delta", item_id=msg_id, output_index=0,
                     content_index=0, delta=text)
        elif ftype == "result":
            result_frame = frame
    await proc.wait()
    full_text = "".join(parts) or (result_frame or {}).get("result", "") or ""
    if full_text:
        item = {"id": msg_id, "type": "message", "status": "completed", "role": "assistant",
                "content": [{"type": "output_text", "text": full_text}]}
        if item_opened:
            emit("response.output_text.done", item_id=msg_id, output_index=0, content_index=0, text=full_text)
            emit("response.content_part.done", item_id=msg_id, output_index=0, content_index=0,
                 part={"type": "output_text", "text": full_text})
        emit("response.output_item.done", output_index=0, item=item)
        rec["output"] = [item]
    if rec["_cancel_requested"]:
        status = "cancelled"
        rec["error"] = {"type": "invalid_request_error", "code": "cancelled", "message": "cancelled by client"}
    elif result_frame is not None:
        subtype, is_error = result_frame.get("subtype"), result_frame.get("is_error")
        if subtype == "success" and not is_error:
            status = "completed"
        elif subtype == "error_max_turns":
            status = "incomplete"
        else:
            status = "failed"
            rec["error"] = {"type": "harness_error", "code": "harness_error",
                             "message": str(result_frame.get("result") or subtype or "turn failed")}
        u = result_frame.get("usage") or {}
        if u:
            rec["usage"] = {"input_tokens": u.get("input_tokens", u.get("inputTokens", 0)) or 0,
                             "output_tokens": u.get("output_tokens", u.get("outputTokens", 0)) or 0}
    else:
        status = "failed"
        rec["error"] = {"type": "harness_error", "code": "harness_error", "message": "no result frame"}
    rec["status"] = status
    term_type = "response.failed" if status in ("failed", "cancelled") else f"response.{status}"
    emit(term_type, response=public_view(rec))
    if queue is not None:
        queue.put_nowait(None)
    rec["_done"].set()
async def sse_gen(queue: "asyncio.Queue"):
    while True:
        try:
            ev = await asyncio.wait_for(queue.get(), timeout=20)
        except asyncio.TimeoutError:
            yield b": keep-alive\n\n"
            continue
        if ev is None:
            break
        yield f"data: {json.dumps(ev)}\n\n".encode()
@app.post("/v1/responses")
async def create_response(request: Request):
    if (e := check_auth(request)):
        return e
    body = await request.json()
    hid = ((body.get("metadata") or {}).get("harness_id")) or HARNESSES[0]["id"]
    h = HARNESS_BY_ID.get(hid)
    if not h:
        return error(404, "invalid_request_error", "harness_not_found", f"no harness {hid!r}")
    prev_id = body.get("previous_response_id")
    if prev_id:
        prev = RESPONSES.get(prev_id)
        if not prev:
            return error(404, "invalid_request_error", "response_not_found", f"no response {prev_id!r}")
        sid = prev["metadata"]["session_id"]
    else:
        sid = new_id("hsess")
    cwd = SESSIONS.setdefault(sid, {"cwd": str(SESSIONS_ROOT / sid)})["cwd"]
    Path(cwd).mkdir(parents=True, exist_ok=True)
    raw_input = body.get("input")
    if isinstance(raw_input, list):
        raw_input = "\n".join(str(i.get("content", i)) if isinstance(i, dict) else str(i) for i in raw_input)
    ignored = [f for f in ("tools", "include") if f in body]  # T-08/T-09: reserved, ignored-but-reported
    meta = {"harness_id": hid, "session_id": sid}
    if ignored:
        meta["ignored_fields"] = ignored
    rec = {"id": new_id("resp"), "object": "response", "created_at": now_ms(), "status": "in_progress",
           "model": body.get("model") or h["defaultModel"], "output": [], "usage": None,
           "metadata": meta, "previous_response_id": prev_id,
           "error": None, "_proc": None, "_pgid": None, "_cancel_requested": False,
           "_done": asyncio.Event(), "_cwd": cwd, "_input": raw_input or ""}
    RESPONSES[rec["id"]] = rec
    if body.get("stream"):
        queue: asyncio.Queue = asyncio.Queue()
        asyncio.create_task(run_turn(rec, queue))
        return StreamingResponse(sse_gen(queue), media_type="text/event-stream")
    if body.get("background"):
        asyncio.create_task(run_turn(rec, None))
        return JSONResponse(public_view(rec))
    await run_turn(rec, None)
    return JSONResponse(public_view(rec))
@app.get("/v1/responses/{rid}")
async def get_response(rid: str, request: Request):
    if (e := check_auth(request)):
        return e
    rec = RESPONSES.get(rid)
    if not rec:
        return error(404, "invalid_request_error", "response_not_found", f"no response {rid!r}")
    return public_view(rec)
@app.delete("/v1/responses/{rid}")
async def delete_response(rid: str, request: Request):
    if (e := check_auth(request)):  # B.4: record only, never cancels an in-flight task
        return e
    if rid not in RESPONSES:
        return error(404, "invalid_request_error", "response_not_found", f"no response {rid!r}")
    return JSONResponse({"id": rid, "object": "response", "deleted": True})
@app.post("/v1/responses/{rid}/cancel")
async def cancel_response(rid: str, request: Request):
    if (e := check_auth(request)):  # C.2: killpg(SIGKILL) direct, no SIGTERM grace, <1s reply
        return e
    rec = RESPONSES.get(rid)
    if not rec:
        return error(404, "invalid_request_error", "response_not_found", f"no response {rid!r}")
    if rec["status"] in TERMINAL_STATUSES:
        return JSONResponse(public_view(rec))
    rec["_cancel_requested"] = True
    if rec["_pgid"]:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(rec["_pgid"], signal.SIGKILL)
    return JSONResponse({"id": rid, "object": "response", "status": "cancelling"})

if __name__ == "__main__":
    import socket
    import uvicorn
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", int(os.environ.get("UHP_SPIKE_PORT", "0"))))
    sock.listen(128)
    print(f"UHP_SPIKE_PORT={sock.getsockname()[1]}", flush=True)
    uvicorn.run(app, fd=sock.fileno(), log_level="warning")