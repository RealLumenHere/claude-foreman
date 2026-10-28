# wq#4759 — B1-T0 replay: classify the synthesis's 14 cap-denied + 11 root blocked tickets

Dry-run only. No spawn, no wq mutation. Classifies each addressable blocked ticket against
`foreman/docs/agent-authority-research/DESIGN.md` (wq#4758) §2.2 (what is holdable) and §3.3/§4
(the Pilot-2 recipe catalog).

## Method and its one honest gap

SYNTHESIS.md (`/home/lumen/docs/agent-authority-research/SYNTHESIS.md:3`) gives only the **counts**
— "capability-denied (14) + root/sudo (11) = 25" of 67 blocked — not the per-ticket ids. The
per-ticket lists lived in reports 1/2/3, which never landed in that dir (the wq#4758 worker was
refused the needs:dir grant, ISSUE-LOG row 1627); DESIGN.md §0 confirms the same wall and says
"Per-ticket lists of the 14 + 11 live in the synthesis. Build ticket B1-T0 replays them."

So the exact 25 cannot be read back verbatim. I **reconstructed** the cluster by applying the
synthesis's own definitions (SYNTHESIS.md:3-4, 10-15) to the live blocked set (68 rows,
`queue.db` `tasks WHERE status='blocked'`, pulled 2026-10-28). Confidence anchors that make the
reconstruction tight rather than a guess:

- credential-burn (3) is exactly the three "REAL burn" rows: wq#2514, wq#4655, wq#4656.
- The capability-denied read cluster is dominated by rows the harness itself tagged
  `CAPABILITY_DENIED` / `CITED_PATH_UNREACHABLE` / `homeroot(...) refused`.
- root/sudo is every row whose stated blocker is privileged execution (sudo / `systemctl
  enable|start` / `apt-get install` / root `install.sh` / unit removal).

Where the live count forced a judgement call the row is flagged, and §"Borderline / not in the 25"
lists the tickets a different reader might swap in. The 14+11 below match the synthesis counts.

## Holdability rules used (verified against source, this branch's add-dirs)

- H1 path-read / H2 repo-read, and the "not holdable, ever" list: DESIGN.md §2.2 (lines 79-92).
- `_deny_reason` (the hard path deny H1 must clear): `capability_lint.py:600-617`.
  `DIR_DENY_EXACT = ("/home/lumen", "/")` at `capability_lint.py:55`; `DIR_DENY_PREFIXES`
  (`.secrets .ssh .claude .config secrets worktrees scratch/worktrees`) at `:46-54`.
- `VERIFY_HOME_ROOT_READ_ALLOW` (the verify-worker homeroot carve-out, 5 named files only):
  `capability_lint.py:66-72`; its refusal string is minted at `:75`.
- Managed-repo set for H2 (`managed_repos()`, `repo_sweep.py:94`) resolves live to: bin, ccc,
  foreman, mailbot, **messenger**, minerva, mission-control, pokemon,
  **nest/NoLongerEvil-Thermostat**, pstack, questfree, fleet/inventory, agents/storage-audit.
  `/home/lumen/re` and `/home/lumen/sentinel` are **not** managed repos.
- Recipe catalog: one recipe exists in the pilot — `cloudflared.ingress.upsert` v1, action
  `upsert|remove`, on node **bes** (fallback anubis), target fixed to `/etc/cloudflared/config.yml`
  (DESIGN.md §3.2-3.3, lines 265-300). Lumen/sobek/maat/thor are never targeted (§3.2).

## Pilot 1 — capability-denied (14)

| wq | what it needs | class §2.2 | verdict + rule |
|---|---|---|---|
| 350 | read `/home/lumen/firmware/sodola-sl-swtgw2224as/TEST_PLAN.md` (outside add_dirs; not a managed repo) | H1 path-read | **HOLDABLE** — `_deny_reason`=None, not credential |
| 1152 | read `/home/lumen/projects/nest/nle-flasher/*` (outside add_dirs; `nle-flasher` ≠ the managed `nest/NoLongerEvil-Thermostat`) | H1 path-read | **HOLDABLE** |
| 4708 | read `/home/lumen/scratch/openhands-test/` | H1 path-read | **HOLDABLE** — `scratch/` is not under the `scratch/worktrees` deny-prefix |
| 4733 | read `/etc/hostname` | H1 path-read | **HOLDABLE** — outside add_dirs, `_deny_reason`=None |
| 4753 | read `/home/lumen/re` (homeroot) | H1 path-read | **HOLDABLE** — `_deny_reason("/home/lumen/re")`=None (not the exact `/home/lumen`, not a deny-prefix); H1 **supersedes** the verify-only `VERIFY_HOME_ROOT_READ_ALLOW` gate that refuses it today (`capability_lint.py:75`) by materializing a read-only bind+add-dir (§2.4) |
| 4757 | read `/home/lumen/re` (homeroot; duplicate of 4753) | H1 path-read | **HOLDABLE** — same as 4753 |
| 4717 | list `/home/lumen` | — | **NOT HOLDABLE** — `/home/lumen` ∈ `DIR_DENY_EXACT` (`capability_lint.py:55`); §2.2 H1 defers only paths "not rejected by `capability_lint._deny_reason`"; §2.4 "the bound unit is the directory the request named, never a parent of it" |
| 2443 | list `/home/lumen/*.md` + `/home/lumen/projects/*/*.md` | — | **NOT HOLDABLE** — first glob's parent is `/home/lumen` (`DIR_DENY_EXACT`, `capability_lint.py:55`); the second is a wildcard across many dirs, which the single-directory bind of §2.4 cannot materialize |
| 3312 | read `/home/lumen/sentinel` (run activate-mcpw-pool-probe.py) | — | **NOT HOLDABLE** — `fleet-exec-policy.yaml:61-69` deliberately excludes it from `allow.read_dirs` (live sntl tokens, wq#1466) with a header stating no worker/needs: tag can widen it (fail-closed broker). A lower-layer policy deny; §1 invariant: an approval "can never turn a policy deny into anything" |
| 1197 | **write** `agent_bridge.py` into `/home/lumen/messenger` (a managed repo) | H2-shaped | **NOT HOLDABLE** — (a) it is a write; §2.2 "Not holdable, ever: writes of any kind"; (b) messenger is excluded from grant lists per `fleet-exec-policy.yaml` (vapid key + chat-log token). The H2 broker is read-only git objects, so it never satisfies this ticket |
| 4444 | **write** live scripts under `/home/lumen/agent` (litellm_config.yaml + minerva_*/lumen_* callers) | — | **NOT HOLDABLE** — writes (§2.2); in-place non-git production edits with `.bak`, not a read grant and not landed by PR |
| 679 | `adb` (Android debug bridge) Bash verb | — | **NOT HOLDABLE** — `adb` is off the Bash allowlist; §2.2 "Bash verbs that are not on the allowlist ... are allowlist proposals made by PR, not runtime approvals" |
| 2761 | raw `ssh` to apep/gameboy/nephthys/seshat; no fleet-exec grant | — | **NOT HOLDABLE** — `ssh` off the allowlist + remote-exec, not a read verb (§2.2). This is the remote-ssh/offline-node class the synthesis routes to a **secondary** tool (Teleport/Boundary, SYNTHESIS.md:43), not Pilot 1 |
| 349 | `ssh` remote-exec to Thor (bench-host) | — | **NOT HOLDABLE** — §2.2 lists "ssh to thor" among the never-held; THOR_HARDLOCK / instinct 2; remote-exec is not a read |

**Pilot-1 result:** 6 holdable (all H1 path-read: 350, 1152, 4708, 4733, 4753, 4757), 8 not
holdable. The §4 success measure ("zero holds of a class §2.2 says is not holdable") is met on this
replay: the 8 refusals each cite a hard rule, not a widenable one. Two sub-findings worth carrying
into B1-T1:

- `/home/lumen/re` (4753/4757) is holdable under H1 purely because `_deny_reason` matches only the
  *exact* string `/home/lumen`, not its children. If B1-T6's deny-by-default base lands, confirm
  H1 still resolves these; until then the current refusal is the narrower verify-only homeroot gate.
- Three cap-denied rows are **writes** (1197, 4444, and the land/cite half of 1155, see below), not
  reads. The hold state is read-only by construction; these need a write-capable lane
  (needs:dir+write / correct repo worktree), not Pilot 1. Count them as structurally out of Pilot 1.

## Pilot 2 — root/sudo (11)

Recipe column = coverage by the one pilot recipe `cloudflared.ingress.upsert` v1 on bes/anubis.

| wq | privileged op | recipe coverage |
|---|---|---|
| 4203 | cloudflared ingress edit `/etc/cloudflared/config.yml` + `systemctl enable lumen-research-reviewer.service` + detached `systemd-run` restart | **PARTIAL** — the ingress edit + restart matches `cloudflared.ingress.upsert(upsert)`, but the unit runs on **lumen** (pilot never targets lumen, §3.2) and the `systemctl enable` of a *new* unit is outside the recipe |
| 3564 | 6 items incl. (3) chrome-mcp cloudflared ingress **removal** + root restart; (6) retdec root install; (2)(4) writes to `/home/lumen/bin` & `/home/lumen/minerva` | **PARTIAL** — item 3 matches `cloudflared.ingress.upsert(remove)` shape; the rest need new recipes / are writes. Mixed cap+root ticket |
| 358 | install `n100-efficiency.service` pack on **bes** (service + sysctl); Sia also needs a sudo pw (wq#1690) | **NO** — needs a new signed sysctl/service recipe (§4, by PR). Bes *is* the pilot node, so a good second recipe candidate |
| 2456 | `bash install.sh` (lumen-timer-spend-guard) + `systemctl start` 5 timers on Lumen | **NO** — no timer-enable recipe; Lumen not a pilot node |
| 3003 | root on Sia/Anubis/Bes/isis/hathor/khepri/nephthys via `install.sh` | **NO** — multi-node install.sh; one new recipe per op/node |
| 3468 | 25 ops-deploy-drift applies owned by wq#3002/3003/3005/3006/3564 | **NO** — meta/deps; clears when the owners' root applies land |
| 3609 | `install.sh` (MINIFLUX_API_KEY) + `ssh sia` verify | **NO** — root install op; also credential + remote-ssh |
| 4333 | `scp`+`ssh` privileged deploy to maksoor; needs a fleet-exec root-write verb that does not exist (`fleet-exec-policy.yaml:230-238`) | **NO** — the remote root-write primitive itself is unimplemented |
| 4487 | `apt-get install fail2ban` + `systemctl enable` on Horus | **NO** — no package-install recipe; Horus is explicitly excluded from every restart/root-exec grant |
| 4704 | root `remove-units.sh` on Lumen | **NO** — unit removal is explicitly **out** of the pilot (§3.2 "NOT unit removal"); Lumen not a pilot node |
| 3659 | `install.sh` needs `LITELLM_ADMIN_KEY` operator master key at install time | **NO** — root install; also frank/credential-gated (operator must supply the master key, a physical act) — the most reconstruction-ambiguous of the 11 (root vs frank-gated) |

**Pilot-2 result:** 0 of 11 fully covered by the single pilot recipe. Two are partial shapes
(4203, 3564-item3) but on non-pilot nodes. This directly supports §4's plan to "extend the catalog
to the 11 root tickets one recipe at a time, each by PR": the pilot's first recipe closes none of
them, and bes-local 358 is the natural second recipe (sysctl/service pack) since the pilot node is
already bes. 4704 (unit removal) and 4487 (Horus, package install) are the two that the design's
own scope rules keep permanently out until the catalog and node-allowlist grow.

## Borderline / not in the 25 (so a reader can re-draw the line)

- **wq#1155** — nest `NoLongerEvil-Thermostat/firmware/builder/build.sh` implemented but
  "cannot be landed or cited" from a foreman worktree. A capability gap, but a **land/cite** one
  (write), not a read hold; fix is correct repo-worktree routing (wq#4623 class), not Pilot 1.
  Swap it in for any cap-denied write row if preferred.
- **wq#4184** — gitea token rotation; real blocker is zero ssh entries in the Bash allowlist
  (remote-exec to anubis), using existing `agent/.env` creds — **remote-ssh class, no root**.
  Belongs with 2761/349 under the secondary Teleport tool, in neither pilot. I kept it out of the
  11 root for that reason.
- **wq#2474** — fleet pw rotation; remaining nodes (apep/gameboy/seshat) are **offline** — physical,
  not root.
- **Credential-burn (3), out of scope:** wq#2514, wq#4655, wq#4656 (correctly unactionable,
  SYNTHESIS.md:4).

## Acceptance

- Table covers all 25 (14 cap-denied + 11 root), matching SYNTHESIS.md:3 counts.
- Not-holdable rows with the exact rule cited: **wq#4717** → `DIR_DENY_EXACT "/home/lumen"`
  (`capability_lint.py:55`); also wq#2443 (same rule), wq#3312 (`fleet-exec-policy.yaml:61-69`),
  wq#1197 / wq#4444 (§2.2 "writes of any kind"), wq#679 (§2.2 allowlist-proposal), wq#2761 / wq#349
  (ssh / remote-exec, §2.2). Eight of the 14 are not holdable.
- Dry-run: no ticket was spawned, claimed, or mutated.
