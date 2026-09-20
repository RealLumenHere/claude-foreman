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
2. Otherwise, does the matched text appear verbatim in the repo's tracked source?
   Then it is committed text the reader was entitled to see, not a runtime secret.

Anything else is unknown provenance and alarms. The default is to alarm: a value we
cannot account for is treated as burned.

Masking itself is unchanged — artefacts stay redacted either way. This only gates
the ntfy page and the rotation ticket.
"""
import re
import subprocess

import masking

# Env var names that carry a credential VALUE. Same vocabulary as masking's
# kv-secret pattern, matched against the variable name rather than the line.
SECRET_NAME_RE = re.compile(r"TOKEN|KEY|SECRET|PASSWORD|PASSWD|PAT", re.IGNORECASE)

# Values under a secret-shaped name that are still not credentials: a path to the
# file holding one (FOREMAN_LITELLM_KEY_FILE), a base URL, a flag.
_NON_SECRET_PREFIXES = ("/", "~", "./", "http://", "https://")

MIN_SECRET_LEN = 12
GIT_GREP_TIMEOUT_S = 20


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
            out.setdefault(label, []).append(m.group(0))
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


def is_committed_literal(value, repo_root, runner=subprocess.run):
    """True if `value` appears verbatim in a tracked file under repo_root.

    Callers MUST have ruled the value out as a live secret first (classify does):
    this puts the value in a subprocess argv, which is exactly the shape instinct 4
    forbids for a real credential.
    """
    if not repo_root:
        return False
    try:
        p = runner(["git", "-C", repo_root, "grep", "--quiet", "--fixed-strings",
                    "-I", "--", value],
                   capture_output=True, timeout=GIT_GREP_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return False
    return p.returncode == 0


def classify(detail, live, repo_root, runner=subprocess.run, cache=None):
    """Split mask labels into (burned, benign).

    `detail` is matched_values()'s {label: [matched strings]}. A label is
    burned if ANY of its matches is live or unaccounted for — one real token in a
    line of otherwise harmless text is still a burn.
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
                cache[match] = is_committed_literal(match, repo_root, runner=runner)
            if not cache[match]:
                accounted = False
                break
        (benign if accounted else burned).append(label)
    return burned, benign
