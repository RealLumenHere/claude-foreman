# wq#2753 — route BUILD tickets to the repo they say they land in; let evidence cite a landed cross-repo commit

ISSUE-LOG #728 (4th instance of the class: #33 questfree, #125 nest, #192/wq#1446
lumen-bin, now wq#2624). wq#2624's detail said "Lands via lumen-pr against
/home/lumen/bin." but was dispatched into a foreman worktree, so the evidence gate
(`acceptance.py:_check_citation`) could never pass — it only accepts a citation that
appears in the *local* worktree's diff vs origin/main.

## Why the existing resolver missed it

`router.repo_for_ticket()` already tries, in order: `needs:repo(...)` tag,
`deliverable_repo_hint` (the `deliverable` column or a `Deliverable: /abs/path` line),
`verify_repo_hint`, `LANE_REPO`, `repo_sweep.repo_hint`. wq#2624 had none of those:
lane `auto`, no needs tag, no `Deliverable:` line — only the prose sentence
"Lands via lumen-pr against /home/lumen/bin." Nothing parses that phrasing, even though
it is the natural way tickets (including wq#2753 itself: "Land via lumen-pr on foreman")
state their landing repo.

## Fix (a) — primary: `landing_repo_hint` in the shared resolver

New `router.landing_repo_hint(ticket)`: parse
`land(s|ing)? via lumen-pr (against|on|to|in) <target>` (case-insensitive) from the
ticket detail.

- `<target>` an absolute path → accepted iff it is, or sits inside, a
  `repo_sweep.managed_repos()` member (longest-prefix match, same trust rule as
  `deliverable_repo_hint` — a stray path can never point dispatch at an arbitrary tree).
- `<target>` a bare name (`foreman`, `bin`) → accepted iff it matches the basename of
  exactly one managed repo; ambiguity or no match → None, never a guess.

Wired into `repo_for_ticket` after `deliverable_repo_hint`, before `verify_repo_hint`/
`LANE_REPO`: per-ticket explicit data beats the lane default; the needs tag and the
deliverable column stay ahead as the more structured signals. Kill switch
`FOREMAN_LANDING_REPO_DISABLE=1`, mirroring `FOREMAN_DELIVERABLE_REPO_DISABLE`.

The change lands in `orchestrator/router.py`, not `dispatcher/foreman_dispatcher.py`
as the ticket guessed: the dispatcher consumes the shared resolver
(`dispatcher/gates.py:172 → router.repo_for_ticket`), so fixing the resolver fixes the
dispatch path, `dispatch_cmd`, and judgment's verify-ticket repo pinning in one place.

## Fix (a2) — the sweep hint must outrank the lane default

Found while running the router suite: 1eb5c48 (wq#2103) added `"auto": FOREMAN_ROOT`
to LANE_REPO, and because `repo_for_ticket` tried LANE_REPO *before*
`repo_sweep.repo_hint`, that one line silently rerouted every `[repo-sweep]` auto-lane
ticket to foreman regardless of the repo the sweep wrote into its own detail — the
same misroute class, and it left 4 router tests failing on main
(`test_repo_for_ticket_falls_back_to_repo_hint` et al.). Fixed by moving
`repo_sweep.repo_hint` ahead of `repo_for_lane` (per-ticket data beats the lane
default, same principle as every other hint) and updating the two tests that asserted
the pre-1eb5c48 "auto has no repo" behavior.

## Fix (b) — fallback: rev-pinned cross-repo citations in the evidence gate

Routing can still legitimately produce cross-repo evidence (wq#2624 itself landed by
hand as lumen-bin `730c972` while the gate ran in a foreman worktree). So
`_check_citation` accepts two new optional fields on a citation entry:

```json
{"id": "AC1", "citation": "lumen-run:117-134",
 "repo": "/home/lumen/bin", "rev": "730c972..."}
```

When `repo` is present the check runs entirely against that repo instead of the local
diff, and every leg can fail:

1. `repo` must be a `managed_repos()` member (fail closed if the list is unreadable);
2. `rev` must be a commit sha AND an ancestor of that repo's `origin/main`/`main` —
   the cited change has *landed*, not merely exists on some branch, and the evidence is
   pinned to an immutable rev (re-runnable, per wq#2630);
3. the cited path must be touched by `rev`'s own diff (`git diff-tree`), mirroring the
   local "in this branch's diff" rule;
4. the cited line range must exist in the file *as committed at `rev`* (`git show`),
   never the live working tree.

## Not done (and why)

- Broker/GitHub-API PR-diff lookup: every managed repo is local; a rev-pinned local
  `git` check proves the same thing with no network, no token, and no new dependency.
- A `repo_mismatch`-style refusal for landing-phrase/lane disagreement: unnecessary —
  the hint simply outranks the lane, so the ticket routes correctly instead of refusing.
