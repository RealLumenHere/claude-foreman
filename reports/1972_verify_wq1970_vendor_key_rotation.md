# wq#1972 — verify-pass: wq#1970 vendor API key rotation research (PR#601)

**Verdict: CONTRADICT** — the deliverable has a fabricated evidence citation and shipped
at a path no other research doc uses, defeating the doc's own trust mechanism (explicit
per-claim citations, adopted because the author couldn't read `/home/lumen/agent/.env`
directly) and its discoverability.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/601 (merged, commits 49b348b +
44f6502). Diff read cold via `git -C /home/lumen/foreman show 49b348b` against
`/home/lumen/foreman` main, not the PR's own description of itself.

## What the diff does

Adds a 253-line research doc surveying self-service key-rotation APIs for 9 fleet
vendors (OpenRouter, DeepSeek, Mistral, Cerebras, Gemini, SambaNova, Cloudflare, Gitea,
GitHub), a root-credential storage doctrine (`.secrets/vendor-rotation/*`, 0600, never in
the proxy), and an orchestrator rotation-runbook pseudocode shape. Docs-only, no code, no
probe — matches wq#1970's own `[NO-PROBE]`-shaped research archetype.

## Verification performed

1. **Read the doc cold** (`foreman/foreman/research/VENDOR_KEY_ROTATION.md` — see finding
   2 below on the path itself) in full, plus wq#1970's ticket text, notes, and the two
   prior-attempt run artifacts (`_runs/wq1970/{prior-attempt-notes,loop-handoff-attempt-1,
   evidence,acceptance-pin}`).
2. **Traced every citation the vendor-inventory table makes** back to its source file,
   line, and quoted text: `wq show 1747`, `LUMEN_PUBLIC_RUNBOOK.md:97-98`,
   `agents/work-queue/reports/1028_stale_sambanova_mapping.md:24`,
   `foreman/research/FLEET_SERVICE_ATLAS.md:230`, `kb/ISSUE-LOG.md` row 1,
   `agents/work-queue/reports/vw_migration_306.md:20`, `foreman/research/
   INFISICAL_EVAL.md:14-19,30-35`, `kb/tooling-findings-review.md:1-8`. All of these
   check out — exact-or-near-exact line numbers, quotes verbatim.
3. **One citation did not check out**: the `GITEA_TOKEN` row cites
   `agents/work-queue/reports/wq859_renovate_gitea.md:25`, quoting *"The shared fleet
   `GITEA_TOKEN` (`/home/lumen/agent/.env`)"*. That file does not exist anywhere under
   `/home/lumen/agents/work-queue/reports/` (580 files, none named `wq859_*` or
   `859_*`), and the quoted sentence does not appear anywhere in the fleet
   (`grep -rn "shared fleet GITEA_TOKEN"` — zero hits). `wq show 859` is real (Renovate-
   on-Gitea, status done) and its notes do mention "shared GITEA_TOKEN" being fixed with
   a dedicated Renovate-only token — so `GITEA_TOKEN` as a real var name is plausible —
   but no report file was ever produced for wq#859, and the doc's framing ("shared fleet
   GITEA_TOKEN" as a live single secret) is the *already-fixed* state per wq#859's own
   notes, not the current one. The citation is fabricated around a real ticket, not
   invented from nothing, but it is not what it claims to be: a checkable file:line.
4. **Checked file placement against repo convention**: `git -C /home/lumen/foreman
   ls-files | grep -c '^research/'` = 92 docs living at repo-root `research/*.md`. The
   new deliverable instead landed at repo-root `foreman/research/VENDOR_KEY_ROTATION.md`
   — physically `/home/lumen/foreman/foreman/research/VENDOR_KEY_ROTATION.md` on disk, one
   directory deeper than every sibling doc. A plain `ls research/VENDOR_KEY_ROTATION.md`
   at repo root fails; any future tooling that globs `research/*.md` will silently miss
   this deliverable.
5. **Root-caused the path bug, independent of wq#1970**: `_runs/wq1970/attempt-2.json`
   shows the authoring worktree was `/home/lumen/worktrees/wq1970-a2` (repo root already
   `.../foreman`), and `evidence.json`'s own AC1 citation is `foreman/research/
   VENDOR_KEY_ROTATION.md:219-244` — the wrong path was written deliberately, matching
   the ticket text's literal `--deliverable foreman/research/VENDOR_KEY_ROTATION.md`
   wording. `orchestrator/router.py:114` encodes the same ambiguity fleet-wide:
   `"research": FOREMAN_ROOT, # research docs land in foreman/research/ (wq#1334)` — a
   comment that reads as cwd-relative but `FOREMAN_ROOT` already **is** `/home/lumen/
   foreman`, so `research/` (no prefix) is correct and `foreman/research/` double-nests.
   This is not unique to wq#1970: `foreman/research/{GLM53_FLASH_EVAL,LPP_BRAIN_AB,
   VLOGS_PURGE_WQ1747}.md` and `foreman/research/brain/pragmatics.md` sit at the same
   wrong depth already (5 misplaced docs total, not a one-off).
6. **Independent escalation** (protocol: contradiction escalates to opus-5@high). Spawned
   a fresh opus-5 agent blind to this report's conclusions, given only the two claims
   above to check from primary sources. It returned **TRUE** on both, with corrections
   folded in above (citation is fabricated-around-a-real-ticket rather than invented
   whole-cloth; placement bug pre-dates and outlives this PR, 5 files not 1; identified
   `router.py:114` as the likely root cause).

## Severity assessment

Not a fabricated-evidence false positive in the Tier-0 instinct-7 sense (no secret values
invented or printed) — this is a citation-integrity defect: one of eight vendor-table
sources points at a file that was never written, in a document whose entire method for
trusting an inferred (not directly read) var-name list is "cited per line below." The
technical content the doc gets right elsewhere (OpenRouter provisioning keys, Cloudflare
token roll, GitHub App installation tokens, DeepSeek/Mistral/Cerebras dashboard-only
status, correctly `VERIFY`-flagged) is unaffected — the defect is narrow but it is exactly
the failure mode an adversarial verify-pass exists to catch, and the file-placement bug
independently makes the deliverable unreachable by convention-following tooling.

## Conclusion

CONTRADICT. The survey's substance is sound and the storage/runbook doctrine is
consistent with existing fleet convention, but the doc ships with one fabricated
file:line citation and lands outside the `research/` directory every sibling doc uses.
Filed wq#1975 (build lane, deps=1970) to fix `VENDOR_KEY_ROTATION.md` in place: correct
or remove the `wq859_renovate_gitea.md` citation, and move the file to
`research/VENDOR_KEY_ROTATION.md`. Filed wq#1976 (build lane, pri 3, no deps) for the
broader pre-existing bug: fix `orchestrator/router.py:114`'s misleading comment/mapping
and relocate the other 4 already-misplaced `foreman/research/*` docs. Did not
re-implement per this ticket's instruction — verify only.
