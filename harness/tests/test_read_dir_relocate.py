"""read_dir_relocate (wq#2118): a credential-shaped _runs artifact leaves the read grant
but is not destroyed, and the pointer that replaces it must not itself trip the sweep."""
import os
import stat
import sys

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS)
import read_dir_relocate as rdr  # noqa: E402
import read_dir_sweep as rds  # noqa: E402

# assembled at runtime so this file is not itself a read_dir_sweep hit once it lands in foreman
FAKE_PAT = "github_" + "pat_11ABCDEFGH0nTgGh17bRlBn_C2ieRMdH3r81BdXM9Gb0T8MwcmtnQ4fNOy9tMQWKEslKOR3FVFBxw"


def _write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)
    return p


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def test_relocates_flagged_and_leaves_pointer(tmp_path):
    root, vault = tmp_path / "foreman", tmp_path / "vault"
    env = _write(root, "_runs/wq1/snapshots/20260907T1Z-home_lumen_agent_.env", "X=1\n")
    cfg = _write(root, "_runs/wq1/snapshots/cfg.yaml", f"token: {FAKE_PAT}\n")
    clean = _write(root, "_runs/wq1/notes.md", "nothing here\n")
    rows = rdr.relocate([str(root)], apply=True, vault=str(vault))
    assert sorted(r[0] for r in rows) == sorted([env, cfg])
    assert all(r[2] is None for r in rows)
    assert os.path.exists(clean)
    assert _mode(vault) == 0o700
    for src, dst, _ in rows:
        assert _mode(dst) == 0o600
        assert not os.path.exists(src)
        pointer = open(src + ".held-out").read()
        assert dst in pointer and FAKE_PAT not in pointer
    assert open(rows[1][1]).read() == f"token: {FAKE_PAT}\n"


def test_sweep_is_clean_after_relocation_and_rerun_is_a_noop(tmp_path):
    root, vault = tmp_path / "foreman", tmp_path / "vault"
    _write(root, "_runs/wq1/snapshots/a-.env", "X=1\n")
    _write(root, "_runs/wq2/fleet9.jsonl", f'{{"t": "token: {FAKE_PAT}"}}\n')
    rdr.relocate([str(root)], apply=True, vault=str(vault))
    assert rds.sweep_root(str(root))[0] == []
    assert rdr.relocate([str(root)], apply=True, vault=str(vault)) == []
    assert len(os.listdir(vault)) == 2


def test_verify_flags_a_loosened_or_missing_vault_file(tmp_path):
    root, vault = tmp_path / "foreman", tmp_path / "vault"
    _write(root, "_runs/wq1/a-.env", "X=1\n")
    _write(root, "_runs/wq2/b-.env", "X=2\n")
    rows = rdr.relocate([str(root)], apply=True, vault=str(vault))
    assert [r[2] for r in rdr.verify([str(root)])] == [None, None]
    os.chmod(rows[0][1], 0o644)
    os.unlink(rows[1][1])
    problems = [r[2] for r in rdr.verify([str(root)])]
    assert "0600" in problems[0] and "missing" in problems[1]


def test_dry_run_moves_nothing(tmp_path):
    root, vault = tmp_path / "foreman", tmp_path / "vault"
    env = _write(root, "_runs/wq1/a-.env", "X=1\n")
    rows = rdr.relocate([str(root)], vault=str(vault))
    assert rows == [(env, None, None)]
    assert os.path.exists(env) and not vault.exists()


def test_only_touches_runs_dir(tmp_path):
    root, vault = tmp_path / "foreman", tmp_path / "vault"
    live = _write(root, "harness/x.env", "X=1\n")
    assert rdr.relocate([str(root)], apply=True, vault=str(vault)) == []
    assert os.path.exists(live)


def test_same_basename_in_two_runs_does_not_collide(tmp_path):
    root, vault = tmp_path / "foreman", tmp_path / "vault"
    _write(root, "_runs/wq1/snapshots/a-.env", "X=1\n")
    _write(root, "_runs/wq2/snapshots/a-.env", "X=2\n")
    rows = rdr.relocate([str(root)], apply=True, vault=str(vault))
    assert len({r[1] for r in rows}) == 2 and all(r[2] is None for r in rows)


def test_existing_vault_file_is_never_overwritten(tmp_path):
    root, vault = tmp_path / "foreman", tmp_path / "vault"
    src = _write(root, "_runs/wq1/a-.env", "X=1\n")
    os.makedirs(vault)
    keep = _write(vault, rdr.vault_name(str(root), src), "older\n")
    rows = rdr.relocate([str(root)], apply=True, vault=str(vault))
    assert rows[0][1] is None and "FileExistsError" in rows[0][2]
    assert os.path.exists(src) and open(keep).read() == "older\n"


def test_symlink_is_left_alone(tmp_path):
    root, vault = tmp_path / "foreman", tmp_path / "vault"
    os.makedirs(root / "_runs")
    os.symlink("/nonexistent/target", root / "_runs" / "link.env")
    assert rdr.relocate([str(root)], apply=True, vault=str(vault)) == []
    assert os.path.islink(root / "_runs" / "link.env")
