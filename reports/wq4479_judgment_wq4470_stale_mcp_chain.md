# wq#4479 — Adversarial verify-pass of wq#4470 (PR#2347: Claude Code CLI as Commander replacement — research)

**Verdict: CONTRADICT (opus-5@high tie-break: CONTRADICT, narrower than my framing).** The
doc's headline verdict (PARTIAL; don't install Claude Code CLI as a Commander replacement;
swapping the stdio backend removes no proxy layer) survives independent re-check and is not
overturned. But the "Where the premise breaks" chain description and the "Recommendation"
section rest on a stale/self-contradicting citation and mischaracterize what fix is actually
"in flight," omitting a more recent, more directly on-point initiative that was already in
the author's own tree.

## What was shipped

PR#2347 (branch `feat/wq4470-research-claude-code-cli-as-commander-re-a1`, commit `59920a3f`)
adds `research/WQ4470_CLAUDE_CODE_COMMANDER_EVAL.md` (84 lines), answering "could Claude Code
CLI replace Desktop Commander as the fleet's remote-control MCP mechanism?"

## Probe

(1) Re-check every local file:line citation against the actual repo state and live systemd
state on Lumen. (2) Check whether the "proxy chain" the doc describes is still the live one.
(3) Prior-art sweep of `ops/` for anything more recent than the doc's own cited `wq#2744`
(P1 pair) that addresses the same "remove the hop" goal — the doctrine's mandatory
prior-art step applies to the verify pass too, not just the original research.

## Finding 1 — the chain description cites a confirmed-retired service, not the live one

The doc (lines 49-57) says Commander's chain is `desktop-commander` (stdio) ->
`supergateway` (stdio->HTTP, `FLEET_SERVICE_ATLAS.md:104`) -> `cloudflared` -> `mcpw.lumennode.uk`
Worker, with the consumer named at `FLEET_SERVICE_ATLAS.md:86` ("desktop-commander-mcp :3100 |
claude.ai browser sessions").

Live check on Lumen:
```
$ systemctl show desktop-commander-mcp.service -p LoadState,ActiveState,UnitFileState
LoadState=masked
ActiveState=inactive
UnitFileState=masked
```
`docs/fleet/retired_services.yaml:322-330` (`retired_on: 2026-10-13`, wq#3805/wq#3781):
"Commander now runs on Horus (mcp-proxy)... The mcp-backend.lumennode.uk -> :3100 cloudflared
route was removed... nothing connects to :3100." The doc quotes this exact line (line 50) one
sentence before describing the dead :3100 chain as "why the chain has the shape it has" — a
self-contradiction inside the doc itself, not just against external state.

Opus-5@high's independent pass adds the sharper point I missed: `research/FLEET_SERVICE_ATLAS.md:3`
timestamps the atlas as a 2026-09-10 snapshot, and the file's own lines 4-5 warn against citing
it as current state without re-checking. The doc cites it as if it were live.

What survives: `ops/wq3527-dc-gateway-loopback-a1/30-mcp-proxy.conf` (opus's find, independently
of the retirement note) shows Horus's `dc-gateway` underwent an "mcp-proxy cutover" that "kept
parity with the old supergateway unit" — desktop-commander -> mcp-proxy -> cloudflared -> mcpw,
same shape, relocated host. So the doc's substantive argument ("swapping the stdio backend
removes no layer") is not actually falsified — only its citation (a dead host's service) is.

## Finding 2 — wq#2744 (P1 pair) is mischaracterized as "the actual fix... already in flight"

The doc's Recommendation (lines 77-84) points at `foreman/ops/p1-commander/` (wq#2744) as "the
real fix for the single-hop/chokepoint complaint" and tells the reader to "let wq#2744's P1
pair land."

`ops/p1-commander/README.md:3-5`: "Prep only, per the ticket: nothing here has been installed,
deployed, or run. Boxes... are not on hand yet." `ops/p1-commander/mcpw-cutover-plan.md`
targets a `MCP_BACKENDS = lumen-only` topology (verified 2026-09-26) — the pre-wq#3805 world.
The step the doc's own quote leans on (README:97-99, "Lumen leaves the control-plane path")
already happened unilaterally via wq#3805 on 2026-10-13, independent of whether P1 ever lands.
Calling this "already in flight" for the current chokepoint is generous to the point of being
misleading: the hardware doesn't exist yet and the plan it will execute is written against a
topology that no longer exists.

## Finding 3 — a more relevant, more recent initiative is omitted entirely

`ops/wq4329-direct-mcp-commander/README.md` (98 lines, title: "Direct MCP connections:
per-machine Commander replacing the Horus SSH hop") exists in the same tree the wq#4470 author
worked from — commits `39002885`/`a8d41035`, dated 2026-10-19 (git log), four days before
wq#4470's own fleet-search citation date (2026-10-23). It is the one fleet initiative aimed
specifically at the complaint wq#4470's Recommendation addresses (the Horus SSH-hop
chokepoint), and it picks a materially different, simpler mechanism — a hosted Remote Desktop
Commander relay (`mcp.desktopcommander.app`) with per-machine OAuth pairing, no P1 hardware,
no Horus hop — already merged for Lumen + Maksoor.

Grepping the doc for `4329`, `remote desktop`, `desktopcommander.app`, `maksoor` returns zero
hits. The doc's prior-art search (`fleet-search web "Claude Code CLI headless mode
automation"`) never looked at the fleet's own in-flight work on this exact sub-question.

Opus-5@high's correction to my original framing: wq#4329 is merged **code**, not a **live**
fix — `install.sh` deliberately does not `daemon-reload`/`enable`/`start` anything, and
`PHASE1-PAIRING-CHECKLIST.md` still needs Frank's one-time OAuth pairing on each machine; no
`desktop-commander` process is running on Lumen today (`pgrep -af desktop-commander`: no
hits). So wq#4329 is in the same "merged, not deployed" state as wq#2744 — my original
characterization of it as "already-shipped" overstated its liveness. The defect is narrower
than that: it is an **omission** (the doc never found or weighed the one competing plan that
targets the exact complaint it's making a recommendation about), not a "doc recommends the
wrong one over a live one."

## Severity and disposition

Genuine factual/citation defect in the doc's "Where the premise breaks" and "Recommendation"
sections — not drift, since the self-contradiction (citing the retirement note and the
retired service's live-sounding description one paragraph apart) was present at authoring
time, verifiable from files already in the author's own tree. Does not overturn the PARTIAL
verdict or the "no layer removed" conclusion, which both independently survive re-check
(including via the mcp-proxy-cutover evidence neither the original doc nor I initially found).

Per fleet precedent (judgment pass finds the defect; a separate ticket applies the forward-fix;
this ticket does not re-implement): filing a follow-up `[fix]` ticket to amend
`research/WQ4470_CLAUDE_CODE_COMMANDER_EVAL.md` — replace the `FLEET_SERVICE_ATLAS.md:86,104`
citation with the live Horus `mcp-proxy` chain (`ops/wq3527-dc-gateway-loopback-a1/
30-mcp-proxy.conf`), describe wq#2744 as prep-only against a superseded topology, and add
wq#4329 as the actually-merged (if not yet live) per-node alternative the Recommendation should
weigh. Not reopening wq#4470.
