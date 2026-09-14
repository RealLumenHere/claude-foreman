# wq#1924 — Adversarial verify-pass of wq#1923 (repo-sweep foreman: CONTENT_DIVERGED, PR #588)

**Verdict: CONTRADICT.** The reconciliation mechanics wq#1923 shipped are correct and verified
live, but the code content it landed (the wq#759 evidence-gate retry in `harness/fworker.py`)
has a real, reproducible correctness bug that predates this ticket but ships unexamined inside
it: the retry is applied to every `wq_done()` caller, including one whose notes never cite a
real artifact even though a real artifact was already confirmed to exist — so the fix silently
mislabels a genuinely-landed ticket as "no code artifact."

## What was checked

- **Reconciliation claim** — ran `python3 harness/repo_reconcile.py status --repo
  /home/lumen/foreman` directly against the live repo (not the worktree): `IN_SYNC
  /home/lumen/foreman main ahead=0 behind=0`, `main == origin/main at ae13525`. Matches wq#1923's
  closing notes exactly.
- **PR #588 content** — `git diff origin/main origin/fix/wq1923-repo-sweep-foreman-content-diverged-a1
  -- harness/fworker.py` is empty: the PR branch's tip is byte-identical to what's already on
  `origin/main` (landed as `51574a6`, same tree as the PR's own `a820db2`). The repo-sweep
  divergence is genuinely resolved — confirmed, not re-implemented.
- **The diff itself** (`harness/fworker.py:529-549`, `wq_done()`): on a non-zero `wq done`
  return code, it string-matches the combined stdout+stderr for `"759"` / `"evidence gate"` /
  `"verifiable artifact"` and, on a hit, retries once with `--no-artifact "<generic reason>"`.
  Cross-checked the match strings against the actual refusal text at `/home/lumen/bin/wq:2536-2539`
  (`REFUSED: wq#759 evidence gate — ... or an explicit --no-artifact "<reason>"...`) — all three
  substrings are present verbatim, so the match is well-grounded, and it correctly does **not**
  match the sibling wq#1673/wq#1702 deliverable-gate refusal (exit 7, no "759"/"evidence
  gate"/"verifiable artifact" in its text), so it doesn't paper over that different failure mode.
  `--no-artifact` argument shape (`wq:3187`, `dest="no_artifact"`, one positional REASON) matches
  the call site's `wq("done", ..., "--no-artifact", reason)`.

## The bug — one `wq_done()` call site has no citable artifact even when one exists

`harness/fworker.py` has three `wq_done()` call sites, and the fix's blast radius covers all of
them, not just the fire-mode one it was written for:

- `:2003` (fire-mode auto-close) — notes are prose only when `evidence is None`. This is the
  genuinely artifact-less case the fix was written for; correct.
- `:2756` (`reconcile()`, SUCCESS/DEGRADED_SUCCESS) — notes cite `state_path`, a real file
  `write_attempt_state()` just wrote to disk. `find_artifact()`'s path branch `os.path.exists()`
  finds it; gate passes normally either way. Unaffected.
- `:2737` (`handle_resumable_failure()`) — reached only after `check_artifacts_done(ticket_id,
  worktree)` returned **True** (a real artifact was just confirmed), yet the notes are
  `f"fworker attempt {attempt}: verified via artifacts after {classification} (session
  {state.session_id})"` — no URL, no path, no sha. `state.session_id` is a claude-CLI session
  UUID (dashed), which fails `ARTIFACT_SHA_RE`'s pure-hex `fullmatch`.

Reproduced against the real regexes (`wq:1874,1877`):

```
$ python3 -c "
import re
ARTIFACT_URL_RE = re.compile(r'https?://\S*/(?:pulls?|commits?)/\S+')
ARTIFACT_SHA_RE = re.compile(r'(?=[0-9a-f]{7,40}\$)[0-9a-f]*[a-f][0-9a-f]*')
notes = 'fworker attempt 2: verified via artifacts after DEGRADED_SUCCESS (session abc12345-6789-def0-1234-56789abcdef0)'
print('url match:', ARTIFACT_URL_RE.search(notes))
print('path-shaped tokens:', [t for t in notes.split() if '/' in t])
"
url match: None
path-shaped tokens: []
```

No url, sha, or path token — `find_artifact()` returns `ok=False`, `wq done` is refused (exit 2)
with the exact `wq#759 evidence gate` / `verifiable artifact` text, and the new retry logic
matches it and closes the ticket via `--no-artifact "fire-mode/operational closure - no code
artifact; action recorded in --notes"`.

That reason string is **false** for this call site: `check_artifacts_done()` already confirmed a
real artifact two lines above. Before this fix, that same refusal raised `RuntimeError` — a loud,
investigable failure. After this fix, it's a silent, successful `done` carrying a fabricated "no
code artifact" label on a ticket that did land code. That's a direct hit on the thing wq#759
itself exists to protect: `wq`'s own `cmd_...` reporting at `wq:3010` buckets closures into
`verified` / `no-artifact` / `unverified` specifically to track "is that trend improving" — this
path moves a `verified` closure into the `no-artifact` bucket every time it fires, for reasons
the code has no way to tell apart from a genuine no-artifact close.

`handle_resumable_failure` is live, reachable code (`fworker.py:2769`), not dead — it's the
FALSE_COMPLETE/INDETERMINATE/HANG/AUTH re-verdict path documented in RELIABILITY_SPEC.md §C7/C8/
C10/C11, not a rare corner.

## Disposition

wq#1923's own deliverable — reconciling the repo — is done and independently confirmed
(`repo_reconcile.py status` → `IN_SYNC`, byte-identical trees). But "verify the diff shipped by
wq#1923" includes the code that diff put on `main`, and that code has a real, reproducible
correctness gap the author's ticket notes ("VERIFIED live: crashes 0 after landing") never
exercised: it only tested the fire-mode call site, not the other two `wq_done()` callers already
in the file. Per the ticket's own instruction, a contradiction escalates to opus-5@high — filing
as a judgment trigger rather than re-implementing a fix here.

**Suggested fix shape** (not applied — verify-pass scope): at `fworker.py:2737`, cite what
`check_artifacts_done()` already found (a path/URL/sha) in the notes instead of prose, the same
way `:2756` already does with `state_path` — or narrow the retry's match condition so it only
fires when the caller can positively assert artifact-lessness (mirroring `:2003`'s `evidence is
None` check) rather than on message-text pattern alone.
