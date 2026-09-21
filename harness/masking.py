"""wq#2275 (token-engine T6, TOKEN_ENGINE_DESIGN.md §6): mask credentials in the
child's stream-json BEFORE anything is written to disk, ntfy, the episode store
or a PR body.

CLI 2.1.233 has no lever to rewrite a tool result before the model sees it
(research/SMARTCRUSHER_HOOK.md:22,40-54 — updatedOutput / updatedToolResult /
replaceOutput / toolResultOverride occur 0 times in the shipped binary; PostToolUse
only appends), so masking here cannot protect the model's context. It protects every
ARTEFACT fworker derives from the stream: raw_events -> attempt notes, episode.jsonl,
failure.json, ntfy bodies, PR text. A hit is therefore an ALARM, not a save
(instinct 4): the token already reached the model and is burned — the caller must
page ntfy and open a rotation ticket (fworker.mask_alarm).

The patterns mirror /home/lumen/bin/mask-secrets (the instinct-4 house set). Keep
the two in lockstep: a pattern added there must be added here the same day. They
are no longer character-identical — sed -E is POSIX ERE with no lookbehind, so the
word boundary below is spelled `(^|[^A-Za-z0-9_])` with a shifted capture group
there and `(?<![A-Za-z0-9_])` here. Same language, different syntax. kv-secret is
also narrower here until wq#2313 carries its tail lookahead across, which costs the
sed copy a false alarm, never a missed mask.

Masking operates on the raw NDJSON line, not the parsed frame: the replacement
alphabet ([A-Za-z0-9._-] and the literal MASKED) contains no JSON metacharacters,
so a masked line parses exactly like the original.
"""
import hashlib
import importlib.util
import os
import re
import sys

# wq#2292: a prefix only counts at a word boundary. Without `(?<![A-Za-z0-9])` the
# `ghs_` in the repo name `highs_solver` reads as a token, and since the alarm is
# pri-1-and-rotate that false positive is expensive: it cost a whole rotation ticket
# on wq#2282, which had merely read the ISSUE-LOG row documenting the identical bug.
# remote_credential_sweep.py:38-43 took the same boundary for the same reason
# (wq#2263). The boundary never hides a real token — a credential is a standalone
# value, so what precedes it is `"`, `:`, `=`, `/`, `-` or space, never an
# alphanumeric and never `_`.
#
# wq#2400: `_` belongs in that class, and leaving it out kept the SAME false positive
# one character away. `see notes_on_xoxb-<two english words>` — a line in wq#2316's own
# draft of ops/wq2316-mask-secrets-boundary/test_mask_secrets_boundary.py, in the
# parametrize list asserting that prefixes inside a word are untouched — still read as
# a slack token, armed the pri-1 alarm and filed this ticket. `_` never separates a
# credential from what precedes it: it only continues an identifier, so a prefix glued
# to one is the tail of a NAME (`notes_on_xoxb-`, `my_cfut_settings`), and a value that
# really is labelled by that name (`SLACK_TOKEN=xoxb-…`) is caught by kv-secret below,
# whose label half does not read `_B` at all.
_B = r"(?<![A-Za-z0-9_])"

# wq#2297 kv-secret label vocabulary, ported from read_dir_sweep.py:38-60 (wq#2115).
# A credential label is a NOUN, optionally qualified: `api key`, `Signing Secret`,
# `clientSecret`. The qualifier ends in a separator or a camelCase hump.
_KV_NOUN = r"(?:secret|token|key|credentials?|passphrase|password|passwd)"
# The hump test is case-SENSITIVE via `(?-i:)` even though the rule as a whole is
# IGNORECASE — that asymmetry is the whole trick: it admits `apiKey:` while refusing
# `monkey:`. Two letters minimum, since a one-letter qualifier is an English article
# ("a key: ..." is prose, not a leak).
_KV_WORD = r"[A-Za-z][A-Za-z0-9]{1,20}(?:[ _-]{1,2}|(?-i:(?=[A-Z])))"
# a public key is not a credential, and infra dirs are full of them
_KV_NOT_PUBLIC = r"(?!pub(?:lic)?[ _-]{0,2}key\b)"
# Nouns unambiguous enough to stand alone. `key` and `pat` are deliberately absent:
# `key:` alone is ordinary YAML and an ordinary Python kwarg, and `PAT` alone is a
# variable name — both need a qualifier in front to read as a credential label. The
# `github_pat_` credential itself is caught by the dedicated github pattern above.
_KV_BARE = r"(?:secret|token|credentials?|passphrase|password|passwd|bearer)"
_KV_LABEL = (r"(?:" + _KV_WORD + r")?" + _KV_NOT_PUBLIC + _KV_WORD + _KV_NOUN
             + r"|" + _KV_BARE)
# An env-var reference or a path is a POINTER to a credential, not the credential:
# `secret = os.environ["X"]`, `master key: /home/lumen/.ssh/id_ed25519`. Masking the
# pointer would hide the leak path the scan record exists to show.
_KV_POINTER = (r"(?!os\.environ)(?!os\.getenv)(?!(?:process\.)?env\.)"
               r"(?!\$\{?[A-Z_])(?![~.]{0,2}/)(?!<)")

# (label, compiled pattern, replacement) — labels feed the alarm/rotation ticket.
# Two labels may repeat (github); mask_text() de-duplicates.
PATTERNS = [
    # Real bodies are fixed-width: gh?_ 36 chars of base62, github_pat_ 82 with
    # underscores. The old {6,} floor also matched this module's OWN output
    # (`ghp_MASKED` is 6 legal chars), so every already-masked artefact re-armed
    # the alarm on the next read — the floors below end that loop too.
    ("github", re.compile(_B + r"(ghp_|gho_|ghu_|ghs_|ghr_)[A-Za-z0-9]{30,}"),
     r"\1MASKED"),
    ("github", re.compile(_B + r"(github_pat_)[A-Za-z0-9_]{20,}"), r"\1MASKED"),
    ("cloudflare", re.compile(_B + r"(cfut_?)[A-Za-z0-9_.-]{6,}"), r"\1MASKED"),
    # wq#2300: the prefix repeats WHOLE dash-terminated letter groups (`sk-`,
    # `sk-ant-`, `sk-or-v1-`) instead of a greedy `[a-zA-Z-]*`. Greedy, group 1 ran
    # as far as the `{16,}` body would allow, so a body opening with a long letter
    # run was partly kept: `sk-ant-` + 24 letters masked to `sk-ant-xxxxxxxxMASKED`,
    # printing 8 characters of the secret into the artefact. The same 8 went into
    # scan()'s `prefix` — the field contracted to hold no value — and left
    # `value_sha256_12` computed over a truncated body, so the "sha256 a candidate
    # and compare" instruction every rotation ticket carries could never match.
    ("sk-key", re.compile(_B + r"(sk-(?:[a-zA-Z]+-)*)[A-Za-z0-9_-]{16,}"),
     r"\1MASKED"),
    ("slack", re.compile(_B + r"(xox[bpasr]-)[A-Za-z0-9-]{8,}"), r"\1MASKED"),
    ("aws", re.compile(_B + r"(AKIA)[0-9A-Z]{16}"), r"\1MASKED"),
    # This one keys off an English word rather than a prefix, so it needs a different
    # boundary than `_B`. It needs a TRAILING one (wq#2288): without it
    # `key = apply_key_allowlist(reg)` (node_render_gateway.py:418) reads as a 19-char
    # secret. A value that is immediately called is a function, never a credential —
    # and the charset half of the lookahead is what makes the `\(` half hold, since a
    # greedy run would otherwise just backtrack one character and match anyway.
    #
    # wq#2297: the trailing boundary alone was not enough, because the LABEL half was a
    # naked keyword alternation with no left boundary. `KEY` matched inside `monkey:`,
    # and bare `KEY`/`PAT` matched the ordinary Python of `key = apply_key_allowlist(`,
    # `FAKE_PAT = "..."` and `top = max(v, key=v.count)`. Reading source code therefore
    # armed a pri-1 rotate-today alarm: 46 lines of harness/ alone matched, and the
    # cascade cost four tickets (wq#2288 on wq#2284, wq#2292 on wq#2282, wq#2297 on
    # wq#2290 — none of them a real credential).
    #
    # The label below is shape-based instead, ported from read_dir_sweep.py:38-60,
    # which solved this for the at-rest sweep (wq#2115, after an exact keyword list
    # missed 3 live credentials in wq#1483). The value floor stays 12, not that
    # sweep's 20: this rule also REDACTS, and over-masking an artefact is free while
    # under-masking one is not. Precision belongs in the label, not the floor.
    #
    # The sed copy in bin/mask-secrets cannot follow: POSIX ERE has no lookaround, so
    # it can express none of this. It has already missed both earlier fixes and is the
    # tool instinct 4 tells every agent to pipe through (ISSUE-LOG row 577).
    ("kv-secret", re.compile(
        r'\b((?:' + _KV_LABEL + r')\b["\s]*[:=]["\s]*)'
        + _KV_POINTER +
        r'[A-Za-z0-9._-]{12,}(?![A-Za-z0-9._-]|\()',
        re.IGNORECASE), r"\1MASKED"),
]


CONTEXT_CHARS = 120   # masked context kept either side of a hit
PREFIX_CHARS = 48     # of the kept, non-secret prefix

MASK = "MASKED"       # the literal every replacement ends in

# Built by concatenation on purpose: a literal `AKIA`+16 in this file would be a
# credential-shaped string in tracked source, which is the exact class of text that
# filed half the rotation backlog (wq#2299, tests/test_token_injection.py).
_CANARY = "AKIA" + "Q" * 16

try:
    _st = os.stat(__file__)
    _SELF = (os.path.abspath(__file__), _st.st_mtime_ns, _st.st_size)
except OSError:      # pragma: no cover - source not on disk (zipimport, -c)
    _SELF = None

_DEPLOYED = {}


def deployed(path=None):
    """This module as its FILE defines it right now, not as the caller imported it.

    wq#2327: an fworker process holds one attempt for up to two hours and scores
    every line of that attempt's stream with the rule table it imported at spawn.
    So a rule narrowed to kill a false positive does not reach the attempts already
    running — they keep paging pri-1 and filing rotate-today tickets under the old
    table for the rest of their life. That is most of the 2026-09-20 storm's tail,
    dated: wq#2297's attempt started 22:37:43, and it filed wq#2327 (22:44:02),
    wq#2356 (23:11:43) and wq#2366 (23:25:49) — all three after the chain guard
    (345bf49, 22:57:04) and the provenance screen (281327c, 23:09:47) had landed,
    all three carrying the pre-22:40 ticket template, and none of them a credential.

    Reloading is deliberately one-directional at the call site (fworker only ever
    DROPS a label the deployed table no longer reads, never adds one), so the worst
    a stale or broken file can do is leave the caller on its own table.

    Two guards, because silencing the tripwire is worse than a false page:
    a fresh copy must execute, and it must still label `_CANARY`. Anything else —
    a half-written deploy, an emptied PATTERNS — falls back to the imported module.
    """
    path = os.path.abspath(path or __file__)
    try:
        st = os.stat(path)
    except OSError:
        return sys.modules[__name__]
    key = (path, st.st_mtime_ns, st.st_size)
    if key == _SELF:
        return sys.modules[__name__]          # unchanged since import; nothing to load
    hit = _DEPLOYED.get(path)
    if hit and hit[0] == key:
        return hit[1]
    try:
        spec = importlib.util.spec_from_file_location(f"{__name__}_deployed", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not mod.mask_text(_CANARY)[1]:
            raise ValueError(f"{path} no longer masks the canary")
    except Exception:  # noqa: BLE001 - any failure means keep the imported table
        return sys.modules[__name__]
    _DEPLOYED[path] = (key, mod)
    return mod


def _matches(rx, text):
    """Matches of `rx`, minus this module's own output read back (wq#2298).

    Every replacement is `<kept prefix>MASKED`, and that output is itself
    credential-shaped to several of the rules above: `cfut_MASKED` is `cfut_` plus
    six legal body characters, `PAT: github_pat_MASKED` is a 17-char kv value, and
    `sk-ant-xxxxxxxxMASKED` re-matches because the sk- prefix backtracks into its
    own body. So a masked artefact re-armed the pri-1 alarm on every later read,
    and the rotation ticket it filed was answered by a worker who grepped for
    `ghp_MASKED` — which alarmed again. That loop cost wq#2282, #2288, #2292 and
    #2298; the floors raised in wq#2292 only ended it for the two github rules.

    A match that ENDS at the mask literal is our own output, never a secret: the
    value is already gone. The cost is a real credential whose last six characters
    are exactly `MASKED` (1 in 62**6 for a random token body), paid once, against
    an alarm loop that fires forever and pages at pri-1 every time.
    """
    return [m for m in rx.finditer(text) if not m.group(0).endswith(MASK)]


def _sub(rx, repl, text):
    """(text, n) — rx.subn() over _matches() only, so mask_text() and scan() agree
    on which matches are real without either re-deriving the rule."""
    out, pos = [], 0
    matches = _matches(rx, text)
    for m in matches:
        out.append(text[pos:m.start()])
        out.append(m.expand(repl))
        pos = m.end()
    out.append(text[pos:])
    return "".join(out), len(matches)


def mask_text(text):
    """(masked_text, [labels that fired]). Order of labels follows PATTERNS."""
    hits = []
    for label, rx, repl in PATTERNS:
        text, n = _sub(rx, repl, text)
        if n and label not in hits:
            hits.append(label)
    return text, hits


def scan(text):
    """(masked_text, [hit record]) — wq#2288. mask_text() answers "did a credential
    print?"; scan() answers "WHICH one?", which is what a rotation ticket needs and
    what the first tripwire ticket (wq#2288, fired on wq#2284) could not answer: the
    labels were all that ever reached disk, so nothing in the run dir identified the
    burned value.

    A record carries the kept prefix (the part the mask leaves — a key name, `ghp_`,
    `AKIA`), the value's length, and 12 hex of its sha256: enough to NAME the
    credential by hashing a candidate, never enough to use it. `context` is a window
    of the FULLY masked line, so it shows the command or file the value printed from
    — the leak path — with every pattern in the window already replaced. The window is
    cut after masking, never before: cutting first is what let a token escape the
    replace in wq#1441 (see tests/test_read_dir_sweep.py).
    """
    matches, cur = [], text
    for label, rx, repl in PATTERNS:
        # finditer then sub on the same string, pattern by pattern, so the records
        # follow exactly the sequence of substitutions mask_text() performs: a value
        # an earlier pattern already shortened to `<prefix>MASKED` no longer matches
        # a later one, and must not be recorded as if it had.
        for m in _matches(rx, cur):
            matches.append((label, m.group(1), m.group(0)[len(m.group(1)):]))
        cur = _sub(rx, repl, cur)[0]

    records, cursor = [], {}
    for label, prefix, value in matches:
        marker = prefix + "MASKED"
        at = cur.find(marker, cursor.get(marker, 0))
        if at < 0:
            context = ""   # a later pattern reshaped the prefix; degrade, never leak
        else:
            cursor[marker] = at + len(marker)
            context = cur[max(0, at - CONTEXT_CHARS):at + len(marker) + CONTEXT_CHARS]
        records.append({
            "label": label,
            "prefix": prefix[-PREFIX_CHARS:],
            "value_len": len(value),
            "value_sha256_12": hashlib.sha256(
                value.encode("utf-8", "replace")).hexdigest()[:12],
            "context": context,
        })
    return cur, records
