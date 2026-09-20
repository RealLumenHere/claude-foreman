"""despag — UHP discovery/harnesses/models (wq#1805 P1-1) plus the `POST
/v1/responses` task-execution surface (wq#1806 P1-2, PHASE0_WIRING_SPEC.md
RECOMMENDATION step 2).

wq#1857 reconciles execution with admission: wq#1806's runner branched before
wq#1807 (P1-4) landed the control-plane invariants, so its create_response had no
path to them. Every started turn now passes BOTH admission gates before
REGISTRY.start, and gives back exactly what they took when the turn finishes:
  * an `Idempotency-Key` reserves the response id (_idem_admit) — a retry of a
    finished key replays the winner's response instead of spawning a second Turn,
    and a retry while the winner still runs is 409 lumen_still_running;
  * the resolved session (an hsess id, not a caller-supplied string) is admitted
    through a per-session lease that fails closed with 409 session_busy, so one
    session never has two `claude -p` children in flight.
The reservation is SETTLED with the real response (there is output to replay) and
the lease is released only when the turn is done — for a background turn that is in
_settle_turn, not at request return.

wq#1809 (P1-3, RECOMMENDATION step 3) adds `stream:true`: `despag.uhp.translate.
RespTranslator` turns a turn's canonical frames into UHP `response.*` SSE events as
they arrive, and the exact same translator replay builds the `output` a blocking
request or a polled GET sees — see `_build_translator`/`_finish_events`/
`_response_view` below.
"""
from __future__ import annotations

import asyncio
import http.client
import json
import logging
import os
import secrets
import subprocess
import time
import uuid
from pathlib import Path

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from despag import admission
from despag.backends import claude_max
from despag.backing.control_sqlite import ControlStore
from despag.runner.turn import TurnRegistry
from despag.uhp import models

logger = logging.getLogger("despag.app")
from despag.uhp.errors import error_response
from despag.uhp.translate import RespTranslator

UHP_VERSION = "2026-08-11"
SUPPORTED_VERSIONS = [UHP_VERSION]

DESPAG_DIR = Path(__file__).resolve().parent
HARNESSES_FILE = DESPAG_DIR / "harnesses.yaml"

# fworker.py:148 LOCAL_POOL_MODELS — despag does not import fworker (that module is
# the task-execution runner, out of scope for this ticket); the two model pools are
# duplicated here deliberately small and static, not lifted as a dependency.
# BEGIN GENERATED inference_nodes.yaml v1 — edit the registry, not this block
LOCAL_POOL_MODELS = ("deepseek-v4-flash", "qwen36-local", "nephthys-qwen14b", "hathor-llama8b")
# END GENERATED

CLAUDE_BIN = os.environ.get("DESPAG_CLAUDE_BIN", "claude")
# Plain `/health` makes LiteLLM probe every configured deployment and can take
# 15s+ (or hang) under load; `/health/liveliness` is the proxy's own fast liveness
# check (no model calls) — what `compute_available` actually needs per request.
LITELLM_HEALTH_URL = os.environ.get("DESPAG_LITELLM_HEALTH", "http://localhost:4000/health/liveliness")
# fworker.py:164-165 FOREMAN_LITELLM_KEY_FILE — the same gateway fworker routes the
# local-pool models through (fworker.py:1102-1103 ANTHROPIC_AUTH_TOKEN, despag/
# backends/litellm.py:build_env), so the health probe needs the same bearer; an
# unauthenticated /health reads 401 ("No api key passed in"), never healthy.
LITELLM_KEY_FILE = os.environ.get("FOREMAN_LITELLM_KEY_FILE", "/home/lumen/.secrets/foreman-litellm.token")


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
    headers = {}
    try:
        headers["Authorization"] = f"Bearer {Path(LITELLM_KEY_FILE).expanduser().read_text().strip()}"
    except OSError:
        pass   # no key on this box — try unauthenticated, consistent with the old behaviour
    try:
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=2)
        conn.request("GET", parsed.path or "/", headers=headers)
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
admission.log_startup_reserves()
SESSIONS: dict[str, dict] = {}

# wq#1857: RESPONSES/SESSIONS are mutated by request handlers AND by the background
# _settle_turn task, which interleave at every `await`. Guard the read-modify-write
# pairs (resolve-or-create a session; stash the CLI's conversation id) so two
# requests can't both mkdir the same workspace or clobber each other's entry. This is
# bookkeeping-level mutual exclusion only — the durable "one turn per session" fence
# is CONTROL.lease_admit, which survives a restart and a second process; this lock
# does not.
_BOOKKEEPING_LOCK = asyncio.Lock()

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


async def _resolve_session(req) -> tuple[str | None, str | None, str | None, str | None]:
    """Returns `(session_id, cwd, resume_session, error_code)`. A fresh task opens a
    new session (workspace directory) implicitly; a continuation reuses the prior
    response's session and, once the CLI has told us its own conversation id, resumes
    it via `--resume` (session_id != errors.md's response_not_found).

    wq#1857: B.4 says a session is named by its most recent response, but wq#1807 also
    honoured an explicit `metadata.session_id`; now that sessions are real hsess
    records that id must NAME ONE — an unknown one is session_not_found, never a new
    workspace, or a client could mint session ids the lease can't reason about."""
    explicit = (req.metadata.model_extra or {}).get("session_id") if req.metadata else None
    async with _BOOKKEEPING_LOCK:
        if explicit:
            session = SESSIONS.get(explicit)
            if session is None:
                return None, None, None, "session_not_found"
            return explicit, session["cwd"], session.get("cli_session_id"), None
        if not req.previous_response_id:
            sid = _new_id("hsess")
            cwd = str(SESSIONS_ROOT / sid)
            Path(cwd).mkdir(parents=True, exist_ok=True)
            SESSIONS[sid] = {"cwd": cwd, "cli_session_id": None}
            return sid, cwd, None, None
        prev = RESPONSES.get(req.previous_response_id)
        if prev is None:
            return None, None, None, "response_not_found"
        sid = prev["session_id"]
        session = SESSIONS[sid]
        return sid, session["cwd"], session.get("cli_session_id"), None


async def _remember_cli_session(session_id: str, turn) -> None:
    """The CLI mints its own conversation id on `system/init`; stash it the first time
    a turn's frames carry one so the NEXT turn in this session can `--resume` it."""
    async with _BOOKKEEPING_LOCK:
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


# --- P1-3: canonical frames -> UHP response.* (despag/uhp/translate.py) ------------
# `_build_translator`/`_finish_events`/`_response_view` are shared by every execution
# path (blocking, background-polled, and the SSE subscriber in `_stream_response`) so
# stream and non-stream agree on the result shape by construction (streaming.md §6,
# S-08) instead of by keeping two assemblies in sync, the way `_build_output`/
# `_build_error` (wq#1806/wq#1857, replaced here) had to.
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
    if turn.status == "timeout":
        return f"wall-clock cap of {turn.wall_clock_s}s exceeded"
    result_frame = _last_result_frame(turn.snapshot_frames())
    message = (result_frame or {}).get("result") or turn.error or "turn failed"
    return str(message)[:2000]


def _finish_events(turn, translator: RespTranslator) -> list[dict]:
    """The terminal leg every execution path shares: turn `Turn.status` into the UHP
    outcome and close the translator exactly once. Idempotent per response_id: a
    second call (a GET racing the SSE path's own terminal, or the idempotency-settle
    call right after it) replays the first call's cached `response` rather than
    closing a second translator with its own fresh item ids — `RespTranslator` mints
    those from `uuid.uuid4()`, so two independent closes of the same frames would
    disagree about the very ids tasks.md §4 "read back" depends on."""
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
    evs[-1]["response"]["metadata"]["harness_id"] = rec["harness_id"]
    # wq#1888 despag P2-2 (PHASE2_WIRING_SPEC.md §B.6): the eleven-plus fields a
    # direct claude -p spawn's classifier.RunState carries that a bare UHP response
    # has no channel for (result_subtype, terminal_reason, saw_limit, session_id,
    # ...) — the LimitObserver already computed all of them off these same frames
    # for the LIMIT wall, so attach the snapshot rather than let a gateway-backed
    # fworker run misclassify every time (§B.3: without this every gateway run falls
    # through to the no-result branch and LIMIT can never fire). Vendor-namespaced
    # (errors.md §1) exactly like the /v1/uhp `lumen.budget` field.
    limit_observer = rec.get("_limit_observer")
    if limit_observer is not None:
        evs[-1]["response"]["lumen"] = {
            "run_state": {**limit_observer.state.to_dict(), "exit_code": turn.exit_code},
        }
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
        view = translator._response_obj("in_progress")
        view["metadata"]["harness_id"] = RESPONSES[response_id]["harness_id"]
        return view
    for frame in turn.snapshot_frames():
        translator.feed(frame)
    if not turn.done.is_set():
        view = translator._response_obj("in_progress")
        view["metadata"]["harness_id"] = RESPONSES[response_id]["harness_id"]
        return view
    return _finish_events(turn, translator)[-1]["response"]


app = FastAPI()


@app.middleware("http")
async def uhp_version_negotiation(request: Request, call_next):
    requested = request.headers.get("uhp-version")
    if requested and requested not in SUPPORTED_VERSIONS:
        # wq#1852: the rejection is the response most about version negotiation, so it
        # carries the header too — stamp it before returning, not only after call_next().
        rejected = error_response(
            "unsupported_protocol_version",
            f"unsupported UHP-Version {requested!r}",
            detail={"supported": SUPPORTED_VERSIONS},
        )
        rejected.headers["UHP-Version"] = UHP_VERSION
        return rejected
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
    body = doc.model_dump()
    # wq#1812: `lumen.budget` — a Lumen vendor extension field (errors.md §1's
    # vendor-namespace convention, applied here to a non-error field: nested under
    # `lumen` so a future protocol field can never collide with it), so an operator
    # can see WHY claude-max admission refused without reading state.json by hand.
    body["lumen"] = {"budget": admission.budget_snapshot()}
    return JSONResponse(body)


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


def _harness_model_ids(harness: dict) -> list[str]:
    """wq#1811: a harness's own model list (despag/tiers.py TIER_MODELS mirrors this
    per-tier). Falls back to [defaultModel] for a harness that predates the `models`
    extension field (e.g. the disabled claude-local-rung harness)."""
    return harness.get("models") or [harness["defaultModel"]]


def _model_entry(harness: dict, model_id: str) -> dict:
    backend = "litellm" if _is_local_model(model_id) else "claude"
    entry = models.Model(
        id=model_id,
        label=model_id,
        backend=backend,
        available=compute_available(harness),
        default=(model_id == harness["defaultModel"]),
    )
    return entry.model_dump(exclude_none=True)


@app.get("/v1/models")
async def list_models(request: Request):
    if (err := require_auth(request)) is not None:
        return err
    # wq#1852: the catalog is keyed by model id, not by harness. Two harnesses can serve
    # the same model (chrn_claude-max and the disabled chrn_claude-local-rung both default
    # to claude-sonnet-5), and without coalescing here the same id appeared twice in one
    # response — once available:true, once available:false. A model is available if ANY
    # harness serving it is available, and default if it is the default of any of them.
    backends: dict[str, dict] = {}
    seen: dict[tuple[str, str], dict] = {}
    for h in HARNESSES:
        for model_id in _harness_model_ids(h):
            backend = "litellm" if _is_local_model(model_id) else "claude"
            bucket = backends.setdefault(backend, {"default": h["defaultModel"], "models": []})
            entry = _model_entry(h, model_id)
            prior = seen.get((backend, model_id))
            if prior is None:
                seen[(backend, model_id)] = entry
                bucket["models"].append(entry)
                continue
            prior["available"] = prior["available"] or entry["available"]
            prior["default"] = prior.get("default", False) or entry.get("default", False)
    catalog = models.ModelCatalog(backends={k: models.ModelCatalogBackendsItem(**v) for k, v in backends.items()})
    return catalog.model_dump()


@app.get("/v1/harnesses/{harness_id}/models")
async def harness_models(harness_id: str, request: Request):
    if (err := require_auth(request)) is not None:
        return err
    harness = HARNESS_BY_ID.get(harness_id)
    if harness is None:
        return error_response("harness_not_found", f"no harness with id {harness_id!r}", param="harness_id")
    default_model = harness["defaultModel"]
    backend = "litellm" if _is_local_model(default_model) else "claude"
    entries = [_model_entry(harness, model_id) for model_id in _harness_model_ids(harness)]
    result = models.HarnessModels(harness_id=harness_id, backend=backend, default=default_model, models=entries)
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
                            timeout_seconds: float | None, session_id: str, lease_fence: int,
                            idem_key: str | None, model: str | None = None,
                            limit_observer=None) -> StreamingResponse:
    translator = _build_translator(response_id)
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _on_frame(frame: dict) -> None:
        # Runs on the Turn's own thread; hop to the event loop before touching the
        # translator or the queue, both of which are only ever touched from the loop.
        # wq#1812: the LIMIT-wall observer runs synchronously right here too, not
        # hopped to the loop — it only ever touches admission's own lock, and doing
        # it inline means a second in-flight session's next admission check sees the
        # pause the instant this frame lands, not after an event-loop round trip.
        limit_flipped = False
        if limit_observer is not None:
            was = limit_observer.state.saw_limit
            limit_observer(frame)
            limit_flipped = limit_observer.state.saw_limit and not was

        def _feed() -> None:
            # wq#1888 (PHASE2_WIRING_SPEC.md §B.6 item 2): a mid-turn LIMIT detection
            # reaches the client as its own event the instant it's seen, not only in
            # the terminal `lumen.run_state` sidecar — without this a rate-limit stall
            # arrives as an opaque `harness_error` (translate.py's fail()) and a
            # gateway-backed fworker has no `resets_at` to resume on (§B.3).
            if limit_flipped:
                queue.put_nowait(translator.vendor_event(
                    "lumen.limit", resets_at=limit_observer.state.latest_resets_at))
            for ev in translator.feed(frame):
                queue.put_nowait(ev)
        loop.call_soon_threadsafe(_feed)

    for ev in translator.start():      # response.created (seq 0) is the first event
        queue.put_nowait(ev)
    turn = REGISTRY.start(response_id, cmd, cwd=cwd, env=env,
                          timeout_seconds=timeout_seconds, on_frame=_on_frame, model=model)

    async def _finish() -> None:
        await asyncio.to_thread(turn.done.wait)
        for ev in _finish_events(turn, translator):
            queue.put_nowait(ev)
        queue.put_nowait(None)
        await _settle_turn(response_id, turn, session_id, lease_fence, idem_key)

    asyncio.create_task(_finish())
    return StreamingResponse(_sse_gen(queue), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _settle_turn(response_id: str, turn, session_id: str, lease_fence: int,
                        idem_key: str | None) -> dict:
    """Wait for a started turn, then give back everything admission took. Runs inline
    for a blocking request and as a background task for `background:true` — either way
    it is the ONLY place a lease is released or a reservation settled after
    REGISTRY.start, so the two paths cannot drift.

    The reservation is settled (not released) with the finished response view: unlike
    the P1-1 stub there IS output, so a retry of the same Idempotency-Key must replay
    it rather than spawn a second Turn."""
    await asyncio.to_thread(turn.done.wait)
    await _remember_cli_session(session_id, turn)
    view = _response_view(response_id)
    if idem_key:
        await CONTROL.idem_settle(idem_key, response_id, view, ttl_s=IDEMPOTENCY_TTL_S)
    if lease_fence:
        await CONTROL.lease_release(session_id, response_id, lease_fence)
    return view


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

    model = req.model or harness["defaultModel"]

    # wq#1812 P1-7: account-global Max admission gate. Checked before ANY other
    # state is touched (idempotency key, session lease) so a refusal needs no
    # cleanup — this is despag's cost-protection wall, not a protocol nicety: it
    # already drained Frank's own interactive session once (ticket wq#1812).
    # litellm-mechanical is exempt (admission.is_local_model), matching the
    # governor's own local-rung exemption.
    if not admission.is_local_model(model):
        running = REGISTRY.count_in_flight(lambda t: not admission.is_local_model(t.model))
        decision = admission.check_admission(model, running_claude_max=running)
        logger.warning(
            "despag admission: model=%s allow=%s running=%s reasons=%s",
            model, decision["allow"], running, decision["reasons"],
        )
        if not decision["allow"]:
            if decision["retry_after_s"] is not None:
                resp = error_response(
                    "lumen_max_window_exhausted",
                    "; ".join(decision["reasons"]) or "claude-max backend paused account-wide",
                    status=429,
                    detail={"retry_after_s": decision["retry_after_s"], "resets_at": decision["resets_at"]},
                )
                resp.headers["Retry-After"] = str(max(1, int(decision["retry_after_s"])))
            else:
                resp = error_response(
                    "quota_exhausted",
                    "; ".join(decision["reasons"]) or "claude-max admission refused",
                )
            return resp

    response_id = _new_id("resp")

    # wq#1807 admission gate 1 of 2: Idempotency-Key. A settled key replays its
    # winner's response verbatim — the whole point is that a retry never spawns a
    # second `claude -p` for work already done.
    idem_key = request.headers.get("idempotency-key")
    reserved_by_us = False
    if idem_key:
        outcome, payload = await _idem_admit(idem_key, response_id)
        if outcome == "replay":
            return JSONResponse(payload)
        if outcome == "busy":
            return error_response(
                "lumen_still_running",
                f"a request with Idempotency-Key {idem_key!r} is still running",
                status=409,
                detail={"retry_after_ms": 500},
            )
        reserved_by_us = True

    async def _abandon() -> None:
        """Nothing was started, so the key must be free for the next retry."""
        if reserved_by_us:
            await CONTROL.idem_release(idem_key, response_id)

    sid, cwd, resume_session, err_code = await _resolve_session(req)
    if err_code is not None:
        await _abandon()
        param = "metadata.session_id" if err_code == "session_not_found" else "previous_response_id"
        bad = req.previous_response_id if err_code == "response_not_found" else (req.metadata.model_extra or {}).get("session_id")
        return error_response(err_code, f"no such {param.split('.')[-1]} {bad!r}", param=param)

    # wq#1807 admission gate 2 of 2: one in-flight turn per session (lifecycle.md).
    # The lease is taken on the RESOLVED hsess id — the thing that actually owns a
    # workspace directory and a resumable CLI conversation — not on the raw
    # previous_response_id the P1-1 stub used, which named a different key per turn
    # and so never fenced a conversation at all.
    lease = await CONTROL.lease_admit(sid, response_id, ttl_s=SESSION_LEASE_TTL_S, reject_on_conflict=True)
    if lease["rejected"]:
        await _abandon()
        return error_response(
            "session_busy",
            f"session {sid!r} already has a task in flight",
            detail={"retry_after_ms": 2000},
        )
    lease_fence = lease["fence"]

    prompt = req.input if isinstance(req.input, str) else _flatten_input(req.input)
    cmd, env = claude_max.build_turn(
        worker_id="despag", model=model, effort=None, worktree=cwd, ticket_id=sid,
        prompt=prompt, resume_session=resume_session,
    )
    RESPONSES[response_id] = {
        "created_at": _now_ms(), "harness_id": harness_id, "session_id": sid,
        "previous_response_id": req.previous_response_id, "model": model,
        "ignored_fields": [f for f in ("tools", "include") if f in body],
    }
    settle_key = idem_key if reserved_by_us else None
    limit_observer = None if admission.is_local_model(model) else admission.LimitObserver()
    # wq#1888: stashed alongside the other per-response bookkeeping so _finish_events
    # (called later, from _settle_turn or a polled GET, not just here) can reach the
    # same observer instance every frame was already fed through above.
    RESPONSES[response_id]["_limit_observer"] = limit_observer

    if req.stream:
        return await _stream_response(response_id, cmd, cwd, env, req.timeout_seconds,
                                       sid, lease_fence, settle_key, model=model,
                                       limit_observer=limit_observer)

    turn = REGISTRY.start(response_id, cmd, cwd=cwd, env=env, timeout_seconds=req.timeout_seconds,
                          on_frame=limit_observer, model=model)
    if req.background:
        # The turn outlives the request, so the lease must too — releasing it here
        # would let a second turn of the same session start alongside this one.
        asyncio.create_task(_settle_turn(response_id, turn, sid, lease_fence, settle_key))
        return JSONResponse(_response_view(response_id))
    return JSONResponse(await _settle_turn(response_id, turn, sid, lease_fence, settle_key))


@app.get("/v1/responses/{response_id}")
async def get_response(response_id: str, request: Request):
    if (err := require_auth(request)) is not None:
        return err
    if response_id not in RESPONSES:
        return error_response("response_not_found", f"no response with id {response_id!r}", param="response_id")
    turn = REGISTRY.get(response_id)
    if turn is not None and turn.done.is_set():
        await _remember_cli_session(RESPONSES[response_id]["session_id"], turn)
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
        # The lease and reservation are settled by _settle_turn when the turn actually
        # ends, cancelled or not — cancelling must not leave a session fenced forever.
        turn.cancel()
    return JSONResponse(_response_view(response_id))
