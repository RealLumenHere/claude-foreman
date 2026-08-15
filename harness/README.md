# harness — T2a worker harness

wq#625, BUILD_PLAN.md §T2a. Implements
[design/T2A_WORKER_HARNESS_DESIGN.md](../design/T2A_WORKER_HARNESS_DESIGN.md), Frank-approved
via PR#2. One `fworker` supervisor process per ticket, spawning exactly one `claude -p`
child at a time on the shared login (multi-worker auth is T2b, gated on T2b-gate).

## Layout

    fworker.py              supervisor: spawn + parse + classify + wq lifecycle
    classifier.py           the LIMIT/TURNS/BUDGET/CRASH/REFUSED decision table (§E)
    worker-settings.json    settings pack — replaces worker-settings-interim.json
    guard.sh                PreToolUse fail-closed guard (§5)
    prompts/worker.md       worker prompt template
    tests/                  pytest suite (zero Anthropic quota)

Runtime paths (governor-owned or created on demand, not checked in):
`../events/<worker-id>.jsonl` (lifecycle + blocked events), `../_runs/wq<ID>/`
(attempt-N.json, prior-attempt-notes.md), `/home/lumen/worktrees/wq<ID>-a<N>/`
(per-attempt git worktrees), `../state.json` / `../STOP` (T1 governor).

## Running a ticket

    python3 harness/fworker.py run --ticket <ID> --repo <path> \
      --model claude-sonnet-5 --effort high --archetype standard-engineering

Launch via `lumen-run` so the supervisor itself survives DC-gateway reaping:

    lumen-run start --name fworker-wq<ID> -- \
      python3 /home/lumen/foreman/harness/fworker.py run --ticket <ID> --repo <path> \
        --model <id> --effort <name> --archetype <name>

Exit codes: `0` success, `10` claim error, `11` deferred (governor/STOP gate), `12`
blocked (2 failed attempts, ticket wq-blocked), `13` limit (ticket released, resets
outside the resume window).

## Build-time discovery vs the design (read before touching --max-turns)

The design was probed against claude CLI 2.1.220; the box now runs 2.1.233, which
**dropped the `--max-turns` flag**. `fworker.py` no longer passes it. Instead the
supervisor counts `assistant` stream-json frames itself (`classifier.RunState.turns`)
and SIGTERMs the child once the per-archetype ceiling is reached, then classifies the
attempt TURNS exactly as if the CLI had enforced it server-side. See the module
docstrings in `fworker.py` and `classifier.py`. If a future CLI reintroduces the flag
(or the `--agents` JSON frontmatter `maxTurns` key proves to apply to `-p` sessions —
unconfirmed this session, `strings` shows the key but only in skill/agent frontmatter
contexts), passing it in addition to the supervisor-side cap is a safe belt-and-braces
follow-up, not a required one.

`claude agents --json` (confirmed working, no TTY needed) is used only as an advisory
liveness signal during `--resume` reconciliation (design §8) — never the control channel.

## Tests

    cd harness && python3 -m pytest tests/ -v

36 cases: guard.sh corpus (every deny class + quoting/`bash -c` evasions, lifecycle
event logging, fail-closed on malformed/empty/off-host input), the classifier's full
decision table including the turn-ceiling case, settings-pack lint (acceptEdits, no
bypassPermissions anywhere in the harness or in the retrofitted gh-lane-worker,
docker ps/logs allow vs docker-star deny, credential-path denies), the STOP/can-dispatch
governor gate, and four end-to-end fworker runs against a fake `claude` shim
(`tests/fake_claude.py`) covering success + worktree isolation, a denied verb actually
blocked at the hook during a real spawn, a synthetic rejected rate-limit frame driving
the real LIMIT path (release, not block), and resume-after-LIMIT reusing the worktree.

`probe_wq625.py` (`/home/lumen/bin/probe_wq625.py`) wraps this suite plus the file-layout
and gh-lane-worker-retrofit checks into the ticket's probe gate; it's the thing CI/a
human runs, not the pytest invocation directly.

## What's deliberately not exercised here (quota-costing, follow-up)

Design §12 item 5/6: one real sonnet-5@low (or haiku) ticket through fworker end to end,
and one gh-lane-worker smoke on a trivial live github-lane ticket. Both cost live Max-plan
quota; this build's probe gate is zero-quota by design (matches the ticket's two named
adversarial criteria — denied-verb-blocked-at-hook, LIMIT!=CRASH — plus the full harness
suite). Schedule the quota-costing smoke off-peak when convenient; nothing in the design
depends on it to land T2a.

## Known gaps vs the full design (explicit, not silent)

- Restart reconciliation (§8) trusts wq status + `_runs/` + an advisory `claude agents
  --json` liveness check; it does not yet re-attach WATCH-ONLY to a live orphaned child
  process by pid/cmdline. A reaped fworker's lease simply expires and the ticket returns
  to the queue loudly (existing wq machinery), which is safe but not the fully-optimized
  path the design describes.
- OTEL env vars are wired into every child's env (§10) but not independently probed here
  beyond the confirmed-live ingest URL already in the design doc; verifying a metric
  actually lands on Bes VictoriaMetrics is part of the quota-costing smoke above.
