# wq#978 — instincts-render live test broken by tiered instincts.yaml

## Root cause

`tests/test_instincts_render.py` and `instincts-render` are **not** in this repo (ccc-repo /
foreman). They live in a separate git repository checked out at `/home/lumen/bin`, with its own
history (`HEAD 057692f "fix(context): render whole instincts rules by tier, not head -c 4096"`,
wq#958) unrelated to this repo's `harness/instincts_render.py` (wq#959). This session's worktree
sandbox has no read/write access outside `/home/lumen/worktrees/wq978-a1` and
`/home/lumen/agents/work-queue`, so `/home/lumen/bin` cannot be edited from here — filed as a
build-lane ticket, but the fix site needs ops-lane-style direct filesystem access (the pattern
wq#960/wq#975 used).

`/home/lumen/bin/tests/test_instincts_render.py:180` does:

```python
kept = rules_of(done.stdout)                                            # list[str] (rule text)
assert kept == yaml.safe_load(open(LIVE_FILE, encoding="utf-8"))["rules"]  # list[dict] since wq#959
```

Since foreman 7d8e59f (wq#959) retiled `/home/lumen/instincts.yaml` to schema 2, `rules:` holds
37 dicts (`id`/`tier`/`text`/`added`/`source`/`supersedes`), never plain strings. `instincts-render`
itself already handles this correctly — `load_rules()` extracts `raw["text"]` for a dict rule
(see `/home/lumen/bin/instincts-render:103-106`) — so `kept` (parsed from the renderer's stdout)
is a list of rule-text strings. Comparing that to the raw dict list can never hold; every rule
tripped it, not just the tiered ones. 17/18 tests pass; only this live-data test fails.

## Fix

`ops/wq978-instincts-render-live-test-fix/fix.patch` — apply from inside `/home/lumen/bin` with
`git apply ops/wq978-instincts-render-live-test-fix/fix.patch` (copy it in first) or `patch -p1`.
Extracts `r["text"]` for dict rules, same as the renderer itself, before comparing:

```python
live_rules = yaml.safe_load(open(LIVE_FILE, encoding="utf-8"))["rules"]
expected = [r["text"] if isinstance(r, dict) else r for r in live_rules]
assert kept == expected
```

## Decision: should the renderer surface tier?

No renderer change. `instincts-render`'s `render()` already annotates tier for readers: when the
selected rules span more than one tier it prefixes each group with a
`# --- tier N — <label> ---` comment (`/home/lumen/bin/instincts-render:132-139`), which is what
`--file /home/lumen/instincts.yaml` (all tiers, no `--max-tier`) produces on the live corpus.
The machine-parseable `rules:` YAML list stays flat text on purpose — every other test in this
file (`rules_of()`) already assumes a plain string list, and downstream consumers only need the
text. Tier is presentation-only metadata here, not structural output.

## Not done — needs ops-lane fs access, out of reach for this session

Applying `fix.patch` to the live `/home/lumen/bin` checkout and landing it through that repo's own
PR flow. Filed as wq#981 (ops lane) to carry this the rest of the way.

Separately noticed but out of scope here: `harness/fworker.py` imports `instincts_render`
(wq#959's module) directly for prompt injection and no longer shells out to
`/home/lumen/bin/instincts-render`; `/home/lumen/instincts.yaml`'s own header now names
`harness/instincts_render.py` as canonical. Whether the old wq#958 `instincts-render` binary +
test are fully dead (like wq#961's stress7 tripwire) or still invoked elsewhere (e.g. a `--core`
session path) wasn't verified from this sandbox — worth a follow-up audit before deciding to
retire rather than patch it.
