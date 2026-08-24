#!/usr/bin/env python3
"""wq#888 -- SmartCrusher-shaped JSON compression for the one hook point we have.

research/SMARTCRUSHER_HOOK.md is the feasibility assessment this implements. The
short version: claude-code 2.1.233 has no way to rewrite a tool *result*, so the
only lever that changes bytes before they enter the context is PreToolUse
`updatedInput` -- the same lever harness/guard.sh already uses for the wq#729
Bash cap and the wq#730 Read bound. That lever rewrites the *command*, so the
compressor has to be a stdin->stdout filter the guard appends to the pipeline:

    { <cmd>
    } 2>&1 | crush.py --max-bytes 2000

Placed there it runs strictly *before* the wq#729 `head -c` cap, which is the
whole point: truncation destroys the tail of a large result, compression keeps
every value and drops only the bytes that repeat.

Encoding (deterministic, no ML, no deps -- all three properties are why headroom
is not a dependency here; see the assessment's "why not headroom" section):

1. Minify. `json.dumps(separators=(",",":"))`. Lossless, applies to every JSON
   payload, and it is the single biggest win on our mix because our own tooling
   dumps `indent=2` and `jq` pretty-prints by default.

2. Columnar-ize homogeneous object arrays -- SmartCrusher's core trick. An array
   of n objects sharing one key set repeats those keys n times:
       [{"id":1,"lane":"a"},{"id":2,"lane":"a"}]
   becomes
       {"__crush__":"cols+rows","const":{"lane":"a"},"cols":["id"],"rows":[[1],[2]]}
   Keys are written once; a column whose value never varies is hoisted into
   `const` and written once too.

3. JSONL in, array out. Line-delimited JSON is a homogeneous array wearing a
   disguise, and a lot of what our stack cats is JSONL (events logs, _runs
   transcripts, wq exports). Detecting it turns the worst case into the best one.

Constraints the encoding holds itself to:

- **Lossless.** `decode(encode(x)) == x` for every x, asserted over real
  transcript payloads in harness/tests/test_crush.py. Nothing here is a summary
  or a sample; a compressed result answers exactly the questions the raw one did.
- **Self-describing.** The `__crush__` marker plus `cols`/`rows` naming is meant
  to be read by the model with no decoder and no prompt-side explanation. That
  matters more than a few extra bytes: an encoding the reader has to be taught
  costs prompt tokens in every session, which is the cost we are trying to cut.
- **Never worse.** Every stage compares its output against its input and keeps
  the smaller. A payload that does not compress passes through byte-identical,
  so wiring this in cannot regress a result that was already tight.
- **Bail, don't guess.** Only arrays whose elements are *all* dicts with the
  *identical* key set are columnar-ized. Union-of-keys with an absence sentinel
  would compress more, but any sentinel we picked could collide with real data
  and losslessness stops being provable by inspection. Same reason a payload
  that already contains a `__crush__` key is returned untouched.
"""

import argparse
import json
import sys

MARKER = "__crush__"
MARKER_VALUE = "cols+rows"

# Below this an array's key-repetition savings do not clear the cols/rows/marker
# overhead, and the "never worse" check would discard the result anyway -- this
# just skips the work.
MIN_ROWS = 2


def _contains_marker(obj):
    """True if MARKER appears as a key anywhere -- makes decode ambiguous."""
    if isinstance(obj, dict):
        if MARKER in obj:
            return True
        return any(_contains_marker(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_marker(v) for v in obj)
    return False


def _columnar(items):
    """Columnar form of a homogeneous object array, or None if not applicable."""
    if len(items) < MIN_ROWS or not all(isinstance(i, dict) for i in items):
        return None
    keys = list(items[0].keys())
    keyset = set(keys)
    if any(set(i.keys()) != keyset for i in items[1:]):
        return None
    if not keys:
        return None

    # A key whose value is identical in every record is written once in `const`
    # instead of once per row. On our payloads this is not a corner case: wq
    # exports, events JSONL and `jq` output all carry per-record fields that are
    # constant across the batch (lane, session_id, agent, hostname).
    const, cols = {}, []
    for k in keys:
        first = items[0][k]
        try:
            same = all(i[k] == first for i in items[1:])
        except Exception:
            same = False
        # Only hoist hashable-ish scalars; a constant dict/list would be correct
        # but the equality scan above is the expensive part and the win is small.
        if same and isinstance(first, (str, int, float, bool, type(None))):
            const[k] = first
        else:
            cols.append(k)

    out = {MARKER: MARKER_VALUE}
    if const:
        out["const"] = const
    if cols:
        out["cols"] = cols
        out["rows"] = [[i[k] for k in cols] for i in items]
    else:
        # Every column was constant: the array collapsed to one record plus a
        # count. `n` is what makes that reversible.
        out["n"] = len(items)
    return out


def _decolumnar(obj):
    const = obj.get("const", {})
    cols = obj.get("cols")
    if cols is None:
        return [dict(const) for _ in range(obj["n"])]
    return [{**const, **dict(zip(cols, row))} for row in obj["rows"]]


def encode(obj):
    """Recursively columnar-ize object arrays. Structure-preserving elsewhere."""
    if isinstance(obj, list):
        inner = [encode(v) for v in obj]
        return _columnar(inner) or inner
    if isinstance(obj, dict):
        return {k: encode(v) for k, v in obj.items()}
    return obj


def decode(obj):
    """Inverse of encode()."""
    if isinstance(obj, dict):
        if obj.get(MARKER) == MARKER_VALUE:
            return [decode(v) for v in _decolumnar(obj)]
        return {k: decode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decode(v) for v in obj]
    return obj


def _parse(text):
    """(value, was_jsonl) for JSON or JSONL text, or (None, False) for neither.

    JSONL is only accepted at >= MIN_ROWS lines. A single JSON object followed by
    trailing prose is not JSONL, and treating it as such would silently drop the
    prose -- the one way this filter could lose data.
    """
    s = text.strip()
    if not s:
        return None, False
    try:
        return json.loads(s), False
    except ValueError:
        pass
    lines = [ln for ln in s.split("\n") if ln.strip()]
    if len(lines) < MIN_ROWS:
        return None, False
    items = []
    for ln in lines:
        try:
            items.append(json.loads(ln))
        except ValueError:
            return None, False
    return items, True


def crush_text(text):
    """(output, stats). Returns `text` unchanged when it is not JSON, contains
    the marker, or does not get smaller -- the "never worse" guarantee."""
    stats = {"in": len(text), "out": len(text), "kind": "passthrough"}
    value, was_jsonl = _parse(text)
    if value is None or _contains_marker(value):
        return text, stats

    encoded = encode(value)
    out = json.dumps(encoded, separators=(",", ":"), ensure_ascii=False)
    if len(out) >= len(text):
        return text, stats
    stats["out"] = len(out)
    stats["kind"] = "jsonl" if was_jsonl else "json"
    stats["saved_pct"] = round(100 * (1 - len(out) / len(text)), 1)
    return out, stats


def uncrush_text(text):
    value, _ = _parse(text)
    if value is None:
        return text
    return json.dumps(decode(value), separators=(",", ":"), ensure_ascii=False)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--decode", action="store_true",
                    help="invert a crushed payload (round-trip proof, not used in the hook)")
    ap.add_argument("--max-bytes", type=int, default=0,
                    help="truncate to N bytes AFTER compressing (composes with the wq#729 cap)")
    ap.add_argument("--stats", action="store_true", help="write a one-line report to stderr")
    args = ap.parse_args(argv)

    text = sys.stdin.read()
    if args.decode:
        sys.stdout.write(uncrush_text(text))
        return 0

    out, stats = crush_text(text)
    if args.max_bytes and len(out) > args.max_bytes:
        out = out[:args.max_bytes]
        stats["truncated"] = True
    if args.stats:
        sys.stderr.write(json.dumps(stats) + "\n")
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
