"""wq#3457: foreman-capability rows are closed once moot, and a declined root is never
re-proposed. The policy here is a fixed dict and the on-disk paths live in tmp_path, so
nothing depends on this host's /home/lumen layout or the live alert queue."""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import capability_lint  # noqa: E402
import capability_proposal as cp  # noqa: E402

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
FRESH = (NOW - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
OLD = (NOW - timedelta(hours=cp.STALE_H + 1)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def tree(tmp_path):
    for d in ("granted/sub", "declined/sub", "broad/narrow", "pending"):
        (tmp_path / d).mkdir(parents=True)
    policy = {"allow": {"read_dirs": [str(tmp_path / "granted")]},
              "declined_read_dirs": [str(tmp_path / "declined"),
                                     {"path": str(tmp_path / "broad"), "exact": True}]}
    return tmp_path, policy


def _grant_row(path, ts=FRESH, rid=1, title=None):
    return {"id": rid, "key": cp.grant_key(path), "ts": ts,
            "title": title or f"grant read root {path} (wq#9 needs_unresolved)"}


def _no_wq(_tid):
    raise AssertionError("a grant-dir row must not consult wq")


def test_declined_subtree_and_exact_root(tree):
    root, policy = tree
    assert cp.declined_root(str(root / "declined/sub"), policy) == str(root / "declined")
    assert cp.declined_root(str(root / "broad"), policy) == str(root / "broad")
    assert cp.declined_root(str(root / "broad/narrow"), policy) is None   # exact: sub-root still proposable
    assert cp.declined_root(str(root / "pending"), policy) is None


def test_declined_root_is_not_proposed(tree):
    root, policy = tree
    lines = [f"dir({root / p}) is not under allow.read_dirs — x" for p in ("declined/sub", "pending")]
    assert cp.unresolved_dir_paths(lines, policy) == [str(root / "pending")]


@pytest.mark.parametrize("rel,ts,want", [
    ("granted/sub", FRESH, "granted:"),
    ("declined/sub", FRESH, "declined:"),
    ("vanished", FRESH, "no longer exists"),
    ("pending", OLD, "stale:"),
    ("pending", FRESH, None),             # live proposal: kept open
    ("broad/narrow", FRESH, None),        # exact decline does not cover a sub-root
])
def test_grant_row_moot_reason(tree, rel, ts, want):
    root, policy = tree
    why = cp.moot_reason(_grant_row(str(root / rel), ts), policy, _no_wq, NOW)
    assert (why is None) if want is None else why.startswith(want) or want in why


def test_truncated_title_is_only_closed_as_stale(tree):
    root, policy = tree
    full = str(root / "granted/sub")
    row = _grant_row(full, title=f"grant read root {full[:-3]} (wq#9")   # path cut by aq list
    assert cp.moot_reason(row, policy, _no_wq, NOW) is None
    assert cp.moot_reason(dict(row, ts=OLD), policy, _no_wq, NOW).startswith("stale:")


@pytest.mark.parametrize("status,closed", [("done", True), ("open", True), ("blocked", False),
                                           (None, False)])
def test_denied_row_follows_ticket_status(status, closed):
    row = {"id": 2, "key": "capability-denied-wq2914", "ts": OLD, "title": "wq#2914 (P1) blocked"}
    seen = []
    why = cp.moot_reason(row, {}, lambda tid: seen.append(tid) or status, NOW)
    assert seen == [2914] and (why is not None) == closed


def _fake_aq(tmp_path, listing):
    out, log = tmp_path / "aq.out", tmp_path / "aq.log"
    out.write_text(listing)
    aq = tmp_path / "aq"
    aq.write_text(f'#!/usr/bin/env bash\nif [ "$1" = list ]; then cat {out}; exit 0; fi\n'
                  f'printf "%s\\n" "$*" >> {log}\n')
    aq.chmod(0o755)
    return str(aq), log


def _line(rid, key, ts, title, source=cp.AQ_SOURCE):
    return f"[{rid}] open     x3   {source} {key:<40} owner=unassigned   {ts}  {title}\n"


def test_reconcile_resolves_only_moot_rows_of_this_source(tree):
    root, policy = tree
    granted, pending = str(root / "granted/sub"), str(root / "pending")
    listing = (_line(10, cp.grant_key(granted), FRESH, f"grant read root {granted} (wq#9 n")
               + _line(11, cp.grant_key(pending), FRESH, f"grant read root {pending} (wq#9 n")
               + _line(12, cp.grant_key(granted), FRESH, "other source", source="sentinel"))
    aq, log = _fake_aq(root, listing)
    res = cp.reconcile(_no_wq, policy, now=NOW, aq_bin=aq)
    assert [(r[0], r[3]) for r in res] == [(10, True)]
    assert log.read_text().startswith("resolve 10 wq#3457 reconcile: granted:")


def test_reconcile_dry_run_writes_nothing(tree):
    root, policy = tree
    granted = str(root / "granted/sub")
    aq, log = _fake_aq(root, _line(10, cp.grant_key(granted), FRESH,
                                   f"grant read root {granted} (wq#9 n"))
    assert [(r[0], r[3]) for r in cp.reconcile(_no_wq, policy, True, NOW, aq)] == [(10, False)]
    assert not log.exists()


def test_reconcile_unreadable_aq_is_none(tmp_path):
    assert cp.reconcile(_no_wq, {}, aq_bin=str(tmp_path / "nope")) is None


def test_live_policy_declines_the_recorded_roots_only():
    """The committed policy: the roots its comments already declined, nothing granted."""
    policy = capability_lint.load_policy(os.path.join(HARNESS_DIR, "fleet-exec-policy.yaml"))
    for p in ("/home/lumen/minerva", "/home/lumen/agent", "/home/lumen/sentinel/config",
              "/home/lumen/fleet/inventory/machines", "/home/lumen/projects"):
        assert cp.declined_root(p, policy), p
    for p in ("/home/lumen/projects/auto-browser", "/home/lumen/scratch", "/home/lumen/re"):
        assert cp.declined_root(p, policy) is None, p
    for root in policy["allow"]["read_dirs"]:
        assert not cp.declined_root(root, policy) or capability_lint._under(root, "/home/lumen/minerva"), root
