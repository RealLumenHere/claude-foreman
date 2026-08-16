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
import re
import resource
import shlex
import signal
import subprocess
import sys
import time
import threading
import queue

sys.path.insert(0, os.path.dirname(__file__))
from classifier import (
    RunState, classify,
    SUCCESS, LIMIT, INDETERMINATE, AUTH, HANG, DEGRADED_SUCCESS, FALSE_COMPLETE,
)

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
STOP_FILE = os.environ.get("FOREMAN_STOP_FILE", os.path.join(FOREMAN_ROOT, "STOP"))
GOVERNOR_CLI = os.environ.get("FOREMAN_GOVERNOR_CLI",
                               os.path.join(FOREMAN_ROOT, "governor", "foreman_usaged.py"))
WORKTREES_ROOT = os.environ.get("FOREMAN_WORKTREES_ROOT", "/home/lumen/worktrees")
WQ_BIN = os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq")
NTFY_BIN = os.environ.get("FOREMAN_NTFY_BIN", "/home/lumen/bin/ntfy-send")
# wq#617 orchestrator tier: routing (tuple assignment) + verify-pass filing.
ORCH_DIR = os.path.normpath(os.path.join(HARNESS_DIR, "..", "orchestrator"))
ROUTER_CLI = os.environ.get("FOREMAN_ROUTER_CLI", os.path.join(ORCH_DIR, "router.py"))
JUDGMENT_CLI = os.environ.get("FOREMAN_JUDGMENT_CLI", os.path.join(ORCH_DIR, "judgment.py"))

# design §3: per-archetype --max-turns defaults (passed to the CLI; the supervisor
# also backstops a few turns above this, see module docstring). Unlisted archetypes
# fall back to standard-engineering's ceiling.
ARCHETYPE_MAX_TURNS = {
    "mechanical-edit": 15,
    "probe-build": 25,
    "standard-engineering": 40,
    "verify-pass": 25,
    "research-synthesis": 25,
    "novel-design": 40,
    "rca-investigation": 60,
    "long-horizon-campaign": 60,
}
DEFAULT_MAX_TURNS = ARCHETYPE_MAX_TURNS["standard-engineering"]

def _envint(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


WALL_CLOCK_DEFAULT_S = 3600
WALL_CLOCK_RCA_S = 7200
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

OTEL_ENDPOINT = "http://100.110.156.91:8429/opentelemetry"

EXIT_OK = 0
EXIT_CLAIM_ERROR = 10
EXIT_DEFERRED = 11
EXIT_BLOCKED = 12
EXIT_LIMIT = 13
EXIT_DEPS_UNMET = 14


def log(msg):
    print(f"[fworker {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {msg}", flush=True)


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


def wq_claim(ticket_id):
    p = wq("claim", "--id", str(ticket_id))
    return p.returncode == 0, p.stdout + p.stderr


def wq_heartbeat(ticket_id):
    wq("heartbeat", str(ticket_id))


def wq_done(ticket_id, notes):
    wq("done", str(ticket_id), "--notes", notes)


def wq_release(ticket_id):
    wq("release", str(ticket_id))


def wq_block(ticket_id, reason):
    wq("block", str(ticket_id), reason)


def ntfy(title, msg, priority="default"):
    try:
        subprocess.run([NTFY_BIN, "--title", title, "--priority", priority, msg],
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
    d = run_dir(ticket_id)
    existing = [f for f in os.listdir(d) if re.match(r"attempt-\d+\.json$", f)]
    nums = [int(re.match(r"attempt-(\d+)\.json$", f).group(1)) for f in existing]
    return (max(nums) + 1) if nums else 1


def write_attempt_state(ticket_id, n, state):
    d = run_dir(ticket_id)
    path = os.path.join(d, f"attempt-{n}.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=str)
    os.rename(tmp, path)
    return path


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

def slugify(text, maxlen=40):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:maxlen] or "ticket"


def make_worktree(repo, ticket_id, attempt, title, archetype):
    wt = os.path.join(WORKTREES_ROOT, f"wq{ticket_id}-a{attempt}")
    os.makedirs(WORKTREES_ROOT, exist_ok=True)
    type_map = {
        "mechanical-edit": "chore", "probe-build": "test", "rca-investigation": "fix",
        "novel-design": "docs",
    }
    branch_type = type_map.get(archetype, "feat")
    branch = f"{branch_type}/wq{ticket_id}-{slugify(title)}"
    p = subprocess.run(
        ["git", "-C", repo, "worktree", "add", wt, "-b", branch, "HEAD"],
        capture_output=True, text=True,
    )
    if p.returncode != 0:
        # branch may already exist from a prior attempt path collision — retry unique
        branch = f"{branch}-a{attempt}"
        p = subprocess.run(
            ["git", "-C", repo, "worktree", "add", wt, "-b", branch, "HEAD"],
            capture_output=True, text=True,
        )
        if p.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {p.stdout} {p.stderr}")
    return wt, branch


# ---------------------------------------------------------------------------
# child env + spawn (design §3)
# ---------------------------------------------------------------------------

def build_env(worker_id, effort, worktree):
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_BASE_URL", None)
    env["HOME"] = "/home/lumen"
    env["FOREMAN_WORKER"] = worker_id
    env["WQ_AGENT"] = worker_id
    env["FOREMAN_EVENTS_DIR"] = EVENTS_DIR
    env["FOREMAN_WORKTREE_ROOT"] = worktree
    if effort:
        env["CLAUDE_CODE_EFFORT_LEVEL"] = effort
    env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "500000"
    env["CLAUDE_CODE_ENABLE_TELEMETRY"] = "1"
    env["OTEL_METRICS_EXPORTER"] = "otlp"
    env["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = OTEL_ENDPOINT
    env["OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE"] = "cumulative"
    env["OTEL_METRIC_EXPORT_INTERVAL"] = "60000"
    return env


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
    try:
        for line in iter(pipe.readline, ""):
            q.put(("line", line))
    except Exception as e:
        q.put(("reader_error", str(e)))
    finally:
        q.put(("eof", None))


def run_child(cmd, cwd, env, max_turns, wall_clock_s, ticket_id, attempt,
              frame_gap_s=None, post_result_grace_s=None):
    """Spawn, parse stream-json, enforce watchdogs. Returns (RunState, exit_code,
    raw_events, killed_reason, cpu_s).

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
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
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
        if now - last_event >= frame_gap_s:
            killed_reason = "inactivity"
            break
        if now - last_heartbeat >= HEARTBEAT_INTERVAL_S:
            wq_heartbeat(ticket_id)
            last_heartbeat = now
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

    return state, exit_code, raw_events, killed_reason, cpu_s


# ---------------------------------------------------------------------------
# main attempt orchestration
# ---------------------------------------------------------------------------

def load_worker_prompt(ticket_id, ticket, worktree, prior_notes_path=None):
    tmpl_path = os.path.join(HARNESS_DIR, "prompts", "worker.md")
    with open(tmpl_path) as f:
        tmpl = f.read()
    prior_notes = ""
    if prior_notes_path and os.path.exists(prior_notes_path):
        with open(prior_notes_path) as f:
            prior_notes = f.read()
    return (tmpl
            .replace("{{TICKET_ID}}", str(ticket_id))
            .replace("{{TITLE}}", ticket.get("title", ""))
            .replace("{{DETAIL}}", ticket.get("detail", ""))
            .replace("{{WORKTREE}}", worktree)
            .replace("{{PRIOR_ATTEMPT_NOTES}}", prior_notes))


def do_attempt(args, attempt, resume_session=None, prior_worktree=None):
    ticket_id = args.ticket
    ticket = wq_show(ticket_id)

    allow, reason = can_dispatch(args.model)
    if not allow:
        log(f"governor DEFERRED dispatch: {reason} — releasing claim")
        wq_release(ticket_id)
        print("DEFERRED")
        return EXIT_DEFERRED

    archetype = args.archetype or "standard-engineering"
    max_turns = args.max_turns or ARCHETYPE_MAX_TURNS.get(archetype, DEFAULT_MAX_TURNS)
    wall_clock = args.wall_clock_timeout or (
        WALL_CLOCK_RCA_S if archetype == "rca-investigation" else WALL_CLOCK_DEFAULT_S)

    worker_id = args.worker_id or f"fworker-wq{ticket_id}-a{attempt}"

    if resume_session:
        worktree, branch = prior_worktree, None
    else:
        worktree, branch = make_worktree(args.repo, ticket_id, attempt,
                                          ticket.get("title", str(ticket_id)), archetype)

    prior_notes_path = os.path.join(run_dir(ticket_id), "prior-attempt-notes.md")
    prompt = load_worker_prompt(
        ticket_id, ticket, worktree,
        prior_notes_path if (attempt > 1 and not resume_session) else None,
    )

    env = build_env(worker_id, args.effort, worktree)
    cmd = build_claude_cmd(prompt, args.model, args.effort, SETTINGS_FILE,
                            add_dirs=["/home/lumen/agents/work-queue"], max_turns=max_turns,
                            resume_session=resume_session, claude_bin=args.claude_bin)

    log(f"attempt {attempt}: spawning {' '.join(shlex.quote(c) for c in cmd[:6])}... "
        f"cwd={worktree} max_turns={max_turns} wall_clock={wall_clock}s")

    t0 = time.time()
    state, exit_code, events, killed_reason, cpu_s = run_child(
        cmd, worktree, env, max_turns, wall_clock, ticket_id, attempt,
        frame_gap_s=args.frame_gap_timeout, post_result_grace_s=args.post_result_grace)
    elapsed = time.time() - t0

    # §C9 SUCCESS-BUT-STUCK: fworker forced this kill AFTER a real terminal result had
    # already landed — the resulting SIGTERM/SIGKILL exit code is expected, not a §D
    # exit-code/frame disagreement, so it must not flip an otherwise-legitimate result
    # to INDETERMINATE. classify() only ever sees a "clean" exit for this one case.
    effective_exit_code = 0 if killed_reason == "post_result_stuck" else exit_code
    raw_classification = classify(state, effective_exit_code)
    classification = raw_classification

    # RELIABILITY_SPEC.md §C10: verify acceptance criteria against artifacts BEFORE
    # anything downstream (including the persisted attempt record) trusts a self-
    # reported success — decided here, not inside reconcile(), so the record on disk
    # carries the same verdict reconcile() acts on. `raw_classification` keeps the
    # pre-verification classifier output too (e.g. DEGRADED_SUCCESS), so RCA can still
    # see WHY this got relabeled instead of just seeing FALSE_COMPLETE.
    if classification in (SUCCESS, DEGRADED_SUCCESS) and not check_artifacts_done(ticket_id, worktree):
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
        "archetype": archetype, "max_turns": max_turns, "wall_clock_s": wall_clock,
        "classification": classification, "raw_classification": raw_classification,
        "exit_code": exit_code, "cpu_s": cpu_s,
        "killed_reason": killed_reason, "turns": state.turns,
        "session_id": state.session_id, "resumed_from": resume_session,
        "saw_limit": state.saw_limit, "latest_resets_at": state.latest_resets_at,
        "compact_boundaries": state.compact_boundaries,
        # wq#617 refusal awareness: where refused turns landed (§C), for the orchestrator
        "refusal_fallbacks": state.refusal_fallbacks,
        "refusal_no_fallback": state.saw_refusal_no_fallback,
        "model_fallbacks": state.model_fallbacks, "api_retries": state.api_retries,
        "result_subtype": state.result_subtype, "elapsed_s": elapsed,
        "started_at": t0, "finished_at": time.time(),
    }
    state_path = write_attempt_state(ticket_id, attempt, attempt_record)

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


def check_artifacts_done(ticket_id, worktree):
    """RELIABILITY_SPEC.md §C10/§C12: never trust the model's own self-report (a
    result:success frame, or a silent/odd frame shape) — verify against artifacts.
    Primary: wq status==done (the worker calls `wq done` itself on a real finish, the
    most direct signal there is). Secondary corroboration: a commit on the default
    branch referencing the ticket, for the case where real work landed but the worker
    died/got cut off before it could close the ticket itself."""
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


def handle_failure(args, ticket_id, attempt, classification, state, events, worktree):
    prior_notes_path = write_prior_attempt_notes(ticket_id, attempt, worktree, events,
                                                  classification)
    if attempt < MAX_ATTEMPTS:
        log(f"{classification}: attempt {attempt} < {MAX_ATTEMPTS}, restarting fresh worktree")
        return do_attempt(args, attempt + 1)

    transcript_hint = f"~/.claude/projects/*/{state.session_id}.jsonl" if state.session_id else "unknown"
    reason = f"{classification} after {attempt} attempts; transcript={transcript_hint}"
    log(f"BLOCKED: {reason}")
    wq_block(ticket_id, reason)
    ntfy("foreman BLOCKED", f"wq#{ticket_id}: {reason}", priority="high")
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

    claimed, msg = wq_claim(args.ticket)
    if not claimed:
        log(f"CLAIM_ERROR: {msg}")
        print("CLAIM_ERROR")
        return EXIT_CLAIM_ERROR

    attempt = next_attempt_n(args.ticket)
    rc = do_attempt(args, attempt)
    return rc


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
    p_run.add_argument("--claude-bin", default="claude",
                        help="override for testing against a fake claude shim")
    p_run.set_defaults(func=cmd_run)

    args = ap.parse_args()
    rc = args.func(args)
    sys.exit(rc if isinstance(rc, int) else 0)


if __name__ == "__main__":
    main()
