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

The patterns are a verbatim port of /home/lumen/bin/mask-secrets (the instinct-4
house set, 6 sed expressions). Keep the two in lockstep: a pattern added there
must be added here the same day.

Masking operates on the raw NDJSON line, not the parsed frame: the replacement
alphabet ([A-Za-z0-9._-] and the literal MASKED) contains no JSON metacharacters,
so a masked line parses exactly like the original.
"""
import re

# (label, compiled pattern, replacement) — labels feed the alarm/rotation ticket.
PATTERNS = [
    ("github", re.compile(r"(ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{6,}"),
     r"\1MASKED"),
    ("cloudflare", re.compile(r"(cfut_?)[A-Za-z0-9_.-]{6,}"), r"\1MASKED"),
    ("sk-key", re.compile(r"(sk-[a-zA-Z-]*)[A-Za-z0-9_-]{16,}"), r"\1MASKED"),
    ("slack", re.compile(r"(xox[bpasr]-)[A-Za-z0-9-]{8,}"), r"\1MASKED"),
    ("aws", re.compile(r"(AKIA)[0-9A-Z]{16}"), r"\1MASKED"),
    ("kv-secret", re.compile(
        r'((TOKEN|KEY|SECRET|PASSWORD|PASSWD|PAT)["\s]*[:=]["\s]*)[A-Za-z0-9._-]{12,}',
        re.IGNORECASE), r"\1MASKED"),
]


def mask_text(text):
    """(masked_text, [labels that fired]). Order of labels follows PATTERNS."""
    hits = []
    for label, rx, repl in PATTERNS:
        text, n = rx.subn(repl, text)
        if n:
            hits.append(label)
    return text, hits
