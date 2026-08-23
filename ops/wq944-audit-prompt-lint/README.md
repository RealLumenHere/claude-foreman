# wq#944 — PRODUCT-LIVENESS reaches the audit sessions it targets

wq#909 wrote the PRODUCT-LIVENESS rule into `/home/lumen/instructions/playbooks/fleet-audit.md`
and pointed at it from `playbooks/session-start.md`. wq#921's verify pass contradicted that
fix: a dispatched audit is an fworker one-shot that runs the numbered bootstrap written into
its own prompt text. It never boots session-start.md, so the rule reached nothing. Rerunning
`FABLE_AUDIT_2026-08-23.md` — the prompt whose run wq#909 exists to fix — still had zero
automatic path to it.

## The two channels a dispatched audit actually reads

| Channel | Why it reaches | Change |
|---|---|---|
| `/home/lumen/instincts.yaml` | bootstrap step 1 of all three prompts is `claude_context.sh --core`, which injects `head -c 4096` of this file; steps 2 read it in full | PRODUCT-LIVENESS added as **rule 1**, so it survives the cutoff |
| `/home/lumen/prompts/{FABLE_AUDIT_2026-08-23,FLEET_OPS_AUDIT_2026-08-23,MECHANICS_AUDIT_PROMPT}.md` | the de facto templates the next audit gets copied from | a `Read .../playbooks/fleet-audit.md` step added to each SESSION BOOTSTRAP |

Those four files live outside git; each was backed up as `*.bak.20260823_wq944` in place, the
house convention for `/home/lumen/instructions` and `/home/lumen/prompts`. `fleet-audit.md` §3
gained a "Where this is wired" note so the next editor does not undo it — in particular, moving
the instincts rule down the list silently unwires channel 1.

## The lint

`audit_prompt_lint.py` re-checks both channels. It asserts the rule is inside the 4096-byte
`--core` window (present-in-file is not present-in-session) and that each audit prompt names
the playbook **inside its own bootstrap block** — a mention anywhere else is the wq#909 shape
and fails.

```
python3 audit_prompt_lint.py                      # live fleet; exit 0 = wired
python3 audit_prompt_lint.py <instincts> <dir>    # any pair, for testing
python3 -m pytest ops/wq944-audit-prompt-lint/    # 7 tests
```

Verified against the pre-fix state restored from the backups: 4 failures, one per broken
channel. Against the live post-fix state: exit 0. `claude_context.sh --core | grep
PRODUCT-LIVENESS` now hits.
