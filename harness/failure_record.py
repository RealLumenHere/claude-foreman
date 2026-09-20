#!/usr/bin/env python3
"""failure_record.py — wq#1330, WORKER_EXEC_CAPABILITY_GAP.md §4 slice 1.

The machine-readable terminal-failure record (`_runs/wq<id>/failure.json`, schema v1)
plus the mechanical triage that decides which of six buckets a dead run belongs to:

    CAPABILITY_DENIED  the sandbox refused something that maps to a §2.3 capability tag
    HARNESS_DEFECT     our own configuration broke (worktree/repo mismatch, hook crash)
    GENUINE_BUG        the work itself failed (traceback, hang inside a test/build)
    REAL_BLOCK         the worker took the good path and called `wq block` itself
    LIMIT              turn/budget ceiling
    UNKNOWN            nothing matched — still recorded, needs a human read

Everything here is mechanical: denied calls are read out of the transcript's
tool_result frames (the same is_permission_denial() signal LoopTracker uses),
`inferred_needs` node names come from `lumen_state.py current` facts and are OMITTED
when unresolvable — never guessed (doc §4.1). Consumers: fworker.handle_failure(),
fworker fire-mode `_record`, dispatcher/self_heal.classify_block() (reads the JSON's
block_class through the `see <path>` pointer in the block reason), and the offline
replay CLI at the bottom (doc §5.2 "triage" gate).

No new capability in this slice — this module only observes and writes JSON.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from action_gate import LoopTracker, is_permission_denial  # noqa: E402
import masking  # noqa: E402  (wq#2275: transcript masking at read time)

# v2 (wq#1436, BROKER_WIRING.md §2.5): adds `refused` and fills unreachable_paths /
# inferred_needs on a spawn refusal, which v1 always wrote empty. Additive — every v1
# key is still present with the same meaning.
SCHEMA_VERSION = 2
FAILURE_FILENAME = "failure.json"

TRIAGE_CAPABILITY_DENIED = "CAPABILITY_DENIED"
TRIAGE_HARNESS_DEFECT = "HARNESS_DEFECT"
TRIAGE_GENUINE_BUG = "GENUINE_BUG"
TRIAGE_REAL_BLOCK = "REAL_BLOCK"
TRIAGE_LIMIT = "LIMIT"
TRIAGE_UNKNOWN = "UNKNOWN"
TRIAGE_VALUES = (TRIAGE_CAPABILITY_DENIED, TRIAGE_HARNESS_DEFECT, TRIAGE_GENUINE_BUG,
                 TRIAGE_REAL_BLOCK, TRIAGE_LIMIT, TRIAGE_UNKNOWN)

LUMEN_STATE = os.environ.get("FOREMAN_LUMEN_STATE",
                             "/home/lumen/minerva/lumen_state.py")

# doc §4.1 argv0 -> tag map. sudo/systemctl/bench handled separately below.
_SSH_ARGV0 = frozenset({"ssh", "scp", "rsync", "tailscale", "ping"})
_BENCH_ARGV0 = frozenset({"adb", "fastboot"})
_RESTART_VERBS = frozenset({"restart", "reload", "start"})
_CAPABILITY_ARGV0 = _SSH_ARGV0 | _BENCH_ARGV0 | {"sudo", "systemctl"}

# subject.predicate = value  (since YYYY-MM-DD) — same shape prompt-forge parses.
_STATE_LINE = re.compile(
    r"^\s*(?P<subject>[\w-]+)\.(?P<predicate>[\w-]+)\s*= (?P<value>.*?)\s*"
    r"\(since \d{4}-\d{2}-\d{2}\)\s*$")
_IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_COMPOUND_DENIAL_RE = re.compile(r"following parts? requires? approval:\s*(.+)",
                                 re.I | re.S)
_PATH_REFUSAL_RE = re.compile(r"blocked\.?\s*for security", re.I)
_TRACEBACK_RE = re.compile(
    r"Traceback \(most recent call last\)|^\s*at .+\(.+:\d+:\d+\)", re.M)
_TEST_BUILD_RE = re.compile(
    r"\b(pytest|npm (run )?(test|build)|yarn|pnpm|make|cargo (test|build|run)"
    r"|go (test|build)|tox|gradle|mvn|unittest)\b")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# node resolution — lumen_state.py current, never a guess (doc §4.1)
# ---------------------------------------------------------------------------

def load_node_map(state_output=None):
    """{ip-or-name -> node} for every subject that declares a tailscale_ip/lan_ip.
    Empty map on any failure — an unresolvable node means the tag is OMITTED."""
    if state_output is None:
        try:
            p = subprocess.run(["python3", LUMEN_STATE, "current"],
                              capture_output=True, text=True, timeout=20)
            state_output = p.stdout if p.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            state_output = ""
    out = {}
    for line in (state_output or "").splitlines():
        m = _STATE_LINE.match(line)
        if not m or m.group("predicate") not in ("tailscale_ip", "lan_ip"):
            continue
        node, ip = m.group("subject"), m.group("value")
        out[ip] = node
        out[node] = node
    return out


# ---------------------------------------------------------------------------
# denied-call extraction from stream-json transcript frames
# ---------------------------------------------------------------------------

def _result_text(block):
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return ""


def _argv(command):
    """Best-effort argv from a Bash command string: env-assignment prefixes and
    common wrappers stripped, so argv[0] is the verb the allowlist judged."""
    try:
        toks = shlex.split(command or "")
    except ValueError:
        toks = (command or "").split()
    out = []
    for t in toks:
        if not out and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
            continue
        out.append(t)
    while out and os.path.basename(out[0]) in ("env", "command", "nice"):
        out.pop(0)
    if out and os.path.basename(out[0]) == "timeout":
        out.pop(0)
        if out and re.match(r"^\d", out[0]):
            out.pop(0)
    return out


def _denied_segment(refusal_text, command):
    """The compound-command variant names the refused segment — prefer it over the
    whole pipeline so argv0 is the segment the pack actually refused."""
    m = _COMPOUND_DENIAL_RE.search(refusal_text or "")
    if m:
        return m.group(1).strip().splitlines()[0].strip()
    return command or ""


def _target_node(argv, node_map):
    """First token that resolves through the lumen_state map ('user@host' split);
    None when nothing resolves — the caller omits the tag rather than guessing."""
    for t in argv[1:]:
        if t.startswith("-"):
            continue
        cand = t.rsplit("@", 1)[-1]
        if cand in node_map:
            return node_map[cand]
    return None


def _raw_target(argv):
    for t in argv[1:]:
        if t.startswith("-"):
            continue
        if _IP_RE.match(t.rsplit("@", 1)[-1]) or "." in t or t.isalpha():
            return t
    return None


def _abs_paths(tool, tool_input):
    if tool != "Bash":
        p = tool_input.get("file_path") or tool_input.get("path")
        return [p] if p else []
    return [t for t in _argv(tool_input.get("command") or "")
            if t.startswith("/") and len(t) > 1]


def extract_denied_calls(events, node_map=None, reachable_roots=None):
    """(denied_calls, unreachable_paths, last_error_sig) from transcript frames.
    denied_calls aggregates by (tool, argv0, refusal, target) with a count, the
    shape doc §4.1 specifies."""
    node_map = node_map or {}
    pending = {}
    denied = {}
    unreachable = []
    last_error_sig = None
    for frame in events or []:
        msg = frame.get("message")
        content = (msg.get("content") if isinstance(msg, dict) else None) or []
        if frame.get("type") == "assistant":
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    pending[b.get("id")] = (b.get("name") or "", b.get("input") or {})
        elif frame.get("type") == "user":
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                    continue
                tool, ti = pending.pop(b.get("tool_use_id"), ("", {}))
                if not b.get("is_error"):
                    continue
                text = _result_text(b)
                sig = LoopTracker._error_sig(text)
                if sig:
                    last_error_sig = sig
                if not is_permission_denial(text):
                    continue
                path_refusal = bool(_PATH_REFUSAL_RE.search(text))
                refusal = "blocked_path" if path_refusal else "requires approval"
                argv = _argv(_denied_segment(text, ti.get("command") or ""))
                argv0 = os.path.basename(argv[0]) if argv else (tool or "?")
                target = None
                node = None
                if tool == "Bash" and argv:
                    node = _target_node(argv, node_map)
                    target = node or _raw_target(argv)
                if path_refusal:
                    for p in _abs_paths(tool, ti):
                        if reachable_roots and any(
                                os.path.abspath(p).startswith(os.path.abspath(r) + os.sep)
                                or os.path.abspath(p) == os.path.abspath(r)
                                for r in reachable_roots):
                            continue
                        if p not in unreachable:
                            unreachable.append(p)
                    if not target:
                        target = (_abs_paths(tool, ti) or [None])[0]
                key = (tool, argv0, refusal, target)
                if key in denied:
                    denied[key]["count"] += 1
                else:
                    denied[key] = {"tool": tool, "argv0": argv0, "target": target,
                                   "node": node, "refusal": refusal, "count": 1,
                                   "argv": argv[:6]}
    return list(denied.values()), unreachable, last_error_sig


# ---------------------------------------------------------------------------
# inferred needs (doc §2.3 tag vocabulary, §4.1 map)
# ---------------------------------------------------------------------------

def infer_needs(denied_calls, unreachable_paths):
    needs = []

    def add(tag):
        if tag not in needs:
            needs.append(tag)

    for d in denied_calls:
        a, argv, node = d.get("argv0"), d.get("argv") or [], d.get("node")
        if a in _SSH_ARGV0:
            if node:
                add(f"ssh({node})")
        elif a == "sudo":
            add(f"root({node or 'lumen'})")
        elif a == "systemctl":
            rest = [t for t in argv[1:] if not t.startswith("-")]
            if rest and rest[0] in _RESTART_VERBS and len(rest) > 1:
                add(f"restart({rest[1]}@lumen)")
        elif a in _BENCH_ARGV0:
            add(f"bench(thor,{a})")
    for p in unreachable_paths:
        add(f"dir({p})")
    return needs


# ---------------------------------------------------------------------------
# worktree origin check (doc §4.1 `worktree` field; the assertion is slice 2)
# ---------------------------------------------------------------------------

def _mask_url(url):
    # instinct 3: a remote URL can carry a credential — mask before it is stored.
    return re.sub(r"://[^/@\s]+@", "://***@", url or "")


def _origin_url(path):
    try:
        p = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"],
                          capture_output=True, text=True, timeout=15)
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _norm_url(url):
    if not url:
        return None
    u = url.strip().rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    return u.lower()


def worktree_origin_check(worktree, expected_repo):
    origin = _origin_url(worktree) if worktree else None
    expected = _origin_url(expected_repo) if expected_repo else None
    match = None
    if origin is not None and expected is not None:
        match = _norm_url(origin) == _norm_url(expected)
    return {"path": worktree, "origin": _mask_url(origin),
            "expected_repo": expected_repo, "match": match}


# ---------------------------------------------------------------------------
# triage (doc §4.1, applied in the doc's order)
# ---------------------------------------------------------------------------

def _last_tool_frames(events):
    """(last tool_use {tool,input}, last is_error tool_result text)."""
    last_use, last_err = None, None
    pending = {}
    for frame in events or []:
        msg = frame.get("message")
        content = (msg.get("content") if isinstance(msg, dict) else None) or []
        if frame.get("type") == "assistant":
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    last_use = {"tool": b.get("name") or "", "input": b.get("input") or {}}
                    pending[b.get("id")] = last_use
        elif frame.get("type") == "user":
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                    last_err = _result_text(b)
    return last_use, last_err


def triage(classification, trip_kind, denied_calls, unreachable_paths,
           worktree_check, events, worker_block_reason=None):
    """(triage, one_line) — mechanical, doc §4.1 order. one_line is the human head
    of the wq block reason; the caller appends '; see <failure.json path>'."""
    capability_hits = [d for d in denied_calls
                       if d.get("argv0") in _CAPABILITY_ARGV0
                       or d.get("refusal") == "blocked_path"]
    needs = infer_needs(denied_calls, unreachable_paths)
    if trip_kind == "PERMISSION" or capability_hits or unreachable_paths:
        n = sum(d["count"] for d in denied_calls) or 1
        if needs:
            head = f"{', '.join(needs)} refused {n}x"
        elif denied_calls:
            head = f"permission-refused {n}x ({denied_calls[0]['argv0']})"
        else:
            head = f"{classification}: permission loop, no mappable denial"
        return TRIAGE_CAPABILITY_DENIED, head

    if worktree_check and worktree_check.get("match") is False:
        return (TRIAGE_HARNESS_DEFECT,
                f"WORKTREE_REPO_MISMATCH expected={worktree_check.get('expected_repo')}")

    last_use, last_err = _last_tool_frames(events)
    if classification == "CRASH" and last_err and _TRACEBACK_RE.search(last_err) \
            and not denied_calls:
        return TRIAGE_GENUINE_BUG, "CRASH: traceback in last tool result"
    if classification == "HANG" and last_use and last_use["tool"] == "Bash" \
            and _TEST_BUILD_RE.search(last_use["input"].get("command") or ""):
        return TRIAGE_GENUINE_BUG, "HANG inside a test/build command"

    if worker_block_reason:
        return TRIAGE_REAL_BLOCK, worker_block_reason.strip().splitlines()[0][:200]

    if classification in ("TURNS", "BUDGET"):
        return TRIAGE_LIMIT, f"{classification} ceiling hit"

    return TRIAGE_UNKNOWN, f"{classification}: no mechanical signature matched"


# ---------------------------------------------------------------------------
# record assembly + write
# ---------------------------------------------------------------------------

def spawn_refusal_fields(refusal):
    """BROKER_WIRING.md §2.5: a spawn refusal already knows exactly which paths were
    unreachable and which tags policy refused — carry those through instead of
    re-parsing the summary string, which is what left `unreachable_paths: []` and
    `inferred_needs: []` in every LAUNCH_FAILURE record (_runs/wq1401/failure.json
    and wq1361/1402/1403/1407). CITED_PATH_UNREACHABLE fills unreachable_paths and
    the dir() tags they imply; CAPABILITY_DENIED_BY_POLICY fills refused[] and no
    inferred needs — a policy refusal is not a missing grant, it is a forbidden one."""
    unreachable = list(getattr(refusal, "unreachable_paths", None) or [])
    refused = list(getattr(refusal, "refused", None) or [])
    return {
        "unreachable_paths": unreachable,
        "refused": refused,
        "inferred_needs": infer_needs([], unreachable),
    }


def build_failure_record(ticket, attempts, classification, block_class,
                         events=None, worktree=None, expected_repo=None,
                         session_id=None, trip_kind=None, worker_block_reason=None,
                         attempt_files=None, node_map=None, reachable_roots=None,
                         check_worktree=True, spawn_refusal=None):
    node_map = load_node_map() if node_map is None else node_map
    denied, unreachable, last_error_sig = extract_denied_calls(
        events, node_map=node_map, reachable_roots=reachable_roots)
    wt = (worktree_origin_check(worktree, expected_repo)
          if check_worktree else {"path": worktree, "origin": None,
                                  "expected_repo": expected_repo, "match": None})
    verdict, one_line = triage(classification, trip_kind, denied, unreachable,
                               wt, events, worker_block_reason=worker_block_reason)
    denied_out = [{k: d[k] for k in ("tool", "argv0", "target", "refusal", "count")}
                  for d in denied]
    structured = {"unreachable_paths": unreachable, "refused": [],
                  "inferred_needs": infer_needs(denied, unreachable)}
    if spawn_refusal is not None:
        # a SpawnRefused has no transcript to mine (events=[]), so its own lists are
        # the only source of these fields.
        structured = spawn_refusal_fields(spawn_refusal)
    return {
        "schema": SCHEMA_VERSION,
        "ticket": ticket,
        "attempts": attempts,
        "classification": classification,
        "block_class": block_class,
        "triage": verdict,
        "summary": one_line,
        "denied_calls": denied_out,
        "inferred_needs": structured["inferred_needs"],
        "worktree": wt,
        "unreachable_paths": structured["unreachable_paths"],
        "refused": structured["refused"],
        "last_error_sig": last_error_sig,
        "transcript": (f"~/.claude/projects/*/{session_id}.jsonl"
                       if session_id else None),
        "attempt_files": attempt_files or [],
    }


def write_failure_record(run_dir_path, record):
    path = os.path.join(run_dir_path, FAILURE_FILENAME)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(record, f, indent=2, default=str)
    os.rename(tmp, path)
    return path


def block_reason_line(record, failure_path):
    """doc §4.2: '<triage>: <one-line>; see <failure.json path>' — no transcript glob."""
    return f"{record['triage']}: {record['summary']}; see {failure_path}"


# ---------------------------------------------------------------------------
# offline replay (doc §5.2 triage gate): last N days of attempt-N.json + transcripts
# ---------------------------------------------------------------------------

_HARNESS_PREFIXES = ("INFRA_CRASH:", "WORK_BLOCKED:", "LIMIT_HIT:",
                     "HARNESS_DEFECT:") + tuple(v + ":" for v in TRIAGE_VALUES)


def _load_transcript(session_id):
    # wq#2275 (T6): the CLI's own transcript is written by the child, so it can't be
    # masked at write time — mask at this read instead, so failure.json and every
    # replay artefact derived here stays credential-free. run_child() already alarmed
    # on the same frames when they streamed through stdout; no second alarm here.
    events = []
    for path in glob.glob(os.path.expanduser(
            f"~/.claude/projects/*/{session_id}.jsonl")):
        try:
            with open(path) as f:
                for line in f:
                    line = masking.mask_text(line.strip())[0]
                    if line:
                        try:
                            events.append(json.loads(line))
                        except ValueError:
                            continue
        except OSError:
            continue
    return events


def _ticket_block_reason(queue_db, ticket):
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{queue_db}?mode=ro", uri=True)
        row = con.execute("SELECT status, block_reason, notes FROM tasks WHERE id=?",
                          (ticket,)).fetchone()
        con.close()
    except Exception:
        return None, None
    if not row:
        return None, None
    status, block_reason, notes = row
    return status, (block_reason or notes or "")


def replay(runs_root, days=30, queue_db=None, node_map=None):
    """One triage verdict per ticket with a failed latest attempt in the window.
    Yields dicts; the CLI prints one line each. Read-only everywhere."""
    import time as _time
    cutoff = _time.time() - days * 86400
    for d in sorted(glob.glob(os.path.join(runs_root, "wq*"))):
        m = re.match(r"wq(\d+)$", os.path.basename(d))
        if not m:
            continue
        ticket = int(m.group(1))
        attempts = sorted(
            (f for f in os.listdir(d) if re.match(r"attempt-\d+\.json$", f)),
            key=lambda f: int(re.match(r"attempt-(\d+)", f).group(1)))
        if not attempts:
            continue
        last = os.path.join(d, attempts[-1])
        if os.path.getmtime(last) < cutoff:
            continue
        try:
            with open(last) as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        classification = rec.get("classification") or rec.get("fire_outcome") or "?"
        status, reason = (None, None)
        if queue_db:
            status, reason = _ticket_block_reason(queue_db, ticket)
        worker_reason = None
        if status == "blocked" and reason \
                and not reason.startswith(_HARNESS_PREFIXES):
            worker_reason = reason
        if classification in ("SUCCESS", "DEGRADED_SUCCESS", "auto_closed", "applied") \
                and not worker_reason:
            # a SUCCESS attempt on a currently worker-blocked ticket is the wq#749
            # sentinel path (wq block + BLOCKED + exit 0) — that is REAL_BLOCK, so it
            # stays in the replay; a genuinely-landed ticket is skipped.
            continue
        events = _load_transcript(rec.get("session_id")) if rec.get("session_id") else []
        fr = build_failure_record(
            ticket=ticket, attempts=len(attempts), classification=classification,
            block_class=None, events=events, worktree=rec.get("worktree"),
            expected_repo=None, session_id=rec.get("session_id"),
            trip_kind=("PERMISSION" if classification == "PERMISSION_LOOP" else None),
            worker_block_reason=worker_reason, attempt_files=attempts,
            node_map=node_map, check_worktree=False)
        yield {"ticket": ticket, "status": status, "classification": classification,
               "triage": fr["triage"], "inferred_needs": fr["inferred_needs"],
               "denied": sum(x["count"] for x in fr["denied_calls"]),
               "summary": fr["summary"]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("replay", help="offline triage replay (doc §5.2)")
    rp.add_argument("--runs-root", default="/home/lumen/foreman/_runs")
    rp.add_argument("--days", type=int, default=30)
    rp.add_argument("--queue-db", default="/home/lumen/agents/work-queue/queue.db")
    rp.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "replay":
        node_map = load_node_map()
        for row in replay(args.runs_root, days=args.days, queue_db=args.queue_db,
                          node_map=node_map):
            if args.json:
                print(json.dumps(row))
            else:
                needs = ",".join(row["inferred_needs"]) or "-"
                print(f"wq{row['ticket']:<6} {row['triage']:<18} "
                      f"{row['classification']:<16} denied={row['denied']:<3} "
                      f"needs={needs} [{row['status'] or '?'}] {row['summary'][:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
