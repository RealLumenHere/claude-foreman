#!/usr/bin/env python3
"""fworker — one supervisor per ticket, one `claude -p` child per attempt.

BUILD_PLAN.md §T2a / design/T2A_WORKER_HARNESS_DESIGN.md. Bespoke process tracking
(native `claude --bg` was spiked and rejected — see the design doc §1): fworker spawns
`claude -p` directly, parses its stream-json stdout, classifies the outcome
(classifier.py, RELIABILITY_SPEC.md §C/§D) and drives the ticket through wq.

CORRECTION (RELIABILITY_SPEC.md §A, wq#646): the installed claude CLI (2.1.233) did
NOT drop --max-turns — that earlier belief was wrong. The flag is supported in print
mode and the CLI errors on ceiling (PROBE_RESULTS.md live-confirmed: --max-turns 1 ->
result:error_max_turns). fworker passes --max-turns so the CLI itself produces the
result frame RELIABILITY_SPEC.md §C2 requires as TURNS evidence ("never legal without
a result frame"). The supervisor ALSO counts real agentic-loop turns itself in the
stream (classifier.py's RunState.observe()/turns) and SIGTERMs the child on a backstop
ceiling set a few turns above --max-turns, purely as defense-in-depth for a CLI that
doesn't self-enforce — see run_child() below. If that backstop has to fire, no result
frame arrives and classify() correctly falls through to KILLED, not TURNS.
(wq#645: a real turn is not one "assistant" frame — extended thinking splits it into a
thinking-only frame plus the tool_use/text frame; counting both double-counted turns
and tripped the backstop at ~half of max_turns.)

Usage:
    fworker.py run --ticket ID --repo PATH [--model MODEL] [--effort NAME]
               [--archetype NAME] [--max-turns N] [--wall-clock-timeout SEC]
               [--worker-id ID] [--resume]
"""
import argparse
import hashlib
import http.client
import json
import os
from datetime import datetime, timedelta, timezone
import re
import resource
import shlex
import socket
import shutil
import signal
import subprocess
import sys
import time
import threading
import traceback
import queue
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "orchestrator"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "dispatcher"))
import router  # noqa: E402 -- wq#2581: escalate_off_local() shared with foreman_dispatcher.py
import recipe_store  # noqa: E402 -- wq#4235/wq#4288: crystallize/freeze outcome recording (fleet_memory)
from classifier import (
    RunState, classify, parse_worker_sentinel,
    SUCCESS, LIMIT, INDETERMINATE, AUTH, HANG, DEGRADED_SUCCESS, FALSE_COMPLETE, OOM,
    TURNS, LAUNCH_FAILURE, KILLED, BUDGET, BLOCK_SENTINEL, CLAIM_ERROR_SENTINEL,
    TICKET_CLOSED_SENTINEL,
    PERMISSION_LOOP, MODEL_NOT_FOUND, AUTH_DENIED,
)
from oom_forensics import oom_journal_evidence
import acceptance as acceptance_mod
import deferral_gate
import hallucination_guard
import action_gate
import exit_gate  # noqa: E402 -- wq#5007
import failure_record
import needs as needs_mod
import capability
import capability_lint
import capability_proposal
import scratch_grant
import agents_spec
import fire_gate
import charging
import burn_filter
import masking
import mask_replay  # wq#4736: the rotated ledger — a settled value is not a new alarm
import cf_hold  # wq#2843: the CF-rotation hold shape, shared with foreman_dispatcher.py

# wq#1332 (WORKER_EXEC_CAPABILITY_GAP.md §2.4): fleet_exec_policy.py lives in the
# lumen-bin repo alongside the fleet-exec broker it backs, not in foreman — same
# cross-repo import shape holddown (bin/holddown) already uses for holddown_lib in
# /home/lumen/agent. fworker is the "dispatcher" §2.2 point 2 means: the per-attempt
# spawn preflight that turns a ticket's needs:ssh/copy/restart tags into a grant.
sys.path.insert(0, "/home/lumen/bin")
import fleet_exec_policy
import permission_profiles
import primary_tree_guard
from langfuse_client import emit_trace_with_span

# RELIABILITY_SPEC.md §A: --max-turns errors on ceiling with a real result frame, so
# fworker passes it and lets the CLI self-enforce. The supervisor's own real-turn
# count (classifier.RunState.turns) is a backstop watchdog only, set this many turns
# above --max-turns so the CLI's own enforcement gets first say in the common case.
TURN_BACKSTOP_BUFFER = 5

# Every root below is overridable via env — the CAL-1 fixture (and this build's own
# zero-quota integration test) exercises fworker against a scratch repo/wq db/events
# dir, never the live foreman tree, same pattern as lumen-pr's LUMEN_PR_WORKTREE_ROOT.
FOREMAN_ROOT = os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman")
HARNESS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_FILE = os.environ.get("FOREMAN_SETTINGS_FILE",
                                os.path.join(HARNESS_DIR, "worker-settings.json"))
GUARD_SH = os.path.join(HARNESS_DIR, "guard.sh")
EVENTS_DIR = os.environ.get("FOREMAN_EVENTS_DIR", os.path.join(FOREMAN_ROOT, "events"))
RUNS_ROOT = os.environ.get("FOREMAN_RUNS_ROOT", os.path.join(FOREMAN_ROOT, "_runs"))
FLEET_EXEC_POLICY_PATH = os.environ.get("FOREMAN_FLEET_EXEC_POLICY",
                                         os.path.join(HARNESS_DIR, "fleet-exec-policy.yaml"))
STOP_FILE = os.environ.get("FOREMAN_STOP_FILE", os.path.join(FOREMAN_ROOT, "STOP"))
# wq#736: the governor (governor/foreman_usaged.py) already polls both plan-usage
# windows into this file every 60-90s — the usage sidecar below just reads it at
# session start/exit rather than hitting the API itself. Own env var (not derived
# from FOREMAN_ROOT alone) so tests can point it at a scratch file without a real
# state.json in the way, matching STOP_FILE/RUNS_ROOT's pattern.
STATE_FILE = os.environ.get("FOREMAN_STATE_FILE", os.path.join(FOREMAN_ROOT, "state.json"))
GOVERNOR_CLI = os.environ.get("FOREMAN_GOVERNOR_CLI",
                               os.path.join(FOREMAN_ROOT, "governor", "foreman_usaged.py"))
WORKTREES_ROOT = os.environ.get("FOREMAN_WORKTREES_ROOT", "/home/lumen/worktrees")
# wq#954: the Bash output cap. claude-code 2.1.233 truncates an assembled Bash
# result to BASH_MAX_OUTPUT_LENGTH *characters* (default 30000, clamped at 150000,
# no lower bound — research/UNCAPPED_BASH.md §4), which replaces wq#729's
# `| head -c` command rewrite in guard.sh. Same 2000 as the old cap; the unit is
# now whole-result characters rather than stdout bytes, so stderr counts toward it
# and multi-byte output buys fewer bytes than it used to. The CLI interpolates this
# value into the Bash tool description, so lowering it also lowers the number the
# model is told it has.
BASH_MAX_OUTPUT_LENGTH = os.environ.get("FOREMAN_BASH_OUTPUT_CAP_CHARS", "2000")
WQ_BIN = os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq")
NTFY_BIN = os.environ.get("FOREMAN_NTFY_BIN", "/home/lumen/bin/ntfy-send")
# wq#617 orchestrator tier: routing (tuple assignment) + verify-pass filing.
ORCH_DIR = os.path.normpath(os.path.join(HARNESS_DIR, "..", "orchestrator"))
# wq#1183/wq#1635 memory: episodic append + archival commit (memory/README.md).
MEMORY_DIR = os.path.normpath(os.path.join(HARNESS_DIR, "..", "memory"))
ROUTER_CLI = os.environ.get("FOREMAN_ROUTER_CLI", os.path.join(ORCH_DIR, "router.py"))
JUDGMENT_CLI = os.environ.get("FOREMAN_JUDGMENT_CLI", os.path.join(ORCH_DIR, "judgment.py"))
# wq#616 (T2b): every cloud worker gets the durable setup-token as a static bearer —
# no refresh cycle, eliminates the shared ~/.claude/.credentials.json refresh-token
# race by construction (FOREMAN_RESEARCH.md §8, 3 GH issues on concurrent-session auth
# cascade). Path only, never the value: wq#615 stored it 0600, ORCH addendum forbids
# printing/logging/committing the token itself.
OAUTH_TOKEN_FILE = os.environ.get("FOREMAN_OAUTH_TOKEN_FILE",
                                   "/home/lumen/.secrets/claude-oat-worker.token")
# wq#754: same env file + contract as foreman_dispatcher.LANGFUSE_ENV — one trace per
# ticket run (stable id, so every attempt lands as a span on the same trace), best-effort.
LANGFUSE_ENV = os.environ.get("FOREMAN_LANGFUSE_ENV",
                               "/home/lumen/.secrets/langfuse-foreman.env")

# wq#1301 (foreman pattern D): all of the per-archetype knobs below (--max-turns,
# local-rung trust, wall-clock ceiling, add-dirs — WALL_CLOCK_DEFAULT_S/RCA_S and
# WORKER_ADD_DIRS further down this file) used to be separate hardcoded dicts here.
# They now come from harness/agents.yaml via agents_spec.py, so the archetype config
# is one reviewable/diffable file instead of scattered module constants — see
# agents.yaml's own header comment and agents_spec.lint() for the drift check against
# research/models.yaml. The names below are kept so every existing call site in this
# file (and its tests) is unchanged; only the definition moved.
_AGENTS = agents_spec.load()
ARCHETYPE_MAX_TURNS = {name: _AGENTS.get(name).max_turns for name in _AGENTS.names()}
DEFAULT_MAX_TURNS = ARCHETYPE_MAX_TURNS["standard-engineering"]

# wq#717 (AUTO-5): the fleet-fires manual-approval archetype. run_fire_ticket() is a
# dedicated flow, not do_attempt()'s retry ladder — see FIRE_MODE.md.
FIRE_ARCHETYPE = "fire-response"

# T4 (BUILD_PLAN.md §T4, wq#618): the LiteLLM local rung. Same trusted-route set as
# governor/governor_core.py's LOCAL_POOL_MODELS and orchestrator/judgment.py's
# LOCAL_POOL_MODELS — deliberately excludes nephthys-qwen3-14b (fallback brain only,
# not a wq worker per models.yaml).
# BEGIN GENERATED inference_nodes.yaml v1 — edit the registry, not this block
LOCAL_POOL_MODELS = ("deepseek-v4-flash", "qwen36-local", "nephthys-qwen14b", "hathor-llama8b", "local-worker")
# END GENERATED
# archetypes agents.yaml marks `local:` for — the only work the local rung is trusted
# with (FOREMAN_RESEARCH §9). Enforced here too (defense-in-depth) so an explicit
# --model/--archetype override can't dispatch a local-pool model outside what the
# router itself would ever choose.
LOCAL_ALLOWED_ARCHETYPES = tuple(
    name for name in _AGENTS.names() if _AGENTS.get(name).local_allowed)
# FOREMAN_RESEARCH §9: "cap local workers' --max-turns lower (they loop more)" —
# tighter than ARCHETYPE_MAX_TURNS' cloud defaults for the same two archetypes.
LOCAL_ARCHETYPE_MAX_TURNS = {
    name: _AGENTS.get(name).local_max_turns for name in LOCAL_ALLOWED_ARCHETYPES
}

LITELLM_BASE_URL = os.environ.get("FOREMAN_LITELLM_BASE_URL", "http://localhost:4000")
# scoped 'foreman' LiteLLM virtual key (T4) — plain-text 0600 file, same convention as
# /home/lumen/.secrets/*.token. NEVER the LiteLLM master key.
LITELLM_KEY_FILE = os.environ.get("FOREMAN_LITELLM_KEY_FILE",
                                   "/home/lumen/.secrets/foreman-litellm.token")
# wq#2831: Keenable.ai web-search MCP endpoint (api.keenable.ai/mcp). The key file
# is dropped by Frank (0600, outside the repo) after he signs up for the free tier —
# missing file means "not provisioned yet", not an error, so callers must tolerate None.
KEENABLE_TOKEN_FILE = os.environ.get("FOREMAN_KEENABLE_TOKEN_FILE",
                                      "/home/lumen/.secrets/keenable.token")
# T10 (wq#2278, TOKEN_ENGINE_DESIGN.md §7 rank 5): per-ticket gateway key,
# minted through the token-engine broker with a TTL and a budget and revoked
# at ticket-end/daemon-start. The long-lived virtual key above becomes the
# FALLBACK for hosts without a broker, and is removed once migration finishes.
TOKEN_ENGINE_SOCK = os.environ.get("TOKEN_ENGINE_SOCK", "/run/token-engine/broker.sock")
GATEWAY_KEY_TTL = os.environ.get("FOREMAN_GATEWAY_KEY_TTL", "24h")
GATEWAY_KEY_BUDGET_USD = os.environ.get("FOREMAN_GATEWAY_KEY_BUDGET_USD", "15.0")

# wq#2488: the reflexion critique riding the LiteLLM gateway is a System-1
# summarisation call (turn attempt 1's tail into a briefing), not attempt work,
# so it uses the same cheap_cloud rung (concurrency.py CHEAP_CLOUD_MODELS) the
# rest of the harness already trusts for that class of call rather than a full
# cloud model.
CRITIQUE_MODEL = os.environ.get("FOREMAN_CRITIQUE_MODEL", "deepseek-v4-flash")
CRITIQUE_TIMEOUT_S = 10


def is_local_model(model):
    return bool(model) and any(model.startswith(m) for m in LOCAL_POOL_MODELS)


# wq#2074: the 40k/131k-ctx rungs hold a full fworker prompt, so unlike the 8k qwen36-local
# they are also trusted for standard-engineering (the 141-ticket backlog's bulk archetype).
LOCAL_LARGE_CTX_MODELS = ("nephthys-qwen14b", "hathor-llama8b")
LOCAL_LARGE_CTX_ARCHETYPES = LOCAL_ALLOWED_ARCHETYPES + ("standard-engineering",)


def local_allowed_archetypes(model):
    if model and any(model.startswith(m) for m in LOCAL_LARGE_CTX_MODELS):
        return LOCAL_LARGE_CTX_ARCHETYPES
    return LOCAL_ALLOWED_ARCHETYPES


# wq#2128: the rung a failed attempt can be charged to. deepseek-v4-flash rides the same
# LiteLLM trust route but is a cloud model (cheap_cloud), so it keeps the cloud timeouts and
# is never charged as a rung fault; `local-worker` is the LiteLLM group the dispatcher rebuild
# (wq#2127) routes to the Macs.
# the explicit + ("local-worker",) keeps the group a rung even if the registry render
# ever drops it from LOCAL_POOL_MODELS again; dict.fromkeys dedupes the overlap (wq#2565).
LOCAL_RUNG_MODELS = tuple(dict.fromkeys(
    tuple(m for m in LOCAL_POOL_MODELS if m != "deepseek-v4-flash") + ("local-worker",)))
# dispatcher/runtime/backend_cooldown.json — the dispatcher reads it to skip a cooling rung.
BACKEND_COOLDOWN_FILE = os.environ.get(
    "FOREMAN_BACKEND_COOLDOWN_FILE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "dispatcher", "runtime", "backend_cooldown.json"))


def is_local_rung(model):
    return bool(model) and any(model.startswith(m) for m in LOCAL_RUNG_MODELS)


def backend_key(model):
    """Cooldown key: the dispatcher names the concrete box in FOREMAN_BACKEND when it
    spawns a worker; without it the dispatched model id is the finest grain we know."""
    return os.environ.get("FOREMAN_BACKEND") or model


def first_frame_timeout(model):
    return charging.first_frame_timeout_s(is_local_rung(model))


def turn_gap_timeout(model):
    """wq#2244: the inactivity gap after the first model frame. A local-rung model gets its
    backend's turn budget; cloud, and FOREMAN_CHARGE_MODE=legacy, keep the single
    INACTIVITY_TIMEOUT_S window."""
    if charging.legacy_mode() or not is_local_rung(model):
        return INACTIVITY_TIMEOUT_S
    return charging.turn_gap_timeout_s(True)


def local_cli_profile(model):
    """wq#2185: local-rung workers get the restricted CLI profile unless
    FOREMAN_CHARGE_MODE=legacy or FOREMAN_LOCAL_CLI_PROFILE=off."""
    return charging.local_cli_profile_on(is_local_rung(model))


# wq#687: silent-downgrade detection. The DeepSeek "best practices" doc that
# prompted this was mostly filter-evasion junk, but one real concern hid in it:
# "am I silently getting a weaker model than I asked for?" The honest answer is
# to compare the model the init frame REPORTS (state.model) against the model we
# DISPATCHED (args.model) — real result metadata, never a made-up header.
#
# Two swaps are LEGITIMATE and must never alarm:
#   1. Sanctioned safeguards redirects — the refusal_fallback targets in
#      models.yaml (Fable/most -> Opus 5 for bio, Opus 4.8 for cyber). These are
#      Anthropic's documented safety behaviour, not a downgrade. The CLI also
#      emits an explicit model_refusal_fallback / model_fallback frame for them,
#      which the classifier already records in state.refusal_fallbacks /
#      state.model_fallbacks — so a swap that is ACCOMPANIED by such a frame is
#      accounted for and never flagged here.
#   2. Local-rung dispatch — args.model IS a local id; the CLI (pointed at
#      LiteLLM) reports whatever LiteLLM served. Handled by is_local_model.
#
# Everything else — observed != dispatched with NO explaining frame — is a real
# unexplained swap and gets surfaced loudly on the attempt record. We do NOT
# retry or reroute on it (that is the orchestrator's call); the worker's job is
# to make it impossible to mistake a downgrade for a clean success.
SANCTIONED_FALLBACK_TARGETS = (
    "claude-opus-5", "claude-opus-4-8",  # refusal_fallback bio/cyber landing zones
)


def _norm_model(m):
    """Strip a trailing -YYYYMMDD date suffix so a future CLI that stamps dated
    ids (claude-fable-5-20260601) does not false-alarm against the bare id we
    dispatch. Bare ids pass through unchanged."""
    if not m:
        return m
    return re.sub(r"-20\d{6}$", "", m)


def check_model_downgrade(dispatched, observed, state):
    """-> (mismatch: bool, detail: str|None). mismatch=True only for an
    UNEXPLAINED swap (see SANCTIONED_FALLBACK_TARGETS / explaining frames)."""
    if not dispatched or not observed:
        return False, None
    d, o = _norm_model(dispatched), _norm_model(observed)
    if d == o:
        return False, None
    if is_local_model(dispatched):
        return False, None  # LiteLLM serves what it serves under a local id
    # a sanctioned safeguards redirect, or the CLI told us it swapped -> explained
    explained = (
        o in SANCTIONED_FALLBACK_TARGETS
        or bool(state.refusal_fallbacks)
        or bool(state.model_fallbacks)
    )
    if explained:
        return False, (f"model swap {d}->{o} EXPLAINED "
                       f"(sanctioned_target={o in SANCTIONED_FALLBACK_TARGETS} "
                       f"refusal_fallbacks={len(state.refusal_fallbacks)} "
                       f"model_fallbacks={len(state.model_fallbacks)})")
    return True, f"UNEXPLAINED model downgrade: dispatched {d}, ran {o}"


def local_rung_gate(model, archetype):
    """-> (allow, reason). Cloud models always pass. A local-pool model is only
    dispatchable for LOCAL_ALLOWED_ARCHETYPES — see module docstring above."""
    if not is_local_model(model):
        return True, None
    allowed = local_allowed_archetypes(model)
    if archetype not in allowed:
        return False, (f"local rung model {model!r} not trusted for archetype "
                        f"{archetype!r} (only {allowed})")
    return True, None


def read_litellm_key():
    with open(LITELLM_KEY_FILE) as f:
        return f.read().strip()


def read_keenable_key():
    """wq#2831: None (not a raised error) when the key isn't provisioned yet, so
    dispatch degrades to no-search rather than failing every attempt."""
    try:
        with open(KEENABLE_TOKEN_FILE) as f:
            return f.read().strip() or None
    except FileNotFoundError:
        return None


def mint_gateway_key(ticket_id, worker_id):
    """Per-ticket LiteLLM key via the token-engine broker (wq#2278, T10).
    Returns the key value for THIS attempt's env, or None to fall back to the
    long-lived key file — a dead broker degrades to the pre-T10 behaviour,
    never to a failed dispatch. The broker frame carries only the key id,
    expiry, and a handoff PATH (0640 tokeneng:token-client); the value is read
    from that file and the file unlinked, so after this returns the key exists
    in the attempt env and the daemon's ledger and nowhere else. Revocation is
    the existing ticket-end/reaper path: the daemon ledgers the mint under
    str(ticket_id), the same id guard.sh --lifecycle end sends."""
    if not os.path.exists(TOKEN_ENGINE_SOCK):
        return None
    try:
        expires = (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        grant = {"ticket": str(ticket_id), "expires": expires,
                 "grants": [{"kind": "gateway_admin", "node": "gateway:litellm"}]}
        raw = json.dumps(grant, sort_keys=True).encode()
        gpath = os.path.join(run_dir(ticket_id), "gateway-grant.json")
        if os.path.exists(gpath):
            os.chmod(gpath, 0o600)
        with open(gpath, "wb") as f:
            f.write(raw)
        os.chmod(gpath, 0o444)  # daemon re-reads + re-hashes it as uid tokeneng
        req = {"verb": "gateway-key",
               "argv": ["mint", "worker", GATEWAY_KEY_TTL, GATEWAY_KEY_BUDGET_USD],
               "ticket": str(ticket_id), "attempt": 1, "worker": worker_id,
               "grant_file": gpath,
               "grant_sha": fleet_exec_policy.sha256_bytes(raw)}
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(20)
        s.connect(TOKEN_ENGINE_SOCK)
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.close()
        resp = json.loads(buf.split(b"\n", 1)[0].decode())
        if not resp.get("ok"):
            log(f"gateway-key mint refused ({resp.get('rule')}: {resp.get('reason')}) "
                f"— falling back to the long-lived key")
            return None
        result = resp.get("result") or {}
        with open(result["key_file"]) as f:
            key = f.read().strip()
        os.unlink(result["key_file"])
        log(f"gateway-key: minted {result.get('key_id')} ttl={GATEWAY_KEY_TTL} "
            f"budget=${GATEWAY_KEY_BUDGET_USD} expires={result.get('expires')}")
        return key or None
    except Exception as e:
        log(f"gateway-key mint failed ({type(e).__name__}: {e}) — falling back "
            f"to the long-lived key")
        return None

def _envint(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# wq#1301: sourced from agents.yaml (see the ARCHETYPE_MAX_TURNS block above) —
# names kept for existing callers (dashboard_data.py reads WALL_CLOCK_RCA_S directly).
WALL_CLOCK_DEFAULT_S = _AGENTS.get("standard-engineering").wall_clock_timeout_s
WALL_CLOCK_RCA_S = _AGENTS.get("rca-investigation").wall_clock_timeout_s


def wall_clock_timeout(archetype):
    """Per-archetype wall-clock ceiling from agents.yaml; unlisted archetypes fall
    back to WALL_CLOCK_DEFAULT_S, same posture as ARCHETYPE_MAX_TURNS.get(...)."""
    if archetype in _AGENTS:
        return _AGENTS.get(archetype).wall_clock_timeout_s
    return WALL_CLOCK_DEFAULT_S
# RELIABILITY_SPEC.md §E: frame-gap watchdog is PRIMARY liveness, T_gap configurable
# 120-300s (above the slowest legit tool call) — wall-clock above is the secondary
# ceiling. 600s predates §E and sat well outside that range; --frame-gap-timeout / this
# env var override it per-run (tests need a fast one), the default now lives in-range.
#
# wq#2128: that one window is split. INACTIVITY_TIMEOUT_S (still FOREMAN_FRAME_GAP_S)
# now applies only AFTER the first model frame. Before it, FIRST_FRAME_TIMEOUT_S rules:
# cloud 240 (the old behaviour), local from FOREMAN_FIRST_FRAME_S (default 1500) because
# prompt eval on the Macs is 8-15 tok/s. FOREMAN_CHARGE_MODE=legacy restores one window.
INACTIVITY_TIMEOUT_S = _envint("FOREMAN_FRAME_GAP_S", 240)
FIRST_FRAME_TIMEOUT_S = charging.CLOUD_FIRST_FRAME_S
# §C9 SUCCESS-BUT-STUCK (#25629): once a terminal result frame has landed, a much
# shorter grace covers the "process just won't exit" case — no more real work is
# coming, so there is no reason to wait out the full frame-gap window before forcing it.
POST_RESULT_GRACE_S = _envint("FOREMAN_POST_RESULT_GRACE_S", 20)
# wq#4707: "no more real work is coming" is false while a background task the agent
# started has not reported back. The CLI keeps the session alive to re-invoke the agent
# with the completion notification, so the 20s grace was killing live workers mid-ticket
# (measured over _runs/wq*/episode.jsonl: 73 post_result_stuck kills, 60 FALSE_COMPLETE,
# 43 whose assistant tail says it is waiting on a background task — wq4686/wq4634/wq4644
# each had real work on disk, PR #2465 open). While a background task is outstanding the
# grace stretches to this instead. Wall-clock still bounds the run, and a child that
# really is finished closes its stdout (eof) in well under either window, so the longer
# window costs nothing on the happy path — it only applies to a process still alive.
POST_RESULT_BG_GRACE_S = _envint("FOREMAN_POST_RESULT_BG_GRACE_S", 900)
HEARTBEAT_INTERVAL_S = 10 * 60
MAX_ATTEMPTS = 2
RESUME_WAIT_MAX_S = 6 * 3600

# wq#992-B: a worker that ran to a terminal verdict has just vacated a cloud slot.
# Rather than let that slot sit idle until the next 20-min dispatch tick, kick one
# dispatch pass immediately so the freed slot refills in seconds — Frank's manual
# "finish -> relaunch" loop, put in the machine. See trigger_refill().
DISPATCH_SERVICE = os.environ.get("FOREMAN_DISPATCH_SERVICE",
                                  "lumen-foreman-dispatch.service")
DISPATCH_DISARM_PATH = os.environ.get("FOREMAN_DISPATCH_DISARM",
                                      "/home/lumen/autonomy/DISPATCH_DISARM")
# Settle delay so this fworker's own `fworker-wq<ID>` unit is fully gone before the
# kicked pass counts running slots (else its still-deactivating unit masks the slot it
# just freed). Small: 3s is negligible against the 20-min gap it replaces.
REFILL_SETTLE_S = _envint("FOREMAN_REFILL_SETTLE_S", 3)
# wq#992-C: once the ticket itself reads done, no more real work is coming. Poll the
# ticket on this cadence inside run_child; after POST_DONE_GRACE_S more with the child
# still alive, force it — this is the "agent ran wq done but keeps taking agentic turns
# with no result frame" case that post_result_grace never arms for (it would otherwise
# burn to the turn-backstop and misclassify TURNS). Grace is generous on purpose: the
# good case (wq done -> result frame -> exit) never trips it; only the lingerer does.
DONE_POLL_INTERVAL_S = _envint("FOREMAN_DONE_POLL_S", 15)
POST_DONE_GRACE_S = _envint("FOREMAN_POST_DONE_GRACE_S", 60)

# wq#725 AUTO-7: every classification that reaches handle_failure() (TURNS,
# LAUNCH_FAILURE, KILLED, OOM, REFUSED, BUDGET, CRASH) gets bucketed into one of three
# block classes so wq self-heal (dispatcher/self_heal.py) can tell an infra-crash block
# apart from a work-content block without re-deriving classifier.py's semantics:
#   INFRA_CRASH  spawn/process environment broke before or during the run; the ticket's
#                content was never really attempted -> safe to auto-release and requeue.
#   LIMIT_HIT    the run hit a turn or budget ceiling; may be worth retrying with more
#                budget, but that's a resourcing call, not a self-heal one.
#   WORK_BLOCKED the worker (or model) made a decision it can't do the work, or the
#                failure shape is an unrecognized catch-all -> always needs Frank; never
#                auto-requeued. The conservative default for anything not explicitly
#                known to be infra or a limit.
#   HARNESS_DEFECT (wq#1107) the run died on OUR configuration — the permission pack
#                refused a tool call the ticket legitimately needed. Requeueing is
#                pointless (the next attempt hits the identical wall) and the run is no
#                evidence about the model, so this bucket is deliberately outside
#                circuit_breaker.CATASTROPHIC_CLASSES: a settings regression must not
#                spend the fleet's dead-man's switch. Needs a harness fix, then release.
BLOCK_INFRA_CRASH = "INFRA_CRASH"
BLOCK_LIMIT_HIT = "LIMIT_HIT"
BLOCK_WORK_BLOCKED = "WORK_BLOCKED"
BLOCK_HARNESS_DEFECT = "HARNESS_DEFECT"

# wq#3539: AUTH_DENIED (an org-level subscription revocation) joins this bucket on
# purpose, unlike MODEL_NOT_FOUND below — the breaker SHOULD still trip on it (the
# fleet really is down), self_heal's ordinary release-on-attempts-remaining rule
# still applies, and its own extra re-arm logic is additive, not a replacement.
_INFRA_CRASH_CLASSIFICATIONS = {LAUNCH_FAILURE, KILLED, OOM, AUTH_DENIED}
_LIMIT_HIT_CLASSIFICATIONS = {TURNS, BUDGET}
_HARNESS_DEFECT_CLASSIFICATIONS = {PERMISSION_LOOP, MODEL_NOT_FOUND}


# --------------------------------------------------------------------------
# wq#5007: exit gate / circuit breaker glue (harness/exit_gate.py). Every helper here
# fails OPEN to the pre-wq#5007 behaviour and says so in the log: verify_done() and
# check_artifacts_done() stay the fail-closed wall behind the gate, so a bug in this
# glue can cost a nudge, never a wedged fleet.
# --------------------------------------------------------------------------

def _breaker(ticket_id):
    return exit_gate.CircuitBreaker(
        os.path.join(run_dir(ticket_id), exit_gate.BREAKER_FILENAME))


def exit_gate_preflight(ticket_id, count_call=True):
    """Reason string when this loop must not be spawned (circuit OPEN inside its
    cooldown, or the hourly call limit is spent); None to go ahead. shadow mode logs
    what it would have refused and returns None."""
    gate_mode = exit_gate.mode()
    if gate_mode == "off":
        return None
    try:
        allow, why = _breaker(ticket_id).preflight()
        if allow and why:
            log(f"exit_gate: wq#{ticket_id} {why}")
        if allow and count_call:
            allow, why = exit_gate.take_call(RUNS_ROOT, ticket_id)
        if not allow:
            if gate_mode == "shadow":
                log(f"exit_gate[shadow]: would refuse wq#{ticket_id}: {why}")
                return None
            return why
    except Exception as e:
        log(f"WARN exit_gate preflight failed open: {e}")
    return None


def exit_gate_defer(ticket_id, why):
    """Release the claim and leave a soft hold for the cooldown so the next dispatch
    tick does not walk straight back into the same refusal."""
    log(f"DEFERRED: wq#{ticket_id} {why} — releasing claim")
    try:
        wq("note", str(ticket_id), f"[exit_gate wq#5007] {why}")
    except Exception:
        pass
    wq_release(ticket_id)
    try:
        ttl = max(1, exit_gate.CB_COOLDOWN_S // 60)
        wq("hold", str(ticket_id), "--agent", "exit-gate", "--ttl", str(ttl))
    except Exception:
        pass
    print("DEFERRED")
    return EXIT_DEFERRED


def exit_gate_before_loop(ticket_id, worktree, attempt, ticket):
    """Per-loop git backup + the progress baseline. Returns the baseline dict."""
    base = {"fp": None, "ticket_sig": None, "backup_ref": None}
    if exit_gate.mode() == "off":
        return base
    try:
        base["backup_ref"] = exit_gate.git_backup(
            worktree, ticket_id, f"a{attempt}")
        if base["backup_ref"]:
            log(f"exit_gate: loop backup -> {base['backup_ref']}")
        base["fp"] = exit_gate.worktree_fingerprint(worktree)
        base["ticket_sig"] = _ticket_progress_sig(ticket)
    except Exception as e:
        log(f"WARN exit_gate backup/baseline failed open: {e}")
    return base


def _ticket_progress_sig(ticket):
    return f"{(ticket or {}).get('status')}|{len(str((ticket or {}).get('notes') or ''))}"


def exit_gate_after_loop(ticket_id, worktree, base, events, state, killed_reason):
    """Tell the breaker what this loop did. Progress is a changed worktree, a
    successful file-mutating tool call anywhere, or a changed ticket record; when none
    of those could be measured it is None (never counted against the ticket). A loop
    that parked on a hold or ended on a question holds the counters steady."""
    if exit_gate.mode() == "off":
        return None
    try:
        stats = exit_gate.loop_stats(events)
        exit_gate.note_session(run_dir(ticket_id), state.session_id)
        fp_after = exit_gate.worktree_fingerprint(worktree)
        try:
            sig_after = _ticket_progress_sig(wq_show(ticket_id))
        except Exception:
            sig_after = None
        measured = base.get("fp") is not None and fp_after is not None
        progress = bool(stats["mutations"]
                        or (measured and fp_after != base["fp"])
                        or (sig_after is not None and base.get("ticket_sig") is not None
                            and sig_after != base["ticket_sig"]))
        if not progress and not measured:
            progress = None
        parked = getattr(state, "stop_reason", None) in ("tool_deferred",)
        err = stats["error_sig"]
        if killed_reason and err:
            err = f"{killed_reason}:{err}"
        cb = _breaker(ticket_id)
        new_state = cb.record_loop(progress, error_sig=err,
                                   output_chars=stats["output_chars"],
                                   asking_questions=stats["asking_questions"] or parked)
        if new_state != exit_gate.CLOSED:
            log(f"exit_gate: wq#{ticket_id} circuit {new_state} ({cb.s.get('reason')})")
            if new_state == exit_gate.OPEN:
                ntfy("foreman circuit OPEN",
                     f"wq#{ticket_id}: {cb.s.get('reason')} — no new loop for "
                     f"{exit_gate.CB_COOLDOWN_S // 60} min, then one half-open probe",
                     priority="high")
        stats["progress"] = progress
        stats["circuit"] = new_state
        stats.pop("final_text", None)
        return stats
    except Exception as e:
        log(f"WARN exit_gate loop record failed open: {e}")
        return None


def exit_gate_decide(ticket_id, raw_classification, worker_sentinel, landed_sha, state):
    """The dual-condition verdict for a worker exit the harness is about to accept."""
    try:
        status = wq_show(ticket_id).get("status")
    except Exception:
        status = None
    indicators = {
        "clean_result": raw_classification == SUCCESS,
        "ticket_done": status == "done",
        "landed_commit": bool(landed_sha),
        "closed_sentinel": worker_sentinel == TICKET_CLOSED_SENTINEL,
    }
    text = state.result_text if isinstance(state.result_text, str) else ""
    return exit_gate.decide(indicators, exit_gate.parse_exit_signal(text)), status


def loop_break_classification(raw_classification, killed_reason, loop_tracker):
    """wq#1107. The loop breaker's SIGTERM makes every trip exit 143 -> KILLED ->
    INFRA_CRASH. That is right when the model was genuinely repeating a failed approach
    and wrong when the repeated failure was this harness's own permission pack refusing
    the call: INFRA_CRASH requeues the ticket into the identical wall, and the dispatcher
    circuit breaker spends its trip budget on a settings bug (2026-08-29: 8/9 attempts,
    breaker at 89%, DISPATCH_DISARM set). Callers keep the KILLED verdict in
    `raw_classification` — this only changes what the ticket and the breaker are told."""
    if (killed_reason == "loop_break"
            and getattr(loop_tracker, "trip_kind", None) == "PERMISSION"):
        return PERMISSION_LOOP
    return raw_classification


def classify_block_reason(classification):
    if classification in _HARNESS_DEFECT_CLASSIFICATIONS:
        return BLOCK_HARNESS_DEFECT
    if classification in _INFRA_CRASH_CLASSIFICATIONS:
        return BLOCK_INFRA_CRASH
    if classification in _LIMIT_HIT_CLASSIFICATIONS:
        return BLOCK_LIMIT_HIT
    return BLOCK_WORK_BLOCKED

OTEL_ENDPOINT = "http://100.110.156.91:8429/opentelemetry"

EXIT_OK = 0
EXIT_CLAIM_ERROR = 10
EXIT_DEFERRED = 11
EXIT_BLOCKED = 12
EXIT_LIMIT = 13
EXIT_DEPS_UNMET = 14
EXIT_CRASH = 15
# wq#717 (AUTO-5) fire-mode
EXIT_FIRE_TIMEOUT = 16
EXIT_FIRE_DIAGNOSIS_FAILED = 17
EXIT_FIRE_APPLY_INCOMPLETE = 18
EXIT_FIRE_MAX_RUNTIME = 19  # wq#1234: hard wall-clock cap exceeded, blocked for a human
EXIT_FIRE_ATTEMPT_FENCE = 20  # wq#1261: 3rd expired/rework attempt, escalated to FRANK
EXIT_LAUNCH_REFUSED = 21  # wq#1331: spawn preflight refused (repo mismatch / unreachable
                          # cited path) — harness defect, attempt not charged
EXIT_MODEL_NOT_FOUND = 22  # wq#1442: dispatched --model id/alias rejected by the CLI
                           # (model_not_found/404) — harness defect, attempt not charged
EXIT_RUNG_FAULT = 23  # wq#2128: the model backend failed, not the ticket — released to open
EXIT_LOCAL_ESCALATED = 24  # wq#2581: local rung exhausted, handed to a cloud rung — released to open
                      # uncharged, backend put on cooldown
EXIT_AWAITING_GRANT = 25  # wq#4765 (§2.3 step 3): a PreToolUse `defer` parked the ticket
                          # (wq await-grant) awaiting a human/orchestrator decision —
                          # uncharged, no refill kick (not in main()'s trigger_refill set)
EXIT_HOLD_RESUME_LOST = 26  # wq#4765 F7: a grant-approved resume's deferred call could not
                            # be re-fired (session pruned, or tool_deferred_unavailable) —
                            # released back to open uncharged for a fresh attempt

# wq#1261 (#13 item 3): expired/rework fire tickets re-dispatch at most FIRE_ATTEMPT_FENCE
# times before the 3rd strike escalates to FRANK instead of running again. Only outcomes
# that mean the ticket bounced without resolving count against the fence — an auto-close
# (probe cleared) or a successful apply never do.
FIRE_ATTEMPT_FENCE = _envint("FOREMAN_FIRE_ATTEMPT_FENCE", 2)
FIRE_FENCE_OUTCOMES = frozenset({"expired", "diagnosis_failed", "timeout_release", "apply_incomplete"})


def log(msg):
    print(f"[fworker {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {msg}", flush=True)


def emit_attempt_span(ticket_id, lane, attempt_record):
    """wq#754: one Langfuse span per attempt, all attempts of one ticket sharing a
    stable trace id — the debugging replacement for grepping attempt-N.json/jsonl.
    Best-effort by contract (DESIGN.md §8): never raises into the caller, degrades to
    a log line when creds aren't minted yet."""
    try:
        shipped = emit_trace_with_span(
            LANGFUSE_ENV,
            trace_id=f"fworker-wq{ticket_id}",
            trace_name=f"fworker:wq{ticket_id}",
            span_name=f"attempt-{attempt_record['attempt']}:{attempt_record['classification']}",
            metadata={
                "ticket": ticket_id, "lane": lane,
                "model": attempt_record["model"], "effort": attempt_record["effort"],
                "archetype": attempt_record["archetype"],
                "attempt": attempt_record["attempt"],
                "outcome_class": attempt_record["classification"],
                "raw_classification": attempt_record["raw_classification"],
                "exit_code": attempt_record["exit_code"],
                "turns": attempt_record["turns"],
                "elapsed_s": attempt_record["elapsed_s"],
                "total_cost_usd": attempt_record.get("total_cost_usd"),
                "session_id": attempt_record["session_id"],
            },
            tags=["fworker", lane or "unknown"],
            trace_metadata={"ticket": ticket_id, "lane": lane},
        )
        if shipped is None:
            log(f"langfuse_disabled — no keys at {LANGFUSE_ENV}, log-only degradation")
    except Exception as e:  # noqa: BLE001 — best-effort by contract (§8), same as dispatcher
        log(f"langfuse_emit_failed: {e!r}")


# ---------------------------------------------------------------------------
# governor gate (T1 contract, design §9)
# ---------------------------------------------------------------------------

def can_dispatch(model):
    """STOP file check, then foreman_usaged.py can-dispatch. Returns (allow, reason)."""
    if os.path.exists(STOP_FILE):
        return False, "STOP file present"
    try:
        p = subprocess.run(
            ["python3", GOVERNOR_CLI, "can-dispatch", model],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        return False, f"governor unreachable: {e}"
    if p.returncode != 0:
        return False, f"governor denied: {p.stdout.strip() or p.stderr.strip()}"
    return True, p.stdout.strip()


def report_limit_event(frame):
    try:
        subprocess.run(
            ["python3", GOVERNOR_CLI, "report-event", json.dumps(frame)],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as e:
        log(f"report-event failed (non-fatal): {e}")


# ---------------------------------------------------------------------------
# wq wrapper (design §7)
# ---------------------------------------------------------------------------

def wq(*args, check=False):
    cmd = [WQ_BIN] + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"wq {' '.join(args)} failed: {p.stdout} {p.stderr}")
    return p


def wq_show(ticket_id):
    """wq#1496: this used to parse `wq show <id>`'s plain-text `key : value` lines —
    stale comment, wq has had --json since wq#1277. Plain-text mode escapes embedded
    newlines as literal backslash-n, which silently broke any MULTILINE-anchored
    consumer of a multi-line detail/notes field. --json round-trips real newlines and
    native column types."""
    p = wq("show", str(ticket_id), "--json")
    try:
        out = json.loads(p.stdout)
    except ValueError:
        return {}
    return out if isinstance(out, dict) and not out.get("error") else {}


def ticket_holder(ticket):
    """wq#869: the identity currently holding `ticket`, or None if unheld.

    wq_show()'s --json path (wq#1496) returns a NULL claimed_by as real None, but the
    "None" string branch stays: a caller holding a pre-wq#1496 plain-text-parsed dict
    (or a literally-named "None" agent) must not be treated as held. Normalising here
    keeps "the claim went away" and "the claim changed hands" one comparison rather
    than three at every call site.
    """
    holder = (ticket.get("claimed_by") or "").strip()
    return None if holder in ("", "None") else holder


def deps_unmet(ticket_id):
    """Read-only dep check ahead of claim/spawn (BUILD_PLAN.md §T2a: "dep-preflight
    before spawn"). `wq claim --id` already refuses at the DB layer if deps aren't
    done (deps_met() in /home/lumen/bin/wq), so this can't let an unready ticket
    through either way — but routing it through that generic claim failure would
    surface as CLAIM_ERROR, indistinguishable from a real claim race. Checking here
    first gives an unmet dep its own clean DEFERRED outcome, with no claim attempt
    and no worktree ever touched.  Returns a list of (dep_id, dep_status) for every
    unmet dep; empty list means ready.
    """
    ticket = wq_show(ticket_id)
    deps = ticket.get("deps", "")
    unmet = []
    for dep_id in [d.strip() for d in deps.split(",") if d.strip()]:
        dep = wq_show(dep_id)
        status = dep.get("status", "unknown")
        if status != "done":
            unmet.append((dep_id, status))
    return unmet


def wq_identity(ticket_id):
    """wq#1116: the identity that OWNS this ticket in wq. Stable across attempts, because what
    wq owns is the TICKET, not the attempt.

    fworker used to claim under its own `lumen:<pid>` fallback and then hand the worker a
    different WQ_AGENT (`fworker-wq<id>-a<n>`), so the name that claimed and the name that closed
    disagreed on every single dispatch. That is what made wq's unregistered-caller backcompat
    branch load-bearing, and that branch is what let two orchestrator sessions close each other's
    tickets on 2026-08-30. Claiming under the name the worker will actually use costs nothing and
    lets wq's holder check mean what it says. worker_id stays per-attempt for logs, events and
    worktree naming; only the wq identity is per-ticket.
    """
    return f"fworker-wq{ticket_id}"


def wq_claim(ticket_id):
    p = wq("claim", wq_identity(ticket_id), "--id", str(ticket_id))
    return p.returncode == 0, p.stdout + p.stderr


def wq_heartbeat(ticket_id):
    wq("heartbeat", str(ticket_id))


# ---------------------------------------------------------------------------
# wq#4765 (agent-authority DESIGN.md §2.3 steps 2-6, §2.6): hold/resume grant reads.
# wq's own write path (`wq grant request/approve/deny/consume`) belongs to hold_gate.py
# and a human/orchestrator — fworker only ever READS grant_requests, via `wq grant
# list/show --json` (wq#4765 companion bin-repo change), never the sqlite file
# directly: wq owns that db's locking and schema.
# ---------------------------------------------------------------------------

def grant_pending_row(ticket_id, session_id):
    """The live pending grant_requests row hold_gate.py filed for this ticket's deferred
    call, or None if there isn't exactly one. §2.6: tool_use_id binds the approval to
    THE ONE pending call, so more than one live pending row for the same (ticket,
    session) is itself a sign something is wrong — treated the same as zero (the
    caller's cross-check then mismatches and falls through to CAPABILITY_DENIED,
    never guesses which row was meant)."""
    if not session_id:
        return None
    p = wq("grant", "list", "--ticket", str(ticket_id), "--session", session_id,
           "--state", "pending", "--json")
    if p.returncode != 0:
        log(f"wq#{ticket_id}: grant list --json failed: {p.stdout} {p.stderr}")
        return None
    try:
        rows = json.loads(p.stdout)
    except ValueError:
        log(f"wq#{ticket_id}: grant list --json gave unparseable output: {p.stdout!r}")
        return None
    return rows[0] if isinstance(rows, list) and len(rows) == 1 else None


def grant_row_by_id(req_id):
    """The grant_requests row by id, or None. Used on a grant-approved resume, where
    the ticket row already names the exact `grant_req` — no (ticket, session) search
    needed, just a direct read of that row's class/target."""
    p = wq("grant", "show", str(req_id), "--json")
    if p.returncode != 0:
        log(f"grant show {req_id} --json failed: {p.stdout} {p.stderr}")
        return None
    try:
        rows = json.loads(p.stdout)
    except ValueError:
        return None
    return rows[0] if isinstance(rows, list) and len(rows) == 1 else None


def wq_await_grant(ticket_id, req_id, session_id):
    p = wq("await-grant", str(ticket_id), "--req", str(req_id), "--session", session_id)
    return p.returncode == 0, p.stdout + p.stderr


def handle_tool_deferred(ticket_id, state):
    """§2.3 steps 2-3, §2.6, §2.7 F4/F9: the child's result frame carried stop_reason
    "tool_deferred" — a PreToolUse `defer` ended the turn. Cross-check deferred_tool_use.id
    against the grant_requests row hold_gate.py filed for this exact call; a clean match
    parks the ticket (`wq await-grant`) and returns EXIT_AWAITING_GRANT, uncharged. A
    mismatch (stale/missing row, race, no session_id) is HOLD_FRAME_MISMATCH: returns
    None so the caller falls through to the ordinary classify()/retry path — exactly
    today's CAPABILITY_DENIED, never a parked ticket for a request that may not
    describe this call."""
    dtu = state.deferred_tool_use or {}
    dtu_id = dtu.get("id")
    row = grant_pending_row(ticket_id, state.session_id)
    if not dtu_id or not row or row.get("tool_use_id") != dtu_id:
        log(f"wq#{ticket_id}: HOLD_FRAME_MISMATCH (deferred_tool_use.id={dtu_id!r}, "
            f"session={state.session_id!r}, matching grant row={row!r}) — treating as "
            f"CAPABILITY_DENIED, not parking")
        return None
    req_id = row["id"]
    ok, out = wq_await_grant(ticket_id, req_id, state.session_id)
    if not ok:
        log(f"wq#{ticket_id}: await-grant failed (req={req_id}): {out.strip()} — "
            f"releasing instead of parking")
        wq_release(ticket_id)
        print("RELEASED")
        return EXIT_CLAIM_ERROR
    ntfy("wq grant awaiting approval",
         f"wq#{ticket_id}: held on class={row.get('class') or '-'} "
         f"target={row.get('target') or '-'}. "
         f"Approve: /home/lumen/bin/wq grant approve {req_id}   "
         f"Deny: /home/lumen/bin/wq grant deny {req_id} --reason \"<why>\"",
         priority="high")
    log(f"wq#{ticket_id}: AWAITING_GRANT req={req_id} class={row.get('class')} "
        f"target={row.get('target')} session={state.session_id}")
    print("AWAITING_GRANT")
    return EXIT_AWAITING_GRANT


def apply_deferral_gate(ticket_id, notes):
    """wq#2597: the notes, with any deferred AC work filed as its own ticket and cited.

    Never raises. The gate exists to stop deferred work from disappearing, and a gate that
    can fail a close would trade that leak for a worse one: a finished ticket left claimed
    and open because `wq add` had a bad minute.

    The scan happens BEFORE the row is read, so a closure that defers nothing — nearly all of
    them — adds no wq call to the close path at all.
    """
    if not deferral_gate.scan(notes, ticket_id):
        return notes
    row = wq_show(ticket_id)
    probe = None
    m = re.search(r"^Liveness:\s*(.+)$", row.get("detail") or "", re.MULTILINE)
    if m:
        probe = m.group(1).strip()
    try:
        return deferral_gate.enforce(
            ticket_id, notes, run_wq=lambda argv: wq(*[str(x) for x in argv]),
            lane=(row.get("lane") or "build"), pri=(row.get("pri") or 2),
            probe=probe, log=log)
    except Exception as e:  # noqa: BLE001 — see docstring: the close outranks the gate
        log(f"wq#{ticket_id}: deferral gate errored, closing with original notes: {e}")
        return notes


def wq_done(ticket_id, notes, no_artifact_reason=None, unlanded_reason=None):
    """Close the ticket. `no_artifact_reason` is the CALLER's own assertion that this
    ticket has no code artifact to cite, plus the reason why — only a caller that knows
    it applied no code change may pass one (wq#1925).

    `unlanded_reason` is the same kind of caller-side assertion for wq#1673's evidence-
    DURABILITY gate: the notes cite a real path that is deliberately never committed to
    main (a run-dir artifact), so "land it and retry" can never be satisfied. Asserted up
    front rather than retried on a refusal text-match, for wq#1925's reason: the refusal
    text cannot tell a deliberately-unlanded path from one that merely has not landed yet.

    wq#2597: notes that defer part of the AC get the deferred work filed as its own ticket
    before the close, because wq#2583 deferred an AC bullet in exactly these notes and
    nothing carried it (harness/deferral_gate.py).
    """
    notes = apply_deferral_gate(ticket_id, notes)
    extra = ["--unlanded", unlanded_reason] if unlanded_reason else []
    p = wq("done", str(ticket_id), "--notes", notes, *extra)
    if p.returncode != 0:
        out = (p.stdout or "") + " " + (p.stderr or "")
        # wq#759 evidence gate: `done` is refused (exit 2) unless --notes names a verifiable
        # artifact (file/PR/commit). Fire-mode + operational tickets legitimately have NO code
        # artifact, so bare `done` was refused and fire-mode treated the refusal as a CRASH.
        # On the evidence-gate refusal, retry ONCE with --no-artifact + the caller's reason
        # (honest: it documents WHY there is no artifact, does not fake one).
        #
        # wq#1925 (wq#1924 CONTRADICT): the retry used to fire for EVERY caller on a text
        # match alone, and refusal text cannot tell "nothing to cite" from "something to
        # cite, badly worded notes". handle_resumable_failure() only reaches `done` after
        # check_artifacts_done() found a real landed commit, so its prose-only notes hit the
        # gate and closed the ticket labelled "no code artifact" — a false label, and one
        # that lands in the very audit bucket (wq closure-quality) wq#759 exists to track.
        # Artifact-lessness is now asserted by the caller, not inferred here; every other
        # caller still raises loudly on the refusal, which is what makes it investigable.
        if no_artifact_reason and ("759" in out or "evidence gate" in out.lower()
                                   or "verifiable artifact" in out.lower()):
            p2 = wq("done", str(ticket_id), "--notes", notes,
                    "--no-artifact", no_artifact_reason, *extra)
            if p2.returncode == 0:
                log(f"wq#{ticket_id} closed via --no-artifact (operational ticket, no code artifact)")
                return
            out = (p2.stdout or "") + " " + (p2.stderr or "")
        log(f"ERROR: wq done {ticket_id} refused (exit {p.returncode}): {out.strip()}")
        raise RuntimeError(f"wq done {ticket_id} refused (exit {p.returncode}): {out.strip()}")


_ROOT_APPLY_ADDED_RE = re.compile(r"^added task (\d+)")
_ROOT_APPLY_DUP_RE = re.compile(r"^DUPLICATE — task (\d+)")


def _ops_root_apply_title(bundle):
    return f"[ops-root-apply] {bundle}: install.sh --verify fails, needs privileged install"


def file_root_apply_followups(ticket_id, lane, followups):
    """wq#4778 (ISSUE-LOG #1636): acceptance.verify()'s citation gate now accepts a
    `no_deploy_needed` override on an ops-bundle citation (same shape as wq done's own
    --no-deploy-needed, wq#1724/wq#3487) for exactly the case a sandboxed BUILD worker
    hits — no ssh/fleet-exec grant onto the live host, deploy target outside every
    worktree's allowed dirs. acceptance.py stays "no wq, no dispatch" (its own module
    docstring) and only hands back a followup descriptor per citation; this is the one
    layer up that actually owns wq, so it is the one that files or reuses the tracked
    follow-up — `wq add`'s own normalized-title dedup (cmd_add) collapses repeated
    closes against the same still-undeployed bundle onto one ticket, same as the
    `_file_or_reuse_root_apply_followup` shape `wq done` already uses. Best effort: a
    filing failure here is never a reason to fail a replay that otherwise passed.
    """
    filed = []
    for f in followups or []:
        bundle = f.get("bundle") or "?"
        install_path = f.get("install_path") or "?"
        why = f.get("why") or "no_deploy_needed"
        title = _ops_root_apply_title(bundle)
        detail = (f"wq#{ticket_id}'s evidence citation closed over {install_path} with "
                  f"no_deploy_needed={why!r}. Run the bundle's install.sh as root (or with "
                  f"sudo), then confirm with `bash {install_path} --verify`.\n"
                  f"Liveness: ops_verified({bundle})")
        p = wq("add", "--lane", lane, "--title", title, "--detail", detail)
        out = (p.stdout or "") + (p.stderr or "")
        m = _ROOT_APPLY_ADDED_RE.search(out) or _ROOT_APPLY_DUP_RE.search(out)
        if m:
            filed.append(int(m.group(1)))
        else:
            log(f"wq#{ticket_id}: wq#4778 root-apply follow-up filing for {bundle} "
                f"produced no parseable ticket id ({out.strip()!r}) — not blocking")
    if filed:
        wq("note", str(ticket_id),
           "[ROOT-APPLY-FILED: " + ", ".join(f"wq#{i}" for i in filed) + "]")
    return filed


def wq_release(ticket_id):
    wq("release", str(ticket_id))


def wq_block(ticket_id, reason):
    """Block deliberately carries NO liveness tag. wq#1676 briefly made one mandatory here
    and refused untagged blocks with exit 6; wq#1998 made the gate advisory at `block`
    instead, precisely because a refusal strands the row in `claimed` under a caller that
    has already given up. Passing --no-probe would clear the warning, but it also stamps
    [NO-PROBE: ...] into the detail, and that marker is what liveness_clear.age_alarm()
    skips on — so tagging every automated block would silence the 7-day stale-gate alarm
    for the exact population it exists to catch. The warning is the correct outcome.

    wq#1721: the return code used to be dropped on the floor, so during the window when
    the gate WAS fatal, every fire-mode and evidence-gate block silently no-op'd while
    fworker went on reporting BLOCKED and the ticket stayed claimed and open. A block that
    did not take has to be loud.
    """
    p = wq("block", str(ticket_id), reason)
    if p.returncode != 0:
        out = ((p.stdout or "") + " " + (p.stderr or "")).strip()
        log(f"ERROR: wq block {ticket_id} refused (exit {p.returncode}): {out}")
    return p


def record_recipe_outcome(ticket, args, success, triage=None):
    """wq#4235: best-effort crystallize/freeze update for this ticket's class. Never
    allowed to affect the ticket's own done/block outcome — recipe_store's
    record_outcome() already fails open (PG down -> JSON fallback); this wraps the classification
    step too, since a bad title/detail regex match is no more a fworker-fatal error
    than a store write failure is.

    wq#4254: `triage` is the terminal failure's failure_record triage class (None on
    the success path, which has no triage). A failure whose triage is in
    TRIAGE_NON_COMPETENCE is NOT recorded at all — not as a loss, not as a win. The
    cell's streak is a claim about this (model, prompt_template)'s competence at this
    ticket class, and a self-block / sandbox denial / harness bug is evidence about
    the ticket or the harness, not about the model. Recording it zeroed the streak and
    unfroze a working recipe (wq#4245's live repro); skipping it leaves the cell
    exactly as the last competence-bearing outcome left it."""
    if triage in failure_record.TRIAGE_NON_COMPETENCE:
        log(f"recipe_store: skipping {triage} outcome (not a competence signal)")
        return
    try:
        ticket_type = recipe_store.classify_ticket_type(
            ticket.get("title") or "", ticket.get("detail") or "")
        if not ticket_type:
            return
        recipe_store.record_outcome(
            ticket_type, args.model, args.archetype or "standard-engineering", success)
    except Exception as e:  # noqa: BLE001 — best-effort by contract
        log(f"recipe_store.record_outcome failed (non-fatal): {e!r}")


def ntfy(title, msg, priority="default"):
    # wq#1407: bin/ntfy-send parses -T/-p only; --title/--priority fell into the
    # message body and every alert went out at default priority (162 in 48h).
    try:
        subprocess.run([NTFY_BIN, "-T", title, "-p", priority, msg],
                        capture_output=True, text=True, timeout=10)
    except Exception:
        pass


def write_mask_hits(ticket_id, attempt, records):
    """wq#2288: persist the redacted forensics for an alarm next to attempt-N.json and
    return the path. Without this the rotation ticket names only a pattern label, and
    the credential cannot be identified at all — which is exactly how wq#2288 (the
    first tripwire firing, on wq#2284) arrived unactionable. Records hold a prefix, a
    length, 12 hex of sha256 and masked context; no value. Best-effort by contract.

    wq#2677: the same file also carries SUPPRESSED records — a candidate the kv-secret
    rule declined to read, tagged with the reason (pointer/call/public-key) and no
    context, since a suppressed value is unmasked in the text it came from. They are
    written by note_suppressions() rather than by an alarm, so the file can exist on a
    run that never alarmed at all; that is the point, because a suppression that should
    have been a hit looks exactly like a clean run from here (wq#2288/wq#2632). Nothing
    that escalates may key on them — see burned_hashes."""
    path = os.path.join(run_dir(ticket_id), f"mask-hits-{attempt}.json")
    try:
        try:
            with open(path) as f:
                prior = json.load(f)
        except (OSError, ValueError):
            prior = []
        stamped = [dict(r, ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                   for r in records or []]
        tmp = f"{path}.tmp"
        with open(acceptance_mod.private_open(tmp), "w") as f:
            json.dump(prior + stamped, f, indent=2)
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        log(f"WARN: mask-hit record write failed for wq#{ticket_id}: {e!r}")
    return path


# wq#2677: a run that suppresses the same pointer on every line of a 20k-line stream
# must not write 20k rows. Distinct (reason, value) pairs are what carry information —
# the cap is per ticket and generous enough that hitting it is itself the signal a
# carve-out has gone overbroad, which is the thing these records exist to show.
SUPPRESSED_CAP = 64
# Sits in the same sink as the (reason, sha12) keys, so the cap is announced once per
# ticket without a second piece of state to keep in step.
SUPPRESSED_CAP_KEY = ("cap", "")


def note_suppressions(ticket_id, attempt, text):
    """Record what the mask rules DECLINED to alarm on, once per distinct value.

    The alarm path only ever wrote on a hit, so the one failure it could not see was
    its own silence: wq#2288's lookahead suppressed every opaque value glued to a `(`
    for a full PR cycle and nothing noticed, because a dropped match and clean text are
    the same bytes downstream (wq#2322, wq#2632 option 3). This runs on every line the
    tripwire reads — before the `not fresh` return in screen_stream_hits, so a run with
    no hits at all still leaves the trail — and writes a row the first time a given
    (reason, value) appears.

    Scoped to the RAW line rather than the model-facing context (wq#2393) on purpose: a
    suppression is not a page, so the narrowing that decides whether a value is burned
    does not apply, and the question being answered — what did this rule table decline
    to read — is about everything it judged.

    The probe is this process's IMPORTED table, not masking.deployed(): the record
    explains what THIS process suppressed, and a deployed-file check costs a stat per
    stream line for an audit row that pages nobody (the reload in drop_stale_labels is
    what keeps a hot fix reaching the runs that DO page).
    """
    try:
        found = masking.suppressions(text)
    except Exception as e:  # noqa: BLE001 - an audit row never breaks the read loop
        log(f"WARN: suppression scan failed for wq#{ticket_id}: {e!r}")
        return []
    if not found:
        return []
    sink = mask_state(ticket_id).setdefault("suppressed", set())
    fresh = []
    for rec in found:
        if len(sink) >= SUPPRESSED_CAP:
            if SUPPRESSED_CAP_KEY not in sink:
                sink.add(SUPPRESSED_CAP_KEY)   # say it once, then stay quiet
                log(f"mask suppression wq#{ticket_id} attempt {attempt}: "
                    f"{SUPPRESSED_CAP} distinct suppressed candidates recorded — "
                    f"capped, later ones are not written")
            break
        key = (rec.get("suppressed"), rec.get("value_sha256_12"))
        if key in sink:
            continue
        sink.add(key)
        fresh.append(rec)
    if fresh:
        write_mask_hits(ticket_id, attempt, fresh)
    return fresh


ROTATION_TITLE_PREFIX = "[rotation] burned credential"
# The same text also appears mid-title on the verify pass a rotation ticket spawns:
# `[verify] wq#2294 [rotation] burned credential in wq#2284 transcript (github)`.
#
# wq#2341 stops the marker BEFORE the run id on purpose. A row filed since then is
# keyed by the burned value (`[rotation] burned credential sha256:<12 hex> (github)`)
# and names no run at all, while the 47 the storm filed — and every verify row they
# spawned — still read `in wq#M transcript`. One marker both generations carry is what
# keeps the chain guard covering the backlog it was written for.

# wq#2332: the settle tool the rotation ticket has to name. A worker handed only
# "sha256 a candidate and compare" rebuilds mask_replay's corpus walk by hand — that
# is an hour of an xhigh worker, and the hand-grep re-arms the alarm it is answering.
# Absolute because the answering worker's cwd is its own lane's worktree, which is not
# necessarily this repo; mask_replay resolves the tracked tree from its own __file__.
MASK_REPLAY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mask_replay.py")

ROTATION_DETAIL_PATH = os.path.join(HARNESS_DIR, "prompts", "rotation-ticket.md")

# wq#2546 (BUILD d): the at-rest masker mask_alarm triggers on a live hit — see
# trigger_mask_at_rest() below.
MASK_AT_REST = os.path.join(HARNESS_DIR, "mask_at_rest.py")


def trigger_mask_at_rest(session_id):
    """wq#2546: best-effort, fire-and-forget at-rest mask of the CC transcript the
    instant a live alarm tells us a value just reached it — no reason to wait for
    the nightly sweep (lumen-mask-at-rest.timer) when we already know which file.

    Detached (start_new_session=True) and never waited on: this runs from inside
    the hot stream-read loop (screen_mask_hits, mid-attempt), and mask_at_rest.py
    does its own off-box restic snapshot before masking — seconds, not
    milliseconds. Blocking here would eat into the same wall-clock/inactivity
    budget the alarm's own attempt is racing. Output discarded; a dead or missing
    script degrades to "the nightly timer catches it next", never to a crash of
    the attempt whose alarm triggered it.

    `session_id=None` (the alarm fired before the CLI's init frame named one, or
    the hit came from the merged-stderr/envelope path) falls through to
    mask_at_rest's own default — the whole transcripts tree, not just one file —
    which is the correct fallback, not a special case to detect here.
    """
    # 2026-09-26: FOREMAN_NO_MASK_AT_REST=1 (same shape as FOREMAN_NO_REFILL, wq#2069)
    # keeps a test process from launching a REAL store sweep. test_masking.py calls
    # mask_alarm() directly; every pytest run of a masking ticket spawned a detached
    # `--store all` (whole store + offsite restic) from the worktree, and wq#2720's
    # worker left 96 of them throttling in its 4-core cgroup — root cpu PSI 96% while
    # the box sat 73% idle, and the dispatcher's cpu_pressure gate halted every pass.
    # mask_at_rest.py also takes a single-flight lock (MASK_AT_REST_LOCK), so a second
    # sweep launched while one is running exits 0 immediately instead of stacking.
    if os.environ.get("FOREMAN_NO_MASK_AT_REST") == "1":
        return
    try:
        args = ["nice", "-n", "10", sys.executable, MASK_AT_REST, "--store",
                "transcripts" if session_id else "all"]
        if session_id:
            args += ["--session", session_id]
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except Exception as e:  # noqa: BLE001
        log(f"WARN: mask_at_rest trigger failed: {e!r}")


def rotation_detail(ticket_id, attempt, tag, hits_path):
    """The rotation ticket's detail, read from disk PER ALARM (wq#2404).

    The third thing this process froze at spawn. masking.deployed() reloads the rule
    table and burn_filter.deployed() the screen, but the ticket TEXT was a literal in
    this file, and a literal in a long-lived process is the version it was spawned
    with: wq#2348 was filed at 23:01:44Z carrying the template 32aa000 had replaced at
    22:40:21Z — 21 minutes earlier — because the process writing it had imported this
    module at 22:14:43Z. The text is the only part of the alarm the answering worker
    actually reads, and a stale one sends them down the hand-grep path (wq#2332) that
    re-arms the alarm. A file is reloadable; this module is not.

    Failure is one-directional, like both siblings: an unreadable, unexpanded or
    gutted template never quiets the alarm, it only falls back to the frozen text
    below, which carries the two pointers a row cannot be settled without — the
    replay command and the forensics path (wq#2288 arrived without them and was
    unactionable).
    """
    values = {"TICKET_ID": str(ticket_id), "ATTEMPT": str(attempt), "TAG": tag,
              "MASK_REPLAY": MASK_REPLAY, "HITS_PATH": hits_path}
    try:
        with open(ROTATION_DETAIL_PATH) as f:
            text = f.read().strip()
        for key, value in values.items():
            text = text.replace("{{%s}}" % key, value)
        missing = [p for p in (f"{MASK_REPLAY} {ticket_id}", hits_path, tag)
                   if p not in text]
        if missing or "{{" in text:
            raise ValueError(f"missing {missing or 'an expansion'}")
        return text
    except Exception as e:  # noqa: BLE001
        log(f"WARN: rotation-ticket template unusable ({e!r}) — filing the frozen text")
        return (f"wq#2275 mask tripwire: pattern(s) {tag} fired on wq#{ticket_id} "
                f"attempt {attempt}'s stream-json. The artefacts are masked; the value "
                f"reached the model context, so per instinct 4 it is burned. FIRST run "
                f"`python3 {MASK_REPLAY} {ticket_id}` (wq#2307) — zero unaccounted "
                f"values means committed text, not a credential: settle the row on that "
                f"evidence and rotate nothing. The same evidence raw is in {hits_path} "
                f"(prefix, length, 12 hex of sha256, masked context; no value). Rotate "
                f"whatever is unaccounted today and close that leak path. "
                f"[{ROTATION_DETAIL_PATH} was unreadable — text frozen at spawn]")


def rotation_chain_refire(ticket_id, attempt, tag, hits_path):
    """wq#2299: True when this alarm fired inside an investigation of one of the
    tripwire's OWN rotation tickets — record it on that ticket instead of filing a
    child one.

    Working a rotation ticket means reading the artefacts, the fixtures and the
    transcript that fired it; on this stream that read IS the next hit. Filing a
    fresh pri-1 ticket for it makes the tripwire self-sustaining: wq#2278 -> wq#2290
    -> wq#2297/wq#2299 -> wq#2303/wq#2304, 34 rotation tickets with 22 still open
    inside the hour, every one of them a committed test fixture rather than a live
    credential (wq#2299's own value was an `sk-ant-oat01-<dummy>` fixture read out of
    tests/test_token_injection.py). A chain has no natural end, and its pri-1 rows
    are what buries the real burn when one comes.

    The evidence is not lost: mask-hits-N.json is written either way, the ticket
    already open on this credential gets a `wq note` pointing at it, and ntfy still
    pages. Only the new ROW is suppressed. An unreadable title alarms normally — a
    guard that cannot prove it is on a chain must never be the thing that silences
    a burn.

    wq#2308: the marker is matched anywhere in the title, not only at its start,
    because every rotation ticket spawns a `[verify] wq#N [rotation] burned
    credential in wq#M ...` row whose worker is sent to re-read the same run dir.
    That corpus still fires: of 1331 frozen `_runs/wq2[23]*/snapshots/*` artefacts,
    23 carry credential-shaped text at today's rules and 9 of those come back
    `burned` from burn_filter, because a literal that WAS a committed fixture when
    the snapshot froze (or a runtime id like `capability_proposal.grant_key()`'s
    `grant-dir-<hash>`) is in no tracked file today, so provenance cannot account
    for it. A prefix-only guard hands each of those 12 open verify rows a fresh
    pri-1 child, which is the chain wq#2299 closed reopening one archetype over.
    """
    try:
        title = (wq_show(ticket_id).get("title") or "")
    except Exception as e:  # noqa: BLE001
        log(f"WARN: chain-guard lookup failed for wq#{ticket_id} ({e!r}) — alarming")
        return False
    if ROTATION_TITLE_PREFIX not in title:
        return False
    try:
        wq("note", str(ticket_id),
           f"wq#2275 tripwire re-fired on attempt {attempt} ({tag}) while working "
           f"this rotation row; forensics appended to {hits_path}. No child rotation "
           f"ticket filed (wq#2299 chain guard) — reading the artefacts this ticket "
           f"points at is what fired it.")
        ntfy("foreman MASK HIT (rotation chain)",
             f"wq#{ticket_id} attempt {attempt}: {tag} re-fired while working a rotation "
             f"ticket — noted on wq#{ticket_id}, no new ticket; records in {hits_path}")
        log(f"MASK HIT wq#{ticket_id} attempt {attempt}: {tag} — rotation-chain re-fire, "
            f"noted on the ticket, no child ticket filed")
    except Exception as e:  # noqa: BLE001
        # wq#2629: mirrors the wq("add", ...) branch below — an alerting failure here
        # must not raise out of the read loop mask_alarm() runs inside (mask_alarm's
        # own docstring: "both best-effort so an alerting failure never takes down the
        # attempt itself"). The chain decision itself (no new row) still stands.
        log(f"WARN: rotation-chain note/page failed for wq#{ticket_id}: {e!r}")
    return True


def burned_hashes(labels, records):
    """sha256_12 of every distinct value recorded under a BURNED label (wq#2341).

    `records` is masking.scan()'s re-scan of the whole raw line, so it also carries the
    values of labels burn_filter screened as committed text. Keying the rotation row on
    those would let a fixture sharing a line with a real token decide which row the
    token dedupes into. Per label is the finest grain the screen actually decides at:
    burn_filter.classify() burns a LABEL when any one of its matches is unaccounted.

    Sorted and de-duplicated, because the key has to be the same for the same set of
    values however many times, and in whatever order, the line printed them.

    wq#2677: a record carrying `suppressed` is skipped outright. Suppressed rows share
    this file and this label — they ARE kv-secret candidates — but they are the audit
    trail of what the rule declined to alarm on, not a burn. Letting one into the key
    would title a rotation ticket after a value the rule deliberately did not mask
    (an `os.environ` pointer, a called identifier, a public key), i.e. re-open by the
    back door exactly the false-alarm class wq#2297/wq#2632 closed.
    """
    burned = set(labels)
    return sorted({r.get("value_sha256_12") for r in records or []
                   if not r.get("suppressed")
                   and r.get("label") in burned and r.get("value_sha256_12")})


def rotation_title(ticket_id, tag, hashes, cf_held=False):
    """The rotation ticket's title — which IS its dedupe key (wq#2341).

    Titled by the RUN, the same value printing in ten runs opened ten pri-1
    rotate-today rows, each drawing an xhigh worker at roughly $6; 28 were open at once
    on 2026-09-20. wq#2298 fixed the biggest source (the detector matching its own
    output) but not the fan-out. Titled by the VALUE, `wq add` collapses the re-fires
    itself: it refuses a title an undone ticket already carries.

    That refusal is also what makes the dedupe safe. The hazard in a value-keyed key is
    a credential that leaks AGAIN after it was rotated — it must page again, and a
    dedupe that remembers forever would swallow it. wq's check compares against
    `status != 'done'` (bin/wq cmd_add), so it only ever collapses into a row that is
    still OPEN, i.e. one whose credential has not been rotated yet. A closed row is no
    longer a match and the next leak of that value files a fresh page. Nothing here has
    to carry a rotation epoch; the queue's own state is the epoch.

    No hash falls back to the run-keyed title of every row filed before this: one row
    per run is noisy, and it beats one row standing in for values we cannot show are
    the same.

    wq#2843: `cf_held` appends cf_hold.TITLE_MARKER. The title carries the mask LABELS
    and nothing else, so a CF credential caught by variable name (`CF_API_TOKEN` under
    the generic `kv-secret` label) named no CF resource anywhere in it — and the
    dispatcher's second wall reads the title alone, so it could not hold the very row
    the tripwire had just recognised. The marker goes at the END, after the label
    parens: `ROTATION_TITLE_PREFIX` (the chain guard) and mask_triage.SOURCE_RE both
    match from the front and a suffix leaves them untouched. It is part of the dedupe
    key like everything else in the title, which is correct — the same value burning
    twice produces the same marker.
    """
    # Sorted and de-duplicated HERE too, not only in burned_hashes: a key that depends
    # on the order a line happened to print two values in is not a key at all.
    hashes = sorted(set(hashes or ()))
    suffix = f" {cf_hold.TITLE_MARKER}" if cf_held else ""
    if not hashes:
        return (f"[rotation] burned credential in wq#{ticket_id} transcript "
                f"({tag}){suffix}")
    if len(hashes) == 1:
        key = f"sha256:{hashes[0]}"
    else:
        # A set of values named by a hash OF their hashes: exact — a value added to or
        # dropped from the set is a different row, and a value that has never been
        # paged on must page — and bounded, where a title listing them grows with a
        # fixture file's worth of matches. The cost of keying the SET is that a later
        # alarm on a subset of it opens a second row: bounded by the distinct sets a
        # leak path produces rather than by the runs that read it, and both rows are
        # settled off the same hashes. One row per value instead would re-spend the
        # xhigh worker per value on the multi-value alarms, which is the cost this is
        # closing.
        digest = hashlib.sha256("\n".join(hashes).encode()).hexdigest()[:12]
        key = f"{len(hashes)} values set:{digest}"
    return f"[rotation] burned credential {key} ({tag}){suffix}"


# `DUPLICATE — task 2310 [fleet/open] already has this title:` (bin/wq cmd_add). The id
# is the row this value is ALREADY open on, which is where the run gets noted instead.
WQ_DUPLICATE_RE = re.compile(r"DUPLICATE\b[^\n]*?\btask (\d+)")

# wq#2782 (ISSUE-LOG 850 rule c): Frank's 2026-09-25 hold on ALL CF-resource rotation —
# the mcpw/Commander outage traced back to an unattended CF token rotation that deleted
# every Cloudflare Access app on the account. A rotation row this tripwire hands a
# fire-and-forget worker is exactly what that hold forbids, so any row naming a
# Cloudflare credential has to come in FRANK-GATED, not open. `cloudflare` is the one
# masking label built for the purpose (cfut_ user tokens, masking.py), but a CF API
# token/zone id/account id caught by variable name lands under the generic kv-secret
# label instead — that only shows up in the record's masked `context` window, never in
# the label itself — so both are checked.
#
# wq#2843: the shape itself now lives in cf_hold.py, because keying on the literal word
# `cloudflare` missed the abbreviated variable names the fleet actually uses
# (`CF_API_TOKEN=` in ops/wq2608-dc-endpoint-bes-a1/dns_access_setup.sh:46) — wq#2787's
# verify pass, ISSUE-LOG 869.


def is_cf_rotation(labels, records):
    """True when this alarm's rotation row names a Cloudflare-shaped credential (wq#2782)."""
    if cf_hold.names_cf_resource(*labels):
        return True
    return cf_hold.names_cf_resource(*[r.get("context") for r in records or ()])


def mask_alarm(ticket_id, attempt, labels, records=None, session_id=None):
    """wq#2275 (T6): a mask hit is an ALARM, not a save (instinct 4). The credential
    already reached the model's context — CLI 2.1.233 has no pre-model rewrite lever
    (research/SMARTCRUSHER_HOOK.md) — so the artefacts are clean but the token is
    burned and must be rotated same-day. Page ntfy AND file a rotation ticket; both
    best-effort so an alerting failure never takes down the attempt itself.

    wq#2341: one row per burned VALUE, not per run that printed it. The forensics are
    written before any of the three ways this can end in no new row (mask output, a
    chain re-fire, a value already open), so evidence never depends on the judgement.

    wq#2546 (BUILD d): also kicks a detached at-rest mask of the CC transcript this
    session is writing to — the disk copy this alarm just proved is unmasked, not
    later, and unconditionally (before the self-output/chain-refire/dedupe branches
    below, which only decide whether a NEW ROW is filed, never whether the file on
    disk gets cleaned up).
    """
    trigger_mask_at_rest(session_id)
    labels = sorted(labels)
    tag = ",".join(labels)
    hits_path = write_mask_hits(ticket_id, attempt, records)
    hashes = burned_hashes(labels, records)
    if hashes and set(hashes).issubset(masking.SELF_OUTPUT_SHA12):
        # wq#2339's triage recipe, run at the alarm instead of on the ticket it filed:
        # every value here is this masker's own output read back, which is the
        # synthetic-fixture shape that fired three rows during wq#2298's own run. The
        # skip is all-or-nothing — one value the list cannot explain and the alarm
        # stands, because a page held back is the failure this whole path exists to
        # avoid.
        log(f"mask hit wq#{ticket_id} attempt {attempt}: {tag} — every value is the "
            f"mask literal by hash ({','.join(hashes)}), not a credential; records in "
            f"{hits_path}, no page and no rotation ticket (wq#2339)")
        return
    settled = mask_replay.load_rotated_ledger()
    if hashes and all(h in settled for h in hashes):
        # wq#4736: the alarm never read the ledger, so a value settled once kept filing
        # fresh pri-1 rows — gitlab.com's public CSP sentry_key (09c067d6e975), settled
        # as wq#3189, came back as wq#4736 the next time a run curled gitlab.com. A
        # ledgered value is dead or was never ours (mask_at_rest skips it the same
        # way); all-or-nothing like the self-output skip above.
        log(f"mask hit wq#{ticket_id} attempt {attempt}: {tag} — every value already "
            f"settled in the rotated ledger ("
            f"{', '.join(h + ' by ' + str(settled[h].get('ticket')) for h in hashes)}); "
            f"records in {hits_path}, no page and no rotation ticket (wq#4736)")
        return
    if rotation_chain_refire(ticket_id, attempt, tag, hits_path):
        return
    ntfy("foreman MASK HIT",
         f"wq#{ticket_id} attempt {attempt}: {tag} credential(s) masked in child "
         f"stream — token reached model context and is BURNED; rotate today",
         priority="high")
    try:
        cf_held = is_cf_rotation(labels, records)
        add_args = ["add", "--lane", "fleet", "--pri", "1",
               # The MARKER in the title stays a literal here on purpose: the chain
               # guard above matches ROTATION_TITLE_PREFIX in it, so a deployed file
               # must not be able to rename a row out of its own chain.
               "--title", rotation_title(ticket_id, tag, hashes, cf_held=cf_held),
               "--detail", rotation_detail(ticket_id, attempt, tag, hits_path),
               "--no-probe", "rotation is a manual credential action; "
                             "no deterministic liveness probe exists"]
        if cf_held:
            # wq#2782: ISSUE-LOG 850 rule c holds ALL CF-resource rotation — file this
            # row frank_gated so the dispatcher's TicketLoad gate (status != "open")
            # refuses it outright, on top of the dispatcher's own CF-rotation title
            # check (foreman_dispatcher.frank_gate_reason) for any row this misses.
            # wq#2843: that title check is why the title carries the marker too — the
            # labels alone name no CF resource when the value was caught by variable
            # name, so without it the second wall reads a row it cannot recognise.
            add_args.append("--frank-gated")
        p = wq(*add_args)
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        dup = WQ_DUPLICATE_RE.search(out)
        if p.returncode == 0:
            log(f"MASK HIT wq#{ticket_id} attempt {attempt}: {tag} — rotation ticket filed")
        elif dup:
            wq("note", dup.group(1),
               f"wq#2275 tripwire: the same value(s) also fired on wq#{ticket_id} "
               f"attempt {attempt} ({tag}); forensics appended to {hits_path}. No "
               f"second rotation ticket filed (wq#2341 value dedupe) — this row is "
               f"still open, so the credential is still unrotated. Rotate once, here.")
            log(f"MASK HIT wq#{ticket_id} attempt {attempt}: {tag} — same value(s) "
                f"already open as wq#{dup.group(1)}, run noted there (wq#2341)")
        else:
            log(f"WARN: rotation ticket add failed for wq#{ticket_id}: {out[:200]}")
    except Exception as e:  # noqa: BLE001
        log(f"WARN: rotation ticket add crashed for wq#{ticket_id}: {e!r}")


def granted_dirs(cmd):
    """The --add-dir paths in a built child command line (wq#2319).

    Read back off the argv rather than threaded down as another parameter: cmd is
    already what run_child()/run_child_gateway() were handed, and it is the exact
    grant the CLI enforces, so the provenance screen and the child cannot disagree
    about which directories were readable.
    """
    return [cmd[i + 1] for i, a in enumerate(cmd)
            if a == "--add-dir" and i + 1 < len(cmd)]


def drop_stale_labels(burned, raw, ticket_id, attempt):
    """wq#2327: labels of `burned` that the rule table ON DISK still reads in `raw`.

    This process scored the line with the table it imported when the attempt
    started. A rule narrowed since then — the storm's fixes landed at 22:54, 22:57,
    23:09 and 23:31 on one evening — does not reach it, so a long attempt keeps
    filing rotate-today tickets under a rule the fleet already retired. Re-scoring
    against `masking.deployed()` is what makes a hot fix take effect on the runs in
    flight, which is the only moment a mask fix is actually worth anything.

    Drop only, never add. A label the deployed table finds and this one did not is a
    value that was never masked out of the artefacts, and it has not been through
    burn_filter's provenance screen either — paging on it would re-open the false
    alarms this closes. It also keeps the failure direction right: `deployed()`
    falls back to the imported module, so the worst case is today's behaviour.
    """
    live = masking.deployed()
    if live is masking:
        return burned
    try:
        labels = set(live.mask_text(raw)[1])
    except Exception as e:  # noqa: BLE001
        log(f"WARN: deployed-rule recheck failed ({e!r}) — alarming on all hits")
        return burned
    stale = sorted(h for h in burned if h not in labels)
    if stale:
        log(f"mask hit wq#{ticket_id} attempt {attempt}: {','.join(stale)} no longer "
            f"matches the deployed rule table ({live.__file__}) — masked, no rotation")
    return [h for h in burned if h in labels]


_MASK_STATE = {}


def mask_state(ticket_id):
    """The tripwire's dedup bookkeeping for a TICKET (wq#2404).

    It used to be two sets local to run_child(), so it reset on every attempt — and
    an fworker process does not: it holds the whole ticket, spawning attempt 2 into
    the same interpreter that scored attempt 1. A retry therefore re-read the same
    committed source and re-filed it. wq#2343 (23:00:04Z, attempt 1) and wq#2348
    (23:01:44Z, attempt 2) are that pair: one run, 110 seconds apart, sharing the
    `cloudflare` label, two pri-1 rotate-today rows for the same fixtures.

    Keyed per ticket rather than process-global so the sets stay small and say what
    they are; held in the IMPORTED module on purpose, since a hot-reloaded copy of
    the alarm path would bring its own empty ones and dedup nothing.
    """
    return _MASK_STATE.setdefault(str(ticket_id),
                                  {"alarmed": set(), "seen": set(),
                                   # wq#2677: (reason, sha12) already written to
                                   # mask-hits-N.json, so a pointer the stream repeats
                                   # on every line is one row, not one per line.
                                   "suppressed": set()})


def burned_labels(screen, fresh, raw, seen, live_secrets, repo_roots):
    """(burned, benign, values) — how one provenance `screen` reads this line.

    Factored out of screen_mask_hits so the imported screen and the DEPLOYED one
    (drop_screened_out) are asked the same question the same way, rather than the
    recheck re-deriving a near-copy that could disagree for reasons other than the
    deploy.

    `seen` is read, never written: both passes must judge the same set of unscreened
    values, so the caller owns that bookkeeping and updates it once, after both.
    """
    values = screen.matched_values(raw, fresh)
    pending, screened = {}, set(seen)
    for label, found in values.items():
        unscreened = [v for v in found if v not in screened]
        if unscreened:
            pending[label] = unscreened
        screened.update(found)
    burned, benign = screen.classify(pending, live_secrets, repo_roots)
    # A label mask_text() reported but whose value cannot be recovered from the
    # raw line is unexplainable, so it pages — the same default as an unknown
    # value. In practice this is a label that fired only on an earlier pattern's
    # `MASKED` remnant, whose underlying value the earlier label already covers.
    burned += [h for h in fresh if h not in values and h not in burned]
    return burned, benign, values


def drop_screened_out(burned, fresh, raw, seen, live_secrets, repo_roots,
                      ticket_id, attempt):
    """wq#2404: labels of `burned` that the screen ON DISK also calls burned.

    The sibling of drop_stale_labels, for the provenance screen instead of the rule
    table. wq#2327 taught the table to reload; the screen kept freezing at spawn, so
    281327c (23:09:47Z, screen against every granted root) and be9994d (23:44:19Z)
    reached no run then in flight — and wq#2348, filed 23:01:44Z by a process that
    imported burn_filter at 22:14:43Z, is ten committed-text values filed as a pri-1
    rotate-today row.

    Drop only, by intersection: a deployed screen can quiet a label this process
    called burned, never raise one it called benign. A raise would page on a value
    that has not been through this process's own screen, which is how the false-alarm
    chain this closes started.

    Two git greps per unaccounted value while a deploy is newer than this process —
    paid only on a line that has already alarmed, never on the hot path. The passes
    deliberately do not share classify()'s cache: is_committed_literal is exactly the
    kind of thing a screen fix changes, and a shared cache would answer with the
    version this process was spawned with, which is the bug.
    """
    screen = burn_filter.deployed()
    if screen is burn_filter:
        return burned
    try:
        live = burned_labels(screen, fresh, raw, seen, live_secrets, repo_roots)[0]
    except Exception as e:  # noqa: BLE001
        log(f"WARN: deployed-screen recheck failed ({e!r}) — alarming on all hits")
        return burned
    dropped = sorted(h for h in burned if h not in live)
    if dropped:
        log(f"mask hit wq#{ticket_id} attempt {attempt}: {','.join(dropped)} is "
            f"accounted for by the deployed screen ({screen.__file__}) — masked, "
            f"no rotation")
    return [h for h in burned if h in live]


def screen_mask_hits(hits, raw, alarmed, seen, live_secrets, repo_roots,
                     ticket_id, attempt, session_id=None):
    """wq#2290: alarm on a mask hit only when the matched text is actually a live
    credential (burn_filter), not a credential-shaped string in the source of any
    repo the child was granted (`repo_roots`, wq#2319 — the worktree alone left the
    doctrine log unaccounted, so it paged on its own rows).

    Masking already happened — this gates only the ntfy page and the rotation
    ticket. Sits where wq#2288's re-scan does, off the hot path: nothing here runs
    until mask_text() has already reported a label.

    Bookkeeping is per matched STRING, not per label: keying only on the label meant
    the first hit of a label consumed that label's one alarm for the attempt, so a
    benign fixture read at turn 2 would silently swallow a real burn at turn 40.
    Labels still alarm at most once — `alarmed` — but only once BURNED. Both sets
    belong to the TICKET, not the attempt (wq#2404, mask_state): the caller holds
    them across a retry, because the process does.

    Two rechecks follow the screen, both drop-only, both against the code ON DISK
    rather than the code this process imported: drop_screened_out for the screen
    itself and drop_stale_labels for the rule table. Order does not matter — they
    intersect — but the screen runs first so its log line names a label as benign
    before the table's names it as retired.
    """
    fresh = [h for h in hits if h not in alarmed]
    if not fresh:
        return
    try:
        burned, benign, values = burned_labels(burn_filter, fresh, raw, seen,
                                               live_secrets, repo_roots)
    except Exception as e:  # noqa: BLE001
        # Never let provenance screening swallow an alarm: on any failure fall back
        # to the wq#2275 behaviour of treating every hit as burned.
        log(f"WARN: burn_filter failed ({e!r}) — alarming on all hits")
        burned, benign, values = fresh, [], {}
    if benign:
        log(f"mask hit wq#{ticket_id} attempt {attempt}: {','.join(sorted(benign))} "
            f"matched committed source text, not a live credential — masked, no rotation")
    if burned:
        burned = drop_screened_out(burned, fresh, raw, seen, live_secrets,
                                   repo_roots, ticket_id, attempt)
        burned = drop_stale_labels(burned, raw, ticket_id, attempt)
    # after both passes: they must judge the same set of unscreened values (wq#2290).
    for found in values.values():
        seen.update(found)
    if burned:
        alarmed.update(burned)
        # wq#2288: re-scan the raw line for the redacted record — only on an alarm.
        mask_alarm(ticket_id, attempt, burned, masking.scan(raw)[1],
                  session_id=session_id)


# ---------------------------------------------------------------------------
# wq#2393: the line is not the context
#
# The tripwire reads the child's PIPE, and the pipe carries three kinds of text:
# frames the model produced, frames the model was handed, and everything else. The
# third kind is not small. run_child merges the child's stderr into stdout (see the
# Popen below), so every hook message, every warning the CLI writes and every byte a
# hook's own subprocess leaks arrives on the same file descriptor as the stream; and
# a real frame is an envelope whose siblings the CLI fills with its own bookkeeping,
# none of which the model sees.
#
# wq#2253 is the case that named it. Its alarm's three values are on ONE line of the
# doctrine log — line 657 of the pre-image the gate froze at 22:51:23 — and that line
# is outside every window the model got: the run's one read of that file returned
# lines 658 onward, the edit it then made patched from 659, and the whole 1.5MB
# session transcript, which stores each frame's message verbatim, matches none of the
# three. A value in no frame was in no context, so nothing was burned and the pri-1
# rotate-today ticket was noise — but telling that apart cost a worker an hour of
# replay, because the screen had no way to say where a hit came from.
#
# So: mask every line, as before (an artefact must never carry a credential, whatever
# channel it arrived on), and page only on the part of a line the model actually
# received. A hit anywhere else is logged, not filed.
# ---------------------------------------------------------------------------

# The two frame types that ARE context. `system`/`init` (cwd, tool list, mcp servers,
# key source), `result` and the gateway's own response envelope are bookkeeping.
MODEL_FACING_FRAMES = ("assistant", "user")


def _content_text(content):
    """Every string inside one message `content`, in order.

    The blocks that reach the model whole: assistant text and thinking, the tool_use
    input it emitted, the tool_result content handed back to it. A shape this does not
    know is serialised rather than skipped — an unrecognised block is still in the
    context window, and scoring it empty is the one error here that loses an alarm.
    """
    if content is None:
        return []
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        out = []
        for item in content:
            out.extend(_content_text(item))
        return out
    if isinstance(content, dict):
        kind = content.get("type")
        if kind == "text":
            return [content.get("text") or ""]
        if kind == "thinking":
            return [content.get("thinking") or ""]
        if kind == "tool_use":
            return _content_text(content.get("input"))
        if kind == "tool_result":
            return _content_text(content.get("content"))
    return [json.dumps(content, default=str)]


def _frame_content(frame):
    """The context-bearing text of one canonical frame, or [] if it carries none."""
    if not isinstance(frame, dict):
        return []
    if frame.get("type") == "result":
        # A duplicate of the last assistant text rather than a fourth channel, and it
        # would alarm on that frame first. Counted anyway: the cost of counting a copy
        # is a second look at an alarm that was going to fire, and the cost of missing
        # the one stream that ever emits only this is a credential nobody rotates.
        result = frame.get("result")
        return [result] if isinstance(result, str) else []
    if frame.get("type") not in MODEL_FACING_FRAMES:
        return []
    message = frame.get("message")
    return _content_text(message.get("content")) if isinstance(message, dict) else []


def model_facing_text(raw):
    """The part of one child stream line that entered the model's context.

    "" means the line carried no context at all — it did not parse (stderr), or it
    parsed into an envelope the model never receives. It does NOT mean the line was
    empty or that masking should skip it; only that a hit on it is not a burn.
    """
    try:
        frame = json.loads(raw)
    except (TypeError, ValueError):
        return ""
    return "\n".join(_frame_content(frame))


def gateway_facing_text(raw):
    """model_facing_text for one gateway SSE data payload.

    The UHP split is cleaner than the CLI's: `response.output` IS the turn — items the
    model emitted and the tool outputs it was handed — while everything beside it is
    the envelope despag computes (`id`, `usage`, and the `lumen.run_state` sidecar
    _observe_gateway_event adopts wholesale). So the whole output list counts, item
    metadata and all. Scoring it through _frames_from_lumen_output instead would score
    only the two item types that mapper needs and silently lose the model's own text.

    Streaming deltas carry the same text one fragment at a time and no output list of
    their own, so they are taken as-is.
    """
    try:
        ev = json.loads(raw)
    except (TypeError, ValueError):
        return ""
    if not isinstance(ev, dict):
        return ""
    parts = [json.dumps((ev.get("response") or {}).get("output") or [], default=str)]
    parts.extend(v for k, v in ev.items() if k in ("delta", "text") and isinstance(v, str))
    return "\n".join(parts)


def screen_stream_hits(hits, raw, alarmed, seen, live_secrets, repo_roots,
                       ticket_id, attempt, facing=model_facing_text, session_id=None):
    """screen_mask_hits, scoped to the context (wq#2393). The only caller of the
    screen from either read loop.

    Labels are re-derived from the context rather than filtered off the raw line's
    set: the same label can be armed by the envelope and by the message on one line,
    and only the second is a burn. Everything the context does not explain is logged
    under its own sentence — a value that never reached the model is a defect in the
    harness's own plumbing, worth reading, but it is not a rotation. `alarmed` is
    consulted here as well as in screen_mask_hits, so a label that already paged does
    not then narrate every envelope it turns up in for the rest of the attempt.

    wq#2677: the suppression trail is written HERE, above the early return, because it
    is the one thing that has to happen on a line with no hits — that is the case the
    trail exists for.
    """
    note_suppressions(ticket_id, attempt, raw)
    fresh = [h for h in hits if h not in alarmed]
    if not fresh:
        return
    context = facing(raw)
    kept = set(masking.mask_text(context)[1]) if context else set()
    outside = sorted({h for h in fresh if h not in kept})
    if outside:
        log(f"mask hit wq#{ticket_id} attempt {attempt}: {','.join(outside)} matched "
            f"outside the model's context (merged stderr or frame envelope) — masked "
            f"in every artefact, no page and no rotation ticket (wq#2393)")
    inside = [h for h in fresh if h in kept]
    if inside:
        screen_mask_hits(inside, context, alarmed, seen, live_secrets, repo_roots,
                         ticket_id, attempt, session_id=session_id)


# ---------------------------------------------------------------------------
# run-state persistence (design §8: attempt-N.json, prior-attempt-notes.md)
# ---------------------------------------------------------------------------

def run_dir(ticket_id):
    d = os.path.join(RUNS_ROOT, f"wq{ticket_id}")
    # wq#2911: transcripts and replay logs under here carry whatever a worker printed.
    # 0711, not 0700: uid tokeneng re-reads the 0444 grants by path (0700 denied every
    # ticket-end as grant:grant_unreadable), so it must traverse but never list; the
    # files themselves are 0600 via acceptance.private_open. chmod too, since exist_ok
    # leaves a pre-existing dir at its old mode.
    os.makedirs(d, mode=0o711, exist_ok=True)
    os.chmod(d, 0o711)
    return d


def next_attempt_n(ticket_id):
    # wq#1499: attempt-N.uncharged.json (dispatcher/uncharge_attempt.py) still owns its
    # number — usage-N.json, evidence/attempt-N and the transcript for that N are on disk
    # as the audit trail. Counting only charged records would hand the next run a number
    # that is already taken and overwrite those sidecars.
    d = run_dir(ticket_id)
    nums = [int(m.group(1)) for m in
            (re.match(r"attempt-(\d+)(?:\.uncharged)?\.json$", f) for f in os.listdir(d)) if m]
    return (max(nums) + 1) if nums else 1


def write_attempt_state(ticket_id, n, state):
    d = run_dir(ticket_id)
    path = os.path.join(d, f"attempt-{n}.json")
    tmp = path + ".tmp"
    with open(acceptance_mod.private_open(tmp), "w") as f:
        json.dump(state, f, indent=2, default=str)
    os.rename(tmp, path)
    _settle_spawn_reservation(ticket_id, n)
    return path


def _settle_spawn_reservation(ticket_id, n):
    """wq#1893: this attempt-N.json just landed, so harness/spawner.py's
    dispatch-spawn:<ticket>:<n> reservation (PHASE0_WIRING_SPEC E.5, wq#1815) has done
    its job of blocking a duplicate Popen — clear it now instead of leaving the row
    'reserved' until SPAWN_RESERVE_TTL_S. Best-effort and isolated: bookkeeping for a
    different subsystem's table must never fail an attempt that already landed."""
    try:
        import spawner  # same dir as this file — already on sys.path (see top of module)
        spawner.settle_spawn(ticket_id, n)
    except Exception as e:  # noqa: BLE001
        log(f"WARN: spawn reservation settle skipped for wq#{ticket_id} attempt {n}: {e!r}")


def fire_fenced_attempt_count(ticket_id):
    """wq#1261 (#13 item 3): how many of this ticket's recorded fire-response attempts
    ended in an outcome that counts against the attempt fence (FIRE_FENCE_OUTCOMES).
    Reads the same attempt-N.json files write_attempt_state() writes for every
    archetype — dispatcher/self_heal.py's attempt_count() reads the identical directory
    for its own (unscoped) count, so this is "the same counter as 1d", filtered to
    fire-response's failing outcomes only."""
    d = run_dir(ticket_id)
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    n = 0
    for name in names:
        if not re.match(r"attempt-\d+\.json$", name):
            continue
        try:
            with open(os.path.join(d, name)) as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        if rec.get("archetype") == FIRE_ARCHETYPE and rec.get("fire_outcome") in FIRE_FENCE_OUTCOMES:
            n += 1
    return n


def read_rate_limits_snapshot():
    """wq#736 / SESSION_ECONOMICS.md §7: a session start/exit reading of the two plan-
    usage windows, sourced from the governor's own poll (state.json) rather than a new
    API call — zero new Anthropic spend, and it sidesteps the guard-denied
    ~/.claude/projects transcript path that kept the fworker dispatch path out of every
    token audit so far. Field names follow the CLI's statusline vocabulary
    (`rate_limits.five_hour/seven_day.used_percentage`), not governor_core's internal
    session/weekly_all/percent naming — this is the translation layer. Best-effort:
    a missing/stale/corrupt state.json (governor not running yet, race at boot) yields
    an all-None snapshot rather than raising, since a data-collection sidecar must never
    be able to fail the attempt it's riding along with.
    """
    snapshot = {
        "captured_at": time.time(),
        "five_hour": {"used_percentage": None, "resets_at": None},
        "seven_day": {"used_percentage": None, "resets_at": None},
        "source": None,
        "stale": None,
    }
    try:
        with open(STATE_FILE) as f:
            gov_state = json.load(f)
    except (OSError, json.JSONDecodeError):
        return snapshot
    limits = gov_state.get("limits") or {}
    session = limits.get("session") or {}
    weekly_all = limits.get("weekly_all") or {}
    snapshot["five_hour"] = {"used_percentage": session.get("percent"),
                              "resets_at": session.get("resets_at")}
    snapshot["seven_day"] = {"used_percentage": weekly_all.get("percent"),
                              "resets_at": weekly_all.get("resets_at")}
    snapshot["source"] = gov_state.get("source")
    snapshot["stale"] = gov_state.get("stale")
    return snapshot


def write_usage_sidecar(ticket_id, n, record):
    """wq#704/wq#706/wq#736: per-attempt usage sidecar into RUNS_ROOT/wq{id}/, written
    unconditionally after run_child() returns — that call site sees clean exit and every
    watchdog-kill path (inactivity/wall_clock/turns_exceeded/post_result_stuck) alike, so
    this covers both without a separate kill-path hook."""
    d = run_dir(ticket_id)
    path = os.path.join(d, f"usage-{n}.json")
    tmp = path + ".tmp"
    with open(acceptance_mod.private_open(tmp), "w") as f:
        json.dump(record, f, indent=2, default=str)
    os.rename(tmp, path)
    return path


def worktree_diff_is_empty(worktree):
    """wq#1499: True/False evidence for classify()'s thin/no-diff LAUNCH_FAILURE
    heuristic; None (never guessed) if the check itself can't run — an unknown diff
    state must not be treated as "empty"."""
    try:
        p = subprocess.run(["git", "-C", worktree, "diff", "--quiet"],
                            capture_output=True, timeout=15)
    except Exception:
        return None
    if p.returncode not in (0, 1):
        return None
    return p.returncode == 0


def _last_tool_error_tail(transcript_events, n=1, each_chars=1500):
    """Last n tool_result blocks flagged is_error — the sharpest "what broke"
    signal in the transcript, alongside assistant_text_tail's narrative tail.
    Mirrors assistant_text_tail's shape/contract."""
    texts = []
    for ev in transcript_events:
        if ev.get("type") != "user":
            continue
        for block in (ev.get("message") or {}).get("content") or []:
            if not (isinstance(block, dict) and block.get("type") == "tool_result"
                    and block.get("is_error")):
                continue
            text = "\n".join(_content_text(block.get("content"))).strip()
            if text:
                texts.append(text[:each_chars])
    return texts[-n:]


def _critique_prior_attempt(classification, mechanical_tail, assistant_tail, tool_error_tail):
    """wq#2488 (Orchestation 1 L858-913 Reflexion loop, minus its rewrite_node —
    that's the worker's job, this only improves its briefing): one cheap-tier
    LiteLLM call that turns attempt 1's raw tail into a WHAT FAILED / WHY / DO
    DIFFERENTLY briefing for attempt 2, instead of leaving attempt 2 to infer a
    root cause from 20 bare event names. FAIL-OPEN by contract (ticket step 4):
    returns None on any error or past CRITIQUE_TIMEOUT_S — a critique failure
    must never cost the retry, so the caller keeps writing the mechanical notes
    it always has regardless of what happens here.
    """
    try:
        key = read_litellm_key()
    except OSError:
        return None
    system = (
        "You write a terse failure briefing so a second attempt at a coding task "
        "does not repeat the first attempt's mistake. Reply with AT MOST 15 lines "
        "total, using exactly these three headings (each followed by 1-4 short "
        "bullet lines) and nothing else - no preamble, no code fences:\n"
        "WHAT FAILED\nWHY\nDO DIFFERENTLY"
    )
    user_parts = [f"classification: {classification}", ""]
    if mechanical_tail:
        user_parts += ["## last events", mechanical_tail, ""]
    if assistant_tail:
        user_parts += ["## final assistant text", "\n---\n".join(assistant_tail), ""]
    if tool_error_tail:
        user_parts += ["## last tool error", "\n---\n".join(tool_error_tail), ""]
    body = json.dumps({
        "model": CRITIQUE_MODEL,
        "max_tokens": 500,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n".join(user_parts)},
        ],
    }).encode()
    req = urllib.request.Request(
        f"{LITELLM_BASE_URL}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=CRITIQUE_TIMEOUT_S) as resp:
            data = json.loads(resp.read())
        text = (data["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return None
    return text or None


def write_prior_attempt_notes(ticket_id, n, worktree, transcript_events, classification):
    d = run_dir(ticket_id)
    path = os.path.join(d, "prior-attempt-notes.md")
    tail = transcript_events[-20:]
    diff_stat = ""
    try:
        p = subprocess.run(["git", "-C", worktree, "diff", "--stat"],
                            capture_output=True, text=True, timeout=15)
        diff_stat = p.stdout
    except Exception:
        pass
    mechanical_lines = []
    for ev in tail:
        etype = ev.get("type", "?")
        sub = ev.get("subtype", "")
        mechanical_lines.append(f"- {etype} {sub}".rstrip())
    lines = [
        f"# Prior attempt {n} notes (wq#{ticket_id})",
        f"classification: {classification}",
        "",
        "## last events (mechanical tail, up to 20)",
    ]
    lines += mechanical_lines
    lines += ["", "## git diff --stat (prior worktree)", "```", diff_stat.strip(), "```"]

    # wq#2488: critique only the attempt that is actually about to be retried
    # (MAX_ATTEMPTS=2, so that's n==1) - a doomed final failure gets no retry to
    # brief and would just spend an LLM call on notes nobody reads.
    if n == 1:
        critique = None
        try:
            critique = _critique_prior_attempt(
                classification,
                "\n".join(mechanical_lines),
                assistant_text_tail(transcript_events, n=1),
                _last_tool_error_tail(transcript_events, n=1),
            )
        except Exception:
            critique = None
        if critique:
            lines += ["", "## critique", critique]

    with open(acceptance_mod.private_open(path), "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


# ---------------------------------------------------------------------------
# worktree
# ---------------------------------------------------------------------------

PROMPT_FORGE_DIR = os.path.normpath(os.path.join(HARNESS_DIR, "..", "prompt-forge"))


def _memory_import(name):
    """Import a memory/ module (episodic) fail-open: fleet memory is
    strictly additive (wq#1183) — a missing or broken memory tree must never
    fail an attempt or a prompt build."""
    if MEMORY_DIR not in sys.path:
        sys.path.insert(0, MEMORY_DIR)
    return __import__(name)


def _prompt_forge_import(name):
    """Import a prompt-forge/ module fail-open, same contract as
    _memory_import (wq#1636)."""
    if PROMPT_FORGE_DIR not in sys.path:
        sys.path.insert(0, PROMPT_FORGE_DIR)
    return __import__(name)


def append_episode_event(ticket_id, event):
    """wq#1183 episodic memory: append-only per-run log.
    Best-effort by contract (memory/README.md failure posture)."""
    try:
        _memory_import("episodic").append_event(ticket_id, event,
                                                 runs_root=RUNS_ROOT)
    except Exception as e:
        log(f"episode append failed (non-fatal): {e!r}")


def commit_episode_to_archive(ticket_id):
    """wq#1635: at attempt end the episode reaches a store sessions actually
    read — a deterministic digest committed to archival_notes via minerva's
    lumen_episode.py (memoryd first, trust from the $WQ_AGENT lane claim).
    episode.jsonl stays behind as raw evidence with a 30d sweep. Supersedes
    the never-run memory/distill.py pipeline (now memory/attic/). Best-effort.

    wq#2442: the same digest also lands as an Obsidian note (memory/
    obsidian_bridge.py) — Claude Projects turned out to be a folder nothing
    read back; the Obsidian vault is the layer meant to actually persist
    across chat sessions, so every ticket that reaches the archive writes
    there too. Independently best-effort: a dead vault mount must not take
    down the archival_notes commit or vice versa."""
    try:
        episodic = _memory_import("episodic")
        digest = episodic.commit_to_archive(ticket_id, runs_root=RUNS_ROOT)
        episodic.sweep_episodes(runs_root=RUNS_ROOT)
    except Exception as e:
        log(f"episode archive commit failed (non-fatal): {e!r}")
        return
    if not digest:
        return
    try:
        _memory_import("obsidian_bridge").write_note(ticket_id, digest)
    except Exception as e:
        log(f"obsidian note write failed (non-fatal): {e!r}")


def assistant_text_tail(transcript_events, n=6, each_chars=1500):
    """Last n assistant text blocks from the stream — the episode's narrative
    payload, mirroring what write_prior_attempt_notes keeps mechanically."""
    texts = []
    for ev in transcript_events:
        if ev.get("type") != "assistant":
            continue
        for block in (ev.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                t = (block.get("text") or "").strip()
                if t:
                    texts.append(t[:each_chars])
    return texts[-n:]


def distilled_memory_block(ticket_id, ticket):
    """(wq#1636) subject-scoped live state for every subject the ticket's
    minerva notes mention — the replacement for a worker re-running
    `claude_context.sh --core` for the same facts. Fail-open: prompt assembly
    never depends on minerva being reachable. (The wq#1183 distilled-fact
    recall source was retired by wq#1635 — the fact store was never built, so
    it only ever contributed an empty block.)"""
    blocks = []
    try:
        block = _prompt_forge_import("fleet_context").ticket_state_block(ticket_id)
        if block:
            blocks.append(block.strip())
    except Exception:
        pass
    return "\n\n".join(blocks)


# The branch is `<type>/wq<id>-<slug>-a<attempt>`; the slug is judged inside that frame
# so a word the neighbours complete into a credential is caught too.
SLUG_FRAME = ("wq0-", "-a99")


def slugify(text, maxlen=40):
    """Branch slug of a ticket title, minus any word that makes it read as a credential.

    wq#2701: wq#2687's title `mask-secrets (bin, sed mirror): cfut_/kv-secret ...`
    slugged to `...-mirror-cfut-...`, which today's cloudflare rule reads as a token.
    The branch name prints in every git status and run record, so the tripwire paged
    it as a burned credential on each attempt. A title about a credential shape is
    exactly the ticket most likely to carry one, so the slug drops such words instead.
    """
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:maxlen]
    kept = []
    for word in s.split("-"):
        if not masking.mask_text("-".join(kept + [word]).join(SLUG_FRAME))[1]:
            kept.append(word)
    return "-".join(kept) or "ticket"


def _git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)


SLOCK_BIN = os.environ.get("FOREMAN_SLOCK", "/home/lumen/bin/slock")
GITLOCK_TTL_MIN = int(os.environ.get("FOREMAN_GITLOCK_TTL_MIN", "5"))  # slock --ttl is MINUTES; setup is ~seconds, this is only the crash-safety bound
GITLOCK_TRIES = int(os.environ.get("FOREMAN_GITLOCK_TRIES", "20"))
GITLOCK_SLEEP_S = float(os.environ.get("FOREMAN_GITLOCK_SLEEP_S", "2"))


def _gitlock_scope(repo):
    """The repo's git-lock scope, IDENTICAL to lumen-pr's gitlock_scope() (bin/lumen-pr)
    so worktree setup serializes against `lumen-pr land` on the same repo. Derived from
    the repo REMOTE slug (not the local path) so it matches regardless of checkout
    location: lumen-plays-pokemon -> lpp-git, else <slug>-git."""
    p = _git(repo, "remote", "get-url", "origin")
    url = p.stdout.strip() if p.returncode == 0 else ""
    name = url.rsplit("/", 1)[-1] if url else os.path.basename(os.path.normpath(repo))
    name = re.sub(r"\.git$", "", name)
    return "lpp-git" if name == "lumen-plays-pokemon" else f"{name}-git"


def _gitlock_acquire(scope, holder, note):
    """slock is non-blocking (fails if held). Retry a bounded number of times — the
    common contender is a transient land — and return False if it stays held so the
    caller can defer rather than race the primary .git."""
    for _ in range(max(1, GITLOCK_TRIES)):
        r = subprocess.run(
            [SLOCK_BIN, "acquire", scope, "--ttl", str(GITLOCK_TTL_MIN),
             "--holder", holder, "--note", note],
            capture_output=True, text=True)
        if r.returncode == 0:
            return True
        time.sleep(GITLOCK_SLEEP_S)
    return False


def _slock_release(scope, holder):
    subprocess.run([SLOCK_BIN, "release", scope, "--holder", holder],
                   capture_output=True, text=True)


class SpawnRefused(RuntimeError):
    """wq#1331 (WORKER_EXEC_CAPABILITY_GAP.md §3.2): the spawn preflight refused to
    start `claude -p`. This is a harness/routing defect, not evidence about the model
    or the ticket — cmd_run's handler writes failure.json but NO attempt-N.json
    (next_attempt_n stays put, so the attempt is not charged), releases the claim so
    the ticket stays open, appends a notes line, and pages ntfy."""

    def __init__(self, code, summary, worktree=None, unreachable_paths=None,
                 refused=None):
        super().__init__(f"{code}: {summary}")
        self.code = code
        self.summary = summary
        self.worktree = worktree
        # wq#1436 (BROKER_WIRING.md §2.5): the refusal carries its own structured
        # lists so failure_record fills unreachable_paths/refused from the refusal
        # rather than re-parsing the summary string.
        self.unreachable_paths = list(unreachable_paths or [])
        self.refused = list(refused or [])


def _git_common_dir_realpath(path):
    p = _git(path, "rev-parse", "--git-common-dir")
    if p.returncode != 0:
        return None
    out = p.stdout.strip()
    if not os.path.isabs(out):
        out = os.path.join(path, out)
    return os.path.realpath(out)


def assert_worktree_repo(worktree, repo, ticket_id):
    """wq#1331/§3.2 (ties to wq#1288): the worktree handed to the child must actually
    belong to the repo the router resolved — normalized origin URLs must match AND the
    two `git rev-parse --git-common-dir` realpaths must be the same .git. A mismatch is
    the wq#433/wq#1152 dispatch-into-the-wrong-tree defect, refused before a single
    token is spent. FOREMAN_NEEDS_DISABLE=1 downgrades to log-only (rollback)."""
    problems = []
    wt_check = failure_record.worktree_origin_check(worktree, repo)
    if wt_check.get("match") is False:
        problems.append(f"origin got={wt_check.get('origin')}")
    wt_common = _git_common_dir_realpath(worktree)
    repo_common = _git_common_dir_realpath(repo)
    if not wt_common or not repo_common or wt_common != repo_common:
        problems.append(f"git-common-dir {wt_common} != {repo_common}")
    if not problems:
        return
    summary = f"WORKTREE_REPO_MISMATCH expected={repo} ({'; '.join(problems)})"
    if needs_mod.disabled():
        log(f"wq#{ticket_id}: {summary} — FOREMAN_NEEDS_DISABLE=1, log-only")
        return
    raise SpawnRefused("WORKTREE_REPO_MISMATCH", summary, worktree=worktree)


# wq#1436 (BROKER_WIRING.md §1): the path/node/policy checks themselves live in
# capability.py, which the dispatcher's pre-skip (slice C) calls too. fworker keeps
# its call so a hand-spawned `lumen-run fworker` still refuses; what it does NOT keep
# is a private copy of the logic — these are the module's own functions.
cited_paths = capability.cited_paths
_under = capability._under


def assert_spawn_capability(ticket_id, ticket, worktree, repo, add_dirs, ticket_needs):
    """§3.2 last clause, widened by BROKER_WIRING.md §1 to the whole capability
    question: can this worker reach what the ticket cites (wq#1152 signature),
    address the nodes its needs tags name, and get the grant those tags ask for?
    Refused before spawn rather than after a burned attempt.
    FOREMAN_NEEDS_DISABLE=1 downgrades to log-only (rollback)."""
    # wq#2184: the lint's advisory bucket, from the same pass the dispatcher ran — a
    # prose-mentioned path the lint let through must not be refused here for reach.
    v = capability.preflight(ticket, repo, add_dirs, ticket_needs, worktree=worktree,
                             policy_path=FLEET_EXEC_POLICY_PATH,
                             advisory_paths=capability_lint.advisory_paths_for(
                                 ticket, repo, add_dirs))
    if v.downgraded:
        log(f"wq#{ticket_id}: {v.summary}")
        return v
    if not v.ok:
        raise SpawnRefused(v.code, v.summary, worktree=worktree,
                           unreachable_paths=v.unreachable_paths, refused=v.refused)
    return v


def assert_spawn_paths(ticket_id, detail, worktree, repo, add_dirs, needs_dirs):
    """The path half alone, for callers that have needs:dir tags and nothing else."""
    return assert_spawn_capability(
        ticket_id, {"detail": detail}, worktree, repo, add_dirs,
        needs_mod.Needs([("dir", d) for d in needs_dirs]))


def resolve_exec_grant(ticket_id, attempt, needs, wall_clock_s, worktree=None):
    """wq#1332 (§2.2 point 2, §2.4): turn a ticket's needs:ssh/copy/restart tags into
    a grant fleet-exec can enforce, refusing the SPAWN (uncharged, same class as
    assert_spawn_paths) if any tag asks for something fleet-exec-policy.yaml forbids
    outright — "a tag the static policy forbids is a dispatch REFUSAL, never a silent
    downgrade". ssh(node) tags are always grantable as read-only `run`: the actual
    argv allowlist is enforced by the broker per-invocation, not knowable at spawn
    time. Returns (grant_dict_or_None, sha256_or_None, path_or_None) — a ticket with
    no exec-shaped needs tags gets (None, None, None), which fworker treats as "no
    FOREMAN_GRANT_FILE for this attempt" (fleet-exec then denies on grant_missing_env
    if the worker tries to call it anyway — the sandbox's own allow rule for
    fleet-exec is unconditional, so this refusal has to come from the grant, not the
    Bash allowlist)."""
    if needs_mod.disabled():
        return None, None, None
    # wq#1436: the allow/deny half is capability.policy_grants — the same code the
    # dispatcher's pre-skip asks "would this be granted?". Only the lease and the
    # 0444 file below are fworker's.
    grants, refused = capability.policy_grants(needs, policy_path=FLEET_EXEC_POLICY_PATH)
    if refused:
        raise SpawnRefused("CAPABILITY_DENIED_BY_POLICY", "; ".join(refused),
                           worktree=worktree, refused=refused)
    if not grants:
        return None, None, None
    expires = (datetime.now(timezone.utc) + timedelta(seconds=wall_clock_s)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    grant = {"ticket": ticket_id, "attempt": attempt, "expires": expires, "grants": grants}
    path = os.path.join(run_dir(ticket_id), "grant.json")
    raw = json.dumps(grant, indent=2, sort_keys=True).encode() + b"\n"
    if os.path.exists(path):
        os.chmod(path, 0o600)
    with open(path, "wb") as f:
        f.write(raw)
    # 0444 not 0400: the token-engine daemon (uid tokeneng) re-reads and
    # re-hashes this file itself rather than trusting the worker
    # (TOKEN_ENGINE_DESIGN.md §1.3, wq#2270). It holds no secret; the sha env
    # var remains the tamper check.
    os.chmod(path, 0o444)
    return grant, fleet_exec_policy.sha256_bytes(raw), path


# wq#2349: how long past the wall-clock ceiling the ticket-end grant stays valid.
# SessionEnd fires after the child exits — which for a watchdog kill is at the
# ceiling itself — so a lease of exactly wall_clock_s would expire during the very
# hook it exists for. The margin holds only {token_engine, daemon}, whose verbs
# revoke this ticket's own tokens, so widening it de-privileges nothing.
TICKET_END_GRANT_MARGIN_S = 1800


def write_ticket_end_grant(ticket_id, attempt, worker_id, wall_clock_s):
    """The grant guard.sh --lifecycle end needs to call the broker's ticket-end verb
    (TOKEN_ENGINE_DESIGN.md §3.4). -> (path, sha256), or (None, None) if it could not
    be written — best-effort like mint_gateway_key: a missing grant costs a revoke
    (the daemon-start reaper still sweeps), never a dispatch.

    This is deliberately NOT resolve_exec_grant's grant. That one is minted only for a
    ticket carrying needs:ssh/copy/restart tags — almost none do — so the SessionEnd
    hook, which passed no grant at all and let te_client fall back to
    FOREMAN_GRANT_FILE, denied on grant:grant_missing_env for 283 of 283 ticket-end
    calls (events/fleet-exec.jsonl) and §3.4 revocation had never once run. Ending a
    ticket is a lifecycle op every attempt is entitled to, not an exec capability, so
    it gets its own grant with exactly the {token_engine, daemon} kind handle() looks
    up, bound to this ticket AND this worker id: load_grant then forces
    grant.ticket == req.ticket, so the verb can only revoke what this attempt minted.

    One file per attempt, not one per ticket: a previous attempt's SessionEnd hook can
    still fire after the next attempt has started, and a shared path would leave that
    hook's FOREMAN_TICKET_END_GRANT_SHA pointing at rewritten bytes (grant_sha_mismatch
    — the same fail-closed denial in a new costume)."""
    try:
        expires = (datetime.now(timezone.utc)
                   + timedelta(seconds=wall_clock_s + TICKET_END_GRANT_MARGIN_S)
                   ).strftime("%Y-%m-%dT%H:%M:%SZ")
        grant = {"ticket": str(ticket_id), "attempt": attempt, "worker": worker_id,
                 "expires": expires,
                 "grants": [{"kind": "token_engine", "node": "daemon"}]}
        path = os.path.join(run_dir(ticket_id), f"ticket-end-grant-a{attempt}.json")
        raw = json.dumps(grant, indent=2, sort_keys=True).encode() + b"\n"
        if os.path.exists(path):
            os.chmod(path, 0o600)
        with open(path, "wb") as f:
            f.write(raw)
        # 0444 for the same reason grant.json above is: uid tokeneng re-reads and
        # re-hashes the file itself rather than trusting the caller's sha.
        os.chmod(path, 0o444)
        return path, fleet_exec_policy.sha256_bytes(raw)
    except OSError as e:
        log(f"wq#{ticket_id}: ticket-end grant not written ({type(e).__name__}: {e}) "
            f"— session-end revocation will skip, reaper still sweeps")
        return None, None


def write_worktree_credential_config(worktree):
    """T1 (wq#2271, TOKEN_ENGINE_DESIGN.md §3.1): route every git credential
    lookup in this worktree through the token-engine helper. Written into the
    per-worktree config file (config.worktree — auditable, never ~/.gitconfig
    and never the repo config every other worktree shares). The empty helper
    entry first is load-bearing: git ACCUMULATES credential.helper values
    across config scopes, and the global scope still carries the store helper
    (~/.git-credentials — the exact leak surface this ticket retires) until
    the migration finalizes; an empty value resets the list so token-engine
    is the only helper this worktree can reach. useHttpPath makes git send
    the repo path with the request, which is how the daemon scopes the
    credential per repo rather than per host.

    T3 (wq#2272, design §3.3) adds core.hooksPath → the root-installed hooks
    dir, whose pre-push refuses userinfo URLs and any forge push whose
    resolved credential.helper is not token-engine. Written even where the
    dir is not installed yet: git treats a missing hooksPath as hookless, so
    the entry is inert until install.sh lands the wall.

    Best-effort by design: a worktree without this config falls back to the
    pre-T1 credential path, which must keep working until the deploy
    finalizes (install.sh's TE_FINALIZE_MOVE) — failing the dispatch would
    brick every lane on a git oddity."""
    steps = [["config", "extensions.worktreeConfig", "true"],
             ["config", "--worktree", "--replace-all", "credential.helper", ""],
             ["config", "--worktree", "--add", "credential.helper", "token-engine"],
             ["config", "--worktree", "credential.useHttpPath", "true"],
             ["config", "--worktree", "core.hooksPath",
              "/usr/local/lib/token-engine/hooks"]]
    for args in steps:
        p = _git(worktree, *args)
        if p.returncode != 0:
            log(f"WARN: worktree credential config failed at 'git {' '.join(args)}' "
                f"in {worktree}: {(p.stderr or '').strip()[:200]}")
            return False
    return True


def make_worktree(repo, ticket_id, attempt, title, archetype):
    """RELIABILITY_SPEC.md §D + wq#649: the branch name embeds the attempt number
    from the start (previously only a retry-fallback appended it), since attempt 1's
    bare branch name would otherwise collide with every LATER attempt of the same
    ticket whose worktree/branch nothing ever cleans up. That alone doesn't cover a
    crash between `git worktree add` succeeding and this attempt's state file being
    written (write_attempt_state runs only after the child exits) — a re-invocation
    after that crash recomputes the SAME attempt number (next_attempt_n counts written
    attempt-N.json files) and calls this with identical args. Adopt-existing-if-ours
    handles that exact replay instead of raising and leaving the wq claim dangling
    (the fworker-626c repro this ticket fixes: 'branch ...-a3 already exists', third
    dangling-claim instance in the fq#614/fq#618 family).
    """
    type_map = {
        "mechanical-edit": "chore", "probe-build": "test", "rca-investigation": "fix",
        "novel-design": "docs",
    }
    branch_type = type_map.get(archetype, "feat")
    branch = f"{branch_type}/wq{ticket_id}-{slugify(title)}-a{attempt}"
    wt = os.path.join(WORKTREES_ROOT, f"wq{ticket_id}-a{attempt}")
    os.makedirs(WORKTREES_ROOT, exist_ok=True)

    # wq#1141: every git op below MUTATES the primary repo's .git — `worktree prune`,
    # `worktree add` and `worktree remove` write worktree bookkeeping, refs and the
    # index/ref locks. Run them under the SAME repo git lock `lumen-pr land` takes
    # (bin/lumen-pr gitlock_scope), so worktree setup can never race a concurrent
    # land/rebase on the primary tree. The wq#1097 corruption was exactly that race: an
    # orchestrator's #78 land on /home/lumen/pokemon vs an unlocked `worktree add` on
    # the same .git. slock is non-blocking and the contender is a transient (seconds)
    # land, so _gitlock_acquire retries briefly, then we raise rather than race — the
    # attempt defers and redispatch retries.
    scope = _gitlock_scope(repo)
    holder = os.environ.get("WQ_AGENT") or f"fworker-wq{ticket_id}-a{attempt}:{os.getpid()}"
    if not _gitlock_acquire(scope, holder, f"wq{ticket_id} a{attempt} worktree setup"):
        raise RuntimeError(
            f"could not take git lock '{scope}' for worktree setup after "
            f"{GITLOCK_TRIES} tries — another agent is mid-merge on {repo}; deferring.")
    try:
        # prune-on-start: clears git's own bookkeeping for any worktree dir that vanished
        # without a proper `git worktree remove` (operator rm -rf, partial crash cleanup),
        # so a since-deleted dir can't make a live branch look falsely "checked out".
        _git(repo, "worktree", "prune")

        if os.path.isdir(wt):
            p = _git(wt, "rev-parse", "--abbrev-ref", "HEAD")
            if p.returncode == 0 and p.stdout.strip() == branch:
                log(f"wq#{ticket_id} attempt {attempt}: adopting worktree left by a prior "
                    f"crashed attempt at the exact same (ticket, attempt) — {wt} ({branch})")
                assert_worktree_repo(wt, repo, ticket_id)
                write_worktree_credential_config(wt)
                return wt, branch
            # not ours (or ours but on the wrong branch) — drop it so `worktree add` below
            # gets a clean path instead of failing on an already-existing directory.
            if _git(repo, "worktree", "remove", "--force", wt).returncode != 0:
                shutil.rmtree(wt, ignore_errors=True)

        p = _git(repo, "worktree", "add", wt, "-b", branch, "HEAD")
        if p.returncode != 0 and "already exists" in (p.stderr or ""):
            # the branch name is deterministic from (ticket_id, attempt), so if it exists
            # with no live worktree attached (the worktree dir was removed some other way,
            # e.g. by hand) it is necessarily ours — reattach instead of erroring.
            log(f"wq#{ticket_id} attempt {attempt}: branch {branch} exists with no live "
                f"worktree — adopting.")
            p = _git(repo, "worktree", "add", wt, branch)
        if p.returncode != 0:
            # last resort: a pid-uniqued branch guarantees no further collision.
            branch = f"{branch}-p{os.getpid()}"
            p = _git(repo, "worktree", "add", wt, "-b", branch, "HEAD")
        if p.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {p.stdout} {p.stderr}")
        # wq#1331 (§3.2): assert the tree we just made is of the repo the router
        # resolved, before any child is spawned against it.
        assert_worktree_repo(wt, repo, ticket_id)
        write_worktree_credential_config(wt)
        return wt, branch
    finally:
        _slock_release(scope, holder)


# ---------------------------------------------------------------------------
# child env + spawn (design §3)
# ---------------------------------------------------------------------------

def read_oauth_token():
    """wq#616: the setup-token minted under wq#615, read fresh per attempt (never
    cached in a module global — a rotated/replaced file must take effect on the next
    spawn without a fworker restart). Returns None, never raises, on any read failure;
    callers fall back to the shared login rather than fail the dispatch outright."""
    try:
        with open(OAUTH_TOKEN_FILE) as f:
            token = f.read().strip()
        return token or None
    except OSError:
        return None


TMP_EXCLUDE_PATTERN = ".tmp/"


def _ensure_worktree_tmpdir(worktree):
    """wq#1915(B): every tool that falls back to tempfile.gettempdir()/$TMPDIR must
    land inside the worktree, or it hits guard.sh's write-outside-worktree denial
    (CAPABILITY_DENIED -- wq#1852/wq#1889, 2 attempts each, both refused 3x on exactly
    this). Returns the created dir's path. 0700: scratch may contain ticket-specific
    intermediate output, no reason to leave it group/other readable.

    Also excludes it via the repo's git info/exclude. wq#1915's own text says "the
    worktree's info/exclude", but structurally a worktree has no info/exclude of its
    own -- `git -C <worktree> rev-parse --git-path info/exclude` resolves to the ONE
    shared file in the common gitdir (verified: every worktree of this repo resolves
    to the same path). That is the correct target anyway: a tracked .gitignore line
    would need its own PR to land before the very first worker that needs it could
    rely on it, and every other worktree of this repo wants .tmp/ ignored too.
    Idempotent (checked-then-appended) so calling this once per attempt is cheap and
    safe under concurrent attempts on different tickets sharing the same repo."""
    tmp_dir = os.path.join(worktree, ".tmp")
    os.makedirs(tmp_dir, mode=0o700, exist_ok=True)
    os.chmod(tmp_dir, 0o700)  # exist_ok path may have pre-existed with a looser mode
    try:
        # cwd=worktree, not `-C worktree`: git's --git-path prints a path relative to
        # the CALLING process's cwd (verified empirically -- `-C` does not make the
        # output relative to its own target), so resolving against our own known
        # `worktree` via os.path.join below is only correct if git's relative output
        # is ALSO relative to that same directory.
        p = subprocess.run(["git", "rev-parse", "--git-path", "info/exclude"],
                           capture_output=True, text=True, timeout=10, cwd=worktree)
        if p.returncode == 0 and p.stdout.strip():
            exclude_path = os.path.normpath(os.path.join(worktree, p.stdout.strip()))
            existing = ""
            if os.path.exists(exclude_path):
                with open(exclude_path) as f:
                    existing = f.read()
            if TMP_EXCLUDE_PATTERN not in existing.splitlines():
                with open(exclude_path, "a") as f:
                    if existing and not existing.endswith("\n"):
                        f.write("\n")
                    f.write(f"{TMP_EXCLUDE_PATTERN}\n")
    except (OSError, subprocess.SubprocessError) as e:
        log(f"WARN: could not add {TMP_EXCLUDE_PATTERN!r} to git info/exclude for "
            f"{worktree}: {e!r}")
    return tmp_dir


def build_env(worker_id, model, effort, worktree, ticket_id, cli_profile=False):
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_BASE_URL", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    env.pop("CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY", None)
    # T4/T2b: the two auth paths are mutually exclusive by pool — a local-pool model
    # gets the LiteLLM gateway vars below and never the cloud static bearer, a cloud
    # model gets the static bearer and never the gateway vars.
    if not is_local_model(model):
        token = read_oauth_token()
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
        else:
            log(f"WARN: no static oauth token at {OAUTH_TOKEN_FILE} — falling back to the "
                f"shared login (refresh-race exposure, FOREMAN_RESEARCH.md §8)")
    env["HOME"] = "/home/lumen"
    env["FOREMAN_WORKER"] = worker_id
    # wq#1116: the worker closes the ticket fworker claimed, so it must carry the identity that
    # claimed it — not its own per-attempt worker_id, which never matched the holder.
    env["WQ_AGENT"] = wq_identity(ticket_id)
    env["FOREMAN_EVENTS_DIR"] = EVENTS_DIR
    env["FOREMAN_WORKTREE_ROOT"] = worktree
    # wq#1915(B): scratch goes under $TMPDIR, which is inside your worktree.
    tmp_dir = _ensure_worktree_tmpdir(worktree)
    env["TMPDIR"] = tmp_dir
    env["TEMP"] = tmp_dir
    env["TMP"] = tmp_dir
    # wq#1697: the /tmp/<agent>/ dir `wq claim` tells the worker to use is created here and
    # handed to the CLI as an --add-dir (scratch_grant_dirs), so following wq is not a denial.
    env["FOREMAN_SCRATCH_DIR"] = scratch_grant.ensure_scratch(env["WQ_AGENT"])
    # wq#900: registered primary trees for guard.sh's branch-freeze deny class —
    # computed here (router.LANE_REPO + static mirror) so the guard never depends
    # on an import at hook time.
    env["FOREMAN_PRIMARY_TREES"] = ":".join(primary_tree_guard.primary_trees())
    if effort:
        env["CLAUDE_CODE_EFFORT_LEVEL"] = effort
    env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "500000"
    env["BASH_MAX_OUTPUT_LENGTH"] = BASH_MAX_OUTPUT_LENGTH
    env["CLAUDE_CODE_ENABLE_TELEMETRY"] = "1"
    env["OTEL_METRICS_EXPORTER"] = "otlp"
    env["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = OTEL_ENDPOINT
    env["OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE"] = "cumulative"
    env["OTEL_METRIC_EXPORT_INTERVAL"] = "60000"
    # T4 (BUILD_PLAN.md §T4): per-worker ONLY, never global — this env dict is built
    # fresh per attempt and only ever handed to THIS attempt's `claude -p` child, never
    # merged back into fworker's own process env, so a local-rung attempt can never
    # leak the gateway vars into a later cloud dispatch on the same host/session.
    if is_local_model(model):
        env["ANTHROPIC_BASE_URL"] = LITELLM_BASE_URL
        # T10 (wq#2278): per-ticket key with TTL+budget when the broker is up;
        # the long-lived virtual key is the no-broker fallback only.
        env["ANTHROPIC_AUTH_TOKEN"] = (mint_gateway_key(ticket_id, worker_id)
                                       or read_litellm_key())
        env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "1"
    if cli_profile:
        env.update(local_profile_env())
    # wq#2831: KEENABLE_API_KEY backs KEENABLE_MCP_CONFIG's Authorization header;
    # unset means the CLI's MCP handshake fails closed (no key file yet) rather than
    # dispatching with a bad Authorization header.
    keenable_key = read_keenable_key()
    if keenable_key:
        env["KEENABLE_API_KEY"] = keenable_key
    return env


# wq#1245: workers (esp. verify tickets) must be able to READ the fleet's primary trees to verify
# cross-repo work and reach live env/config. Writes stay in the worktree by harness discipline +
# primary_tree_guard; this only widens read/context access.
# wq#1301: default list now sourced from agents.yaml's `defaults.add_dirs` (name kept
# for existing callers/tests). add_dirs_for() below resolves the per-archetype list —
# every archetype uses this same default today, but a future archetype-specific
# add_dirs entry in agents.yaml takes effect without another fworker.py change.
WORKER_ADD_DIRS = _AGENTS.default_add_dirs


def add_dirs_for(archetype, needs_dirs=None, repo=None):
    """Per-archetype --add-dir list (wq#1301), unioned with the ticket's own
    needs:dir tags (wq#1331 §3.2 — read context only; landing still goes through the
    worktree). needs_dirs is already empty when FOREMAN_NEEDS_DISABLE=1 because
    needs.parse() returned nothing.

    wq#4623: `repo` (the primary the worktree is cut from) is appended too — wq#4608 got
    a messenger worktree's ticket and no path to the live /home/lumen/messenger copy.
    Read context only: primary_tree_guard write-freezes every router.REPO_TAGS /
    LANE_REPO tree. capability.effective_add_dirs mirrors this.

    wq#1439 (BROKER_WIRING.md §2.3): every needs:dir is checked against the lint's
    own deny-list before it is unioned, so a hand-written `needs:dir(/home/lumen/.ssh)`
    is refused at spawn (uncharged SpawnRefused, same code as a policy refusal) and
    not only at dispatch — needs.validate() only checks "absolute path". The check
    is all-or-nothing: one denied dir refuses the spawn rather than granting the
    safe remainder (§4 row vi, "never partially granted")."""
    base = _AGENTS.get(archetype).add_dirs if archetype in _AGENTS else WORKER_ADD_DIRS
    out = list(base)
    refused = []
    for d in needs_dirs or []:
        why = capability_lint.dir_denied(d)
        if why:
            refused.append(f"dir({d}) {why}")
            continue
        if d not in out:
            out.append(d)
    if refused:
        raise SpawnRefused("CAPABILITY_DENIED_BY_POLICY", "; ".join(refused),
                           refused=refused)
    if repo and repo not in out:
        out.append(repo)
    return out

def scratch_grant_dirs(ticket_id):
    """wq#1697: the wq scratch dir for this ticket's owner identity, as an --add-dir."""
    return [scratch_grant.scratch_dir(wq_identity(ticket_id))]


# design/orchestrator_verify_lane.md §5.2: verify-probe is a lane, not a repo toolchain —
# its grant (read-only reach + probe execution, writes only to reports/**) must apply no
# matter which repo the ticket names, so it bypasses the per-repo repos.d/ union entirely
# rather than being declared as one more repos.d/<repo>.json fragment.
VERIFY_PROBE_SETTINGS_FILE = os.path.join(HARNESS_DIR, "permission-profiles", "verify-probe.json")


# repo-read grant (wq#4762, DESIGN.md §2.5): verify-pass gets the SAME box as a
# plain build worker on this repo PLUS this one rule, never the reverse — an
# ordinary build-archetype ticket on the same repo renders without it.
ARCHETYPE_EXTRA_ALLOW = {
    "verify-pass": ["Bash(/home/lumen/bin/repo-read *)"],
}


def resolve_worker_settings(repo, ticket_id, archetype=None):
    """ISSUE-SOLUTIONS-fable.md §2b: render this repo's per-repo toolchain permission
    profile (permission-profiles/repos.d/<repo>.json) into the ticket's run_dir, unioned
    from the shared profile fragments. A repo with no profile declaration yet falls back
    to the static SETTINGS_FILE unchanged — no regression for repos not yet migrated.
    verify-probe overrides the per-repo profile outright (see VERIFY_PROBE_SETTINGS_FILE).
    verify-pass instead keeps the per-repo profile and adds ARCHETYPE_EXTRA_ALLOW's
    rules on top (wq#4762) — it stays build-worker-shaped for everything else.
    """
    if archetype == "verify-probe":
        return VERIFY_PROBE_SETTINGS_FILE
    settings_file, digest = permission_profiles.resolve_settings_file(
        repo, SETTINGS_FILE, run_dir(ticket_id),
        archetype_allow=ARCHETYPE_EXTRA_ALLOW.get(archetype))
    if digest:
        log(f"wq#{ticket_id}: rendered permission profile {digest[:12]} -> {settings_file}")
    return settings_file


# wq#2185: the local-rung CLI profile. The Claude CLI's own request (default system
# prompt, the full built-in tool catalogue, MCP tool definitions) was ~14k of the 18.5k
# tokens a Mac had to prefill before the first frame; the two local archetypes only need
# these six tools. Not `--bare`: the action gate and stop gate hooks must keep running.
LOCAL_CLI_TOOLS = "Bash,Read,Edit,Write,Grep,Glob"
LOCAL_CLI_FLAGS = ("--tools", LOCAL_CLI_TOOLS, "--strict-mcp-config",
                   "--exclude-dynamic-system-prompt-sections")


LOCAL_BACKGROUND_MODEL = "deepseek-v4-flash"

# wq#2831: Keenable.ai web-search MCP endpoint, passed via --mcp-config rather than a
# repo-root .mcp.json — ISSUE-LOG #862 found Write(".mcp.json") refused as a sensitive
# file with no human to approve it in an unattended worker session. The Authorization
# header expands ${KEENABLE_API_KEY} from the attempt env (build_env), never inlined
# here; an unset var means the CLI no-ops the server rather than sending a bad header.
KEENABLE_MCP_CONFIG = json.dumps({"mcpServers": {"keenable": {
    "type": "http", "url": "https://api.keenable.ai/mcp",
    "headers": {"Authorization": "Bearer ${KEENABLE_API_KEY}"}}}})


def local_profile_env():
    """Env half of the local CLI profile (all measured against 2.1.276, see
    cli_calibration.py). The CLI's session-title call is a second ~2.4k-token request
    that took the box's second slot: DISABLE_TERMINAL_TITLE stops it, and any other
    small/fast-model call is routed to a cheap non-Mac model the scoped gateway key
    allows. CLAUDE.md files, auto-memory and the git-status/instructions blocks are the
    CLI's per-machine context: the slim prompt is self-contained, and the fleet
    CLAUDE.md's interactive busy-key protocol is wrong for a headless worker."""
    return {"CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1",
            "ANTHROPIC_SMALL_FAST_MODEL": LOCAL_BACKGROUND_MODEL,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": LOCAL_BACKGROUND_MODEL,
            "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "1"}


def build_claude_cmd(prompt, model, effort, settings_file, add_dirs, max_turns=None,
                      resume_session=None, claude_bin="claude", local_profile=False,
                      resume_no_prompt=False):
    """wq#4765 (§2.3 step 6, §2.7 F9): `resume_no_prompt` is the grant-approval resume
    shape — bare `-p` with no prompt text, so the CLI re-fires PreToolUse for the one
    pending deferred call instead of dropping it (the docs' own words: "-p without a
    prompt when you resume a session with a deferred tool call"). Refuses outright
    rather than silently sending a prompt anyway: a resume that carries one drops the
    deferred call, and that failure mode is otherwise invisible until the ticket is
    mysteriously still stuck on the same hold a turn later."""
    if resume_no_prompt:
        if prompt:
            raise ValueError("fworker refuses to spawn a resume with a prompt (wq#4765 "
                              "F9): a deferred tool call is dropped if --resume carries "
                              f"one (resume_session={resume_session!r})")
        if not resume_session:
            raise ValueError("resume_no_prompt requires resume_session")
        cmd = [claude_bin, "-p"]
    else:
        cmd = [claude_bin, "-p", prompt]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    cmd += ["--settings", settings_file]
    cmd += ["--permission-mode", "acceptEdits"]
    for d in add_dirs:
        cmd += ["--add-dir", d]
    cmd += ["--output-format", "stream-json", "--verbose", "--include-hook-events"]
    cmd += ["--mcp-config", KEENABLE_MCP_CONFIG]
    if local_profile:
        cmd += list(LOCAL_CLI_FLAGS)
    if resume_session:
        cmd += ["--resume", resume_session]
    return cmd


# ---------------------------------------------------------------------------
# stream reader — background thread so the main loop can enforce inactivity /
# wall-clock / turn-ceiling / heartbeat without blocking on a readline().
# ---------------------------------------------------------------------------

def _reader_thread(pipe, q):
    """RELIABILITY_SPEC.md §A/§F (#25670, shim 14-15, wq#648/S3): `pipe` is a
    `text=True, bufsize=1` TextIOWrapper over the child's stdout. Its readline() already
    does exactly what the NDJSON contract needs — byte-level read + newline split, with
    any partial trailing bytes (a line still in flight, no \\n yet) held in the
    TextIOWrapper's own internal buffer and NOT handed back until either a full line or
    EOF arrives. That's true regardless of how the child's own writes are chunked,
    which is what makes block-buffered piped stdout (#25670: the child can batch several
    KB before a flush) a NOT-A-FAILURE class rather than a parser bug — the frames just
    arrive late in a burst, never malformed or missing. This thread only forwards
    complete lines; run_child() below does the json.loads()+unparsed-fallthrough.
    """
    try:
        for line in iter(pipe.readline, ""):
            q.put(("line", line))
    except Exception as e:
        q.put(("reader_error", str(e)))
    finally:
        q.put(("eof", None))


# ---------------------------------------------------------------------------
# external SIGTERM (unit stop) — distinct from run_child()'s internal watchdog kill,
# which SIGTERMs the CHILD and then retries in-process (wq#1894).
# ---------------------------------------------------------------------------

# do_attempt() populates this right before spawning the child and marks it written
# once that attempt's own write_attempt_state() call lands normally — the window this
# covers is exactly "child in flight, no attempt-N.json yet", the wq#1852/1883 gap.
# Deliberately not wired into fire-mode's run_fire_child(): its attempt numbering is
# tracked separately (run_fire_ticket's attempt_n) and run_child() there is always
# called with attempt=1, so charging through this context would mislabel the record.
#
# wq#1911: "written" going True does NOT mean the claim is free to sit unguarded --
# it only means THIS attempt's record landed. The gap between one attempt's
# mark_attempt_written() and the next set_sigterm_context() (e.g. handle_limit's
# up-to-6h RESUME_WAIT_MAX_S sleep) still holds the wq claim with "written" stuck
# True from the finished attempt, so the old handler silently no-op'd there. "idle"
# tracks that gap explicitly: True means "claim is ours, no child in flight, no
# attempt-N.json pending" -- a SIGTERM must release the claim but must NOT fabricate
# or overwrite an attempt record for a slot that already landed.
_SIGTERM_CTX = {"ticket_id": None, "attempt": None, "worker_id": None, "proc": None,
                "written": False, "idle": False}


def set_sigterm_context(ticket_id, attempt, worker_id):
    _SIGTERM_CTX.update(ticket_id=ticket_id, attempt=attempt, worker_id=worker_id,
                         proc=None, written=False, idle=False)


def set_sigterm_idle(ticket_id):
    """wq#1911: mark the inter-attempt gap (claim held, no child in flight) as
    SIGTERM-safe-to-release. Call before any multi-second-or-longer wait between
    mark_attempt_written() and the next set_sigterm_context() -- currently only
    handle_limit's resume-wait sleep, the one with real-world (up to 6h) exposure."""
    _SIGTERM_CTX.update(ticket_id=ticket_id, attempt=None, proc=None, written=True,
                         idle=True)


def mark_attempt_written():
    """This attempt's record already landed the normal way (do_attempt's own
    write_attempt_state call) — a SIGTERM from here on must not re-charge or
    re-release; that would race the retry ladder's own decision for the SAME claim."""
    _SIGTERM_CTX["written"] = True


def handle_external_sigterm(signum, frame):
    """wq#1894: SIGTERM delivered to the fworker PROCESS itself (e.g. `systemctl stop
    fworker-wq<id>`), not run_child()'s frame-gap/wall-clock/turns watchdog — that
    internal path already funnels through _kill_proc_tree() and retries in the same
    process, so it needs no help here. An external stop kills the whole worker with no
    such retry: left alone, the ticket sits 'claimed' until lease expiry (wq#1852 sat
    6h) and the spawn reservation sits held until SPAWN_RESERVE_TTL_S, because nothing
    ever writes attempt-N.json or releases either.

    On the way out: kill whatever child is running (the same _kill_proc_tree every
    other terminate path uses), charge a minimal KILLED/signal attempt so
    next_attempt_n() moves on, and release the wq claim. write_attempt_state() settles
    the spawn reservation as its own side effect (_settle_spawn_reservation, wq#1893),
    so there is nothing separate to release there. A no-op (just exit) if no attempt is
    in flight, or this attempt's record already landed through the normal path.

    wq#1911: "landed through the normal path" is not the same as "safe to ignore" --
    set_sigterm_idle() marks the inter-attempt gap (e.g. handle_limit's resume-wait
    sleep) where the claim is still ours but no child is running and no attempt is
    pending. That gap still needs the claim released; it must NOT get a fabricated
    KILLED record written over the attempt that already landed."""
    ctx = _SIGTERM_CTX
    ticket_id, attempt = ctx["ticket_id"], ctx["attempt"]
    if ticket_id is not None and ctx["idle"]:
        log(f"SIGTERM: wq#{ticket_id} — worker stopping between attempts (no child "
            f"in flight, e.g. mid LIMIT resume-wait); releasing claim.")
        try:
            wq_release(ticket_id)
        except Exception as e:  # noqa: BLE001
            log(f"WARN: wq#{ticket_id} SIGTERM idle-release failed: {e!r}")
        sys.exit(128 + signum)
    if ticket_id is not None and not ctx["written"]:
        log(f"SIGTERM: wq#{ticket_id} attempt {attempt} — worker stopping with no "
            f"attempt-{attempt}.json yet; charging KILLED, releasing claim.")
        if ctx["proc"] is not None:
            _kill_proc_tree(ctx["proc"])
        try:
            write_attempt_state(ticket_id, attempt, {
                "ticket": ticket_id, "attempt": attempt, "worker_id": ctx["worker_id"],
                "classification": KILLED, "raw_classification": KILLED,
                "killed_reason": "signal", "finished_at": time.time(),
            })
        except Exception as e:  # noqa: BLE001 — still must release the claim below
            log(f"WARN: wq#{ticket_id} SIGTERM attempt-state write failed: {e!r}")
        try:
            wq_release(ticket_id)
        except Exception as e:  # noqa: BLE001
            log(f"WARN: wq#{ticket_id} SIGTERM release failed: {e!r}")
    sys.exit(128 + signum)


def _kill_proc_tree(proc, term_timeout=30, kill_timeout=10):
    """wq#1798 (dup-reported as wq#1813 — same gap, same fix, both close here): signal
    the whole process GROUP, not just the CLI pid. start_new_session=True at the spawn
    site (below) makes proc.pid double as its own process group id, so a group-wide kill
    reaches Bash-tool grandchildren (build/sleep/server) that would otherwise survive the
    CLI's death, keep the stdout pipe open, and keep mutating the worktree after fworker
    has already recorded KILLED. This is the single terminate site every caller (plain
    run, fire-mode's run_fire_child, and do_attempt's resume path) funnels through, since
    they all bottom out in this same run_child() watchdog loop — see
    tests/test_kill_group.py for a direct unit-level pin of this function, and
    tests/test_fworker_integration.py:test_watchdog_kill_reaches_grandchild_process for
    the full-CLI-dispatch version of the same acceptance."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=term_timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=kill_timeout)
    except subprocess.TimeoutExpired:
        pass


# ---------------------------------------------------------------------------
# wq#1888 despag P2-2: gateway-backed spawn (canary) — run_child_gateway
#
# FOREMAN_GATEWAY_LANES=<comma list> (default empty: no lane) opts a ticket's own
# `lane` into running its turn through despag's `POST /v1/responses` instead of a
# local `claude -p` child, so despag/admission.py becomes the ONE admission gate for
# the Max pool (PHASE2_WIRING_SPEC.md §B.7) instead of two independent ones. See
# harness/GATEWAY_CANARY.md for what to watch and the revert.
# ---------------------------------------------------------------------------

FOREMAN_GATEWAY_URL = os.environ.get("FOREMAN_GATEWAY_URL", "http://127.0.0.1:8790")
# instinct 4: fworker reads its OWN copy of despag's bearer token — never the CLI's
# CLAUDE_CODE_OAUTH_TOKEN, which never appears in the gateway request at all (the
# JSON body below carries only input/model/stream, nothing from `env`).
FOREMAN_GATEWAY_TOKEN_FILE = os.environ.get(
    "FOREMAN_GATEWAY_TOKEN_FILE", os.path.expanduser("~/.secrets/despag-api.token"))
GATEWAY_CONNECT_TIMEOUT_S = _envint("FOREMAN_GATEWAY_CONNECT_TIMEOUT_S", 10)
# Read-loop poll granularity — small next to wall_clock_s/frame_gap_s (minutes), same
# role as run_child's own poll_timeout: how often the watchdog checks can fire, not a
# meaningful latency bound on its own.
GATEWAY_POLL_S = _envint("FOREMAN_GATEWAY_POLL_S", 2)


def gateway_lanes():
    raw = os.environ.get("FOREMAN_GATEWAY_LANES", "")
    return {p.strip() for p in raw.split(",") if p.strip()}


def gateway_lane_enabled(lane):
    lane = (lane or "").strip()
    return bool(lane) and lane in gateway_lanes()


class GatewayUnavailable(Exception):
    """Connect failure or a 5xx from despag — the only two shapes run_child_gateway
    treats as 'the gateway itself is down', triggering the direct claude -p fallback.
    A 4xx (e.g. despag's own admission gate refusing the turn) is a real answer, not
    an availability problem, and must not fall back — see _gateway_post_stream."""


def _parse_claude_argv(cmd):
    """Pull (prompt, model) back out of build_claude_cmd()'s argv. Keeping
    run_child_gateway's signature identical to run_child's (PHASE2_WIRING_SPEC.md
    §B.1: the call site is a ~6-line, 2-site diff) means the one extra hop of
    re-deriving what the caller already had as separate variables happens here,
    once, rather than by widening both call sites' parameter lists."""
    prompt = cmd[2] if len(cmd) > 2 and cmd[1] == "-p" else None
    model = None
    if "--model" in cmd:
        i = cmd.index("--model")
        if i + 1 < len(cmd):
            model = cmd[i + 1]
    return prompt, model


def _read_gateway_token():
    with open(os.path.expanduser(FOREMAN_GATEWAY_TOKEN_FILE)) as f:
        return f.read().strip()


def _gateway_connection(timeout_s):
    parsed = urllib.parse.urlparse(FOREMAN_GATEWAY_URL)
    cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    return cls(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
               timeout=timeout_s), (parsed.path.rstrip("/") or "")


def _gateway_post_stream(prompt, model, *, connect_timeout, read_timeout):
    """POST /v1/responses stream:true. Returns the open http.client.HTTPResponse for
    the caller to read SSE lines off of, with its socket's timeout re-armed from
    `connect_timeout` to the much shorter `read_timeout` (`HTTPConnection.sock` is a
    plain public attribute — the socket object `resp.fp` wraps, so this reaches every
    later `resp.readline()` too, not just the connect) so the watchdog loop below can
    poll at its own granularity instead of blocking for up to `connect_timeout` per
    read. Raises GatewayUnavailable on connect failure or a 5xx; anything else (a 4xx
    UHP error envelope) is returned as-is for the caller to read the body of and map
    onto a real verdict, never a fallback."""
    try:
        conn, base_path = _gateway_connection(connect_timeout)
        token = _read_gateway_token()
        body = json.dumps({"input": prompt, "model": model, "stream": True}).encode()
        conn.request("POST", f"{base_path}/v1/responses", body=body, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        })
        resp = conn.getresponse()
    except OSError as e:
        # ECONNREFUSED (dead port), DNS failure, connect timeout — despag is not
        # reachable at all. This is the fallback's own repro shape (point
        # FOREMAN_GATEWAY_URL at a dead port).
        raise GatewayUnavailable(f"gateway connect failed: {e}") from e
    if resp.status >= 500:
        raise GatewayUnavailable(f"gateway HTTP {resp.status}")
    if conn.sock is not None:
        conn.sock.settimeout(read_timeout)
    return resp


def _gateway_cancel(response_id, *, connect_timeout):
    """Best-effort — mirrors _kill_proc_tree's own posture of never raising into the
    caller. Substitutes for process-group cancel semantics: there is no local pid to
    killpg, so a client-side wall-clock/frame-gap trip asks despag's runner to kill
    the process GROUP it owns instead (app.py: POST /v1/responses/{id}/cancel ->
    despag.runner.turn.Turn.cancel(), which already killpg's — PHASE2_WIRING_SPEC.md
    §B.5)."""
    try:
        conn, base_path = _gateway_connection(connect_timeout)
        token = _read_gateway_token()
        conn.request("POST", f"{base_path}/v1/responses/{response_id}/cancel", body=b"{}",
                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        conn.getresponse().read()
    except Exception as e:  # noqa: BLE001 — best-effort, never the reason a turn fails
        log(f"gateway cancel({response_id}) failed (non-fatal): {e}")


def _frames_from_lumen_output(output_items):
    """Reverse of despag.uhp.translate.blocks_from_canonical: one canonical
    assistant/user frame per finished UHP output item, in order. Not fed through
    state.observe() (the `lumen.run_state` sidecar already carries the authoritative
    turns/last_assistant_text/etc a real classifier.RunState computed off the SAME
    frames server-side — see _observe_gateway_event) — only the loop tracker
    (Layer 2, wq#985) needs the raw tool_use frames themselves, since its state
    lives outside classifier.RunState (PHASE2_WIRING_SPEC.md §B.5: 'the loop
    breaker survives')."""
    frames = []
    for item in output_items:
        t = item.get("type")
        if t == "function_call":
            args = item.get("arguments")
            try:
                inp = json.loads(args) if isinstance(args, str) else (args or {})
            except (TypeError, ValueError):
                inp = {"_raw": args}
            frames.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": item.get("call_id") or item.get("id"),
                 "name": item.get("name"), "input": inp}]}})
        elif t == "function_call_output":
            frames.append({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": item.get("call_id"),
                 "content": item.get("output"), "is_error": False}]}})
    return frames


def _observe_gateway_event(ev, state, loop_tracker):
    """One UHP `response.*` SSE event folded onto `state`. Returns (done, exit_code):
    `done` is True once a terminal event (completed/incomplete/failed) has been
    folded in, at which point the caller stops reading. `exit_code` is only
    meaningful once `done` is True.

    Mid-turn `lumen.limit` (PHASE2_WIRING_SPEC.md §B.6 item 2) is folded through the
    exact synthetic rate_limit_event frame classifier.RunState.observe() already
    understands, and reported to the shared governor the same way run_child's own
    watchdog loop does (report_limit_event) — so the account-global rate-limit
    history despag/admission.py's own check_admission reads back
    (foreman_usaged.load_events) stays in sync regardless of which backend served a
    turn.
    """
    etype = ev.get("type")
    if etype == "lumen.limit":
        frame = {"type": "rate_limit_event",
                 "rate_limit_info": {"status": "rejected", "resetsAt": ev.get("resets_at")}}
        was = state.saw_limit
        state.observe(frame)
        if state.saw_limit and not was:
            report_limit_event(frame)
        if loop_tracker is not None:
            loop_tracker.observe(frame)
        return False, None
    if etype not in ("response.completed", "response.incomplete", "response.failed"):
        return False, None

    response = ev.get("response") or {}
    for frame in _frames_from_lumen_output(response.get("output") or []):
        if loop_tracker is not None:
            loop_tracker.observe(frame)

    # §B.6 item 1: the terminal sidecar IS a classifier.RunState snapshot (computed
    # server-side, off the real canonical frames, by the same class this process
    # would use for a direct spawn) — adopt it wholesale rather than re-derive a
    # weaker approximation from the UHP output shape.
    sidecar = (response.get("lumen") or {}).get("run_state") or {}
    for key, value in sidecar.items():
        if key != "exit_code" and hasattr(state, key):
            setattr(state, key, value)
    state.usage = state.usage or response.get("usage")
    return True, sidecar.get("exit_code")


def run_child_gateway(cmd, cwd, env, max_turns, wall_clock_s, ticket_id, attempt,
                       frame_gap_s=None, post_result_grace_s=None, loop_tracker=None,
                       first_frame_s=None, post_result_bg_grace_s=None):
    """Gateway-backed sibling of run_child(): identical signature and identical
    6-tuple return (state, exit_code, raw_events, killed_reason, cpu_s, pid), so a
    caller's choice of which one to call is a plain conditional (PHASE2_WIRING_SPEC.md
    §B.1). Spawns nothing locally — POSTs `/v1/responses` `stream:true` to despag
    (FOREMAN_GATEWAY_URL) and maps its UHP events back onto the canonical frame shape
    classify() already understands (_observe_gateway_event).

    Falls back to a direct run_child() spawn on any gateway connect failure or 5xx
    (never on a 4xx — despag refusing the request is a real answer, e.g. its own
    admission gate, not an availability problem), recording `gateway_fallback` +
    `gateway_fallback_reason` on the returned state so the canary is measurable
    (do_attempt copies both onto the attempt record). `cpu_s` is always 0.0 (no local
    child to RUSAGE_CHILDREN) and `pid` is always None on the gateway path itself —
    both already documented as corroboration-only, never fed into classify().
    """
    frame_gap_s = frame_gap_s if frame_gap_s is not None else INACTIVITY_TIMEOUT_S
    prompt, model = _parse_claude_argv(cmd)

    def _fallback(reason):
        log(f"wq#{ticket_id} attempt {attempt}: gateway unavailable ({reason}) — "
            f"falling back to direct claude -p spawn")
        fb_state, exit_code, raw_events, killed_reason, cpu_s, pid = run_child(
            cmd, cwd, env, max_turns, wall_clock_s, ticket_id, attempt,
            frame_gap_s=frame_gap_s, post_result_grace_s=post_result_grace_s,
            loop_tracker=loop_tracker, first_frame_s=first_frame_s,
            post_result_bg_grace_s=post_result_bg_grace_s)
        fb_state.gateway_backend = False
        fb_state.gateway_fallback = True
        fb_state.gateway_fallback_reason = reason
        return fb_state, exit_code, raw_events, killed_reason, cpu_s, pid

    poll_s = max(0.25, min(GATEWAY_POLL_S, frame_gap_s))
    try:
        resp = _gateway_post_stream(prompt, model, connect_timeout=GATEWAY_CONNECT_TIMEOUT_S,
                                     read_timeout=poll_s)
    except GatewayUnavailable as e:
        return _fallback(str(e))

    state = RunState()
    state.gateway_backend = True
    state.gateway_fallback = False
    state.gateway_fallback_reason = None
    raw_events = []
    response_id = None
    killed_reason = None
    exit_code = None
    # wq#2275/wq#2290, per TICKET since wq#2404: labels already alarmed and matched
    # strings already screened. This process outlives the attempt; so does the dedup.
    _mask = mask_state(ticket_id)
    mask_alarmed, burn_seen = _mask["alarmed"], _mask["seen"]
    live_secrets = burn_filter.live_secret_values(env)
    repo_roots = None    # wq#2319: resolved on the first mask hit (screen_mask_hits)
    start = time.time()
    deadline = start + wall_clock_s
    last_event = start

    if resp.status >= 400:
        # despag rejected the request outright (e.g. its own admission gate, or a
        # malformed body) — never a fallback case. A 429 lumen_max_window_exhausted/
        # quota_exhausted IS the LIMIT verdict (admission refused before any turn
        # ran); every other 4xx has no result frame, so classify() correctly falls
        # to LAUNCH_FAILURE (saw_init stays False).
        raw = resp.read()
        try:
            err = (json.loads(raw or b"{}").get("error")) or {}
        except json.JSONDecodeError:
            err = {}
        raw_events.append({"type": "despag_error", "status": resp.status, "error": err})
        if resp.status == 429:
            state.saw_limit = True
            state.latest_resets_at = (err.get("detail") or {}).get("resets_at")
        return state, 1, raw_events, None, 0.0, None

    with resp:
        buf = []
        while True:
            now = time.time()
            if now >= deadline:
                killed_reason = "wall_clock"
                break
            if now - last_event >= frame_gap_s:
                killed_reason = "inactivity"
                break
            try:
                line = resp.readline()
            except (TimeoutError, OSError):
                continue
            if not line:
                break
            text = line.decode("utf-8", "replace").rstrip("\n")
            if text.startswith(":"):
                last_event = time.time()   # SSE keep-alive: still evidence of life
                continue
            if not text:
                if buf:
                    raw = "".join(buf)
                    buf = []
                    if raw.startswith("data:"):
                        last_event = time.time()
                        # wq#2275 (T6): same pre-parse masking as run_child() — the
                        # gateway stream feeds the identical artefact chain.
                        raw_payload = raw[len("data:"):].strip()
                        payload, mask_hits = masking.mask_text(raw_payload)
                        if mask_hits and repo_roots is None:
                            repo_roots = burn_filter.provenance_roots(
                                cwd, granted_dirs(cmd))
                        screen_stream_hits(mask_hits, raw_payload, mask_alarmed,
                                           burn_seen, live_secrets, repo_roots,
                                           ticket_id, attempt,
                                           facing=gateway_facing_text,
                                           session_id=state.session_id)
                        try:
                            ev = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        raw_events.append(ev)
                        if response_id is None:
                            response_id = ((ev.get("response") or {}).get("id"))
                        done, ev_exit_code = _observe_gateway_event(ev, state, loop_tracker)
                        if done:
                            exit_code = ev_exit_code
                            break
                continue
            buf.append(text)

    if killed_reason in ("wall_clock", "inactivity"):
        if response_id is not None:
            _gateway_cancel(response_id, connect_timeout=GATEWAY_CONNECT_TIMEOUT_S)
        if killed_reason == "inactivity":
            state.hung = True
            state.silent_gap_s = round(time.time() - last_event, 1)
        # No local process to normalize a signal exit from — 143 (SIGTERM
        # convention, RELIABILITY_SPEC.md §D) is the honest equivalent of "this
        # watchdog stopped it", matching what a direct spawn's own
        # _kill_proc_tree(SIGTERM) would have reported.
        exit_code = 143

    return state, exit_code, raw_events, killed_reason, 0.0, None


# wq#4707: the CLI feeds a finished background task back to the agent as a new turn,
# so a result frame emitted while one is outstanding is "turn over", not "run over".
#
# wq#4728: a completion is read only from the CLI's own notification channel, never from
# arbitrary tool output. The authoritative signal is a structured frame —
# {"type": "system", "subtype": "task_notification", "task_id": ..., "status":
# "completed"|"failed"|"stopped"} — emitted exactly once per task at its terminal state
# (anthropics/claude-code#104943; CodeBuddy's stdio protocol doc, aligned to Claude Code
# 2.1.220, states it outright: task_notification is the authoritative terminal signal and
# completion must not be derived from other events). The text turn the CLI hands back
# (<task-notification>…) is accepted too, since that is the shape wq4686's captured
# frames have. What is no longer accepted is the same phrasing inside an unrelated
# tool_result: matching any user-frame text meant that reading a file, a test log or a
# ticket that merely says "background task … completed" cleared a real pending task and
# silently put the session back on the short grace with work still outstanding (wq#4726).
_BG_NOTIFY_SUBTYPE = "task_notification"
_BG_TERMINAL_STATUSES = ("", "completed", "success", "failed", "error", "stopped",
                         "killed", "cancelled", "canceled", "timed_out", "timeout")
_BG_NOTIFY_RE = re.compile(r"<\s*/?\s*task[-_]notification\b", re.I)
# Fallback phrasings for the text channel, so a change in the wrapper does not blind the
# detector. Only ever applied to a CLI-injected turn or to the tracked task's own
# tool_result — see BackgroundTaskWatch._is_completion_text.
_BG_COMPLETE_RE = re.compile(
    r"\btask\b[^\n]{0,80}\b(?:completed|finished|failed|exited|has finished)|"
    r"background (?:task|command|shell|process|run)[^\n]{0,80}"
    r"(?:completed|finished|failed|exited|is done)",
    re.I)
# The CLI's own auto-background of a Bash call that outran the tool timeout — the agent
# never asked for it, so it only ever shows up in the tool_result text (wq4239).
_BG_AUTO_RE = re.compile(
    r"\b(?:moved|sent|pushed|continues?|now running|running|runs)\b[^\n]{0,40}"
    r"\b(?:in|to) the background\b|"
    r"running in background with id",
    re.I)
# Tools whose work is backgrounded unless the call says otherwise (the Agent tool
# reports back through the same completion notification a background Bash does).
_BG_BY_DEFAULT_TOOLS = ("Agent", "Task")


class BackgroundTaskWatch:
    """Tracks whether this session has a background task still outstanding.

    Starts are read from the agent's own tool_use input (run_in_background on Bash, or
    any Agent/Task call that did not opt out) plus the CLI's auto-background of an
    over-long Bash call. Completions are read from the CLI's notification channel only:
    a system/task_notification frame, the <task-notification> turn it hands the agent, or
    the tracked call's own tool_result — never from an unrelated tool_result, and never
    while nothing is pending (wq#4728).

    Detection is deliberately generous on the start side and strict on the completion
    side: over-counting a start only stretches the post-result grace for a process that
    is still alive (and POST_RESULT_BG_GRACE_S still bounds it), while under-counting one
    — or clearing it on text that was never a completion — is what killed 60 workers
    mid-ticket.
    """

    def __init__(self):
        self.started = 0
        self.completed = 0
        self._bg_ids = set()   # tool_use ids already counted, so their own
                               # "running in the background" result is not a 2nd start

    @property
    def pending(self):
        return max(0, self.started - self.completed)

    def observe(self, frame):
        ftype = frame.get("type")
        if ftype == "system":
            # The authoritative terminal signal, one per task, no text matching at all.
            if (frame.get("subtype") == _BG_NOTIFY_SUBTYPE
                    and str(frame.get("status") or "").lower() in _BG_TERMINAL_STATUSES):
                self._complete()
            return
        msg = frame.get("message")
        content = (msg.get("content") if isinstance(msg, dict) else None) or []
        if not isinstance(content, list):
            return
        if ftype == "assistant":
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                    continue
                ti = b.get("input") if isinstance(b.get("input"), dict) else {}
                bg = ti.get("run_in_background")
                if bg is True or (bg is None and b.get("name") in _BG_BY_DEFAULT_TOOLS):
                    self.started += 1
                    self._bg_ids.add(b.get("id"))
        elif ftype == "user":
            for b in content:
                if not isinstance(b, dict):
                    continue
                is_result = b.get("type") == "tool_result"
                text = "\n".join(_content_text(b.get("content") if is_result else [b]))
                if not text:
                    continue
                if self._is_completion_text(b, is_result, text):
                    self._complete()
                elif (is_result
                        and b.get("tool_use_id") not in self._bg_ids
                        and _BG_AUTO_RE.search(text)):
                    self.started += 1
                    self._bg_ids.add(b.get("tool_use_id"))

    def _complete(self):
        """Count a completion only against work actually outstanding: pending is
        started - completed, so a stray match while nothing is pending would bank credit
        that a later real start then silently spends (wq#4728 — a worker's own dispatch
        prompt can quote notification text)."""
        if self.pending:
            self.completed += 1

    def _is_completion_text(self, block, is_result, text):
        if not self.pending:
            return False
        if is_result:
            # Ordinary foreground output cannot speak for a background task — only the
            # tracked call's own result can. Mirrors the start side's `tool_use_id not
            # in self._bg_ids` gate, which the completion side was missing (wq#4726).
            if block.get("tool_use_id") not in self._bg_ids:
                return False
        return bool(_BG_NOTIFY_RE.search(text) or _BG_COMPLETE_RE.search(text))


def run_child(cmd, cwd, env, max_turns, wall_clock_s, ticket_id, attempt,
              frame_gap_s=None, post_result_grace_s=None, loop_tracker=None,
              first_frame_s=None, post_result_bg_grace_s=None):
    """Spawn, parse stream-json, enforce watchdogs. Returns (RunState, exit_code,
    raw_events, killed_reason, cpu_s, pid).

    RELIABILITY_SPEC.md §E: the frame-gap watchdog (frame_gap_s, default
    INACTIVITY_TIMEOUT_S) is PRIMARY liveness — no frames for that long with the
    process still alive is a stall (§C8), SIGTERM'd here and resumable by the caller.
    Wall-clock (wall_clock_s) is the secondary ceiling. Neither is CPU-based: CPU time
    is corroboration only (returned as cpu_s for the attempt record / LAUNCH_FAILURE
    triage), never a watchdog trigger — a child can legitimately burn near-zero CPU
    while waiting on a slow tool call.

    wq#2128: first_frame_s splits the window. Until the first model frame (anything but
    the CLI's system/init and retry chatter, see charging.is_model_frame) the run is held
    to first_frame_s measured from spawn; frame_gap_s only applies after it. None keeps the
    single pre-split window (legacy mode, and callers that pass an explicit frame_gap_s).

    wq#4707: post_result_bg_grace_s replaces post_result_grace_s for as long as the
    session has a background task outstanding (BackgroundTaskWatch), because the CLI is
    then holding the session open to deliver that task's completion to the agent — the
    one case where a terminal result does not mean the run is over.
    """
    frame_gap_s = frame_gap_s if frame_gap_s is not None else INACTIVITY_TIMEOUT_S
    post_result_grace_s = (post_result_grace_s if post_result_grace_s is not None
                            else POST_RESULT_GRACE_S)
    post_result_bg_grace_s = (post_result_bg_grace_s if post_result_bg_grace_s is not None
                              else POST_RESULT_BG_GRACE_S)
    cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    # ISSUE-SOLUTIONS-fable.md #18: start_new_session=True puts the child in its own
    # session/process group so a signal delivered to fworker's process group (e.g. an
    # interactive connector recycle killing the whole group) does not take the `claude`
    # child down with it — the same detach discipline dispatch-batch applies one level up.
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, start_new_session=True,
    )
    # wq#1894: only meaningful when the caller has set a matching context via
    # set_sigterm_context() (do_attempt's plain path) — fire-mode's run_fire_child()
    # never does, so this is a no-op write there.
    _SIGTERM_CTX["proc"] = proc
    q = queue.Queue()
    t = threading.Thread(target=_reader_thread, args=(proc.stdout, q), daemon=True)
    t.start()

    state = RunState()
    raw_events = []
    start = time.time()
    last_event = start
    last_heartbeat = start
    deadline = start + wall_clock_s
    killed_reason = None
    turn_backstop = max_turns + TURN_BACKSTOP_BUFFER
    result_seen_at = None
    bg_watch = BackgroundTaskWatch()   # wq#4707
    done_seen_at = None          # wq#992-C: when the ticket itself first read `done`
    last_done_poll = start       # throttle the wq status poll to DONE_POLL_INTERVAL_S
    # short poll interval when either watchdog window is itself short (tests), so
    # detection latency stays a fraction of the window instead of the flat 5s floor.
    poll_timeout = max(0.25, min(5, frame_gap_s, post_result_grace_s) / 4)
    first_frame_seen = first_frame_s is None
    # wq#2404: the ticket's sets, not the attempt's — see mask_state().
    _mask = mask_state(ticket_id)
    mask_alarmed, burn_seen = _mask["alarmed"], _mask["seen"]
    live_secrets = burn_filter.live_secret_values(env)
    repo_roots = None    # wq#2319: resolved on the first mask hit (screen_mask_hits)

    while True:
        now = time.time()
        if now >= deadline:
            killed_reason = "wall_clock"
            break
        # §C9 SUCCESS-BUT-STUCK: once a terminal result has landed, don't wait out the
        # full frame-gap window — a much shorter grace covers "process won't exit".
        # wq#4707: unless a background task the agent started has yet to report back,
        # in which case the session is legitimately parked waiting to be re-invoked
        # with its completion and gets the longer window.
        grace_s = (max(post_result_grace_s, post_result_bg_grace_s)
                   if bg_watch.pending else post_result_grace_s)
        if result_seen_at is not None and now - result_seen_at >= grace_s:
            killed_reason = "post_result_stuck"
            if bg_watch.pending:
                log(f"post_result_stuck: {bg_watch.pending} background task(s) never "
                    f"reported back within {int(grace_s)}s — stopping the session")
            break
        # wq#992-C: the ticket itself reached `done` mid-run. Give a short grace for a
        # trailing episode-commit / final message, then stop the lingering session so it
        # can't keep taking agentic turns (which frame-gap wouldn't catch and the
        # turn-backstop would misclassify TURNS). Checked BEFORE frame-gap so a
        # finished-but-quiet agent is recognized as done, not a hang.
        if done_seen_at is not None and now - done_seen_at >= POST_DONE_GRACE_S:
            killed_reason = "ticket_done"
            break
        if not first_frame_seen and now - start >= first_frame_s:
            killed_reason = "first_frame"
            break
        if first_frame_seen and now - last_event >= frame_gap_s:
            killed_reason = "inactivity"
            break
        if now - last_heartbeat >= HEARTBEAT_INTERVAL_S:
            wq_heartbeat(ticket_id)
            last_heartbeat = now
        # wq#992-C: throttled ticket-status poll (one `wq show` per DONE_POLL_INTERVAL_S).
        # Best-effort: a failed poll just leaves done_seen_at unset and the frame-gap /
        # wall-clock watchdogs still bound the run.
        if done_seen_at is None and now - last_done_poll >= DONE_POLL_INTERVAL_S:
            last_done_poll = now
            try:
                if wq_show(ticket_id).get("status") == "done":
                    done_seen_at = time.time()
                    log(f"ticket_done: wq#{ticket_id} reached done mid-run; grace "
                        f"{POST_DONE_GRACE_S}s then stopping the session.")
            except Exception:
                pass
        try:
            kind, payload = q.get(timeout=poll_timeout)
        except queue.Empty:
            continue
        if kind == "eof":
            break
        if kind == "reader_error":
            killed_reason = "reader_error"
            break
        line = payload.strip()
        last_event = time.time()
        if not line:
            continue
        # wq#2275 (T6): mask BEFORE parse — raw_events/state and everything derived
        # from them (attempt notes, episode store, failure.json, ntfy, PR text) only
        # ever see the masked line. Masking stays whole-line: this pipe carries the
        # child's stderr too, and an artefact must not keep a credential whatever
        # channel printed it. The ALARM is narrower (wq#2393, screen_stream_hits) —
        # a hit only pages once the value is in text the model actually received,
        # since there the CLI has no lever to rewrite it and it is burned.
        raw_line = line
        line, mask_hits = masking.mask_text(line)
        if mask_hits and repo_roots is None:
            repo_roots = burn_filter.provenance_roots(cwd, granted_dirs(cmd))
        screen_stream_hits(mask_hits, raw_line, mask_alarmed, burn_seen,
                           live_secrets, repo_roots, ticket_id, attempt,
                           session_id=state.session_id)
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            raw_events.append({"type": "unparsed", "raw": line[:500]})
            continue
        raw_events.append(frame)
        state.observe(frame)
        bg_watch.observe(frame)     # wq#4707
        # wq#4707: a real turn after a result means the session resumed work (the
        # background task's completion landed and the agent is acting on it), so the
        # grace must rearm from the NEXT result rather than keep counting from the
        # stale one. Without this the countdown opened by the first result killed the
        # session 20s later however much live work followed — the ~25s, 4-7 turn
        # attempt 2 of wq4686/wq4239, scored FALSE_COMPLETE with an empty result.
        if result_seen_at is not None and frame.get("type") in ("assistant", "user"):
            result_seen_at = None
        if not first_frame_seen and charging.is_model_frame(frame):
            first_frame_seen = True
        # Layer 2 (TIERED_INTELLIGENCE_DESIGN.md, wq#985): loop breaker. This is
        # what Frank had to do manually on the Miko — the harness does it for him.
        if loop_tracker is not None:
            loop_reason = loop_tracker.observe(frame)
            if loop_reason:
                killed_reason = "loop_break"
                log(f"LOOP BREAK: {loop_reason} — stopping session, handoff to follow")
                break
            if loop_tracker.steer:
                # wq#2138: a -p child has no stdin, so the one steer rides a --resume.
                killed_reason = "steer"
                log("STEER: CLI shell refusal — stopping session to resume it with the hint")
                break
            if loop_tracker.ceiling:
                # wq#4634: a transport ceiling (Bash tool timeout / supergateway reap,
                # wq#3264) killed a running command. The model cannot fix this by
                # retrying, so stop the session; do_attempt re-runs the command under
                # watchdog and resumes with the verdict.
                killed_reason = "tool_ceiling"
                log("TOOL CEILING: transport killed a running command — stopping "
                    "session to re-issue it via watchdog and resume with the verdict")
                break
        # wq#1819: the wire nests status/resetsAt/rateLimitType under
        # rate_limit_info; the flat read never matched, so no rejection ever
        # reached the governor. Unwrap and hand the governor the inner dict.
        if frame.get("type") == "rate_limit_event":
            rl_info = frame.get("rate_limit_info") or frame
            if rl_info.get("status") == "rejected":
                report_limit_event(rl_info)
        if frame.get("type") == "result":
            result_seen_at = time.time()
        if state.turns >= turn_backstop:
            killed_reason = "turns_exceeded"
            break

    state.bg_tasks_started = bg_watch.started      # wq#4707, observability only
    state.bg_tasks_pending = bg_watch.pending
    if killed_reason:
        if killed_reason in ("inactivity", "first_frame"):
            state.hung = True
            state.first_frame_timeout = killed_reason == "first_frame"
            if killed_reason == "inactivity":
                state.silent_gap_s = round(time.time() - last_event, 1)
        elif killed_reason == "turns_exceeded":
            state.turns_exceeded = True
        _kill_proc_tree(proc)
    else:
        proc.wait()
    # wq#1894: proc is reaped either way by this point — clear the SIGTERM handler's
    # reference so it never re-signals an already-dead pid.
    if _SIGTERM_CTX["proc"] is proc:
        _SIGTERM_CTX["proc"] = None

    exit_code = proc.returncode
    if exit_code is not None and exit_code < 0:
        # POSIX: a signal-terminated child reports returncode as -signum. Normalize to
        # the shell convention (128+signum) so RELIABILITY_SPEC.md §C5/§C6's exit
        # 143/137 checks see the same numbers a `claude agents`/journal inspection
        # would (SIGTERM=143, SIGKILL=137).
        exit_code = 128 - exit_code

    # §E: CPU corroboration-only — RUSAGE_CHILDREN is cumulative across every child this
    # process has ever reaped, so a before/after delta isolates just this one. Never fed
    # into classify(); attached to the attempt record for LAUNCH_FAILURE/CRASH triage
    # ("tiny CPU corroborates" per §C3) and left for a human/orchestrator to read.
    cpu_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu_s = ((cpu_after.ru_utime + cpu_after.ru_stime)
             - (cpu_before.ru_utime + cpu_before.ru_stime))

    # wq#900 (c): post-run backstop — no child leaves a PRIMARY tree off its default
    # branch. guard.sh should have denied the checkout; if it was evaded, restore
    # (clean trees only) and alert within seconds of the child exiting instead of
    # waiting for the timer. Never let this check fail the attempt itself.
    try:
        for d in primary_tree_guard.check_and_restore_all(
                context=f"post-run wq#{ticket_id} attempt {attempt}", log=log):
            log(f"PRIMARY DRIFT after wq#{ticket_id} attempt {attempt}: "
                f"{d['repo']} on {d['head']!r} restored={d['restored']}")
    except Exception as e:
        log(f"WARN: wq#900 post-run primary tree check failed: {e}")

    return state, exit_code, raw_events, killed_reason, cpu_s, proc.pid


# ---------------------------------------------------------------------------
# wq#4634: re-issue a ceiling-killed shell command via watchdog, then resume.
# Design: design/wq4634_tool_ceiling_watchdog_reissue.md. Detection lives in
# action_gate (CEILING_KILL_*_RE / LoopTracker.ceiling); this is the reissue half.
# ---------------------------------------------------------------------------

WATCHDOG_BIN = os.environ.get("FOREMAN_WATCHDOG_BIN", "/home/lumen/bin/watchdog")
REISSUE_TIMEOUT_MIN = _envint("FOREMAN_REISSUE_TIMEOUT_MIN", 30)

CEILING_RESUME_TAIL = (
    "Your previous shell command was killed by a transport ceiling (Bash tool "
    "timeout / supergateway group reap, wq#3264), not by the command itself, and the "
    "harness intervened. If a verdict line appears above, that is the command's true "
    "result: read the tail file it names and continue the ticket from there. Do NOT "
    "re-run the command bare — the same ceiling will kill it again. If you must run "
    "it (or any other long command) again, wrap it: /home/lumen/bin/watchdog "
    "--no-send --label <name> -- <cmd>, then poll /tmp/watchdog-<name>.result.json.")


def reissue_ceiling_killed(ticket_id, attempt, ceiling, worktree,
                           timeout_min=None, poll_s=5.0, grace_s=120):
    """Re-run a ceiling-killed command detached under watchdog (setsid survives both
    wq#3264 kill modes), wait for its result.json, and return the verdict line that
    rides the --resume back into the session. Every failure path still returns a
    resume line — the session always learns what happened to its command."""
    label = f"fw-{ticket_id}-a{attempt}"
    cmd = (ceiling or {}).get("command") or ""
    if not cmd:
        return (f"[watchdog:{label}] SKIPPED — a ceiling kill was detected but the "
                f"command could not be recovered from the stream. {CEILING_RESUME_TAIL}")
    # The command ran once under the PreToolUse gate, but fworker re-running it
    # directly bypasses hooks — re-classify, and refuse RED rather than execute it.
    try:
        gate_verdict = action_gate.classify_bash(
            cmd, action_gate.load_compiled_rules())[0]
    except Exception as e:
        log(f"reissue: classify_bash failed ({e}) — failing closed")
        gate_verdict = "RED"
    if gate_verdict == "RED":
        return (f"[watchdog:{label}] REFUSED — the killed command is RED under the "
                f"action gate; fworker will not re-run it outside the hook. "
                f"{CEILING_RESUME_TAIL}")
    tmin = timeout_min if timeout_min is not None else REISSUE_TIMEOUT_MIN
    result_path = f"/tmp/watchdog-{label}.result.json"
    try:
        spawn = subprocess.run(
            [WATCHDOG_BIN, "--no-send", "--label", label, "--cwd", worktree,
             "-t", str(tmin), "--", cmd],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return (f"[watchdog:{label}] SPAWN-FAILED ({e.__class__.__name__}) — the "
                f"command was NOT re-run. {CEILING_RESUME_TAIL}")
    if spawn.returncode != 0:
        err, _ = masking.mask_text((spawn.stdout + spawn.stderr).strip()[-300:])
        return (f"[watchdog:{label}] SPAWN-FAILED rc={spawn.returncode}: {err} — the "
                f"command was NOT re-run. {CEILING_RESUME_TAIL}")
    log(f"reissue: watchdog {label} spawned (timeout {tmin}m) — waiting for "
        f"{result_path}")
    # result.json is written unconditionally at verdict time (before --callback would
    # fire), so the file IS the synchronization artifact; fworker runs no HTTP hook.
    deadline = time.time() + tmin * 60 + grace_s
    last_hb = time.time()
    while time.time() < deadline:
        if time.time() - last_hb >= HEARTBEAT_INTERVAL_S:
            wq_heartbeat(ticket_id)
            last_hb = time.time()
        if os.path.exists(result_path):
            try:
                with open(result_path) as f:
                    res = json.load(f)
            except (OSError, json.JSONDecodeError):
                time.sleep(min(poll_s, 1.0))   # mid-write; re-read next pass
                continue
            line = (f"[watchdog:{label}] {res.get('status') or 'UNKNOWN'} — "
                    f"{res.get('note') or 'no note'} — output tail: "
                    f"{res.get('tail') or res.get('out') or '(none)'}")
            masked, _ = masking.mask_text(line)
            log(f"reissue: verdict {res.get('status')} after "
                f"{res.get('duration_s')}s for {label}")
            return f"{masked} {CEILING_RESUME_TAIL}"
        time.sleep(poll_s)
    return (f"[watchdog:{label}] NO-VERDICT — the re-run is still in flight after "
            f"{tmin}m+grace; its verdict will land at {result_path} (output: "
            f"/tmp/watchdog-{label}.out). Poll that file. {CEILING_RESUME_TAIL}")


# ---------------------------------------------------------------------------
# wq#4289: aider runtime, the local path for after the Anthropic promo credits
# expire (2026-11-09)
#
# `fworker run --runtime aider` (or FOREMAN_RUNTIME=aider) runs the attempt's turn
# through aider against the local lumen/code model instead of `claude -p`. Everything
# around the turn is the ordinary path: make_worktree()'s linked worktree, run_child()'s
# watchdogs and masking, do_attempt()'s artifact gate and reconcile(). aider only edits
# files (--no-auto-commits); the harness then does what a claude worker does at the end
# of its session: lumen-pr commit/open/land, then `wq done` citing the PR. The ordinary
# SUCCESS branch files the verify-pass that reviews the local model's diff.
# ---------------------------------------------------------------------------

RUNTIMES = ("claude", "aider")
AIDER_BIN = os.environ.get("FOREMAN_AIDER_BIN", "/home/lumen/.local/bin/aider")
AIDER_MODEL = os.environ.get("FOREMAN_AIDER_MODEL", "openai/lumen/code")
# instinct 4: the backend URL and key live in this 0600 file and reach the child's env
# only, never argv or the log.
AIDER_ENV_FILE = os.environ.get("FOREMAN_AIDER_ENV_FILE", "/etc/lumen-aider-fworker.env")
AIDER_ENV_KEYS = ("OPENAI_API_BASE", "OPENAI_API_KEY")
LUMEN_PR_BIN = os.environ.get("FOREMAN_LUMEN_PR_BIN", "/home/lumen/bin/lumen-pr")
LUMEN_PR_TIMEOUT_S = _envint("FOREMAN_AIDER_LAND_TIMEOUT_S", 1200)
CONV_TYPES = ("feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci",
              "chore", "revert")


def runtime_of(args):
    """--runtime, else FOREMAN_RUNTIME, else claude. An unknown value is a config error."""
    rt = getattr(args, "runtime", None) or os.environ.get("FOREMAN_RUNTIME") or "claude"
    if rt not in RUNTIMES:
        raise ValueError(f"unknown fworker runtime {rt!r} (one of {', '.join(RUNTIMES)})")
    return rt


def aider_backend_env(env, path=None):
    """The child env plus OPENAI_API_BASE/OPENAI_API_KEY from the aider env file (the
    same file bin/aider-fworker sources). Raises OSError if it can't be read."""
    out = dict(env)
    with open(path or AIDER_ENV_FILE) as f:
        for line in f:
            k, sep, v = line.strip().removeprefix("export ").partition("=")
            if sep and k in AIDER_ENV_KEYS:
                out[k] = v.strip().strip("\"'")
    return out


def build_aider_cmd(ticket, history_dir, attempt, model=None, aider_bin=None):
    """The aider argv. The message is the ticket itself, not the claude worker prompt:
    that prompt is mostly wq/lumen-pr/tool rules aider can't act on. History files go to
    the run dir so nothing aider writes for itself lands in the worktree's diff."""
    message = (f"Implement this ticket: {ticket.get('title', '')}\n\n"
               f"{ticket.get('detail', '')}\n\n"
               "Edit the files this needs and nothing else. Do not commit; the harness "
               "commits and opens the pull request.")
    return [aider_bin or AIDER_BIN, "--model", model or AIDER_MODEL, "--message", message,
            "--yes-always", "--no-auto-commits", "--no-dirty-commits", "--no-gitignore",
            "--no-show-model-warnings", "--no-check-update", "--no-analytics",
            "--no-pretty", "--no-stream", "--no-fancy-input",
            "--no-suggest-shell-commands", "--no-restore-chat-history",
            "--chat-history-file", os.path.join(history_dir, f"aider-chat-a{attempt}.md"),
            "--input-history-file", os.path.join(history_dir, f"aider-input-a{attempt}")]


def aider_changed_paths(worktree):
    """Paths aider changed, minus aider's own `.aider*` cache files (the repo-map tags
    cache always lands in the repo root). Staged by name: `git add -A` is banned (§10.8)."""
    p = _git(worktree, "status", "--porcelain", "-z", "--untracked-files=all")
    if p.returncode != 0:
        raise RuntimeError(f"git status failed in {worktree}: {p.stderr.strip()}")
    paths = []
    entries = p.stdout.split("\0")
    i = 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if len(e) < 4:
            continue
        xy, path = e[:2], e[3:]
        if "R" in xy or "C" in xy:
            i += 1   # -z puts a rename's source path in the next entry
        if not any(part.startswith(".aider") for part in path.split("/")):
            paths.append(path)
    return paths


def aider_commit_subject(branch, title):
    """A Conventional Commit subject lumen-pr's lint_subject accepts: the branch's type,
    the ticket title without its wq#/FOLLOW-UP markers, at most 60 chars, no period."""
    ctype = branch.split("/", 1)[0] if "/" in branch else "chore"
    if ctype not in CONV_TYPES:
        ctype = "chore"
    t = re.sub(r"(?i)\b(follow-up\s+)?wq#\d+:?\s*|board:\s*", "", title or "")
    t = re.sub(r"\s+", " ", t).strip(" :-.") or "apply ticket change"
    t = t[0].lower() + t[1:]
    return f"{ctype}: {t[:60].rstrip(' .')}"


def _lumen_pr(env, *args):
    p = subprocess.run([LUMEN_PR_BIN, *args], capture_output=True, text=True, env=env,
                       timeout=LUMEN_PR_TIMEOUT_S)
    if p.returncode != 0:
        out, _ = masking.mask_text(((p.stdout or "") + (p.stderr or "")).strip())
        raise RuntimeError(f"lumen-pr {args[0]} failed (exit {p.returncode}): {out[-800:]}")
    return p.stdout


def land_aider_changes(worktree, ticket_id, ticket, attempt, model, env):
    """Commit aider's edits, open and land the PR, close the ticket citing it. Returns
    the PR url, or None when aider changed nothing. Raises RuntimeError on a refusal."""
    paths = aider_changed_paths(worktree)
    if not paths:
        return None
    p = _git(worktree, "add", "--", *paths)
    if p.returncode != 0:
        raise RuntimeError(f"git add failed: {p.stderr.strip()}")
    branch = _git(worktree, "branch", "--show-current").stdout.strip()
    subject = aider_commit_subject(branch, ticket.get("title"))
    _lumen_pr(env, "commit", worktree, subject, "--refs", f"wq#{ticket_id}")
    body_path = os.path.join(run_dir(ticket_id), f"aider-pr-body-a{attempt}.md")
    with open(body_path, "w") as f:
        f.write(f"## What\n\n{ticket.get('title', '')}\n\n"
                f"## Why\n\nRefs: wq#{ticket_id}\n\n"
                f"## How\n\nEdited by aider ({model}) under fworker's aider runtime "
                f"(wq#4289), attempt {attempt}. Files: "
                f"{', '.join(paths[:20])}{' ...' if len(paths) > 20 else ''}\n\n"
                f"## Testing\n\nThe fworker evidence and artifact gates run after landing, "
                f"and the verify-pass ticket reviews this diff.\n\n"
                f"## Risk / rollback\n\nLocal-model output. Revert this PR's merge commit.\n")
    out = _lumen_pr(env, "open", worktree, "--title", subject, "--body-file", body_path)
    m = re.search(r"https?://\S+/pulls?/(\d+)", out)
    if not m:
        raise RuntimeError(f"lumen-pr open printed no PR url: {out.strip()[-300:]}")
    pr_url, pr_number = m.group(0), m.group(1)
    _lumen_pr(env, "land", worktree, pr_number)
    wq_done(ticket_id, f"{pr_url} landed: aider runtime ({model}) attempt {attempt}, "
                       f"{len(paths)} file(s) changed")
    return pr_url


def run_child_aider(cmd, cwd, env, max_turns, wall_clock_s, ticket_id, attempt,
                    frame_gap_s=None, post_result_grace_s=None, loop_tracker=None,
                    first_frame_s=None, post_result_bg_grace_s=None):
    """aider sibling of run_child(), same signature and 6-tuple return. `cmd` (the claude
    argv) is ignored: aider gets build_aider_cmd()'s ticket message instead.

    aider prints plain text, never stream-json, so the verdict rides a synthesized result
    frame: success only once the PR has landed and the ticket is done, an error result
    otherwise. aider exits 0 on backend/API errors (bin/aider-fworker note), so "no file
    changed" is the check that catches those, not the exit code."""
    state = RunState()
    state.runtime = "aider"
    ticket = wq_show(ticket_id)
    model = AIDER_MODEL
    try:
        aider_env = aider_backend_env(env)
    except OSError as e:
        log(f"wq#{ticket_id} attempt {attempt}: aider env file unreadable: {e}")
        return state, 1, [{"type": "aider_error", "error": str(e)}], None, 0.0, None
    aider_cmd = build_aider_cmd(ticket, run_dir(ticket_id), attempt, model=model)
    log(f"wq#{ticket_id} attempt {attempt}: aider runtime, model {model}")
    # first_frame_s=None: aider never emits a model frame, so the single frame-gap window
    # (any output line counts as life) is the liveness check.
    state, exit_code, raw_events, killed_reason, cpu_s, pid = run_child(
        aider_cmd, cwd, aider_env, max_turns, wall_clock_s, ticket_id, attempt,
        frame_gap_s=frame_gap_s, post_result_grace_s=post_result_grace_s,
        loop_tracker=None, first_frame_s=None,
        post_result_bg_grace_s=post_result_bg_grace_s)
    state.runtime = "aider"
    if killed_reason or exit_code != 0:
        return state, exit_code, raw_events, killed_reason, cpu_s, pid

    try:
        pr_url = land_aider_changes(cwd, ticket_id, ticket, attempt, model, env)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        pr_url, failure = None, f"aider edits could not be landed: {e}"
    else:
        failure = (None if pr_url else
                   f"aider exited 0 but changed no files (model {model}); aider exits 0 "
                   f"on backend errors, see the unparsed lines in this attempt's events")
    if failure:
        log(f"wq#{ticket_id} attempt {attempt}: {failure}")
        frame = {"type": "result", "subtype": "error_during_execution", "is_error": True,
                 "result": failure}
    else:
        frame = {"type": "result", "subtype": "success", "is_error": False,
                 "result": f"aider runtime landed {pr_url}\nTICKET_CLOSED"}
    raw_events.append(frame)
    state.observe(frame)
    return state, exit_code, raw_events, killed_reason, cpu_s, pid


# ---------------------------------------------------------------------------
# main attempt orchestration
# ---------------------------------------------------------------------------

def _sub_instincts_tier0(tmpl):
    """wq#959: tier-0 hard constraints go into every fworker prompt (R1 §6 —
    workers previously received zero instinct text). Fail-open to the bare
    template: a broken/missing/schema-1 instincts.yaml must never stop a spawn."""
    try:
        import instincts_render
        block = instincts_render.tier0_block(warn=log)
    except Exception:
        block = ""
    if block:
        return tmpl.replace("{{INSTINCTS_TIER0}}", block)
    return tmpl.replace("{{INSTINCTS_TIER0}}\n\n", "").replace("{{INSTINCTS_TIER0}}", "")


def load_worker_prompt(ticket_id, ticket, worktree, prior_notes_path=None,
                        acceptance_doc=None, rework=None, archetype=None):
    tmpl_path = os.path.join(HARNESS_DIR, "prompts", "worker.md")
    with open(tmpl_path) as f:
        tmpl = _sub_instincts_tier0(f.read())
    # ISSUE-LOG #17: the done-contract (§1a) and, on a rejected attempt, the structured
    # rework block (§1d) — which goes at the TOP, ahead of everything else, because its
    # first rule is that the worker's first action is to re-run the repro commands.
    contract = acceptance_mod.contract_block(acceptance_doc, worktree) if acceptance_doc else ""
    rework_text = acceptance_mod.rework_block(rework) if rework else ""
    # wq#4716: per-archetype instructions from agents.yaml (research-synthesis's web recipe).
    addendum = (_AGENTS.get(archetype).prompt_addendum.strip()
                if archetype in _AGENTS else "")
    for placeholder, block in (("{{ACCEPTANCE_CONTRACT}}", contract),
                                ("{{ARCHETYPE_ADDENDUM}}", addendum),
                                ("{{REWORK}}", rework_text)):
        tmpl = (tmpl.replace(placeholder, block) if block
                else tmpl.replace(placeholder + "\n\n", "").replace(placeholder, ""))
    prior_notes = ""
    if prior_notes_path and os.path.exists(prior_notes_path):
        with open(prior_notes_path) as f:
            prior_notes = f.read()
    memory_block = distilled_memory_block(ticket_id, ticket)
    if not memory_block:
        tmpl = tmpl.replace("{{DISTILLED_MEMORY}}\n\n", "").replace(
            "{{DISTILLED_MEMORY}}", "")
    return (tmpl
            .replace("{{TICKET_ID}}", str(ticket_id))
            .replace("{{TITLE}}", ticket.get("title", ""))
            .replace("{{DETAIL}}", ticket.get("detail", ""))
            .replace("{{WORKTREE}}", worktree)
            .replace("{{DISTILLED_MEMORY}}", memory_block)
            .replace("{{PRIOR_ATTEMPT_NOTES}}", prior_notes))


def local_prompt_template(model, archetype):
    """wq#2128: path of the slim local template for this dispatch, or None to use
    worker.md. Only a local-rung model on a local-trusted archetype gets one, and
    FOREMAN_CHARGE_MODE=legacy always gets worker.md."""
    if charging.legacy_mode() or not is_local_rung(model):
        return None
    if archetype not in LOCAL_ALLOWED_ARCHETYPES:
        return None
    path = os.path.join(HARNESS_DIR, "prompts", f"local_{archetype}.md")
    return path if os.path.exists(path) else None


# wq#2455: this branch bypasses worker.md, so worker.md's DOCTRINE block (wq#2439) does
# NOT reach a local-rung dispatch — each local_*.md carries only the doctrine its own
# archetype needs. local_probe-build.md restates the prior-art rule in ~45 tokens because
# "probe and build" IS the RE/tool-build/"how do we solve X" shape the rule targets;
# local_mechanical-edit.md deliberately omits it (a named mechanical edit is out of that
# scope, and the 4k-token target is the point). Adding a third local archetype means
# deciding this again — the doctrine does not arrive by inheritance here.


def load_local_prompt(tmpl_path, ticket_id, ticket, worktree, prior_notes_path=None,
                       acceptance_doc=None, rework=None):
    """Slim local prompt. Everything above the `---` line is static text with no
    placeholders, so it is byte-identical across tickets and the backend's KV prefix
    cache is reused; every per-ticket value (rework, contract, ticket, notes) sits after
    it. One regex pass, so a `{{X}}` inside a ticket detail is never re-expanded.
    No instincts pack, no distilled memory: the 4k-token target is the point."""
    with open(tmpl_path) as f:
        tmpl = f.read()
    prior_notes = ""
    if prior_notes_path and os.path.exists(prior_notes_path):
        with open(prior_notes_path) as f:
            prior_notes = f.read()
    values = {
        "REWORK": acceptance_mod.rework_block(rework) if rework else "",
        "ACCEPTANCE_CONTRACT": (acceptance_mod.contract_block(acceptance_doc, worktree)
                                 if acceptance_doc else ""),
        "TICKET_ID": str(ticket_id), "TITLE": ticket.get("title", ""),
        "DETAIL": ticket.get("detail", ""), "WORKTREE": worktree,
        "PRIOR_ATTEMPT_NOTES": prior_notes,
    }
    head, sep, tail = tmpl.partition("\n---\n")
    for key in [k for k, v in values.items() if not v]:
        tail = tail.replace("{{%s}}\n\n" % key, "").replace("{{%s}}" % key, "")
    tail = re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m.group(1), m.group(0)), tail)
    return head + sep + tail


# ---------------------------------------------------------------------------
# wq#717 (AUTO-5): fleet-fires manual-approval workflow — see FIRE_MODE.md
# ---------------------------------------------------------------------------

def load_fire_prompt(phase, ticket_id, ticket, worktree, plan_text=None):
    fname = "fire-diagnosis.md" if phase == "diagnosis" else "fire-apply.md"
    tmpl_path = os.path.join(HARNESS_DIR, "prompts", fname)
    with open(tmpl_path) as f:
        tmpl = _sub_instincts_tier0(f.read())
    out = (tmpl
           .replace("{{TICKET_ID}}", str(ticket_id))
           .replace("{{TITLE}}", ticket.get("title", ""))
           .replace("{{DETAIL}}", ticket.get("detail", ""))
           .replace("{{WORKTREE}}", worktree)
           .replace("{{PLAN_PATH}}", fire_gate.plan_path(ticket_id))
           .replace("{{APPROVAL_PATH}}", fire_gate.approval_path(ticket_id))
           .replace("{{DRIFT_PATH}}", fire_gate.drift_path(ticket_id)))
    if plan_text is not None:
        out = out.replace("{{PLAN_TEXT}}", plan_text)
    return out


def run_fire_child(phase, args, ticket, ticket_id, worktree, worker_id, max_turns,
                    wall_clock, plan_text=None):
    """One `claude -p` child for one fire-mode phase. Same spawn/parse/classify
    plumbing as do_attempt()'s single child, but env carries FOREMAN_FIRE_PHASE so
    guard.sh can enforce the diagnosis-phase restrictions (harness/guard.sh, wq#717)."""
    prompt = load_fire_prompt(phase, ticket_id, ticket, worktree, plan_text=plan_text)
    env = build_env(worker_id, args.model, args.effort, worktree, ticket_id)
    env["FOREMAN_FIRE_PHASE"] = phase
    if phase == "diagnosis":
        env["FOREMAN_FIRE_PLAN_PATH"] = fire_gate.plan_path(ticket_id)
    settings_file = resolve_worker_settings(args.repo, ticket_id)
    cmd = build_claude_cmd(prompt, args.model, args.effort, settings_file,
                            add_dirs=add_dirs_for(FIRE_ARCHETYPE),
                            max_turns=max_turns, claude_bin=args.claude_bin)
    log(f"wq#{ticket_id} fire-mode {phase}: spawning "
        f"{' '.join(shlex.quote(c) for c in cmd[:6])}...")
    state, exit_code, events, killed_reason, cpu_s, pid = run_child(
        cmd, worktree, env, max_turns, wall_clock, ticket_id, 1)
    effective_exit_code = 0 if killed_reason == "post_result_stuck" else exit_code
    return classify(state, effective_exit_code)


def run_fire_ticket(args):
    """wq#717 (AUTO-5): fleet-fires lane manual-approval workflow. Unlike do_attempt()'s
    single-shot retry ladder, one `fworker run --archetype fire-response` invocation
    drives the WHOLE diagnose -> hold -> apply state machine (fire_gate.next_action) to
    a terminal outcome, polling in-process while awaiting Frank rather than exiting and
    counting on a later redispatch: wq's `blocked` status has no automatic reopen (a
    ticket `wq block`ed here would need a human to `wq edit --status open` it too), so
    the hold is a live loop with periodic heartbeats, not a released/reclaimed lease.
    Guardrails enforced here (ticket's MUST list): the approval file is validated by
    fire_gate (ticket-id match, bearer token, plan-hash == current plan) before ANY
    apply-phase child spawns, and the apply-phase child's own mandatory re-verify step
    (fire-apply.md) can still veto via drift_path if state moved while we were waiting.

    wq#1234: wq#1154/#1160/#1161 all ran 13-22h holding for an approval that never came,
    even after the underlying probe condition had already cleared on its own. Every loop
    iteration now re-verifies the originating probe (fire_gate.probe_cleared) — if it's
    PASSing again, the ticket auto-closes with 'condition cleared' instead of continuing
    to hold — and total wall-clock is capped at max_runtime_s: once exceeded, the ticket
    blocks with the current probe state for a human instead of churning.

    wq#1261 (#13): probe_cleared() now prefers the ticket's own `fire_probe: {cmd,
    clear_when_exit}` line over the aq-row check, and its command output is attached as
    this run's evidence (fire_probe_evidence.json in the run dir) when it auto-closes.
    Every terminal outcome is also recorded as attempt-N.json (write_attempt_state, the
    same convention do_attempt() uses) with a `fire_outcome` field; expired/diagnosis-
    failed/timeout-released/apply-incomplete outcomes count against FIRE_ATTEMPT_FENCE —
    the 3rd such outcome escalates straight to FRANK instead of running fire-mode again.
    """
    ticket_id = args.ticket
    ticket = wq_show(ticket_id)

    fenced = fire_fenced_attempt_count(ticket_id)
    if fenced >= FIRE_ATTEMPT_FENCE:
        reason = (f"FRANK: fire-response attempt fence tripped — {fenced} prior "
                  f"expired/rework attempt(s) (fence={FIRE_ATTEMPT_FENCE}); this ticket "
                  f"needs a human, not another automatic fire-mode run "
                  f"(ISSUE-SOLUTIONS-fable.md §4 item 3).")
        log(f"wq#{ticket_id} fire-mode: {reason}")
        wq_block(ticket_id, reason)
        ntfy("fire ticket escalated to FRANK",
             f"wq#{ticket_id}: {fenced} fire-response attempts already bounced "
             f"(expired/rework) — attempt fence reached, escalated for human review "
             f"instead of a {fenced + 1}th automatic run.", priority="urgent")
        print("BLOCKED")
        return EXIT_FIRE_ATTEMPT_FENCE

    attempt_n = next_attempt_n(ticket_id)
    worktree, branch = make_worktree(args.repo, ticket_id, 1,
                                      ticket.get("title", str(ticket_id)), FIRE_ARCHETYPE)
    worker_id = args.worker_id or f"fworker-wq{ticket_id}-fire"
    max_turns = args.max_turns or ARCHETYPE_MAX_TURNS.get(FIRE_ARCHETYPE, DEFAULT_MAX_TURNS)
    wall_clock = args.wall_clock_timeout or wall_clock_timeout(FIRE_ARCHETYPE)
    poll_s = _envint("FOREMAN_FIRE_POLL_S", 60)
    timeout_hours = _envint("FOREMAN_FIRE_TIMEOUT_HOURS", fire_gate.DEFAULT_TIMEOUT_HOURS)
    max_runtime_s = _envint("FOREMAN_FIRE_MAX_RUNTIME_S", fire_gate.DEFAULT_MAX_RUNTIME_S)
    notified_hold = False
    last_reject_code = None
    start_ts = time.time()
    detail = ticket.get("detail", "")

    def _record(fire_outcome, **extra):
        rec = {"ticket": ticket_id, "attempt": attempt_n, "worker_id": worker_id,
               "archetype": FIRE_ARCHETYPE, "fire_outcome": fire_outcome,
               "started_at": start_ts, "finished_at": time.time()}
        rec.update(extra)
        # wq#1330: fence-counting fire outcomes are terminal failures — same
        # failure.json + triage + sha256 contract as handle_failure(). No transcript
        # events flow through this supervisor loop, so triage keys off the child
        # classification alone (UNKNOWN when there is none) — recorded, never silent.
        if fire_outcome in FIRE_FENCE_OUTCOMES:
            try:
                frec = failure_record.build_failure_record(
                    ticket=ticket_id, attempts=attempt_n,
                    classification=extra.get("classification") or fire_outcome.upper(),
                    block_class=classify_block_reason(
                        extra.get("classification") or fire_outcome.upper()),
                    events=[], worktree=worktree, expected_repo=args.repo,
                    session_id=None, attempt_files=[f"attempt-{attempt_n}.json"])
                fpath = failure_record.write_failure_record(run_dir(ticket_id), frec)
                rec.update({"triage": frec["triage"], "failure_json_path": fpath,
                            "failure_json_sha256": failure_record.sha256_file(fpath)})
            except Exception as e:  # noqa: BLE001 — bookkeeping must not eat the outcome
                log(f"WARN: fire-mode failure.json not written: {e!r}")
        write_attempt_state(ticket_id, attempt_n, rec)

    while True:
        cleared, evidence = fire_gate.probe_cleared(detail)
        log(f"wq#{ticket_id} fire-mode re-verify: originating probe condition "
            f"{'CLEARED' if cleared else 'still active'}")
        if cleared:
            evidence_path = None
            if evidence is not None:
                evidence_path = os.path.join(run_dir(ticket_id), "fire_probe_evidence.json")
                tmp = evidence_path + ".tmp"
                with open(acceptance_mod.private_open(tmp), "w") as f:
                    json.dump(evidence, f, indent=2, default=str)
                os.rename(tmp, evidence_path)
            notes = ("condition cleared: re-verify found the originating probe no "
                     "longer firing — auto-closed by the fire-response archetype "
                     "(wq#1261), no manual apply needed.")
            if evidence_path:
                notes += (f" Probe evidence: {evidence_path} "
                          f"(cmd={evidence['cmd']!r} exit={evidence['exit_code']}).")
            # the one caller that can honestly assert artifact-lessness: this branch
            # auto-closes precisely BECAUSE no change was applied (wq#1925).
            wq_done(ticket_id, notes,
                    no_artifact_reason="fire-mode auto-close: the originating probe "
                                       "cleared on its own and no code change was "
                                       "applied; re-verify result recorded in --notes",
                    # wq#1721: the cited evidence lives in this run's RUNS_ROOT dir, which
                    # is per-run state and never committed — wq#1673's durability gate
                    # refuses it otherwise, and the refusal is unsatisfiable by design.
                    unlanded_reason=("fire-mode probe evidence is a per-run artifact under "
                                     "the fworker run dir, never landed on main by design"
                                     ) if evidence_path else None)
            ntfy("fire ticket auto-closed",
                 f"wq#{ticket_id}: re-verify found the originating probe condition has "
                 f"cleared on its own — auto-closed, no change applied.",
                 priority="default")
            fire_gate.clear_fire_state(ticket_id)
            _record("auto_closed", evidence_path=evidence_path)
            print("TICKET_CLOSED")
            return EXIT_OK

        if fire_gate.runtime_exceeded(start_ts, max_runtime_s):
            action, info = fire_gate.next_action(ticket_id, timeout_hours=timeout_hours)
            reason = (f"EXPIRED: fire-response exceeded max runtime ({max_runtime_s}s) "
                      f"without the probe condition clearing — current state: "
                      f"action={action} info={info}")
            log(f"wq#{ticket_id} fire-mode: {reason} — blocking for human review")
            wq_block(ticket_id, reason)
            ntfy("fire ticket exceeded max runtime",
                 f"wq#{ticket_id}: {reason} (fence: {fenced + 1}/{FIRE_ATTEMPT_FENCE} "
                 f"expired/rework attempts so far)", priority="high")
            _record("expired", max_runtime_s=max_runtime_s, last_action=action)
            print("BLOCKED")
            return EXIT_FIRE_MAX_RUNTIME

        action, info = fire_gate.next_action(ticket_id, timeout_hours=timeout_hours)

        if action == "diagnose":
            classification = run_fire_child("diagnosis", args, ticket, ticket_id,
                                              worktree, worker_id, max_turns, wall_clock)
            log(f"wq#{ticket_id} fire-mode diagnosis: {classification}")
            if fire_gate.read_plan(ticket_id) is None:
                log(f"wq#{ticket_id} fire-mode: diagnosis ended ({classification}) with "
                    f"no plan doc written — releasing")
                wq_release(ticket_id)
                ntfy("fire ticket diagnosis failed",
                     f"wq#{ticket_id}: diagnosis phase ended ({classification}) without "
                     f"writing a plan doc to {fire_gate.plan_path(ticket_id)} — released "
                     f"for retry.", priority="high")
                _record("diagnosis_failed", classification=classification)
                print("RELEASED")
                return EXIT_FIRE_DIAGNOSIS_FAILED
            continue  # plan now exists — re-derive next_action

        if action == "hold":
            code = info.get("code")
            if not notified_hold:
                plan_text = fire_gate.read_plan(ticket_id) or ""
                summary = "\n".join(plan_text.splitlines()[:20])
                ntfy("fire ticket awaiting approval",
                     f"wq#{ticket_id}: plan ready at {fire_gate.plan_path(ticket_id)}. "
                     f"Approve by creating {fire_gate.approval_path(ticket_id)} as JSON: "
                     '{"ticket": ' + str(ticket_id) + ', "plan_hash": "'
                     + fire_gate.sha256_text(plan_text) + '", "token": "<fire-approval '
                     'token file contents>"}. Timeout in ' + str(timeout_hours)
                     + f"h if unapproved. Plan summary:\n{summary}", priority="high")
                notified_hold = True
            elif code != "missing" and code != last_reject_code:
                # Frank (or something) attempted an approval and it failed validation —
                # surface why immediately rather than silently waiting out the timeout;
                # de-duped so a still-broken retry doesn't ntfy on every poll.
                last_reject_code = code
                ntfy("fire ticket approval rejected",
                     f"wq#{ticket_id}: approval file present but invalid ({code}): "
                     f"{info.get('detail')}", priority="high")
            wq_heartbeat(ticket_id)
            time.sleep(poll_s)
            continue

        if action == "timeout_release":
            log(f"wq#{ticket_id} fire-mode: {timeout_hours}h elapsed with no valid "
                f"approval (last: {info.get('code')}) — releasing")
            ntfy("fire ticket timed out",
                 f"wq#{ticket_id}: no valid approval within {timeout_hours}h "
                 f"(last state: {info.get('code')}) — released, dispatcher can requeue.",
                 priority="high")
            wq_release(ticket_id)
            _record("timeout_release", last_code=info.get("code"))
            print("RELEASED")
            return EXIT_FIRE_TIMEOUT

        # action == "apply"
        plan_text = info["plan_text"]
        classification = run_fire_child("apply", args, ticket, ticket_id, worktree,
                                          worker_id, max_turns, wall_clock, plan_text=plan_text)
        log(f"wq#{ticket_id} fire-mode apply: {classification}")
        if os.path.exists(fire_gate.drift_path(ticket_id)):
            log(f"wq#{ticket_id} fire-mode: apply phase found live state had drifted "
                f"from the approved plan — no change applied, re-diagnosing")
            ntfy("fire ticket state drifted since approval",
                 f"wq#{ticket_id}: re-verify caught drift from the approved plan — "
                 f"nothing was changed. Re-running diagnosis.", priority="high")
            fire_gate.clear_fire_state(ticket_id)
            notified_hold = False
            last_reject_code = None
            continue
        # Success/failure from here on is the child's own wq done/block/release call
        # (fire-apply.md carries the same "call wq done/block/release yourself"
        # contract as the standard worker.md) — this supervisor's job is done once a
        # valid-approval apply phase has actually run.
        print(classification)
        if classification in (SUCCESS, DEGRADED_SUCCESS):
            _record("applied", classification=classification)
            return EXIT_OK
        _record("apply_incomplete", classification=classification)
        return EXIT_FIRE_APPLY_INCOMPLETE


def do_attempt(args, attempt, resume_session=None, prior_worktree=None, steer=None,
               ceiling_spent=False, grant_resume=False, exit_nudge=None):
    ticket_id = args.ticket
    ticket = wq_show(ticket_id)
    archetype = args.archetype or "standard-engineering"
    # wq#869: whoever holds the ticket as this attempt starts — the baseline a
    # CLAIM_ERROR sentinel is corroborated against below. Captured here rather than
    # assumed to be wq_identity() so a resumed attempt compares against the claim it
    # actually inherited.
    claim_holder_at_start = ticket_holder(ticket)
    # wq#4289: both gates below police args.model's pool; the aider runtime spends none of it.
    aider_runtime = runtime_of(args) == "aider"

    allow, reason = (True, None) if aider_runtime else can_dispatch(args.model)
    if not allow:
        log(f"governor DEFERRED dispatch: {reason} — releasing claim")
        wq_release(ticket_id)
        print("DEFERRED")
        return EXIT_DEFERRED

    allow, reason = (True, None) if aider_runtime else local_rung_gate(args.model, archetype)
    if not allow:
        log(f"DEFERRED: {reason} — releasing claim")
        wq_release(ticket_id)
        print("DEFERRED")
        return EXIT_DEFERRED

    # wq#5007: circuit breaker + hourly call limit — refused before a token is spent,
    # for every loop (fresh spawn and in-process resume alike). A grant-approved
    # resume is exempt: Frank's approval is spent on exactly this spawn (wq#4765).
    if not grant_resume:
        gate_refusal = exit_gate_preflight(ticket_id)
        if gate_refusal:
            return exit_gate_defer(ticket_id, gate_refusal)
        # wq#5007: a session older than SESSION_EXPIRY is not resumed — the loop
        # restarts on a fresh session in the same worktree, with prior-attempt notes.
        try:
            if (resume_session and exit_gate.mode() == "enforce"
                    and exit_gate.session_expired(run_dir(ticket_id), resume_session)):
                log(f"exit_gate: session {resume_session} is past its "
                    f"{exit_gate.SESSION_EXPIRY_S // 3600}h expiry — fresh session")
                resume_session, steer, exit_nudge = None, None, None
        except Exception as e:
            log(f"WARN exit_gate session expiry check failed open: {e}")

    if args.max_turns:
        max_turns = args.max_turns
    elif is_local_model(args.model):
        max_turns = LOCAL_ARCHETYPE_MAX_TURNS.get(archetype, ARCHETYPE_MAX_TURNS.get(archetype, DEFAULT_MAX_TURNS))
    else:
        max_turns = ARCHETYPE_MAX_TURNS.get(archetype, DEFAULT_MAX_TURNS)
    wall_clock = args.wall_clock_timeout or wall_clock_timeout(archetype)

    worker_id = args.worker_id or f"fworker-wq{ticket_id}-a{attempt}"

    # wq#1331 (§3.2): the ticket's own needs:dir tags widen read context; needs:repo
    # was already honoured by the router before --repo reached us. parse() returns
    # empty under FOREMAN_NEEDS_DISABLE=1 (rollback).
    ticket_needs = needs_mod.parse(ticket.get("needs"), ticket.get("detail", ""))
    add_dirs = add_dirs_for(archetype, ticket_needs.dirs, repo=args.repo)
    add_dirs += [d for d in scratch_grant_dirs(ticket_id) if d not in add_dirs]
    # wq#4765 (§2.4 point 1, "Claude layer"): a grant-approved resume's H1 grant widens
    # add_dirs by exactly the approved target directory — read from the ticket's own
    # grant_req row, never re-derived, so this spawn can only ever see what was actually
    # approved. H2 (repo-read) grants are the broker's business (needs:repo), not add_dir.
    grant_row = None
    if grant_resume and ticket.get("grant_req"):
        grant_row = grant_row_by_id(ticket["grant_req"])
        if grant_row and grant_row.get("class") == "H1" and grant_row.get("target"):
            target = grant_row["target"]
            if target not in add_dirs:
                add_dirs.append(target)

    # wq#4765: a grant-approved resume carries NO prior_worktree (it is a brand-new
    # fworker process, not an in-process recursive resume like steer/tool_ceiling/HANG
    # below) — it reaches here via resume_session alone. make_worktree()'s own
    # adopt-existing-if-ours branch (wq#649) is what makes this safe: (ticket_id,
    # attempt) is a deterministic path, and next_attempt_n() returns the SAME attempt
    # number here that the original deferred attempt used (that attempt never wrote
    # attempt-N.json — EXIT_AWAITING_GRANT is uncharged), so the worktree the deferred
    # call ran in is adopted, not recreated.
    if prior_worktree:
        worktree, branch = prior_worktree, None
    else:
        worktree, branch = make_worktree(args.repo, ticket_id, attempt,
                                          ticket.get("title", str(ticket_id)), archetype)
        # §3.2 last clause: refuse (uncharged) while zero tokens are spent, not after
        # a burned attempt — the wq#1152 signature is a cited path the sandbox can't
        # reach. A resume reuses a worktree that already passed this gate.
        assert_spawn_capability(ticket_id, ticket, worktree, args.repo, add_dirs,
                                ticket_needs)

    # wq#1332 (§2.2 point 2): resolved every attempt (not just fresh spawns) so a
    # resumed session's grant lease tracks its OWN wall_clock, not the first
    # attempt's — raises SpawnRefused (uncharged) if a needs:copy/restart tag asks
    # for something fleet-exec-policy.yaml forbids outright.
    grant, grant_sha, grant_path = resolve_exec_grant(ticket_id, attempt, ticket_needs,
                                                       wall_clock, worktree=worktree)

    prior_notes_path = os.path.join(run_dir(ticket_id), "prior-attempt-notes.md")
    # §1a: criteria are machine-checkable and pinned BEFORE the worker starts, so the
    # prompt can carry the done-contract and the Stop hook has something to enforce.
    acceptance_doc = pin_acceptance(ticket_id, dict(ticket, archetype=archetype),
                                     worktree, attempt)
    # §1d: only a LIVE rejection renders — rework.json outlives the rejection it
    # describes (it is kept as the run's history), the marker does not.
    rework = (acceptance_mod.read_json(
        os.path.join(run_dir(ticket_id), acceptance_mod.REWORK_FILENAME))
        if evidence_rejected(ticket_id) else None)
    prior_notes_arg = prior_notes_path if (attempt > 1 and not resume_session) else None
    local_tmpl = local_prompt_template(args.model, archetype)
    if local_tmpl:
        prompt = load_local_prompt(local_tmpl, ticket_id, ticket, worktree, prior_notes_arg,
                                    acceptance_doc=acceptance_doc, rework=rework)
    else:
        prompt = load_worker_prompt(ticket_id, ticket, worktree, prior_notes_arg,
                                     acceptance_doc=acceptance_doc, rework=rework,
                                     archetype=archetype)
    # wq#2128: a prompt that cannot fit the served context is a harness fault, refused
    # before any token is spent and never charged (SpawnRefused writes no attempt record).
    # wq#2185: the size gate counts the CLI's own fixed request too (the local profile's
    # measured overhead), and a prompt that cannot prefill inside the first-frame budget
    # at the backend's measured rate is refused the same way, before Mac time is spent.
    cli_profile = local_cli_profile(args.model)
    prompt_tokens_est = charging.prompt_tokens_est(prompt, cli_profile)
    # wq#2128: an explicit --frame-gap-timeout keeps the single pre-split window.
    first_frame_s = (None if charging.legacy_mode() or args.frame_gap_timeout is not None
                     else first_frame_timeout(args.model))
    # wq#2244: the gap between frames after the first one; local = the backend's turn budget.
    turn_gap_s = (args.frame_gap_timeout if args.frame_gap_timeout is not None
                  else turn_gap_timeout(args.model))
    # wq#4289: the aider runtime never sends this prompt, so its size can't refuse it.
    sends_prompt_local = is_local_rung(args.model) and not aider_runtime
    # wq#4765: a grant-approved resume sends NO prompt at all (build_claude_cmd below,
    # resume_no_prompt=True) — `prompt` here is computed but never transmitted, so its
    # size can't legitimately refuse this spawn.
    over_ctx = (None if charging.legacy_mode() or not sends_prompt_local or grant_resume
                else charging.prompt_over_ctx(prompt, cli_profile=cli_profile))
    if over_ctx:
        raise SpawnRefused("PROMPT_OVER_CTX", over_ctx, worktree=worktree)
    over_budget = (charging.prompt_over_budget(prompt_tokens_est, first_frame_s)
                   if sends_prompt_local and not grant_resume else None)
    if over_budget:
        raise SpawnRefused("PROMPT_OVER_BUDGET", over_budget, worktree=worktree)

    env = build_env(worker_id, args.model, args.effort, worktree, ticket_id,
                    cli_profile=cli_profile)
    if grant_path:
        # wq#1332: fleet-exec's OWN enforcement step 1 re-derives this sha256 from
        # the file it reads — this env var is the tamper check, not a convenience.
        env["FOREMAN_GRANT_FILE"] = grant_path
        env["FOREMAN_GRANT_SHA"] = grant_sha
        env["FOREMAN_ATTEMPT"] = str(attempt)
        env["FLEET_EXEC_WORKER_AUDIT_PATH"] = os.path.join(EVENTS_DIR, f"{worker_id}.jsonl")
    # wq#2349: written for EVERY attempt, grant_path or not — the SessionEnd revoke is
    # a lifecycle op, not one of the exec capabilities above.
    te_grant_path, te_grant_sha = write_ticket_end_grant(ticket_id, attempt, worker_id,
                                                          wall_clock)
    if te_grant_path:
        env["FOREMAN_TICKET_END_GRANT_FILE"] = te_grant_path
        env["FOREMAN_TICKET_END_GRANT_SHA"] = te_grant_sha
    # §1c: the Stop hook reads these — which ticket this session owns, and where its
    # per-session block counter lives (capped, so it can never wedge a session).
    env["FOREMAN_TICKET_ID"] = str(ticket_id)
    env["FOREMAN_STOP_GATE_STATE"] = os.path.join(run_dir(ticket_id), "stop-gate.json")
    oauth_token_used = "CLAUDE_CODE_OAUTH_TOKEN" in env
    # Layers 1+3 (TIERED_INTELLIGENCE_DESIGN.md, wq#985): compile per-project
    # .fworker-rules.yaml (repo root + worktree root, additive over builtins) into
    # the fworker-owned run dir at ticket start — mid-session edits to the worktree
    # copy of the rules file have no effect on this attempt. The PreToolUse hook
    # (action_gate.py, wired in worker-settings.json) reads the compiled path; with
    # the env unset it still enforces the builtin RED/AMBER lists.
    try:
        gate_rules_path = action_gate.compile_rules(
            [args.repo, worktree],
            os.path.join(run_dir(ticket_id), f"gate-rules-attempt-{attempt}.json"))
        if gate_rules_path:
            env[action_gate.GATE_RULES_ENV] = gate_rules_path
            log(f"gate: project rules compiled -> {gate_rules_path}")
    except Exception as e:
        # A malformed rules file must not silently drop protection: refuse to run.
        log(f"gate: .fworker-rules.yaml compile FAILED ({e}) — releasing ticket, "
            f"fix the rules file before dispatching against this project.")
        wq_release(ticket_id)
        return EXIT_DEFERRED
    env[action_gate.SNAPDIR_ENV] = os.path.join(run_dir(ticket_id), "snapshots")
    env[action_gate.HANDOFF_ENV] = run_dir(ticket_id)
    loop_tracker = action_gate.LoopTracker(steered=bool(steer),
                                           ceiling_spent=ceiling_spent)
    # wq#5007: same-error needs 5 occurrences, not 3; no-progress triggers stay at 3.
    if exit_gate.mode() == "enforce":
        loop_tracker = exit_gate.CircuitLoopTracker(loop_tracker)
    # wq#5007: the gate is only enforced on a session whose prompt carried the
    # EXIT_SIGNAL contract (worker.md); other prompt families are recorded, not gated.
    exit_gate_enforced = (exit_gate.mode() == "enforce" and not aider_runtime
                          and exit_gate.PROTOCOL_MARKER in (prompt or ""))
    if exit_gate_enforced:
        env["FOREMAN_EXIT_GATE_PROTOCOL"] = "1"
    settings_file = resolve_worker_settings(args.repo, ticket_id, archetype=archetype)
    # wq#4765 (§2.3 step 6, §2.7 F9): a grant-approved resume spawns with --resume <sid>
    # and NO prompt — the CLI drops the deferred call if a resume carries one, so
    # grant_resume forces prompt=None here regardless of what load_worker_prompt built.
    cmd = build_claude_cmd(None if grant_resume else (steer or exit_nudge or prompt),
                            args.model, args.effort, settings_file,
                            add_dirs=add_dirs, max_turns=max_turns,
                            resume_session=resume_session, claude_bin=args.claude_bin,
                            local_profile=cli_profile, resume_no_prompt=grant_resume)

    # wq#1439 (§4 row i): the add-dir list is the argv proof that a needs:dir tag
    # reached the child, so it is logged in full rather than truncated with cmd[:6].
    log(f"attempt {attempt}: spawning {' '.join(shlex.quote(c) for c in cmd[:6])}... "
        f"cwd={worktree} max_turns={max_turns} wall_clock={wall_clock}s "
        f"add_dirs={' '.join('--add-dir ' + shlex.quote(d) for d in add_dirs)}")

    # wq#1894: from here until this attempt's own write_attempt_state() lands below, an
    # external SIGTERM (unit stop) must charge a minimal KILLED record and release the
    # claim itself — see handle_external_sigterm().
    set_sigterm_context(ticket_id, attempt, worker_id)
    gate_base = exit_gate_before_loop(ticket_id, worktree, attempt, ticket)  # wq#5007
    t0 = time.time()
    rate_limits_start = read_rate_limits_snapshot()
    # wq#1888 despag P2-2: the one call-site diff the gateway canary needs — which
    # backend runs this attempt's turn, picked by the ticket's own `lane` against
    # FOREMAN_GATEWAY_LANES (default empty: no lane, so every ticket keeps spawning
    # claude -p directly until an operator opts one in — harness/GATEWAY_CANARY.md).
    if aider_runtime:
        run_fn = run_child_aider
    elif gateway_lane_enabled(ticket.get("lane")):
        run_fn = run_child_gateway
    else:
        run_fn = run_child
    state, exit_code, events, killed_reason, cpu_s, child_pid = run_fn(
        cmd, worktree, env, max_turns, wall_clock, ticket_id, attempt,
        frame_gap_s=turn_gap_s, post_result_grace_s=args.post_result_grace,
        loop_tracker=loop_tracker, first_frame_s=first_frame_s,
        post_result_bg_grace_s=getattr(args, "post_result_bg_grace", None))
    gate_loop = exit_gate_after_loop(ticket_id, worktree, gate_base, events, state,
                                     killed_reason)  # wq#5007
    if state.stop_reason == "tool_deferred":
        # wq#4765 (§2.3 steps 2-3): the turn ended on a PreToolUse `defer`, clean exit
        # (killed_reason is None here — the CLI itself stopped the turn, no watchdog
        # fired). A clean cross-check match parks the ticket and returns here directly;
        # a mismatch (None) falls through to the ordinary classify()/retry path below,
        # same as today's plain CAPABILITY_DENIED.
        outcome = handle_tool_deferred(ticket_id, state)
        if outcome is not None:
            return outcome
    elif state.stop_reason == "tool_deferred_unavailable" and grant_resume:
        # §2.7 F7: this grant-approved resume's deferred call can no longer be
        # re-fired (session pruned past cleanupPeriodDays, or otherwise gone). The
        # approval is spent either way — release (clears resume_session/grant_req,
        # wq's own `wq release`) rather than treat this as an ordinary attempt
        # failure; a fresh attempt starts the hold from scratch if the call recurs.
        log(f"wq#{ticket_id}: HOLD_RESUME_LOST (tool_deferred_unavailable) on resumed "
            f"session {state.session_id} — releasing for a fresh attempt")
        wq_release(ticket_id)
        print("HOLD_RESUME_LOST")
        return EXIT_HOLD_RESUME_LOST
    if killed_reason == "steer" and state.session_id:
        # wq#2138: same attempt number — a harness-side refusal must not cost the
        # ticket an attempt. The resumed tracker is `steered`, so a second refusal
        # is a real loop_break, classified PERMISSION_LOOP (harness defect).
        return do_attempt(args, attempt, resume_session=state.session_id,
                           prior_worktree=worktree, steer=loop_tracker.steer,
                           ceiling_spent=loop_tracker.ceiling_spent)
    if killed_reason == "tool_ceiling" and state.session_id:
        # wq#4634: same attempt number — a transport kill must not cost the ticket an
        # attempt (wq#2138 precedent). reissue_ceiling_killed blocks here until the
        # watchdog verdict (or its own hard cap), then the verdict line rides the
        # --resume. ceiling_spent=True: a second ceiling kill in this attempt chain is
        # an ordinary error — no reissue recursion; the resume line already told the
        # session to use watchdog itself.
        verdict_line = reissue_ceiling_killed(ticket_id, attempt,
                                              loop_tracker.ceiling, worktree)
        log(f"tool_ceiling: resuming session {state.session_id} with the watchdog "
            f"verdict")
        return do_attempt(args, attempt, resume_session=state.session_id,
                           prior_worktree=worktree, steer=verdict_line,
                           ceiling_spent=True)
    if killed_reason == "loop_break":
        # Layer 2 handoff: "SESSION PAUSED: repeated unsuccessful approach detected.
        # Your work has been preserved for a reviewing agent." — written to the run
        # dir so prior-attempt notes and (future L4) review both find it.
        hpath = os.path.join(run_dir(ticket_id), f"loop-handoff-attempt-{attempt}.md")
        try:
            with open(acceptance_mod.private_open(hpath), "w") as hf:
                hf.write(f"# SESSION PAUSED — repeated unsuccessful approach detected\n\n"
                         f"ticket: wq#{ticket_id} attempt {attempt} worker {worker_id}\n\n"
                         f"{loop_tracker.report()}\n")
            log(f"loop handoff written -> {hpath}")
        except OSError as e:
            log(f"WARN: loop handoff write failed: {e}")
    # run_child() has already returned by every path here — clean exit and every
    # watchdog kill (inactivity/wall_clock/turns_exceeded/post_result_stuck) alike —
    # so this snapshot is the session-exit reading regardless of how the child ended.
    rate_limits_exit = read_rate_limits_snapshot()
    elapsed = time.time() - t0

    # §C9 SUCCESS-BUT-STUCK: fworker forced this kill AFTER a real terminal result had
    # already landed — the resulting SIGTERM/SIGKILL exit code is expected, not a §D
    # exit-code/frame disagreement, so it must not flip an otherwise-legitimate result
    # to INDETERMINATE. classify() only ever sees a "clean" exit for this one case.
    effective_exit_code = (0 if killed_reason in ("post_result_stuck", "ticket_done")
                           else exit_code)
    raw_classification = classify(state, effective_exit_code, elapsed_s=elapsed,
                                   diff_is_empty=worktree_diff_is_empty(worktree))
    classification = raw_classification
    # wq#992-C: run_child stopped the child specifically because the ticket itself
    # reached `done` mid-run — the most direct completion signal there is. That is a
    # verified success, not a watchdog failure, and there may be no result frame for
    # classify() to key on, so force SUCCESS. The §C10 artifact gate immediately below
    # still runs and re-confirms status==done, so a racy flip-back to open self-corrects
    # to FALSE_COMPLETE (-> resume) rather than force-closing on a stale read.
    if killed_reason == "ticket_done":
        classification = SUCCESS
    else:
        classification = loop_break_classification(raw_classification, killed_reason,
                                                    loop_tracker)
        if classification != raw_classification:
            log(f"{classification}: loop break was a harness permission refusal, not a "
                f"model loop — verdict overridden (raw {raw_classification})")

    # wq#687: silent-downgrade check. Compare the model the init frame reported
    # against the one we dispatched; an UNEXPLAINED swap (not a sanctioned
    # safeguards redirect, not a CLI-announced fallback, not a local-rung id) is
    # surfaced loudly so a downgrade can never masquerade as a clean SUCCESS.
    # Verdict-only — never retries or reroutes (orchestrator's call).
    model_downgraded, model_swap_detail = check_model_downgrade(
        args.model, state.model, state)
    if model_downgraded:
        log(f"WARNING wq#687: {model_swap_detail} — ticket {ticket_id} attempt "
            f"{attempt}; classification {classification} was produced by a model "
            f"OTHER than dispatched. Surfacing on attempt record.")

    # RELIABILITY_SPEC.md §E "persistent journal for OOM forensics": exit 137 is
    # already sufficient evidence for the OOM verdict (classify() above) — this is
    # corroboration only, same as cpu_s, attached to the attempt record so a human/
    # orchestrator deciding "raise MemoryMax or shrink" (§C6) doesn't have to go
    # run journalctl by hand. Best-effort; never raises, never changes classification.
    oom_journal = oom_journal_evidence(t0, pid=child_pid) if classification == OOM else None

    # wq#749: a worker that did exactly what worker.md tells it to do on an
    # unworkable ticket — `wq block`, print BLOCKED, exit 0 — produces an ordinary
    # result:success frame; classify() has no way to know that was a legitimate
    # terminal outcome rather than a lie. Read the sentinel FIRST and corroborate it
    # against the ticket's actual status before the FALSE_COMPLETE check below ever
    # runs, so a real block/claim-race never gets relabeled and retried. The sentinel
    # alone is never trusted (a worker could print BLOCKED without calling `wq
    # block`) — only the cross-check against wq's own DB state makes it terminal.
    #
    # wq#869: that promise used to hold for BLOCKED only. CLAIM_ERROR was gated on
    # `status != "done"`, which the ordinary in-progress status ("claimed") satisfies
    # on its own — so any session could print the literal line CLAIM_ERROR with zero
    # claim contention and buy itself a release + no-retry + no FALSE_COMPLETE flag,
    # exactly the self-report trust RELIABILITY_SPEC.md §C10 exists to deny. The
    # corroborating DB fact for a claim race is the CLAIM, not the status: the ticket
    # must have left this attempt's holder — stolen by another identity, or reopened
    # out from under it. A ticket still held by whoever held it at attempt start
    # falls through to the ordinary FALSE_COMPLETE path.
    worker_sentinel = None
    sentinel_status = None
    sentinel_holder = None
    if classification in (SUCCESS, DEGRADED_SUCCESS):
        worker_sentinel = parse_worker_sentinel(state.result_text)
        if worker_sentinel in (BLOCK_SENTINEL, CLAIM_ERROR_SENTINEL):
            sentinel_ticket = wq_show(ticket_id)
            sentinel_status = sentinel_ticket.get("status")
            sentinel_holder = ticket_holder(sentinel_ticket)
    sentinel_confirmed_block = (worker_sentinel == BLOCK_SENTINEL
                                 and sentinel_status == "blocked")
    claim_lost = (worker_sentinel == CLAIM_ERROR_SENTINEL
                  and claim_holder_at_start is not None
                  and sentinel_holder != claim_holder_at_start)
    sentinel_confirmed_claim_error = (claim_lost and sentinel_status != "done")

    # wq#3116: a worker that legitimately blocked itself (or lost its claim) landed
    # no artifact and made no change — classify()'s SUCCESS/DEGRADED_SUCCESS verdict
    # is about the *session* (clean exit, well-formed result frame), not the ticket
    # outcome, and must not reach the attempt record, charging, or the episode
    # digest as a success (that mislabel is exactly what let wq#3114's blocked
    # attempt land as "attempt 1 SUCCESS" in lumen_episode). Overridden here, before
    # anything downstream reads `classification`, so every consumer sees the truth.
    if sentinel_confirmed_block:
        classification = BLOCK_SENTINEL
    elif sentinel_confirmed_claim_error:
        classification = CLAIM_ERROR_SENTINEL

    # RELIABILITY_SPEC.md §C10: verify acceptance criteria against artifacts BEFORE
    # anything downstream (including the persisted attempt record) trusts a self-
    # reported success — decided here, not inside reconcile(), so the record on disk
    # carries the same verdict reconcile() acts on. `raw_classification` keeps the
    # pre-verification classifier output too (e.g. DEGRADED_SUCCESS), so RCA can still
    # see WHY this got relabeled instead of just seeing FALSE_COMPLETE.
    # ISSUE-LOG #17 §1b: the evidence replay runs FIRST, because it is the stronger
    # check — the artifact gate below asks "did something land", this one asks "does the
    # claim reproduce". A ticket with no pinned criteria skips it and the pre-#17
    # behaviour stands unchanged.
    evidence_escalated = False
    if (classification in (SUCCESS, DEGRADED_SUCCESS)
            and not sentinel_confirmed_block and not sentinel_confirmed_claim_error):
        evidence_ok, evidence_escalated = verify_done(ticket_id, worktree, attempt)
        if not evidence_ok:
            log(f"{classification} but the harness's own replay of evidence.json did not "
                f"reproduce the claim — ISSUE-LOG #17: done is a replay, not a claim. "
                f"Treating as FALSE_COMPLETE.")
            classification = FALSE_COMPLETE

    if (classification in (SUCCESS, DEGRADED_SUCCESS)
            and not sentinel_confirmed_block and not sentinel_confirmed_claim_error
            and not check_artifacts_done(ticket_id, worktree)[0]):
        log(f"{classification} but acceptance criteria unmet (ticket still open, no "
            f"landed commit found) — RELIABILITY_SPEC.md §C10: never trust the "
            f"self-report, treating as FALSE_COMPLETE.")
        classification = FALSE_COMPLETE

    # wq#5007 dual-condition exit gate: an exit the artifact checks above just
    # accepted still needs >= 2 harness-verified indicators AND the worker's explicit
    # EXIT_SIGNAL: true. `ticket_done` kills are exempt — the harness stopped that
    # session itself, there is no worker exit claim to gate.
    gate_nudge = None
    gate_info = {"mode": exit_gate.mode(), "enforced": exit_gate_enforced,
                 "loop": gate_loop, "backup_ref": gate_base.get("backup_ref")}
    if (exit_gate.mode() != "off" and killed_reason != "ticket_done"
            and classification in (SUCCESS, DEGRADED_SUCCESS)
            and not sentinel_confirmed_block and not sentinel_confirmed_claim_error):
        try:
            _, gate_landed_sha = check_artifacts_done(ticket_id, worktree)
            gate_decision, gate_status = exit_gate_decide(
                ticket_id, raw_classification, worker_sentinel, gate_landed_sha, state)
            gate_info.update(exit=gate_decision.exit, reason=gate_decision.reason,
                             indicators=gate_decision.indicators,
                             signal=gate_decision.signal)
            if gate_decision.exit:
                _breaker(ticket_id).reset("exit")
            elif exit_gate_enforced:
                log(f"exit_gate: wq#{ticket_id} exit NOT accepted "
                    f"({gate_decision.reason}; indicators={gate_decision.indicators}; "
                    f"EXIT_SIGNAL={gate_decision.signal})")
                if state.session_id and not exit_nudge:
                    gate_nudge = exit_gate.nudge_text(gate_decision, ticket_id)
                elif (gate_decision.signal is None and gate_status == "done"
                        and len(gate_decision.indicators) >= exit_gate.MIN_INDICATORS):
                    # Safety valve (Ralph v0.10.1 forces an exit on repeated
                    # completion indicators without the signal): the worker was
                    # asked once and still sent no EXIT_SIGNAL, but the ticket is
                    # done and the harness verified it. Two consecutive loops with
                    # the indicators held end the loop as DEGRADED_SUCCESS — never
                    # plain SUCCESS, and never on an explicit `false`.
                    log(f"exit_gate: wq#{ticket_id} safety valve — no EXIT_SIGNAL "
                        f"after the nudge, ticket verified done "
                        f"({gate_decision.indicators}); accepting as "
                        f"DEGRADED_SUCCESS")
                    gate_info["safety_valve"] = True
                    classification = DEGRADED_SUCCESS
                    _breaker(ticket_id).reset("exit (safety valve)")
                else:
                    classification = FALSE_COMPLETE
                # An explicit `false` on a ticket that is not itself done means the
                # worker says more work remains: a commit that merely names the
                # ticket must not close it (handle_resumable_failure honours this).
                if (not gate_nudge and gate_decision.signal is False
                        and gate_status != "done"):
                    state.exit_gate_explicit_false = True
            else:
                log(f"exit_gate[{exit_gate.mode()}]: wq#{ticket_id} would not accept "
                    f"this exit ({gate_decision.reason}) — recorded only")
        except Exception as e:
            log(f"WARN exit_gate decision failed open: {e}")
            gate_info["error"] = str(e)[:200]
            gate_nudge = None
    if gate_nudge:
        # one corrective resume, same attempt number (wq#2138 precedent): a missing
        # closing line must not cost the ticket an attempt. Outside the try above on
        # purpose — an exception in the resumed loop is that loop's own, never a
        # reason to accept this exit.
        return do_attempt(args, attempt, resume_session=state.session_id,
                          prior_worktree=worktree,
                          ceiling_spent=loop_tracker.ceiling_spent,
                          exit_nudge=gate_nudge)

    log(f"attempt {attempt}: classification={classification} raw={raw_classification} "
        f"exit={exit_code} killed_reason={killed_reason} turns={state.turns} "
        f"elapsed={elapsed:.0f}s cpu_s={cpu_s:.1f} session_id={state.session_id}")

    gap_s = getattr(state, "silent_gap_s", None)
    charged_to, failure_cause = charging.classify_charge(
        classification, turns=state.turns, is_local=is_local_rung(args.model),
        killed_reason=killed_reason, events=events,
        result_text=state.result_text if isinstance(state.result_text, str) else None,
        prompt_tokens=prompt_tokens_est, prefill_tps=charging.prefill_tps(),
        first_frame_s=first_frame_s, gap_s=gap_s, turn_gap_s=turn_gap_s)
    attempt_record = {
        "ticket": ticket_id, "attempt": attempt, "worker_id": worker_id,
        "charged_to": charged_to, "failure_cause": failure_cause,
        "first_frame_s": first_frame_s, "gap_s": gap_s, "turn_gap_s": turn_gap_s, "prompt_profile": local_tmpl and os.path.basename(local_tmpl),
        "prompt_tokens_est": prompt_tokens_est, "prefill_tps": charging.prefill_tps(),
        "local_cli_profile": cli_profile,
        "worktree": worktree, "branch": branch, "model": args.model, "effort": args.effort,
        "grant_sha256": grant_sha,
        "observed_model": state.model,
        "model_downgraded": model_downgraded,
        "model_swap_detail": model_swap_detail,
        "archetype": archetype, "max_turns": max_turns, "wall_clock_s": wall_clock,
        "classification": classification, "raw_classification": raw_classification,
        "exit_code": exit_code, "cpu_s": cpu_s, "oauth_token_used": oauth_token_used,
        "oom_journal": oom_journal,
        "killed_reason": killed_reason, "loop_reason": loop_tracker.tripped,
        "exit_gate": gate_info,  # wq#5007
        "turns": state.turns,
        "session_id": state.session_id, "resumed_from": resume_session,
        "saw_limit": state.saw_limit, "latest_resets_at": state.latest_resets_at,
        "compact_boundaries": state.compact_boundaries,
        # wq#617 refusal awareness: where refused turns landed (§C), for the orchestrator
        "refusal_fallbacks": state.refusal_fallbacks,
        "refusal_no_fallback": state.saw_refusal_no_fallback,
        "model_fallbacks": state.model_fallbacks, "api_retries": state.api_retries,
        "result_subtype": state.result_subtype, "elapsed_s": elapsed,
        "started_at": t0, "finished_at": time.time(),
        "total_cost_usd": state.total_cost_usd, "duration_ms": state.duration_ms,
        # wq#1888 despag P2-2: canary measurability — getattr, not state.foo, since
        # a plain run_child() attempt never sets these (only run_child_gateway does).
        "gateway_backend": getattr(state, "gateway_backend", False),
        "gateway_fallback": getattr(state, "gateway_fallback", False),
        "gateway_fallback_reason": getattr(state, "gateway_fallback_reason", None),
        "runtime": getattr(state, "runtime", "claude"),
        "aider_model": AIDER_MODEL if aider_runtime else None,
    }
    state_path = write_attempt_state(ticket_id, attempt, attempt_record)
    mark_attempt_written()  # wq#1894: attempt-N.json landed — SIGTERM no longer our job
    emit_attempt_span(ticket_id, ticket.get("lane"), attempt_record)

    # wq#704/wq#706/wq#736: usage sidecar, alongside attempt-N.json rather than folded
    # into it — a distinct file so token-audit tooling (SESSION_ECONOMICS.md) can glob
    # RUNS_ROOT/wq*/usage-*.json without parsing the full attempt record.
    usage_record = {
        "ticket": ticket_id, "attempt": attempt, "worker_id": worker_id,
        "session_id": state.session_id,
        "classification": classification, "killed_reason": killed_reason,
        "started_at": t0, "finished_at": time.time(), "elapsed_s": elapsed,
        "turns": state.turns, "result_num_turns": state.num_turns,
        "usage": state.usage, "total_cost_usd": state.total_cost_usd,
        "duration_ms": state.duration_ms,
        "rate_limits_start": rate_limits_start, "rate_limits_exit": rate_limits_exit,
    }
    write_usage_sidecar(ticket_id, attempt, usage_record)

    # wq#1183 episodic memory: one attempt_end event per attempt into the
    # append-only per-run episode log. It carries the narrative (result text +
    # assistant tail) alongside the mechanical outcome — the input the wq#1635
    # archival commit digests from. Best-effort.
    append_episode_event(ticket_id, {
        "type": "attempt_end", "attempt": attempt, "worker_id": worker_id,
        "lane": ticket.get("lane"), "title": ticket.get("title"),
        "classification": classification,
        "raw_classification": raw_classification,
        "killed_reason": killed_reason, "model": args.model,
        "observed_model": state.model, "turns": state.turns,
        "elapsed_s": round(elapsed, 1), "session_id": state.session_id,
        "result_text": (state.result_text
                        if isinstance(state.result_text, str) else "")[:4000],
        "assistant_tail": assistant_text_tail(events),
        # wq#4707: so the next burn-down can separate "killed while its own background
        # task was still running" from a genuine post-result hang without re-reading
        # assistant tails for the phrase "waiting for the background".
        "bg_tasks_started": getattr(state, "bg_tasks_started", 0),
        "bg_tasks_pending": getattr(state, "bg_tasks_pending", 0),
    })
    commit_episode_to_archive(ticket_id)

    # wq#749: reconcile()'s SUCCESS/DEGRADED_SUCCESS branch calls `wq done` — wrong
    # for a ticket the worker itself already blocked. Terminal here, no retry, no
    # reconcile: the ticket already carries the worker's own verdict.
    # ISSUE-LOG #17 §1d: the attempt ceiling. verify_done() has already blocked the
    # ticket for Frank; dispatching a fourth attempt at criteria three attempts could
    # not prove is the churn this gate exists to stop.
    if evidence_escalated:
        log(f"BLOCKED: wq#{ticket_id} escalated by the evidence gate — repeated attempts "
            f"could not prove the pinned criteria. No further dispatch.")
        return EXIT_BLOCKED

    if sentinel_confirmed_block:
        log(f"BLOCKED: ticket already carries the worker's own block (wq#749 sentinel "
            f"confirmed by status={sentinel_status!r}) — not a false complete, no retry.")
        ntfy("foreman BLOCKED", f"wq#{ticket_id} blocked by worker", priority="high")
        return EXIT_BLOCKED
    if sentinel_confirmed_claim_error:
        log(f"CLAIM_ERROR: worker reported a claim race on wq#{ticket_id}, corroborated "
            f"by the claim leaving {claim_holder_at_start!r} (now {sentinel_holder!r}, "
            f"status={sentinel_status!r}) — releasing, no retry (wq#749, wq#869).")
        wq_release(ticket_id)
        return EXIT_CLAIM_ERROR

    return reconcile(args, ticket_id, attempt, classification, state, events,
                      worktree, state_path, charged_to=charged_to,
                      failure_cause=failure_cause)


def file_verify_pass(ticket_id, args):
    """wq#617 (T3): a shipped diff gets its adversarial verify-pass ticket filed by the
    orchestrator tier (orchestrator/judgment.py; policy in research/models.yaml
    verify_pass:). Best-effort — a filing failure is logged, never a harness failure."""
    if os.environ.get("FOREMAN_NO_VERIFY_PASS"):
        return
    cmd = [sys.executable, JUDGMENT_CLI, "verify-pass", "--for", str(ticket_id)]
    if getattr(args, "model", None):
        cmd += ["--author-model", args.model]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        log(f"verify-pass filing: rc={p.returncode} {p.stdout.strip()[:300]}")
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"verify-pass filing skipped: {e}")


def resolve_tuple_from_ticket(args):
    """wq#617: --model/--effort/--archetype omitted -> read the tuple the orchestrator wrote
    into the ticket (wq model/effort/archetype columns); if the ticket carries none, ask the
    router to assign one now. Explicit flags always win."""
    if args.model and args.archetype:
        return
    t = wq_show(args.ticket)
    def col(k):
        v = t.get(k)
        return None if v in (None, "", "None") else v
    model, effort, arch = col("model"), col("effort"), col("archetype")
    if not model:
        try:
            p = subprocess.run([sys.executable, ROUTER_CLI, "assign", str(args.ticket)],
                               capture_output=True, text=True, timeout=60)
            d = json.loads(p.stdout or "{}")
            if d.get("rung"):
                model, effort, arch = d["rung"]["model"], d["rung"]["effort"], d["archetype"]
                log(f"router assigned wq#{args.ticket}: {model}@{effort} ({arch})")
        except (OSError, subprocess.TimeoutExpired, ValueError) as e:
            log(f"router assign failed ({e}); falling back to CLI defaults")
    if not args.model and model:
        args.model, args.effort = model, effort
    if not args.archetype and arch:
        args.archetype = arch
    if not args.model:
        args.model, args.effort = "claude-sonnet-5", args.effort or "high"


# ---------------------------------------------------------------------------
# evidence-gated done — ISSUE-SOLUTIONS-fable.md §1 (ISSUE-LOG #17)
# ---------------------------------------------------------------------------

REJECTION_MARKER = "evidence-rejected.json"


def evidence_gate_on():
    return os.environ.get("FOREMAN_EVIDENCE_GATE", "1").lower() not in ("0", "false", "no")


def _rejection_path(ticket_id):
    return os.path.join(run_dir(ticket_id), REJECTION_MARKER)


def evidence_rejected(ticket_id):
    """True once this ticket's evidence has failed replay and has not since passed.
    check_artifacts_done() consults it so a landed-looking artifact (a commit whose
    message names the ticket) can never resurrect a done the harness already refused."""
    return os.path.isfile(_rejection_path(ticket_id))


def _clear_rejection(ticket_id):
    try:
        os.remove(_rejection_path(ticket_id))
    except OSError:
        pass


def _acceptance_model_call():
    """The spec's one cheap model call, opt-in via FOREMAN_ACCEPTANCE_MODEL. Unset (the
    default) means criteria are derived deterministically from the ticket text — no
    quota spent per dispatch, and no derivation that can fail on a rate limit."""
    model = os.environ.get("FOREMAN_ACCEPTANCE_MODEL")
    if not model:
        return None

    def call(prompt):
        p = subprocess.run(["claude", "-p", prompt, "--model", model],
                           capture_output=True, text=True, timeout=180)
        if p.returncode != 0:
            raise RuntimeError(f"derivation model exited {p.returncode}: {p.stderr[:200]}")
        return p.stdout
    return call


def pin_acceptance(ticket_id, ticket, worktree, attempt):
    """§1a: render the ticket's machine-checkable criteria into the worktree and pin
    them by hash in the fworker-owned run dir before the worker starts. Re-pinning on a
    later attempt reuses the ORIGINAL doc — the goalposts do not move between attempts,
    and a worker that edited its worktree copy gets the pinned copy back. Returns the
    doc actually in force (None when the gate is off or nothing checkable was derived).
    """
    if not evidence_gate_on():
        return None
    rd = run_dir(ticket_id)
    doc = acceptance_mod.pinned_doc(rd)
    if not doc:
        try:
            doc = acceptance_mod.derive(dict(ticket, id=ticket_id),
                                         model_call=_acceptance_model_call())
        except Exception as e:
            log(f"WARN: acceptance derivation failed ({e}) — dispatching ungated")
            return None
    if not doc.get("criteria"):
        return None
    try:
        path, digest = acceptance_mod.pin(worktree, rd, doc)
    except OSError as e:
        log(f"WARN: could not pin acceptance.json ({e}) — dispatching ungated")
        return None
    log(f"wq#{ticket_id} attempt {attempt}: acceptance pinned {digest[:12]} "
        f"({len(doc['criteria'])} criteria, {doc.get('derived_by')}) -> {path}")
    return doc


def _reclaim_after_revert(ticket_id):
    """Undo a premature self-`wq done`. release+claim rather than a bare release: the
    row must not sit unclaimed while this fworker is still working it, and wq bumps the
    row's `attempt`/`fence` on the re-claim — which is exactly the attempt counter §1d
    escalates on."""
    if wq_show(ticket_id).get("status") == "done":
        wq_release(ticket_id)
        ok, msg = wq_claim(ticket_id)
        if not ok:
            log(f"WARN: re-claim after evidence rejection failed: {msg.strip()}")


def verify_done(ticket_id, worktree, attempt, base_ref="origin/main"):
    """§1b: the harness's own verdict on a done claim. Re-runs every `test` criterion
    here and re-checks every citation against the diff; the worker's logged exit code is
    a hint, never proof. On failure the ticket goes to rework (or, at
    FOREMAN_EVIDENCE_MAX_ATTEMPTS row attempts, escalates to Frank) — never to done.

    Returns (ok, escalated).
    """
    if not evidence_gate_on():
        return True, False
    rd = run_dir(ticket_id)
    pinned = acceptance_mod.pinned_doc(rd)
    if not pinned or not pinned.get("criteria"):
        return True, False

    # Tamper detection reads the WORKTREE copy specifically — that is the worker's only
    # writable copy, so it is the one an edit would show up in; run_dir's copy is never
    # touched after pin() and would silently hide the tamper if checked instead (wq#1905).
    live = acceptance_mod.read_acceptance(worktree)
    tampered = live is not None and acceptance_mod.doc_hash(live) != acceptance_mod.doc_hash(pinned)
    # wq#1905: snapshot the worker's evidence.json into run_dir (outside its sandbox,
    # never git-tracked) before reading it — run_dir becomes the actual replay source,
    # the worktree copy only a fallback for when the snapshot itself couldn't be taken.
    acceptance_mod.snapshot_evidence(worktree, rd)
    evidence = acceptance_mod.read_evidence(worktree, run_dir=rd)
    ev_dir = os.path.join(rd, "evidence", f"attempt-{attempt}")
    # Diff against the sha origin/main pointed at when this ticket was dispatched, not
    # the live ref: if this ticket's own PR has since merged, HEAD is now an ancestor of
    # live origin/main and the diff would collapse to empty (ISSUE-LOG #143/#156).
    frozen_ref = acceptance_mod.pinned_origin_sha(rd) or base_ref
    result = acceptance_mod.verify(pinned, evidence, worktree, base_ref=frozen_ref,
                                    evidence_dir=ev_dir,
                                    claim_check=hallucination_guard.claim_checker())
    cc = result.get("claim_check")
    if cc and (cc["flagged"] or cc["inconclusive"]):
        log(f"wq#{ticket_id}: hallucination guard ({cc['mode']}): "
            f"{len(cc['flagged'])} flagged, {cc['inconclusive']} inconclusive, "
            f"{cc.get('skipped', 0)} skipped of {len(cc['checked'])} claims")
    if tampered:
        result["ok"] = False
        result["failed"].insert(0, {
            "id": "PIN", "text": "acceptance.json is pinned at dispatch",
            "expected": f"sha256 {acceptance_mod.doc_hash(pinned)[:12]}",
            "observed": f"sha256 {acceptance_mod.doc_hash(live)[:12]} — the worktree copy "
                        f"was edited; the pinned criteria still govern",
            "repro": f"sha256sum {os.path.join(worktree, acceptance_mod.ACCEPTANCE_FILENAME)}",
            "output_tail": ""})

    if result["ok"]:
        _clear_rejection(ticket_id)
        followups = result.get("root_apply_followups")
        if followups:
            lane = wq_show(ticket_id).get("lane") or "build"
            filed = file_root_apply_followups(ticket_id, lane, followups)
            if filed:
                log(f"wq#{ticket_id}: wq#4778 filed/reused root-apply follow-up(s) "
                    f"{', '.join(f'wq#{i}' for i in filed)} for no_deploy_needed citation(s)")
        log(f"wq#{ticket_id}: evidence replay PASSED "
            f"({len(result['checked'])} criteria re-run/re-checked by the harness)")
        return True, False

    try:
        os.makedirs(ev_dir, mode=0o700, exist_ok=True)
        # wq#1905: source the just-snapshotted run_dir copy, not the worktree — it is
        # the one this replay actually verified against.
        src = os.path.join(rd, acceptance_mod.EVIDENCE_FILENAME)
        if os.path.isfile(src):
            dst = os.path.join(ev_dir, acceptance_mod.EVIDENCE_FILENAME)
            shutil.copy2(src, dst)
            os.chmod(dst, 0o600)
    except OSError:
        pass

    row_attempt = 0
    try:
        row_attempt = int(wq_show(ticket_id).get("attempt") or 0)
    except ValueError:
        pass
    doc = acceptance_mod.rework_doc(ticket_id, attempt, result, prior_evidence_dir=ev_dir)
    rework_path = acceptance_mod.write_rework(rd, doc)
    acceptance_mod.write_json(_rejection_path(ticket_id),
                               {"attempt": attempt, "failed": [f["id"] for f in result["failed"]]})
    ids = ", ".join(f.get("id", "?") for f in result["failed"])
    log(f"wq#{ticket_id}: evidence replay REJECTED (unproven: {ids}) — rework at "
        f"{rework_path}; the claim never reaches done.")

    max_attempts = _envint("FOREMAN_EVIDENCE_MAX_ATTEMPTS", 3)
    if row_attempt >= max_attempts:
        reason = (f"evidence gate: {row_attempt} attempts, still unproven ({ids}). "
                  f"Machine replay of evidence.json failed each time — see {rework_path}. "
                  f"Escalating instead of dispatching again (ISSUE-LOG #17).")
        wq_block(ticket_id, reason)
        ntfy("foreman evidence gate", f"wq#{ticket_id} escalated after {row_attempt} "
                                       f"unproven attempts", priority="high")
        return False, True
    _reclaim_after_revert(ticket_id)
    return False, False


def check_artifacts_done(ticket_id, worktree):
    """RELIABILITY_SPEC.md §C10/§C12: never trust the model's own self-report (a
    result:success frame, or a silent/odd frame shape) — verify against artifacts.
    Primary: wq status==done (the worker calls `wq done` itself on a real finish, the
    most direct signal there is). Secondary corroboration: a commit on the default
    branch referencing the ticket, for the case where real work landed but the worker
    died/got cut off before it could close the ticket itself.

    ISSUE-LOG #17: a ticket whose evidence already failed harness replay is never
    "confirmed by artifacts" — a landed-looking commit message is exactly the artifact a
    FALSE_COMPLETE produces, and letting it back in here would hand the rejected claim a
    second door into `done`.

    Returns (confirmed, commit_sha). wq#1925: the sha is the citable artifact this check
    just proved exists — a caller that goes on to close the ticket has to name it in
    --notes (wq#759's gate), and only this function knows what it found. commit_sha is
    None when the confirmation is the ticket's own status, i.e. it is already `done` and
    there is nothing left to close."""
    if evidence_rejected(ticket_id):
        return False, None
    cur = wq_show(ticket_id)
    if cur.get("status") == "done":
        return True, None
    try:
        # a landed PR merges via lumen-pr's remote API call, not a local fetch — the
        # worktree's cached origin/main can be stale, which would false-negative a
        # real landing. Best-effort refresh first; a failed/slow fetch just leaves the
        # (possibly stale) local ref, same conservative fallthrough as no signal at all.
        subprocess.run(["git", "-C", worktree, "fetch", "-q", "origin", "main"],
                        capture_output=True, text=True, timeout=15)
    except Exception:
        pass
    try:
        p = subprocess.run(
            ["git", "-C", worktree, "log", "origin/main", "--format=%H", "-n", "1",
             "--grep", rf"wq#{ticket_id}\b"],
            capture_output=True, text=True, timeout=15)
        if p.returncode == 0 and p.stdout.strip():
            sha = p.stdout.split()[0]
            # wq#3035 (wq#2922 root cause): a commit merely NAMING this ticket is not
            # proof the CITED fix landed — a docs/progress commit mentioning "wq#N"
            # satisfies this grep exactly as well as the real fix does. Re-check every
            # proof_type=citation criterion's evidence against origin/main itself
            # (never the branch diff verify() uses, which is legitimately still open
            # mid-attempt) before trusting the grep hit.
            rd = run_dir(ticket_id)
            pinned = acceptance_mod.pinned_doc(rd)
            if pinned and pinned.get("criteria"):
                evidence = acceptance_mod.read_evidence(worktree, run_dir=rd)
                landed_ok, failed = acceptance_mod.citations_landed(pinned, evidence, worktree)
                if not landed_ok:
                    reasons = "; ".join(f"{f['id']}: {f['why']}" for f in failed)
                    log(f"wq#{ticket_id}: commit {sha[:12]} mentions this ticket, but "
                        f"citation evidence does not resolve on origin/main ({reasons}) "
                        f"— not confirmed done")
                    return False, None
            return True, sha
    except Exception:
        pass
    return False, None


def handle_resumable_failure(args, ticket_id, attempt, classification, state, events, worktree):
    """RELIABILITY_SPEC.md §C7/§C8/§C10/§C11: FALSE_COMPLETE, INDETERMINATE, HANG
    (§C8's STALL) and AUTH all get a ground-truth artifact check FIRST — §C7's
    "verify artifacts, re-verdict" applies just as much to a dropped-result
    drain-death as to an explicit unmet-criteria success frame (do_attempt() already
    ran this same check for the SUCCESS/DEGRADED_SUCCESS case before relabeling it
    FALSE_COMPLETE; re-running it here is what makes INDETERMINATE/HANG/AUTH's own
    "verify artifacts, re-verdict" requirement real instead of just resuming blind).
    If artifacts don't confirm completion, these four share one more property: a live
    session_id worth resuming rather than a launch that needs a fresh worktree (AUTH
    resumes so the next `claude` invocation's own OAuth refresh — BUILD_PLAN.md §2 —
    gets a chance to clear it; HANG/STALL resumes past a stall instead of restarting
    real progress from zero). Falls through to handle_failure's block-after-
    MAX_ATTEMPTS path once attempts are exhausted or there is no session_id to resume.
    """
    confirmed, landed_sha = check_artifacts_done(ticket_id, worktree)
    if confirmed and getattr(state, "exit_gate_explicit_false", False):
        # wq#5007: the worker itself said EXIT_SIGNAL: false on a ticket that is not
        # done — a commit naming the ticket is not a completion over its objection.
        log(f"{classification}: a landed commit names wq#{ticket_id} but the worker "
            f"reported EXIT_SIGNAL: false — not closing on the artifact alone.")
        confirmed = False
    if confirmed:
        log(f"{classification} but artifacts confirm the ticket actually landed "
            f"(session {state.session_id}) — verifying beats trusting the missing/odd "
            f"frame shape.")
        # wq#1925: cite the commit the check just found. A landed_sha means the ticket is
        # still open (the already-`done` case returns None), and prose-only notes would be
        # refused by wq#759's gate even though the artifact is right here.
        if landed_sha:
            wq_done(ticket_id, f"fworker attempt {attempt}: verified via artifacts "
                                f"after {classification} (session {state.session_id}) "
                                f"- landed commit {landed_sha}")
        file_verify_pass(ticket_id, args)
        return EXIT_OK
    write_prior_attempt_notes(ticket_id, attempt, worktree, events, classification)
    if charged_attempt_count(ticket_id, attempt) < MAX_ATTEMPTS and state.session_id:
        log(f"{classification}: attempt {attempt} < {MAX_ATTEMPTS}, resuming session "
            f"{state.session_id}")
        return do_attempt(args, attempt + 1, resume_session=state.session_id,
                           prior_worktree=worktree)
    return handle_failure(args, ticket_id, attempt, classification, state, events, worktree)


def reconcile(args, ticket_id, attempt, classification, state, events, worktree, state_path,
              charged_to=None, failure_cause=None):
    # SUCCESS/DEGRADED_SUCCESS only ever reach here when do_attempt() already verified
    # artifacts confirm the ticket landed (else it relabeled the classification
    # FALSE_COMPLETE before calling reconcile at all) — safe to trust directly.
    if classification in (SUCCESS, DEGRADED_SUCCESS):
        ticket = wq_show(ticket_id)
        if ticket.get("status") != "done":
            wq_done(ticket_id, f"fworker attempt {attempt} ({classification}): {state_path}")
        else:
            log(f"{classification}: worker already ran `wq done`.")
        record_recipe_outcome(ticket, args, success=True)
        file_verify_pass(ticket_id, args)
        return EXIT_OK

    if classification == LIMIT:
        return handle_limit(args, ticket_id, attempt, state, worktree)

    if classification == MODEL_NOT_FOUND:
        return handle_model_not_found(args, ticket_id, attempt, state, worktree)

    # wq#3539: an org-level subscription revocation page Frank the moment it's seen,
    # not only once the breaker later trips 9/9 on the symptom — the cause is in the
    # text, right here, before it's diluted into an undifferentiated failure-rate
    # number. Falls through to the ordinary handle_failure() path below (INFRA_CRASH
    # bucket) same as LAUNCH_FAILURE/KILLED/OOM.
    if classification == AUTH_DENIED:
        ntfy("foreman AUTH_DENIED",
             f"wq#{ticket_id}: {state.result_text or 'Claude subscription access denied'} "
             f"— this is an account/org-level denial, not a per-ticket failure. "
             f"self_heal will re-probe and clear DISPATCH_DISARM once access returns.",
             priority="urgent")

    # wq#2128: the backend failed, not the ticket — never the resume/retry ladder below.
    if charged_to == charging.CHARGE_RUNG:
        return handle_failure(args, ticket_id, attempt, classification, state, events,
                               worktree, charged_to=charged_to, failure_cause=failure_cause)

    if classification in (FALSE_COMPLETE, INDETERMINATE, AUTH, HANG):
        return handle_resumable_failure(args, ticket_id, attempt, classification, state,
                                         events, worktree)

    # TURNS / LAUNCH_FAILURE / KILLED / OOM / REFUSED / BUDGET / CRASH
    return handle_failure(args, ticket_id, attempt, classification, state, events, worktree)


def handle_limit(args, ticket_id, attempt, state, worktree):
    resets_at = state.latest_resets_at
    now = time.time()
    resets_epoch = None
    if isinstance(resets_at, (int, float)):
        resets_epoch = float(resets_at)
    elif isinstance(resets_at, str):
        try:
            resets_epoch = time.mktime(time.strptime(resets_at[:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            resets_epoch = None

    wait_s = (resets_epoch - now) if resets_epoch else None
    if wait_s is not None and 0 <= wait_s <= RESUME_WAIT_MAX_S and state.session_id:
        log(f"LIMIT: sleeping {wait_s:.0f}s then resuming session {state.session_id}")
        # wq#1911: up to RESUME_WAIT_MAX_S (6h) of no child in flight and no attempt
        # pending -- _SIGTERM_CTX["written"] is still stuck True from this attempt's
        # own LIMIT record, so without this the handler silently no-op'd here and the
        # claim sat until lease expiry on an external stop (the wq#1852 bug, relocated).
        set_sigterm_idle(ticket_id)
        slept = 0
        while slept < wait_s:
            chunk = min(600, wait_s - slept)
            time.sleep(chunk)
            slept += chunk
            wq_heartbeat(ticket_id)
        return do_attempt(args, attempt, resume_session=state.session_id,
                           prior_worktree=worktree)

    log(f"LIMIT: no boundable reset window (resets_at={resets_at}) — releasing ticket, "
        f"exiting so the orchestrator reschedules after reset.")
    wq_release(ticket_id)
    ntfy("foreman LIMIT", f"wq#{ticket_id} paused on rate limit, resets_at={resets_at}",
         priority="high")
    return EXIT_LIMIT


def record_terminal_failure(ticket_id, attempt, classification, state, events,
                             worktree, expected_repo, archetype,
                             worker_block_reason=None, lane=None, needs_dirs=None):
    """wq#1330 (WORKER_EXEC_CAPABILITY_GAP.md §4): write _runs/wq<id>/failure.json
    (schema v1, mechanical triage), fold its sha256 into this attempt's
    attempt-N.json, and emit a failure span on the ticket's Langfuse trace.
    Returns (record, failure_path). Best-effort on the sidecars — the block itself
    must never be lost to a bookkeeping error."""
    block_class = classify_block_reason(classification)
    d = run_dir(ticket_id)
    attempt_files = sorted(f for f in os.listdir(d) if re.match(r"attempt-\d+\.json$", f))
    try:
        # wq#1331: a dir granted via needs:dir was reachable — without it triage would
        # wrongly re-propose the tag the ticket already carried.
        reachable = [worktree] + list(add_dirs_for(archetype or "standard-engineering",
                                                    needs_dirs)) + scratch_grant_dirs(ticket_id)
    except Exception:
        reachable = [worktree]
    rec = failure_record.build_failure_record(
        ticket=ticket_id, attempts=attempt, classification=classification,
        block_class=block_class, events=events or [], worktree=worktree,
        expected_repo=expected_repo,
        session_id=(state.session_id if state else None),
        trip_kind=("PERMISSION" if classification == PERMISSION_LOOP else None),
        worker_block_reason=worker_block_reason,
        attempt_files=attempt_files, reachable_roots=reachable)
    fpath = failure_record.write_failure_record(d, rec)
    fsha = failure_record.sha256_file(fpath)
    apath = os.path.join(d, f"attempt-{attempt}.json")
    try:
        with open(apath) as f:
            arec = json.load(f)
        arec.update({"triage": rec["triage"], "failure_json_path": fpath,
                     "failure_json_sha256": fsha})
        write_attempt_state(ticket_id, attempt, arec)
    except (OSError, ValueError) as e:
        log(f"WARN: failure.json sha not folded into {apath}: {e}")
    try:
        emit_trace_with_span(
            LANGFUSE_ENV,
            trace_id=f"fworker-wq{ticket_id}",
            trace_name=f"fworker:wq{ticket_id}",
            span_name=f"failure-{attempt}:{rec['triage']}",
            metadata={"ticket": ticket_id, "lane": lane, "attempt": attempt,
                      "triage": rec["triage"], "classification": classification,
                      "block_class": block_class,
                      "inferred_needs": ",".join(rec["inferred_needs"]),
                      "failure_json_path": fpath, "failure_json_sha256": fsha},
            tags=["fworker", "failure", lane or "unknown"],
            trace_metadata={"ticket": ticket_id, "lane": lane})
    except Exception as e:  # noqa: BLE001 — best-effort by contract (§8)
        log(f"langfuse_emit_failed (failure span): {e!r}")
    return rec, fpath


def handle_unhandled_crash(args, ticket_id, attempt, tb, context="attempt"):
    """wq#1671: an unhandled exception reaching cmd_run's outer try/except means
    do_attempt() (or run_fire_ticket()) died before its OWN classify/write_attempt_state
    ever ran -- the ticket was claimed (wq column bumped) but nothing on disk says so:
    no attempt-N.json (self_heal.attempt_count/runaway.stuck_ticket_ids never see it) and,
    until this fix, no failure.json either (runaway.harness_defect_ticket_ids, wq#1415,
    never sees it). Both existing breakers are blind and the ticket just gets reclaimed
    every pass. Bucketed HARNESS_DEFECT like handle_spawn_refused (wq#1331) -- same
    reasoning: the run says nothing about the model or the ticket, only that the harness
    broke before real work started, so attempt-N.json stays deliberately UNwritten (must
    not eat into RUNAWAY_MAX_ATTEMPTS/circuit_breaker budget for a crash that has nothing
    to do with the ticket's own difficulty) -- but failure.json now IS written, so
    harness_defect_ticket_ids() holds the ticket open instead of respawning it blind,
    exactly like a SpawnRefused. dispatcher/runaway.py's wq-attempt-column check (also
    wq#1671) is the backstop for what even this handler cannot reach: a hard external
    kill (OOM/SIGKILL) that runs no Python except block at all."""
    log(f"CRASH: unhandled exception during wq#{ticket_id} {context} — releasing claim.\n{tb}")
    try:
        d = run_dir(ticket_id)
        rec = failure_record.build_failure_record(
            ticket=ticket_id, attempts=0, classification=LAUNCH_FAILURE,
            block_class=BLOCK_HARNESS_DEFECT, events=[], worktree=None,
            expected_repo=getattr(args, "repo", None), check_worktree=False)
        rec["triage"] = failure_record.TRIAGE_HARNESS_DEFECT
        rec["summary"] = f"unhandled exception in {context}: {tb.strip().splitlines()[-1][:200]}"
        fpath = failure_record.write_failure_record(d, rec)
        log(f"failure.json (uncharged, HARNESS_DEFECT) -> {fpath}")
    except Exception as fe:  # noqa: BLE001 — this handler must never itself block the release
        log(f"WARN: failed to write crash failure record for wq#{ticket_id}: {fe!r}")
    wq_release(ticket_id)
    ntfy("foreman CRASH",
         f"wq#{ticket_id} {context} crashed (claim released): "
         f"{tb.strip().splitlines()[-1]}", priority="high")
    print("CRASH")
    return EXIT_CRASH


def handle_spawn_refused(args, ticket_id, e):
    """wq#1331 (§3.2): a SpawnRefused preflight is LAUNCH_FAILURE with the refusal
    reason, bucketed HARNESS_DEFECT like PERMISSION_LOOP (wq#1107) — the run says
    nothing about the model or the ticket. No attempt-N.json is written, so
    next_attempt_n() recomputes the same attempt next dispatch: the attempt is not
    charged (and never reaches the breaker — HARNESS_DEFECT is outside
    circuit_breaker.CATASTROPHIC_CLASSES). failure.json still lands so slice-1
    triage/measurement sees the refusal; the ticket goes back to open with a notes
    line and ntfy pages lumen-fleet."""
    d = run_dir(ticket_id)
    rec = failure_record.build_failure_record(
        ticket=ticket_id, attempts=0, classification=LAUNCH_FAILURE,
        block_class=BLOCK_HARNESS_DEFECT, events=[], worktree=e.worktree,
        expected_repo=args.repo, spawn_refusal=e)
    rec["triage"] = failure_record.TRIAGE_HARNESS_DEFECT
    rec["summary"] = e.summary
    fpath = failure_record.write_failure_record(d, rec)
    log(f"LAUNCH_FAILURE [{e.code}] wq#{ticket_id}: {e.summary} — releasing claim, "
        f"attempt not charged; failure.json -> {fpath}")
    p = wq("note", str(ticket_id),
           f"LAUNCH_FAILURE {e.summary}; attempt not charged (harness defect); see {fpath}")
    if p.returncode != 0:
        log(f"WARN: wq note append failed: {(p.stdout or p.stderr).strip()}")
    wq_release(ticket_id)
    ntfy("foreman LAUNCH_FAILURE",
         f"wq#{ticket_id} {e.code}: {e.summary} (attempt not charged)", priority="high")
    return EXIT_LAUNCH_REFUSED


def handle_model_not_found(args, ticket_id, attempt, state, worktree):
    """wq#1442 (evidence wq#1436/wq#1434: --model opus-5, died in 1.9s/1 turn/$0 cost,
    result_text "There's an issue with the selected model (opus-5)..."). Bucketed
    HARNESS_DEFECT like handle_spawn_refused (wq#1331) and PERMISSION_LOOP (wq#1107):
    the CLI rejected the --model string fworker itself dispatched, so the run is no
    evidence about the ticket, and retrying hits the identical 404. Unlike the
    ordinary HARNESS_DEFECT path (which still burns up to MAX_ATTEMPTS before the
    terminal block), this attempt is uncharged outright — do_attempt() already wrote
    attempt-{attempt}.json/usage-{attempt}.json before classify() ran, so they're
    removed here the same way handle_spawn_refused simply never writes them, and
    next_attempt_n() recomputes this same slot next dispatch."""
    d = run_dir(ticket_id)
    rec = failure_record.build_failure_record(
        ticket=ticket_id, attempts=0, classification=MODEL_NOT_FOUND,
        block_class=BLOCK_HARNESS_DEFECT, events=[], worktree=worktree,
        expected_repo=args.repo, session_id=state.session_id if state else None)
    rec["triage"] = failure_record.TRIAGE_HARNESS_DEFECT
    rec["summary"] = f"model {args.model!r} rejected by the CLI (model_not_found/404)"
    fpath = failure_record.write_failure_record(d, rec)
    for fname in (f"attempt-{attempt}.json", f"usage-{attempt}.json"):
        try:
            os.remove(os.path.join(d, fname))
        except OSError:
            pass
    log(f"MODEL_NOT_FOUND wq#{ticket_id}: model {args.model!r} rejected by the CLI — "
        f"releasing claim, attempt not charged; failure.json -> {fpath}")
    p = wq("note", str(ticket_id),
           f"MODEL_NOT_FOUND: dispatched model {args.model!r} rejected by the CLI "
           f"(model_not_found/404); attempt not charged (harness defect) — canonicalize "
           f"this ticket's model column (wq#1442) before redispatching; see {fpath}")
    if p.returncode != 0:
        log(f"WARN: wq note append failed: {(p.stdout or p.stderr).strip()}")
    wq_release(ticket_id)
    ntfy("foreman MODEL_NOT_FOUND",
         f"wq#{ticket_id}: model {args.model!r} rejected by CLI (attempt not charged)",
         priority="high")
    return EXIT_MODEL_NOT_FOUND


def charged_attempt_count(ticket_id, attempt):
    """wq#2128: attempts that count toward MAX_ATTEMPTS. A rung- or harness-charged
    attempt does not; a record written before charged_to existed is a ticket charge.
    FOREMAN_CHARGE_MODE=legacy: the attempt number itself, as before."""
    if charging.legacy_mode():
        return attempt
    d = run_dir(ticket_id)
    recs = []
    for f in os.listdir(d):
        if re.match(r"attempt-\d+\.json$", f):
            try:
                with open(os.path.join(d, f)) as fh:
                    recs.append(json.load(fh))
            except (OSError, ValueError):
                recs.append({})
    return max(1, charging.charged_attempts(recs))


def handle_rung_fault(args, ticket_id, attempt, classification, cause, state, events,
                       worktree):
    """wq#2128: charged_to == rung. The backend failed (hang before any turn, LiteLLM
    5xx/timeout/cooldown, unreachable, ctx overflow), so the attempt says nothing about
    the ticket: cool the backend down, release the ticket to open, do not retry here (the
    same backend would fail the same way) and do not count it toward MAX_ATTEMPTS.
    If the ticket is already blocked (the worker blocked it itself) that block stands, but
    its reason gets the RUNG_FAULT: prefix so nobody reads it as a work block."""
    confirmed, _ = check_artifacts_done(ticket_id, worktree)
    if confirmed:
        return handle_resumable_failure(args, ticket_id, attempt, classification, state,
                                         events, worktree)
    backend = backend_key(args.model)
    try:
        entry = charging.mark_backend_cooldown(BACKEND_COOLDOWN_FILE, backend, cause)
        cooldown = f"{backend} cooling until {entry['until']:.0f} (strike {entry['strikes']})"
    except OSError as e:
        cooldown = f"{backend} cooldown NOT recorded ({e})"
        log(f"WARN: wq#2128 backend cooldown write failed: {e}")
    line = (f"{charging.RUNG_FAULT_PREFIX} {classification}/{cause} on backend {backend}; "
            f"attempt {attempt} not charged to the ticket; {cooldown}")
    log(line)
    ticket = wq_show(ticket_id)
    if ticket.get("status") == "blocked":
        prior = ticket.get("block_reason") or ticket.get("notes") or ""
        wq_block(ticket_id, charging.rung_fault_reason(
            prior if prior != "None" else f"{classification}/{cause} on backend {backend}"))
    else:
        p = wq("note", str(ticket_id), line)
        if p.returncode != 0:
            log(f"WARN: wq note append failed: {(p.stdout or p.stderr).strip()}")
        wq_release(ticket_id)
    ntfy("foreman RUNG_FAULT", f"wq#{ticket_id}: {classification}/{cause} on {backend} "
         f"(attempt not charged)")
    return EXIT_RUNG_FAULT


def handle_failure(args, ticket_id, attempt, classification, state, events, worktree,
                    charged_to=None, failure_cause=None):
    prior_notes_path = write_prior_attempt_notes(ticket_id, attempt, worktree, events,
                                                  classification)
    if charged_to == charging.CHARGE_RUNG:
        return handle_rung_fault(args, ticket_id, attempt, classification, failure_cause,
                                  state, events, worktree)
    if charged_attempt_count(ticket_id, attempt) < MAX_ATTEMPTS:
        log(f"{classification}: attempt {attempt} < {MAX_ATTEMPTS}, restarting fresh worktree")
        return do_attempt(args, attempt + 1)

    # wq#1330: terminal failure — machine-readable record + mechanical triage. If the
    # worker already blocked the ticket itself (the good path), that reason wins:
    # triage copies it (REAL_BLOCK) and the ticket text is left untouched.
    ticket = wq_show(ticket_id)
    worker_reason = None
    if ticket.get("status") == "blocked":
        worker_reason = ticket.get("block_reason") or ticket.get("notes") or None
        if worker_reason == "None":
            worker_reason = None
    rec, fpath = record_terminal_failure(
        ticket_id, attempt, classification, state, events, worktree,
        expected_repo=args.repo, archetype=(args.archetype or "standard-engineering"),
        worker_block_reason=worker_reason, lane=ticket.get("lane"),
        needs_dirs=needs_mod.parse(ticket.get("needs"), ticket.get("detail", "")).dirs)
    block_class = rec["block_class"]
    reason = failure_record.block_reason_line(rec, fpath)

    # wq#2581: a local-rung ticket that reaches here otherwise never sees a stronger
    # model -- MAX_ATTEMPTS above always reruns args.model unchanged, so the retry ladder
    # never walks up. Hand it to the next non-local rung in its own archetype chain
    # instead of blocking it, UNLESS the triage says a stronger model can't help anyway:
    # REAL_BLOCK (the worker itself decided this needs a human/real decision),
    # CAPABILITY_DENIED (a sandbox policy denial -- no model gets past that), or a cited
    # path the sandbox couldn't reach (same reasoning as CAPABILITY_DENIED: an escalated
    # model hits the identical wall). router.escalate_off_local is itself idempotent
    # per ticket (a notes marker), so this can never ping-pong a ticket back to local.
    if (is_local_rung(args.model)
            and rec["triage"] != failure_record.TRIAGE_REAL_BLOCK
            and rec["triage"] != failure_record.TRIAGE_CAPABILITY_DENIED
            and not rec.get("unreachable_paths")):
        escalated, detail = router.escalate_off_local(ticket_id, reason=classification)
        if escalated:
            log(f"ESCALATED [{classification}]: wq#{ticket_id} local rung exhausted -> "
                f"{detail}, released to open instead of blocked")
            ntfy(f"wq#{ticket_id} local rung exhausted ({classification}) — escalated "
                 f"to {detail} instead of blocking", title="foreman: local rung escalated")
            return EXIT_LOCAL_ESCALATED
        log(f"local rung exhausted but not escalating: {detail}")

    log(f"BLOCKED [{block_class}/{rec['triage']}]: {reason}")
    record_recipe_outcome(ticket, args, success=False, triage=rec["triage"])
    if rec["triage"] != failure_record.TRIAGE_REAL_BLOCK:
        wq_block(ticket_id, reason)
    # wq#1697 fix 3: a P0/P1 parked on a capability denial gets a visible aq row carrying
    # the exact policy diff; fail-open, never eats the block.
    capability_proposal.file_denied_row(ticket, rec, fpath)
    ntfy("foreman BLOCKED", f"wq#{ticket_id} [{block_class}]: {reason}", priority="high")
    return EXIT_BLOCKED


# ---------------------------------------------------------------------------
# restart reconciliation (design §8) — trusted BEFORE claiming anything
# ---------------------------------------------------------------------------

def reconcile_on_resume(ticket_id):
    cur = wq_show(ticket_id)
    if cur.get("status") == "done":
        log("ticket already done — nothing to resume.")
        return True
    live = []
    try:
        p = subprocess.run(["claude", "agents", "--json", "--all"],
                            capture_output=True, text=True, timeout=15)
        if p.returncode == 0:
            live = json.loads(p.stdout or "[]")
    except Exception as e:
        log(f"claude agents --json unavailable during resume reconciliation: {e}")
    d = run_dir(ticket_id)
    attempts = sorted(
        (f for f in os.listdir(d) if re.match(r"attempt-\d+\.json$", f)),
    )
    if attempts:
        with open(os.path.join(d, attempts[-1])) as f:
            last = json.load(f)
        log(f"last recorded attempt: {last.get('attempt')} classification="
            f"{last.get('classification')}")
    log(f"claude agents --json liveness check: {len(live)} session(s) visible (advisory only).")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_run(args):
    if args.resume:
        reconcile_on_resume(args.ticket)
    resolve_tuple_from_ticket(args)

    unmet = deps_unmet(args.ticket)
    if unmet:
        detail = ", ".join(f"#{d} ({s})" for d, s in unmet)
        log(f"DEFERRED: wq#{args.ticket} deps not met: {detail}")
        print("DEFERRED")
        return EXIT_DEPS_UNMET

    # design §3 step 1: governor gate precedes claim (step 2). Checking here, same
    # as deps_unmet above, means a denied dispatch never touches the claim at all —
    # no lease to release, no churn. do_attempt() re-checks this per-attempt too
    # (design's "per-attempt setup"), since a retry/resume can run long after this
    # initial gate; that path releases the claim it's already holding on deny.
    # wq#4289: the aider runtime spends neither pool these two gates police.
    aider_runtime = runtime_of(args) == "aider"
    allow, reason = (True, None) if aider_runtime else can_dispatch(args.model)
    if not allow:
        log(f"DEFERRED: wq#{args.ticket} governor denied dispatch before claim: {reason}")
        print("DEFERRED")
        return EXIT_DEFERRED

    # T4: same pre-claim treatment as the governor gate above — a local-pool model
    # dispatched outside LOCAL_ALLOWED_ARCHETYPES never touches the claim.
    allow, reason = ((True, None) if aider_runtime else
                     local_rung_gate(args.model, args.archetype or "standard-engineering"))
    if not allow:
        log(f"DEFERRED: wq#{args.ticket} {reason}")
        print("DEFERRED")
        return EXIT_DEFERRED

    # wq#1116: fworker's own heartbeat/done/release go through wq() with an inherited env, so
    # pin the ticket identity here — before the claim — and every later call is the holder
    # rather than a fresh `lumen:<pid>` that wq has to wave through.
    os.environ["WQ_AGENT"] = wq_identity(args.ticket)

    # wq#5007: same pre-claim treatment for an OPEN circuit — the claim is never
    # touched while a ticket is cooling down (the call is counted in do_attempt).
    gate_refusal = exit_gate_preflight(args.ticket, count_call=False)
    if gate_refusal:
        log(f"DEFERRED: wq#{args.ticket} {gate_refusal}")
        print("DEFERRED")
        return EXIT_DEFERRED

    claimed, msg = wq_claim(args.ticket)
    if not claimed:
        log(f"CLAIM_ERROR: {msg}")
        print("CLAIM_ERROR")
        return EXIT_CLAIM_ERROR

    # wq#717 (AUTO-5): fleet-fires manual-approval workflow — a dedicated state
    # machine (diagnose/hold/apply), not do_attempt()'s retry ladder. See FIRE_MODE.md.
    if args.archetype == FIRE_ARCHETYPE:
        try:
            return run_fire_ticket(args)
        except SpawnRefused as e:
            return handle_spawn_refused(args, args.ticket, e)
        except Exception:
            tb = traceback.format_exc()
            return handle_unhandled_crash(args, args.ticket, None, tb, context="fire-mode run")

    attempt = next_attempt_n(args.ticket)
    # wq#4765 (§2.3 steps 5-6): `wq grant approve` reopens a parked ticket to `open`
    # WITH resume_session kept (never cleared there — only release/done clear it), so
    # a freshly claimed ticket carrying one is exactly "this claim is the grant-approved
    # resume" and nothing else: every path that returns a ticket to `open` any other
    # way (release, a fresh dispatch) clears resume_session/grant_req first.
    resume_sid = wq_show(args.ticket).get("resume_session")
    try:
        if resume_sid:
            log(f"wq#{args.ticket}: grant-approved resume (session {resume_sid}) — "
                f"spawning --resume with no prompt, same attempt {attempt}")
            return do_attempt(args, attempt, resume_session=resume_sid, grant_resume=True)
        return do_attempt(args, attempt)
    except SpawnRefused as e:
        # wq#1331: preflight refusal — release uncharged, leave ticket open with a
        # notes line; NOT the generic crash path (which would page as CRASH and
        # tell the record nothing mechanical).
        return handle_spawn_refused(args, args.ticket, e)
    except Exception:
        # RELIABILITY_SPEC.md §D "no path ends holding a lease": do_attempt's own
        # retry/resume logic (handle_failure/handle_limit/handle_resumable_failure)
        # only runs for OUTCOMES it classified (§C) — an exception here means
        # something broke OUTSIDE that flow (wq#649: make_worktree's git plumbing was
        # the repro) before any of it got a chance to release or reclassify. Release
        # immediately rather than leaving this for the lease reaper (reaper.py) to
        # reclaim minutes/hours later.
        tb = traceback.format_exc()
        return handle_unhandled_crash(args, args.ticket, attempt, tb,
                                       context=f"attempt {attempt}")


def trigger_refill(ticket_id, outcome):
    """wq#992-B: kick one dispatch pass so the cloud slot this worker just freed refills
    in seconds instead of at the next 20-min timer tick.

    Detached via `sudo -n systemd-run` with a short settle sleep so this fworker returns
    and its own `fworker-wq<ID>` unit deactivates BEFORE the pass runs — otherwise
    concurrency.list_running_units() still counts this worker's unit as running and masks
    the slot it just freed. The pass is slock-serialized, ceiling-bounded and idempotent,
    so a burst of near-simultaneous completions coalesces and can never over-spawn; the
    20-min timer remains the backstop for hard supervisor deaths that fire no kick.
    Fail-open by contract: any problem is logged and swallowed (the timer still covers
    the slot); a refill kick must never change this worker's own exit outcome.
    """
    if os.environ.get("FOREMAN_NO_REFILL") == "1":
        return
    for path in (DISPATCH_DISARM_PATH, STOP_FILE):
        if os.path.exists(path):
            log(f"refill: {os.path.basename(path)} present — not kicking a pass "
                f"after wq#{ticket_id}")
            return
    settle = max(0, REFILL_SETTLE_S)
    unit = f"fdisp-kick-wq{ticket_id}-{int(time.time())}"
    cmd = ["sudo", "-n", "systemd-run", "--collect", f"--unit={unit}",
           "/bin/sh", "-c",
           f"sleep {settle}; exec systemctl start --no-block {DISPATCH_SERVICE}"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"refill: kick failed to launch after wq#{ticket_id}: {e}")
        return
    if p.returncode == 0:
        log(f"refill: wq#{ticket_id} {outcome} -> kicked {DISPATCH_SERVICE} "
            f"in ~{settle}s ({unit})")
    else:
        log(f"refill: kick after wq#{ticket_id} failed rc={p.returncode}: "
            f"{(p.stderr or p.stdout).strip()}")


def cmd_verify_evidence(args):
    """Read-only replay: same verdict fworker will reach, minus the state transition."""
    rd = run_dir(args.ticket)
    pinned = (acceptance_mod.pinned_doc(rd)
              or acceptance_mod.read_acceptance(args.worktree, run_dir=rd))
    if not pinned or not pinned.get("criteria"):
        print(json.dumps({"ok": True, "reason": "no pinned acceptance criteria"}))
        return EXIT_OK
    frozen_ref = acceptance_mod.pinned_origin_sha(rd) or args.base_ref
    # wq#1905: same snapshot-then-read-from-run_dir replay path verify_done() uses —
    # this CLI is the same verdict "minus the state transition" (see docstring above),
    # so it reads the same source. When the caller (a worker self-checking mid-session)
    # can't reach run_dir at all, the snapshot best-effort no-ops and the read falls
    # back to the worktree copy, same as before this ticket.
    acceptance_mod.snapshot_evidence(args.worktree, rd)
    result = acceptance_mod.verify(
        pinned, acceptance_mod.read_evidence(args.worktree, run_dir=rd), args.worktree,
        base_ref=frozen_ref,
        claim_check=hallucination_guard.claim_checker() if args.claim_check else None)
    print(json.dumps(result, indent=2))
    return EXIT_OK if result["ok"] else EXIT_BLOCKED


def main():
    # wq#1894: installed once here, not at import time — tests import this module
    # directly (test_kill_group.py, test_fworker_integration.py) without going through
    # main(), and must keep the test runner's own default SIGTERM disposition.
    signal.signal(signal.SIGTERM, handle_external_sigterm)
    ap = argparse.ArgumentParser(description="fworker — per-ticket claude -p supervisor")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="claim a ticket and drive it to completion")
    p_run.add_argument("--ticket", type=int, required=True)
    p_run.add_argument("--repo", required=True)
    # wq#617: defaults are None so the ticket's wq tuple (or the router) decides;
    # explicit flags still win. See resolve_tuple_from_ticket().
    p_run.add_argument("--model", default=None)
    p_run.add_argument("--effort", default=None)
    p_run.add_argument("--archetype", default=None)
    p_run.add_argument("--max-turns", type=int, default=None)
    p_run.add_argument("--wall-clock-timeout", type=int, default=None)
    # RELIABILITY_SPEC.md §E: frame-gap watchdog is the primary liveness check,
    # T_gap configurable 120-300s (default/env var: INACTIVITY_TIMEOUT_S above).
    p_run.add_argument("--frame-gap-timeout", type=int, default=None)
    p_run.add_argument("--post-result-grace", type=int, default=None)
    # wq#4707: the grace that applies instead while a background task is outstanding.
    p_run.add_argument("--post-result-bg-grace", type=int, default=None)
    p_run.add_argument("--worker-id", default=None)
    p_run.add_argument("--resume", action="store_true")
    # wq#714: systemd unit PATH lacks ~/.npm-global/bin, so the bare "claude"
    # default crashed every spawn with FileNotFoundError within 1s.
    p_run.add_argument("--claude-bin", default="/home/lumen/.npm-global/bin/claude",
                        help="override for testing against a fake claude shim")
    # wq#4289: default None so FOREMAN_RUNTIME (dispatcher/unit env) can pick it.
    p_run.add_argument("--runtime", choices=RUNTIMES, default=None,
                        help="claude (default) or aider: the local lumen/code path")
    p_run.set_defaults(func=cmd_run)

    # ISSUE-LOG #17: the same replay fworker runs after the child exits, exposed so a
    # worker (or a critic) can self-check BEFORE claiming done. Read-only — it never
    # touches the wq row.
    p_ver = sub.add_parser("verify-evidence",
                           help="replay evidence.json against the pinned acceptance.json")
    p_ver.add_argument("--ticket", type=int, required=True)
    p_ver.add_argument("--worktree", required=True)
    p_ver.add_argument("--base-ref", default="origin/main")
    p_ver.add_argument("--claim-check", action="store_true",
                       help="also run the local-inference claim check (wq#2020); off by "
                            "default so a worker's self-check stays fast")
    p_ver.set_defaults(func=cmd_verify_evidence)

    args = ap.parse_args()
    rc = args.func(args)
    # wq#992-B: a terminal verdict that vacated a cloud slot chains the next ticket now,
    # rather than leaving the slot idle until the 20-min timer. The three run-outcomes
    # that mean "this worker ran and freed its slot" — a verified done, an
    # attempts-exhausted block, and (wq#2581) a local-rung ticket just escalated onto a
    # cloud rung, which is immediately dispatchable and gains nothing from waiting for
    # the timer. Pre-claim deferrals (deps/governor/claim) never occupied the slot
    # meaningfully, and LIMIT means the whole login is throttled (a kicked pass would
    # just be governor-denied), so neither kicks. Fail-open, after the exit code is
    # fixed so it can't perturb the outcome.
    if getattr(args, "func", None) is cmd_run and rc in (EXIT_OK, EXIT_BLOCKED, EXIT_LOCAL_ESCALATED):
        trigger_refill(getattr(args, "ticket", "?"),
                       {EXIT_OK: "done", EXIT_BLOCKED: "blocked",
                        EXIT_LOCAL_ESCALATED: "escalated"}[rc])
    sys.exit(rc if isinstance(rc, int) else 0)


if __name__ == "__main__":
    main()
