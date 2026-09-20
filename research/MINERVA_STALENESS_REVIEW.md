# Minerva staleness review — fact invalidation & re-verification

- ticket: wq#1607 (auto, p1) · agent `work-minerva-review-1db2ca`
- router: `claude-fable-5 @high` (archetype novel-design)
- date: 2026-09-09 PT · analysis + design only, no code changed
- scope: `lumen_state.py` (bi-temporal facts), `lumen_episode.py` / `archival_notes`
  (episodic), pgvector `research_docs` (research index), `claude_memory.md` (prose),
  plus the artifacts that consume them (LIVE_FLEET_STATE, FLEET_MAP, fleet inventory,
  storage manifest, instincts).

## 0. The finding in one paragraph

The bi-temporal model is sound and does the job it was built for: when a *new value*
arrives, the old one is closed correctly and history stays queryable. Every staleness
failure we have hit is on the other path — the case where **no new value ever arrives**.
Nothing in the system distinguishes "checked today, unchanged" from "nobody has looked
since June." Measured on the live store right now: 830 open facts, **0 with a stored
confidence below 1.0**, 9 with a half-life, and **10 that have ever been re-confirmed
after their initial ingest**. The evidence anchor (`last_confirmed_at`) exists, the decay
function exists, the re-confirm branch in `ingest()` exists — and the single automated
reconciler in the fleet skips all three with `if old == ip: continue`. Meanwhile the only
contradiction scanner we own matches IP-shaped values only, which covers **6 of 312**
superseded facts. So the drift Frank keeps finding (storage manifest, inventory, node
state) is not a gap in the model. It is a gap in *evidence refresh* and *contradiction
reach*, and both are fixable inside the existing schema.

---

## 1. How each layer decides a fact is still valid

### 1.1 `lumen_state.py` — bi-temporal fact store (830 open / 312 closed)

Validity is **purely structural**: a row is current iff `valid_to IS NULL`. There is no
time-based expiry and no evidence requirement. A fact ingested on 2026-06-01 and never
touched again is served with the same authority as one observed an hour ago.

Three clocks exist in the schema; only two are actually driven:

| clock | columns | driven by | state today |
|---|---|---|---|
| world truth | `valid_from` / `valid_to` | supersession on new value | works correctly |
| system knowledge | `ingested_at` / `invalidated_at` | every write | works correctly |
| **evidence age** | `last_confirmed_at` | re-observation of the same value | **effectively dead** |

`effective_confidence()` (read-time decay, never written back) is correct in design and
carries an honest calibration caveat in its docstring. It is inert in practice:
`half_life_days` is set on 9 rows, all `predicate=price`, from the `VOLATILE_HALFLIFE`
resale vocabulary. No fleet predicate is in that map, so `eff_confidence == 1.0` for
821/830 open facts and `--min-confidence` filters nothing. This confirms and extends the
wq#571 audit: the decay path is not merely dormant at recall, it has no inputs.

`ingest()` *does* have the right behaviour — an identical value returns
`action: reconfirmed` and bumps `last_confirmed_at`. Nothing calls it that way on a
schedule (see 2.1).

Ages of the evidence anchor across the 830 open facts:

```
0-7d     60      31-90d   505      >180d      2
8-30d   241      91-180d   22
```

The 505-fact bulge in 31-90d is the July build-out. Those facts have not been
re-observed since; the store cannot tell you which of them are still true.

### 1.2 `lumen_episode.py` / `archival_notes` — episodic (1686 notes)

Validity here is **retrieval-shaped, not truth-shaped**. A note is servable unless it is
(a) an absorbed duplicate in `note_merges`, (b) `revoked_at IS NOT NULL`, or
(c) `superseded_by IS NOT NULL`. `search_v3` now applies all three unconditionally
(`lumen_recall2.py:494-506`) — this closes gap #2 from the wq#575 known-gaps list, which
should be struck from the handoff.

The exclusions are correct and almost never fire:

```
notes total 1686 · superseded 1 · revoked 6 · merged 5
```

So 1674 notes are permanently, equally valid. There is no path by which a note asserting
"Nephthys serves embeddings" becomes less retrievable when the state store learns
Nephthys was decommissioned. The two layers do not talk.

Ranking has no freshness term either: `W_REC = 0.00`, tuned to zero in wq#574 with the
comment "freshness was costing recall, not buying it." **That tuning was right and should
not be reverted** — it was measured on the frozen golden set. But it means the only
defence against serving a stale note is the trap penalty in the bench, and that penalty
currently measures the problem rather than preventing it: `trap_at1_rate = .250` in the
baseline of record (wq#595). One in four contradiction/knowledge-update queries returns
the superseded note above the current one.

Age is not the lever here anyway. Only 3 notes are older than 90 days and none older than
180 — the corpus turns over roughly every 90 days at ~18 notes/day. Time-based forgetting
has nothing to bite on, exactly as the wq#571 research concluded. **Contradiction, not
age, is the right invalidation signal for this layer.**

### 1.3 pgvector `research_docs` — the research index (10,390 docs)

This is the healthiest layer and the model the others should copy. Validity is decided by
**content identity against the filesystem**: `content_hash` + `mtime` + a UNIQUE
`filepath`, and a reconcile pass that DELETEs rows whose file is gone
(`index_research.py:162`). Verified live: 10,390 rows, **0 pointing at a missing file**.

Its residual risk is different in kind and unaddressed: a research document *describing*
fleet state stays retrievable forever with no link to the store that knows the state
changed. The row is not stale — the file really does say that — but the answer is. Doc-
level freshness is unmarked, so a July "Nephthys inference bench" report competes with a
September fact on equal footing.

### 1.4 `claude_memory.md` — prose

Validity is decided by **a human reading it**. There is no expiry, no provenance, and no
mechanical link to the store. `lumen_dream.py` provides the only automated check, and its
reach is described in 2.4.

Live measurements: 5,471 lines, 267 sections, **core floor 10,816 tokens against an 8K
threshold** (flagged ⚠️ in every dream report and not acted on). `lumen_prose_lifecycle`
generates section-demotion proposals; they are proposal-only by standing law, which is
correct, and nobody applies them, which is the gap. Prose staleness here is not an
accident of the design — it is the design working as intended with the human half missing.

---

## 2. Every place staleness accumulates undetected

Ordered by how much drift each one is currently hiding.

### 2.1 The reconfirm-on-match skip (root cause, one line)

`state_reconcile.py:70-72`:

```python
old = cur.get((subj, 'tailscale_ip'))
if old == ip:
    continue          # <-- observation discarded
```

This is the **only** automated re-verification loop in the fleet. It runs daily
(`lumen-state-reconcile.timer`, 17:30 UTC), reads live `tailscale status`, and when the
wire agrees with the store it throws the observation away. Yesterday's run logged
`[APPLIED] no tailscale_ip drift; store matches the wire` — a real, successful
verification of 25 nodes that left no trace in the database. Fifteen `tailscale_ip` facts
still carry `last_confirmed_at = NULL`, meaning the store believes they have never been
confirmed at all, while in fact they have been confirmed ~75 times.

This is the class defect: **verification that produces no record is indistinguishable
from no verification.**

### 2.2 No verifier exists for 12 of 15 predicate families

Even fixed, 2.1 only covers `tailscale_ip`. There is no probe for `lan_ip`, `ssh_user`,
`role`, `status`, `hardware`, `ram`, `storage`, `power_plug`, `kernel`, `os`,
`*_version`, or any `*_status` predicate. The probes largely *exist* as ad-hoc scripts
(`fleet-inv`, `lsblk`/`mdadm` survey, `systemctl`, `plug-ctl`, `smartctl`) — they are
just not wired to the store. `status`-class facts are the most perishable and the least
checked: the oldest are 67, 67, 61, 61, 58 days without re-observation.

### 2.3 "No drift" and "nothing checked" print the same line

`fleet-inv drift` today: **0 DRIFT, 34 NO-FACT**, exit code 0. Thirty-four inventory
fields — including `lumen.lan_ip`, `mirror.tailscale_ip`, `phone.tailscale_ip`, and 16
`ssh.user` values — have no counterpart in the state store at all, and the tool reports
that outcome as success. Nothing alarms on it, nothing tickets it. Same shape in
`state_reconcile`, same shape in the dream report's "None — prose IPs match the store."

**Unverifiable must be a reported, alarmed quantity, separate from verified-clean.**

### 2.4 The contradiction scanner reaches 2% of superseded facts

`lumen_dream.stale_prose_scan()` finds prose that cites a value the store has superseded.
It is the right idea, with two crippling limits:

1. **Value-shape**: `superseded_ips()` keeps a closed fact only if
   `IP_RE.fullmatch(value)`. Of 312 closed facts, **6 are IP-shaped; 306 are invisible.**
   The invisible remainder is exactly what Frank keeps finding: `status` (53),
   `power_plug` (15), `role` (10), `powers_node` (6), `hardware`/`hw` (6), `lan_ip` (4).
2. **Artifact reach**: it scans `claude_memory.md` only. `archival_notes` (1686 rows,
   machine-retrieved into every session), `FLEET_MAP.md`, `storage_manifest.yaml`, the
   fleet inventory YAMLs, `instincts.yaml` and the playbooks are all unscanned.

Consequence: the 2026-09-06 report says "Stale prose flagged (0) — None, prose IPs match
the store" while `nephthys.role = DECOMMISSIONED` and `pbox.status = DECOMMISSIONED` sit
superseded in the store and their old values sit live in prose and notes.

### 2.5 Cadence — weekly scan, daily drift

`lumen-dream.timer` fires Sunday 18:00 UTC. A fact superseded on Monday goes six days
before the scanner looks at it, and (per 2.4) is 98% likely to be invisible when it does.

### 2.6 The rendered block can outlive its regeneration

`LIVE_FLEET_STATE.md` is what every session actually reads — it is injected at `--core`
boot. It is regenerated only by `/home/lumen/bin/state-regen`, called by hand or by
`state_reconcile` **only when changes were found**. A hand `ingest` that forgets
`state-regen` leaves the injected block silently behind the store. The procedure is
documented in the session-start skill, which means it is enforced by memory rather than
by machinery.

### 2.7 Core budget overflow with no eviction policy

Boot emitted: `LIVE_FLEET_STATE=5734 tok exceeds soft cap 4000 … (not truncated here)`.
The selector warns and passes the whole block through. `claude_memory.md` core floor is
10,816 tok against an 8K threshold. There is no rule for *which* facts lose their core
tier as the store grows — `render_tier` is assigned at write time from a length heuristic
and never revisited. Growth alone will eventually force truncation, and nothing chooses
what gets dropped.

### 2.8 Hand-maintained artifacts with no regeneration path

- `storage_manifest.yaml` — one-shot `gen_manifest.py` output, hand-tuned after
  generation, mutated through nine `*_pre_overlay_apply` backups. No timer regenerates
  it; nothing compares it to live `lsblk`/`mdadm`. This is one of the three drift cases
  Frank named.
- `FLEET_MAP.md` — hand-edited, currently two days behind the store's newest fact.
- `HANDOFF.md` §3 — carries July-era entries under a hand-written "everything below this
  line is July-era and mostly done" disclaimer. Prose managing its own staleness.
- `instincts.yaml` — 40 rules, merge-in only, no retirement path. `instinct_worth`
  scoring exists (wq#578) and annotates proposals nobody applies.

### 2.9 Free-text provenance

`source_ref` is populated on 830/830 facts, which looks like full provenance and is not:
the values are prose (`"Frank 2026-07-16 plug-install session"`, `"mem:anubis"`,
`"claude-web-0731"`). Nothing can mechanically answer "was this observed or was this
stated by Frank?" — which matters, because the standing instinct that a Frank statement
outranks an observation cannot be enforced by a verifier that cannot tell them apart.

### 2.10 Ticket bodies

`wq` ticket detail ages with no check. The RE-VERIFY-BEFORE-ESCALATION instinct exists
precisely because of this, i.e. the current mitigation is an instruction to a human.

### 2.11 Cross-layer: research docs and notes assert fleet state

Covered in 1.3/1.2. Neither layer can be told that a fact it asserts has since been
superseded, because no such channel exists.

---

## 3. Design — fact invalidation and re-verification

Design principle throughout: **the bi-temporal model is not changed.** `valid_from` /
`valid_to` remain world truth and stay the only thing that decides "current". Everything
below adds a fourth, orthogonal axis — *how good is our evidence that the current row is
still true* — and makes it visible at the point of use. A fact never becomes non-current
because it got old. It becomes *flagged*, and something goes and looks.

### 3.1 Schema additions (all additive, all nullable — migration 0009)

On `state_facts`:

| column | type | meaning |
|---|---|---|
| `source_kind` | TEXT | `observed` · `frank_stated` · `derived` · `doc` |
| `verify_recipe` | TEXT | id of a registered probe, or NULL = unverifiable |
| `verify_interval_days` | REAL | evidence TTL; NULL = resolve from `fact_class` |
| `contradicted_at` | TEXT | set by the contradiction pass, cleared on re-verify |
| `derived_from` | TEXT | comma-joined parent fact ids, for `source_kind=derived` |

New table `fact_verifications` (append-only, the audit trail 2.1 is missing):

```sql
CREATE TABLE fact_verifications (
  id TEXT PRIMARY KEY, fact_id TEXT NOT NULL, subject TEXT, predicate TEXT,
  probe TEXT NOT NULL, observed TEXT, outcome TEXT NOT NULL,   -- confirmed|superseded|unknown|error
  ts TEXT NOT NULL, detail TEXT);
CREATE INDEX idx_fv_fact ON fact_verifications(fact_id, ts);
```

New table `note_contradictions` (the missing state→episodic channel):

```sql
CREATE TABLE note_contradictions (
  note_id TEXT NOT NULL, fact_id TEXT NOT NULL, dead_value TEXT,
  current_value TEXT, detected_at TEXT, cleared_at TEXT,
  PRIMARY KEY (note_id, fact_id));
```

No column is added to `archival_notes` — same rule wq#576 followed, for the same reason.

`schema_version` stays without a default so legacy rows read NULL, per the wq#575
precedent.

### 3.2 Evidence refresh — kill the match-skip (class fix)

Contract change, enforced at the boundary rather than per-script:

> A reconciler reports `(subject, predicate, observed_value, probe_id)` to the store.
> **The reconciler never compares.** `ingest()` decides: equal → `reconfirmed`
> (bump `last_confirmed_at`, clear `contradicted_at`, append a `fact_verifications` row);
> different → `superseded` (existing path, plus a verification row); absent/unreadable →
> `unknown` (verification row only, no fact mutation).

`if old == new: continue` becomes a banned shape. It is the same class as the
silent-LLM-fallback pathology already in the episode backfill: a successful check that
writes nothing looks identical to a check that never ran.

`ingest()` needs one behavioural addition beyond what it already does: on `reconfirmed`
it must also clear `contradicted_at` and write the verification row. `history()` and
`as_of()` stay bit-for-bit reproducible because `valid_from` is still untouched.

### 3.3 Verifier registry — one place, many probes

`/home/lumen/minerva/verify/registry.yaml`, one entry per probe:

```yaml
- id: tailscale_ip
  predicates: [tailscale_ip]
  subjects: known-in-store          # never invents subjects (existing safety rule)
  command: tailscale status --json
  extract: peers[].TailscaleIPs[0] by HostName (+ ALIASES/IGNORE as today)
  interval_days: 1
```

Seed set, all wrapping probes that already exist:

| probe | predicates | source | interval |
|---|---|---|---|
| `tailscale_ip` | `tailscale_ip` | `tailscale status --json` | 1 |
| `fleet_inventory` | `lan_ip`, `ssh_user`, `power_plug` | `fleet-inv get` | 1 |
| `service_state` | `*_status` where value names a unit | `systemctl is-active` over ssh-list | 1 |
| `node_storage` | `storage`, `storage_layout`, `ram` | `lsblk -J` + `mdstat` + `dmidecode` survey | 7 |
| `os_kernel` | `kernel`, `os`, `*_version` | `uname -r` / `lsb_release` / pkg query | 7 |
| `plug_map` | `powers_node`, `plug_ip`, `ha_entity` | `plug-ctl` + HA entity list | 3 |
| `endpoint_live` | `url`, `port` | HTTP status probe | 1 |

`lumen_verify.py sweep` selects facts where
`evidence_age > COALESCE(verify_interval_days, class_default)`, runs each due probe once
(batched per node, not per fact), and reports the four-way outcome. Fail-open per probe;
a probe that errors yields `unknown`, never a supersession. Runs on
`lumen-state-verify.timer` at 06:00 UTC (before the 13:00 brief, after overnight work).

Class defaults when `verify_interval_days` is NULL: `status` 3d · `config` 14d ·
`identity` 90d · `narrative` no probe (prose lifecycle owns it) · `price`/market keeps
the existing `VOLATILE_HALFLIFE`.

### 3.4 Provenance rules that the verifier obeys

- `frank_stated` — **never auto-superseded by an observation.** A conflicting observation
  writes a `fact_verifications` row with `outcome=unknown` and raises an aq row for a
  human to reconcile. This encodes the standing instinct in the schema instead of prose,
  and it is the guard that keeps 3.3 from ever overwriting something Frank told us.
- `observed` — full sweep participation, reconfirm and supersede both allowed.
- `derived` — invalidated when any `derived_from` parent is superseded, transitively;
  it must never be reconfirmed on its own.
- `doc` — read out of a document; verifiable only by re-reading that document, and
  eligible for contradiction flagging but not supersession.

Backfill: `source_kind` for existing rows is inferable from the current `source_ref`
prose with a conservative mapping (`Frank …`/`frank-directive-*` → `frank_stated`;
`reconcile:*`, `kasa …`, `HA …` → `observed`; `mem:*`, `override:*` → `doc`; wq/session
refs → `observed`). Anything ambiguous stays NULL, which behaves as `observed`-without-
probe, i.e. unverifiable, i.e. it shows up in the coverage number instead of being
silently assumed.

### 3.5 Contradiction detection — generalised beyond IPs

Replace `superseded_ips()` with a **dead-value index** built from all 312+ closed facts,
then match by value shape rather than by regex:

1. **Structured values** (IP, MAC, hostname, unit name, path, port, serial, plug id,
   short enum like `RETIRED`) → exact token match with word boundaries. Selection rule:
   a closed value qualifies if it is ≤ 60 chars and matches
   `^[A-Za-z0-9._:/@\-]+$`, or is a known-shape token. This lifts coverage from 6/312 to
   an estimated 200+/312 without prose false-positive risk.
2. **Prose values** (>60 chars, `fact_class=narrative`) → **no string matching.** These
   are handled by the note-level rule in 3.6 or not at all. Substring-matching prose is
   how a scanner earns a reputation for noise.

Two suppressors, both required, on top of the existing `NEGATION_RE`:

- **Changelog suppressor** — if the *current* value also appears within the same line
  (prose) or the same note, the text is documenting the transition, not asserting the
  dead value. Skip.
- **As-of suppressor** — if the hit line/note carries a date earlier than the fact's
  `valid_to`, it is a correct historical record. Downgrade to informational.

Artifact reach, with the action tier each one gets:

| artifact | action on hit |
|---|---|
| `archival_notes` | **write** `note_contradictions` row (machine-consumed → machine-fixed) |
| `LIVE_FLEET_STATE*.md` | **regenerate** (derived file, no proposal needed) |
| `storage_manifest.yaml`, fleet inventory YAML | **aq row + wq ticket** (owned config) |
| `FLEET_MAP.md`, playbooks, `instincts.yaml` | **proposal** in the dream report |
| `claude_memory.md` | **proposal only** — standing law, unchanged |

Cadence: move the contradiction pass out of the weekly dream run into
`lumen-state-verify.timer` (daily), leaving dedup/instincts/salience on the weekly run.
A superseded fact should be chased within a day, not six.

### 3.6 Staleness scoring — visible, ordinal, never a probability

Keep the existing calibration caveat verbatim: this is an **ordering and
reconfirm-prompting signal**, never rendered as "N% likely true."

```
staleness(f):
  if contradicted_at is not NULL          -> CONTRADICTED     (hard, overrides all)
  if verify_recipe is NULL                -> UNVERIFIABLE     (not decay — coverage)
  r = evidence_age_days / effective_interval_days
  r < 1.0  -> FRESH        1.0 <= r < 3.0 -> DUE        r >= 3.0 -> STALE
```

Rendering, which is the whole point — staleness that is not visible at the point of use
does not exist:

- `lumen_state.py current` gains an evidence column: `[verified 2d ago]`,
  `[DUE — 19d, probe fleet_inventory]`, `[STALE — 94d unverified]`,
  `[UNVERIFIABLE — no probe]`, `[⚠ CONTRADICTED by note …]`.
- `LIVE_FLEET_STATE.md` suffixes `?` on DUE and `‼` on STALE/CONTRADICTED facts. This is
  the single highest-value change in the document: every session reads that block, and
  today it renders 830 facts with identical, unqualified authority.
- `handoff verify` prints one coverage line: `facts 830 · fresh N · due N · stale N ·
  unverifiable N · contradicted N`, and the same numbers drive an aq row when
  `stale + unverifiable` rises week over week.

Eviction policy for 2.7 falls out of this for free: when the core block is over budget,
drop **STALE and UNVERIFIABLE topic-tier facts first**, never a FRESH one. Age-of-evidence
is a better eviction key than the current write-time length heuristic.

### 3.7 Episodic invalidation — contradiction, not age

`search_v3` already excludes revoked/superseded/absorbed rows unconditionally. Extend
that path with a **soft** penalty, not a fourth exclusion:

```
if note_id in note_contradictions (cleared_at IS NULL):
    score *= CONTRA_PENALTY        # env MEMORY_V4_W_CONTRA, default 0.5, 1.0 = off
```

Rationale for soft rather than hard: a contradicted note usually still carries valid
procedure around the one dead fact, and hard-excluding it loses the procedure class the
golden set specifically protects.

Do **not** re-enable `W_REC`. The freshness prior was measured to cost recall on the
frozen set; this is a targeted penalty on notes we have positive evidence about, which is
a different instrument. Evaluation is non-negotiable and already exists: run against
`eval/golden_v4_frozen_20260814.jsonl` (sha256 `8ba5afb0ce12585606b8dcff5ffb8245af24b0f0f32ccbddf3d4121f1bbf46b7`),
cite the sha in the artifact, and treat **any rise in `trap_at1_rate` as disqualifying**
per wq#595. `trap_at1_rate = .250` is the number this change exists to move; if it does
not move, the contradiction index is not reaching the notes that matter and the fix is in
3.5, not in the penalty weight.

Notes whose contradiction is total (the note is *only* the dead fact) get a
`superseded_by` pointing at the note or fact that replaced it — the existing hard path,
used deliberately and rarely, which is why it currently reads 1.

### 3.8 Research index — doc-level freshness marking

Nothing to fix in the ingest path; it is correct. Add one derived column,
`asserts_fleet_state BOOLEAN`, set when a doc's text matches the dead-value index or the
live subject vocabulary, and surface `mtime` age in federated results so a July report is
visibly a July report. No deletion, no re-embedding — a marking change only.

### 3.9 What this does *not* do

- Does not expire facts. Nothing becomes non-current from age alone; `valid_to` is still
  only written by supersession.
- Does not auto-edit `claude_memory.md` or `instincts.yaml`. Proposal-only stands.
- Does not introduce an LLM judge anywhere in the invalidation path. Every mechanism
  above is deterministic, which is what makes the bi-temporal store trustworthy in the
  first place.
- Does not add calibration tooling. The score stays ordinal and honestly labelled.

---

## 4. Ranked fix list

Ranked by drift caught per unit of effort. Items 1-4 are the ones that would have caught
the storage-manifest / inventory / node-state drift Frank hit.

| # | fix | catches | effort | risk |
|---|---|---|---|---|
| 1 | **Reconfirm-on-match** — `state_reconcile` calls `ingest()` on equality; `ingest()` writes `fact_verifications` and clears `contradicted_at`. Ban the compare-in-the-reconciler shape. | Makes evidence age real for the 25 nodes already being checked daily and for every probe added later. Prerequisite for 3, 5, 6. | S | low — additive write, no fact mutation |
| 2 | **Generalise the contradiction scanner** to all structured closed values, with the changelog + as-of suppressors. | 6/312 → ~200+/312 superseded facts. Directly the node-state class. | M | medium — false positives; suppressors and the ≤60-char structured gate are the mitigation |
| 3 | **Extend contradiction reach to `archival_notes`** + `note_contradictions` table. | 1686 machine-retrieved notes currently unscanned. The layer that feeds every precheck. | M | low — new table, no note mutation |
| 4 | **Coverage as an alarmed metric** — `checked/matched/drifted/unverifiable` from every reconciler; `fleet-inv drift` exits non-zero on NO-FACT growth; `handoff verify` prints the line. | The 34 NO-FACT fields, and every future "no drift found" that means "nothing looked". | S | low |
| 5 | **Render staleness in `LIVE_FLEET_STATE` and `current`** (`?` / `‼` / evidence age). | Turns invisible drift into visible drift in the block every session reads. Highest value per line changed after #1. | S | low — display only |
| 6 | **Verifier registry + `lumen_verify.py sweep`** with the 7 seed probes, daily timer. | The 12 predicate families with no probe at all; the 505 facts in the 31-90d bulge. | L | medium — probes touch nodes; fail-open per probe, `unknown` never supersedes |
| 7 | **`source_kind` + the frank_stated guard.** | Makes #6 safe: guarantees a probe can never overwrite something Frank stated. Should land *with* #6, not after. | S | low |
| 8 | **Storage manifest + FLEET_MAP regeneration** — survey-driven regen on a timer, diffed against the committed YAML, drift → aq + wq. | The named storage-manifest case; the 2-day-stale FLEET_MAP. | M | low — diff-and-report first, apply later |
| 9 | **Contradiction penalty in `search_v3`** (`W_CONTRA` 0.5, env-gated) evaluated against the frozen golden set. | `trap_at1_rate .250`. Gated on #3 producing rows. | M | medium — must not raise trap@1; rollback is one env var |
| 10 | **Daily cadence** for the contradiction pass (split from the weekly dream run). | The 6-day window between supersession and scan. | S | low |
| 11 | **Core-budget eviction by evidence age** — drop STALE/UNVERIFIABLE topic facts first. | The 5,734-vs-4,000 token overflow, and the 10,816-vs-8,000 prose core floor. | M | low |
| 12 | **Research-doc `asserts_fleet_state` marking + mtime in federated results.** | July reports competing with September facts. | M | low |
| 13 | **Apply the prose-lifecycle demotion plans** that already generate weekly and go unapplied. | 5,471 lines of prose with a human-only validity check. | S | low — operator-invoked, already built |

Sequencing: 1 → 4 → 5 are a single small first pass and are worth doing together (they
share the ingest/report surfaces). 2 → 3 → 10 is the second pass and the one that pays for
the review. 6 + 7 is the third and largest. 9 gates on 3. 8, 11, 12, 13 are independent.

### Tickets filed from this review

| fix | ticket | lane/pri |
|---|---|---|
| 1 reconfirm-on-match | wq#1608 | build p0 |
| 2 generalise contradiction scanner (+10 daily cadence) | wq#1609 | build p0 |
| 3 contradiction detection over `archival_notes` | wq#1610 | build p1 |
| 4 coverage metric | wq#1611 | build p1 |
| 5 render staleness (+11 eviction key) | wq#1612 | build p1 |
| 6+7 verifier registry + `source_kind` | wq#1613 | build p2 |
| 9 contradiction penalty in `search_v3` | wq#1614 | build p2 |
| 8 storage manifest + FLEET_MAP regeneration | wq#1615 | build p2 |

Fixes 12 and 13 are not ticketed yet: 12 depends on 2 landing first, and 13 is an
operator action on plans that already generate weekly.

## 5. What already works — do not break it

- **Deterministic key-based supersession.** No LLM in the invalidation path. This is the
  reason the store is trustworthy; every design above stays deterministic.
- **The out-of-order guard** (`valid_from` earlier than the open row → closed historical
  insert). It is correct and it is subtle; the verifier sweep must route through
  `ingest()` so it inherits this rather than reimplementing it.
- **`invalidate-never-delete`**, in both `state_facts` and `note_merges`. History is what
  makes the dead-value index in 3.5 possible at all.
- **`effective_confidence` never writes back.** Read-time decay only. Keep it.
- **The calibration caveat** in the code. Do not let a staleness score become a
  probability in any rendering.
- **pgvector `content_hash` + `mtime` + delete-reconcile.** 10,390 rows, 0 orphans. This
  is the pattern the other layers should be measured against.
- **`search_v3`'s unconditional revoked/superseded/absorbed exclusion.** Note for the
  handoff: this closes known-gap #2 from the wq#575 list, which still reads as open.
- **`W_REC = 0.00`.** It was measured. Leave it.
- **Proposal-only for `claude_memory.md` and `instincts.yaml`.** Standing law; every
  mechanism above respects it.

## Appendix — how the numbers in this document were measured

```bash
# evidence age, half-life and reconfirm coverage across open facts
sqlite3 minerva.db "select count(*) from state_facts where valid_to is null;"                 # 830
sqlite3 minerva.db "select count(*) from state_facts where valid_to is null
                    and half_life_days is not null;"                                          # 9
sqlite3 minerva.db "select count(*) from state_facts where valid_to is null
                    and last_confirmed_at > ingested_at;"                                     # 10
sqlite3 minerva.db "select count(*) from state_facts where valid_to is null
                    and confidence < 1.0;"                                                    # 0

# contradiction-scanner reach: closed facts whose value is IP-shaped
python3 -c "…IP_RE.fullmatch over closed values…"          # 6 visible / 306 invisible of 312

# episodic invalidation actually in use
sqlite3 minerva.db "select count(*) from archival_notes;"                                     # 1686
sqlite3 minerva.db "select count(*) from archival_notes where superseded_by is not null;"     # 1
sqlite3 minerva.db "select count(*) from archival_notes where revoked_at is not null;"        # 6

# inventory-vs-store coverage
/home/lumen/bin/fleet-inv drift            # 0 drift, 34 NO-FACT, exit 0

# research index orphan check
psql research -tAc "select filepath from research_docs" | while read p; do [ -f "$p" ] || echo "$p"; done
                                                                                   # 0 of 10390
# the reconciler's own log
journalctl -u lumen-state-reconcile.service     # "[APPLIED] no tailscale_ip drift; store matches the wire."
```
