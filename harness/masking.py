"""wq#2275 (token-engine T6, TOKEN_ENGINE_DESIGN.md §6): mask credentials in the
child's stream-json BEFORE anything is written to disk, ntfy, the episode store
or a PR body.

CLI 2.1.233 has no lever to rewrite a tool result before the model sees it
(research/SMARTCRUSHER_HOOK.md:22,40-54 — updatedOutput / updatedToolResult /
replaceOutput / toolResultOverride occur 0 times in the shipped binary; PostToolUse
only appends), so masking here cannot protect the model's context. It protects every
ARTEFACT fworker derives from the stream: raw_events -> attempt notes, episode.jsonl,
failure.json, ntfy bodies, PR text. A hit is therefore an ALARM, not a save
(instinct 4): the token already reached the model and is burned — the caller must
page ntfy and open a rotation ticket (fworker.mask_alarm).

The patterns mirror /home/lumen/bin/mask-secrets (the instinct-4 house set). Keep
the two in lockstep: a pattern added there must be added here the same day. They
are no longer character-identical — sed -E is POSIX ERE with no lookbehind, so the
word boundary below is spelled `(^|[^A-Za-z0-9_])` with a shifted capture group
there and `(?<![A-Za-z0-9_])` here. Same language, different syntax. kv-secret is
also narrower here until wq#2313 carries its tail lookahead across, which costs the
sed copy a false alarm, never a missed mask.

Masking operates on the raw NDJSON line, not the parsed frame: the replacement
alphabet ([A-Za-z0-9._-] and the literal MASKED) contains no JSON metacharacters,
so a masked line parses exactly like the original.
"""
import hashlib
import re
import sys

import live_code

# wq#2292: a prefix only counts at a word boundary. Without `(?<![A-Za-z0-9])` the
# `ghs_` in the repo name `highs_solver` reads as a token, and since the alarm is
# pri-1-and-rotate that false positive is expensive: it cost a whole rotation ticket
# on wq#2282, which had merely read the ISSUE-LOG row documenting the identical bug.
# remote_credential_sweep.py:38-43 took the same boundary for the same reason
# (wq#2263). The boundary never hides a real token — a credential is a standalone
# value, so what precedes it is `"`, `:`, `=`, `/`, `-` or space, never an
# alphanumeric and never `_`.
#
# wq#2400: `_` belongs in that class, and leaving it out kept the SAME false positive
# one character away. `see notes_on_xoxb-<two english words>` — a line in wq#2316's own
# draft of ops/wq2316-mask-secrets-boundary/test_mask_secrets_boundary.py, in the
# parametrize list asserting that prefixes inside a word are untouched — still read as
# a slack token, armed the pri-1 alarm and filed this ticket. `_` never separates a
# credential from what precedes it: it only continues an identifier, so a prefix glued
# to one is the tail of a NAME (`notes_on_xoxb-`, `my_cfut_settings`), and a value that
# really is labelled by that name (`SLACK_TOKEN=xoxb-…`) is caught by kv-secret below,
# whose label half does not read `_B` at all.
#
# wq#2694: the one alphanumeric that IS a boundary is the end of our own mask literal.
# `_body()` stops a run at the literal, so in `ghp_MASKED` + `ghp_<live>` the first
# match dies under its floor and the live half must start on its own — which the class
# refused, the character before it being the `D` of `MASKED`. Hits `[]`, token in the
# clear, on every prefix rule (ops/wq2694-after-mask/probe_after_mask.py). The exception
# is exact and case-sensitive: no identifier carries upper-case `MASKED` glued to a
# credential prefix, and a value that sits there is a live token against our own output.
# bin/mask-secrets spells the same exception as a `|MASKED` alternative (wq#2697).
# The two rules keyed on a word (url-auth, kv-secret) open on `\b` and are blind at the
# same position for the same reason, so they take it as `_WB`. Case-sensitive via
# `(?-i:)` because kv-secret is IGNORECASE, for the reason `_body()` gives.
MASK = "MASKED"       # the literal every replacement ends in
_AFTER_MASK = r"(?<=(?-i:" + MASK + r"))"
_B = r"(?:" + _AFTER_MASK + r"|(?<![A-Za-z0-9_]))"
_WB = r"(?:" + _AFTER_MASK + r"|\b)"


def _body(charset, quant):
    """`charset` + `quant`, except the run may not cross the mask literal (wq#2638).

    Every body class below is wide enough to spell `MASKED`, so a greedy run starting
    at a live value swallowed the `<prefix>MASKED` sitting directly behind it and the
    whole thing became ONE match ending in the literal — which wq#2298's fixed-point
    filter then dropped, secret included. Reproduced live: `cfut_<live>cfut_MASKED`
    masked to nothing, hits `[]`, the value printed in the clear with no alarm; same
    for sk-key, github_pat_ and slack. The rule the filter wanted ("a match that ends
    at the literal is our own output") is only true of a match that IS our own output,
    and a charset that can cross the literal is what breaks the implication.

    Bounding the body restores it structurally: the run stops at the literal, so the
    live half matches on its own and alarms, and the masked half behind it is left as
    it was. It also ends the other half of the break — `cfut_MASKED.` re-fired the
    alarm because the trailing prose `.` is a legal body character, so the match ran
    one past the literal and no longer ended in it.

    The cost is a real body that CONTAINS `MASKED` (not merely ends in it): it is
    truncated at the literal and, below the floor, stops matching. For a random base62
    body that is ~1 in 62**6 per position, the same order of cost wq#2298 already
    priced in, and it is paid in the direction that alarms rather than the one that
    goes silent.

    The literal is matched case-SENSITIVELY (`(?-i:)`) even where the rule around it
    is IGNORECASE: kv-secret is, and a case-blind lookahead there would also stop the
    body at ordinary English — a passphrase holding the word `unmasked` would be cut
    at it, under-masking a live value to buy nothing, since only the exact upper-case
    literal is ever this module's output. (The example is described rather than
    written out for the reason test_the_mask_suite_does_not_arm_its_own_tripwire
    gives: a whole credential shape in tracked source files a rotation ticket.)
    """
    return r"(?:(?!(?-i:" + MASK + r"))" + charset + r")" + quant


# wq#2297 kv-secret label vocabulary, ported from read_dir_sweep.py:38-60 (wq#2115).
# A credential label is a NOUN, optionally qualified: `api key`, `Signing Secret`,
# `clientSecret`. The qualifier ends in a separator or a camelCase hump.
_KV_NOUN = r"(?:secret|token|key|credentials?|passphrase|password|passwd)"
# The hump test is case-SENSITIVE via `(?-i:)` even though the rule as a whole is
# IGNORECASE — that asymmetry is the whole trick: it admits `apiKey:` while refusing
# `monkey:`. Two letters minimum, since a one-letter qualifier is an English article
# ("a key: ..." is prose, not a leak).
_KV_WORD = r"[A-Za-z][A-Za-z0-9]{1,20}(?:[ _-]{1,2}|(?-i:(?=[A-Z])))"
# a public key is not a credential, and infra dirs are full of them
_KV_NOT_PUBLIC = r"(?!pub(?:lic)?[ _-]{0,2}key\b)"
# Nouns unambiguous enough to stand alone. `key` and `pat` are deliberately absent:
# `key:` alone is ordinary YAML and an ordinary Python kwarg, and `PAT` alone is a
# variable name — both need a qualifier in front to read as a credential label. The
# `github_pat_` credential itself is caught by the dedicated github pattern above.
_KV_BARE = r"(?:secret|token|credentials?|passphrase|password|passwd|bearer)"
_KV_LABEL = (r"(?:" + _KV_WORD + r")?" + _KV_NOT_PUBLIC + _KV_WORD + _KV_NOUN
             + r"|" + _KV_BARE)
# The same label with the public-key carve-out taken back out — one of the three
# inputs suppressions() below needs to see what this rule declined to read (wq#2677).
_KV_LABEL_ANY = (r"(?:" + _KV_WORD + r")?" + _KV_WORD + _KV_NOUN + r"|" + _KV_BARE)
# An env-var reference or a path is a POINTER to a credential, not the credential:
# `secret = os.environ["X"]`, `master key: /home/lumen/.ssh/id_ed25519`. Masking the
# pointer would hide the leak path the scan record exists to show.
_KV_POINTER = (r"(?!os\.environ)(?!os\.getenv)(?!(?:process\.)?env\.)"
               r"(?!\$\{?[A-Z_])(?![~.]{0,2}/)(?!<)")
# A value that is immediately CALLED is a function, not a credential. wq#2288 spelled
# that as a trailing `(?![A-Za-z0-9._-]|\()`, which read the next character instead of
# the value. Two consequences, both bad. The charset half was inert — the body run is
# already greedy, so nothing followed it to backtrack for — and with it there the `\(`
# half could not shorten the match either: the body would have had to give back a
# character into its own charset, which the charset half forbids. So the whole match was
# DROPPED. `TOKEN=<22 opaque chars>(` masked and alarmed before wq#2288 and did neither
# after (wq#2322 verify-pass, wq#2632 ruling) — a silent miss on the layer instinct 4
# calls an alarm, which is the one failure direction worse than the false positive.
# The test now reads the VALUE. A called identifier is segmented: `read_oauth_token(`,
# `key_file.read_text(`, `readOauthToken(`. A credential body is an undifferentiated
# run. Every paren-adjacent match the old half suppressed across the repo carries a `_`
# or a `.`, so requiring a separator or a camelCase hump costs nothing: the corpus hit
# count is identical before and after, none of the suppressions is regained and no new
# one appears (ops/wq2632-kv-paren/probe_paren.py, re-runnable).
# `-` is absent from both charsets on purpose: no identifier contains one, so a dashed
# value glued to a `(` is a credential, not a call.
_KV_SEGMENTED = r"[A-Za-z_][A-Za-z0-9_.]*[_.][A-Za-z0-9_.]*"
# case-SENSITIVE via `(?-i:)` under an IGNORECASE rule, same trick as _KV_WORD: the hump
# only exists if the source really wrote `oauthToken`, not `OAUTHTOKEN`.
_KV_HUMPED = r"(?-i:[A-Za-z][A-Za-z0-9]*[a-z][A-Z][A-Za-z0-9]*)"
_KV_NOT_CALLED = r"(?!(?:" + _KV_SEGMENTED + r"|" + _KV_HUMPED + r")\()"
_KV_BODY = r"[A-Za-z0-9._-]"


def _kv_rule(label, guards):
    """The kv-secret rule, parameterised by which of its suppressions are in force.

    wq#2677: the rule and the probe that records what it suppressed are built from one
    string so they cannot drift. A carve-out added to the rule but not to the probe
    would be invisible to the audit trail the probe exists to produce, which is the
    failure mode this closes — not a variant of it.
    wq#2694 landed on main after this branch's base: the leading boundary must accept
    the position right after our own mask literal too (_WB), not just a plain \b, or a
    kv-secret label glued behind "MASKED" goes blind the same way url-auth did.
    """
    return (_WB + r'((?:' + label + r')\b["\s]*[:=]["\s]*)' + guards
            + _body(_KV_BODY, '{12,}'))


_KV_SECRET = re.compile(_kv_rule(_KV_LABEL, _KV_POINTER + _KV_NOT_CALLED),
                        re.IGNORECASE)
# wq#2677: the same rule with EVERY suppression removed. All three — _KV_NOT_PUBLIC in
# the label, _KV_POINTER and _KV_NOT_CALLED in front of the body — are negative
# lookaheads, so a suppressed candidate is not a narrowed match, it is no match at all:
# no mask, no label, no record, byte-identical to clean text. That is why wq#2288's
# trailing lookahead could go silent on every opaque value glued to an open paren for a
# full PR cycle with nothing on the alarm path noticing (wq#2322, wq#2632 ruling). This
# probe is what turns that silence into a row.
_KV_CANDIDATE = re.compile(_kv_rule(_KV_LABEL_ANY, ""), re.IGNORECASE)
# reason -> the rule carrying ONLY that suppression. A candidate the variant fails to
# match at the same start is one that suppression dropped. Tested independently rather
# than in sequence, so a value two of them refuse is named by both instead of by
# whichever happened to be written first.
_KV_SUPPRESSIONS = (
    ("pointer", re.compile(_kv_rule(_KV_LABEL_ANY, _KV_POINTER), re.IGNORECASE)),
    ("call", re.compile(_kv_rule(_KV_LABEL_ANY, _KV_NOT_CALLED), re.IGNORECASE)),
    ("public-key", re.compile(_kv_rule(_KV_LABEL, ""), re.IGNORECASE)),
)
SUPPRESSED_LABEL = "kv-secret"   # the only rule that carries suppressions today

# The fixed token prefixes, named once so the rules below and _OWN_TAIL cannot drift:
# what a rule KEEPS is what the next rule reads back glued to the mask literal.
_PRE_GH = r"ghp_|gho_|ghu_|ghs_|ghr_"
_PRE_GHPAT = r"github_pat_"
_PRE_CFUT = r"cfut_?"
_PRE_SK = r"sk-(?:[a-zA-Z]+-)*"
_PRE_SLACK = r"xox[bpasr]-"
_PRE_AWS = r"AKIA"

# (label, compiled pattern, replacement) — labels feed the alarm/rotation ticket.
# Two labels may repeat (github); mask_text() de-duplicates.
PATTERNS = [
    # Real bodies are fixed-width: gh?_ 36 chars of base62, github_pat_ 82 with
    # underscores. The old {6,} floor also matched this module's OWN output
    # (`ghp_MASKED` is 6 legal chars), so every already-masked artefact re-armed
    # the alarm on the next read — the floors below end that loop too.
    ("github", re.compile(_B + r"(" + _PRE_GH + r")"
                          + _body(r"[A-Za-z0-9]", "{30,}")), r"\1MASKED"),
    ("github", re.compile(_B + r"(" + _PRE_GHPAT + r")"
                          + _body(r"[A-Za-z0-9_]", "{20,}")), r"\1MASKED"),
    ("cloudflare", re.compile(_B + r"(" + _PRE_CFUT + r")"
                              + _body(r"[A-Za-z0-9_.-]", "{6,}")), r"\1MASKED"),
    # wq#2300: the prefix repeats WHOLE dash-terminated letter groups (`sk-`,
    # `sk-ant-`, `sk-or-v1-`) instead of a greedy `[a-zA-Z-]*`. Greedy, group 1 ran
    # as far as the `{16,}` body would allow, so a body opening with a long letter
    # run was partly kept: `sk-ant-` + 24 letters masked to `sk-ant-xxxxxxxxMASKED`,
    # printing 8 characters of the secret into the artefact. The same 8 went into
    # scan()'s `prefix` — the field contracted to hold no value — and left
    # `value_sha256_12` computed over a truncated body, so the "sha256 a candidate
    # and compare" instruction every rotation ticket carries could never match.
    ("sk-key", re.compile(_B + r"(" + _PRE_SK + r")"
                          + _body(r"[A-Za-z0-9_-]", "{16,}")), r"\1MASKED"),
    ("slack", re.compile(_B + r"(" + _PRE_SLACK + r")"
                         + _body(r"[A-Za-z0-9-]", "{8,}")), r"\1MASKED"),
    ("aws", re.compile(_B + r"(" + _PRE_AWS + r")" + _body(r"[0-9A-Z]", "{16}")),
     r"\1MASKED"),
    # wq#2514: a credential embedded in a URL, `scheme://user:secret@host` — git's
    # credential store is one per line. Ported from read_dir_sweep's url_basic_auth
    # (wq#2479). Absent here, wq#2511's mask-hits context window printed a gitea token
    # in the clear two lines above the github values it was quoting. Same exclusions:
    # templates/env-refs, ALL_CAPS names and placeholder words are pointers, not values.
    #
    # wq#2760: two DSN shapes it missed, both leaks. `redis://:<pw>@host` carries a
    # password with NO user, so the `{1,64}` user floor refused the line outright. And a
    # raw `@` inside the password (`u:p@ss...@host`, unencoded, which DSN parsers take
    # up to the LAST `@` — urllib's rpartition) cut the body at the first `@`, under
    # the floor: no match, value in the clear. The body now admits `@` and backtracks
    # to the last `@host` before a `/` or space, over-masking a query-string `@` at
    # worst.
    ("url-auth", re.compile(
        _WB + r"([A-Za-z][A-Za-z0-9+.\-]+://[^\s:/@'\"`]{0,64}:)"
        # wq#2704: `\\*` because a transcript is JSONL — a shell-escaped `\${VAR}`
        # arrives as `\\${VAR}`, which the bare class read as a 16-char credential.
        r"(?!\\*[${%<*.]|(?i:password|passwd|secret|changeme|example|redacted|masked|xxxx+)@)"
        r"(?!os\.environ|os\.getenv|(?:process\.)?env\.)"
        r"(?![A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+@)"
        # The one rule that must NOT take _body(): it is anchored on the `@` ahead of
        # it, so the body cannot run past the credential into whatever follows, and
        # there is nothing for a bound to save it from. A bound would instead stop the
        # body at a literal INSIDE the value (`user:<live>MASKED@host`, what a sed
        # pass over the same line leaves behind) and then fail the `@` lookahead
        # outright — no match, no alarm. _matches() carries the fixed point here: the
        # self-output body (`MASKED`) is six characters, under the floor, so only the
        # exact-output test can fire, and only on our own output. (wq#2638)
        r"[^\s/'\"`]{8,}(?=@[A-Za-z0-9\[])"), r"\1MASKED"),
    # This one keys off an English word rather than a prefix, so it needs a different
    # boundary than `_B`. It needs a call test (wq#2288): without one,
    # `key = apply_key_allowlist(reg)` (node_render_gateway.py:418) reads as a 19-char
    # secret. That test is _KV_NOT_CALLED, and it is a lookahead on the value's SHAPE,
    # not on the character after it — see there for why the trailing form it replaces
    # silently dropped opaque values instead of merely narrowing the rule (wq#2632).
    #
    # wq#2297: the trailing boundary alone was not enough, because the LABEL half was a
    # naked keyword alternation with no left boundary. `KEY` matched inside `monkey:`,
    # and bare `KEY`/`PAT` matched the ordinary Python of `key = apply_key_allowlist(`,
    # `FAKE_PAT = "..."` and `top = max(v, key=v.count)`. Reading source code therefore
    # armed a pri-1 rotate-today alarm: 46 lines of harness/ alone matched, and the
    # cascade cost four tickets (wq#2288 on wq#2284, wq#2292 on wq#2282, wq#2297 on
    # wq#2290 — none of them a real credential).
    #
    # The label below is shape-based instead, ported from read_dir_sweep.py:38-60,
    # which solved this for the at-rest sweep (wq#2115, after an exact keyword list
    # missed 3 live credentials in wq#1483). The value floor stays 12, not that
    # sweep's 20: this rule also REDACTS, and over-masking an artefact is free while
    # under-masking one is not. Precision belongs in the label, not the floor.
    #
    # The sed copy in bin/mask-secrets cannot follow: POSIX ERE has no lookaround, so
    # it can express none of this. It has already missed both earlier fixes and is the
    # tool instinct 4 tells every agent to pipe through (ISSUE-LOG row 577).
    ("kv-secret", _KV_SECRET, r"\1MASKED"),
]


CONTEXT_CHARS = 120   # masked context kept either side of a hit
PREFIX_CHARS = 48     # of the kept, non-secret prefix


def value_sha12(value):
    """The 12 hex of sha256 that NAMES a value everywhere on the alarm path.

    scan()'s record, mask_replay/mask_triage's rows and — since wq#2341 — the rotation
    ticket's own title all have to derive it the same way, or a hash printed on a row
    cannot be compared against a candidate, which is the one thing those records exist
    to allow (wq#2288 arrived unactionable for exactly that reason).
    """
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:12]


# wq#2648: a tool that shortens what it prints (`text[:24] + "..."`) cuts a committed
# fixture mid-value, and `.` is in the kv-secret body charset, so the matched value is
# `<fixture prefix>...` — held nowhere in the tree, while the prefix is. Accounting may
# retry without the ellipsis: a truncated LIVE token's prefix is still in no committed
# file, so it stays burned.
TRUNCATION_MARK = "..."


def untruncated(value, floor):
    """`value` minus a trailing ellipsis if what is left is at least `floor` long, else
    None — a stub that short could be excused by almost any text."""
    if value.endswith(TRUNCATION_MARK):
        cut = value[:-len(TRUNCATION_MARK)]
        if len(cut) >= floor:
            return cut
    return None


# wq#2663: a printer that keeps `value[:14] + "..."` leaves a 9-char body after `cfut_`,
# under any body floor, so a preview of a committed 22-char fixture paged as burned.
# Retried WITH the rule's literal prefix the stub is only excused by text holding
# `cfut_<those 9 chars>`, i.e. a token whose head is itself committed. The body keeps
# its own floor so a long lead-in (`GITEA_TOKEN="`) cannot carry a 2-char stub.
TRUNCATED_BODY_FLOOR = 6


def untruncated_whole(prefix, body, floor):
    """`prefix` + `body` minus a trailing ellipsis when the whole reaches `floor` and
    the cut body reaches TRUNCATED_BODY_FLOOR, else None."""
    cut = untruncated(body, TRUNCATED_BODY_FLOOR)
    if cut and prefix and len(prefix + cut) >= floor:
        return prefix + cut
    return None


# wq#2339/wq#2341: values that are this masker's OWN output read back, by hash. A hit
# record matching one of these is the mask literal itself — a credential cannot survive
# having been replaced by it — so an alarm carrying nothing else is not a burn and is
# not worth a rotate-today row. wq#2298 stopped the table matching its own output, but
# only for the table ON DISK: a process that imported an older one keeps scoring with
# it for the life of the ticket (wq#2327, wq#2404), and the hash is what reaches the
# hits file either way.
#
# The first two follow MASK because a self-match records the body AFTER the kept prefix
# (`ghp_`+mask records `MASKED`; a kv label in front of a whole masked github value
# records `github_pat_MASKED`). The third is the pre-wq#2300 sk- backtrack tail, whose
# body that bug's greedy prefix truncated — no literal here can derive it; it is named
# by the wq#2339 triage of the storm's own mask-hits files.
SELF_OUTPUT_SHA12 = frozenset({
    value_sha12(MASK),                     # 20227a136ec3
    value_sha12("github_pat_" + MASK),     # 2c9e04df04aa
    "f50f2d7b79dd",
})

# Built by concatenation on purpose: a literal `AKIA`+16 in this file would be a
# credential-shaped string in tracked source, which is the exact class of text that
# filed half the rotation backlog (wq#2299, tests/test_token_injection.py).
_CANARY = "AKIA" + "Q" * 16

_SELF = live_code.fingerprint(__file__)


def _still_masks(mod):
    """A deployed table that no longer labels `_CANARY` is refused: silencing the
    tripwire is worse than a false page, and an emptied PATTERNS or a half-written
    deploy is indistinguishable from a table that simply stopped caring."""
    return bool(mod.mask_text(_CANARY)[1])


def deployed(path=None):
    """This module as its FILE defines it right now, not as the caller imported it.

    wq#2327: an fworker process scores every line of the stream with the rule table
    it imported at spawn. So a rule narrowed to kill a false positive does not reach
    the runs already going — they keep paging pri-1 and filing rotate-today tickets
    under the old table for the rest of their life. That is most of the 2026-09-20
    storm's tail, dated: wq#2297's attempt started 22:37:43, and it filed wq#2327
    (22:44:02), wq#2356 (23:11:43) and wq#2366 (23:25:49) — all three after the chain
    guard (345bf49, 22:57:04) and the provenance screen (281327c, 23:09:47) had
    landed, all three carrying the pre-22:40 ticket template, and none a credential.

    wq#2404: that life is a TICKET, not an attempt, which this docstring used to
    claim. wq#2278's process spawned attempt 1 at 22:14:43Z, started attempt 2 at
    23:00:53Z without respawning, and filed wq#2348 at 23:01:44Z off the table it had
    imported 47 minutes before the attempt that fired it began. Nothing about the
    window is bounded by an attempt boundary — which is also why the alarm
    bookkeeping keyed to one (fworker.mask_state) had to follow.

    Reloading is deliberately one-directional at the call site (fworker only ever
    DROPS a label the deployed table no longer reads, never adds one), so the worst
    a stale or broken file can do is leave the caller on its own table. The load,
    the fingerprint and the fallback live in live_code, shared with burn_filter's
    screen — the other half of the alarm path that used to freeze at spawn.
    """
    return live_code.deployed(sys.modules[__name__], _SELF, _still_masks, path)


# What this table can leave behind a kept prefix: the mask literal, optionally behind
# prefixes the table itself kept when an inner rule masked first. Only the FIXED token
# prefixes are ours to recognise — a kv label or a URL userinfo is arbitrary text, so
# admitting either would hand any match an excuse to be called our own output.
_OWN_TAIL = re.compile(r"(?:" + "|".join(
    (_PRE_GH, _PRE_GHPAT, _PRE_CFUT, _PRE_SK, _PRE_SLACK, _PRE_AWS)) + r")*" + MASK)


def _matches(rx, text):
    """Matches of `rx`, minus this module's own output read back (wq#2298).

    Every replacement is `<kept prefix>MASKED`, and that output is itself
    credential-shaped to several of the rules above: `cfut_MASKED` is `cfut_` plus
    six legal body characters, `PAT: github_pat_MASKED` is a 17-char kv value, and
    `sk-ant-xxxxxxxxMASKED` re-matches because the sk- prefix backtracks into its
    own body. So a masked artefact re-armed the pri-1 alarm on every later read,
    and the rotation ticket it filed was answered by a worker who grepped for
    `ghp_MASKED` — which alarmed again. That loop cost wq#2282, #2288, #2292 and
    #2298; the floors raised in wq#2292 only ended it for the two github rules.

    wq#2638: "ends at the mask literal" was the wrong test, and it dropped live
    credentials. A body class wide enough to spell `MASKED` runs straight through the
    literal, so `cfut_<live token>cfut_MASKED` — a live value with an already-masked
    one glued to its tail, which is any log tail, diff hunk or separator-free JSON —
    is ONE match ending in the literal. Dropping it dropped the live half too:
    hits `[]`, value in the clear, no alarm.

    The test is now what the rule always meant: after the kept prefix, the rest of the
    match must be OUR output and nothing else — mask literal, optionally behind the
    prefixes this table itself keeps (`_OWN_TAIL`). Those stack because the rules
    compose: url-auth reading a line the github rule already masked sees
    `https://x:ghp_MASKED`, whose tail is another rule's kept prefix and the literal,
    while `https://x:<live password>MASKED` — the same shape with a live value in
    front — is not, and alarms. `_body()` above is the structural half of the fix and
    carries every prefix rule; this half is what covers url-auth, which cannot take a
    bounded body (see there), and what stops the composed re-arm for all of them.

    wq#2674 re-checked the specific shape that motivated this docstring's own citations
    (kb/ISSUE-LOG.md row 724, `"...read back as cfut_" + "MASKED."`) plus the kv-secret
    twin ("check whether kv-secret has the same exposure"): trailing prose punctuation
    (`cfut_MASKED.`, `token: MASKED.`, `Signing Secret: MASKED).`) does not re-arm either
    rule, because the bounded body in `_body()` already refuses to enter the literal at
    all — there is no position left where a body run can absorb `MASKED` and then keep
    going into the punctuation behind it. Confirmed no residual gap for any PATTERNS
    label; regression pinned in test_masking.py::test_masked_output_with_prose_behind_it_does_not_re_arm,
    which already parametrizes over every rule including kv-secret via ADJACENT.
    """
    return [m for m in rx.finditer(text)
            if not _OWN_TAIL.fullmatch(m.group(0)[len(m.group(1)):])]


def _sub(rx, repl, text):
    """(text, n) — rx.subn() over _matches() only, so mask_text() and scan() agree
    on which matches are real without either re-deriving the rule."""
    out, pos = [], 0
    matches = _matches(rx, text)
    for m in matches:
        out.append(text[pos:m.start()])
        out.append(m.expand(repl))
        pos = m.end()
    out.append(text[pos:])
    return "".join(out), len(matches)


def mask_text(text):
    """(masked_text, [labels that fired]). Order of labels follows PATTERNS."""
    hits = []
    for label, rx, repl in PATTERNS:
        text, n = _sub(rx, repl, text)
        if n and label not in hits:
            hits.append(label)
    return text, hits


def suppressions(text):
    """[record] for every kv-secret candidate a suppression DROPPED (wq#2677).

    wq#2632 narrowed the call test and left the failure direction alone: a candidate a
    suppression refuses leaves no mask, no label and no scan record, and a dropped
    match is indistinguishable from clean text. So an overbroad carve-out reads as
    silence — which is exactly how wq#2288's lookahead survived a whole PR cycle while
    suppressing every opaque value glued to a `(`. A record per suppression makes the
    carve-out's cost countable after the fact: mask_triage can ask what the rule
    decided not to alarm on, and an overbroad one shows up as a pile of rows.

    The record carries the suppression reason alongside the same three fields a hit
    record identifies a value by — prefix, value_len, value_sha256_12. It carries NO
    `context`: a suppressed candidate is by definition not masked in the output text,
    so a window cut around it would print in the clear the very value the rule declined
    to mask, which would make the audit trail a leak. The hash still names the value
    for anyone holding a candidate to compare.

    A record is emitted only where the real rule produces no overlapping match, so a
    label that backtracks into a shorter form the rule DOES read (`Signing Secret:`
    via the bare-noun arm) is a hit, not a suppression. De-duplicated per call by
    (reason, value): one line repeating a pointer ten times is one thing suppressed.
    """
    kept = [(m.end(1), m.end()) for m in _matches(_KV_SECRET, text)]
    records, seen = [], set()
    for m in _matches(_KV_CANDIDATE, text):
        lo, hi = m.end(1), m.end()
        if any(start < hi and lo < end for start, end in kept):
            continue
        reasons = [name for name, rx in _KV_SUPPRESSIONS if not rx.match(text, m.start())]
        if not reasons:
            # The rule reads this span some other way (a different start, or _matches
            # screening our own output there). Nothing was suppressed; record nothing
            # rather than a row no reason explains.
            continue
        value, reason = text[lo:hi], "+".join(reasons)
        if (reason, value) in seen:
            continue
        seen.add((reason, value))
        records.append({
            "label": SUPPRESSED_LABEL,
            "suppressed": reason,
            "prefix": m.group(1)[-PREFIX_CHARS:],
            "value_len": len(value),
            "value_sha256_12": value_sha12(value),
        })
    return records


def scan(text, include_suppressed=False):
    """(masked_text, [hit record]) — wq#2288. mask_text() answers "did a credential
    print?"; scan() answers "WHICH one?", which is what a rotation ticket needs and
    what the first tripwire ticket (wq#2288, fired on wq#2284) could not answer: the
    labels were all that ever reached disk, so nothing in the run dir identified the
    burned value.

    A record carries the kept prefix (the part the mask leaves — a key name, `ghp_`,
    `AKIA`), the value's length, and 12 hex of its sha256: enough to NAME the
    credential by hashing a candidate, never enough to use it. `context` is a window
    of the FULLY masked line, so it shows the command or file the value printed from
    — the leak path — with every pattern in the window already replaced. The window is
    cut after masking, never before: cutting first is what let a token escape the
    replace in wq#1441 (see tests/test_read_dir_sweep.py).

    `include_suppressed` appends suppressions()'s rows — what the rule decided NOT to
    alarm on (wq#2677). It is OFF by default because an empty record list is the
    assertion the alarm's own read-path tests make ("this file does not read as a
    credential"), and a suppression is precisely not that: it must never be able to
    turn a clean file into an alarm. Callers that want the audit trail ask for it; the
    escalation predicates (fworker.burned_hashes) skip every row carrying `suppressed`.
    """
    matches, cur = [], text
    for label, rx, repl in PATTERNS:
        # finditer then sub on the same string, pattern by pattern, so the records
        # follow exactly the sequence of substitutions mask_text() performs: a value
        # an earlier pattern already shortened to `<prefix>MASKED` no longer matches
        # a later one, and must not be recorded as if it had.
        for m in _matches(rx, cur):
            matches.append((label, m.group(1), m.group(0)[len(m.group(1)):]))
        cur = _sub(rx, repl, cur)[0]

    records, cursor = [], {}
    for label, prefix, value in matches:
        marker = prefix + "MASKED"
        at = cur.find(marker, cursor.get(marker, 0))
        if at < 0:
            context = ""   # a later pattern reshaped the prefix; degrade, never leak
        else:
            cursor[marker] = at + len(marker)
            context = cur[max(0, at - CONTEXT_CHARS):at + len(marker) + CONTEXT_CHARS]
        records.append({
            "label": label,
            "prefix": prefix[-PREFIX_CHARS:],
            "value_len": len(value),
            "value_sha256_12": value_sha12(value),
            "context": context,
        })
    if include_suppressed:
        # Read off the RAW text, not `cur`: a suppressed candidate is unmasked in both,
        # but the raw text is what the rule actually judged.
        records.extend(suppressions(text))
    return cur, records
