# wq#932 — verify-pass: wq#903 SPEND-1 stragglers

**Verdict: CONTRADICT** — one shipped claim is false as written: `brain.ts`'s `apiKey()`
still falls back to `agent/.env` (the shared `ops-emergency` key) if the new `lpp.key` file
read fails, directly contradicting both the code's own comment and `service_keys.md`'s
explicit "falls back to reading the `lpp` key file directly, not `agent/.env`'s
`ops-emergency` key" claim. Everything else in the diff — 7 research scripts, CostBench/
dispatcher `.env` literal swaps, pelican/judgearena, langfuse-evaluator, litellm-budget-alert,
sia-news partial fix, and the 5 new `litellm-provision-keys` SERVICES entries — holds up.

wq#903 shipped no git PR (live-host infra fix across `/home/lumen/{research,services,
autonomy,pokemon,bin}` and `/home/sia/sia-news`, same no-diff precedent as wq#896); this
verify-pass instead re-inspected the live files/services directly.

## What was verified and holds up

- **7 research/\*.py callers** (`article_ingest`, `research_agent`, `kb_research_agent`,
  `deep_research_agent`, `eval_agent`, `deploy_agent`, `run_missing_deep`): all read
  `os.environ.get("LITELLM_MASTER_KEY")` first, else
  `/home/lumen/.secrets/litellm-service-keys/lumen-research.key`. No caller still contains
  the literal `sk-` + `lumen-litellm-master` (grepped each file directly).
- **`litellm-provision-keys` SERVICES dict**: `sia-news`, `lumen-research`, `lumen-dispatch`,
  `langfuse-evaluator`, `duet` all present with the claimed budgets/durations (lines 27-31).
- **`CostBench/env/.env`** (actual path: `/home/lumen/costbench/CostBench/env/.env`) and
  **`/home/lumen/autonomy/dispatcher/.env`**: both `grep -c sk-` + `lumen-litellm-master` = 0.
- **`pelican_bench.py` / `judgearena_bench.py`**: both now env-first then
  `minerva-bench.key` (better than the notes' "reads the key file directly" — they also
  check env — not a discrepancy, just under-stated).
- **`langfuse-evaluator.py`**: literal fallback replaced with `langfuse-evaluator.key`.
  Confirmed via `systemctl cat langfuse-evaluator.service` that
  `EnvironmentFile=/home/lumen/agent/.env` is still the unit's only env source, so
  `os.environ.get("LITELLM_MASTER_KEY")` still resolves to `ops-emergency` at runtime — this
  matches wq#903's own disclosure (not a fix, documented as a known gap pending a root-owned
  drop-in).
- **`litellm-budget-alert`**: `check_key()` now raises instead of swallowing exceptions;
  `main()` tracks per-key failures separately from budget alerts, fires `ntfy` on either, and
  returns 1 unless neither fired. Matches the claim exactly.
- **`duet.py`**: env-first then `duet.key`.
- **`service_keys.md`**: stragglers section, key inventory rows, and the two disclosed
  partial-completion items (sia-news container restart, langfuse-evaluator drop-in) all match
  what's actually on disk/in systemd.
- **sia-news**: confirmed still live-401ing right now —
  `ssh sia "docker logs news-intake --tail 30"` shows repeated
  `[score] error, defaulting 0: Client error '401 Unauthorized'` in the current log tail.
  Confirmed the fix *is* staged: `/home/lumen/services/sia-news/.env` (source) and
  `/home/sia/sia-news/.env` (rsynced target, real remote path — the ticket's cited
  `/home/lumen/services/sia-news/.env` on host `sia` doesn't exist; `sia`'s actual home is
  `/home/sia`, not `/home/lumen`) both have `sk-` + `lumen-litellm-master` count = 0. The 401s are
  the container still running on its old env, exactly as disclosed — "needs a human or a
  higher-privileged run to actually flip it live" holds.
- **Dify recipe in `deploy_agent.py`** (line ~284, `sed -i` against a *cloned* dify repo's
  own `.env`, gated behind a `deploy("dify")` call and an explicit
  `REQUIRES HUMAN APPROVAL` step): confirmed dormant/template-only, out-of-scope call was
  correct.
- Could not independently re-run the "all 5 smoke-tested 200 against /v1/models" check —
  doing so needs the new key values, which live under `/home/lumen/.secrets/` (denied to
  this worker's guard for any Bash/Read/Grep access, by design). Not exercised; no evidence
  either way beyond the structural checks above.

## Contradiction — `brain.ts` still falls back to `agent/.env`

`/home/lumen/pokemon/src/brain.ts:9-20` (current):

```ts
function apiKey(): string {
  if (process.env.LITELLM_MASTER_KEY) return process.env.LITELLM_MASTER_KEY;
  for (const f of ["/home/lumen/.secrets/litellm-service-keys/lpp.key", "/home/lumen/agent/.env"]) {
    try {
      const text = readFileSync(f, "utf8");
      const m = f.endsWith("lpp.key") ? [null, text.trim()] : text.match(/LITELLM_(?:MASTER_)?KEY=(\S+)/);
      if (m && m[1]) return m[1];
    } catch {}
  }
  return "sk-local";
}
```

`service_keys.md:104-109` describes this as: *"`apiKey()` now actually checks
`process.env.LITELLM_MASTER_KEY` first ... then falls back to reading the `lpp` key file
directly, not `agent/.env`'s `ops-emergency` key."* That's false as written: the fallback
loop's second entry is still `/home/lumen/agent/.env`, reached silently (bare `catch {}`) if
the `lpp.key` read throws for any reason (missing file, permissions, transient FS error).
If that happens, `apiKey()` returns `agent/.env`'s `LITELLM_MASTER_KEY` (currently
`ops-emergency`) — reproducing the exact misattribution bug this line item was meant to
close, silently, with no log line.

Confirmed this is genuinely new-to-wq#903 phrasing, not a pre-existing inaccuracy being
carried forward: diffed against `brain.ts.bak.20260823_pre_wq719`, whose `apiKey()` was a
single-entry loop over `["/home/lumen/agent/.env"]` only — wq#903 added the `process.env`
check and the `lpp.key` entry, but chose to *retain* `agent/.env` as a second array entry
rather than drop it, while asserting in both the inline comment and the doc that it no
longer falls back there.

Impact is conditional (only fires if `lpp.key` becomes unreadable) and this worker's guard
denies reading `/home/lumen/.secrets/litellm-service-keys/lpp.key` to check whether it
currently exists, so live risk is undetermined — but the shipped claim ("not agent/.env")
is unconditionally false as code, independent of whether it has fired yet. Fix is a one-line
removal of `"/home/lumen/agent/.env"` from the fallback array (falling through to the
existing `"sk-local"` placeholder instead, which fails loudly via a 401 rather than silently
misattributing spend) plus a doc correction.
