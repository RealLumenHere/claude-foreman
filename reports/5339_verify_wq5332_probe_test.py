"""wq#5339: adversarial probes of wq#5332's new valid-grubenv branch (remote-bes.sh:25-45).

Reuses the committed fixtures of ops/wq5247-failed-units/test_failed_units.py (stub
systemctl/sudo/journalctl on PATH, tmp grubenv). Each probe drives an input the PR's own
tests do not: DRY=1 on the new branch, signature ok but `grub-editenv list` failing for a
non-corruption reason, and signature ok while `list` reports "invalid environment block".

Run: pytest -q reports/5339_verify_wq5332_probe_test.py
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
_T = os.path.join(HERE, "..", "ops", "wq5247-failed-units", "test_failed_units.py")
_spec = importlib.util.spec_from_file_location("wq5247_tests", _T)
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)

stubs = T.stubs  # pytest fixture, re-exported


def test_q1_dry_run_on_valid_grubenv_does_not_restart(stubs):
    tmp, b, state, env = stubs
    g = tmp / "grubenv"
    g.write_bytes(T.GOOD_ENV)
    T._grub_stub(b, g)
    p = T._remote("remote-bes.sh", dict(env, WQ5247_GRUBENV=str(g)), "1", "Q1")
    assert p.returncode == 0 and "would: reset-failed + restart" in p.stdout
    assert state.read_text().strip() == "failed"  # stub restart never ran
    assert g.read_bytes() == T.GOOD_ENV


def test_q2_signature_ok_but_list_errors_is_unfixed_no_restart(stubs):
    tmp, b, state, env = stubs
    g = tmp / "grubenv"
    g.write_bytes(T.GOOD_ENV)
    T.write_exe(b / "grub-editenv", 'echo "grub-editenv: error: cannot open: EIO" >&2; exit 1\n')
    p = T._remote("remote-bes.sh", dict(env, WQ5247_GRUBENV=str(g)), "0", "Q2")
    assert p.returncode == 1 and "grubenv signature ok but grub-editenv list rc=1" in p.stdout
    assert state.read_text().strip() == "failed"
    assert not (tmp / "grubenv.corrupt.bak.Q2").exists() and g.read_bytes() == T.GOOD_ENV


def test_q3_signature_ok_but_list_says_invalid_goes_to_corrupt_arm(stubs):
    # Case-arm ordering: the 'invalid environment block' arm (remote-bes.sh:24) must win
    # over the new ok|* arm (:25), so a block grub itself rejects is still recreated.
    tmp, b, state, env = stubs
    g = tmp / "grubenv"
    g.write_bytes(T.GOOD_ENV)
    T.write_exe(b / "grub-editenv", f"""
        if [ "$2" = create ]; then printf '# GRUB Environment Block\\n' > "$1"; exit 0; fi
        echo "grub-editenv: error: invalid environment block." >&2; exit 1
    """)
    p = T._remote("remote-bes.sh", dict(env, WQ5247_GRUBENV=str(g)), "0", "Q3")
    assert "grubenv corrupt" in p.stdout and p.returncode == 0
    assert (tmp / "grubenv.corrupt.bak.Q3").read_bytes() == T.GOOD_ENV
