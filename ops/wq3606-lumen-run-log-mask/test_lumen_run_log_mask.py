"""wq#3606: `lumen-run log` masks the job journal before it reaches a transcript.

The script is copied into a tmp bin dir next to the committed mask-secrets fixture
(ops/wq2316-mask-secrets-boundary), the way install.sh puts it next to the deployed one.
journalctl and hostname are stubbed; the fake secret is built at runtime so this file
never carries a credential-shaped literal.
"""
import os
import shutil
import stat
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "lumen-run")
MASK = os.path.join(os.path.dirname(HERE), "wq2316-mask-secrets-boundary", "mask-secrets")

SECRET = "sk-" + "lf-" + "0a1b2c3d" * 5


@pytest.fixture
def lumen_bin(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()
    shutil.copy(SRC, b / "lumen-run")
    shutil.copy(MASK, b / "mask-secrets")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "journalctl").write_text(
        "#!/bin/sh\n"
        "echo '    LANGFUSE_INIT_PROJECT_SECRET_KEY: %s'\n"
        "echo 'plain job output'\n"
        "echo LUMEN_JOB_EXIT=0\n" % SECRET)
    (stubs / "hostname").write_text("#!/bin/sh\necho lumen\n")
    for d in (b, stubs):
        for f in d.iterdir():
            f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return b, stubs


def run_log(lumen_bin):
    b, stubs = lumen_bin
    env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}"}
    return subprocess.run(["bash", str(b / "lumen-run"), "log", "lumen-job-x"],
                          capture_output=True, text=True, env=env, timeout=30)


def test_a_secret_in_the_journal_is_masked(lumen_bin):
    p = run_log(lumen_bin)
    assert p.returncode == 0, p.stderr
    assert SECRET not in p.stdout
    assert "MASKED" in p.stdout


def test_ordinary_output_and_the_exit_sentinel_pass_through(lumen_bin):
    p = run_log(lumen_bin)
    assert "plain job output" in p.stdout
    assert "LUMEN_JOB_EXIT=0" in p.stdout


def test_without_mask_secrets_it_prints_nothing_and_fails(lumen_bin):
    (lumen_bin[0] / "mask-secrets").unlink()
    p = run_log(lumen_bin)
    assert p.returncode == 3
    assert p.stdout == ""
    assert "refusing to print an unmasked journal" in p.stderr


def test_the_unmasked_original_would_have_leaked(lumen_bin, tmp_path):
    # Control: the deployed log line before this fix, against the same journal stub.
    b, stubs = lumen_bin
    old = (b / "lumen-run").read_text()
    start = old.index("  log)\n")
    end = old.index("    ;;\n", start) + len("    ;;\n")
    old = old[:start] + ('  log)   unit="${1:?unit required}"; '
                         'journalctl -u "$unit" --no-pager -o cat 2>/dev/null;;\n') + old[end:]
    (b / "lumen-run").write_text(old)
    p = run_log(lumen_bin)
    assert SECRET in p.stdout
