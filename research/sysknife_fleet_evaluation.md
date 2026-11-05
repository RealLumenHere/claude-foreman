# SysKnife fleet evaluation (wq#4859)

Research date: 2026-11-05. Subject: SysKnife v0.30.0, source read at
`lacs-project/sysknife@2b3da5428a5b2005c2ae4a89690d077f532a266f` (main, 2026-11-04).
Source paths below are relative to that checkout.

## Verdict

**Adopt the design, not the product (for now).** SysKnife is a well-built, honest version of
the root-operation broker the fleet already designed (`foreman/docs/agent-authority-research/DESIGN.md`
§3, "ops-apply daemon"). It does not fit the fleet as-is, for four reasons:

1. **Its approval model is the opposite of ours.** The daemon only accepts an approval from the
   *same account* that previewed the transaction (`crates/sysknife-daemon/src/dispatcher.rs:819-823`).
   `wq grant` refuses exactly that (self-approval, F8, `bin/wq:3608-3619`). A fleet worker running
   as uid `lumen` with Bash or Python can send the daemon an `Approve` frame for its own
   transaction, so the human gate does not hold for our agents. Details in Q4.
2. **No Docker or compose.** The containers family is Podman-only (`docs/action-reference.md:251-252`).
   Docker Compose, which carries most fleet services, has no typed action.
3. **No automatic rollback on Ubuntu/apt.** Rollback is rpm-ostree only. The project says so:
   "Your fleet is Ubuntu/apt and you require automatic rollback today — that is roadmap, not
   shipped" (`docs/comparison.md:97-98`).
4. **One daemon per host, and fleet management is still on the roadmap** ("web dashboard for teams and
   fleet management", `ROADMAP.md`, Phase 10). Remote use means SSH-forwarding each host's socket
   (`docs/vm-daemon-setup.md`, "Who you are over a tunnel").

Recommendation: keep building ops-apply (DESIGN.md §3) and borrow four SysKnife mechanisms, listed
under "What to borrow". Re-check SysKnife after Docker coverage and a cross-account approval mode
ship. Don't install the system daemon fleet-wide.

## Project facts (verified)

| Fact | Value | Source (fetched 2026-11-05) |
|---|---|---|
| License, stars, created, last push | MIT, 142, 2026-06-16, 2026-11-04 | https://api.github.com/repos/lacs-project/sysknife |
| Latest release | v0.30.0, published 2026-11-03 | https://api.github.com/repos/lacs-project/sysknife/releases/latest |
| Release cadence | 0.26.0 (10-22) to 0.30.0 (11-03): 6 releases in 12 days | https://crates.io/api/v1/crates/sysknife-cli |
| crates.io downloads | 1,196 total | same |
| Open issues | 63 | GitHub API above |
| Catalogue size | 191 typed actions in the generated reference (Low 64 / Medium 56 / High 71). Docs say "190+" and the ticket says 189: the count moves with each release | parsed from `docs/action-reference.md` |
| Validated distros | Ubuntu 22.04 / 24.04 / 26.04 (live VM runs). Fedora Atomic is "eligible, unvalidated" | `README.md:27-29` |
| Planner | Needs an LLM provider (Anthropic, OpenAI, Gemini, Ollama, Groq, DeepSeek, Mistral, xAI) | `docs/architecture.md:27` |
| Audit store | SQLite by default; PostgreSQL recommended for production; best-effort RFC 5424 syslog forwarding | `docs/configuration.md:56-89` |

Pace is very high: 6 releases in 12 days, with the wire protocol and the audit encoding
(v1 to v4) still changing. Treat it as pre-1.0.

## Q1. Install on Lumen, run `sysknife doctor`

**Partly done. Doctor was not run: the worker allowlist refused it.**

- Downloaded the `sysknife` and `sysknife-daemon` v0.30.0 x86_64 release binaries to
  `/tmp/fworker-wq4859/bin/`. Both SHA-256s match the release's `sha256sums-linux-x86_64.txt`:
  `6f912999…368bfe22` (cli) and `721b32dc…5f12ca09` (daemon).
- Running `/tmp/fworker-wq4859/bin/sysknife-v0.30.0-linux-x86_64 doctor` got
  `This command requires approval`. Arbitrary binaries are not on `harness/worker-settings.json`,
  and I did not work around the gate. Logged as ISSUE-LOG row 1655.
- No daemon runs on Lumen (`/run/sysknife/daemon.sock` is absent, checked 2026-11-05). Read from
  source, doctor (`apps/sysknife-cli/src/runner.rs`, around line 1035) connects to the daemon
  socket, fetches host state, and reports `socket`, `host`, LLM `provider`/`model` and `distro`.
  On Lumen today it would report the daemon as unreachable, because nothing is installed.
- A daemon that can change anything has to be the **system** service, installed with
  `sudo make install`. That creates a `sysknife` system user, sudoers grants, polkit rules and
  `/usr/lib/sysknife/action-steps` (`README.md:147-156, 185-206`). The user-mode service can only
  run read-only actions. That is a root-level change to Lumen, which is out of scope for a
  research worker and not justified by this verdict.

**To finish Q1 by hand** (an operator on Lumen, about 1 minute, read-only):
`ssh lumen@<lumen-ip> '/tmp/fworker-wq4859/bin/sysknife-v0.30.0-linux-x86_64 doctor --json'`.
Expect a daemon-unreachable result until a daemon is installed.

## Q2. Replace or augment lumen-change-safety?

**Augment at most. Don't replace.**

I couldn't find a `lumen-change-safety` SKILL.md in the dirs this worker can read. It is
referenced in `kb/ISSUE-LOG.md`, and the ticket describes it as the
verify-first / back-up-first / no-reboot-without-permission discipline. Against that discipline:

| Standing order | SysKnife enforces it? |
|---|---|
| Verify before change | Partly. The daemon builds an immutable preview with a content hash and rejects stale approvals. Some actions re-check live state at execute time (autoremove set `dispatcher.rs:1168-1192`, deployment identity `:1095-1122`). It does not run fleet-specific pre-checks. |
| Back up before config change | No general mechanism. Only rpm-ostree has rollback. File actions are scoped writes with no backup and restore. |
| No restarting Thor / protected units | No. `[policy.risk_overrides]` can only *raise* an action's risk tier (`docs/configuration.md:69-75`). There is no per-parameter denylist like "never `RestartService cloudflared` on lumen", which is what our `fleet-exec-policy.yaml` `never.restart` rows express. `RebootSystem` is a normal High action. |
| Vault / cloudflared / Commander only via detached systemd-run (instinct 6) | Not modelled. |

The skill covers judgement that a closed catalogue can't hold. SysKnife covers the *mechanical*
half (no shell string, signed record) for the operations in its catalogue.

## Q3. Catalogue mapping to fleet operations

Families and risk tiers parsed from `docs/action-reference.md` (L/M/H):

| Fleet operation | SysKnife action | Risk | Fit |
|---|---|---|---|
| `apt update` / `apt upgrade` | `AptUpdate` / `AptUpgrade` (runs `dist-upgrade -y`) | L / H | Good. The argv is built by an allowlist helper with `--` before package names and `APT::Cmd::Pattern-Only=1` (`docs/ubuntu-action-reference.md:28-80`). No rollback on apt. |
| install / remove / hold package | `AptInstall`, `AptRemove`, `AptPurge`, `AptHold`, `AptAutoremove` | M | Good |
| `systemctl restart/start/stop/enable` | `RestartService`, `StartService`, `StopService`, `SetServiceEnabled`, `ReloadDaemon` | M | Good, but no protected-unit list (Q2) |
| `systemctl mask` | `MaskService` | H | Good |
| journal reads | `GetServiceLogs`, `GetJournalLog`, `CollectDiagnostics` | L | Good |
| reboot a node | `RebootSystem` | H | Exists. Thor protection would have to live outside SysKnife. |
| `docker compose up/pull/restart` | **none** | – | **Gap.** Containers are Podman-only (`CreateContainer`/`StartContainer`/… run `action-steps podman`) |
| firewall | `UfwAllow`/`UfwDeny`/`UfwDeleteRule`/`UfwEnable` | H | Good |
| SSH keys, sudoers.d, users | `AddAuthorizedKey`, `GrantSudoAccess`, `CreateUser`, … | H | Good |
| sysctl, mounts, swap, LVM | `SetSysctl`, `AddMount`, `ExtendLogicalVolume`, … | H | Good |
| timers / scheduled jobs | `ListTimers`, `CreateScheduledJob` | L / H | Partial |
| cloudflared ingress edit, Tailscale, git deploys, Vault | **none** | – | **Gap.** These are our highest-risk ops (DESIGN.md §3.3 recipe `cloudflared.ingress.upsert`) |
| certs | `ObtainCertificate`, `RenewCertificates` | H / M | Good (certbot) |

Summary: it covers the Ubuntu base-OS layer well. It covers none of the fleet-specific layer
(Docker Compose stacks, cloudflared, Tailscale, Vault), and that layer is where incidents happen.
Adding these means upstream Rust `ActionSpec`s plus sudoers grants (`docs/typed-actions.md:140-143`).

## Q4. Can the approval gate integrate with `wq grant`?

**Not without changing SysKnife's identity model.**

How SysKnife approves, from source:
- The wire `Approve` message is `{request_id, transaction_id}` and nothing else
  (`dispatcher.rs:267-270`).
- The daemon authorises it by kernel-attested uid (`SO_PEERCRED`) and role group, and **requires
  the approver to be the account that previewed** (`caller_owns_transaction`, `dispatcher.rs:835-878`,
  with the rationale at `:819-823`).
- The "human" part is client-side only. `sysknife approve` prompts when stdin is a TTY and asks
  for the exact action name on High (`apps/sysknife-cli/src/runner.rs:599-624`). The daemon
  never sees that prompt.
- Receipts are single-use, bound to one immutable preview, and expire after 15 minutes
  (`docs/mcp.md:209-211`).

Consequences for the fleet:
- **The agent can approve itself.** An fworker runs as uid `lumen` with Bash and Python. If
  `lumen` is in the sysknife groups, the worker can write an `Approve` frame to the socket
  (length-prefixed JSON, `docs/architecture.md:167-169`) and get a valid receipt. SysKnife's
  own threat model assumes the model sits only behind MCP, with no shell. Our workers have a shell.
- **`wq grant approve` cannot mint a receipt.** It runs as a different identity, the orchestrator
  or Frank, which is the point of F8. The daemon refuses that account as "created by a different
  account" (`dispatcher.rs:899-904`).
- A workable bridge needs a **broker uid**: workers are kept out of the `sysknife` socket group
  and submit to a small root- or broker-owned service. The broker calls `wq grant consume
  --fingerprint` (atomic one-shot, `bin/wq:3733-3742`), then previews and approves as itself.
  That is ops-apply's gate (DESIGN.md §3.2) with SysKnife as the node executor. The audit row
  would name the broker uid, not Frank, so the wq `decided_by` must be carried alongside.

## Q5. MCP: can Claude Code / Commander use `sysknife_plan` + `sysknife_execute` instead of Bash?

**Technically yes. It only adds safety if Bash goes away for that lane.**

- The MCP tools are `sysknife_plan`, `sysknife_execute`, `sysknife_history`, `sysknife_doctor`,
  `sysknife_audit_verify` and the read-only `sysknife_get_*` queries (`README.md:84-91`).
  `npx sysknife-setup --claude` writes `.mcp.json` plus a `.claude/hookify.*.local.md`
  (`README.md:169-173`).
- `sysknife_plan` runs its **own** LLM planning loop inside the brain, which needs a provider key
  in `~/.config/sysknife` or env. That means Claude asks a second LLM to pick typed actions. For
  fleet use we'd want to drive the typed actions directly (the CLI's `--dry-run --json` path, or
  daemon preview frames) and skip the second model.
- The safety argument ("10 of 11 agents bypass raw-string shell guards", GuardFall, cited at
  `docs/typed-actions.md:20-24`, UNVERIFIED: I didn't fetch the GuardFall paper) holds only
  if the agent *has no other channel*. An fworker with Bash plus a SysKnife MCP is no safer than
  today, because of Q4's self-approval. A dedicated ops lane with Bash removed from its toolset
  and SysKnife (or ops-apply) MCP as its only mutation path is the configuration that delivers
  the guarantee.
- Commander runs on HORUS (instinct 1). Each target node needs its own daemon, reached by an
  SSH-forwarded socket. The role comes from the *remote* SSH user's groups
  (`docs/vm-daemon-setup.md`, "Who you are over a tunnel").

## Q6. Audit trail vs `lumen_episode.py`

**Complement, don't replace.**

- `lumen_episode.py` (`minerva/lumen_episode.py`) is semantic memory: `commit` (summary,
  changed/worked/failed/why), `recall`, `precheck`. It's built for learning and retrieval, not
  for tamper-evidence.
- SysKnife's chain is a forward hash chain. Each row's `chain_hash` *is* an Ed25519 signature over
  a tagged encoding of `seq, key_id, transaction_id, request_hash, action_name, risk_level,
  approval_id, caller_role, caller_principal, event_tip, …` (`docs/the-audit-chain.md:11-40`).
  A parallel approval-event chain v2 names the approver, consumer and revoker (`:255-275`).
  Verification needs only the public key (`:199-220`). Known blind spot: tail truncation needs
  external anchoring (`:290-297`).
- Integration path: point `[storage] backend = "postgres"` at the fleet PostgreSQL, or poll
  `sysknife audit export`, and write one `lumen_episode commit` per terminal transaction (summary =
  action + risk + outcome, ref = wq ticket). Periodically anchor the chain tip into an append-only
  location (a git commit in the ops repo) to close the truncation gap.

## What to borrow into ops-apply (DESIGN.md §3) now

1. **Signed hash chain as the ops-apply log format.** Ed25519 over a prefix-free tagged encoding,
   a `chain_version` per row, a separate approval-event chain naming the principal, and
   tip anchoring. Our §3 design currently signs the manifest digest only.
2. **`SO_PEERCRED` principal on the gate socket**, recorded in the signed row. Don't trust a
   body field (`dispatcher.rs:809-878`).
3. **Execute-time revalidation of approved state** (the autoremove-set and deployment-identity
   checks). This generalises our §3.4 TOCTOU sha check to any action whose effect is resolved late.
4. **The apt argv helper pattern** (`packaging/sysknife-action-steps`). It shows why a sudoers grant
   with a trailing `*` is unsafe: `-o APT::Update::Pre-Invoke::=<cmd>` runs as root
   (`docs/ubuntu-action-reference.md:28-37`). This feeds directly into `never.sudo_argv_deny` (V9).

## Re-evaluate when

- a Docker/compose action family ships, and
- the daemon supports a cross-account approver (an approver role distinct from the previewer), or
  a receipt minted by an external signer, which is what `wq grant` needs, and
- apt-side rollback or snapshot (e.g. LVM/btrfs pre-snapshot) ships.

## Evidence index

- Binaries + checksum match: `/tmp/fworker-wq4859/bin/` (ephemeral; hashes recorded above).
- Source checkout: `/tmp/fworker-wq4859/src/sysknife` @ `2b3da54` (ephemeral; rev-pinned above).
- Doctor refusal: worker allowlist, exact command quoted in Q1.
