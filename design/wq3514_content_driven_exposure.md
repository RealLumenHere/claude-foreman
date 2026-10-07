# wq#3514 — content-driven exposure detection (exposure-census, selfheal class 8)

Class behind wq#3512: leakwatch (class 3) only watches directories listed in
`selfheal.yaml`, so every new secret-bearing dir is blind until someone remembers to
add it. The census2 prototype (`/home/lumen/scratch/fix-1/census2.py`) inverted the
model — harvest the live secret *values* from the known stores, then flag any file in
the tree that holds an exact live value and is actually readable — and proved the walk
is cheap (402k files, 5.5 min) but the harvest is noisy: 5334 files "held a live
value" because the entropy filter admitted non-secrets (`VERIFY_JUDGE_MODEL`,
`R2_ACCOUNT_ID`, `AMAZON_ASSOCIATE_TAG` dominate `census2_hits.txt` — model names and
account ids pass a character-class test).

Shipped as `ops/wq3354-fleet-selfheal/exposure_census.py`. Decisions, in ticket order:

## 1. Calibration: label-gated harvest, not a sharper entropy knob

A character-class/entropy filter cannot separate `openai/gpt-4o-2026` from a real
key — both are long multi-class opaque strings. What *does* separate them is the name
on the line, and the house already maintains a battle-tested credential label
vocabulary: `harness/masking.py` (kv-secret labels, known prefixes `ghp_`/`sk-ant-`/…,
two-phase union, wq#2297/2806/3154 lineage). So the harvest runs `masking.read()` over
each store line and keeps only values masking itself would redact, plus two shapes
masking is not aimed at: basic-auth URL passwords (`://user:pass@`, covers
`.git-credentials`) and whole-file raw tokens (`.secrets/*` single-token files).
census2's class filter is kept as a floor (≥12 chars, ≥3 character classes) so an
exact match stays evidence rather than coincidence.

Second layer, because no gate is perfect: **prevalence demotion**. A digest found in
more than `prevalence_cap` (default 20) distinct files is demoted to a calibration
note — a real live credential does not legitimately appear in dozens of files, but a
mis-harvested config string does. This is the triage-before-alarming the ticket asks
for: a bad harvest produces one note naming the source key, never a flood.

## 2. Scheduled sweep, not a leakwatch extension

inotify cannot cover the class: content-driven means *every* dir in the tree is in
scope, and a recursive watch over ~/ (tens of thousands of dirs, heavy churn in
worktrees/caches) blows watch limits and still has to read file content on every
event. The walk takes minutes; the selfheal sweep timer fires every 1 min. So:
own oneshot unit `lumen-exposure-census.service` + daily timer, nice 19 / idle IO,
own state file (the 1-min sweep and a 6-min census sharing `state.json` would race
read-modify-write). The timer is added to `guards.timers` so class 1 restarts it if
it goes missing. leakwatch stays as the real-time layer on known stores; the census
is the daily safety net that finds the dirs leakwatch doesn't know — its ticket text
tells the worker to add the offending dir to the leakwatch rules.

## 3. Values never printed, never persisted

Only `sha256` digests leave `harvest()`: the live map is `{digest: source_name}`,
target tokens are hashed and looked up by digest, reports carry `sha12=` + the source
*name* (`agent/.env:SOME_KEY`), state persists per-dir signatures of names+sha12s.
Listing lines are sha12-first (wq#3547: a label glued ahead of a fingerprint reads as
kv-secret) and the tests assert `masking.scan(ticket_detail)` stays clean and that no
planted value appears in any outlet text or saved state.

## 4. Traversal: exposure requires a reachable path

A world-readable file under a 0750 home is not world-exposed. A hit only alarms when
every ancestor directory grants the matching traversal bit: `o+x` all the way down
for world exposure, `g+x`-or-`o+x` for group exposure. Ancestor modes are cached per
dir, so the check is O(depth) once per directory.

## Group-readable files with live values: alert-only, same bar as wq#3456

Decision: they get the leakwatch treatment, not tickets. A group-readable hit alarms
(aq alert, once per dir-episode) only when `leakwatch.group_readers()` shows the
group reaching an account besides the owner — a user-private group (0640 lumen:lumen)
reaches nobody and stays silent. No chmod: unlike leakwatch's rules, census hits are
arbitrary files a service may legitimately serve through its group.

## Action: report-only

The census never chmods or edits. A live value in a world-reachable file is burned
whether or not we flip a bit afterwards — rotation is the fix — and mass-chmodding
arbitrary matched files (web content, shared configs) can break things leakwatch's
curated store list cannot. One ticket per offending *dir* per episode (page urgent),
`max_dir_tickets` per sweep with an overflow alert, resolve when a later complete
sweep finds the dir clean. A sweep that hits `time_cap_s` skips the resolve phase so
a partial walk never closes a real episode.
