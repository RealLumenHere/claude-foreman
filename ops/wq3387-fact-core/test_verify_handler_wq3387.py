"""wq#3387: the bundle's `install.sh --verify` handler, per ops/VERIFY_CONTRACT.md.

Read-only, exit 0 when the FACT_core clone, its venv, a running FACT container
and the web UI are all present, exit 1 with one MISS line per gap otherwise.
"""
import http.server
import os
import stat
import subprocess
import threading
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent

BASH = subprocess.run(["which", "bash"], capture_output=True, text=True).stdout.strip()


def run(*args, env_overrides=None):
    env = dict(os.environ)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [BASH, str(BUNDLE / "install.sh"), *args],
        capture_output=True, text=True, env=env,
    )


def fake_bin(tmp_path, name, body):
    d = tmp_path / "fakebin"
    d.mkdir(exist_ok=True)
    f = d / name
    f.write_text(f"#!/bin/sh\n{body}\n")
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return d


def test_handler_precedes_any_install_step():
    text = (BUNDLE / "install.sh").read_text()
    assert text.index('if [ "${1:-}" = --verify ]; then') < text.index("install (on Lumen")


def test_verify_reports_one_miss_per_absent_piece(tmp_path):
    """Nothing installed yet (no FACT_core clone/venv, no docker, no listener on the
    test port) -- every check must independently fail, proving each one can fail."""
    install_dir = tmp_path / "no-such-FACT_core"
    venv_dir = tmp_path / "no-such-venv"
    # Lumen itself has a real docker client in /usr/bin, so put a fake one first in
    # PATH that resolves but reports no containers -- proves the "found docker but
    # nothing fact-labelled is running" branch, which is the realistic failure mode
    # on a host that has docker without FACT installed.
    fakebin = fake_bin(tmp_path, "docker", 'if [ "$1" = "ps" ]; then :; fi')
    proc = run(
        "--verify",
        env_overrides={
            "FACT_INSTALL_DIR": str(install_dir),
            "FACT_VENV_DIR": str(venv_dir),
            "FACT_PORT": "18765",
            "PATH": f"{fakebin}:/usr/bin:/bin",
        },
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert proc.stdout.count("MISS ") == 4
    assert "FACT_core clone" in proc.stdout
    assert "install venv" in proc.stdout
    assert "no running container name/image matches 'fact'" in proc.stdout
    assert "web UI not responding" in proc.stdout


def test_verify_passes_when_every_piece_is_live(tmp_path):
    install_dir = tmp_path / "FACT_core"
    (install_dir / ".git").mkdir(parents=True)
    (install_dir / ".git" / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/fkie-cad/FACT_core.git\n'
    )

    venv_dir = tmp_path / "venv-fact"
    (venv_dir / "bin").mkdir(parents=True)
    (venv_dir / "bin" / "activate").write_text("# stub venv activate\n")

    fakebin = fake_bin(
        tmp_path, "docker",
        'if [ "$1" = "ps" ]; then echo "fact_frontend_1 fact_core-frontend:latest"; fi',
    )

    class _OKHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _OKHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        proc = run(
            "--verify",
            env_overrides={
                "FACT_INSTALL_DIR": str(install_dir),
                "FACT_VENV_DIR": str(venv_dir),
                "FACT_PORT": str(port),
                "PATH": f"{fakebin}:/usr/bin:/bin",
            },
        )
    finally:
        thread.join(timeout=2)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VERIFY OK" in proc.stdout
