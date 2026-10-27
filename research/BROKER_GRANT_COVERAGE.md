# Broker GRANT coverage — let scoped-safe fleet-ops tickets run

Status: BUILT, slice 1 (policy data + lint tagging). wq#1441. Route: fable-5.1@high.
Builds on research/BROKER_WIRING.md (§ refs below are to that doc). Tree: claude-foreman
@ a87dd57, live probes 2026-09-08 05:00-06:30 UTC.

## 0. What is actually true (verified, not restated)

| brief claim | live state | verdict |
|---|---|---|
| no ALLOW rules for scoped-safe ops | policy is deny-shaped only: `never.*` + `run_readonly_cmds`; `restart` = "anything not never-listed", `dir` = "any existing non-deny-listed cited dir" (capability_lint.py:319-338 pre-change) | TRUE — open by default behind a deny list |
| tickets aren't auto-tagged with the needs they require | `capability_applied` has fired ONCE in the live log (wq#1439, `dir(/home/lumen/agent)`); every restart/ssh derivation is derive-only because the live mode is `apply-dir` | TRUE for actions; dir tagging works |
| fleet-ops tickets get DENIED, dispatchable()=0 nightly | last 24h of `pass_end`: dispatchable 3-16, spawned 1-3 per pass. The DENY rows are 4 `dir` refusals for cited SECRET paths (wq#1466/1467 `.secrets/*`, wq#1446/1474 `scratch/worktrees`) + wq#1441's own text naming `restart(tailscaled@thor)` | PARTLY — denies are correct denies; the 0 is from an older window |
| fleet lane maps to foreman but tickets land needs_unresolved | `needs_unresolved` rows in the retained log are all v2 verbs (`reboot`/`sudo`/`adb`) or non-existent cited paths (wq#1453-1458 cite `/home/lumen/fleet/inventory/*` before it existed) | TRUE, and honest: no grant exists for those |
| live broker enforces slice E | `/home/lumen/bin` primary is checked out on `feat/wq1374-…` (wq#1374 done 09-03, lumen-bin PR #60 open), 1 commit off `main`, with another lane's uncommitted 174-line `wq` edit (mtime 2026-09-07 19:29Z). `main` == `origin/main` and HAS slice E (`d327b5c`); the on-disk `fleet-exec` is pre-E (no `sudo -n`, no `fleet-exec-restore`) | the worker-facing broker is the OLD one until that tree is put back on main — ticketed, not touched (row 80 precedent: not this lane's commit) |

The defect that matters most was found by the sweep, not the brief: `dir(/home/lumen/agent)`
was auto-granted on wq#1439/#1451 and `/home/lumen/agent` holds the LIVE `GITHUB_TOKEN`
(agent/.env, 0600 but the worker runs as lumen) plus 20 `.env.bak.*` copies with the same
token and older DeepSeek keys. A read grant is `--add-dir`; every file under it is readable.

## 1. Class, not instance

Class: **grants were derived from what a ticket MENTIONS, bounded only by a deny list.** The
deny list is a list of things someone already thought of; the secret under `agent/` and the
38 tokens under `sentinel/config` (wq#1466) were not on it. Where else the class lives: the
base `add_dirs` in agents.yaml — every worker already reads `mailbot/.dev.vars:1` (a DeepSeek
key) and `kb/gatus/gatus.env` today (§3 table). What makes the class impossible: a target is
grantable only if it is on an explicit `allow:` list, and a read root goes on that list only
with a zero-hit sweep in the PR body. The lint is the single writer of tags (§2.1 of the wiring
doc), so the allow check lives there; the broker's own `never`/argv gates are unchanged and
still run at call time.

## 2. Design (shipped in this slice)

### 2.1 `fleet-exec-policy.yaml: allow:` — data, PR-only, three lists
- `read_dirs`: 11 roots, each swept zero-hit on 2026-09-08 (§3). `dir(X)` is emitted only
  when X is under one of them; the DIR_DENY list (hard-coded, capability_lint.py:38) still
  runs first and still wins.
- `restart: {isis: [ollama], lumen: [lumen-messenger]}`: per-target, each verified before
  listing — `LoadState=loaded`, `sudo -n true` exit 0 on the node, not in `never.restart`, a
  fleet probe exists to verify after (ollama: gatus `/api/tags`; lumen-messenger: unit
  active + chat.lumennode.uk). `qbittorrent@thoth` (wq#1157) is NOT listed: thoth still times
  out on :22, so the per-target check cannot run.
- `ssh: [lumen, isis, bes, anubis, maat, sobek]`: read-only `run` only; thor stays out.

### 2.2 `capability_lint` order of evaluation (deterministic; no LLM)
1. `never` (nodes, restart globs) → `refused`, always, even for a prose-shaped mention —
   "restart tailscaled on thor" is a deny row, not a shrug (test iii).
2. `allow` → tag.
3. Neither: explicit shapes (`restart(u@n)` tag form, `ssh <node>`) → `unresolved` with
   the hint `propose by PR to fleet-exec-policy.yaml allow:`; prose shapes (`restart u on n`,
   `systemctl restart u` inside a sentence, `... on <node>`, bare IPs) → `unresolved` only
   when the unit token is unit-shaped (`[-.@]` in it), else nothing. This kills the live false
   tags `restart(without@lumen)` / `restart(on@lumen)` derived from wq#1440's detail.
4. A missing `allow:` section allows nothing (an edited-away section must not restore the
   open default). Rollback stays `FOREMAN_NEEDS_LINT_MODE=warn` (derive + log, decide nothing).

### 2.3 `harness/read_dir_sweep.py`
Basename globs (`*.env`, `.env*`, `*.token`, `*.key`, `*.pem`, `id_*`, …) + token regexes
(GitHub PAT, `sntl-p`, `sk-*`, AWS, Slack `xox*`/`xapp-`, PEM private key, `key/secret/
password/token = <20+ chars>` excluding `os.environ/…`, `${VAR}`, `<placeholder>`). Masks the
matched token to 4 chars BEFORE truncating the excerpt (the throwaway sweep this ticket first
used truncated first and printed a live PAT whole — that shape is a pinned test).
`--policy` sweeps every `allow.read_dirs` root; exit 1 on any hit.

### 2.4 Not changed
`capability.py` / `needs.py` (Liveness Gate has uncommitted edits there in the primary —
not touched, no collision), `fworker.resolve_exec_grant`, guard.sh, the never-list,
`fleet_exec_policy.py`. The lint mode default stays `apply-dir` in this slice; flipping to
`apply` is slice 2 after test iii's forged-Needs drill (§4).

## 3. Sweep evidence (2026-09-08, `read_dir_sweep.py`, masked)

| root | files | hits | on allow? |
|---|---|---|---|
| /home/lumen/research_prompts | 74 | 0 | yes |
| /home/lumen/decisions | 9 | 0 | yes |
| /home/lumen/instructions (incl. playbooks, executive, approvals, runs) | 189 | 0 | yes |
| /home/lumen/security | 8 | 0 | yes |
| /home/lumen/etc | 7 | 0 | yes |
| /home/lumen/hapi/eval | 5 | 0 | yes |
| /home/lumen/pstack | 160 | 0 | yes |
| /home/lumen/personas | 50 | 0 | yes |
| /home/lumen/questfree/docs | 20 | 0 | yes |
| /home/lumen/projects/bench | 7 | 0 | yes |
| /home/lumen/infra | 16 | 0 | yes |
| /home/lumen/proposals (wq#1535: promoter-published proposal.md + plan.md, masked at write) | 45 | 0 | yes (added 2026-09-08) |
| /home/lumen/agent | 443 | 899 | **NO** — agent/.env (live GITHUB_TOKEN), 20× .env.bak.* (same PAT + old DeepSeek keys), litellm_config.yaml.bak.* (`api_key: os.environ/…` = benign, the .env files are not) |
| /home/lumen/sentinel/probes | 26 | 22 | NO — sentinel_probes.py:23 + every .bak: `TOKEN = "sntl…"` (wq#1466 rotation scope) |
| /home/lumen/sentinel/config | — | 38 tokens | NO — wq#1466 |
| /home/lumen/minerva/context | 435 | 7 | NO — context/sections/027_4-credentials-secrets.md:11 (OpenRouter key), sections.json.bak.20260709:243 (same table), LIVE_FLEET_STATE_FULL.md:159 (sntl in prose) — wq#1467 |
| /home/lumen/messenger | 2236 | 3 | NO — data/vapid_private.pem, logs/server_chat_history.json:2473 (a Slack `xapp-` app token pasted into a chat) |
| /home/lumen/fleet/inventory | 61 | 1 | NO — patches/wq1454/sentinel_probes.py.patch:20 (sntl) |
| /home/lumen/kb (base add_dir, already granted) | 28 | 1 | kb/gatus/gatus.env (basename) |
| /home/lumen/mailbot (base add_dir, already granted) | 63 | 1 | mailbot/.dev.vars:1 `DEEPSEEK_API_KEY=sk-4…` |

### 3.1 Every base add_dir (2026-09-20, wq#2116)

The §3 table sampled candidate roots. This is the same sweep over the roots every worker
already holds — `agents.yaml` base `add_dir`, no ticket needed to read them.

| root | files | hits | verdict |
|---|---|---|---|
| /home/lumen/kb | 31 | 0 | clean (wq#1483 moved `gatus/gatus.env` out) |
| /home/lumen/pokemon | 3189 | 0 | clean |
| /home/lumen/mailbot | 78 | 1 | `evals/run.mjs:118` — `process.env` read, false positive (wq#2115) |
| /home/lumen/bin | 400 | 2 | `probe_wq616.py:55,61` — `…-dummy` fixture literal (wq#2115) |
| /home/lumen/projects/nest | 3202 | 2 | `build/nle-initramfs-root/etc/ssl/certs/*.pem` — CA bundle (3 certs) + CRL bundle (3 CRLs), zero `PRIVATE KEY` blocks, `*.pem` basename false positive |
| /home/lumen/agents/work-queue | 1009 | 8 | **real** — `_runs/fleet{9,12}-*.jsonl` transcripts quote the live gatus push token back; §3.2 |
| /home/lumen/foreman | 24705 | 348 | §3.2 |

Live non-test source hits under foreman, triaged one by one — all four are false positives,
no credential literal in live foreman source:

| file:line | shape | verdict |
|---|---|---|
| `despag/app.py:100` | `kv_secret` | `API_TOKEN` = `_load_or_create_token()` — the RHS is a call, not a value; the token is read from a 0600 file (`app.py:92-97`) |
| `claude-shim/claude_shim.py:83` | `kv_secret` | `API_KEY` = `_load_or_create_api_key()` — same, generated + written 0600 (`claude_shim.py:75-80`) |
| `research/headroom_proxy_gate.py:42` | `sk_key` | `SYNTHETIC_BEARER` — literal says `not-a-real-credential`, it is the gate's negative control |
| `events/fworker-wq1482-a3.jsonl:37,38` | `gh_pat` | `ghp_` + `A-Z0-9` in order — a worker event quoting the canonical alphabet fixture |

The first two are a third false-positive class, distinct from wq#2115's two: `kv_secret`
matches a **Python identifier or call** on the right of `=`, because `_load_or_create_token`
is 21 characters of `[A-Za-z0-9_]`. Noted on wq#2115 so one owner fixes one regex.

Two hits were **real**, both copies of `/home/lumen/sentinel/config/config.yaml` pasted into
git-tracked foreman files — `ops/wq897-litellm-auth-monitor-probe/gatus_config.diff:4,9` and
`reports/wq908_pokemon_liveness_probe.md:172,177`. Same value in both, and the same value the
`_runs` snapshot of the live config carries, so it is the shared `5-probes` gatus push token,
not a placeholder. Redacted here; the value is in git history, so it rotates with wq#1466.

### 3.2 `_runs/` snapshots launder the allow list — retention pass required

323 of foreman's 348 hits are under `_runs/<ticket>/snapshots/`, and they are not fixture
noise. `action_gate.snapshot()` takes a verbatim pre-image of every AMBER-classified file
edit, so a worker that edits a credential file copies it whole into foreman — and foreman is
every worker's `--add-dir`. What is actually sitting there:

- `20260907T145802Z-…sentinel_config_config.yaml` and `20260909T074301Z-…` — two full copies
  of the 38-token gatus config (§3 row: **NO**, wq#1466), 76 hits.
- 15+ copies of `…home_lumen_agent_.env` — the live `GITHUB_TOKEN` (§3 row: **NO**), 3 hits each.
- `*-git-state.txt` — `git diff` of a dirty worktree, whatever the edit put in it.

So the decision for scope item (3) is **yes, this needs its own pass**, and not because the
files are old: the allow list refuses `dir(/home/lumen/agent)` and `dir(/home/lumen/sentinel/config)`
by name, and `_runs/` hands a worker the contents anyway. Frozen historical record is a fine
reason to keep an artifact, not a reason to keep it inside a read grant.

Two halves, split by who can do them:

1. **Source** (done here): `snapshot()` routes every artifact through `_stow()`, which asks
   `read_dir_sweep`'s own globs + patterns whether the bytes are credential-shaped. If they
   are, the pre-image goes to `~/.secrets/gate-snapshots` at 0600 — outside every `add_dir` —
   and the snapdir gets a path-only `.held-out` pointer. Rollback still works; the grant
   no longer reaches the bytes. An import failure counts as sensitive (fail closed).
2. **History**: the 57 existing run directories still hold the old copies, and
   `/home/lumen/agents/work-queue/_runs` holds 8 more — the same gatus token, quoted back
   inside worker transcripts, in a second base `add_dir`. Neither is git-tracked (foreman:
   `git ls-files _runs` = 0; work-queue is not a repo), so this is a local-disk purge, not
   a history rewrite — out of a worker's reach (`rm` outside the worktree is denied by
   guard.sh). Filed as a follow-up ticket.

A transcript is the harder half of that second bucket: `_stow` cannot help there, because
the token reaches the JSONL as ordinary tool output, not as a snapshot. The durable fix for
it is the same one wq#1466 is already scoped to do — take the literal out of
`sentinel/config/config.yaml` so there is nothing for a worker to read and echo.

### 3.3 Re-run after wq#2115 landed (2026-09-28, wq#2116)

Scope item (2) of wq#2116: same sweep, same roots, run once wq#2115's label-shape and
`process.env` work was on main (merged as `6e35157`).

| root | files | hits | vs §3.1 |
|---|---|---|---|
| /home/lumen/kb | 34 | 1 | `ISSUE-LOG.md:832` — row 723's own prose, quoting the broker's `ghp_selftest` canary while explaining it is not a token. Documentation of a finding |
| /home/lumen/mailbot | 86 | 1 | `evals/run.mjs:118` **cleared** — the `process.env` guard works. New: `DESIGN_v2.md:39`, the label shape now reads prose ("Thread key … root Message-ID") |
| /home/lumen/pokemon | 3245 | 3 | was 0 — all three are new, all the call-on-RHS class: `const key = Notepad.correctionKey(line)` ×2, `AQ_KEY = "lpp-brain-` + `unreachable"` (an alert dedupe key) |
| /home/lumen/bin | 457 | 12 | was 2 — `probe_wq616.py` unchanged, plus shell/python test fixtures (`GITEA_PASSWORD=` + `sandboxpw`, `'DEEPSEEK': 'sk-N…'`) and `new_…(notes, token, abs_path)` in `wq` ×5, counting `.bak` copies (wq#2114) |
| /home/lumen/projects/nest | 3202 | 2 | unchanged — CA + CRL bundles, `*.pem` basename |
| /home/lumen/agents/work-queue | 1009 | 3 | the 8 **real** `_runs/fleet{9,12}-*.jsonl` hits are **gone**: wq#2118 relocated them 2026-09-20 18:26 and left `.held-out` pointers. The 3 remaining are one board line of prose plus its two `.bak` copies |
| /home/lumen/foreman | 45797 | 390 | was 348 (861 at the wq#2123 re-check) — composition below |

Foreman's 390, by writer rather than by directory:

| what | hits | verdict |
|---|---|---|
| `.pytest_cache/v/cache/nodeids` | 198 | untracked pytest cache in the primary tree; the node ids of the masking suite carry its fixture literals |
| `_runs/mask_at_rest/20260925T042118Z.jsonl` | 119 | the mask tool's own findings log. Every hit is either the literal `MASKED` placeholder inside a reconstructed `http://lumen:MASKED@host` string, or a `test_masking.py` parametrize line quoted into the record's `context` window |
| `harness/tests/*` | 16 | live fixtures — wq#2120's fingerprint allowlist is still open, so this class did **not** clear |
| `_runs/wq<n>/` — `mask-hits-*.json`, `evidence/*/replay-AC1.log`, 8 in `snapshots/` | 33 | same shape one layer down: mask reports and worker evidence quoting pytest node ids, spread over 15 run dirs |
| `reports/`, `research/`, `dispatcher/`, `events/`, `ops/`, `memory/` | 21 | report and doc text, including this file's own §3.1 table |
| `despag/app.py`, `claude-shim/claude_shim.py` | 2 | the call-on-RHS class of §3.1, unchanged (wq#2120). `research/headroom_proxy_gate.py` is now **0** |
| `_runs/wq1135/snapshots/…-home_lumen_.git-credentials` | 1 | the one real pre-image left. 2026-08-30, predates `_stow` |

So scope item (2)'s answer is split: the `process.env` class cleared, the fixture class did
not, and wq#2115's wider `kv_secret` label shape bought a new crop of call-on-RHS positives in
roots that were clean before. The count moved 348 → 390 while the number of real credentials
in it moved 2 → 1.

That last one is worth stating precisely, because it is the bound on how bad `_runs` still is:
`guard.sh` refuses `.git-credentials*` **by basename**, so a worker's `Read`/`Bash` cannot
reach the snapshot even though the bytes sit inside the read grant. Verified this session: any
worker command naming that path is refused with `guard.sh: BLOCKED — denied path: git
credential store (.git-credentials*, .netrc, .fleet_pw)`. The sweep sees it because the sweep
is not a worker. The bytes are still a retention problem (wq#2118), not an open grant.

### 3.4 `_is_sensitive` failed open on anything the sweep would not read

§3.2's `_stow()` asks `read_dir_sweep` whether a pre-image is credential-shaped, and got a
useful answer only for files the sweep can read as text. `_read_text()` returns `None` for a
symlink, a file over `MAX_FILE_BYTES`, a NUL in the first 4KB, or an `OSError`, and `""` for a
genuinely empty file; `_is_sensitive` collapsed both with `if not text: return False`. So
"the scanner declined to look" was recorded as "the scanner found nothing", and the bytes went
to the 0644 snapdir. A symlink named `notes.txt`, a sqlite db and a 3MB log each carried a
token straight back through the grant §3.2 was written to close. The basename half was blind
the same way: it globbed the path's own basename, which for a symlink is the link name.

Fixed in `harness/action_gate.py:544-557` — `None` now means sensitive, `""` stays clean, and
basenames are globbed for both `path` and `realpath(path)` through `basename_candidates()`.
Pinned by five tests at `harness/tests/test_action_gate.py:468-523`, including
`test_empty_preimage_still_lands_in_snapdir`, which is the boundary the fix must not cross.

### 3.5 Dead `read_dirs` entry replaced (wq#4691)

`allow.read_dirs` named `/home/lumen/research_prompts`, which does not exist — the real
research-prompt inbox the research-prompt-adapter (wq#4677) queues into is
`/home/lumen/scratch/research_prompts`. Swept the real path before granting it:

| root | files | hits | verdict |
|---|---|---|---|
| /home/lumen/scratch/research_prompts | 4611 | 2 | false positives — `sk_key` matched a 24+ char hyphenated URL slug naming the company "SK Telecom" / "SK Hynix" inside queued article text, not an API key: `done/intel_alleged_china_ties_at_sk_telecom_alarmed_us_officials_and_tr_7fb176.txt:4` and `done/intel_samsung_and_sk_hynix_plan__590_billion_chip_investment_as_ai_4d5c80.txt:4`, both a `URL: https://the-decoder.com/...sk-t.../` line |

Replaced the dead entry in `harness/fleet-exec-policy.yaml:74` in place rather than adding a
second line, since nothing ever read the old path (it never existed on disk).

## 4. Test-to-failure

| # | case | result |
|---|---|---|
| unit | `tests/test_capability_allow.py` (20): allow-hit tags; outside-allow → unresolved+hint; deny beats allow; missing section allows nothing; restart matrix (never>allow>shape, 10 rows); ssh explicit vs prose; live policy shape (every read root passes DIR_DENY, every restart entry passes never) | PASS |
| unit | `tests/test_read_dir_sweep.py` (15): masked-at-truncation-edge, basenames, 5 token shapes, binary/.git skipped | PASS |
| unit | existing `test_capability_lint.py` (79) + `test_capability.py` under the new fixture allow section | PASS (fixture gained `allow:`; no expectation weakened — every row that expects a tag is under a fixture allow root) |
| replay | live open queue (30 tickets), old vs new lint, same repo resolution | 0 tickets change bucket. The 4 already-refused rows (1446/1466/1467/1474) additionally lose their partial `dir` tags — correct, refusal beats a partial grant (§4 row vi) |
| iii | never-list: `restart tailscaled on thor` / `restart(postgresql@maat)` / `systemctl restart cloudflared` → `refused` deterministically; MAC-spoof text has no verb shape → no tag; `rm -rf` is not a verb the lint emits and `run_readonly_cmds` has no `rm` | PASS (unit); broker-side forged-Needs drill = slice 2 |
| ii (live) | `fleet-exec restart isis ollama` from the lumen-bin `origin/main` checkout (fe8e492) with the new policy, forged grant naming it | PASS 2026-09-08 05:46Z: audit `allow policy:restart_allowed`, `before={loaded,active,running,516198266878}` → `after={…,680501786733}`, `verified=true`, exit 0; ActiveEnterTimestamp `2026-09-06 08:08:02` → `2026-09-08 05:46:26 UTC`; `holddown list` → no flags; `/api/tags` still serves `qwen3.6:35b-a3b` + `qwen3:32b` |
| iii (live, broker) | forged grant.json (sha-valid, ticket 1441) listing restart thor/tailscaled, lumen/cloudflared, maat/postgresql, isis/gitea + ssh run on thor/isis; ten calls | every mutating call DENIED and audited: `policy:never_nodes` (thor), `policy:never_restart` ×2, `policy:restart_not_allowed` (gitea@isis — the new allow row), `policy:not_readonly` (`systemctl restart` on thor, `rm -rf`, `ip link set … address 64:00:6a:58:fc:d8`), `policy:shell_escape_basename` (`bash -c`), `policy:credential_path_blocked` (`cat ~/.ssh/id_ed25519`). Only `run thor -- uptime` allowed (the designed read-only exception). Zero ssh sessions opened for any denied call (deny precedes transport) |
| iv | `dispatchable` after landing | dry-run pass from the merged tree: dispatchable 4, lint_scanned 11, no new skips; the same pass spawned wq#1485 (row i) and wq#1484 |
| i (live) | worker read an allow-listed dir via needs:dir and landed this line | PASS 2026-09-08 wq#1485 |

## 5. Slices

| slice | content | repo | rollback | state |
|---|---|---|---|---|
| 1 | this: `allow:` section + lint gating + sweep tool + tests + this note | foreman | `FOREMAN_NEEDS_LINT_MODE=warn`; or delete the `allow:` lists (allows nothing) | BUILT |
| 2 | lint default `apply` (restart/ssh tags written); test iii forged-grant drill against the broker | foreman | `FOREMAN_NEEDS_LINT_MODE=apply-dir` | DONE (this PR; drill §4) |
| 3 | live proofs i / ii / iv | foreman + live | — | DONE (§4; wq#1485 PR #344) |
| 4 | lumen-bin `fleet_exec_policy.check_restart` honours `allow.restart` (broker enforces allow, not only the lint) | lumen-bin | no allow section = old behaviour | LANDED fe8e492 (wq#1484) — NOT yet on the `/home/lumen/bin` primary (wq#1481) |
| 5 | `lumen-read-dir-sweep.timer` daily → `harness/runtime/read_dir_sweep.json`; lint refuses `dir(X)` on a fresh hit under X and treats a >48h-stale sweep as unresolved | foreman + one unit | timer disabled = slice-1 behaviour | ticket |

## 6. Findings → tickets (same turn)
- GITHUB_TOKEN burned into a transcript by the throwaway sweep — rotation is a Frank physical act (mint on his account); `agent/.env.bak.*` should be 0600-and-pruned or moved under `.secrets/`.
- `/home/lumen/bin` primary on a feature branch = live broker is pre-slice-E.
- messenger chat log holds a Slack app token; base add_dirs expose `mailbot/.dev.vars` and `kb/gatus/gatus.env` to every worker.
