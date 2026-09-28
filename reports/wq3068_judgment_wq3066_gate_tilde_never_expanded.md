# wq#3068 judgment: wq#3066 CONTRADICTED wq#3054 — `~` is not a legal gate path

**Decision: drop `~` from the accepted enforced-clause forms. The reader is not taught to
expand it.** An enforced `--gate` clause must be an absolute path. `label:value` markers,
absolute paths and `--force` are unchanged, and `gate_met()` is untouched, so no stored gate
changes meaning. Shipped in lumen-bin PR #181 (`e45b16715d`), with
`tests/test_wq3068_gate_tilde_refused.py`.

## What wq#3066 found

`reports/wq3066_verify_wq3054_tilde_gate_never_expanded.md` stands in full.
`bad_gate_clauses()` accepted `~/x:exists` as a legal ENFORCED clause (`head.startswith('~')`,
shipped by wq#3033 / PR #178 and re-shipped verbatim by wq#3054 / PR #180), while `gate_met()`
never calls `os.path.expanduser()` and so ran `os.path.exists()` on the literal string `~/x`.
`wq add --gate "~/.bashrc:exists"` was accepted; `wq claim --id <tid>` was then refused forever
with `gate: ~/.bashrc missing`, with the file present the whole time. That is wq#3033's own
"claim refused forever" class, reachable through a form `print_bad_gate()` advertised in its
own refusal text ("absolute or ~, path[:exists|missing]").

wq#3066 is also right that this is in wq#3054's scope, not merely inherited. wq#3054's
justification was not "the parse is tidier"; it was that one shared parse means the guard and
the reader *can never disagree about a clause*. For `~` they disagreed about the thing that
matters — satisfiability.

## The fork, and why the reader does not get `expanduser()`

- **Expand `~` in `gate_met()`** (the cheap one-line option the ticket names first) — rejected.
- **Refuse `~` at write time** — taken.

A gate is a durable predicate: it is written by one process and read back later by whichever
process calls `claim`. `os.path.expanduser()` resolves against the *reading* process's `HOME`.
So expanding in the reader does not make the guard and the reader agree; it makes them agree
*while the writer's `HOME` and the claimant's `HOME` happen to match*. That is a hidden
environmental precondition of exactly the kind this chain (wq#3033 → wq#3047 → wq#3054 →
wq#3066) keeps re-litigating, and the guard already refuses relative paths for the same reason
in its own words — they "resolve against whatever cwd the caller happens to have." Accepting
`~` while refusing `./x` is incoherent on the guard's own terms.

The failure modes are not symmetric either. Today's bug is loud: the ticket is refused, always,
with the path in the message. Reader-side expansion would replace it with a quiet one — a gate
that passes for one claimant and fails for another, on a queue whose whole job is deciding who
may claim what. Trading a deterministic refusal for an environment-dependent one is the wrong
direction even though it costs one line instead of one.

An absolute path names one file in every process. That is the property an enforced clause needs,
and it is the only string form that has it.

### The third option, considered and rejected

Normalising `~` at **write** time (store the expanded absolute path) would keep the ergonomics
*and* the determinism — the stored string would be absolute. It was rejected on drift grounds:
it requires every gate write path to remember to normalise, which is a new invariant of the same
shape as the one that produced wq#3033 (a guard implemented twice) and wq#3054 (two definitions
of one string). Refusing a form adds no invariant; it removes one. If a future ticket wants the
ergonomics back, write-time normalisation is the shape to revisit — not reader-side expansion.

### The cost is measurably zero

- `queue.db`: **0** rows with `~` in `gate` (`SELECT id,status,gate FROM tasks WHERE gate LIKE
  '%~%'` → 0), so nothing is stranded and nothing needs migrating.
- No doc, script or test writes a `~` gate. The only thing that taught the form was
  `print_bad_gate()`'s own refusal text, which this change fixes.
- `gate_met()` is unchanged: the guard is write-path only, so every stored gate keeps the
  meaning it has today.

## What shipped

- `bad_gate_clauses()`: `if not head.startswith('/')` — the `or head.startswith('~')` arm is gone.
- `print_bad_gate()`: names the absolute form and the reason (`gate_met()` does not expand `~`)
  instead of advertising `~`.
- `gate_met()` docstring: records that the path is checked verbatim — no `expanduser()`, no
  `abspath()` — and why.
- `tests/test_wq3033_gate_path_guard.py`: `test_add_accepts_a_tilde_path_gate` inverted to
  `test_add_refuses_a_tilde_path_gate`. It asserted the defect; leaving it green would have
  meant the suite disagreed with the guard.

## Evidence

- `tests/test_wq3068_gate_tilde_refused.py`: **11 passed**.
- Falsifiable (wq#2630): the same file against the pre-fix binary (`8c44a2b`, PR #180, exported
  to `/tmp/fworker-wq3068/prefix/wq`) → **6 failed, 5 passed**. Every refuse-case fails without
  the fix, including the one asserting the refusal no longer teaches `~`.
- The premise is run, not argued.
  `test_a_forced_tilde_gate_strands_the_ticket_even_though_the_path_exists` `--force`s past the
  guard and shows `claim --id` refused with `gate: ~ missing`; its control
  `test_the_same_path_written_in_full_is_claimable` claims the *identical directory* named
  absolutely. That pair is the decision, executed.
- Probe input is pinned and immutable: `HOME` itself, not a live file. `~` and
  `os.path.expanduser("~")` denote the same directory by definition on any account this runs
  as, so the pair isolates exactly one variable — whether the stored head is expanded — with no
  file to create and none to rot.
- Boundary coverage beyond the population the fix was designed around: the `:missing` branch
  (where an unexpanded `~` clause is vacuously *met* — a gate silently enforcing nothing, the
  other half of the same defect), a bare `~` with no colon, a `~` clause mixed into a comma list
  with a good one, the `edit` write path as well as `add`, and an absolute path that merely
  *contains* a `~` (an editor backup file), which must keep working.
- wq#3033 + wq#3054 suites: **23 passed** — both still hold, with the one inverted assertion.
- Regression: full `tests/` (minus `test_rotate_provider_keys_swap.py`, a script-style file that
  `sys.exit`s at import) → **12 failed, 498 passed**. The same 12
  (`test_wq_done_gate_terms.py`, `test_wq_evidence_durability.py`,
  `test_wq_hygiene_reconcile.py`) fail identically against the pre-fix binary — pre-existing.
- Post-land probe against the deployed binary (not the worktree copy): `/home/lumen/bin/wq`
  at `7fde3f0` refuses `--gate "~/.bashrc:exists"` with
  `REFUSED: --gate clause(s) not an absolute path ... (wq#3033, wq#3054, wq#3068)`.
- Logged as row 1018 in `/home/lumen/kb/ISSUE-LOG.md`.

## Disposition

wq#3066's CONTRADICT is upheld. wq#3054's shared-parse refactor is kept — the structure was
right, the guarantee was just overclaimed for one head form, and it is true now because the only
enforced head the guard accepts is one the reader can actually resolve. wq#3033's prose-gate
class and wq#3047's marker-gate fix both remain closed.
