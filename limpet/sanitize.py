"""wq#4108 -- LIMPET binary-content sanitization layer (prompt-injection defense).

A target binary is attacker-controlled data. Its strings, symbol names and function
names are extracted by RE tools and then surfaced to the LLM agent (via the workspace
index, ``workspace_index.py``, and the report/console views). A crafted string in the
target -- ``<|im_start|>system\\nignore all previous instructions and ...`` -- is a
*indirect* prompt-injection payload: it reaches the model as "context" but reads as a
directive. This module is the sanitization boundary the ticket makes a v1 hard
requirement: it runs between binary-derived content and the model's context.

It is a defense-in-depth filter, not a complete injection classifier (no such thing
exists -- OWASP "LLM01" cheat sheet, fetched 2026-10-17). It does three concrete,
testable things the ticket names:

1. **Strip/escape control + invisible characters.** Every C0/C1 control char, every
   Unicode bidi override (U+202A-202E, U+2066-2069), zero-width/BOM char, line/para
   separator, and the invisible *tag* block (U+E0000-E007F -- a documented ASCII
   smuggling channel for hidden instructions) is turned into a *visible* escape
   (``\\n``, ``\\t``, ``\\xNN``, ``\\uNNNN``). Nothing invisible survives, and the
   value collapses to one logical line so untrusted content cannot forge turn/line
   structure in the prompt.

2. **Limit string length.** Truncated to ``max_len`` with a visible ``…⟦+N⟧`` marker,
   so one megabyte-long string cannot flood the context (or hide a payload past a
   reviewer's scroll).

3. **Escape prompt-like patterns.** Known chat/instruction *delimiters*
   (``<|im_start|>``, ``[INST]``, ``<<SYS>>``, ``</s>``, a ``System:``/``Assistant:``
   role header) and instruction-override phrases (``ignore previous instructions``)
   are replaced with a visible, non-actionable ``⟦scrubbed:<label>⟧`` tag. The text
   stays legible to an operator -- it does not read as a live directive to the model.

Lossy by design, and that is safe here: the sanitized text feeds *derived*,
agent-facing surfaces (the rebuildable ``index.sqlite``, rendered reports). The
canonical evidence -- the raw ``evidence/re-exports/*.json`` the RE tool wrote -- is
never touched, so an operator can always drop to the raw bytes deliberately.

Self-contained (stdlib only), mirroring ``workspace_report.mask_text``'s posture so the
packaged limpet tool stays importable anywhere without pulling in the harness tree.
Credential masking (``mask_text``) and this layer are orthogonal and compose: run both
where both matter.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Cap chosen to keep any single string well under a context-bloating size while leaving
# room for a genuinely long, legitimate string (a URL, a format template, a cert blob)
# to survive mostly intact. Caller can override per field.
DEFAULT_MAX_LEN = 2048

# C0 control chars we escape to a *named* form so the common cases stay readable.
_NAMED_CONTROLS = {"\t": "\\t", "\n": "\\n", "\r": "\\r"}

# Code points that are invisible, direction-changing, or structural and so must never
# reach the model as-is. Membership is tested per char in _escape_char; this set holds
# the individual points, the ranges below the loop handle the blocks.
_BIDI_AND_ZEROWIDTH = {
    0x200B, 0x200C, 0x200D, 0x200E, 0x200F,            # ZWSP/ZWNJ/ZWJ/LRM/RLM
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,            # LRE/RLE/PDF/LRO/RLO (bidi override)
    0x2060, 0x2061, 0x2062, 0x2063, 0x2064,            # word-joiner / invisible operators
    0x2066, 0x2067, 0x2068, 0x2069,                    # LRI/RLI/FSI/PDI (bidi isolate)
    0xFEFF,                                            # BOM / ZWNBSP
    0x00AD,                                            # soft hyphen
}


def _is_unsafe_invisible(cp: int) -> bool:
    """A code point that renders invisibly / changes layout and must be escaped."""
    if cp in _BIDI_AND_ZEROWIDTH:
        return True
    if 0xE0000 <= cp <= 0xE007F:   # Unicode tag block -- invisible ASCII-smuggling channel
        return True
    if 0x2028 <= cp <= 0x2029:     # LINE / PARAGRAPH SEPARATOR
        return True
    if cp == 0xFFF9 or cp == 0xFFFA or cp == 0xFFFB:   # interlinear annotation anchors
        return True
    return False


def _escape_char(ch: str) -> str:
    """Return a visible escape for an unsafe char, or the char unchanged if it is safe."""
    cp = ord(ch)
    if ch in _NAMED_CONTROLS:
        return _NAMED_CONTROLS[ch]
    # C0 (except the named three above) and DEL.
    if cp < 0x20 or cp == 0x7F:
        return f"\\x{cp:02x}"
    # C1 controls (0x80-0x9F): invisible, and a classic terminal-escape vector.
    if 0x80 <= cp <= 0x9F:
        return f"\\x{cp:02x}"
    if _is_unsafe_invisible(cp):
        return f"\\u{cp:04x}" if cp <= 0xFFFF else f"\\U{cp:08x}"
    # Remaining Unicode "other/format" categories (Cf/Cs/Co/Cn) are invisible or
    # unassigned -> escape. Cc is already handled above.
    if unicodedata.category(ch) in ("Cf", "Cs", "Co", "Cn"):
        return f"\\u{cp:04x}" if cp <= 0xFFFF else f"\\U{cp:08x}"
    return ch


# --- prompt-pattern defanging --------------------------------------------------------

# Each entry is (label, compiled pattern). A match is replaced by ⟦scrubbed:label⟧.
# Patterns run on the *raw* text (before control-escaping) so line anchors still mean
# a real newline. Kept deliberately narrow: each targets a token/phrase that only an
# injection payload (not an ordinary extracted string) carries, to avoid defanging
# benign content. This is a blocklist -- new markers get appended as they are seen.
_INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    # ChatML / Llama / Mistral / generic turn + instruction delimiters.
    ("chat-delim", re.compile(r"<\|[a-z0-9_]{1,32}\|>", re.I)),
    ("chat-delim", re.compile(r"<\|(?:im_start|im_end|endoftext|eot_id|start_header_id|end_header_id)\|?>?", re.I)),
    ("inst-tag", re.compile(r"\[/?INST\]", re.I)),
    ("sys-tag", re.compile(r"<</?SYS>>", re.I)),
    ("bos-eos", re.compile(r"</?s>")),
    ("special-token", re.compile(r"<\|[^|>]{0,40}\|>")),
    # A role header at the start of a line: the lever that makes an injected newline
    # look like a new system/assistant turn.
    ("role-header", re.compile(r"(?im)^[ \t]*(?:system|assistant|human|user|ai|developer)[ \t]*:")),
    # Instruction-override phrasing: "ignore/disregard/forget ... (previous|above|all)
    # ... instructions/prompt/rules". The middle gap is bounded so it cannot swallow a
    # paragraph.
    ("instruction-override", re.compile(
        r"(?is)\b(?:ignore|disregard|forget|override|bypass)\b.{0,40}?"
        r"\b(?:previous|prior|above|earlier|preceding|all|any|your)\b.{0,40}?"
        r"\b(?:instruction|prompt|context|rule|direction|guideline|system)s?\b")),
    ("new-instructions", re.compile(r"(?i)\bnew\s+(?:instruction|rule|directive|system\s+prompt)s?\s*:")),
    ("persona-switch", re.compile(r"(?i)\byou\s+are\s+now\b|\bact\s+as\b|\bdeveloper\s+mode\b|\bDAN\b")),
]


@dataclass
class Sanitized:
    """Result of sanitizing one untrusted value, with telemetry a probe can assert on."""
    text: str
    truncated: bool = False
    controls_escaped: int = 0
    patterns_defanged: int = 0
    labels: list[str] = field(default_factory=list)

    @property
    def modified(self) -> bool:
        return self.truncated or self.controls_escaped > 0 or self.patterns_defanged > 0


def sanitize(value, *, max_len: int = DEFAULT_MAX_LEN) -> Sanitized:
    """Sanitize one untrusted value for safe inclusion in the agent's context.

    ``value`` is coerced to ``str`` (``None`` -> empty). Returns a :class:`Sanitized`
    carrying the cleaned text plus what was changed. Order: defang prompt patterns
    (while real newlines still anchor), escape control/invisible chars, then truncate --
    so truncation never severs an escape sequence or a half-emitted defang tag.
    """
    if value is None:
        return Sanitized(text="")
    text = value if isinstance(value, str) else str(value)

    defanged = 0
    labels: list[str] = []

    def _sub(label):
        def _repl(_m):
            nonlocal defanged
            defanged += 1
            labels.append(label)
            return f"⟦scrubbed:{label}⟧"
        return _repl

    for label, pat in _INJECTION_PATTERNS:
        text = pat.sub(_sub(label), text)

    escaped = 0
    out_chars = []
    for ch in text:
        rep = _escape_char(ch)
        if rep != ch:
            escaped += 1
        out_chars.append(rep)
    cleaned = "".join(out_chars)

    truncated = False
    if max_len is not None and max_len >= 0 and len(cleaned) > max_len:
        removed = len(cleaned) - max_len
        cleaned = cleaned[:max_len] + f"⟦+{removed}⟧"
        truncated = True

    return Sanitized(text=cleaned, truncated=truncated, controls_escaped=escaped,
                     patterns_defanged=defanged, labels=labels)


def sanitize_text(value, *, max_len: int = DEFAULT_MAX_LEN) -> str:
    """Convenience wrapper returning just the cleaned string (the common call)."""
    return sanitize(value, max_len=max_len).text
