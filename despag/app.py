"""wq#1805/wq#1806/wq#1807/wq#1809: despag package — UHP discovery/harnesses/models
(P1-1), the `POST /v1/responses` task-execution surface (P1-2), the idempotency+lease
control plane around it (P1-4), and the canonical-frame -> UHP `response.*` SSE
translator (P1-3, PHASE0_WIRING_SPEC.md RECOMMENDATION step 3). `stream:false`
(blocking and `background:true`+polling) and `stream:true` (SSE) both build their
`output`/`usage` through the same `despag.uhp.translate.RespTranslator` replay of a
turn's canonical frames, so the two paths agree on the result shape by construction
(streaming.md §6, S-08) rather than by keeping two assemblies in sync.
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
from fastapi.responses import JSONResponse, StreamingResponse

from despag.backends import claude_max
from despag.backing.control_sqlite import ControlStore
from despag.runner.turn import TurnRegistry
from despag.uhp import models
from despag.uhp.errors import error_response
from despag.uhp.translate import RespTranslator

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

# wq#1807: control-plane store for Idempotency-Key reservation + session lease/
# fence. Same DESPAG_DATA_DIR convention as despag.backing.make_backing(), kept as
# a separate file (control.db) since it is hot, small, single-writer state, not a
# graph/blob/secret record.
DESPAG_DATA_DIR = Path(os.environ.get("DESPAG_DATA_DIR", "~/despag-data")).expanduser()
CONTROL = ControlStore(str(DESPAG_DATA_DIR / "control.db"))
IDEMPOTENCY_TTL_S = 24 * 3600            # errors.md/tasks.md: >=24h retention
SESSION_LEASE_TTL_S = 300                # one in-flight task per session (lifecycle.md)


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


# --- P1-2/P1-3 task execution (PHASE0_WIRING_SPEC.md RECOMMENDATION steps 2-3) ------
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


# --- P1-3: canonical frames -> UHP response.* (despag/uhp/translate.py) ------------
def _build_translator(response_id: str) -> RespTranslator:
    rec = RESPONSES[response_id]
    return RespTranslator(
        response_id, rec["model"], rec.get("previous_response_id"), True, rec["created_at"],
        session_id=rec["session_id"], ignored_fields=rec.get("ignored_fields") or None,
    )


def _last_result_frame(frames: list[dict]) -> dict | None:
    return next((f for f in reversed(frames) if f.get("type") == "result"), None)


def _turn_failure_message(turn) -> str:
    if turn.status == "hang":
        return f"no frame for {turn.frame_gap_s}s (frame-gap watchdog)"
    result_frame = _last_result_frame(turn.snapshot_frames())
    message = (result_frame or {}).get("result") or turn.error or "turn failed"
    return str(message)[:2000]


def _finish_events(turn, translator: RespTranslator) -> list[dict]:
    """The terminal leg every execution path shares (blocking, background-polled, and
    the SSE subscriber): turn `Turn.status` into the UHP outcome and close the
    translator exactly once, so every path reads the final response object off the
    identical assembly (streaming.md §6, S-08). Idempotent per response_id: a second
    call (a GET racing the SSE path's own terminal) replays the first call's cached
    `response` rather than closing a second translator with its own fresh item ids —
    `RespTranslator` mints those from `uuid.uuid4()`, so two independent closes of the
    same frames would disagree about the very ids tasks.md §4 requires to "read back"."""
    rec = RESPONSES[turn.id]
    cached = rec.get("_final")
    if cached is not None:
        ev_type = {"completed": "response.completed", "incomplete": "response.incomplete",
                   "failed": "response.failed"}[cached["status"] if cached["status"] != "cancelled" else "failed"]
        return [{"type": ev_type, "sequence_number": translator.seq, "response": cached}]
    status = _STATUS_MAP.get(turn.status, "failed")
    if status == "failed":
        evs = translator.fail(_turn_failure_message(turn))
    elif status == "incomplete":
        reason = "max_turns" if turn.status == "incomplete" else "timeout"
        evs = translator.complete(status, incomplete_reason=reason)
    elif status == "cancelled":
        evs = translator.complete(status, error={"type": "invalid_request_error", "code": "cancelled",
                                                    "message": "cancelled by client"})
    else:
        evs = translator.complete(status)
    rec["_final"] = evs[-1]["response"]
    return evs


def _response_view(response_id: str) -> dict:
    """A terminal response is re-read, not rebuilt — see `_finish_events` for why."""
    cached = RESPONSES[response_id].get("_final")
    if cached is not None:
        return cached
    turn = REGISTRY.get(response_id)
    translator = _build_translator(response_id)
    if turn is None:
        return translator._response_obj("in_progress")
    for frame in turn.snapshot_frames():
        translator.feed(frame)
    if not turn.done.is_set():
        return translator._response_obj("in_progress")
    return _finish_events(turn, translator)[-1]["response"]


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


async def _idem_admit(idem_key: str, resp_id: str) -> tuple[str, dict | None]:
    """Reserve `idem_key` for resp_id, waiting briefly if someone else already
    holds it (tasks.md: "wait if first still running").

    Returns ("won", None): we hold the reservation — caller executes and MUST
    idem_settle or idem_release it. ("replay", output): a prior winner already
    settled this key — return their output verbatim, no second execution.
    ("busy", None): a prior winner is still running after the bounded wait."""
    existing = await CONTROL.idem_reserve(idem_key, resp_id, ttl_s=IDEMPOTENCY_TTL_S)
    for _ in range(10):
        if existing is None:
            return "won", None
        if existing.get("state") == "done":
            return "replay", existing["output"]
        await asyncio.sleep(0.05)
        existing = await CONTROL.idem_get(idem_key)
        if existing is None:            # the winner released — free to try for ourselves
            existing = await CONTROL.idem_reserve(idem_key, resp_id, ttl_s=IDEMPOTENCY_TTL_S)
    return "busy", None


async def _sse_gen(queue: "asyncio.Queue"):
    """No polling: a subscriber blocks on the queue a frame is pushed onto the moment
    the turn's reader thread appends it (`despag.runner.turn.Turn.on_frame`, bridged
    across threads with `call_soon_threadsafe`); the 20s wait is only for keep-alive,
    never a poll interval (streaming.md §1 "MUST NOT buffer")."""
    while True:
        try:
            ev = await asyncio.wait_for(queue.get(), timeout=20)
        except asyncio.TimeoutError:
            yield b": keep-alive\n\n"
            continue
        if ev is None:
            break
        yield f"data: {json.dumps(ev)}\n\n".encode()


async def _stream_response(response_id: str, cmd: list[str], cwd: str, env: dict,
                            timeout_seconds: float | None, *, on_done) -> StreamingResponse:
    translator = _build_translator(response_id)
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _on_frame(frame: dict) -> None:
        # Runs on the Turn's own thread; hop to the event loop before touching the
        # translator or the queue, both of which are only ever touched from the loop.
        def _feed() -> None:
            for ev in translator.feed(frame):
                queue.put_nowait(ev)
        loop.call_soon_threadsafe(_feed)

    for ev in translator.start():      # response.created (seq 0) is the first event
        queue.put_nowait(ev)
    turn = REGISTRY.start(response_id, cmd, cwd=cwd, env=env,
                          timeout_seconds=timeout_seconds, on_frame=_on_frame)

    async def _finish() -> None:
        await asyncio.to_thread(turn.done.wait)
        for ev in _finish_events(turn, translator):
            queue.put_nowait(ev)
        queue.put_nowait(None)
        await on_done(turn)

    asyncio.create_task(_finish())
    return StreamingResponse(_sse_gen(queue), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/v1/responses")
async def create_response(request: Request):
    if (err := require_auth(request)) is not None:
        return err
    try:
        body = await request.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        req = models.CreateResponseRequest(**body)
    except Exception as e:  # noqa: BLE001 — a malformed request body is a 400, not a 500
        return error_response("invalid_input", str(e), status=400)

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

    idem_key = request.headers.get("idempotency-key")
    response_id = _new_id("resp")
    reserved_by_us = False
    if idem_key:
        outcome, payload = await _idem_admit(idem_key, response_id)
        if outcome == "replay":
            return payload
        if outcome == "busy":
            return error_response(
                "lumen_still_running",
                f"a request with Idempotency-Key {idem_key!r} is still running",
                status=409,
                detail={"retry_after_ms": 500},
            )
        reserved_by_us = True

    lease_fence = 0
    if sid:
        lease = await CONTROL.lease_admit(sid, response_id, ttl_s=SESSION_LEASE_TTL_S, reject_on_conflict=True)
        if lease["rejected"]:
            if reserved_by_us:
                await CONTROL.idem_release(idem_key, response_id)
            return error_response(
                "session_busy",
                f"session {sid!r} already has a task in flight",
                detail={"retry_after_ms": 2000},
            )
        lease_fence = lease["fence"]

    prompt = req.input if isinstance(req.input, str) else _flatten_input(req.input)
    model = req.model or harness["defaultModel"]
    cmd, env = claude_max.build_turn(
        worker_id="despag", model=model, effort=None, worktree=cwd, ticket_id=sid,
        prompt=prompt, resume_session=resume_session,
    )
    RESPONSES[response_id] = {
        "created_at": int(time.time()), "harness_id": harness_id, "session_id": sid,
        "previous_response_id": req.previous_response_id, "model": model,
        "ignored_fields": [f for f in ("tools", "include") if f in body],
    }

    async def _settle(turn) -> None:
        """The completion hook every execution path funnels through (blocking,
        background+polled, and streaming's own `_finish`): release the session lease
        and settle the idempotency reservation with the REAL output once the turn is
        actually done, replacing P1-1's stub-era release-only behaviour now that a
        retried key has a result worth replaying (despag/app.py module docstring, P1-1)."""
        await asyncio.to_thread(turn.done.wait)
        _remember_cli_session(sid, turn)
        if lease_fence:
            await CONTROL.lease_release(sid, response_id, lease_fence)
        if reserved_by_us:
            await CONTROL.idem_settle(idem_key, response_id, _response_view(response_id), ttl_s=IDEMPOTENCY_TTL_S)

    if req.stream:
        return await _stream_response(response_id, cmd, cwd, env, req.timeout_seconds, on_done=_settle)

    turn = REGISTRY.start(response_id, cmd, cwd=cwd, env=env, timeout_seconds=req.timeout_seconds)
    if req.background:
        asyncio.create_task(_settle(turn))
        return JSONResponse(_response_view(response_id))
    await _settle(turn)
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
