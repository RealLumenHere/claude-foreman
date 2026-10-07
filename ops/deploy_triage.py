#!/usr/bin/env python3
"""ops bundle deploy-vs-retire register (wq#2985, follow-up to wq#1732).

wq#1732 audited all 92 foreman/ops bundles carrying an install.sh and left 36
PENDING with no owning ticket. This module holds the disposition for each of
those 36 and, more importantly, the *live signals* each disposition rests on,
so the call can be re-checked instead of re-argued.

Why this is not just a markdown table: wq#1732's verdicts came from a content
hash index over 13 fixed local roots (see ops-deploy-drift ROOTS). That index
reports ABSENT for three kinds of bundle that are in fact deployed --

  * an install root the index does not walk (/opt, /home/sia, /usr/local/lib),
  * a target on another fleet node (Anubis, Bes, Sia, the MacPro6,1 group),
  * a payload live under a renamed unit (wq858 ships trivy-scan.timer; the
    live unit is lumen-trivy-weekly.timer).

Deploying on an ABSENT verdict alone would therefore have re-installed running
services. Every disposition below that claims a bundle is live cites a unit
this module re-reads from systemd at run time (`--check`), so the claim rots
loudly rather than silently.

Usage:
    python3 ops/deploy_triage.py            # print the register
    python3 ops/deploy_triage.py --check    # re-derive live signals, exit 1 on drift
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

# Roots ops-deploy-drift indexes. A bundle installing outside these cannot be
# judged by the content index at all -- see SCOPE below.
INDEXED_ROOTS = (
    "/home/lumen/bin", "/home/lumen/minerva", "/home/lumen/agents", "/home/lumen/foreman",
    "/home/lumen/autonomy", "/etc/systemd/system", "/home/lumen/.config/systemd/user",
    "/etc/lumen", "/home/lumen/agent", "/usr/local/bin", "/home/lumen/services",
    "/home/lumen/maint", "/home/lumen/fleet-units",
)

# Dispositions. Every bundle in the wq#1732 untracked set carries exactly one.
KEEP_LIVE = "LIVE"              # deployed; --verify handler is the standing proof
KEEP_DRIFT = "LIVE_DRIFT"       # deployed, payload has moved on; reconcile, do not install
KEEP_DEPLOY = "DEPLOY"          # genuinely not installed; deploy ticket filed
KEEP_ASSERT = "ASSERTION"       # no install step by design; --verify asserts live state
OWNED = "OWNED"                 # an open ticket already owns it
SUPERSEDED = "SUPERSEDED"       # a later bundle or a renamed live unit replaced it
RETIRED = "RETIRED"             # keep nothing; reason recorded

# bundle -> (disposition, live units that must hold, follow-up, reason)
#
# `units` is the falsifiable half: for a LIVE/LIVE_DRIFT/SUPERSEDED call it is
# the unit whose presence in `systemctl list-unit-files` is the evidence. For a
# DEPLOY call it is the unit that must be ABSENT -- if one of these ever shows
# up, the bundle deployed behind our back and the register is wrong.
REGISTER: dict[str, dict] = {
    "wq1184-gatus-health-checks": dict(
        disposition=KEEP_DRIFT, units=["lumen-gatus.service", "lumen-gatus-ntfy-relay.service"],
        follow_up="wq#3005",
        reason="6/9 payload files byte-identical live; the bundle's gatus.service and "
               "gatus-ntfy-relay.service were installed under lumen- prefixed names, which "
               "is why the name index missed them."),
    "wq1396-minerva-proposal-promoter": dict(
        disposition=SUPERSEDED, units=["minerva-promote.timer"],
        absent_units=["minerva-proposal-promoter.timer"],
        superseded_by="ops/wq1500-minerva-promoter (live minerva-promote.timer)",
        follow_up=None,
        reason="the bundle supersedes itself: install.sh:7-11 refuses to run without "
               "WQ1396_FORCE_LEGACY=1 because wq#1500 replaced it, and running both "
               "double-files every CVE proposal. minerva-promote.timer is enabled+active; "
               "the wq1396 timer is correctly absent. Deploying on the ABSENT verdict would "
               "have duplicated every security ticket."),
    "wq2904-judge-model-group-fix": dict(
        disposition=KEEP_DEPLOY, follow_up="wq#3006",
        reason="hand-merge into the live gateway config; --verify reports the "
               "wq2821-judge-llama8b model_name marker missing. Needs a container-capable "
               "lane, not a worker."),
    "wq1609-minerva-contradiction-scan": dict(
        disposition=KEEP_DRIFT, units=["lumen-contradiction-scan.timer"],
        follow_up="wq#3005",
        reason="timer enabled+active; --verify reports bin/lumen-contradiction-scan differs "
               "from the bundle. Deployed then edited in place."),
    "wq1298-gitleaks-detect": dict(
        disposition=KEEP_LIVE, units=["gitleaks-detect.timer"],
        follow_up=None,
        reason="installed 2026-10-07 (wq#3486), VERIFY OK. The first attempt died at exit 141: "
               "`git ls-remote | head -1` under pipefail SIGPIPEs git, so install.sh could "
               "never have completed; fixed in the same PR."),
    "wq2086-presentation-stability": dict(
        disposition=KEEP_LIVE, units=["lumen-presentation-stability.timer"], follow_up=None,
        reason="minerva_presentation_stability.py is live and byte-identical, and the timer "
               "the wq#2985 triage found absent is now installed and enabled (wq#3468 re-check, "
               "2026-10-07)."),
    "wq2851-aer-storm-alarm": dict(
        disposition=KEEP_DEPLOY, scope="remote", hosts=["isis", "hathor", "khepri", "nephthys"],
        follow_up="wq#3003",
        reason="targets the MacPro6,1 group, not Lumen; nothing installed anywhere. The "
               "local index could not have judged this either way."),
    "wq2806-config-shape-mask": dict(
        disposition=KEEP_DRIFT, units=["lumen-mask-at-rest.timer"],
        follow_up="wq#3005",
        reason="bin/mask-secrets is live but differs from the bundle and the supergateway "
               "log pipe is not running the on-disk copy. A redeploy, not an install."),
    "wq2500-rsshub-bridge": dict(
        disposition=RETIRED, scope="remote", hosts=["sia"],
        follow_up="wq#3576",
        reason="RETIRE (wq#3486). rsshub.service starts node lib/index.js, which upstream "
               "no longer ships (wq#2505), and install.sh never builds dist/. Nothing ever "
               "landed on Sia: unit not-found, /home/sia/services/rsshub absent (checked "
               "2026-10-07). The velocity-feed need moves to a fresh bundle, wq#3576."),
    "wq2739-tripwire-corpus": dict(
        disposition=OWNED, owner="wq#2981", follow_up=None,
        reason="assertion bundle: --verify runs a tripwire scan over the repo, so its MISS "
               "lines are live findings in files written after the bundle "
               "(reports/wq2970_*.md, research/BROKER_GRANT_COVERAGE.md), not undeployed "
               "payload. wq#2981 already owns the findings."),
    "wq1617-litellm-fragment-merge": dict(
        disposition=KEEP_ASSERT, follow_up=None,
        reason="merge_litellm_fragment.py is a one-shot merge helper, not a deployed file; "
               "there is nothing for it to be absent from. Ticket notes confirm the merge "
               "landed."),
    "wq2472-vlogs-fleet-rollout": dict(
        disposition=KEEP_LIVE, units=["vlogs-forwarder.timer"], scope="remote",
        hosts=["anubis", "hathor", "isis", "maat", "sobek", "thoth", "khepri"],
        follow_up=None,
        reason="forward-journal.py installs to /opt/vlogs-forwarder on OTHER nodes -- a "
               "root the index does not walk, on hosts it cannot reach -- so it read "
               "ABSENT. The handler added here checks all 7 rolled-out nodes over ssh and "
               "returns VERIFY OK: byte-identical payload and an active timer on every one."),
    "wq2676-vault-ui-drift-ha-context": dict(
        disposition=KEEP_ASSERT, follow_up="wq#3006",
        reason="ships no payload; --verify asserts an HA_HOSTS marker in the minerva context "
               "files. The marker is genuinely missing, which is a context-drift finding, "
               "not an undeployed bundle."),
    "wq2608-dc-endpoint-bes-a1": dict(
        disposition=KEEP_DEPLOY, scope="remote", hosts=["bes"],
        follow_up="wq#3003",
        reason="--verify queries Bes directly and reports dc-gateway.service inactive, "
               "not-enabled, sha absent, and no mcp-bes ingress rule in cloudflared."),
    "wq2607-dc-endpoint-anubis": dict(
        disposition=KEEP_DEPLOY, scope="remote", hosts=["anubis"],
        follow_up="wq#3003",
        reason="6 of 8 payload files absent. wq2608 ships a same-named dc-gateway.service, "
               "but that is the Bes endpoint; the Anubis tunnel half is distinct and "
               "unshipped, so this is not superseded."),
    "wq2286-gitea-secret-scan": dict(
        disposition=KEEP_LIVE, scope="remote", hosts=["anubis"], follow_up=None,
        reason="installs to /usr/local/lib/lumen-secret-scan and the Gitea hook dir on "
               "Anubis, which the local index cannot see. The handler added here asks "
               "Anubis directly and returns VERIFY OK: scan.py byte-identical, allow list "
               "present, zero repos without the pre-receive shim."),
    "wq1613-minerva-verifier-registry": dict(
        disposition=KEEP_DRIFT, units=["lumen-state-verify.timer"], follow_up="wq#3005",
        reason="bin/lumen-verify and minerva/verify/registry.yaml are both live but differ "
               "from the bundle; three lumen-verify.bak.20260924_* files on the box date the "
               "hand edits."),
    "wq2460-minerva-context-authority": dict(
        disposition=KEEP_LIVE, follow_up=None,
        reason="in-place library edit, and it is live: the handler added here reports "
               "VERIFY OK on the wq#2460 marker and the AUTHORITATIVE/STALE/UNVERIFIED "
               "vocabulary in /home/lumen/minerva/minerva_verify.py. The byte difference "
               "the audit saw is later tickets editing the same file, which is the design."),
    "wq1528-minerva-factpack-budget": dict(
        disposition=KEEP_DRIFT, follow_up="wq#3005",
        reason="zz-wq1528-judge-window.conf is live byte-identical; minerva_factpack.py and "
               "minerva_verify.py are live and evolved onward under wq2430/wq2460. Nothing "
               "absent."),
    "wq1294-session-handoff": dict(
        disposition=KEEP_DRIFT, units=["session-handoff.timer"], follow_up="wq#3005",
        reason="timer enabled+active and byte-identical; the handler added here reports "
               "/etc/systemd/system/session-handoff.service differs from the bundle. "
               "Deployed then hand-edited -- reconcile, do not reinstall."),
    "wq1632-lumen-state-lifecycle": dict(
        disposition=KEEP_DEPLOY, follow_up="wq#3004",
        reason="state_lifecycle.py and registry.seed.yaml both absent from every indexed "
               "root and no lifecycle entry point exists in bin/."),
    "wq1640-lumen-state-render-frank-side-hardware-q-a3": dict(
        disposition=KEEP_DRIFT, units=["prefs-block-check.timer"], follow_up="wq#3005",
        reason="both units byte-identical and the timer enabled+active, but the handler "
               "added here reports /home/lumen/minerva/prefs_block.py differs. The "
               "library half was applied live from the BUILD session and has moved since; "
               "reconcile, do not reinstall."),
    "wq2019-proposal-reviewer-fix": dict(
        disposition=KEEP_DRIFT, follow_up="wq#3005",
        reason="half-live, and the missing half is a regression. minerva_dedup.py carries "
               "the route_conflict intent-verb fix, but the live "
               "/home/lumen/minerva/minerva_proposal.py contains no 'MacPro6,1' while the "
               "bundle's copy contains it twice: the stale hardware-INVARIANTS fix is not "
               "in the file a later in-place edit left behind."),
    "wq2027-intel-proposal-source-grounding": dict(
        disposition=KEEP_DEPLOY, follow_up="wq#3004",
        reason="fetch_source.py absent from every indexed root; the eligibility gate it "
               "backs cannot be running."),
    "wq1614-search-v3-contradiction-penalty": dict(
        disposition=KEEP_LIVE, follow_up=None,
        reason="in-place library edit, live since 2026-09-28 (wq#3017). The blanket "
               "penalty was gate-rejected under wq#3004; the query-scoped rework passed "
               "the golden gate unmodified (gate_20260928_140308.json, INERT: no metric "
               "moved) and was installed by install.sh. --verify checks the _asks_about "
               "scope markers, so the rejected blanket version fails it."),
    "wq1704-missing-watchdogs": dict(
        disposition=KEEP_LIVE,
        units=["lumen-spof-daemons.timer", "lumen-spof-restarts.timer",
               "lumen-spof-roundtrip.timer", "lumen-spof-timers.timer"],
        follow_up=None,
        reason="all four watchdog timers enabled+active and 9/10 payload files "
               "byte-identical; the tenth is smoke.py, a test. The DRIFTED verdict was noise."),
    "wq1633-minerva-projection-dirty-flag": dict(
        disposition=KEEP_LIVE, units=["lumen-federate-reindex.path"], follow_up=None,
        reason="projection_state.py is live byte-identical, and lumen-federate-reindex.path, "
               "absent at the wq#2985 triage, is now installed and enabled (wq#3468 re-check, "
               "2026-10-07)."),
    "wq1705-guard-meta-watchdog": dict(
        disposition=KEEP_LIVE, units=["lumen-guard-meta-watchdog.timer"],
        follow_up=None,
        reason="installed 2026-10-07 (wq#3486), VERIFY OK. wq3354's sh_guards.py was written "
               "assuming this owns the guard-service restart, so it was a missing prerequisite, "
               "not a stale bundle. Pre-install --dry-run classified all 4 failed units "
               "not-a-guard: nothing to restart."),
    "wq1652-cfd-actuator": dict(
        disposition=KEEP_LIVE, units=["lumen-cfd-actuator.timer"],
        follow_up="wq#3575",
        reason="stage 0 installed 2026-10-07 (wq#3575), --verify VERIFY OK. Before install, "
               "SIGHUP was found to exit cloudflared 2026.7.3 (Restart=on-failure treats "
               "that as clean), so the HUP reload path and HUP smoke were dropped first. "
               "Callers: stage 1 wadjet-ha-monitor/failback live (lumen-bin #238), "
               "stage 2 healer via ops/wq3575-healer-cfd-actuator; stages 3-4 "
               "(public guardian, sudoers cleanup) still direct."),
    "wq1554-langfuse-lag-monitor": dict(
        disposition=OWNED, owner="wq#1555", follow_up=None,
        reason="wq#1555 (blocked) already owns the deploy: it needs /home/lumen/langfuse in "
               "a worker's --add-dir plus the v4.32.0 endpoint confirmation. Filing a second "
               "deploy ticket would duplicate it."),
    "wq1373-cf-token-liveness": dict(
        disposition=KEEP_LIVE, units=["cf-token-liveness.timer"],
        follow_up=None,
        reason="installed 2026-10-07 (wq#3486), VERIFY OK; the install's own dry-run "
               "selftest reported 2/2 mailbot D1 tokens live before the timer was enabled."),
    "wq1406-paid-fallback-guard": dict(
        disposition=RETIRED, units=["lumen-budget-alert.timer", "lumen-deepseek-spend.timer"],
        absent_units=["paid-spend-tripwire.timer"],
        follow_up=None,
        reason="RETIRE (wq#3486). paid_spend_tripwire.py:129 reads the master key "
               "positionally, the wq#2902 leak class, and the drain is already paged by "
               "lumen-budget-alert (per-key 80%) and spend-alert.py on lumen-deepseek-spend "
               "(thresholds + 2x-7d burn). The callback half needed a gateway merge and "
               "proxy restart the bundle never staged."),
    "wq1299-phoenix-tracer": dict(
        disposition=RETIRED, follow_up=None,
        reason="RETIRE. The bundle exports traces via a LiteLLM OTLP callback; the live "
               "gateway config has zero otel/otlp/phoenix references and 9 langfuse ones, "
               "and langfuse-evaluator/-smoke/-idle-alert timers are enabled+active. A "
               "second tracer on the gateway duplicates a path that has been live for the "
               "618h this sat unshipped."),
    "wq858-trivy-weekly-scan": dict(
        disposition=SUPERSEDED, units=["lumen-trivy-weekly.timer"],
        superseded_by="live lumen-trivy-weekly.timer", follow_up=None,
        reason="the scan is live under a renamed unit -- lumen-trivy-weekly.timer is "
               "enabled+active, v0.74.0 per the ticket's own notes -- while the bundle ships "
               "trivy-scan.timer. The bundle's units are dead names, not pending work."),
    "wq901-ntfy-oob-429": dict(
        disposition=KEEP_LIVE, units=["ntfy-oob-canary.timer"],
        follow_up=None,
        reason="installed 2026-10-07 (wq#3486), VERIFY OK: ntfy-send.conf now reads "
               "NTFY_OOB_PRIORITIES=\"urgent\" and the install's in-line canary run got "
               "oob=200 (the unit greps for it, and install.sh is set -e)."),
    "wq394-netdata-canary": dict(
        disposition=KEEP_DRIFT, units=["wq394-netdata-sampler.timer", "netdata.service"],
        follow_up="wq#3005",
        reason="both units byte-identical and enabled+active -- the 867h age is how long "
               "this has been running, not how long it has been pending -- but the handler "
               "added here reports sampler.py and analyze.py differ under "
               "/usr/local/lib/wq394-netdata-canary. Hand-patched since install."),
}

# Bundles whose disposition means the ops/ directory should carry a marker
# instead of an install path.
NEEDS_MARKER = {RETIRED, SUPERSEDED}


def live_unit_files() -> dict[str, str]:
    """unit -> enablement state, from systemd. Read-only."""
    try:
        p = subprocess.run(["systemctl", "list-unit-files", "--no-pager", "--no-legend"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return {}
    out = {}
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            out[parts[0]] = parts[1]
    return out


# Destinations of an install verb, i.e. where the payload actually lands. A
# build dir or a state file mentioned elsewhere in install.sh is not an install
# target and must not decide scope -- that over-reach is what made wq1298 and
# wq1652 read as non-local on the first pass.
_DEST_RE = re.compile(
    r"^\s*(?:sudo\s+)?(?:install|cp|ln|rsync)\b[^\n]*?"
    r"(/(?:home|etc|usr|opt|srv|var)/[A-Za-z0-9_./-]+)\s*$", re.M)
_UNIT_RE = re.compile(r"(/etc/systemd/system/[A-Za-z0-9_@.-]+\.(?:service|timer|path))")


def declared_roots(bundle_dir: str) -> list[str]:
    """Absolute paths the bundle's install.sh installs payload to."""
    try:
        src = open(os.path.join(bundle_dir, "install.sh"), encoding="utf-8",
                   errors="replace").read()
    except OSError:
        return []
    return sorted(set(_DEST_RE.findall(src)) | set(_UNIT_RE.findall(src)))


def scope_of(bundle: str, bundle_dir: str) -> str:
    """local | remote | unindexed-root. Decided before disposition, never after.

    A remote or unindexed-root bundle can never be classified DEPLOY on content
    index evidence alone, because the index never looked where it installs.
    """
    rec = REGISTER.get(bundle, {})
    if rec.get("scope"):
        return rec["scope"]
    try:
        shipped = set(os.listdir(bundle_dir))
    except OSError:
        shipped = set()
    for r in declared_roots(bundle_dir):
        if r.startswith(INDEXED_ROOTS):
            continue
        # A destination outside the index only blinds the audit if a payload
        # file lands there. A state or log dir the installer merely creates
        # holds nothing the content index was ever going to find.
        if os.path.basename(r) in shipped:
            return "unindexed-root"
    return "local"


def check(ops_dir: str) -> list[str]:
    """Re-derive every falsifiable signal in the register. Returns drift lines."""
    units = live_unit_files()
    problems = []
    for bundle, rec in sorted(REGISTER.items()):
        bd = os.path.join(ops_dir, bundle)
        if not os.path.isdir(bd) and rec["disposition"] not in NEEDS_MARKER:
            problems.append(f"{bundle}: bundle dir missing but disposition is "
                            f"{rec['disposition']}")
            continue
        for u in rec.get("units", []):
            if u not in units:
                problems.append(f"{bundle}: {rec['disposition']} rests on {u}, which is no "
                                f"longer a known unit")
        for u in rec.get("absent_units", []):
            if u in units:
                problems.append(f"{bundle}: classified {rec['disposition']} but {u} is now installed "
                                f"({units[u]}) -- it deployed outside this register")
        if rec["disposition"] == KEEP_DEPLOY and scope_of(bundle, bd) != "local" \
                and not rec.get("hosts"):
            problems.append(f"{bundle}: DEPLOY on a non-local scope with no hosts recorded")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="re-derive live signals; exit 1 if the register has drifted")
    ap.add_argument("--markdown", action="store_true",
                    help="emit DEPLOY_TRIAGE.md's table (generated, never hand-edited)")
    ap.add_argument("--ops-dir", default=os.path.dirname(os.path.abspath(__file__)))
    args = ap.parse_args(argv)

    if args.markdown:
        print("| bundle | disposition | resolution | evidence |")
        print("| --- | --- | --- | --- |")
        order = {KEEP_DEPLOY: 0, KEEP_DRIFT: 1, OWNED: 2, KEEP_LIVE: 3, KEEP_ASSERT: 4,
                 SUPERSEDED: 5, RETIRED: 6}
        for bundle, rec in sorted(REGISTER.items(),
                                  key=lambda kv: (order[kv[1]["disposition"]], kv[0])):
            res = (rec.get("owner") or rec.get("superseded_by")
                   or rec.get("follow_up") or "no action")
            print(f"| `{bundle}` | {rec['disposition']} | {res} | "
                  f"{rec['reason'].replace('|', '/')} |")
        return 0

    if args.check:
        problems = check(args.ops_dir)
        for p in problems:
            print(f"DRIFT {p}")
        print(f"{'DRIFT' if problems else 'OK'} {len(REGISTER)} bundles, "
              f"{len(problems)} problem(s)")
        return 1 if problems else 0

    counts: dict[str, int] = {}
    for bundle, rec in sorted(REGISTER.items()):
        d = rec["disposition"]
        counts[d] = counts.get(d, 0) + 1
        owner = rec.get("owner") or rec.get("superseded_by") or rec.get("follow_up") or "-"
        print(f"{d:<11} {bundle:<52} {owner}")
    print("  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
