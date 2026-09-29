# wq#3160 — verify-pass of wq#3154 (kv-secret sed pattern-half guard, PR#1428)

**Verdict: CONFIRM.** wq#3154's fix correctly narrows wq#3153's over-broad guard so it
only suppresses `kv-secret` on the exact sed pattern-half shape it was written for, and
the wq#3156 bypass (a real value starting with `.*#`/`.+/` etc. going undetected) no
longer reproduces. The burned value this ticket names, `sha256:17e43898b34b`, is
independently reconfirmed as a sed pattern-half template string, not a credential — no
rotation needed.

## What changed (PR#1428, landed on main as `0f808944`; branch tip `91471824`)

`harness/masking.py` and `token-engine/token_engine_daemon.py` both had `_KV_POINTER`'s
wq#3153 guard — which refused any value starting with `.*`/`.+` + a sed-delimiter
character, keyed on that 3-char opener alone — replaced with a guard that also requires
the delimiter to be immediately followed by the *same label* that opened the match
(a new `(?P<kl>...)` capture in `_kv_rule`, referenced via `(?P=kl)`):

```
(?!\.[*+][#/@:!~%|,]\^?(?P=kl)[:=])
```

4 files changed, 28 insertions(+), 3 deletions(-): the guard in both copies, plus new
tests in `harness/tests/test_masking.py` and `token-engine/tests/test_daemon.py`.

## Independent reproduction (not re-implementation)

**Preimage of the burned hash.** Reconstructed `17e43898b34b` from the label named in
wq#3156's own report (`API_BEARER_TOKEN`, wq#3150's deploy script) without ever writing
the value as a single literal:

```
label = "API_BEARER" + "_TOKEN"
val = ".*#" + label + "=$" + "token#"
sha256(val)[:12] == "17e43898b34b"   # True, len 27
```

Confirmed exact match — this is the sed pattern-half (`s#^API_BEARER_TOKEN=.*#API_BEARER_TOKEN=$token#`),
not a generated secret. No rotation required.

**Test suites.** Both ran clean, no re-implementation, as shipped:
- `python3 -m pytest harness/tests/test_masking.py -q` → 171 passed
- `python3 -m pytest token-engine/tests/test_daemon.py -q` → 61 passed

**The PR's own replay probe.**
`python3 harness/mask_replay.py 3150` → exit 0, "0 credential-shaped values ... 0
unaccounted"; `54b1b4739062` and `17e43898b34b` both listed under "today's table does
not reproduce" (i.e. now-suppressed pattern-halves, not silently-dropped credentials —
the replay tool still records them for audit, per wq#2677's design).

## Adversarial probes beyond the shipped test suite

Wrote throwaway probes (not committed — none needed for reproducibility beyond the
commands above) against `harness.masking.mask_text` directly, targeting the direction
that matters for a masking rule: can a real, distinguishable secret go **undetected**
because of the new guard. `L = "API_BEARER_TOKEN"`, `real_secret = "Q"*24`:

| input shape | hits | why |
|---|---|---|
| `L=.*#L=$token#` (exact decoy) | `[]` | correctly suppressed — the intended case |
| `L=.*#OTHER_TOKEN=<real_secret>` | `['kv-secret']` | different label on the replacement side — wq#3156's exact bypass — now fires |
| `L=.*#<L, lowercased>=<real_secret>` | `['kv-secret']` | backreference is case-sensitive even though the rule compiles with `re.IGNORECASE` — a case-varied replacement label still fires (safe-direction: over-alarms rather than misses) |
| `L=` + decoy prefix + `<real_secret>` glued directly after the embedded label (i.e. one single value that happens to contain the whole decoy shape as a prefix) | `['kv-secret']` | the outer match is suppressed, but the scan resumes at the *embedded* `API_BEARER_TOKEN=` inside the value and re-matches from there, catching the `real_secret` tail — nothing is silently swallowed |
| same, with `:` instead of `=` before the tail | `['kv-secret']` | same resumption behavior via the `:`/`=` alternation in the guard and `_KV_SEP` |
| sed pattern-half with a `;` delimiter (outside the guard's `[#/@:!~%|,]` class) | `[]` | not a guard effect — `;` was never in `_KV_BODY`'s charset, so the value truncates below the 12-char floor with or without wq#3154's guard; pre-existing, out of this PR's scope |

In every constructed case, the high-entropy tail of a real value still produces a
`kv-secret` hit somewhere in the string — the guard never causes a genuine secret to go
completely undetected, only changes which substring is reported once a same-label decoy
precedes it. That is the same property the shipped
`test_a_wildcard_opener_without_the_label_behind_it_still_fires` /
`test_a_literal_on_the_sed_replacement_side_still_fires` tests already assert, extended
to a few shapes those tests don't cover (case variation, self-embedded decoy).

## Verdict

Confirmed on the board. wq#3154 does what it claims: narrows wq#3153's guard to the
literal sed-substitution shape, restores detection for the wq#3156 bypass class, and the
two burned hashes remain correctly classified as non-credential template text — nothing
to rotate.
