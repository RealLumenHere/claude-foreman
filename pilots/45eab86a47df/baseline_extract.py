#!/usr/bin/env python3
"""Baseline arm for pilot 45eab86a47df.

Reimplements the extraction regexes from the ALREADY-DEPLOYED
familiarity_scorer.py `extract()` (Minerva proposal fd2622e69837, wq#443)
verbatim, so the pilot measures "what Lumen does today" per the CARD's
Baseline instruction without touching /home/lumen/agent (outside this
worker's --add-dir; see kb/ISSUE-LOG.md #240). Source snapshot compared
against: foreman/_runs/wq2190/snapshots/20260920T182319Z-*familiarity_scorer.py
lines 40-66.
"""
import re

STOP = {"The", "This", "That", "These", "Those", "A", "An", "It", "If",
        "In", "On", "At", "For", "And", "But", "Not", "New", "Latest",
        "Here", "There", "What", "When", "Why", "How", "Who", "Also",
        "Github", "GitHub", "Http", "Https", "Www", "AI", "ML", "LLM",
        "API", "CLI", "GPU", "CPU", "TAM", "USA", "IMO", "TIL", "PSA",
        "HINT", "NOTE", "EDIT", "UPDATE", "THREAD", "BREAKING"}

PAT_TITLECASE = re.compile(r"\b([A-Z][a-zA-Z0-9]+(?:[ -][A-Z][a-zA-Z0-9]+)+)\b")
PAT_ARXIV = re.compile(r"\barXiv:?\s?(\d{4}\.\d{4,5})\b", re.I)
PAT_REPO = re.compile(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]{3,})\b")
PAT_ACRO = re.compile(r"\b([A-Z][A-Z0-9]{3,23})\b")
PAT_PRODUCT = re.compile(r"\b([A-Z][a-z]+[A-Z][a-zA-Z0-9]+)\b")  # CamelCase


def norm(e):
    return re.sub(r"\s+", " ", e.strip()).lower()


def extract(text):
    if not text:
        return set()
    text = re.sub(r"https?://\S+", " ", text)
    out = set()
    for m in PAT_ARXIV.finditer(text):
        out.add("arxiv:" + m.group(1))
    for m in PAT_REPO.finditer(text):
        s = m.group(1)
        if "." not in s.split("/")[0] and not s.startswith("t.co"):
            out.add(s)
    for pat in (PAT_TITLECASE, PAT_PRODUCT, PAT_ACRO):
        for m in pat.finditer(text):
            s = m.group(1)
            if s not in STOP and len(s) >= 4:
                out.add(s)
    return {norm(e) for e in out
            if 4 <= len(norm(e)) <= 40 and len(norm(e).split()) <= 4}


if __name__ == "__main__":
    import json
    import sys
    sample = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "sample.json"))
    for item in sample["items"]:
        got = extract(item["title"] + " " + item["reasoning"])
        print(item["id"], sorted(got))
