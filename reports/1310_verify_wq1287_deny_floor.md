# wq#1310 — Adversarial verify-pass of wq#1287 (PR#220: floor deny rules unconditionally)

**Verdict: CONFIRM** — the diff correctly and minimally closes the wq#1267 CONTRADICT
(`repos.d/<repo>.json` omitting `"base"` silently dropped `base.json`'s `deny` list). No
regression found against any of the 3 shipped repo configs; full harness suite green.

## What was shipped (PR#220, commit `558372e`, branch `pr220-verify` fetched via
`refs/pull/220/head`)

`harness/permission_profiles.py`: `render_permissions()` now unconditionally loads
`base.json` and unions its `deny` list into `deny`/`seen_deny` *before* iterating the
repo's declared `profiles`, so a config like `{"profiles": ["python"]}` (no `"base"`) can
no longer render an empty deny list. `harness/tests/test_permission_profiles.py`: added
`test_deny_floor_holds_even_when_repo_config_omits_base_profile`, reproducing the exact
shape from the wq#1267 report and asserting `Bash(*reboot*)`/`env`/`.env` denies survive.
+31 lines, 0 deletions, 2 files — no re-implementation, matches the "minimal fix option
(a)" the wq#1267 report recommended.

## Probe

Fetched `refs/pull/220/head` into this worktree, checked out both changed files cold, ran:

```
$ python3 -m pytest harness/tests/test_permission_profiles.py -v   # 14/14 passed
$ python3 -m pytest harness/tests/ -q                               # 400/400 passed
```

Read the full `render_permissions()` body (not just the diff hunk) to check the loop
after the new unconditional union: if a repo's `profiles` *does* include `"base"`,
`base.json` gets loaded twice, but `_dedup_extend` seeds `seen_deny` from the first
(unconditional) load, so the second pass is a no-op — verified idempotent, no double
entries, by inspection and by the pre-existing
`test_merged_pack_keeps_deny_list_and_hooks_from_static_settings` still passing unchanged.

## Adversarial checks that did not break it

- **Allow/deny overlap regression**: checked whether any shipped toolchain fragment
  (`node-ts.json`, `python.json`, `shell.json`) declares an `allow` rule that the newly-
  floored `base.json` `deny` would now shadow for a repo that previously omitted `"base"`
  to deliberately unlock something denied at base (e.g. `docker`/`env`). None of the three
  define their own `deny`, and none of their `allow` entries overlap base's deny patterns
  (`docker *`, `env`, `printenv*`, `sudo *`, etc.) — no repo currently depends on the old
  (buggy) opt-out behavior. Reproduced via `python3 -c` cross-check, not just eyeballing.
- **Empty/omitted `profiles`**: `repo_conf.get("profiles") or ["base"]` — an explicit
  `{"profiles": []}` falls back to `["base"]` same as a missing key, doesn't bypass the
  floor a second way.
- **`merged["permissions"]["deny"]` assignment**: confirmed the tail of the function still
  assigns the fully-unioned `deny` list (not just base's) to the merged settings pack —
  the two-line fix composes with, not replaces, the per-profile deny union.

## One informational note, not part of the verdict

Main (`origin/main` as checked out in this worktree, commit `3634dec`) already carries a
byte-identical diff to PR#220's `558372e` (`git diff 3634dec pr220-verify -- <2 files>` is
empty), same commit message, `Refs: wq#1287`. After checking out PR#220's two files into
this worktree, `git status --short` is clean against the branch's existing tree — the fix
is already shipped on `main` under a different commit hash (consistent with a squash-land),
so PR#220 is very likely mergeable/closeable as already-landed rather than still pending.
This doesn't change the verdict: the code that shipped is correct.

## Verdict

**CONFIRM.** No contradiction found; escalation to opus-5@high not triggered.
