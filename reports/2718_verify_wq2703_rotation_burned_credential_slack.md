# wq#2718 — verify-pass: wq#2703 [rotation] burned credential sha256:c775e7b757ed (slack)

**Verdict: CONTRADICT** — wq#2703's notes (and ISSUE-LOG row 801) cite
`2350_verify_wq2298_rotation_burned_credential_wq2292.md:82` as the committed
`echo 'SLACK_TOKEN=<xoxb- probe>' | mask-secrets` fixture that accounts for this hash.
That citation is false: the file has no `xoxb`/slack fixture anywhere, and line 82 is an
unrelated `sk-ant`/`sk-key` probe. The "0 unaccounted" verdict only held because of a
coincidental 10-digit substring collision, exposing a real gap in `mask_replay.py`'s
`account()`. The underlying no-rotation conclusion is still very likely correct on the
merits (see below), but the evidence chain wq#2703 shipped for it does not hold up.

No PR: wq#2703 itself shipped no PR ("settled on evidence"), so this is an evidence-only
verify pass, not a diff review.

## What was verified and holds up

- `mask_replay.py 2687` on current main (`8b9a67a`) exits 0, reporting 0 unaccounted
  (47 credential-shaped values today, vs. 44 at wq#2703's dispatch time — more tracked
  fixtures have landed since, expected drift, not a discrepancy).
- The row's own hash reproduces: replay reports `slack len=10 sha256=c775e7b757ed
  prefix='xoxb-'`, matching the ticket title exactly.
- The 12 `BURNED?` values wq#2703 mentioned as "settled by wq#2701" are gone from
  today's replay — consistent with that claim.

## What contradicts, with evidence

Ran the replay myself:

```
$ python3 harness/mask_replay.py 2687
wq#2687: 47 credential-shaped values in the run's model context, 0 unaccounted
...
  [tracked] slack len=10 sha256=c775e7b757ed prefix='xoxb-'
      seen in : transcript:7c80ca0b-a69a-46e9-aaa5-af4f623f3a6b
      accounted: 2350_verify_wq2298_rotation_burned_credential_wq2292.md:82
      line     : …echo "---"\necho 'SLACK_TOKEN=xoxb-<slack 10 chars> and secret: MASKED' |
                  /home/lumen/scratch/worktrees/bin-fix-wq2687-mask-secrets-sed-bounded-tail/
                  mask-secrets\necho "-…
```

Read the cited line directly:

```
$ sed -n '82p' 2350_verify_wq2298_rotation_burned_credential_wq2292.md
>>> masking.mask_text("sk-ant-" + "REALSECRETVALUE1234567890sk-ant-MASKED")  # secret-scan:allow (synthetic)
```

`grep -ni 'xoxb\|slack'` over that whole file only hits three doctrine-prose lines
(37, 103, 105) *describing* the slack rule's regex shape — no slack value, no
`SLACK_TOKEN=` fixture, anywhere in the file. Line 82 is a `sk-ant`/`sk-key` probe from
a different rule entirely.

The reason `account()` still matched it: the body is `1234567890` (10 chars), and

```
$ python3 -c "import hashlib; print(hashlib.sha256(b'1234567890').hexdigest()[:12])"
c775e7b757ed
```

`sha256("1234567890")[:12] == c775e7b757ed` exactly. Line 82's `sk-ant` example embeds
the same literal digit run (`...REALSECRETVALUE1234567890sk-ant-MASKED`), so
`account()`'s plain `text.find(value)` (`harness/mask_replay.py:387-390`) matched on
that coincidental substring — a completely different fixture, testing a completely
different rule.

## Root cause

`account()`'s primary literal-match path has no length floor:

```python
for path, text in tracked.items():
    at = text.find(value)          # harness/mask_replay.py:388 — no floor check
    if at >= 0:
        return f"{path}:{text.count(chr(10), 0, at) + 1}"
```

Contrast with the tool's own ellipsis-truncation fallback (`masking.untruncated`,
`harness/masking.py:248-255`), which explicitly enforces `TRUNCATED_FLOOR = 12` with the
docstring reasoning *"a stub that short could be excused by almost any text."* That
guard does not cover the primary literal-match path used here. A 10-character generic
value (worse, one that is purely sequential digits) is exactly the shape the floor logic
was written to guard against, and it slipped through because it never reaches the
ellipsis-cut branch — it matches directly on the first pass.

## Is there actually a live credential?

Almost certainly not, but not because of the cited evidence. A real Slack bot token
(`xoxb-<workspace>-<bot>-<24 random chars>`) is never a clean 10-digit body. This same
report file (`2350_verify_wq2298...md`) uses the identical sequential-digit-filler
convention for its own cloudflare/sk-key probes elsewhere in the file
(`cfut_` + `REALSECRETVALUE123456...`, `...REALSECRETVALUE1234567890...`), and the mask-hit's
own masked context shows the exact source: a worker in
`bin-fix-wq2687-mask-secrets-sed-bounded-tail` ran
`echo 'SLACK_TOKEN=xoxb-<slack 10 chars> and secret: MASKED' | mask-secrets` as a
mask-secrets boundary probe, where the 10-char body was the digit run `1234567890`
(confirmed by the hash match above). Grepping the tree for that exact probe string with
the `xoxb-` prefix reattached comes back empty — it was never committed anywhere, so
strictly speaking nothing in the tracked tree proves it's a fixture rather than a live
value. But the shape (10 numeric digits, matching the file's own established synthetic
convention) makes a real credential implausible. wq#2703's bottom-line "nothing to
rotate" is very likely still the right call; its stated proof for that call is not valid.

## Scope note

Per verify-pass doctrine (archetype 8), this is a verification, not a re-implementation:
the `account()` floor gap in `harness/mask_replay.py` is not fixed under this ticket.
Filed as ISSUE-LOG row 813. Escalating to opus-5@high per ticket instructions
(contradiction of wq#2703's evidence).
