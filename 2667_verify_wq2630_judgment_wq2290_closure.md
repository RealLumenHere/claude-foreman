# wq#2667 — verify of wq#2630 (PR#1027, merged 6e497db, judgment on wq#2355's CONTRADICT of wq#2290)

Verifier: claude-sonnet-5@high, fresh instance. Author: claude-opus-5. Verdict:
**CONFIRM** — no contradiction, no escalation.

wq#2630 is an opus-5@high judgment ticket ruling on wq#2355's CONTRADICT of wq#2290's
`done` closure (burn_filter's provenance screen only ever passed `cwd` as `repo_root`,
never a granted `--add-dir` root). Its diff (`harness/prompts/worker.md` +9,
`reports/2630_judgment_wq2290_closure.md` +209) makes four kinds of claims: (a) code/test
facts re-checked against current HEAD, (b) a live reproduction purporting to show
wq#2355's own headline evidence does not reproduce, (c) two corrective `wq note`s actually
appended without reopening either ticket, (d) a doctrine rule landed verbatim at
`harness/prompts/worker.md:25-34`. I read the report cold, then independently re-derived
every checkable claim rather than trusting its citations. Did not re-implement.

## Per claim

| wq#2630 claim | Result | Evidence |
|---|---|---|
| PR#814 as merged screens provenance against `cwd` only | CONFIRMED | `git show 281327c:harness/burn_filter.py` — `classify`/`is_committed_literal` take a bare `repo_root` string end to end, no plural/list handling. |
| The gap is already closed on current HEAD (`provenance_roots`, multi-root) | CONFIRMED | `harness/burn_filter.py:137-146,179-222` (`_as_roots`, `provenance_roots`, `is_committed_literal` over `repo_roots`); `harness/fworker.py:2776,2935` both call `burn_filter.provenance_roots(cwd, granted_dirs(cmd))`. Line numbers drifted by ~7 from the report's citation (unrelated commits landed since) but the shape is identical. |
| Regression suite passes on HEAD | CONFIRMED (count drift, immaterial) | `python3 -m pytest harness/tests/test_burn_filter.py -q` → **41 passed** today vs. the report's 40 — one more test (`test_the_recheck_does_not_reuse_this_processs_provenance_answers` and siblings) was added by an unrelated later commit; not a regression. |
| wq#2290's done-notes contain the quoted overclaim sentence verbatim | CONFIRMED | `wq show 2290` notes: *"Probe on the merged tree: 35 tracked files page before, 0 after, live token still pages."* — exact match. |
| Timeline (wq#2319 created 22:36:38Z / PR#814 merged 23:09:47Z / wq#2290 done 23:10:27Z / wq#2319 claimed 23:29:19Z / done 23:44:31Z) | CONFIRMED | `wq show 2319` (`created_at`/`claimed_at`/`done_at`), `wq show 2290` (`done_at`), `git show -s --format=%cI 281327c` — every timestamp matches to the second. |
| wq#2355's cited fixture (`TOKEN="abcdefgh12345678` at `ISSUE-LOG.md:831`) never existed in any committed revision | CONFIRMED | `grep -c` for the literal in the current working tree → `0`; `git -C /home/lumen/kb show 0df6e21:ISSUE-LOG.md \| grep -c` → `0`. |
| Current `harness/masking.py` scores zero labels on today's real `ISSUE-LOG.md` | CONFIRMED | Ran `masking.mask_text()` (current HEAD) over `/home/lumen/kb/ISSUE-LOG.md` working tree → `labels: []`. |
| `verify_pass.escalate_on_contradiction: opus-5@high` is the routing instrument that fired here | CONFIRMED | `research/models.yaml:429-437` — `verify_pass: {tuple: sonnet-5@high, escalate_on_contradiction: opus-5@high, required_for: [rca-investigation, standard-engineering], ...}`. |
| Corrective `wq note`s were actually appended to both wq#2290 and wq#2355, neither reopened | CONFIRMED | `wq show 2290`/`wq show 2355` both carry the `[2026-09-25 09:24:4x] wq#2630 opus-5@high judgment ...` notes verbatim as quoted in the report, and both tickets are still `status: done`. |
| The doctrine rule landed exactly where claimed | CONFIRMED | `git show 6e497db -- harness/prompts/worker.md` — the "AN ACCEPTANCE PROBE MUST BE ABLE TO FAIL" paragraph is live at lines 27-35, word-for-word what the report quotes, and (self-referentially) is the same text this verify-pass's own dispatch prompt was generated from. |
| wq#2355's own live reproduction does not reproduce, and the file it pinned to is unstable | PROBED, NOT CONTRADICTED — see below | Independent re-run today (a third point in time, after both the wq#2355 and wq#2630 reports) got a **third different** result set, which corroborates rather than undermines the report's point. |

## Running the probe myself

Extracted PR#814-vintage `masking.py`+`burn_filter.py` (`git show 281327c:...`) into an
isolated module pair and ran `classify(matched_values(text), set(), repo_root)` against
today's real `/home/lumen/kb/ISSUE-LOG.md` for both `repo_root=/home/lumen/foreman` and
`repo_root=/home/lumen/kb`:

```
today's working tree:  hits {cloudflare:1, sk-key:1, kv-secret:1}   # kv-secret line 689: "PAT: github_pat_MASKED"
  repo_root=/home/lumen/foreman -> burned []  benign [cloudflare, sk-key, kv-secret]
  repo_root=/home/lumen/kb      -> burned []  benign [cloudflare, sk-key, kv-secret]
```

This is neither wq#2355's result (`kv-secret` BURNED under `cwd=foreman`) nor wq#2630's
own re-run (`kv-secret` absent entirely, `cloudflare` burned under `kb`). `git -C
/home/lumen/kb status --porcelain` confirms `ISSUE-LOG.md` is dirty right now (` M
ISSUE-LOG.md`), same as when wq#2630 ran its own re-check. Three runs, three different
outcomes, off the same unpinned file, over three points in time — this is the exact
failure mode wq#2630's report names (an unpinned probe against a live mutable file in
another repo is not re-runnable evidence), demonstrated again rather than refuted. It does
not touch the report's actual load-bearing evidence, which is the pinned, rev-frozen
wq#2288 transcript replay behind ISSUE-LOG row 594 / wq#2319 — re-confirmed above via the
untouched timeline and the current, closed `provenance_roots` code path.

## Verdict

CONFIRM. Every checkable factual claim in `reports/2630_judgment_wq2290_closure.md`
reproduces exactly (timestamps to the second, quoted strings verbatim, code line shapes
identical modulo unrelated drift, corrective notes present, neither ticket reopened, rule
landed as quoted). The one claim that is explicitly a live re-run against a mutable file
does not reproduce identically — expected and disclosed by the report itself — and my own
independent re-run produced a third distinct result, which is further evidence for the
report's "unpinned probe" diagnosis, not against it. No re-implementation performed, per
verify-pass doctrine.
