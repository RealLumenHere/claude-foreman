import os
import subprocess

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run_eval.sh")


def run(env_overrides):
    env = dict(os.environ)
    env.update(env_overrides)
    return subprocess.run(
        [SCRIPT, "--check-only"], env=env, capture_output=True, text=True, timeout=10,
    )


def test_default_localhost_ok():
    p = run({})
    assert p.returncode == 0
    assert "OK:" in p.stdout


def test_loopback_127_ok():
    p = run({"LITELLM_BASE_URL": "http://127.0.0.1:4000"})
    assert p.returncode == 0


def test_router_ip_refused():
    p = run({"LITELLM_BASE_URL": "http://192.168.1.1:4000"})
    assert p.returncode != 0
    assert "REFUSED" in p.stderr
    assert "bridge" in p.stderr.lower()


def test_remote_host_refused():
    p = run({"LITELLM_BASE_URL": "http://100.112.151.100:4000"})
    assert p.returncode != 0
    assert "REFUSED" in p.stderr


def test_concurrency_within_range_ok():
    p = run({"WQ1300_MAX_CONCURRENCY": "3"})
    assert p.returncode == 0


def test_concurrency_zero_refused():
    p = run({"WQ1300_MAX_CONCURRENCY": "0"})
    assert p.returncode != 0
    assert "REFUSED" in p.stderr


def test_concurrency_too_high_refused():
    p = run({"WQ1300_MAX_CONCURRENCY": "5"})
    assert p.returncode != 0
    assert "REFUSED" in p.stderr


def test_concurrency_non_numeric_refused():
    p = run({"WQ1300_MAX_CONCURRENCY": "abc"})
    assert p.returncode != 0
    assert "REFUSED" in p.stderr
