"""wq#2128 (dispatch rebuild step 4): who pays for a failed attempt.

Pure logic, no fworker imports, so the rule table, the backend cooldown file and the
local-prompt size gate are unit-testable on their own. Rule table:
research/DISPATCH_FIX_RESEARCH.md section 2.2.

  charged_to  meaning                                        effect
  ----------  ---------------------------------------------  -------------------------
  ticket      the work itself failed                         counts toward MAX_ATTEMPTS
  rung        the model backend failed (hang before any      released to open, not
              turn, LiteLLM 5xx/timeout/cooldown, unreach-   counted, backend cooled
              able, ctx overflow)                            down
  harness     our own config/spawn broke                     uncharged (as before)
  budget      a quota/budget ceiling                         as before

Lever: FOREMAN_CHARGE_MODE=legacy turns every step-4 behaviour off (rung typing, the
first-frame split, the slim local prompt and its size gate).
wq#2185 adds the local CLI profile (fworker.LOCAL_CLI_FLAGS + local_profile_env: six
tools, no MCP, one slot per worker), the calibrated CLI overhead in the size gate, the
first-frame budget refusal (PROMPT_OVER_BUDGET) and the prompt_over_budget charge (the
harness, not the rung, when we sent more than the box could prefill). Its own lever:
FOREMAN_LOCAL_CLI_PROFILE=off restores the bare CLI command and the prompt-only gate.
"""
import fcntl
import json
import os
import re
import time

CHARGE_TICKET = "ticket"
CHARGE_RUNG = "rung"
CHARGE_HARNESS = "harness"
CHARGE_BUDGET = "budget"

RUNG_FAULT_PREFIX = "RUNG_FAULT:"

COOLDOWN_BASE_S = 15 * 60
COOLDOWN_MAX_S = 2 * 60 * 60

# Chars-per-token used for the local prompt size gate; deliberately conservative
# (real tokenizers land nearer 4) so the gate errs toward refusing.
CHARS_PER_TOKEN = 3.5
PROMPT_CTX_FRACTION = 0.6
DEFAULT_SERVED_CTX = 32768

# wq#2185: fixed per-request overhead of `claude -p` under the local CLI profile
# (fworker.LOCAL_CLI_FLAGS + local_profile_env), chars/3.5 like everything else here,
# measured by `python3 harness/cli_calibration.py` on the CLI version below. A unit test
# fails when the installed CLI differs, or a fresh measurement drifts from this number.
CALIBRATED_CLI_VERSION = "2.1.276"
LOCAL_CLI_OVERHEAD_TOKENS = 3925
# Two slots on one Mac prefill at once and time-slice: a 250-token request took 352 s
# behind a 16k prefill (dispatcher/config.json local_backends note, wq#2130).
SHARED_BOX_FACTOR = 2

CLOUD_FIRST_FRAME_S = 240
DEFAULT_LOCAL_FIRST_FRAME_S = 1500

# Classification names mirror classifier.py's string constants.
_BACKEND_EVIDENCE_CLASSES = {"CRASH", "LAUNCH_FAILURE", "INDETERMINATE", "KILLED", "HANG"}

_CTX_OVERFLOW_RE = re.compile(
    r"context[_ ]length[_ ]exceeded|maximum context length|exceeds? the (?:available )?"
    r"context|prompt is too long|input is too long|context window (?:is )?(?:full|exceeded)"
    r"|ContextWindowExceededError", re.I)
_BACKEND_DOWN_RE = re.compile(
    r"ECONNREFUSED|ECONNRESET|ETIMEDOUT|Connection refused|Connection reset|"
    r"Bad Gateway|Service Unavailable|Gateway Time-?out|no healthy deployments|"
    r"\bcooldown\b|InternalServerError|APIConnectionError|APITimeoutError|"
    r"litellm\.Timeout|Request timed out|Name or service not known|"
    r"Network is unreachable|No route to host",
    re.I)
_BACKEND_5XX_RE = re.compile(r"\b(?:HTTP[/ \d.]*|status(?: code)?[: =]+|API Error:? ?)5\d\d\b", re.I)


def legacy_mode(env=None):
    return (env if env is not None else os.environ).get("FOREMAN_CHARGE_MODE", "") == "legacy"


def local_cli_profile_on(is_local, env=None):
    """wq#2185: the restricted CLI profile applies to local-rung workers only, and both
    FOREMAN_CHARGE_MODE=legacy and FOREMAN_LOCAL_CLI_PROFILE=off switch it off."""
    e = env if env is not None else os.environ
    return bool(is_local) and not legacy_mode(e) and e.get("FOREMAN_LOCAL_CLI_PROFILE", "") != "off"


def _int_env(name, default, env=None):
    try:
        v = int((env if env is not None else os.environ).get(name, default))
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# timeouts
# ---------------------------------------------------------------------------

def first_frame_timeout_s(is_local, env=None):
    """Cloud stays at 240. Local comes from FOREMAN_FIRST_FRAME_S (the dispatcher passes
    it per backend), default 1500: prompt eval on the Macs is 8-15 tok/s on a CPU-only
    box, so a 4k prompt is a 1-8 minute first frame."""
    if not is_local:
        return CLOUD_FIRST_FRAME_S
    return _int_env("FOREMAN_FIRST_FRAME_S", DEFAULT_LOCAL_FIRST_FRAME_S, env)


def is_model_frame(frame):
    """The first frame that proves the backend produced output. The CLI's system/init
    frame (and api_retry chatter) is emitted before the first model call, so it says
    nothing about a slow prompt eval."""
    return isinstance(frame, dict) and frame.get("type") not in (None, "system")


# ---------------------------------------------------------------------------
# cause typing
# ---------------------------------------------------------------------------

def _frame_texts(events, result_text):
    if isinstance(result_text, str) and result_text:
        yield result_text
    for ev in events or ():
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "unparsed":
            yield str(ev.get("raw") or "")
            continue
        if ev.get("type") == "result" and isinstance(ev.get("result"), str):
            yield ev["result"]
        if ev.get("type") == "api_error" or ev.get("api_error"):
            yield json.dumps(ev.get("error") or ev.get("api_error") or ev, default=str)
        if ev.get("type") == "system" and ev.get("subtype") == "api_retry":
            status = ev.get("error_status")
            yield f"api_retry {ev.get('error') or ''} status {status if status else ''}"
            if isinstance(status, int) and status >= 500:
                yield f"HTTP {status}"


def backend_fault_cause(events, result_text=None):
    """Return one of ctx_overflow / backend_5xx / backend_unreachable, or None when the
    transcript carries no evidence the backend (rather than the work) failed."""
    texts = list(_frame_texts(events, result_text))
    for t in texts:
        if _CTX_OVERFLOW_RE.search(t):
            return "ctx_overflow"
    for t in texts:
        if _BACKEND_5XX_RE.search(t):
            return "backend_5xx"
    for t in texts:
        if _BACKEND_DOWN_RE.search(t):
            return "backend_unreachable"
    return None


def classify_charge(classification, *, turns=0, is_local=False, killed_reason=None,
                    events=None, result_text=None, env=None, prompt_tokens=None,
                    prefill_tps=None, first_frame_s=None):
    """(charged_to, failure_cause) for one attempt, per FIX_RESEARCH 2.2.

    Order matters: budget and harness rows first (they are never the backend's fault),
    then the local-rung rows, then the ticket rows. Anything not in the table keeps
    today's behaviour: charged to the ticket.

    wq#2185: a local first_frame kill whose own recorded prompt could not have prefilled
    inside the budget (prompt_tokens / prefill_tps > first_frame_s) is the harness's
    fault, not the rung's: the box was healthy, we sent it too much."""
    cls = classification
    cause = (cls or "").lower() or None
    if cls in ("SUCCESS", "DEGRADED_SUCCESS"):
        return CHARGE_TICKET, None
    if cls in ("LIMIT", "BUDGET"):
        return CHARGE_BUDGET, cause
    if cls == "MODEL_NOT_FOUND":
        return CHARGE_HARNESS, "model_not_found"

    if not legacy_mode(env):
        if (is_local and cls == "HANG" and turns == 0 and killed_reason == "first_frame"
                and sent_too_much(prompt_tokens, prefill_tps, first_frame_s)):
            return CHARGE_HARNESS, "prompt_over_budget"
        if is_local and cls in _BACKEND_EVIDENCE_CLASSES and not (cls == "HANG" and turns > 0):
            evidence = backend_fault_cause(events, result_text)
            if evidence:
                return CHARGE_RUNG, evidence
        if is_local and cls == "HANG" and turns == 0:
            return CHARGE_RUNG, ("first_frame_timeout" if killed_reason == "first_frame"
                                 else "hang_no_turns")

    if cls == "LAUNCH_FAILURE":
        return CHARGE_HARNESS, "launch_failure"
    if cls == "HANG":
        return CHARGE_TICKET, "hang_after_turns" if turns > 0 else "hang_no_turns"
    if cls == "CRASH":
        return CHARGE_TICKET, "crash_after_turns" if turns > 0 else "crash_no_turns"
    return CHARGE_TICKET, cause


def rung_fault_reason(reason):
    """Prefix a terminal block reason so a human (and wq self-heal) can tell a backend
    fault from a work block."""
    reason = reason or ""
    return reason if reason.startswith(RUNG_FAULT_PREFIX) else f"{RUNG_FAULT_PREFIX} {reason}"


def charged_attempts(records, legacy=False):
    """How many of a ticket's attempt records count toward MAX_ATTEMPTS: everything but a
    rung fault. A recorded harness fault (LAUNCH_FAILURE) still counts, as it does today;
    the uncharged harness cases (SpawnRefused, MODEL_NOT_FOUND) write no record at all.
    A record with no charged_to (written before this change) is a ticket charge."""
    return sum(1 for rec in records if isinstance(rec, dict)
               and (legacy or rec.get("charged_to") != CHARGE_RUNG))


# ---------------------------------------------------------------------------
# backend cooldown file (dispatcher/runtime/backend_cooldown.json)
# ---------------------------------------------------------------------------

def cooldown_s(strikes):
    return min(COOLDOWN_BASE_S * (2 ** max(0, strikes - 1)), COOLDOWN_MAX_S)


def _read_cooldown(path):
    try:
        with open(path) as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def mark_backend_cooldown(path, backend, cause, now=None):
    """Record one rung fault against `backend`: 15 min, doubling per consecutive fault
    to a 2 h cap. Consecutive means the previous cooldown ended less than COOLDOWN_MAX_S
    ago; after a clean stretch longer than that the count restarts. Atomic write under
    an flock so concurrent fworkers cannot lose an update. Returns the entry written."""
    now = time.time() if now is None else now
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        doc = _read_cooldown(path)
        prev = doc.get(backend) if isinstance(doc.get(backend), dict) else {}
        recent = prev and now < float(prev.get("until", 0)) + COOLDOWN_MAX_S
        strikes = int(prev.get("strikes", 0)) + 1 if recent else 1
        entry = {"until": now + cooldown_s(strikes), "strikes": strikes,
                 "cause": cause, "last_fault": now}
        doc[backend] = entry
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            json.dump(doc, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    return entry


def backend_cooling(path, backend, now=None):
    now = time.time() if now is None else now
    entry = _read_cooldown(path).get(backend)
    return isinstance(entry, dict) and now < float(entry.get("until", 0))


# ---------------------------------------------------------------------------
# slim local prompt size gate
# ---------------------------------------------------------------------------

def served_ctx(env=None):
    return _int_env("FOREMAN_SERVED_CTX", DEFAULT_SERVED_CTX, env)


def estimate_tokens(text):
    return int(len(text) / CHARS_PER_TOKEN) + 1


def prompt_budget_tokens(env=None):
    return int(PROMPT_CTX_FRACTION * served_ctx(env))


def prompt_tokens_est(text, cli_profile=False):
    """Everything the backend prefills before the first frame: our prompt text plus, under
    the local CLI profile, the CLI's own fixed request (system prompt + the six tool
    definitions), measured once by cli_calibration.py."""
    return estimate_tokens(text) + (LOCAL_CLI_OVERHEAD_TOKENS if cli_profile else 0)


def prompt_over_ctx(text, env=None, cli_profile=False):
    """None when the rendered prompt fits; otherwise a one-line refusal summary."""
    tokens, budget = prompt_tokens_est(text, cli_profile), prompt_budget_tokens(env)
    if tokens <= budget:
        return None
    return (f"rendered local prompt ~{tokens} tokens (chars/{CHARS_PER_TOKEN}"
            f"{', incl. CLI overhead' if cli_profile else ''}) exceeds "
            f"{PROMPT_CTX_FRACTION:.0%} of served ctx {served_ctx(env)} = {budget}")


def prefill_tps(env=None):
    """Measured prompt-eval rate of the backend, tokens/s; the dispatcher passes it per
    backend as FOREMAN_PREFILL_TPS. None when unknown (no budget check is possible)."""
    try:
        v = float((env if env is not None else os.environ).get("FOREMAN_PREFILL_TPS", ""))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def prefill_seconds(tokens, tps, shared_box=False):
    """Estimated first-frame prefill time; `shared_box` doubles it (both slots prefill
    at once and time-slice, wq#2130's measurement)."""
    return tokens / tps * (SHARED_BOX_FACTOR if shared_box else 1)


def prompt_over_budget(tokens, first_frame_s, env=None):
    """None when the prompt can prefill inside the first-frame budget; otherwise a
    one-line refusal summary. wq#2185: a spawn that cannot finish its first frame is
    refused before Mac time is spent, as a harness fault, not tried and killed."""
    tps = prefill_tps(env)
    if tps is None or not first_frame_s:
        return None
    est = prefill_seconds(tokens, tps, shared_box=True)
    if est <= first_frame_s:
        return None
    return (f"prompt ~{tokens} tokens / prefill {tps:g} tok/s x{SHARED_BOX_FACTOR} (shared box) "
            f"= ~{est:.0f}s exceeds the {first_frame_s}s first-frame budget")


def sent_too_much(tokens, tps, first_frame_s):
    """True when the recorded prompt alone could not have prefilled inside the budget on
    a healthy, unshared box: a first_frame kill of such an attempt says nothing about
    the backend."""
    return bool(tokens and tps and first_frame_s and tokens / tps > first_frame_s)
