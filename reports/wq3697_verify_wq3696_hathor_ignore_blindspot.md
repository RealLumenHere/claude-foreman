# wq#3697 — verify-pass (archetype 8) of wq#3696: hathor tailscale-offline, added to selfheal nodes.ignore

Reviewer: claude-sonnet-5 @ high (fresh instance, same model as author). Worktree
`wq3697-a1`. Verified 2026-10-08. Subject: PR #1720 (`lumen/claude-foreman`,
squash `fc1839ad`, merge commit `6c9534c3`, already on `main` in this worktree's
history).

## Verdict

**CONTRADICT.** The commit's premise — "the 30min tailscale outage... is designed
behavior [auto-sleep napping], not an incident" — is true for the specific idle-sleep
window, but the fix doesn't scope the suppression to that window. `nodes.ignore` is an
unconditional, permanent silencer (`ops/wq3354-fleet-selfheal/sh_nodes.py:118-120`):
once a node is in `ignore`, `sweep()` sets `ns["not_fleet"] = True` and never files a
ticket for that node again, for any outage, of any length, for any cause. hathor
already has a second, independently documented, **unfixed** failure mode on the exact
same signal (tailnet reachability after its sleep→wake cycle) that this blanket
suppression now hides forever:

- `/home/lumen/bin/wake-dispatch.sh:96` (wq#3685, committed, not test fixture):
  `# LiteLLM reaches Macs over the tailnet; a cold boot can leave tailscaled 'network
  down' (seen on hathor 2026-10-08)`, followed at line 97 by a check that only **logs**
  `"awake on LAN but tailnet $tsip:$PORT unreachable (tailscaled boot race? 'sudo
  systemctl restart tailscaled' fixed it 2026-10-08)"` — it does not auto-remediate.
- hathor's wake path is a real cold boot, not a resume: WoL magic-packet wake was
  tested and **failed** on hathor 2026-10-08 (`lumen_state hathor.wake_on_lan`); the
  working path is a full smart-plug power cycle (`plug-ctl p3 off; sleep 6; ... on`),
  confirmed again in `wake-dispatch.sh`'s own wake-method ordering (WoL attempted,
  falls back to plug cycle).
- So every wake out of the new `auto-sleep.timer` poweroff is a cold boot, and cold
  boot is the exact trigger already observed (same day, 2026-10-08) to leave
  tailscaled dark on the tailnet with no self-heal.
- The codebase already distinguishes these two cases by design. `selfheal.yaml`'s
  `nodes.notes` dict exists precisely for "known-issue context folded into a node's
  outage ticket... **NOT a silencer like ignore above, the page still fires**" — and
  its one live example is explicit about the danger: `nephthys: "...do NOT add
  nephthys to nodes.ignore, that would just silence the page he needs."` hathor's
  tailscaled-boot-race is the same shape of problem (recurring, real, needs a human to
  notice and intervene) that the repo's own convention says belongs in `notes`, not
  `ignore` — the PR put it in `ignore`.
- No other selfheal class fills the gap. `sh_ollama.py`'s module docstring states
  plainly: "A host whose `/api/ps` does not answer is left alone; ollama being down is
  CLASS 2 and lumen-failed-units territory" — CLASS 2 (crashloop/guards) runs local
  systemd checks, not a remote tailnet-reachability probe. CLASS 6 (`sh_nodes.py`) was
  the one check watching "is this node visible on the tailnet," and it is now
  permanently blind for hathor.

## Reproduction

Ran the actual shipped `sh_nodes.sweep()` against a fake `tailscale status --json`
reporting hathor offline, first crossing the 1800s threshold, then again 6 hours
later, both with and without hathor in `ignore` (script left at
`/tmp/fworker-wq3697/repro_hathor_blindspot.py`, reproducible from
`/home/lumen/worktrees/wq3697-a1` at commit `fc1839ad`):

```
$ python3 /tmp/fworker-wq3697/repro_hathor_blindspot.py
hathor_in_ignore=True: ticket@30min=False ticket@6h=False
hathor_in_ignore=False: ticket@30min=True ticket@6h=False
```

With the shipped config (`hathor_in_ignore=True`), no ticket fires at 30 minutes *or*
at 6 hours — the suppression has no duration bound, confirming it is not scoped to the
"~30min nap" the commit describes. Flipping the one input the fix controls
(`hathor_in_ignore=False`) flips the 30-minute result back to a filed ticket, so this
is a falsifiable probe, not a guess. (The `False` "no new ticket at 6h" result in that
row is correct dedup behavior — the 30-min ticket is already open — not a gap.)

Also ran the existing suite to confirm no mechanical regression from the YAML edit
itself: `cd ops/wq3354-fleet-selfheal && python3 -m pytest tests/ -k node -q` → `3
passed`. The YAML is well-formed and `hathor` parses into `nodes.ignore` as intended;
the defect is in which knob was used, not in how it was wired.

## What still holds

- The stated class (recurring ~30min tailscale blips purely from the new idle-sleep
  timer, already in the install audit window) is real, and it's genuinely noisy to
  page every nap if that were the only thing wrong with hathor.
- `nodes.ignore` and `nodes.notes` are both correctly implemented per their own
  contract; this isn't a code bug, it's a config/judgment choice on which contract to
  invoke for a node that has two independent problems (one truly benign, one real and
  unfixed) sharing the same symptom.
- selfheal.yaml parses cleanly post-edit; `pytest tests/ -k node` is green; the fix
  does not break any existing covered behavior.

## Judgment call needed

Should hathor's tailscale-offline alert be fully silenced (`ignore`, as shipped), kept
alive with context (`notes`, the nephthys pattern), or something bounded (e.g. only
suppress outages under some ceiling tied to the sleep timer's own window, paging
anything longer)? That's a tradeoff between alert fatigue from routine naps and
losing the only safety net for a same-day, already-observed, unfixed boot-race bug on
a fleet-managed inference node.

Escalating per ticket instructions to opus-5@high — filed as wq#3702.
