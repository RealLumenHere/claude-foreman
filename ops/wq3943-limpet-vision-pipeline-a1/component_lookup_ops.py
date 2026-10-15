"""wq#3943 — component identity resolution for the LIMPET vision pipeline.

Resolves a VLM-read marking (design/wq3900_limpet_vision_component_id.md §5)
to a part identity through, in order of specificity: the real iTXTech/
FlashDetector CLI if it's installed (adopted via TOOL_REGISTRY — not yet,
status [verify-at-adoption]), else a conservative local decode over
well-documented flash manufacturer marking prefixes, then Part-DB's REST API
(optional — degrades to "not configured" when LIMPET_PARTDB_BASE_URL is
unset, per design doc §5.3/§7: adoption decides the URL, the pipeline only
consumes it). No resolver here ever upgrades an unmatched marking to a
guessed identity (§4's hallucination control, extended to the resolve stage):
a miss returns resolved=None, never a fabricated part.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request

PARTDB_BASE_URL = os.environ.get("LIMPET_PARTDB_BASE_URL", "")
PARTDB_API_TOKEN = os.environ.get("LIMPET_PARTDB_API_TOKEN", "")
PARTDB_TIMEOUT_SEC = 15

# [verify-at-adoption]: Part-DB-server exposes an API-Platform REST surface; this is
# the documented resource path for a name search as of model knowledge (cutoff
# 2026-01). Confirm against the real instance at the TOOL_REGISTRY go/no-go before
# trusting a hit (design/wq3900_limpet_vision_component_id.md §5.3/§7) — a wrong path
# here fails closed (HTTP error -> "not configured"-shaped result), it never
# fabricates a part.
PARTDB_SEARCH_PATH = "/api/parts?name={query}"

FLASHDETECTOR_BIN = shutil.which("flashdetector") or ""

# Flash marking prefix -> (manufacturer, family). These prefixes are the
# manufacturer's own branding convention (how the part is literally named in its own
# datasheet), not an inferred guess. Density/voltage-suffix decoding is deliberately
# NOT attempted — too many per-family exceptions to encode without live verification.
# A hit reports manufacturer+family only, confidence "medium", and leaves the full
# marking in `mpn` for the operator to confirm against a datasheet — consistent with
# wq3900 §4's "never auto-upgrade to a guessed identity."
_FLASH_PREFIXES = (
    (re.compile(r"^W?25Q\d", re.IGNORECASE), "Winbond", "SPI NOR flash (25Q series)"),
    (re.compile(r"^W?25X\d", re.IGNORECASE), "Winbond", "SPI NOR flash (25X series)"),
    (re.compile(r"^GD25Q\d", re.IGNORECASE), "GigaDevice", "SPI NOR flash (GD25Q series)"),
    (re.compile(r"^MX25L\d", re.IGNORECASE), "Macronix", "SPI NOR flash (MX25L series)"),
    (re.compile(r"^MT25Q\d", re.IGNORECASE), "Micron", "SPI NOR flash (MT25Q series)"),
    (re.compile(r"^S25FL\d", re.IGNORECASE), "Infineon/Cypress", "SPI NOR flash (S25FL series)"),
    (re.compile(r"^K9[A-Z]\d", re.IGNORECASE), "Samsung", "NAND flash (K9 series)"),
)


class PartDbError(RuntimeError):
    pass


def _flashdetector_external(marking: str) -> dict | None:
    """Shell out to the real FlashDetector CLI if it's on PATH. Not installed
    on this build lane — returns None, never raises, so its absence is a
    degrade, not a pipeline failure; this is the upgrade hook for when
    FlashDetector actually clears its TOOL_REGISTRY adoption gate."""
    if not FLASHDETECTOR_BIN:
        return None
    try:
        proc = subprocess.run(
            [FLASHDETECTOR_BIN, marking], capture_output=True, text=True, timeout=10, shell=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def flash_marking_decode(marking: str) -> dict | None:
    """Local flash-marking-grammar decode (see module docstring). Returns
    None — never a guess — when no prefix matches."""
    hit = _flashdetector_external(marking)
    if hit is not None:
        return hit

    stripped = marking.strip()
    for pattern, manufacturer, family in _FLASH_PREFIXES:
        if pattern.match(stripped):
            return {
                "resolver": "flashdetector-style",
                "manufacturer": manufacturer,
                "family": family,
                "mpn": stripped,
                "confidence": "medium",
                "datasheet": None,
            }
    return None


def partdb_search(query: str, base_url: str = PARTDB_BASE_URL, token: str = PARTDB_API_TOKEN) -> dict:
    """Search Part-DB for `query` (a marking or candidate MPN). Returns
    {"ok", "results": [...], "error"} — never raises; an unconfigured or
    unreachable Part-DB is a legitimate degraded result, not a pipeline
    failure (design doc §7)."""
    if not base_url:
        return {"ok": False, "results": [], "error": "Part-DB not configured (set LIMPET_PARTDB_BASE_URL)"}

    url = base_url.rstrip("/") + PARTDB_SEARCH_PATH.format(query=urllib.parse.quote(query))
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=PARTDB_TIMEOUT_SEC) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        return {"ok": False, "results": [], "error": f"Part-DB HTTP {exc.code} (endpoint [verify-at-adoption]: {url})"}
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "results": [], "error": f"Part-DB unreachable at {base_url}: {exc}"}

    if isinstance(payload, list):
        results = payload
    elif isinstance(payload, dict):
        results = payload.get("results", payload.get("hydra:member", []))
    else:
        results = []
    return {"ok": True, "results": results if isinstance(results, list) else [], "error": None}


def lookup_part(marking: str, package: str = "") -> dict:
    """Resolve `marking` to a part identity through the resolver chain
    (flash-grammar decode, then Part-DB). Returns
    {"query", "resolved": {...}|None, "candidates": [...], "needs_review"}
    per design/wq3900_limpet_vision_component_id.md §5/§6 — `resolved` is set
    only on a confident single hit, never a best-effort guess."""
    query = {"marking": marking, "package": package}

    flash_hit = flash_marking_decode(marking)
    if flash_hit is not None:
        return {"query": query, "resolved": flash_hit, "candidates": [flash_hit], "needs_review": False}

    partdb = partdb_search(marking)
    if partdb["ok"] and len(partdb["results"]) == 1:
        return {"query": query, "resolved": partdb["results"][0], "candidates": partdb["results"], "needs_review": False}
    if partdb["ok"] and len(partdb["results"]) > 1:
        return {"query": query, "resolved": None, "candidates": partdb["results"], "needs_review": True}

    return {
        "query": query, "resolved": None, "candidates": [], "needs_review": True,
        "resolver_error": partdb["error"],
    }
