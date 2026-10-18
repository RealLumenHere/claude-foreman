# wq#4222 — verify-pass on wq#4205 / PR#2145 ("sandbox_gate build detection for C/make-based tools")

**Verdict: CONTRADICT.** The build-detection and exit-code propagation the ticket
asked for both work correctly. But the diff also adds a new evidence field,
`install_size_kb`, meant to be a measured footprint of the install step
(`du -sk .`), and that measurement is trivially overridable by the untrusted
install text it is supposed to be measuring: `SIZE_RE.search()` returns the
*first* line in `install_out` matching `^SANDBOX_INSTALL_SIZE_KB=\d+$`, not the
real probe's line, which is always appended last. A proposal that echoes one
matching line before doing anything else gets whatever size it wants recorded
as "measured" evidence, with install_exit and smoke_exit both still reporting
correctly. sandbox_gate's whole reason to exist is running proposals sourced
from scraped, unvetted GitHub repos (`research/AGENTFORGE_EVAL.md` — proposals
are machine-generated from repo titles/intel, no human curation before the
gate runs them) — exactly the actor this field needs to resist.

## What PR#2145 (46b6850b) claims

Adds `BUILD_DETECT` (cmake/make autodetection by file presence, never by
project name) and `_wrap_install()` (`research/sandbox_gate.py:85-93`), which
wraps the proposal's own install text as
`"{install} && ({BUILD_DETECT}); _rc=$?; {probe}; exit $_rc"`, where `probe`
is `echo "SANDBOX_INSTALL_SIZE_KB=$(du -sk . 2>/dev/null | cut -f1)"`
(line 92). `gate()` then does `SIZE_RE.search(install_out)` (line 207) and
stores the match as the new `install_size_kb` evidence field. Ticket's own
docstring on `_wrap_install` (lines 86-91): "a failed build still fails
install_exit the same way a failed install already does, and the probe output
rides the existing stdout capture." Confirmed true for failed/absent builds —
that part of the claim holds (see Break attempt #1 and #3).

## Break attempt

**1. Build-failure propagation — holds.** A Makefile project whose `cc` step
fails to compile returns `install_exit=2`, `smoke_exit=None` (smoke correctly
skipped), consistent with how a pre-diff failed install already behaved:

```
$ python3 research/tests/_adhoc_wq4222_probe.py   # fixture: syntax-broken widget.c + Makefile
{"install_exit": 2, "smoke_exit": null, "install_size_kb": 12}
```

**2. CMake path and the no-build-file (pip/apt-style) path — hold.** Both the
new CMake branch and the pre-existing "neither CMakeLists.txt nor Makefile"
branch pass with `install_exit=0`, `smoke_exit=0`, confirming the change is
backward-compatible with non-C proposals (the `if/elif/fi` with no matching
branch exits 0, same as a bare successful install always did).

**3. `install_size_kb` is spoofable by the proposal it is supposed to measure
— break.** `SIZE_RE.search()` (line 207) is `re.search`, which returns the
*leftmost* match in `install_out`, not the last one. The real probe's `echo`
always runs last in the wrapped command (line 93), but nothing stops the
proposal's own `install` text from printing an earlier line in the same
shape. Reproduced:

```python
spoof = ('echo "SANDBOX_INSTALL_SIZE_KB=1" && '
         "printf 'hi\\n' > out.txt")
sandbox_gate.gate(proposal_with(install=spoof, smoke="cat out.txt"),
                   backend="local-bwrap", evidence_dir=tmp)
# -> {"install_exit": 0, "smoke_exit": 0, "install_size_kb": 1}
```

`install_size_kb` comes back `1` — the attacker's chosen value — not the
real `du -sk .` measurement of the directory the proposal actually populated
(which would include `out.txt` and would not be `1`). The proposal's own text
fully controls the number that lands in `evidence/<id>.json` as a "measured"
field, with no indication anywhere in the evidence that the value is
self-reported rather than probed. This is a new attack surface: before this
diff, `install_size_kb` did not exist, so there was nothing to spoof.

## Why this matters despite nothing currently gating on it

`bridge.py` does not yet read `install_size_kb` for any admission decision
(`grep -n install_size_kb research/bridge.py` — no hits); it is schema-checked
by `EVIDENCE_FIELDS` (line 54) but otherwise inert today, which is why the
ticket's own 15-test suite and the 40-test full `research/tests/` run both
pass clean — nothing exercises the collision. But the field's own design
comment calls it a measurement meant to survive into `evidence/<id>.json` as
evidence a human or a future gate step reads to judge a proposal's footprint
(`EVIDENCE_FIELDS`'s own header comment: "bridge.py validates against this").
A field documented as measured-not-trusted-input that can be fully overridden
by the untrusted input it measures is exactly the failure mode sandbox_gate's
other design choices (masking before tail, no-egress smoke, CLEAN_ENV) exist
to prevent elsewhere in this same file.

## Not reproduced as new findings

- Did not check whether any in-flight proposal in `research/proposals/pending/`
  already contains a matching marker line (accidental or deliberate) — this
  report establishes the collision is reachable by construction, not that a
  live proposal has hit it yet.
- Did not attempt a second-order attack (e.g. forging an evidence value large
  enough to make a legitimate small proposal look resource-heavy) — one
  worked reproduction of the primitive is enough to establish the break;
  the direction of the forged value is the attacker's choice either way.

## Reproduction

- `python3 -m pytest research/tests/ -q` → 40 passed (ticket's own probe and
  the full suite both green; this finding is outside what either exercises).
- Ad-hoc script (local-bwrap backend, host has `/usr/bin/bwrap` + cmake/make/cc,
  so `needs_bwrap` tests actually ran, not skipped):
  - failed Makefile build → `install_exit=2, smoke_exit=null, install_size_kb=12`
  - CMake build → `install_exit=0, smoke_exit=0, install_size_kb=288`
  - no build file (pre-existing path) → `install_exit=0, smoke_exit=0, install_size_kb=8`
  - spoofed marker in install text → `install_exit=0, smoke_exit=0, install_size_kb=1`
    (attacker-chosen, not `du`-measured)
- Code cited: `research/sandbox_gate.py:81-93` (`SIZE_MARKER`, `SIZE_RE`,
  `_wrap_install`), `research/sandbox_gate.py:207-208` (`size_match`,
  `install_size_kb` extraction), `research/sandbox_gate.py:54`
  (`EVIDENCE_FIELDS` schema comment citing `bridge.py`).

Not implementing a fix here — this is a verify-pass. Fix shape for the
escalated judgment: anchor the probe so it cannot be shadowed by the
proposal's own output — e.g. a per-run nonce in `SIZE_MARKER`
(`uuid.uuid4().hex`) baked into both the echo and the regex at `_wrap_install`
call time, or take the *last* match (`list(SIZE_RE.finditer(install_out))[-1]`)
on the theory the probe always runs last — the nonce is the more robust of
the two, since "last" still assumes the proposal doesn't append more output
after the shell line finishes (it can: backgrounded jobs writing to the same
fd can still flush after `exit $_rc` returns control to `_run`).
