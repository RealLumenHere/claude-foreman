"""wq#1998/wq#2015 — the liveness creation gate must stay ADVISORY at `wq block`.

`cmd_block` shares add/decide's on-ramp, but `_liveness_creation_check`'s `sys.exit(6)`
returns before `cmd_block` reaches `_finish(a, 'blocked')`. So a refused block never lands
and the row stays `claimed` until its lease expires — under a caller that has already given
up. An untagged blocked row is still findable (`liveness_clear.age_alarm()`); a stranded
claimed row is not. A retreat must always land.

wq#2015 is why this file exists: wq#1998's patch was deployed to the live
`/home/lumen/bin/wq` but never committed to the bin repo, so wq#1987's content-divergence
sweep reconciled the file back and silently reverted it, taking 17 harness tests red on one
cause. The patch is committed now (lumen-bin PR #97); this is the alarm for the next time a
lane rewrites `wq` in place, same shape as test_wq1539_release_dead_worker_order.py's.

The behavioural contract itself is covered by
test_liveness_clear.py::test_block_creation_gate_and_liveness_flags; here we assert the
keeper's own verdict on the live binary, plus that the gate is still FATAL everywhere else.
"""
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WQ_BIN = os.environ.get("WQ_BIN", "/home/lumen/bin/wq")
ENSURE = os.path.join(REPO, "ops", "wq1998-advisory-block-gate",
                      "ensure_wq_advisory_block_gate.py")


def test_the_live_wq_is_still_patched():
    """ops/wq841's lesson: `wq` is rewritten in place by whichever lane touches it next.
    This is the alarm for the wq#1998 advisory-block patch specifically."""
    p = subprocess.run([sys.executable, ENSURE, "--check", "--json", "--wq", WQ_BIN],
                       capture_output=True, text=True)
    report = json.loads(p.stdout)
    assert report["patched"], (
        f"{WQ_BIN} lost the wq#1998 advisory-block patch — every untagged `wq block` now "
        f"exits 6 and strands its ticket 'claimed' (wq#2015). "
        f"Repair: python3 {ENSURE} --apply\n{report['patches']}")


def test_the_keeper_rejects_a_reverted_wq():
    """The keeper is only worth having if its verify() actually fails on the regression:
    strip the patch back out of the live source and it must refuse, naming the rc=6 block."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("wq1998_keeper", ENSURE)
    keeper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(keeper)

    src = keeper.read(WQ_BIN)
    for _name, old, new, _n in keeper.PATCHES:
        src = src.replace(new, old)
    assert all(v == "pending" for v in keeper.status(src).values()), keeper.status(src)
    assert keeper.apply_to(src) == keeper.read(WQ_BIN), "re-apply must round-trip exactly"

    import tempfile
    reverted = os.path.join(tempfile.mkdtemp(prefix="wq1998-reverted"), "wq")
    with open(reverted, "w", encoding="utf-8") as f:
        f.write(src)
    try:
        keeper.verify(reverted)
    except RuntimeError as e:
        assert "must land" in str(e), e
    else:
        raise AssertionError("keeper.verify() passed on a wq with the advisory patch removed")
