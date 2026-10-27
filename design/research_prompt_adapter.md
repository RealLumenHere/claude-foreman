# research-prompt-adapter — drain scratch/research_prompts through the wq research lane

wq#4677 · 2026-10-26 (PDT) · session build-rpq-adapter-451616 · status: DESIGN → BUILD

## 1. Root cause (evidence grade: verified on the box 2026-10-26)

`research-prompt-queue` is advertised at every `--core` boot as "drop a .txt here, fleet
researchers drain it". Nothing has drained it since 2026-07 (wq#2741, wq#3911, wq#4626):

- The only consumer, `agent/lumen_deep_research_orchestrator.py`, has no unit/timer/cron and
  is a direct Groq/Gemini surface outside LiteLLM. It stays DORMANT (ticket rule).
- The writers are also off: `lumen-intel.timer` is `disabled`/`inactive` (checked
  `systemctl is-enabled`), `agent/github_scout.py` has no unit, no cron, and its sqlite db
  `agent/github_scout.db` last changed 2026-09-09.
- Meanwhile the fleet grew a real research path: `wq add --lane research --title "RESEARCH: …"`
  which fworkers drain (router fix wq#4633; wq#4679 is a live example).

**Class:** a boot-advertised inbox with no consumer. The fix is an adapter that turns the inbox
into the thing that IS drained (wq tickets), so the advertised contract becomes true again
without reviving a dormant engine.

## 2. File-format survey (live inbox, 2026-10-26 21:3x PDT)

Ticket text said ~55; the inbox holds **50** top-level `*.txt` (plus `done/` 4559 files,
`vip/` 0, `stuck/` 0). Two families:

| family | count | writer | shape | newest |
|---|---|---|---|---|
| `intel_<slug>_<6hex>.txt` | 48 | `agent/lumen_intel.py` (`write_prompt`) | line 1 is the generic "Research the following topic for a technical homelab/AI audience."; real subject on the `Title:` line; `Source:`/`URL:`/`Intel score:` fields; asks for a 1500–2500-word **Ghost blog article** | 2026-10-14 |
| hand/agent prompts | 2 | Frank / orchestrator sessions | `miko3-deep-hw-research.txt` (7951 B, line 1 `RESEARCH PROMPT: …`), `twn4-multitech3-linux-reflash.txt` (6060 B, line 1 `DELIVERABLE: <path>`) | 2026-10-25 |

Not present today: the July `00_gap_*` github_scout prompts the ticket mentions (already in
`done/`/`research_prompts_done/`), and `00_social_*`, `00_claude_*`, `00_PRIORITY_*` shapes that
other writers (`github_scout.py`, `claude_bridge.py`) can emit. The adapter handles all of them
generically (below).

Sizes: 581–7951 B; none over 8 KB today. `scratch/research_prompts_done/` (github_scout's
PROCESSED_DIR) holds 10384 files.

## 3. Design

### Inbox + outcomes
- Inbox: `/home/lumen/scratch/research_prompts/*.txt` (top level only; `done/`, `vip/`,
  `stuck/` untouched). Every writer already targets this path, so no writer repoint is needed
  for *delivery* — only github_scout's dedupe gate (§6).
- Filed → `mv` to `queued/<wq-id>_<name>.txt` (ticket text).
- Rejected (empty, whitespace-only, stale, unreadable) → `expired/<name>.txt` with the reason in
  the journal. Never deleted.

### Title derivation (first match wins, trimmed to 120 chars, newline/ctrl stripped)
1. a `Title:` line anywhere in the first 20 lines (the `intel_*` shape) — the literal first
   line there is boilerplate;
2. first non-empty line with a leading `RESEARCH PROMPT:` / `DELIVERABLE:` / `PROMPT:` label
   removed (keeps the path after `DELIVERABLE:` out of the title: it is a deliverable, not a
   subject — the path lands in the detail);
3. the file stem, `_`→space.
Ticket title = `RESEARCH: <derived>`.

### Dedupe key (idempotence has three layers, cheapest first)
1. **`queued/` suffix check**: a file whose exact name already exists as `queued/*_<name>.txt`
   is never re-filed; it is moved aside into `expired/` tagged `dup-name`.
2. **content sha256** over the normalised body (CRLF→LF, trailing whitespace stripped): a ledger
   `/home/lumen/logs/.research-prompt-adapter.state.json` maps sha → {wq_id, name, filed_at}.
   Same body under a new filename → no ticket, file moves to `queued/<existing-id>_<name>.txt`.
3. **wq's own title guard**: `wq add` exits 1 with `DUPLICATE — task <id> [...]` when a
   non-done ticket has the same normalised title. The adapter parses `<id>` and treats it
   exactly like layer 2 (move to `queued/<id>_…`, record in the ledger). So a re-run can never
   file twice even if the ledger is lost.
Lost-ledger case: layers 1 and 3 still hold; only a prompt whose ticket was already `done`
could be re-filed, which is the correct behaviour (a new request after completion).

### Stale policy
- A prompt whose mtime is older than `--max-age-days` (default **30**) at pickup goes to
  `expired/` (reason `stale`), not to the lane. Reason: every past backlog here was a dormant
  writer dumping dozens of files nobody asked for; a 10-minute timer must never turn a
  month-old dump into 50 fworker runs of Claude capacity.
- Per-run cap `--max-per-run` (default **10**). Anything beyond waits for the next tick, so a
  burst writer can at most add 60 tickets/hour and the lane stays scannable.
- Today's triage (§5) is applied by hand under the same rule, with the reasoning recorded.

### Size cap
- Body ≤ 8192 bytes → whole body in `--detail`, path cited.
- Body > 8192 bytes → `--detail` = first 2000 chars + `FULL PROMPT (<n> bytes, >8KB): <abs path>`
  + the path to the queued copy. The worker reads the file. NOTE (advisory on wq#4677):
  `harness/fleet-exec-policy.yaml allow.read_dirs` lists `/home/lumen/research_prompts`, not
  `/home/lumen/scratch/research_prompts`, so an fworker is walled off the path today. Follow-up
  ticket filed (§8) to add the real path by PR; until then >8KB prompts still carry a 2000-char
  head so the ticket is workable, and the full file is a `cat` away for an orchestrator.

### Ticket shape
```
wq add --lane research --pri 3 --by research-prompt-adapter \
  --title "RESEARCH: <derived>" \
  --detail "<body or head>\n\nSOURCE FILE: <inbox path> (moves to queued/<id>_<name>.txt once filed)\nFiled by research-prompt-adapter (wq#4677)." \
  --liveness "test -f /home/lumen/scratch/research/<stem>.md"
```
`--liveness` is mandatory on `wq add` (wq#1676); the probe names the conventional output
path (`scratch/research/<stem>.md`) so `done` has a checkable deliverable. `--pri 3`, lane
`research`, no model/archetype (router classifies, wq#4633). No `--claim`. No direct-provider
calls anywhere: the adapter only execs `/home/lumen/bin/wq` and moves files.

### Landing zone + mirror (added after the first live drain, wq#4690)
An fworker's sandbox cannot write `scratch/` (its only write path is a PR into a repo tree), so
wq#4690's runbook landed at `foreman/research/<basename>` and the ticket note asked for an
orchestrator `cp`. A hand step is not a fix (instinct: fix the class, in machinery):
- the ticket detail now carries a `LANDING:` line telling the worker to land the deliverable by PR as
  `/home/lumen/foreman/research/<deliverable basename>`;
- the probe is `test -f <deliverable> || test -f /home/lumen/foreman/research/<basename>`;
- every tick runs `mirror_landed()`: for each `queued/*.txt` whose deliverable is expected under
  `scratch/research` and is absent, copy `foreman/research/<basename>` across if it exists
  (copy-if-absent, atomic rename, never overwrite). Summary line gains `mirrored=N`.
Tests T14–T16 cover the dual probe, the mirror, and the never-overwrite rule.

### Runtime
- `/home/lumen/bin/research-prompt-adapter` — python3, stdlib only (`subprocess`, `hashlib`,
  `json`, `fcntl`, `argparse`, `pathlib`). Flags: `--dry-run`, `--once` (default), `--inbox`,
  `--state`, `--wq`, `--max-age-days`, `--max-per-run`, `--max-inline-bytes`. All paths are
  env-overridable (`RPA_INBOX`, `RPA_STATE`, `RPA_WQ`) so the test harness runs against a stub
  `wq` and a temp inbox; production never sets them.
- Exclusive `flock` on `<state>.lock` so a slow run and the next tick never double-file.
- Exit 0 = ran (filed N, dup D, expired E, skipped S printed as one `summary:` line);
  2 = could not run (inbox unreadable, lock busy is 0 with `summary: locked`).
- `research-prompt-adapter.timer`: `OnBootSec=3min`, `OnUnitActiveSec=10min`,
  `RandomizedDelaySec=60`, `Persistent=true`. Service: `Type=oneshot`, `User=lumen`,
  `ExecStartPost=-/home/lumen/bin/lumen-cron-stamp %n`, `TimeoutStartSec=300`, `Nice=10`.
- Foreman ops bundle `ops/wq4677-research-prompt-adapter/` (README, install.sh with the
  wq#1696 `--verify` contract, both units, a byte-identical copy of the binary for `vfile`,
  `tests/test_adapter.py`). The bundle installs units only; the binary lands through the
  lumen-bin PR (same split as ops/wq3215).

## 4. Options considered
- **Revive `lumen_deep_research_orchestrator.py`** — rejected by the ticket and by HANDOFF s4
  (direct Groq/Gemini outside LiteLLM, no critic gate).
- **Teach fworkers to read the inbox directly** — spreads the inbox contract into every lane's
  guard policy; the queue is the single intake the fleet already has, keep it there.
- **Adapter → wq (chosen)** — one 150-line stdlib script, idempotent by construction, every
  prompt becomes a routable, auditable, closeable unit of work with a liveness probe.
- **Symlink inbox into `foreman/`** — no: foreman is a Class A repo, scratch is not.

## 5. Triage of the live inbox (applied 2026-10-26, counts recorded in the ticket close)
- 48 × `intel_*` (2026-10-14) → `expired/` (reason `writer-off+target-retired`): their writer
  timer is disabled, every prompt asks for a Ghost blog article and Ghost is retired, and the
  fleet's "reverse stale work, do not execute it" instinct applies. Filing them would spend 48
  fworker runs on articles nobody will publish. If Frank wants the intel engine back, that is a
  separate decision (re-enable `lumen-intel.timer`); the adapter will then file whatever it writes.
- 2 kept and filed by the adapter's first live run: `miko3-deep-hw-research.txt`,
  `twn4-multitech3-linux-reflash.txt` (both real fleet RE work, <12 days old).
- July `00_gap_*` github_scout prompts: none at top level (already in `done/`); nothing to move.

## 6. github_scout.py repoint
Scout writes `00_gap_*.txt` into `PROMPTS_DIR` = the adapter inbox, so delivery already lands
in the right place. What breaks is its **re-emission gate**: it only consults
`research_prompts_done/` to decide a repo was already researched (2026-07-05 fix). Consumed
prompts now move to `research_prompts/queued/` and `expired/`, so the gate would go blind again
and re-emit the same repos daily. Patch (`/home/lumen/agent` is not a git repo → timestamped
`.bak` first): the `GAP_REFRESH_DAYS` block also globs `PROMPTS_DIR/queued/*_<stem>.txt` and
`PROMPTS_DIR/expired/<stem>.txt`. Scout stays dormant (no unit); this only makes it safe to
re-enable.

## 7. Test-to-failure plan
Harness `tests/test_adapter.py` (stub `wq` that mimics `added task N [research]` and the
`DUPLICATE — task N` refusal; temp inbox):
1. five distinct prompts → five tickets, five `queued/` moves, ledger has five shas;
2. second run on the same inbox → `filed=0`, nothing moves;
3. duplicate body under a new name → no ticket, moved to `queued/<existing-id>_…`;
4. empty file + whitespace-only file → `expired/`, no wq call;
5. 20 KB file → one ticket, detail carries the head + path marker, body not inlined;
6. ledger deleted + stub answers DUPLICATE → no new ticket, file still moved, ledger rebuilt;
7. stale file (mtime −40d) → `expired/stale`;
8. 15 files with `--max-per-run 10` → 10 filed, 5 left for the next tick;
9. `wq` exits non-zero with no DUPLICATE marker → file stays in the inbox, run reports
   `failed=1`, exit 0 (next tick retries);
10. lock held by another process → `summary: locked`, exit 0, nothing touched.
Live: `--dry-run` on the real inbox, then the real first run (expects filed=2), then a second
real run (expects filed=0), then `systemctl list-timers research-prompt-adapter.timer`.

## 8. Rollback + follow-ups
- Rollback: `sudo systemctl disable --now research-prompt-adapter.timer; sudo rm
  /etc/systemd/system/research-prompt-adapter.{service,timer}; sudo systemctl daemon-reload`;
  files in `queued/` keep their `<id>_` prefix so the tickets they produced remain traceable.
  `git -C /home/lumen/bin checkout -- research-prompt-adapter` (or revert the PR).
- Follow-ups filed: fleet-exec-policy `allow.read_dirs` → `/home/lumen/scratch/research_prompts`
  (advisory on wq#4677); decide whether `lumen-intel.timer` comes back (Frank-decision gate,
  not a block on this ticket).
