#!/usr/bin/env python3
"""next_turn_intent.py — next-turn intent anticipation for multi-turn research
queries (wq#2521, minerva proposal 949b49798335).

Prompt Forge already keeps a per-session history of generated prompts
(server.py's STATE_DIR/history/*.json — wq#957 design open question 3). For a
research project that spans several turns (category "research" or the
"web_research" filter), the category/filters chosen on recent turns are a
strong predictor of what the *next* turn will ask for again — Frank rarely
switches domain mid-investigation. This module turns that existing history
into a lightweight, deterministic prediction so the UI/client can pre-rank the
next research turn's category and filters before Frank types anything.

Deliberately no LLM call here: llm.py's docstring fixes prompt-forge's LLM use
at exactly two steps (clarifying questions, framing) — Option B boundary,
PROMPT_FORGE_DESIGN.md. Recency-weighted frequency over history is enough to
anticipate intent and keeps a dead gateway from ever affecting this signal.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_STATE_DIR = os.environ.get(
    "PROMPT_FORGE_STATE_DIR", "/home/lumen/agents/prompt-forge")

RECENCY_DECAY = 0.5  # older turns count for less
MIN_RESEARCH_TURNS = 2  # below this, there isn't enough signal to predict


def load_history(state_dir=None, limit: int = 20) -> list[dict]:
    """Full history entries (not server.py's trimmed _history()), oldest
    first. Malformed or missing files are skipped/ignored — history is
    advisory, never a hard dependency. "Malformed" covers both unparseable
    JSON and valid JSON of the wrong shape: a file holding a list or a bare
    scalar parses cleanly, so only a shape check keeps it from reaching
    _is_research_turn()'s .get() and raising AttributeError out of
    predict_next_intent() (wq#2522/wq#2523)."""
    hist_dir = Path(state_dir or DEFAULT_STATE_DIR) / "history"
    entries = []
    try:
        paths = sorted(hist_dir.glob("*.json"))[-limit:]
    except OSError:
        return []
    for p in paths:
        try:
            entry = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _is_research_turn(entry: dict) -> bool:
    return entry.get("category") == "research" or "web_research" in (entry.get("filters") or [])


def predict_next_intent(history: list[dict], recent_n: int = 8) -> dict:
    """Predict the likely category/filters of the *next* research turn from
    the tail of `history` (oldest-first, as returned by load_history).

    Returns {"predicted_category", "predicted_filters", "confidence",
    "based_on"}. confidence is the recency-weighted share of the predicted
    category among the research turns considered — 0.0 (and no prediction)
    when fewer than MIN_RESEARCH_TURNS research turns are present."""
    turns = [e for e in history[-recent_n:] if _is_research_turn(e)]
    if len(turns) < MIN_RESEARCH_TURNS:
        return {
            "predicted_category": None,
            "predicted_filters": [],
            "confidence": 0.0,
            "based_on": len(turns),
        }

    category_weight: dict[str, float] = {}
    filter_weight: dict[str, float] = {}
    total_weight = 0.0
    last = len(turns) - 1
    for i, entry in enumerate(turns):
        weight = RECENCY_DECAY ** (last - i)
        total_weight += weight
        cat = entry.get("category") or "research"
        category_weight[cat] = category_weight.get(cat, 0.0) + weight
        for f in entry.get("filters") or []:
            filter_weight[f] = filter_weight.get(f, 0.0) + weight

    predicted_category = max(category_weight, key=category_weight.get)
    predicted_filters = [f for f, _ in
                         sorted(filter_weight.items(), key=lambda kv: -kv[1])[:3]]
    confidence = round(category_weight[predicted_category] / total_weight, 3)

    return {
        "predicted_category": predicted_category,
        "predicted_filters": predicted_filters,
        "confidence": confidence,
        "based_on": len(turns),
    }
