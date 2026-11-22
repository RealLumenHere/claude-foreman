# wq#5331 — verify-pass of wq#4883 (PR #2782, commit 3e3cc1fc)

Verdict: **CONFIRMED.** The RCA holds on Lumen: the blerbz plugins never
disappeared. They were copied to `~/.claude/plugins/blerbz/` and never
registered, so Claude Code never loaded them. The audit script gets the result
right on the live host. It has four false-negative edges (below), all outside
the case wq#4883 had to settle. None of them changes the verdict, so there is
nothing to escalate.

## Re-checked claims (Lumen, 2026-11-22, read-only)

| RCA claim | Re-check | Result |
|---|---|---|
| blerbz dirs present, mtimes Oct 31 20:42 | `ls -la --time-style=long-iso ~/.claude/plugins/blerbz/*` | all three dirs plus their contents at `2026-10-31 20:42` ✔ |
| no `installed_plugins.json` | `ls ~/.claude/plugins/` | absent ✔ |
| known_marketplaces = official only | parsed JSON | `["claude-plugins-official"]` ✔ |
| no `enabledPlugins` in settings + both .bak | `grep -c -i plugin` | 0 in settings.json, both baks, **and `remote-settings.json`** (not checked by the author; I added it, `{}` keys) ✔ |
| `pluginUsage` has no blerbz entry | parsed `~/.claude.json` (key names only) | 6 `@builtin` + `github@synced`, which is the 7 entries the RCA reports ✔ |
| `.trash` = old synced GitHub copy only | `find .trash -maxdepth 4` | synced-bucket manifest + GitHub plugin only ✔ |
| CC 2.1.329, npm global, OAuth claude_max/stripe/admin | package.json, `installMethod`, oauthAccount subset | all match ✔ |
| continuez + planz `hooks.json` = `{}` | `cat` | both `{}` ✔ |
| planz disabled template object matcher | `hooks.json.disabled:6,20` | `"matcher": {"tool_name": "Task"}` ✔ |
| confidenz hooks Stop+SubagentStop | `cat` | ✔ |
| continuez auto-approver: Read/Glob 100, `^(ls|…|cat|…)\s` 85, allow at :190, threshold 70 | grep evaluator.py:28,29,40,190; settings.json:2 | ✔ |
| orch-12 "empty dir not conclusive" at analysis :208-219 | grep | :208 heading, :212 caveat ✔ |
| ISSUE-LOG row 1745 | grep | present (ISSUE-LOG.md:1894) ✔ |

Untracked by the RCA but harmless: `~/.claude/plugins/store/` (created
2026-11-22 12:15) holds one `cc-plugin-agents-md_builtin-*.json`, which is
builtin-plugin state. The script is right to skip it.

Prior-art search (`fleet-search web "claude code installed_plugins.json
enabledPlugins local plugin not loading"`) agrees with the RCA's
registration model: the results discuss enabledPlugins/installed_plugins.json
gating (e.g. anthropics/claude-code#27247, #51118). The GitHub search for the
blerbz upstream QUICKSTART returned `401 no usable token`, so the claim about
upstream QUICKSTART steps 3–5 is **unverified** here. No conclusion depends
on it.

## Probe

- `python3 -m pytest -q tests/test_claude_plugin_audit.py` → `3 passed`.
- Live: `python3 scripts/claude_plugin_audit.py` → rc=1, orphans =
  inference-{confidenz,continuez,planz}. Every one has `enabled_key_present`
  false and `ever_loaded` false. confidenz has hooks `ok` [Stop, SubagentStop].
  continuez and planz are `empty`. This matches the RCA's "Reproducible" line.
- The repo test `test_registered_plugin_is_not_orphan` can fail: a registered
  plugin must come back with rc 0.

## Adversarial edges (`/tmp/fworker-wq5331/probe_edges.py`, run against rev 3e3cc1fc)

| # | Input | Result | Severity |
|---|---|---|---|
| A | plugin dir with `hooks/hooks.json` but no `.claude-plugin/plugin.json` (the manifest is optional in CC) | **not reported** (false negative) | low. All blerbz dirs have manifests |
| B | orphan copy `blerbz/foo` + `installed_plugins.json` with `foo@other` at another path | **not reported**. `audit()` at line 96 also matches on bare name, so any same-name registration hides the copy | low |
| C | `hooks.json` with `"hooks": []`, or event→dict groups | no crash. List reads as `empty`; dict groups give `ok` with no problem flagged | cosmetic |
| D | garbage `settings.json`, `.claude.json` = `[]` | no crash, orphan still found | ok |
| E | v1 `installed_plugins.json` (dict record) | registers correctly | ok |
| F | plugin 4 levels under a top dir | not reported (`max_depth=3` by design) | low |

Doc nit: the RCA cites continuez `README:216–224` for the Stop/PermissionRequest
hooks. The Hook Events table is actually at README.md:210–216, and :218–224 is
the output format.

## Not verifiable from this lane

Thor/Maksoor are still unexamined. `fleet-exec run thor …` gives
`DENY [grant:no_ssh_run]` for this worker too. The RCA says so itself, so
this is not a contradiction. Running `claude plugin list` (the authoritative
check) is not on the worker allowlist: `/home/lumen/.npm-global/bin/claude
plugin list` was refused.
