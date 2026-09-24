# wq#2457 — verify-pass on wq#2428 / PR #876 ("timer spend guard")

**Verdict: CONTRADICT.** `timer_spend_audit.py`'s script-body markers match
string literals inside data structures — topic-keyword lists, an
exclude-list, a `FORBIDDEN_*_ENVS` guard tuple — not calls. Run for real, as
`lumen`, with full read access (the exact deploy shape the PR itself
prescribes: "as lumen, with every ExecStart target readable, --strict is
meaningful and this is the real audit" — `install.sh:9-12`), the tool
reports **4 live `cloud` findings today, all four false positives**, against
four real, enabled, currently-firing timers. `lumen-timer-spend-guard.timer`
runs `--alert` (`lumen-timer-spend-guard.service:7`) as `User=lumen`
(same read reach as this probe), so on first fire it files an `aq` ticket and
a high-priority `ntfy` push claiming four scheduled units reach Anthropic
with no budget gate — none of which is true.

## What PR #876 claims

The PR's own commit message and README both present the false-positive class
as solved: "Four of the first run's findings were prose rather than calls
(backticked docstrings, an `--owner` field, a comment) and one was `claude
auth status`, which bills nothing; all five shapes are now rejected and
pinned by tests" (commit `044e1bde59`; mirrored in `README.md:60-67`). The
fix that shipped for that class — strip backticked spans, skip whole-line
comments (`markers_in_script`, `timer_spend_audit.py:172-175`), and exempt
`_NON_BILLING` CLI subcommands (`timer_spend_audit.py:77-79`) — is real and
`tests/test_timer_spend_audit.py` pins it (16 cases across
`test_prose_about_claude_is_not_a_call` and
`test_non_billing_subcommands_are_not_spend`).

That fix does not cover a different, equally common shape: a bare string
literal `"claude"` or `'ANTHROPIC_API_KEY'` sitting inside a Python list,
tuple, or f-string join — not a comment, not a docstring, not backticked.
Nothing in the test suite exercises that shape, and it is not hypothetical:
it is what four separate live scripts under `/home/lumen/{agent,personas,
minerva}` actually contain, in files the PR's own sandbox admits it could
not read (`README.md`: "the 104 `unknown` are scripts under
`/home/lumen/{agent,autonomy,services}` ... **Running `install.sh` ... as
`lumen` is what finishes this audit.**").

## Break attempt

`reports/2457_probe_timer_spend_script_marker_false_positives.py` imports the
real, unmodified `timer_spend_audit` module and feeds it four lines copied
verbatim from the four live files, unmodified:

```
$ python3 reports/2457_probe_timer_spend_script_marker_false_positives.py
FLAGGED  /home/lumen/agent/lumen_intel.py:93  [lumen-intel.timer (enabled, fires hourly)]
         line: '    "code generation", "swe-bench", "claude", "gpt", "llama", "qwen",'
         markers_in_script() -> [(1, 'claude-cli')]
FLAGGED  /home/lumen/agent/signal_engine.py:47  [lumen-signal-engine.timer (enabled, daily)]
         line: '    "ai","llm","ollama","mcp","claude","gpt","openai","anthropic",'
         markers_in_script() -> [(1, 'claude-cli')]
FLAGGED  /home/lumen/personas/worklog_gen.py:17  [lumen-worklog.timer (enabled, ~6h)]
         line: 'EXCLUDE=("persona","agent_bridge","brain","worklog","hermit","identity","seal","opus","sonnet","claude","fable","anthropic","litellm","lumen-convo-learn","case-book","self-updating")'
         markers_in_script() -> [(1, 'claude-cli')]
FLAGGED  /home/lumen/minerva/minerva_factory.py:284  [minerva-factory.timer (enabled, ~4h)]
         line: "FORBIDDEN_KEY_ENVS = ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY')"
         markers_in_script() -> [(1, 'anthropic-key')]

4/4 data-literal lines misread as a live Anthropic call
```

None of these four lines executes anything:

- `lumen_intel.py:93` and `signal_engine.py:47` — entries in a topic/keyword
  classifier's tag list (content tagging vocabulary), matched by the
  `claude-cli` regex `[\"']claude[\"']\s*[,\]]` (`timer_spend_audit.py:61`),
  which was written to catch argv-literal shapes like
  `["claude", "-p", prompt]` and cannot distinguish that from any other
  comma-terminated quoted `"claude"` in a list.
- `worklog_gen.py:17` — a bash array of terms *excluded* from a persona's
  work log ("never let Lumen read his own persona/identity plumbing in his
  ledger", the line directly above it) — `"claude"` appears as a value being
  filtered out, not invoked.
- `minerva_factory.py:284` — `FORBIDDEN_KEY_ENVS = ('ANTHROPIC_API_KEY',
  'OPENAI_API_KEY')`, a guard tuple whose entire purpose (per the comment
  block immediately above, lines 279-282) is to stop an Anthropic key from
  being auto-applied to a route, after exactly that happened once on
  2026-08-15. The `anthropic-key` marker regex
  `ANTHROPIC_(?:API_KEY|AUTH_TOKEN)` (`timer_spend_audit.py:65`) matches the
  string regardless of whether it appears as a live `os.environ` read or as
  a name being forbidden.

## This is live today, not a hypothetical

All four services are real, enabled, currently-firing timers
(`systemctl list-timers --all`, 2026-09-24):

```
minerva-factory.timer                Thu 2026-09-24 11:35:08 UTC   (~4h cadence)
lumen-intel.timer                    Thu 2026-09-24 12:20:17 UTC   (~1h cadence)
lumen-worklog.timer                  Thu 2026-09-24 16:13:55 UTC   (~6h cadence)
lumen-signal-engine.timer            Fri 2026-09-25 06:00:00 UTC   (daily)
```

A full, unrestricted run of `timer_spend_audit.py` (no `--roots`, this
worker's own sandbox happened to have read reach into
`/home/lumen/{agent,personas,minerva}` this session) against the live fleet
returns:

```
171 timers — cloud=4 unknown=25 pinned=0 clean=142
  CLOUD    lumen-intel.service [enabled]
           /home/lumen/agent/lumen_intel.py:93 claude-cli
           /home/lumen/agent/lumen_intel.py:107 claude-cli
  CLOUD    lumen-signal-engine.service [enabled]
           /home/lumen/agent/signal_engine.py:47 claude-cli
  CLOUD    lumen-worklog.service [enabled]
           /home/lumen/personas/worklog_gen.py:17 claude-cli
  CLOUD    minerva-factory.service [enabled]
           /home/lumen/minerva/minerva_factory.py:284 anthropic-key
```

(`lumen_intel.py:107` is the same keyword-list shape one line further into
the same tag vocabulary — a second hit from the identical root cause, not a
fifth distinct case.)

## Why this matters for the verdict

The PR's stated purpose is to make "no scheduled service defaults to Max"
checkable and `lumen-timer-spend-guard.timer` is "what keeps it checked"
(`README.md:9-13`) — an unattended daily job whose only output on a finding
is an `aq` ticket plus a high-priority `ntfy` push (`alert()`,
`timer_spend_audit.py:385-404`). Installing it today files that alert on the
very first run, about four units that spend no Anthropic quota at all. The
PR's own commit message already identifies the failure mode this produces —
"Flagging it would train the reader to ignore this guard" — while
describing a narrower fix (CLI subcommand exemptions) that leaves the
broader data-literal shape wide open. A guard whose first live alert is
wrong is worse than no guard: it does not merely miss cost, it manufactures
false ones an operator has to spend time disproving, at the same terminal
that already got burned once — `minerva_factory.py:279-282` records the
2026-08-15 `anthropic-fable5` incident this exact false positive would send
someone chasing.

Parts of the PR are sound and independently re-verified:

- `parse_show_blocks` / `exec_argv` / `env_file_path` / `gateway_pin` /
  `script_target` — read, and exercised against the real systemctl output on
  this box; all 32 shipped tests re-run clean (`python3 -m pytest tests -q`
  → `32 passed`).
- The `unknown`-never-masks-as-clean design (unreadable scripts, unreadable
  `EnvironmentFile`s) is correct and independently confirmed: this session's
  full-read sweep resolved 25 units the PR's own sandbox reported `unknown`
  down to real verdicts, and reclassified zero of the PR-author's `clean`
  findings — the mechanism the PR relies on to "finish the audit" as `lumen`
  works exactly as designed. It just walks into this regex gap on the way.
- The five originally-named timers (`sia-news-pull`, `motd-news`,
  `lumen-scout`, `lumen-ebay-watch`, `lumen-news-digest`) — re-checked,
  verdicts unchanged from the PR's table (clean / vendor / no-call / no-call
  / unknown).
- Not independently exercised: `install.sh`'s `sudo install` / `systemctl
  enable --now` steps (would touch `/etc/systemd/system`, outside this
  worker's sandbox and this ticket's remit either way).

## Reproduction

`reports/2457_probe_timer_spend_script_marker_false_positives.py` — no
network or systemd state touched, imports the shipped module unmodified,
output pasted verbatim above. `python3 -m pytest ops/wq2428-timer-spend-guard/tests -q`
— 32 passed, unchanged from the PR.

Not implementing a fix here — this is a verify-pass, not a re-implementation.
Fix shape for the escalated judgment: the `claude-cli` and `anthropic-key`
script-body markers need a structural check the same way the argv-side
`claude-cli` marker already has one (`markers_in_exec`,
`timer_spend_audit.py:155-165`, "checked structurally rather than by regex")
— e.g. require the matched token sit inside a real call expression
(`subprocess.run([...])`, `os.environ[...]`) rather than any quoted,
comma-terminated string, or exclude matches that fall inside a `[...]`/`(...)`
literal whose enclosing assignment name suggests a list/tuple of terms
(`_EXCLUDE`, `_FORBIDDEN`, `KEYWORDS`, etc. — heuristic, so probably paired
with a widened test corpus rather than trusted alone). Either way, add
regression tests seeded with the four real shapes above before the guard
timer installs, and re-run the full-read sweep to confirm `cloud` drops to 0
on the current fleet before this is trusted to alert unattended.

Also noted, not blocking the verdict: the ticket that dispatched this
verify-pass says bundles without a `--verify` handler need one added "before
confirming" (`ops/VERIFY_CONTRACT.md`, wq#1696) — `install.sh` has none.
Since this verify-pass ends in CONTRADICT, not confirm, none was added here;
whoever lands the regex fix should add the handler in the same change so the
corrected bundle both verifies its own on-box state and is confirmable on
the first pass.
