# wq#2999 — verify-pass (archetype 8) of wq#2991: capability lint route ssh-needing research tickets

Reviewer: claude-sonnet-5 @ high (fresh instance, same model as author). Worktree
`wq2999-a1`. Verified 2026-09-28. Subject: PR #1288 (merged `24be56ef7` +
merge commit `1c964926`, already on `main` in this worktree's history). ISSUE-LOG
row 971.

## Verdict

**CONTRADICT.** The class the ticket describes (an authored `ssh <node>` instruction
whose object is a generic placeholder, not a real hostname, silently falling into the
"prose, not a node mention" bucket instead of `unresolved`) is real, and the fix's
one committed regression case (`ssh the node`, `ssh each node`) genuinely works —
confirmed by running `pytest harness/tests/test_capability_lint.py`, 142/142 pass.

But the fix itself — `_SSH_GENERIC_TARGETS = frozenset({"the", "a", "an", "each",
"every", "any", "that", "this", "some"})` (`harness/capability_lint.py:140`), consumed
at `harness/capability_lint.py:653` — introduces a false-positive class the module's
own design explicitly guards against elsewhere. Putting the bare indefinite article
`"a"` (and, to a lesser extent, `"some"`/`"any"`) into this set means **any** ordinary
past-tense sentence of the shape `ssh a <noun phrase>` — recounting a prior ssh
attempt, not authoring a new one — now lands in `unresolved`, which holds the ticket
pre-spawn exactly like a real capability gap (`dispatcher/gates.py:184-207` →
`dispatcher/foreman_dispatcher.py:976` `enforce_capability`, unconditionally, no
`explicit`/prose distinction the way `_host_shaped` and `_add_restart` both have).

This is not hypothetical — it reproduces on the actual shipped module, not a
reimplementation:

```
$ cd harness && python3 -c "
import sys; sys.path.insert(0, 'tests')
import test_capability_lint as t
for s in ['we tried ssh a few times before it worked',
          'ran ssh a couple different ways to reproduce',
          'that worker attempted ssh a total of five times',
          'ssh a moment ago failed the same way',
          'logs show ssh a nonzero number of times']:
    d = t.derive(s)
    print(repr(s), '->', d.tag_strings, d.unresolved)
"
'we tried ssh a few times before it worked' -> [] ["ssh a: names ssh as an instruction with no resolvable node — file an explicit needs:ssh(<node>) tag per target, or route this ticket to an orchestrator (a sandbox worker cannot invoke ssh directly)"]
'ran ssh a couple different ways to reproduce' -> [] [same]
'that worker attempted ssh a total of five times' -> [] [same]
'ssh a moment ago failed the same way' -> [] [same]
'logs show ssh a nonzero number of times' -> [] [same]
```

Every one of these is exactly the retrospective-narration register this codebase's
own tickets and ISSUE-LOG rows are written in (this very PR's commit message narrates
"wq#2914 was dispatched to a sandboxed BUILD worker 5x... died each time"; a ticket
saying "we tried ssh a couple more times before giving up" is at least as plausible).
The module already has a named defense against exactly this failure mode —
`_host_shaped`'s docstring (`harness/capability_lint.py:102-107`): *"a false
`unresolved` is a ticket slice C would refuse to spawn, while a missed one still
fails closed at the broker"* — and the SSH_ROWS fixture already carries two prose
exemptions tuned against this trap (`"ssh to bes"` → target `"to"`, `"ssh outcome"` →
target `"outcome"`, `harness/tests/test_capability_lint.py:356-359`). The new
determiner set reopens the same trap for the single most common word in English
immediately following a verb ("a"), and no test row covers it — the new test rows
(`test_capability_lint.py:365-367`) only exercise `"the"` and `"each"`, never `"a"`.

### Secondary finding — coverage gap against the ticket's own stated class

The ticket title asks to "route ssh-needing research tickets to an orchestrator,"
i.e. close the class, not just the one wq#2914 literal. `_SSH_RE`
(`harness/capability_lint.py:99`) captures the single token immediately after `ssh`
(or after flags), so the fix only fires when that token is one of the nine
determiners. Equally natural authored instructions using a different filler word
still fall through to `continue // prose, not a node mention` untouched:

```
'ssh into the node and read the log' -> [] []
'ssh onto the node directly' -> [] []
'you should ssh to the node' -> [] []
```

These are indistinguishable in intent from `"ssh the node"` (an authored instruction
with an unresolvable object) but get zero `unresolved` finding — the exact silent-drop
bug wq#2914 hit, just spelled with `to`/`into`/`onto` instead of `the`. The PR's own
commit message calls the set "closed... not a heuristic," which is true for
false-negative safety (no real fleet node is named `the`/`a`/`some`), but does not
make it a class fix for the ticket's stated scope — it fixes the one literal string
wq#2914 used and stops there. This is a scope gap, not a regression; flagged as
secondary to the false-positive finding above, which is the one that changes
already-passing tickets' dispatch behavior.

## What still holds

- The core routing mechanism (unresolved → pre-skip/park before spawn, never a
  guess) is correct and unchanged; `dispatcher/gates.py` `Capability.check` and
  `enforce_capability` wire `derived.unresolved` into the hold path exactly as
  described.
- No roster collision risk today: the fixture/live roster
  (`lumen, isis, thor, thoth, bes, anubis, maat, sobek`) shares no member with the
  determiner set, so the "no real fleet subject can ever equal one of these words"
  premise holds for false-negative purposes.
- 142/142 existing + new tests pass as shipped; the regression this PR targeted
  (`ssh the node`, `ssh each node`) is genuinely fixed.

## Reproduction

```
cd /home/lumen/worktrees/wq2999-a1/harness
python3 -m pytest tests/test_capability_lint.py -q        # 142 passed
python3 -c "import sys; sys.path.insert(0,'tests'); import test_capability_lint as t; \
  print(t.derive('we tried ssh a few times before it worked').unresolved)"
```

Escalating per ticket instructions to opus-5@high — filed as wq#3001.
