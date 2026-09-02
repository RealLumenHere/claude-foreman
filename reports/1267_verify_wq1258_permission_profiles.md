# wq#1267 — Adversarial verify-pass of wq#1258 (per-repo toolchain permission profiles)

**Verdict: CONTRADICT** — `render_permissions()` builds `permissions.deny` purely from the
union of a repo's *declared* profile fragments, with no floor and no validation that `base`
(the only fragment carrying real deny rules) is one of them. A `repos.d/<repo>.json` that
omits `"base"` from `profiles` silently renders a merged settings pack with an **empty deny
list** — reproduced empirically below — dropping protections (`Bash(env)`, `Bash(printenv*)`,
`.env`/`.secrets` reads) that CLAUDE.md's own Tier-0 "MASK BEFORE PRINT" instinct depends on,
and that `guard.sh` does not independently re-implement.

## What was shipped (PR#195, commits `2a43753`/`1551da5`, merged to `origin/main`)

`harness/permission_profiles.py` + 4 shared fragments (`base.json`/`python.json`/
`node-ts.json`/`shell.json`) + `repos.d/{foreman,pokemon,mailbot}.json` + wiring into
`fworker.resolve_worker_settings()` (called from both `do_attempt()` and `run_fire_child()`
in place of the old bare `SETTINGS_FILE`). 13 new tests in `test_permission_profiles.py`.
Read cold via `git show origin/main:<path>` (my own worktree branched before the merge and
lacks these files; `/home/lumen/foreman`, the live primary tree, is also still 12 commits
behind `origin/main` and has never seen this code — noted below as informational, not part
of the verdict).

## Probe: reconstructed the shipped tree and ran it

`git archive origin/main harness | python3 -c "tarfile...extractall(...)"` into a scratch dir
(no `tar` on the allowlist), then:

```
$ python3 -m pytest harness/tests/test_permission_profiles.py -v
13 passed
$ python3 -m pytest harness/tests/ -q   (excluding 2 files with deps outside harness/)
376 passed, 6 failed
```

All 6 failures (`test_memory_injection.py`, `test_instincts_render.py`,
`test_local_rung.py`) are `ModuleNotFoundError`/missing-fixture errors from
`harness/../memory/` — a sibling directory I didn't archive (`git archive origin/main
harness` only pulls the `harness/` subtree). Confirmed by reading `fworker.py:97`
(`MEMORY_DIR = .../harness/../memory`) — an artifact of my reconstruction, not a real
regression. The 13 tests actually shipped by this diff all pass.

## The contradiction

`permission_profiles.py:58-76`:

```python
allow, deny = [], []
seen_allow, seen_deny = set(), set()
for name in profile_names:
    prof = _load_json(os.path.join(PROFILES_DIR, f"{name}.json"))
    _dedup_extend(allow, seen_allow, prof.get("allow", []))
    _dedup_extend(deny, seen_deny, prof.get("deny", []))
...
merged["permissions"]["allow"] = allow
merged["permissions"]["deny"] = deny          # replaces base_settings' deny entirely
```

Only `base.json` carries a `deny` key (25 rules: reboot/shutdown/systemctl
stop|disable|mask/dd/docker --remove-orphans/ip link set/macchanger/git push
--force/rm -rf //docker/sudo/`Bash(env)`/`Bash(env |*)`/`Bash(printenv*)`/`Read(**/.env)`/
`Read(**/.credentials.json)`/`Read(/home/lumen/.secrets/**)`/etc. — the exact list CLAUDE.md
attributes to "A PreToolUse guard hook"). `python.json`/`node-ts.json`/`shell.json` have no
`deny` key at all. Nothing in `render_permissions()` requires `"base"` to be present in a
repo's `profiles` list — it's pure convention that the three shipped `repos.d/*.json` all
include it.

Reproduced directly against the shipped module:

```
$ python3 probe.py
static deny count: 25
merged deny count for profiles=["python"] only: 0
merged deny contents: []
```

A `repos.d/<repo>.json` written as `{"profiles": ["python"]}` (a natural thing for someone
onboarding a python-only repo to write, mirroring the shape the shipped `foreman.json`
almost has — foreman's is `["base","python","shell"]`) renders a settings pack with **zero**
deny rules.

`harness/guard.sh` is documented (guard.sh:4-6) as "the wall behind the settings-pack deny
rules" and does independently re-implement most of the same denials in its own bash regex
(reboot/shutdown/sudo, systemctl stop/disable/mask, `dd ... of=`, `--remove-orphans`, `ip
link set`, `macchanger`, `git push --force`, `rm -rf` outside worktree/tmp, `docker` except
ps/logs, `.secrets` path, `claude setup-token`) — so those stay blocked even with an empty
JSON deny list. But grepped the full 304-line file: **no check for bare `env`, `env |...`,
or `printenv`** anywhere in it. Those three denials exist *only* in the JSON deny list. With
an empty deny list, a worker dispatched against a `base`-omitting repo could run `env` or
`printenv` and get the full process environment — including `ANTHROPIC_AUTH_TOKEN`
(`fworker.build_env()`) — straight into its own transcript, which is exactly the scenario
CLAUDE.md's Tier-0 instinct #3 names by example ("never run a command that can emit a
credential (... env ...) without piping through a mask first").

Not a live incident: all 3 shipped configs (`foreman`, `pokemon`, `mailbot`) include
`"base"`, so nothing dispatched today is exposed. It's a latent gap for the next repo
onboarded. Telling: the suite's own `test_extra_allow_included_and_deduped_against_profile_rules`
fixture (`test_permission_profiles.py:104`) *already* constructs `{"profiles": ["python"],
...}` — the exact vulnerable shape — merges it, and only asserts on `allow`. One more
assertion on `deny` in that same test would have caught this before it shipped.

## Other checks — no issues found

- `resolve_settings_file()`'s content-addressed write (`out_path` keyed by
  `render_hash()[:12]`, `os.replace` from a `.tmp`) is race-safe per ticket: `run_dir(id)`
  is `_runs/wq<ID>/`, one per ticket, so concurrent tickets never share an output path.
  Idempotency test (`test_resolve_settings_file_writes_once_and_is_idempotent`) reproduced.
- `repo_name()`/verb-rendering/dedup-against-`extra_allow` all check out against direct
  reading and their tests, reproduced independently.
- wq1258's own scoping note ("wq-row hash stamp deferred to §2c, no wq schema field yet") is
  accurate — grepped `/home/lumen/bin/wq`'s `ALTER TABLE` migrations; no drift/hash/digest
  column exists anywhere in the tasks table.
- `/home/lumen/foreman` (primary tree, live dispatcher target per
  `lumen-job-fworker-wq1259-*.service`'s `ExecStart`) is still on `44160ef`, 12 commits
  behind `origin/main`, and has no `permission_profiles.py` on disk at all — the feature
  isn't active for anything currently dispatching. This appears to be normal `sync-live`
  cadence (primary trees only fast-forward via `lumen-pr land`'s best-effort `merge
  --ff-only`, which no-ops on a dirty tree — `/home/lumen/foreman` currently has a modified
  `dispatcher/health_state.json` — or via an explicit `sync-live` run), not a defect
  introduced by this diff. Flagged as informational, not part of the verdict.

## Disposition

Do not treat as fully landed. Minimal fix: either (a) always union `base.json`'s `deny`
unconditionally regardless of declared `profiles` (deny rules aren't toolchain-specific, so
there's no reason they should be opt-in the way `allow` fragments are), or (b) validate at
render time that the merged `deny` is non-empty (or that `"base"` is present) and refuse to
render otherwise. Either closes the gap without touching the three repos already migrated.
