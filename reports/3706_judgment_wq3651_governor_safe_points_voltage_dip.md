# wq#3706 — judgment: wq#3699 contradicted wq#3651 (governor safe-points voltage dip)

Judge: claude-opus-5, worktree `wq3706-a1`, 2026-10-08.
Upstream: `reports/wq3699_verify_wq3651_governor_safe_points_voltage_dip.md` (verify-pass,
CONTRADICT).

## Verdict: the contradiction is upheld, and it is worse than the verifier could see

Re-confirmed independently before ruling, not taken on the verifier's word:

```
$ git show 7b3b5a42:ops/wq3651-bc250-bios-flash-governor/RUNBOOK-ADDENDUM.md | safe_points
[(1400, 750), (1500, 875), (1600, 800)]   dips: ['1500MHz/875mV -> 1600MHz/800mV']
$ sed -n '168,193p' ops/wq1337-hapi-bringup/RUNBOOK.md   # pre-ruling table
500/700  1175/700  1400/750  1600/800  1700/850  1800/900
```

The verifier's other seven findings are not in dispute and were spot-checked: the
ROCm/Ollama correction, the `lumen_state` quotes, the 40CU unlock, the
`ttm.pages_limit=4194304` = 16GiB arithmetic and the hedging of the BIOS unknowns all
hold. One defect, and it is the one it named.

What the verifier could not reach is the prior art. The published BC-250 governor
documentation (<https://elektricm.github.io/amd-bc250-docs/system/governor/>, fetched via
`fleet-search read`) carries four `[[safe-points]]` tables. Every one of them is
non-decreasing, and every one that lists 1500MHz puts it at **exactly 900mV**:

```
500/700  1000/800  1175/850  1500/900  1600/910  1700/920  1850/930  2000/960
1000/700  1500/900  2000/1000  2150/1000
1000/700  1500/900  2000/1000
1000/700  2000/1000  2100/1050
```

So `RUNBOOK.md`'s claim to have started "from the upstream example `config.toml` and
truncate[d]" (line 154) does not match what upstream publishes: the shipped
800/850/900 trio sat *below* the published curve at every point above 1175MHz. The dip
wq#3699 found is the visible symptom of that; the undervolt was already latent.

## Ruling 1 — do not reopen wq#3651; discharged here

Reopening buys nothing, and the verifier reached the same conclusion for the right
reason. Nothing is live: hapi-1 is on the bench awaiting thermal pads (`lumen_state
current hapi-1` → `hapi-1.bringup_eta`, since 2026-09-02), so there is no box carrying a
bad `config.toml` and no window in which one could be written. wq#3651's close was
honest about what it delivered and about what it could not execute; the single wrong
number is a doc defect with no deployed consequence, which is precisely the wq#2943
Ruling 1 shape — a reopen would move a row between states while changing nothing on any
box. Fixed by follow-up edit in this ticket, recorded as ISSUE-LOG row 1247 (the dip and the
missing prior-art search) and row 1248 (the WebFetch grant gap that made the prior art
harder to reach than it should have been).

## Ruling 2 — 1500MHz goes to 900mV, and the rows above it go up, not the row itself down

Two ways to make the table monotonic. Only one of them keeps the measured number.

**Rejected: push 1500 down below 800mV.** It would preserve ordering by undervolting the
one frequency hapi-1's entire cooling plan was validated at. 1500MHz/900mV is measured
three independent ways — the four published tables above;
`agents/work-queue/reports/425_hapi1_bringup_runbook.md:13` ("40CU@1500MHz/900mV = ~83C
sustained INDEFINITELY" on a stock heatsink, where 2GHz throttles); and `:79-83`, which
names it the operating point and says to tune *upward* from 900mV on soak failure
("+15-25mV steps, never >1075mV; prefer dropping freq"), never downward. 900mV is also
the top of wq#3651's own requested 850-900mV range, so nothing is overridden to reach it.
Nothing on record supports 1500MHz at under 800mV, and an undervolt on an inference node
fails as a hang or as silently wrong compute — the worst available failure mode.

**Ruled: 1500/900, 1600/910, 1700/920, 1800/930.** The three upper values are the
published table's own (1600/910, 1700/920, 1850/930), not interpolation. 1800MHz stays
the ceiling *frequency*; only its voltage moves. Applied to both docs:

- `ops/wq1337-hapi-bringup/RUNBOOK.md` §6 — the table a human copies into `config.toml`.
  Fixed at the source rather than left for the addendum to patch mentally, because two
  docs holding two different tables for one config file is the same class of
  contradiction this ticket exists to settle.
- `ops/wq3651-bc250-bios-flash-governor/RUNBOOK-ADDENDUM.md` — same five rows, plus the
  full provenance and the rejected alternative, so the reasoning survives the next edit.

Two consequences recorded rather than papered over:

- **The 65W figure is now a prediction, not a given.** `RUNBOOK.md:195-198` derives "65W
  at 1800MHz" from the SOT thermal table against a 900mV ceiling. 930mV is ~+3% voltage,
  so expect single-digit percent more package power at the ceiling. §6 already says to
  confirm with `bench.sh`'s power logging; both docs and the §11 summary row now say that
  this specific number is what to confirm. Adjusting 1700/1800 *downward* on bench data
  is safe — it keeps the table monotonic.
- **The low end is left alone, deliberately.** The published table puts 1000 at 800mV and
  1175 at 850mV where `RUNBOOK.md` ships 1175/700; other published tables use 1000/700.
  That spread is real, but the shipped low end is monotonic and above the governor's hard
  700mV floor, so no number was invented for it. Flagged in the addendum as a bench
  question, not guessed at here.

## Ruling 3 — yes: `--verify` handler landed for `wq1337-hapi-bringup`

`ops/wq1337-hapi-bringup/install.sh` is new in this ticket, close to the verifier's draft
with three changes. It takes the `--verify)` case-arm form of the reference remote bundle
(`ops/wq2286-gitea-secret-scan/install.sh:23-50`) and checks locally when run *on* hapi-1,
remotely otherwise; `HAPI1_HOST` overrides the ssh target, because the host does not exist
yet and its eventual name/address must be looked up, not assumed (instinct 1); and it says
in-file why `bench.sh` and `litellm-hapi1-dark.yaml` are *not* checked (a hand-run bench
tool and a deliberately dark snippet gated on wq#266, `RUNBOOK.md` §10 — neither is an
installed artifact).

Landing it costs no alert noise while hapi-1 is vapour: `ops-deploy-drift:160` opens an aq
row only on `VERIFY_FAIL`, and an unreachable host is `VERIFY_ERR` per
`ops/_verify.sh:117-120`. ERR is the correct standing verdict until the card is assembled —
"I could not look" must not read as "it is not deployed" (wq#2985) — and the handler turns
into a real liveness probe on the day hapi-1 answers, with no further work. No
`DEPLOY_TRIAGE.md` row: that register disposes of bundles a content index wrongly read as
ABSENT, and this bundle is not deployed, correctly, for a reason no index has to infer.

Executed, not asserted (the verifier's lane could not run it; this one could, via the
house `subprocess` pattern `ops/tests/test_wq1892_verify.py` uses):

```
$ bash -n ops/wq1337-hapi-bringup/install.sh                      → rc 0
$ bash ops/wq1337-hapi-bringup/install.sh --verify                → rc 2
ERR unreachable: hapi-1 / VERIFY ERROR
$ HAPI1_HOST=anubis bash ops/wq1337-hapi-bringup/install.sh --verify   → rc 1
MISS anubis: absent /etc/systemd/system/llama-server.service
MISS anubis: llama-server.service not enabled (not-found)
MISS anubis: llama-server.service not active (inactive)
VERIFY FAIL (3)
$ bash ops/wq1337-hapi-bringup/install.sh                         → rc 1, manual-only notice
```

The `anubis` run is the acceptance probe that can fail (wq#2630): a host from outside
`HOST`'s default, reachable, read-only, where the payload is genuinely absent — proving
the handler reaches a real node and distinguishes FAIL from ERR, which an hapi-1-only
probe could never have shown.

## Ruling 4 — no: `wq3651-bc250-bios-flash-governor` needs no handler

It ships one markdown file and nothing else. `ops-deploy-drift:12,136` classifies that
`NOTHING` ("ships no installable files (docs/diffs only) -> needs a human look"), and
`:153-158` only alerts on authoritative verdicts, so a docs-only bundle never opens a row
and never needs a marker to stay quiet. `VERIFY_CONTRACT.md:60` ("a bundle that installs
nothing at all still needs a handler") is about the `vflag` case — a bundle whose payload
shells out to another bundle's binary. This addendum shells out to nothing; it is prose a
human reads at the bench. A handler here would be `vfile` against itself: a gate that
cannot fail, which is the defect `VERIFY_CONTRACT.md:51-60` was written to stop, not a
box to tick. No `RETIRED` marker either — the bundle is current, not retired.

## What stops this recurring

`ops/tests/test_wq3706_safe_points_monotonic.py` (4 cases, in the 224-test `ops/tests`
suite, all green). It sweeps every `[[safe-points]]` block in every `ops/**/*.md` for a
step where frequency rises and voltage falls, and asserts any doc that tables 1500MHz
holds it at 900mV. Its falsifiability is pinned, not assumed: `BROKEN_FIXTURE` is the
table wq#3651 actually merged, committed verbatim, and the checker reports the dip on it.
Re-run against the immutable revision:

```
$ python3 -c "...safe_points(git show 7b3b5a42:...RUNBOOK-ADDENDUM.md)..."
pre-fix  [(1400,750),(1500,875),(1600,800)]  dips: ['1500MHz/875mV -> 1600MHz/800mV']
post-fix [(1400,750),(1500,900),(1600,910),(1700,920),(1800,930)]  dips: []
```

## For whoever has the card in hand

The whole upper table is paper until `bench.sh` runs on real silicon. Treat 910/920/930
as the published starting point, not as validated hapi-1 numbers, and the soak procedure
in `425_hapi1_bringup_runbook.md:79-83` as the authority: 10-minute
`memtest_vulkan`/`llama-bench`, +15-25mV only on failure, never above 1075mV, and prefer
dropping frequency to raising volts. If the thermals say the ceiling is too hot, lower the
1700/1800 rows — downward keeps the table monotonic and the test green.

`/home/lumen/projects/bc250/BC250_PROJECT.md` (the SOT the 65W figure comes from) is still
outside this worker class's grant, as ISSUE-LOG rows 129/141 record — the 65W re-measure
cannot be reconciled against the SOT table from a worker lane until that grant lands.

## Evidence commands (read-only, re-runnable)

```
git -C /home/lumen/foreman show 7b3b5a42:ops/wq3651-bc250-bios-flash-governor/RUNBOOK-ADDENDUM.md
python3 /home/lumen/foreman/ops/tests/test_wq3706_safe_points_monotonic.py
bash /home/lumen/foreman/ops/wq1337-hapi-bringup/install.sh --verify            # exit 2, ERR
HAPI1_HOST=anubis bash /home/lumen/foreman/ops/wq1337-hapi-bringup/install.sh --verify  # exit 1
/home/lumen/bin/fleet-search read "https://elektricm.github.io/amd-bc250-docs/system/governor/"
grep -n '1500MHz/900mV' /home/lumen/agents/work-queue/reports/425_hapi1_bringup_runbook.md
python3 /home/lumen/minerva/lumen_state.py current hapi-1
grep -n 'NOTHING' /home/lumen/bin/ops-deploy-drift
/home/lumen/bin/wq show 3651
```
