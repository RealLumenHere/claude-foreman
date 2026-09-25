# wq#2630 — judgment: wq#2355's CONTRADICT of wq#2290 (burn_filter cwd-only provenance)

**Verdict, one line: the CONTRADICT stands on the merits, wq#2290's `done` was *not*
premature, one sentence of its done-notes was an overclaim, and the transferable defect
is a probe shape — a self-probe drawn from the same population the fix was designed
around — which wq#2355 then committed itself.**

Escalation source: `reports/2355_verify_wq2290_burn_filter.md` (PR#987, `05661e4c3a`),
routed here by `research/models.yaml:431` (`verify_pass.escalate_on_contradiction:
opus-5@high`). Re-derived from primary evidence rather than from wq#2355's citations.

## 0. What I re-verified independently

| claim | source | result |
| --- | --- | --- |
| PR#814 as merged screens provenance against `cwd` only | `git show 281327c:harness/burn_filter.py`, `fworker.py:1984/2133` at that rev | **holds** — `repo_root` is a bare string end to end |
| the gap is already closed on current HEAD | `harness/burn_filter.py:137/146/179/221`, `harness/fworker.py:1220/2769/2928` | **holds** — `provenance_roots(cwd, granted_dirs(cmd))`, `repo_roots` plural, resolved lazily on first hit |
| regression coverage exists for the multi-root path | `harness/tests/test_burn_filter.py:383/394/437/446/453` | **holds** — incl. `test_run_child_screens_against_the_dirs_its_own_argv_granted` |
| the suite passes on HEAD | `python3 -m pytest harness/tests/test_burn_filter.py -q` | **40 passed in 5.63s** |
| wq#2290's done-notes contain the quoted probe sentence | `wq show 2290` | **holds** verbatim: *"Probe on the merged tree: 35 tracked files page before, 0 after, live token still pages."* |
| ISSUE-LOG row 594 diagnoses this exact gap | `/home/lumen/kb/ISSUE-LOG.md:687` | **holds** — *"not the pattern — the screen… `fworker` passed it `cwd` alone"* |
| wq#2355's own live reproduction | re-run today, see §3 | **does not reproduce** |

## 1. Was wq#2290's `done` premature? — **No.**

wq#2290 was a **pri-1 rotation ticket**, not a hardening ticket. Its detail set the bar:
*"Identify the credential …, rotate it today, and close the leak path that printed it."*

- **Identify:** done, and correct. No credential was burned; the hit was
  `harness/tests/test_token_injection.py`'s `sk-ant-oat01-<dummy>` fixtures plus an
  unanchored `sk-` prefix. Independently re-confirmed five days later by wq#2323
  (ISSUE-LOG row 722) from scratch. Not disputed by wq#2355 either.
- **Rotate:** correctly *not* performed — rotating on a false positive is the failure
  mode, not the fix.
- **Close the leak path that printed it:** the leak path in scope was singular and
  in-`cwd` (wq#2278's read of a tracked file in the worktree). `burn_filter` closes it,
  is correct within its single-root design, and still passes 40/40 today.

Everything the ticket asked for was delivered and demonstrated. A rotation ticket does
not become premature because a hardening patch it volunteered has a narrower scope than
the general problem.

**Materiality of the gap is also bounded, and in the fleet's favour:**

```
2026-09-20 22:36:38Z  wq#2319 created      (by the PRE-814 unanchored pattern layer)
2026-09-20 23:09:47Z  PR#814 merged        (cwd-only screen goes live)
2026-09-20 23:10:27Z  wq#2290 done
2026-09-20 23:29:19Z  wq#2319 claimed
2026-09-20 23:44:31Z  wq#2319 done         (PR#835, provenance_roots plural)
```

The cwd-only screen was live for **~35 minutes**, and the very next worker in the same
chain found and closed the gap. wq#2355's framing that wq#2319 was *"filed ~33 minutes
before PR#814 even merged"* is factually right but reads as an indictment; the sequence
it actually describes is the rotation chain converging, which is what it is for. No
rotation ticket attributable to the merged screen's cwd-only scope was filed inside that
window.

**What *is* defective is one sentence of the done-notes.** *"35 tracked files page
before, 0 after"* is offered as evidence that the false-positive class is closed. It
cannot be: the probe population is tracked files under `cwd`, which is exactly the
population `is_committed_literal(value, cwd)` was written to excuse. The probe could not
have failed. That is an **evidence overclaim in the notes**, not a premature closure —
and the right remedy is a correcting `wq note`, not a reopen of a correctly-closed,
already-superseded pri-1.

## 2. Is this a closure-quality signal worth tracking? — **Yes, but not as a new metric.**

Name the defect precisely, because "wq#2290 closed early" is the wrong lesson and would
generalise badly:

> **Confirmatory probe.** An acceptance probe whose inputs are drawn only from the
> population the fix was derived from. It can confirm and can never falsify, so it
> carries no information about the fix's boundary — which is the only thing in doubt.

For any fix that takes a **scope parameter** — a root, a dir list, a repo, a host, a
table, a model pool — the probe must include at least one input from *outside* that
parameter's default value. wq#2290's fix took `repo_root`; its probe never varied
`repo_root`. `/home/lumen/kb/ISSUE-LOG.md` was sitting right there as the one read
doctrine guarantees on every single ticket, including wq#2290's own attempt.

**The existing instrument cannot see this.** `wq closure-quality` (`/home/lumen/bin/wq:3231`,
wq#759) buckets done tickets as verified / no-artifact / unverified purely on artifact
presence and landedness (`wq:2601-2649`, `2705-2815`). wq#2290 scores **verified** — it
has a landed PR. The fleet reads 95% verified for W38 and W39 on that metric while this
failure mode passes straight through:

```
lane       week       done  verified  no-artifact  unverified  pct-verified
fleet      2026-W38     85        81            4           0           95%
fleet      2026-W39     59        56            3           0           95%
```

Do **not** add a probe-adequacy counter to it. Artifact presence is mechanically
checkable; probe adequacy is a judgment, and a metric that needs judgment per row
degrades into noise. The fleet already owns the correct instrument for this class:
`verify_pass` (`research/models.yaml:429-437`), an adversarial re-read of a shipped diff
by a fresh instance, `required_for: [rca-investigation, standard-engineering]`. **It
worked here** — wq#2355 is that gate firing and catching exactly what the self-probe
missed. Its only fault was latency, and latency on a 35-minute-lived gap that a sibling
ticket had already closed is an acceptable cost.

So: track it as a **named rule in doctrine**, not a counter. See §4.

## 3. wq#2355's own reproduction does not reproduce — same defect, mirrored

wq#2355's headline block reports, against today's real `ISSUE-LOG.md` under PR#814's
code:

```
hit labels: ['cloudflare', 'sk-key', 'kv-secret']
classify(repo_root=/home/lumen/foreman)  burned: ['kv-secret']
classify(repo_root=/home/lumen/kb)       burned: []
```

Re-run today with the same inputs (`git show 281327c:harness/{masking,burn_filter}.py`,
`/tmp/fworker-wq2630/repro*.py`):

```
working tree today:  hits ['cloudflare', 'sk-key']            # no kv-secret at all
  repo_root=/home/lumen/foreman -> burned []                  # cwd-only burns NOTHING
  repo_root=/home/lumen/kb      -> burned []
kb HEAD 6bbb609:     hits ['cloudflare', 'sk-key']
  repo_root=/home/lumen/foreman -> burned []
  repo_root=/home/lumen/kb      -> burned ['cloudflare']      # the INVERSE of the report
```

Three findings, each checked rather than inferred:

1. **No revision of `ISSUE-LOG.md` in kb's entire history produces a `kv-secret` hit**
   under PR#814's masker — all 17 revisions, `2026-09-02T10:21` through
   `2026-09-25T06:26`, scanned (`/tmp/fworker-wq2630/repro5.py`). Only the two revisions
   from today score any label, both `cloudflare` + `sk-key`, both benign against
   `cwd=/home/lumen/foreman`.
2. **The fixture wq#2355 quotes has never existed in the file.** It cites
   `TOKEN="abcdefgh12345678` at `ISSUE-LOG.md:831`. `grep -c` for that literal:
   **0** in the working tree, **0** at `0df6e21`. Row 722 writes that fixture
   deliberately defanged — the `TOKEN=` prefix and the quoted body are two separate
   string literals joined by `+`, so the concatenation never appears as one run of
   characters and cannot trip the masker (paraphrased here rather than quoted, for the
   same reason). It sits at line 833, and is the only occurrence in the log.
3. **Today the cwd-only root is the quieter of the two.** The credential-shaped strings
   the log quotes are quoted *from foreman's own committed fixtures and canaries*, so
   `git grep` in `cwd` finds them; `kb` alone does not. wq#2355's own demo, re-run,
   argues the opposite of what it concludes.

I cannot rule out that kb's working tree held a transiently different, `kv-secret`-shaped
line at ~05:00-06:00Z today — the file is uncommitted-dirty right now (`git -C
/home/lumen/kb status --porcelain` → ` M ISSUE-LOG.md`) and is batch-committed, so
intermediate states leave no trace. That is precisely the problem: **wq#2355 pinned its
probe to a live mutable file in another repo and did not snapshot it, so its central
piece of evidence is not re-runnable** — the same class of defect it charged wq#2290
with, one rung up.

**This does not overturn the CONTRADICT.** The verdict's load-bearing evidence is not
wq#2355's demo; it is ISSUE-LOG row 594 / wq#2319, written the same night, replayed
against the **frozen wq#2288 transcript snapshot** (`in_worktree=False in_kb=True` on
three `sk-key` values), by a worker that then fixed the code and shipped tests. That is
contemporaneous, pinned, and independently reproducible from the snapshot. The gap was
real. wq#2355 reached the right verdict on borrowed evidence and then decorated it with
a demo that does not hold up.

Grading wq#2355: **verdict CONFIRMED, headline evidence DOWNGRADED.** Correct call,
correct stand-down from re-fixing already-merged work, sound reading of the merged diff
(§"What was verified and holds up" all re-checked and accurate) — with one unpinned
reproduction that should not have been presented as the centrepiece.

## 4. Process change — one rule, where every worker already reads

Landed in this PR at `harness/prompts/worker.md:25-34`, adjacent to the existing
*"'Done' means demonstrated"* line, because that is the sentence workers are already
reading when they decide what evidence to gather:

> **An acceptance probe must be able to fail.** If your fix takes a scope parameter (a
> root, a dir list, a repo, a host, a model pool), probe at least one input from OUTSIDE
> that parameter's default — for `--add-dir`-aware code that means a granted non-`cwd`
> directory, `/home/lumen/kb/ISSUE-LOG.md` being the read doctrine guarantees on every
> ticket. Pin the probe's input to something immutable (a committed fixture, a rev-pinned
> read), never a live mutable file in another repo — an unpinned probe is not re-runnable
> evidence.

Sentence one is the wq#2290 lesson; sentence two is the wq#2355 lesson. Both are
cheap to obey and both would have been caught at authoring time.

Deliberately **not** done, and why:

- **No reopen of wq#2290.** Correctly closed against its own bar, superseded in 35
  minutes, reopening is churn. A correcting `wq note` is appended instead.
- **No code fix.** `provenance_roots` is live on HEAD with 40 passing tests, and the
  pattern layer independently closed the same door: current `harness/masking.py` scores
  **zero labels** on today's `ISSUE-LOG.md`. Two independent layers now hold.
- **No new closure-quality counter.** Reasoned in §2.
- **No change to `verify_pass` routing.** The gate fired and was right; the config at
  `research/models.yaml:429-437` needs nothing.

## Summary for the chain

| question | answer |
| --- | --- |
| (1) wq#2290 closure premature? | **No.** Bar met and demonstrated. One done-notes sentence overclaimed; corrected by note, not reopen. |
| (2) closure-quality signal worth tracking? | **Yes — as doctrine, not a metric.** Named: *confirmatory probe*. `wq closure-quality` structurally cannot see it; `verify_pass` already can and did. |
| (3) process change? | **Yes, one rule**, `harness/prompts/worker.md:25` — probes must be able to fail, and must be pinned. |
| bonus | wq#2355's live reproduction does not reproduce; verdict upheld on wq#2319's pinned evidence instead. |

Correcting `wq note`s appended to wq#2290 (close stands, done-notes sentence corrected)
and wq#2355 (verdict upheld, headline evidence downgraded). Neither reopened.

Refs: wq#2290, wq#2319, wq#2323, wq#2355, ISSUE-LOG rows 594/716/722/759.
