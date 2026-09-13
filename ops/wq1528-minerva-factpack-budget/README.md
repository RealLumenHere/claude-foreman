# wq#1528 — factpack facts budget: retrieved facts survive the digest

Design: `PROMOTER_JUDGE_REPAIR_DESIGN.md` §2c/§2e (wq#1383 window class, 3rd
occurrence; follow-up to wq#1501).

## Problem

`minerva_factpack.build_context()` = compact judge digest + CLOSED_WORLD +
retrieved facts, sliced to `CAP`. The facts got whatever was LEFT after the
digest: at live CAP 20000 with the compact digest at 17535 + closed-world 693,
facts had 1772 chars — under half the TOP_K=25 retrieval. Every digest growth
spurt (wq#1383 x3) silently starved or zeroed the facts. Separately,
`factpack.conf` (2026-07-27) still set the legacy env name `JUDGE_CTX_CHARS=16000`,
which no code reads (`minerva_verify` reads `VERIFY_JUDGE_CTX_CHARS`), but which
the wq#1501 replay check DID read — so the check measured a window nothing used,
against the raw (25K) digest the judge never sees.

## Change

- `minerva_factpack.py`: `FACTS_RESERVE` (env `FACTPACK_FACTS_RESERVE`, default
  4000 ≈ TOP_K 25 × 160-char line) reserved before the digest; CAP default
  20000 → 22500; `build_context` floors the facts room at the reserve so a
  digest spurt costs the facts tail (window slack), never every fact.
- `minerva_verify.py`: `JUDGE_CTX_CHARS` default 20000 → 23000;
  `JUDGE_WINDOW_MAX_CHARS` 23500 = qwen36-local rung bound (n_ctx 8192 tokens,
  measured 6144-token prompt at 20000 chars, minus 600 completion + 256 safety
  at 3.0 chars/token). `_assert_judge_window_covers_digest()` extended: floor is
  now `compact digest + CLOSED_WORLD + FACTS_RESERVE` (was hardcoded +1500) and
  the window is checked against the rung bound.
- `zz-wq1528-judge-window.conf`: sets the ordered sizes on
  `minerva-proposal.service`; `zz-` sorts after `factpack.conf` so the correct
  values win for both env names.
- `ops/wq1501-minerva-judge-rung/verify_judge.py` `window` check rewritten to
  model the real pipeline: effective env name, compact digest via
  `minerva_factpack.judge_digest` (live path, mirror fallback), full ordering
  `digest + closed-world + reserve <= FACTPACK_CAP <= window <= --rung-max`.

Ordering on 2026-09-13: 17535 + 693 + 4000 = 22228 ≤ 22500 ≤ 23000 ≤ 23500.

## Install / verify / rollback

```
/home/lumen/bin/lumen-run start --root --name wq1528-factpack -- \
    /home/lumen/foreman/ops/wq1528-minerva-factpack-budget/install.sh
python3 /home/lumen/foreman/ops/wq1501-minerva-judge-rung/verify_judge.py window --strict
```

Installer is hash-gated against the mirror pre-image (sha256 in `install.sh`;
built from `/home/lumen/research/minerva-ref`, ISSUE-LOG #233) and backs up to
`/home/lumen/backups/wq1528/`. Installed live 2026-09-13 05:52 UTC; window
replay OK (`window=23000 cap=22500 reserve=4000 digest(compact)=17535`).
Rollback: restore the two `.bak` files, `rm` the drop-in, `daemon-reload`.
