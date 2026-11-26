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
# wq#3232: a backtick-quoted span like `python -c` names a technique ("set a sitecustomize
# or a python -c wrapper to DEBUG"), not a runnable invocation -- -c takes a following
# argument, so a span ending in it with nothing after it can never succeed on replay no
# matter what the worker does (acceptance.json is pinned at dispatch).
#
# wq#3235: that shipped as a 3-item {-c, -e, -m} allowlist, which left the same defect
# live one flag over -- `pytest -k` derived an AC1 that exits 4 (argparse: "-k/--keyword
# expected one argument"). A flag's arity is not knowable from the token; every parser
# (argparse, type-flag, clap) needs the tool's own declared schema, which we do not have
# for 19 allowlisted binaries. So the default inverts: a command ending in a flag token is
# NOT replayable unless the flag is known-boolean. The costs are asymmetric -- a wrongly
# rejected candidate merely falls back to the citation criterion (and the model derivation
# path may still propose it), while a wrongly accepted one pins a gate nothing can satisfy.
#
# wq#3238: that allowlist shipped as ONE FLAT SET shared by all 21 REPLAY_ALLOWED_BINARIES,
# which inverted the same defect for a different binary. Arity is a property of (binary,
# flag), not of the flag token: `-b` and `-u` are boolean for python but require a
# following argument for git (`git checkout -b` and `git branch -u` both exit 129, probed
# live 2026-09-30). _derive_deterministic() never minted those -- it drops every `git`
# candidate before a criterion exists -- but the MODEL derivation path does not: derive()
# -> _validate_criteria() -> replayable() has no git guard, so a model-proposed
# {cmd: "git checkout -b", expect_exit: 0} was KEPT, against derive()'s own docstring
# invariant ("never an unrunnable gate").
#
# So the allowlist is keyed per binary. A complete arity table for 21 tools is not
# available -- that is wq#3235's point and it still holds -- but this table does not have
# to be complete to be sound, because the DEFAULT IS STILL REJECT: a binary with no entry,
# or a flag outside its entry, is a dangling flag, and the candidate falls back to the
# citation criterion (the cheap failure). Only flags checked against the real tool are
# listed, and the test is the TRAILING position specifically: a flag earns an entry only
# when the command ending in it is complete AND terminating. That is why python's -i/-u/-b
# /-E are gone though they are genuinely boolean -- each one at the end of a command is a
# bare REPL invocation. Before wq#3239 _replay() inherited stdin and such a command sat
# until the replay timeout; it now gets EOF and exits 0, which is a vacuous pass. Either
# way it proves nothing, so it stays out of the table.
#
# Entry rule, drawn from the evidence rather than from taste: every counterexample this
# bug class has produced is a SINGLE-LETTER flag (-c/-e/-m in wq#3232, -k in wq#3233, -b/-u
# here), because that is where arity is binary-specific. So a single-letter entry must be
# checked against the real tool before it is listed -- `make -k` takes no argument while
# `pytest -k` demands one (probed live 2026-09-30), a pair no flat set can express. A
# long-form `--switch` whose name is a predicate (--dry-run, --noEmit, --porcelain) has no
# value grammar to get wrong and is listed on convention.
#
# wq#3242 (judgment, from wq#3241's CONTRADICT): the entry rule above is necessary but not
# sufficient, because "the real tool" is not one grammar for a binary that dispatches to
# subcommands. `-s` was listed for git on the strength of `git status -s` / `git log -s` /
# `git show -s` -- all boolean -- but `-s` is `--strategy` for `git rebase` and `git merge`,
# where it requires a value (both exit 129, "switch `s' requires a value", probed live
# 2026-09-30). One binary key, two arities: the wq#3235 defect recurring one level down.
# Two changes, because a fourth recurrence of one bug class says the table is the wrong
# place to hold the invariant:
#   1. `-s` is gone from git's set. The capability lost is nil -- `--short`/`--stat`/
#      `--oneline` cover the same query intent in long form and stay listed -- while the
#      false accept it bought was an unsatisfiable pinned AC.
#   2. For a SUBCOMMAND binary the short-flag ban is enforced in replayable() itself
#      (_SUBCOMMAND_BINARIES), not left to the table's contents, so re-adding `-s` to git's
#      set cannot resurrect `git merge -s`. This is why (binary, subcommand) keying was
#      rejected: it is sound too (default still reject), but it makes the table an order of
#      magnitude bigger, puts every future entry back on the honour system of "was this pair
#      probed", and buys only rare boolean short flags nobody needs a criterion for.
_FLAG_TOKEN_RE = re.compile(r"^-{1,2}[A-Za-z0-9]")
# A single-dash token: `-s`, `-vv`, `-sb`. Its arity is the subcommand's choice, never the
# binary's, for anything in _SUBCOMMAND_BINARIES.
_SHORT_FLAG_RE = re.compile(r"^-[A-Za-z0-9]+$")
# Zero-arity by convention across every allowlisted binary -- no CLI takes an argument TO
# its help. (Their exit codes differ: `git -h` is 129, `go -h` is 2. That is the gate's
# business, not this check's; a wrong expect_exit fails the AC with the tool's own error.
# This check only answers "could the command be missing a required argument".)
_HELP_FLAGS = {"-h", "--help", "--version", "-V"}
_PYTEST_ZERO_ARG = _HELP_FLAGS | {
    "-x", "-v", "-vv", "-vvv", "-q", "-qq", "-s", "--verbose", "--quiet", "--exitfirst",
    "--last-failed", "--lf", "--ff", "--co", "--collect-only", "--no-header", "-ra",
}
_ZERO_ARG_FLAGS_BY_BINARY = {
    "pytest": _PYTEST_ZERO_ARG,
    # `python -m pytest ...` is parsed by pytest, not python -- see _arity_binary().
    "python": _HELP_FLAGS,
    "python3": _HELP_FLAGS,
    # NOT -b (git checkout -b) and NOT -u (git branch -u): the wq#3237 finding. NOT -s
    # either (git rebase/merge -s == --strategy <name>): the wq#3241 finding -- and no
    # other short flag, by _SUBCOMMAND_BINARIES below.
    "git": _HELP_FLAGS | {"--short", "--porcelain", "--stat", "--oneline",
                          "--exit-code", "--name-only",
                          # wq#3240: the read-only forms of `git branch` end in these
                          "--list", "--show-current"},
    "ruff": _HELP_FLAGS | {"--fix", "--check", "--diff"},
    "mypy": _HELP_FLAGS | {"--strict", "--no-incremental"},
    "flake8": _HELP_FLAGS | {"--count", "--statistics"},
    "shellcheck": _HELP_FLAGS,
    "make": _HELP_FLAGS | {"-n", "--dry-run", "-k", "--keep-going"},
    "npm": _HELP_FLAGS | {"--dry-run", "--silent"},
    "npx": _HELP_FLAGS,
    "pnpm": _HELP_FLAGS,
    "yarn": _HELP_FLAGS,
    "node": _HELP_FLAGS,
    "jest": _HELP_FLAGS | {"--ci", "--silent", "--listTests", "--onlyFailures"},
    "vitest": _HELP_FLAGS | {"--run"},
    "tsc": _HELP_FLAGS | {"--noEmit", "--strict", "--listFiles"},
    "go": _HELP_FLAGS,
    "cargo": _HELP_FLAGS | {"--locked", "--offline", "--all-features"},
    # bash/sh with a trailing flag and no script reads stdin -- never a proof command.
    "bash": _HELP_FLAGS,
    "sh": set(),
}
# Binaries that dispatch to subcommands with independent flag grammars, so a short flag's
# arity is a property of (binary, subcommand) and cannot be answered by a binary-keyed set.
# The help flags stay exempt for the reason at _HELP_FLAGS: nothing takes an argument to its
# help. Everything else single-dash is rejected for these, table entry or not (wq#3242).
# Flat CLIs are absent on purpose -- pytest's -x/-q, make's -k mean one thing per binary.
_SUBCOMMAND_BINARIES = {"git", "go", "cargo", "npm", "npx", "pnpm", "yarn"}


def _arity_binary(argv):
    """The binary whose flag schema governs argv's TRAILING token.

    `python -m pytest -k` is parsed by pytest, not python, so keying on argv[0] alone
    would check `-k` against python's table and get the arity of a different program.
    is_whole_suite() already unwraps the same shape."""
    head = os.path.basename(argv[0])
    if head in ("python", "python3") and argv[1:2] == ["-m"] and len(argv) > 2:
        mod = os.path.basename(argv[2])
        if mod in _ZERO_ARG_FLAGS_BY_BINARY:
            return mod
    return head
_CITATION_RE = re.compile(r"^(?P<path>[^\s:]+):(?P<start>\d+)(?:-(?P<end>\d+))?$")

# Tickets whose deliverable is a document, not a diff, get no baseline diff criterion —
# a report-writing archetype has nothing to cite in `git diff` and must not be gated on
# one. They still get command criteria if the ticket names commands.
NON_DIFF_ARCHETYPES = {"verify-pass", "rca-investigation", "fire-response",
                       "research", "research-synthesis", "documentation",
                       "review-readonly"}
# wq#5145: archetypes whose worker has no Write and no Bash (permission_profiles.
# REVIEW_READONLY_*), so it can neither write evidence.json nor run a replayed command.
# They get NO pinned criteria: the harness itself validates the deliverable
# (review_sarif.emit parses the findings block and writes the SARIF) before closing.
NO_EVIDENCE_ARCHETYPES = {"review-readonly"}
# wq#5143 (wq#5041 gap 2 — CodeAF: "work that cannot be checked waits for the human"): a
# ticket for which derive() yields no replayable criterion used to dispatch UNGATED and
# close on the worker's own `wq done` prose — verify() is vacuous on an empty doc and
# exit_gate only needs ticket_done + a clean result. These archetypes are exempt because
# the harness checks their deliverable by another route: review-readonly's findings are
# parsed into SARIF by the harness (wq#5145), and a verify-pass IS the check (its verdict
# feeds orchestrator/judgment.py). Any other archetype with zero criteria is UNCHECKABLE
# and goes to Frank's queue before a worktree or a token is spent.
CHECKED_ELSEWHERE_ARCHETYPES = NO_EVIDENCE_ARCHETYPES | {"verify-pass"}
# Frank's "run it ungated anyway" answer: this marker in the ticket's notes or detail
# lifts the gate for that ticket alone.
UNCHECKABLE_ACK = "[uncheckable-ok]"

VALID_PROOF_TYPES = ("test", "citation")
REPLAY_TIMEOUT_S = int(os.environ.get("FOREMAN_REPLAY_TIMEOUT_S", "900") or 900)
# wq#3234: a whole-suite pytest cannot finish inside 900s — `pytest harness/tests` took
# 1241s and repo-root `pytest -p no:randomly` 2457s on Lumen (wq#3222, 2026-09-30), so
# every done claim on such an AC timed out into rework forever. A suite-shaped command
# gets this budget (~1.5x the slowest measured run); a pinned `timeout_s` is honoured but
# clamped to [REPLAY_TIMEOUT_S, REPLAY_TIMEOUT_MAX_S] so ticket text can't wedge the gate.
SUITE_REPLAY_TIMEOUT_S = int(os.environ.get("FOREMAN_SUITE_REPLAY_TIMEOUT_S", "3600") or 3600)
REPLAY_TIMEOUT_MAX_S = max(SUITE_REPLAY_TIMEOUT_S, REPLAY_TIMEOUT_S)
# pytest options that consume the next token, so that token is not a test target.
_PYTEST_VALUE_FLAGS = {"-p", "-k", "-m", "-c", "-o", "-W", "--rootdir", "--confcutdir",
                       "--basetemp", "--deselect", "--ignore", "--ignore-glob",
                       "--junitxml", "--maxfail", "--durations", "--tb", "--log-level",
                       "--override-ini", "--import-mode", "--cov", "--timeout"}
OUTPUT_TAIL_LINES = 30


# ---------------------------------------------------------------------------
# 1a — derivation
# ---------------------------------------------------------------------------

# wq#3240: the gate must not WRITE to the branch it is judging. Arity (wq#3238/#3242) says
# whether a git command is complete; it says nothing about whether a complete one is safe.
# `{cmd: "git checkout -b feature", expect_exit: 0}` is well formed, passes every arity
# check, gets pinned, and _replay() then runs it with cwd=worktree -- moving HEAD under the
# diff the citation criteria are checked against. So git is allowlisted by SUBCOMMAND, on
# both the derivation and the replay path (a hand-written pinned cmd reaches _replay too).
# Default is reject: only subcommands that read are listed.
_GIT_READONLY_SUBCOMMANDS = {
    "status", "diff", "log", "show", "rev-parse", "ls-files", "ls-tree", "cat-file",
    "merge-base", "rev-list", "diff-tree", "blame", "describe", "show-ref",
    "for-each-ref", "grep",
}
# `git branch` is read-only only when it is asked to list; bare `git branch NAME` creates.
_GIT_BRANCH_LIST_FLAGS = {"--list", "-l", "--show-current"}
_GIT_BRANCH_MUTATING_FLAGS = {
    "-d", "-D", "-m", "-M", "-c", "-C", "-u", "-f", "--delete", "--move", "--copy",
    "--force", "--set-upstream-to", "--unset-upstream", "--edit-description",
}


def _git_mutation_reason(argv):
    """Why this git argv may write to the repo or worktree, or "" if it only reads."""
    if os.path.basename(argv[0]) != "git" or len(argv) < 2:
        return ""
    sub = argv[1]
    if sub in _HELP_FLAGS:
        return ""
    if sub.startswith("-"):
        # `git -c k=v ...` can run arbitrary configured commands and -C/--git-dir retarget
        # the repo; none of that is a proof command, so global options are not accepted.
        return f"git global option {sub!r} is not accepted before the subcommand (wq#3240)"
    if sub == "branch":
        flags = [a for a in argv[2:] if a.startswith("-")]
        if any(f.split("=", 1)[0] in _GIT_BRANCH_MUTATING_FLAGS for f in flags):
            return "'git branch' with a mutating flag changes refs (wq#3240)"
        if not any(f in _GIT_BRANCH_LIST_FLAGS for f in flags):
            return ("'git branch' creates a branch unless it is asked to list; "
                    "pass --list or --show-current (wq#3240)")
        return ""
    if sub not in _GIT_READONLY_SUBCOMMANDS:
        return (f"'git {sub}' is not on the read-only allowlist: a gate may not write to "
                "the branch it is judging (wq#3240)")
    if any(a == "--output" or a.startswith("--output=") for a in argv[2:]):
        return f"'git {sub} --output' writes a file into the worktree (wq#3240)"
    return ""


def replayable(cmd, pinned=False):
    """(ok, reason) — may the harness re-run this command itself?

    `pinned=True` means the criterion is already in a pinned acceptance.json, so the
    dangling-flag check is skipped: refusing it there would newly reject a command that
    ends in a boolean flag _ZERO_ARG_FLAGS_BY_BINARY does not happen to list, and buys
    nothing -- a genuinely dangling flag fails the gate on its own exit code either way,
    with the tool's real error message instead of ours. The check earns its keep only
    before the pin, where it is the difference between a satisfiable criterion and no
    criterion."""
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
    why = _git_mutation_reason(argv)
    if why:
        return False, why
    last = argv[-1]
    if not pinned and _FLAG_TOKEN_RE.match(last) and "=" not in last:
        owner = _arity_binary(argv)
        if (owner in _SUBCOMMAND_BINARIES and last not in _HELP_FLAGS
                and _SHORT_FLAG_RE.match(last)):
            return False, (f"{last!r} is a dangling flag for {owner!r}: a short flag's "
                           f"arity is chosen by the subcommand, and {owner} has one grammar "
                           "per subcommand (wq#3242)")
        if last not in _ZERO_ARG_FLAGS_BY_BINARY.get(owner, ()):
            return False, (f"{last!r} is a dangling flag for {owner!r}: may require a "
                           "following argument, none present")
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


def is_whole_suite(cmd):
    """True for a pytest run over a directory or the whole rootdir (no file/node-id
    targets), e.g. `pytest harness/tests` or `pytest -p no:randomly`."""
    try:
        argv = shlex.split(cmd or "")
    except ValueError:
        return False
    if argv[:1] and os.path.basename(argv[0]) in ("python", "python3") and argv[1:3] == ["-m", "pytest"]:
        rest = argv[3:]
    elif argv[:1] and os.path.basename(argv[0]) == "pytest":
        rest = argv[1:]
    else:
        return False
    targets, skip = [], False
    for tok in rest:
        if skip:
            skip = False
        elif tok in _PYTEST_VALUE_FLAGS:
            skip = True
        elif not tok.startswith("-"):
            targets.append(tok)
    return not targets or any("::" not in t and not t.endswith(".py") for t in targets)


def replay_timeout(criterion):
    """Seconds the harness allows this criterion's replay: its pinned `timeout_s` if
    any (clamped), else the suite budget for a whole-suite command, else the default.
    Computed at replay time too, so docs pinned before wq#3234 get the suite budget."""
    pinned = criterion.get("timeout_s")
    try:
        pinned = int(pinned) if pinned is not None else None
    except (TypeError, ValueError):
        pinned = None
    if pinned is None:
        pinned = SUITE_REPLAY_TIMEOUT_S if is_whole_suite(criterion.get("cmd")) else REPLAY_TIMEOUT_S
    return min(max(pinned, REPLAY_TIMEOUT_S), REPLAY_TIMEOUT_MAX_S)


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
                         "proof_type": "test", "cmd": cand, "expect_exit": 0,
                         "timeout_s": replay_timeout({"cmd": cand})})
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
                         "expect_exit": int(c.get("expect_exit", 0) or 0),
                         "timeout_s": replay_timeout(c)})
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
    if (ticket.get("archetype") or "").strip() in NO_EVIDENCE_ARCHETYPES:
        return {"ticket": f"wq#{ticket.get('id', '?')}", "derived_by": "readonly-archetype",
                "criteria": [],
                "notes": ["read-only archetype: no Write/Bash, so no evidence.json; the "
                          "harness validates the findings block and emits SARIF itself"]}
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


def uncheckable(doc, ticket):
    """wq#5143: why `ticket` has no machine-checkable done, or None when it has one.
    `doc` is derive()'s output, or None when derivation itself raised."""
    archetype = (ticket.get("archetype") or "").strip()
    if archetype in CHECKED_ELSEWHERE_ARCHETYPES:
        return None
    if doc is not None and doc.get("criteria"):
        return None
    if UNCHECKABLE_ACK in f"{ticket.get('notes') or ''}\n{ticket.get('detail') or ''}":
        return None
    if doc is None:
        return "acceptance derivation raised, so nothing machine-checkable was built"
    arch = archetype or "standard-engineering"
    tail = (", and its deliverable is not a diff to cite"
            if arch in NON_DIFF_ARCHETYPES else "")
    return (f"no replayable acceptance criterion derived (archetype {arch}: the ticket "
            f"names no runnable test command{tail})")


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


def private_open(path, append=False):
    """Open `path` for writing as 0600 (truncating unless `append`); also tightens a
    pre-existing file, which O_CREAT's mode argument alone would leave at its old bits
    (wq#2911). Every file under _runs goes through here: the run dir is 0711 so uid
    tokeneng can reach its grants, which makes any 0644 file in it readable by name."""
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    os.fchmod(fd, 0o600)
    return fd


def write_json(path, doc):
    tmp = path + ".tmp"
    with open(private_open(tmp), "w") as f:
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
            dst = os.path.join(run_dir, EVIDENCE_FILENAME)
            shutil.copy2(src, dst)
            os.chmod(dst, 0o600)   # wq#2911: copy2 carries the worktree's 0644 over
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
    ok, why = replayable(cmd, pinned=True)
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
    timeout_s = replay_timeout(criterion)
    try:
        # wq#3239: stdin is closed, not inherited. A pinned criterion that reads stdin
        # (bare `python3 -i`, `sh`, a tool that prompts) otherwise blocks on the harness's
        # own stdin until the replay timeout, up to 3600s on a suite-shaped command.
        p = runner(argv, cwd=worktree, env=env, capture_output=True, text=True,
                   timeout=timeout_s, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout_s}s", "", None
    except (OSError, ValueError) as e:
        return False, f"replay failed to start ({e})", "", None
    # merged streams on purpose (ISSUE-LOG #30: an API error on stdout fooled a
    # stderr-only grep) — the harness keeps both and judges on the exit code.
    merged = (p.stdout or "") + (p.stderr or "")
    log_path = None
    if evidence_dir:
        try:
            # wq#2911: a replay log holds whatever the command printed, fixture tokens
            # included — owner-only, like every other file under _runs.
            os.makedirs(evidence_dir, mode=0o700, exist_ok=True)
            log_path = os.path.join(evidence_dir, f"replay-{criterion['id']}.log")
            with open(private_open(log_path), "w") as f:
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
#
# wq#4643: the rule is bundle-scoped, not install.sh-scoped (ops/VERIFY_CONTRACT.md "A bundle
# is implicated by any file in it"): wq#4458/wq#4638 cited only a payload
# (ops/wq3354-fleet-selfheal/selfheal.yaml) and skipped the check. Any file under
# ops/wqNNNN-*/ implicates that bundle's install.sh when it exists — same predicate as
# bin/wq's _ops_bundle_install (OPS_BUNDLE_DIR_RE).
_OPS_BUNDLE_DIR_RE = re.compile(r"(?:^|/)ops/wq\d+[\w.-]*/(?=.)")


def _ops_bundle_install(worktree, rel):
    """Absolute path of the install.sh of the ops/wqNNNN-* bundle repo-relative `rel` sits
    in, or None when it sits in no bundle or the bundle ships no install.sh. Matched on
    `rel`, so a worktree that itself lives under an ops/wq* path cannot implicate it."""
    m = _OPS_BUNDLE_DIR_RE.search(rel)
    if not m:
        return None
    install = os.path.join(worktree, rel[:m.end()] + "install.sh")
    return install if os.path.isfile(install) else None


def _sha256(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


# wq#4778 (ISSUE-LOG #1636): wq done's own ops-deploy gate has a --no-deploy-needed
# escape for exactly the shape a sandboxed BUILD worker hits here — no ssh/fleet-exec
# grant onto the live host, and the deploy target sits outside every worktree's allowed
# dirs — but this citation gate had no equivalent, so a worker with a provably correct
# config fix could never self-close (wq#4774). This module stays "no wq, no dispatch"
# (module docstring): the override does not file anything itself, it only hands the
# caller (fworker.py, which already owns wq_show/wq_block) a followup descriptor naming
# the bundle, so the follow-up ticket — same wq#3487 shape, same
# `Liveness: ops_verified(<bundle>)` auto-close tag — gets filed/reused one layer up.
def _ops_deploy_evidence(entry, abspath):
    target = str(entry.get("deploy_target") or "").strip()
    art_dir = os.path.dirname(abspath)
    no_deploy_needed = str(entry.get("no_deploy_needed") or "").strip()
    if not target:
        if no_deploy_needed:
            followup = {"bundle": os.path.basename(art_dir), "install_path": abspath,
                        "why": no_deploy_needed}
            return True, f"{abspath}: no_deploy_needed={no_deploy_needed!r}", followup
        return False, (f"{abspath} is an ops install artifact — landing on this branch proves "
                       f"the script exists, not that it reached a target; add a "
                       f"'deploy_target' path to this criterion naming a live file whose bytes "
                       f"match one shipped in {art_dir}, or a 'no_deploy_needed' reason naming "
                       f"why a sandboxed worker cannot reach the target"), None
    if not os.path.isfile(target):
        return False, f"deploy_target {target} does not exist", None
    # wq#2762 (ISSUE-LOG #859, closes wq#1734's verify-pass escalation): a deploy_target
    # inside the artifact's own directory byte-matches itself and passes with zero real
    # deployment — reject that before the walk, not after.
    real_target = os.path.realpath(target)
    real_art_dir = os.path.realpath(art_dir)
    if real_target == real_art_dir or real_target.startswith(real_art_dir + os.sep):
        return False, (f"deploy_target {target} resolves inside {art_dir} — the artifact's own "
                       f"shipped payload cannot prove its own delivery"), None
    # wq#2813: realpath does not see through a hard link — a deploy_target outside art_dir
    # that shares an inode with a shipped file is still the artifact's own payload, not a
    # deployed copy. Checked against every shipped file before any byte-match, so a duplicate
    # payload elsewhere in the artifact cannot launder it.
    for root, _dirs, files in os.walk(art_dir):
        for name in files:
            cand = os.path.join(root, name)
            try:
                same = os.path.samefile(target, cand)
            except OSError:
                same = False
            if same:
                return False, (f"deploy_target {target} is a hard link to {cand} — the "
                               f"artifact's own shipped payload cannot prove its own delivery"), None
    target_hash = _sha256(target)
    for root, _dirs, files in os.walk(art_dir):
        for name in files:
            if name == "install.sh":
                continue
            cand = os.path.join(root, name)
            if target_hash is not None and _sha256(cand) == target_hash:
                return _ops_deploy_verify(entry, target, cand)
    return False, f"deploy_target {target} matches no file shipped in {art_dir}", None


# wq#2762 (ISSUE-LOG #859): a deploy_target byte-match only proves the artifact's payload
# reached *some* file — it proves nothing about (a) whether the ticket's premise about the
# target's live state still held at deploy time (wq#2713: healer coverage dropped for a
# LIVE redis-server on a false retired-unit premise) or (b) whether the install was tried
# read-only first (wq#2472: install.sh chowned to a 'lumen' user that only exists on
# Lumen). Require a 'deploy_verify' field naming both, checked structurally per wq#2630
# ("a probe must be able to fail") so a bare non-empty string can't satisfy it.
_DEPLOY_VERIFY_PREMISE_RE = re.compile(r"premise\s*:\s*\S")
_DEPLOY_VERIFY_PROBE_RE = re.compile(r"probe\s*:\s*\S")


def _ops_deploy_verify(entry, target, cand):
    verify = str(entry.get("deploy_verify") or "").strip()
    if not verify:
        return False, (f"{target} byte-matches {cand}, but that only proves the payload "
                       f"reached a file, not that the ticket's premise about the target's "
                       f"live state still held or that the install was tried read-only "
                       f"first; add a 'deploy_verify' field naming both a 'premise: "
                       f"<re-check against live state>' and a 'probe: <dry-run/read-only "
                       f"check against the real target>'"), None
    if not (_DEPLOY_VERIFY_PREMISE_RE.search(verify) and _DEPLOY_VERIFY_PROBE_RE.search(verify)):
        return False, (f"deploy_verify {verify!r} must name both a 'premise: <re-check>' and "
                       f"a 'probe: <dry-run/read-only check>' against the real target"), None
    return True, f"{target} byte-matches {cand}; deploy_verify={verify!r}", None


# wq#2753 (ISSUE-LOG #728): a legitimately cross-repo change (wq#2624 landed in
# lumen-bin while its gate ran in a foreman worktree) could never cite anything —
# _check_citation only accepted files in the LOCAL worktree's diff. A citation entry may
# now carry {"repo": "/abs/managed/repo", "rev": "<sha>"} and is then checked entirely
# against that repo: the rev must be a landed commit (ancestor of main — immutable and
# re-runnable per wq#2630, never a live working tree), its own diff must touch the cited
# file, and the cited lines must exist in the file as committed at that rev.
_REV_RE = re.compile(r"^[0-9a-f]{7,40}$")


def _crossrepo_citation(entry, rel, start, end):
    repo = os.path.realpath(str(entry.get("repo") or ""))
    rev = str(entry.get("rev") or "").strip()
    try:
        try:
            import repo_sweep
        except ImportError:  # stop_gate loads this module from outside harness/
            import sys
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import repo_sweep
        managed = {os.path.realpath(p) for p in repo_sweep.managed_repos()}
    except Exception as e:  # fail closed: an unreadable repo list must not widen the gate
        return False, f"cross-repo citation: managed-repo list unavailable ({e!r})"
    if repo not in managed:
        return False, f"cross-repo citation: {repo} is not a managed repo"
    if not _REV_RE.match(rev):
        return False, "cross-repo citation needs a 'rev' commit sha pinning the landed change"
    for ref in ("origin/main", "main", "origin/HEAD"):
        p = _git(repo, "merge-base", "--is-ancestor", rev, ref)
        if p and p.returncode == 0:
            break
    else:
        return False, f"{rev} has not landed on {repo}'s main"
    p = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", rev)
    if not p or p.returncode != 0:
        return False, f"cannot read {rev}'s diff in {repo}"
    if os.path.normpath(rel) not in {os.path.normpath(n) for n in p.stdout.splitlines()
                                     if n.strip()}:
        return False, f"{rel} is not touched by {repo}@{rev}"
    p = _git(repo, "show", f"{rev}:{rel}")
    if not p or p.returncode != 0:
        return False, f"{rel} does not exist at {repo}@{rev}"
    n_lines = len(p.stdout.splitlines())
    if start < 1 or end < start or end > n_lines:
        return False, f"{rel}@{rev} has {n_lines} lines; cited {start}-{end}"
    return True, f"{rel}:{start}-{end} in {repo}@{rev}"


def _check_citation(entry, worktree, changed):
    """Returns (ok, why, tail, root_apply_followup). `root_apply_followup` is a
    {"bundle", "install_path", "why"} dict when an ops-bundle citation closed via the
    wq#4778 `no_deploy_needed` override, else None — the caller (verify(), then
    fworker.py one layer up) files/reuses the follow-up ticket this module itself never
    touches wq to create."""
    cite = str(entry.get("citation") or "").strip()
    m = _CITATION_RE.match(cite)
    if not m:
        return False, f"citation {cite!r} is not file:line", "", None
    rel = m.group("path")
    start = int(m.group("start"))
    end = int(m.group("end") or start)
    if entry.get("repo"):
        ok, why = _crossrepo_citation(entry, rel, start, end)
        return ok, why, "", None
    abspath = os.path.realpath(os.path.join(worktree, rel))
    if not abspath.startswith(os.path.realpath(worktree) + os.sep):
        return False, "citation path escapes the worktree", "", None
    if not os.path.isfile(abspath):
        return False, f"{rel} does not exist", "", None
    with open(abspath, errors="replace") as f:
        n_lines = sum(1 for _ in f)
    if start < 1 or end < start or end > n_lines:
        return False, f"{rel} has {n_lines} lines; cited {start}-{end}", "", None
    norm = os.path.normpath(rel)
    if norm not in {os.path.normpath(c) for c in changed}:
        return False, f"{rel} is not touched by this branch's diff", "", None
    install = _ops_bundle_install(os.path.realpath(worktree), norm)
    if install:
        ok, why, followup = _ops_deploy_evidence(entry, install)
        if not ok:
            return False, why, "", None
        if followup:
            return True, why, "", followup
    return True, f"{rel}:{start}-{end} in diff", "", None


# wq#3035 (wq#2922 root cause): _check_citation above proves a citation matches the
# WORKER'S OWN branch diff — proof the claim is not hallucinated, nothing more. It is
# right to stay branch-scoped for the ordinary per-attempt replay (verify(), called from
# fworker.verify_done): the cited code legitimately exists only in the branch while an
# attempt is still in flight. But citing file:line in an unmerged branch satisfied `done`
# exactly as well as citing landed code — wq#2922 closed done (attempt-3 SUCCESS,
# proof_type=citation) while the vulnerability it documented stayed open on main for
# days. _crossrepo_citation already demands its rev be an ancestor of the OTHER repo's
# main; the same-repo path never made the same demand, which is backwards. This is the
# missing landed-check, meant to run once — at the done transition
# (fworker.check_artifacts_done) — never during the ordinary attempt replay.
def citation_landed_on_main(entry, worktree):
    """Same-repo citation (no 'repo' field — cross-repo already pins and checks its own
    landed rev via _crossrepo_citation), re-resolved against THIS repo's own landed main
    instead of the branch diff. Returns (ok, why)."""
    if entry.get("repo"):
        return True, "cross-repo citation already pins and checks its own landed rev"
    cite = str(entry.get("citation") or "").strip()
    m = _CITATION_RE.match(cite)
    if not m:
        return False, f"citation {cite!r} is not file:line"
    rel = m.group("path")
    start = int(m.group("start"))
    end = int(m.group("end") or start)
    saw_ref = False
    for ref in ("origin/main", "main", "origin/HEAD"):
        rv = _git(worktree, "rev-parse", "--verify", "--quiet", ref)
        if not rv:
            return False, "git unavailable while checking landing"
        if rv.returncode != 0:
            continue
        saw_ref = True
        e = _git(worktree, "cat-file", "-e", f"{ref}:{rel}")
        if not e:
            return False, "git unavailable while checking landing"
        if e.returncode != 0:
            return False, f"{rel} does not exist on {ref} — cited from an unmerged branch"
        show = _git(worktree, "show", f"{ref}:{rel}")
        if not show or show.returncode != 0:
            return False, f"{rel} could not be read from {ref}"
        n_lines = len(show.stdout.splitlines())
        if start < 1 or end < start or end > n_lines:
            return False, f"{rel}@{ref} has {n_lines} lines; cited {start}-{end}"
        return True, f"{rel}:{start}-{end} landed on {ref}"
    if not saw_ref:
        return False, "no main/origin/main ref resolvable to check landing against"
    return False, "citation could not be checked against a landed ref"


def citations_landed(pinned, evidence, worktree):
    """Every proof_type=citation criterion's evidence, re-checked against landed main —
    the done-time counterpart to verify()'s branch-scoped per-attempt check. Returns
    (ok, failed): failed is a list of {"id", "why"}."""
    failed = []
    by_id = {str(e.get("id")): e for e in ((evidence or {}).get("criteria") or [])
             if isinstance(e, dict) and e.get("id")}
    for c in (pinned or {}).get("criteria") or []:
        if c.get("proof_type") != "citation":
            continue
        cid = str(c.get("id"))
        entry = by_id.get(cid)
        if entry is None:
            failed.append({"id": cid, "why": "no evidence entry to check against main"})
            continue
        ok, why = citation_landed_on_main(entry, worktree)
        if not ok:
            failed.append({"id": cid, "why": why})
    return not failed, failed


def verify(acceptance, evidence, worktree, base_ref="origin/main", evidence_dir=None,
           runner=subprocess.run, claim_check=None):
    """Harness-side verdict on a worker's done claim. Never trusts a logged exit code:
    every `test` criterion is re-run here, every citation re-checked against the diff.

    `claim_check(acceptance, evidence, worktree)` is the optional local-inference pass
    (hallucination_guard.claim_check, wq#2020) run after the mechanical replay: its
    verdict lands in result["claim_check"], and its `flagged` records fail the gate only
    when its mode is `enforce`. It never runs on the Stop-hook path (no caller passes it).

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
            ok, observed, tail, followup = _check_citation(entry, worktree, changed)
            record = {"id": cid, "text": c.get("text", ""),
                      "expected": "file:line inside this branch's diff",
                      "observed": observed,
                      "repro": "git diff --name-only $(git merge-base HEAD origin/main)..HEAD",
                      "claimed": entry.get("citation"), "output_tail": tail}
            if followup:
                record["root_apply_followup"] = followup
                result.setdefault("root_apply_followups", []).append(followup)
        record["ok"] = ok
        result["checked"].append(record)
        if not ok:
            result["failed"].append(record)
    if claim_check is not None and not result["failed"]:
        # only worth local inference once the mechanical replay is clean
        cc = claim_check(acceptance, evidence, worktree)
        result["claim_check"] = cc
        if cc.get("mode") == "enforce":
            result["failed"].extend(cc.get("flagged") or [])
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
