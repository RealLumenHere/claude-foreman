"""wq#2843: the ONE shape that decides whether a rotation row is under Frank's
CF-resource hold (ISSUE-LOG 850 rule c, 2026-09-25), read by both walls.

wq#2782 wrote the rule twice — `fworker.CF_ROTATION_RE` for the tripwire that files
the row and `foreman_dispatcher.CF_ROTATION_HOLD_RE` for the dispatcher that refuses
it — and both keyed on the literal substring `cloudflare`. The verify pass (wq#2787,
ISSUE-LOG 869) CONTRADICTed that: the fleet's own live config names CF credentials
without the word, `ops/wq2608-dc-endpoint-bes-a1/dns_access_setup.sh:46` sets
`CF_API_TOKEN=`, which the masker labels `kv-secret` with context `CF_API_TOKEN=MASKED`
— no `cloudflare` anywhere — so a burned CF API token filed as an ordinary OPEN row and
dispatched to an unattended worker, exactly what the hold forbids. The shape lives here
so widening it can never again land on one wall and miss the other.

Two readings, deliberately unequal in strictness:

  `cloudflare`  bare, no trailing `\\b`: the masking label for `cfut_` user tokens, and
                also the prefix of every `CLOUDFLARE_*` env name, which a `\\b` would
                refuse (`_` is a word character, so `\\bcloudflare\\b` does not match
                `CLOUDFLARE_ZONE_ID`).

  `CF[_-]…`     the abbreviated variable-name shape: `CF_API_TOKEN`, `CF_ZONE_ID`,
                `CF_ACCOUNT_ID`, `CF_DNS_API_TOKEN`, `cf-hold`. The lookbehind is
                `(?<![A-Za-z0-9])` rather than `\\b` so the shape is still found as a
                SEGMENT of a longer identifier (`WQ_CF_API_TOKEN` — after `_`, `\\b`
                would not fire), and the separator requirement is what keeps it off
                unrelated CF-prefixed identifiers: `CFG_SECRET`, `CFLAGS`, `cfg-token`
                have no `_`/`-` directly behind `CF`, so they never match.

Case-insensitive, and over-firing is the safe direction here on purpose: a match only
ever routes a credential-rotation row to Frank instead of to a fire-and-forget worker,
while a miss is the 2026-09-25 mcpw/Commander outage again.

The hold is temporary by design. Closing ISSUE-LOG 850 means deleting this module and
its two callers, not adding an override — there is none.
"""
import re

# One source string, compiled here and composed into the dispatcher's title check.
CF_NAMED = r"(?:cloudflare|(?<![A-Za-z0-9])CF[_-](?=[A-Za-z0-9]))"
CF_NAMED_RE = re.compile(CF_NAMED, re.I)

# The marker fworker puts in a held row's TITLE (rotation_title). A rotation title
# carries only the mask LABELS, never the masked context, so a CF token caught by
# variable name under the generic `kv-secret` label leaves no CF trace for a
# title-only reader — the dispatcher's second wall was blind to precisely the row the
# first wall had just identified. The marker is itself in the `CF[_-]` shape above, so
# the one pattern both walls read is what catches it.
TITLE_MARKER = "[cf-hold]"


def names_cf_resource(*texts):
    """True when any of `texts` names a Cloudflare resource by word or variable name."""
    return any(CF_NAMED_RE.search(t or "") for t in texts)
