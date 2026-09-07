"""harness/harness_preflight.py — ISSUE-SOLUTIONS-fable.md §2c: harness-preflight <repo>.

PERMISSION_LOOP (ISSUE-LOG.md #14) kept killing waves hours into a run because a
repo's rendered permission pack (§2b, permission_profiles.py) was missing a rule the
worker's actual build/test/probe commands needed — discovered live, mid-wave, with the
fleet circuit breaker already tripping. This module proves the pack BEFORE dispatch,
two layers:

  1. static_lint(): offline, split each declared command on the same operators Claude
     Code's permission layer splits on (&&, ||, ;, |) and check every segment against
     the rendered profile's allow/deny rules. Catches a missing rule in milliseconds,
     no quota spent.
  2. run_live_proof(): the truth — spawn one throwaway headless `claude -p` session in
     the repo under the rendered settings and watch its stream-json for the harness's
     own "requires approval"/"blocked. For security" refusal text
     (action_gate.is_permission_denial, same signal PERMISSION_LOOP classification
     already keys off). Zero denials -> write a stamp.

Only static_lint has to pass before run_live_proof is attempted (no point spawning a
session for a pack the static layer already proved broken).

The dispatch gate (dispatch_gate_check(), wired into dispatcher/foreman_dispatcher.py
stage 9b) is a THIRD thing again: cheap and offline, called on every dispatch pass.

wq#1409 (research/NIGHTLY_DISPATCH_RESILIENCE.md §1.4/§2.1/§3.2): the gate keys on
profile_digest() — sorted(set(allow)), sorted(set(deny)), permissions.defaultMode and
sorted(set(cmds)) — which is exactly the input set the proof covers (does the rendered
allow/deny set deny any preflight command, statically and live). It used to key on
the whole rendered dict + ordered cmds, so a hook edit, a rule reorder (wq#1382) or a
reworded probe constant re-staled every stamp with an identical rule set: 760
no_fresh_stamp skips in ~26h on 2026-09-02/03 while ~0 work ran. Two staleness
classes now:
  HARD  profile_digest differs -> the permission set really changed -> reprove
        before any spawn (never loosened; dispatcher retries transient errors).
  SOFT  profile_digest matches, settings_hash (old full-dict digest) differs ->
        benign churn -> (True, "stamp_soft_stale ...") so the caller dispatches,
        logs it once and reseals the stamp.
FOREMAN_STAMP_STRICT=1 restores full-dict gating (rollback lever, no code change).

Repos with no permission-profiles/repos.d/<repo>.json are not migrated to the profile
system (permission_profiles.py's own fallback boundary) and are not gated here either
— additive migration, no regression for a repo nobody has onboarded yet.
"""
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HARNESS_DIR)
import permission_profiles  # noqa: E402
from action_gate import is_permission_denial  # noqa: E402

SETTINGS_FILE = os.environ.get("FOREMAN_SETTINGS_FILE",
                                os.path.join(HARNESS_DIR, "worker-settings.json"))
STAMP_DIR = os.environ.get("FOREMAN_PREFLIGHT_STAMP_DIR",
                            os.path.join(HARNESS_DIR, "runtime", "preflight"))
RENDER_DIR = os.environ.get("FOREMAN_PREFLIGHT_RENDER_DIR",
                             os.path.join(HARNESS_DIR, "runtime", "preflight", "render"))
CHEAPEST_MODEL = "claude-haiku-4-5"
LIVE_PROOF_TIMEOUT = 300
# wq#1407: the dispatcher unit's PATH has no /home/lumen/.npm-global/bin, so a bare
# "claude" spawn from handle_stale_preflight raised FileNotFoundError on every auto-
# reprove (14/14 on 2026-09-03). Same default fworker.py uses.
CLAUDE_BIN = os.environ.get("FOREMAN_CLAUDE_BIN", "/home/lumen/.npm-global/bin/claude")
# wq#1409 rollback lever: gate on the full rendered dict again (pre-1409 behavior).
STAMP_STRICT = os.environ.get("FOREMAN_STAMP_STRICT") == "1"

# §2a: "one small shared inspection profile... stop writing pipeline-specific rules
# forever." This probe is repo-agnostic (every profile includes base, and base is
# where the inspection verbs live) so it always runs, proving pipe targets are
# covered regardless of which toolchain profiles a repo declares.
INSPECTION_PIPE_PROBE = "git status | tail -5 | grep -c . | wc -l"

# wq#1332 (§3.2 point 4): the fleet-exec allow rule is unconditional (every worker
# gets Bash(/home/lumen/bin/fleet-exec *) — the broker's own grant check, not the
# Bash allowlist, is what denies a ticket with no needs: tags), so one representative
# command per v1 verb is proved here the same way INSPECTION_PIPE_PROBE proves the
# pipe shape: repo-agnostic, always appended, regardless of this repo's declared
# verbs/preflight_cmds.
FLEET_EXEC_BIN = "/home/lumen/bin/fleet-exec"
FLEET_EXEC_PREFLIGHT_PROBES = [
    f"{FLEET_EXEC_BIN} run isis -- true",
    f"{FLEET_EXEC_BIN} copy isis /tmp/probe /home/isis/lumen-drop",
    f"{FLEET_EXEC_BIN} restart isis probe.service",
    f"{FLEET_EXEC_BIN} grant",
]

_OPERATORS = {"&&", "||", ";", "|"}


def split_segments(cmd):
    """Split `cmd` into segments the way Claude Code's permission layer does: on
    &&, ||, ;, | (§2a) — each segment is checked against the allowlist separately."""
    lex = shlex.shlex(cmd, posix=True, punctuation_chars="|&;")
    lex.whitespace_split = True
    segments, current = [], []
    for tok in lex:
        if tok in _OPERATORS:
            if current:
                segments.append(" ".join(current))
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(" ".join(current))
    return segments


def _rule_inner(rule):
    if rule.startswith("Bash(") and rule.endswith(")"):
        return rule[len("Bash("):-1]
    return None


def _segment_matches(segment, rule_inner):
    if rule_inner.endswith("*"):
        return segment.startswith(rule_inner[:-1])
    return segment == rule_inner


def segment_rule(segment, rules):
    """First rule (raw 'Bash(...)' string) matching `segment`, else None."""
    seg = segment.strip()
    for rule in rules:
        inner = _rule_inner(rule)
        if inner is not None and _segment_matches(seg, inner):
            return rule
    return None


def static_lint(cmds, allow, deny):
    """Offline layer. Returns a list of failure strings (empty = clean). A segment
    hitting a deny rule fails even if some allow rule also matches it (deny wins,
    matching Claude Code's own precedence)."""
    failures = []
    for cmd in cmds:
        for segment in split_segments(cmd):
            if not segment.strip():
                continue
            deny_hit = segment_rule(segment, deny)
            if deny_hit:
                failures.append(f"{cmd!r}: segment {segment!r} denied by {deny_hit}")
                continue
            if not segment_rule(segment, allow):
                failures.append(f"{cmd!r}: segment {segment!r} matches no allow rule")
    return failures


def load_repo_config(repo_path):
    conf_path = permission_profiles.repo_config_path(repo_path)
    if not os.path.isfile(conf_path):
        return None
    with open(conf_path) as f:
        return json.load(f)


def preflight_commands(repo_path, repo_conf):
    """§2c: 'each verbs script plus a preflight_cmds list... plus a worst-case
    compound that pipes through the inspection verbs.'"""
    cmds = list(repo_conf.get("preflight_cmds", []))
    repo_abs = os.path.normpath(repo_path)
    for verb in repo_conf.get("verbs", []):
        cmds.append(os.path.join(repo_abs, verb))
    cmds.append(INSPECTION_PIPE_PROBE)
    cmds.extend(FLEET_EXEC_PREFLIGHT_PROBES)
    return cmds


def preflight_hash(rendered_settings, cmds):
    """Full-dict digest (pre-wq#1409 gating key). Still recorded in every stamp as
    `settings_hash` so a benign change is visible (soft-stale), and still the gating
    key under FOREMAN_STAMP_STRICT=1."""
    blob = json.dumps({"settings": rendered_settings, "cmds": cmds}, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def profile_digest(rendered_settings, cmds):
    """wq#1409 §2.1: digest of ONLY what the proof covers. Order-insensitive and
    duplicate-insensitive on purpose — permission_profiles already unions/dedups, so
    two renders with the same rule set are the same profile whatever order the
    fragments were merged in. Hooks, model, docstrings, key order: not inputs."""
    perms = rendered_settings.get("permissions", {}) or {}
    blob = json.dumps({
        "allow": sorted(set(perms.get("allow", []) or [])),
        "deny": sorted(set(perms.get("deny", []) or [])),
        "mode": perms.get("defaultMode"),
        "cmds": sorted(set(cmds)),
    }, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def gating_digest(rendered_settings, cmds):
    """The digest a stamp must match for the repo to dispatch."""
    if STAMP_STRICT:
        return preflight_hash(rendered_settings, cmds)
    return profile_digest(rendered_settings, cmds)


def stamp_path(repo_path, stamp_dir=None):
    d = stamp_dir or STAMP_DIR
    return os.path.join(d, f"{permission_profiles.repo_name(repo_path)}.ok")


def _write_stamp_payload(repo_path, payload, stamp_dir=None):
    d = stamp_dir or STAMP_DIR
    os.makedirs(d, exist_ok=True)
    path = stamp_path(repo_path, d)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)
    return path


def write_stamp(repo_path, digest, cmds, stamp_dir=None, settings_hash=None,
                profile_hash=None):
    """Stamp format is unchanged for existing readers ({repo, hash, cmds, proofed_at});
    wq#1409 adds `profile_hash` (the gating key) and `settings_hash` (full-dict) next
    to it. `hash` stays equal to whatever gates — profile_hash normally, the full-dict
    hash under FOREMAN_STAMP_STRICT=1 — so any reader that only knows `hash` keeps
    reading the gating value."""
    payload = {"repo": permission_profiles.repo_name(repo_path), "hash": digest, "cmds": cmds,
               "profile_hash": profile_hash or digest,
               "settings_hash": settings_hash,
               "proofed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return _write_stamp_payload(repo_path, payload, stamp_dir)


def read_stamp(repo_path, stamp_dir=None):
    path = stamp_path(repo_path, stamp_dir)
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return json.load(f)


STAMP_FRESH, STAMP_SOFT_STALE, STAMP_HARD_STALE, STAMP_MISSING = (
    "fresh", "soft_stale", "hard_stale", "missing")


def stamp_status(repo_path, rendered_settings, cmds, stamp_dir=None):
    """Classify the stamp against the current render (wq#1409 §2.1). Returns one of
    STAMP_FRESH / STAMP_SOFT_STALE / STAMP_HARD_STALE / STAMP_MISSING.

    A pre-1409 stamp carries only `hash` (= full-dict digest). It is honoured as
    fresh iff that equals today's full-dict digest — the proof it records was made
    under exactly this render, nothing is unproven — and is hard-stale otherwise, so
    the next reprove writes the new format. No rollout reprove storm on a fresh
    legacy stamp; no legacy stamp ever passes a changed profile."""
    stamp = read_stamp(repo_path, stamp_dir)
    if not stamp:
        return STAMP_MISSING
    gating = gating_digest(rendered_settings, cmds)
    settings_hash = preflight_hash(rendered_settings, cmds)
    if STAMP_STRICT:
        strict_key = stamp.get("settings_hash") or stamp.get("hash")
        return STAMP_FRESH if strict_key == gating else STAMP_HARD_STALE
    stamped_profile = stamp.get("profile_hash")
    if stamped_profile is None:  # legacy stamp
        return STAMP_FRESH if stamp.get("hash") == settings_hash else STAMP_HARD_STALE
    if stamped_profile != gating:
        return STAMP_HARD_STALE
    if stamp.get("settings_hash") != settings_hash:
        return STAMP_SOFT_STALE
    return STAMP_FRESH


def stamp_is_fresh(repo_path, digest, stamp_dir=None):
    """Kept for callers that hold a digest: true iff the stamp's gating value is it."""
    stamp = read_stamp(repo_path, stamp_dir)
    return bool(stamp) and stamp.get("hash") == digest


def reseal_stamp(repo_path, settings_file=None, render_dir=None, stamp_dir=None):
    """wq#1409 §3.2: a soft-stale stamp (profile unchanged, full-dict digest moved)
    still proves the profile. Refresh `settings_hash` in place — proofed_at is kept
    (it is the proof's time, not this write's), `resealed_at` records the churn — so
    the dispatcher's stamp_soft_stale event fires once per benign change instead of
    on every pass until the next real reprove. Refuses (returns None) unless the
    stamp is exactly soft-stale right now: never turns a hard-stale or legacy stamp
    into a fresh one without a proof."""
    repo_conf = load_repo_config(repo_path)
    if repo_conf is None:
        return None
    _settings_path, rendered, cmds = _rendered_settings_and_cmds(
        repo_path, repo_conf, settings_file or SETTINGS_FILE, render_dir or RENDER_DIR)
    if stamp_status(repo_path, rendered, cmds, stamp_dir=stamp_dir) != STAMP_SOFT_STALE:
        return None
    stamp = read_stamp(repo_path, stamp_dir)
    stamp["settings_hash"] = preflight_hash(rendered, cmds)
    stamp["cmds"] = cmds
    stamp["resealed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return _write_stamp_payload(repo_path, stamp, stamp_dir)


def _rendered_settings_and_cmds(repo_path, repo_conf, settings_file, render_dir):
    settings_path, _digest = permission_profiles.resolve_settings_file(
        repo_path, settings_file, render_dir)
    with open(settings_path) as f:
        rendered = json.load(f)
    cmds = preflight_commands(repo_path, repo_conf)
    return settings_path, rendered, cmds


def build_probe_prompt(cmds):
    numbered = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(cmds))
    return ("Run exactly these commands, one at a time, in this repo's root, and report "
            "each exit code. Do not modify anything. If a command fails, still move on "
            "and report the next command's exit code.\n" + numbered)


def _result_text(block):
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return ""


def _frame_denials(frames):
    denials = []
    for frame in frames:
        if frame.get("type") != "user":
            continue
        msg = frame.get("message")
        content = (msg.get("content") if isinstance(msg, dict) else None) or []
        for b in content:
            if not (isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error")):
                continue
            text = _result_text(b)
            if is_permission_denial(text):
                denials.append(text[:300])
    return denials


def _default_runner(cmd, cwd):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=LIVE_PROOF_TIMEOUT)
    frames = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            frames.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return frames


def run_live_proof(repo_path, settings_path, cmds, claude_bin=None,
                    model=CHEAPEST_MODEL, runner=None):
    """Spawn (or, under `runner`, simulate) one throwaway headless session and return
    the list of permission-denial texts observed (empty = clean proof)."""
    runner = runner or _default_runner
    claude_bin = claude_bin or CLAUDE_BIN
    prompt = build_probe_prompt(cmds)
    cmd = [claude_bin, "-p", prompt, "--model", model, "--settings", settings_path,
           "--permission-mode", "acceptEdits", "--output-format", "stream-json", "--verbose"]
    frames = runner(cmd, repo_path)
    return _frame_denials(frames)


class PreflightResult:
    def __init__(self, repo_path, ok, reason, static_failures, denials, digest=None,
                 profile_hash=None, settings_hash=None):
        self.repo_path = repo_path
        self.ok = ok
        self.reason = reason
        self.static_failures = static_failures
        self.denials = denials
        self.digest = digest
        self.profile_hash = profile_hash
        self.settings_hash = settings_hash

    def to_dict(self):
        return {"repo": permission_profiles.repo_name(self.repo_path), "ok": self.ok,
                "reason": self.reason, "static_failures": self.static_failures,
                "denials": self.denials, "hash": self.digest,
                "profile_hash": self.profile_hash, "settings_hash": self.settings_hash}


def preflight(repo_path, claude_bin=None, model=CHEAPEST_MODEL, runner=None,
              settings_file=None, render_dir=None, stamp_dir=None):
    """Run both layers and, on a clean proof, write the stamp. Returns PreflightResult."""
    repo_conf = load_repo_config(repo_path)
    if repo_conf is None:
        return PreflightResult(repo_path, ok=False,
                                reason="no permission-profiles/repos.d/<repo>.json — "
                                       "repo not migrated to profiles, preflight not applicable",
                                static_failures=[], denials=[])

    settings_path, rendered, cmds = _rendered_settings_and_cmds(
        repo_path, repo_conf, settings_file or SETTINGS_FILE, render_dir or RENDER_DIR)
    perms = rendered.get("permissions", {})
    digest = gating_digest(rendered, cmds)
    hashes = {"profile_hash": profile_digest(rendered, cmds),
              "settings_hash": preflight_hash(rendered, cmds)}

    static_failures = static_lint(cmds, perms.get("allow", []), perms.get("deny", []))
    if static_failures:
        return PreflightResult(repo_path, ok=False, reason="static lint failed",
                                static_failures=static_failures, denials=[], digest=digest,
                                **hashes)

    try:
        denials = run_live_proof(repo_path, settings_path, cmds, claude_bin=claude_bin,
                                  model=model, runner=runner)
    except (OSError, subprocess.TimeoutExpired) as e:
        return PreflightResult(repo_path, ok=False, reason=f"live proof errored: {e!r}",
                                static_failures=[], denials=[], digest=digest, **hashes)
    if denials:
        return PreflightResult(repo_path, ok=False, reason="live proof found permission denials",
                                static_failures=[], denials=denials, digest=digest, **hashes)

    write_stamp(repo_path, digest, cmds, stamp_dir=stamp_dir, **hashes)
    return PreflightResult(repo_path, ok=True, reason="ok", static_failures=[], denials=[],
                            digest=digest, **hashes)


def dispatch_gate_check(repo_path, settings_file=None, render_dir=None, stamp_dir=None):
    """Cheap, offline check the dispatcher runs before every spawn (§2c dispatch gate).
    Returns (ok, reason); reason is None when ok. Unmigrated repos always pass — see
    module docstring."""
    repo_conf = load_repo_config(repo_path)
    if repo_conf is None:
        return True, None

    _settings_path, rendered, cmds = _rendered_settings_and_cmds(
        repo_path, repo_conf, settings_file or SETTINGS_FILE, render_dir or RENDER_DIR)
    status = stamp_status(repo_path, rendered, cmds, stamp_dir=stamp_dir)
    name = permission_profiles.repo_name(repo_path)
    if status == STAMP_FRESH:
        return True, None
    if status == STAMP_SOFT_STALE:
        # wq#1409 §3.2: ok=True with a reason — the caller may dispatch, should log
        # it, and should reseal_stamp() so this fires once, not every pass.
        return True, (f"stamp_soft_stale for {name}: worker-settings/hook/order churn, "
                      f"permission profile unchanged — dispatching on the existing proof")
    if status == STAMP_MISSING:
        return False, f"no preflight stamp for {name} — run harness-preflight {repo_path}"
    return False, (f"stale preflight stamp for {name} (allow/deny/defaultMode/preflight_cmds "
                    f"set changed since last proof) — re-run harness-preflight {repo_path}")
