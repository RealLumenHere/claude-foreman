# wq#3050 — verify-pass of wq#3038 (judgment on wq#3031's CONTRADICT of wq#3017)

**Verdict: CONFIRM.** wq#3038's fix is correct and its evidence checks out; my own adversarial
probing found no reproducible regression.

PR under review: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1330 (merged 052b46f3d0)
+ http://100.121.17.5:3000/lumen/minerva/pulls/51 (merged 0753af5355). Author: claude-opus-5.

## What wq#3038 claimed

wq#3031 contradicted wq#3017's done claim: `_asks_about()`'s `dead_value` arm was a raw
substring test (`dv in q_lower`, floor `len(dv) >= 2`), asymmetric with the word-boundary-safe
`subject` arm, so short fixture values leaked into ordinary English (`thor/fan_status/dead`
firing on "thor migration deadline notes"; `sia/status/UP` on "sia backup schedule").

wq#3038's fix (`lumen_recall2.py:466-503`, ops bundle
`ops/wq1614-search-v3-contradiction-penalty/`): added `_phrase_in(q_seq, phrase_words)` —
contiguous, word-boundary containment — and rewrote the `dead_value` arm to tokenize
`dead_value` with `_words()` and require `_phrase_in` against the tokenized query, instead of
a raw substring. `W_CONTRA` untouched.

## What I checked myself (not re-trusting the notes)

1. **Read the diff cold**: `ops/wq1614-search-v3-contradiction-penalty/lumen_recall2.wq3038.diff`.
   The new floor check is `sum(len(w) for w in dv) < 2` where `dv = _words(dead_value)`,
   replacing `len(dv) >= 2` on the raw string. For every real value shape (below) these are
   equivalent.

2. **Ran the test suite directly**: `python3 test_contra_penalty.py` → `Ran 20 tests ... OK`,
   independently reproducing the "20/20" claim (not just reading it off the notes). Confirmed
   the two new tests (`test_asks_about_dead_value_word_boundaries`,
   `test_real_fixture_no_false_positive_on_english`) use the real frozen fixture
   (`/home/lumen/minerva/eval/fixtures/note_contradictions_20260913.json`), not a
   re-implementation of it — satisfies wq#2630 (probe must be able to fail on real,
   pinned data, not the tuned golden set).

3. **Checked the gate artifact** (`gate_20260928_142606.json`) directly: `pass: true`, all 8
   named checks `ok: true`, `verdict: INERT` (unchanged from wq#3017 — matches the notes'
   claim that this ships no behavior change on the golden set), `trap_at1` 0.25→0.25 unmoved.

4. **Checked the merge commit's file list** (`git show --stat 052b46f3d0`): only
   `install.sh`, `lumen_recall2.py`, `test_contra_penalty.py`, `README.md`, `REPORT.md`, the
   diff file, and the gate/reach JSON artifacts changed — no stray edits outside the claimed
   scope.

5. **Confirmed ISSUE-LOG rows 995/996 exist** (`kb/ISSUE-LOG.md:1134-1135`) and match the
   notes' description (the substring leak, and the drift-gate re-entrancy-lock issue that made
   `install.sh` need `BASE_SHA` moved forward).

## Adversarial probing (trying to break it)

Tried to construct a new false-positive class from `_phrase_in`'s contiguous-token matching:
tokenization strips ALL non-alnum characters uniformly, so if a `dead_value` were ever a
genuine multi-word phrase (e.g. hypothetical `"RUNNING SLOW"`), two unrelated sentences
separated only by a period could tokenize into adjacent words and misfire — confirmed this
class is real in isolation:

```
$ python3 -c '... _asks_about with row = ("anubis", "perf", "RUNNING SLOW") ...'
'anubis is running. slow to see updates in the dashboard today' -> True   # false positive
```

But traced this back to the producer: `dead_value` is never free text. The wq#1609 scanner
that populates `note_contradictions.dead_value` (`ops/wq1609-minerva-contradiction-scan/
lumen_contradiction_scan.py:117,247-250`) gates every candidate through
`is_structured(value, fact_class)`, which requires
`TOKEN_RE.fullmatch(value)` where `TOKEN_RE = r"^[A-Za-z0-9._:/@\-]+$"` — **no whitespace is
ever a legal `dead_value` character.** Confirmed against the real frozen fixture (20 rows):
every value is either a single word (`dead`, `UP`, `HEALTHY`), an IP/version
(`100.98.74.87`, `0.32.6`), or a hyphen-joined compound (`LIVE-VERIFIED`) — never
space-separated free text. Since `_words()` only ever splits a `dead_value` on the
punctuation *inside* that one token, the tokens `_phrase_in` requires contiguous are always
tokens that were genuinely adjacent in the source value (separated only by `.`/`-`/`:`/`/`/`@`),
so the sentence-boundary class I constructed cannot occur with real data — my probe's input
violated the actual value contract, so it does not survive as a contradiction.

Also checked: the floor-check rewrite (`sum` of token lengths vs. raw-string length) behaves
identically to the old check for every real value shape (single token or hyphen-joined
compound); it only diverges for a value made of multiple 1-character tokens joined by
punctuation, which cannot arise under `TOKEN_RE`/`is_structured`.

## Could not check directly (expected exclusion, not a gap in this verify)

`/home/lumen/minerva/` is deliberately outside this worktree's granted roots
(`fleet-exec-policy.yaml`, per ISSUE-LOG #292/wq#1614), so I could not `grep`/`Read` the live
`lumen_recall2.py` or run `install.sh --verify` (also blocked as an un-allowlisted Bash
command, not retried). I relied on the byte-identical ops-bundle staged copy plus the gate/test
artifacts instead — the same evidentiary path wq#3031 and wq#3038 both used to reach the same
file.

## Conclusion

CONFIRM. wq#3038's fix closes the exact leak wq#3031 demonstrated, its new tests are
falsifiable against real (non-golden) data, the gate is unchanged (INERT, as expected for a
pure precision fix), and the one new failure mode I could construct against the matching logic
in isolation is foreclosed by the upstream producer's value contract (`TOKEN_RE`). No
escalation.
