# wq#1675 — read-only subsystem mode (`<name>:ro`)

## The block

`subs_free` asked one question of every declared subsystem: *is this scope held by someone who
is not me?* A ticket that only READS a subsystem answered it identically to one that rewrites
the subsystem, so two analyses of one thing serialized for no reason.

That is how wq#1668 (ticket lifecycle + closure-quality audit) sat unclaimable behind wq#1667
(foreman dispatch + governor walkthrough). Both are archetype `research-synthesis`, both
declared `subsystems=foreman`, and neither wrote a byte of foreman — the lock could not tell
two readers from two writers. Same class as the #569/#571 minerva-db block, found again by
`research/TICKET_CLOSURE_AUDIT.md` (claude-foreman#414) as F7.

## The mode

A declared subsystem token is now `<base>`, `<base>:rw` or `<base>:ro`.

| token | lock | refused by |
| --- | --- | --- |
| `foreman`, `foreman:rw` | EXCLUSIVE — one holder, as it has always been | a live writer, **or any live reader** |
| `foreman:ro` | SHARED — unlimited concurrent holders | a live writer only |

Bare is `:rw`, and every `subsystems` value in the live queue is bare, so no existing ticket
changes behaviour. `wq add --archetype research-synthesis|verify-pass` records its subsystems as
`:ro` and prints a NOTE saying so; an explicit `:ro`/`:rw` on the command line is never rewritten.

Live, on a scratch queue:

```
$ wq add --subsystems foreman --archetype research-synthesis --title "audit A"
NOTE (wq#1675): archetype research-synthesis is read-only, so foreman recorded as shared (:ro)
 — other analysis tickets may hold the same subsystem at the same time, and any writer is
refused while they do. Pass '<name>:rw' if this ticket will actually write the subsystem.
added task 1 [auto] subsystems=foreman:ro

$ wq claim audit-a --id 1     -> CLAIMED 1 ... LOCKED subsystems: foreman:ro
$ wq claim audit-b --id 2     -> CLAIMED 2 ... LOCKED subsystems: foreman:ro     # wq#1668 unblocked
$ wq claim builder --id 3     -> REFUSED: cannot claim wq#3 by id — subsystem foreman
                                 held by audit-a +1 more (read-only)
```

## Why readers live in their own table

`slocks` is keyed `scope TEXT PRIMARY KEY` and is shared with `bin/slock`, `queue-status`, `mctl`
and `lumen-sqlutil`. A reader *set* cannot be expressed in one row, and widening that key would
change a table four other tools read. So readers go in an additive `slocks_ro(scope, holder, …)`
keyed `(scope, holder)`; writers keep using `slocks` byte-for-byte as before. That is why every
other consumer of `queue.db` is untouched by this change.

`slock` is patched only so its own writer path is not blind to the new table: `slock acquire`
now refuses while live readers hold the scope (an exclusive hold taken there would write under
them), and `slock who` lists read holds with a `READ-ONLY` marker. `slock` never *takes* a read
hold — it is an exclusive-lock tool.

## The opt-out is load-bearing

This ticket, wq#1675, is itself archetype `research-synthesis` on `wq-tooling` — and it *writes*
`wq`. Defaulting analysis archetypes to `:ro` is right for the common case and wrong for this
one, so the default exists alongside a one-token escape: `--subsystems wq-tooling:rw`. The NOTE
printed at add names the escape, because an orchestrator that cannot see the default cannot
correct it.

`orch:<lane>` scopes are unaffected: `split_sub` only recognises `:ro`/`:rw` as modes, so
`orch:build` stays one base name rather than becoming base `orch` — collapsing every lane's
orchestrator lock into one would have been the quiet way to break this.

## Deploy

```
ops/wq1675-subsystem-ro/ensure_wq_subsystem_ro.py --check       # 12 anchors across wq + slock
ops/wq1675-subsystem-ro/ensure_wq_subsystem_ro.py --apply       # backup, verify, roll back on failure
ops/wq1675-subsystem-ro/ensure_wq_subsystem_ro.py --selftest    # re-run the checks any time
```

Idempotent, same keeper shape as `ops/wq1498-dead-worker-release` and `ops/wq1479-close-verdict`:
anchored string patches, each asserted to match exactly once; both candidates proven on a scratch
`queue.db` before either replaces a live binary; a failed post-apply run rolls both files back
from timestamped `.bak.wq1675.*` backups (`*.bak*` is gitignored in lumen-bin).

`--selftest` is the regression test. Ten scenarios: the add-time `:ro` default lands; explicit
`:rw` opts out; a non-analysis archetype is untouched; two readers hold one base at once; a writer
is refused under them and the refusal names a reader; `slock acquire` is refused under the same
readers and `slock who` lists them; releasing every reader frees the base; a reader is still
refused under a live writer; two bare tickets still serialize; `orch:<lane>` survives the split.

Applied live 2026-09-11: `/home/lumen/bin/wq`, `/home/lumen/bin/slock`.
