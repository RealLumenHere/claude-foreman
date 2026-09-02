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
stage 10) is a THIRD thing again: cheap and offline, called on every dispatch pass. It
recomputes hash(rendered_settings + preflight_cmds) and refuses to let the caller
spawn unless that hash matches the stamp's — settings or commands changed since the
last proof reads as "no valid stamp", same as no stamp at all.

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

# §2a: "one small shared inspection profile... stop writing pipeline-specific rules
# forever." This probe is repo-agnostic (every profile includes base, and base is
# where the inspection verbs live) so it always runs, proving pipe targets are
# covered regardless of which toolchain profiles a repo declares.
INSPECTION_PIPE_PROBE = "git status | tail -5 | grep -c . | wc -l"

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
    return cmds


def preflight_hash(rendered_settings, cmds):
    blob = json.dumps({"settings": rendered_settings, "cmds": cmds}, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def stamp_path(repo_path, stamp_dir=None):
    d = stamp_dir or STAMP_DIR
    return os.path.join(d, f"{permission_profiles.repo_name(repo_path)}.ok")


def write_stamp(repo_path, digest, cmds, stamp_dir=None):
    d = stamp_dir or STAMP_DIR
    os.makedirs(d, exist_ok=True)
    payload = {"repo": permission_profiles.repo_name(repo_path), "hash": digest, "cmds": cmds,
               "proofed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = stamp_path(repo_path, d)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)
    return path


def read_stamp(repo_path, stamp_dir=None):
    path = stamp_path(repo_path, stamp_dir)
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return json.load(f)


def stamp_is_fresh(repo_path, digest, stamp_dir=None):
    stamp = read_stamp(repo_path, stamp_dir)
    return bool(stamp) and stamp.get("hash") == digest


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


def run_live_proof(repo_path, settings_path, cmds, claude_bin="claude",
                    model=CHEAPEST_MODEL, runner=None):
    """Spawn (or, under `runner`, simulate) one throwaway headless session and return
    the list of permission-denial texts observed (empty = clean proof)."""
    runner = runner or _default_runner
    prompt = build_probe_prompt(cmds)
    cmd = [claude_bin, "-p", prompt, "--model", model, "--settings", settings_path,
           "--permission-mode", "acceptEdits", "--output-format", "stream-json", "--verbose"]
    frames = runner(cmd, repo_path)
    return _frame_denials(frames)


class PreflightResult:
    def __init__(self, repo_path, ok, reason, static_failures, denials, digest=None):
        self.repo_path = repo_path
        self.ok = ok
        self.reason = reason
        self.static_failures = static_failures
        self.denials = denials
        self.digest = digest

    def to_dict(self):
        return {"repo": permission_profiles.repo_name(self.repo_path), "ok": self.ok,
                "reason": self.reason, "static_failures": self.static_failures,
                "denials": self.denials, "hash": self.digest}


def preflight(repo_path, claude_bin="claude", model=CHEAPEST_MODEL, runner=None,
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
    digest = preflight_hash(rendered, cmds)

    static_failures = static_lint(cmds, perms.get("allow", []), perms.get("deny", []))
    if static_failures:
        return PreflightResult(repo_path, ok=False, reason="static lint failed",
                                static_failures=static_failures, denials=[], digest=digest)

    denials = run_live_proof(repo_path, settings_path, cmds, claude_bin=claude_bin,
                              model=model, runner=runner)
    if denials:
        return PreflightResult(repo_path, ok=False, reason="live proof found permission denials",
                                static_failures=[], denials=denials, digest=digest)

    write_stamp(repo_path, digest, cmds, stamp_dir=stamp_dir)
    return PreflightResult(repo_path, ok=True, reason="ok", static_failures=[], denials=[],
                            digest=digest)


def dispatch_gate_check(repo_path, settings_file=None, render_dir=None, stamp_dir=None):
    """Cheap, offline check the dispatcher runs before every spawn (§2c dispatch gate).
    Returns (ok, reason); reason is None when ok. Unmigrated repos always pass — see
    module docstring."""
    repo_conf = load_repo_config(repo_path)
    if repo_conf is None:
        return True, None

    _settings_path, rendered, cmds = _rendered_settings_and_cmds(
        repo_path, repo_conf, settings_file or SETTINGS_FILE, render_dir or RENDER_DIR)
    digest = preflight_hash(rendered, cmds)
    if stamp_is_fresh(repo_path, digest, stamp_dir=stamp_dir):
        return True, None

    name = permission_profiles.repo_name(repo_path)
    if read_stamp(repo_path, stamp_dir=stamp_dir) is None:
        return False, f"no preflight stamp for {name} — run harness-preflight {repo_path}"
    return False, (f"stale preflight stamp for {name} (settings or preflight_cmds changed "
                    f"since last proof) — re-run harness-preflight {repo_path}")
