# wq#2846 — verify-pass of wq#2836 (PR#1168, `93a79d6`/`ops/wq2836-ir-triage/`): CONTRADICT

Verifier: claude-sonnet-5@high, fresh instance. Date 2026-09-28. All probe secrets are
runtime-assembled filler (`'gh' + 'p_' + 'Q' * 36`, `'sk-' + 'Q' * 40`), split at the
prefix seam, never typed as a whole credential-shaped literal; no live credential was
read, printed or handled anywhere in this verification.

## Claim under test

wq#2836 ships `ops/wq2836-ir-triage/ir_triage.py`: a read-only, proposal-only IR-triage
lane that collects fragments from the alert queue, `lumen-failed-units` state and the
weekly Trivy report, and states (README.md:20, ir_triage.py:14): "Its text is masked
through `harness/masking.py`" before it ever reaches the model or a persisted artifact.
The design explicitly calls out the credential tripwire (`guard-fired`) as always-collected
evidence (ir_triage.py:53-54) — this lane is meant to *safely* summarize a real leaked
secret for human review, not merely avoid re-leaking noise.

## What holds

- `mask()` is genuinely invoked on every fragment (`frag()`, ir_triage.py:81-82), and the
  citation-validation logic (only IDs present in the fragment set may be cited; a run with
  no valid citation writes nothing) is sound — reran `test_fabricated_citations_never_reach_a_proposal`
  and `test_plan_steps_without_artifact_or_valid_cite_are_dropped` independently, both pass.
- SQL is fully parameterized (`save()`, ir_triage.py:296-303); `collect_aq()`'s read is
  `mode=ro` and genuinely makes no write (verified `alerts.db` bytes unchanged across the call,
  same assertion the shipped `test_collect_aq_keeps_high_and_security_rows_read_only` makes).
- Full shipped suite: `10 passed` (`python3 -m pytest -q ops/wq2836-ir-triage/`).

## Evidence the design fails open (contradiction)

Both `collect_aq()` and `collect_trivy()` build the fragment's raw text by **slicing the
untrusted upstream field to a fixed display budget first, then masking the slice** —
the reverse of instinct 4 ("mask before print"):

- `collect_aq()` (ir_triage.py:100-101): `(r['message'] or '')[:200]` is embedded into
  `text` before `frag()` calls `mask(text)`.
- `collect_trivy()`'s FATAL-report branch (ir_triage.py:130-132): `raw.strip()[:250]` is
  embedded into the fragment text the same way, before `frag()` masks it.

`harness/masking.py`'s prefix rules only fire once a **full-length body** appears behind
the prefix (`github` needs `{30,}` body chars, `sk-key` needs `{16,}`, `cloudflare` `{6,}`,
`slack` `{8,}`, `aws` a fixed `{16}` — masking.py:371-396). If the 200/250-char cut lands
inside a live token's body, the surviving fragment holds the token's fixed prefix plus a
body run **shorter than the rule's floor** — the rule doesn't fire at all, and that
prefix+partial-body is real, live credential material, not a false negative on a fake.
This is not a truncation of an already-safe string; it is truncation of the untrusted
input *before* the one guard that makes it safe to keep.

Reproduced independently against the shipped code, unconfounded (secret is well past the
floor in full; only the pre-mask slice is short):

| collector | crafted input | cut point | result |
|---|---|---|---|
| `collect_aq` | `'gh' + 'p_' + 'Q'*36` (44-char token, prefix+36-body, floor 30) glued 180 chars into a `guard-fired` alert `message` | `message[:200]` | fragment text ends `...ghp_QQQQQQQQQQQQQQQQ` — 20 live chars (prefix + 16-char body, 14 short of the `{30,}` floor) in the clear, `hits=[]` |
| `collect_trivy` | `'sk-' + 'Q'*40` glued into a FATAL trivy report body | `raw.strip()[:250]` | fragment text ends `...sk-QQQQQQQQQQQQQQQQ` — 19 live chars (prefix + 16-char body) in the clear |

Scripts: `/tmp/fworker-wq2846/probe_truncate_mask.py`, `/tmp/fworker-wq2846/probe_trivy_truncate_mask.py`
(both import the shipped `ir_triage.py` and `harness/masking.py` directly, no reimplementation).

Once a `guard-fired` fragment like this is selected as evidence — which is the lane's
*stated purpose* for that source (ir_triage.py:53-54) — the partial token text is
embedded verbatim in `explanation()`'s cited-fragment payload and in `save()`'s markdown
twin (ir_triage.py:322-323), both of which are **persisted artifacts**: a `minerva.db`
`eval_result` row and a file under `/home/lumen/minerva/proposals/pending/`. This is
exactly the class of artifact `harness/masking.py`'s own docstring says masking exists to
protect ("mask credentials... BEFORE anything is written to disk").

The bug is not specific to my crafted length — it is present at every message/report
length where a real token's body happens to straddle the collector's own display-budget
cut point, and is strictly independent of the fragment's `[:400]` post-mask truncation
(`frag()`, ir_triage.py:82), which is safe because it truncates *after* masking, not before.

## Severity, and one thing I could not check

Rate depends on where in a raw alert/report message a live token happens to sit relative
to the 200/250-char cut — not rare for `guard-fired` messages specifically, since those
exist to carry the very text a tripwire found a credential in, and log/report lines
routinely exceed 200-250 characters before the interesting part.

wq#2836's own done-notes (`wq show 2836`) record that the lane already ran live once and
wrote a real proposal, `deddbcfcba55`. I could not inspect that proposal or its
`eval_result` — `/home/lumen/minerva` is outside this session's allowed directories
(`find`/grep both refused: "may only search files in the allowed working directories").
**This needs to be checked before anything else**: if that live run's evidence included a
`guard-fired` fragment whose message was long enough to hit this truncation, the partial
credential may already be sitting in a persisted, human-visible proposal today, which
under instinct 4 is a same-day rotation event, not a code-review finding.

## Disposition

CONTRADICT. The lane's core anti-hallucination design (citation validation, read-only
collection, blocked-status gating) is sound and independently reproduced; the masking
step it depends on for safety is applied to already-truncated text in two of its three
collectors, which lets a live credential's prefix and a partial body leak into persisted
proposal artifacts whenever the cut lands inside the token. Not re-implemented (per this
ticket's instructions) — the fix (mask the full raw field, then truncate the masked
result) is a one-line reorder in `collect_aq()` and `collect_trivy()`, but sits with the
judgment ticket to decide urgency, including whether `deddbcfcba55` needs inspection/rotation
before the fix lands. Escalating as a `[judgment]` ticket for claude-opus-5@high per this
ticket's contradiction protocol. ISSUE-LOG row appended.
