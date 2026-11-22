#!/usr/bin/env bash
# Install minerva-proposal-promoter (wq#1396): script -> /home/lumen/bin, units -> systemd.
# Idempotent: re-running converges to the same state.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
# This bundle is SUPERSEDED (see the refusal below), so "correct" is the
# opposite of the usual shape: the wq1500 promoter must be live and this one's
# units must be absent. wq#1732's content index saw only the absence and filed
# the bundle as a pending deploy; installing on that verdict would have
# double-filed every CVE proposal, which is precisely what the refusal prevents.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  # The successor's units must be installed as wq#1500 ships them...
  vunit_file "$HERE/../wq1500-minerva-promoter/minerva-promote.service" \
             /etc/systemd/system/minerva-promote.service
  vunit_file "$HERE/../wq1500-minerva-promoter/minerva-promote.timer" \
             /etc/systemd/system/minerva-promote.timer
  # ...but wq#5252: no venabled/vactive/vtimer_scheduled for minerva-promote.timer. It is
  # intended-off: the 2026-10-08 subtraction stopped and disabled the whole Minerva
  # proposal machine on purpose (/home/lumen/scratch/subtraction/REPORT.md:25-26, 1A:
  # factory, classifier, proposal, eval, promote, defer-review), and all six timers are
  # still disabled. With the factory off there is nothing to promote. Asserting
  # enabled+active failed --verify permanently, and the ops-root-apply it filed (wq#5252)
  # told the reader to run this bundle as root, which only hits the refusal below (or,
  # with WQ1396_FORCE_LEGACY=1, installs the superseded promoter). Re-enabling the
  # Minerva machine is a decision, not a heal; whoever makes it adds
  # venabled/vactive/vtimer_scheduled minerva-promote.timer back here.
  for u in minerva-proposal-promoter.service minerva-proposal-promoter.timer; do
    if [ -e "/etc/systemd/system/$u" ]; then
      _vmiss "superseded unit present: /etc/systemd/system/$u (wq#1500 owns this job)"
    fi
  done
  if [ -e /home/lumen/bin/minerva-proposal-promoter ]; then
    _vmiss "superseded binary present: /home/lumen/bin/minerva-proposal-promoter"
  fi
  vdone
fi

# wq#1500 (2026-09-13): superseded by minerva_promote.py (minerva repo) + ops/wq1500-minerva-promoter/.
# Different column/marker/lane and no [SEC] prefix -> running both double-files every CVE proposal.
if [ "${WQ1396_FORCE_LEGACY:-0}" != "1" ]; then
  echo "refuse: wq#1396 promoter is superseded by ops/wq1500-minerva-promoter/install.sh (see README)" >&2
  exit 2
fi

install -m 0755 "$HERE/minerva_proposal_promoter.py" /home/lumen/bin/minerva-proposal-promoter
sudo install -m 0644 "$HERE/minerva-proposal-promoter.service" /etc/systemd/system/minerva-proposal-promoter.service
sudo install -m 0644 "$HERE/minerva-proposal-promoter.timer" /etc/systemd/system/minerva-proposal-promoter.timer
sudo systemctl daemon-reload
sudo systemctl enable --now minerva-proposal-promoter.timer
systemctl list-timers minerva-proposal-promoter.timer --no-pager --no-legend
