# wq#4883 — Claude plugin persistence RCA (blerbz inference-* plugins)

Date: 2026-11-22. Host examined: Lumen (read-only). Thor/Maksoor: not reachable
from this worker (`fleet-exec run thor|maksoor` → `DENY [grant:grant_missing_env]`).

## Verdict

**Nothing disappeared. The blerbz plugins were never installed in the sense
Claude Code uses — they were copied to disk and never registered, so Claude
Code has never loaded them.** The ticket premise "`~/.claude/plugins/` empty"
is also false on Lumen: the directory is populated and the blerbz copy is
intact with its original mtimes.

Do not "reinstall" by copying again — that reproduces the same non-install.

## Evidence (Lumen)

| Check | Result |
|---|---|
| `~/.claude/plugins/blerbz/{inference-confidenz,inference-continuez,inference-planz}` | present, all mtimes `Oct 31 20:42` (the orch-12 copy), untouched since |
| `~/.claude/plugins/installed_plugins.json` | **absent** — no marketplace install was ever recorded |
| `~/.claude/plugins/known_marketplaces.json` | only `claude-plugins-official` (no `local`/blerbz marketplace) |
| `enabledPlugins` in `~/.claude/settings.json` and both `.bak` copies (2026-11-05, 2026-11-18) | **absent** (`grep -c plugin` = 0) |
| `pluginUsage` in `~/.claude.json` (CC's record of every plugin it loaded) | 7 entries, all `@builtin` or `@synced` (`github@synced`); **no blerbz entry ever** |
| `~/.claude/plugins/synced/…/manifest.json` | account-synced set = GitHub MCP only (`installationPreference: available`) |
| `.trash/` | holds only an old copy of the synced GitHub plugin; no blerbz |
| repo root of blerbz-plugins (`../QUICKSTART.md`, `.claude-plugin/marketplace.json`) | not copied — only the three plugin subdirs exist |

Reproducible: `python3 scripts/claude_plugin_audit.py` → exit 1, three
orphans, `installed_plugins_json: false`, `enabledPlugins: {}`,
`ever_loaded` has no blerbz key.

## Diagnosis by ticket question

1. **Claude Code version / sign-in.** 2.1.329, npm global install
   (`~/.npm-global/bin/claude`, `installMethod: global`). Signed in via OAuth,
   `organizationType: claude_max`, `billingType: stripe_subscription`, plan
   "Ultra", role admin. Account sync is working (GitHub plugin arrives
   `@synced`), so sign-in is not the cause.
2. **Account-synced vs local.** blerbz is neither. Account-synced plugins
   live under `plugins/synced/<org>_<user>/` and come from the Anthropic
   plugin directory; blerbz is a third-party GitHub repo. A local install
   needs the upstream QUICKSTART steps 3–5 (record in
   `installed_plugins.json`, a `local` marketplace in
   `known_marketplaces.json`, `enabledPlugins["inference-continuez@local"]`)
   or `claude plugin install <name>@<marketplace>`. Only the file copy
   (≈steps 1–2) was done. Docs: code.claude.com/docs/en/plugins/install
   ("User scope … The entry goes in enabledPlugins in ~/.claude/settings.json")
   and …/plugins/troubleshooting (`claude plugin list` is the authority).
3. **Hooks registered?** No — an unregistered plugin's hooks are never read.
   Independently, even if registered:
   - `inference-continuez/hooks/hooks.json` is `{}` — the auto-continue
     plugin ships **no hooks** although its README:216–224 documents
     `Stop` + `PermissionRequest` hooks. Registered, it would do nothing.
   - `inference-planz/hooks/hooks.json` is `{}`; its `hooks.json.disabled`
     template uses `"matcher": {"tool_name": "Task"}` (object) where the
     schema expects a string — would fail validation if re-enabled.
   - `inference-confidenz/hooks/hooks.json` is well-formed (`Stop`,
     `SubagentStop` → `confidence-hook.sh`).
4. **Version compatibility.** Not the cause: nothing was loaded to be
   incompatible. The planz matcher shape above is the only schema defect.

## Why the audit said "0 plugins"

The "0 plugins / dir empty" claim originates in the orch-12 external analysis
(`foreman/docs/mcp-acquisition-analysis/orch12-original-analysis.md:208-219`),
which itself notes an empty-dir finding is not conclusive. Any audit that
asks Claude Code (`/plugin`, `claude plugin list`, `pluginUsage`) correctly
reports zero third-party plugins; an `ls` of the dir would not have shown it
empty on Lumen.

## Risk note before anyone registers these

`inference-continuez` is an auto-approver: `scripts/permission-evaluator.py`
(PermissionRequest hook, `permissionDecision: "allow"` at :190) scores
`Read/Glob/Grep` at 100 and Bash matching `^(ls|pwd|echo|cat|head|tail|wc|grep|find)\s`
at 85 (:27-40), above the 70 threshold in the plugin's `settings.json`. On fleet hosts that
would sit beside the guard.sh deny-list and per-command allowlist, and `cat`
auto-approval bypasses the mask-before-print instinct. Recommendation: do not
enable continuez on Lumen or any foreman/worker host; if wanted at all, scope
it per-session with `claude --plugin-dir` on Thor interactively.

## Remaining

- Thor/Maksoor not examined (fleet-exec grant missing for this worker). Same
  check there: `python3 scripts/claude_plugin_audit.py` (copy the script) or
  `claude plugin list`.
- Allowlist gaps hit: `claude --version`, `claude plugin list` (read-only;
  version read from package.json instead).
