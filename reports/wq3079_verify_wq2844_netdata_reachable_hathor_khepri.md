# wq#3079 verify-pass: wq#2844 (Mac netdata :19999 unreachable on hathor/khepri)

**Verdict: CONFIRM.** Independent, adversarial re-check of the diff shipped by wq#2844
(`main@ba599b2f`, "feat(ops): add production netdata bundle for the Mac Pros"). Read the
diff cold, tried to break it, ran the ticket's own acceptance probe fresh from this
session — it holds. This verify-pass is a separate check from the note already on
wq#2844's own ticket (`reports/wq2844_verify_netdata_reachable.md`, produced by the fix's
own second attempt); the numbers below were captured independently, not copied from it.

## What the diff does

`ops/wq2844-netdata-macs/` (install.sh, netdata.conf.template, netdata.sources, pinned
keyring, RUNBOOK.md, a 7-case test file) installs the official Netdata 2.11.x apt package
on hathor/nephthys/khepri, binds it to the node's own tailnet IP only
(`bind to = TAILSCALE_IP:19999`), and gives `install.sh --verify` a read-only remote check.
Root cause: netdata was simply absent on those three Macs, not a bind/firewall problem on
an existing install.

## Adversarial checks run this session (2026-09-28)

1. **Read cold**: `git show ba599b2f` for all three shipped files, `install.sh` end to
   end, `netdata.sources`, `RUNBOOK.md`. Logic is idempotent (skips apt install if
   `dpkg -s netdata` succeeds, only rewrites `netdata.conf` on content diff, refuses on any
   apt-simulated Remove/Upgrade), refuses non-tailnet IPs, refuses to run if
   `needrestart` isn't list-only. No obvious break.
2. **Live curl, no ssh, no proxy** (the ticket's actual reachability claim):
   - `curl -m5 http://100.80.71.13:19999/api/v1/info` → **200**, `mirrored_hosts:
     ["hathor"]` (hathor, tailnet IP from this session, not copied from the prior report)
   - `curl -m5 http://100.120.16.78:19999/api/v1/info` → **200**, `mirrored_hosts:
     ["khepri"]`
   - `mirrored_hosts` on each response names only that node — rules out hitting a mirror
     or relay instead of the node itself.
3. **The ticket's actual acceptance probe**, run fresh:
   ```
   isis      raise=False clear=True te0t=72.4 fan=1100   action=hold
   hathor    raise=False clear=True te0t=76.9 fan=1100   action=hold
   nephthys  raise=False clear=True te0t=34.5 fan=1100   action=hold
   khepri    raise=False clear=True te0t=73.9 fan=1099.5 action=hold
   thor      raise=None  clear=None te0t=None fan=None   action=notify no netdata source
   ```
   hathor and khepri both return a real verdict, not null — matches the ticket's bar.
   Read `ops/fan_floor_alarm.py` end to end (`check()`, `window_stats()`,
   `next_state()`): a missing/partial window returns `None`/carries state, it does not
   fabricate a verdict, so this result is not a false positive from lenient logic.
4. **Bind-address claim, not just reachability** (the part a reachability-only probe
   would miss): `fleet-exec run hathor ss -ltnH` shows netdata listening on
   `100.80.71.13:19999` only — no `0.0.0.0:19999` fallback, confirming the tailnet-only
   bind the template claims. Could not run the same `ss` check on khepri:
   `fleet-exec run khepri ...` → `DENY [grant:no_ssh_run]: no ssh grant for khepri with
   verb run` — `harness/fleet-exec-policy.yaml:174`'s `ssh:` allowlist still only lists
   `[lumen, isis, bes, anubis, maat, sobek, hathor]`, a pre-existing gap logged
   independently at kb ISSUE-LOG #670 (2026-09-24), not something wq#2844 broke or was
   asked to fix. khepri's bind-only claim therefore rests on the HTTP-level checks above
   (§2-3) plus install.sh applying byte-identical logic to both nodes, not on a live `ss`
   read — flagged as a residual verification gap, not a contradiction.
5. **Pinned keyring hash**: `KEY_SHA256` in `install.sh:24` matches
   `sha256sum ops/wq2844-netdata-macs/netdata-archive-keyring.gpg` exactly (64 hex chars,
   verified programmatically, not by eye).
6. **Test suite**: `python3 -m pytest ops/tests/test_wq2844_netdata_macs.py -q` → 7
   passed (syntax, pinned-keyring, verify-marker-precedes-install ordering, tailnet-only
   template assertions, non-tailnet-IP refusal, and two `--verify` stub cases proving a
   wildcard-bind listener is flagged as a MISS).

## One cosmetic finding, does not block confirm

`ops/wq2844-netdata-macs/netdata.conf.template:1-2` kept the header comment verbatim from
`ops/wq394-netdata-canary/netdata.conf.template` — it still reads "wq#394 canary" and
points at `reports/wq394_netdata_canary.md` for rationale, which is the *other* bundle's
report, not this one. Copy-paste artifact from reusing the canary's known-good
tailnet-bind shape; does not affect `bind to`/`allow ... from`, which is what
`test_template_is_tailnet_only_and_agent_only` and the live checks above actually cover.
Worth a follow-up one-line doc fix, not a functional defect.

## Bottom line

No contradiction found. hathor and khepri netdata is reachable from Lumen over the
tailnet, bound tailnet-only (confirmed live for hathor, inferred for khepri from
identical install logic + passing HTTP-level checks), and
`ops/fan_floor_alarm.py check` returns real, non-null verdicts for both — reproduced
independently this session, not taken on the prior report's word. wq#2844 stays **done**.
