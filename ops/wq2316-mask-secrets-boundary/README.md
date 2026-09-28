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

## wq#2720 — a wide body swallowing a DIFFERENT rule's prefix

`ghp_<body>AKIA<16>xoxb-<body>`, glued with no separator, ran github's `[A-Za-z0-9]{30,}`
straight through `AKIA` and the letters `xoxb`, stopping only at the `-` that charset can't
cross — the slack rule never saw its `xoxb-` prefix and its body printed in the clear
(`ghp_MASKED-<slack body>`). Same shape for any rule whose charset covers another's prefix
letters (cloudflare's and sk-key's underscore/dash charsets cover all five others).

POSIX ERE has no lookahead, so a body can't be told "stop before AKIA" directly. Before each
of the five fixed-prefix rules runs, every OTHER rule's prefix is marked with a byte no
charset contains (STX, `\x02`), the rule runs, and the mark is stripped again immediately —
so a greedy run halts at the mark instead of reading through it, and the wq#2292/wq#2400
false-positive fixes stay intact because the mark never survives to the NEXT rule's own
boundary check. One cycle only fixes the swallow, not the fixed left-to-right order it
exposes (slack runs before aws, so it can't yet see the boundary aws's masking is about to
create for it in the same pass); the whole program now runs through itself six times, one
per fixed-prefix family, mirroring masking.py's `mask_text()`/`scan()` looping over
`PATTERNS` until a pass changes nothing.

kv-secret deliberately gets NO bracket. It is the fallback for a labelled value that IS
another rule's shape (`API_KEY: sk-ant-<token>`, `TOKEN: AKIA<lowercase junk that fails AWS's
own charset>`), and a mark at position zero would refuse it outright, floor and all — a real
leak, not a false-positive trade. Confirmed live before reverting it.

## wq#2899 — a foreign rule's ALREADY-masked residue still undershoots the next rule's floor

wq#2848 (not documented here yet — see the `_PROG` header comment) changed the wq#2720 mark
from a bare prefix to the foreign rule's FULL shape, so a stray 4-5 character substring that
merely spells another rule's prefix no longer truncates a real token below its own floor. That
fix only helps while the foreign token is still LIVE. Once that rule has already run (earlier
in this file's fixed order, or an earlier one of the six passes) and replaced its span with
`<its own kept prefix>MASKED`, the next rule's charset still can't cross the mask literal (no
charset ever contains `\x01`), so a live gap shorter than the current rule's floor — sandwiched
between this rule's own prefix and the neighbour's already-masked residue — still failed the
whole match and stayed in the clear: an sk- token whose body embeds `cfut_`/`cfut` or leads
with `xoxb-` leaked 3-17 characters of its own head. wq#2848's class-wide probe flagged this
6/1900, identical on the pre-2720 masker.

One absorb line per open-floor rule (not AWS — its `[0-9A-Z]` charset can't spell any other
rule's lowercase/underscore/dash prefix), run right after that rule's own strip: boundary +
this rule's prefix + any length of its own charset + one of the OTHER rules' bare kept prefix
+ `\x01` collapses to boundary + prefix + `\x01`. No mark byte needed — POSIX ERE already gives
a `*` characters back to satisfy a required literal suffix. The trailing list excludes the
rule's own prefix for the same wq#2638 reason the mark line above it does; a letters-only gap
immediately in front of a SECOND, coincidental `sk-` (not a foreign rule's prefix) is a
separate quirk of the greedy `sk-([a-zA-Z]+-)*` prefix. wq#2924 fixed it in masking.py, which
this copy now delegates to, by limiting the prefix to one provider qualifier
(`sk-(?:(?:ant|or|proj|svcacct|admin|None)-)?`).

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
