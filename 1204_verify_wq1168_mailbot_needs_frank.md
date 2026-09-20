# wq#1204 — verify-pass of wq#1168 (mailbot: stop reflexive "owner looped in")

Verdict: **CONTRADICT (partial)** — the persona-prompt half and the needs_frank plumbing work; the stated goal is not met because a deterministic path still emits the tic.

Diff read: mailbot `31fc00f` (cherry-pick of `d16d9d4`/`c6972d1`), on `main`. Files: `src/leakfilter.js`, `src/worker.js`, `mail_recall.py`, migration (now `0007_needs_frank.sql`).

## What holds
- `worker.js:64,82` DEFAULT_PROMPT/ANGRY_PROMPT now forbid the closing owner mention unless a real handoff is signalled.
- `worker.js:755-756` gate: `!probe && !alreadyFlagged && needsFrank(bodyText)`; `needs_frank` is persisted on the inbound row (`worker.js:366-367`).
- `mail_recall.py needs-frank` works: temp-DB probe (`ops/wq1204-verify/probe_recall.py`, schema from `mr.SCHEMA`) listed only the flagged thread, `1 waiting on Frank`.
- Messenger `email_recall action=needs_frank` is not in this diff (tracked as wq#1197).

## Defects (evidence)
1. **`SAFE_CLOSERS` still hardcodes the tic** — `leakfilter.js:366-371`: all 4 closers name the owner/Frank, one is literally "The owner's looped in." `safeFallback()` (`leakfilter.js:374-379`) is the deterministic floor sent from `worker.js:822,843,859` (leak-trip after regen / 3 dodge attempts refused), so 100% of those replies claim a handoff. `needs_frank` is NOT set on that path, so the claim is also false against the recall table: the bot tells the sender the owner has it while `needs-frank` lists nothing. Probe: 4/4 closers match `/owner|frank|looped/i`.
2. **Primary trigger has a false negative** — "Can I talk to Frank?" does not fire. Regex 1 (`leakfilter.js:168`) lists `the owner|a human|...` but not Frank/him; regex 2 needs `reach|contact|call|email`, not `talk to`.
3. **Money regex over-fires** — `leakfilter.js:171` bare `rate|deal|cost|price|quote|contract`: "big deal", "heart rate of a calf", "cost of living", "price of hay" all trip the handoff; regex 5 also matches "set up a meeting room". Fail direction is one extra mention, once.
4. **"Once per thread" is once per ~10 messages** — `alreadyFlagged` reads `loadHistory(..., limit=10)` (`worker.js:382-387`), keyed by contact email, not thread. The flag scrolls out of the window and the handoff can re-fire.
5. Nits: `needs-frank` help says `--since 7d`, argparse default is `30d` (`mail_recall.py:182`); no way to mark a flagged row handled, so it lists for the full window.

## Probes
The original `ops/wq1204-verify/probe_needs_frank.py` scraped the regex literals out of `leakfilter.js` and ran 22 cases under Python `re`: 6 mismatches (5 over-fire, 1 miss). It was retired in wq#2171: a Python transcription of JS regex literals does not prove worker behaviour, and it broke once wq#2147 put comments inside `NEEDS_FRANK_RES`. Its coverage now lives in mailbot probes that import the production module:
- `/home/lumen/mailbot/ops/wq2147-verify/probe.mjs` — the classifier cases, `safeFallback`/`SAFE_CLOSERS`, and the thread-keyed `alreadyFlagged` source assertions.
- `/home/lumen/mailbot/ops/wq2164-verify/probe_pronoun_overfire.mjs` — the wq#2168 pronoun negatives.

`ops/wq1204-verify/run_mailbot_probes.sh` runs both. (`node` was refused at runtime by the worker gate, "This command requires approval", so the Python port was the original workaround; the runner needs a shell where `node` is allowed.)

## Suggested fix shape (not implemented; verify-pass)
- Replace `SAFE_CLOSERS` with owner-free closers (or take a `frankHandoff` arg and use owner closers only then).
- Add `frank|him|manager` to regex 1 and `talk to` to regex 2; tighten `rate|deal|cost|price` to money context.
- Flag lookup: query `needs_frank` by thread without the LIMIT 10 window.
