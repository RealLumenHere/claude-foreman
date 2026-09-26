# wq#2316 — word-boundary fix for `/home/lumen/bin/mask-secrets`

`harness/masking.py` and `/home/lumen/bin/mask-secrets` carry the same house credential
patterns and masking.py:14-20 declares them a lockstep pair. wq#2292/wq#2294 anchored the
Python copy; the sed copy never followed, so it still matches a prefix in the middle of an
ordinary word.

Live on the installed tool before this bundle:

```
$ echo 'repo highs_solver / risk-assessment-20260921-rollout' | /home/lumen/bin/mask-secrets
repo highs_MASKED / risk-assessment-MASKED
```

Both are plain English. The `ghs_` case is the one that filed a pri-1 rotate-today ticket
for a credential that never existed (wq#2294) and then destroyed the evidence a mask-hit
triage runs on — it blocked the first pass of the wq#2294 census. The `sk-` case is the
same bug in the other rule and was not previously recorded.

## What changed

Every prefix rule gains a left word boundary. POSIX ERE has no lookbehind, so masking.py's
`(?<![A-Za-z0-9])` is spelled as a captured `(^|[^A-Za-z0-9])` that the replacement
re-emits — each prefix group shifts one right, hence `\1\2MASKED`.

The two rules the ticket names also take masking.py's current bodies rather than the older
literals quoted in the ticket text, because the point of the port is lockstep:

| rule | before | after | why |
|---|---|---|---|
| `gh[pours]_` | `[A-Za-z0-9_]{6,}` | `[A-Za-z0-9]{30,}` | real bodies are 36 base62; `{6,}` also matched this tool's own output, so every re-read of a masked file re-armed the alarm (masking.py:68-71) |
| `github_pat_` | folded into the rule above | own rule, `[A-Za-z0-9_]{20,}` | different body shape and floor (masking.py:74) |
| `sk-` | `(sk-[a-zA-Z-]*)` | `(sk-([a-zA-Z]+-)*)` | wq#2300: the greedy form kept 8 characters of a real secret in the output and in `scan()`'s `prefix` field |
| `cfut_`, `xox`, `AKIA` | unanchored | anchored | same one-line transform; wq#2313 asks for it and leaving them divergent would not be lockstep |
| kv-secret | unanchored | unchanged | keys on an English word, not a prefix — anchoring would miss `githubToken: ...` |
| url-auth (wq#2760) | absent | `scheme://user:<pw>@host`, user may be empty, `<pw>` may hold `@`, no floor | a DSN under an unlabelled name (`DATABASE_URL=postgresql://…`) printed in the clear and burned a rotated password (wq#2700); wider than masking.py on purpose — no floor, no placeholder exclusions |

The kv-secret rule stays divergent on purpose. Carrying masking.py's shape-based label and
tail lookahead across is **wq#2313**, which also owns the `echo` guard. Until then this copy
over-masks an already-masked value when a credential label precedes it (`"pat": "MASKED"`),
which costs a false alarm, never a missed mask.

## Install

Worker sessions cannot run this — `/home/lumen/bin` is a primary tree (guard.sh, wq#1719)
and `bash ops/*` is not on the worker Bash allowlist (ISSUE-LOG row 540). Run it from an
orchestrator session, from Frank, or via `lumen-run`:

```
bash /home/lumen/foreman/ops/wq2316-mask-secrets-boundary/install.sh
bash /home/lumen/foreman/ops/wq2316-mask-secrets-boundary/install.sh --verify
```

No root required. The previous version is kept at `mask-secrets.pre-wq2316` on first change,
and `/home/lumen/bin` is left with one modified tracked file for its owner to commit.

## Gate

```
echo 'repo highs_solver' | /home/lumen/bin/mask-secrets     # unchanged
python3 -m pytest ops/wq2316-mask-secrets-boundary/test_mask_secrets_boundary.py -q
```

The tests drive the shipped sed script as a subprocess, not a Python re-implementation of
it — a re-implementation is exactly how the two copies drifted apart while both claimed to
be in lockstep.
