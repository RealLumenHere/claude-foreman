# wq#3132 — judgment: wq#3127's fix (PR#1406) contradicted

Verifier: claude-sonnet-5@high, worktree `wq3132-a1`, 2026-09-29 (fresh instance; author
model claude-opus-5-5). Target: `harness/masking.py`'s new kv-secret "echo" suppression
(commit `98e08381`, PR#1406) against the pri-1 rotation row wq#3127 closed as "fixed, nothing
rotated" (mask_replay.py 3122, 3/3 unaccounted values named as upstream litellm identifiers:
`self.secret_key`, `self.upstream_langfuse_secret_key`, `credentials_dict`).

## Ruling — contradiction UPHELD

wq#3127's fix is real for the population it was tested against: `harness/tests/test_masking.py`
adds 10 tests (4 "should suppress", 6 "should still fire") and all pass on shipped HEAD —
verified: `python3 -m pytest harness/tests/test_masking.py -q -k "echo or wq3127"` → 10 passed.

But the new guard's suffix/prefix segments are quantified with `*`/`+` (unbounded repetition),
not bounded to the single extra segment its own comment describes ("the secret key kwarg
handed `self.` plus the label, or the credentials kwarg handed the label plus `_dict`"). A
value shaped as `self.<label>` followed by an **arbitrarily long chain** of `_`/`.`-joined
lowercase-letter segments (each ≤20 chars, per-segment escape already covered by the shipped
tests) is swallowed *in full*, with no length or segment-count cap on the chain as a whole.

This is not a hypothetical: it is a straight regression relative to pre-fix behavior, not a
pre-existing gap. Reproduced with a runtime-built probe (no credential-shaped literal
authored; segments built via list/join at runtime per the masking guidance in this repo's own
CLAUDE.md):

```python
# /tmp/fworker-wq3132/probe_echo3.py (scratch, not committed)
import sys, re
sys.path.insert(0, "/home/lumen/worktrees/wq3132-a1")
sys.path.insert(0, "/home/lumen/worktrees/wq3132-a1/harness")
from harness import masking
from harness.masking import (
    _kv_rule, _KV_LABEL, _KV_POINTER, _KV_NOT_CALLED, _KV_NOT_IDENT, _KV_NOT_SELF,
)

label = "secret_" + "key"
words = ["correct", "horse", "battery", "staple", "extra", "words", "here", "too"]
line = label + "=self." + label + "_" + "_".join(words)

pre_fix_rule = re.compile(
    _kv_rule(_KV_LABEL, _KV_POINTER + _KV_NOT_CALLED + _KV_NOT_IDENT + _KV_NOT_SELF),
    re.IGNORECASE,
)
print("pre-fix (no echo guard) would alarm:", bool(pre_fix_rule.search(line)))
masked, hits = masking.mask_text(line)
print("post-fix hits:", hits, "| fully invisible:", masked == line)
```

Result on shipped HEAD (`98e08381`):

```
pre-fix (no echo guard) would alarm: True
post-fix hits: [] | fully invisible: True
```

`secret_key=self.<label>_correct_horse_battery_staple_extra_words_here_too` (`<label>` is the
echoed label, spelled out by the repro script above; writing it whole here re-arms the alarm
now that wq#3133 bounded the guard) — a line the
**pre-fix** rule set (pointer + call + identifier + self-name guards, no echo) would have
flagged as `kv-secret` — is, on the code as shipped, masked as `[]` hits: no mask, no alarm,
no rotation record, indistinguishable from clean text. No other PATTERNS entry provides a
backstop: every non-kv-secret rule in `masking.py` is a fixed-provider-prefix match (github,
cloudflare, sk-key, slack, aws) and none of them engages on a bare `label=value` line with no
such prefix. Once the echo guard's zero-width lookahead succeeds, `_KV_SECRET` does not match
at that label position **at all** — there is nothing left to catch this shape.

Root cause: `_KV_ECHO_SEG = r"[a-z]{1,20}"` bounds a single *segment*, but both halves of
`_KV_NOT_ECHO` repeat that segment with `+`/`*` with no cap on repetition count or total
length — `(?:[a-z]{1,20}[._]_*)+` (prefix) and `(?:[._][a-z]{1,20})+`/`*` (suffix). The
shipped tests only ever probe a chain of length 0 or 1 (`self.<label>`, `<label>_dict`,
`settings.db_password`), which is exactly the upstream-source shape wq#3127 read off
litellm's `langfuse.py`. A probe drawn from that same population — a short, single-segment
chain — cannot falsify a repetition operator; it takes a probe *outside* that population
(here: an 8-segment chain) per the wq#2630 acceptance-probe doctrine to show the guard has no
upper bound at all.

Practical exposure: this is the module instinct 4 exists to guarantee never goes silent. A
credential that happens to be composed of `.`/`_`-joined lowercase-letter runs (a diceware-
style passphrase, a lowercase word-based recovery token, or simply a secret an attacker/buggy
logger deliberately or accidentally concatenates after `self.<label>`) is now permanently
invisible to kv-secret if it sits immediately behind the label — worse than the pre-fix state
for that shape, and with no compensating rule elsewhere in the file.

## Not re-implemented

Per this ticket's instruction, no fix was written here. `harness/masking.py` and
`harness/tests/test_masking.py` are unmodified in this worktree; both probes live only under
`/tmp/fworker-wq3132/` (scratch, not committed).

## Escalation

Filed per `verify_pass.escalate_on_contradiction`: follow-up fix ticket wq#3133
(model=claude-opus-5, effort=high, archetype=standard-engineering, lane=build, pri=2, refs
wq#3132/wq#3127), mirroring wq#3126 → wq#3129. wq#3127 noted directly (`wq note 3127 ...`)
since it is `status=done` and `wq review` only accepts `pending_review`. ISSUE-LOG row
appended.

## Acceptance evidence

- `python3 -m pytest harness/tests/test_masking.py -q -k "echo or wq3127"` → 10/10 pass
  (fix's own probes still green; not the thing in dispute).
- `python3 -m pytest harness/tests/test_masking.py -q` → 159 passed, 1 unrelated pre-existing
  failure (`test_the_mask_suite_does_not_arm_its_own_tripwire`, a literal `sk-key`-shaped
  fixture in `harness/tests/test_read_dir_sweep.py`, a file wq#3127's diff never touched — not
  caused by and out of scope for this verify-pass).
- `python3 /tmp/fworker-wq3132/probe_echo3.py` → shows the exact same label+guard chain that
  the pre-fix rule set would have flagged now returns zero hits on shipped `98e08381`, so the
  probe is outside the fix's own design population per wq#2630 and can fail (and does).
