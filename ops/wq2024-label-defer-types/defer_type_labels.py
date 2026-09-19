#!/usr/bin/env python3
"""defer_type_labels.py — classify a deferred minerva proposal's decision_reason into
one of Frank's 7 TYPE labels (wq#2024), so his review queue can filter to only the
kind he actually needs to look at: 'defer-needs-decision'.

Standalone, dependency-free pure function. Designed to be called from
minerva_defer_review.py's cmd_review (research/DEFER_PILOT_LANE.md's ordered checks:
SETTLED -> PILOT_SHAPED -> INFO_GAP -> stale) right after a row is decided 'defer', and
from backfill_defer_labels.py for the existing pile. See README.md for exact call sites
and why this file lives here instead of in the minerva repo (sandbox reach, wq#2024
ISSUE-LOG entry).
"""
import re

LABELS = (
    "defer-needs-decision",   # Frank should review — waiting on his call
    "defer-dep-blocked",      # waiting on another proposal/ticket/module to land
    "defer-duplicate-suspect",  # flagged as overlapping existing work — VERIFY, don't trust
    "defer-empirical",        # needs a benchmark/pilot to decide -> PILOT lane
    "defer-info-gap",         # uncertain, missing info -> RESEARCH prompt
    "defer-needs-hardware",   # infeasible on hardware as stated — re-check vs current fleet
    "defer-theoretical",      # paper/academic/unproven/needs adaptation — Frank skips these
)

# Order is the classification priority, deliberately: the categories Frank must see
# (needs-decision, dep-blocked) are checked first so a reason that also happens to use
# hedgy/academic language never gets swallowed by 'defer-theoretical'. duplicate-suspect
# and needs-hardware are checked next because both carry a "VERIFY, don't trust" note in
# the ticket — putting them ahead of the two broad research/theory buckets means a defer
# that is BOTH "duplicate-looking" and "sounds unproven" still lands where it needs a
# second look, not where it gets silently skipped. 'defer-theoretical' is last: its
# phrasing ("promising but unproven", "requires adaptation") is the broadest and most
# likely to co-occur with a more specific, more actionable signal.

_NEEDS_DECISION = re.compile(
    r"frank|needs?\s+(a\s+)?decision|design\s+(call|decision)|waiting\s+on\s+(a\s+)?(frank|human|owner)|"
    r"needs?\s+(a\s+)?(product|policy|architecture)\s+call|your\s+call|human\s+judg",
    re.I,
)

_DEP_BLOCKED = re.compile(
    r"blocked\s+(on|by)|depends?\s+on|waiting\s+(on|for)\s+(wq#?\d+|proposal|ticket|module|another)|"
    r"prereq|has\s+not\s+landed|not\s+yet\s+(built|shipped|merged)|needs\s+wq#?\d+\s+first",
    re.I,
)

_DUPLICATE_SUSPECT = re.compile(
    r"duplicate|overlaps?\s+(with\s+)?(existing|another)|already\s+(covered|proposed|exists)|"
    r"same\s+as\s+(proposal|wq#?\d+)|redundant\s+with",
    re.I,
)

_EMPIRICAL = re.compile(
    r"needs?\s+a\s+(benchmark|pilot|measurement|trial)|benchmark\s+to\s+decide|a/b\s*test|"
    r"\bpilot\b.*\bdecide\b|measure\s+(before|to\s+know)|run\s+it\s+to\s+see",
    re.I,
)

_INFO_GAP = re.compile(
    r"\buncertain\b|missing\s+info|unclear\s+(if|whether)|need\s+more\s+information|"
    r"not\s+enough\s+(data|detail)|unknown\s+whether|insufficient\s+detail",
    re.I,
)

_NEEDS_HARDWARE = re.compile(
    r"infeasible\s+on\s+this\s+hardware|no\s+(dedicated\s+)?gpu\b|no\s+avx-?512|"
    r"insufficient\s+ram|broadwell|no\s+cuda|not\s+enough\s+(vram|memory)|"
    r"hardware[-\s]?infeasible",
    re.I,
)

_THEORETICAL = re.compile(
    r"paper[-\s]?(focused|driven)?\b|academic|research[-\s]focused|"
    r"not\s+a\s+ready[-\s]to[-\s]use\s+tool|promising\s+but\s+unproven|"
    r"unproven\s+in\s+production|requires?\s+significant\s+adaptation|proof[-\s]of[-\s]concept\s+only",
    re.I,
)

# Checked in this exact order — see the block comment above for why.
_RULES = (
    ("defer-needs-decision", _NEEDS_DECISION),
    ("defer-dep-blocked", _DEP_BLOCKED),
    ("defer-duplicate-suspect", _DUPLICATE_SUSPECT),
    ("defer-needs-hardware", _NEEDS_HARDWARE),
    ("defer-empirical", _EMPIRICAL),
    ("defer-info-gap", _INFO_GAP),
    ("defer-theoretical", _THEORETICAL),
)


def classify_defer_type(reason):
    """Return one of LABELS for a defer's decision_reason text, or None when no rule
    matches (callers should surface unmatched rows for a human look rather than
    guessing — see backfill_defer_labels.py's --report-unmatched)."""
    text = reason or ""
    for label, pattern in _RULES:
        if pattern.search(text):
            return label
    return None
