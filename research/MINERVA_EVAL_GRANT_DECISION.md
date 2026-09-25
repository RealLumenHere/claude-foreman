# MINERVA_EVAL_GRANT_DECISION — narrow read grant for /home/lumen/minerva/eval

Author: orch-despag4 (de-spaghettifier orchestrator), 2026-09-13 03:40Z
Scope: research + decision only. No policy edit made. Unblocks wq#1738 → wq#1614.

## 0. Verdict

**GO on the narrow grant** `/home/lumen/minerva/eval` — credential-free (fleet sweep 0/508),
prefix-bounded (cannot reach `/home/lumen/minerva`, `context/`, `sections.json`), and the
exact shape the policy already uses for `/home/lumen/hapi/eval` and `/home/lumen/questfree/docs`.

**But the grant alone does not unblock wq#1614.** That ticket needs three artifacts and only
one of them is in `eval/`. §5 gives the two companion moves (a code-only mirror for
`lumen_recall2.py`, a fixture export for the `minerva.db` rows) so the whole chain lands.

**wq#1738 must not be dispatched to a worker at all.** Its body cites `/home/lumen/minerva`
(the parent), so it stays `needs_unresolved` even after the grant; and editing
`fleet-exec-policy.yaml` is orchestrator work by design. The grant PR closes wq#1738 directly.

## 1. What is in /home/lumen/minerva/eval

- 519 files, 11 MB, 378 subdirs (mostly 12-hex proposal/run ids), no symlinks.
  Types: 470 `.json`, 23 `.py`, 11 `.pyc`, 3 `.md`, 2 `.jsonl`, plus timestamped `.bak` copies
  of `recall_golden.jsonl` (`*.20260814_pre_wq572`, `*.20260814_pre_wq595`, `*.20260815_pre574reseed`).
- Content: recall/salience golden sets and bench scripts —
  `eval/golden_v4_frozen_20260814.jsonl` (the frozen set wq#1614 evaluates against),
  `eval/recall_golden.jsonl`, `eval/recall_bench.py`, `eval/bench_ollama.py`,
  `eval/wq466|wq468|wq572|wq573|wq574/` feature-dump and scoring scripts.

**Credential check — clean.**
- Fleet-standard sweep (the one the policy requires): `python3 harness/read_dir_sweep.py
  /home/lumen/minerva/eval` → `files=508 hits=0` (2026-09-13 03:33Z). Same tool and same
  regex/basename set that gates every existing root (`fleet-exec-policy.yaml:63-65`).
- The raw `grep -rlE "token|secret|api.?key|password|BEGIN.*PRIVATE"` hits are all lexical:
  - tokenizer code: `eval/wq468/dump_features.py:43,69,145`, `eval/wq574/dump_features.py:53,129`,
    `eval/wq466/test_tag_entry.py:36` (`lr._tokens(query)`).
  - golden-set *queries about* secrets: `eval/wq573/tier_controls.py:27-28`
    (`'where is the nordlynx token stored'`, expected substr `nordlynx_token`),
    `eval/wq572/build_golden_v4.py:39,63,80` (expected substrings such as
    `github-frank.token`, `/home/lumen/secrets`). These are file *names* and note ids the
    recall system must find — never values. Zero `sk-`, `ghp_`, PEM, or `KEY=` shapes.
- No `.env`, `.token`, `.key`, `.pem`, `id_*` basenames (would trip
  `capability_lint.DIR_DENY_BASENAME_GLOBS`, `harness/capability_lint.py:54`).

## 2. What is blocked and why it needs the tree

- **wq#1614** `[minerva] contradiction penalty in search_v3, evaluated on the frozen golden set`
  — status `blocked`. Needs (its own block_reason): (a) `minerva/lumen_recall2.py::search_v3`
  to patch, (b) `minerva/eval/golden_v4_frozen_20260814.jsonl`
  (sha256 `8ba5afb0…bf46b7`) to measure `trap_at1_rate` (any rise disqualifies, wq#595),
  (c) `minerva.db` `note_contradictions` rows written by wq#1610.
- **wq#1738** `[harness] narrow read grant …` — the unblock ticket, `open`, never dispatched:
  every pass ends `needs_unresolved — dir(/home/lumen/minerva/eval) … dir(/home/lumen/minerva)
  is not under allow.read_dirs` (dispatcher log, screenshot 2026-09-13). Its body cites the
  parent, so no grant of `eval/` alone will ever let it spawn; and a worker may not edit the
  policy anyway (`fleet-exec-policy.yaml:57-60`, propose-by-PR only).
- No other open ticket cites `minerva/eval` (queue scan 2026-09-13).
- Why the sandbox, not the allowlist, refuses: `capability.preflight` flags cited paths outside
  worktree ∪ add_dirs; `/home/lumen/minerva` is not a `never:` entry but is deliberately absent
  from `allow.read_dirs` (`fleet-exec-policy.yaml:66-68`, wq#1467: credentials mirrored into
  `minerva/context/sections`). Absent-from-allow ⇒ `needs_unresolved` ⇒ pre-skip.

## 3. Is a narrow grant safe — option analysis

**(a) Add only `/home/lumen/minerva/eval` to `allow.read_dirs` — RECOMMENDED.**
- Root matching is path-component bounded, not string-prefix:
  `harness/capability.py:118-120` `_under(path, root)` → `path == root or
  path.startswith(root + os.sep)` after `normpath`. `allowed_read_dir()`
  (`capability_lint.py:290-297`) iterates roots with the same `_under`. So
  `/home/lumen/minerva/eval` matches itself and descendants only; `/home/lumen/minerva`,
  `/home/lumen/minerva/context/...`, `/home/lumen/minerva/eval_sets`, `/home/lumen/minerva.db`
  do **not** match (`eval_sets` fails the `root + os.sep` test — it is a sibling, not a child).
- Defense in depth is already live even if a worker somehow reached further: `harness/guard.sh:129`
  and `:335` block reads of `claude_credentials.md*`,
  `/home/lumen/minerva/context/sections/*credentials*` and
  `/home/lumen/minerva/context/sections.json*` by path, tested in
  `harness/tests/test_guard.py:111-117`.
- Precedent: `/home/lumen/hapi/eval` (`fleet-exec-policy.yaml:76`) and `/home/lumen/questfree/docs`
  (`:79`) are sub-roots of parents that are not granted. Same shape, same reasoning.
- Runtime freshness gate: `capability_lint.py:489-491` fails closed if
  `harness/runtime/read_dir_sweep.json` (`READ_DIR_SWEEP_MAX_AGE_HOURS = 48`, `:36`) lacks the
  root. `lumen-read-dir-sweep.timer` next fires 05:15Z daily; run
  `read_dir_sweep.py --write-runtime` once after landing so the root is covered immediately.
- Residual risk: someone later drops a credential into `eval/`. Mitigated by the daily
  `--write-runtime` sweep, which aq-alerts on any *new* hit (wq#1482) and makes the lint fail
  closed for that root.

**(b) Copy/symlink into an already-granted root (e.g. `/home/lumen/hapi/eval`) — NO.**
- Symlinks are followed by the worker's tools; a symlink into `minerva/` would be a grant by
  the back door with none of the sweep/lint evidence attached. Copies drift from the frozen set
  and put a minerva artifact under a `hapi` root, confusing every future reader. It also does
  nothing for `lumen_recall2.py` or `minerva.db`.

**(c) Leave excluded, rewrite wq#1614 to not need it — NO.**
- wq#1614's acceptance *is* the measured eval on the frozen set; wq#595 forbids shipping a
  scoring change without it. Rewriting away the eval would be a false done by construction.

## 4. Exposure check — does `eval/` grant reach `context/` or `sections`?

No. Verified three ways:
1. Code: `_under` requires `root + os.sep` (`capability.py:120`); `/home/lumen/minerva/context`
   does not start with `/home/lumen/minerva/eval/`.
2. Policy semantics: `allow.read_dirs` is an allow-list of roots consumed only by
   `allowed_read_dir()`; there is no wildcard or parent expansion anywhere in
   `capability_lint.py` (`grep -n "read_dirs"` → `:294` only).
3. Runtime guard: even a hand-typed `Read /home/lumen/minerva/context/sections/027_…` is blocked
   by `guard.sh:129/335` independent of the policy file.

The one thing the grant *does* expose that is worth naming: golden-set queries reveal **which**
secret files exist and where (`github-frank.token`, `nordlynx_token`, `/home/lumen/secrets`).
That is metadata already present in `kb/ISSUE-LOG.md` and `lumen_state`, both readable by
workers today. No new information class.

## 5. Companion moves so wq#1614 actually lands (not part of the policy change)

1. **`lumen_recall2.py` (code to patch):** it lives at the minerva root, which stays off the
   list. Use the mirror pattern that landed for de-spaghettifier (PR #509,
   `/home/lumen/research/harnessrouter-ref`): create `/home/lumen/research/minerva-ref/` as an
   rsync of *code only* — `lumen_recall2.py`, `minerva_memory.py` (its import, `:25`), and the
   `eval/` tree — excluding `context/`, `sections*`, `*.db`, `*.bak*`, `__pycache__`; sweep it
   zero-hit; grant that root in the same PR. Minerva is a git repo, so `git -C
   /home/lumen/minerva diff` against the mirror shows drift.
2. **`minerva.db` rows (`note_contradictions`):** never grant the db. Export the rows the eval
   needs into `minerva/eval/fixtures/note_contradictions_<date>.json` from the fleet-ops session
   (one `sqlite3 … | jq` line, operator-run), so the worker measures against a fixture under the
   granted root.
3. **Write path for wq#1614:** minerva is not a lane repo. Re-scope to the `ops/wq1610-*` /
   `ops/wq1613-*` shape (patch + `install.sh` + tests in the foreman repo, applied on Lumen by
   the fleet-ops session after review). The worker develops and measures against the mirror +
   fixture; the deploy step re-runs the eval on the live tree before `install.sh` copies the
   patched file in. Update wq#1614's detail accordingly and lift its block once 1-2 are done.
4. **Close wq#1738 from the grant PR** (`--notes` = PR + sweep evidence). It is not worker work.
5. Follow-up (from wq#1738's second finding, file separately): `capability.preflight` does not
   resolve repo-relative citations like `eval/golden_v4_frozen_20260814.jsonl` against the
   ticket's repo, so wq#1614 sat "dispatchable" while unreadable. Small lint improvement.

## 6. GO — exact change (for prompt 2, after Frank's review)

`harness/fleet-exec-policy.yaml`, inside `allow.read_dirs`, after the de-spaghettifier entries
(`:102-103`):

```yaml
    # wq#1738 / wq#1614: minerva golden sets + bench scripts only. /home/lumen/minerva itself
    # stays OFF this list (wq#1467: credentials mirrored into context/sections). Sub-root
    # matching is component-bounded (harness/capability.py:118-120), so this grants nothing
    # under context/, sections.json, minerva.db or eval_sets/. harness/read_dir_sweep.py
    # 2026-09-13: /home/lumen/minerva/eval files=508 hits=0 (evidence in the PR body).
    - /home/lumen/minerva/eval
```

PR body must carry the sweep line verbatim; then once:
`ssh lumen@100.117.66.102 'cd /home/lumen/foreman && python3 harness/read_dir_sweep.py --write-runtime | tail -3'`
so `harness/runtime/read_dir_sweep.json` covers the root before the 05:15Z timer would.

If §5.1 is taken in the same PR, add `/home/lumen/research/minerva-ref` beneath it with its own
zero-hit sweep line.

## 7. Evidence log (commands run 2026-09-13 03:30-03:40Z, on Lumen)

- `ls -la /home/lumen/minerva/eval; find … -type f | wc -l` → 519 files / 11M / 0 symlinks
- `python3 harness/read_dir_sweep.py /home/lumen/minerva/eval --max-lines 25` → `files=508 hits=0`
- `grep -rlE "token|secret|api.?key|password|BEGIN.*PRIVATE" eval` → 15 files, all lexical (§1)
- `wq show 1738`, `sqlite3 queue.db "select … where id in (1614,1738)"` (§2)
- `sed -n 55,75p harness/fleet-exec-policy.yaml`; `grep -n "^\s+- /home/lumen"` (§3 precedent)
- `harness/capability.py:116-122`, `harness/capability_lint.py:43-56,290-297,329-352,484-494`
- `harness/guard.sh:127-131,335`; `harness/tests/test_guard.py:111-117`
- `systemctl list-timers | grep read-dir-sweep` → next 05:15Z; runtime sweep ts 2026-09-12T05:16Z, 15 roots
