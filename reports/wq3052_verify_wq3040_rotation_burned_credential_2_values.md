# wq#3052 verify-pass: wq#3040 [rotation] burned credential 2 values set:da27bce124b6 (slack) — CONFIRM

**Verdict: CONFIRM.** PR 1332 (http://100.121.17.5:3000/lumen/claude-foreman/pulls/1332,
landed as `4b093413`) does exactly what wq#3040 claims: both slack-shaped hashes from the
`da27bce124b6` set (`977ab277cd08`, `00761f53a2b4`) are the same hand-typed
`xoxb-MASKEDxoxb-<phrase>` orphan-chain probe dummy from wq#3037, not a credential, and
pinning the one committed body explains both.

## What the PR does

Three files, from `main`'s prior tip forward:

- `harness/tests/fixtures/wq3037_values.py`: adds a fourth pin —
  `("977ab277cd08", "slack", "xoxb-", "xo" "xb-ano" "ther-e" "xample" "-value" "-long-" "enough")`
  — split at the prefix seam per the file's existing convention, with a comment explaining
  the second hash is the same string with `xoxb-` attached.
- `harness/tests/test_mask_replay.py:2237-2261`: bumps
  `test_wq3037_pins_are_their_hashes_and_read_silently`'s count assertion 3 → 4, adds
  `assert [r[0] for r in masking._orphan_matches(text)] == []` (the fixture file itself
  must not re-trip the orphan backstop), and updates
  `test_wq3037_corpus_burned_nothing`'s docstring to cover wq#3040 alongside wq#3039.
- `harness/prompts/worker.md`: adds the rule this leak path was missing — every
  `masking.PATTERNS` prefix counts, `MASKED` in front doesn't neuter the next link, and
  every link must be built at runtime (`p = "xo" "xb-"; p + "MASKED" + p + "x" * 20`)
  instead of typed as a literal. This text is live in the current worker prompt verbatim.

`wq show 3040`'s done-notes match this diff exactly: "no rotation: both slack hashes
(977ab277cd08, 00761f53a2b4) are wq3037's hand-typed xoxb-MASKEDxoxb-<English phrase> probe
dummy; pinned in wq3037_values.py, worker.md literal rule widened to chained MASKED probes;
mask_replay 3037 exits 0 (0 unaccounted). ISSUE-LOG #1001."

## Reproduction (fresh, this host, `_runs/wq3037` present)

```
$ python3 -m pytest harness/tests/test_mask_replay.py -k wq3037 -v
test_wq3037_pins_are_their_hashes_and_read_silently PASSED
test_wq3037_corpus_burned_nothing PASSED
2 passed in 8.13s

$ python3 harness/mask_replay.py 3037
wq#3037: 5 credential-shaped values in the run's model context, 0 unaccounted
  [tracked] slack len=33 sha256=977ab277cd08 prefix='xoxb-'
      accounted: harness/tests/fixtures/wq3037_values.py:27
No rotation: every value is committed text, or a string today's masking.PATTERNS no longer
reads as a credential.

wq#3037: 10 hit(s) the run's tripwire recorded that today's table does not reproduce —
a since-narrowed rule or an unreplayable source; audit only:
  ...
  slack sha256=00761f53a2b4
  ...
```

Exit 0, 0 unaccounted, matching wq#3040's done-notes exactly.

## Adversarial angle: does one pin actually explain both hashes?

wq#3040's claim rests on `00761f53a2b4` being "the same string with `xoxb-` attached" —
worth checking independently rather than taking the commit comment's word for it. Loaded the
live fixture module and hashed both forms directly:

```
$ python3 -c "
import sys; sys.path.insert(0,'harness'); sys.path.insert(0,'harness/tests')
import masking, test_mask_replay as t
_, scope = t._load_fixture_module(t.WQ3037_FIXTURES)
for sha, label, prefix, whole in scope['WQ3037_VALUES']:
    body = whole[len(prefix):]
    print(label, 'pin=', sha, 'sha(body)=', masking.value_sha12(body),
          'sha(whole)=', masking.value_sha12(whole))
"
slack pin= 977ab277cd08 sha(body)= 977ab277cd08 sha(whole)= 00761f53a2b4
```

Confirmed: `977ab277cd08` is `sha256_12` of the body alone (33 chars); `00761f53a2b4` is
`sha256_12` of `prefix + body` (38 chars) — the exact pair named in the ticket's
`set:da27bce124b6` and in ISSUE-LOG #1001. The wider hash is the raw pre-backstop regex
match; `masking._orphan_matches`'s cut reduces it to the body-only span before
`mask_replay.py` scores the corpus, so `00761f53a2b4` never appears in the accounted or
unaccounted lists — only in the "hit(s) ... does not reproduce" audit list, confirmed above.
This is the identical mechanism wq#3049 independently verified for the github/sk-key pair
from the same wq#3037 probe (`870b480040bc`/`0bc654921d14` there), so this isn't a new,
untested rationale — it's the third time this exact shape has checked out.

Also checked the new fixture file doesn't itself re-trip the tripwire it's pinning against:
`masking._orphan_matches(open(fixture_path).read())` returns `[]`, matching the test's new
assertion.

## Falsifiability check (wq#2630)

`test_wq3037_corpus_burned_nothing` calls `_assert_corpus_pinned`, which pops the fixture's
committed text out of the tracked-file set and re-replays, asserting anything that comes
loose is a subset of the fixture's own pinned hashes — so removing the slack pin's committed
text would surface `977ab277cd08` as unaccounted. This helper is unmodified and reused
verbatim across eleven prior rows now (wq#2687 through wq#3037/wq#3040), a proven-falsifiable
pattern, not new-and-untested for this ticket. It passed above.

## Disposition

`wq#3052` closes CONFIRM. PR 1332 stands as shipped (already on `main` as `4b093413`);
nothing to rotate, nothing to re-implement.
