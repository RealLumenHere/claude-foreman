"""wq#1805: despag P1-1 package skeleton — UHP discovery/harnesses/models.

No task execution here (PHASE0_WIRING_SPEC.md RECOMMENDATION step 1 is discovery
only; step 2+ wires the runner). `POST /v1/responses` therefore answers 501
lumen_not_implemented rather than running a harness.
"""
from __future__ import annotations

import http.client
import json
import os
import secrets
import subprocess
from pathlib import Path

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

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
    return error_response(
        "lumen_not_implemented",
        "task execution is not implemented by this build of despag",
        status=501,
    )


@app.get("/v1/responses/{response_id}")
async def get_response(response_id: str, request: Request):
    if (err := require_auth(request)) is not None:
        return err
    # No response is ever stored in P1-1 (task execution is 501), so every id is
    # out of scope — 404, same as any other unknown object (architecture.md).
    return error_response("response_not_found", f"no response with id {response_id!r}", param="response_id")
