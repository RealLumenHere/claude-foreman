# R4 ledger — auto-emit FRANK-* signal at intervention time (design before build)
## session wq1014-a3, 2026-08-30

## ROOT CAUSE (established by wq925-r4; not re-derived here)
The ledger's two high-precision inputs are dead: nobody writes `FRANK-FIX:` /
`FRANK-DETECT:` / `FRANK-REPEAT:` / `DOWNTIME-UNDETECTED-MIN=` markers (0 in all
of W35), and gated `frank_response` was retired by the 2026-08-22 autonomy
directive. The collector runs on its conservative regex fallback over episode
narrations, which the R4 report itself flags as undercounting.

## CLASS
A measurement that depends on someone *remembering* to annotate silently blinds
itself — same class as DELIVERY_FAILURE. The fix must remove memory from the
loop entirely, not relocate it (human memory → session memory is still memory).

## OPTIONS CONSIDERED
(a) **Write-time stamping hook** (wq/episode commit hook classifies Frank-origin
    text and stamps a marker): rejected as primary. It runs the same regex on the
    same narrated prose, just earlier — no new information, one more moving part,
    and `lumen_episode.py` lives outside this repo (live-edit, not PR-landable).
(b) **`lumen_episode.py commit --frank-event {fix,detect,repeat,downtime=N}`**
    pushed via instincts: rejected as primary. Sessions forget exactly like
    humans do; the R4 report already proved instruction-driven annotation reads
    zero. Kept as an optional precision channel: the markers stay parsed, so any
    session that *does* annotate still lands a `conf=marker` event.
(c) **Mine the durably-logged chat-origin signal**: CHOSEN. Every Frank
    intervention — messenger-bridged or typed at a console — necessarily arrives
    as a `type:"user"` turn in a Claude session transcript
    (`~/.claude/projects/*/<session>.jsonl`). That log already exists, is written
    by the harness (not by anyone's memory), and covers the intervention class
    *structurally*: Frank cannot intervene without producing a line in it.

## FIX (collector-side only; one file, PR-landable, zero new emitters)
New source in `ops/intervention_ledger.py`, run by the existing daily timer:

1. `collect_chat(c, since, root=~/.claude/projects)` scans transcript files with
   mtime ≥ since−1d, extracts Frank-typed user turns, classifies each with
   Frank-voice regexes (his own words, not third-person narration), and inserts
   events with `conf='chat'`.
2. **Headless exclusion** (the transcripts also contain fworker/cron sessions
   whose "user" turns are harness prompts, not Frank):
   - project dirs named `-home-lumen-worktrees-*` are skipped wholesale;
   - a session containing a harness sentinel ("You are a Lumen fleet",
     "YOUR TICKET: wq#", "<<autonomous-loop") is discarded entirely;
   - per-message: skip tool_results, sidechains (subagents), and injected turns
     (`<command-name>`, `<local-command-stdout>`, `<system-reminder>`,
     `Caveat:`, `[Request interrupted`).
3. **Frank-voice classifiers** (precedence detect > repeat > fix, matching the
   report's b > e > a damning-ness rule): detect = "X is down/broken/not
   working/why is X failing"; repeat = "I already told you / how many times /
   still broken"; fix = "fix it / restart / revert / roll back / get it working".
   Anything else (chatter, new-feature asks, questions) is NOT an event —
   conservative bias, same rationale as the existing regex fallback.
4. **Burst collapse**: one event per (session, kind, day). A five-message rant
   about one outage is one intervention, not five. `source_ref =
   chat:<projdir>:<session8>:<date>`; the existing `UNIQUE(ts,kind,source_ref)`
   makes re-runs idempotent.
5. **Cross-source dedupe**: at rollup, a `conf='regex'` event is suppressed when
   a `conf='chat'` event of the same (kind, day) exists — the chat event is the
   same incident seen from higher ground, and double-counting inflates the number
   the ledger exists to shrink. Suppression is by *chat only*: marker/structured/
   manual never suppress regex, so W31–W34 history (incl. the W34=13 baseline of
   record) is bit-identical before/after this change.
6. **Credential hygiene** (instinct 3): event descriptions are ≤160-char snippets
   of Frank's message with token-shaped substrings masked before storage.
7. **Failure isolation**: chat mining is wrapped so a transcript-format surprise
   degrades to a stderr WARN — the daily `run` still collects, snapshots, emits.
8. **Calibration**: new `mine-chat --since --dry-run [--root]` subcommand prints
   would-be events with session + snippet, writing nothing. First post-land step:
   run it live over 14d and eyeball precision before trusting the trend shift.

## WHAT THIS DOES NOT DO
- Metric (d) undetected-downtime still has no automatic sensor — that is wq#1015
  (liveness-vs-alert diff), unchanged by this work.
- No instincts/settings/hook changes anywhere; frozen-config rules untouched.
- No LLM classification (messenger-rung batch LLM is instinct-barred); regex on
  Frank's first-person voice is materially higher precision than regex on
  third-person narration, and the dry-run exists to tune it.

## RISKS / LIMITS (stated, not hidden)
- Transcript retention (`cleanupPeriodDays`, default 30d) bounds lookback;
  the daily 14d window is safely inside it. Backfill beyond retention is regex/
  manual only, as today.
- Headless heuristics could misclassify a future harness shape; the sentinel
  list is a constant at the top of the file, and dry-run output makes a leak
  visible before it pollutes the trend.
- Frank messages that describe a fault without fault-words ("interesting, LPP
  shows 0 turns") will be missed — conservative by design; the regex fallback
  over episodes still catches the session's own narration of such cases.

## TEST TO FAILURE (fixtures, no live DB/transcripts touched at dev time)
- classifier: fix/detect/repeat/none samples + precedence + burst collapse.
- headless exclusion: a worker transcript whose harness prompt contains
  fault-words must produce zero events; worktree project dirs skipped.
- injected-turn exclusion: command/system-reminder/tool_result user lines.
- idempotency: second collect run inserts nothing new.
- baseline preservation: regex+manual same (kind,day) → both count (history
  unchanged); regex+chat same (kind,day) → one counts.
- masking: a token-shaped string in Frank's message never reaches the DB.

## ROLLBACK
Single file revert (`git revert` of the landing commit) + next timer run;
`conf='chat'` rows are inert to the old code (rollup counts them like any other
event) so no DB surgery is needed either way.
