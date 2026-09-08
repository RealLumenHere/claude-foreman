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
import json
import os
from datetime import datetime, timedelta, timezone
import re
import resource
import shlex
import shutil
import signal
import subprocess
import sys
import time
import threading
import traceback
import queue

sys.path.insert(0, os.path.dirname(__file__))
from classifier import (
    RunState, classify, parse_worker_sentinel,
    SUCCESS, LIMIT, INDETERMINATE, AUTH, HANG, DEGRADED_SUCCESS, FALSE_COMPLETE, OOM,
    TURNS, LAUNCH_FAILURE, KILLED, BUDGET, BLOCK_SENTINEL, CLAIM_ERROR_SENTINEL,
    PERMISSION_LOOP, MODEL_NOT_FOUND,
)
from oom_forensics import oom_journal_evidence
import acceptance as acceptance_mod
import action_gate
import failure_record
import needs as needs_mod
import capability
import agents_spec
import fire_gate

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
# wq#1183 pattern-C memory: episodic append + distilled-fact recall (memory/README.md).
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
LOCAL_POOL_MODELS = ("deepseek-v4-flash", "qwen36-local")
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


def is_local_model(model):
    return bool(model) and any(model.startswith(m) for m in LOCAL_POOL_MODELS)


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
    if archetype not in LOCAL_ALLOWED_ARCHETYPES:
        return False, (f"local rung model {model!r} not trusted for archetype "
                        f"{archetype!r} (only {LOCAL_ALLOWED_ARCHETYPES})")
    return True, None


def read_litellm_key():
    with open(LITELLM_KEY_FILE) as f:
        return f.read().strip()

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
INACTIVITY_TIMEOUT_S = _envint("FOREMAN_FRAME_GAP_S", 240)
# §C9 SUCCESS-BUT-STUCK (#25629): once a terminal result frame has landed, a much
# shorter grace covers the "process just won't exit" case — no more real work is
# coming, so there is no reason to wait out the full frame-gap window before forcing it.
POST_RESULT_GRACE_S = _envint("FOREMAN_POST_RESULT_GRACE_S", 20)
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

_INFRA_CRASH_CLASSIFICATIONS = {LAUNCH_FAILURE, KILLED, OOM}
_LIMIT_HIT_CLASSIFICATIONS = {TURNS, BUDGET}
_HARNESS_DEFECT_CLASSIFICATIONS = {PERMISSION_LOOP, MODEL_NOT_FOUND}


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
    """Parse `wq show <id>`'s `key : value` text into a dict — wq has no --json."""
    p = wq("show", str(ticket_id))
    out = {}
    for line in p.stdout.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip()
    return out


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


def wq_done(ticket_id, notes):
    p = wq("done", str(ticket_id), "--notes", notes)
    if p.returncode != 0:
        # wq#759 evidence gate (or any other refusal): log loudly so fworker never
        # silently swallows a refused close (wq#837 contradict finding).
        log(f"ERROR: wq done {ticket_id} refused (exit {p.returncode}): "
            f"{p.stdout.strip()} {p.stderr.strip()}")
        raise RuntimeError(f"wq done {ticket_id} refused (exit {p.returncode}): "
                           f"{p.stdout.strip()}")


def wq_release(ticket_id):
    wq("release", str(ticket_id))


def wq_block(ticket_id, reason):
    wq("block", str(ticket_id), reason)


def ntfy(title, msg, priority="default"):
    # wq#1407: bin/ntfy-send parses -T/-p only; --title/--priority fell into the
    # message body and every alert went out at default priority (162 in 48h).
    try:
        subprocess.run([NTFY_BIN, "-T", title, "-p", priority, msg],
                        capture_output=True, text=True, timeout=10)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# run-state persistence (design §8: attempt-N.json, prior-attempt-notes.md)
# ---------------------------------------------------------------------------

def run_dir(ticket_id):
    d = os.path.join(RUNS_ROOT, f"wq{ticket_id}")
    os.makedirs(d, exist_ok=True)
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
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=str)
    os.rename(tmp, path)
    return path


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
    with open(tmp, "w") as f:
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
    lines = [
        f"# Prior attempt {n} notes (wq#{ticket_id})",
        f"classification: {classification}",
        "",
        "## last events (mechanical tail, up to 20)",
    ]
    for ev in tail:
        etype = ev.get("type", "?")
        sub = ev.get("subtype", "")
        lines.append(f"- {etype} {sub}".rstrip())
    lines += ["", "## git diff --stat (prior worktree)", "```", diff_stat.strip(), "```"]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


# ---------------------------------------------------------------------------
# worktree
# ---------------------------------------------------------------------------

def _memory_import(name):
    """Import a memory/ module (episodic, recall) fail-open: fleet memory is
    strictly additive (wq#1183) — a missing or broken memory tree must never
    fail an attempt or a prompt build."""
    if MEMORY_DIR not in sys.path:
        sys.path.insert(0, MEMORY_DIR)
    return __import__(name)


def append_episode_event(ticket_id, event):
    """wq#1183 episodic memory: append-only per-run log, the distiller's input.
    Best-effort by contract (memory/README.md failure posture)."""
    try:
        _memory_import("episodic").append_event(ticket_id, event,
                                                 runs_root=RUNS_ROOT)
    except Exception as e:
        log(f"episode append failed (non-fatal): {e!r}")


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


def distilled_memory_block(ticket):
    """wq#1183 recall: top-k distilled facts for the worker prompt prelude,
    scoped to fleet + this ticket's repo when one is declared. Empty string on
    any failure — prompt assembly never depends on the memory store."""
    try:
        recall = _memory_import("recall")
        scopes = {"fleet"}
        repo = (ticket.get("repo") or "").strip()
        if repo:
            scopes.add(f"repo:{os.path.basename(repo)}")
        lane = (ticket.get("lane") or "").strip()
        if lane:
            scopes.add(f"lane:{lane}")
        return recall.render_block(recall.top_facts(scopes=scopes))
    except Exception:
        return ""


def slugify(text, maxlen=40):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:maxlen] or "ticket"


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
    v = capability.preflight(ticket, repo, add_dirs, ticket_needs, worktree=worktree,
                             policy_path=FLEET_EXEC_POLICY_PATH)
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
    # 0400 file below are fworker's.
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
    os.chmod(path, 0o400)
    return grant, fleet_exec_policy.sha256_bytes(raw), path


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


def build_env(worker_id, model, effort, worktree, ticket_id):
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
        env["ANTHROPIC_AUTH_TOKEN"] = read_litellm_key()
        env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "1"
    return env


# wq#1245: workers (esp. verify tickets) must be able to READ the fleet's primary trees to verify
# cross-repo work and reach live env/config. Writes stay in the worktree by harness discipline +
# primary_tree_guard; this only widens read/context access.
# wq#1301: default list now sourced from agents.yaml's `defaults.add_dirs` (name kept
# for existing callers/tests). add_dirs_for() below resolves the per-archetype list —
# every archetype uses this same default today, but a future archetype-specific
# add_dirs entry in agents.yaml takes effect without another fworker.py change.
WORKER_ADD_DIRS = _AGENTS.default_add_dirs


def add_dirs_for(archetype, needs_dirs=None):
    """Per-archetype --add-dir list (wq#1301), unioned with the ticket's own
    needs:dir tags (wq#1331 §3.2 — read context only; landing still goes through the
    worktree). needs_dirs is already empty when FOREMAN_NEEDS_DISABLE=1 because
    needs.parse() returned nothing."""
    base = _AGENTS.get(archetype).add_dirs if archetype in _AGENTS else WORKER_ADD_DIRS
    out = list(base)
    for d in needs_dirs or []:
        if d not in out:
            out.append(d)
    return out

def resolve_worker_settings(repo, ticket_id):
    """ISSUE-SOLUTIONS-fable.md §2b: render this repo's per-repo toolchain permission
    profile (permission-profiles/repos.d/<repo>.json) into the ticket's run_dir, unioned
    from the shared profile fragments. A repo with no profile declaration yet falls back
    to the static SETTINGS_FILE unchanged — no regression for repos not yet migrated.
    """
    settings_file, digest = permission_profiles.resolve_settings_file(
        repo, SETTINGS_FILE, run_dir(ticket_id))
    if digest:
        log(f"wq#{ticket_id}: rendered permission profile {digest[:12]} -> {settings_file}")
    return settings_file


def build_claude_cmd(prompt, model, effort, settings_file, add_dirs, max_turns=None,
                      resume_session=None, claude_bin="claude"):
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


def run_child(cmd, cwd, env, max_turns, wall_clock_s, ticket_id, attempt,
              frame_gap_s=None, post_result_grace_s=None, loop_tracker=None):
    """Spawn, parse stream-json, enforce watchdogs. Returns (RunState, exit_code,
    raw_events, killed_reason, cpu_s, pid).

    RELIABILITY_SPEC.md §E: the frame-gap watchdog (frame_gap_s, default
    INACTIVITY_TIMEOUT_S) is PRIMARY liveness — no frames for that long with the
    process still alive is a stall (§C8), SIGTERM'd here and resumable by the caller.
    Wall-clock (wall_clock_s) is the secondary ceiling. Neither is CPU-based: CPU time
    is corroboration only (returned as cpu_s for the attempt record / LAUNCH_FAILURE
    triage), never a watchdog trigger — a child can legitimately burn near-zero CPU
    while waiting on a slow tool call.
    """
    frame_gap_s = frame_gap_s if frame_gap_s is not None else INACTIVITY_TIMEOUT_S
    post_result_grace_s = (post_result_grace_s if post_result_grace_s is not None
                            else POST_RESULT_GRACE_S)
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
    done_seen_at = None          # wq#992-C: when the ticket itself first read `done`
    last_done_poll = start       # throttle the wq status poll to DONE_POLL_INTERVAL_S
    # short poll interval when either watchdog window is itself short (tests), so
    # detection latency stays a fraction of the window instead of the flat 5s floor.
    poll_timeout = max(0.25, min(5, frame_gap_s, post_result_grace_s) / 4)

    while True:
        now = time.time()
        if now >= deadline:
            killed_reason = "wall_clock"
            break
        # §C9 SUCCESS-BUT-STUCK: once a terminal result has landed, don't wait out the
        # full frame-gap window — a much shorter grace covers "process won't exit".
        if result_seen_at is not None and now - result_seen_at >= post_result_grace_s:
            killed_reason = "post_result_stuck"
            break
        # wq#992-C: the ticket itself reached `done` mid-run. Give a short grace for a
        # trailing episode-commit / final message, then stop the lingering session so it
        # can't keep taking agentic turns (which frame-gap wouldn't catch and the
        # turn-backstop would misclassify TURNS). Checked BEFORE frame-gap so a
        # finished-but-quiet agent is recognized as done, not a hang.
        if done_seen_at is not None and now - done_seen_at >= POST_DONE_GRACE_S:
            killed_reason = "ticket_done"
            break
        if now - last_event >= frame_gap_s:
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
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            raw_events.append({"type": "unparsed", "raw": line[:500]})
            continue
        raw_events.append(frame)
        state.observe(frame)
        # Layer 2 (TIERED_INTELLIGENCE_DESIGN.md, wq#985): loop breaker. This is
        # what Frank had to do manually on the Miko — the harness does it for him.
        if loop_tracker is not None:
            loop_reason = loop_tracker.observe(frame)
            if loop_reason:
                killed_reason = "loop_break"
                log(f"LOOP BREAK: {loop_reason} — stopping session, handoff to follow")
                break
        if frame.get("type") == "rate_limit_event" and frame.get("status") == "rejected":
            report_limit_event(frame)
        if frame.get("type") == "result":
            result_seen_at = time.time()
        if state.turns >= turn_backstop:
            killed_reason = "turns_exceeded"
            break

    if killed_reason:
        if killed_reason == "inactivity":
            state.hung = True
        elif killed_reason == "turns_exceeded":
            state.turns_exceeded = True
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        except ProcessLookupError:
            pass
    else:
        proc.wait()

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
                        acceptance_doc=None, rework=None):
    tmpl_path = os.path.join(HARNESS_DIR, "prompts", "worker.md")
    with open(tmpl_path) as f:
        tmpl = _sub_instincts_tier0(f.read())
    # ISSUE-LOG #17: the done-contract (§1a) and, on a rejected attempt, the structured
    # rework block (§1d) — which goes at the TOP, ahead of everything else, because its
    # first rule is that the worker's first action is to re-run the repro commands.
    contract = acceptance_mod.contract_block(acceptance_doc, worktree) if acceptance_doc else ""
    rework_text = acceptance_mod.rework_block(rework) if rework else ""
    for placeholder, block in (("{{ACCEPTANCE_CONTRACT}}", contract),
                                ("{{REWORK}}", rework_text)):
        tmpl = (tmpl.replace(placeholder, block) if block
                else tmpl.replace(placeholder + "\n\n", "").replace(placeholder, ""))
    prior_notes = ""
    if prior_notes_path and os.path.exists(prior_notes_path):
        with open(prior_notes_path) as f:
            prior_notes = f.read()
    memory_block = distilled_memory_block(ticket)
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
                with open(tmp, "w") as f:
                    json.dump(evidence, f, indent=2, default=str)
                os.rename(tmp, evidence_path)
            notes = ("condition cleared: re-verify found the originating probe no "
                     "longer firing — auto-closed by the fire-response archetype "
                     "(wq#1261), no manual apply needed.")
            if evidence_path:
                notes += (f" Probe evidence: {evidence_path} "
                          f"(cmd={evidence['cmd']!r} exit={evidence['exit_code']}).")
            wq_done(ticket_id, notes)
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


def do_attempt(args, attempt, resume_session=None, prior_worktree=None):
    ticket_id = args.ticket
    ticket = wq_show(ticket_id)
    archetype = args.archetype or "standard-engineering"

    allow, reason = can_dispatch(args.model)
    if not allow:
        log(f"governor DEFERRED dispatch: {reason} — releasing claim")
        wq_release(ticket_id)
        print("DEFERRED")
        return EXIT_DEFERRED

    allow, reason = local_rung_gate(args.model, archetype)
    if not allow:
        log(f"DEFERRED: {reason} — releasing claim")
        wq_release(ticket_id)
        print("DEFERRED")
        return EXIT_DEFERRED

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
    add_dirs = add_dirs_for(archetype, ticket_needs.dirs)

    if resume_session:
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
    prompt = load_worker_prompt(
        ticket_id, ticket, worktree,
        prior_notes_path if (attempt > 1 and not resume_session) else None,
        acceptance_doc=acceptance_doc, rework=rework,
    )

    env = build_env(worker_id, args.model, args.effort, worktree, ticket_id)
    if grant_path:
        # wq#1332: fleet-exec's OWN enforcement step 1 re-derives this sha256 from
        # the file it reads — this env var is the tamper check, not a convenience.
        env["FOREMAN_GRANT_FILE"] = grant_path
        env["FOREMAN_GRANT_SHA"] = grant_sha
        env["FOREMAN_ATTEMPT"] = str(attempt)
        env["FLEET_EXEC_WORKER_AUDIT_PATH"] = os.path.join(EVENTS_DIR, f"{worker_id}.jsonl")
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
    loop_tracker = action_gate.LoopTracker()
    settings_file = resolve_worker_settings(args.repo, ticket_id)
    cmd = build_claude_cmd(prompt, args.model, args.effort, settings_file,
                            add_dirs=add_dirs, max_turns=max_turns,
                            resume_session=resume_session, claude_bin=args.claude_bin)

    log(f"attempt {attempt}: spawning {' '.join(shlex.quote(c) for c in cmd[:6])}... "
        f"cwd={worktree} max_turns={max_turns} wall_clock={wall_clock}s")

    t0 = time.time()
    rate_limits_start = read_rate_limits_snapshot()
    state, exit_code, events, killed_reason, cpu_s, child_pid = run_child(
        cmd, worktree, env, max_turns, wall_clock, ticket_id, attempt,
        frame_gap_s=args.frame_gap_timeout, post_result_grace_s=args.post_result_grace,
        loop_tracker=loop_tracker)
    if killed_reason == "loop_break":
        # Layer 2 handoff: "SESSION PAUSED: repeated unsuccessful approach detected.
        # Your work has been preserved for a reviewing agent." — written to the run
        # dir so prior-attempt notes and (future L4) review both find it.
        hpath = os.path.join(run_dir(ticket_id), f"loop-handoff-attempt-{attempt}.md")
        try:
            with open(hpath, "w") as hf:
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
    # block`) — only the ticket-status cross-check makes it terminal.
    worker_sentinel = None
    sentinel_status = None
    if classification in (SUCCESS, DEGRADED_SUCCESS):
        worker_sentinel = parse_worker_sentinel(state.result_text)
        if worker_sentinel in (BLOCK_SENTINEL, CLAIM_ERROR_SENTINEL):
            sentinel_status = wq_show(ticket_id).get("status")
    sentinel_confirmed_block = (worker_sentinel == BLOCK_SENTINEL
                                 and sentinel_status == "blocked")
    sentinel_confirmed_claim_error = (worker_sentinel == CLAIM_ERROR_SENTINEL
                                       and sentinel_status != "done")

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
            and not check_artifacts_done(ticket_id, worktree)):
        log(f"{classification} but acceptance criteria unmet (ticket still open, no "
            f"landed commit found) — RELIABILITY_SPEC.md §C10: never trust the "
            f"self-report, treating as FALSE_COMPLETE.")
        classification = FALSE_COMPLETE

    log(f"attempt {attempt}: classification={classification} raw={raw_classification} "
        f"exit={exit_code} killed_reason={killed_reason} turns={state.turns} "
        f"elapsed={elapsed:.0f}s cpu_s={cpu_s:.1f} session_id={state.session_id}")

    attempt_record = {
        "ticket": ticket_id, "attempt": attempt, "worker_id": worker_id,
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
    }
    state_path = write_attempt_state(ticket_id, attempt, attempt_record)
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
    # append-only per-run episode log. This — not the attempt record — is what
    # memory/distill.py reads, so it carries the narrative (result text +
    # assistant tail) alongside the mechanical outcome. Best-effort.
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
    })

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
        log(f"CLAIM_ERROR: worker reported a claim race on wq#{ticket_id} "
            f"(status={sentinel_status!r}) — releasing, no retry (wq#749).")
        wq_release(ticket_id)
        return EXIT_CLAIM_ERROR

    return reconcile(args, ticket_id, attempt, classification, state, events,
                      worktree, state_path)


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

    live = acceptance_mod.read_acceptance(worktree)
    tampered = live is not None and acceptance_mod.doc_hash(live) != acceptance_mod.doc_hash(pinned)
    evidence = acceptance_mod.read_evidence(worktree)
    ev_dir = os.path.join(rd, "evidence", f"attempt-{attempt}")
    # Diff against the sha origin/main pointed at when this ticket was dispatched, not
    # the live ref: if this ticket's own PR has since merged, HEAD is now an ancestor of
    # live origin/main and the diff would collapse to empty (ISSUE-LOG #143/#156).
    frozen_ref = acceptance_mod.pinned_origin_sha(rd) or base_ref
    result = acceptance_mod.verify(pinned, evidence, worktree, base_ref=frozen_ref,
                                    evidence_dir=ev_dir)
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
        log(f"wq#{ticket_id}: evidence replay PASSED "
            f"({len(result['checked'])} criteria re-run/re-checked by the harness)")
        return True, False

    try:
        os.makedirs(ev_dir, exist_ok=True)
        src = os.path.join(worktree, acceptance_mod.EVIDENCE_FILENAME)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(ev_dir, acceptance_mod.EVIDENCE_FILENAME))
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
    second door into `done`."""
    if evidence_rejected(ticket_id):
        return False
    cur = wq_show(ticket_id)
    if cur.get("status") == "done":
        return True
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
            ["git", "-C", worktree, "log", "origin/main", "--oneline", "-n", "1",
             "--grep", rf"wq#{ticket_id}\b"],
            capture_output=True, text=True, timeout=15)
        if p.returncode == 0 and p.stdout.strip():
            return True
    except Exception:
        pass
    return False


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
    if check_artifacts_done(ticket_id, worktree):
        log(f"{classification} but artifacts confirm the ticket actually landed "
            f"(session {state.session_id}) — verifying beats trusting the missing/odd "
            f"frame shape.")
        if wq_show(ticket_id).get("status") != "done":
            wq_done(ticket_id, f"fworker attempt {attempt}: verified via artifacts "
                                f"after {classification} (session {state.session_id})")
        file_verify_pass(ticket_id, args)
        return EXIT_OK
    write_prior_attempt_notes(ticket_id, attempt, worktree, events, classification)
    if attempt < MAX_ATTEMPTS and state.session_id:
        log(f"{classification}: attempt {attempt} < {MAX_ATTEMPTS}, resuming session "
            f"{state.session_id}")
        return do_attempt(args, attempt + 1, resume_session=state.session_id,
                           prior_worktree=worktree)
    return handle_failure(args, ticket_id, attempt, classification, state, events, worktree)


def reconcile(args, ticket_id, attempt, classification, state, events, worktree, state_path):
    # SUCCESS/DEGRADED_SUCCESS only ever reach here when do_attempt() already verified
    # artifacts confirm the ticket landed (else it relabeled the classification
    # FALSE_COMPLETE before calling reconcile at all) — safe to trust directly.
    if classification in (SUCCESS, DEGRADED_SUCCESS):
        if wq_show(ticket_id).get("status") != "done":
            wq_done(ticket_id, f"fworker attempt {attempt} ({classification}): {state_path}")
        else:
            log(f"{classification}: worker already ran `wq done`.")
        file_verify_pass(ticket_id, args)
        return EXIT_OK

    if classification == LIMIT:
        return handle_limit(args, ticket_id, attempt, state, worktree)

    if classification == MODEL_NOT_FOUND:
        return handle_model_not_found(args, ticket_id, attempt, state, worktree)

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
                                                    needs_dirs))
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


def handle_failure(args, ticket_id, attempt, classification, state, events, worktree):
    prior_notes_path = write_prior_attempt_notes(ticket_id, attempt, worktree, events,
                                                  classification)
    if attempt < MAX_ATTEMPTS:
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
    log(f"BLOCKED [{block_class}/{rec['triage']}]: {reason}")
    if rec["triage"] != failure_record.TRIAGE_REAL_BLOCK:
        wq_block(ticket_id, reason)
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
    allow, reason = can_dispatch(args.model)
    if not allow:
        log(f"DEFERRED: wq#{args.ticket} governor denied dispatch before claim: {reason}")
        print("DEFERRED")
        return EXIT_DEFERRED

    # T4: same pre-claim treatment as the governor gate above — a local-pool model
    # dispatched outside LOCAL_ALLOWED_ARCHETYPES never touches the claim.
    allow, reason = local_rung_gate(args.model, args.archetype or "standard-engineering")
    if not allow:
        log(f"DEFERRED: wq#{args.ticket} {reason}")
        print("DEFERRED")
        return EXIT_DEFERRED

    # wq#1116: fworker's own heartbeat/done/release go through wq() with an inherited env, so
    # pin the ticket identity here — before the claim — and every later call is the holder
    # rather than a fresh `lumen:<pid>` that wq has to wave through.
    os.environ["WQ_AGENT"] = wq_identity(args.ticket)

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
            log(f"CRASH: unhandled exception during wq#{args.ticket} fire-mode run — "
                f"releasing claim.\n{tb}")
            wq_release(args.ticket)
            ntfy("foreman CRASH",
                 f"wq#{args.ticket} fire-mode crashed (claim released): "
                 f"{tb.strip().splitlines()[-1]}", priority="high")
            print("CRASH")
            return EXIT_CRASH

    attempt = next_attempt_n(args.ticket)
    try:
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
        log(f"CRASH: unhandled exception during wq#{args.ticket} attempt {attempt} — "
            f"releasing claim.\n{tb}")
        wq_release(args.ticket)
        ntfy("foreman CRASH",
             f"wq#{args.ticket} attempt {attempt} crashed (claim released): "
             f"{tb.strip().splitlines()[-1]}", priority="high")
        print("CRASH")
        return EXIT_CRASH


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
              or acceptance_mod.read_acceptance(args.worktree))
    if not pinned or not pinned.get("criteria"):
        print(json.dumps({"ok": True, "reason": "no pinned acceptance criteria"}))
        return EXIT_OK
    frozen_ref = acceptance_mod.pinned_origin_sha(rd) or args.base_ref
    result = acceptance_mod.verify(
        pinned, acceptance_mod.read_evidence(args.worktree), args.worktree,
        base_ref=frozen_ref)
    print(json.dumps(result, indent=2))
    return EXIT_OK if result["ok"] else EXIT_BLOCKED


def main():
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
    p_run.add_argument("--worker-id", default=None)
    p_run.add_argument("--resume", action="store_true")
    # wq#714: systemd unit PATH lacks ~/.npm-global/bin, so the bare "claude"
    # default crashed every spawn with FileNotFoundError within 1s.
    p_run.add_argument("--claude-bin", default="/home/lumen/.npm-global/bin/claude",
                        help="override for testing against a fake claude shim")
    p_run.set_defaults(func=cmd_run)

    # ISSUE-LOG #17: the same replay fworker runs after the child exits, exposed so a
    # worker (or a critic) can self-check BEFORE claiming done. Read-only — it never
    # touches the wq row.
    p_ver = sub.add_parser("verify-evidence",
                           help="replay evidence.json against the pinned acceptance.json")
    p_ver.add_argument("--ticket", type=int, required=True)
    p_ver.add_argument("--worktree", required=True)
    p_ver.add_argument("--base-ref", default="origin/main")
    p_ver.set_defaults(func=cmd_verify_evidence)

    args = ap.parse_args()
    rc = args.func(args)
    # wq#992-B: a terminal verdict that vacated a cloud slot chains the next ticket now,
    # rather than leaving the slot idle until the 20-min timer. Only the two run-outcomes
    # that mean "this worker ran and freed its slot" — a verified done and an
    # attempts-exhausted block. Pre-claim deferrals (deps/governor/claim) never occupied
    # the slot meaningfully, and LIMIT means the whole login is throttled (a kicked pass
    # would just be governor-denied), so neither kicks. Fail-open, after the exit code is
    # fixed so it can't perturb the outcome.
    if getattr(args, "func", None) is cmd_run and rc in (EXIT_OK, EXIT_BLOCKED):
        trigger_refill(getattr(args, "ticket", "?"),
                       "done" if rc == EXIT_OK else "blocked")
    sys.exit(rc if isinstance(rc, int) else 0)


if __name__ == "__main__":
    main()
