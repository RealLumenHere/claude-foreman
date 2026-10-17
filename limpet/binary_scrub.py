"""wq#4108 -- binary-content scrubbing: the control-char + length half of the
prompt-injection defense, composed with wq#4091's phrasing classifier.

wq#4091 shipped two of the three things the v1 sanitization layer needs: an injection
*phrasing* classifier (``injection.classify`` -- "ignore previous instructions",
"SYSTEM: maintenance mode") and a workspace *gate* (``sanitize.sanitize_workspace``)
that redacts those hits in evidence files before the agent reads them. It did **not**
address the other two things wq#4108 names: stripping control characters and limiting
string length. A recovered string can hijack or corrupt the agent's context with no
injection *phrasing* at all --

  * control/escape bytes (``\\x1b[2J``, a NUL, a backspace run) that forge terminal
    state or hide text from a reviewer,
  * invisible Unicode -- bidi overrides (``U+202E``), zero-width joiners, and the
    invisible *tag* block (``U+E0000..E007F``), a documented channel for smuggling an
    instruction past a human reader entirely,
  * a megabyte-long string that floods the context window (or buries a payload past the
    scroll).

This module is that missing half. It is deliberately narrow and orthogonal to
``injection.py``: it does **not** re-classify phrasing (that is ``injection``'s job and
reusing it keeps one source of truth). It escapes every unsafe char to a *visible* form
and caps length. ``scrub_for_context`` composes the two into the single call the
binary-content->LLM boundary wants: redact known injection phrasing (reusing
``injection``), then escape controls and cap length.

Pure, stdlib-only, no I/O -- mirrors ``injection.py``'s posture so the two feel like one
system. The caller decides *where* to apply it; ``workspace_index`` applies it on ingest
into the derived, agent-facing ``index.sqlite`` while leaving the canonical raw evidence
under ``evidence/re-exports/`` untouched.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

import injection

# Cap chosen to leave a genuinely long legitimate string (a URL, a format template, a
# cert blob) mostly intact while keeping any single value well under a context-bloating
# size. Caller can override per field.
DEFAULT_MAX_LEN = 2048

# C0 controls we escape to a *named* form so the common cases stay readable.
_NAMED_CONTROLS = {"\t": "\\t", "\n": "\\n", "\r": "\\r"}

# Individual invisible / direction-changing code points that must never reach the model
# as-is. Block ranges are handled in _is_unsafe_invisible.
_BIDI_AND_ZEROWIDTH = {
    0x200B, 0x200C, 0x200D, 0x200E, 0x200F,            # ZWSP/ZWNJ/ZWJ/LRM/RLM
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,            # LRE/RLE/PDF/LRO/RLO (bidi override)
    0x2060, 0x2061, 0x2062, 0x2063, 0x2064,            # word-joiner / invisible operators
    0x2066, 0x2067, 0x2068, 0x2069,                    # LRI/RLI/FSI/PDI (bidi isolate)
    0xFEFF,                                            # BOM / ZWNBSP
    0x00AD,                                            # soft hyphen
    0xFFF9, 0xFFFA, 0xFFFB,                            # interlinear annotation anchors
}


def _is_unsafe_invisible(cp: int) -> bool:
    """True iff the code point renders invisibly / changes layout and must be escaped."""
    if cp in _BIDI_AND_ZEROWIDTH:
        return True
    if 0xE0000 <= cp <= 0xE007F:   # Unicode tag block -- invisible ASCII-smuggling channel
        return True
    if 0x2028 <= cp <= 0x2029:     # LINE / PARAGRAPH SEPARATOR
        return True
    return False


def _escape_char(ch: str) -> str:
    """Visible escape for an unsafe char, or the char unchanged if it is safe."""
    if ch in _NAMED_CONTROLS:
        return _NAMED_CONTROLS[ch]
    cp = ord(ch)
    if cp < 0x20 or cp == 0x7F or 0x80 <= cp <= 0x9F:   # C0 (minus named), DEL, C1
        return f"\\x{cp:02x}"
    if _is_unsafe_invisible(cp):
        return f"\\u{cp:04x}" if cp <= 0xFFFF else f"\\U{cp:08x}"
    # Remaining Cf/Cs/Co/Cn (format/surrogate/private-use/unassigned) are invisible or
    # undefined -> escape. Cc is already covered above.
    if unicodedata.category(ch) in ("Cf", "Cs", "Co", "Cn"):
        return f"\\u{cp:04x}" if cp <= 0xFFFF else f"\\U{cp:08x}"
    return ch


@dataclass
class Scrubbed:
    """Result of scrubbing one value, with telemetry a probe can assert on."""
    text: str
    truncated: bool = False
    controls_escaped: int = 0
    redacted: bool = False

    @property
    def modified(self) -> bool:
        return self.truncated or self.controls_escaped > 0 or self.redacted


def scrub(value, *, max_len: int = DEFAULT_MAX_LEN) -> Scrubbed:
    """Escape control/invisible chars and cap length. No phrasing classification.

    ``value`` is coerced to ``str`` (``None`` -> empty). Every unsafe char becomes a
    visible escape, so nothing invisible survives and the value collapses to one logical
    line (``\\n``/``\\t``/``\\r`` are escaped), which also denies an injected string the
    ability to forge turn/line structure in the prompt. Length is then capped with a
    visible ``...[+N]`` marker. Truncation runs last so it never severs an escape.
    """
    if value is None:
        return Scrubbed(text="")
    text = value if isinstance(value, str) else str(value)

    escaped = 0
    out = []
    for ch in text:
        rep = _escape_char(ch)
        if rep != ch:
            escaped += 1
        out.append(rep)
    cleaned = "".join(out)

    truncated = False
    if max_len is not None and max_len >= 0 and len(cleaned) > max_len:
        removed = len(cleaned) - max_len
        cleaned = cleaned[:max_len] + f"...[+{removed}]"
        truncated = True

    return Scrubbed(text=cleaned, truncated=truncated, controls_escaped=escaped)


def scrub_for_context(value, *, max_len: int = DEFAULT_MAX_LEN) -> Scrubbed:
    """The full binary-content->LLM-context transform for one value.

    Composes wq#4091's phrasing redaction with this module's control/length scrub:
    a value matching a known injection shape (``injection.is_injection``) is first
    replaced whole with ``injection.REDACTION_TOKEN`` -- the landed house strategy, one
    source of truth for *what counts as* an injection -- and the result is then control-
    escaped and length-capped. Safe and idempotent whether or not the workspace gate
    (``sanitize.sanitize_workspace``) already ran: the redaction token carries no control
    chars, and a second pass over it is a no-op. ``None``/non-str pass through ``scrub``.
    """
    if isinstance(value, str) and injection.is_injection(value):
        res = scrub(injection.REDACTION_TOKEN, max_len=max_len)
        res.redacted = True
        return res
    return scrub(value, max_len=max_len)


def scrub_text(value, *, max_len: int = DEFAULT_MAX_LEN) -> str:
    """Convenience wrapper returning just the context-safe string (the common call)."""
    return scrub_for_context(value, max_len=max_len).text
