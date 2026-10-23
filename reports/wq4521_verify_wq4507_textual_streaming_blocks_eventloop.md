# wq#4521 — verify-pass: wq#4507 Limpet Textual TUI for agent chat (PR#2373)

**Verdict: CONTRADICT** — `limpet/agent_tui.py`'s `on_input_submitted` iterates
`AgentSession.reply_stream()`, a synchronous generator, directly inside a synchronous
Textual message handler with no `await`/yield-to-loop between chunks. Any backend whose
chunks take real wall-clock time to produce freezes the entire app (repaint, the 1s
tool-table refresh timer, the quit binding) for the duration of the reply. This is a
regression against the ticket's own "streaming token display" requirement and
`docs/agent-chat-spec.md` §4.1/§4.3 ("UI must stay live while a turn is in flight").

Commit: `0e01744b` on `main` (merged), PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/2373.
Files: `limpet/agent_tui.py`, `limpet/test_agent_tui.py`, `limpet/requirements-tui.txt`.

A prior verify attempt on this same ticket (wq#4521 attempt 1) found and reported this
same contradiction and filed judgment ticket **wq#4522** (`deps=4521`, open, assigned
`claude-opus-5@high`), but that attempt's worktree was abandoned before it could stage,
commit, and land its report (it tried `lumen-pr commit` without `git add`-ing the new
report file first — see `_runs/wq4521/loop-handoff-attempt-1.md`). This attempt (a2)
independently reproduced the finding from scratch, cold, before reading attempt 1's
report text, then cross-checked the numbers.

## What the diff does

Adds `limpet/agent_tui.py`: a `AgentSession` class (turn log + `notes/session.jsonl`
persistence, honest "no backend wired" notice when none is injected) shared by two
frontends — a Textual `LimpetChatApp` (chat log, status bar, tool-call `DataTable`
refreshed every 1s, streaming `Static` for the in-flight reply) and a `run_readline`
MVP fallback for headless/no-Textual environments. `main()` picks Textual only when
it's importable, `--no-textual` wasn't passed, and `sys.stdin.isatty()`.

## Verification performed

1. **Read the diff cold** (`git show 0e01744b`), then the ticket (`wq show 4507`) and the
   two specs it cites (`docs/agent-chat-spec.md`, and `docs/agent-processor-spec.md` —
   the latter is what the ticket detail actually names ("Agent-processor-spec.md §9"),
   but that doc only has §1–§8; the implementer's docstring instead cites
   `agent-chat-spec.md` §2/§4/§6/§9, which does have the relevant TUI layout sections.
   Reasonable substitution given the ticket's own citation doesn't resolve — not scored
   as a defect.
2. **Re-ran the shipped suite independently**: `python3 -m pytest limpet/test_agent_tui.py -q`
   → 17 passed (16 claimed in the ticket's done-notes + the Textual smoke test, which runs
   here rather than skipping since this environment has `textual==8.2.8` installed,
   unlike the author's environment where it skipped via `importorskip`). No regressions.
3. **Adversarial probe — slow backend inside the real Textual app** (not the shipped
   suite, which only exercises `backend=None` or an instant generator): built
   `AgentSession` with `backend=lambda text: (time.sleep(0.4) or chunk for chunk in ...)`
   yielding 3 chunks (1.2s total), drove it with Textual's own `app.run_test()` pilot —
   `inp.value = "hi there"; await pilot.press("enter")`. Measured:
   - `pilot.press("enter")` wall time: **1.276s** (two independent runs: 1.276s, 1.276s) —
     matches 3×0.4s almost exactly, i.e. the entire backend iteration happened
     synchronously inside the single message dispatch the pilot awaited.
   - Patched `LimpetChatApp._refresh_tools` (the method wired to `set_interval(1.0, ...)`
     in `on_mount`) with a timestamp-recording spy installed **before** app construction,
     so the timer's bound-method callback captured the spy from the start. The timer's
     prior fire was at t0−0.121s, so its next scheduled fire was due at ~t0+0.879s — well
     inside the press-handler's 1.276s window. Instead it fired once, late, at
     **t0+1.212s** — i.e. it missed its 0.879s deadline by ~333ms and only ran once
     control returned to the event loop near the end of the blocking sleep sequence. Zero
     fires in the timer's actually-scheduled window. This is independent confirmation of
     attempt 1's claim ("the 1s tool-table set_interval refresh fired zero times during
     that window") via a cleaner before/during/after timestamp partition.
   - Final state is still correct (`session.turns` ends with `("agent", "abc")`), so this
     is purely a liveness/responsiveness bug, not a data-correctness one — but it is
     exactly the failure mode constraint §0 and the ticket's "streaming token display"
     line exist to prevent: a multi-second tool/model call would make the whole TUI
     (including `ctrl+c` quit, per the `BINDINGS`) appear hung.
4. **Why this is currently invisible**: `main()` always wires `AgentSession(workspace)`
   with `backend=None`, so the only live chunk today is the instant
   `NO_BACKEND_NOTICE` string — zero delay, so the bug has no symptom yet. It is latent in
   the public `backend: Callable[[str], Iterable[str]]` contract that the next ticket
   (wiring the real `limpet-session` service, `docs/agent-chat-spec.md` §9 step 1)
   inherits verbatim. Any synchronous or slow backend plugged into that contract
   reproduces the freeze immediately.
5. **Checked for related gaps while in the file**: the module computes a full
   `workspace_header()` (target_id, identity, git state) in `status_from_env()` but only
   ever extracts `devices` from it — target_id/identity/git-dirty are never rendered
   anywhere in either frontend. Spec §4.4 wants an always-visible workspace header with
   those fields, but the ticket's own detail line scopes the status bar to
   "model/provider/devices" only, which is exactly what's built — not scored against this
   ticket, noted for whoever picks up the spec's §4.4 panel.

## Conclusion

The diff is clean, tested, and honest about what isn't wired up yet (no fabricated LLM
replies), but it ships a synchronous-generator-in-a-sync-handler integration that blocks
Textual's entire event loop for the duration of any non-instant backend reply —
contradicting the ticket's "streaming token display" requirement the moment a real
backend is attached. **CONTRADICT.** Judgment ticket wq#4522 (opus-5@high) already exists
for the fix decision; this report supersedes/confirms attempt 1's abandoned draft of the
same report under the same filename.
