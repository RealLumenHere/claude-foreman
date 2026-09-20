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
word boundary below is spelled `(^|[^A-Za-z0-9])` with a shifted capture group
there and `(?<![A-Za-z0-9])` here. Same language, different syntax. kv-secret is
also narrower here until wq#2313 carries its tail lookahead across, which costs the
sed copy a false alarm, never a missed mask.

Masking operates on the raw NDJSON line, not the parsed frame: the replacement
alphabet ([A-Za-z0-9._-] and the literal MASKED) contains no JSON metacharacters,
so a masked line parses exactly like the original.
"""
import hashlib
import re

# wq#2292: a prefix only counts at a word boundary. Without `(?<![A-Za-z0-9])` the
# `ghs_` in the repo name `highs_solver` reads as a token, and since the alarm is
# pri-1-and-rotate that false positive is expensive: it cost a whole rotation ticket
# on wq#2282, which had merely read the ISSUE-LOG row documenting the identical bug.
# remote_credential_sweep.py:38-43 took the same boundary for the same reason
# (wq#2263). The boundary never hides a real token — a credential is a standalone
# value, so what precedes it is `"`, `:`, `=`, `/` or space, never an alphanumeric.
_B = r"(?<![A-Za-z0-9])"

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
    ("sk-key", re.compile(_B + r"(sk-[a-zA-Z-]*)[A-Za-z0-9_-]{16,}"), r"\1MASKED"),
    ("slack", re.compile(_B + r"(xox[bpasr]-)[A-Za-z0-9-]{8,}"), r"\1MASKED"),
    ("aws", re.compile(_B + r"(AKIA)[0-9A-Z]{16}"), r"\1MASKED"),
    # No boundary here on purpose: this one keys off an English word, not a prefix,
    # and `(?<![A-Za-z0-9])` would stop matching the camelCase leak shapes that are
    # the whole point of it (`githubToken: ...`, `apiKey = ...`). The boundary it
    # does need is a TRAILING one (wq#2288): without it `key = apply_key_allowlist(reg)`
    # (node_render_gateway.py:418) reads as a 19-char secret, which is what fired the
    # tripwire on wq#2284 and cost another pri-1 rotate-today ticket. A value that is
    # immediately called is a function, never a credential — and the charset half of
    # the lookahead is what makes the `\(` half hold, since a greedy run would
    # otherwise just backtrack one character and match anyway.

    ("kv-secret", re.compile(
        r'((TOKEN|KEY|SECRET|PASSWORD|PASSWD|PAT)["\s]*[:=]["\s]*)'
        r'[A-Za-z0-9._-]{12,}(?![A-Za-z0-9._-]|\()',
        re.IGNORECASE), r"\1MASKED"),
]


CONTEXT_CHARS = 120   # masked context kept either side of a hit
PREFIX_CHARS = 48     # of the kept, non-secret prefix


def mask_text(text):
    """(masked_text, [labels that fired]). Order of labels follows PATTERNS."""
    hits = []
    for label, rx, repl in PATTERNS:
        text, n = rx.subn(repl, text)
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
        for m in rx.finditer(cur):
            matches.append((label, m.group(1), m.group(0)[len(m.group(1)):]))
        cur = rx.sub(repl, cur)

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
