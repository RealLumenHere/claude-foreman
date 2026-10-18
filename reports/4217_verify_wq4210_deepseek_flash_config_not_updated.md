# wq#4217 — verify-pass on wq#4210 / PR#2138 ("canonicalize deepseek-flash model id")

**Verdict: CONTRADICT.** The shipped diff renames the model's `id` in
`research/models.yaml` but never touches the live LiteLLM gateway config the ticket
was titled after, and the compatibility mechanism it adds (`legacy_aliases:`) is read
by nothing. The rename is live today only in the fleet's own router — `deepseek-flash`
exists nowhere in the real gateway and at least four independently hand-maintained
pool/tier classification lists still spell the retired name, so any caller that now
asks for the roster's own advertised id breaks.

## What PR#2138 (0e678d3e) claims

`research/models.yaml`: `id: deepseek-v4-flash` → `id: deepseek-flash`, with
`legacy_aliases: [deepseek-v4-flash, deepseek-v4-flash-vision-exp]`. Commit comment:
"deepseek-flash is now the canonical litellm_config.yaml model_name. Both legacy
names above still resolve (served by the new model at Flash pricing) -- no caller
update required." wq#4210's own done-notes add: "No live litellm_config.yaml in-repo
... models.yaml is the in-repo model-identity source orchestrator/router.py and
model-ref-lint both read."

That second claim (models.yaml is read by both) is true, confirmed below, and is
exactly why the first claim is false: router.py reading `id` directly means the
rename is already live in this repo's own tooling, into a fleet that was never
updated to match.

## Break attempt

**1. The live gateway config never got the new name.** `model_ref_lint.py` hardcodes
`CONFIG = "/home/lumen/agent/litellm_config.yaml"` — the real file, outside any repo
(confirmed: `git ls-files | grep litellm_config.yaml` in this repo returns only the
wq#2903 test fixture). Loading it with the lint tool's own `load_live()`:

```
$ python3 -I /tmp/fworker-wq4217/check_deepseek_live.py
CONFIG: /home/lumen/agent/litellm_config.yaml
live count: 35
deepseek-related live model_name entries: ['deepseek-chat', 'deepseek-v4-flash', 'deepseek-v4-pro', 'openrouter-deepseek-pro']
deepseek-related entries retired (in .bak* but not live): ['deepseek']
```

`deepseek-flash` is not in that list. It does not exist anywhere in the live
litellm_config.yaml. The ticket's own instruction was explicit: "update
litellm_config.yaml to use deepseek-flash as the canonical model name" — that step
was skipped; only the in-repo manifest was edited.

**2. `legacy_aliases:` is decorative — nothing reads it.**

```
$ grep -rn "legacy_aliases" --include="*.py" --include="*.yaml" --include="*.sh" .
research/models.yaml:235:    legacy_aliases: [deepseek-v4-flash, deepseek-v4-flash-vision-exp]
```

One hit: the line that defines it. `orchestrator/router.py`'s actual alias builder
(`_build_aliases`, line ~177-206) reads the model's `alias` field (singular) and the
`claude-` prefix-stripped shorthand — never `legacy_aliases`. The new field is not
wired into the one alias-resolution path this codebase has.

**3. The rename is already live in the router, and it diverges from four other
hand-maintained lists.** `router.py` indexes models by `id` directly
(`self.models[m["id"]] = m`, line 296), so its own roster already answers with the
new name:

```
$ python3 orchestrator/router.py roster | grep -i deepseek
deepseek-flash               LIVE-local
```

Four places still hardcode the literal string `deepseek-v4-flash` for pool/tier
eligibility, none updated by this diff:

- `governor/governor_core.py:104` — `_FROZEN_LOCAL_POOL = ("deepseek-v4-flash", ...)`
- `orchestrator/judgment.py:42` — `LOCAL_POOL_MODELS = ("deepseek-v4-flash", ...)`
- `orchestrator/router.py:1001` — `LOCAL_RUNG_POOL_MODELS = ("deepseek-v4-flash", ...)`
- `despag/tiers.py:37` — `TIER_MODELS["mechanical"] = ("deepseek-v4-flash", "qwen36-local")`

`despag/tiers.py`'s own docstring (lines 8-10) says these ids "come from `python3
orchestrator/router.py roster`, not invented" — i.e. this list is *supposed* to track
roster output exactly. It no longer does: roster says `deepseek-flash`, the tier list
still says `deepseek-v4-flash`. `despag/tiers.py`'s `resolve_tier` "only ever returns
a model from the requested tier's OWN list, or raises `ModelUnavailable`" (422
`model_unavailable`) — so a despag mechanical-tier caller that asks for the id the
router itself now advertises gets rejected. `governor_core.py`'s and
`judgment.py`'s copies feed local-pool/governor-exemption pricing classification the
same way: membership tests against the old literal silently stop matching once a
ticket's model is `deepseek-flash` instead of `deepseek-v4-flash`.

`dispatcher/admission_gate.py`, `harness/fworker.py` and several `ops/wq*` fixtures
also still spell `deepseek-v4-flash` literally (grep, 30+ files total) — the mirror
list above is the subset confirmed to key off model identity for pool/tier decisions,
not an exhaustive list of every caller.

## Why the designated liveness gate didn't catch this

The ticket's liveness gate is `ops_verified(wq2903-model-ref-drift)` —
`model-ref-lint`. Ran it live, unrestricted roots (reads the real
`/home/lumen/agent/litellm_config.yaml` and `.bak*` siblings):

```
$ python3 ops/wq2903-model-ref-drift/model_ref_lint.py --json
```

Zero findings for any `deepseek*` name. That's correct *for what the tool checks*:
`deepseek-v4-flash` is still live (so it's not RETIRED) and `deepseek-flash` only
ever appears as a `models.yaml` `id:`/manifest entry, which the tool treats as valid
CLI roster, not a gateway-call context to validate (so it's not UNKNOWN either). The
tool's docstring states its scope precisely: callers that keep calling a name
`litellm_config.yaml` dropped. This is the mirror case — a canonical *id* changed
while the literal gateway name it used to equal stayed put — which is a different
failure class the tool was never built to see. `router.py`'s own `check` subcommand
(`ok: true`) also doesn't check cross-file id consistency; it validates
routing/archetype/dominance data, not whether `despag/tiers.py` or
`governor_core.py`'s hand-copied pool lists still agree with the manifest.

wq#4210's "Tests: ... 124 passed" is real but not probative here:
`test_router.py::test_local_rung_models_mirrors_fworker` (per `router.py:998`
comment) pins the mirrored lists to each other, not to the manifest's live `id` —
an acceptance probe that compares the retired spelling against its own retired-
spelling copies can't fail on exactly the input that changed.

## Not reproduced as new findings

- Did not attempt to reach the real `/home/lumen/agent/litellm_config.yaml` to add
  the missing `deepseek-flash` model_list entry — that file is outside this BUILD
  worker's granted directories (`--add-dir`), and this is a verify-pass, not a
  re-implementation.
- Did not check whether anything has actually dispatched a ticket with
  `model: deepseek-flash` since 0e678d3e landed (2026-10-18 07:53 UTC); this report
  establishes the break is reachable, not that it has already paged.

## Reproduction

- `python3 orchestrator/router.py roster | grep -i deepseek` → `deepseek-flash LIVE-local`.
- `python3 ops/wq2903-model-ref-drift/model_ref_lint.py --json` → no deepseek finding
  (live gateway still has `deepseek-v4-flash`, not `deepseek-flash`).
- `grep -rn "legacy_aliases"` → one hit, the definition itself.
- `grep -n "deepseek-v4-flash" governor/governor_core.py orchestrator/judgment.py orchestrator/router.py despag/tiers.py` → four hardcoded literals this diff left unupdated.

Not implementing a fix here — this is a verify-pass. Fix shape for the escalated
judgment: either (a) deploy an ops bundle (same pattern as wq#2903's
`patch_callers.py`) that adds a `deepseek-flash` `model_name` entry to the live
`litellm_config.yaml` pointing at the same provider model, keeping
`deepseek-v4-flash` as a second entry for back-compat, and patches the four literal
pool lists to add (not replace) `deepseek-flash`; or (b) revert the `id:` rename in
`models.yaml` until the live config and its mirror lists can move together. Either
way, `legacy_aliases:` needs a real consumer or should not be claimed as the thing
that makes old names resolve.
