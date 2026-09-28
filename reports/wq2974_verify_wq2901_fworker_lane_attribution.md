# wq#2974 — verify-pass on wq#2901 (fworker episode commits 403)

## Verdict: CONFIRM

wq#2901's fix is correct, deployed, and empirically working. No contradiction found.

## What shipped (read cold, across two repos)

- **foreman** `91b2457` (`fix(episodic): ref the ticket so the store can derive
  the lane`) — `memory/episodic.py`: `digest["ref"]` is now `wq#<ticket>` instead
  of the episode file path; `episode_bin_path()` re-resolves `LUMEN_EPISODE_BIN`
  per call instead of at import time; `harness/tests/conftest.py` gets an
  autouse fixture stubbing `LUMEN_EPISODE_BIN` so the shim test suite stops
  writing real rows into the live store under `fworker-wq1` (was 319
  memoryd rejects/day from pytest alone, per the fixture's own comment).
- **minerva** `b319bf7` (PR #44, `fix(memoryd): fleet workers get lane
  attribution from the claim receipt`) — `v4/memoryd/kernel.py` /
  `v4/memoryd/daemon.py`: adds `FWORKER_IDENTITY_RE` (`^fworker-wq(\d+)$`),
  `Kernel.ticket_claimant()`, `Kernel.lane_drift()`; the `/commit` identity
  gate now branches on the fworker name shape — accepts when
  `source_ref == "wq#<N>"` **and** `ticket_claimant(N) == agent` (claim
  provenance from `queue.db`, not the session registry fworkers are
  deliberately absent from), 403s a foreign claim or bad `source_ref`,
  degrades reads to an `"unattributed"` identity rather than blocking them.
  `/healthz` gains `lane_drift` (lanes `wq` dispatches with no contract row).
  Also adds the missing lane rows (`build`, `pilot`, `fleet-fires`, `ops`,
  `mailbot`, …) to `v4/schema/memory_contract_v1.yaml`.
- **minerva** `2a59855` (PR #45) — `lane_drift()` counts a lane before its
  first ticket exists as drift too (off-by-one the first commit missed).
- **minerva** `42d8c2a` / PR #46 (already merged into `main`, verified
  byte-identical via `git diff pr46 main`) — `v4/probes/drills_575.py`:
  replaces the hardcoded, long-purged `mv4-w575-05322f` fixture identity with
  one that mints a live `queue.db` session when the ambient `WQ_AGENT` is an
  `fworker-wq<N>` name (which cannot speak for ticket 575), and makes `D20`'s
  committed content carry the ticket id + run timestamp so a second run
  can't get graded against the first run's deduped row (`kernel.py`
  dedupes identical content to the first note's id/lane).

## Adversarial checks

1. **Ran the live probe myself, twice, as an actual fleet worker
   (`WQ_AGENT=fworker-wq2974`)** — exactly the caller shape this fix targets,
   and exactly the shape the old drill script could not represent (it
   hardcoded a session name a fresh-instance fworker isn't). Both runs:
   `drill_agent()` correctly detected the ambient `fworker-wq2974` identity
   is unusable for a wq#575 drill and minted a fresh session
   (`mv4-drill-0d5cb2`, then `mv4-drill-<other>`), then:
   - `D19_no_lane_drift`: PASS, `[]` — no lane `wq` dispatches is missing from
     the contract.
   - `D20_fworker_attribution`: PASS both runs — `accept http=201
     lane=build (ticket lane=build)` on wq#2974's own ticket lane, **and**
     the foreign-claim half correctly 403s (`fleet worker fworker-wq297[56]
     must write with source_ref=wq#297[56], not '...'`) — confirms the gate
     rejects a claim/ref mismatch, not just accepts a matching one. This
     matches the ticket notes' own acceptance bar ("D20 accept http=201 lane
     from the ticket on two consecutive runs ... with the foreign-claim half
     still 403").
2. **11 of 22 drills still fail** (`D12_accept_path`, `D8`, `D13`, `D14`,
   `D15`, `D10`, `D5`, `D2_kill9_atomicity`, `D3`, `D16`, `D17`) — I checked
   this isn't a regression before accepting it. All 11 hardcode
   `SRC = "wq#575"`, a ticket closed in August that no live session can hold
   a claim on; `D17`'s own detail spells the shared cause: `agent does not
   hold a live claim on wq#575`. This is filed separately as **wq#2971**
   (open, unclaimed), whose own text lists the identical 11 drill names and
   cites a before/after probe against the **pre-fix** daemon
   (`b319bf7^`, `/tmp/fworker-wq2901/prefix_baseline.json`) returning the
   *same* reject reason — i.e. these fail with or without wq#2901's diff, so
   they don't count against it. My own live run reproduces the identical 11
   names, corroborating wq#2971's claim rather than contradicting wq#2901.
3. **Live `memory_audit`, not just the probe**: fworker-identity rejects
   ("not in the wq session registry") for `agent_id LIKE 'fworker-wq%'` are
   **0** since 09:20Z (the fix window), against **16** successful fworker
   writes logged in the same window — real fleet traffic, not just the drill.
4. **Unit tests**, run fresh in this session (not trusted from the PR
   description):
   - `memory/tests/test_episodic.py` (foreman worktree): 18 passed.
   - `v4/memoryd/tests/test_fworker_identity.py` (minerva `main`, fetched via
     `git fetch` of the gitea repo — this worktree has no minerva checkout):
     11 passed.
   - `tests/test_episode_kind_contract.py` (minerva `main`): 10 passed.
5. **Checked for a forgery/read hole**: unregistered read access
   (`for_write=False`) degrades to an `"unattributed"` identity rather than
   403ing, so an `fworker-wq<N>` name can read without holding N's claim.
   This is called out and accepted in the design doc (§2.5, "reads are the
   lesser risk") rather than left implicit — a real scope choice, not a
   silent gap, and out of scope for the 403-on-write symptom this ticket
   names.
6. **Checked the caller contract, not just the diff**: `harness/fworker.py`'s
   `wq_identity()` returns exactly `fworker-wq{ticket_id}` (matches
   `FWORKER_IDENTITY_RE`), and `wq done` (`/home/lumen/bin/wq:1414`) writes
   `claimed_by` rather than clearing it — only `wq release` clears it
   (`wq:768`) — so `ticket_claimant()` still resolves to the fworker identity
   at the point `commit_episode_to_archive()` runs (before `wq done`/`block`/
   `release`, confirmed at `harness/fworker.py:3969` vs. the reconcile call
   after it). The claim-provenance design assumption holds against the
   actual caller.

## Not re-litigated

wq#2953 already verified the *deployment* half (daemon restart loaded the
`afba293` contract, `lane_drift=[]`, rejects stopped) — see
`reports/wq2953_memoryd_restart_verified.md`. This report focuses on the
diff's correctness and the probe's evidence; the two overlap only in citing
the same live `/healthz` and `memory_audit` state, independently re-queried
here.

## Evidence

- `git fetch http://100.121.17.5:3000/lumen/minerva.git refs/pull/44/head
  refs/pull/45/head refs/pull/46/head main` into a scratch clone under
  `/tmp/fworker-wq2974/minerva-check` (this worktree has no minerva checkout;
  `origin` here is `claude-foreman`, a different gitea repo).
- `python3 /home/lumen/minerva/v4/probes/drills_575.py <out>` — run twice,
  outputs saved under `/tmp/fworker-wq2974/drills_out.json`,
  `drills_out2.json`.
- Read-only `sqlite3` query against
  `file:/home/lumen/minerva/minerva.db?mode=ro` for `memory_audit` reject/
  write counts since `2026-09-28T09:20:00Z`.
- `/home/lumen/bin/wq show 2901`, `wq show 2971`, `wq show 2974`.
