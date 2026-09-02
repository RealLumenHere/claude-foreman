"""Evidence-gated done — ISSUE-SOLUTIONS-fable.md §1 (FALSE_COMPLETE, issue #17).

"done" stops being a speech act by the model and becomes a state transition the harness
performs only after it has itself observed the evidence:

  §1a  acceptance.json is derived from the ticket at DISPATCH and pinned by fworker
       (run-dir copy is the truth; the worktree copy is the worker's read-only view).
  §1b  the worker's only path to done is evidence.json; verify_done() re-runs every
       `test` criterion itself and checks every `citation` against the diff.
  §1c  hooks/stop_gate.py refuses to let the session end with the gap unfilled.
  §1d  a rejection emits a structured rework.json that the next attempt's prompt opens
       with, so rework re-observes the failure instead of re-claiming success.

Everything here is deterministic and injectable: derivation takes a `model_call`, replay
takes a `runner`, so the tests never spawn a model or shell out to a real suite.
"""
import hashlib
import json
import os
import re
import subprocess

ACCEPTANCE_FILE = "acceptance.json"
EVIDENCE_FILE = "evidence.json"
REWORK_FILE = "rework.json"
BLOCKS_FILE = "stop-gate-blocks.json"
EVIDENCE_DIR = "evidence"

TICKET_ENV = "FOREMAN_TICKET"
RUN_DIR_ENV = "FOREMAN_RUN_DIR"

MAX_CRITERIA = 6
GATE_ENV = "FOREMAN_EVIDENCE_GATE"
REPLAY_TIMEOUT_S = int(os.environ.get("FOREMAN_REPLAY_TIMEOUT_S", "900"))
DERIVE_MODEL = os.environ.get("FOREMAN_ACCEPTANCE_MODEL", "claude-haiku-4-5-20251001")

# A derived `cmd` is executed by the harness at done time. The derivation model only ever
# sees the ticket body, but a criterion is still model output running as a command: drop
# anything with a destructive or privilege-escalating shape rather than replay it.
UNSAFE_CMD = re.compile(
    r"(^|[\s;|&])(sudo|rm\s+-rf\s+/|mkfs|dd\s+if=|reboot|shutdown|systemctl\s+(stop|disable|mask)"
    r"|shred|mv\s+/\s|chown\s+-R\s+/|curl[^|]*\|\s*(ba)?sh)\b", re.I)

CITATION_RE = re.compile(r"^(?P<path>[^:\s]+):(?P<start>\d+)(?:-(?P<end>\d+))?$")

DERIVE_PROMPT = """Turn this ticket into at most {n} machine-checkable acceptance criteria.

Rules:
- Every criterion that CAN be a command MUST be a command: {{"proof_type": "test",
  "cmd": "<shell command runnable from the repo worktree>", "expect_exit": 0}}.
- A criterion that cannot be executed becomes {{"proof_type": "citation"}} — it will be
  satisfied by a file:line reference the harness checks against the diff.
- Prefer the exact test command the ticket names. Never invent a test file that the
  ticket does not name; if no command is nameable, use a citation criterion.
- Output JSON ONLY, no prose: {{"criteria": [{{"id": "AC1", "text": "...",
  "proof_type": "test", "cmd": "...", "expect_exit": 0}}]}}

TICKET wq#{ticket_id}: {title}

{detail}
"""


# ---------------------------------------------------------------------------
# §1a — derivation + pinning
# ---------------------------------------------------------------------------

def gate_enabled():
    """FOREMAN_EVIDENCE_GATE=off disables §1 for a dispatch: nothing is pinned, so
    evidence_gate() reports "ungated" and the Stop hook fails open. The pre-§1 harness
    fixtures (canned workers that close a ticket without evidence) run under this, and it
    is the rollback switch if a lane needs the old behaviour back in a hurry."""
    return os.environ.get(GATE_ENV, "on").strip().lower() not in ("0", "off", "false", "no")


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(path):
    try:
        with open(path, "rb") as f:
            return sha256_bytes(f.read())
    except OSError:
        return None


def _clean_criterion(raw, index):
    """Normalize one model/heuristic criterion; None if it cannot be made checkable."""
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text") or "").strip()
    if not text:
        return None
    cid = str(raw.get("id") or f"AC{index}").strip() or f"AC{index}"
    ptype = str(raw.get("proof_type") or "").strip().lower()
    cmd = str(raw.get("cmd") or "").strip()
    if ptype == "test" and cmd and not UNSAFE_CMD.search(cmd):
        try:
            expect = int(raw.get("expect_exit", 0))
        except (TypeError, ValueError):
            expect = 0
        return {"id": cid, "text": text, "proof_type": "test", "cmd": cmd,
                "expect_exit": expect}
    return {"id": cid, "text": text, "proof_type": "citation",
            "pattern": "file:line required"}


def _heuristic_criteria(ticket):
    """Deterministic fallback: any command the ticket body itself names becomes a `test`
    criterion; the work always carries at least one citation criterion. Derivation must
    never be able to block a dispatch, so every failure path lands here."""
    body = f"{ticket.get('title', '')}\n{ticket.get('detail', '')}"
    cmds = []
    for m in re.finditer(r"`([^`\n]+)`", body):
        c = m.group(1).strip()
        if re.match(r"^(pytest|python3? -m pytest|npm (run |)test|npx |bin/rt-|make |"
                    r"bash |sh |\./)", c):
            if not UNSAFE_CMD.search(c) and c not in cmds:
                cmds.append(c)
    out = []
    for i, c in enumerate(cmds[:MAX_CRITERIA - 1], start=1):
        out.append({"id": f"AC{i}", "text": f"`{c}` passes", "proof_type": "test",
                    "cmd": c, "expect_exit": 0})
    out.append({"id": f"AC{len(out) + 1}",
                "text": "the change is cited in the diff (file:line the ticket's work landed in)",
                "proof_type": "citation", "pattern": "file:line required"})
    return out


def _claude_criteria(ticket, claude_bin="claude", timeout=180):
    """One cheap model call, JSON only. Any failure returns None -> heuristic fallback.
    FOREMAN_ACCEPTANCE_DERIVE=heuristic skips the call (what the tests run under)."""
    if os.environ.get("FOREMAN_ACCEPTANCE_DERIVE", "model").lower() != "model":
        return None
    prompt = DERIVE_PROMPT.format(n=MAX_CRITERIA, ticket_id=ticket.get("id", "?"),
                                  title=ticket.get("title", ""),
                                  detail=ticket.get("detail", ""))
    try:
        p = subprocess.run([claude_bin, "-p", prompt, "--model", DERIVE_MODEL],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    return p.stdout


def parse_criteria_payload(payload):
    """Pull the criteria list out of a model reply that may be fenced or chatty."""
    if payload is None:
        return None
    if isinstance(payload, (list, dict)):
        data = payload
    else:
        m = re.search(r"\{.*\}", str(payload), re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except ValueError:
            return None
    raw = data.get("criteria") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return None
    out = []
    for i, item in enumerate(raw[:MAX_CRITERIA], start=1):
        c = _clean_criterion(item, i)
        if c:
            out.append(c)
    return out or None


def derive_criteria(ticket, model_call=None, claude_bin="claude"):
    """§1a: ticket -> criteria. `model_call(ticket)` returns the raw model reply (or
    None); the deterministic heuristic covers every failure so dispatch never blocks."""
    call = model_call if model_call is not None else (
        lambda t: _claude_criteria(t, claude_bin=claude_bin))
    try:
        criteria = parse_criteria_payload(call(ticket))
    except Exception:
        criteria = None
    return criteria or _heuristic_criteria(ticket)


def pin_acceptance(ticket, run_dir_path, worktree, model_call=None, claude_bin="claude",
                    log=None):
    """Write the pinned acceptance.json into the fworker-owned run dir (the truth) and a
    read-only-by-policy copy into the worktree (the worker's view). Returns the pinned
    document, or None if it could not be written (dispatch continues ungated)."""
    if not gate_enabled():
        return None
    try:
        criteria = derive_criteria(ticket, model_call=model_call, claude_bin=claude_bin)
        doc = {"ticket": f"wq#{ticket.get('id', '?')}",
               "title": ticket.get("title", ""),
               "criteria": criteria}
        body = json.dumps(doc, indent=2, sort_keys=True)
        doc["sha256"] = sha256_bytes(body.encode())
        os.makedirs(run_dir_path, exist_ok=True)
        pinned = os.path.join(run_dir_path, ACCEPTANCE_FILE)
        with open(pinned, "w") as f:
            f.write(body)
        if worktree:
            with open(os.path.join(worktree, ACCEPTANCE_FILE), "w") as f:
                f.write(body)
        return doc
    except (OSError, TypeError, ValueError) as e:
        if log:
            log(f"acceptance: pin failed ({e}) — attempt runs ungated")
        return None


def load_pinned(run_dir_path):
    """The pinned acceptance document, with its hash recomputed from the bytes on disk.
    None when the gate is switched off, so every reader (fworker's evidence_gate, the
    Stop hook) goes ungated from this one check even if an older pin is still on disk."""
    if not gate_enabled():
        return None
    path = os.path.join(run_dir_path, ACCEPTANCE_FILE)
    try:
        with open(path, "rb") as raw:
            body = raw.read()
        doc = json.loads(body)
    except (OSError, ValueError):
        return None
    doc["sha256"] = sha256_bytes(body)
    return doc


def load_evidence(worktree, run_dir_path=None):
    """The worker writes evidence.json in its worktree; a run-dir copy is accepted too."""
    for base in (worktree, run_dir_path):
        if not base:
            continue
        try:
            with open(os.path.join(base, EVIDENCE_FILE)) as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return None


def worktree_copy_matches(run_dir_path, worktree):
    """§1a: the worker cannot edit acceptance.json — its worktree copy must still hash to
    the pinned bytes. A missing worktree copy is not tampering (nothing to check)."""
    wt = os.path.join(worktree, ACCEPTANCE_FILE)
    if not os.path.exists(wt):
        return True
    return sha256_file(wt) == sha256_file(os.path.join(run_dir_path, ACCEPTANCE_FILE))


# ---------------------------------------------------------------------------
# §1b — replay
# ---------------------------------------------------------------------------

def evidence_gaps(acceptance, evidence):
    """Cheap STRUCTURAL check (no replay) — what the Stop hook reports back to the model.
    Returns a list of human-readable gaps; empty means "complete enough to submit"."""
    criteria = (acceptance or {}).get("criteria") or []
    if not criteria:
        return []
    if not isinstance(evidence, dict):
        return [f"evidence.json is missing — it must carry one entry per criterion "
                f"({', '.join(c['id'] for c in criteria)})"]
    by_id = {}
    for item in evidence.get("criteria") or []:
        if isinstance(item, dict) and item.get("id"):
            by_id[str(item["id"])] = item
    gaps = []
    for c in criteria:
        got = by_id.get(c["id"])
        if got is None:
            gaps.append(f"{c['id']} ({c['text']}): no entry in evidence.json")
        elif c.get("proof_type") == "test":
            if not str(got.get("cmd_run") or "").strip():
                gaps.append(f"{c['id']}: needs cmd_run (the command you ran: {c.get('cmd')})")
            elif got.get("exit_code") is None:
                gaps.append(f"{c['id']}: needs the exit_code you observed")
        elif not CITATION_RE.match(str(got.get("citation") or "")):
            gaps.append(f"{c['id']}: needs citation \"path:line\" or \"path:start-end\"")
    return gaps


def _run(cmd, cwd, timeout):
    p = subprocess.run(cmd, cwd=cwd, shell=True, timeout=timeout,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p.returncode, p.stdout


def changed_files(worktree, base_ref="origin/main"):
    """Union of committed-vs-base, staged, unstaged and untracked paths. A worker that
    has not pushed yet still has a real diff; a citation must land inside it."""
    out = set()
    cmds = [["diff", "--name-only", f"{base_ref}...HEAD"],
            ["diff", "--name-only", "HEAD"],
            ["diff", "--name-only", "--cached"],
            ["ls-files", "--others", "--exclude-standard"]]
    for args in cmds:
        try:
            p = subprocess.run(["git", "-C", worktree] + args,
                               capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if p.returncode == 0:
            out.update(line.strip() for line in p.stdout.splitlines() if line.strip())
    return out


def _check_citation(criterion, got, worktree, touched):
    cite = str(got.get("citation") or "")
    m = CITATION_RE.match(cite)
    if not m:
        return {"expected": "citation \"path:line\"", "observed": f"{cite!r}",
                "repro": "cite the file:line your change landed in"}
    path, start = m.group("path"), int(m.group("start"))
    end = int(m.group("end") or start)
    abspath = path if os.path.isabs(path) else os.path.join(worktree, path)
    rel = os.path.relpath(abspath, worktree)
    if not os.path.isfile(abspath):
        return {"expected": f"{path} exists in the worktree", "observed": "no such file",
                "repro": f"ls {path}"}
    with open(abspath, errors="ignore") as f:
        nlines = sum(1 for _ in f)
    if start < 1 or end < start or end > nlines:
        return {"expected": f"lines {start}-{end} inside {path} ({nlines} lines)",
                "observed": "line range outside the file",
                "repro": f"sed -n '{start},{end}p' {path}"}
    if rel not in touched:
        return {"expected": f"{rel} appears in the diff",
                "observed": "cited file was not touched by this branch",
                "repro": "git diff --name-only origin/main...HEAD"}
    return None


def verify_done(acceptance, evidence, worktree, run_dir_path, base_ref="origin/main",
                runner=None, timeout=REPLAY_TIMEOUT_S):
    """§1b: harness-side, deterministic. Every criterion must appear in evidence.json;
    every `test` criterion is RE-RUN here (the model's logged exit code is a hint, never
    proof — and the harness captures both streams into one file, closing issue #30's
    stdout/stderr trap); every `citation` must resolve inside the diff.

    Returns {"ok": bool, "failed": [ ... ]} where each failure carries expected/observed/
    repro/output_tail — exactly the shape §1d feeds back into the rework prompt.
    """
    run = runner or _run
    criteria = (acceptance or {}).get("criteria") or []
    if not criteria:
        return {"ok": True, "failed": [], "checked": 0}

    by_id = {}
    if isinstance(evidence, dict):
        for item in evidence.get("criteria") or []:
            if isinstance(item, dict) and item.get("id"):
                by_id[str(item["id"])] = item

    logdir = os.path.join(run_dir_path, EVIDENCE_DIR)
    try:
        os.makedirs(logdir, exist_ok=True)
    except OSError:
        logdir = None

    touched = None
    failed = []
    for c in criteria:
        cid = c["id"]
        got = by_id.get(cid)
        if got is None:
            failed.append({"id": cid, "expected": f"an evidence entry for {cid}",
                           "observed": "missing from evidence.json",
                           "repro": c.get("cmd") or c.get("text", ""),
                           "output_tail": ""})
            continue
        if c.get("proof_type") == "test":
            cmd = c.get("cmd") or ""
            expect = int(c.get("expect_exit", 0))
            try:
                rc, out = run(cmd, worktree, timeout)
            except subprocess.TimeoutExpired:
                rc, out = -1, f"harness replay timed out after {timeout}s"
            except OSError as e:
                rc, out = -1, f"harness could not run the command: {e}"
            if logdir:
                try:
                    with open(os.path.join(logdir, f"{cid}.replay.log"), "w") as f:
                        f.write(out or "")
                except OSError:
                    pass
            if rc != expect:
                failed.append({
                    "id": cid, "expected": f"exit {expect}", "observed": f"exit {rc}",
                    "repro": cmd,
                    "output_tail": "\n".join((out or "").splitlines()[-30:]),
                    "claimed_exit": got.get("exit_code"),
                })
        else:
            if touched is None:
                touched = changed_files(worktree, base_ref)
            problem = _check_citation(c, got, worktree, touched)
            if problem:
                problem.update({"id": cid, "output_tail": ""})
                failed.append(problem)
    return {"ok": not failed, "failed": failed, "checked": len(criteria)}


# ---------------------------------------------------------------------------
# §1d — structured rework
# ---------------------------------------------------------------------------

def write_rework(run_dir_path, attempt, failed, prior_evidence=None):
    doc = {"attempt": attempt, "failed": failed,
           "prior_evidence": prior_evidence or os.path.join(run_dir_path, EVIDENCE_DIR)}
    path = os.path.join(run_dir_path, REWORK_FILE)
    try:
        os.makedirs(run_dir_path, exist_ok=True)
        with open(path, "w") as f:
            json.dump(doc, f, indent=2)
    except OSError:
        return None
    return path


def rework_block(run_dir_path):
    """§1d: rendered at the TOP of the rework prompt. Convergence comes from the
    repro-first rule — a worker that must re-observe the failure cannot open by
    re-claiming success."""
    try:
        with open(os.path.join(run_dir_path, REWORK_FILE)) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return ""
    lines = [
        "!! PRIOR ATTEMPT REJECTED BY MACHINE REPLAY — READ THIS FIRST !!",
        "",
        f"Attempt {doc.get('attempt', '?')} claimed done. The harness re-ran the "
        "acceptance criteria itself and observed these failures:",
        "",
    ]
    for f_ in doc.get("failed", []):
        lines.append(f"- {f_.get('id')}: expected {f_.get('expected')}, "
                     f"observed {f_.get('observed')}")
        if f_.get("repro"):
            lines.append(f"  repro: {f_['repro']}")
        tail = (f_.get("output_tail") or "").strip()
        if tail:
            lines.append("  output tail:")
            lines += [f"    {ln}" for ln in tail.splitlines()[-12:]]
    lines += [
        "",
        "THREE HARD RULES FOR THIS ATTEMPT:",
        "1. Your FIRST action is to run each `repro` command above and confirm you "
        "observe the same failure. You may not proceed until you have.",
        "2. The previous attempt's claims were rejected by machine replay, not by "
        "opinion — do not re-assert them.",
        f"3. The same pinned acceptance.json still governs "
        f"({os.path.join(run_dir_path, ACCEPTANCE_FILE)}); it is read-only.",
        "",
        f"prior evidence: {doc.get('prior_evidence', '')}",
    ]
    return "\n".join(lines)


def contract_block(acceptance, worktree):
    """§1a: the done-contract paragraph appended to the worker's dispatch prompt."""
    criteria = (acceptance or {}).get("criteria") or []
    if not criteria:
        return ""
    lines = [
        "EVIDENCE-GATED DONE (ISSUE-SOLUTIONS-fable.md §1) — this ticket's acceptance "
        "criteria are pinned and machine-checked:",
        "",
    ]
    for c in criteria:
        if c.get("proof_type") == "test":
            lines.append(f"- {c['id']} [{c['text']}]: run `{c['cmd']}`, "
                         f"must exit {c.get('expect_exit', 0)}")
        else:
            lines.append(f"- {c['id']} [{c['text']}]: cite file:line (or file:start-end) "
                         f"in a file this branch touched")
    lines += [
        "",
        f"Before you stop, write {os.path.join(worktree, EVIDENCE_FILE)}:",
        '  {"criteria": [{"id": "AC1", "cmd_run": "<cmd>", "exit_code": 0},',
        '                {"id": "AC2", "citation": "path/file.py:88-104"}]}',
        "",
        "The harness RE-RUNS every command itself and checks every citation against the "
        "diff. Prose claims of success are ignored; a mismatch routes the ticket to "
        f"rework, never done. {ACCEPTANCE_FILE} is read-only — do not edit it, and keep "
        f"neither it nor {EVIDENCE_FILE} out of your commits (they are harness files, "
        f"not part of the change).",
    ]
    return "\n".join(lines)
