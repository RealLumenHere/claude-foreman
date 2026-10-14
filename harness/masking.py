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

# The fixed token prefixes, named once so the rules below and _OWN_TAIL cannot
# drift: what a rule KEEPS is what the next rule reads back glued to the mask
# literal.
_PRE_GH = r"ghp_|gho_|ghu_|ghs_|ghr_"
_PRE_GHPAT = r"github_pat_"
_PRE_CFUT = r"cfut_?"
_PRE_SLACK = r"xox[bpasr]-"
_PRE_AWS = r"AKIA"
# wq#2924: `sk-` takes at most ONE qualifier, and only one a provider actually
# issues: Anthropic `ant`, OpenRouter `or`, OpenAI `proj`/`svcacct`/`admin`/`None`.
# The prefix used to repeat any `[a-zA-Z]+-` unit, the one prefix in this table that
# was not a fixed literal, so it grew INTO the body — and a kept prefix is the part a
# mask prints. Any body opening with letters then a dash handed those letters to
# group 1: `sk-proj-` + a base64url body that happens to open `Ab-` printed `Ab`, and
# a letters-led body running into a second `sk-` printed everything before it.
# wq#2913 refused a FOREIGN prefix inside a unit, which fixed that shape for the
# other rules and left sk-key's own. A fixed vocabulary closes the class: an unknown
# qualifier is now masked as body, which only widens coverage.
_PRE_SK = r"sk-(?:(?:ant|or|proj|svcacct|admin|None)-)?"
# wq#3825: five families bin/mask-secrets, handle-store.js and Horus redact.js all
# learned from wq#3716 and masking.py never did — the fourth copy the lockstep missed.
_PRE_GOOGLE = r"AIza"
# One bounded qualifier, same shape as _PRE_SK's fixed vocabulary and for the same
# reason (wq#2924): an unbounded `[a-z]+-` repeated group is the prefix that grows
# into the body. Tailscale's real qualifiers (`auth-`, `client-`, `ephemeral-`...) are
# not a closed set the way sk-'s providers are, so the cap is a length bound instead
# of a vocabulary — long enough for every qualifier seen, short enough that a body
# opening with lowercase letters then a dash cannot ride the group past the floor.
# wq#3833: the cap is 14 because the kept qualifier plus its dash must stay UNDER the
# tailscale body floor (16). The group is optional, so a re-read of our own output
# `tskey-<qualifier>-MASKED` can also take the qualifier as the body. At the old cap of
# 20, a 15-20 letter qualifier made that body reach the floor, mask(mask(x)) rewrote
# its own output, and the tripwire paged a pri-1 rotation over a probe's kept label
# (wq#3830, `tskey-` + 20 letters + `-`). A longer lowercase run is now masked as body.
_PRE_TAILSCALE = r"tskey-(?:[a-z]{1,14}-)?"
_PRE_GROQ = r"gsk_"


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
    truncated at the literal and, below the floor, stops matching outright — the
    floored quantifier simply declines the position, same as any other body too
    short for its rule. For a random base62 body that is ~1 in 62**6 per position,
    the same order of cost wq#2298 already priced in.

    wq#2922: unguarded, that stop WAS the silent direction — the whole rule failing
    to match reads identically to clean text, no different from wq#2288's suppressed
    call test going quiet for a full PR cycle. `_orphan_matches()` is the backstop
    that makes the claim below true instead of aspirational: it walks the same
    guarded run with no floor, so it always matches however short, and treats a run
    that stops on the literal — with more of the same charset glued past it, not a
    clean `_OWN_TAIL` re-read of our own output — as an alarm with no mask and no
    context (masking a body this short can re-swallow `<prefix>MASKED` and revive
    the wq#2638 leak the guard exists to prevent). That is now paid in the direction
    that alarms rather than the one that goes silent.

    The literal is matched case-SENSITIVELY (`(?-i:)`) even where the rule around it
    is IGNORECASE: kv-secret is, and a case-blind lookahead there would also stop the
    body at ordinary English — a passphrase holding the word `unmasked` would be cut
    at it, under-masking a live value to buy nothing, since only the exact upper-case
    literal is ever this module's output. (The example is described rather than
    written out for the reason test_the_mask_suite_does_not_arm_its_own_tripwire
    gives: a whole credential shape in tracked source files a rotation ticket.)

    wq#2913: the MASK literal is the ONLY thing that stops a body run. The
    foreign-prefix stop this parameter used to carry (wq#2720, narrowed by wq#2848,
    patched again by wq#2899) is gone with the whole stop-based design — see
    mask_text() for why a stop can only ever subtract coverage, and what replaced it.
    """
    return r"(?:(?!(?-i:" + MASK + r"))" + charset + r")" + quant


def _floored_body(charset, floor):
    """`charset` reaching `floor`, the ordinary open-floor body."""
    return _body(charset, "{" + str(floor) + ",}")


# wq#2297 kv-secret label vocabulary, ported from read_dir_sweep.py:38-60 (wq#2115).
# A credential label is a NOUN, optionally qualified: `api key`, `Signing Secret`,
# `clientSecret`. The qualifier ends in a separator or a camelCase hump.
#
# wq#2806: `pass` only as an IDENTIFIER segment behind `_` (`NTFY_PASS`, `WEBAPP_PASS`,
# `TAPO_PASS` — two live agent/.env values printed in the clear under the old
# vocabulary), never in prose or a hyphenated compound: `first pass:` has a space in
# front, `verify-pass:` (harness/agents.yaml, a lane name) a dash, `bypass` no separator,
# and the lookbehind refuses all three. `salt` is langfuse's API-key hashing secret (`SALT:` in its
# compose file, 64 hex, in the clear); it is bare below because that is how it is
# labelled, and a prose `salt:` followed by a 12-char opaque run is not a shape the
# fleet corpus holds.
# `key` is a NAMED group so the identifier guard below can be conditional on it.
# The whole noun is named `nn` (and the bare one `nb`) for the hump-echo guard (wq#3324).
_KV_NOUN = r"(?P<nn>secret|token|(?P<kn>key)|credentials?|passphrase|password|passwd|(?<=_)pass|salt)"
# The hump test is case-SENSITIVE via `(?-i:)` even though the rule as a whole is
# IGNORECASE — that asymmetry is the whole trick: it admits `apiKey:` while refusing
# `monkey:`. Two letters minimum, since a one-letter qualifier is an English article
# ("a key: ..." is prose, not a leak).
#
# wq#2806: the hump is `(?=[A-Z][a-z])`, not `(?=[A-Z])`. A bare upper-case lookahead
# consumes nothing, so an ALL_CAPS identifier could be split ANYWHERE: `PUBLIC_KEY`
# read as `PUBL` + `IC_` + `KEY`, which slips past _KV_NOT_PUBLIC (it is tested at the
# start of the LAST qualifier, and `IC_` is not `public`). The depth-2 cap hid this;
# with deeper labels it masked `LANGFUSE_PUBLIC_KEY`, and `PUBLIC_KEY=<v>` alone
# matched on main already. A real hump is followed by a Capitalised word (`apiKey`,
# `clientSecret`, `APIKey`), never by another capital.
# wq#2806: one-letter segments are fine behind `_`/`-` (`X_API_KEY`); the two-letter
# minimum only guards the SPACE separator, where a lone letter is an English article.
# wq#2806 (perf, second pass): every alternative ends at a point the text FIXES — the
# first `_`/`-`, the first space, or the first capital — so a word has one reading and
# the `{0,7}` nesting cannot multiply. The earlier hump form `[A-Za-z0-9]{1,20}(?=[A-Z][a-z])`
# could end at ANY capital inside a mixed-case run, and a 554 KB base64 blob in a
# Commander log line (humps every few characters) took the rule past 8 s per window
# and stalled the sweep. Hump words are camelCase segments: `api`, `Key`, `client`,
# `OA`+`uth`, `API`; never a bare `Key` at the start (a bare noun stays _KV_BARE only).
_KV_WORD = r"(?:[A-Za-z][A-Za-z0-9]{0,20}[_-]{1,2}|[A-Za-z][A-Za-z0-9]{1,20} {1,2}|(?-i:(?:[A-Z][a-z0-9]+|[A-Z]{2,3}[a-z0-9]*|[a-z][a-z0-9]+)(?=[A-Z][a-z])))"
# a public key is not a credential, and infra dirs are full of them
_KV_NOT_PUBLIC = r"(?!pub(?:lic)?[ _-]{0,2}key\b)"
# Nouns unambiguous enough to stand alone. `key` and `pat` are deliberately absent:
# `key:` alone is ordinary YAML and an ordinary Python kwarg, and `PAT` alone is a
# variable name — both need a qualifier in front to read as a credential label. The
# `github_pat_` credential itself is caught by the dedicated github pattern above.
_KV_BARE = r"(?P<nb>secret|token|credentials?|passphrase|password|passwd|bearer|salt)"
# wq#2806: the label used to be `(WORD)? WORD NOUN` — at most two qualifiers — and it
# opens on `\b`. `_` is a word character, so a label can only ever be read from its
# FIRST segment, and a fleet identifier is 3-6 segments deep:
# `LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY`, `MCP_PG_READER_PASSWORD`,
# `R2_SECRET_ACCESS_KEY`, `CLAUDE_SHIM_API_KEY` — all live, all printed in the clear
# by this rule while the cruder sed copy (unanchored on the noun) read every one of
# them. Up to eight qualifiers now. The public-key carve-out keeps its place directly
# before the LAST qualifier, the only position it can occupy since the noun is last,
# so `..._PUBLIC_KEY` is refused at any depth (ops/wq2806-config-shape-mask/DESIGN.md).
_KV_DEPTH = r"{0,7}"
_KV_LABEL = (r"(?:" + _KV_WORD + r")" + _KV_DEPTH + _KV_NOT_PUBLIC + _KV_WORD + _KV_NOUN
             + r"|" + _KV_BARE)
# The same label with the public-key carve-out taken back out — one of the three
# inputs suppressions() below needs to see what this rule declined to read (wq#2677).
_KV_LABEL_ANY = (r"(?:" + _KV_WORD + r")" + _KV_DEPTH + _KV_WORD + _KV_NOUN + r"|" + _KV_BARE)
# wq#2806: an env-file identifier — ALL_CAPS, at least one `_` qualifier, ending in a
# STRONG noun. `KEY` and `PAT` stay out (`SORT_KEY=`, `PARTITION_KEY=` are ordinary
# config), and the whole thing is case-sensitive so no prose reaches it. This label is
# unambiguous enough to take a floor of 8 instead of 12: `MONGO_INITDB_ROOT_PASSWORD`
# and `WEBAPP_PASS` are 10 and 11 characters, live, and were under the general floor.
_KV_ENV_LABEL = (r"(?-i:[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*"
                 r"_(?:PASSWORD|PASSWD|PASSPHRASE|PASS|SECRET|TOKEN|SALT"
                 # a glued noun, ALL_CAPS so no hump can split it: Kibana's
                 # `XPACK_ENCRYPTEDSAVEDOBJECTS_ENCRYPTIONKEY` (dify compose, live).
                 # Only behind a qualifier that names a credential — `HOCKEY` stays out.
                 r"|(?:ENCRYPTION|SECRET|API|ACCESS|AUTH|PRIVATE|MASTER|SIGNING)KEY))")
# An env-var reference or a path is a POINTER to a credential, not the credential:
# `secret = os.environ["X"]`, `master key: /home/lumen/.ssh/id_ed25519`. Masking the
# pointer would hide the leak path the scan record exists to show.
# wq#2806: a URL joins the list, because `:` and `/` are now body characters and
# `api key: https://docs.example/keys` would otherwise reach the floor.
_KV_POINTER = (r"(?!os\.environ)(?!os\.getenv)(?!(?:process\.)?env\.)"
               r"(?!\$\{?[A-Z_])(?![~.]{0,2}/)(?!<)"
               r"(?![A-Za-z][A-Za-z0-9+.\-]*://)"
               # a redaction or template placeholder, the set url-auth already refuses:
               # `***REDACTED***`, `%s`, `your-token-here`, `changeme`. Two of them sat
               # in committed diffs/digests and armed the tripwire the moment `*` joined
               # the body charset and the env form's floor dropped to 8.
               # shell default-expansion `${FLEET_EXEC_SSH_KEY:-/path}` reads as label +
               # `:` + `-/path` now that labels go three deep (wq#2278 replay corpus);
               # no credential opens with the `:-` `:=` `:+` `:?` operator character.
               r"(?![-+?=])"
               # wq#3153: a regex wildcard then a sed delimiter is the PATTERN half of a
               # substitution, `s#^<LABEL>=.*#<LABEL>=$TOKEN#` (wq#3150's deploy
               # script rewriting API_BEARER_TOKEN in .env). Refusing it here loses nothing: the scan resumes at the
               # replacement's own label, so a literal written there still fires.
               # wq#3154: only when that replacement label IS this label. Keyed on the
               # 3-char opener alone it silenced any value that happened to start
               # `.*#`/`.+/` with no sed around it (wq#3156's contradiction).
               # wq#3157 is the judgment that ruling settles, and it lands on this exact
               # regex from the other side — wq#3154 and wq#3157 derived it independently
               # (ISSUE-LOG #1063/#1065), so it stays one guard, not two dialects. The
               # ruling, for whoever widens this next: wq#3153's suppression was ARGUED
               # from a two-sided structure and SPELLED as three characters of value
               # shape, and the distance between those two is the whole defect. A floor
               # or count tightening cannot close it — a sed pattern half and a
               # credential body are the same length and the same charset — so the guard
               # has to test the structure it claims. Coverage for every delimiter
               # `_KV_BODY` can hold is test_every_sed_delimiter_in_the_body_charset_is
               # _covered; ops/wq3157-sed-guard/probe_sed_guard.py fails in BOTH
               # directions — reverting to wq#3153 breaks it, and so does dropping the
               # suppression altogether.
               r"(?!\.[*+][#/@:!~%|,]\^?(?P=kl)[:=])"
               r"(?![*%])(?!(?i:your[-_]|changeme|change[-_]me|example|redacted|masked"
               r"|placeholder|xxxx+)\b)")
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
# wq#2806: `[A-Za-z0-9._-]` was the charset of the TOKENS the rule was written for.
# A fleet PASSWORD carries `+ ! # : / =` (GITEA_PASSWORD, live: the body stopped at its
# `+` and the mask kept the 12 characters behind it; GHOST_ADMIN_KEY is `id:secret` and
# the 64-char secret half survived whole). An explicit allow-list, still with no
# whitespace, quote, backtick or `, ; ( ) [ ] { } < > | \ & ?`, so the delimiters of
# JSON, YAML, shell and Python still end a value and _KV_NOT_CALLED keeps its meaning.
_KV_BODY = r"[A-Za-z0-9._\-+/=:!#$%^*@~]"
# wq#2806: the quote class around the separator admits `'` — `password: 'v'` is the
# YAML style, and the old `["\s]*` never opened on it.
# The whitespace is `[ \t]`, not `\s`: a label whose value sits on the NEXT line is a
# YAML mapping (`verify-pass:\n    primary_tuple: ...`), and the old class read the
# first key of that mapping as the value.
_KV_SEP = r'\b["\' \t]*[:=]["\' \t]*'


# wq#2806: a `key`-labelled snake_case CONSTANT is an identifier, not a credential.
# `PROBE_TICKETS_FILED_KEY = "probe_tickets_filed_x"` (a dict key name, sentinel_probes
# .diff) reached the rule the moment labels could be read three segments deep — `key`
# is the one noun that names a config/dict slot as often as a credential, which is why
# it was never allowed bare. The value shape decides: all-lowercase words joined by
# `_` with nothing else is how source names things and how no provider mints keys.
# Guarded for `key` labels ONLY (a password may well be `my_db_pass_1`), tested from
# the rule's START as a lookahead over label+separator+value, and recorded as
# suppression "identifier" so an overbroad reading shows up as rows, not silence.
_KV_IDENT_VALUE = r"_*[a-z0-9]+(?:_[a-z0-9]+)+"   # `_stale_probe_tickets` is one too
# wq#3322: a composite key is described by naming its parts — `deviceKey = bus:port:vid:pid.`
# in wq#3314's report of ewf-flasher's api.ts — and that sentence paged as a burned
# credential. LETTERS-only segments joined by `:`, counted: a `:`-joined value with a
# digit (GHOST_ADMIN_KEY's hex `id:secret`) is not a field list and still fires.
# Either shape may end a sentence, so trailing prose punctuation is allowed where the
# body then ENDS, the same allowance _KV_NOT_SELF makes.
_KV_KEY_FIELDS = r"[a-z]{1,20}(?::[a-z]{1,20}){1,7}"
# wq#3456: prose names a key by the env var that holds it — `LiteLLM key = ` + the
# 18-char ALL_CAPS `..._KEY` name + ` in agent/.env.` (claude_memory.md:248, sha12
# 1154a18d6ea3) — and leakwatch filed it as a burned credential (wq#3449). LETTERS-only
# segments joined by `_`, counted like the guards above: a digit anywhere (`AKIA`-style
# IDs, base32 secrets) or a run past the caps is a value and still fires.
_KV_ENV_NAME = r"[A-Z]{1,20}(?:_[A-Z]{1,20}){1,7}"
# A CONDITIONAL on the `kn` group (Python `(?(name)yes)`): it only runs once a label has
# matched and only when that label's noun was `key`. The first cut of this guard was a
# lookahead at the rule's START that re-ran the whole depth-7 label grammar at every
# word boundary of the line; on a 1.2 MB Commander log line that doubled the rule's cost
# (450 ms -> 890 ms) and the all-rules sweep of /home/lumen/logs did not finish.
_KV_NOT_IDENT = (r"(?(kn)(?!(?-i:" + _KV_IDENT_VALUE + r"|" + _KV_ENV_NAME + r"|" + _KV_KEY_FIELDS
                 + r")[.,;!?]*(?![A-Za-z0-9._\-+/=:!#$%^*@~])))")
# wq#2933: a value that repeats its own label is a self-name placeholder, not a
# credential. `agent/litellm_config.yaml` writes provider keys that way (ISSUE-LOG rows
# 871/914: LiteLLM's get_secret resolves the name from the container env), and quoting
# that line armed the tripwire twice: wq#2923 as a 15-char hit, then again as 16 chars
# when a grep pattern put a regex `.` behind the name. Trailing prose/regex punctuation
# is allowed only where the body then ENDS, so `LABEL: LABEL.<opaque>` still fires.
# `kl` is the label group _kv_rule() names; recorded as suppression "self-name".
_KV_NOT_SELF = r"(?!(?P=kl)[.,:;!?]*(?![A-Za-z0-9._\-+/=:!#$%^*@~]))"
# wq#3127: the same echo one step removed — source passing a variable or attribute whose
# own name carries the label: the secret key kwarg handed `self.` plus the label, or the
# credentials kwarg handed the label plus `_dict` (upstream litellm's langfuse.py, read
# by wq#3122; three such values filed as burned). The label must sit between `.`/`_`
# seams with at least one more segment, and every other segment is lowercase LETTERS
# only, at most 20: a label-prefixed hex run has digits and still fires, and a bare
# `LABEL: LABEL` stays self-name's row. A value followed by `(` is left to "call", so
# the two copies of the kv rule keep agreeing on what they suppress (the daemon has no
# echo guard). Recorded as suppression "echo".
# wq#3133: every repetition in this guard is COUNTED. The first cut spelled the
# qualifier run `+` and the tail run `*`, so `self.` plus the label plus an arbitrarily
# long chain of `_`-joined lowercase words rode the guard past the alarm in full — no
# mask, no record, nothing for rotation to find, and no other PATTERNS entry backstops a
# bare `label=value` line. That is strictly worse than the pre-echo guard set, which
# flagged the same line (wq#3132's contradiction of wq#3127). The caps are the observed
# shapes plus headroom, not one segment: the langfuse value this was written for is
# `self.upstream_langfuse_<label>`, three qualifier segments deep already. A longer run
# of lowercase words is no longer an identifier naming the label — it is a value, and a
# value gets read.
_KV_ECHO_SEG = r"[a-z]{1,20}"
_KV_ECHO_GAP = r"_{0,3}"                    # `__private` seams, bounded like the rest
_KV_ECHO_PRE = _KV_ECHO_GAP + r"(?:" + _KV_ECHO_SEG + r"[._]" + _KV_ECHO_GAP + r"){1,3}"
_KV_NOT_ECHO = (r"(?!(?:(?-i:" + _KV_ECHO_PRE + r")(?P=kl)(?-i:(?:[._]"
                + _KV_ECHO_SEG + r"){0,2})|(?P=kl)(?-i:(?:[._]" + _KV_ECHO_SEG + r"){1,2}))"
                r"(?![A-Za-z0-9._\-+/=:!#$%^*@~(]))")
# wq#3324: the same echo spelled in camelCase. JS/TS source hands an object-literal key
# a local named after it — `access_` + `token: newAccessToken,` and its refresh twin, in
# the OAuth token handler wq#3317 grepped; both values were filed as burned. The value is
# a lowercase-led identifier of letters only, at most three humps before the last, and
# the LAST hump is the label's own noun (`nn`, or `nb` for a bare label), capitalised
# and all lowercase after its first letter, with the body ending there. A digit, a run
# past the caps, an ALL_CAPS noun, or anything glued behind the noun is a value and gets
# read. A backreference to the noun group the label did not use fails, so the
# alternation picks whichever one matched. Recorded as suppression "hump-echo".
_KV_NOT_HUMP_ECHO = (r"(?!(?-i:[a-z]{1,20}(?:[A-Z][a-z]{1,20}){0,3}"
                     r"(?=[A-Z][a-z]{1,19}(?![A-Za-z0-9._\-+/=:!#$%^*@~(])))"
                     r"(?:(?P=nn)|(?P=nb))(?![A-Za-z0-9._\-+/=:!#$%^*@~(]))")


def _kv_rule(label, guards, floor="{12,}"):
    """The kv-secret rule, parameterised by which of its suppressions are in force.

    wq#2677: the rule and the probe that records what it suppressed are built from one
    string so they cannot drift. A carve-out added to the rule but not to the probe
    would be invisible to the audit trail the probe exists to produce, which is the
    failure mode this closes — not a variant of it.
    wq#2694 landed on main after this branch's base: the leading boundary must accept
    the position right after our own mask literal too (_WB), not just a plain \b, or a
    kv-secret label glued behind "MASKED" goes blind the same way url-auth did.
    wq#2720 gave this body no foreign-prefix stop while every fixed-prefix rule had
    one — a labelled value that simply IS another rule's shape (`"API_KEY":
    "sk-ant-<token>"`) has to resolve HERE too, and a stop at position zero would
    refuse it outright, floor and all. wq#2913 made that exception the rule: no body
    anywhere takes a foreign stop, and every rule reads the ORIGINAL text the way
    burn_filter.matched_values() always did (see mask_text()).
    """
    return (_WB + r'((?P<kl>' + label + r')' + _KV_SEP + r')' + guards
            + _body(_KV_BODY, floor))


_KV_SECRET = re.compile(_kv_rule(_KV_LABEL, _KV_POINTER + _KV_NOT_CALLED + _KV_NOT_IDENT
                                 + _KV_NOT_SELF + _KV_NOT_ECHO + _KV_NOT_HUMP_ECHO),
                        re.IGNORECASE)
# wq#2806: the env-identifier form of the same rule, floor 8 (see _KV_ENV_LABEL). It
# shares the label string "kv-secret" the way the two github rules share theirs: one
# alarm class, one rotation-ticket shape, one set of suppressions to reason about.
_KV_SECRET_ENV = re.compile(_kv_rule(_KV_ENV_LABEL, _KV_POINTER + _KV_NOT_CALLED
                                     + _KV_NOT_SELF, "{8,}"))
# wq#2806: XML — `<password>v</password>`, ClickHouse's users.d/*.xml (the wq#2754
# leak: cat-ing it mid-rotation printed the new password in the clear under every rule
# above, because no `:`/`=` follows the label). The tag name carries a credential noun
# as a segment (`<password>`, `<password_sha256_hex>`, `<access_key>`, `<api-token>`);
# the body is XML text, so it cannot hold `<`, and it is anchored on the `</` that closes
# it — the url-auth argument: an anchored body cannot run past its value, so it takes no
# _body() bound, and `_matches()` screens `<password>MASKED</password>` as own output.
_XML_NOUN = r"(?:secret|token|key|credentials?|passphrase|password|passwd|pass|salt)"
_XML_TAG = (r"(?:[A-Za-z0-9._\-]*[._\-])?" + _XML_NOUN
            + r"(?:[._\-][A-Za-z0-9._\-]*)?")
# No leading boundary: `<` is not a word character, so `\b` can never sit in front of
# it, and nothing that precedes a `<` can be the tail of an identifier — the `<` IS
# the boundary, on both sides of our own mask literal (wq#2694).
_XML_SECRET = re.compile(r"(<" + _XML_TAG + r">)[^<\s]{8,}(?=</)", re.IGNORECASE)
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
    ("identifier", re.compile(_kv_rule(_KV_LABEL_ANY, _KV_NOT_IDENT), re.IGNORECASE)),
    ("self-name", re.compile(_kv_rule(_KV_LABEL_ANY, _KV_NOT_SELF), re.IGNORECASE)),
    ("echo", re.compile(_kv_rule(_KV_LABEL_ANY, _KV_NOT_ECHO), re.IGNORECASE)),
    ("hump-echo", re.compile(_kv_rule(_KV_LABEL_ANY, _KV_NOT_HUMP_ECHO), re.IGNORECASE)),
)
SUPPRESSED_LABEL = "kv-secret"   # the only rule that carries suppressions today

# (label, compiled pattern, replacement) — labels feed the alarm/rotation ticket.
# Two labels may repeat (github); mask_text() de-duplicates.
PATTERNS = [
    # Real bodies are fixed-width: gh?_ 36 chars of base62, github_pat_ 82 with
    # underscores. The old {6,} floor also matched this module's OWN output
    # (`ghp_MASKED` is 6 legal chars), so every already-masked artefact re-armed
    # the alarm on the next read — the floors below end that loop too.
    ("github", re.compile(_B + r"(" + _PRE_GH + r")"
                          + _floored_body(r"[A-Za-z0-9]", 30)), r"\1MASKED"),
    ("github", re.compile(_B + r"(" + _PRE_GHPAT + r")"
                          + _floored_body(r"[A-Za-z0-9_]", 20)), r"\1MASKED"),
    ("cloudflare", re.compile(_B + r"(" + _PRE_CFUT + r")"
                              + _floored_body(r"[A-Za-z0-9_.-]", 6)),
     r"\1MASKED"),
    # wq#2300: the prefix repeats WHOLE dash-terminated letter groups (`sk-`,
    # `sk-ant-`, `sk-or-v1-`) instead of a greedy `[a-zA-Z-]*`. Greedy, group 1 ran
    # as far as the `{16,}` body would allow, so a body opening with a long letter
    # run was partly kept: `sk-ant-` + 24 letters masked to `sk-ant-xxxxxxxxMASKED`,
    # printing 8 characters of the secret into the artefact. The same 8 went into
    # scan()'s `prefix` — the field contracted to hold no value — and left
    # `value_sha256_12` computed over a truncated body, so the "sha256 a candidate
    # and compare" instruction every rotation ticket carries could never match.
    ("sk-key", re.compile(_B + r"(" + _PRE_SK + r")"
                          + _floored_body(r"[A-Za-z0-9_-]", 16)), r"\1MASKED"),
    ("slack", re.compile(_B + r"(" + _PRE_SLACK + r")"
                         + _floored_body(r"[A-Za-z0-9-]", 8)), r"\1MASKED"),
    ("aws", re.compile(_B + r"(" + _PRE_AWS + r")"
                       + _body(r"[0-9A-Z]", "{16}")),
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
        # wq#2931: a `<name>` template placeholder behind a token prefix — `ghp_` glued
        # to `<body>` is how a docstring spells "a github value here", and wq#2913's
        # own docstring paged pri-1 on it. wq#2954: the placeholder must BE the rest of
        # the body, running straight to the `@`, and only a prefix may precede it:
        # short lowercase segments each closed by `_`/`-` (`ghp_`, `github_pat_`,
        # `sk-ant-`). Excusing a placeholder anywhere in the body let a live value
        # glued to an annotation (`<live><truncated>@host`, a hand-redaction that
        # left the live head in place) through with no mask and no alarm.
        # wq#2977: "a token prefix" is the closed vocabulary this table keeps (_PRE_*)
        # or ONE short lowercase segment (`tok_`); the earlier `{0,3}` of free segments
        # let a live head of up to 21 characters (`abc123-def456_ghi789-<x>@host`) ride
        # in front of the annotation unmasked, with no alarm.
        r"(?!(?:(?:" + _PRE_GH + r")|(?:" + _PRE_GHPAT + r")|(?:" + _PRE_CFUT + r")|(?:"
        + _PRE_SK + r")|(?:" + _PRE_SLACK + r")|(?:" + _PRE_AWS + r")|[a-z][a-z0-9]{0,5}[_-])?"
        r"<[A-Za-z_][A-Za-z0-9_-]*>@)"
        # The one rule that must NOT take _body(): it is anchored on the `@` ahead of
        # it, so the body cannot run past the credential into whatever follows, and
        # there is nothing for a bound to save it from. A bound would instead stop the
        # body at a literal INSIDE the value (`user:<live>MASKED@host`, what a sed
        # pass over the same line leaves behind) and then fail the `@` lookahead
        # outright — no match, no alarm. _matches() carries the fixed point here: the
        # self-output body (`MASKED`) is six characters, under the floor, so only the
        # exact-output test can fire, and only on our own output. (wq#2638)
        r"[^\s/'\"`]{8,}(?=@[A-Za-z0-9\[])"), r"\1MASKED"),
    # wq#2806: XML text bodies (see _XML_SECRET) and the env-identifier kv form, which
    # runs BEFORE the general kv-secret so the stricter label claims the line first; a
    # value both would read is masked once and recorded once (scan() sees `MASKED`
    # behind the label on the second rule and _matches() screens it).
    ("xml-secret", _XML_SECRET, r"\1MASKED"),
    ("kv-secret", _KV_SECRET_ENV, r"\1MASKED"),
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
    # wq#3825: the five families bin/mask-secrets (and handle-store.js, Horus
    # redact.js) carry and this table never did. Fixed-prefix shapes (google,
    # tailscale, groq) follow the same `_B` + floored-body shape as github/
    # cloudflare/slack/aws above, but are deliberately NOT added to _OWN_TAIL,
    # _PRE_ANY or _ORPHAN_SHAPES: those three are exact-equality-tested against
    # token_engine_daemon.py's own mirrored copies
    # (test_mask_underfloor_invariant.py), and wiring a fifth copy into that daemon
    # is outside wq#3825's port-into-masking.py scope. The gap this leaves (an
    # under-floor value of one of these three families passes through unmasked and
    # unalarmed, the same class wq#2922 closed for the other six) is real and
    # un-backstopped — a scoped follow-up, not a silent one, now that it is named
    # here instead of merely absent.
    ("google", re.compile(_B + r"(" + _PRE_GOOGLE + r")"
                          + _body(r"[0-9A-Za-z_-]", "{35}")), r"\1MASKED"),
    ("tailscale", re.compile(_B + r"(" + _PRE_TAILSCALE + r")"
                             + _floored_body(r"[A-Za-z0-9-]", 16)), r"\1MASKED"),
    ("groq", re.compile(_B + r"(" + _PRE_GROQ + r")"
                        + _floored_body(r"[A-Za-z0-9]", 20)), r"\1MASKED"),
    # A bearer token keeps the "Bearer " word as its label-like prefix, same reason
    # kv-secret keeps the kv label: the value alone is an opaque blob no narrower
    # rule would otherwise catch, so what makes it readable as a credential is the
    # word in front of it, not a fixed short literal glued to the body.
    ("bearer", re.compile(_WB + r"([Bb]earer[ \t]+)"
                          + _floored_body(r"[A-Za-z0-9._~+/=-]", 20)), r"\1MASKED"),
    # A JWT has no kept prefix at all: `eyJ` is the base64 of `{"`, part of the
    # secret shape itself, not a label the way `ghp_`/`AKIA` are — so group 1 is an
    # empty capture and the whole three-segment match is replaced by the bare
    # literal, same as the sed copy's unconditional `s/.../\x01/g`.
    ("jwt", re.compile(_WB + r"()eyJ" + _floored_body(r"[A-Za-z0-9_-]", 8) + r"\."
                       + _floored_body(r"[A-Za-z0-9_-]", 8) + r"\."
                       + _floored_body(r"[A-Za-z0-9_-]", 8)), r"\1MASKED"),
]


# wq#2922: the six fixed-token-prefix rules above, named again here (label, prefix,
# body charset, floor) so the orphan-prefix backstop's floor and charset cannot drift
# from the PATTERNS entry it backs — the same drift concern wq#2848's now-removed
# _SHAPES_BY_LABEL table existed to close. kv-secret, xml-secret and url-auth carry no
# FIXED short literal prefix (a label noun or a URL scheme is not "a known prefix"),
# so they are not in scope for this backstop and are not listed.
_ORPHAN_SHAPES = [
    ("github", _PRE_GH, r"[A-Za-z0-9]", 30),
    ("github", _PRE_GHPAT, r"[A-Za-z0-9_]", 20),
    ("cloudflare", _PRE_CFUT, r"[A-Za-z0-9_.-]", 6),
    ("sk-key", _PRE_SK, r"[A-Za-z0-9_-]", 16),
    ("slack", _PRE_SLACK, r"[A-Za-z0-9-]", 8),
    ("aws", _PRE_AWS, r"[0-9A-Z]", 16),
]
_ORPHAN_PATTERNS = [
    (label, re.compile(_B + r"(" + prefix + r")(" + _body(charset, "*") + r")"),
     re.compile(_body(charset, "*")), floor)
    for label, prefix, charset, floor in _ORPHAN_SHAPES
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

# Any fixed token prefix, and the longest one, for read()'s inside-a-span re-read.
_PRE_ANY = re.compile(r"(?:" + "|".join(
    (_PRE_GH, _PRE_GHPAT, _PRE_CFUT, _PRE_SK, _PRE_SLACK, _PRE_AWS)) + r")")
_PRE_LONGEST = len("github_pat_")


def _unbounded(rx):
    """`rx` with its leading word boundary removed, or `rx` unchanged if it opens on
    a character that is its own boundary (xml-secret's `<`).

    Derived from the compiled rule rather than declared beside it, so it cannot
    drift from the rule it is a variant of. read() uses this ONLY inside text a
    boundary-qualified rule already claimed — see there for why that is sound.
    """
    for boundary in (_B, _WB):
        if rx.pattern.startswith(boundary):
            return re.compile(rx.pattern[len(boundary):], rx.flags)
    return rx


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
    return [m for m in rx.finditer(text) if not _own_output(m)]


def _own_output(m):
    """`m` is this module's own replacement read back — see _matches()."""
    return bool(_OWN_TAIL.fullmatch(m.group(0)[len(m.group(1)):]))


def _orphan_matches(text):
    """[(label, prefix, value)] — wq#2922's fail-loud backstop.

    `_body()`'s MASK-literal guard (wq#2638) is unconditional inside the floor too:
    a real value that happens to CONTAIN the literal below its rule's own floor
    cannot reach `{floor,}` reps at all, so the floored rule does not shorten the
    match, it declines the position outright — the whole rule silently fails and the
    live value passes through in the clear with no record (measured 96/96 in
    reports/wq2868_probe_underfloor_class.py section B; wq#2846 reaches the same
    silence a different way, truncating a field below the floor before this module
    ever sees it). Deferring the guard past the floor here, the way wq#2848 deferred
    the (now-removed) foreign-prefix guard, is the wrong fix: a body allowed to cross
    the literal swallows `<prefix>MASKED` again and `_matches()`'s own-tail screen
    drops the whole match, secret included — the wq#2638 leak, restored.

    So this does not extend the floored rule; it walks the SAME guarded charset run
    with no floor at all, which therefore always matches, however short — there is no
    length left for the match to fail at. That short run only ever alarms (never
    masks: a value this short may be mostly the mask literal itself, and redacting it
    risks the same re-swallow) when the run stopped ON the literal — not merely
    short for some unrelated reason, a foreign prefix's own text or nothing to do
    with the guard at all, which is not this class and is left alone — AND the two
    real halves either side of the literal, put back together, would have reached
    the rule's OWN floor if the literal had not been sitting between them. That
    second test is what a bare "does the charset continue past the literal" check
    got wrong: prose regularly resumes right behind an already-masked value
    (`the value read back as cfut_MASKED.`, test_masked_output_with_prose_behind
    _it_does_not_re_arm's own fixture — cloudflare's charset admits the trailing
    `.`), and a single stray character must not re-arm the alarm wq#2638 built this
    guard to keep quiet. The probe this backstop closes (section B) always leaves at
    least 9 real characters on one side of the injected literal or the other, so the
    combined-floor test catches every one of its 96 cases while leaving a lone
    trailing period alone.

    wq#3037/wq#3051: those two tests were not enough, and this docstring used to be the
    reason nobody looked — it claimed a `<prefix>MASKED<prefix>` chain was already safe
    because "this rule's charset cannot run through" the second prefix. That is false
    for five of the six rows: a prefix's own characters are a subset of its own body
    charset by construction, and only github's `[A-Za-z0-9]` excludes anything (the `_`
    in `ghp_`). So the trailing run walked straight into the second mention and lent its
    length to the first, and `<prefix><under-floor fill>MASKED<prefix>` alarmed with no
    credential in it anywhere — a table cell, two redacted samples concatenated with no
    delimiter, a report about this very rule. That is not free: the daemon mirror of
    this function is what `token_engine_daemon.read_worktree_file` reads a PR title and
    body through, and one hit there is `Deny("body_mask_tripwire", ...)`, so a worker
    writing about masked-output adjacency could be refused its own PR.

    Hence the third test: the trailing run must not RE-OPEN. A known fixed prefix
    starting exactly where our own output ended is a new mention, not this body
    resuming, and its characters are not this match's to count. The POSITION is the
    whole argument. A prefix sitting there is preceded by the mask literal, which is
    the one place `_B`'s `_AFTER_MASK` lookbehind admits a prefix glued to what is in
    front of it — so that second mention is already read as a match in its own right,
    by this loop and by PATTERNS, and counting it here counts it twice. A prefix
    appearing LATER inside the trailing run is glued to body characters instead, `_B`
    refuses it, and nothing else will read it, so the run keeps it: that is the wq#2702
    glue `read()`'s phase 2 exists for, and it stays body here. What the test gives up
    is a live body that contains `MASKED` and then, immediately behind it, one of the
    six prefixes, with too little left after that prefix to reach its own floor —
    rarer than the 1-in-62**6 the literal already costs, and paid to close a false
    Deny that is reachable on the landing path today.
    """
    return [(label, prefix, value) for label, prefix, value, _lo, _hi in _orphan_spans(text)]


def _orphan_spans(text):
    """_orphan_matches() with offsets: (label, prefix, value, lo, hi), read()'s shape.

    wq#3048: `value` is `before + after`, joined across the tail between them, so it
    is not a substring of `text` and a caller that has to FIND it (mask_replay cutting
    the line an orphan printed on) cannot. `text[lo:hi]` is the raw run from the end of
    the kept prefix through `after`, tail included.
    """
    found = []
    for label, rx, guarded_star, floor in _ORPHAN_PATTERNS:
        for m in rx.finditer(text):
            before = m.group(2)
            if len(before) >= floor:
                continue  # the rule's own floored body already reads this one
            tail = _OWN_TAIL.match(text, m.end())
            if not tail:
                continue  # short for an unrelated reason — not this class
            if _PRE_ANY.match(text, tail.end()):
                continue  # a second mention opens there — not this body resuming
            after = guarded_star.match(text, tail.end())
            if len(before) + len(after.group(0)) >= floor:
                found.append((label, m.group(1), before + after.group(0),
                              m.end(1), after.end()))
    return found


# The same rules with their leading boundary dropped, for read()'s second phase.
_INNER = [(label, _unbounded(rx)) for label, rx, _repl in PATTERNS]


def read(text):
    """[(label, prefix, value, start, end)] — every PATTERNS rule read INDEPENDENTLY
    over `text`, in PATTERNS order; `start`/`end` bound the part a mask REDACTS,
    i.e. the match minus the prefix the replacement keeps (wq#2913).

    Every rule's replacement is `\\1MASKED`, so group 1 is the kept prefix and the
    rest of the match is the value — one shape, so one span rule for all of them.

    Two phases, and the second one is why a plain union is not enough on its own.

    Phase 1 is the union the ticket describes: each rule, boundary-qualified, over
    the whole original text.

    Phase 2 re-reads INSIDE what phase 1 already claimed, with the leading word
    boundary dropped, starting at every fixed token prefix that begins there. The
    boundary (`_B`, wq#2292/wq#2400) refuses a prefix glued to an alphanumeric or
    `_`, because in ORDINARY text that is an identifier tail — `notes_on_xoxb-`,
    `highs_solver` — and the false positive costs a rotation ticket. Inside a span,
    the text is not ordinary: another rule has already read those characters as
    credential body, so a prefix sitting there is a prefix, not a word tail, and
    that is precisely the wq#2702 glue the first union misses. `ghp_<body>AKIA<16>
    xoxb-<body>`: github's `[A-Za-z0-9]` body runs through `AKIA` and the letters
    of `xoxb` and stops only at the `-`, and `xoxb-` is then preceded by an
    alphanumeric, so the slack rule declines it and `-<slack body>` prints in the
    clear. Phase 2 finds `xoxb-` inside github's span and slack reads the rest.

    Phase 2 can only ADD spans and labels, never remove either, so it keeps the
    monotonicity that makes mask_text() fail-closed. It repeats until it finds no
    new match; each round must add a match it has not already seen, and matches are
    finite, so it terminates. Candidate offsets come from `_PRE_ANY` rather than
    from every offset in the span: a fixed prefix is the whole class of thing the
    boundary can wrongly refuse, and scanning a long kv-secret value character by
    character is the shape that took a rule past 8 s per window in wq#2806.

    The reported `value` stops where the next credential starts, even though the
    SPAN does not. That split is the whole lesson of the design this replaces: a
    stop at a foreign prefix was right about NAMING and wrong about MASKING, and
    wq#2720 used one mechanism for both, so the naming it wanted cost the coverage
    it could not afford. Here the span runs to the end of the greedy match (nothing
    is ever uncovered) while the value a record hashes runs only to the start of the
    first match found inside it — so `ghp_<body>AKIA<16>xoxb-<body>`, where github's
    `[A-Za-z0-9]` run swallows the two tokens behind it, still hashes github's own
    36 characters and not a 60-character composite no rotation worker could match a
    candidate against (test_mask_replay.py::test_wq2848_replay_reproduces_every
    _live_hit replays a real run dir's recorded hashes through exactly this).
    """
    found, seen = [], set()

    def keep(index, label, m):
        key = (index, m.start(), m.end())
        if key in seen:
            return False
        seen.add(key)
        found.append((index, label, m.group(1), m.start(), m.end(1), m.end()))
        return True

    for index, (label, rx, _repl) in enumerate(PATTERNS):
        for m in _matches(rx, text):
            keep(index, label, m)

    spans = _merged([(lo, hi) for _i, _l, _p, _s, lo, hi in found])
    while True:
        grew = False
        for lo, hi in spans:
            stop = min(len(text), hi + _PRE_LONGEST)
            for at in [p.start() for p in _PRE_ANY.finditer(text, lo, stop)]:
                if at > hi:
                    break
                for index, (label, rx) in enumerate(_INNER):
                    m = rx.match(text, at)
                    if m and not _own_output(m) and keep(index, label, m):
                        grew = True
        if not grew:
            break
        spans = _merged([(lo, hi) for _i, _l, _p, _s, lo, hi in found])

    found.sort(key=lambda row: (row[0], row[4]))
    starts = sorted(start for _i, _l, _p, start, _lo, _hi in found)
    kept, seen_span = [], set()
    for row in found:
        _index, label, prefix, _start, lo, hi = row
        if (label, lo, hi) in seen_span or _redundant(text, lo, hi, found):
            continue
        seen_span.add((label, lo, hi))
        names = min([s for s in starts if lo < s < hi], default=hi)
        kept.append((label, prefix, text[lo:names], lo, hi))
    return kept


def _redundant(text, lo, hi, found):
    """`text[lo:hi]` says nothing the matches strictly inside it do not already say.

    This is `_matches()`'s own-output screen (wq#2298, wq#2638) applied to the text
    THIS pass is about to mask rather than to masking already on the page, and under
    a union it has to be: a wide rule reading a value that is itself another rule's
    whole credential is the composition those tickets describe, and only the
    sequential sweep used to resolve it for free. A git remote whose userinfo
    password IS a github token is ONE burn: url-auth reads that whole token as its
    value, github reads the token's body as its own, and before wq#2913 url-auth
    simply saw the kept prefix and the literal by the time its turn came and
    screened itself. (The shape is described rather than written out for the reason
    test_the_mask_suite_does_not_arm_its_own_tripwire gives.) Kept as two, it files
    two rotation tickets
    under two hashes for one credential — the ticket-storm shape wq#2282/wq#2288/
    wq#2292/wq#2298 cost four tickets to end.

    Only the CONTAINER is ever dropped: the substitution uses matches STRICTLY
    inside this one (contained and shorter), so github, which has nothing inside it,
    reads as 36 live characters and stays. Two matches over the identical span
    explain nothing about each other and neither is dropped here — the same-span,
    same-label pair (kv-secret's env form and its general form on one line) is
    de-duplicated by label in read() instead.

    It cannot re-open the hole wq#2913 closed. Dropping a container drops only the
    characters of its span that the inner matches do not already cover, and the
    screen passes only when those characters spell `(<fixed token prefix>)*MASKED`.
    A fixed prefix is this table's declared NON-secret part — what every rule's
    `\\1MASKED` replacement keeps on purpose — so no live character of any value
    loses coverage to this screen (test_a_container_screen_never_uncovers_a_live
    _character).
    """
    inner = _merged([(a, b) for _i, _l, _p, _v, a, b in found
                     if lo <= a and b <= hi and (b - a) < (hi - lo)])
    if not inner:
        return False
    shown = _redact(text[lo:hi], [(a - lo, b - lo) for a, b in inner])
    return bool(_OWN_TAIL.fullmatch(shown))


def _merged(spans):
    """`spans` sorted, with overlapping and touching ones fused into one."""
    merged = []
    for lo, hi in sorted(spans):
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return [(lo, hi) for lo, hi in merged]


def _redact(text, spans):
    """`text` with every span in `_merged()` output replaced by the mask literal."""
    out, pos = [], 0
    for lo, hi in spans:
        out.append(text[pos:lo])
        out.append(MASK)
        pos = hi
    out.append(text[pos:])
    return "".join(out)


def _remap(index, spans):
    """Where `index` (an offset into the original text) lands in `_redact()`'s output.

    An index INSIDE a span maps to that span's own literal — there is no finer
    position left to point at once the characters are gone.
    """
    delta = 0
    for lo, hi in spans:
        if hi <= index:
            delta += (hi - lo) - len(MASK)
        elif lo <= index:
            return lo - delta
        else:
            break
    return index - delta


def mask_text(text):
    """(masked_text, [labels that fired]). Order of labels follows PATTERNS.

    wq#2913: every rule reads the ORIGINAL text and the output redacts the UNION of
    what they found. Coverage is therefore monotone in the rule table — it is at
    least each single rule's own coverage — which is what makes this fail-CLOSED by
    construction.

    What it replaces was a sequential sweep, each rule reading the previous rule's
    output, with a foreign-prefix stop (wq#2720) keeping one rule's greedy body from
    swallowing the position where another rule's credential starts, and a second and
    third pass (wq#2720, again wq#2848, again wq#2899) re-running the table to pick
    up what a stop had freed. The class that design could not escape, and the reason
    it was patched three times: A STOP CAN ONLY SUBTRACT COVERAGE, AND NOTHING
    RE-ADDS IT. A full foreign shape past the rule's floor stopped the run, the
    foreign rule masked only its own shape, and the remainder — github charset, no
    prefix of its own — had nothing left to key on, so no later pass could pick it
    up. Measured on wq#2848's own fix at 200/200 trials: a github body, a full AWS
    shape, an 8-character tail, and the tail printed in the clear
    (kb/reports/wq2868_mask_stop_judgment.md, probe wq2868_tail_probe.py).

    A union has no stop to subtract with. The glued case wq#2720 was built for is
    covered because slack finds `xoxb-…` on the original text on its own and
    github's span covers the rest; the wq#2899 case is covered because sk-key reads
    the live `cfut_…` inside its own body rather than a neighbour's `MASKED`
    residue; and the wq#2848 noise cases are covered because there is no stop to
    truncate anything below a floor. No foreign-shape table to keep in sync with
    PATTERNS, no floor arithmetic, no pass cap.

    The per-rule MASK-literal guard (wq#2638) stays: the input may ALREADY carry
    this module's output (a quoted log tail, an outbound PR body), and a body run
    that crosses the literal makes a live value and an already-masked one into one
    match that `_matches()` then screens away whole. `_matches()`'s own-tail screen
    stays with it, for the same input.

    The cost is one the union direction always pays: where two rules read the same
    characters (`token: ghp_<body>` is a github token AND a kv-secret value), both
    labels fire and the merged span redacts the wider of the two, so the inner
    rule's kept prefix goes with it. More masking and more alarms, never less.

    wq#2922: `_orphan_matches()` adds a label to `hits` for a floored rule's own
    guard cutting a real value short of its floor on the MASK literal, but adds
    nothing to `spans` — an under-floor remnant is alarmed, never masked (see there
    for why masking it would revive the wq#2638 leak the guard exists to prevent).
    """
    found = read(text)
    hits = []
    for label, _prefix, _value, _lo, _hi in found:
        if label not in hits:
            hits.append(label)
    for label, _prefix, _value in _orphan_matches(text):
        if label not in hits:
            hits.append(label)
    spans = _merged([(lo, hi) for _l, _p, _v, lo, hi in found])
    return _redact(text, spans), hits


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


def raw_matches(text):
    """([(label, prefix, value)], masked_text) — the matches scan() records, in its order.

    wq#2875: mask_replay used to re-derive this with one finditer per rule over the raw
    text, which never sees a value only freed on a later pass (wq#2720). wq#2848's
    `ghp_<body>AKIA<16>xoxb-<body>` probe fired aws and slack live while the replay read
    github alone, so its "0 unaccounted" never scored the ticket's own two values. The
    replay now reads through this, so it cannot disagree with the tripwire it audits.

    wq#2913: `read()` is that agreement now — one traversal, on the original text,
    shared with mask_text() and scan(), so none of the three can re-derive the rule
    differently from the other two.
    """
    found = read(text)
    spans = _merged([(lo, hi) for _l, _p, _v, lo, hi in found])
    return ([(label, prefix, value) for label, prefix, value, _lo, _hi in found],
            _redact(text, spans))


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
    Reads through `read()`, the same single traversal mask_text() masks from (wq#2913),
    so the records and the masked text cannot disagree about what fired.

    wq#2913: the context window is cut at the value's OWN masked offset, mapped
    through the merged spans, rather than found by searching the masked text for
    `<prefix>MASKED`. The search was already approximate — a record whose prefix a
    later pattern reshaped got an empty window — and under a union it would be wrong
    more often, because a wider rule's span can swallow a narrower rule's kept prefix
    (`token: ghp_<body>` redacts from `token: ` on, so no `ghp_MASKED` is left to
    find). The offset is exact and needs no marker to survive.

    wq#2922: `_orphan_matches()`'s rows are appended the same unconditional way
    `include_suppressed`'s are, and for the same reason its own docstring gives for
    carrying no `context` — the value is not masked in `cur`, so a window around it
    would print in the clear exactly what the rest of this record exists to flag
    without leaking. Unlike a suppression it is never optional: an orphan record is
    an alarm this module could not otherwise raise, not an audit trail of a carve-out
    it chose to apply.
    """
    found = read(text)
    spans = _merged([(lo, hi) for _l, _p, _v, lo, hi in found])
    cur = _redact(text, spans)
    records = []
    for label, prefix, value, lo, _hi in found:
        at = _remap(lo, spans)
        context = cur[max(0, at - CONTEXT_CHARS):at + len(MASK) + CONTEXT_CHARS]
        records.append({
            "label": label,
            "prefix": prefix[-PREFIX_CHARS:],
            "value_len": len(value),
            "value_sha256_12": value_sha12(value),
            "context": context,
        })
    for label, prefix, value in _orphan_matches(text):
        records.append({
            "label": label,
            "prefix": prefix[-PREFIX_CHARS:],
            "value_len": len(value),
            "value_sha256_12": value_sha12(value),
        })
    if include_suppressed:
        # Read off the RAW text, not `cur`: a suppressed candidate is unmasked in both,
        # but the raw text is what the rule actually judged.
        records.extend(suppressions(text))
    return cur, records


def main(stdin, stdout):
    """stdin -> masked stdout, line by line. `/home/lumen/bin/mask-secrets` is a
    wrapper around this (wq#2913).

    That tool used to be a sed -E program mirroring PATTERNS by hand. POSIX ERE has
    no lookaround, so every lookahead here had to be re-expressed structurally — the
    mask literal as a sentinel byte, the foreign-prefix stop as a second sentinel,
    wq#2899's absorb as one more substitution per rule — and the copy drifted from
    this file on every one of those tickets, in the direction that misses a mask
    (ISSUE-LOG row 577, wq#2726 -> wq#2732 shipped a leak to bin/). The house tool
    instinct 4 points every agent at now runs the rules this module defines, so
    there is one implementation to keep right instead of two.

    Line by line rather than whole-stream: every rule's body class excludes
    whitespace, so no match can span a newline and the result is identical, while a
    long pipe stays bounded in memory the way the sed program was.
    """
    for line in stdin:
        stdout.write(mask_text(line)[0])
    return 0
