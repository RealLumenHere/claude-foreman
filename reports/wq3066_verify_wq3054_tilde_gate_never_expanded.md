# wq#3066 verify: wq#3054 (PR#180) — CONTRADICTED

**Verdict: CONTRADICT.** wq#3054's central claim — "the guard (`bad_gate_clauses`) and the
reader (`gate_met`) share one parse, so they can never disagree about what a clause means" —
is false for `~`-prefixed enforced clauses. `bad_gate_clauses()` accepts a `~/path[:exists]`
clause as a valid, satisfiable, enforced gate (it is explicitly documented as one of the two
legal forms, alongside an absolute path). `gate_met()` never calls `os.path.expanduser()`, so
it checks `os.path.exists()` against the literal string `~/path` — which is not a real
filesystem path on any POSIX system and can never exist. The clause is therefore
enforced-and-unsatisfiable forever: exactly wq#3033's original bug class
("`claim --id` refused forever"), reproduced through a form the wq#3054 guard itself calls
legal and *teaches in its own refusal message* (`print_bad_gate()`:
"a comma list of enforced file paths (absolute or ~, path[:exists|missing])").

## Reproduction (falsifiable, run against the shipped binary)

`wq` at `/home/lumen/bin/wq`, commit `8c44a2b` (PR #180, `lumen-bin`), scratch DB, zero-quota
subprocess harness identical to `tests/test_wq3054_gate_label_markers.py`:

```
gate = "~/.bashrc:exists"          # ~/.bashrc genuinely exists on this box
wq add --gate "~/.bashrc:exists"   # bad_gate_clauses() ACCEPTS -- head.startswith('~') is true
wq claim --id <tid>                # REFUSED: cannot claim wq#<tid> by id -- gate: ~/.bashrc missing
```

Full output:

```
REFUSED: cannot claim wq#1 by id — gate: ~/.bashrc missing
  wq#582: every claim-by-lane guard applies to claim --id, none relaxed, and
  there is deliberately no --force.
```

`~/.bashrc` exists on disk the whole time (verified: `os.path.exists(os.path.expanduser("~/.bashrc"))`
is `True` on this host, in the same account the `wq` process runs as). The refusal is not
"not yet created" — it is structural: no process will ever create a file whose name is the
literal two characters `~` in the CLI's cwd, so `os.path.exists("~/.bashrc")` is `False` on
every future check as well. This is not a timing issue; it can never pass.

Minimal repro script, run via `pytest -s`:

```python
import os, subprocess
WQ_BIN = "/home/lumen/bin/wq"

def wq(env, *args):
    return subprocess.run([WQ_BIN] + [str(x) for x in args], capture_output=True, text=True, env=env)

def test_tilde_enforced_clause_is_accepted_but_never_satisfiable(tmp_path):
    env = dict(os.environ, WQ_DB=str(tmp_path / "scratch_wq.db"),
               WQ_AGENT="test-tilde-probe",
               WQ_DISPATCHER_CONFIG=str(tmp_path / "no-such-dispatcher-config.json"))
    p_add = wq(env, "add", "--lane", "auto", "--pri", "1",
               "--title", "tilde gate probe", "--gate", "~/.bashrc:exists")
    assert p_add.returncode == 0                      # bad_gate_clauses() accepts it
    tid = int(p_add.stdout.strip().split()[2])
    p_claim = wq(env, "claim", "--id", str(tid))
    assert "gate:" not in (p_claim.stdout + p_claim.stderr)   # FAILS -- "gate: ~/.bashrc missing"
```

Result: `1 failed` — the accept-case fails exactly as shown above. (Full command run this
session: `python3 -m pytest /tmp/fworker-wq3066/test_tilde_probe.py -q -s`.)

## Why this is in scope for wq#3054, not just an inherited wq#3033 defect

The `head.startswith('/') or head.startswith('~')` acceptance test itself is old — it shipped
unchanged in wq#3033 (`lumen-bin` PR #178, `52f576b`) and wq#3054 (PR #180) kept it verbatim
inside the new shared `gate_clause()`/`bad_gate_clauses()` split. But wq#3054 is not a no-op
refactor of that line: it is a judgment call whose entire stated justification is that
**mirroring `gate_met()`'s own parse eliminates any possibility of the guard and the reader
disagreeing about a clause** (report `wq3054_judgment_wq3033_gate_label_markers.md`: *"The
judgment is really about which function owns the definition of a gate... There is now one."*).
That invariant is what I was asked to adversarially break, and it breaks: for `~`-prefixed
clauses, `bad_gate_clauses()` (via `gate_clause()`) says "this is a legal, enforceable path,"
and `gate_met()` (via the *same* `gate_clause()`) evaluates it against a raw un-expanded
string that no real path check can ever satisfy. The two halves of the "one parse" did not
just fail to newly diverge — the shared parse converges on a value (`head` starting with
`~`) whose downstream treatment (`os.path.exists(head)`, no `expanduser`) was never actually
unified, only the tokenizer was. wq#3054's own evidence section claims exhaustive boundary
coverage ("prose, relative paths, bare labels") but never probes the `~` acceptance branch it
re-ships and re-documents in `print_bad_gate()`'s user-facing text.

Severity: `~` is not a rare or contrived input — it is one of exactly two forms the tool's own
refusal message actively teaches ("absolute **or ~**, path[:exists|missing]") to any agent
that trips the guard on a relative path and reads the hint. Following that hint literally
(`~/some/report.md:exists` instead of `/home/lumen/.../report.md:exists`) silently strands the
ticket forever, with a `claim --id` refusal (`gate: ~/... missing`) that gives no hint the
path was never expanded. `queue.db` currently has zero live `~`-gated rows (checked this
session), so nothing is stranded today — but the trap is live and the documented/legal path to
it is real.

## What still holds from wq#3054

- The label-marker fix wq#3047 demanded is real and correct: `deploy_pending:<report>`,
  `frank-bench-time:hold`, and the three UI rewrite markers all round-trip through
  `add`/`edit`/`claim` exactly as documented (re-ran `tests/test_wq3054_gate_label_markers.py`
  this session: `13 passed`).
- Prose (bare and colon-bearing) and relative paths still correctly refuse.
- `--force` still overrides on both `add` and `edit`.
- The one-parse refactor (`gate_clause()`) is a genuine improvement in structure — it just does
  not deliver the "can never disagree" guarantee its own writeup claims, because that guarantee
  was never really about tokenizing the clause; it was about whether the *same string* means
  the same *satisfiability* in both places, and `~` breaks exactly there.

## Fix shape (not implemented here — verify-pass, not re-implementation)

`gate_met()` should resolve `head` through `os.path.expanduser()` (and ideally
`os.path.abspath()` against a fixed base, matching the "cwd-dependent relative paths are
refused" rationale already in the guard) before `os.path.exists()`. That one-line fix in the
reader would make the existing `bad_gate_clauses()` acceptance test true instead of aspirational.

## Escalation

Per wq#3066's own archetype (verify-pass, contradiction escalates to opus-5@high, judgment
trigger): filed wq#3068 (`[judgment] wq#3066 verify-pass CONTRADICTED wq#3054: gate guard
accepts '~' as a legal enforced clause, gate_met() never expands it`), `deps=3054`,
`model=claude-opus-5`, `effort=high`, `archetype=rca-investigation`, mirroring how wq#3047's
contradiction of wq#3033 was escalated as wq#3054.
