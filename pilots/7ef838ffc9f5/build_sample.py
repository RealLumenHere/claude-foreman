#!/usr/bin/env python3
"""wq#1902 pilot — fixed sample for the personalization-benchmark-seed pilot.

The source paper (classified_intel id f28bb9e611091397100df50ffb8d6be4,
"Benchmarking the Personalization Capabilities of Large Language Models",
arXiv, no URL captured at intake) is not fetchable from this session (no
WebSearch/WebFetch permission granted to a headless build worker). Its exact
tasks cannot be pulled, so per the card's own "edit before the run" clause
this pilot substitutes a small hand-authored proxy sample covering the same
general personalization-benchmark shape described in the paper's classified
summary: preference recall and persona-adherence under injected user facts.
This is the same substitution pattern used by pilots 0eed2f5bdb92, 7c516dfb1a14
and 7187e39f4cbf (synthetic proxy sample, documented, not hidden).

Each task pairs a persona/preference fact set with a query that only a
model actually using those facts can answer correctly, plus a rubric of
required substrings for scoring.
"""
import hashlib
import json
from pathlib import Path

TASKS = [
    {
        "id": 0,
        "facts": "The user is a vegetarian and is allergic to peanuts.",
        "query": "Suggest one dinner recipe for me tonight.",
        "rubric": ["vegetarian"],
        "forbidden": ["peanut", "chicken", "beef", "pork", "fish"],
    },
    {
        "id": 1,
        "facts": "The user always wants answers in bullet points, never prose paragraphs.",
        "query": "Explain how photosynthesis works.",
        "rubric": ["-", "*"],
        "forbidden": [],
    },
    {
        "id": 2,
        "facts": "The user's favorite programming language is Rust and they dislike Python.",
        "query": "What language should I use for my next side project?",
        "rubric": ["rust"],
        "forbidden": [],
    },
    {
        "id": 3,
        "facts": "The user lives in Berlin and wants distances/times in metric units.",
        "query": "How far is a marathon and about how long would it take me to walk it?",
        "rubric": ["km", "kilomet"],
        "forbidden": ["miles"],
    },
    {
        "id": 4,
        "facts": "The user's dog is named Biscuit and they mentioned this earlier in the conversation.",
        "query": "Write a one-sentence birthday message for my dog.",
        "rubric": ["biscuit"],
        "forbidden": [],
    },
    {
        "id": 5,
        "facts": "The user is a beginner who has never coded before and gets overwhelmed by jargon.",
        "query": "What is a variable in programming?",
        "rubric": ["simple", "beginner", "think of it", "like a"],
        "forbidden": [],
    },
    {
        "id": 6,
        "facts": "The user prefers concise answers under 2 sentences.",
        "query": "What causes rain?",
        "rubric": [],
        "forbidden": [],
        "max_sentences": 2,
    },
    {
        "id": 7,
        "facts": "The user told you earlier they are training for a half-marathon in 6 weeks.",
        "query": "Give me one piece of advice for this week.",
        "rubric": ["marathon", "half-marathon", "half marathon", "race", "training"],
        "forbidden": [],
    },
]


def main() -> None:
    out = Path(__file__).parent / "sample.json"
    payload = {"seed_note": "hand-authored, no RNG", "tasks": TASKS}
    text = json.dumps(payload, indent=2, sort_keys=True)
    out.write_text(text)
    digest = hashlib.sha256(text.encode()).hexdigest()
    print(f"wrote {out}")
    print(f"sha256 {digest}")


if __name__ == "__main__":
    main()
