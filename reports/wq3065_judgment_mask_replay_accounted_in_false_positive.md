# wq#3065 — judgment on wq#3062's CONTRADICT of wq#3061 (rotation row set:b05f5da514d1)

wq#3062 is upheld: wq#3061's evidence was false. Its *verdict* — no rotation — turns out
to be right, but it was not established, and this ticket establishes it.

## 1. Does either burned value require rotation? No. Both are pinned.

Both preimages are now known, by hash, without either value being printed from the
transcript:

| sha256[:12] | len | rule prefix | the value is |
|---|---|---|---|
| `f80e633770c8` | 12 | `HR_SECRET_KEY= ` | the bare English plural `placeholders` |
| `95aa1524eedf` | 25 | `remove-key=` | the freedesktop autostart key name `X-GNOME-Autostart-enabled` |

`sha256("placeholders")[:12] == f80e633770c8` and
`sha256("X-GNOME-Autostart-enabled")[:12] == 95aa1524eedf` — 48-bit preimage matches on
strings guessed from each hit's own context, which is what identifies them. The first
corroborates wq#3062's independent in-process `==` check.

`_runs/wq3059/mask-hits-1.json` shows where they came from: both were cut out of wq#3059's
**own English notes**. It was the verify-pass of wq#2120's `read_dir_sweep` ack list, and
its notes name the three hits that list acks — the two HR_* labels in the upstream
harnessrouter self-hosting doc, then the ordinary noun, and a `desktop-file-edit` flag
from `research_prompts/.scratch/autorandr_Makefile:71`. The kv-secret rule glued each
label to the next 12+ word characters. Neither string has a Lumen credential behind it,
and neither is a secret at all: one is a dictionary word, the other is a public
desktop-entry key. **Nothing to rotate**, and the row's disposition is now carried by
`harness/tests/fixtures/wq3059_values.py` (split literals + hash-checked pins), the
wq#3055 / ISSUE-LOG-row-1008 precedent wq#3061 skipped.

`read_dir_sweep` separately acks the Makefile hit (`ba3880ebd497`,
`read_dir_sweep.py:180-182`), but that fingerprint is over the span *its* regex cut from
line 71, and `research_prompts/.scratch` is outside every dir a foreman worktree may read
— the access gap wq#3059 itself recorded. Recomputing it here returns `32b0a89dc1b1`, so
the pin deliberately does not lean on the ack; it rests on the preimage.

## 2a. Should `len(value) < TRUNCATED_FLOOR` become `<=`? No.

`<=` is the wrong fix for three reasons:

1. It moves the boundary one character. `placeholders` is 12; `configuration` is 13,
   `implementation` 14. The next coincidence is one word away.
2. It desyncs the floor from `burn_filter.MIN_SECRET_LEN`, which `mask_replay.py:415`
   ties it to on purpose — `burn_filter._is_accounted` accounts a body alone at
   `len(body) >= MIN_SECRET_LEN`. The two would disagree about the same value.
3. Length was never the discriminator. **Shape** is: every credential family
   `masking.PATTERNS` knows carries digits, mixed case or a fixed prefix in its body, so a
   body of nothing but lowercase words is prose or a doc placeholder.

Shipped instead (`harness/mask_replay.py`): `_needs_prefix(value)` — under the floor **or**
matching `[a-z]+(?:[- ][a-z]+)*` — and `account()` demands the prefix adjacent for those.
The floor itself is untouched.

The separator class is space and `-` only, deliberately. `_` and `.` join words in
*identifiers*, which is what real code and the house's own dummy vocabulary are made of;
including them un-accounted five of wq#3036's sweep-corpus values
(`resolve_secret_from_env`-shaped snake_case) and bought nothing, because what fired this
row was an English word and English does not use underscores.

Two cheaper rules were tried against live data and rejected, both recorded in the code
comment so the next reader does not re-derive them:

- **Position** ("a word, a space, then the hit = prose"): this tree also says
  `placeholders` at the head of a wrapped docstring line (`harness/fworker.py:3307`),
  which reads as a literal by any local rule. `account()` still returned a hit.
- **Multiplicity** ("an ambient word is in many files"): measured over the committed tree,
  `placeholders` is in 18 files and the house's own filler dummy `abcdefghijkl` is in 15.
  The dummies are as ambient as the English.

### Blast radius, measured

Three previously-accounted values in other corpora were riding a coincidental bare-body
match and now need their prefix. All three preimages confirmed by hash — they are
sequential-alphabet or repeated-character filler, not credentials:

- `42492da06234` = `"a" * 20`, which had accounted **inside** wq#3021's `ghp_aaaa…` github
  dummy (`wq3021_values.py:14`). Still excused by `today_closed`; that test stays green.
- `f39dac6cbaba` = `abcdefghijklmnop`, which had accounted inside wq#2687's longer
  `abcdefghijklmnop.` pin. Still excused; green.
- `dd65eea0329d` = `abcdefghijklmnopqrst`, wq#2687's `SECRET=` probe body, whose only bare
  occurrence was in an unrelated test file. Pinned into `WQ2687_PROSE` — the tuple wq#2765
  created for exactly this ("ordinary words … which a bare-body lookup had cleared by
  coincidence"), under wq#2701's already-settled verdict for that corpus. No new rotation
  row is adjudicated.

`test_a_body_at_the_floor_still_accounts_without_its_prefix` kept its property and swapped
its body off the sequential alphabet (`abcdefghijkl` → `a9Fk2mQp7xLz`): the ISSUE-LOG
814/822 mitigation it guards is about real credential bodies left on disk without their
wrapped label, and those are not twelve lowercase letters.

## 2b. Should `today_closed` check the specific (label, value) pair? Yes — and not here.

`today_closed`'s `any(account(v, tracked, p) for p, v in today_hits)`
(`mask_replay.py:705-706`) runs over every hit on the **line**, so one accounted hit
excuses every other hit on it. wq#2423's concern was a recut of the *same span*, so the
scope should be the span; the patch is two lines (filter `_located(CURRENT_PATTERNS, line,
True)` to hits overlapping `[lo - line_at, hi - line_at)` and take `all()`, which also
subsumes the "table never fires here" branch).

It is not in this PR, and the reason is not caution:

- Measured, it surfaces **13 values** across wq#2687's and wq#3021's corpora that nothing
  else accounts — sk-key and kv-secret probe bodies from those runs' own transcripts,
  currently excused by nothing but this `any()`. `test_wq2687_corpus_burned_nothing` and
  `test_wq3021_corpus_burned_nothing` go red together.
- Clearing them means pinning 13 values, i.e. **settling two other rotation rows**. That
  is not this row's judgment to make.
- It is not needed for this row. With the `account()` fix alone and the new fixture
  removed, both of set:b05f5da514d1's values are already
  `accounted_in: None, today_closed: False` — the bogus match is gone, so the `any()` has
  nothing to launder with. Verified on the live corpus.

`test_today_closed_still_excuses_a_whole_line_at_once` now pins the live behaviour, so the
follow-up has to come and change it deliberately rather than discover the coupling by
turning two pin tests red.

## Evidence (re-runnable)

- `python3 harness/mask_replay.py 3059` → exit 0, 2 values, 0 unaccounted, both
  `accounted: harness/tests/fixtures/wq3059_values.py:34` and `:36`. wq#3061's
  `dispatcher/DESIGN.md:462` citation is gone.
- Fixture is load-bearing: with `harness/tests/fixtures/wq3059_values.py` popped from
  `tracked_text()`, both values return `accounted_in: None` and `today_closed: False`.
  This is the probe that can fail, and it fails without the pin.
- Pre-fix rule reproduced in-process: with `_needs_prefix` replaced by
  `len(v) < TRUNCATED_FLOOR`, `account("placeholders", {prose}, "HR_SECRET_KEY= ")`
  returns the prose file; with the shipped rule it returns `None`.
- `pytest harness/tests/test_mask_replay.py` — full suite; results in the PR.
