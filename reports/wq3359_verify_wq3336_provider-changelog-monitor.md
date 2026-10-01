# wq#3359 verify: wq#3336 provider-watch (CF/Tailscale/Ollama/LiteLLM/...)

Verdict: **CONFIRM**

PR: `http://100.121.17.5:3000/lumen/claude-foreman/pulls/1559`. Gitea's unauthenticated
API/web returned 404 on this repo/PR (private repo, no token available in this sandbox —
not worked around, see "What I could not check" below). Verified instead via the local
`foreman` clone, which already had the branch history fetched: three commits reachable
from `main` (`b54f2e2d` feat, `755f2d21` feat install-verify, `1331665a` fix — missing
Anthropic provider), all already merged to `main` as of this ticket. Diff covers
`ops/wq3336-provider-watch/{DESIGN.md,install.sh,provider-watch.{service,timer},
provider_watch.py,test_provider_watch.py}` — 518-line script, 264-line test file.

## What wq#3336 claimed

Generalizes wq#3335's Cloudflare-only `cf-changelog-watch` into one script covering
12 providers (Cloudflare — present but excluded from the default unattended run — plus
Tailscale, Ollama, LiteLLM, Anthropic, Cerebras, DeepSeek, SambaNova, Mistral,
OpenRouter, Langfuse, PiKVM) across four source types (rss, npm/pypi, github_releases,
sitemap). Carries over wq#3335's dedup/first-run-baseline/error-isolation design, and
claims a fix for an inherited bug: `first_run` was gated on `os.path.exists(state_path)`,
so a corrupt-but-present state file would skip the baseline step and flood one ticket
per historical match. DESIGN.md documents every source URL as live-verified 2026-10-01
and names PiKVM/kvmd-armbian (the ticket's literal ask) as nonexistent, substituting
pikvm/kvmd with a cited reason.

## What I checked

**Test suite** — ran the shipped suite unmodified:
```
$ python3 -m pytest ops/wq3336-provider-watch/test_provider_watch.py -q
......................                                                   [100%]
22 passed in 0.19s
```

**Regression test is real, not vacuous** — copied `provider_watch.py` to scratch,
reverted just the `first_run` line to the pre-fix shape
(`first_run = not os.path.exists(state_path)`, probed before `load_state`), reran only
`test_run_corrupt_state_file_rebaselines_instead_of_reflooding` against the reverted
copy:
```
FAILED test_pw_buggy.py::test_run_corrupt_state_file_rebaselines_instead_of_reflooding
assert [1] == []
provider-watch: 1 match(es), 1 new, 0/1 source(s) errored
```
Fails against the old logic, passes against shipped code — the test pins the claimed
fix, matching kb/ISSUE-LOG.md row 1146. Scratch files removed after the check.

**Live probe against all 14 real sources** (the ticket's own acceptance bar — "run its
probe"), not a mocked fixture:
```
$ python3 provider_watch.py run --providers tailscale,ollama,litellm,anthropic,cerebras,\
  deepseek,sambanova,mistral,openrouter,langfuse,pikvm --state /tmp/.../probe-state.json --dry-run
provider-watch: [dry-run] first run — would seed 349 existing match(es) as baseline, file 0
```
Second pass with a seeded-but-empty `"seen": {}` state (forces the non-first-run path
against the same live sources, falsifiable both ways — an all-sources network failure
would read `0 match(es)` here instead of findings):
```
provider-watch: 349 match(es), 349 new, 0/14 source(s) errored
```
0/14 source errors confirms DESIGN.md's "verified live" claims hold now, not just at
authoring time — including the specific substitutions it defends (Mistral `/rss.xml`
not `/news/rss.xml`; OpenRouter `/docs/changelog/rss.xml`; Cerebras/DeepSeek sitemap
fallback with no native RSS; PiKVM `pikvm/kvmd` not the nonexistent `kvmd-armbian`).

**Ticket-filing path doesn't silently break** — `file_ticket()` calls `wq add --lane
research --pri p3 ...` with no `--liveness`/`--frank-gated`. Read
`harness/liveness_clear.py:creation_check` (the wq#1676 mandatory on-ramp gate a naive
reading might expect to refuse an untagged add): it only enforces the `Liveness:`
requirement when `lane` is a human lane or `frank_gated` is set
(`if not (human or frank_gated): return True, "NOT_GATED", ""`). `research` is not a
human lane (confirmed against `bin/wq`'s own lane-mapping help text), so provider-watch's
auto-filed tickets hit `NOT_GATED` and file cleanly — same shape `cf-changelog-watch`
(wq#3335) already uses in production. Also confirmed `bin/wq:1616`'s actual add-success
output (`f"added task {rowid} [...]"`) matches the `r"added task (\d+)"` regex
`file_ticket()` parses for the ticket-id backref.

**Deploy-state claims** — DESIGN.md's "what's deployed vs. privileged follow-up" table
says `/home/lumen/bin/provider-watch` is installed (byte-identical copy) but the systemd
unit/timer are not (requires root, out of worker-sandbox reach). Verified both halves:
`diff` shows the deployed binary byte-identical to the worktree source; `systemctl
is-enabled`/`is-active provider-watch.timer` both report not-found/inactive. Matches the
PR's own disclosure exactly — not a gap it hid.

**`install.sh --verify` contract** — present (`vfile`/`vunit_file`/`venabled`/`vactive`
calls match `ops/_verify.sh`'s documented contract). Could not execute it directly
(`bash ops/.../install.sh` isn't on this worker's per-command allowlist — only `bash
scripts/*` is; naming the exact refused shape per the ticket's own rules rather than
working around it). Hand-walked the same four checks it would run (`vfile` via `diff`,
`venabled`/`vactive` via `systemctl`, above) — all consistent with what `--verify` would
report (OK on the binary, MISS on the two units — the documented, undeployed-by-design
gap, not a contract bug).

## Minor finding (non-blocking, not a contradiction)

`provider-watch.service`'s `Description=` line lists providers by name and was not
updated when the `1331665a` fix commit added Anthropic to `DEFAULT_PROVIDERS` —
`git show 1331665a --stat` touched only `DESIGN.md`, `provider_watch.py`,
`test_provider_watch.py`, not the `.service` file. Purely cosmetic: `ExecStart=
/home/lumen/bin/provider-watch run` has no `--providers` flag, so it runs
`DEFAULT_PROVIDERS` (which does include anthropic) regardless of what the unit's
human-readable description string says. Not worth a contradiction/escalation; noting
for whoever next touches this bundle.

## What I could not check

- Could not reach the actual Gitea PR #1559 pages/diff (repo private, no credential
  available to this sandbox's curl — `git fetch origin` itself worked via the
  pre-configured credential helper, so verification proceeded against the merged
  commits in the local clone instead, which is the artifact that actually ships).
- Did not exercise a real (non-dry-run) `wq add` call end-to-end to avoid filing noise
  tickets against the live board; instead verified the gate logic and output-format
  contract by reading `bin/wq` and `harness/liveness_clear.py` directly (above).

## Action taken

Confirmed on wq#3336 via `wq note`. No fix needed — verify-only per ticket instruction.
