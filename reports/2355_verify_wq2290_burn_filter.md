# wq#2355 — verify-pass: wq#2290 [rotation] burned credential in wq#2278 transcript (sk-key)

**Verdict: CONTRADICT** — the shipped diff correctly implements a live-vs-committed
provenance screen and closes the specific false-positive it was filed to fix, but its
own provenance check only covers the child's worktree (`cwd`), never the rest of the
`--add-dir` roots the child was actually granted. Doctrine orders every worker to read
`/home/lumen/kb/ISSUE-LOG.md` before touching anything, and that log is, by design, full
of quoted credential-shaped fixture text from prior false alarms — so the diff as merged
still files a false-positive pri-1 rotation ticket on that exact, guaranteed-to-happen
read. This is not hypothetical: it reproduces live today against the code exactly as
merged, and it is the documented root cause of the same-night incident wq#2319
(ISSUE-LOG row 594), filed ~33 minutes before wq#2290's own PR even landed and
diagnosed, after the fact, as this precise gap.

PR: `lumen/claude-foreman` #814, commit `281327c608cd6e2a06fe759cd81f49d337a3daf1`
(`Refs: wq#2290`), merged 2026-09-20T23:09:47Z.

## What was verified and holds up

- `python3 -m pytest harness/tests/test_burn_filter.py -q` on current `HEAD` (`979b51d`,
  which carries PR#814 plus later follow-ups) → **38/38 pass**.
- Re-extracted the diff exactly as merged (`git show 281327c -- harness/burn_filter.py
  harness/fworker.py harness/tests/test_burn_filter.py`) and read all 146 new lines of
  `burn_filter.py`, the `fworker.py` call-site changes, and all 14 new tests cold.
- Core design is sound and each invariant it claims actually holds in the code:
  - A live secret is compared in-process (`live_secret_values(env)` against a plain
    Python `set`) and is **never** passed to a subprocess argv — verified directly by
    `test_live_secret_is_never_passed_to_a_subprocess` and by reading `classify()`'s
    order of operations (live-set membership checked before any `git grep` call).
  - `matched_values()` correctly re-derives every label against the *original* line
    rather than the sequentially-masked one, which avoids a real blind spot (`kv-secret`
    running after `sk-key` would otherwise see `sk-ant-MASKED`, a string that exists on
    no disk and would always fail its own provenance check) — confirmed by
    `test_every_label_sees_the_real_token_not_an_earlier_mask` and by direct reading.
  - Fail-open is real: `screen_mask_hits()` wraps the classify call in
    `except Exception` and falls back to alarming on every hit, so a screening bug
    degrades to the pre-wq#2290 (noisier but safe) behaviour, never to silence.
  - Bookkeeping moved from per-label to per-matched-string (`burn_seen`/`seen`), fixing
    the real bug it names: an early benign fixture no longer consumes a label's one
    alarm for the rest of the attempt.
  - `git grep --quiet --fixed-strings -I -- <value>` is the right invocation — checked
    directly (`git -C /home/lumen/foreman grep --quiet --fixed-strings -I --
    "LOCAL_POOL_MODELS"` → exit 0; same call with an absent string → exit 1), so a
    misreading of git's `--`-before-pattern semantics is not a live concern here.
  - Correctly closes the incident it targeted: `harness/tests/test_token_injection.py`'s
    `sk-ant-oat01-…` fixtures are committed text in the worktree, so `is_committed_literal`
    excuses them against `repo_root=cwd` and the false alarm wq#2290 was filed over does
    not recur for *that* read.

## Contradiction — the provenance root is `cwd` only, and doctrine's own mandatory read defeats it

`harness/fworker.py` at `281327c`, both call sites (stream-json gateway path and the
plain-JSON path):

```
1984:                        screen_mask_hits(mask_hits, raw_payload, mask_alarmed,
1985:                                         burn_seen, live_secrets, cwd,
1986:                                         ticket_id, attempt)
...
2133:        screen_mask_hits(mask_hits, raw_line, mask_alarmed, burn_seen,
2134:                         live_secrets, cwd, ticket_id, attempt)
```

`screen_mask_hits` forwards that single value straight into `burn_filter.classify(...,
repo_root)` → `is_committed_literal(value, repo_root)`, which only ever `git grep`s
inside `cwd` — the worktree. Nothing in this diff threads the child's other granted
`--add-dir` roots (`WORKER_ADD_DIRS`/`add_dirs_for()`) into the provenance check at all;
`repo_root` is a bare string parameter throughout, not a list.

Reproduced live, against the code exactly as merged, using today's real
`/home/lumen/kb/ISSUE-LOG.md` (a file every worker is doctrinally required to read
before any change, and whose entire purpose is to quote the credential-shaped strings
that caused prior false alarms — this ticket read it as step one, per doctrine, before
writing this report):

```
$ git show 281327c:harness/masking.py > masking.py       # code as merged with PR814
$ git show 281327c:harness/burn_filter.py > burn_filter.py

$ python3 - <<'PY'   # (run via a script file, not inline heredoc, per house rules)
import masking, burn_filter as bf
text = open("/home/lumen/kb/ISSUE-LOG.md").read()
_, hits = masking.mask_text(text)
detail = bf.matched_values(text, hits)
print(bf.classify(detail, set(), "/home/lumen/foreman"))   # cwd only, as fworker.py:1986/2134 pass it
print(bf.classify(detail, set(), "/home/lumen/kb"))         # the directory the text actually lives in
PY
hit labels: ['cloudflare', 'sk-key', 'kv-secret']
classify against cwd=/home/lumen/foreman only (what PR814 fworker.py passes):
  burned: ['kv-secret']  benign: ['cloudflare', 'sk-key']
classify against repo_root=/home/lumen/kb (the directory doctrine mandates reading):
  burned: []  benign: ['cloudflare', 'sk-key', 'kv-secret']
```

The `kv-secret` hit is `TOKEN="` + `abcdefgh12345678` — a benign fixture quoted verbatim at
`/home/lumen/kb/ISSUE-LOG.md:831` (row 722, itself a prior verify-pass documenting this
exact fixture). It is committed text, entitled to be read, and would still fire a
pri-1 rotation ticket under PR814's own logic, because `/home/lumen/kb` is a distinct
git repo from `/home/lumen/foreman` (`git -C /home/lumen/kb rev-parse --show-toplevel`
→ `/home/lumen/kb`) and the diff never looks there.

This is not a contrived edge case — it is the documented root cause of a same-night
production incident. ISSUE-LOG row 594 (wq#2319, alarm filed 22:36:38Z, i.e. **before**
PR#814 even merged at 23:09:47Z) states, after replaying the incident against the new
screen: *"not the pattern — the screen. wq#2290's `burn_filter.is_committed_literal`
excuses a hit that appears in committed source, but `fworker` passed it `cwd` alone
(`fworker.py:2054/2203`)… The three values that paged are committed text in a DIFFERENT
repo… Doctrine orders every worker to read `kb/ISSUE-LOG.md` before changing anything,
and the log's job is to quote the credential shapes that caused prior false alarms — so
the log pages on the sentence documenting the alarm… Self-sustaining independent of how
tight the patterns get."` wq#2290's own done-notes claim *"Probe on the merged tree: 35
tracked files page before, 0 after"* — that probe was run against the tracked repo tree
only and never exercised a granted-but-non-cwd directory, despite doctrine mandating
exactly that read (ISSUE-LOG.md) as literally the first action of every ticket,
including this one and including wq#2290's own attempt.

## Disposition

Code as shipped in PR#814: correct and well-tested within the single-repo-root design it
chose — **confirmed** for that scope. Its claim to have closed the false-positive
rotation-ticket problem (the ticket's actual acceptance bar): **contradicted**,
reproducibly, for the one read path doctrine guarantees will happen on almost every
ticket.

Not fixing this myself — the underlying gap was independently closed by wq#2319
(`repo_roots`, plural, threaded through `fworker.py:1220-1260` and `burn_filter.py`'s
`_as_roots()`/`provenance_roots()` on current `HEAD`, confirmed live), so a second fix
under this ticket would be redundant re-implementation of already-merged work, which the
verify archetype explicitly rules out. Escalating to opus-5@high per ticket instructions
and `research/models.yaml`'s `verify_pass.escalate_on_contradiction`. Recommend the
escalation review judge (1) whether wq#2290's `done` closure was premature — its own
probe should have covered a granted non-cwd directory, and doctrine's own mandatory
ISSUE-LOG.md read was sitting right there as the test case — and (2) whether this is
worth a closure-quality note for the rotation-ticket chain, since the same-night pattern
(wq#2278 → wq#2290 → wq#2299/wq#2319 → …) is exactly the kind of self-sustaining cascade
wq#2290 was meant to end, and for about 33 minutes on the night of 2026-09-20 it did not.
