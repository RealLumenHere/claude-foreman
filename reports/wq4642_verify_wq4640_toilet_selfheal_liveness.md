# wq#4642 verify-pass on wq#4640's judgment (PR 2468)

Verifier: build worker wq4642-a1 (claude-sonnet-5 @high, fresh instance), 2026-10-25.
Target: PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/2468, branch
`fix/wq4640-judgment-wq-4639-verify-pass-on-wq-4638--a1`, commits 044a8013 + f6687668.
wq#4640 was itself a judgment on wq#4639's CONTRADICT of wq#4638 (toilet `nodes.notes`
merged into the repo seed but never deployed to the live sweep config). Not
re-implemented — read cold, probed, and independently re-run.

## Verdict: CONFIRM

Every claim in the PR and its ISSUE-LOG row 1584 entry reproduces independently.

## What the diff actually ships

`git diff 9f24db34 f6687668 --stat`: 5 files, 461 insertions, 0 deletions — no code
change to any repo-tracked file:

- `ops/VERIFY_CONTRACT.md` (new) — documents the `install.sh --verify` contract.
- `reports/wq4640_judgment_wq4639_toilet_selfheal_notes_liveness.md` (new) — the writeup.
- `reports/wq4640_probes/{probe_can_fail.py,probe_gate_gap.py,probe_note_folds.py}` (new).

The actual fix — deploying the toilet note — is an out-of-band operational action
against `/home/lumen/agents/fleet-selfheal/selfheal.yaml`, outside any repo, by design
(ops bundles live outside the lane repo per `VERIFY_CONTRACT.md`). This matches the
precedent the judgment cites (wq#4460) and is not something a repo diff can show
directly, so it has to be checked against the live box, not the patch.

## Independent re-verification

1. **Live deploy, direct check** — ran the bundle's own predicate fresh, not copied
   from the PR:
   `python3 ops/wq3354-fleet-selfheal/selfheal_merge.py --check --seed <repo seed> --live /home/lumen/agents/fleet-selfheal/selfheal.yaml` → **rc=0** (no MISS).

2. **Authoritative contract check** — the literal `install.sh --verify` entry point
   `VERIFY_CONTRACT.md` requires, run via a `subprocess.run(["bash", ".../install.sh",
   "--verify"])` wrapper (the literal string is refused by the BUILD allowlist per
   ISSUE-LOG row 1500; this is the documented equivalent, not a substitute check):
   stdout `VERIFY OK`, **rc=0**. Satisfies this ticket's stated liveness gate,
   `ops_verified(wq3354-fleet-selfheal)`.

3. **All three probe scripts, re-run in place** (checked out from the PR branch into
   the already-existing `/home/lumen/foreman` checkout — the `reports/wq4640_probes/`
   relative-path math (`parents[2]`) requires running from inside a real checkout of
   this repo; copies were removed afterward, confirmed untracked before and after):

   - `probe_can_fail.py` → `PROBE PASS`. P1 (pre-note seed @7e93b7a4 as live) rc=1
     MISS `nodes.notes.toilet`; P2 (immutable pre-merge backup) rc=1, same MISS; P3
     (real live config) rc=0; P4 toilet not in `ignore`/`sleepers` (still pages); P5
     live note byte-identical to seed@9f24db34. All five sub-checks land where the
     PR claims — this is a probe that can fail (P1/P2 are the falsifying inputs) and
     didn't.
   - `probe_gate_gap.py` → `PROBE PASS (gate gap reproduced)`. Read `OPS_INSTALL_RE`
     live out of `/home/lumen/bin/wq`, confirmed e7d2dd76 (wq#4638's actual commit)
     produces zero gate hits despite touching a file inside a bundle dir that ships
     an `install.sh` — i.e. the root-cause claim (filename-scoped, not
     bundle-scoped) reproduces against the real regex, not a copy of it.
   - `probe_note_folds.py` → `PROBE PASS`. Deployed `sh_nodes.py` hash-matches the
     repo copy; toilet's note folds as `known issue:`, a pre-existing note
     (nephthys) still folds, a note-less control emits nothing.

4. **Standing fix ticket** — `wq#4641` exists (`/home/lumen/bin/wq show 4641`),
   title `[gate] wq done ops-deploy gate misses payload-only ops bundle changes
   (OPS_INSTALL_RE)`, and its root-cause text matches the judgment's claim verbatim
   (same file:line citations, same regex, same recurrence history).

5. **wq#4638 not reopened** — `/home/lumen/bin/wq show 4638` → `status: done`,
   consistent with the judgment's decision (3): the note is live, closing was correct
   in hindsight, nothing to reopen.

6. **"Not landable from this worktree" claim** — `git -C /home/lumen/bin diff --stat
   wq` shows an uncommitted 43-line diff to `wq` already sitting in the shared bin
   checkout, confirming the stated reason the standing fix (wq#4641) couldn't be
   committed from wq#4640's worktree.

## Nothing to contradict

No gap found between what the PR claims and what the live box / probes / tickets
show. The judgment made the right call on precedent (option (a), not waiting on a
root window), found a real mechanical root cause rather than patching the symptom a
third time, and filed the standing fix where it actually belongs (bin repo, separate
ticket) instead of pretending to land it from a foreman worktree that can't reach it.

## Board

CONFIRM — no escalation. wq#4642 closes done.
