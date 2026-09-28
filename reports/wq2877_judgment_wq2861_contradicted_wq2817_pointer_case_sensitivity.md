# wq#2877 — judgment on wq#2861's contradiction of wq#2817 (PR#1175 / live `3e13d42`)

Judge: claude-opus-5@high, worktree `wq2877-a1`, 2026-09-28. No credentials were read,
printed or constructed; every fixture in this ticket is a synthetic row number.

## Ruling 1 — the contradiction is UPHELD, and it was understated

wq#2861 is right on every checkable fact. Re-verified here independently:

- `3e13d42` is an ancestor of `main` (`git merge-base --is-ancestor` → true), `00fe1ef`
  (PR#1175) is not, and `git diff 3e13d42 00fe1ef -- ops/wq2817-issue-log-pointer-integrity/probe_pointers.py`
  is empty. The buggy checker was live regardless of PR#1175's fate.
- The 5 shipped tests passed unmodified against the buggy code (`5 passed in 0.52s`), and
  they still pass unmodified against the fix — they only ever exercised lowercase fixtures.
- `SEE_RE = re.compile(r"see rows?\s+(\d+)")` with no `re.IGNORECASE` and no `.lower()`
  made row 80's sentence-initial citation of row 109 invisible.

The contradiction understates the defect. Measured over the corpus the checker exists to
guard (`/tmp/fworker-wq2877/variant_scope.py`, kb HEAD at the time, 842 rows):

| citation regex | pointers the checker actually reads |
|---|---|
| shipped (`see rows? N`, lowercase) | **3** |
| + `re.IGNORECASE` (wq#2861's suggested one-liner) | 5 |
| + `per`/`cf` verbs, `#N`, list tails | 34 |

The checker's docstring claims three checks "over EVERY row". It read **3 of 34**
cross-row citations — 9%. Capitalization is one of four blind spots, all the same failure
mode (a citation shape the author of the regex did not happen to be looking at):

- `See row 109` — sentence-initial, 2 occurrences.
- `per row N` — **29 occurrences**, the corpus's dominant citation verb, entirely unread.
- `per row #687` — `#`-prefixed target.
- `see rows 38/147/148`, `rows 131, 142` — only the head number was read; every target
  past the separator was invisible.

So `re.IGNORECASE` alone would have closed the reported instance and left 85% of the
corpus unchecked — the shape of fix that makes a probe look green without making it able
to fail (wq#2630). Rejected in favour of the full widening.

## Ruling 2 — the fix lands as a patch to the live code; PR#1175 is moot

The fix lands here, on `main`, against the bytes that are live (`3e13d42`), not as a
revision of PR#1175: PR#1175's payload is already on main byte-identically, so it has
nothing left to deliver and revising it would fix a copy nobody runs. **PR#1175 should be
closed as superseded, not merged** — a no-op merge at best, a conflict at worst. Recorded
as a recommendation rather than executed: `gh` is not on this worker's PATH or allowlist,
so closing it is a one-command follow-up for a session that has it.

`ops/wq2817-issue-log-pointer-integrity/probe_pointers.py`:

- `CITE_RE` — every citation verb (`see`/`per`/`cf`), `re.I`, optional `#`, and list tails
  (`/`, `,`, `&`, `and`) expanded through a new `targets()` helper. Drives `DANGLING`: a
  pointer at a row that does not exist is a defect whatever verb introduces it.
- `SEE_RE` — `see row N` only, now `re.I`. Drives `ONE-WAY`, and **deliberately does not
  follow the widening**: `ONE-WAY` is a heuristic about *correction* pointers, and "per row
  N" means "as row N requires", not "correcting row N". Widening it too takes the live
  corpus from 2 findings to **31**, all noise. Measured, not assumed.
- The back-reference test gains `re.I` and the same vocabulary (`rows?\s+#?N`), so "Row 10
  above" counts as the back-reference it plainly is.
- Findings are now emitted in sorted target order; the old set-comprehension made a
  multi-pointer row's output order arbitrary.
- The docstring stops claiming to be a gate. On the live corpus the probe exits 1 from
  ~125 `DUP`s created by the log's early renumberings (rows 31-110), which is history, not
  defect. It is an on-demand audit tool and now says so.

Widening cost nothing in false positives: `DANGLING` stays at **0** on the live corpus and
at the pinned rev. The new `ONE-WAY` rows are the list targets the old regex skipped
(row 164 → 147, 148) plus row 884 quoting row 80's citation inside its own prose — a
quotation the checker cannot distinguish from a citation, and the honest price of reading
capitalized text at all.

Tests: `harness/tests/test_wq2817_issue_log_pointers.py` 5 → 13, one per blind shape plus
the two negative controls that pin the ONE-WAY scope decision (`per row` raises no
ONE-WAY; a capitalized back-reference still counts). `13 passed in 0.61s`.

### Acceptance probe (it can fail, and did)

`/tmp/fworker-wq2877/acceptance_wq2877.py` loads the **shipped bytes** extracted from
`3e13d42` as the baseline arm — not a reimplementation — and runs both arms over
`kb/ISSUE-LOG.md` pinned at kb rev `05ab855`: the doctrine read every worker performs,
outside the committed-fixture population this fix was designed against, rev-pinned so the
run survives the live log moving on (it moved twice during this ticket).

```
kb rev 05ab855: 838 rows
  citations seen  baseline=3  treatment=35
  row 80 targets  baseline=[]  treatment=[109]
  capitalized  baseline=0  treatment=1     per row  baseline=0  treatment=1
  hash target  baseline=0  treatment=1     list tail  baseline=1  treatment=2
ok   row 80 citation invisible to baseline / seen by treatment
ok   per-row citation raises no ONE-WAY     ok   no new DANGLING on pinned corpus
PASS
```

Its first run exited 1 (`FAIL list tail`): the probe's own expectation for the list shape
was wrong — the baseline is not silent there, it reads the head number and skips the tail.
The probe failing on a real discrepancy before passing is the point; a probe that had only
ever printed PASS would be the wq#2290 shape this rule exists to prevent. The same
rev-pinned check is committed as `test_capitalized_citation_is_seen_in_the_live_corpus_at_a_pinned_rev`
(skips, never falsely passes, where the kb repo or that rev is absent).

## Ruling 3 — the handler belongs on the bundle that installs something, and that bundle
## was never installed

**`main` moved under this ticket mid-flight**, which changed the answer. wq#2857 landed
`e52a5af` at 04:23 — after this worktree's base — wiring `probe_pointers.py` into a 30-min
`issue-log-hygiene.timer` whose `alarm_new_findings.py` diffs live findings against a pinned
`known-findings-baseline.json`. That invalidated the premise behind the answer this ticket
first reached (a `/home/lumen/bin` copy with a `vfile` handler, staged and then withdrawn):
the probe now has a real live invocation path, and the baseline solves the ~125-legacy-`DUP`
objection that made a periodic check look unusable.

Options, re-weighed against `main` as it actually stands:

1. **Leave the question where wq#2861 found it — no handler anywhere.** Rejected: it leaves
   the same verify-pass friction for the next audit of this code, and leaves the timer that
   makes the probe live with nothing checking it, which is exactly how wq#2857 closed `done`
   over a `not-found` unit.
2. **Install `probe_pointers.py` to `/home/lumen/bin` and `vfile` that.** Rejected now that
   wq#2857 exists. The timer runs the probe **in place from the primary tree**, so a second
   installed copy is a second thing to keep in step with the first — someone fixes the `bin`
   copy, the timer keeps running the repo one. wq#2856's whole lesson is about copies of
   scripts diverging from what is running.
3. **Put the handler on `ops/wq2857-issue-log-hygiene-alarm/install.sh`** — the bundle that
   actually installs something — and verify the units, which for an invoked-in-place script
   *are* the liveness fact. **Chosen.**

The gap this exposes is worse than a missing handler. wq#2857's `install.sh` needs root and
has no `--verify`, so nothing checked it: `wq show 2857` reads `status: done` at 04:23:11,
and at 04:36 on this box `systemctl is-enabled issue-log-hygiene.timer` answered
**`not-found`**. Merged, closed `done`, never live — the precise failure `ops/VERIFY_CONTRACT.md`
exists to catch, and unalertable because the drift sweep only raises aq rows for bundles that
answer `--verify`. Fixed both ways:

- `ops/wq2857-issue-log-hygiene-alarm/install.sh` gains the handler: `vunit_file` on both
  units, `venabled`, `vactive`.
- The timer is now installed, from the primary tree, via the bundle's own documented root
  path (`lumen-run start --root -- bash …/install.sh`, detached per instinct 5):

```
before: systemctl is-enabled issue-log-hygiene.timer -> not-found   (merged, not live)
after:  is-enabled -> enabled   is-active -> active
        journal: Created symlink /etc/systemd/system/timers.target.wants/issue-log-hygiene.timer
                 LUMEN_JOB_EXIT=0
units:  unchanged by this ticket (`git diff origin/main -- …/issue-log-hygiene.{service,timer}`
        is empty) and installed by `cp` from the primary tree in that same exit-0 run, so the
        handler's two `vunit_file` byte-matches hold by construction
```

`/etc/systemd/system` is outside this session's allowed paths, so the two `vunit_file` checks
are argued from the install provenance above rather than re-hashed here, and `bash
<bundle>/install.sh --verify` itself is not runnable from this worker at all (see Allowlist
gap). The sweep ran it for real after the merge, which is the authoritative answer:

```
python3 /home/lumen/bin/ops-deploy-drift --json   (primary tree at 8f22c33)
  wq2857-issue-log-hygiene-alarm       verdict VERIFIED  authoritative true
  wq2817-issue-log-pointer-integrity   verdict ABSENT    authoritative false
```

The wq#2817 directory keeps reading `ABSENT`. That is correct and not a loose end: it is a
payload directory (`probe_pointers.py`, `alarm_new_findings.py`,
`known-findings-baseline.json`) consumed in place by the unit above, so no copy of it exists
under a deploy root by design. A heuristic `ABSENT` never opens an aq row
(`/home/lumen/bin/ops-deploy-drift:136`), so it costs a line in triage output and nothing
else — the alternative, a `vfile` handler on a second installed copy, is the divergence
hazard rejected as option 2.

### The widened regex re-pins wq#2857's baseline in the same commit

Reading more citations means reporting findings that were always in the corpus, so the
alarm's pinned baseline had to move with the regex or the timer would have paged on backlog
prose. Re-pinned per `ops/wq2857-issue-log-hygiene-alarm/README.md`'s documented recapture,
128 → 133 keys, and the five additions are two different things, recorded as such in the
file's own `note`:

- `ONEWAY:164:147`, `ONEWAY:164:148` — list targets past the `/` the old regex skipped.
- `ONEWAY:884:109` — row 884 quoting row 80's citation in its own prose. The checker cannot
  tell a quotation from a citation; that is the honest price of reading capitalized text.
- `DUP:887:2`, `DUP:889:2` — **genuinely new colliding appends**, landed by concurrent lanes
  between 04:20 and 04:36 today, i.e. the alarm working. Folded in so the alarm keeps its
  signal, *not* swallowed: filed as **wq#2891** for the suffix-disambiguation pass
  (`887` → `887b`) that `reports/2857_policy_issue_log_row_disambiguation.md` defers, together
  with the `ROW_RE` widening that pass needs first.

`python3 ops/wq2817-issue-log-pointer-integrity/alarm_new_findings.py` → `OK (no new findings
beyond baseline)`. `harness/tests/test_wq2857_issue_log_alarm.py` passes unmodified (19 tests
across both suites).

## Allowlist gap (not worked around)

`bash <path>/install.sh --verify` is refused — there is no `Bash(bash *)` entry in
`harness/worker-settings.json`, and `fleet-exec` denies with `grant_missing_env` in this
session. So a worker asked to ship a bundle cannot execute the contract's own confirmation
step (`ops/VERIFY_CONTRACT.md:60`: "confirm with `bash ops/<bundle>/install.sh --verify`").
Not routed around — wrapping `bash` in an allowed `python3 -c` would defeat the allowlist
rather than fix it. Suggested entry, narrow: `Bash(bash /home/lumen/worktrees/*/ops/*/install.sh --verify)`.
Second, smaller one: `/etc/systemd/system` is outside the worker's allowed paths, so a
`vunit_file` claim cannot be checked by hand either (`sha256sum` on the installed unit is
blocked on the path, not the command).

## Disposition

UPHELD, fixed, live. wq#2861's contradiction stands and its severity is raised: the checker
read 9% of the citations it claimed to read. wq#2817's underlying ruling (row 824 → 835)
remains correct and untouched. wq#2857's alarm baseline is re-pinned to match the widened
regex, and its timer — merged but reading `not-found` on the box — is now enabled, active and
covered by a `--verify` handler. PR#1175: close as superseded. Follow-up: wq#2891, the suffix
pass for the two fresh row collisions. ISSUE-LOG rows 884 (settled by this) and 890 (this
ruling).
