# wq#899 — PR backlog reconciliation

2026-08-23. Follows FLEET_OPS_AUDIT 2026-08-23 (grade B), which found ~40 open pull requests
across the Lumen Gitea repos whose work-queue tickets were already `done`.

## What was actually wrong

`wq done` gained an evidence gate in wq#759: a ticket cannot close unless `--notes` names a
verifiable artifact. A pull-request URL satisfies it. But the gate only ever **shape-checked**
the URL — it never asked the forge whether the PR had landed. So "done" could mean "an agent
opened a branch," and 30 tickets meant exactly that.

The sharpest case was wq#825. Its dispatcher health alarm was running in production, but the
only copy of the source was PR #80's branch and a working tree. A clean checkout would have
deleted it, and the ticket said the work was finished.

Two thirds of the backlog turned out to be something less alarming and more interesting: PRs
that were never going to land, held open by nothing but the absence of anyone to close them.

## Method

`ops/wq899-pr-recon/pr_recon.py` joins four independent facts per PR, because no single one
of them decides anything:

| question | source |
|---|---|
| is the PR open, and does the forge think it merges? | Gitea API |
| is the head already an ancestor of main? | `git merge-base --is-ancestor` |
| is every commit already upstream by patch-id? | `git cherry` — catches squash and cherry-pick |
| what does the ticket say, and which PR do its own notes cite? | `queue.db` |

That last column did most of the work. The dominant pattern was **losing duplicate arms**: two
agents pick up the same ticket, one PR merges and gets written into the ticket's closing notes,
the other stays open forever. Five PRs (#33, #54, #55, #92, #109) were that, provably — each
ticket's own evidence names a different PR, and that PR is merged.

The second pattern was the CAL-1b calibration trials (wq#807–815): four tasks, each handed to
two configurations to measure variance. Eight PRs, four pairs. The deliverable was the
measurement, already recorded on the tickets; the code was probe payload. Both arms of a pair
cannot land, and picking one is arbitrary.

## Disposition

Every decision, with its evidence, is in `ops/wq899-pr-recon/decisions.tsv`.

**Landed (10)** — real work that was not on main:

- `claude-foreman#80` (wq#825) — the urgent one. Deployed, unmerged, one clean checkout from gone.
- `claude-foreman#112` (wq#841) — durable `reopened_count`, purely additive.
- `claude-foreman#86` `#98` `#91` — wq#759 gate docs, its verify pass, and the A11 maintenance
  window design.
- `claude-foreman#107` (wq#870) — `fworker_stats.py` empty-section crash.
- `claude-foreman#101` `#102` (wq#857, wq#859) — VictoriaLogs and Renovate, both already live
  out of band; landing them makes main match production instead of trailing it.
- `claude-foreman#52` (wq#753) — A3 node_exporter rollout.
- `minerva#7` (wq#603/654/655) — federated tier-penalty sweep.

**Closed with a stale-reference note (16)** — nothing lost:

- Already on main, empty diff: `lumen-bin#13`, `questfree#4`.
- Superseded by a merged PR the ticket itself cites: `#33`→#34, `#54`→#105, `#55`→#97,
  `#92`→#89, `#109`→#100, `#90`→#112 plus d9e25a9.
- CAL-1b calibration arms: `#64` `#65` `#66` `#67` `#68` `#69` `#70` `#71`.

**Ticket reopened, PR left open (5)** — real work that does not merge cleanly. Rebasing needs a
force-push, which this lane cannot do, so the honest move was to stop claiming the ticket was
finished:

`#36` (wq#731), `#53` (wq#763), `#73` (wq#780), `lumen-bin#16` (wq#662), `mission-control#5`
(wq#627). Each PR carries a note saying why it is still open and what to do with it.

`#53` is worth a footnote: it merged cleanly at the start of this pass and conflicted by the
end, because #112 landed an errata file next to the A13 baseline it adds. Reconciling a backlog
changes the backlog.

Open PRs went 37 → 11. Of the 11, ten sit on tickets that are legitimately in flight, and one
(`#73`) carries an explicit note.

## The class fix

Reconciling the backlog by hand is worthless if the next month refills it, so wq#759's gate now
resolves the PR instead of parsing it. `ops/wq899-pr-recon/wq899_merge_state_gate.diff`:

- **merged** → closes, as before.
- **open, head already an ancestor of the base branch** → closes. A squash or cherry-pick
  leaves the PR open while the code genuinely is on main; refusing that would train agents to
  route around the gate.
- **open, head not on main** → **refused**, exit 3, naming the PR and what to do.
- **closed without merging** → refused.
- **forge unreachable, no token, unparseable answer** → warns and allows.

That last line is deliberate. **The gate fails open on infrastructure and closed on facts.** A
gate that wedges every lane in the fleet the first time Gitea restarts is a worse bug than the
one it prevents. `--unmerged "<reason>"` is the on-the-record escape hatch, matching wq#759's
existing `--no-artifact`, and it tags the ticket with `[UNMERGED-PR: ...]`.

`ops/wq899-pr-recon/test_merge_state_gate.py` proves all eight cases end to end: it applies the
shipped diff to a copy of the live `wq`, runs it as a real subprocess against a scratch queue DB
and a fake forge on localhost, and asserts the exit codes. Applying the diff is part of the
test, so the artifact cannot silently rot away from the tool it patches.

```
$ python3 ops/wq899-pr-recon/test_merge_state_gate.py
PASS  merged PR closes the ticket
PASS  open unlanded PR is REFUSED
PASS  open PR already on main is accepted
PASS  unreachable forge warns but allows
PASS  --unmerged closes and is recorded
PASS  file-path artifact still closes without the forge
PASS  PR closed without merging is REFUSED
PASS  wq#759 empty-artifact gate still bites
```

The patch is **not yet applied to `/home/lumen/bin/wq`** — this lane can only write inside its
own worktree, and `wq` lives in lumen-bin. wq#905 tracks applying it, following the wq#841
precedent of shipping a reviewed diff here and applying it out of band. Leaving that untracked
would have reproduced the exact failure this ticket is about.

## Re-running the audit

```
python3 ops/wq899-pr-recon/pr_recon.py           # the full join, one line per open PR
python3 ops/wq899-pr-recon/pr_recon.py --gate    # exit 1 if any done ticket has an unnoted open PR
python3 ops/wq899-pr-recon/reconcile.py --dry-run
```

`--gate` is the acceptance criterion as a command rather than a claim. It currently reports
`11/11 open PRs reconciled`.

## Did the ten merges break anything

No. The foreman suite went **12 failed / 476 passed** before to **12 failed / 514 passed**
after: the same failure count, and 38 more tests passing — the ones #80 and #112 brought with
them. The 12 failures are pre-existing, in `test_fworker_integration.py` (6),
`test_shim_matrix.py` (5) and `test_local_rung.py` (1), none of which any merge touches.

A caveat on method, because it nearly produced a false alarm. An intermediate run reported 50
failures. It had been started immediately after `git rebase` and overlapped a `lumen-pr open`
push in the same worktree, and every test that broke shells out to git. On a quiet tree the
number is 12. **Do not run this suite concurrently with git operations in the same worktree** —
it reports the interference as test failures, and the shape of the result (a plausible-looking
regression right after a batch of merges) is exactly the shape that invites a wrong revert.
