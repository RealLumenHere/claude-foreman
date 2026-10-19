# wq#4408 — verify-pass on wq#4406 (judgment on wq#4404's CONTRADICT of wq#4401, NanoBot dissection)

PR#2300, commit `dba71a0e` on `research/WQ4351_NANOBOT_DISSECTION.md`. Author:
claude-opus-5 (fworker-wq4406). Verifier: claude-sonnet-5@high (fresh instance,
wq#4408).

## Method

Archetype 8: read PR#2300 cold, then independently re-fetched every
primary-source reference the diff relies on — `pyproject.toml` at both pinned
refs (`a1241ee6`, `909b277e`), `nanobot/providers/registry.py`,
`nanobot/agent/{subagent,memory,loop}.py`, `nanobot/agent/tools/subagent.py`,
the `nanobot/channels/` and `nanobot/providers/` directory listings, PyPI
JSON, and the npm/GitHub existence checks — via `curl` against
`raw.githubusercontent.com`, `api.github.com`, and `pypi.org`, all pinned to
the exact SHAs the diff cites. Not trusted from wq#4406's own report; re-run
from scratch.

## Confirmed accurate (re-checked independently)

- **E4** (pyproject.toml ref mismatch): at `a1241ee6`, file is 117 lines,
  `:117` is `testpaths = ["tests"]`, `grep -c entry` → 0, `:72-73` is
  `[project.scripts]` / `nanobot = "nanobot.cli.commands:app"`. At
  `909b277e`, `:116-117` is `[project.scripts]` /
  `nanobot = "nanobot.cli.entry:main"`, verbatim. Both refs in the patched
  doc text are now correct. Confirmed.
- **`a1241ee6` provider listing**: `nanobot/providers/` contents are exactly
  `__init__.py, azure_openai_provider.py, base.py, custom_provider.py,
  litellm_provider.py, openai_codex_provider.py, registry.py,
  transcription.py` — 4 implementations + 4 infra files, matching the
  patched "adapters vs. infra" framing.
- **LiteLLM dropped on main**: zero case-insensitive `litellm` occurrences in
  `pyproject.toml` at `909b277e`. Confirmed.
- **`dashscope` ProviderSpec**: `registry.py:693-702` at `909b277e` is
  exactly the quoted `ProviderSpec(name="dashscope", ...,
  backend="openai_compat", ...)` block; `qwen` is at `:695`. Confirmed.
- **7 backend values**: `registry.py:71-72` (comment) lists exactly
  `openai_compat | anthropic | azure_openai | openai_codex | xai_grok |
  github_copilot | bedrock` — 7 values, each with exactly one
  `*_provider.py` module in the `909b277e` `providers/` listing. Confirmed.
- **E6 channel count**: `nanobot/channels/` at `909b277e` has exactly 19
  directories, names matching the patched list verbatim (via GitHub contents
  API). Confirmed.
- **E2/E3/SpawnTool-rename/PyPI/npm/GitHub/ClawHub**: all re-checked byte-for-
  byte against the cited lines — `subagent.py:115`, `loop.py:56`,
  `memory.py:21/45/75/114/139-144/151-155`, `subagent.py` (tools)
  `:59` → `class SubagentTool(Tool):`, PyPI summary/author, npm package
  (HTTP 200), GitHub repo (HTTP 200 via HTML; API 403 was rate-limiting, not
  a real 404 — verified by response body), `clawhub/SKILL.md` (HTTP 200).
  All clean, matching wq#4406's report.

## Contradiction found

**The new "39 of 46 ProviderSpec entries ride `openai_compat`" claim (E5 fix,
both in the errata row and in the rewritten §3 superseded-note) is itself
miscounted — the real split is 38 of 46, not 39 of 46.**

`registry.py` at `909b277e` has 47 total `backend="openai_compat"`-style
assignments across the file, but only 46 of those sit inside the actual
catalog — the `PROVIDERS: tuple[ProviderSpec, ...] = (` literal, which opens
at `registry.py:215` and closes at `registry.py:943`. The 47th
`ProviderSpec(` construction, at `registry.py:969-979`, is inside
`create_dynamic_spec()`, a factory function whose own docstring says
*"Create a dynamic ProviderSpec for custom user-defined providers"* — i.e. a
runtime constructor for user-added providers, not a member of the static
46-entry catalog PR#2300 is describing. Its `backend="openai_compat"`
default at `:975` is outside the `215–943` tuple range entirely.

Restricting the count to lines 215–943 only (the actual catalog):

```
$ sed -n '215,943p' registry.py | grep -c '^    ProviderSpec($'
46
$ sed -n '215,943p' registry.py | grep -c 'backend="openai_compat"'
38
```

46 catalog entries, 38 riding `openai_compat` — not 39. PR#2300's "39 of 46"
double-counts `create_dynamic_spec`'s factory default as if it were one of
the 46 catalog members, which is the same category of error (conflating two
structurally different things that happen to look alike) the whole chain
(wq#4401 E1, wq#4404 E5-as-fabrication, wq#4406's own E4/E5 corrections) has
been chasing. This specific number is new in PR#2300 — neither wq#4401 nor
wq#4404 asserted an exact count, so it isn't inherited from an earlier pass;
wq#4406 introduced it in the same patch that corrects the previous round's
miscounts.

The surrounding structural claims (7 backends, 46 catalog entries, DashScope
at `:693-702`/`openai_compat`, `qwen` at `:695`) are all independently
confirmed correct above — only the "39" ride-count is wrong. The doc's
higher-level point (the breadth comes from `openai_compat`-backed
`ProviderSpec`s, not from a wide adapter list) is unaffected; the specific
number is not.

Falsifiable, re-runnable, SHA-pinned probe (outside the population wq#4406
checked — it never counted the catalog boundary against `create_dynamic_spec`):

```
curl -s https://raw.githubusercontent.com/HKUDS/nanobot/909b277e/nanobot/providers/registry.py \
  | sed -n '215,943p' | grep -c 'backend="openai_compat"'
# → 38, not 39
```

## Verdict

**CONTRADICT.** One miscounted primary-source number, newly introduced by
the patch meant to fix exactly this class of defect. Per archetype 8 this
escalates to opus-5@high for judgment: whether to patch "39" → "38" in place
(narrow fix, both rows: the E5 errata row and the §3 superseded-note), or
whether — given this is the third round in a row where a judgment pass
chasing a prior round's identifier error introduces a new one of its own —
a different author strategy (e.g. a scripted count instead of a manually
transcribed one) is warranted for any further numeric claims in this doc.

Escalation ticket: wq#4413 ([judgment] wq#4408 verify-pass on wq#4406
CONTRADICTED — ProviderSpec openai_compat count off by one), model
claude-opus-5, effort high, deps wq4408.
