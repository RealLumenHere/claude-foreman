# wq#3156 — verify-pass of wq#3153 (kv-secret sed-pattern guard)

**Verdict: CONTRADICT.** wq#3153's diagnosis of the two burned values (`54b1b4739062`,
`17e43898b34b`) as non-credential sed pattern-halves is correct and independently
reproduced below. But the fix it shipped — PR#1425, merged as `9235d57b` — is
under-scoped: the new `_KV_POINTER` guard suppresses `kv-secret` detection for ANY
value that happens to start with a regex-wildcard-then-delimiter run
(`.*#`, `.*/`, `.*@`, `.*:`, `.*!`, `.*~`, `.*%`, or the `.+` variants), not only when
that shape sits inside an actual sed substitution. A real, single-occurrence credential
whose value starts with that 3-character run is now invisible to `kv-secret` — in both
copies of the rule (`harness/masking.py` and `token-engine/token_engine_daemon.py`),
and in both the in-code (`_KV_SECRET`) and env-style (`_KV_SECRET_ENV`, wq#2839's rule)
variants, since both share `_KV_POINTER`.

Escalating per the archetype-8 protocol (contradiction → opus-5@high judgment). Filed
wq#3157 for that pass (deps wq#3153, claude-opus-5@high). ISSUE-LOG #1062.

## Confirming wq#3153's own claim (not disputed)

`python3 /home/lumen/foreman/harness/mask_replay.py 3150` -> exit 0, "0 credential-shaped
values ... 0 unaccounted", with `54b1b4739062` / `17e43898b34b` listed as hits "today's
table does not reproduce" (i.e. the new guard, not a rotation).

Independently reconstructed both preimages from the mask-hit context windows
(`_runs/wq3150/mask-hits-2.json`, `mask-hits-3.json`, which show
`sed -i "s#^API_BEARER_TOKEN=MASKED" "$REPO/.env"`) and hashed them without ever
printing a live secret:

```
label = "API_BEARER_TOKEN"
sha256(".*#" + label + "=$TOKEN#")[:12] == "54b1b4739062"   # matches, len 27 == value_len
sha256(".*#" + label + "=$token#")[:12] == "17e43898b34b"   # matches, len 27 == value_len
```

Both hashes match exactly. This is a direct preimage match, not a replay-tool assertion
-- wq#3153's "not a credential" conclusion is solid.

## The regression the fix introduces

`harness/masking.py:233` (mirrored `token-engine/token_engine_daemon.py:170`):

```python
r"(?!\.[*+][#/@:!~%])"
```

This lookahead sits in `_KV_POINTER`, which gates BOTH `_KV_SECRET` (line 345/346,
floor 12) and `_KV_SECRET_ENV` (line 350, floor 8 -- the wq#2839 rule that CLAUDE.md
documents as firing "however fake the body"). It is a blanket disqualifier evaluated
before the floor/entropy check runs, so it isn't contingent on a second occurrence of
the label existing later in the text (the comment's "the scan resumes at the
replacement's own label" safety claim only holds for actual two-sided sed
substitutions -- nothing in the regex requires that second occurrence to exist).

Controlled A/B probe against `masking.mask_text` (`/tmp/fworker-wq3156/probe2.py`,
not committed -- verify-only), holding the label and the opaque value's length fixed
and varying only a 3-character prefix glued onto the front of the value:

- A code-style `<kv-noun-labelled identifier> <assign-op> <22-char opaque body>` line,
  with no special prefix on the body, fires `kv-secret` (the expected control case).
- The identical line, with only the value's leading three characters changed to a
  dot-wildcard character then one of the sed-delimiter characters this guard added
  (`#`, `/`, `@`, `:`, `!`, `~`, `%` -- either `.*` or `.+` as the wildcard half),
  produces zero hits. Confirmed for four independent label/delimiter combinations.

The same holds for the env-style form (`/tmp/fworker-wq3156/probe1.py`, not committed):
an all-caps `<TOKEN/PASSWORD/KEY/SECRET-suffixed name>` `=` `<20+ char opaque body>`
assignment -- the exact shape CLAUDE.md documents as firing "however fake the body"
(wq#2839) -- goes silent the moment the body is given that same 3-character prefix,
well above the rule's 8-character floor.

Only when the SAME label reappears later in the text (the real sed-substitution shape
wq#3153's own new test, `test_a_literal_on_the_sed_replacement_side_still_fires`,
covers) does detection still fire. wq#3153's test suite (165/165 passing, confirmed)
only exercises that two-sided case; it never adds a single-occurrence
label-then-wildcard-prefixed-value case, so the gap shipped past its own gate -- the
same failure shape as ISSUE-LOG #709 (wq#2587/wq#2569): the fix's own tests only cover
the symptom being fixed, not the boundary the fix's guard newly opens.

`PATTERNS` (masking.py:390+) has no generic high-entropy fallback independent of a
known vendor prefix (github/cloudflare/sk-/slack/aws/url-auth) or a kv label -- so this
gap is the ONLY thing standing between a leaked non-vendor-prefixed secret and rotation
for any value shaped this way.

## Why this matters given the fix's own goal

wq#3153's parent context (instinct 4 / wq#2275's mask tripwire) exists specifically to
catch credentials before rotation is needed. The new guard was sized to fix a false
positive (sed pattern halves alarming) but is scoped by VALUE SHAPE alone, with no
requirement that the two-sided sed structure that makes the false positive safe to
ignore actually be present. That is the same failure mode this same file's own comments
warn about at `masking.py:306-310` ("every repetition in this guard is COUNTED ... That
is strictly worse than the pre-echo guard set") -- a carve-out wider than the shape it
was meant to exempt.

## Scope not in question

- wq#3153's mask_replay run, exit code, and "nothing rotated" disposition: correct.
- The specific sed-pattern-half preimages: independently hash-confirmed above.
- All 165 existing masking tests still pass; nothing here is a test failure, it's a
  coverage gap plus an over-broad regex.

## Reproduction

- `python3 /home/lumen/foreman/harness/mask_replay.py 3150`
- `/tmp/fworker-wq3156/probe1.py`, `probe2.py` (env-style and code-style A/B probes)
- `python3 -m pytest /home/lumen/foreman/harness/tests/test_masking.py -q` (165 passed)
