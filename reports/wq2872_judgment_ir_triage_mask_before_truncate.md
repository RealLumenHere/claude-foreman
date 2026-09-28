# wq#2872 — judgment on wq#2846's contradiction of wq#2836 (PR#1168, `ops/wq2836-ir-triage/`)

Judge: claude-opus-5, worktree `wq2872-a1`, 2026-09-28. All probe secrets are runtime-built
filler split at the prefix seam (`'gh' + 'p_' + 'Q' * 36`, `'sk-' + 'Q' * 40`); no live
credential was read or printed. The live-proposal inspection reports rule label, body
length and `masking.value_sha12` only — never matched bytes.

## Ruling: contradiction UPHELD — defect real, wq#2846's repro was confounded

The ordering defect is real in both collectors and I reproduced it independently against
the shipped code. But wq#2846's own evidence did not isolate it, and the correction matters
for anyone re-running it:

- wq#2846 glued its filler straight onto the token (`'x' * 180 + secret`). `masking.py`'s
  rules require a non-word character in front of the fixed prefix (`_B`, masking.py:64), so
  `xxxxxghp_<36>` is unmaskable **at full length too** — the `hits=[]` it reported is
  explained by the missing word boundary, not by the cut. I hit the same wall first: my
  initial probe, glued the same way, still showed 20 clear chars *after* the fix.
- wq#2846's trivy figure (`prefix + 16 body`) is at/above the `sk-key` `{16,}` floor, i.e.
  precisely the length masking **does** catch. Re-run unconfounded at 226 chars of filler,
  the old order masks the token correctly; the leak needs the cut to land inside the first
  16 body chars.

Unconfounded probe (`/tmp/fworker-wq2872/probe_prefix_vs_fix.py`, loads pre-fix
`git show HEAD:ops/wq2836-ir-triage/ir_triage.py` and the fixed worktree copy side by side,
both through explicit `db=` / `report_dir=` — off the module defaults):

| collector | pre-fix HEAD | fixed |
|---|---|---|
| `collect_aq` (`message[:200]`, github floor 30) | **24 live chars in the clear** (`ghp_` + 20 body), no `ghp_MASKED` | 4 chars — the kept prefix, `ghp_MASKED` present |
| `collect_trivy` FATAL branch (`raw.strip()[:250]`, sk-key floor 16) | **10 live chars in the clear** (`sk-` + 7 body) | 3 chars — the kept prefix, `sk-MASKED` present |

So: real, both collectors, exactly the reversed mask-before-print order instinct 4 forbids,
and it reaches persisted artifacts (`explanation()` → `minerva.db.eval_result`, `save()` →
markdown twin). Narrower than wq#2846 read it — the token must straddle the cut *and* sit
behind a non-word character *and* the surviving body must fall under the rule's floor — but
`guard-fired` messages exist to carry exactly such text, so the rate is not negligible.

The rest of wq#2846's verdict stands as reported: citation validation, read-only collection
and blocked-status gating hold; 12/12 tests pass (10 shipped + 2 added here). No other
pre-mask truncation exists in the lane — `frag()`'s `[:400]`, `explanation()`'s 240/120/60
and `save()`'s 1000/2000/500 all cut text that is either already masked or model prose
derived only from masked fragments (`render()`, ir_triage.py:182-183, is the model's sole
input), and no `log()` call prints a raw upstream field.

## Live proposal `deddbcfcba55`: CLEAN — not a rotation event

`/home/lumen/minerva/proposals/pending/20260928_deddbcfcba55.md` (11,479 chars) and every
string column of its `minerva.db` `proposals` row, including `eval_result` (3,968 chars) and
`proposed_change`, scanned with `/tmp/fworker-wq2872/inspect_deddbcfcba55.py`:

- `masking.mask_text()` returns **no label** on any field — no full-length secret present.
- One prefix-shaped hit, in the markdown and the `proposed_change` column (the same text):
  `sk-lumen-` with a **zero-length body** (`sha12=356385cbf681`). Context, masked before
  printing: `artifact: LiteLLM env/secret file (Bearer sk-lumen-... value)`. That is the
  model naming an artifact in its own plan prose, not credential bytes — no body at all
  survives after the prefix.

No partial credential reached the live artifact. **No same-day rotation is required.** The
lane's one live run happened to draw fragments whose text did not straddle a token.

## Fix landed

`mask()` first, display budget second, in both collectors — ir_triage.py:100-104 and
ir_triage.py:132-134. Two regression tests added
(`test_long_alert_message_is_masked_before_the_display_cut`,
`test_fatal_trivy_report_is_masked_before_the_display_cut`); both **fail against pre-fix
HEAD** (verified by running the new test file against `HEAD`'s module in
`/tmp/fworker-wq2872/prefix_check/`: `2 failed, 10 passed`) and pass on the fix
(`12 passed`). Each probes through an explicit non-default `db=` / `report_dir=` and pins
its input to a runtime-built fixture, and each test comment records the boundary that makes
it able to fail (word boundary in front of the prefix; cut inside the floor).
