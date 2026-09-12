"""wq#1805/wq#1806: despag package — UHP discovery/harnesses/models (P1-1) plus the
`POST /v1/responses` task-execution surface (P1-2, PHASE0_WIRING_SPEC.md
RECOMMENDATION step 2). Execution is wired for `stream:false` only (blocking and
`background:true`+polling); `stream:true` still answers 501 lumen_not_implemented —
SSE is the next ticket (RECOMMENDATION step 3).
"""
from __future__ import annotations

import asyncio
import http.client
import json
import os
import secrets
import subprocess
import time
import uuid
from pathlib import Path

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from despag.backends import claude_max
from despag.runner import canonical
from despag.runner.turn import TurnRegistry
from despag.uhp import models
from despag.uhp.errors import error_response

UHP_VERSION = "2026-08-11"
SUPPORTED_VERSIONS = [UHP_VERSION]

DESPAG_DIR = Path(__file__).resolve().parent
HARNESSES_FILE = DESPAG_DIR / "harnesses.yaml"

# fworker.py:148 LOCAL_POOL_MODELS — despag does not import fworker (that module is
# the task-execution runner, out of scope for this ticket); the two model pools are
# duplicated here deliberately small and static, not lifted as a dependency.
LOCAL_POOL_MODELS = ("deepseek-v4-flash", "qwen36-local")

CLAUDE_BIN = os.environ.get("DESPAG_CLAUDE_BIN", "claude")
LITELLM_HEALTH_URL = os.environ.get("DESPAG_LITELLM_HEALTH", "http://localhost:4000/health")


def _is_local_model(model_id: str) -> bool:
    return bool(model_id) and any(model_id.startswith(m) for m in LOCAL_POOL_MODELS)


def load_harnesses() -> list[dict]:
    raw = yaml.safe_load(HARNESSES_FILE.read_text())["harnesses"]
    return [models.Harness(**h).model_dump(exclude_none=True) for h in raw]


HARNESSES = load_harnesses()
HARNESS_BY_ID = {h["id"]: h for h in HARNESSES}


def _load_or_create_token() -> str:
    """Bearer token for every route except discovery. Never logged, never
    returned in a response. Default path matches deploy-time convention; the
    file is 0600 and created with a random token if it doesn't exist yet."""
    token_path = Path(os.environ.get("DESPAG_API_TOKEN_FILE", "~/.secrets/despag-api.token")).expanduser()
    if not token_path.exists():
        token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        token_path.write_text(secrets.token_urlsafe(32))
    token_path.chmod(0o600)
    return token_path.read_text().strip()


API_TOKEN = _load_or_create_token()


def _claude_logged_in() -> bool:
    try:
        proc = subprocess.run([CLAUDE_BIN, "auth", "status"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    try:
        info = json.loads(proc.stdout)
    except ValueError:
        return False
    return bool(info.get("loggedIn"))


def _litellm_healthy() -> bool:
    from urllib.parse import urlparse

    parsed = urlparse(LITELLM_HEALTH_URL)
    try:
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=2)
        conn.request("GET", parsed.path or "/")
        resp = conn.getresponse()
        return 200 <= resp.status < 300
    except OSError:
        return False
    finally:
        try:
            conn.close()
        except NameError:
            pass


def compute_available(harness: dict) -> bool:
    """harnesses.md: `available` is computed, never asserted. A harness the
    catalog marks `enabled: false` is never available, regardless of backend
    reachability (claude-local-rung, disabled per the ticket)."""
    if not harness.get("enabled", True):
        return False
    if _is_local_model(harness.get("defaultModel", "")):
        return _litellm_healthy()
    token_path = Path(os.environ.get("DESPAG_API_TOKEN_FILE", "~/.secrets/despag-api.token")).expanduser()
    return token_path.exists() and os.access(token_path, os.R_OK) and _claude_logged_in()


# --- P1-2 task execution (PHASE0_WIRING_SPEC.md RECOMMENDATION step 2) --------------
# One process serves every concurrent turn: REGISTRY is the shared table
# despag.runner.turn.Turn instances (each its own thread + `claude -p` child) are
# started into and polled from. RESPONSES/SESSIONS are UHP-shaped bookkeeping on top
# — a session is a workspace directory (implicit on first task, UHP lifecycle.md), a
# response is one turn's request/result pairing.
SESSIONS_ROOT = Path(os.environ.get("DESPAG_SESSIONS_ROOT", "/tmp/despag-sessions"))
REGISTRY = TurnRegistry()
RESPONSES: dict[str, dict] = {}
SESSIONS: dict[str, dict] = {}

# turn.status -> UHP ResponseStatus (lifecycle.md: only these five exist). "timeout"
# (wall-clock cap, a budget stop) maps to incomplete; "hang" (frame-gap watchdog, no
# result ever arrived) is not a budget stop and maps to failed.
_STATUS_MAP = {
    "completed": "completed",
    "failed": "failed",
    "incomplete": "incomplete",
    "cancelled": "cancelled",
    "timeout": "incomplete",
    "hang": "failed",
}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _flatten_input(items: list) -> str:
    parts = []
    for it in items:
        parts.append(str(it.get("content", it)) if isinstance(it, dict) else str(it))
    return "\n".join(parts)


def _resolve_session(previous_response_id: str | None) -> tuple[str | None, str | None, str | None, str | None]:
    """Returns `(session_id, cwd, resume_session, error_code)`. A fresh task opens a
    new session (workspace directory) implicitly; a continuation reuses the prior
    response's session and, once the CLI has told us its own conversation id, resumes
    it via `--resume` (session_id != errors.md's response_not_found)."""
    if not previous_response_id:
        sid = _new_id("hsess")
        cwd = str(SESSIONS_ROOT / sid)
        Path(cwd).mkdir(parents=True, exist_ok=True)
        SESSIONS[sid] = {"cwd": cwd, "cli_session_id": None}
        return sid, cwd, None, None
    prev = RESPONSES.get(previous_response_id)
    if prev is None:
        return None, None, None, "response_not_found"
    sid = prev["session_id"]
    session = SESSIONS[sid]
    return sid, session["cwd"], session.get("cli_session_id"), None


def _remember_cli_session(session_id: str, turn) -> None:
    """The CLI mints its own conversation id on `system/init`; stash it the first time
    a turn's frames carry one so the NEXT turn in this session can `--resume` it."""
    session = SESSIONS.get(session_id)
    if session is None or session.get("cli_session_id"):
        return
    for frame in turn.snapshot_frames():
        if frame.get("type") == "system" and frame.get("subtype") == "init" and frame.get("session_id"):
            session["cli_session_id"] = frame["session_id"]
            return


def _extract_text(frame: dict) -> str:
    content = (frame.get("message") or {}).get("content") or []
    return "".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")


def _build_output(turn) -> tuple[list[dict], dict | None, dict | None]:
    """Turn -> `(output, usage, result_frame)`. Not the general canonical-frame ->
    UHP-event `_RespTranslator` (PHASE0_WIRING_SPEC.md C.1/RECOMMENDATION step 3, out
    of scope here — that's for SSE); this only needs the final blocking/polled shape."""
    parts, result_frame = [], None
    for frame in turn.snapshot_frames():
        if frame.get("type") == "assistant":
            text = _extract_text(frame)
            if text:
                parts.append(text)
        elif frame.get("type") == "result":
            result_frame = frame
    text = "".join(parts) or (result_frame or {}).get("result") or ""
    output = []
    if text:
        output = [{"id": _new_id("msg"), "type": "message", "status": "completed", "role": "assistant",
                   "content": [{"type": "output_text", "text": text}]}]
    usage = canonical._norm_token_usage((result_frame or {}).get("usage")) or None
    return output, usage, result_frame


def _build_error(turn, result_frame: dict | None) -> dict | None:
    status = turn.status
    if status in (None, "completed"):
        return None
    if status == "cancelled":
        return {"type": "invalid_request_error", "code": "cancelled", "message": "cancelled by client"}
    if status == "timeout":
        return {"type": "harness_error", "code": "timeout",
                "message": f"wall-clock cap of {turn.wall_clock_s}s exceeded"}
    if status == "hang":
        return {"type": "harness_error", "code": "timeout",
                "message": f"no frame for {turn.frame_gap_s}s (frame-gap watchdog)"}
    if status == "incomplete":
        return None   # a budget stop, not a failure — incomplete_details carries the reason
    message = (result_frame or {}).get("result") or turn.error or "turn failed"
    return {"type": "harness_error", "code": "harness_error", "message": str(message)[:2000]}


def _response_view(response_id: str) -> dict:
    rec = RESPONSES[response_id]
    turn = REGISTRY.get(response_id)
    if turn is None or turn.status is None:
        status = "in_progress"
        output, usage, error, incomplete_details = [], None, None, None
    else:
        output, usage, result_frame = _build_output(turn)
        status = _STATUS_MAP.get(turn.status, "failed")
        error = _build_error(turn, result_frame)
        incomplete_details = None
        if status == "incomplete":
            reason = "max_turns" if turn.status == "incomplete" else "timeout"
            incomplete_details = {"reason": reason}
    metadata = {"session_id": rec["session_id"], "harness_id": rec["harness_id"]}
    if rec.get("ignored_fields"):
        metadata["ignored_fields"] = rec["ignored_fields"]
    return {
        "id": response_id, "object": "response", "created_at": rec["created_at"], "status": status,
        "error": error, "incomplete_details": incomplete_details,
        "previous_response_id": rec.get("previous_response_id"), "model": rec["model"],
        "output": output, "store": True, "usage": usage, "metadata": metadata,
    }


app = FastAPI()


@app.middleware("http")
async def uhp_version_negotiation(request: Request, call_next):
    requested = request.headers.get("uhp-version")
    if requested and requested not in SUPPORTED_VERSIONS:
        return error_response(
            "unsupported_protocol_version",
            f"unsupported UHP-Version {requested!r}",
            detail={"supported": SUPPORTED_VERSIONS},
        )
    response = await call_next(request)
    response.headers["UHP-Version"] = UHP_VERSION
    return response


def require_auth(request: Request) -> JSONResponse | None:
    header = request.headers.get("authorization", "")
    if not header:
        return error_response("missing_credential", "no bearer token supplied")
    if header != f"Bearer {API_TOKEN}":
        return error_response("invalid_credential", "the bearer token is malformed, unknown, or revoked")
    return None


@app.get("/v1/uhp")
async def discovery():
    capabilities = models.Capabilities(
        streaming=True,
        sessions=True,
        cancellation=True,
        idempotency=True,
        files_input=False,
        files_output=False,
        session_listing=False,
        harness_management=False,
        session_sharing=False,
    )
    doc = models.Discovery(
        object="uhp.discovery",
        protocol="uhp",
        versions=SUPPORTED_VERSIONS,
        default_version=UHP_VERSION,
        conformance_class="core",
        capabilities=capabilities,
        implementation=models.DiscoveryImplementation(name="despag", version="0.1.0"),
    )
    return JSONResponse(doc.model_dump())


@app.get("/v1/harnesses")
async def list_harnesses(request: Request):
    if (err := require_auth(request)) is not None:
        return err
    return {"harnesses": HARNESSES}


@app.get("/v1/harnesses/{harness_id}")
async def get_harness(harness_id: str, request: Request):
    if (err := require_auth(request)) is not None:
        return err
    harness = HARNESS_BY_ID.get(harness_id)
    if harness is None:
        # architecture.md: out-of-scope objects are 404, never 403.
        return error_response("harness_not_found", f"no harness with id {harness_id!r}", param="harness_id")
    return harness


def _model_entry(harness: dict) -> dict:
    model_id = harness["defaultModel"]
    backend = "litellm" if _is_local_model(model_id) else "claude"
    entry = models.Model(id=model_id, label=model_id, backend=backend, available=compute_available(harness), default=True)
    return entry.model_dump(exclude_none=True)


@app.get("/v1/models")
async def list_models(request: Request):
    if (err := require_auth(request)) is not None:
        return err
    backends: dict[str, dict] = {}
    for h in HARNESSES:
        model_id = h["defaultModel"]
        backend = "litellm" if _is_local_model(model_id) else "claude"
        bucket = backends.setdefault(backend, {"default": model_id, "models": []})
        bucket["models"].append(_model_entry(h))
    catalog = models.ModelCatalog(backends={k: models.ModelCatalogBackendsItem(**v) for k, v in backends.items()})
    return catalog.model_dump()


@app.get("/v1/harnesses/{harness_id}/models")
async def harness_models(harness_id: str, request: Request):
    if (err := require_auth(request)) is not None:
        return err
    harness = HARNESS_BY_ID.get(harness_id)
    if harness is None:
        return error_response("harness_not_found", f"no harness with id {harness_id!r}", param="harness_id")
    model_id = harness["defaultModel"]
    backend = "litellm" if _is_local_model(model_id) else "claude"
    result = models.HarnessModels(harness_id=harness_id, backend=backend, default=model_id, models=[_model_entry(harness)])
    return result.model_dump(exclude_none=True)


@app.post("/v1/responses")
async def create_response(request: Request):
    if (err := require_auth(request)) is not None:
        return err
    body = await request.json()
    try:
        req = models.CreateResponseRequest(**body)
    except Exception as e:  # noqa: BLE001 — a malformed request body is a 400, not a 500
        return error_response("invalid_input", str(e), status=400)
    if req.stream:
        # RECOMMENDATION step 2 wires blocking + background/polling only; step 3
        # (SSE, gapless sequence_number, the general _RespTranslator) is the next
        # ticket.
        return error_response(
            "lumen_not_implemented",
            "streaming responses (stream:true) are not implemented by this build of despag",
            status=501,
        )
    harness_id = (req.metadata.harness_id if req.metadata else None) or HARNESSES[0]["id"]
    harness = HARNESS_BY_ID.get(harness_id)
    if harness is None:
        return error_response("harness_not_found", f"no harness with id {harness_id!r}", param="metadata.harness_id")
    if not compute_available(harness):
        return error_response("model_unavailable", f"harness {harness_id!r} has no available model", status=422)
    sid, cwd, resume_session, err_code = _resolve_session(req.previous_response_id)
    if err_code is not None:
        return error_response(err_code, f"no response with id {req.previous_response_id!r}",
                               param="previous_response_id")
    prompt = req.input if isinstance(req.input, str) else _flatten_input(req.input)
    model = req.model or harness["defaultModel"]
    cmd, env = claude_max.build_turn(
        worker_id="despag", model=model, effort=None, worktree=cwd, ticket_id=sid,
        prompt=prompt, resume_session=resume_session,
    )
    response_id = _new_id("resp")
    RESPONSES[response_id] = {
        "created_at": _now_ms(), "harness_id": harness_id, "session_id": sid,
        "previous_response_id": req.previous_response_id, "model": model,
        "ignored_fields": [f for f in ("tools", "include") if f in body],
    }
    turn = REGISTRY.start(response_id, cmd, cwd=cwd, env=env, timeout_seconds=req.timeout_seconds)
    if not req.background:
        await asyncio.to_thread(turn.done.wait)
    _remember_cli_session(sid, turn)
    return JSONResponse(_response_view(response_id))


@app.get("/v1/responses/{response_id}")
async def get_response(response_id: str, request: Request):
    if (err := require_auth(request)) is not None:
        return err
    if response_id not in RESPONSES:
        return error_response("response_not_found", f"no response with id {response_id!r}", param="response_id")
    turn = REGISTRY.get(response_id)
    if turn is not None and turn.done.is_set():
        _remember_cli_session(RESPONSES[response_id]["session_id"], turn)
    return JSONResponse(_response_view(response_id))


@app.post("/v1/responses/{response_id}/cancel")
async def cancel_response(response_id: str, request: Request):
    if (err := require_auth(request)) is not None:
        return err
    if response_id not in RESPONSES:
        return error_response("response_not_found", f"no response with id {response_id!r}", param="response_id")
    turn = REGISTRY.get(response_id)
    if turn is not None:
        # sessions.md §4: idempotent on a terminal task — Turn.cancel() is a no-op
        # once turn.done is set, so a terminal response's status never changes (C-02).
        # Reply promptly either way: cancel() only requests a killpg, it doesn't wait.
        turn.cancel()
    return JSONResponse(_response_view(response_id))
