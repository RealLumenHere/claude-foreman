# wq#4342 — verify wq#4341 (PR#2241, README amendment to wq#4329's bundle)

**Verdict: CONTRADICT on how the fix landed, escalated to opus-5@high — judgment: CONTEST
(low severity). CONFIRM on content.** wq#4341's corrected text is factually accurate and
should stay. But it merged as a standalone, from-scratch README.md branched off main's tip
instead of as a commit on PR#2232's own still-open branch, which (a) leaves main's bundle
directory describing three files that do not exist there, and (b) guarantees a confirmed
add/add git conflict the moment PR#2232 eventually merges.

## What shipped (re-read cold)

`git show --stat 39002885` (merged into main via c8e18bbc, PR#2241): one file,
`ops/wq4329-direct-mcp-commander/README.md`, `new file mode 100644`, 98 insertions, no
deletions. wq#4341's own done-notes: "amended .../ops/wq4329-direct-mcp-commander/README.md
(still-open PR#2232) to strike the wrong ISSUE-LOG#1016 dead-end citation and redo A-vs-B
honestly."

## Content check: holds up

Independently re-verified every substantive claim in the amended README:

- `/home/lumen/kb/ISSUE-LOG.md` row 1016 status field reads `SOLVED (wq#3069); off-host
  verify still blocked` — matches the README's claim exactly.
- `/home/lumen/bin/lumen-run` line 54 documents `--root`, line 79 parses it, line 112 calls
  `sudo -n systemd-run --unit=... --collect "${runas[@]}" ...` — matches the `lumen-run:112`
  citation.
- `Bash(/home/lumen/bin/lumen-run *)` is on `harness/worker-settings.json`'s allowlist
  (line 19) — matches.
- `docs/fleet/retired_services.yaml:319-330` (`desktop-commander-mcp-lumen` entry) confirms
  the preserved unit backup at `/home/lumen/backups/wq3805/desktop-commander-mcp.service.orig`
  — matches.

No fabricated facts, no broken citations in the text itself.

## The contradiction: where it landed, not what it says

1. **Ancestry.** `git log -1 --format='%H %P' 39002885` → parent `020d70b3`, a commit on
   main's own mainline. `git merge-base 39002885 a8d41035` (PR#2232's commit, branch
   `feat/wq4329-project-direct-mcp-connections-per-machi-a2`) → `25f0b51c`, an ancestor of
   both. wq#4341 branched fresh off main's tip, not off PR#2232's branch, even though its
   own notes name that PR as still open.
2. **Orphaned description.** Current main (`ls ops/wq4329-direct-mcp-commander/`) has only
   `README.md`. `git branch -a | grep 4329` and `git merge-base --is-ancestor a8d41035 HEAD`
   (→ false) confirm `install.sh`, `remote-desktop-commander.service`, and
   `PHASE1-PAIRING-CHECKLIST.md` exist only on the still-unmerged PR#2232 branch. The merged
   README's own "What's in this bundle" section (lines 79–92) describes all three in present
   tense as if they ship in this bundle. They do not, on main, right now.
3. **Reproduced hazard, not hypothetical.** `git merge-tree c8e18bbc
   feat/wq4329-project-direct-mcp-connections-per-machi-a2` → `CONFLICT (add/add): Merge
   conflict in ops/wq4329-direct-mcp-commander/README.md`. Whenever PR#2232 merges, this
   conflict is guaranteed. Both sides are full-length README rewrites with no marker saying
   which is the correction — a naive resolution (take PR#2232's side, or a merge-bot landing
   it) would silently restore the "closed structural dead-end" ISSUE-LOG#1016 citation that
   wq#4340 and wq#4341 exist to remove.
4. **My own ticket's verify-handler requirement is structurally unsatisfiable.**
   `orchestrator/judgment.py:174-182` (`ops_bundles_cited`) matches any `ops/wqNNNN-*` path
   string in a shipped ticket's notes/detail/deliverable and includes it as long as
   `os.path.isdir()` is true — it never checks for an `install.sh`. wq#4341's notes named the
   bundle dir, so wq#4342 inherited "Bundle(s) without a --verify handler: ...Add one...
   before confirming." There is no `install.sh` on main for this bundle to attach a handler
   to; the real one is unreviewed content on PR#2232's branch. Writing one now would mean
   either fabricating install.sh content for files absent from the repo under verification,
   or copying another ticket's unmerged, unreviewed code into main under this ticket's name —
   both out of scope for a verify-pass ("do not re-implement"). This is a gate misfire
   (tracked separately, wq#4345), not grounds by itself to contest wq#4341 — but it is a
   direct symptom of the same landing defect.

## Escalation and opus-5@high judgment

Per protocol, a contradiction escalates to opus-5@high. Spawned a fresh opus-5@high instance
with the full ancestry/file/conflict evidence above and asked it to judge materiality and
next steps (not to re-implement). Verdict: **CONTEST, low severity** — the landing choice is
a real defect (a better move was available: commit the amendment onto PR#2232's own branch
instead of opening an independent PR against main's tip), main's description of files that
don't exist is actively misleading rather than merely transient, and the add/add conflict is
a confirmed future cost with a real (if not catastrophic) risk of a bad resolution silently
reintroducing the false citation. Severity is low because it's docs-only, nothing runtime
breaks, and the fix is small. The opus-5@high instance also independently located and
confirmed the `ops_bundles_cited` gate-misfire root cause at `judgment.py:143-154`.

Filed two follow-ups:

- **wq#4344** `[fix] wq#4341 README landed orphaned from still-open PR#2232 — resolve
  guaranteed add/add conflict` (deps: 4341, pri 2): merge main into PR#2232's branch, resolve
  the README conflict in favor of 39002885's corrected text, re-check the "What's in this
  bundle" section against the branch's actual files, comment on PR#2232 so no one restores
  the old text, then land it. If PR#2232 is abandoned instead, strip or banner main's
  orphaned README. Carries a falsifiable liveness probe: `git merge-tree` between main and
  the PR#2232 branch currently fails with `CONFLICT`; done means it doesn't.
- **wq#4345** `[fix] judgment.ops_bundles_cited cites doc-only ops/ dirs with no install.sh,
  misfiring the --verify gate` (deps: 4342, pri 3): require an `install.sh` to exist before a
  cited bundle dir triggers the "Bundle(s) without a --verify handler" line.

## Not re-litigated

- Whether the hosted-relay OAuth pairing completes end-to-end — out of scope, same as
  wq#4340.
- Rewriting the README or redoing the A-vs-B comparison myself — already done correctly by
  wq#4341; not re-implemented here.
- Adding a `--verify` handler to `ops/wq4329-direct-mcp-commander/install.sh` — no such file
  exists on main to attach one to (see point 4 above); fabricating one is out of scope for a
  verify-pass.
