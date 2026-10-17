"""wq#4091 -- prompt-injection detection for strings recovered from a target binary.

Source: ``design/wq3999_rex_skill_dissection.md`` section 3, flagged there as the
TOP import (the dissection guide's priority call,
``design/wq3999_rex_skill_dissection.md:79``) and as a gap: a fleet grep found no
quarantine/sanitize/injection-detection primitive anywhere in ``limpet/`` before this
ticket. REx@Skill's own framing (``REx:scripts/injection.py:16-18``, quoted in the
dissection) is the threat model this module answers: a string constant the target's
author controls -- a recovered string, a symbol/function name, a banner, a log line
-- is a channel straight into the analysing agent's context. wq#3806's untrusted-input
hygiene (``design/wq3806_limpet_architecture.md:247-250``) stops a planted ``json.py``
from *running*; this module stops a planted sentence from being *believed*. Those are
different attacks and need different defenses.

Two independently-sourced inputs went into this module: the dissection's prose
description of REx's two-layer mechanism and its cited literal phrases (both quoted
above each pattern list below), and this fleet's existing credential-masking primitive
(``workspace_report.mask_text``/``_mask_tree``), whose "walk every string in the IR
structurally, replace the whole value with a stable token" shape this module reuses
for consistency -- the two defenses should feel like one system, not two. The upstream
``tihanyin/REx-skill`` repo itself was not reachable this session (``gh api`` and
``WebFetch`` both require interactive approval this worker cannot give itself, which is
the refused-command class the fleet's own CLAUDE.md tells a worker not to retry around;
see ISSUE-LOG), so the STRONG layer below is a direct, deliberately close rendering of
the dissection's cited literal examples and the CUE vocabulary is this module's own
equivalent of "weaker analysis vocabulary", not a byte-for-byte port of REx's word list.

Two layers, same asymmetry REx documents (``design/wq3999_rex_skill_dissection.md:105-107``):
every plausible injection argues toward a false *negative* -- "report clean, move on" --
so this module is tuned aggressively in that one direction. A redacted ordinary string
costs a little context; a leaked injection costs the integrity of the run
(``...dissection_guide.md:108-109``).

  STRONG  model-directed phrasing, matched anywhere in a string regardless of length.
          Verdict/status forgery, maintenance-mode / system-prompt impersonation, and
          direct meta-instructions ("ignore previous", "suppress all findings") are the
          shapes that most directly try to overwrite an agent's own judgement.
  CUE     weaker reassurance/dismissal vocabulary ("false positive", "already patched",
          "nothing to report", ...), matched **only** on prose-shaped strings: >=32
          chars and containing no printf conversion specifier
          (``_FORMAT_SPEC_RE`` below). That guard is load-bearing, not decorative --
          without it, ordinary program output like ``"[audit] false positive rate:
          %0.2f%%"`` would false-positive on every build that logs its own QA metrics.

A clean ``classify()`` result means "none of the known shapes", never "no injected
text is present" -- the pattern sets are a starting set, honestly incomplete
(``...dissection_guide.md:110-112``), and are meant to grow as new shapes are found.

Pure and filesystem-free, like ``manifest.py``'s parser: no I/O, no workspace
argument, just text in and a classification out. ``sanitize.py`` is the filesystem-
walking caller that applies this to a workspace's evidence tree.
"""
from __future__ import annotations

import re

REDACTION_TOKEN = "<<REDACTED-UNTRUSTED-EMBEDDED-STRING>>"

LAYER_STRONG = "strong"
LAYER_CUE = "cue"

MIN_PROSE_LEN = 32

# Layer 1 -- model-directed phrasings, matched anywhere regardless of length.
# Literal examples straight from the dissection (verdict=NOT_VULNERABLE, "SYSTEM:
# maintenance mode", "suppress all findings", IGNORE PREVIOUS, status:clean, P(safe)=,
# tool_result=), each loosened only enough to survive whitespace/case variation a
# planted string would still read as the same instruction (e.g. "Verdict : Not
# Vulnerable") -- not loosened enough to drift onto unrelated prose.
_STRONG_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r"verdict\s*[=:]\s*not[_ -]?vulnerable",
    r"system\s*:\s*maintenance\s*mode",
    r"suppress\s+all\s+findings",
    r"ignore\s+(?:all\s+)?previous\s+(?:instructions?|findings?|context)?",
    r"status\s*[=:]\s*clean\b",
    r"p\(\s*safe\s*\)\s*=",
    r"tool_result\s*=",
)]

# Layer 2 -- weaker analysis-dismissal vocabulary. Gated by MIN_PROSE_LEN + the
# format-specifier guard below so ordinary program strings don't trip it.
_CUE_WORDS = (
    "false positive",
    "already patched",
    "previously reviewed and approved",
    "nothing to report",
    "no vulnerability found",
    "not exploitable",
    "safe to ignore",
    "benign and expected",
    "pre-approved by security",
    "do not flag this",
    "this is expected behavior",
    "automated scan exempt",
    "whitelisted by the security team",
)
_CUE_PATTERNS = [re.compile(re.escape(w), re.IGNORECASE) for w in _CUE_WORDS]

# A standard printf/scanf conversion specifier -- its presence means the string is
# (or quotes) a format string, not free-form prose, so the CUE layer skips it.
_FORMAT_SPEC_RE = re.compile(r"%[-+0 #]*\d*(?:\.\d+)?[hlLqjzt]{0,2}[diouxXeEfFgGaAcspn%]")


def _is_prose_shaped(text: str) -> bool:
    return len(text) >= MIN_PROSE_LEN and not _FORMAT_SPEC_RE.search(text)


def classify(text: str) -> str | None:
    """Return ``LAYER_STRONG``/``LAYER_CUE`` if ``text`` matches a known injection
    shape, else ``None``. Pure: no I/O, no side effects.
    """
    if not isinstance(text, str) or not text:
        return None
    for pat in _STRONG_PATTERNS:
        if pat.search(text):
            return LAYER_STRONG
    if _is_prose_shaped(text):
        for pat in _CUE_PATTERNS:
            if pat.search(text):
                return LAYER_CUE
    return None


def is_injection(text: str) -> bool:
    """True iff ``text`` matches a known model-directed-injection shape."""
    return classify(text) is not None
