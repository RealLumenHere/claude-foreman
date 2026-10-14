# wq#3862 verify: wq#3858 [guard] guard.sh refused plain wq done with long --notes 3x

Archetype 8 adversarial verify pass. Author: claude-opus-5-5. Verifier: claude-sonnet-5
(fresh instance). Verify repo: `/home/lumen/foreman`.

## What wq#3858 claimed

wq#3858 was scoped `[NO-ARTIFACT: investigation; report only]` — RCA only, no fix to land.
Its finding (notes field, mirrored in ISSUE-LOG row 1304): wq3854-a1's `wq done --notes`
calls 1–2 were blocked not by the wq#2274 credential-shape rule (as the original bug report
assumed) but by `guard.sh:122`'s denied-verb scan, which reads the raw Bash command string
— including the text inside a quoted `--notes` arg — for `sudo|reboot|shutdown|poweroff|halt
|init 0|6`. Both blocked notes contained the prose word "reboot" describing a post-reboot
smoke test, not an attempt to reboot anything. `failure.json` (argv0 `ssh`) is a *different*
failure from attempt 2 (ssh not allowlisted), not the same incident. Call 3 (reworded to
"restart") is explicitly flagged **unconfirmed** — the transcript for it is guard-denied, so
wq3858 could not prove it passed the guard but then failed wq's own `--no-artifact` evidence
gate (wq#759/wq:3074); that part is a hypothesis, not a demonstrated claim. wq3852/wq3853
passed because their notes contained no denied word. A narrow fix was prototyped
(`/tmp/fworker-wq3858/guard_proposed.sh`, ephemeral) and specified in full in the new ticket
it filed, wq#3861, rather than landed in this ticket.

## What I checked

- `git -C /home/lumen/foreman log --oneline main..fix/wq3858-...-a1`: **empty** — wq3858's
  branch has zero commits ahead of main.
- `git -C /home/lumen/foreman diff main fix/wq3858-...-a1 --stat`: only shows files main
  gained *after* the branch point (branch is stale-behind, not ahead) — confirms no code
  diff was ever produced on this branch.
- `find reports -iname "*3858*"`: no report file either (the write-up lives in the ticket's
  own `notes` field and ISSUE-LOG row 1304, not a `reports/` doc — consistent with
  NO-ARTIFACT framing, nothing to fault).
- **Conclusion up front: there is no diff to adversarially break.** wq3858 did exactly what
  its ticket asked (investigate, don't fix) and did not overstep into shipping code under a
  report-only ticket. The actual fix is scoped to wq#3861 (status: `claimed`, in progress
  under a different worker as of this verify pass) and is out of this ticket's scope.
- Given no diff, I verified the *investigation's claims* against primary evidence instead,
  since those claims are wq3858's actual deliverable:
  - `harness/guard.sh` lines ~117–124: confirmed the denied-verb `for target in "$RAW_CMD"
    "$NORM"` loop and the `grep -qE '(^|[^[:alnum:]_./-])(sudo|reboot|shutdown|poweroff|halt
    |init +[06])\b'` pattern exist exactly as described, and that it runs before any
    quote-awareness.
  - `events/fworker-wq3854-a1.jsonl`: lines 5–6 are `"event":"blocked","reason":"denied verb
    (sudo/reboot/shutdown/poweroff/halt/init 0|6)"` at `06:56:11Z`/`06:56:18Z`, with
    `snippet` showing the blocked text is the quoted `--notes` prose containing "reboot" —
    matches the claim exactly, not the wq#2274 credential-shape reason.
  - Same file: no 3rd `wq done` attempt or `blocked`/`gate` event appears between the
    `06:56:18Z` block and the `06:56:22Z` session `end` — the transcript genuinely has
    nothing to confirm or refute call 3, matching wq3858's own "unconfirmed" hedge. It did
    not overclaim here.
  - `_runs/wq3854/failure.json`: contains `"argv0": "ssh"`, `"target": "lumen@..."` —
    confirms this is the separate ssh-not-allowlisted failure from attempt 2, correctly
    distinguished from the attempt-1 guard block.
  - `wq show 3852` / `wq show 3853` notes: neither contains any of
    sudo/reboot/shutdown/poweroff/halt/"init 0"/"init 6" — confirms the comparison claim
    ("passed because their notes did not contain a denied word").
  - ISSUE-LOG row 1304: present, status `open: proposal, needs a BUILD ticket to land` —
    consistent with wq#3861 existing as that build ticket and not yet being done.
  - wq#3861 (the proposal ticket): read in full — its spec matches wq3858's notes
    (blank quoted args of a lone `wq` call before the denied-verb scan only, narrow
    must-allow/must-block corpus, credential-shape rule untouched). Confirms wq3858 did not
    silently smuggle a broader fix past its own NO-ARTIFACT scope.

## Could I break it?

There is nothing executable to break — wq3858 shipped zero lines of code. The only thing an
adversarial pass can test is whether the *claims* hold up against primary evidence, and every
checkable claim did (guard.sh source, two independent event-log entries, failure.json,
ISSUE-LOG, and both comparison tickets). The one claim wq3858 could not verify (call 3) it
explicitly flagged as unconfirmed rather than asserting — the honest move, not a gap to
fault.

## Verdict

**CONFIRM.** wq#3858's investigation is accurate, evidence-backed on every checkable point,
and correctly scoped as report-only — it diagnosed an over-match in guard.sh's denied-verb
scan (prose inside quoted `wq --notes` args gets scanned as if it were a command) without
touching the credential-shape rule the ticket said not to loosen, and deferred the actual fix
to a new ticket (wq#3861) instead of landing one under a NO-ARTIFACT ticket. No contradiction,
no escalation to judgment.

---
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
