"""wq#4686 (design/orchestrator_verify_lane.md S5.2/S5.3): route a verify ticket by
its DERIVED needs, not by its lane of origin.

verify_needs_block_reasons() must refuse exactly the two S5.3 examples (ssh(thor)
THOR_HARDLOCK, a credential-prefix homeroot path) and clear exactly the examples S5.2
says demand reach (ssh to a grantable node, an allow-listed home-root file, the
minerva dir). classify_verify_archetype() must pick verify-probe only when a need (or
an ops_verified liveness) demands reach, verify-pass otherwise.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "..", "harness"))
import router  # noqa: E402
import needs as needs_mod  # noqa: E402


def test_archetype_10_is_verify_probe():
    assert router.ARCHETYPE_NUMBERS[10] == "verify-probe"
    assert "verify-probe" in router.ARCHETYPES


def test_ssh_thor_is_unsatisfiable_thor_hardlock():
    n = needs_mod.Needs([("ssh", "thor")])
    reasons = router.verify_needs_block_reasons(n)
    assert len(reasons) == 1
    assert "thor" in reasons[0] and "never_nodes" in reasons[0]
    assert "fleet_exec_policy.py:" in reasons[0]


def test_ssh_grantable_node_has_no_block_reason():
    n = needs_mod.Needs([("ssh", "anubis")])
    assert router.verify_needs_block_reasons(n) == []


def test_homeroot_credential_prefix_is_unsatisfiable():
    n = needs_mod.Needs([("homeroot", "/home/lumen/.config/systemd/user/foo")])
    reasons = router.verify_needs_block_reasons(n)
    assert len(reasons) == 1
    assert "capability_lint.py:" in reasons[0]


def test_homeroot_allowlisted_file_has_no_block_reason():
    n = needs_mod.Needs([("homeroot", "/home/lumen/claude_memory.md")])
    assert router.verify_needs_block_reasons(n) == []


def test_homeroot_outside_allowlist_is_unsatisfiable():
    # a home-root file that clears the deny-list (no credential marker) but is not one
    # of the five named VERIFY_HOME_ROOT_READ_ALLOW entries is still refused -- the
    # allowlist is closed, not "anything non-secret under /home/lumen".
    n = needs_mod.Needs([("homeroot", "/home/lumen/some_other_doc.md")])
    reasons = router.verify_needs_block_reasons(n)
    assert len(reasons) == 1
    assert "VERIFY_HOME_ROOT_READ_ALLOW" in reasons[0]


def test_classify_verify_probe_on_ssh_need():
    n = needs_mod.Needs([("ssh", "anubis")])
    arch, _ = router.classify_verify_archetype(n)
    assert arch == "verify-probe"


def test_classify_verify_probe_on_homeroot_need():
    n = needs_mod.Needs([("homeroot", "/home/lumen/claude_memory.md")])
    arch, _ = router.classify_verify_archetype(n)
    assert arch == "verify-probe"


def test_classify_verify_probe_on_minerva_dir():
    n = needs_mod.Needs([("dir", "/home/lumen/minerva")])
    arch, _ = router.classify_verify_archetype(n)
    assert arch == "verify-probe"


def test_classify_verify_probe_on_ops_liveness_alone():
    n = needs_mod.Needs([])
    arch, _ = router.classify_verify_archetype(n, liveness="ops_verified(wq10-good)")
    assert arch == "verify-probe"


def test_classify_verify_pass_on_no_reach_demand():
    n = needs_mod.Needs([])
    arch, _ = router.classify_verify_archetype(n)
    assert arch == "verify-pass"


def test_classify_verify_pass_on_plain_repo_need():
    # needs:repo()/needs:dir() for an ordinary managed repo (not minerva) is what every
    # cross-repo verify-pass ticket already carries (wq#1220/wq#4623) -- it must not
    # tip the archetype to verify-probe on its own.
    n = needs_mod.Needs([("repo", "/home/lumen/mailbot"), ("dir", "/home/lumen/mailbot")])
    arch, _ = router.classify_verify_archetype(n)
    assert arch == "verify-pass"
