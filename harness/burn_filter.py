"""wq#2290: decide whether a mask hit is a BURNED credential or just credential-shaped
source text, so the wq#2275 tripwire pages only for the former.

Why this exists
---------------
masking.PATTERNS is deliberately loose — an artefact that keeps a token because the
pattern was too strict is the expensive failure, so the masker over-matches on
purpose and that is correct. The ALARM built on top of it inherited the looseness,
and an alarm means something much narrower: "a live token reached the model's
context, rotate it today" (instinct 4). Those two are not the same event.

On 2026-09-20 they diverged in production. wq#2278 read harness/tests/
test_token_injection.py, whose fixtures are strings like sk-ant-oat01-<dummy>, and
the tripwire filed a pri-1 rotation ticket (wq#2290) for a value that was never
live. Eight such tickets were open within minutes of each other, none of them a
real burn.

wq#2292 and wq#2288 tightened the patterns themselves — prefixes anchored at a word
boundary, a trailing lookahead on kv-secret — and that removed most of it. What
those cannot reach is a string that is genuinely shaped like a credential and
genuinely is not one. Reading the tracked tree still pages from 35 files: the auth
fixtures above, and the ordinary source that kv-secret reads as a secret because it
keys off an English word rather than a prefix (dispatch_health.py,
spawn_loop_guard.py, read_dir_sweep.py). No pattern can tell those from the real
thing, because as text they are the real thing. Provenance can.

The discriminator
-----------------
Two questions, cheapest first, and the order matters for instinct 4:

1. Is the matched text one of the credentials the harness itself handed this child?
   Those are the values genuinely at risk of reaching the model. In-process set
   comparison — a live secret is NEVER passed to a subprocess argv here.
2. Otherwise, does the matched text appear verbatim in the tracked source of any repo
   the child was allowed to read? Then it is committed text the reader was entitled
   to see, not a runtime secret.

Anything else is unknown provenance and alarms. The default is to alarm: a value we
cannot account for is treated as burned.

Question 2 means EVERY granted repo, not just the worktree (wq#2319). A worker reads
across its --add-dir roots, and doctrine orders every one of them to read
/home/lumen/kb/ISSUE-LOG.md before changing anything — a log whose rows quote the
credential shapes that filed the rotation backlog. Screening against the worktree
alone, those rows are unaccounted, so the log pages on its own text and the ticket it
files sends the next worker to read it again. That is the wq#2278 -> #2290 -> #2299 ->
#2319 chain: replaying wq#2288's 22:36 alarm, all three sk-key values are prose from
ISSUE-LOG.md:657 (`risk-asse...`-family words the then-unanchored `sk-` prefix ran
into), `in_worktree=False in_kb=True`. Counting kb as the source it is turns that
attempt's `burned=['sk-key']` into `burned=[]`.

Masking itself is unchanged — artefacts stay redacted either way. This only gates
the ntfy page and the rotation ticket.
"""
import re
import subprocess
import sys
import types

import live_code
import masking

# Env var names that carry a credential VALUE. Same vocabulary as masking's
# kv-secret pattern, matched against the variable name rather than the line.
SECRET_NAME_RE = re.compile(r"TOKEN|KEY|SECRET|PASSWORD|PASSWD|PAT", re.IGNORECASE)

# Values under a secret-shaped name that are still not credentials: a path to the
# file holding one (FOREMAN_LITELLM_KEY_FILE), a base URL, a flag.
_NON_SECRET_PREFIXES = ("/", "~", "./", "http://", "https://")

MIN_SECRET_LEN = 12
GIT_GREP_TIMEOUT_S = 20
GIT_REV_PARSE_TIMEOUT_S = 5


class Hit(str):
    """A matched string that also remembers its body: the match minus the pattern's
    fixed prefix (group 1 of every masking.PATTERNS rule).

    Still the whole match as far as equality, hashing and the provenance cache go, so
    a caller that never asks for `.body` sees plain text. A plain `str` handed to
    classify() has no body and is screened whole, as before wq#2415.
    """
    __slots__ = ("body",)

    def __new__(cls, whole, body):
        self = super().__new__(cls, whole)
        self.body = body
        return self


def matched_values(text, labels=None):
    """{label: [matched strings]} for `text`, restricted to `labels` if given.

    masking.scan() already walks the same patterns, but deliberately reduces each
    value to a sha256 prefix because its records are written to disk (wq#2288). The
    provenance lookup below needs the string itself, so it is re-derived here and
    kept in memory only — nothing from this function reaches an artefact.

    Unlike scan(), every pattern matches the ORIGINAL text rather than the partially
    masked text carried between patterns. scan() is right to be sequential: it
    reports what each substitution did. Classification needs the opposite. sk-key
    runs before kv-secret, so sequentially kv-secret sees `KEY": "sk-ant-MASKED` —
    a string that exists nowhere on disk, so its provenance lookup could only ever
    fail and every such line would page. Against the original, kv-secret sees the
    real `KEY": "sk-ant-<token>` and can be resolved. Callers pass `labels` from
    mask_text(), so a pattern that only matches the original never invents a label
    the masker did not report.
    """
    out = {}
    for label, rx, _repl in masking.PATTERNS:
        if labels is not None and label not in labels:
            continue
        for m in rx.finditer(text):
            out.setdefault(label, []).append(Hit(m.group(0), m.group(0)[len(m.group(1)):]))
    return out


def live_secret_values(env):
    """The credential values present in the child's environment.

    A name-based sweep rather than a fixed list of variables: build_env() sets
    CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_AUTH_TOKEN depending on the pool, and
    anything inherited from fworker's own environment is just as live.
    """
    live = set()
    for name, value in (env or {}).items():
        if not isinstance(value, str) or not SECRET_NAME_RE.search(name):
            continue
        value = value.strip()
        if len(value) < MIN_SECRET_LEN or value.startswith(_NON_SECRET_PREFIXES):
            continue
        live.add(value)
    return live


def _as_roots(repo_roots):
    """One root or many, as a list. Accepts the pre-wq#2319 single-string form."""
    if not repo_roots:
        return []
    if isinstance(repo_roots, str):
        return [repo_roots]
    return [r for r in repo_roots if r]


def provenance_roots(cwd, add_dirs=(), runner=subprocess.run):
    """The granted directories whose committed text can excuse a mask hit.

    `cwd` first: it is the worktree the child is working in, so it explains the most
    hits for the least work, and the rest of the list is only consulted when it does
    not. A directory that is not inside a git repo (the wq scratch dir,
    /home/lumen/agents/work-queue) has no committed text and is dropped here rather
    than costing a failed `git grep` per matched value.

    Each surviving directory is kept AS GRANTED, never widened to its repo root:
    /home/lumen/projects/nest sits in the /home/lumen/projects repo, and text from a
    sibling project the child could not read must not excuse its hit.

    Callers resolve this on the first mask hit, not at attempt start: most attempts
    never match a pattern at all, so the `git rev-parse` per granted dir would be
    pure waste on the common path — and run_child()'s only subprocess before the
    first hit is meant to be the child itself (wq#1284 pins its Popen kwargs).
    """
    roots, seen = [], set()
    for d in [cwd, *add_dirs]:
        if not d or d in seen:
            continue
        seen.add(d)
        try:
            p = runner(["git", "-C", d, "rev-parse", "--is-inside-work-tree"],
                       capture_output=True, timeout=GIT_REV_PARSE_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError):
            continue
        if p.returncode == 0:
            roots.append(d)
    return roots


def is_committed_literal(value, repo_roots, runner=subprocess.run):
    """True if `value` appears verbatim in a tracked file under any of `repo_roots`.

    Callers MUST have ruled the value out as a live secret first (classify does):
    this puts the value in a subprocess argv, which is exactly the shape instinct 4
    forbids for a real credential. That is also why the roots are tried in order and
    the first hit returns: for a value that IS accounted for, the extra roots cost
    nothing, and the argv exposure of an unaccounted value does not grow with how
    many directories the child was granted.

    `-e` marks the value as the pattern and `-- .` bounds the search to the granted
    directory, so a value is only excused by text the child could actually read.
    """
    for root in _as_roots(repo_roots):
        try:
            p = runner(["git", "-C", root, "grep", "--quiet", "--fixed-strings",
                        "-I", "-e", value, "--", "."],
                       capture_output=True, timeout=GIT_GREP_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError):
            continue
        if p.returncode == 0:
            return True
    return False


def _is_accounted(match, repo_roots, runner):
    """`match` whole, else its body when it carries one long enough to mean something.

    The body lookup adds no argv exposure: the body is a substring of the whole match
    that was just put there. Neither does the last resort, the body minus a trailing
    `...` (masking.untruncated, wq#2648): a printer that shortened a fixture, retried
    with the rule's prefix attached when the cut body alone is under the floor
    (masking.untruncated_whole, wq#2663).
    """
    if is_committed_literal(match, repo_roots, runner=runner):
        return True
    body = getattr(match, "body", "")
    if (len(body) >= MIN_SECRET_LEN and body != match
            and is_committed_literal(body, repo_roots, runner=runner)):
        return True
    cut = masking.untruncated(body or match, MIN_SECRET_LEN)
    if not cut and body and match.endswith(body):
        cut = masking.untruncated_whole(match[:-len(body)], body, MIN_SECRET_LEN)
    return bool(cut) and is_committed_literal(cut, repo_roots, runner=runner)


def classify(detail, live, repo_roots, runner=subprocess.run, cache=None):
    """Split mask labels into (burned, benign).

    `detail` is matched_values()'s {label: [matched strings]}. A label is
    burned if ANY of its matches is live or unaccounted for — one real token in a
    line of otherwise harmless text is still a burn.

    A match not found whole is retried as its body (wq#2415). The house mitigation for
    a fixture that arms the tripwire is to split its literal at the prefix seam
    (`"sk-ant-" + "oat01-..."`), which keeps the source unmatched but leaves no
    verbatim whole match in the tree — while a `git log` still prints the revision that
    held it whole. Only the body is on disk, and mask_replay/mask_triage already
    account the body for the same reason. The retry is bounded below by MIN_SECRET_LEN
    so a short body cannot excuse a match by coincidence, and it runs after the live
    check, so it never gets the chance to excuse a credential the child was handed.
    """
    cache = {} if cache is None else cache
    burned, benign = [], []
    for label, matches in detail.items():
        if not matches:
            continue
        if any(secret in match for match in matches for secret in live):
            burned.append(label)
            continue
        accounted = True
        for match in matches:
            if match not in cache:
                cache[match] = _is_accounted(match, repo_roots, runner)
            if not cache[match]:
                accounted = False
                break
        (benign if accounted else burned).append(label)
    return burned, benign


_SELF = live_code.fingerprint(__file__)

# Not credential-shaped on purpose (masking.py:136): a real-looking literal in this
# file would be read as a burn by the tripwire that reads this file.
_CANARY_PREFIX = "burn-filter-canary-"
_CANARY = _CANARY_PREFIX + "0" * 12


def _still_screens(mod):
    """A deployed screen must still call a live secret burned.

    The whole value of reloading here is DROPPING alarms, so the failure this guard
    has to catch is a screen that drops all of them — an emptied classify, a
    half-written deploy. Passing the canary as `live` short-circuits ahead of
    is_committed_literal, so the self-test costs no subprocess.

    wq#2415 widened what counts as committed text (a match is also accounted by its
    body), and a wider tree is a wider excuse: the control has to be re-planted and
    re-run over the widened screen (ops/wq2359-rotation-triage/README.md:68-75) or a
    screen that excuses everything reads as a screen that found nothing burned. So
    the same canary is also run through the body retry with an in-process runner:
    with nothing committed it must still burn, and with only the body committed it
    must clear — the second half is what proves the first is not a screen that
    burns everything. wq#2648's truncation retry is re-planted the same way, with the
    canary printed cut short.
    """
    burned, _ = mod.classify({"canary": [_CANARY]}, {_CANARY}, [])
    if burned != ["canary"]:
        return False
    body = _CANARY[len(_CANARY_PREFIX):]
    for hit in (mod.Hit(_CANARY, body), mod.Hit(_CANARY + "...", body + "...")):
        for committed, want in ((set(), ["canary"]), ({body}, [])):
            def runner(argv, committed=committed, **_):
                return types.SimpleNamespace(
                    returncode=0 if argv[argv.index("-e") + 1] in committed else 1)
            if mod.classify({"canary": [hit]}, set(), ["/"], runner=runner)[0] != want:
                return False
    return True


def deployed(path=None):
    """This screen as its FILE defines it right now (wq#2404, live_code).

    The twin of masking.deployed(), for the other half of the alarm path. wq#2327
    made the rule TABLE reload per call; the provenance screen kept freezing at spawn,
    so the two fixes that taught it what a benign hit looks like — 281327c (screen
    against every granted repo root) at 23:09:47Z and be9994d at 23:44:19Z — reached
    no run then in flight. wq#2348, filed 23:01:44Z by a process that had imported
    this module at 22:14:43Z, is what that costs: ten values, every one of them
    committed text this screen now accounts for, filed as a pri-1 rotate-today row.

    Used one-directionally at the call site (fworker.screen_mask_hits): a label stays
    burned only if the deployed screen agrees, so a newer screen can quiet an alarm
    but never raise one, and a screen that fails `_still_screens` quiets nothing.
    """
    return live_code.deployed(sys.modules[__name__], _SELF, _still_screens, path)
