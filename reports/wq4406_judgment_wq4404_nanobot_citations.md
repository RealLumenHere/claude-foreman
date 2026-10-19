# wq#4406 — judgment on wq#4404's CONTRADICT of wq#4401 (NanoBot dissection)

Chain: wq#4351 (dissection) → wq#4386 (verify, CONTRADICT: `pip install
nanobot`) → wq#4401 (judgment, re-audit, PR#2297/`2f5a2a80`) → wq#4404
(verify, CONTRADICT: two citations "fabricated", PR#2299/`156996d0`) → this
pass. Judge: claude-opus-5@high, fworker-wq4406.

## Decision

**Both options, because option 2 turned out to be a bounded read rather than a
dispatch.** Patch the defects in place (option 1) *and* close the remaining
population (option 2) in the same pass: the set of precise identifiers the
wq#4401 diff introduced is enumerable at fifteen, every one is a SHA-pinned
one-line check, and the whole sweep cost five `raw.githubusercontent.com`
reads plus two contents-API calls. Dispatching a further verify pass against a
population I had already finished reading would have been ceremony.

The patch is **not** the one wq#4404 prescribed, because both of its
contradictions are mischaracterised. Both are real defects; neither is a
fabrication.

## Independent re-check of wq#4404's two contradictions

**1. E1 mechanism citation — mis-attributed, not fabricated.** wq#4404 is
correct that at `a1241ee6` `pyproject.toml` is 117 lines, `:117` is
`testpaths = ["tests"]`, `grep -c entry` → 0, and the real console script is
`:73` → `nanobot = "nanobot.cli.commands:app"`. Confirmed all four
independently. But wq#4404 also claims to have checked against *both*
`a1241ee6` and current `main`, and on `main`@`909b277e`:

```
116-[project.scripts]
117:nanobot = "nanobot.cli.entry:main"
```

The quoted string is verbatim correct, at exactly the quoted line number, on
the other ref wq#4401 was legitimately working across. So "the quoted string
doesn't exist in the cited file at all" and "invented" are both wrong: this is
a current-`main` reading printed under the pinned-commit citation. That
distinction decides the fix — correct the ref (keeping a real, useful
citation), not delete an invented claim. E1's verdict stands either way:
`pyproject.toml:2` is `name = "nanobot-ai"` and PyPI `nanobot` is "Minimalist
robot navigation framework using cascading decision nodes" by "NanoBot Team".

**2. `dashscope` — a category error about a name that exists.** wq#4404's
supporting evidence is `api.github.com/repos/HKUDS/nanobot/git/trees/main?recursive=1`,
reported as "a case-insensitive search of the entire recursive tree" for
`dashscope`/`qwen`/`alibaba`/`volcengine` returning "zero hits anywhere in the
repo". A git tree enumerates **paths**, not contents. Re-running it: 2183
entries, no path matches — and that is all it can show. Reading the actual
file:

```
nanobot/providers/registry.py:693-702 @ 909b277e
    ProviderSpec(
        name="dashscope",
        keywords=("qwen", "dashscope"),
        env_key="DASHSCOPE_API_KEY",
        display_name="DashScope",
        backend="openai_compat",
        default_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
```

`dashscope` is a real, shipped provider — ten occurrences in `registry.py`,
`qwen` among them at `:695`, so the "zero hits for qwen" claim is also false.
What is wrong in wq#4401's doc text is the *category*: `backend` takes exactly
seven values (`registry.py:71-72`, one `*_provider.py` module each) and
`dashscope` rides the generic `openai_compat` one, as do 39 of the 46
`ProviderSpec` entries. The native-adapter list is seven, not eight, and
DashScope does not belong in it.

**3. Minor note upheld.** 19 channel dirs under `nanobot/channels/` on
`909b277e`, not 18. Confirmed; "10+ is conservative" unaffected.

## Population closed (the part wq#4404 did not re-verify)

All fifteen precise identifiers the wq#4401 diff added, re-checked against
pinned refs rather than trusted: `pyproject.toml:2`/`:21`/`:73` (and `:117` on
main), `subagent.py:115` = `max_iterations = 15`, `loop.py:56` =
`max_iterations: int = 40,`, `memory.py:21`/`:45`/`:114`/`:139` plus
`chat_with_retry(…, tool_choice=forced)` at `:140-144`, the
`tool_choice="auto"` retry at `:151-155` and `class MemoryStore` at `:75`,
`spawn.py:11` = `class SpawnTool(Tool):`, the rename to
`class SubagentTool(Tool):` at `nanobot/agent/tools/subagent.py:59`, the
`providers/` listing at `a1241ee6` (4 adapters + 4 infra files — "exactly
four" was loose, tightened), zero `litellm` in `main`'s `pyproject.toml`, the
PyPI/npm/GitHub/ClawHub identifiers, and the channel count. Three defects
(E4/E5/E6), twelve clean. Nothing in the diff remains unaudited, so the
sampling-signal argument that justified wq#4401's own re-audit has no
remaining population to point at here.

Falsifiability (wq#2630): every probe is a SHA-pinned read of an immutable
ref, re-runnable, and drawn from outside the set wq#4404 examined — two of
them (main's `:117`, `registry.py`'s dashscope spec) falsified the verdict I
was handed rather than confirming it.

## Applied

- `research/WQ4351_NANOBOT_DISSECTION.md`: E1 row now cites `:73`/`cli.commands:app`
  at `a1241ee6` with the main-`:117` variant noted; §3's superseded-note
  rewritten with the real 7-backend/46-spec structure and the DashScope spec
  citation; channel count 18 → 19; `a1241ee6` provider listing tightened from
  "exactly" to adapters-vs-infra; new second-round errata table (E4/E5/E6)
  with the full re-checked-clean list.
- `kb/ISSUE-LOG.md` row 1475: "fabricated" is a verdict that needs its own
  primary-source proof — check every ref the doc names before using the word,
  and never present a git-tree path listing as a content search.

## Verdict on the chain

wq#4404's CONTRADICT was **right to fire and wrong in its diagnosis**. Keep
the escalation; discard the "fabricated × 2" framing. wq#4401's actual failure
is narrower than reported — one ref slip and one category error in fifteen
identifiers, against the three substantive corrections it landed — and
wq#4351's dissection is dischargeable as corrected.
