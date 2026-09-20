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
there and `(?<![A-Za-z0-9])` here. Same language, different syntax.

Masking operates on the raw NDJSON line, not the parsed frame: the replacement
alphabet ([A-Za-z0-9._-] and the literal MASKED) contains no JSON metacharacters,
so a masked line parses exactly like the original.
"""
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
    # the whole point of it (`githubToken: ...`, `apiKey = ...`).
    ("kv-secret", re.compile(
        r'((TOKEN|KEY|SECRET|PASSWORD|PASSWD|PAT)["\s]*[:=]["\s]*)[A-Za-z0-9._-]{12,}',
        re.IGNORECASE), r"\1MASKED"),
]


def mask_text(text):
    """(masked_text, [labels that fired]). Order of labels follows PATTERNS."""
    hits = []
    for label, rx, repl in PATTERNS:
        text, n = rx.subn(repl, text)
        if n and label not in hits:
            hits.append(label)
    return text, hits
