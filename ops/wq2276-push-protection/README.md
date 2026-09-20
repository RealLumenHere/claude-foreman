# wq#2276 — provider-side push protection (token-engine T7)

Design: `research/TOKEN_ENGINE_DESIGN.md` §6 — a token in a commit should be
refused **at the forge**, not just caught by local sweeps.

## Tool

```
python3 ops/wq2276-push-protection/push_protection.py scan          # read-only
python3 ops/wq2276-push-protection/push_protection.py scan --fix    # enable where permitted
python3 ops/wq2276-push-protection/push_protection.py scan --json   # machine-readable
```

Inventory = `agent-scope.conf` (wq#546 registry) + live enumeration of the
RealLumenHere account (private repos included). Token resolution follows
`lumen-github-identity.sh` (identity-map override, then per-owner default),
with a fallback across owner tokens because a settings PATCH needs **admin**,
not the authoring identity. Tokens are never printed (instinct #4).
NEVER-class repos are read but never mutated.

## State as of 2026-09-20 (`state-20260920.json` is the raw snapshot)

| repo | state | why |
|---|---|---|
| RealLumenHere/interview-prep | **protected** | ss+pp already enabled |
| RealLumenHere/simpill-utils | **protected** | ss+pp already enabled |
| RealLumenHere/questfree | forge-unavailable | private repo, no GHAS on the free plan: `PATCH 422 Secret scanning is not available` — owner plan decision, or accept local-layer coverage (T1/T3/T5/T6 + hourly sweep) |
| frankstupak/* (10 repos: lumen-plays-pokemon, claude-command-center, .github, acp-llm-cli, agent-orchestrator, interview-prep, lumen-blog, lumen-content, simpill-utils, supergateway) | **owner-queued** | `PATCH 403 Resource not accessible by personal access token` — the github-frank PAT cannot change security settings; needs Frank in the repo Settings UI (or a PAT with the Administration permission) |
| frankstupak/winforge, frankstupak/rom-transfer-tmp | owner-queued | NEVER-class: agents may not write these repos at all (wq#546) |
| lumen/* (8 Gitea repos: claude-foreman, lumen-bin, lumen-messenger, mailbot, mission-control, minerva, questfree, fleet-inventory) | forge-unsupported | Gitea has no secret scanning / push protection; follow-up = server-side pre-receive hook on Anubis |

Owner steps are **queued as wq tickets, not gates** (instinct #3): the local
sweep timer (part a of this ticket) plus T1/T3/T5/T6 cover the window until
Frank flips the GitHub toggles.

## Re-audit

Run `scan` any time; exit 1 only on `error`/`no-token` rows. Re-run `--fix`
after Frank widens the PAT — it is idempotent (already-protected repos are
skipped).
