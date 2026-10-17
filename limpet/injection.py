"""wq#4091 -- prompt-injection detector for strings recovered from a target binary.

Ported from REx@Skill's ``scripts/injection.py`` two-layer detector, dissected in
``design/wq3999_rex_skill_dissection.md`` §3 (quoted literals below are taken from
that dissection's verbatim examples, our grounding for the actual REx source --
the clone itself was read in an isolated scratch dir during that session and is
not present in this worktree). The threat model: a string constant embedded in
the target binary (a banner, an error message, a decompiled literal) is a
channel an attacker controls straight into the analysing agent's context. The
agent must read recovered strings to do its job, so the defense is a detector
that runs *before* the agent ever sees them (``sanitize.py``), not a rule asking
the agent to be careful.

Two layers, both tuned toward over-detection -- REx's asymmetry
(dissection §3): every real injection argues toward a false *negative*
("report clean"), so the detector can be aggressive in exactly one direction.
A legitimate program string redacted by mistake costs a little context; an
injected string that reaches the agent costs the integrity of the whole run.

  STRONG  matches model-directed phrasing anywhere in a string, regardless of
          length: a forged verdict/status line, an instruction override, a
          forged tool_result/system block.
  CUE     matches weaker analysis vocabulary, but ONLY on strings shaped like
          prose -- length >= MIN_PROSE_LEN and no printf conversion specifier
          -- which is what keeps ordinary program output like
          "[audit] checksum=%u" or a short format tag out of the net.

A clean scan means "none of the known shapes matched", never "no injected text
exists": the pattern set is a starting set, not a proof (REx's own caveat,
dissection §3 point 3).
"""
from __future__ import annotations

import re

LAYER_STRONG = "strong"
LAYER_CUE = "cue"

MIN_PROSE_LEN = 32

_STRONG_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r"ignore\s+(all\s+)?(the\s+)?previous\s+instructions",
    r"disregard\s+(all\s+)?(the\s+)?(above|prior|previous)",
    r"verdict\s*[=:]\s*not[_\s-]?vulnerable",
    r"status\s*[=:]\s*clean\b",
    r"system\s*:\s*maintenance\s+mode",
    r"suppress\s+(all\s+)?findings",
    r"p\s*\(\s*safe\s*\)\s*=",
    r"tool_result\s*=",
    r"you\s+are\s+now\s+in\s+(debug|maintenance|admin)\s+mode",
    r"this\s+(binary|file|sample)\s+is\s+(safe|not\s+vulnerable|benign)",
)]

_CUE_WORDS = [re.compile(p, re.IGNORECASE) for p in (
    r"\bas\s+an\s+ai\b",
    r"\bdo\s+not\s+(report|flag|log)\s+this\b",
    r"\bnothing\s+to\s+report\b",
    r"\banalysis\s+complete[,.:]?\s+no\s+(issues|findings)",
    r"\btrust\s+me[,.:]",
    r"\bstop\s+analyz(e|ing)\s+(this|here)\b",
)]

_PRINTF_SPEC = re.compile(r"%[-+ 0#]*\d*(\.\d+)?[hlLqjzt]*[diouxXeEfFgGaAcspn%]")


def _is_prose_shaped(s: str) -> bool:
    return len(s) >= MIN_PROSE_LEN and not _PRINTF_SPEC.search(s)


def scan(s: str) -> list:
    """Return every pattern hit in ``s``, sorted by position. Empty if clean."""
    hits = []
    for pat in _STRONG_PATTERNS:
        for m in pat.finditer(s):
            hits.append({"layer": LAYER_STRONG, "pattern": pat.pattern, "span": m.span()})
    if _is_prose_shaped(s):
        for pat in _CUE_WORDS:
            for m in pat.finditer(s):
                hits.append({"layer": LAYER_CUE, "pattern": pat.pattern, "span": m.span()})
    hits.sort(key=lambda h: h["span"][0])
    return hits


def is_injection(s: str) -> bool:
    """Pure predicate: does ``s`` match any known injection shape?"""
    if not isinstance(s, str) or not s:
        return False
    return bool(scan(s))
