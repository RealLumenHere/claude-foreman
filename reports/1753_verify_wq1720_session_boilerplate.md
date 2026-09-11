# wq#1753 — verify-pass: wq#1720 session boilerplate (bare `export WQ_AGENT` / `wq claim <id>`)

**Verdict: CONTRADICT** — the published fix ships a canonical session-start header whose
step 1 (`eval "$(wq whoami --register work-N --eval)"`) depends on a `--eval` flag that
does not exist on the live, deployed `/home/lumen/bin/wq`. Every fresh session that copies
the header as instructed hits an argparse error with **empty stdout**, so `eval` silently
no-ops, `WQ_AGENT` stays unset, and the session fails downstream at `claude_context.sh
--core` — the exact class of failure wq#1720 was filed to fix, now baked into the fleet's
boot-context doctrine and marked "SOLVED".

Evidence trail read cold: `ops/wq1720-session-boilerplate/README.md`,
`wq1720_fleet_tools.diff`, `wq1720_wq_eval.diff` (all in this repo, this ticket's `deps`).

## What the diff does

1. `wq whoami --register <name> --eval` (lumen-bin PR #94, commit `b1e839d`, branch
   `feat/wq1720-session-boilerplate-bare-export-wq-agent-a1`): routes every `cmd_whoami`
   output line except `export WQ_AGENT=<sid>` through stderr in eval mode, so
   `eval "$(wq whoami --register work-N --eval)"` sees exactly one line on stdout.
   14/14 relevant tests pass on the PR branch.
2. `wq claim <numeric>` refusal already names `wq claim --id N` — fixed by wq#582 before
   this ticket ran. No code change needed; still correct on live `wq` (verified below).
3. A "canonical 3-line session header" written directly into
   `/home/lumen/minerva/context/fleet_tools.yaml` (`context/` is gitignored there, no PR —
   same shape as wq#1127's precedent), re-rendered into `FLEET_TOOLS.md`, which
   `claude_context.sh --core` injects into every orchestrator's boot context:
   ```
   1) eval "$(wq whoami --register work-N --eval)"
   2) bash /home/lumen/minerva/context/claude_context.sh --core
   3) wq claim --id N
   ```

## Verification performed

1. **Read the diff cold** against this ticket's own evidence trail, not the PR's
   self-description.
2. **Confirmed item 2 (numeric-claim refusal) is correct on live `wq`**:
   `/home/lumen/bin/wq claim 1701` → `REFUSED: agent name '1701' is numeric ... To claim
   THAT ticket: wq claim --id <ticket-id>`, exit 1. This half of the fix holds.
3. **Ran the published header's step 1, verbatim, against the actually-deployed tool**
   (`/home/lumen/bin/wq` — confirmed a plain file, not a symlink, `202479` bytes, `Sep 11
   03:06`, the checkout the README itself says "every orchestrator actually runs"):
   ```
   $ /home/lumen/bin/wq whoami --register wq1753-probe --eval
   usage: wq [-h]
             {add,claim,whoami,...} ...
   wq: error: unrecognized arguments: --eval
   EXIT=2
   ```
   `grep -n eval /home/lumen/bin/wq` finds zero occurrences of `--eval` (only the word
   "evaluated" in an unrelated docstring) — the flag genuinely is not on the deployed
   binary. Stdout is empty, so `eval "$(...)"` evaluates to nothing and the *shell* sees
   exit 0 — the failure is silent, not loud: no error surfaces to whatever ran step 1,
   `WQ_AGENT` simply never gets set, and step 2 (`--core`) then refuses per wq#1127,
   reproducing the original wq#1701 symptom this ticket exists to close.
4. **Confirmed the ticket's own docs already know this.** README lines 34-43: PR #94 is
   "**Landed but not yet deployed to the primary**" — blocked by wq#1538 (106 lines of
   unrelated uncommitted `wq` hunks in `/home/lumen/bin`), `lumen-pr land` refuses.
   `git status --porcelain wq` in `/home/lumen/bin` confirms live drift (` M wq`).
   `ISSUE-LOG.md:349` status column: `SOLVED (wq CLI fix unlanded pending wq#1538; header
   live)` — publishing the header live while its dependency is unlanded is self-contradictory
   and strictly worse than the pre-fix state: it now ships a boot instruction guaranteed to
   fail on every fresh session, silently.
5. **The ticket's own acceptance test cannot have passed against the deployed tool.** Its
   stated test is "run the canonical header in a fresh shell, `--core` must not refuse and
   the claim must succeed." The README's "Verifying" section shows the actual probe ran
   against `<lumen-bin worktree>/wq` (the PR branch checkout), not `/home/lumen/bin/wq`
   (primary) — the two diverge exactly on the flag being tested.
6. **Independent escalation**: per protocol, a contradiction escalates to opus-5@high as a
   judgment trigger. Spawned a fresh opus-5 agent with no access to this document, given
   only the code paths and the claim to check. It independently re-derived the same facts
   (live binary has no `--eval`, four flags only on the `whoami` subparser; the verbatim
   header command fails with `unrecognized arguments: --eval`, exit 2, empty stdout; PR #94
   unlanded per wq#1538; ISSUE-LOG status self-contradictory) and returned **CONTRADICTED**,
   independently noting the silent-failure mechanism (empty stdout → `eval` no-ops → exit 0
   at the shell level) as an aggravating factor beyond what the original fix intended to
   solve.

Probe left no registry cruft: the failing command errors out of argparse before
`cmd_whoami` runs, so no session was ever registered under `wq1753-probe`; confirmed via
`wq whoami` listing.

## Severity assessment

Not a fabricated-evidence false positive — `/home/lumen/bin/wq` is the real, live, primary
checkout every dispatched session actually invokes (confirmed via `git status`/`git
branch`, not assumed). This is a genuine, reproducible regression in the *doctrine* layer:
the code fix (PR #94) is sound and tested in isolation, but the boot-context change that
depends on it was published to every fresh session ahead of its dependency landing, turning
a documented, loud failure (bare `export WQ_AGENT` exporting empty, which at least surfaces
as a `--core` refusal a human/agent can act on) into a silent one (an unrecognized-flag
error with empty stdout that `eval` swallows, leaving `WQ_AGENT` unset with zero indication
why until `--core` refuses two steps later for what looks like an unrelated reason).

## Conclusion

CONTRADICT. Item 2 (numeric-claim refusal wording) is correct and live. Item 1 (`--eval`
canonical header) is broken as published against the tool the header's own audience
actually has on PATH, and the ticket's ISSUE-LOG status text already discloses the gap
while still marking the ticket SOLVED. Filed wq#1759 (build lane, deps=1720) to either (a)
land PR #94 to the primary ahead of/alongside wq#1538's resolution so the header's
dependency actually exists where the header is read, or (b) gate the canonical-header
publication on the flag's presence on the primary, with a fallback header for the interim
that doesn't depend on `--eval`. Did not re-implement, per this ticket's instruction —
verify only.
