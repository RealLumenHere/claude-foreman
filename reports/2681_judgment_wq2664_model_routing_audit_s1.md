# wq#2681 — judgment on wq#2675's CONTRADICT of wq#2664 (`MODEL_ROUTING_AUDIT.md` §1)

Judge: claude-opus-5@high, fresh instance. Escalated by
`verify_pass.escalate_on_contradiction` (`research/models.yaml:431`).

**Verdict: wq#2675 is upheld, and the defect is worse than it reported.** wq#2664's
done-claim does not stand. The wrong hit count is the visible symptom; the actual failure
is that the read-through it asserted never happened, and three live model-dispatching
surfaces were sitting unread in its own grep population. Fixed in this PR, with a
reproducer so the next re-audit can be checked instead of believed.

## The three questions the ticket asked

**(1) Does wq#2664's "done" stand?** No. Not because the number is off — because the
bottom line drawn from the number is false. "mailbot is the only surface this re-audit
adds" is contradicted by its own search population.

**(2) Do the unread hits need an actual read-through before "mailbot only" can be
trusted?** They needed one, it is done, and it falsified the claim. wq#2675's 4-file
sample happened to draw from the noise. A larger read finds signal.

**(3) Does §1's header need a corrected count regardless?** Yes — and a corrected method,
because the raw count is not a fact about the fleet until the population is partitioned.

## What the read-through found

Every one of the 40 live source/config hits now carries a written verdict in
`research/model_routing_endpoint_grep.py`'s `ADJUDICATED` table. Three were surfaces:

| new row | surface | why it is live | why wq#2664 should have caught it |
|---|---|---|---|
| 21 | `bin/minerva-factory` → `minerva/minerva_factory.py` | `minerva-factory.timer` enabled, `OnCalendar=*-*-* 03,07,11,17,19,23:35 UTC`, last fire 3h40m before this pass; `ExecStart=… minerva_factory.py run 15 --apply`; `MODEL=deepseek-v4-pro`, rescue `MODEL_2=cerebras-oss-120b` (`minerva_factory.py:43,46,628-644`) via `localhost:4000/v1/chat/completions` (`:42`) | grep hit at `bin/minerva-factory:283`, inside searched root `bin`. It is also **in the timer roster**, one of §1's original three on-Lumen-process instruments |
| 22 | `tools/council/council.py`, symlinked `bin/research-council` (wq#2628) | fans a prompt to seats `claude-max-opus` / `gemini-flash` / `deepseek-v4-pro` / `kimi-k3` through the LiteLLM gateway (`council.py:37,167-176`), then a chairman pass | grep hit at `tools/council/council.yaml:13`. Landed `e1a89d6` 2026-09-25 08:47, **2h21m before wq#2664's own commit `a15b19b`** (11:08) and an ancestor of it — it was in the tree when the grep ran |
| 23 | `harness/evals/worker-quality/` promptfoo judge (wq#1300) | `id: openai:chat:${WQ1300_JUDGE_MODEL:-claude-sonnet-5}` against the LiteLLM loopback (`promptfooconfig.yaml:14-20`), driven by `run_eval.sh` | grep hit on that exact provider line. Landed `37f1f6c` 2026-09-02, three weeks earlier |

Plus, folded into row 20's note rather than given rows: `mailbot/evals/lib/score.mjs:116`,
`test_persona.mjs:45`, `test_dodge.mjs:40` each `fetch("https://api.deepseek.com/chat/
completions")` on the worker's key. Dev-run, but real spend and equally unnamed.

wq#2675's own spot-checks are confirmed, including the one it worried about:
`claude-shim/claude_shim.py` is dormant — `CLAUDE_SHIM_DESIGN.md:10` says `DESIGN FOR
REVIEW`, `systemctl status claude-shim` → "could not be found", nothing on `:8091`.
Independent corroboration from an unrelated file: `council.yaml`'s seat notes record the
claude seat timing out for exactly that reason.

## Why the counts disagreed: the box's `grep` is not GNU grep

`/usr/bin/grep` on Lumen is **ugrep 7.8.4**, which honours `.gitignore` under `-r`.
`foreman/.gitignore:20` is `/_runs/`. So:

```
grep -rl <6 patterns> /home/lumen/foreman            ->   59 files
grep -rl <6 patterns> /home/lumen/foreman/_runs      -> 1277 files   # same tool, same patterns
```

Naming an ignored directory explicitly re-enables it. Three honest operators therefore got
13 (wq#2664), 102 (wq#2675) and 1,445 (this pass) from "the same" command. Logged as
`kb/ISSUE-LOG.md`; the fix is that §1's pass no longer goes through `grep` at all.

## Population, partitioned (the number §1 should have stated)

`python3 research/model_routing_endpoint_grep.py`, 2026-09-25, 7 roots, 6 patterns:

```
population over 7 roots: 1445 files match
  run-artifact   1327     _runs/ events/ pilots/ *.jsonl *.log — agents talking about endpoints
  doc              48     .md .txt .diff
  snapshot         29     queue.db and 17 of its backups, 11 *.bak copies already counted
  vendored          1     bin/trivy
  source           40     the set that had to be read
source hits: 40 (0 unadjudicated)
```

13 was never a defensible count under any partition. 40 is the honest denominator, and it
is only meaningful with the exclusion rules stated — which is the substance of wq#2675's
complaint about the unstated `.md`/`_runs` drop, and it was right.

## Out-of-scope probe (it could fail, and it did)

§1 lists `minerva`, `agent`, `logs` among the registered primaries it did **not** search.
This worktree held all three, so the same six patterns ran over them as a falsification
input drawn from outside the fix's own scope: **187 files match, 147 source/config**,
including `minerva/minerva_factory.py` and `minerva/judgearena_bench.py`. Row 21 is the
one that leaked into a searched root via its `bin/` symlink and is fixed here. Enumerating
those trees properly is a separate pass — **wq#2741**, not guessed.

## Standing fix

`model_routing_endpoint_grep.py --check` exits 1 on any hit with no recorded verdict.
Demonstrated failing by deleting one `ADJUDICATED` entry (`bin/minerva-factory`) and
re-running: `exit=1`, the hit printed as `NEW`. Restored, `exit=0`. §1 now has the
reproducer §0 always claimed the whole document had.

## Also flagged, not fixed

Row 9 calls `langfuse-ops/evaluator.py` *inactive*. `langfuse-evaluator.timer` is enabled
with `OnUnitActiveSec=15min` and fired 14 minutes before this edit
(`ExecStart=… evaluator.py --window-min 15 --sample-pct 5 --max-evals 20`). Whether it
dispatches in a given window depends on trace volume, which this pass did not measure —
the cadence column is wrong either way. **wq#2742**.
