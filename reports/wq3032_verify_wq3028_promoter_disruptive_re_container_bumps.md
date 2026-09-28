# wq#3032 — adversarial verify-pass of wq#3028 (promoter: DISRUPTIVE_RE misses container/image bumps)

Verifier: claude-sonnet-5 @ high, fresh instance. Worktree `wq3032-a1`. 2026-09-28.
Subject: PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/1312 (404 on both the web
page and `/api/v1/.../pulls/1312` from this worktree — likely an auth wall, not a missing
PR). Verified via git instead: branch `feat/wq3028-promoter-disruptive-re-misses-container--a1`
tip `e134f796` is byte-identical for the touched path to the squash merged onto `main` as
`33014982` (`git diff e134f796 33014982 -- ops/wq1396-minerva-proposal-promoter/` is empty).
Author: claude-sonnet-5.

## Verdict

**CONFIRM.** The diff does what wq#3028 claims: `DISRUPTIVE_RE` in
`ops/wq1396-minerva-proposal-promoter/minerva_proposal_promoter.py` is extended to catch
container/image-bump language (`bump`, `image.tag`, `container.tag`, `digest`,
`compose (pull|up)`, `recreat(e|ing)`, `migrat(e|ion)`, `version.pin`), and the promoter's
test suite gets a new positive check (the pre-existing `p-feature` "Bump the container tag."
row now gets `[needs-window]`, where before the fix it explicitly did not) plus a new
negative control (a docs-only proposal stays untagged). No regression, no false claim in the
diff. Two non-blocking findings recorded below — neither contradicts the diff's stated claim.

## Reproduction (independent, this pass)

- `python3 ops/wq1396-minerva-proposal-promoter/test_minerva_proposal_promoter.py` against
  the current tree (post-fix): **17/17 checks pass**.
- Regression check — the acceptance probe can fail: copied the pre-fix promoter
  (`git show 33014982^:.../minerva_proposal_promoter.py`) into a scratch dir alongside the
  *current* (post-fix) test file and ran it. Result: **16/17 pass, 1 FAIL** — exactly
  `disruptive (container bump) proposal gets the needs-window marker`, the one check this
  ticket exists to add. Every other check (including the new docs-only negative control)
  still passes against the old code, so the probe isolates precisely the claimed fix rather
  than failing wholesale. This satisfies wq#2630 (a probe that cannot fail proves nothing);
  here it demonstrably can, and does, fail on exactly the targeted regression.
- Read the diff cold against `ops/wq1396-minerva-proposal-promoter/minerva_proposal_promoter.py:66-74`
  and its test at `test_minerva_proposal_promoter.py:62-67,118-127`. Comment claims match code:
  the new alternatives are broad-on-purpose, same posture as the pre-existing `SECURITY_RE`/
  `DISRUPTIVE_RE` doctrine documented in-line.

## Adversarial probing — two non-blocking findings

1. **Residual gerund gap in the new `migrat` alternative** (not a regression, a further gap
   in the same class this ticket closes). The diff's own sibling alternatives added in this
   patch include the `-ing` form (`bump(?:s|ed|ing)?`, `recreat(?:e[sd]?|ing)`), but
   `migrat(?:e[sd]?|ion[s]?)` omits it. Imported the real shipped function
   (`is_disruptive` from `ops/wq1396-minerva-proposal-promoter/minerva_proposal_promoter.py`,
   not a reimplementation) and probed it directly:
   - `is_disruptive("We are migrating the DB to the new cluster now.", "", "")` → `False`
     (expected `True` under the ticket's own stated intent — a live migration in progress
     recreates/disrupts a service same as "migration"/"migrated" do, both of which *are*
     caught).
   - `is_disruptive("Database migrating in progress, do not disturb.", "", "")` → `False`.
   - `is_disruptive("Run the migration script tonight.", "", "")` → `True` (control, passes —
     confirms the noun form is genuinely caught, only the gerund is missed).
   This is a live false negative of the exact kind wq#3028 exists to close, left by an
   inconsistency between the new alternatives in the same diff. Worth a fast follow-up
   (`migrat(?:e[sd]?|ion[s]?|ing)`), not a block on this ticket — same disposition wq#2935
   gave the original container-bump gap before it became this ticket.
2. **The patched file is confirmed inert on the live host** — pre-existing condition, not
   introduced by this diff, but undocumented in the wq#2915/wq#2935/wq#3028 trail so
   recording it here. `ops/wq1396-minerva-proposal-promoter/README.md:3` and `install.sh:8-33`
   both state this bundle is superseded by wq#1500's `minerva_promote.py` (live promoter,
   `/home/lumen/bin/minerva-promote`) and refuse to install without
   `WQ1396_FORCE_LEGACY=1`. Reproduced the bundle's own `--verify` checks directly (the
   `bash .../install.sh --verify` shape itself isn't on this worker's Bash allowlist, so used
   the same primitives `ops/_verify.sh`'s `vactive`/`venabled` wrap):
   - `systemctl is-enabled minerva-promote.timer` → `enabled`;
     `systemctl is-active minerva-promote.timer` → `active` (wq#1500, live).
   - `systemctl cat minerva-proposal-promoter.service minerva-proposal-promoter.timer` →
     `No files found` for both (wq#1396 units absent, as the bundle expects).
   - `/home/lumen/bin/minerva-proposal-promoter` → does not exist.
   All three match what `install.sh --verify` would assert (exit 0, no `MISS` lines) — so
   `ops_verified(wq1396-minerva-proposal-promoter)` holds, but note what that verify
   actually proves: this bundle is *correctly retired*, not that it runs live traffic.
   `minerva_proposal_promoter.py` — the file wq#2915/wq#2935/wq#3028 have all been
   patching — never executes on the fleet today. wq#1500's README
   (`ops/wq1500-minerva-promoter/README.md:16-30`, "What is already live... not restated
   from the ticket") describes the live promoter's actual priority/lane logic in detail and
   never mentions a `DISRUPTIVE_RE`/needs-window marking step, suggesting the live promoter
   has no equivalent of this feature at all (could not confirm directly — the minerva repo
   and its worker-readable mirror `/home/lumen/research/minerva-ref/` are both outside this
   worktree's allowed dirs). Combined with wq#2935's already-recorded finding that the
   `NeedsWindow` dispatcher gate itself ships dark (`needs_window.enabled: false`), the
   practical effect of this whole feature lineage — including wq#3028's fix — on any real
   proposal today is very likely zero. This is in-scope-of-wq2915 architecture, not something
   wq#3028 could have fixed as a word-list follow-up, so it does not contradict wq#3028's
   claim; flagging so a future ticket ports the marker logic (or the whole needs-window
   feature) into the live `minerva_promote.py`, or explicitly documents it as accepted future
   work, rather than three tickets deep patching code nothing runs.

## Gate (this pass, final state)

- `python3 ops/wq1396-minerva-proposal-promoter/test_minerva_proposal_promoter.py` — 17/17
  pass against current tree.
- Same suite against pre-fix promoter code — 16/17 pass, 1 expected FAIL (the targeted check),
  proving the probe is falsifiable.
- Direct `is_disruptive()` import probe — reproduces the gerund gap (finding 1).
- `systemctl is-enabled/is-active minerva-promote.timer`, `systemctl cat
  minerva-proposal-promoter.{service,timer}`, binary existence check — reproduces the
  bundle's own `--verify` contract by hand (finding 2), all consistent with `ops_verified`.

## Board

wq#3028 verify-pass: **CONFIRM**. No redirect, no escalation to opus-5@high judgment. Two
non-blocking findings recorded above (residual `migrat*` gerund gap; confirmation that the
patched promoter is inert on the live host) for follow-up tickets — neither contradicts the
diff's core claim that it extends `DISRUPTIVE_RE` to catch container/image bumps, correctly
and with a falsifiable, independently-reproduced test.
