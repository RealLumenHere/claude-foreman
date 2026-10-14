# wq#3844 — verify wq#3842: n8n-watcher guards justification correction

Verdict: **CONFIRM**. wq#3842's judgment (upholding wq#3841's contradiction of
wq#3839 and shipping the correction) holds under adversarial re-check. No
escalation.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1834 — not reachable
via unauthenticated `curl` from this worktree (404, same auth gap wq#3791 and
wq#3841 hit). Not treated as fleet evidence; verified instead against the
merged content directly: commit `226a1316`, currently `HEAD` of `main` (and
of this verify worktree's branch base). Diff touches
`docs/fleet/retired_services.yaml` (+30/-10), `research/FLEET_SERVICE_ATLAS.md`
(4 annotation sites + 1 header line), and adds the judgment writeup itself.

## What I re-checked independently (did not take the report's word for any of it)

1. **`n8n_watcher.py:17-19,36` ntfy-fix lines.** Live source is outside this
   worktree's allowed paths (`/home/lumen/agent/` is not an `--add-dir`), so
   I used the same snapshot the judgment cites —
   `foreman/_runs/wq3766/snapshots/20261012T133202Z-home_lumen_agent_n8n_watcher.py`
   (two days before retirement) — and grepped it myself: line 17
   `NTFY_SEND = "/home/lumen/bin/ntfy-send"  # wq#1645: house path...`, line 19
   `...real topic 'lumen-alerts'...`, line 36
   `[NTFY_SEND, "-t", "lumen-alerts", ...]`. Exact match, not paraphrased.

2. **Control-flow claim for "Seeked to end of log (0 lines)".** The judgment
   asserts that message is only reachable when `EVENT_LOG.exists()` is true
   (`n8n_watcher.py:110-114`). Read that span in the same snapshot myself:
   `if state["last_pos"] == 0 and EVENT_LOG.exists(): ... log.info(f"Seeked
   to end of log ({state['last_pos']} lines)")` — confirmed, and it shows
   something the judgment didn't even need to spell out: `state["last_pos"]`
   is set to `len(lines)` right before that log call, so "0 lines" in the
   log line *is* `last_pos == 0` by construction — the two pieces of
   evidence (journal line + state.json) aren't independent corroboration of
   two different things, they're the same fact observed twice. Doesn't
   weaken the conclusion, but the report's "independently" framing in §1 is
   slightly generous. Also confirmed `CHECK_EVERY = 60` and that
   `save_state(state)` runs unconditionally every loop iteration (not inside
   an `if`), which is what the 60s-unconditional-write claim rests on.

3. **journalctl, live, both units.** Ran `journalctl -u n8n-watcher.service`
   myself for Oct 07: line-for-line match, same PID (`1718326`), same two
   timestamps (`23:38:39.956` ntfy sent, `.957` seeked-0-lines). Ran
   `journalctl -u n8n.service` for Oct 14 00:50-01:00: both "Processed 0
   draft workflows, 0 published workflows" lines present at the claimed
   timestamps (00:54:58, 00:56:23) with matching PIDs. Ran both units
   together for 02:40-02:45: both stopped at `02:42:07`/`02:42:08`, and
   n8n-watcher's own stop line reports `6d 3h 3min 28.096s wall clock time`
   — matches the report's "whole 6-day run" phrasing (Oct 07 23:38 → Oct 14
   02:42 is 6d3h), confirming no restart happened in between that could have
   reset `last_pos`.

4. **`n8n_watcher_state.json` snapshot.** Read
   `foreman/_runs/wq3842/snapshots/20261014T031442Z-...json` directly:
   `{"last_pos": 0, "last_alert": {}}`, mtime `2026-10-14 02:41:45.257...` —
   matches the report's cited content and timestamp exactly (one minute
   before the 02:42:07 stop).

5. **Acceptance probe, re-run myself rather than trusting the report's
   count.** `grep -n "F-[0-9]"` and a broader `grep -ni "atlas|§7|finding"`
   over the full `retired_services.yaml` turns up the same three other
   entries the report names (`agent5-split`/F-4, `lumen-rerank-thread-watch`/
   F-10, `lumen-agent`/F-4) plus one unrelated hit (`DSH_EVAL.md §7`, a
   different document, correctly out of scope) and the report's own new
   prose at the n8n entry. Read all three in context: each states the
   finding *together with* its resolution (`agent5-split`: "Found by wq#1627
   (F-4), cleaned by wq#1643"; `lumen-rerank-thread-watch`: states the finding
   and that it's a no-op by design; `lumen-agent`: explicitly says the stall
   rule is "inert by construction... self-arms if Frank ever unmasks it").
   None of them assert a closed finding as a live fact the way the
   pre-fix n8n entry did. Probe reproduces clean — it could have found more
   broken entries and didn't.

6. **ISSUE-LOG row 1298.** `grep -n "^| 1298 "` on
   `/home/lumen/kb/ISSUE-LOG.md` — row exists, text matches the report's
   description of the class (present-tense atlas finding copied into a
   permanent record as a live justification a month after the ticket that
   fixed it).

7. **Follow-up ticket wq#3843.** `wq show 3843` — exists, pri 3, archetype
   `mechanical-edit`, scoped exactly as described (back-annotate F-1..F-18,
   don't re-audit, spot-check two closures against live source). Currently
   `claimed` by `fworker-wq3843`, not done — correctly filed as a separate
   ticket rather than silently expanded into this PR's scope.

8. **The one claim scoped as "not ruled on."** Checked
   `/home/lumen/bin/retirement-audit:29-34` myself:
   `CONF_PATH` defaults to `/home/lumen/docs/fleet/retired_services.yaml`
   (not the `foreman/` copy this PR edits), and the script's required schema
   (`name`/`kind`/`expect`, plus `unit`/`container`/`pattern` per kind) is
   confirmed structurally different from this file's `name`/`units`/
   `load_state`/`guards`. The judgment's observation is accurate: this file
   is not actually the one `retirement-audit` reads, despite its own header
   claiming otherwise. Correctly left untouched as out-of-scope-for-this-fix
   rather than silently folded in.

## Attempts to break it

- Checked whether the ntfy-fix snapshot (Oct 12) could be stale relative to
  the Oct 14 retirement — i.e. whether the fix could have been reverted in
  the two-day gap. No later snapshot of `n8n_watcher.py` exists in
  `foreman/_runs/`, but the Oct 07 23:38:39 journal line (the watcher's
  *last start*, running unmodified through to the 02:42:07 stop per the
  single-continuous-run CPU-time accounting above) already shows the fixed
  behavior live — a source revert between Oct 12 and Oct 14 would still have
  to survive without a restart to matter, and no restart is recorded.
- Checked for other `retired_services.yaml` entries citing an atlas finding
  that the probe might have missed by only grepping `F-[0-9]` — broadened to
  a case-insensitive `atlas|§7|finding` sweep; no additional hits beyond
  what's listed in point 5.
- Checked that the PR didn't quietly touch a fourth file or revert something
  outside its stated scope — `git show 226a1316 --stat` shows exactly the
  three files the commit message and report claim.

Nothing above contradicts the ruling. The over-statement in point 2 is a
one-sentence editorial nit in how the evidence is framed, not a factual
error, and doesn't change the verdict.

## Verdict

**CONFIRM.** Every cited log line, source line, snapshot, ticket, and
ISSUE-LOG row checks out against live re-derivation, not just against the
report's own quotes of them. The scope calls (annotate four sites instead of
rewriting the atlas; file the full F-1..F-18 sweep as wq#3843 instead of
doing it inline; note but don't fix the `retirement-audit` path/schema
mismatch) are each independently justified and consistent with house
doctrine on not scope-creeping a fix. No escalation.
