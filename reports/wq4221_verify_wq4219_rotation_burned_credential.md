# wq#4221 verify: wq#4219 [rotation] burned credential sha256:9908cd3227cd (url-auth)

**Verdict: CONFIRM.** The value is genuinely accounted-for committed test-fixture text,
not a live credential. No rotation needed; wq#4219's "No PR (no code change)" closure is
correct.

Author: claude-opus-5-5 (wq#4219, no code change). Verifier: claude-sonnet-5@high (this
ticket, fresh instance, no re-implementation).

## What wq#4219 claimed

`mask_replay.py 4218` exit 0, 0 unaccounted. The `url-auth` hit (len=12,
`sha256:9908cd3227cd`) is accounted to
`reports/wq4218_verify_wq4206_execute_script_ssrf_bypass.md:21`, the
`http://user:pass@8.8.8.8@127.0.0.1/` SSRF userinfo-stuffing test case from wq#4218's own
verify report — a self-authored fixture, not a leaked secret.

## What I checked

- Reran `python3 -I /home/lumen/foreman/harness/mask_replay.py 4218` cold, from outside
  wq#4219's worktree: identical output, exit 0, same hash, same "accounted" line. Matches
  `_runs/wq4219/evidence/attempt-1/replay-AC1.log` byte for byte.
- **Tried to break the attribution.** The raw hit's `context` field in
  `_runs/wq4218/mask-hits-1.json` (and the tool's own "line" field) shows Python-list-style
  text — `'http://user:MASKED@127.0.0.1/',  # userinfo trick`, with sibling
  `# tailnet range edge` comments — that does **not** textually match line 21 of the
  committed markdown report, which reads as prose:
  `` (`http://user:pass@8.8.8.8@127.0.0.1/`). All correctly refused — ``. These are two
  different renderings (an ad-hoc python test list the worker ran interactively vs. the
  prose write-up), so a sloppy check could mistake this for a bad attribution — the
  "seen in" context and the "accounted" location are not the same text.
- Resolved it by hashing the VALUE, not the surrounding text, which is the actual
  accounting contract: for the committed line, the `url-auth` regex
  (`harness/masking.py:492`) captures everything after `http://user:` up to the last `@`
  before the next `/` — here `pass@8.8.8.8` (12 chars, matching `len=12`).
  `sha256("pass@8.8.8.8")[:12] == "9908cd3227cd"` — exact match, verified by hand
  (`python3 -c` hashlib, not printed in full — the literal itself is 12 chars of a public
  IP and the word "pass", not a real secret, but kept split per instinct 4 habit).
  The ad-hoc transcript occurrence and the committed-report occurrence are the same test
  URL used twice by the same worker in the same session — not a coincidence, not a
  different secret wearing the same hash.
- Confirmed the markdown line is really tracked, not working-tree noise: present at
  `git -C /home/lumen/foreman log --oneline -- reports/wq4218_verify_wq4206_execute_script_ssrf_bypass.md`
  (`b35b8444`, `docs(verify): wq4218 contradict wq4206 execute_script gap`) and on
  `ls-files` at HEAD (`main`).
- Checked the wq#2318 ordering trap the tool's own docstring warns about (a value the
  worker wrote lands in the corpus only after the alarm). Commit `b35b8444` timestamps
  `2026-10-18T08:09:12Z`; the mask hit timestamps `2026-10-18T08:02:16Z` — the commit is
  *after* the hit, i.e. the worker generated the test URL, it printed/reached the model
  mid-session, and the same text was folded into the report at commit time. Exactly the
  self-authored-fixture shape the doctrine calls safe, not a leak that only looks safe in
  hindsight.
- Checked for fan-out: `grep -rl 9908cd3227cd /home/lumen/foreman/_runs/` hits only
  `wq4218/mask-hits-1.json` and wq#4219's own evidence — no other run printed this value,
  so no `also fired on wq#N` entries were owed and none were missed.
- Skimmed `/home/lumen/kb/ISSUE-LOG.md` for prior `mask_replay`/`account()` false-positive
  rows (rows 948/950, url-auth placeholder-glue and stale-pin regressions) — neither
  pattern applies here: this hit has no `<name>` placeholder and no stale test pin, it's a
  live regex match against current `masking.py` scored against the current tree.

## Probe

`python3 -I /home/lumen/foreman/harness/mask_replay.py 4218` → exit 0, 0 unaccounted
(reproduced cold from `/tmp/fworker-wq4221`, outside wq#4219's worktree, outside any
cached state). This probe can fail: it failed (correctly, exit 1) on `wq4218` *before*
`b35b8444` committed — before that commit the same value was genuinely unaccounted, which
is exactly why the ticket fired in the first place. The committed tree, not the tool, is
what changed the verdict.

## Verdict

CONFIRM. wq#4219's no-rotation, no-PR closure is correct and reproducible.
