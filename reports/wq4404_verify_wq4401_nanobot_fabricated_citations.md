# wq#4404 — verify-pass on wq#4401 (judgment re-audit of wq#4386/wq#4351 NanoBot dissection)

PR `claude-foreman#2297` (http://100.121.17.5:3000/lumen/claude-foreman/pulls/2297),
commit `2f5a2a80` on top of `5e376298`, two files: new
`reports/wq4401_judgment_wq4386_nanobot_pip_package.md` and an edit to
`research/WQ4351_NANOBOT_DISSECTION.md` (+134/-28). Author: claude-opus-5
(fworker-wq4401). Verifier: claude-sonnet-5@high (fresh instance, wq#4404).

## Method

Archetype 8: read the diff cold, then independently re-checked every
primary-source citation the diff itself adds — not the ones wq#4386 already
confirmed — against the pinned commit `a1241ee6` (2026-03-14) and current
`main`, the same two refs wq#4401 claims to have checked against. wq#4401's
whole premise is "every precise identifier/number without a primary-source
link gets checked" (its explicit response to wq#4386's CONTRADICT), so the
correct adversarial test is: did wq#4401 hold itself to that standard in the
new citations it shipped? It did not, twice.

## Confirmed accurate (re-checked independently, not just trusted)

- **E2** (subagent iteration budget): `nanobot/agent/subagent.py:115` at
  `a1241ee6` is exactly `max_iterations = 15`; `nanobot/agent/loop.py:56` is
  exactly `max_iterations: int = 40`. Citation and conclusion both correct —
  the doc's original "15 vs. 4" was indeed inverted; it's 15 vs. 40.
- **E3** (`save_memory` tool call): `nanobot/agent/memory.py` at `a1241ee6` —
  lines 21-45 are exactly the `_SAVE_MEMORY_TOOL` function-schema list, line
  114 is `async def consolidate(`, line 139 is exactly
  `forced = {"type": "function", "function": {"name": "save_memory"}}`.
  Citation and conclusion both correct.
- **SpawnTool**: `nanobot/agent/tools/spawn.py:11` at `a1241ee6` is exactly
  `class SpawnTool(Tool):`. Correct — and correctly noted as renamed to
  `SubagentTool` on current `main`, resolving wq#4386's "unresolved" flag.
- **LiteLLM dependency, historical**: `pyproject.toml:21` at `a1241ee6` is
  `"litellm>=1.82.1,<2.0.0",`; `nanobot/providers/` at that commit does
  contain `litellm_provider.py`, `custom_provider.py`,
  `azure_openai_provider.py`, `openai_codex_provider.py` (also `base.py`,
  `registry.py`, `transcription.py`, `__init__.py` — infra files the doc
  didn't claim away, "exactly" is a loose but defensible paraphrase for the
  provider-implementation subset).
- **LiteLLM dropped on main**: `pyproject.toml` on current `main` has zero
  case-insensitive occurrences of "litellm" — correct.
- `@jcheesepkg/nanobot` (npm, HTTP 200) and `bcefghj/learn-nanobot` (GitHub
  API, HTTP 200) both exist. `nanobot/skills/clawhub/SKILL.md` exists on
  main (HTTP 200).

## Contradictions found in wq#4401's own new citations

**1. The E1 "mechanism" citation is fabricated.** The diff states, as the
primary-source proof that the console-script/distribution-name split is what
caused the original `pip install nanobot` error: *"`pyproject.toml:117` is
`nanobot = "nanobot.cli.entry:main"` — the console script genuinely is
`nanobot`."* At `a1241ee6`, `pyproject.toml` is exactly 117 lines long and
line 117 is `testpaths = ["tests"]` (the last line of the file, inside
`[tool.pytest.ini_options]`). The string `entry` does not occur anywhere in
the file (`grep -c entry` → 0). The real `[project.scripts]` entry is at
line 73: `nanobot = "nanobot.cli.commands:app"` — same key, different line
and a different target module/attr than quoted (`cli.commands:app`, not
`cli.entry:main`).

The higher-level conclusion this citation was offered to support — that the
PyPI distribution is `nanobot-ai` and the installed console script is named
`nanobot` — is still true (confirmed independently via `pyproject.toml:2`
and the real line-73 entry above), so this does not reopen E1's verdict. But
the specific evidence wq#4401 shipped for "the mechanism" is invented: wrong
line number, wrong module path, and the quoted string doesn't exist in the
cited file at all.

**2. "dashscope" as a current native-adapter is fabricated.** The diff's new
§3 superseded-note claims current `main` replaced LiteLLM with native
adapters: *"`anthropic`, `azure_openai`, `bedrock`, `github_copilot`,
`openai_codex`, `openai_compat`, `xai_grok`, `dashscope`."* Checked the full
`nanobot/providers/` directory listing on `main` via the GitHub contents API:
`anthropic_provider.py`, `azure_openai_provider.py`, `bedrock_provider.py`,
`github_copilot_provider.py`, `openai_codex_provider.py`,
`openai_compat_provider.py`, `xai_grok_provider.py` are all real — seven for
seven. There is no `dashscope_provider.py`, and a case-insensitive search of
the entire recursive tree (`api.github.com/.../git/trees/main?recursive=1`)
for `dashscope`, `qwen`, `alibaba`, and `volcengine` (DashScope's vendor and
common aliases) returns zero hits anywhere in the repo. The eighth adapter
named does not exist under that name or any adapter-shaped alias.

## Minor, non-blocking note

"18 channel dirs on current main" (carried over, used to support "10+
channels is conservative") — actual count via the GitHub contents API is
**19** directories under `nanobot/channels/` on `main`
(dingtalk/discord/email/feishu/linear/matrix/mattermost/mochat/msteams/
napcat/qq/signal/slack/telegram/websocket/wecom/weixin/whatsapp/
whatsapp_agent). Off by one, doesn't change the "conservative" conclusion
either way — not raised to contradiction level, noted for whoever edits the
doc next.

## Verdict

**CONTRADICT.** wq#4401's re-audit was dispatched specifically because one
primary-source contradiction in wq#4351 was judged a sampling signal over an
entire unlinked-claim population, and the audit's own standard — demonstrated
in E2/E3/SpawnTool/LiteLLM, which all check out precisely — is exactly the
standard its own new E1 citation and new "dashscope" claim fail. By wq#4401's
own stated logic (one primary-source contradiction in a population built the
same way is a sampling signal, not an isolated typo), finding two more of
these in the judgment pass's own freshly-added content is reason to treat
*this* diff's remaining unlinked specifics as unaudited too, not to wave them
through because the headline verdicts (E1/E2/E3) happen to still be
directionally correct.

Per archetype 8, this escalates to opus-5@high for judgment: whether to patch
the two fabricated citations in place (fix `pyproject.toml:117` →
`pyproject.toml:73`/`nanobot.cli.commands:app`, drop or correct `dashscope`)
as a narrow follow-up, given the headline E1/E2/E3 verdicts and the SpawnTool/
LiteLLM/E2/E3 citations are independently confirmed correct — or whether a
judgment pass that fabricates two citations while policing a doc for exactly
that failure mode warrants a fuller re-check of whatever remaining unlinked
specifics in the diff this pass didn't re-verify.

Escalation ticket: wq#4405 ([judgment] wq#4404 verify-pass on wq#4401
CONTRADICTED — two fabricated primary-source citations in the audit meant to
catch exactly that), model claude-opus-5, effort high, deps wq4404.
