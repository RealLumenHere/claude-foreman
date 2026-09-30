# wq#3202 — verify-pass of wq#3199 (PR#32: un-break known-associates gate CANARY anchor)

**Ticket:** wq#3202, archetype verify-pass, verifier claude-sonnet-5@high
**Target:** wq#3199 (author claude-opus-5), PR http://100.121.17.5:3000/lumen/mailbot/pulls/32,
merged as `a3e0e81` on `/home/lumen/mailbot` main.
**Verdict: CONFIRM.** No contradiction found.

## Claim under test

wq#3198 found that `test_known_associates.mjs`'s `loadPromptModule()` sliced
`src/worker.js` from a `const CANARY` string literal that no longer existed
after wq#1169 moved `CANARY` to an `export const` in `src/leakfilter.js` —
every invocation crashed with an uncaught exception before either the static
or live gate ran. wq#3199 claims to fix this by re-anchoring the slice start
on `const DEFAULT_PROMPT` and sourcing `CANARY` from a real dynamic `import()`
of `leakfilter.js` (pure ESM, zero imports, so plain node resolves it, unlike
`worker.js` which pulls in `cloudflare:email`), injecting it into the
evaluated block as a parameter since `new Function` can't run an import
statement.

## What I checked

Read `test_known_associates.mjs` (a3e0e81) cold, `src/worker.js` and
`src/leakfilter.js` on main, the wq#3199/wq#3198/wq#1358 ticket chain, and
ISSUE-LOG.md row 1093. Then ran the probes myself rather than trusting the
done-notes' citations.

1. **Anchors actually exist on main.** `grep -n` on `src/worker.js`:
   `const DEFAULT_PROMPT` at line 52, `async function callDeepSeek` at line
   160 — slice covers `KNOWN_ASSOCIATES` (140) and `buildSystem` (146).
   `CANARY` is imported at line 8, not declared inside the slice, matching
   the `declaresCanary` branch the fix depends on.
2. **`leakfilter.js` is genuinely import-free.** `grep -n "^import"` →
   no hits, so `pathToFileURL(...).href` dynamic import resolves in plain
   node as claimed.
3. **Static gate, run fresh against main:**
   `node test_known_associates.mjs --static` → **7/7 exit 0** (previously an
   uncaught throw, confirmed by wq#3198's reproduction).
4. **Live gate, run fresh against main (real DeepSeek call, not just
   re-reading the author's transcript):**
   `node test_known_associates.mjs --samples 1` → **11/11 exit 0**. The
   actual reply returned: *"Jay I know as your roommate and occasional
   collaborator, and the man who hands my address out to people who didn't
   ask for it"* — genuinely places Jay by role (not a parrot of the input
   name), canary not leaked, no disclaimer. This independently reproduces
   the live-gate claim rather than taking the done-notes' 17/17 on faith.
5. **Falsifiability probe (wq#2630), run fresh from OUTSIDE the fix's
   default `--worker` scope.** Extracted the rev-pinned pre-roster
   `src/worker.js@31fc00f` (`git show 31fc00f:src/worker.js`, immutable
   commit, not a live mutable file) — confirmed it has a local
   `const CANARY = "ibis-lantern-0x5c7"` and no `KNOWN_ASSOCIATES`, i.e. it's
   genuinely pre-fix code. Ran:
   `node test_known_associates.mjs --static --worker <pinned 31fc00f
   worker.js> --leakfilter src/leakfilter.js` → **1/7, exit 1**: canary
   check passes (the pre-refactor file declares its own CANARY, correctly
   handled by the `declaresCanary` guard), all six roster checks FAIL
   cleanly — no crash, no false pass. The gate can fail; it is not vacuous.
6. **Wiring.** `package.json` diff adds `test:associates` (`--static`) and
   `test:associates:live` (full), matching the done-notes' claim that
   nothing previously referenced the file.
7. **ISSUE-LOG row 1093** exists and its root-cause/solution text matches
   the diff and the ticket chain.

## Things I tried to break and why they don't hold up

- **ROLE regex false positives** (e.g. a reply that disclaims knowing Jay
  but happens to contain "roommate" or "collaborat" elsewhere): the
  `DISCLAIMERS` check runs as a separate, independently-scored assertion in
  the same sample, so a disclaiming reply still fails the sample overall
  even if `places Jay by role` spuriously passes. Not a gate that can be
  silently defeated — it's a known limitation of keyword heuristics, not a
  regression this diff introduces, and it's orthogonal to the crash this
  ticket fixes.
- **`new Function` on `worker.js` source** — inherited design from before
  wq#3199, operates on trusted first-party source (not attacker input); not
  a new attack surface added by this diff.
- **Merge commit hash mismatch** — done-notes cite `1cf1eae124` as the PR
  merge commit while main shows `a3e0e81`; confirmed both are valid commit
  objects in the repo (`git cat-file -t` on `1cf1eae124` → `commit`), most
  likely Gitea's pre-squash SHA vs. the squashed commit landed on main. Not
  a correctness issue with the shipped diff.

## Conclusion

Every claim in wq#3199's done-notes reproduces under a fresh, independently
run probe: the crash is gone, the static gate passes on current main, the
live gate passes with a real API call showing paraphrased-but-genuine
placement of Jay, and the falsifiability probe against the pinned pre-fix
commit fails cleanly instead of crashing or vacuously passing. No basis to
contradict. **CONFIRM — no escalation to judgment.**
