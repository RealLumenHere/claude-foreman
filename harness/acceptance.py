"""Evidence-gated done — ISSUE-SOLUTIONS-fable.md §1 (ISSUE-LOG #17, FALSE_COMPLETE).

`done` stops being a speech act by the model and becomes a state transition the harness
performs only after it has itself observed the evidence:

  1a  at dispatch, the ticket is turned into a machine-checkable `acceptance.json`,
      written into the worktree and PINNED by hash outside the worker's reach (run_dir);
  1b  the worker's claim is `evidence.json`; the harness re-runs every `test` criterion
      itself and re-checks every `citation` against the diff — the model's logged exit
      code is a hint, never proof;
  1c  `hooks/stop_gate.py` blocks the session from ending while evidence is missing;
  1d  a rejection emits `rework.json` — the repro commands and the harness's own replay
      output — which the next dispatch renders at the top of the prompt.

This module is the pure logic (no wq, no dispatch): fworker.py wires it into the attempt
loop, stop_gate.py into the Stop hook. Zero quota — nothing here calls a model unless a
caller injects one (`derive(..., model_call=...)`).
"""
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess

ACCEPTANCE_FILENAME = "acceptance.json"
EVIDENCE_FILENAME = "evidence.json"
REWORK_FILENAME = "rework.json"
PIN_FILENAME = "acceptance-pin.json"

# wq#1905: acceptance.json/evidence.json used to be written only at the tracked
# worktree root — a worktree of the FOREMAN repo itself, so 82 commits on main each
# clobbered the previous ticket's evidence with its own. The run dir (fworker's
# RUNS_ROOT/wq<id>, never git-tracked) is the durable per-ticket home now; the
# worktree copy is kept only as a fallback for a worker whose sandbox can't reach it.
FOREMAN_ROOT = os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman")
RUNS_ROOT = os.environ.get("FOREMAN_RUNS_ROOT", os.path.join(FOREMAN_ROOT, "_runs"))


def run_dir_path(ticket_id):
    """Same layout as fworker.run_dir(), without fworker's heavier import surface and
    without creating it — a run dir that doesn't exist yet just means callers fall back
    to the worktree copy."""
    return os.path.join(RUNS_ROOT, f"wq{ticket_id}")

# Replaying a criterion means the HARNESS runs the command, so the command has to come
# from a bounded set: acceptance.json is derived from ticket text, and ticket text is
# not a trusted script. First token must be one of these, and no shell metacharacters
# are accepted at all (the replay runs argv-style, never through a shell).
REPLAY_ALLOWED_BINARIES = {
    "pytest", "python", "python3", "make", "npm", "npx", "pnpm", "yarn", "node",
    "jest", "vitest", "tsc", "go", "cargo", "ruff", "mypy", "flake8", "shellcheck",
    "bash", "sh", "git",
}
_SHELL_META = re.compile(r"[;&|<>`$()\\\n]")
_CITATION_RE = re.compile(r"^(?P<path>[^\s:]+):(?P<start>\d+)(?:-(?P<end>\d+))?$")

# Tickets whose deliverable is a document, not a diff, get no baseline diff criterion —
# a report-writing archetype has nothing to cite in `git diff` and must not be gated on
# one. They still get command criteria if the ticket names commands.
NON_DIFF_ARCHETYPES = {"verify-pass", "rca-investigation", "fire-response",
                       "research", "research-synthesis", "documentation"}

VALID_PROOF_TYPES = ("test", "citation")
REPLAY_TIMEOUT_S = int(os.environ.get("FOREMAN_REPLAY_TIMEOUT_S", "900") or 900)
OUTPUT_TAIL_LINES = 30


# ---------------------------------------------------------------------------
# 1a — derivation
# ---------------------------------------------------------------------------

def replayable(cmd):
    """(ok, reason) — may the harness re-run this command itself?"""
    if not cmd or not cmd.strip():
        return False, "empty command"
    if _SHELL_META.search(cmd):
        return False, "shell metacharacters are not replayable (argv-only execution)"
    try:
        argv = shlex.split(cmd)
    except ValueError as e:
        return False, f"unparseable command ({e})"
    if not argv:
        return False, "empty command"
    head = os.path.basename(argv[0])
    if argv[0].startswith("./") or argv[0].startswith("/"):
        # a script path is fine as long as it is a script (a bare path is far more often
        # a file the ticket MENTIONS than a command) and resolves inside the worktree at
        # replay time — the latter is checked in _replay, where the worktree is known.
        if not head.endswith((".sh", ".py")):
            return False, f"{argv[0]!r} is a path, not a runnable script"
        return True, ""
    if head not in REPLAY_ALLOWED_BINARIES:
        return False, f"{head!r} is not in the replay allowlist"
    return True, ""


# wq#1705: "make", "go" and "check" are English verbs as well as build tools, so an
# UNQUOTED ticket line can open with one and still be prose — that ticket was titled
# "make failed guard services self-heal or alert instead of staying dead" and derived
# the criterion `make failed guard services self-heal or alert instead of staying dead`,
# a command no repo can satisfy and which the worker cannot repair (acceptance.json is
# pinned at dispatch). A backticked span is an author saying "this is a command" and is
# still trusted as-is; a bare line has to earn it. Prose gives itself away with function
# words no build target ever uses, and with length — real invocations are short.
_PROSE_TOKENS = frozenset("""
a an and are as at be but by for from if in instead into is it its of on or so than
that the their them then there these they this to until when where which while who
with without you your
""".split())
_MAX_BARE_CMD_TOKENS = 6


def _looks_like_prose(argv):
    """True when a bare line opening with a build-tool name is really a sentence."""
    rest = argv[1:]
    if len(argv) > _MAX_BARE_CMD_TOKENS:
        return True
    return any(tok.lower() in _PROSE_TOKENS for tok in rest)


def _command_candidates(text):
    """Command-shaped spans in ticket text: backticked spans first, then bare lines."""
    out = []
    for span in re.findall(r"`([^`\n]+)`", text or ""):
        out.append(span.strip())
    for line in (text or "").splitlines():
        s = line.strip().lstrip("-*0123456789. ").strip()
        if not s:
            continue
        argv = s.split()
        if argv[0] in ("pytest", "npm", "make", "cargo", "go") and not _looks_like_prose(argv):
            out.append(s)
    seen, uniq = set(), []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def _derive_deterministic(ticket):
    """Ticket text -> criteria, with no model call. Commands the ticket already names
    become `test` criteria; everything else falls back to the one criterion that is
    always machine-checkable for a code ticket — a cited line inside the diff."""
    text = f"{ticket.get('title', '')}\n{ticket.get('detail', '')}"
    criteria, n = [], 0
    for cand in _command_candidates(text):
        ok, _ = replayable(cand)
        if not ok:
            continue
        head = cand.split()[0]
        # Ticket prose is mostly nouns in backticks. Only an invocation that STARTS with
        # a test-runner binary is read as a criterion here — a script path or a `git`
        # incantation is house plumbing or a file being named, not a proof. (A model
        # derivation may still propose those; it is proposing, not pattern-matching.)
        if head == "git" or head not in REPLAY_ALLOWED_BINARIES:
            continue
        n += 1
        criteria.append({"id": f"AC{n}", "text": f"`{cand}` passes",
                         "proof_type": "test", "cmd": cand, "expect_exit": 0})
        if n >= 5:
            break
    archetype = (ticket.get("archetype") or "").strip()
    if archetype not in NON_DIFF_ARCHETYPES:
        criteria.append({
            "id": f"AC{n + 1}",
            "text": "the change is real: cite file:line of the code that implements it, "
                    "in a file this branch actually touches",
            "proof_type": "citation"})
    return criteria


def _validate_criteria(criteria):
    """Keep only well-formed, replayable criteria. Returns (kept, rejected_reasons)."""
    kept, rejected = [], []
    seen = set()
    for c in criteria if isinstance(criteria, list) else []:
        if not isinstance(c, dict):
            rejected.append("non-object criterion")
            continue
        cid = str(c.get("id") or "").strip()
        ptype = str(c.get("proof_type") or "").strip()
        text = str(c.get("text") or "").strip()
        if not cid or cid in seen or ptype not in VALID_PROOF_TYPES or not text:
            rejected.append(f"malformed criterion {cid or '<no id>'}")
            continue
        if ptype == "test":
            ok, why = replayable(c.get("cmd") or "")
            if not ok:
                rejected.append(f"{cid}: {why}")
                continue
            kept.append({"id": cid, "text": text, "proof_type": "test",
                         "cmd": c["cmd"].strip(),
                         "expect_exit": int(c.get("expect_exit", 0) or 0)})
        else:
            kept.append({"id": cid, "text": text, "proof_type": "citation"})
        seen.add(cid)
    return kept, rejected


def derive(ticket, model_call=None):
    """Build the acceptance doc for a ticket.

    `model_call(prompt) -> str` is the optional cheap model call from the spec ("turn
    this ticket body into N criteria; every criterion that can be a command MUST be a
    command"). Anything it returns is validated and any criterion the harness could not
    replay is dropped — a model that invents `pytest tests/does_not_exist.py` costs a
    rejected criterion, never an unrunnable gate. If it returns nothing usable, the
    deterministic derivation stands.
    """
    criteria, derived_by, notes = [], "deterministic", []
    if model_call is not None:
        try:
            raw = model_call(derivation_prompt(ticket))
            doc = json.loads(_json_span(raw))
            criteria, rejected = _validate_criteria(doc.get("criteria"))
            notes.extend(rejected)
            if criteria:
                derived_by = "model"
        except Exception as e:  # a derivation failure must never block a dispatch
            notes.append(f"model derivation failed: {e}")
            criteria = []
    if not criteria:
        criteria, rejected = _validate_criteria(_derive_deterministic(ticket))
        notes.extend(rejected)
        derived_by = "deterministic"
    return {"ticket": f"wq#{ticket.get('id', '?')}", "derived_by": derived_by,
            "criteria": criteria, "notes": notes}


def _json_span(raw):
    """The first {...} span in a model reply — tolerates prose/fences around the JSON."""
    s = raw if isinstance(raw, str) else json.dumps(raw)
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("no JSON object in reply")
    return s[i:j + 1]


def derivation_prompt(ticket):
    return (
        "Turn this ticket into machine-checkable acceptance criteria. Reply with JSON "
        'only: {"criteria":[{"id","text","proof_type","cmd","expect_exit"}]}.\n'
        "- every criterion that CAN be a command MUST be one: proof_type \"test\", with "
        "`cmd` runnable non-interactively from the repo root and `expect_exit` (usually 0)\n"
        "- a criterion that cannot be a command is proof_type \"citation\": it will be "
        "satisfied by a file:line reference inside this branch's diff\n"
        "- 2 to 5 criteria, no prose outside the JSON\n\n"
        f"TICKET wq#{ticket.get('id', '?')}: {ticket.get('title', '')}\n\n"
        f"{ticket.get('detail', '')}\n")


# ---------------------------------------------------------------------------
# pinning — the worker may not move the goalposts
# ---------------------------------------------------------------------------

def doc_hash(doc):
    return hashlib.sha256(
        json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_json(path, doc):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def dispatch_origin_sha(worktree, base_ref="origin/main"):
    """The sha `base_ref` points at right now, resolved once at dispatch time. Frozen
    into the pin record so a later replay diffs against the same commit the worker
    actually started from — not whatever origin/main has moved to by then (which, once
    this ticket's own PR merges, would make HEAD an ancestor of it and collapse the
    diff to nothing; ISSUE-LOG #143/#156)."""
    p = _git(worktree, "rev-parse", base_ref)
    if p and p.returncode == 0 and p.stdout.strip():
        return p.stdout.strip()
    return None


def pin(worktree, run_dir, doc, origin_sha=None):
    """Render acceptance.json into the worktree and pin its hash, plus the dispatch-time
    origin/main sha, in the fworker-owned run dir (which the worker's settings do not
    reach). Returns (path, hash)."""
    path = os.path.join(worktree, ACCEPTANCE_FILENAME)
    write_json(path, doc)
    digest = doc_hash(doc)
    if origin_sha is None:
        origin_sha = dispatch_origin_sha(worktree)
    write_json(os.path.join(run_dir, PIN_FILENAME),
                {"hash": digest, "criteria": len(doc.get("criteria", [])),
                 "ticket": doc.get("ticket"), "doc": doc, "origin_sha": origin_sha})
    # wq#1905: a plain-named copy alongside the wrapped pin record, so tools that just
    # want "the acceptance doc for this ticket" (read_acceptance's run_dir-first lookup)
    # find it without unwrapping acceptance-pin.json. This copy is never rewritten after
    # dispatch, unlike the worktree one — it is not what the tamper check inspects.
    write_json(os.path.join(run_dir, ACCEPTANCE_FILENAME), doc)
    return path, digest


def _pin_record(run_dir):
    return read_json(os.path.join(run_dir, PIN_FILENAME)) or {}


def pinned_hash(run_dir):
    return _pin_record(run_dir).get("hash")


def pinned_doc(run_dir):
    """The doc as pinned at dispatch — the only copy that governs. The worktree copy is
    the worker's convenience; this one is what verification reads."""
    return _pin_record(run_dir).get("doc")


def pinned_origin_sha(run_dir):
    """The origin/main sha frozen at dispatch time, or None for pins made before this
    field existed (falls back to the live ref in that case)."""
    return _pin_record(run_dir).get("origin_sha")


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def read_acceptance(worktree, run_dir=None):
    """wq#1905: run_dir first when given — it is the copy pin() wrote once at dispatch
    and nothing after that touches. Callers doing TAMPER detection (did the worker edit
    its mutable worktree copy?) must not pass run_dir here; they need the worktree copy
    specifically, by design."""
    if run_dir:
        doc = read_json(os.path.join(run_dir, ACCEPTANCE_FILENAME))
        if doc is not None:
            return doc
    return read_json(os.path.join(worktree, ACCEPTANCE_FILENAME))


def read_evidence(worktree, run_dir=None):
    """wq#1905: run_dir first when given. Pair with snapshot_evidence() beforehand if
    the run_dir copy needs to be fresh — this function alone never looks at the
    worktree once a run_dir copy exists."""
    if run_dir:
        doc = read_json(os.path.join(run_dir, EVIDENCE_FILENAME))
        if doc is not None:
            return doc
    return read_json(os.path.join(worktree, EVIDENCE_FILENAME))


def snapshot_evidence(worktree, run_dir):
    """wq#1905: copy the worker's worktree evidence.json into run_dir right before a
    replay. The fworker process (unlike the sandboxed worker, which cannot write outside
    its worktree/add-dirs) can always reach run_dir, so this is what makes run_dir the
    replay's actual source rather than merely a second place a stale copy could sit.
    Best-effort: a worktree the worker never wrote to, or a run_dir the caller's own
    sandbox can't reach (the read-only self-check CLI, invoked BY the worker), must not
    break the read that follows — it just falls back to the worktree copy instead."""
    src = os.path.join(worktree, EVIDENCE_FILENAME)
    try:
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(run_dir, EVIDENCE_FILENAME))
    except OSError:
        pass


# wq#1905: these two filenames are per-ticket claims, not source — 82 commits on main
# each clobbered a different ticket's evidence.json before the tracked-tree copies were
# removed (git rm --cached) and gitignored. staged_leak() is the lint: a non-empty
# result means one of them is staged for commit in `worktree` and the commit must be
# refused, not warned about.
LEAK_FILENAMES = (ACCEPTANCE_FILENAME, EVIDENCE_FILENAME)


def staged_leak(worktree):
    try:
        p = subprocess.run(["git", "-C", worktree, "diff", "--cached", "--name-only"],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    if p.returncode != 0:
        return []
    staged = set(p.stdout.splitlines())
    return [name for name in LEAK_FILENAMES if name in staged]


# ---------------------------------------------------------------------------
# 1c — the cheap, no-replay completeness check the Stop hook runs
# ---------------------------------------------------------------------------

def gaps(acceptance, evidence):
    """What is missing from `evidence` relative to `acceptance`, without running
    anything. One dict per gap: {id, need}."""
    out = []
    criteria = (acceptance or {}).get("criteria") or []
    by_id = {}
    for e in ((evidence or {}).get("criteria") or []):
        if isinstance(e, dict) and e.get("id"):
            by_id[str(e["id"])] = e
    for c in criteria:
        cid = str(c.get("id"))
        e = by_id.get(cid)
        if e is None:
            need = (f"run `{c.get('cmd')}` and record cmd_run/exit_code"
                    if c.get("proof_type") == "test"
                    else "record a file:line citation")
            out.append({"id": cid, "need": f"no entry in {EVIDENCE_FILENAME} — {need}"})
            continue
        if c.get("proof_type") == "test":
            if not e.get("cmd_run"):
                out.append({"id": cid, "need": "cmd_run missing"})
            if e.get("exit_code") is None:
                out.append({"id": cid, "need": "exit_code missing"})
        elif not str(e.get("citation") or "").strip():
            out.append({"id": cid, "need": "citation missing (file:line)"})
    return out


# ---------------------------------------------------------------------------
# 1b — verification by replay
# ---------------------------------------------------------------------------

def _git(worktree, *args):
    try:
        return subprocess.run(["git", "-C", worktree] + list(args),
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None


def changed_files(worktree, base_ref="origin/main"):
    """Files this branch touches: committed diff against the merge-base, plus anything
    still dirty in the worktree (evidence is often written before `lumen-pr commit`)."""
    names = set()
    base = None
    for ref in (base_ref, "main", "origin/HEAD"):
        p = _git(worktree, "merge-base", "HEAD", ref)
        if p and p.returncode == 0 and p.stdout.strip():
            base = p.stdout.strip()
            break
    if base:
        p = _git(worktree, "diff", "--name-only", f"{base}..HEAD")
        if p and p.returncode == 0:
            names.update(n for n in p.stdout.splitlines() if n.strip())
    p = _git(worktree, "status", "--porcelain")
    if p and p.returncode == 0:
        for line in p.stdout.splitlines():
            name = line[3:].strip()
            if "->" in name:  # rename: take the destination
                name = name.split("->")[-1].strip()
            if name:
                names.add(name)
    return names


def _tail(text, n=OUTPUT_TAIL_LINES):
    lines = (text or "").splitlines()
    return "\n".join(lines[-n:])


def _replay(criterion, worktree, evidence_dir, runner):
    """Run the criterion's command ourselves. Returns (ok, observed, output_tail, log)."""
    cmd = criterion.get("cmd") or ""
    ok, why = replayable(cmd)
    if not ok:
        return False, "not replayable", why, None
    argv = shlex.split(cmd)
    if argv[0].startswith("./"):
        target = os.path.realpath(os.path.join(worktree, argv[0]))
        if not target.startswith(os.path.realpath(worktree) + os.sep):
            return False, "script outside worktree", f"{argv[0]} escapes the worktree", None
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                        "CLAUDE_CODE_OAUTH_TOKEN")}
    try:
        p = runner(argv, cwd=worktree, env=env, capture_output=True, text=True,
                   timeout=REPLAY_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False, f"timeout after {REPLAY_TIMEOUT_S}s", "", None
    except (OSError, ValueError) as e:
        return False, f"replay failed to start ({e})", "", None
    # merged streams on purpose (ISSUE-LOG #30: an API error on stdout fooled a
    # stderr-only grep) — the harness keeps both and judges on the exit code.
    merged = (p.stdout or "") + (p.stderr or "")
    log_path = None
    if evidence_dir:
        try:
            os.makedirs(evidence_dir, exist_ok=True)
            log_path = os.path.join(evidence_dir, f"replay-{criterion['id']}.log")
            with open(log_path, "w") as f:
                f.write(f"$ {cmd}\n{merged}\n[exit {p.returncode}]\n")
        except OSError:
            log_path = None
    expect = int(criterion.get("expect_exit", 0) or 0)
    return (p.returncode == expect, f"exit {p.returncode}", _tail(merged), log_path)


# wq#1724: a citation into an ops/wqNNNN-*/install.sh proves the SCRIPT landed on this
# branch, not that it ever reached its target — wq#1632 closed done on exactly this shape
# (install.sh landed, ticket closed, lumen_state.py never saw a write). Extends this same
# citation check rather than adding a parallel gate: an ops-install citation additionally
# needs a `deploy_target` naming a live file whose bytes match one the artifact ships (same
# rule bin/wq's `ops_deploy_check` enforces on `wq done`, applied here to evidence.json).
_OPS_INSTALL_RE = re.compile(r"(?:^|/)ops/wq\d+[\w.-]*/install\.sh$")


def _sha256(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _ops_deploy_evidence(entry, abspath):
    target = str(entry.get("deploy_target") or "").strip()
    art_dir = os.path.dirname(abspath)
    if not target:
        return False, (f"{abspath} is an ops install artifact — landing on this branch proves "
                       f"the script exists, not that it reached a target; add a "
                       f"'deploy_target' path to this criterion naming a live file whose bytes "
                       f"match one shipped in {art_dir}")
    if not os.path.isfile(target):
        return False, f"deploy_target {target} does not exist"
    target_hash = _sha256(target)
    for root, _dirs, files in os.walk(art_dir):
        for name in files:
            if name == "install.sh":
                continue
            cand = os.path.join(root, name)
            if target_hash is not None and _sha256(cand) == target_hash:
                return True, f"{target} byte-matches {cand}"
    return False, f"deploy_target {target} matches no file shipped in {art_dir}"


def _check_citation(entry, worktree, changed):
    cite = str(entry.get("citation") or "").strip()
    m = _CITATION_RE.match(cite)
    if not m:
        return False, f"citation {cite!r} is not file:line", ""
    rel = m.group("path")
    start = int(m.group("start"))
    end = int(m.group("end") or start)
    abspath = os.path.realpath(os.path.join(worktree, rel))
    if not abspath.startswith(os.path.realpath(worktree) + os.sep):
        return False, "citation path escapes the worktree", ""
    if not os.path.isfile(abspath):
        return False, f"{rel} does not exist", ""
    with open(abspath, errors="replace") as f:
        n_lines = sum(1 for _ in f)
    if start < 1 or end < start or end > n_lines:
        return False, f"{rel} has {n_lines} lines; cited {start}-{end}", ""
    norm = os.path.normpath(rel)
    if norm not in {os.path.normpath(c) for c in changed}:
        return False, f"{rel} is not touched by this branch's diff", ""
    if _OPS_INSTALL_RE.search(norm):
        ok, why = _ops_deploy_evidence(entry, abspath)
        if not ok:
            return False, why, ""
    return True, f"{rel}:{start}-{end} in diff", ""


def verify(acceptance, evidence, worktree, base_ref="origin/main", evidence_dir=None,
           runner=subprocess.run):
    """Harness-side verdict on a worker's done claim. Never trusts a logged exit code:
    every `test` criterion is re-run here, every citation re-checked against the diff.

    Returns {"ok", "failed": [...], "checked": [...]}.
    """
    result = {"ok": False, "failed": [], "checked": []}
    criteria = (acceptance or {}).get("criteria") or []
    if not criteria:
        result["ok"] = True  # nothing machine-checkable was derived; gate is vacuous
        return result
    by_id = {str(e.get("id")): e for e in ((evidence or {}).get("criteria") or [])
             if isinstance(e, dict) and e.get("id")}
    changed = changed_files(worktree, base_ref)
    for c in criteria:
        cid = str(c.get("id"))
        entry = by_id.get(cid)
        if entry is None:
            result["failed"].append({
                "id": cid, "text": c.get("text", ""),
                "expected": "an entry in evidence.json",
                "observed": "missing",
                "repro": c.get("cmd") or "(cite file:line touched by the diff)",
                "output_tail": ""})
            continue
        if c.get("proof_type") == "test":
            ok, observed, tail, log_path = _replay(c, worktree, evidence_dir, runner)
            record = {"id": cid, "text": c.get("text", ""),
                      "expected": f"exit {int(c.get('expect_exit', 0) or 0)}",
                      "observed": observed, "repro": c.get("cmd"),
                      "claimed": entry.get("exit_code"), "replay_log": log_path,
                      "output_tail": tail}
        else:
            ok, observed, tail = _check_citation(entry, worktree, changed)
            record = {"id": cid, "text": c.get("text", ""),
                      "expected": "file:line inside this branch's diff",
                      "observed": observed,
                      "repro": "git diff --name-only $(git merge-base HEAD origin/main)..HEAD",
                      "claimed": entry.get("citation"), "output_tail": tail}
        record["ok"] = ok
        result["checked"].append(record)
        if not ok:
            result["failed"].append(record)
    result["ok"] = not result["failed"]
    return result


# ---------------------------------------------------------------------------
# 1d — structured rework
# ---------------------------------------------------------------------------

def rework_doc(ticket_id, attempt, result, prior_evidence_dir=None, reason=None):
    return {
        "ticket": f"wq#{ticket_id}",
        "attempt": attempt,
        "reason": reason or "machine replay of evidence.json did not reproduce the claim",
        "failed": [{k: f.get(k) for k in
                    ("id", "text", "expected", "observed", "claimed", "repro",
                     "output_tail", "replay_log") if f.get(k) is not None}
                   for f in (result or {}).get("failed", [])],
        "prior_evidence": prior_evidence_dir,
    }


def write_rework(run_dir, doc):
    path = os.path.join(run_dir, REWORK_FILENAME)
    write_json(path, doc)
    return path


def rework_block(rework):
    """The block the next dispatch renders at the TOP of the prompt. The three hard
    rules are what makes rework converge: a worker that must re-observe the failure
    first cannot open by re-asserting the claim that was machine-rejected."""
    if not rework or not rework.get("failed"):
        return ""
    lines = [
        "REWORK — YOUR PREVIOUS ATTEMPT'S EVIDENCE FAILED HARNESS REPLAY.",
        "",
        f"Attempt {rework.get('attempt', '?')} of this ticket claimed done. The harness "
        "re-ran the claim itself and observed otherwise. Three hard rules, before "
        "anything else:",
        "1. Your FIRST actions are the `repro` commands below. Run each one and confirm "
        "you observe the same failure. You may not proceed until you have.",
        "2. The previous claims were rejected by machine replay, not by opinion. Do not "
        "re-assert them; do not argue with them.",
        "3. The same pinned acceptance.json still governs. It is not editable, and its "
        "hash is checked when you claim done.",
        "",
    ]
    for f in rework["failed"]:
        lines.append(f"- [{f.get('id')}] {f.get('text', '')}")
        lines.append(f"  expected: {f.get('expected')}   harness observed: {f.get('observed')}")
        if f.get("claimed") is not None:
            lines.append(f"  you claimed: {f.get('claimed')}")
        if f.get("repro"):
            lines.append(f"  repro: {f['repro']}")
        tail = (f.get("output_tail") or "").strip()
        if tail:
            lines.append("  harness replay output (tail):")
            lines.extend("    " + t for t in tail.splitlines()[-OUTPUT_TAIL_LINES:])
    if rework.get("prior_evidence"):
        lines.append(f"\nPrior evidence: {rework['prior_evidence']}")
    return "\n".join(lines) + "\n"


def contract_block(acceptance, worktree):
    """The done-contract paragraph appended to the worker prompt (1a). Prose claims are
    ignored; the only path to a surviving `done` is evidence the harness can replay."""
    criteria = (acceptance or {}).get("criteria") or []
    if not criteria:
        return ""
    lines = [
        "DONE IS A REPLAY, NOT A CLAIM (evidence gate, ISSUE-LOG #17):",
        f"- {ACCEPTANCE_FILENAME} in your worktree is PINNED by hash at dispatch. You may "
        "not edit it; an edited copy fails the hash check and routes the ticket to rework.",
        f"- Before you stop, write {os.path.join(worktree, EVIDENCE_FILENAME)} with one "
        "entry per criterion:",
        '  {"criteria": [{"id": "AC1", "cmd_run": "<the cmd>", "exit_code": 0},',
        '                {"id": "AC2", "citation": "path/to/file.py:88-104"}]}',
        "- The harness RE-RUNS every `test` criterion itself and re-checks every citation "
        "against this branch's diff. Your logged exit code is a hint, never proof, and "
        "prose claims in your final message are ignored entirely.",
        "- A Stop hook refuses to let the session end while evidence is missing. Self-check "
        f"any time with: python3 {os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fworker.py')} "
        "verify-evidence --ticket <id> --worktree <worktree>",
        "- Criteria for this ticket:",
    ]
    for c in criteria:
        if c.get("proof_type") == "test":
            lines.append(f"  [{c['id']}] {c['text']} — run `{c['cmd']}`, "
                         f"expect exit {c.get('expect_exit', 0)}")
        else:
            lines.append(f"  [{c['id']}] {c['text']} — cite file:line")
    return "\n".join(lines) + "\n"
