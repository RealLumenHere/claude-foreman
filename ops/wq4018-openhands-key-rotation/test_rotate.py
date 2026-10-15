"""wq#4018: offline checks for the compose rewrite and the hash mask_replay keys on."""
import hashlib
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_key  # noqa: E402
import rotate  # noqa: E402

BODY = "Q" * 22
KEY = "sk-" + BODY
COMPOSE = (
    "services:\n"
    "  openhands:\n"
    "    environment:\n"
    "      - LLM_BASE_URL=http://host.docker.internal:4000/v1\n"
    f"      - LLM_API_KEY={KEY}\n"
)


def test_sha12_hashes_body_after_prefix():
    assert probe_key.sha12(KEY) == hashlib.sha256(BODY.encode()).hexdigest()[:12]


def test_rewrite_replaces_literal_with_env_reference(tmp_path):
    p = tmp_path / "docker-compose.yml"
    p.write_text(COMPOSE)
    assert rotate.rewrite_compose(str(p)) == 5
    text = p.read_text()
    assert KEY not in text
    assert "      - LLM_API_KEY=${OPENHANDS_LLM_API_KEY}\n" in text
    assert "LLM_BASE_URL=http://host.docker.internal:4000/v1" in text
    backup = tmp_path / "docker-compose.yml.bak.wq4018"
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert probe_key.compose_env(str(p))["LLM_API_KEY"] == "${OPENHANDS_LLM_API_KEY}"


def test_rewrite_never_overwrites_the_backup_holding_the_burned_key(tmp_path):
    p = tmp_path / "docker-compose.yml"
    p.write_text(COMPOSE)
    rotate.rewrite_compose(str(p))
    with pytest.raises(SystemExit):
        rotate.rewrite_compose(str(p))
    assert KEY in (tmp_path / "docker-compose.yml.bak.wq4018").read_text()


def test_rewrite_refuses_ambiguous_compose(tmp_path):
    p = tmp_path / "docker-compose.yml"
    p.write_text(COMPOSE + f"      - LLM_API_KEY={KEY}\n")
    with pytest.raises(SystemExit):
        rotate.rewrite_compose(str(p))
    assert p.read_text().count(KEY) == 2


def test_dotenv_is_0600_and_replaces_only_our_var(tmp_path):
    p = tmp_path / ".env"
    p.write_text("OTHER=1\nOPENHANDS_LLM_API_KEY=old\n")
    rotate.write_dotenv("new", str(p))
    assert p.read_text() == "OTHER=1\nOPENHANDS_LLM_API_KEY=new\n"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
