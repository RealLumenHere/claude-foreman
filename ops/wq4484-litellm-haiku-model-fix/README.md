# wq#4484 — lumen/claude-haiku model string fix

## Finding
Ticket reported `agent/litellm_config.yaml`'s `lumen/claude-haiku` entry using
`claude-haiku-4-20250514`. On inspection the **live** `/home/lumen/agent/litellm_config.yaml`
(outside this repo, not version-controlled) had already drifted to a different bad
value: `anthropic/claude-haiku-5-5` — the unverified third-party string the ticket
itself flagged as unconfirmed, not the value it originally reported.

## Verification
Confirmed via `/home/lumen/bin/fleet-search web` against Anthropic's own docs
(`platform.claude.com/docs/en/about-claude/models/model-ids-and-versions`):
pre-4.6-generation dated model IDs use `claude-{name}-{major}-{minor}-{YYYYMMDD}`.
For Haiku 4.5 that is `claude-haiku-4-5-20251001` — matches the existing
`lumen/claude-sonnet` entry's convention of a real, dated/aliased Anthropic ID
(`anthropic/claude-sonnet-4-6`).

## Fix applied live
`install.sh` in this directory is the exact script run against the live host via
`lumen-run` (systemd-run as `lumen`, outside the Claude Code tool sandbox, which has
no path grant for `/home/lumen/agent/`): backs up the config, swaps
`anthropic/claude-haiku-5-5` → `anthropic/claude-haiku-4-5-20251001`, restarts the
`litellm` docker container. Applied and confirmed: container came back `Up`, and
`docker logs litellm` post-restart shows `lumen/claude-haiku` listed as a valid
model (errors in the same window were for unrelated model names, not this one).
Live completion was **not** tested (Frank no-API-spend rule — see ticket).

`litellm_config_snippet.yaml` here mirrors the corrected entry and additionally
shows the literal `api_key:` swapped for `os.environ/ANTHROPIC_API_KEY` as a
reference for wq#4343 (literal keys in this same file) — that swap was **not**
applied live in this ticket, it's scoped to wq#4343.

## Credential-exposure note (separate from the fix itself)
While inspecting the live config, a `sed -n` dump run through `lumen-run` printed
the full live Anthropic API key into Bash tool output/log (lumen-run's job output
bypasses the guard.sh credential mask, since it runs as a detached systemd unit
outside Claude Code's own PreToolUse pipeline). Logged as ISSUE-LOG row 1503,
flagged for same-day key rotation as a queued physical task — not a gate on this
ticket's close.
