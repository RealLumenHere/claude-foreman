# wq#1860 — Adversarial verify-pass of wq#1810 (despag P1-5: workspace = git worktree per session, checkpoint tar, mtime TTL, refs/lumen/collected cursor, D.1 workspace_snapshot frame)

**Verdict: CONTRADICT** — `despag/workspace.py` (commit `6a133da`, PR #534) is well
built and its own test suite is honest (10/10 reproduced live). But the checkpoint
exclude list and the HOME-redirect design it ships in the same file are mutually
contradictory: `CHECKPOINT_EXCLUDE` excludes `.claude` at any depth, and
`harness_home()` puts the CLI's transcripts at `<ws>/.harness/home/.claude/projects/`
— itself a `.claude` directory, at depth. Every checkpoint silently strips the exact
transcripts the module's own docstrings say survive it, defeating `--resume` and
`resume_lost()` (E.8) for any session that has ever been checkpointed and rehydrated.
This is not a hypothetical edge case; it is `wq#1810`'s own acceptance text taken
literally: "excluding every CLI credential file (`.claude/` ... at any depth ...)"
**and** "HOME redirected to `<ws>/.harness/home` so `~/.claude/projects` transcripts
travel with the checkpoint" cannot both be true of the same implementation, and the
shipped one picked the former while documenting the latter as satisfied.

## What was shipped

`despag/workspace.py` (563 lines, new): session identity (`sanitize_sid`/
`workspace_path`/`ensure_workspace` — a session's workspace is `git worktree add` of
the harness's declared repo, or a shared `scratch-repo` for the conformance
harnesses); TTL reap (`reap_stale`, mtime-driven, skips live-leased dirs, wired at
session-create time and via `python3 -m despag reap`); checkpoint/rehydrate (tar with
`CHECKPOINT_EXCLUDE`, spool-to-disk, sha256, `BlobStore`, probe short-circuit);
collection cursor `refs/lumen/collected` (advanced only by `advance_cursor()`, never
by `checkpoint()`'s own commit — E.4); D.1 `workspace_snapshot` (`build_snapshot`/
`capture_and_store_snapshot`/`previous_snapshot`, 64KB-capped, largest-diff-hunk-first
drop order). `despag/backing/control_sqlite.py` adds `lease_is_live` (fail-safe
`True` on a store error). `despag/harnesses.yaml` adds a `repo:` field per harness
(`scratch` for all three current harnesses). `despag/__main__.py` adds the `reap`
CLI subcommand. `despag/tests/test_workspace.py`: 10 tests.

## Reproduced live

- `python3 -m pytest despag/tests/test_workspace.py -q` → **10 passed** (this worktree,
  no changes).
- `despag/reports/p1-5_workspace.json` (committed by this PR, generated against a live
  `despag` instance) shows the actual session-continuation conformance checks C-01/
  C-02/C-03 as **skipped**, not passed (`"conformant": false`, `"the first task did
  not return an id and session_id"`, `HTTP 501`) — `despag/app.py`'s task-creation
  endpoint isn't wired to sessions yet, so this PR's own primitives are unit-tested in
  isolation only; nothing in the shipped evidence exercises checkpoint→rehydrate→
  `--resume` end to end. That gap is why the exclude-list defect below has no test or
  conformance check to catch it.

## The contradiction, reproduced with the actual module (not a mock)

```
$ python3 /tmp/wq1860check2/repro_transcript_loss.py     # despag.workspace, real
                                                            # ensure_workspace/checkpoint,
                                                            # tmp DESPAG_DATA_DIR/
                                                            # DESPAG_WORKTREES_ROOT
BEFORE checkpoint: transcript exists -> True
resume_lost BEFORE checkpoint: False
checkpoint result: {'sha256': '842d64d5...', 'bytes': 415, 'head': '0d67ad9'}
tarball contains .harness entries: ['./.harness/', './.harness/home/']
tarball contains transcript file: False
AFTER restore-from-checkpoint: transcript exists -> False
resume_lost AFTER restore-from-checkpoint: True
```

Script: created a workspace via `ensure_workspace`, wrote a transcript at
`harness_home(sid)/.claude/projects/resp-abc123.jsonl` (the exact path
`resume_lost()` reads, `workspace.py:274`), ran the real `checkpoint()`, inspected the
resulting tarball (`tar tzf`) — it contains the empty `.harness/home/` directory but
not the transcript inside it — then wiped the workspace (keeping `.git`, mirroring
what `rehydrate()` does) and untarred the checkpoint back in place. The transcript is
gone and `resume_lost()` — the function whose entire purpose (E.8) is to detect this
failure mode — reports `True`: a `--resume` after any checkpoint/rehydrate cycle
silently starts a fresh conversation instead of continuing.

Isolated to the tar layer, independent of despag, to confirm this isn't a spooling or
BlobStore artifact: GNU tar's `--exclude=PATTERN` (no `/` in the pattern) matches the
pattern against every path component at any depth, not just the top level, so
`--exclude=.claude` matches `./.harness/home/.claude` exactly as it matches a
top-level `./.claude`. `CHECKPOINT_EXCLUDE` (`despag/workspace.py:288-291`) is built
from bare component names for every excluded name, so this applies identically to
`.claude`, `.credentials.json`, `.env`, `tmp`, `node_modules`, `.venv`, and
`__pycache__` — any of those names anywhere under the workspace vanishes from the
checkpoint, not just at the workspace root.

## Why the tests didn't catch it

`test_checkpoint_excludes_credentials` (`despag/tests/test_workspace.py:91-112`)
creates `.claude/` directly under the workspace root and asserts it's excluded —
correct for that case, and it is the only test that touches `.claude` exclusion at
all. No test creates a file under `harness_home(sid)/.claude/projects/` (the path
`harness_home()`'s own docstring, lines 262-265, and `resume_lost()`, lines 268-284,
both exist to protect) and checks it survives a checkpoint/rehydrate round trip. The
gap is exactly the boundary between "exclude credentials" and "preserve the one thing
that happens to be named `.claude` but isn't a credential."

## Adversarial angles tried, nothing found

- `ensure_workspace`'s SessionExpired path, `reap_stale`'s live-lease skip and
  mtime-only-not-tocttou-safe-but-acceptable reap, `advance_cursor`/`checkpoint`
  cursor independence (E.4), `previous_snapshot`'s exactly-one-frame feed-forward, and
  `build_snapshot`'s 64KB shrink order — all read line-by-line against
  `PHASE0_WIRING_SPEC.md` and the HR reference this PR cites; all match, and all have
  a passing test that isn't circular.
- `remove_workspace` doesn't delete the `despag/<sid>` branch left behind in the
  declared repo after `git worktree remove` — a slow leak in the shared scratch repo's
  branch list, not a correctness bug (a reaped sid can never legitimately recreate a
  workspace — `was_reaped` markers are permanent), left unfiled as noise relative to
  the main finding.
- `ensure_workspace`'s `-b branch HEAD` / retry-without-`-b` path for "branch already
  exists" — correct for the one case it's built for (a workspace directory removed
  out from under a still-registered branch); didn't find a path where the retry masks
  a different failure.

## Bottom line

CONTRADICT. Five of the module's six documented pieces are correct and covered.
The sixth — checkpoint/rehydrate preserving `.harness/home` transcripts for
`--resume` — is false as shipped, demonstrated live against the real module, and
follows directly from the ticket's own two acceptance clauses being unsatisfiable
together as written ("`.claude/` ... at any depth" vs. "`~/.claude/projects`
transcripts travel with the checkpoint" when the redirect target is itself named
`.claude`). Filed `wq#1861` (`claude-opus-5@high`, deps `wq#1810`) to fix — likely an
anchored exclude (`--exclude=./.claude` at the workspace root only, or excluding
`.harness/home/.claude/config*`/credentials specifically rather than the whole
`.claude` subtree) plus a round-trip test asserting a file under
`harness_home(sid)/.claude/projects/` survives `checkpoint()`+extract. Not
re-implemented here per this archetype's instruction.

## ISSUE-LOG

Appended as issue entry (see `/home/lumen/kb/ISSUE-LOG.md`): CHECKPOINT_EXCLUDE's
unanchored `.claude` tar exclude also strips `.harness/home/.claude` transcripts;
solution deferred to wq#1861.
